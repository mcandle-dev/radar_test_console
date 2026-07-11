"""UciSerialDevice 단위 테스트 — 시리얼 없이 가짜 트랜스포트로 UCI 인코딩/디코딩 검증.

uci 라이브러리의 Factory 메커니즘에 MockTransport('mock:' 포트)를 등록해
실제 프레이밍 코드 경로(send_message → write / data_received → NTF 핸들러)를 그대로 탄다.
"""

import time
from typing import Any, Dict, List, Tuple

import pytest

import uci_params
from models import DeviceEvent, Measurement, SessionState
from radar_device import UciSerialDevice
from uci.transport import ITransport

SESSION_ID = uci_params.SESSION_ID
SID_LE = SESSION_ID.to_bytes(4, "little")
WORKER_TIMEOUT_S = 2.0

# UCI 응답 기본값: 모든 명령 Ok (SESSION_INIT은 Fira 2.0 핸들=세션ID 반환)
OK_RESPONSES: Dict[Tuple[int, int], bytes] = {
    (0x01, 0x00): bytes([0x00]) + SID_LE,  # SESSION_INIT
    (0x01, 0x03): bytes([0x00, 0x00]),  # SESSION_SET_APP_CONFIG
    (0x01, 0x01): bytes([0x00]),  # SESSION_DEINIT
    (0x02, 0x00): bytes([0x00]),  # RANGE_START
    (0x02, 0x01): bytes([0x00]),  # RANGE_STOP
}


class MockTransport(ITransport):
    """'mock:' 포트를 처리하는 가짜 트랜스포트 — 쓰기 기록 + 자동 응답 + NTF 주입."""

    instances: List["MockTransport"] = []  # weakref 소멸 방지용 강한 참조

    def __init__(self, callback: Any, *args: Any, **kwargs: Any) -> None:
        self.cb = callback  # weakref.WeakMethod(Client.data_received)
        self.written: List[bytes] = []
        self.responses = dict(OK_RESPONSES)
        self.alive = True
        MockTransport.instances.append(self)

    def write(self, packet: bytes) -> None:
        """명령 프레임을 기록하고, 준비된 응답이 있으면 즉시 되돌려 준다."""
        self.written.append(bytes(packet))
        gid, oid = packet[0] & 0x0F, packet[1] & 0x3F
        payload = self.responses.get((gid, oid))
        if payload is not None:
            header = bytes([(0x02 << 5) | gid, oid, 0x00, len(payload)])
            self.cb()(bytearray(header + payload))

    def inject_ntf(self, gid: int, oid: int, payload: bytes) -> None:
        """보드가 보낸 NTF 프레임을 흉내 낸다."""
        header = bytes([(0x03 << 5) | gid, oid, 0x00, len(payload)])
        self.cb()(bytearray(header + payload))

    def close(self) -> None:
        self.alive = False

    def is_alive(self) -> bool:
        return self.alive

    @staticmethod
    def handle(port: str) -> bool:
        return port.startswith("mock:")


class Harness:
    """디바이스 + 콜백 수집기 + 트랜스포트 접근을 묶은 테스트 하네스."""

    def __init__(self, dest_mac: str = "5F:DD") -> None:
        self.device = UciSerialDevice(dest_mac=dest_mac)
        self.measurements: List[Measurement] = []
        self.events: List[DeviceEvent] = []
        self.logs: List[str] = []
        self.device.on_measurement = self.measurements.append
        self.device.on_state = self.events.append
        self.device.on_log = self.logs.append

    def connect(self) -> MockTransport:
        self.device.connect("mock:0")
        assert self.device.is_connected()
        return MockTransport.instances[-1]

    def wait_worker(self) -> None:
        worker = self.device._worker
        assert worker is not None
        worker.join(WORKER_TIMEOUT_S)
        assert not worker.is_alive()


def make_twr_range_ntf(dist_cm: int, meas_status: int = 0x00) -> bytes:
    """RANGE_DATA(SESSION_INFO) NTF의 TWR 측정 1건짜리 페이로드를 만든다."""
    p = (0).to_bytes(4, "little")  # sequence
    p += SID_LE  # session handle
    p += b"\x00"  # RFU
    p += (120).to_bytes(4, "little")  # ranging interval
    p += b"\x01"  # measurement type = TWR
    p += b"\x00"  # RFU
    p += b"\x00"  # MAC 주소 모드 = 2바이트
    p += (0).to_bytes(4, "little")  # primary session id
    p += b"\x00" * 4  # RFU
    p += b"\x01"  # 측정 개수 = 1
    p += bytes.fromhex("5fdd")  # MAC (LE)
    p += bytes([meas_status])  # status
    p += b"\x00"  # NLoS
    p += dist_cm.to_bytes(2, "little")  # 거리(cm)
    p += b"\x00" * 12  # AoA 4종 (값+FOM)
    p += b"\x00"  # slot in error
    p += b"\x00"  # rssi
    p += b"\x00" * 11  # RFU
    return p


