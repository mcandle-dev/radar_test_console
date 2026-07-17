# BLE OOB 인터페이스 사양서 — 폰 주소 자동 교환

> **버전: v0.2 (Draft)** · 작성일 2026-07-13 · 마스터 위치: `radar_test_console/docs/oob/` (사본: `uwb_controlee_app/docs/oob/`)
> **양쪽 리포에 동일 사본을 커밋하고, 개정 시 반드시 버전을 올려 양쪽을 함께 갱신할 것.**
>
> **한 줄 정의:** 폰 앱(uwb_controlee_app)이 BLE GATT peripheral로 자신의 UWB 주소·Session ID를 광고하고,
> PC 콘솔(radar_test_console)이 BLE central(bleak)로 이를 자동 수신하여 수동 입력을 제거한다.

---

## 1. 목적 및 범위

| 구분 | 내용 |
|---|---|
| 목적 | 현행 1차 방식(폰 화면 주소를 눈으로 보고 콘솔에 수동 입력)을 자동화. "앱 Start → 콘솔 [OOB 스캔] → 주소 자동 수신 → (옵션) 자동 레인징" |
| In-Scope | **연결 방식 선택 UI(수동/OOB 자동 — 양쪽 모두, 기본 수동)**, BLE 광고, GATT 연결, OOB_INFO(주소+SessionID+버전) Read/Notify, 주소 재발급 갱신, 다중 폰 스캔 목록 |
| Out-of-Scope | 보안 페어링/본딩(브링업 도구 — Just Works·평문), UWB 파라미터 전체 협상(FiRa CSML), 콘솔→폰 제어(Write), 백그라운드 광고 |
| 하위 호환 | **OOB는 부가 경로다.** BLE 실패 시 기존 수동 입력 흐름이 그대로 동작해야 한다 (양쪽 모두). |

## 2. 역할 정의

```
[폰: uwb_controlee_app]                     [PC: radar_test_console]
 GATT Peripheral / Advertiser    ◀─BLE──▶    GATT Central (Python bleak)
 · Start 시 광고 시작                         · [OOB 스캔] → 연결 → OOB_INFO 수신
 · OOB_INFO 제공 (Read/Notify)                · 주소 입력칸 자동 반영
 · Stop 시 광고·GATT 중지                     · (토글 ON 시) start_ranging 자동 호출
```

- UWB 역할은 기존과 동일: 보드=controller, 폰=controlee. **BLE 방향은 폰=peripheral, PC=central** (Windows에서 Python peripheral은 사실상 불가 — bleak는 central 전용).

## 3. GATT 스키마 (확정값 — 양쪽 동일해야 함)

| 항목 | 값 |
|---|---|
| **Service UUID** | `5F1D0001-9A8B-4C7D-B2E3-6F4A5D8C9B0A` |
| **OOB_INFO Characteristic UUID** | `5F1D0002-9A8B-4C7D-B2E3-6F4A5D8C9B0A` |
| OOB_INFO 속성 | **Read + Notify** (Write 없음) |
| 광고 페이로드 | Service UUID 포함 (콘솔은 UUID로 필터 스캔) + Local Name `UWB-OOB` |
| MTU | 기본 23(데이터 20B)으로 충분 — 협상 불필요 (페이로드 7B) |
| 보안 | 없음 (open read). 상용 단계에서 FiRa CSML/보안 채널로 대체 예정 |

## 4. OOB_INFO 페이로드 (v1 — 7 bytes 고정)

| 오프셋 | 크기 | 필드 | 형식 | 예 |
|---|---|---|---|---|
| 0 | 1B | `protocol_version` | uint8, **v1 = 0x01** | `01` |
| 1 | 2B | `uwb_address` | 앱 화면 표시 순서 그대로의 raw 2바이트. **"5F:DD" → `5F DD`** (바이트 반전 없음) | `5F DD` |
| 3 | 4B | `session_id` | uint32 **little-endian**. 기본 42 → `2A 00 00 00` | `2A 00 00 00` |

**바이트 순서 주의 (이 도메인 최대 함정 — STS IV 반전 전례 있음):**

