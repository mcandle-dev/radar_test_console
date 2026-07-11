"""UCI 세션 파라미터 — 폰 앱(uwb_controlee_app)과 바이트 단위로 일치해야 하는 값의 단일 출처.

기준: sasodoma/uwb-ranging @ aad72a0 의 run_fira_twr.py 기본 실행
      (`--mac 00:00 --dest-mac <폰주소>`)과 동일.
대조 상대: D:/dev/uwb_controlee_app 의 UwbDefaults.kt (CONFIG_UNICAST_DS_TWR + FREQUENT).
대조표: uwb_controlee_app/docs/파라미터_대조_4단계.md

주의: 여기 값 하나라도 폰과 다르면 에러 없이 '무증상 실패'(세션은 붙는데 측정 0건)한다.
      SESSION_ID와 dest MAC(폰 주소)만 가변, 나머지는 고정.

multicast: 폰 주소를 쉼표로 여러 개 넣으면 one-to-many(MULTI_NODE_MODE=1) 세션이 된다.
      이때 폰 쪽도 multicast 프로파일(CONFIG_MULTICAST_DS_TWR)로 시작해야 측정이 나온다.
"""

import re
from typing import List, Tuple, Union

from uci import App

# --- 가변 파라미터 (UI에서 변경 가능) ---
SESSION_ID = 42  # 폰 앱 기본 Session ID와 쌍
DEFAULT_DEST_MAC = "00:00"  # 폰 주소 placeholder — 실제 값은 폰 앱 화면에서 확인해 입력
MAX_CONTROLEES = 5  # 동시 controlee(타겟) 상한 — UI 타겟 표시 한도와 동일

# --- 역할/토폴로지 (폰=controlee/responder 와 상보) ---
DEVICE_TYPE = 1  # controller
DEVICE_ROLE = 1  # initiator
DEVICE_MAC_ADDRESS = 0x0000  # 폰 앱의 '보드 MAC' 기본값 "00:00"과 쌍
MULTI_NODE_MODE_UNICAST = 0  # controlee 1개
MULTI_NODE_MODE_ONE_TO_MANY = 1  # controlee 2개 이상 (multicast)
RANGING_ROUND_USAGE = 2  # DS-TWR deferred
SCHEDULE_MODE = 1  # time-based
RFRAME_CONFIG = 3  # SP3

# --- RF 설정 ---
CHANNEL_NUMBER = 9
PREAMBLE_CODE_INDEX = 9
SFD_ID = 2

# --- Static STS (와이어 바이트 = 08 07 / 01 02 03 04 05 06 — 폰 sessionKeyInfo와 일치) ---
STS_CONFIG = 0  # static
VENDOR_ID = 0x0708  # LE 인코딩 → 와이어 08 07
STATIC_STS_IV = 0x060504030201  # LE 인코딩 → 와이어 01 02 03 04 05 06

# --- 타이밍/리포트 ---
RANGING_DURATION = 120  # ms (= 폰 RANGING_UPDATE_RATE_FREQUENT)
SLOT_DURATION = 2400  # RSTU (2ms)
SLOTS_PER_RR = 6
HOPPING_MODE = 1  # enabled
RSSI_REPORTING = 1
AOA_RESULT_REQ = 1  # all-enabled (보드는 안테나 1개 — 각도는 폰 쪽만 유효)
STS_LENGTH = 1  # 64 심볼
UWB_INITIATION_TIME = 0
MAX_NUMBER_OF_MEASUREMENTS = 0  # 무제한
BLOCK_STRIDE_LENGTH = 0
RESULT_REPORT_CONFIG = 0x0B  # tof(1) | azimuth(2) | fom(8) — run_fira_twr.py 기본

_DEST_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}$")

# session_set_app_config 에 넣는 (파라미터, 값) 목록의 원소 타입
AppConfig = Tuple[App, Union[int, List[int]]]


def dest_mac_to_uci(mac: str) -> int:
    """'5F:DD' 표기 폰 주소를 UCI 리틀엔디언 정수로 변환한다 (sasodoma 변환 로직 그대로).

    예: '5F:DD' → int('DD5F',16) = 0xDD5F → 와이어 2바이트(LE) = 5F DD.
    결과적으로 양쪽 화면에 보이는 문자열이 같으면 무선 구간 바이트도 일치한다.
    """
    mac = mac.strip()
    if not _DEST_MAC_RE.match(mac):
        raise ValueError(f"폰 주소는 'XX:XX' hex 2바이트 형식이어야 함 (입력: {mac!r})")
    return int(mac[-2:] + mac[0:2], 16)


