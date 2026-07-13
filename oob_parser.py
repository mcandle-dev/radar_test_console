"""OOB_INFO 페이로드 파서 — 순수 함수. BLE·UI 없이 단위테스트되는 계층 (사양서 §4).

바이트 순서 주의 (이 도메인 최대 함정 — STS IV 반전 전례):
  uwb_address = 표시 순서 그대로 2B (반전 금지), session_id = uint32 LE.
"""

from dataclasses import dataclass
from typing import Literal, Optional

from oob_params import (
    ADDRESS_LEN,
    OFFSET_ADDRESS,
    OFFSET_SESSION_ID,
    OFFSET_VERSION,
    PAYLOAD_MIN_LEN,
    PROTOCOL_VERSION,
    SESSION_ID_LEN,
)

OobParseKind = Literal["ok", "invalid"]


@dataclass
class OobInfo:
    """OOB_INFO 페이로드 v1의 파싱 결과 값."""

    protocol_version: int
    uwb_address: str  # 폰 화면 표시와 동일한 "XX:XX" (대문자)
    session_id: int
    version_mismatch: bool  # version != 0x01 — UI가 "스펙 버전 확인" 경고 표시
    raw_hex: str  # 수신 원문 (진단 원칙: 원문 보존)


@dataclass
class OobParseResult:
    """parse_oob_info()의 반환값 — 실패해도 예외 대신 invalid + raw hex를 보존한다."""

    kind: OobParseKind
    info: Optional[OobInfo]
    error: Optional[str]
    raw_hex: str


def parse_oob_info(data: bytes) -> OobParseResult:
    """OOB_INFO 바이트열을 파싱한다. 길이 ≥7B만 검사, 추가 바이트는 무시 (전방 호환)."""
    raw_hex = data.hex(" ").upper()
    if len(data) < PAYLOAD_MIN_LEN:
        return OobParseResult(
            "invalid",
            None,
            f"payload too short: {len(data)}B < {PAYLOAD_MIN_LEN}B",
            raw_hex,
        )

    version = data[OFFSET_VERSION]
    address = _format_address(data[OFFSET_ADDRESS : OFFSET_ADDRESS + ADDRESS_LEN])
    session_id = int.from_bytes(
        data[OFFSET_SESSION_ID : OFFSET_SESSION_ID + SESSION_ID_LEN], "little"
    )
    info = OobInfo(
        protocol_version=version,
        uwb_address=address,
        session_id=session_id,
        # 상위 버전도 앞 7B는 v1 규칙으로 파싱 시도 + 경고 (하드 실패 금지 — 사양서 §4)
        version_mismatch=version != PROTOCOL_VERSION,
        raw_hex=raw_hex,
    )
    return OobParseResult("ok", info, None, raw_hex)


def build_oob_info(
    address: str, session_id: int, version: int = PROTOCOL_VERSION
) -> bytes:
    """parse_oob_info의 역함수 — 시뮬레이터·테스트용 7B 페이로드를 만든다 (사양서 §4)."""
    address_bytes = bytes.fromhex(address.replace(":", ""))  # 표시 순서 그대로
    if len(address_bytes) != ADDRESS_LEN:
        raise ValueError(
            f"주소는 'XX:XX' hex 2바이트 형식이어야 함 (입력: {address!r})"
        )
    return (
        bytes([version]) + address_bytes + session_id.to_bytes(SESSION_ID_LEN, "little")
    )


def _format_address(address_bytes: bytes) -> str:
    """주소 2B를 폰 화면 표기 "XX:XX"로 만든다 — 전송 순서 = 표시 순서 (반전 금지)."""
    return ":".join(f"{b:02X}" for b in address_bytes)