def make_session_status_ntf(state: int, reason: int) -> bytes:
    """SESSION_STATUS_NTF 페이로드(sid + state + reason)를 만든다."""
    return SID_LE + bytes([state, reason])


# --- dest MAC 변환 (sasodoma run_fira_twr.py의 변환 로직과 동일해야 함) ---


def test_dest_mac_to_uci_reverses_bytes() -> None:
    assert uci_params.dest_mac_to_uci("5F:DD") == 0xDD5F
    assert uci_params.dest_mac_to_uci("00:00") == 0x0000
    assert uci_params.dest_mac_to_uci("ab:01") == 0x01AB  # 소문자 허용


@pytest.mark.parametrize("bad", ["5FDD", "GG:00", "5F:DD:AA", "", "5F:D", " : "])
def test_dest_mac_to_uci_rejects_bad_format(bad: str) -> None:
    with pytest.raises(ValueError):
        uci_params.dest_mac_to_uci(bad)


# --- start_ranging 명령 시퀀스 인코딩 ---


def test_start_ranging_sends_init_config_start_frames() -> None:
    h = Harness()
    tr = h.connect()
    h.device.start_ranging()
    h.wait_worker()

    assert len(tr.written) == 3
    # SESSION_INIT: sid(LE 4B) + type Ranging(0)
    assert tr.written[0] == bytes([0x21, 0x00, 0x00, 0x05]) + SID_LE + b"\x00"
    # RANGE_START: sid(LE 4B)
    assert tr.written[2] == bytes([0x22, 0x00, 0x00, 0x04]) + SID_LE
    # 성공 시 RANGING 상태 이벤트
    assert [e.state for e in h.events] == [SessionState.RANGING]


def test_set_app_config_tlv_bytes_match_phone_profile() -> None:
    """폰(UwbDefaults.kt)과 바이트 단위 일치가 필요한 TLV들을 와이어 그대로 검증한다."""
    h = Harness(dest_mac="5F:DD")
    tr = h.connect()
    h.device.start_ranging()
    h.wait_worker()

    frame = tr.written[1]
    assert frame[:2] == bytes([0x21, 0x03])  # SESSION_SET_APP_CONFIG
    payload = frame[4:]
    assert payload[:4] == SID_LE
    assert payload[4] == 26  # 파라미터 개수 (uci_params.build_app_configs)
    tlvs = payload[5:]
    expected_tlvs = [
        b"\x00\x01\x01",  # DEVICE_TYPE = controller
        b"\x11\x01\x01",  # DEVICE_ROLE = initiator
        b"\x03\x01\x00",  # MULTI_NODE_MODE = unicast
        b"\x01\x01\x02",  # RANGING_ROUND_USAGE = DS-TWR deferred
        b"\x06\x02\x00\x00",  # DEVICE_MAC_ADDRESS = 00:00
        b"\x07\x02\x5f\xdd",  # DST_MAC_ADDRESS: '5F:DD' → 와이어 5F DD
        b"\x04\x01\x09",  # CHANNEL_NUMBER = 9
        b"\x22\x01\x01",  # SCHEDULE_MODE = time
        b"\x02\x01\x00",  # STS_CONFIG = static
        b"\x12\x01\x03",  # RFRAME_CONFIG = SP3
        b"\x27\x02\x08\x07",  # VENDOR_ID: 와이어 08 07
        b"\x28\x06\x01\x02\x03\x04\x05\x06",  # STATIC_STS_IV: 와이어 01..06
        b"\x0d\x01\x01",  # AOA_RESULT_REQ = all-enabled
        b"\x2b\x08" + b"\x00" * 8,  # UWB_INITIATION_TIME = 0
        b"\x14\x01\x09",  # PREAMBLE_CODE_INDEX = 9
        b"\x15\x01\x02",  # SFD_ID = 2
        b"\x08\x02\x60\x09",  # SLOT_DURATION = 2400
        b"\x09\x04\x78\x00\x00\x00",  # RANGING_DURATION = 120
        b"\x1b\x01\x06",  # SLOTS_PER_RR = 6
        b"\x32\x02\x00\x00",  # MAX_NUMBER_OF_MEASUREMENTS = 0
        b"\x2c\x01\x01",  # HOPPING_MODE = enabled
        b"\x13\x01\x01",  # RSSI_REPORTING = 1
        b"\x2d\x01\x00",  # BLOCK_STRIDE_LENGTH = 0
        b"\x05\x01\x01",  # NUMBER_OF_CONTROLEES = 1
        b"\x2e\x01\x0b",  # RESULT_REPORT_CONFIG = tof|azimuth|fom
        b"\x35\x01\x01",  # STS_LENGTH = 64심볼
    ]
    for tlv in expected_tlvs:
        assert tlv in tlvs, f"TLV 누락/불일치: {tlv.hex('.')}"
    assert len(tlvs) == sum(len(t) for t in expected_tlvs)  # 그 외 파라미터 없음


