"""디바이스 추상화 계층 — RadarDevice 인터페이스 + 시뮬레이터/CLI/UCI 구현 (코딩가이드 3·4장)."""

import logging
import random
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, List, Optional

import serial
from serial.tools import list_ports as serial_list_ports

import uci_params
from models import DeviceEvent, Measurement, SessionState
from parser import parse_line
from uci import (
    Client as UciClient,
    Gid,
    OidCore,
    OidRanging,
    OidSession,
    RangingData,
    RangingMeas,
    SessionState as UciSessionState,
    SessionStateChangeReason as UciStateReason,
    SessionType,
    Status as UciStatus,
    UciComError,
)

logger = logging.getLogger(__name__)

# 콜백 타입: 디바이스에 이벤트가 생기면 UI가 등록한 함수를 호출한다.
MeasurementCallback = Callable[[Measurement], None]
StateCallback = Callable[[DeviceEvent], None]
LogCallback = Callable[[str], None]

DEFAULT_BAUD = 115200
THREAD_JOIN_TIMEOUT_S = 2.0
SERIAL_READ_TIMEOUT_S = 1.0  # readline 블로킹 해제 주기 = 종료 플래그 확인 주기
LINK_LOG_PREFIX = "[LINK]"  # 연결 계층 오류 로그 표식 (UI가 색으로 구분)
CMD_START = "START"
CMD_STOP = "STOP"

# UCI(DW3_QM33 SDK UCI 펌웨어) 모드 — sasodoma 스크립트와 동일 조건
UCI_LOG_PREFIX = "[UCI]"  # UCI 프로토콜 정보 로그 표식 (UI가 색으로 구분)
UCI_DEFAULT_BAUD = 115200  # sasodoma UartTransport 기본값과 동일 (CLI 펌웨어와도 같음)
UCI_WORKER_JOIN_TIMEOUT_S = 6.0  # 명령 응답 타임아웃(4s)보다 길게

# 시뮬레이터 동작 파라미터 (코딩가이드 4.2)
SIM_PORT_NAME = "SIM"
SIM_SESSION_STEP_INTERVAL_S = 0.5  # 세션 단계 간 간격
SIM_MEASUREMENT_INTERVAL_S = 0.1  # 10Hz
SIM_DIST_INITIAL_CM = 200
SIM_DIST_MIN_CM = 20
SIM_DIST_MAX_CM = 300
SIM_DIST_STEP_CM = 10  # 랜덤 워크 1회 최대 변화량
SIM_ANGLE_INITIAL_DEG = 0
SIM_ANGLE_MIN_DEG = -90
SIM_ANGLE_MAX_DEG = 90
SIM_ANGLE_STEP_DEG = 5
SIM_OOB_TIMEOUT_REASON = "OOB_TIMEOUT"
SIM_NUM_TARGETS = 3  # 다중 타겟 UI 검증용 기본 생성 수
SIM_TARGET_DIST_SPACING_CM = 40  # 타겟별 초기 거리 간격
SIM_TARGET_ANGLE_SPREAD_DEG = 35  # 타겟별 초기 각도 간격
SIM_RSSI_INITIAL_DBM = -65
SIM_RSSI_MIN_DBM = -95
SIM_RSSI_MAX_DBM = -40
SIM_RSSI_STEP_DBM = 2  # 랜덤 워크 1회 최대 변화량


