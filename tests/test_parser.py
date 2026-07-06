"""parser.parse_line 단위테스트 — 하드웨어·UI 없이 파싱 규칙만 검증한다."""

import pytest

from models import SessionState
from parser import is_out_of_range, parse_line

FIXED_TS = 1750000000.0  # 순수성 확보용 고정 타임스탬프


class TestMeasurement:
    """측정 라인 파싱."""

    def test_normal(self) -> None:
        """정상 측정: DIST와 ANGLE 모두 추출."""
        r = parse_line("DIST:85,ANGLE:-12", ts=FIXED_TS)
        assert r.kind == "measurement"
        assert r.measurement is not None
        assert r.measurement.dist_cm == 85
        assert r.measurement.angle_deg == -12
        assert r.event is None
        assert r.error is None
        assert r.raw == "DIST:85,ANGLE:-12"
        assert r.ts == FIXED_TS

    def test_dist_only(self) -> None:
        """ANGLE 없으면 angle_deg=None (거리만 표시, UI는 'N/A')."""
        r = parse_line("DIST:120", ts=FIXED_TS)
        assert r.kind == "measurement"
        assert r.measurement is not None
        assert r.measurement.dist_cm == 120
        assert r.measurement.angle_deg is None

    def test_key_order_swapped(self) -> None:
        """키 순서 무관: ANGLE이 먼저 와도 동일하게 파싱."""
        r = parse_line("ANGLE:45,DIST:300", ts=FIXED_TS)
        assert r.kind == "measurement"
        assert r.measurement is not None
        assert r.measurement.dist_cm == 300
        assert r.measurement.angle_deg == 45

    def test_lowercase_keys(self) -> None:
        """대소문자 무관: 소문자 키도 파싱 성공."""
        r = parse_line("dist:85,angle:-12", ts=FIXED_TS)
        assert r.kind == "measurement"
        assert r.measurement is not None
        assert r.measurement.dist_cm == 85
        assert r.measurement.angle_deg == -12

    def test_unknown_fields_ignored(self) -> None:
        """미지 필드(RSSI, Q 등)는 무시하고 파싱 성공 (전방 호환)."""
        r = parse_line("DIST:85,ANGLE:-12,RSSI:-60,Q:99", ts=FIXED_TS)
        assert r.kind == "measurement"
        assert r.measurement is not None
        assert r.measurement.dist_cm == 85
        assert r.measurement.angle_deg == -12


class TestInvalid:
    """깨진 라인 — 앱을 죽이지 말고 원문·사유를 보존 (NFR-5)."""

    def test_broken_line(self) -> None:
        """알 수 없는 키만 있는 깨진 라인 → invalid + 원문 보존."""
        r = parse_line("DI?T:8x,ANGL", ts=FIXED_TS)
        assert r.kind == "invalid"
        assert r.measurement is None
        assert r.event is None
        assert r.error is not None
        assert r.raw == "DI?T:8x,ANGL"

    def test_empty_line(self) -> None:
        """빈 라인 → invalid."""
        r = parse_line("", ts=FIXED_TS)
        assert r.kind == "invalid"
        assert r.error is not None

    def test_whitespace_only_line(self) -> None:
        """공백뿐인 라인도 빈 라인으로 취급."""
        r = parse_line("   \t ", ts=FIXED_TS)
        assert r.kind == "invalid"

    def test_dist_not_a_number(self) -> None:
        """DIST 값이 숫자가 아니면 invalid (숫자 변환 실패)."""
        r = parse_line("DIST:abc,ANGLE:-12", ts=FIXED_TS)
        assert r.kind == "invalid"
        assert r.error is not None
        assert r.raw == "DIST:abc,ANGLE:-12"


class TestState:
    """세션/OOB 상태 라인 파싱 (요구사항정의서 5.4)."""

    @pytest.mark.parametrize(
        "name",
        ["SLEEP", "BLE_ADV", "BLE_CONN", "OOB_DONE", "RANGING"],
    )
    def test_normal_states(self, name: str) -> None:
        """정상 상태 5종: STATE:<값> → DeviceEvent(reason=None)."""
        r = parse_line(f"STATE:{name}", ts=FIXED_TS)
        assert r.kind == "state"
        assert r.event is not None
        assert r.event.state == SessionState(name)
        assert r.event.reason is None
        assert r.measurement is None

    def test_err_with_reason(self) -> None:
        """STATE:ERR는 REASON 사유를 동반한다."""
        r = parse_line("STATE:ERR,REASON:OOB_TIMEOUT", ts=FIXED_TS)
        assert r.kind == "state"
        assert r.event is not None
        assert r.event.state == SessionState.ERR
        assert r.event.reason == "OOB_TIMEOUT"

    def test_unknown_state_value(self) -> None:
        """모르는 STATE 값은 UNKNOWN으로 살려서 전달 (앱이 죽지 않음)."""
        r = parse_line("STATE:FUTURE_MODE", ts=FIXED_TS)
        assert r.kind == "state"
        assert r.event is not None
        assert r.event.state == SessionState.UNKNOWN