def parse_dest_macs(text: str) -> List[int]:
    """쉼표/공백으로 구분한 폰 주소 목록을 UCI 정수 리스트로 변환한다.

    1개면 unicast, 2개 이상이면 one-to-many(multicast) 세션이 된다.
    빈 입력·형식 오류·중복·MAX_CONTROLEES 초과는 ValueError.
    """
    parts = [p for p in re.split(r"[,\s]+", text.strip()) if p]
    if not parts:
        raise ValueError("폰 주소가 비어 있음 — 'XX:XX' hex 형식으로 입력")
    if len(parts) > MAX_CONTROLEES:
        raise ValueError(f"폰 주소는 최대 {MAX_CONTROLEES}개까지 (입력 {len(parts)}개)")
    macs = [dest_mac_to_uci(p) for p in parts]
    if len(set(macs)) != len(macs):
        raise ValueError(f"폰 주소에 중복이 있음: {text!r}")
    return macs


def split_dest_macs(text: str) -> List[str]:
    """입력 텍스트를 개별 'xx:xx' 표시 문자열(소문자)로 분해한다.

    UCI RANGE_DATA의 target_id 표기(소문자 'xx:xx')와 같아서 UI 행 선등록에 쓴다.
    형식 오류·중복·초과는 parse_dest_macs와 동일하게 ValueError.
    """
    parse_dest_macs(text)  # 형식/중복/개수 검증 재사용
    return [p.lower() for p in re.split(r"[,\s]+", text.strip()) if p]


def build_app_configs(dest_macs_uci: List[int]) -> List[AppConfig]:
    """SESSION_SET_APP_CONFIG 파라미터 목록을 만든다.

    순서·구성은 run_fira_twr.py 기본 실행과 동일 (진단·키 회전 옵션은 미사용이라 제외).
    controlee 수에 따라 unicast/one-to-many(multicast)를 자동 선택한다.
    """
    multi_node_mode = (
        MULTI_NODE_MODE_UNICAST
        if len(dest_macs_uci) == 1
        else MULTI_NODE_MODE_ONE_TO_MANY
    )
    return [
        # Fira 필수 최소 구성
        (App.DeviceType, DEVICE_TYPE),
        (App.DeviceRole, DEVICE_ROLE),
        (App.MultiNodeMode, multi_node_mode),
        (App.RangingRoundUsage, RANGING_ROUND_USAGE),
        (App.DeviceMacAddress, DEVICE_MAC_ADDRESS),
        # 추가 구성
        (App.ChannelNumber, CHANNEL_NUMBER),
        (App.ScheduleMode, SCHEDULE_MODE),
        (App.StsConfig, STS_CONFIG),
        (App.RframeConfig, RFRAME_CONFIG),
        (App.ResultReportConfig, RESULT_REPORT_CONFIG),
        (App.VendorId, VENDOR_ID),
        (App.StaticStsIv, STATIC_STS_IV),
        (App.AoaResultReq, AOA_RESULT_REQ),
        (App.UwbInitiationTime, UWB_INITIATION_TIME),
        (App.PreambleCodeIndex, PREAMBLE_CODE_INDEX),
        (App.SfdId, SFD_ID),
        (App.SlotDuration, SLOT_DURATION),
        (App.RangingInterval, RANGING_DURATION),
        (App.SlotsPerRr, SLOTS_PER_RR),
        (App.MaxNumberOfMeasurements, MAX_NUMBER_OF_MEASUREMENTS),
        (App.HoppingMode, HOPPING_MODE),
        (App.RssiReporting, RSSI_REPORTING),
        (App.BlockStrideLength, BLOCK_STRIDE_LENGTH),
        (App.NumberOfControlees, len(dest_macs_uci)),
        (App.DstMacAddress, dest_macs_uci),  # UCI 스펙상 리스트 (2바이트 × N)
        (App.StsLength, STS_LENGTH),
    ]