| 필드 | 규칙 |
|---|---|
| uwb_address | 표시 문자열 순서 = 전송 순서. 반전 금지 |
| session_id | little-endian. 42 = `2A 00 00 00` (❌ `00 00 00 2A` 아님) |

- 파서는 길이 ≥7B만 확인하고 **뒤에 붙는 추가 바이트는 무시** (전방 호환).
- `protocol_version`이 0x01보다 크면: 앞 7B는 v1 규칙으로 파싱 시도 + UI에 "스펙 버전 불일치 경고" 표시 (하드 실패 금지).

## 5. 시퀀스 (정상 흐름)

```
폰 앱                              PC 콘솔                          보드(UCI)
 │ Start 탭                          │                                │
 │ controleeSessionScope 확보        │                                │
 │ prepareSession collect (WAITING)  │                                │
 │ BLE 광고 시작 ──────────────────▶ │ [OOB 스캔] → UUID 필터 발견     │  → 타임라인 BLE_ADV
 │ ◀────────────── GATT 연결 ─────── │                                │  → BLE_CONN
 │ OOB_INFO Read 응답 ─────────────▶ │ 주소·SessionID 파싱·검증        │  → OOB_DONE
 │ (광고 중지 — 연결 중)              │ 주소 입력칸 자동 반영            │
 │                                   │ [토글 ON] start_ranging ──────▶ │ UCI 세션 시작
 │ ◀═════════════ UWB DS-TWR 레인징 ═════════════════════════════════▶│  → RANGING
 │ (Notify 대기 유지)                 │ (연결 유지 — 주소 변경 감시)     │
```

**상태 규칙 (확정):**

0. **연결 방식은 양쪽 모두 사용자가 먼저 선택한다: `수동`(기존 1차 방식) / `OOB 자동`. 기본값 = 수동** (검증된 경로를 회귀 기준으로 보존). 수동 모드에서는 아래 BLE 동작이 전혀 일어나지 않고 기존 시나리오 그대로 동작한다. 모드 전환은 레인징 중이 아닐 때만 허용.
1. **광고 시작 = OOB 모드 선택 상태에서 앱 Start(WAITING 진입) 시점.** 수동 모드이거나 앱 실행만으로는 광고하지 않는다. (광고 보임 = OOB 모드 controlee 대기 중 = 콘솔이 붙어도 됨 — 기존 "앱 먼저 Start" 순서 제약과 의미 일치)
2. 광고는 GATT 연결 중 중지, 연결 해제 후 WAITING이면 재개.
3. 앱 Stop → GATT 서버 종료(콘솔에 disconnect) + 광고 중지.
4. **주소 재발급**(Stop→재Start 등으로 스코프 재발급) 시: 새 OOB_INFO를 **Notify**로 푸시. 콘솔은 입력칸 갱신 + 로그 기록. ★주소 휘발성이 이 자동화의 존재 이유.

## 6. 콘솔 세션 타임라인 매핑

| 타임라인 단계 | 트리거 |
|---|---|
| BLE_ADV | 스캔에서 Service UUID 발견 |
| BLE_CONN | GATT 연결 성공 |
| OOB_DONE | OOB_INFO 수신 + 파싱·검증 통과 |
| RANGING | start_ranging 후 첫 측정 수신 |
| ERR | 아래 예외 표의 각 실패 (REASON 병기) |

## 7. 예외 처리 (Corner Cases)