class TestCliJson:
    """Qorvo CLI 펌웨어(DW3_QM33 SDK)의 JSON 블록 파싱."""

    FULL_LINE = (
        '{"Block":123,"results":[{"Addr":"0x0001","Status":"Ok","D_cm":85,'
        '"LPDoA_deg":0.00,"LAoA_deg":-12.60,"LFoM":85,"RAoA_deg":0.00,"CFO_100ppm":-166}]}'
    )

    def test_full_cli_line(self) -> None:
        """실제 CLI 출력 한 줄: D_cm→거리, LAoA_deg→각도(반올림 정수)."""
        r = parse_line(self.FULL_LINE, ts=FIXED_TS)
        assert r.kind == "measurement"
        assert r.measurement is not None
        assert r.measurement.dist_cm == 85
        assert r.measurement.angle_deg == -13
        assert r.error is None
        assert r.raw == self.FULL_LINE

    def test_no_angle_key(self) -> None:
        """LAoA_deg 없으면 angle_deg=None (DWM3001CDK는 AoA 미지원 가능)."""
        r = parse_line(
            '{"Block":5,"results":[{"Status":"Ok","D_cm":120}]}', ts=FIXED_TS
        )
        assert r.kind == "measurement"
        assert r.measurement is not None
        assert r.measurement.dist_cm == 120
        assert r.measurement.angle_deg is None

    def test_dist_float_rounded(self) -> None:
        """D_cm가 실수여도 반올림 정수로 수용."""
        r = parse_line('{"results":[{"D_cm":85.6}]}', ts=FIXED_TS)
        assert r.kind == "measurement"
        assert r.measurement is not None
        assert r.measurement.dist_cm == 86

    def test_top_level_dist(self) -> None:
        """results 배열 없이 최상위에 D_cm가 있어도 측정으로 인정."""
        r = parse_line('{"D_cm":42,"LAoA_deg":7.2}', ts=FIXED_TS)
        assert r.kind == "measurement"
        assert r.measurement is not None
        assert r.measurement.dist_cm == 42
        assert r.measurement.angle_deg == 7

    def test_rx_timeout_block(self) -> None:
        """측정 없는 블록(Rx timeout 등) → invalid, Status를 사유에 보존."""
        r = parse_line(
            '{"Block":124,"results":[{"Addr":"0x0001","Status":"Rx timeout"}]}',
            ts=FIXED_TS,
        )
        assert r.kind == "invalid"
        assert r.error is not None
        assert "Rx timeout" in r.error

    def test_empty_results(self) -> None:
        """빈 results 배열 → invalid (앱은 죽지 않음)."""
        r = parse_line('{"Block":1,"results":[]}', ts=FIXED_TS)
        assert r.kind == "invalid"
        assert r.error is not None

    def test_broken_json(self) -> None:
        """잘린/깨진 JSON → invalid + 원문 보존."""
        raw = '{"Block":123,"results":[{"D_cm":85'
        r = parse_line(raw, ts=FIXED_TS)
        assert r.kind == "invalid"
        assert r.error is not None
        assert r.raw == raw

    def test_json_root_not_object(self) -> None:
        """오브젝트가 아닌 JSON({로 시작하지 않으면 기존 분기로 감) — 배열 원문 등."""
        r = parse_line('{"oops": 1}', ts=FIXED_TS)
        assert r.kind == "invalid"

    def test_out_of_range_json_dist(self) -> None:
        """JSON 경로도 범위 초과 판별과 연동된다."""
        r = parse_line('{"results":[{"D_cm":9999}]}', ts=FIXED_TS)
        assert r.kind == "measurement"
        assert r.measurement is not None
        assert is_out_of_range(r.measurement)


class TestOutOfRange:
    """범위 초과 — 파싱은 성공, UI 경고용 판별만 참."""

    def test_dist_over_max(self) -> None:
        """거리 5000cm 초과 → 성공 파싱 + out_of_range=True."""
        r = parse_line("DIST:9999,ANGLE:0", ts=FIXED_TS)
        assert r.kind == "measurement"
        assert r.measurement is not None
        assert r.measurement.dist_cm == 9999
        assert is_out_of_range(r.measurement)

    def test_angle_over_max(self) -> None:
        """각도 ±90° 초과 → 성공 파싱 + out_of_range=True."""
        r = parse_line("DIST:100,ANGLE:135", ts=FIXED_TS)
        assert r.kind == "measurement"
        assert r.measurement is not None
        assert is_out_of_range(r.measurement)

    def test_in_range_is_not_flagged(self) -> None:
        """정상 범위 값은 경고 대상이 아니다."""
        r = parse_line("DIST:85,ANGLE:-12", ts=FIXED_TS)
        assert r.measurement is not None
        assert not is_out_of_range(r.measurement)