class RadarDevice(ABC):
    """Qorvo 단말 추상화. UI는 오직 이 인터페이스에만 의존한다.

    사용 흐름:
        dev = SimulatorDevice()        # 또는 QorvoSerialDevice()
        dev.on_measurement = ...       # 콜백 등록
        dev.connect("SIM")
        ...
        dev.disconnect()
    """

    def __init__(self) -> None:
        # UI가 채워 넣는 콜백 (기본은 아무것도 안 함).
        # 콜백은 백그라운드 스레드에서 호출되므로, UI는 큐에만 넣어야 한다.
        self.on_measurement: MeasurementCallback = lambda m: None
        self.on_state: StateCallback = lambda e: None
        self.on_log: LogCallback = lambda s: None

    @abstractmethod
    def connect(self, port: str, baud: int = DEFAULT_BAUD) -> None:
        """단말에 연결하고 백그라운드 수신을 시작한다."""

    @abstractmethod
    def disconnect(self) -> None:
        """수신 스레드를 안전하게 종료하고 연결을 닫는다."""

    @abstractmethod
    def is_connected(self) -> bool:
        """현재 연결 여부."""

    @abstractmethod
    def start_ranging(self) -> None:
        """ranging 시작 명령 (펌웨어 미지원 가능 — 실패해도 앱이 죽지 않게)."""

    @abstractmethod
    def stop_ranging(self) -> None:
        """ranging 정지 명령 (펌웨어 미지원 가능 — 실패해도 앱이 죽지 않게)."""

    @staticmethod
    @abstractmethod
    def list_ports() -> List[str]:
        """선택 가능한 포트 목록 (시뮬레이터는 ['SIM'] 반환)."""


