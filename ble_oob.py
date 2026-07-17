"""BLE OOB 클라이언트 추상화 — BleOobClient(ABC) + SimulatorOobClient (변경요구서 §2).

RadarDevice 패턴과 동일: UI는 이 인터페이스에만 의존하고, 콜백은 백그라운드
스레드에서 오므로 큐에만 넣는다. UI 코드에 `import bleak` 절대 금지 (CLAUDE.md).
BLE 끊김은 UWB 세션과 무관해야 한다 — 이 계층은 UCI 레인징을 절대 건드리지 않는다.
"""

import asyncio
import logging
import random
import threading
import time
from abc import ABC, abstractmethod
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple

from models import DeviceEvent, SessionState
from oob_params import (
    ADV_LOCAL_NAME,
    CONNECT_TIMEOUT_S,
    OOB_INFO_CHAR_UUID,
    PAYLOAD_MIN_LEN,
    SCAN_TIMEOUT_S,
    SERVICE_UUID,
)
from oob_parser import OobParseResult, build_oob_info, parse_oob_info

# bleak 미설치·미지원 환경에서도 수동/시뮬 경로는 살아 있어야 한다 (OOB는 부가 경로).
try:
    from bleak import BleakClient, BleakScanner
    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData
    from bleak.exc import BleakError

    _BLEAK_IMPORT_ERROR: Optional[str] = None
except ImportError as exc:  # pragma: no cover - bleak는 requirements.txt에 고정
    _BLEAK_IMPORT_ERROR = str(exc)

logger = logging.getLogger(__name__)

OOB_LOG_PREFIX = "[OOB]"  # OOB 계층 로그 표식 (UI가 색으로 구분)

# ERR 이벤트 REASON 값 (사양서 §7)
REASON_BLE_CONN_FAIL = "BLE_CONN_FAIL"
REASON_OOB_PARSE = "OOB_PARSE"

# 연결 해제 사유 (UI 로그 표기 — 사용자 조작인지 상대 종료인지 구분)
DISCONNECT_USER = "사용자 요청"
DISCONNECT_PEER = "상대 기기 연결 종료"

BLE_THREAD_NAME = "ble-oob-loop"
BLE_THREAD_JOIN_S = 3.0  # close() 시 루프 스레드 종료 대기 (좀비 스레드 금지)

# 시뮬레이터 동작 파라미터 (실물 BLE의 체감 지연을 흉내 — 테스트는 delay_s로 단축)
SIM_SCAN_DELAY_S = 0.5
SIM_CONNECT_DELAY_S = 0.3
SIM_READ_DELAY_S = 0.2
SIM_DEFAULT_ADDRESSES = ["5F:DD"]  # 사양서 §4 예시 주소
SIM_DEFAULT_SESSION_ID = 42  # 폰 앱 기본 Session ID (uci_params.SESSION_ID와 쌍)
SIM_RSSI_DBM = -55
SIM_DEVICE_ID_PREFIX = "SIM-OOB-"


@dataclass
class OobPeripheral:
    """스캔에서 발견된 OOB 광고 기기 1대 (UI 목록 표시용)."""

    device_id: str  # BLE 주소(실물) 또는 시뮬 식별자 — connect()에 그대로 넘긴다
    name: str  # 광고 Local Name (예: "UWB-OOB")
    rssi: int  # dBm — 스캔 캐시의 죽은 광고 구분 보조 (변경요구서 §5)
    ts: float  # 발견 시각(epoch) — 위와 동일 목적


# 콜백 타입: 모두 백그라운드 스레드에서 호출된다 → UI는 큐에만 넣을 것.
ScanResultCallback = Callable[[List[OobPeripheral]], None]
OobInfoCallback = Callable[[OobParseResult], None]
StateCallback = Callable[[DeviceEvent], None]
DisconnectCallback = Callable[[str], None]
LogCallback = Callable[[str], None]


