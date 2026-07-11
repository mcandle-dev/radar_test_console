"""UI 진입점 — 연결바/세션 타임라인/레이더/수치 패널/로그 콘솔 (요구사항정의서 6.1).

주의: UI는 RadarDevice 인터페이스에만 의존한다. 이 파일에 import serial 금지.
"""

import asyncio
import logging
import queue
import time
from collections import deque
from pathlib import Path
from typing import Deque, Optional, Tuple

import flet as ft

import uci_params
from models import DeviceEvent, Measurement, SessionState
from parser import is_out_of_range, parse_line
from radar_device import (
    LINK_LOG_PREFIX,
    UCI_LOG_PREFIX,
    QorvoSerialDevice,
    RadarDevice,
    SimulatorDevice,
    UciSerialDevice,
)
from radar_view import RadarView

# 보드가 없으면 시뮬레이터(True), 있으면 실물(False). 이 한 줄만 바꾸면 된다.
USE_SIMULATOR = False
# 실물 모드에서: UCI 펌웨어(DW3_QM33 SDK UCI)면 True, CLI 텍스트 펌웨어면 False.
USE_UCI = True

PUMP_INTERVAL_S = 0.05  # 큐 → 화면 반영 주기 50ms (NFR-1 지연 100ms 이내)
RX_TIMEOUT_S = 2.0  # 무수신 판정 (NFR-3)
RECONNECT_INTERVAL_S = 2.0  # 자동 재연결 재시도 주기 (NFR-4)
RATE_WINDOW_S = 1.0  # 수신율(건/초) 계산 창
LOG_MAX_LINES = 300  # 로그 콘솔 화면 최대 줄 수 (메모리 보호)
LOG_HISTORY_MAX = 10_000  # 파일 저장용 히스토리 최대 줄 수
LOG_DIR = Path("logs")  # 로그 파일 저장 폴더 (FR-6)
DEFAULT_BAUD = 115200
BAUD_OPTIONS = (9600, 57600, 115200, 230400, 921600)
LOG_FONT = "Consolas"
WINDOW_WIDTH = 1060
WINDOW_HEIGHT = 900
PANEL_WIDTH = 280
LOG_HEIGHT = 220

TIMELINE_STAGES = (
    SessionState.SLEEP,
    SessionState.BLE_ADV,
    SessionState.BLE_CONN,
    SessionState.OOB_DONE,
    SessionState.RANGING,
)

COLOR_OK = ft.Colors.GREEN_400
COLOR_ACTIVE = ft.Colors.BLUE_400
COLOR_IDLE = ft.Colors.GREY_700
COLOR_NO_RX = ft.Colors.GREY_500  # 수신없음 = 회색 (요구사항정의서 6.3)
COLOR_ERR = ft.Colors.RED_400
COLOR_WARN = ft.Colors.ORANGE_400
COLOR_TEXT_DIM = ft.Colors.GREY_400

logger = logging.getLogger(__name__)


