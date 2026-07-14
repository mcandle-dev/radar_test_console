"""main.select_primary_target 단위테스트 — 근접 우선, 동률 시 RSSI 우선 규칙 검증."""

from main import select_primary_target
from models import Measurement

NOW = 1750000000.0
TIMEOUT_S = 2.0


def _m(dist_cm: int | None, rssi_dbm: float | None = None, ts: float = NOW) -> Measurement:
    return Measurement(dist_cm=dist_cm, angle_deg=0, raw="", ts=ts, rssi_dbm=rssi_dbm)


def test_closest_distance_wins() -> None:
    """거리가 다르면 무조건 최근접 타겟이 우선이다 (RSSI 무관)."""
    targets = {
        "a": _m(dist_cm=200, rssi_dbm=-40),
        "b": _m(dist_cm=100, rssi_dbm=-90),
    }
    assert select_primary_target(targets, NOW, TIMEOUT_S) == "b"


def test_tie_breaks_on_rssi() -> None:
    """거리가 같으면 RSSI가 더 큰(약한 신호가 아닌) 타겟이 우선이다."""
    targets = {
        "a": _m(dist_cm=150, rssi_dbm=-70),
        "b": _m(dist_cm=150, rssi_dbm=-50),
    }
    assert select_primary_target(targets, NOW, TIMEOUT_S) == "b"


def test_missing_rssi_loses_tiebreak() -> None:
    """거리가 같을 때 RSSI 미확보 타겟은 RSSI가 있는 타겟에게 밀린다."""
    targets = {
        "a": _m(dist_cm=150, rssi_dbm=None),
        "b": _m(dist_cm=150, rssi_dbm=-80),
    }
    assert select_primary_target(targets, NOW, TIMEOUT_S) == "b"


def test_distance_missing_excludes_target() -> None:
    """거리가 없는(dist_cm=None) 타겟은 후보에서 제외된다."""
    targets = {
        "a": _m(dist_cm=None, rssi_dbm=-10),
        "b": _m(dist_cm=200, rssi_dbm=-90),
    }
    assert select_primary_target(targets, NOW, TIMEOUT_S) == "b"


def test_stale_target_excluded() -> None:
    """무수신 타임아웃을 넘긴 타겟은 더 가까워도 후보에서 제외된다."""
    targets = {
        "a": _m(dist_cm=50, rssi_dbm=-30, ts=NOW - 10),  # 오래된 측정
        "b": _m(dist_cm=200, rssi_dbm=-90, ts=NOW),
    }
    assert select_primary_target(targets, NOW, TIMEOUT_S) == "b"


def test_no_candidates_returns_none() -> None:
    """후보가 하나도 없으면 None을 반환한다."""
    assert select_primary_target({}, NOW, TIMEOUT_S) is None