class SimulatorDevice(RadarDevice):
    """보드 없이 가짜 거리/각도와 세션 상태를 만들어 내는 하네스.

    simulate_oob_timeout=True면 OOB 단계에서 STATE:ERR,REASON:OOB_TIMEOUT을
    재현해 UI의 에러 표시를 보드 없이 테스트할 수 있다.
    num_targets개의 타겟("1".."N")을 동시에 흘려 다중 타겟 UI를 검증한다.
    """

    def __init__(
        self,
        simulate_oob_timeout: bool = False,
        num_targets: int = SIM_NUM_TARGETS,
    ) -> None:
        super().__init__()
        self.simulate_oob_timeout = simulate_oob_timeout
        self._num_targets = max(1, num_targets)
        self._stop_event = threading.Event()
        self._ranging_enabled = True  # stop_ranging()으로 측정만 일시정지
        self._thread: threading.Thread | None = None

    def connect(self, port: str = SIM_PORT_NAME, baud: int = DEFAULT_BAUD) -> None:
        """시뮬레이션 스레드를 시작한다 (port/baud는 인터페이스 호환용)."""
        if self.is_connected():
            logger.warning("이미 연결됨 — connect 무시")
            return
        self._stop_event.clear()
        self._ranging_enabled = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("SimulatorDevice 연결됨 (port=%s)", port)

    def disconnect(self) -> None:
        """플래그로 스레드를 깨워 종료하고 join한다 (좀비 스레드 금지)."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=THREAD_JOIN_TIMEOUT_S)
            self._thread = None
        logger.info("SimulatorDevice 연결 해제됨")

    def is_connected(self) -> bool:
        """시뮬레이션 스레드가 살아 있으면 연결 상태로 본다."""
        return self._thread is not None and self._thread.is_alive()

    def start_ranging(self) -> None:
        """측정 생성 재개 (실물에선 START 명령에 해당)."""
        self._ranging_enabled = True
        self.on_log("TX START (simulated)")

    def stop_ranging(self) -> None:
        """측정 생성 일시정지 (실물에선 STOP 명령에 해당)."""
        self._ranging_enabled = False
        self.on_log("TX STOP (simulated)")

    @staticmethod
    def list_ports() -> List[str]:
        """시뮬레이터는 가상 포트 하나만 노출한다."""
        return [SIM_PORT_NAME]

    # --- 내부: 백그라운드 스레드 (UI 직접 갱신 금지, 콜백만 호출) ---

    def _run(self) -> None:
        """세션 핸드셰이크를 재현한 뒤 측정 루프로 진입한다."""
        if self._emit_session_flow():
            self._run_ranging_loop()

    def _emit_session_flow(self) -> bool:
        """BLE_ADV→BLE_CONN→OOB_DONE→RANGING을 0.5초 간격으로 흘린다.

        실패 토글이 켜져 있으면 OOB 단계에서 ERR을 내고 False 반환(측정 없음).
        """
        for state in (
            SessionState.BLE_ADV,
            SessionState.BLE_CONN,
            SessionState.OOB_DONE,
            SessionState.RANGING,
        ):
            if self._stop_event.wait(SIM_SESSION_STEP_INTERVAL_S):
                return False
            if self.simulate_oob_timeout and state == SessionState.OOB_DONE:
                self._emit_state(SessionState.ERR, SIM_OOB_TIMEOUT_REASON)
                return False
            self._emit_state(state, None)
        return True

    def _run_ranging_loop(self) -> None:
        """타겟별 거리/각도/RSSI 랜덤 워크를 10Hz로 생성해 콜백으로 내보낸다."""
        dists, angles, rssis = self._initial_targets()
        while not self._stop_event.wait(SIM_MEASUREMENT_INTERVAL_S):
            if not self._ranging_enabled:
                continue
            for tid in dists:
                dists[tid] = _clamped_walk(
                    dists[tid], SIM_DIST_STEP_CM, SIM_DIST_MIN_CM, SIM_DIST_MAX_CM
                )
                angles[tid] = _clamped_walk(
                    angles[tid],
                    SIM_ANGLE_STEP_DEG,
                    SIM_ANGLE_MIN_DEG,
                    SIM_ANGLE_MAX_DEG,
                )
                rssis[tid] = _clamped_walk(
                    rssis[tid], SIM_RSSI_STEP_DBM, SIM_RSSI_MIN_DBM, SIM_RSSI_MAX_DBM
                )
                self._emit_measurement(tid, dists[tid], angles[tid], rssis[tid])

    def _initial_targets(
        self,
    ) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
        """타겟 ID("1"..N)별 초기 거리/각도/RSSI를 서로 겹치지 않게 배치한다."""
        dists: dict[str, int] = {}
        angles: dict[str, int] = {}
        rssis: dict[str, int] = {}
        center = (self._num_targets - 1) / 2
        for i in range(self._num_targets):
            tid = str(i + 1)
            dists[tid] = SIM_DIST_INITIAL_CM + i * SIM_TARGET_DIST_SPACING_CM
            angles[tid] = int(
                SIM_ANGLE_INITIAL_DEG + (i - center) * SIM_TARGET_ANGLE_SPREAD_DEG
            )
            rssis[tid] = SIM_RSSI_INITIAL_DBM
        return dists, angles, rssis

    def _emit_measurement(self, tid: str, dist: int, angle: int, rssi: int) -> None:
        """타겟 1건 측정을 원시 라인 로그와 함께 콜백으로 내보낸다."""
        raw = f"DIST:{dist},ANGLE:{angle},TARGET:{tid},RSSI:{rssi}"
        self.on_log(raw)
        self.on_measurement(
            Measurement(
                dist_cm=dist,
                angle_deg=angle,
                raw=raw,
                ts=time.time(),
                target_id=tid,
                rssi_dbm=float(rssi),
            )
        )

    def _emit_state(self, state: SessionState, reason: str | None) -> None:
        """세션 이벤트를 원시 라인 로그와 함께 콜백으로 내보낸다."""
        raw = f"STATE:{state.value}" + (f",REASON:{reason}" if reason else "")
        self.on_log(raw)
        self.on_state(DeviceEvent(state=state, reason=reason, raw=raw, ts=time.time()))


def _clamped_walk(value: int, step: int, lo: int, hi: int) -> int:
    """랜덤 워크 한 스텝: ±step 내에서 움직이되 [lo, hi]로 클램프."""
    return max(lo, min(hi, value + random.randint(-step, step)))


class QorvoSerialDevice(RadarDevice):
    """실제 DWM3001CDK와 USB-UART로 통신하는 구현 (pyserial)."""

    def __init__(self) -> None:
        super().__init__()
        self._ser: Optional[serial.Serial] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def connect(self, port: str, baud: int = DEFAULT_BAUD) -> None:
        """포트를 열고 백그라운드 readline 루프를 시작한다. 실패해도 앱은 죽지 않는다."""
        if self.is_connected():
            logger.warning("이미 연결됨 — connect 무시")
            return
        try:
            self._ser = serial.Serial(port, baud, timeout=SERIAL_READ_TIMEOUT_S)
        except (serial.SerialException, OSError, ValueError) as e:
            msg = _friendly_open_error(port, e)
            logger.error("포트 열기 실패: %s (%s)", msg, e)
            self.on_log(f"{LINK_LOG_PREFIX} 연결 실패: {msg}")
            self._ser = None
            return
        self._running = True
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()
        logger.info("QorvoSerialDevice 연결됨 (port=%s, baud=%d)", port, baud)

    def disconnect(self) -> None:
        """플래그로 루프를 멈추고 join 후 포트를 닫는다 (좀비 스레드 금지)."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=THREAD_JOIN_TIMEOUT_S)
            self._thread = None
        if self._ser is not None:
            try:
                self._ser.close()
            except (serial.SerialException, OSError) as e:
                logger.warning("포트 닫기 중 오류 (이미 분리됐을 수 있음): %s", e)
            self._ser = None
        logger.info("QorvoSerialDevice 연결 해제됨")

    def is_connected(self) -> bool:
        """수신 루프가 살아 있으면 연결 상태로 본다 (케이블 분리 시 루프가 죽어 False)."""
        return self._running and self._thread is not None and self._thread.is_alive()

    def start_ranging(self) -> None:
        """START 명령 전송 (펌웨어 미지원 가능 — 실패해도 no-op + 로그)."""
        self._send_command(CMD_START)

    def stop_ranging(self) -> None:
        """STOP 명령 전송 (펌웨어 미지원 가능 — 실패해도 no-op + 로그)."""
        self._send_command(CMD_STOP)

    @staticmethod
    def list_ports() -> List[str]:
        """PC에 연결된 시리얼 포트를 자동 탐색한다 (예: ['COM3', 'COM7'])."""
        return sorted(p.device for p in serial_list_ports.comports())

    # --- 내부: 백그라운드 스레드 (UI 직접 갱신 금지, 콜백만 호출) ---

    def _reader(self) -> None:
        """한 줄 읽어 파싱 → 콜백 호출. 시리얼 오류(케이블 분리 등) 시 루프 종료."""
        while self._running and self._ser is not None:
            try:
                line = self._ser.readline().decode("utf-8", errors="replace").strip()
            except (serial.SerialException, OSError) as e:
                # 케이블 분리·포트 소멸 — 앱을 죽이지 않고 루프만 끝낸다 (NFR-5)
                logger.error("시리얼 수신 오류: %s", e)
                self.on_log(f"{LINK_LOG_PREFIX} 수신 오류 (케이블 분리?): {e}")
                self._running = False
                return
            if not line:  # timeout 주기 도래 — 종료 플래그만 확인하고 계속
                continue
            self._route_line(line)

    def _route_line(self, line: str) -> None:
        """원시 라인을 로그로 흘리고, 파싱 결과를 종류별 콜백으로 보낸다."""
        self.on_log(line)
        result = parse_line(line)
        if result.kind == "measurement" and result.measurement is not None:
            self.on_measurement(result.measurement)
        elif result.kind == "state" and result.event is not None:
            self.on_state(result.event)
        # invalid는 on_log만으로 충분 — UI가 원문+사유를 색으로 표시한다

    def _send_command(self, cmd: str) -> None:
        """텍스트 명령 한 줄 전송. 실패는 경고 로그만 (앱 무중단)."""
        if not self.is_connected() or self._ser is None:
            self.on_log(f"{LINK_LOG_PREFIX} 미연결 상태 — {cmd} 전송 안 함")
            return
        try:
            self._ser.write(f"{cmd}\n".encode("ascii"))
            self.on_log(f"TX {cmd}")
        except (serial.SerialException, OSError) as e:
            logger.warning("%s 전송 실패 (펌웨어 미지원 가능): %s", cmd, e)
            self.on_log(f"{LINK_LOG_PREFIX} {cmd} 전송 실패: {e}")