class SessionTimeline(ft.Row):
    """T-0~T-3 세션 5단계 진행을 색으로 보여주는 타임라인 (완료=녹/진행중=청/대기=회/실패=적)."""

    def __init__(self) -> None:
        self._chips: dict[SessionState, ft.Container] = {}
        self._reason = ft.Text("", color=COLOR_ERR, size=12)
        self._current_idx = -1
        controls: list[ft.Control] = []
        for i, stage in enumerate(TIMELINE_STAGES):
            chip = ft.Container(
                content=ft.Text(stage.value, size=12, color=ft.Colors.WHITE),
                padding=ft.Padding(12, 6, 12, 6),
                border_radius=14,
                bgcolor=COLOR_IDLE,
            )
            self._chips[stage] = chip
            controls.append(chip)
            if i < len(TIMELINE_STAGES) - 1:
                controls.append(ft.Text("→", color=COLOR_TEXT_DIM))
        controls.append(self._reason)
        super().__init__(controls=controls, alignment=ft.MainAxisAlignment.CENTER)

    def apply_event(self, event: DeviceEvent) -> None:
        """세션 이벤트를 반영한다. ERR은 진행 중이던 다음 단계를 적색+사유로 표시."""
        if event.state == SessionState.ERR:
            fail_idx = min(self._current_idx + 1, len(TIMELINE_STAGES) - 1)
            self._paint(fail_idx, failed=True)
            self._reason.value = f"사유: {event.reason or '알 수 없음'}"
            return
        if event.state not in TIMELINE_STAGES:  # UNKNOWN 등은 타임라인 미반영
            return
        self._current_idx = TIMELINE_STAGES.index(event.state)
        self._paint(self._current_idx, failed=False)
        self._reason.value = ""

    def reset(self) -> None:
        """연결 시작 시 전체 단계를 대기(회색)로 되돌린다."""
        self._current_idx = -1
        for chip in self._chips.values():
            chip.bgcolor = COLOR_IDLE
        self._reason.value = ""

    def _paint(self, idx: int, failed: bool) -> None:
        """idx 앞=완료(녹), idx=진행중(청)/실패(적), 뒤=대기(회)."""
        for i, stage in enumerate(TIMELINE_STAGES):
            if i < idx:
                self._chips[stage].bgcolor = COLOR_OK
            elif i == idx:
                self._chips[stage].bgcolor = COLOR_ERR if failed else COLOR_ACTIVE
            else:
                self._chips[stage].bgcolor = COLOR_IDLE


