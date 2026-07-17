"""oob_parser.parse_oob_info 단위테스트 — 사양서 §4의 바이트 순서 규칙을 고정한다."""

import pytest

from oob_parser import build_oob_info, parse_oob_info

# 사양서 §4 예시 그대로: version=01, 주소 "5F:DD" → 5F DD, session_id 42 → 2A 00 00 00
PAYLOAD_V1 = bytes.fromhex("015FDD2A000000")


class TestNormal:
    """정상 7B 페이로드."""

    def test_spec_example(self) -> None:
        """사양서 예시 페이로드가 그대로 파싱된다."""
        r = parse_oob_info(PAYLOAD_V1)
        assert r.kind == "ok"
        assert r.info is not None
        assert r.info.protocol_version == 0x01
        assert r.info.uwb_address == "5F:DD"
        assert r.info.session_id == 42
        assert r.info.version_mismatch is False
        assert r.error is None

    def test_raw_hex_preserved(self) -> None:
        """원문 hex가 진단용으로 보존된다."""
        r = parse_oob_info(PAYLOAD_V1)
        assert r.raw_hex == "01 5F DD 2A 00 00 00"
        assert r.info is not None
        assert r.info.raw_hex == r.raw_hex


class TestByteOrder:
    """바이트 순서 — 이 도메인 최대 함정 (STS IV 반전 전례)."""

    def test_address_not_reversed(self) -> None:
        """주소는 전송 순서 = 표시 순서. 5F DD → "5F:DD" (❌ "DD:5F" 아님)."""
        r = parse_oob_info(bytes.fromhex("015FDD2A000000"))
        assert r.info is not None
        assert r.info.uwb_address == "5F:DD"
        assert r.info.uwb_address != "DD:5F"

    def test_session_id_little_endian(self) -> None:
        """session_id는 LE: 2A 00 00 00 → 42 (❌ 0x2A000000 아님)."""
        r = parse_oob_info(bytes.fromhex("015FDD2A000000"))
        assert r.info is not None
        assert r.info.session_id == 42

    def test_session_id_le_multibyte(self) -> None:
        """다바이트 값도 LE: 78 56 34 12 → 0x12345678."""
        r = parse_oob_info(bytes.fromhex("015FDD78563412"))
        assert r.info is not None
        assert r.info.session_id == 0x12345678


class TestInvalid:
    """길이 미달 — 하드 실패 대신 invalid + raw hex 보존."""

    def test_too_short(self) -> None:
        """7B 미만이면 invalid, 원문 hex는 로그용으로 남는다."""
        r = parse_oob_info(bytes.fromhex("015FDD2A"))
        assert r.kind == "invalid"
        assert r.info is None
        assert r.error is not None
        assert "4B" in r.error
        assert r.raw_hex == "01 5F DD 2A"

    def test_empty(self) -> None:
        """빈 페이로드도 예외 없이 invalid."""
        r = parse_oob_info(b"")
        assert r.kind == "invalid"
        assert r.raw_hex == ""


class TestForwardCompat:
    """전방 호환 — 추가 바이트 무시, 상위 버전은 경고만."""

    def test_extra_bytes_ignored(self) -> None:
        """7B 뒤에 붙는 바이트는 무시하고 앞 7B만 파싱한다."""
        r = parse_oob_info(PAYLOAD_V1 + bytes.fromhex("FFEE"))
        assert r.kind == "ok"
        assert r.info is not None
        assert r.info.uwb_address == "5F:DD"
        assert r.info.session_id == 42

    def test_version_0x02_warns(self) -> None:
        """version 0x02는 v1 규칙으로 파싱 성공 + version_mismatch 플래그 (하드 실패 금지)."""
        r = parse_oob_info(bytes.fromhex("025FDD2A000000"))
        assert r.kind == "ok"
        assert r.info is not None
        assert r.info.version_mismatch is True
        assert r.info.protocol_version == 0x02
        assert r.info.uwb_address == "5F:DD"
        assert r.info.session_id == 42


class TestBuild:
    """build_oob_info — 시뮬레이터·테스트용 인코더 (parse의 역함수)."""

    def test_spec_example_bytes(self) -> None:
        """ "5F:DD"/42 → 사양서 예시 바이트열과 정확히 일치."""
        assert build_oob_info("5F:DD", 42) == PAYLOAD_V1

    def test_round_trip(self) -> None:
        """build → parse 왕복 시 값이 보존된다 (바이트 순서 회귀 방지)."""
        r = parse_oob_info(build_oob_info("A1:B2", 0x12345678))
        assert r.info is not None
        assert r.info.uwb_address == "A1:B2"
        assert r.info.session_id == 0x12345678

    def test_bad_address_rejected(self) -> None:
        """2바이트가 아닌 주소는 ValueError."""
        with pytest.raises(ValueError):
            build_oob_info("5F:DD:AA", 42)
