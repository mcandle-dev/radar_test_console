"""OOB 클라이언트 단위테스트 — 폰·BLE 없이 전 흐름·실패 토글과
BleakOobClient의 BLE 무관 부분(스캔 결과 변환·페이로드 처리·안전 종료)을 검증한다."""

import asyncio
import threading
import time
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import ble_oob
from ble_oob import (
    REASON_BLE_CONN_FAIL,
    REASON_OOB_PARSE,
    BleakOobClient,
    BleOobClient,
    OobPeripheral,
    SimulatorOobClient,
    is_bleak_available,
    peripherals_from_discovery,
)
from models import DeviceEvent, SessionState
from oob_parser import OobParseResult, build_oob_info

TEST_DELAY_S = 0.01  # 시뮬 지연 단축 (테스트 속도)
WAIT_TIMEOUT_S = 2.0  # 콜백 대기 상한 — 초과하면 테스트 실패


class Harness:
    """콜백 수집기 — 각 콜백을 기록하고 Event로 대기를 푼다."""

    def __init__(self, client: BleOobClient) -> None:
        self.scans: List[List[OobPeripheral]] = []
        self.infos: List[OobParseResult] = []
        self.states: List[DeviceEvent] = []
        self.disconnects: List[str] = []
        self.logs: List[str] = []
        self.scan_done = threading.Event()
        self.info_done = threading.Event()
        self.state_done = threading.Event()
        client.on_scan_result = self._on_scan
        client.on_oob_info = self._on_info
        client.on_state = self._on_state
        client.on_disconnect = self.disconnects.append
        client.on_log = self.logs.append

    def _on_scan(self, found: List[OobPeripheral]) -> None:
        self.scans.append(found)
        self.scan_done.set()

    def _on_info(self, result: OobParseResult) -> None:
        self.infos.append(result)
        self.info_done.set()

    def _on_state(self, event: DeviceEvent) -> None:
        self.states.append(event)
        self.state_done.set()

    def wait_state(self, state: SessionState) -> DeviceEvent:
        """해당 상태 이벤트가 올 때까지 대기 후 반환한다 (없으면 assert 실패)."""
        for _ in range(10):
            for e in self.states:
                if e.state == state:
                    return e
            self.state_done.clear()
            self.state_done.wait(WAIT_TIMEOUT_S)
        raise AssertionError(f"{state} 이벤트가 오지 않음 (수신: {self.states})")


def make_client(
    addresses: Optional[List[str]] = None, fail_mode: Optional[str] = None
) -> tuple[SimulatorOobClient, Harness]:
    """지연을 단축한 시뮬 클라이언트와 콜백 수집기를 만든다."""
    client = SimulatorOobClient(
        addresses=addresses, fail_mode=fail_mode, delay_s=TEST_DELAY_S
    )
    return client, Harness(client)


def connect_first(client: SimulatorOobClient, h: Harness) -> OobPeripheral:
    """스캔 → 첫 기기 연결까지 진행한다 (테스트 공통 준비)."""
    client.scan()
    assert h.scan_done.wait(WAIT_TIMEOUT_S)
    peripheral = h.scans[0][0]
    client.connect(peripheral)
    h.wait_state(SessionState.BLE_CONN)
    return peripheral


class TestScan:
    """스캔 — 발견 목록과 BLE_ADV 점등."""

    def test_default_single_phone(self) -> None:
        """기본 1대 발견 + BLE_ADV 이벤트."""
        client, h = make_client()
        client.scan()
        assert h.scan_done.wait(WAIT_TIMEOUT_S)
        assert len(h.scans[0]) == 1
        assert h.scans[0][0].name == "UWB-OOB"
        h.wait_state(SessionState.BLE_ADV)

    def test_multiple_phones(self) -> None:
        """폰 여러 대 = 목록 여러 개 (다중 선택 UI 검증용)."""
        client, h = make_client(addresses=["5F:DD", "A1:B2"])
        client.scan()
        assert h.scan_done.wait(WAIT_TIMEOUT_S)
        assert len(h.scans[0]) == 2

    def test_no_advertiser(self) -> None:
        """0대면 빈 목록 + BLE_ADV 없음 (사양서 §7-1 '광고 없음')."""
        client, h = make_client(addresses=[])
        client.scan()
        assert h.scan_done.wait(WAIT_TIMEOUT_S)
        assert h.scans[0] == []
        assert all(e.state != SessionState.BLE_ADV for e in h.states)