class NumericPanel(ft.Container):
    """거리·각도·수신율·최종수신·세션·상태를 보여주는 우측 수치 패널."""

    def __init__(self) -> None:
        self._dist = ft.Text("—", size=32, weight=ft.FontWeight.BOLD)
        self._angle = ft.Text("—", size=24)
        self._rate = ft.Text("0 건/초")
        self._last_rx = ft.Text("—")
        self._session = ft.Text("N/A")
        self._status = ft.Text("미연결", color=COLOR_IDLE)
        self._rate_value = 0
        super().__init__(
            width=PANEL_WIDTH,
            padding=16,
            border_radius=12,
            bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.WHITE),
            content=ft.Column(
                controls=[
                    self._row("거리", self._dist),
                    self._row("각도", self._angle),
                    ft.Divider(height=1),
                    self._row("수신율", self._rate),
                    self._row("최종수신", self._last_rx),
                    self._row("세션", self._session),
                    self._row("상태", self._status),
                ],
                spacing=12,
            ),
        )

    @staticmethod
    def _row(label: str, value: ft.Text) -> ft.Row:
        """'라벨 : 값' 한 줄."""
        return ft.Row(
            controls=[ft.Text(label, width=70, color=COLOR_TEXT_DIM), value],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def set_measurement(
        self, m: Measurement, warn: bool, target_label: Optional[str] = None
    ) -> None:
        """측정값 갱신. 범위 초과면 경고색, ANGLE 없으면 'N/A'."""
        color = COLOR_WARN if warn else ft.Colors.WHITE
        prefix = ""
        if target_label:
            prefix = f"{target_label}: "
        elif m.target_id:
            prefix = f"{m.target_id}: "
        if m.dist_cm is None:
            self._dist.value = "—"
        else:
            self._dist.value = f"{prefix}{m.dist_cm} cm"
        self._dist.color = color
        self._angle.value = "N/A" if m.angle_deg is None else f"{m.angle_deg} °"
        self._angle.color = color
        self._last_rx.value = time.strftime("%H:%M:%S", time.localtime(m.ts))

    def set_rate(self, count: int) -> bool:
        """수신율 표시. 값이 바뀌었을 때만 True (불필요한 page.update 방지)."""
        if count == self._rate_value:
            return False
        self._rate_value = count
        self._rate.value = f"{count} 건/초"
        return True

    def set_session(self, event: DeviceEvent) -> None:
        """세션 상태 표시. ERR은 사유와 함께 적색."""
        if event.state == SessionState.ERR:
            self._session.value = f"● ERR ({event.reason or '?'})"
            self._session.color = COLOR_ERR
        else:
            self._session.value = f"● {event.state.value}"
            self._session.color = (
                COLOR_OK if event.state == SessionState.RANGING else COLOR_ACTIVE
            )

    def set_status(self, label: str, color: str) -> None:
        """연결/수신 상태 텍스트 갱신."""
        self._status.value = label
        self._status.color = color

    def reset_values(self) -> None:
        """미연결 상태 표시(값 '—')로 되돌린다."""
        self._dist.value = "—"
        self._angle.value = "—"
        self._session.value = "N/A"
        self._session.color = ft.Colors.WHITE


class LogConsole(ft.Container):
    """timestamp + RX/TX + 원문 + 파싱결과(색상)를 흘리는 하단 로그 콘솔."""

    def __init__(self) -> None:
        self._list = ft.ListView(auto_scroll=True, expand=True, spacing=0)
        self._history: list[str] = []  # 화면 줄 수 제한과 별도로 파일 저장용 전체 보관
        header = ft.Row(
            controls=[
                ft.Text("로그 콘솔", weight=ft.FontWeight.BOLD),
                ft.Container(expand=True),
                ft.TextButton(
                    "저장",
                    tooltip="세션 로그를 logs/*.txt로 저장 (FR-6)",
                    on_click=self._on_save,
                ),
                ft.TextButton("지우기", on_click=self._on_clear),
            ]
        )
        super().__init__(
            height=LOG_HEIGHT,
            padding=8,
            border_radius=12,
            bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.WHITE),
            content=ft.Column(controls=[header, self._list], spacing=4),
        )

    def append_rx(self, raw: str) -> None:
        """수신 원시 라인을 파서로 재분류해 색·결과 표기와 함께 추가한다."""
        color, verdict = self._classify(raw)
        self._append_line(f"RX  {raw:<40} {verdict}", color)

    def append_sys(self, text: str) -> None:
        """앱 자체 이벤트(연결/해제 등)를 회색으로 남긴다."""
        self._append_line(f"--  {text}", COLOR_TEXT_DIM)

    def clear(self) -> None:
        """화면 로그를 비운다 (파일 저장용 히스토리는 유지)."""
        self._list.controls.clear()

    def save_to_file(self) -> Optional[Path]:
        """세션 로그 전체를 logs/radar_log_*.txt로 저장한다 (FR-6, 사후 분석용)."""
        if not self._history:
            return None
        LOG_DIR.mkdir(exist_ok=True)
        path = LOG_DIR / f"radar_log_{time.strftime('%Y%m%d_%H%M%S')}.txt"
        path.write_text("\n".join(self._history) + "\n", encoding="utf-8")
        return path

    def _on_save(self, e: ft.ControlEvent) -> None:
        """저장 버튼: 파일로 쓰고 결과를 로그에 남긴다."""
        try:
            path = self.save_to_file()
        except OSError as err:
            self.append_sys(f"로그 저장 실패: {err}")
        else:
            self.append_sys(
                f"로그 저장됨: {path}" if path else "저장할 로그가 없습니다"
            )
        self.update()

    def _on_clear(self, e: ft.ControlEvent) -> None:
        """지우기 버튼: 화면을 비우고 즉시 반영한다."""
        self.clear()
        self.update()

    @staticmethod
    def _classify(raw: str) -> Tuple[str, str]:
        """순수 파서로 라인 종류를 판별해 (색, 결과표기)를 돌려준다."""
        if raw.startswith(LINK_LOG_PREFIX):
            return COLOR_ERR, "⚠ 연결 오류"
        if raw.startswith("TX"):
            return ft.Colors.CYAN_300, "→ 전송"
        if raw.startswith(UCI_LOG_PREFIX):  # UCI 프로토콜 정보 (파싱 대상 아님)
            return ft.Colors.PURPLE_200, "ⓘ UCI"
        result = parse_line(raw)
        if result.kind == "measurement":
            return ft.Colors.GREY_300, "✔ 파싱 OK"
        if result.kind == "state":
            is_err = result.event is not None and result.event.state == SessionState.ERR
            return (COLOR_ERR, "✔ 세션(ERR)") if is_err else (COLOR_ACTIVE, "✔ 세션")
        return COLOR_ERR, f"✘ 파싱 실패 ({result.error})"

    def _append_line(self, text: str, color: str) -> None:
        """한 줄 추가 + 최대 줄 수 초과분 삭제 (오래된 것부터)."""
        stamp = time.strftime("%H:%M:%S")
        entry = f"{stamp}  {text}"
        self._list.controls.append(
            ft.Text(entry, size=11, font_family=LOG_FONT, color=color)
        )
        overflow = len(self._list.controls) - LOG_MAX_LINES
        if overflow > 0:
            del self._list.controls[:overflow]
        self._history.append(entry)
        hist_overflow = len(self._history) - LOG_HISTORY_MAX
        if hist_overflow > 0:
            del self._history[:hist_overflow]


