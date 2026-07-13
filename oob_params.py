"""BLE OOB 계약 상수 — 사양서(docs/oob/BLE_OOB_인터페이스_사양서.md v0.2)와
폰 앱(uwb_controlee_app/UwbDefaults.kt)이 바이트 단위로 일치해야 하는 값의 단일 출처.

주의: 여기 값은 사양서 개정(버전 업 + 양 리포 동시 커밋) 없이 바꾸지 말 것.
"""

# --- GATT 스키마 (사양서 §3 확정값) ---
# bleak는 UUID를 소문자로 정규화하므로 소문자로 둔다 (비교 시 대소문자 함정 방지).
SERVICE_UUID = "5f1d0001-9a8b-4c7d-b2e3-6f4a5d8c9b0a"
OOB_INFO_CHAR_UUID = "5f1d0002-9a8b-4c7d-b2e3-6f4a5d8c9b0a"
ADV_LOCAL_NAME = "UWB-OOB"  # 광고 Local Name (스캔 필터는 Service UUID가 1차)

# --- OOB_INFO 페이로드 v1 (사양서 §4 — 7B 고정) ---
PROTOCOL_VERSION = 0x01
PAYLOAD_MIN_LEN = 7  # 이상만 검사, 뒤 추가 바이트는 무시 (전방 호환)
OFFSET_VERSION = 0  # uint8
OFFSET_ADDRESS = 1  # raw 2B — 표시 순서 그대로, 반전 금지
OFFSET_SESSION_ID = 3  # uint32 little-endian
ADDRESS_LEN = 2
SESSION_ID_LEN = 4

# --- 타임아웃 (사양서 §7 / 검수 §8) ---
SCAN_TIMEOUT_S = 10.0  # [OOB 스캔] 1회 스캔 시간
CONNECT_TIMEOUT_S = 10.0  # GATT 연결 타임아웃 → 초과 시 ERR,REASON:BLE_CONN_FAIL
