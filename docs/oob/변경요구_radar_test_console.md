# 변경요구서 — radar_test_console: BLE OOB Central 추가

> 대상 리포: `mcandle-dev/radar_test_console` · 기준 사양: `BLE_OOB_인터페이스_사양서.md v0.2`
> 작성일 2026-07-13 · Claude Code 실행 위치: `D:\dev\radar_test_console`
> TODO.md §1 "OOB 2차 — 폰 주소 자동 교환"의 구체화 문서.

---

## 0. 선행 작업 — 문서 커밋

| 파일 | 내용 |
|---|---|
| `docs/oob/BLE_OOB_인터페이스_사양서.md` | **마스터** 커밋 (앱 리포에 사본) |
| `docs/oob/변경요구_radar_test_console.md` | 본 문서 |
| `CLAUDE.md` | OOB 계약 상수(UUID·페이로드)와 "BLE는 `ble_oob.py` 계층에만, UI에 import bleak 금지" 규칙 추가 |
| `docs/TODO.md` §1 | 본 문서 링크로 세분화 갱신 |

## 1. 기능 요구사항

| ID | 기능 | 우선 | 상세 |
|---|---|---|---|
| FR-OOB-0 | **연결 방식 선택** | 必 | `수동 입력`/`OOB 자동` 세그먼트. **기본값 수동**(기존 방식 그대로). 레인징 중 변경 비활성. 수동 선택 시 화면·동작이 현행과 100% 동일 (회귀 없음) |
| FR-OOB-1 | BLE 스캔·연결 | 必 | **OOB 모드에서** [OOB 스캔] 버튼 → Service UUID 필터 스캔(bleak) → 발견 목록 표시. 1대면 자동 선택, 다중이면 선택 UI. 연결 타임아웃 10s |
| FR-OOB-2 | OOB_INFO 수신·파싱 | 必 | 사양서 §4의 7B 파싱(버전/주소/session_id). 파싱 실패 시 raw hex를 로그에 보존 |
| FR-OOB-3 | 주소 자동 반영 | 必 | 수신 주소를 폰 주소 입력칸에 자동 입력. 다중 선택 시 쉼표 조합(`5F:DD, A1:B2`) — multicast(TODO §2)와 연계 |
| FR-OOB-4 | SessionID 교차 검증 | 必 | 콘솔 설정값과 수신값 불일치 시 경고 + "수신값으로 갱신" 선택지 (무증상 실패 방어) |
| FR-OOB-5 | 자동 시작 토글 | 必 | 토글 ON이면 주소 반영 직후 start_ranging 자동 호출. **기본 OFF(수동)** — 브링업 단계별 관찰 원칙 |
| FR-OOB-6 | 주소 변경 Notify 처리 | 必 | Notify 수신 시 입력칸 갱신 + 로그. 레인징 중이면 "주소 변경됨 — 재시작 필요" 경고 |
| FR-OOB-7 | 타임라인 실연동 | 必 | BLE_ADV(발견)→BLE_CONN(연결)→OOB_DONE(수신·검증)→RANGING(첫 측정)을 실제 이벤트로 점등. ERR 시 REASON 표시 — 기존 타임라인 위젯이 드디어 실데이터를 받음 |
| FR-OOB-8 | 시뮬레이터 OOB | 必 | `SimulatorOobClient`: 가짜 스캔 결과·OOB_INFO·주소 변경·실패 시나리오(BLE_CONN_FAIL, OOB_PARSE) 재현 — 폰 없이 UI 전체 검증 |
| FR-OOB-9 | BT 어댑터 부재 처리 | 必 | Windows BT 없음/OFF 시 OOB 버튼 비활성+사유. 수동 입력 경로는 항상 유지 |

## 1a. 화면 변경 (UI 요구사항)

연결바 영역에 **연결 방식 세그먼트**를 추가하고, 모드에 따라 폰 주소 입력칸과 OOB 컨트롤의 노출·활성이 전환된다.

```
┌────────────────────────────────────────────────────────────────┐
│ [연결바] 포트:[COM5▼] ... │ 연결 방식: [ ●수동 입력 │ ○OOB 자동 ] │ ← ★신규 FR-OOB-0
├────────────────────────────────────────────────────────────────┤
│ (수동)  폰 주소 [ 5F:DD, A1:B2 ]  (편집 가능 — 기존 그대로)        │
│ (OOB)   폰 주소 [ 5F:DD ] 🔒자동   [OOB 스캔] ●연결됨  [☐자동시작] │ ← 읽기전용+자동 반영
├────────────────────────────────────────────────────────────────┤
│ [세션 타임라인] SLEEP→BLE_ADV→BLE_CONN→OOB_DONE→RANGING          │ ← OOB: 실이벤트 연동
│                                                                 │    수동: N/A(회색) 유지
│ (레이더 뷰 / 수치 패널 / 로그 콘솔 — 기존과 동일)                   │
└────────────────────────────────────────────────────────────────┘
```

**모드별 동작 요약**

| 항목 | 수동 입력 (기본) | OOB 자동 |
|---|---|---|
| 폰 주소 입력칸 | 편집 가능 (현행 그대로) | 읽기전용 + 자동 반영 (🔒 표시) |
| [OOB 스캔]·자동시작 토글 | 숨김(또는 비활성) | 노출 |
| 세션 타임라인 | N/A 회색 (현행) | BLE 실이벤트로 점등 (FR-OOB-7) |
| start_ranging | 수동 버튼 (현행) | 수동 버튼 + 토글 ON 시 자동 |
| USE_SIMULATOR와의 관계 | **직교** — 시뮬 모드에서도 방식 선택 가능. OOB×시뮬 = `SimulatorOobClient` 사용 | 동일 |
| BT 어댑터 없음 | 영향 없음 | OOB 세그먼트 선택 시 비활성+사유 (FR-OOB-9) |