| # | 상황 | 폰 앱 동작 | 콘솔 동작 |
|---|---|---|---|
| 1 | 스캔 0건 | — | "광고 없음 — 폰 앱에서 Start 했는지 확인" 안내. 수동 입력 경로 유지 |
| 2 | 폰 여러 대 발견 | — | 목록 표시 후 선택(다중 선택 = multicast 주소 자동 구성, 쉼표 조합). 1대면 자동 선택 |
| 3 | GATT 연결 실패/타임아웃(10s) | 광고 지속 | ERR,REASON:BLE_CONN_FAIL + 재시도 버튼 |
| 4 | OOB_INFO 파싱 실패(길이<7B 등) | — | ERR,REASON:OOB_PARSE + raw hex 로그 (진단 원칙: 원문 보존) |
| 5 | Session ID 불일치 (콘솔 설정값 ≠ 수신값) | — | **경고 표시 + 수신값으로 갱신할지 선택** (무증상 실패 방어 — 이 검증이 SessionID를 넣은 이유) |
| 6 | 버전 불일치 (version>0x01) | — | v1 규칙 파싱 시도 + "스펙 버전 확인" 경고 |
| 7 | 레인징 중 BLE 끊김 | WAITING이면 광고 재개 | **UWB 세션은 유지** (BLE는 OOB 전용 — 끊겨도 레인징 무관). 로그만 기록 |
| 8 | 주소 재발급 Notify 수신 | Notify 발신 | 입력칸 갱신 + "주소 변경됨" 로그. 레인징 중이면 "재시작 필요" 경고 |
| 9 | BLE 권한 거부(폰) | 배너 안내(기존 UWB 권한 패턴 재사용). **UWB 수동 흐름은 계속 동작** | — |
| 10 | Windows BT 어댑터 없음/OFF | — | OOB 버튼 비활성 + 사유 표시. 수동 입력 경로 유지 |

## 8. 검수 기준 (E2E)

1. 폰 Start → 콘솔 [OOB 스캔] → **10초 이내** 주소가 입력칸에 자동 반영된다.
2. 자동 시작 토글 ON이면 주소 수신 즉시 레인징이 시작되어 거리가 표시된다 (기본은 OFF=수동).
3. 폰 Stop→재Start(주소 변경) 시 콘솔 입력칸이 **자동 갱신**되고 로그에 남는다.
4. 폰에서 BLE 권한을 거부해도 양쪽 모두 기존 수동 흐름으로 레인징이 된다.
5. 콘솔 SessionID를 43으로 바꾼 뒤 OOB 수신 시 불일치 경고가 뜬다.
6. 타임라인이 BLE_ADV→BLE_CONN→OOB_DONE→RANGING으로 실제 진행과 함께 점등된다.
7. 레인징 중 BLE만 끊어도 거리 측정이 계속된다.
8. 양쪽 모두 `수동` 모드 선택 시 기존 1차 방식(폰 화면 주소를 콘솔에 수동 입력)이 v0.1 이전과 동일하게 동작한다 (회귀 없음).
9. 앱을 OOB 모드로, 콘솔을 수동 모드로 두는 교차 조합에서도 레인징이 성립한다 (모드는 각자 독립).

## 9. 리스크 (선제 관리)

1. **Windows bleak 거동** — 스캔 캐시·재연결이 어댑터/드라이버별 편차. `BleOobClient` 추상화 + `SimulatorOobClient`로 UI를 격리하고, 실 BLE는 마지막에 결합.
2. **Android BLE 권한 매트릭스** — API 31+: `BLUETOOTH_ADVERTISE`/`BLUETOOTH_CONNECT` 런타임 권한. 기존 UWB 권한 플로우와 통합하되 거부 시 OOB만 비활성.
3. **광고+UWB 동시 동작** — 2.4GHz BLE와 CH9(≈8GHz) UWB는 주파수 분리로 간섭 무시 가능하나, 일부 기기 전력 정책이 광고를 스로틀 → 광고 인터벌은 balanced(≈250ms) 권장.
4. **스펙 드리프트** — 이 문서는 양 리포에 사본 존재. 개정은 반드시 버전 업 + 양쪽 동시 커밋. UUID·페이로드는 코드 상수 파일(`UwbDefaults.kt` / `oob_params.py`)로만 참조.

## 10. 개정 이력

| 버전 | 일자 | 내용 |
|---|---|---|
| v0.1 | 2026-07-13 | 최초 작성 (주소+SessionID+버전 / 마스터 radar_test_console / 자동시작 토글·기본 수동) |
| v0.2 | 2026-07-13 | 연결 방식 선택(수동/OOB 자동, 기본 수동) 추가 — 광고는 OOB 모드에서만, 검수 8·9번 추가 |
