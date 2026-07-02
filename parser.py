"""UART 라인 파서 — 순수 함수. 하드웨어·UI 없이 단위테스트되는 계층 (요구사항정의서 5장)."""

import time
from typing import Dict, Optional

from models import DeviceEvent, Measurement, ParseResult, SessionState

# 정상 범위 (요구사항정의서 5.2) — 벗어나도 파싱은 성공, UI가 경고색으로 표시한다.
DIST_MIN_CM = 0
DIST_MAX_CM = 5000
ANGLE_MIN_DEG = -90
ANGLE_MAX_DEG = 90

FIELD_SEP = ","
KEY_VALUE_SEP = ":"


def parse_line(line: str, ts: Optional[float] = None) -> ParseResult:
    """수신 라인 한 줄을 측정/세션/무효 중 하나로 분류해 ParseResult로 반환한다."""
    if ts is None:
        ts = time.time()
    raw = line.strip()
    if not raw:
        return _invalid(raw, "empty line", ts)

    fields = _split_fields(raw)
    # STATE 키가 있으면 세션 이벤트, DIST 키가 있으면 측정으로 분기 (키 순서·대소문자 무관)
    if "STATE" in fields:
        return _parse_state(fields, raw, ts)
    if "DIST" in fields:
        return _parse_measurement(fields, raw, ts)
    return _invalid(raw, "no known keys (DIST/STATE)", ts)


def is_out_of_range(m: Measurement) -> bool:
    """측정값이 정상 범위를 벗어났는지 판별한다 (UI 경고색 표시용)."""
    if m.dist_cm is not None and not (DIST_MIN_CM <= m.dist_cm <= DIST_MAX_CM):
        return True
    if m.angle_deg is not None and not (ANGLE_MIN_DEG <= m.angle_deg <= ANGLE_MAX_DEG):
        return True
    return False


def _split_fields(raw: str) -> Dict[str, str]:
    """ "KEY:VALUE,KEY:VALUE" 형식을 대문자 키 dict로 분해한다. 형식이 아닌 토큰은 버린다."""
    fields: Dict[str, str] = {}
    for token in raw.split(FIELD_SEP):
        if KEY_VALUE_SEP not in token:
            continue
        key, value = token.split(KEY_VALUE_SEP, 1)
        fields[key.strip().upper()] = value.strip()
    return fields


def _parse_measurement(fields: Dict[str, str], raw: str, ts: float) -> ParseResult:
    """DIST(필수)/ANGLE(선택)을 정수로 변환한다. 미지 필드(RSSI 등)는 무시 = 전방 호환."""
    dist_cm = _to_int(fields["DIST"])
    if dist_cm is None:
        return _invalid(raw, f"DIST is not an integer: {fields['DIST']!r}", ts)

    angle_deg: Optional[int] = None
    if "ANGLE" in fields:
        angle_deg = _to_int(fields["ANGLE"])
        if angle_deg is None:
            return _invalid(raw, f"ANGLE is not an integer: {fields['ANGLE']!r}", ts)

    measurement = Measurement(dist_cm=dist_cm, angle_deg=angle_deg, raw=raw, ts=ts)
    return ParseResult("measurement", measurement, None, raw, None, ts)


def _parse_state(fields: Dict[str, str], raw: str, ts: float) -> ParseResult:
    """STATE 값을 SessionState로 매핑한다. 모르는 값은 UNKNOWN으로 살려서 전달."""
    value = fields["STATE"].upper()
    try:
        state = SessionState(value)
    except ValueError:
        state = SessionState.UNKNOWN
    reason = fields.get("REASON")
    event = DeviceEvent(state=state, reason=reason, raw=raw, ts=ts)
    return ParseResult("state", None, event, raw, None, ts)


def _to_int(value: str) -> Optional[int]:
    """문자열을 정수로 변환한다. 실패하면 None (호출부가 invalid로 분류)."""
    try:
        return int(value)
    except ValueError:
        return None


def _invalid(raw: str, error: str, ts: float) -> ParseResult:
    """깨진 라인 — 앱을 죽이지 않고 원문과 사유를 보존한다 (NFR-5)."""
    return ParseResult("invalid", None, None, raw, error, ts)
