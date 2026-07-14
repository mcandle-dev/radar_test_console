"""데이터 모델 — 측정값·세션 이벤트·파싱 결과 (코딩가이드 3.1 + CLAUDE.md 확정 사항)."""

from dataclasses import dataclass
from enum import Enum
from typing import Literal, Optional


class SessionState(str, Enum):
    """BLE OOB ~ UWB 세션 단계 (요구사항정의서 5.4의 STATE 값)."""

    SLEEP = "SLEEP"
    BLE_ADV = "BLE_ADV"
    BLE_CONN = "BLE_CONN"
    OOB_DONE = "OOB_DONE"
    RANGING = "RANGING"
    ERR = "ERR"
    UNKNOWN = "UNKNOWN"


@dataclass
class Measurement:
    """한 번의 거리/각도 측정값."""

    dist_cm: Optional[int]  # 거리(cm). 없으면 None
    angle_deg: Optional[int]  # 각도(°, 0=정면). 미지원이면 None
    raw: str  # 원시 수신 라인 (디버깅용)
    ts: float  # 수신 시각(epoch)
    target_id: Optional[str] = None  # 다중 타겟 표시용 식별자
    rssi_dbm: Optional[float] = None  # 신호 세기(dBm). 미지원이면 None


@dataclass
class DeviceEvent:
    """세션 상태/에러 이벤트."""

    state: SessionState
    reason: Optional[str]  # ERR일 때 사유 (예: "OOB_TIMEOUT")
    raw: str
    ts: float


ParseKind = Literal["measurement", "state", "invalid"]


@dataclass
class ParseResult:
    """parse_line()의 반환값 — 라인 종류에 따라 measurement/event 중 하나만 채워진다."""

    kind: ParseKind
    measurement: Optional[Measurement]
    event: Optional[DeviceEvent]
    raw: str
    error: Optional[str]
    ts: float