def test_start_ranging_with_invalid_dest_mac_emits_err_without_tx() -> None:
    h = Harness(dest_mac="붙여넣기실수")
    tr = h.connect()
    h.device.start_ranging()
    h.wait_worker()

    assert tr.written == []
    assert [e.state for e in h.events] == [SessionState.ERR]
    assert h.events[0].reason == "DEST_MAC_INVALID"


def test_set_app_config_failure_deinits_session() -> None:
    h = Harness()
    tr = h.connect()
    # SET_APP_CONFIG 응답을 InvalidParam(0x04) + 실패 목록 1건으로 교체
    tr.responses[(0x01, 0x03)] = bytes([0x04, 0x01, 0x27, 0x04])
    h.device.start_ranging()
    h.wait_worker()

    assert [e.state for e in h.events] == [SessionState.ERR]
    # 실패 후 SESSION_DEINIT까지 보냈는지 (좀비 세션 금지)
    assert tr.written[-1] == bytes([0x21, 0x01, 0x00, 0x04]) + SID_LE


# --- NTF 디코딩 → 콜백 ---


def test_range_data_ntf_reports_distance_with_no_angle() -> None:
    h = Harness()
    tr = h.connect()
    h.device.start_ranging()
    h.wait_worker()

    tr.inject_ntf(0x02, 0x00, make_twr_range_ntf(dist_cm=85))

    assert len(h.measurements) == 1
    m = h.measurements[0]
    assert m.dist_cm == 85
    assert m.angle_deg is None  # 보드 안테나 1개 → UI 'N/A' 정상
    assert m.raw == "DIST:85"
    assert m.ts == pytest.approx(time.time(), abs=5.0)


def test_range_data_ntf_uses_mac_address_as_target_id() -> None:
    h = Harness()
    tr = h.connect()
    h.device.start_ranging()
    h.wait_worker()

    tr.inject_ntf(0x02, 0x00, make_twr_range_ntf(dist_cm=85))

    assert len(h.measurements) == 1
    assert h.measurements[0].target_id == "5f:dd"


def test_range_data_ntf_with_failed_measurement_logs_only() -> None:
    h = Harness()
    tr = h.connect()
    tr.inject_ntf(0x02, 0x00, make_twr_range_ntf(dist_cm=0, meas_status=0x21))

    assert h.measurements == []
    assert any("측정 실패" in log for log in h.logs)


def test_session_status_ntf_active_maps_to_ranging() -> None:
    h = Harness()
    tr = h.connect()
    tr.inject_ntf(0x01, 0x02, make_session_status_ntf(state=0x02, reason=0x00))

    assert [e.state for e in h.events] == [SessionState.RANGING]


def test_session_status_ntf_idle_with_error_reason_maps_to_err() -> None:
    h = Harness()
    tr = h.connect()
    # Idle(0x03) + MaxRangingRoundRetryCountReached(0x01) = 펌웨어가 세션을 내림
    tr.inject_ntf(0x01, 0x02, make_session_status_ntf(state=0x03, reason=0x01))

    assert [e.state for e in h.events] == [SessionState.ERR]
    assert h.events[0].reason == "MaxRangingRoundRetryCountReached"


# --- 정지/해제 시 세션 정리 ---


def test_stop_ranging_sends_stop_then_deinit() -> None:
    h = Harness()
    tr = h.connect()
    h.device.start_ranging()
    h.wait_worker()
    tr.written.clear()

    h.device.stop_ranging()
    h.wait_worker()

    assert tr.written == [
        bytes([0x22, 0x01, 0x00, 0x04]) + SID_LE,  # RANGE_STOP
        bytes([0x21, 0x01, 0x00, 0x04]) + SID_LE,  # SESSION_DEINIT
    ]


def test_stop_without_session_is_noop() -> None:
    h = Harness()
    tr = h.connect()
    h.device.stop_ranging()
    h.wait_worker()

    assert tr.written == []
    assert any("STOP 무시" in log for log in h.logs)


def test_disconnect_cleans_up_active_session_and_transport() -> None:
    h = Harness()
    tr = h.connect()
    h.device.start_ranging()
    h.wait_worker()

    h.device.disconnect()

    stop_frame = bytes([0x22, 0x01, 0x00, 0x04]) + SID_LE
    deinit_frame = bytes([0x21, 0x01, 0x00, 0x04]) + SID_LE
    assert stop_frame in tr.written
    assert deinit_frame in tr.written
    assert not tr.alive
    assert not h.device.is_connected()


def test_set_dest_mac_rejects_bad_and_keeps_previous() -> None:
    h = Harness(dest_mac="5F:DD")
    assert h.device.set_dest_mac("A1:B2") is True
    assert h.device.set_dest_mac("oops") is False
    tr = h.connect()
    h.device.start_ranging()
    h.wait_worker()

    # 마지막으로 유효했던 'A1:B2'가 쓰였는지 (와이어 A1 B2)
    assert b"\x07\x02\xa1\xb2" in tr.written[1]
