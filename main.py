"""UI 진입점 — 연결바/세션 타임라인/레이더/수치 패널/로그 콘솔 (요구사항정의서 6.1).

주의: UI는 RadarDevice 인터페이스에만 의존한다. 이 파일에 import serial 금지.
"""

import asyncio
import logging
import queue
import time
from collections import deque
from pathlib import Path
from typing import Deque, Dict, NamedTuple, Optional, Sequence, Tuple

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
from radar_view import RadarTarget, RadarView

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
PANEL_WIDTH = 470
LOG_HEIGHT = 220

MAX_TARGETS = 5  # 동시 표시 타겟 수 (uci_params.MAX_CONTROLEES와 동일)
# 타겟별 고정 팔레트 — 테이블 ● 표시와 레이더 점/링이 같은 색을 쓴다
TARGET_COLORS = ("#00E676", "#40C4FF", "#FF80AB", "#B388FF", "#FFEA00")
DEFAULT_TARGET_LABEL = "타겟"  # target_id가 없는 단일 타겟 표시명

# 타겟 테이블: 행 상태 표기 + 컬럼 폭(px)
TARGET_STATUS_EMPTY = "—"  # 빈 슬롯
TARGET_STATUS_WAIT = "대기"  # 주소는 등록됐지만 아직 측정 없음
TARGET_STATUS_OK = "정상"
TARGET_STATUS_NO_RX = "수신없음"  # 2초 이상 무수신 (타겟별)
TABLE_FONT_SIZE = 12
COL_TARGET_W = 92
COL_DIST_W = 60
COL_ANGLE_W = 46
COL_RATE_W = 62
COL_LAST_RX_W = 64
COL_STATUS_W = 56

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
COLOR_PRIMARY_TARGET = ft.Colors.RED_ACCENT_400  # 최근접·최대RSSI 타겟 강조색

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


class TargetRow(NamedTuple):
    """타겟 테이블 한 행의 표시 스냅숏 (튜플 비교로 변경 감지)."""

    label: str
    dist_cm: Optional[int]
    angle_deg: Optional[int]
    rssi_dbm: Optional[float]
    warn: bool
    rate: int
    last_rx: str
    stale: bool
    color: str