class UciSerialDevice(RadarDevice):
    """DWM3001CDK UCI 펌웨어(DW3_QM33 SDK)를 UCI 호스트로 직접 구동하는 구현.

    역할: 보드 = controller/initiator, 폰(uwb_controlee_app) = controlee/responder.
    폰 앱을 먼저 Start시킨 뒤 start_ranging()을 호출해야 한다.
    세션 파라미터는 uci_params.py 한 파일에 고정 (폰과 바이트 단위 일치 필수).
    폰 주소를 쉼표로 여러 개 주면 one-to-many(multicast) 세션으로 시작한다.
    """

    def __init__(self, dest_mac: str = uci_params.DEFAULT_DEST_MAC) -> None:
        super().__init__()
        self._client: Any = None  # uci.Client (동적 합성 클래스라 Any)
        self._transport: Any = None  # Client는 weakref만 들므로 강한 참조를 직접 유지
        self._dest_mac = dest_mac
        self._session_handle: Optional[int] = None
        self._cmd_lock = threading.Lock()  # UCI 명령/응답은 한 번에 하나만
        self._worker: Optional[threading.Thread] = None

    # --- 연결 관리 ---

    def connect(self, port: str, baud: int = UCI_DEFAULT_BAUD) -> None:
        """UCI 트랜스포트(시리얼)를 열고 NTF 수신을 시작한다. 실패해도 앱은 죽지 않는다."""
        if self.is_connected():
            logger.warning("이미 연결됨 — connect 무시")
            return
        try:
            self._client = UciClient(
                port=port, baudrate=baud, notif_handlers=self._notif_handlers()
            )
            self._transport = self._client.transport()
        except (UciComError, serial.SerialException, OSError, ValueError) as e:
            msg = _friendly_open_error(port, e)
            logger.error("UCI 포트 열기 실패: %s (%s)", msg, e)
            self.on_log(f"{LINK_LOG_PREFIX} 연결 실패: {msg}")
            self._client = None
            self._transport = None
            return
        logger.info("UciSerialDevice 연결됨 (port=%s, baud=%d)", port, baud)
        self.on_log(f"{UCI_LOG_PREFIX} UCI 트랜스포트 열림 (port={port}, baud={baud})")

    def disconnect(self) -> None:
        """세션을 정리(좀비 세션 금지)하고 트랜스포트를 닫는다."""
        if self._worker is not None:
            self._worker.join(timeout=UCI_WORKER_JOIN_TIMEOUT_S)
            self._worker = None
        if self._session_handle is not None and self.is_connected():
            self._do_stop()  # 연결 해제 전 ranging_stop + session_deinit
        if self._client is not None:
            try:
                self._client.close()
            except (UciComError, serial.SerialException, OSError) as e:
                logger.warning("UCI 트랜스포트 닫기 중 오류: %s", e)
            self._client = None
            self._transport = None
        logger.info("UciSerialDevice 연결 해제됨")

    def is_connected(self) -> bool:
        """트랜스포트 수신 스레드가 살아 있으면 연결 상태로 본다."""
        return self._transport is not None and bool(self._transport.is_alive())

    @staticmethod
    def list_ports() -> List[str]:
        """PC에 연결된 시리얼 포트를 자동 탐색한다 (CLI 구현과 동일)."""
        return QorvoSerialDevice.list_ports()

    # --- 세션 제어 (명령 응답 대기가 있어 워커 스레드에서 수행 — UI 무정지) ---

    def set_dest_mac(self, mac: str) -> bool:
        """폰 주소('XX:XX', 쉼표로 복수 가능)를 갱신한다. 오류면 False + 로그 (기존 값 유지)."""
        try:
            macs = uci_params.parse_dest_macs(mac)
        except ValueError as e:
            self.on_log(f"{UCI_LOG_PREFIX} 폰 주소 무시: {e}")
            return False
        self._dest_mac = mac.strip()
        mode = "unicast" if len(macs) == 1 else f"multicast({len(macs)})"
        self.on_log(f"{UCI_LOG_PREFIX} 폰 주소(DST_MAC) = {self._dest_mac} [{mode}]")
        return True

    def start_ranging(self) -> None:
        """session_init → set_app_config → ranging_start (run_fira_twr.py 흐름)."""
        self._spawn_worker(self._do_start)

    def stop_ranging(self) -> None:
        """ranging_stop → session_deinit."""
        self._spawn_worker(self._do_stop)

    def _spawn_worker(self, target: Callable[[], None]) -> None:
        """세션 명령 시퀀스를 백그라운드에서 실행한다 (중복 실행 방지)."""
        if not self.is_connected():
            self.on_log(f"{LINK_LOG_PREFIX} 미연결 상태 — UCI 명령 전송 안 함")
            return
        if self._worker is not None and self._worker.is_alive():
            self.on_log(f"{UCI_LOG_PREFIX} 이전 명령 처리 중 — 무시")
            return
        self._worker = threading.Thread(target=target, daemon=True)
        self._worker.start()

    def _do_start(self) -> None:
        """세션 시작 시퀀스. 각 단계 실패 시 ERR 이벤트 + 세션 정리."""
        try:
            dest_macs_uci = uci_params.parse_dest_macs(self._dest_mac)
        except ValueError as e:
            self._emit_err("DEST_MAC_INVALID", str(e))
            return
        with self._cmd_lock:
            try:
                handle = self._session_init()
                if handle is None:
                    return
                if not self._session_configure(handle, dest_macs_uci):
                    return
                self._ranging_start(handle)
            except UciComError as e:
                self._emit_err("UCI_COM", str(e))

    def _session_init(self) -> Optional[int]:
        """SESSION_INIT. 성공 시 세션 핸들(Fira 1.3이면 세션 ID) 반환."""
        sid = uci_params.SESSION_ID
        self.on_log(f"TX {UCI_LOG_PREFIX} SESSION_INIT (id={sid})")
        status, handle = self._client.session_init(sid, SessionType.Ranging)
        if status != UciStatus.Ok:
            self._emit_err("SESSION_INIT", _uci_status_name(status))
            return None
        return int(handle) if handle is not None else sid

    def _session_configure(self, handle: int, dest_macs_uci: List[int]) -> bool:
        """SESSION_SET_APP_CONFIG — 파라미터는 uci_params.build_app_configs()."""
        mode = (
            "unicast" if len(dest_macs_uci) == 1 else f"multicast({len(dest_macs_uci)})"
        )
        self.on_log(
            f"TX {UCI_LOG_PREFIX} SET_APP_CONFIG (dest_mac={self._dest_mac} [{mode}], "
            f"ch={uci_params.CHANNEL_NUMBER}, interval={uci_params.RANGING_DURATION}ms)"
        )
        configs = uci_params.build_app_configs(dest_macs_uci)
        status, msg = self._client.session_set_app_config(handle, configs)
        if status != UciStatus.Ok:
            self._emit_err(
                "SET_APP_CONFIG", f"{_uci_status_name(status)} {msg}".strip()
            )
            self._session_deinit(handle)
            return False
        return True

    def _ranging_start(self, handle: int) -> None:
        """RANGE_START. 성공하면 RANGING 상태 이벤트를 올린다."""
        self.on_log(f"TX {UCI_LOG_PREFIX} RANGE_START")
        status = self._client.ranging_start(handle)
        if status != UciStatus.Ok:
            self._emit_err("RANGE_START", _uci_status_name(status))
            self._session_deinit(handle)
            return
        self._session_handle = handle
        self._emit_state(SessionState.RANGING, None, f"{UCI_LOG_PREFIX} ranging 시작")

    def _do_stop(self) -> None:
        """세션 정지 시퀀스. 세션이 없으면 no-op + 로그 (앱 무중단)."""
        with self._cmd_lock:
            handle = self._session_handle
            if handle is None:
                self.on_log(f"{UCI_LOG_PREFIX} 활성 세션 없음 — STOP 무시")
                return
            try:
                self.on_log(f"TX {UCI_LOG_PREFIX} RANGE_STOP")
                status = self._client.ranging_stop(handle)
                if status != UciStatus.Ok:
                    self.on_log(
                        f"{UCI_LOG_PREFIX} RANGE_STOP 실패: {_uci_status_name(status)}"
                    )
                self._session_deinit(handle)
            except UciComError as e:
                self._emit_err("UCI_COM", str(e))
            finally:
                self._session_handle = None

    def _session_deinit(self, handle: int) -> None:
        """SESSION_DEINIT — 실패해도 로그만 남기고 진행 (좀비 세션 방지 최선 노력)."""
        try:
            self.on_log(f"TX {UCI_LOG_PREFIX} SESSION_DEINIT")
            status = self._client.session_deinit(handle)
            if status != UciStatus.Ok:
                self.on_log(
                    f"{UCI_LOG_PREFIX} SESSION_DEINIT 실패: {_uci_status_name(status)}"
                )
        except UciComError as e:
            self.on_log(f"{UCI_LOG_PREFIX} SESSION_DEINIT 통신 오류: {e}")
        self._session_handle = None

    # --- NTF 수신 (uci 트랜스포트의 ReaderThread에서 호출 — 콜백만, UI 접근 금지) ---

    def _notif_handlers(self) -> dict:
        """UCI NTF → 기존 콜백 체계로 잇는 핸들러 테이블."""
        return {
            (Gid.Ranging, OidRanging.Start): self._on_range_data_ntf,
            (Gid.Session, OidSession.Status): self._on_session_status_ntf,
            (Gid.Core, OidCore.DeviceStatus): self._on_device_status_ntf,
            ("default", "default"): self._on_unknown_ntf,
        }

    def _on_range_data_ntf(self, payload: bytes) -> None:
        """SESSION_INFO_NTF(RANGE_DATA) → 거리(cm)를 on_measurement로 전달."""
        try:
            data = RangingData(bytes(payload))
        except (ValueError, IndexError) as e:
            self.on_log(f"{UCI_LOG_PREFIX} RANGE_DATA 디코드 실패: {e}")
            return
        if data.ranging_meas != RangingMeas.Twr:
            self.on_log(f"{UCI_LOG_PREFIX} TWR 아닌 측정 무시: {data.ranging_meas!r}")
            return
        for meas in data.meas:
            self._emit_twr_measurement(meas)

    def _emit_twr_measurement(self, meas: Any) -> None:
        """TWR 측정 1건 처리. UCI 거리는 이미 cm. 각도는 항상 None(안테나 1개 → 'N/A')."""
        if meas.status != UciStatus.Ok:
            self.on_log(f"{UCI_LOG_PREFIX} 측정 실패: {_uci_status_name(meas.status)}")
            return
        dist_cm = int(meas.distance)
        raw = f"DIST:{dist_cm}"  # 기존 데이터 계약과 같은 표기 → 로그 콘솔이 ✔로 분류
        target_id = _normalize_target_id(getattr(meas, "mac_add", None))
        rssi_dbm = _normalize_rssi(getattr(meas, "rssi", None))
        self.on_log(raw)
        self.on_measurement(
            Measurement(
                dist_cm=dist_cm,
                angle_deg=None,
                raw=raw,
                ts=time.time(),
                target_id=target_id,
                rssi_dbm=rssi_dbm,
            )
        )

    def _on_session_status_ntf(self, payload: bytes) -> None:
        """SESSION_STATUS_NTF → Active=RANGING, 오류 사유 동반 시 ERR."""
        state = UciSessionState(int.from_bytes(payload[4:5], "little"))
        reason = UciStateReason(int.from_bytes(payload[5:6], "little"))
        raw = f"{UCI_LOG_PREFIX} 세션 상태: {state.name} (사유: {reason.name})"
        self.on_log(raw)
        if state == UciSessionState.Active:
            self._emit_state(SessionState.RANGING, None, raw)
        elif reason != UciStateReason.StateChangeWithSessionManagementCommands:
            # 명령에 의한 정상 전이가 아닌 Idle/DeInit = 펌웨어가 세션을 내린 것
            self._emit_state(SessionState.ERR, reason.name, raw)

    def _on_device_status_ntf(self, payload: bytes) -> None:
        """CORE_DEVICE_STATUS_NTF — 진단용 로그만 남긴다."""
        state = int.from_bytes(payload[0:1], "little")
        self.on_log(f"{UCI_LOG_PREFIX} 디바이스 상태 NTF: {state:#x}")

    def _on_unknown_ntf(self, gid: int, oid: int, payload: bytes) -> None:
        """알 수 없는 NTF는 무시하지 않고 원문 hex를 로그로 남긴다 (전방 호환)."""
        self.on_log(
            f"{UCI_LOG_PREFIX} NTF gid={gid} oid={oid}: {bytes(payload).hex('.')}"
        )

    # --- 공통 헬퍼 ---

    def _emit_state(self, state: SessionState, reason: Optional[str], raw: str) -> None:
        """세션 상태 이벤트를 기존 상태 콜백으로 올린다."""
        self.on_state(DeviceEvent(state=state, reason=reason, raw=raw, ts=time.time()))

    def _emit_err(self, step: str, detail: str) -> None:
        """실패를 로그 + ERR 이벤트로 알린다 (앱은 죽지 않는다)."""
        msg = f"{UCI_LOG_PREFIX} {step} 실패: {detail}"
        logger.error(msg)
        self.on_log(msg)
        self._emit_state(SessionState.ERR, step, msg)