class RadarApp:
    """디바이스 콜백 → queue → 50ms 펌프로 화면을 갱신하는 앱 전체 배선."""

    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self.events: "queue.Queue[Tuple[str, object]]" = queue.Queue()
        self.device: RadarDevice = self._create_device()
        self.device.on_measurement = lambda m: self.events.put(("meas", m))
        self.device.on_state = lambda e: self.events.put(("state", e))
        self.device.on_log = lambda s: self.events.put(("log", s))

        self.radar = RadarView()
        self.timeline = SessionTimeline()
        self.panel = NumericPanel()
        self.log = LogConsole()
        self._max_targets = 5
        self._target_labels: dict[str, str] = {}
        self._build_connection_bar()

        self._meas_ts: Deque[float] = deque()
        self._last_rx: Optional[float] = None
        self._status_cache: Tuple[str, str] = ("", "")
        self._latest_targets: dict[str, Measurement] = {}
        self._running = True
        # 자동 재연결(NFR-4): 사용자가 원한 연결이 끊기면 포트 복귀를 기다렸다 재시도
        self._want_connected = False
        self._last_port = ""
        self._last_baud = DEFAULT_BAUD
        self._connected_at = 0.0
        self._next_reconnect_ts = 0.0

    @staticmethod
    def _create_device() -> RadarDevice:
        """USE_SIMULATOR/USE_UCI 스위치에 따라 디바이스 구현을 고른다."""
        if USE_SIMULATOR:
            return SimulatorDevice()
        return UciSerialDevice() if USE_UCI else QorvoSerialDevice()

    def _build_connection_bar(self) -> None:
        """포트/속도 선택 + 상태 LED + 연결 토글 버튼."""
        ports = self.device.list_ports()
        self.port_dd = ft.Dropdown(
            width=140,
            options=[ft.dropdown.Option(p) for p in ports],
            value=ports[0] if ports else None,
        )
        self.baud_dd = ft.Dropdown(
            width=120,
            options=[ft.dropdown.Option(str(b)) for b in BAUD_OPTIONS],
            value=str(DEFAULT_BAUD),
        )
        # 실패 재현 토글은 시뮬레이터 전용 — 실물 모드에선 숨긴다
        self.fail_switch = ft.Switch(
            label="OOB 실패 재현", value=False, visible=USE_SIMULATOR
        )
        # 폰 주소(DST_MAC)는 세션마다 바뀔 수 있어 실행 중 변경 가능 — UCI 모드 전용
        self.dest_mac_tf = ft.TextField(
            label="폰 주소",
            value=uci_params.DEFAULT_DEST_MAC,
            width=110,
            tooltip="폰 앱 화면의 '내 주소' (XX:XX hex) — Enter로 반영",
            visible=isinstance(self.device, UciSerialDevice),
            on_submit=self._on_dest_mac_commit,
            on_blur=self._on_dest_mac_commit,
        )
        self.target_fields: list[ft.TextField] = []
        for idx in range(1, self._max_targets + 1):
            field = ft.TextField(
                label=f"타겟 {idx}",
                value=str(idx),
                width=70,
                dense=True,
                tooltip=f"타겟 {idx} 표시 이름",
                on_submit=self._on_target_label_commit,
                on_blur=self._on_target_label_commit,
            )
            self.target_fields.append(field)
        self.target_wrap = ft.Column(
            controls=self.target_fields,
            spacing=4,
            tight=True,
        )
        # FR-8: ranging 시작/정지 명령 (펌웨어 미지원이면 디바이스가 no-op+로그 처리)
        self.start_btn = ft.OutlinedButton(
            "시작",
            tooltip="START 전송 (FR-8)",
            on_click=lambda e: self.device.start_ranging(),
        )
        self.stop_btn = ft.OutlinedButton(
            "정지",
            tooltip="STOP 전송 (FR-8)",
            on_click=lambda e: self.device.stop_ranging(),
        )
        self.led = ft.Container(
            width=14, height=14, border_radius=7, bgcolor=COLOR_IDLE
        )
        self.led_label = ft.Text("미연결", color=COLOR_TEXT_DIM)
        self.connect_btn = ft.FilledButton("연결", on_click=self._on_connect_click)
        self.connection_bar = ft.Row(
            controls=[
                ft.Text("포트"),
                self.port_dd,
                ft.IconButton(
                    icon=ft.Icons.REFRESH,
                    tooltip="포트 새로고침",
                    on_click=self._on_refresh_ports,
                ),
                ft.Text("속도"),
                self.baud_dd,
                self.fail_switch,
                self.dest_mac_tf,
                self.target_wrap,
                self.start_btn,
                self.stop_btn,
                ft.Container(expand=True),
                self.led,
                self.led_label,
                self.connect_btn,
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def build(self) -> ft.Control:
        """6.1 레이아웃: 연결바 / 타임라인 / (레이더|수치) / 로그."""
        return ft.Column(
            controls=[
                self.connection_bar,
                self.timeline,
                ft.Row(
                    controls=[
                        ft.Container(
                            content=self.radar,
                            expand=True,
                            alignment=ft.Alignment(0.5, 0.5),
                        ),
                        self.panel,
                    ],
                    expand=True,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
                self.log,
            ],
            expand=True,
            spacing=12,
        )

    # --- 이벤트 핸들러 (메인 스레드) ---

    def _on_connect_click(self, e: ft.ControlEvent) -> None:
        """연결/해제 토글."""
        if self.device.is_connected():
            self._disconnect()
        else:
            self._connect()
        self.page.update()

    def _on_dest_mac_commit(self, e: ft.ControlEvent) -> None:
        """폰 주소 입력 확정(Enter/포커스 이탈) 시 디바이스에 반영한다."""
        if isinstance(self.device, UciSerialDevice):
            self.device.set_dest_mac(self.dest_mac_tf.value or "")

    def _on_target_label_commit(self, e: ft.ControlEvent) -> None:
        """타겟 라벨 입력이 바뀌면 표시 이름을 갱신한다."""
        self._target_labels = {}
        for field in self.target_fields:
            label = (field.value or "").strip()
            if not label:
                continue
            self._target_labels[label] = label

    def _on_refresh_ports(self, e: ft.ControlEvent) -> None:
        """포트 목록 재탐색."""
        ports = self.device.list_ports()
        self.port_dd.options = [ft.dropdown.Option(p) for p in ports]
        if self.port_dd.value not in ports:
            self.port_dd.value = ports[0] if ports else None
        self.page.update()

    def _connect(self) -> None:
        """표시 상태를 초기화하고 디바이스 수신을 시작한다."""
        if isinstance(self.device, SimulatorDevice):
            self.device.simulate_oob_timeout = self.fail_switch.value
        self.timeline.reset()
        self.panel.reset_values()
        self.radar.hide_point()
        self._meas_ts.clear()
        self._last_rx = None
        self._latest_targets.clear()
        port = self.port_dd.value or ""
        if not port:
            self.log.append_sys(
                "연결할 포트가 없습니다 — 케이블 연결 후 새로고침(↻)을 누르세요"
            )
            return
        baud = int(self.baud_dd.value or DEFAULT_BAUD)
        self.device.connect(port, baud)
        # 포트 점유 등으로 열기에 실패했을 수 있다 — 실제 연결됐을 때만 버튼 전환
        if self.device.is_connected():
            self._want_connected = True
            self._last_port = port
            self._last_baud = baud
            self._connected_at = time.time()
            self.connect_btn.text = "해제"
            self.log.append_sys(f"연결 시작: port={port}, baud={baud}")

    def _disconnect(self) -> None:
        """디바이스를 안전 종료하고 화면을 미연결 상태로 되돌린다."""
        self._want_connected = False  # 사용자가 원한 해제 — 자동 재연결 중단
        self.device.disconnect()
        self.connect_btn.text = "연결"
        self.radar.hide_point()
        self.log.append_sys("연결 해제됨")

    # --- 큐 펌프 (백그라운드 스레드에서 50ms 주기, 콜백은 큐에만 쌓임) ---

    async def pump_loop(self) -> None:
        """큐를 비워 화면에 반영하는 갱신 루프. 메인 UI 루프에서 즉시 갱신한다."""
        while self._running:
            dirty = self._drain_queue()
            dirty |= self._refresh_rate()
            dirty |= self._refresh_status()
            dirty |= self._try_reconnect()
            if dirty:
                self.page.update()
            await asyncio.sleep(PUMP_INTERVAL_S)

    def shutdown(self) -> None:
        """앱 종료: 펌프 중단 + 디바이스 안전 해제 (좀비 스레드 금지)."""
        self._running = False
        self.device.disconnect()

    def _drain_queue(self) -> bool:
        """콜백이 쌓아 둔 이벤트를 전부 꺼내 위젯에 반영한다."""
        dirty = False
        while True:
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "meas" and isinstance(data, Measurement):
                self._apply_measurement(data)
            elif kind == "state" and isinstance(data, DeviceEvent):
                self.timeline.apply_event(data)
                self.panel.set_session(data)
            elif kind == "log" and isinstance(data, str):
                # STATE 라인 등 모든 RX도 '수신'으로 취급 (워치독은 진짜 침묵만 잡는다)
                if not data.startswith(("TX", LINK_LOG_PREFIX)):
                    self._last_rx = time.time()
                self.log.append_rx(data)
            dirty = True
        return dirty

    def _apply_measurement(self, m: Measurement) -> None:
        """측정 1건을 레이더·수치 패널에 반영한다. 다중 타겟이면 최대 5개까지 동시 표시한다."""
        warn = is_out_of_range(m)
        target_key = m.target_id or "default"
        if m.dist_cm is not None and m.angle_deg is not None:
            self._latest_targets[target_key] = m
            self._trim_targets()
            points = [
                (
                    t.dist_cm if t.dist_cm is not None else 0,
                    t.angle_deg if t.angle_deg is not None else 0,
                    is_out_of_range(t),
                    self._target_label(t.target_id),
                )
                for t in list(self._latest_targets.values())
                if t.dist_cm is not None and t.angle_deg is not None
            ]
            if points:
                self.radar.update_points(points)
            else:
                self.radar.hide_point()
        else:
            self.radar.hide_point()
        self.panel.set_measurement(m, warn, self._target_label(m.target_id))
        self._last_rx = m.ts
        self._meas_ts.append(m.ts)

    def _trim_targets(self) -> None:
        """최대 타겟 수를 초과한 오래된 항목부터 정리한다."""
        if len(self._latest_targets) <= self._max_targets:
            return
        ordered = sorted(
            self._latest_targets.items(),
            key=lambda item: item[1].ts,
            reverse=True,
        )
        self._latest_targets = dict(ordered[: self._max_targets])

    def _target_label(self, target_id: Optional[str]) -> Optional[str]:
        """타겟 ID를 사용자 지정 라벨로 바꿔 화면에 표시한다."""
        if target_id is None:
            return None
        return self._target_labels.get(target_id, target_id)

    def _refresh_rate(self) -> bool:
        """최근 1초 창의 측정 건수 = 수신율(건/초)."""
        cutoff = time.time() - RATE_WINDOW_S
        while self._meas_ts and self._meas_ts[0] < cutoff:
            self._meas_ts.popleft()
        return self.panel.set_rate(len(self._meas_ts))

    def _refresh_status(self) -> bool:
        """정상/수신없음/끊김/미연결 상태를 판정해 LED·패널에 반영 (NFR-3·4)."""
        status = self._status_now()
        if status == self._status_cache:
            return False
        self._status_cache = status
        label, color = status
        if label == "수신없음":
            self.log.append_sys(f"경고: {RX_TIMEOUT_S:.0f}초 이상 수신 없음 (NFR-3)")
            self.radar.hide_point()  # 6.4: 수신없음이면 점 숨김 (수치는 마지막 값 유지)
        elif label == "끊김":
            self.log.append_sys("연결 끊김 감지 — 포트 복귀 시 자동 재연결 (NFR-4)")
        self.led.bgcolor = color
        self.led_label.value = label
        self.led_label.color = color
        self.panel.set_status(label, color)
        return True

    def _status_now(self) -> Tuple[str, str]:
        """현재 상태 판정: 미연결(회) / 끊김(적) / 수신없음(회) / 정상(녹)."""
        if not self._want_connected:
            return ("미연결", COLOR_IDLE)
        if not self.device.is_connected():
            return ("끊김", COLOR_ERR)
        ref = self._last_rx if self._last_rx is not None else self._connected_at
        if time.time() - ref > RX_TIMEOUT_S:
            return ("수신없음", COLOR_NO_RX)
        return ("정상", COLOR_OK)

    def _try_reconnect(self) -> bool:
        """끊김 상태에서 포트가 다시 보이면 재연결한다 (NFR-4 자동 재연결)."""
        if not self._want_connected or self.device.is_connected():
            return False
        now = time.time()
        if now < self._next_reconnect_ts:
            return False
        self._next_reconnect_ts = now + RECONNECT_INTERVAL_S
        if self._last_port not in self.device.list_ports():
            return False  # 포트 미복귀 — 다음 주기까지 조용히 대기
        self.log.append_sys(f"자동 재연결 시도: {self._last_port}")
        self.device.connect(self._last_port, self._last_baud)
        if self.device.is_connected():
            self._connected_at = now
            self.log.append_sys("자동 재연결 성공")
        return True


def main(page: ft.Page) -> None:
    """Flet 페이지 구성 + 펌프 스레드 시작."""
    logging.basicConfig(level=logging.INFO)
    page.title = "360° 레이더 테스트 콘솔 (DWM3001CDK)"
    page.theme_mode = ft.ThemeMode.DARK
    page.window.width = WINDOW_WIDTH
    page.window.height = WINDOW_HEIGHT
    page.padding = 16

    app = RadarApp(page)
    page.add(app.build())
    # 콜백은 큐에만 쌓이고, 메인 UI 루프의 태스크가 50ms마다 꺼내 그린다.
    page.run_task(app.pump_loop)
    page.on_disconnect = lambda e: app.shutdown()


if __name__ == "__main__":
    ft.app(main)