class TestConnectAndRead:
    """연결 → OOB_INFO 수신 정상 흐름."""

    def test_full_flow(self) -> None:
        """BLE_CONN → read_oob → on_oob_info(ok) + OOB_DONE."""
        client, h = make_client()
        connect_first(client, h)
        assert client.is_connected()
        client.read_oob()
        assert h.info_done.wait(WAIT_TIMEOUT_S)
        result = h.infos[0]
        assert result.kind == "ok"
        assert result.info is not None
        assert result.info.uwb_address == "5F:DD"
        assert result.info.session_id == 42
        h.wait_state(SessionState.OOB_DONE)

    def test_read_without_connect_ignored(self) -> None:
        """미연결 read_oob는 no-op + 로그 (앱 무중단)."""
        client, h = make_client()
        client.read_oob()
        assert not h.info_done.wait(0.1)
        assert any("미연결" in log for log in h.logs)


class TestFailToggles:
    """실패 토글 — ERR 재현 (사양서 §7-3·4)."""

    def test_conn_fail(self) -> None:
        """BLE_CONN_FAIL: ERR 이벤트 + 연결 안 됨."""
        client, h = make_client(fail_mode=REASON_BLE_CONN_FAIL)
        client.scan()
        assert h.scan_done.wait(WAIT_TIMEOUT_S)
        client.connect(h.scans[0][0])
        err = h.wait_state(SessionState.ERR)
        assert err.reason == REASON_BLE_CONN_FAIL
        assert not client.is_connected()

    def test_oob_parse_fail(self) -> None:
        """OOB_PARSE: invalid 결과(raw hex 보존) + ERR 이벤트."""
        client, h = make_client(fail_mode=None)
        connect_first(client, h)
        client.fail_mode = REASON_OOB_PARSE  # 연결 후 파싱만 실패시키기
        client.read_oob()
        assert h.info_done.wait(WAIT_TIMEOUT_S)
        assert h.infos[0].kind == "invalid"
        assert h.infos[0].raw_hex  # 진단 원칙: 원문 보존
        err = h.wait_state(SessionState.ERR)
        assert err.reason == REASON_OOB_PARSE


class TestAddressChange:
    """주소 재발급 Notify (사양서 §5-4) — 이 자동화의 존재 이유."""

    def test_notify_delivers_new_address(self) -> None:
        """주소 변경 재현 시 새 주소가 on_oob_info로 온다."""
        client, h = make_client()
        connect_first(client, h)
        client.read_oob()
        assert h.info_done.wait(WAIT_TIMEOUT_S)
        h.info_done.clear()
        client.simulate_address_change("A1:B2")
        assert h.info_done.wait(WAIT_TIMEOUT_S)
        assert h.infos[-1].info is not None
        assert h.infos[-1].info.uwb_address == "A1:B2"

    def test_notify_requires_connection(self) -> None:
        """미연결이면 주소 변경 재현은 no-op + 로그."""
        client, h = make_client()
        client.simulate_address_change("A1:B2")
        assert not h.info_done.wait(0.1)
        assert any("미연결" in log for log in h.logs)


class TestDisconnect:
    """연결 해제 — 콜백과 대기 타이머 정리."""

    def test_disconnect_callback(self) -> None:
        """disconnect 시 on_disconnect 호출 + 연결 해제."""
        client, h = make_client()
        connect_first(client, h)
        client.disconnect()
        assert not client.is_connected()
        assert len(h.disconnects) == 1

    def test_disconnect_cancels_pending_read(self) -> None:
        """read 대기 중 disconnect하면 결과가 오지 않는다 (좀비 타이머 금지)."""
        client, h = make_client()
        connect_first(client, h)
        client.read_oob()
        client.disconnect()  # 타이머 발화 전 취소
        assert not h.info_done.wait(0.1)


def fake_discovery(
    address: str,
    local_name: Optional[str],
    rssi: int,
    device_name: Optional[str] = None,
) -> Dict[str, Tuple[Any, Any]]:
    """bleak 스캔 결과 형태(dict[주소] = (BLEDevice, AdvertisementData))를 흉내 낸다."""
    device = SimpleNamespace(address=address, name=device_name)
    adv = SimpleNamespace(local_name=local_name, rssi=rssi, service_uuids=[])
    return {address: (device, adv)}