def _uci_status_name(status: Any) -> str:
    """UCI Status enum을 'Name(0x..)' 표기로 바꾼다 (로그용)."""
    return f"{status.name}({int(status):#x})"


def _normalize_target_id(value: Any) -> Optional[str]:
    """UCI의 MAC 주소 문자열을 화면용 식별자 형태로 정규화한다."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text or text == "na":
        return None
    parts = [part for part in text.split(":") if part]
    if len(parts) == 2:
        return ":".join(reversed(parts))
    return text


def _normalize_rssi(value: Any) -> Optional[float]:
    """UCI rssi 필드를 화면용 dBm으로 정규화한다. 0은 '미지원/측정 없음' 표식이라 None 처리."""
    if value is None:
        return None
    rssi = float(value)
    if rssi == 0.0:
        return None
    return round(rssi, 1)


def _friendly_open_error(port: str, e: Exception) -> str:
    """포트 열기 실패 원인을 사람이 읽을 수 있는 문장으로 바꾼다."""
    text = str(e)
    if (
        "PermissionError" in text
        or "Access is denied" in text
        or "액세스가 거부" in text
    ):
        return f"{port} 포트를 다른 프로그램이 사용 중입니다 (TeraTerm/putty 등을 닫고 재시도)"
    if "FileNotFoundError" in text or "could not open port" in text.lower():
        return f"{port} 포트를 찾을 수 없습니다 (케이블 연결과 포트 이름 확인)"
    return f"{port} 열기 실패: {text}"
