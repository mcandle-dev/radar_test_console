"""UART 라인 파서 — 순수 함수. 하드웨어·UI 없이 단위테스트되는 계층 (요구사항정의서 5장)."""

import json
import time
from typing import Any, Dict, Optional

from models import DeviceEvent, Measurement, ParseResult, SessionState

# 정상 범위 (요구사항정의서 5.2) — 벗어나도 파싱은 성공, UI가 경고색으로 표시한다.
DIST_MIN_CM = 0
DIST_MAX_CM = 5000
ANGLE_MIN_DEG = -90
ANGLE_MAX_DEG = 90

FIELD_SEP = ","
KEY_VALUE_SEP = ":"

# Qorvo DWM3001CDK CLI 펌웨어(DW3_QM33 SDK)의 JSON 블록 키 — 소문자로 비교한다.
# 예: {"Block":123,"results":[{"Addr":"0x0001","Status":"Ok","D_cm":85,"LAoA_deg":-12.5}]}
JSON_PREFIX = "{"
JSON_RESULTS_KEY = "results"
JSON_DIST_KEY = "d_cm"
JSON_ANGLE_KEY = "laoa_deg"
JSON_STATUS_KEY = "status"


def parse_line(line: str, ts: Optional[float] = None) -> ParseResult:
    """수신 라인 한 줄을 측정/세션/무효 중 하나로 분류해 ParseResult로 반환한다."""
    if ts is None:
        ts = time.time()
    raw = line.strip()
    if not raw:
        return _invalid(raw, "empty line", ts)

    # Qorvo CLI 펌웨어는 한 블록을 JSON 오브젝트 한 줄로 출력한다 → 전용 분기
    if raw.startswith(JSON_PREFIX):
        return _parse_json_line(raw, ts)

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


def _parse_json_line(raw: str, ts: float) -> ParseResult:
    """Qorvo CLI 펌웨어의 JSON 블록에서 첫 유효 측정(D_cm/LAoA_deg)을 추출한다."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _invalid(raw, f"JSON decode failed: {exc}", ts)
    if not isinstance(data, dict):
        return _invalid(raw, "JSON root is not an object", ts)

    entry = _find_json_measurement(data)
    if entry is None:
        return _invalid(raw, _json_failure_reason(data), ts)

    dist_cm = _number_to_int(entry[JSON_DIST_KEY])
    if dist_cm is None:
        return _invalid(raw, f"D_cm is not a number: {entry[JSON_DIST_KEY]!r}", ts)

    angle_deg: Optional[int] = None
    if JSON_ANGLE_KEY in entry:
        angle_deg = _number_to_int(entry[JSON_ANGLE_KEY])
        if angle_deg is None:
            return _invalid(raw, f"LAoA_deg is not a number: {entry[JSON_ANGLE_KEY]!r}", ts)

    measurement = Measurement(dist_cm=dist_cm, angle_deg=angle_deg, raw=raw, ts=ts)
    return ParseResult("measurement", measurement, None, raw, None, ts)


def _find_json_measurement(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """results 배열(없으면 최상위)에서 D_cm 키를 가진 첫 항목을 소문자 키 dict로 반환한다."""
    root = _lower_keys(data)
    results = root.get(JSON_RESULTS_KEY)
    if isinstance(results, list):
        candidates = [_lower_keys(e) for e in results if isinstance(e, dict)]
    else:
        candidates = [root]
    for entry in candidates:
        if JSON_DIST_KEY in entry:
            return entry
    return None


def _json_failure_reason(data: Dict[str, Any]) -> str:
    """측정이 없는 JSON 블록의 사유를 만든다 (Rx timeout 등 Status를 로그로 살린다)."""
    root = _lower_keys(data)
    results = root.get(JSON_RESULTS_KEY)
    if not isinstance(results, list):
        return "no D_cm in JSON"
    statuses = [
        str(_lower_keys(e).get(JSON_STATUS_KEY, "?")) for e in results if isinstance(e, dict)
    ]
    if not statuses:
        return "empty JSON results"
    return f"no D_cm in JSON results (Status: {', '.join(statuses)})"


def _lower_keys(d: Dict[str, Any]) -> Dict[str, Any]:
    """dict 키를 소문자로 통일한다 (JSON 키 대소문자 무관 비교용)."""
    return {str(k).lower(): v for k, v in d.items()}


def _number_to_int(value: Any) -> Optional[int]:
    """JSON 숫자(int/float/숫자 문자열)를 반올림 정수로 변환한다. 실패하면 None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(round(value))
    if isinstance(value, str):
        try:
            return int(round(float(value)))
        except ValueError:
            return None
    return None


def _to_int(value: str) -> Optional[int]:
    """문자열을 정수로 변환한다. 실패하면 None (호출부가 invalid로 분류)."""
    try:
        return int(value)
    except ValueError:
        return None


def _invalid(raw: str, error: str, ts: float) -> ParseResult:
    """깨진 라인 — 앱을 죽이지 않고 원문과 사유를 보존한다 (NFR-5)."""
    return ParseResult("invalid", None, None, raw, error, ts)