class BleOobClient(ABC):
    """BLE OOB central 추상화. UI는 오직 이 인터페이스에만 의존한다.

    사용 흐름:
        client = SimulatorOobClient()      # 또는 BleakOobClient()
        client.on_scan_result = ...        # 콜백 등록
        client.scan()                      # → on_scan_result(발견 목록)
        client.connect(peripheral)         # → on_state(BLE_CONN) 또는 ERR
        client.read_oob()                  # → on_oob_info(파싱 결과) + OOB_DONE
        client.disconnect()
    """

    def __init__(self) -> None:
        # UI가 채워 넣는 콜백 (기본은 아무것도 안 함).
        self.on_scan_result: ScanResultCallback = lambda peripherals: None
        self.on_oob_info: OobInfoCallback = lambda result: None  # Read·Notify 공통
        self.on_state: StateCallback = lambda e: None  # 세션 타임라인 실연동용
        self.on_disconnect: DisconnectCallback = lambda reason: None
        self.on_log: LogCallback = lambda s: None

    @abstractmethod
    def scan(self) -> None:
        """Service UUID 필터 스캔을 시작한다. 결과는 on_scan_result로 온다 (비블로킹)."""

    @abstractmethod
    def connect(self, peripheral: OobPeripheral) -> None:
        """발견 기기에 GATT 연결한다. 성공=BLE_CONN, 실패=ERR(BLE_CONN_FAIL) 이벤트."""

    @abstractmethod
    def read_oob(self) -> None:
        """OOB_INFO를 읽는다. 결과는 on_oob_info + OOB_DONE(성공)/ERR(OOB_PARSE) 이벤트."""

    @abstractmethod
    def disconnect(self) -> None:
        """GATT 연결을 닫는다. UWB 세션에는 영향을 주지 않는다."""

    @abstractmethod
    def is_connected(self) -> bool:
        """현재 GATT 연결 여부."""

    def close(self) -> None:
        """앱 종료·모드 이탈 시 자원 정리. 기본은 연결 해제와 동일 (스레드형은 재정의)."""
        self.disconnect()

    def _emit_state(self, state: SessionState, reason: Optional[str]) -> None:
        """세션 이벤트를 원시 라인 로그와 함께 콜백으로 내보낸다 (SimulatorDevice와 동일 표기)."""
        raw = f"STATE:{state.value}" + (f",REASON:{reason}" if reason else "")
        self.on_log(raw)
        self.on_state(DeviceEvent(state=state, reason=reason, raw=raw, ts=time.time()))