class TargetPanel(ft.Container):
    """타겟별 거리·각도·수신율 행 + 공통 상태(전체 수신율/세션/상태) 패널."""

    def __init__(self) -> None:
        self._empty = ft.Text("측정 대기 중 — 타겟 없음", color=COLOR_TEXT_DIM, size=12)
        self._rows = ft.Column(controls=[self._empty], spacing=10, tight=True)
        self._rate = ft.Text("0 건/초")
        self._last_rx = ft.Text("—")
        self._session = ft.Text("N/A")
        self._status = ft.Text("미연결", color=COLOR_IDLE)
        self._rate_value = 0
        self._snapshot: Tuple[TargetRow, ...] = ()
        super().__init__(
            width=PANEL_WIDTH,
            padding=16,
            border_radius=12,
            bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.WHITE),
            content=ft.Column(
                controls=[
                    ft.Text("타겟", weight=ft.FontWeight.BOLD),
                    self._rows,
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

    def update_targets(self, rows: Sequence[TargetRow]) -> bool:
        """타겟 행들을 갱신한다. 내용이 바뀌었을 때만 True (불필요한 update 방지)."""
        snap = tuple(rows)
        if snap == self._snapshot:
            return False
        self._snapshot = snap
        self._rows.controls = [self._build_row(r) for r in snap] or [self._empty]
        return True

    @staticmethod
    def _build_row(r: TargetRow) -> ft.Control:
        """타겟 1행 = [● 라벨 … 거리] + [각도·수신율·최종수신] 두 줄."""
        name_color = COLOR_TEXT_DIM if r.stale else ft.Colors.WHITE
        dist_color = (
            COLOR_TEXT_DIM if r.stale else (COLOR_WARN if r.warn else ft.Colors.WHITE)
        )
        dot = ft.Container(
            width=10,
            height=10,
            border_radius=5,
            bgcolor=COLOR_IDLE if r.stale else r.color,
        )
        dist_text = "—" if r.dist_cm is None else f"{r.dist_cm} cm"
        angle_text = "N/A" if r.angle_deg is None else f"{r.angle_deg}°"
        rssi_text = "N/A" if r.rssi_dbm is None else f"{r.rssi_dbm:g} dBm"
        detail = f"각도 {angle_text} · RSSI {rssi_text} · {r.rate} 건/초 · {r.last_rx}"
        if r.stale:
            detail += " · 수신없음"
        name = ft.Text(r.label, size=13, weight=ft.FontWeight.BOLD, color=name_color)
        dist = ft.Text(dist_text, size=16, weight=ft.FontWeight.BOLD, color=dist_color)
        head = ft.Row(
            controls=[dot, name, ft.Container(expand=True), dist],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        return ft.Column(
            controls=[head, ft.Text(detail, size=11, color=COLOR_TEXT_DIM)],
            spacing=2,
            tight=True,
        )

    def set_rate(self, count: int) -> bool:
        """전체 수신율 표시. 값이 바뀌었을 때만 True (불필요한 page.update 방지)."""
        if count == self._rate_value:
            return False
        self._rate_value = count
        self._rate.value = f"{count} 건/초"
        return True

    def set_last_rx(self, ts: float) -> None:
        """마지막 수신 시각(전체 기준)을 갱신한다."""
        self._last_rx.value = time.strftime("%H:%M:%S", time.localtime(ts))

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
        """미연결 상태 표시로 되돌린다 (타겟 행 비움)."""
        self._snapshot = ()
        self._rows.controls = [self._empty]
        self._last_rx.value = "—"
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


def select_primary_target(
    targets: Dict[str, Measurement], now: float, timeout_s: float
) -> Optional[str]:
    """대표 타겟 키를 고른다: 거리 최소 우선, 동률이면 RSSI 최대 우선.

    거리 미확보(dist_cm=None) 또는 무수신 타임아웃을 넘긴 타겟은 후보에서 제외한다.
    """
    candidates = [
        (key, m)
        for key, m in targets.items()
        if m.dist_cm is not None and (now - m.ts) <= timeout_s
    ]
    if not candidates:
        return None

    def rank(item: Tuple[str, Measurement]) -> Tuple[int, float]:
        _, m = item
        rssi = m.rssi_dbm if m.rssi_dbm is not None else float("-inf")
        return (m.dist_cm, -rssi)  # 거리 오름차순, 동률이면 RSSI 내림차순

    return min(candidates, key=rank)[0]


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
        self.panel = TargetPanel()
        self.log = LogConsole()
        self._build_connection_bar()

        self._meas_ts: Deque[float] = deque()
        self._last_rx: Optional[float] = None
        self._status_cache: Tuple[str, str] = ("", "")
        # 타겟별 상태: 최신 측정 / 수신율 창 / 배정 색 / 레이더 스냅숏(변경 감지)
        self._latest_targets: dict[str, Measurement] = {}
        self._target_ts: dict[str, Deque[float]] = {}
        self._target_colors: dict[str, str] = {}
        self._radar_snapshot: Tuple[RadarTarget, ...] = ()
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
        # 쉼표로 여러 개 입력하면 one-to-many(multicast) 세션이 된다
        self.dest_mac_tf = ft.TextField(
            label="폰 주소",
            value=uci_params.DEFAULT_DEST_MAC,
            width=220,
            tooltip=(
                "폰 앱 화면의 '내 주소' (XX:XX hex) — Enter로 반영. "
                f"쉼표로 최대 {uci_params.MAX_CONTROLEES}개 (예: 5F:DD, A1:B2)"
            ),
            visible=isinstance(self.device, UciSerialDevice),
            on_submit=self._on_dest_mac_commit,
            on_blur=self._on_dest_mac_commit,
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
        self._reset_targets()
        self._meas_ts.clear()
        self._last_rx = None
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
        self._reset_targets()
        self.panel.reset_values()
        self.log.append_sys("연결 해제됨")

    def _reset_targets(self) -> None:
        """타겟별 상태와 레이더 표시를 모두 비운다 (연결 시작/해제 시)."""
        self._latest_targets.clear()
        self._target_ts.clear()
        self._target_colors.clear()
        self._radar_snapshot = ()
        self.radar.hide_point()

    # --- 큐 펌프 (백그라운드 스레드에서 50ms 주기, 콜백은 큐에만 쌓임) ---

    async def pump_loop(self) -> None:
        """큐를 비워 화면에 반영하는 갱신 루프. 메인 UI 루프에서 즉시 갱신한다."""
        while self._running:
            dirty = self._drain_queue()
            dirty |= self._refresh_targets()
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
        """측정 1건을 타겟별 최신값·수신율 창에 반영한다 (그리기는 _refresh_targets)."""
        key = m.target_id or "default"
        self._latest_targets[key] = m  # 기존 키 재대입은 삽입 순서 유지 → 행 순서 안정
        self._target_ts.setdefault(key, deque()).append(m.ts)
        self._assign_color(key)
        self._trim_targets()
        self.panel.set_last_rx(m.ts)
        self._last_rx = m.ts
        self._meas_ts.append(m.ts)

    def _trim_targets(self) -> None:
        """최대 타겟 수 초과 시 가장 오래 안 보인 타겟부터 제거한다 (행 순서 유지)."""
        overflow = len(self._latest_targets) - MAX_TARGETS
        if overflow <= 0:
            return
        oldest = sorted(self._latest_targets, key=lambda k: self._latest_targets[k].ts)[
            :overflow
        ]
        for key in oldest:
            del self._latest_targets[key]
            self._target_ts.pop(key, None)
            self._target_colors.pop(key, None)

    def _assign_color(self, key: str) -> None:
        """새 타겟에 팔레트 색을 배정한다 (제거된 타겟의 색은 재사용)."""
        if key in self._target_colors:
            return
        used = set(self._target_colors.values())
        free = [c for c in TARGET_COLORS if c not in used]
        self._target_colors[key] = free[0] if free else TARGET_COLORS[0]

    def _target_rate(self, key: str, now: float) -> int:
        """타겟별 최근 1초 창의 측정 건수."""
        ts = self._target_ts.get(key)
        if ts is None:
            return 0
        cutoff = now - RATE_WINDOW_S
        while ts and ts[0] < cutoff:
            ts.popleft()
        return len(ts)

    def _refresh_targets(self) -> bool:
        """타겟 테이블·레이더를 최신 스냅숏으로 갱신한다. 변경이 있으면 True."""
        now = time.time()
        primary_key = self._primary_target_key(now)
        rows: list[TargetRow] = []
        radar_targets: list[RadarTarget] = []
        for key, m in self._latest_targets.items():
            row, radar = self._snapshot_target(key, m, now, key == primary_key)
            rows.append(row)
            if radar is not None:
                radar_targets.append(radar)
        dirty = self.panel.update_targets(rows)
        snap = tuple(radar_targets)
        if snap != self._radar_snapshot:
            self._radar_snapshot = snap
            self.radar.update_points(list(snap))
            dirty = True
        return dirty

    def _primary_target_key(self, now: float) -> Optional[str]:
        """최근접(거리 최소) 타겟을 고르고, 거리가 같으면 RSSI가 큰 쪽을 우선한다."""
        return select_primary_target(self._latest_targets, now, RX_TIMEOUT_S)

    def _snapshot_target(
        self, key: str, m: Measurement, now: float, is_primary: bool
    ) -> Tuple[TargetRow, Optional[RadarTarget]]:
        """타겟 1개의 테이블 행과 레이더 표시(없으면 None) 스냅숏을 만든다."""
        stale = (now - m.ts) > RX_TIMEOUT_S  # 타겟별 수신없음 → 회색 행 + 레이더 제외
        warn = is_out_of_range(m)
        color = (
            COLOR_PRIMARY_TARGET
            if is_primary
            else self._target_colors.get(key, TARGET_COLORS[0])
        )
        label = key if m.target_id else DEFAULT_TARGET_LABEL
        row = TargetRow(
            label=label,
            dist_cm=m.dist_cm,
            angle_deg=m.angle_deg,
            rssi_dbm=m.rssi_dbm,
            warn=warn,
            rate=self._target_rate(key, now),
            last_rx=time.strftime("%H:%M:%S", time.localtime(m.ts)),
            stale=stale,
            color=color,
        )
        radar: Optional[RadarTarget] = None
        if not stale and m.dist_cm is not None:
            radar = RadarTarget(m.dist_cm, m.angle_deg, warn, label, color)
        return row, radar

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
            # 6.4: 점 숨김은 _refresh_targets의 타겟별 stale 처리로 수행 (같은 2초 기준)
            self.log.append_sys(f"경고: {RX_TIMEOUT_S:.0f}초 이상 수신 없음 (NFR-3)")
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