## 2. 아키텍처 요구사항 (기존 원칙 연장)

- **`BleOobClient`(ABC) 추상화**: `scan()` / `connect(device)` / `read_oob()` / `disconnect()` + 콜백 `on_oob_info` / `on_disconnect` / `on_log`. 구현 2개: `BleakOobClient`(실물) / `SimulatorOobClient`(하네스). — `RadarDevice` 패턴과 동일.
- **UI 코드에 `import bleak` 금지.** BLE는 `ble_oob.py` 계층에만.
- bleak는 asyncio 기반 → Flet 메인 루프와 분리된 **전용 스레드의 이벤트 루프**에서 구동, 결과는 기존 콜백→queue→50ms 타이머 경로로 UI 전달 (스레드 규칙 동일).
- OOB 페이로드 파서는 순수 함수(`oob_parser.py`) + pytest (바이트 순서 케이스: 주소 비반전, session_id LE, 7B 미만, 추가 바이트 무시, 버전 0x02).
- 상수는 `oob_params.py` 한 곳 (UUID·버전·타임아웃) — `uci_params.py`와 같은 원칙.
- BLE 끊김은 UWB 세션과 무관해야 함: OOB 클라이언트 종료가 UCI 레인징을 중단시키지 않는다.

## 3. 검수 기준

1. **시뮬레이터만으로**: 스캔→목록→주소 자동 반영→타임라인 점등→(토글 ON) 자동 시작이 전부 재현된다.
2. 실폰: 앱 Start 후 [OOB 스캔] → 10초 내 주소 자동 반영.
3. SessionID 불일치·파싱 실패·연결 타임아웃이 각각 구분된 경고/로그로 나타난다.
4. 폰 Stop→재Start 시 입력칸 자동 갱신 + 로그.
5. 레인징 중 BLE 끊김에도 거리 측정 지속.
6. BT 어댑터 비활성 PC에서 앱이 죽지 않고 수동 입력으로 동작.
7. **수동 모드 선택 시 현행 화면·동작과 100% 동일**하고 BLE 코드가 실행되지 않는다 (회귀 없음).
8. 레인징 중에는 연결 방식 세그먼트가 비활성화된다.
9. 시뮬 모드(USE_SIMULATOR=True)+OOB 모드 조합에서 SimulatorOobClient로 전 흐름이 재현된다.

## 4. Claude Code 복붙 프롬프트

**개정 단계 (먼저):**
```
docs/oob/BLE_OOB_인터페이스_사양서.md 와 docs/oob/변경요구_radar_test_console.md 를 읽어라.
CLAUDE.md에 OOB 계약(UUID·페이로드 7B 포맷)과 'UI에 import bleak 금지, BLE는 ble_oob.py 계층에만'
규칙을 추가하고, docs/TODO.md §1을 본 변경요구서 기준으로 세분화 갱신하라.
코드는 만들지 말고 diff를 보여주고 멈춰라.
```

**구현 단계:**
```
사양서와 변경요구서 기준으로 구현하라. requirements.txt에 bleak 추가(버전 고정).
1단계: oob_params.py + oob_parser.py(순수 함수) + tests/test_oob_parser.py
       (주소 비반전, session_id LE=2A 00 00 00, 7B 미만 실패, 추가 바이트 무시, 버전 0x02 경고).
2단계: ble_oob.py — BleOobClient(ABC) + SimulatorOobClient (스캔·수신·주소변경·실패 토글 재현).
3단계: UI 통합 — 연결 방식 세그먼트(FR-OOB-0, 기본 수동, 레인징 중 비활성, §1a 목업 기준),
       OOB 모드에서만 [OOB 스캔]·자동 시작 토글(기본 OFF) 노출, 주소 입력칸 읽기전용+자동 반영,
       SessionID 불일치 경고, 세션 타임라인 실연동. 수동 모드는 현행과 100% 동일해야 함.
       시뮬레이터(SimulatorOobClient)로 전 흐름 검증.
4단계: BleakOobClient — 전용 스레드 asyncio 루프, 타임아웃 10s, Notify 구독, 안전 종료.
5단계: BT 부재 처리, 재연결, 로그 정리.
각 단계 끝에서 pytest/실행 확인 후 멈춰라. BLE 실패가 기존 수동 흐름을 절대 막지 않게 하라.
```

## 5. 리스크

- **bleak×Flet 이벤트 루프 충돌** — 최다 예상 트러블. asyncio 루프는 반드시 별도 스레드, UI 갱신은 기존 queue 경로만.
- Windows 스캔 캐시로 죽은 광고가 목록에 남을 수 있음 → 스캔 결과에 RSSI·타임스탬프 표기, 새로고침 버튼.
- 폰 여러 대 + multicast 결합은 TODO §2 실측과 함께 검증 (unicast/multicast 프로파일 함정 주의).

## 6. 전체 진행 순서 (두 리포 종합)

| 순서 | 리포 | 작업 | 검증 |
|---|---|---|---|
| 1 | 양쪽 | 사양서·변경요구서 커밋 + 문서 개정 | diff 리뷰 |
| 2 | 앱 | OOB peripheral 구현 | nRF Connect 단독 |
| 3 | 콘솔 | 1~3단계 (파서+시뮬레이터+UI) | 폰 불필요 |
| 4 | 콘솔 | 4단계 BleakOobClient | 실폰 연결 |
| 5 | 통합 | E2E (사양서 §8 검수 7항목) | 실기기 |

※ 2와 3은 병행 가능 (콘솔 3단계까지는 시뮬레이터만 사용).