class SimulatorOobClient(BleOobClient):
    """폰 없이 OOB 전 흐름을 재현하는 하네스 (FR-OOB-8).

    addresses로 여러 대(다중 폰 스캔 목록), fail_mode로 실패 시나리오
    (BLE_CONN_FAIL/OOB_PARSE), simulate_address_change()로 주소 재발급
    Notify를 재현한다. delay_s를 주면 모든 단계 지연을 그 값으로 통일(테스트용).
    """

    def __init__(
        self,
        addresses: Optional[List[str]] = None,
        session_id: int = SIM_DEFAULT_SESSION_ID,
        fail_mode: Optional[str] = None,  # REASON_BLE_CONN_FAIL | REASON_OOB_PARSE
        delay_s: Optional[float] = None,
    ) -> None:
        super().__init__()
        self._addresses = list(
            SIM_DEFAULT_ADDRESSES if addresses is None else addresses
        )
        # session_id·fail_mode는 UI 재현 토글에서 실행 중 변경 가능 (공개 속성)
        self.session_id = session_id
        self.fail_mode = fail_mode
        self._scan_delay = delay_s if delay_s is not None else SIM_SCAN_DELAY_S
        self._connect_delay = delay_s if delay_s is not None else SIM_CONNECT_DELAY_S
        self._read_delay = delay_s if delay_s is not None else SIM_READ_DELAY_S
        self._connected: Optional[OobPeripheral] = None
        self._timers: List[threading.Timer] = []

    # --- BleOobClient 구현 ---

    def scan(self) -> None:
        """지연 후 addresses 만큼 가짜 발견 목록을 만든다. 0대면 빈 목록(광고 없음)."""
        self.on_log(f"{OOB_LOG_PREFIX} 스캔 시작 (시뮬레이터)")
        self._schedule(self._scan_delay, self._finish_scan)

    def connect(self, peripheral: OobPeripheral) -> None:
        """지연 후 연결. fail_mode=BLE_CONN_FAIL이면 ERR 재현 (사양서 §7-3)."""
        if self._connected is not None:
            self.on_log(f"{OOB_LOG_PREFIX} 이미 연결됨 — connect 무시")
            return
        self.on_log(f"{OOB_LOG_PREFIX} 연결 시도: {peripheral.device_id}")
        self._schedule(self._connect_delay, lambda: self._finish_connect(peripheral))

    def read_oob(self) -> None:
        """지연 후 OOB_INFO 전달. fail_mode=OOB_PARSE면 7B 미만 페이로드 재현 (§7-4)."""
        if self._connected is None:
            self.on_log(f"{OOB_LOG_PREFIX} 미연결 상태 — read_oob 무시")
            return
        self._schedule(self._read_delay, self._finish_read)

    def disconnect(self) -> None:
        """대기 중 타이머를 모두 취소하고 연결을 닫는다 (좀비 타이머 금지)."""
        for t in self._timers:
            t.cancel()
        self._timers.clear()
        if self._connected is not None:
            self._connected = None
            self.on_log(f"{OOB_LOG_PREFIX} 연결 해제")
            self.on_disconnect("사용자 요청")

    def is_connected(self) -> bool:
        """현재 가짜 GATT 연결 여부."""
        return self._connected is not None

    # --- 시뮬레이터 전용 (실패·주소 재발급 재현) ---

    def simulate_address_change(self, new_address: Optional[str] = None) -> None:
        """주소 재발급 Notify 재현 (사양서 §5-4) — 연결 중일 때만 유효."""
        if self._connected is None:
            self.on_log(f"{OOB_LOG_PREFIX} 미연결 상태 — 주소 변경 재현 무시")
            return
        index = self._peripheral_index(self._connected)
        self._addresses[index] = new_address or _random_address(self._addresses)
        self.on_log(f"{OOB_LOG_PREFIX} Notify: 주소 재발급 → {self._addresses[index]}")
        self._deliver_payload(self._addresses[index])

    # --- 내부: 타이머 스레드에서 실행 (UI 직접 갱신 금지, 콜백만 호출) ---

    def _finish_scan(self) -> None:
        """발견 목록을 콜백으로 내보낸다. 1대 이상이면 BLE_ADV 점등 (사양서 §6)."""
        now = time.time()
        found = [
            OobPeripheral(
                device_id=f"{SIM_DEVICE_ID_PREFIX}{i}",
                name=ADV_LOCAL_NAME,
                rssi=SIM_RSSI_DBM,
                ts=now,
            )
            for i in range(len(self._addresses))
        ]
        self.on_log(f"{OOB_LOG_PREFIX} 스캔 완료: {len(found)}대 발견")
        if found:
            self._emit_state(SessionState.BLE_ADV, None)
        self.on_scan_result(found)

    def _finish_connect(self, peripheral: OobPeripheral) -> None:
        """연결 완료 또는 실패 토글에 따른 ERR."""
        if self.fail_mode == REASON_BLE_CONN_FAIL:
            self._emit_state(SessionState.ERR, REASON_BLE_CONN_FAIL)
            return
        self._connected = peripheral
        self._emit_state(SessionState.BLE_CONN, None)

    def _finish_read(self) -> None:
        """OOB_INFO Read 응답 — 정상 페이로드 또는 파싱 실패 재현."""
        if self.fail_mode == REASON_OOB_PARSE:
            broken = build_oob_info("5F:DD", self.session_id)[: PAYLOAD_MIN_LEN - 3]
            result = parse_oob_info(broken)
            self.on_log(
                f"{OOB_LOG_PREFIX} 파싱 실패: {result.error} (raw: {result.raw_hex})"
            )
            self.on_oob_info(result)
            self._emit_state(SessionState.ERR, REASON_OOB_PARSE)
            return
        if self._connected is None:  # read 대기 중 disconnect된 경우
            return
        address = self._addresses[self._peripheral_index(self._connected)]
        self._deliver_payload(address)
        self._emit_state(SessionState.OOB_DONE, None)

    def _deliver_payload(self, address: str) -> None:
        """주소로 7B 페이로드를 만들어 파싱 결과를 콜백으로 내보낸다 (Read·Notify 공통)."""
        result = parse_oob_info(build_oob_info(address, self.session_id))
        self.on_log(f"{OOB_LOG_PREFIX} OOB_INFO 수신: {result.raw_hex}")
        self.on_oob_info(result)

    def _peripheral_index(self, peripheral: OobPeripheral) -> int:
        """시뮬 기기 ID("SIM-OOB-N")에서 주소 목록 인덱스를 얻는다."""
        return int(peripheral.device_id.removeprefix(SIM_DEVICE_ID_PREFIX))

    def _schedule(self, delay_s: float, fn: Callable[[], None]) -> None:
        """지연 콜백 타이머를 등록한다 (disconnect 시 일괄 취소 대상)."""
        self._timers = [t for t in self._timers if t.is_alive()]  # 끝난 타이머 정리
        timer = threading.Timer(delay_s, fn)
        timer.daemon = True
        self._timers.append(timer)
        timer.start()