class TestDiscoveryMapping:
    """스캔 결과 → UI 목록 변환 (실 BLE 없이 검증 가능한 순수 함수)."""

    def test_rssi_from_advertisement(self) -> None:
        """RSSI는 광고 데이터에서 온다 — bleak 3.x의 BLEDevice에는 rssi가 없다."""
        found = peripherals_from_discovery(
            fake_discovery("AA:BB:CC:DD:EE:FF", "UWB-OOB", -62), ts=123.0
        )
        assert len(found) == 1
        assert found[0].device_id == "AA:BB:CC:DD:EE:FF"  # connect()에 그대로 넘어간다
        assert found[0].name == "UWB-OOB"
        assert found[0].rssi == -62
        assert found[0].ts == 123.0

    def test_name_falls_back_to_default(self) -> None:
        """광고에 이름이 없어도 목록에서 사라지지 않는다 (이름은 표시용일 뿐)."""
        found = peripherals_from_discovery(fake_discovery("AA:BB", None, -70), ts=1.0)
        assert found[0].name == "UWB-OOB"


class TestBleakClientOffline:
    """BleakOobClient — 실 BLE 없이 검증되는 경로 (페이로드 처리·미연결·안전 종료)."""

    def test_bleak_available(self) -> None:
        """requirements.txt에 고정된 bleak가 실제로 import된다."""
        assert is_bleak_available()

    def test_scan_stops_after_first_matching_advertisement(
        self, monkeypatch: Any
    ) -> None:
        """첫 OOB UUID 광고가 오면 전체 timeout을 기다리지 않고 스캔을 종료한다."""
        device = SimpleNamespace(address="AA:BB:CC:DD:EE:FF", name="Galaxy")
        adv = SimpleNamespace(
            local_name="UWB-OOB",
            rssi=-48,
            service_uuids=[ble_oob.SERVICE_UUID.lower()],
        )

        class FakeScanner:
            """진입 직후 광고 한 건을 전달하고 context 종료 여부를 기록한다."""

            stopped = False

            def __init__(self, detection_callback: Any, **_kwargs: Any) -> None:
                self.callback = detection_callback

            async def __aenter__(self) -> "FakeScanner":
                asyncio.get_running_loop().call_soon(self.callback, device, adv)
                return self

            async def __aexit__(self, *_args: Any) -> None:
                FakeScanner.stopped = True

        monkeypatch.setattr(ble_oob, "BleakScanner", FakeScanner)
        client = BleakOobClient(scan_timeout_s=5.0)
        h = Harness(client)

        started = time.monotonic()
        asyncio.run(client._scan_async())

        assert time.monotonic() - started < 0.5
        assert FakeScanner.stopped
        assert len(h.scans) == 1
        assert h.scans[0][0].device_id == device.address
        assert h.wait_state(SessionState.BLE_ADV)

    def test_read_emits_oob_done(self) -> None:
        """Read 성공: on_oob_info(ok) + OOB_DONE 점등 (시뮬과 동일한 계약)."""
        client = BleakOobClient()
        h = Harness(client)
        client._deliver(build_oob_info("5F:DD", 42), notify=False)
        assert h.infos[0].kind == "ok"
        assert h.infos[0].info is not None
        assert h.infos[0].info.uwb_address == "5F:DD"
        assert h.wait_state(SessionState.OOB_DONE)

    def test_notify_updates_address_without_oob_done(self) -> None:
        """주소 재발급 Notify는 값만 갱신 — OOB_DONE은 최초 Read에만 (FR-OOB-6)."""
        client = BleakOobClient()
        h = Harness(client)
        client._deliver(build_oob_info("A1:B2", 42), notify=True)
        assert h.infos[0].info is not None
        assert h.infos[0].info.uwb_address == "A1:B2"
        assert all(e.state != SessionState.OOB_DONE for e in h.states)

    def test_short_payload_emits_parse_err(self) -> None:
        """7B 미만: invalid + raw hex 보존 + ERR(OOB_PARSE) (사양서 §7-4)."""
        client = BleakOobClient()
        h = Harness(client)
        client._deliver(b"\x01\x5f", notify=False)
        assert h.infos[0].kind == "invalid"
        assert h.infos[0].raw_hex == "01 5F"
        assert h.wait_state(SessionState.ERR).reason == REASON_OOB_PARSE

    def test_disconnect_and_close_without_connection(self) -> None:
        """연결·루프가 없어도 disconnect/close가 예외 없이 no-op (앱 종료 경로)."""
        client = BleakOobClient()
        client.disconnect()
        client.close()
        assert not client.is_connected()
