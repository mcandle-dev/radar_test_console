"""디바이스 추상화 계층 — RadarDevice 인터페이스 + 시뮬레이터 구현 (코딩가이드 3·4장)."""

import logging
import random
import threading
import time
from abc import ABC, abstractmethod
from typing import Callable, List, Optional

import serial
from serial.tools import list_ports as serial_list_ports

from models import DeviceEvent, Measurement, SessionState
from parser import parse_line

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
    """

    def __init__(self, simulate_oob_timeout: bool = False) -> None:
        super().__init__()
        self.simulate_oob_timeout = simulate_oob_timeout
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
        """거리/각도 랜덤 워크를 10Hz로 생성해 콜백으로 내보낸다."""
        dist = SIM_DIST_INITIAL_CM
        angle = SIM_ANGLE_INITIAL_DEG
        while not self._stop_event.wait(SIM_MEASUREMENT_INTERVAL_S):
            if not self._ranging_enabled:
                continue
            dist = _clamped_walk(
                dist, SIM_DIST_STEP_CM, SIM_DIST_MIN_CM, SIM_DIST_MAX_CM
            )
            angle = _clamped_walk(
                angle, SIM_ANGLE_STEP_DEG, SIM_ANGLE_MIN_DEG, SIM_ANGLE_MAX_DEG
            )
            raw = f"DIST:{dist},ANGLE:{angle}"
            self.on_log(raw)
            self.on_measurement(
                Measurement(dist_cm=dist, angle_deg=angle, raw=raw, ts=time.time())
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