class BleakOobClient(BleOobClient):
    """실물 BLE central (bleak) — 폰의 GATT peripheral에서 OOB_INFO를 읽는다.

    bleak은 asyncio 기반이므로 **전용 스레드의 이벤트 루프**에서만 돌린다
    (Flet 메인 루프와 섞으면 교착 — 변경요구서 §5의 최다 예상 트러블).
    UI로 가는 길은 기존 콜백 경로 하나뿐이다.
    """

    def __init__(
        self,
        scan_timeout_s: float = SCAN_TIMEOUT_S,
        connect_timeout_s: float = CONNECT_TIMEOUT_S,
    ) -> None:
        super().__init__()
        if _BLEAK_IMPORT_ERROR is not None:
            raise RuntimeError(f"bleak 사용 불가 — {_BLEAK_IMPORT_ERROR}")
        self._scan_timeout = scan_timeout_s
        self._connect_timeout = connect_timeout_s
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._client: Optional["BleakClient"] = None
        # 스캔에서 받은 BLEDevice 원본 — 연결 시 주소 문자열보다 안전(Windows 캐시 회피)
        self._devices: Dict[str, "BLEDevice"] = {}
        self._user_disconnect = False  # 끊김 사유 구분 (사용자 요청 vs 상대 종료)

    # --- BleOobClient 구현 (모두 비블로킹: 루프 스레드에 넘기고 즉시 반환) ---

    def scan(self) -> None:
        """Service UUID 필터 스캔. 결과는 on_scan_result로 온다."""
        self.on_log(
            f"{OOB_LOG_PREFIX} 스캔 시작 "
            f"(UUID 필터, 최대 {self._scan_timeout:.0f}s · 첫 발견 즉시 종료)"
        )
        self._submit(self._scan_async())

    def connect(self, peripheral: OobPeripheral) -> None:
        """GATT 연결 + Notify 구독. 실패·타임아웃은 ERR(BLE_CONN_FAIL)."""
        if self._client is not None:
            self.on_log(f"{OOB_LOG_PREFIX} 이미 연결됨 — connect 무시")
            return
        self.on_log(f"{OOB_LOG_PREFIX} 연결 시도: {peripheral.device_id}")
        self._submit(self._connect_async(peripheral))

    def read_oob(self) -> None:
        """OOB_INFO를 Read한다. 성공=OOB_DONE, GATT 실패=ERR, 페이로드 불량=ERR(OOB_PARSE)."""
        self._submit(self._read_async())

    def disconnect(self) -> None:
        """GATT 연결만 닫는다. 루프 스레드는 재사용을 위해 남긴다 (종료는 close())."""
        if self._loop is None or self._client is None:
            return
        self._user_disconnect = True
        self._submit(self._disconnect_async())

    def is_connected(self) -> bool:
        """현재 GATT 연결 여부."""
        client = self._client
        return client is not None and client.is_connected

    def close(self) -> None:
        """연결 해제까지 기다린 뒤 루프·스레드를 종료한다 (앱 종료 — 좀비 스레드 금지)."""
        loop, thread = self._loop, self._thread
        if loop is None or thread is None:
            return
        self._user_disconnect = True
        try:  # 루프를 멈추기 전에 GATT 해제가 실제로 끝나야 한다
            asyncio.run_coroutine_threadsafe(self._disconnect_async(), loop).result(
                BLE_THREAD_JOIN_S
            )
        except (TimeoutError, BleakError, OSError) as exc:
            logger.warning("BLE 종료 대기 실패: %s", exc)
        self._loop, self._thread = None, None
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=BLE_THREAD_JOIN_S)
        if thread.is_alive():
            logger.warning(
                "BLE 루프 스레드가 %.0fs 내에 끝나지 않음", BLE_THREAD_JOIN_S
            )

    # --- 루프 스레드에서 실행되는 코루틴 (여기서도 콜백만 호출, UI 직접 접근 금지) ---

    async def _scan_async(self) -> None:
        """첫 OOB 광고 발견 즉시 스캔을 끝낸다. 어댑터 오류는 빈 목록으로 알린다."""
        discovered: Dict[str, Tuple["BLEDevice", "AdvertisementData"]] = {}
        first_match = asyncio.Event()

        def on_detect(device: "BLEDevice", adv: "AdvertisementData") -> None:
            """UUID가 일치하는 첫 광고만 보존하고 대기 중인 스캔을 깨운다."""
            if first_match.is_set() or not _advertises_oob_service(adv):
                return
            discovered[device.address] = (device, adv)
            first_match.set()

        try:
            scanner = BleakScanner(
                detection_callback=on_detect,
                service_uuids=[SERVICE_UUID],
            )
            async with scanner:
                try:
                    await asyncio.wait_for(first_match.wait(), self._scan_timeout)
                except asyncio.TimeoutError:
                    pass
        except (BleakError, OSError) as exc:
            self.on_log(f"{OOB_LOG_PREFIX} 스캔 실패: {exc} (수동 입력 경로는 유효)")
            self.on_scan_result([])
            return
        self._devices = {addr: dev for addr, (dev, _adv) in discovered.items()}
        found = peripherals_from_discovery(discovered, time.time())
        self.on_log(f"{OOB_LOG_PREFIX} 스캔 완료: {len(found)}대 발견")
        for p in found:  # 스캔 캐시의 죽은 광고를 사용자가 가려낼 수 있게 RSSI를 남긴다
            self.on_log(f"{OOB_LOG_PREFIX}   {p.name} · {p.device_id} · {p.rssi}dBm")
        if found:
            self._emit_state(SessionState.BLE_ADV, None)
        self.on_scan_result(found)

    async def _connect_async(self, peripheral: OobPeripheral) -> None:
        """연결 타임아웃 10s(사양서 §7-3). 성공 후 Notify 구독은 실패해도 하드 실패 아님."""
        target = self._devices.get(peripheral.device_id) or peripheral.device_id
        client = BleakClient(
            target,
            disconnected_callback=self._on_peer_disconnect,
            timeout=self._connect_timeout,
        )
        try:
            await client.connect()
        except (BleakError, OSError, asyncio.TimeoutError) as exc:
            self.on_log(f"{OOB_LOG_PREFIX} 연결 실패: {exc}")
            await self._safe_close(client)
            self._emit_state(SessionState.ERR, REASON_BLE_CONN_FAIL)
            return
        self._client = client
        self._user_disconnect = False
        self._emit_state(SessionState.BLE_CONN, None)
        await self._subscribe_notify(client)

    async def _subscribe_notify(self, client: "BleakClient") -> None:
        """주소 재발급 Notify 구독 (FR-OOB-6). 실패해도 Read 경로는 살아 있으므로 경고만."""
        try:
            await client.start_notify(OOB_INFO_CHAR_UUID, self._on_notify)
        except (BleakError, OSError) as exc:
            self.on_log(
                f"{OOB_LOG_PREFIX} Notify 구독 실패 — 주소 변경 감지 불가: {exc}"
            )

    async def _read_async(self) -> None:
        """OOB_INFO Read 1회. GATT 오류는 링크 문제이므로 BLE_CONN_FAIL로 구분한다."""
        client = self._client
        if client is None:
            self.on_log(f"{OOB_LOG_PREFIX} 미연결 상태 — read_oob 무시")
            return
        try:
            data = await client.read_gatt_char(OOB_INFO_CHAR_UUID)
        except (BleakError, OSError) as exc:
            self.on_log(f"{OOB_LOG_PREFIX} OOB_INFO Read 실패: {exc}")
            self._emit_state(SessionState.ERR, REASON_BLE_CONN_FAIL)
            return
        self._deliver(bytes(data), notify=False)

    async def _disconnect_async(self) -> None:
        """GATT만 닫는다 — UCI 레인징은 절대 건드리지 않는다 (BLE는 OOB 전용)."""
        client, self._client = self._client, None
        if client is None:
            return
        await self._safe_close(client)
        self.on_log(f"{OOB_LOG_PREFIX} 연결 해제")
        self.on_disconnect(DISCONNECT_USER)

    async def _safe_close(self, client: "BleakClient") -> None:
        """종료 경로의 예외는 앱을 죽이지 않되 삼키지도 않는다 (로그 보존)."""
        try:
            await client.disconnect()
        except (BleakError, OSError) as exc:
            logger.warning("BLE 연결 해제 중 오류: %s", exc)

    # --- bleak 콜백 (루프 스레드) ---

    def _on_notify(self, _char: object, data: bytearray) -> None:
        """주소 재발급 Notify (사양서 §5-4) — 폰 Stop→재Start 시 새 OOB_INFO가 온다."""
        self._deliver(bytes(data), notify=True)

    def _on_peer_disconnect(self, _client: "BleakClient") -> None:
        """상대가 끊음. 레인징 중이어도 UWB 세션은 그대로 둔다 (사양서 §7-7)."""
        if self._user_disconnect:  # 사용자 요청 경로에서 이미 알렸다
            return
        self._client = None
        self.on_log(f"{OOB_LOG_PREFIX} BLE 끊김 — UWB 레인징에는 영향 없음")
        self.on_disconnect(DISCONNECT_PEER)

    # --- 공통 ---

    def _deliver(self, data: bytes, notify: bool) -> None:
        """Read·Notify 공통 처리: 파싱 → 콜백. 실패해도 raw hex를 남긴다 (사양서 §7-4)."""
        label = "Notify" if notify else "Read"
        result = parse_oob_info(data)
        if result.kind != "ok":
            self.on_log(
                f"{OOB_LOG_PREFIX} {label} 파싱 실패: {result.error} (raw: {result.raw_hex})"
            )
            self.on_oob_info(result)
            self._emit_state(SessionState.ERR, REASON_OOB_PARSE)
            return
        self.on_log(f"{OOB_LOG_PREFIX} OOB_INFO {label} 수신: {result.raw_hex}")
        self.on_oob_info(result)
        if not notify:  # OOB_DONE은 최초 Read 성공에만 (Notify는 주소 갱신 경로)
            self._emit_state(SessionState.OOB_DONE, None)

    def _submit(self, coro: Coroutine[Any, Any, None]) -> None:
        """코루틴을 루프 스레드에 넘긴다 (비블로킹). 예외는 삼키지 말고 로그로."""
        future = asyncio.run_coroutine_threadsafe(coro, self._ensure_loop())
        future.add_done_callback(self._log_failure)

    def _log_failure(self, future: "Future[None]") -> None:
        """코루틴에서 새어 나온 예외를 UI 로그에 노출한다 (무증상 실패 방어)."""
        if future.cancelled():
            return
        exc = future.exception()
        if exc is not None:
            logger.exception("OOB 비동기 작업 실패", exc_info=exc)
            self.on_log(f"{OOB_LOG_PREFIX} 내부 오류: {exc}")

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """전용 asyncio 루프를 지연 생성한다 (Flet 메인 루프와 공유 금지)."""
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._run_loop,
                args=(self._loop,),
                name=BLE_THREAD_NAME,
                daemon=True,
            )
            self._thread.start()
        return self._loop

    def _run_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """루프 스레드 본체 — stop() 되면 루프를 닫고 끝난다."""
        asyncio.set_event_loop(loop)
        loop.run_forever()
        loop.close()


