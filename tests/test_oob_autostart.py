"""OOB 자동 시작 대기 상태의 단위 테스트."""

from types import SimpleNamespace
from typing import Any

from main import OobAutoStartGate, RadarApp


class FakeDevice:
    """연결 상태와 start_ranging 호출 횟수만 제공하는 UI 테스트 대역."""

    def __init__(self) -> None:
        self.connected = False
        self.start_count = 0

    def is_connected(self) -> bool:
        """테스트가 설정한 연결 상태를 반환한다."""
        return self.connected

    def start_ranging(self) -> None:
        """실제 UCI 대신 시작 호출 횟수만 센다."""
        self.start_count += 1


def test_disconnected_request_stays_pending_until_connected() -> None:
    """OOB 주소를 먼저 받아도 UCI 연결 후 한 번 시작할 수 있게 요청을 보존한다."""
    gate = OobAutoStartGate()
    gate.request()

    assert not gate.consume_if_ready(connected=False, session_ready=True, ranging=False)
    assert gate.pending
    assert gate.consume_if_ready(connected=True, session_ready=True, ranging=False)
    assert not gate.pending


def test_consumed_request_does_not_start_twice() -> None:
    """연결 완료 처리가 반복돼도 자동 시작 요청은 한 번만 소비된다."""
    gate = OobAutoStartGate(pending=True)

    assert gate.consume_if_ready(connected=True, session_ready=True, ranging=False)
    assert not gate.consume_if_ready(connected=True, session_ready=True, ranging=False)


def test_session_mismatch_blocks_until_user_resolves_it() -> None:
    """SessionID 불일치 상태에서는 연결돼 있어도 시작하지 않고 승인을 기다린다."""
    gate = OobAutoStartGate(pending=True)

    assert not gate.consume_if_ready(connected=True, session_ready=False, ranging=False)
    assert gate.pending
    assert gate.consume_if_ready(connected=True, session_ready=True, ranging=False)


def test_cancel_discards_pending_request() -> None:
    """수동 모드 복귀 시 이전 OOB 자동 시작 요청을 재사용하지 않는다."""
    gate = OobAutoStartGate(pending=True)

    gate.cancel()

    assert not gate.consume_if_ready(connected=True, session_ready=True, ranging=False)


def test_radar_app_retries_pending_start_once_after_connection() -> None:
    """UI 오케스트레이션이 미연결 요청을 보존하고 연결 직후 한 번 호출한다."""
    app: Any = RadarApp.__new__(RadarApp)
    device = FakeDevice()
    logs: list[str] = []
    app.device = device
    app.log = SimpleNamespace(append_sys=logs.append)
    app.autostart_sw = SimpleNamespace(value=True)
    app.oob_led = SimpleNamespace(bgcolor=None)
    app.oob_status = SimpleNamespace(value="", color=None)
    app._oob_autostart = OobAutoStartGate(pending=True)
    app._pending_session_id = None
    app._ranging = False

    assert not app._try_oob_autostart()
    assert device.start_count == 0
    assert "UCI 연결 대기" in app.oob_status.value

    device.connected = True
    assert app._try_oob_autostart()
    assert device.start_count == 1
    assert not app._try_oob_autostart()
    assert device.start_count == 1