def is_bleak_available() -> bool:
    """실물 BLE 사용 가능 여부. False면 OOB 모드 진입을 막고 수동 경로를 유지한다 (FR-OOB-9)."""
    return _BLEAK_IMPORT_ERROR is None


def peripherals_from_discovery(
    discovered: Dict[str, Tuple["BLEDevice", "AdvertisementData"]], ts: float
) -> List[OobPeripheral]:
    """bleak 스캔 결과를 UI 목록용으로 변환한다 (RSSI·발견시각 표기 — 스캔 캐시 대응)."""
    return [
        OobPeripheral(
            device_id=address,
            name=adv.local_name or device.name or ADV_LOCAL_NAME,
            rssi=adv.rssi,
            ts=ts,
        )
        for address, (device, adv) in discovered.items()
    ]


def _advertises_oob_service(adv: "AdvertisementData") -> bool:
    """광고의 Service UUID 목록에 OOB UUID가 있는지 대소문자 무관하게 확인한다."""
    expected = SERVICE_UUID.lower()
    return any(uuid.lower() == expected for uuid in (adv.service_uuids or []))


def _random_address(exclude: List[str]) -> str:
    """기존 목록과 겹치지 않는 새 'XX:XX' 주소를 만든다 (주소 재발급 재현용)."""
    while True:
        address = f"{random.randint(0, 255):02X}:{random.randint(0, 255):02X}"
        if address not in exclude:
            return address
