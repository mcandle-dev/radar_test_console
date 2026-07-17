# CHANGELOG — 360° 레이더 테스트 콘솔

> 날짜별 변경 이력. 새 작업을 커밋할 때마다 **맨 위에** 항목을 추가한다.
> 상세 배경·검증 과정은 각 날짜의 `작업기록_*.md` / `작업일지_*.md` 참고.

## 2026-07-17

### OOB 실기기 E2E + 레인징 시작 타이밍 안정화 (`e27f87f`)

- **원인 확인**: Android controlee는 유효 측정이 없으면 약 10초 후 UWB 세션을 자동
  종료하는데, PC의 `BleakScanner.discover(timeout=10)`가 일치 광고를 일찍 발견해도
  전체 시간을 기다려 OOB 완료 전에 폰 세션이 종료됨. 실제 실패 로그에서 폰 세션은
  17:17:37 시작→약 17:17:47 종료, PC `RANGE_START`는 17:18:05로 확인
- **BLE 스캔 조기 종료**: Service UUID가 일치하는 첫 광고를 발견하면 즉시 스캔을
  중단하고 GATT 연결·OOB_INFO Read로 진행. 최대 10초 타임아웃은 광고가 없을 때만 적용
- **자동 시작 유실 방지**: OOB 주소를 받았을 때 UCI가 미연결이거나 Session ID 확인이
  남아 있으면 요청을 보존하고, 연결/자동 재연결/Session ID 갱신 후 준비 조건이 갖춰질
  때 `start_ranging()`을 정확히 한 번 호출
- 자동 시작 대기 사유(`UCI 연결 대기`, `SessionID 확인 대기`, `레인징 종료 대기`)를
  OOB 상태와 로그에 표시하고, 수동 모드 복귀 시 보류 요청을 폐기
- 실물 기본 실행 모드를 UCI 보드 경로(`USE_SIMULATOR=False`, `USE_UCI=True`)로 정렬
- `AGENTS.md` 추가 — UCI/OOB 계약, 저장소 경계, 아키텍처 및 검증 절차 정리
- 테스트 5개 추가(첫 광고 조기 종료, 자동 시작 보존·1회 소비·취소·Session ID 대기) —
  **전체 103개 통과**, Ruff 통과, 변경 파일 Black 검사 통과
- **실기기 E2E 성공**: Galaxy controlee OOB 주소 자동 수신 후 DWM3001CDK와 연결 및
  레인징 정상 동작을 사용자 확인

### OOB·RSSI 작업 병합 + Flet 0.85.3 호환 수정 (`f9ccf4e`, `093827a`)

- OOB 2차 구현(`afcd974`)과 타겟 RSSI/최근접 강조 작업(`cc88b32`)을 `main`에 병합
- Flet 0.85.3에서 Dropdown 이벤트를 `on_select`로, SegmentedButton 선택값을
  `selected`로 맞춰 실행 시 API 불일치를 수정

## 2026-07-14

### OOB 2차 — BLE 폰 주소 자동 교환: 구현 1~4단계 (5단계 중) (`afcd974`)

> **인수인계 지점.** 남은 작업은 5단계(안정성)와 실폰 E2E 검수 — `docs/TODO.md` §0 참고.
> 기준선 태그 **`v1.0_non-oob`**(`b058912`) = 이 작업 이전, 폰 주소 수동 입력 방식.

- **1단계 — 계약·파서**: `oob_params.py`(UUID·버전·타임아웃 단일 출처),
  `oob_parser.py`(OOB_INFO 7B 페이로드 순수 함수 — 주소는 표시 순서 그대로 2B(반전 금지),
  session_id는 uint32 LE). 길이 ≥7B만 검사하고 추가 바이트는 무시(전방 호환),
  version>0x01이면 v1 규칙으로 파싱 시도 + 경고(하드 실패 금지)
- **2단계 — BLE 추상화**: `ble_oob.py` — `BleOobClient`(ABC) +
  `SimulatorOobClient`(폰 없이 스캔·수신·주소 재발급 Notify·실패 시나리오 재현).
  `RadarDevice` 패턴과 동일하게 UI는 인터페이스에만 의존, 콜백→queue→50ms 타이머 경로 유지
- **3단계 — UI 통합**: 연결 방식 세그먼트(`수동 입력`/`OOB 자동`, **기본 수동**, 레인징 중 전환 불가),
  [OOB 스캔]+발견 목록(1대면 자동 선택), 주소 입력칸 자동 반영(OOB 모드에서 읽기전용),
  SessionID 교차 검증(불일치 시 경고 + "수신값으로 갱신"), 자동 시작 토글(**기본 OFF**),
  주소 변경 Notify 처리, **세션 타임라인 실연동**(BLE_ADV→BLE_CONN→OOB_DONE이 드디어 실이벤트)
- **4단계 — 실물 BLE**: `BleakOobClient` — bleak asyncio 루프를 **전용 스레드**에서 구동
  (Flet 메인 루프와 분리), Service UUID 필터 스캔(RSSI·발견시각 표기 — 스캔 캐시 대응),
  연결 타임아웃 10s → 실패 시 `ERR,REASON:BLE_CONN_FAIL`, OOB_INFO Read + Notify 구독,
  `close()`에서 GATT 해제 후 루프·스레드 join(좀비 스레드 금지).
  bleak 미설치 PC에서도 앱이 죽지 않고 수동 경로 유지(`is_bleak_available()`)
- **BLE는 UWB와 무관**: `ble_oob.py`는 UCI를 전혀 호출하지 않는다 — BLE가 끊겨도 레인징은 유지
- 테스트 86개 통과 (OOB 파서 + 시뮬 클라이언트 + BleakOobClient의 BLE 무관 경로)
- **검증 수준**: 시뮬레이터로 전 흐름 재현, 폰 없이 실어댑터 스캔 확인(비블로킹·좀비 스레드 없음).
  **실폰 E2E는 미검증** — 다음 담당자의 첫 작업
- 문서: `docs/oob/BLE_OOB_인터페이스_사양서.md`(v0.2, 마스터 — 폰 리포에 사본),
  `docs/oob/변경요구_radar_test_console.md`(FR-OOB-0~9), CLAUDE.md에 OOB 계약·bleak 격리 규칙 추가

### 타겟 RSSI 표시 + 최근접·최대RSSI 타겟 강조 (`cc88b32`)
- `Measurement.rssi_dbm` 추가: 텍스트(`RSSI` 필드)·UCI(`meas.rssi`, 0=미지원→`None`
  정규화)·시뮬레이터(랜덤 워크) 경로에서 채움. 타겟 패널 상세 줄에 `RSSI -71.5 dBm` 표시
- `select_primary_target()`: 거리 최소 우선, 동률이면 RSSI 최대 우선으로 대표 타겟을 골라
  패널 점·레이더 점/링을 빨간색(`COLOR_PRIMARY_TARGET`)으로 강조
- 테스트 11개 추가 (parser RSSI 3 · UCI 디바이스 RSSI 2 · 대표 타겟 선정 6) — 전체 67개 통과
- **미검증**: 실제 Flet 창에서 빨간색 강조 육안 확인은 다음 세션 과제 (개발 환경에 네이티브
  Windows GUI 스크린샷 도구 없음 — 컴포넌트 단위 구동으로 대체 검증)

## 2026-07-11

### 다중 타겟 표시 + UCI multicast (`0a5a957`, `63abe1b`)
- **타겟별 행 패널**: 우측 패널을 타겟(주소)별 행 구조로 개편 — 행마다 거리·각도·
  타겟별 수신율·최종수신 표시, 2초 무수신 타겟 회색 처리, 최대 5개 자동 관리
- **타겟별 색상**: 5색 고정 팔레트를 등장 순서로 배정, 패널 ●와 레이더 표시 동일 색
- **레이더 다중 표시**: 각도 있는 타겟=점, 각도 없는 타겟(UCI 보드, 안테나 1개)=거리 링
- **UCI multicast**: 폰 주소 쉼표 다중 입력(최대 5개) → one-to-many 세션 자동 전환.
  1개 입력 시 기존 unicast와 와이어 바이트 동일 (폰 인터롭 유지)
- **시뮬레이터**: 기본 3개 타겟 동시 생성 (다중 타겟 UI를 보드 없이 검증)
- 연결바의 기능 없던 타겟 1~5 입력칸 제거 (레이아웃 복구)
- 테스트 7개 추가 (multicast TLV/parse_dest_macs) — 전체 56개 통과
- **실기기 검증 성공**: 폰 주소 수동 입력(1차 방식, OOB 없음) → ranging → 타겟별 거리 표시
- `docs/TODO.md` 체크리스트 신설 (OOB 2차 / multicast 실측 / 진단 도구)

### 타겟 식별 기반 + Flet 업그레이드 + UCI 재동기화 (`89bde00`)
- `Measurement.target_id` 추가: 텍스트(TARGET/ID)·UCI(mac_add 정규화) 경로에서 타겟 식별
- Flet 0.28.3 → **0.85.3** 업그레이드: `run_thread`→`run_task`(async 펌프),
  `Padding`/`Alignment` API 마이그레이션
- `uci/` 벤더 라이브러리 재동기화 (qorvo_cal 파라미터 갱신, Client.close 방어화 등)

## 2026-07-06

### 6단계: UCI 직접 구동 (`f8bfed2`)
- `UciSerialDevice` 추가 — DWM3001CDK UCI 펌웨어(DW3_QM33 SDK)를 UCI 호스트로 직접 구동
- 보드=controller/initiator, 폰(uwb_controlee_app)=controlee/responder 인터롭
- `uci_params.py`: 폰과 바이트 단위 일치가 필요한 세션 파라미터 단일 출처
- `uci/` 벤더 라이브러리 vendoring, MockTransport 기반 단위 테스트

## 2026-07-03

### Qorvo CLI JSON 파서 (`ddc819c`)
- `parser.parse_line()`에 CLI 펌웨어 JSON 블록(`D_cm`/`LAoA_deg`) 지원 추가
- UCI/Pixel 인터롭 Q&A 문서 추가

## 2026-07-02

### 초기 구현 1~5단계 (`5d4ef18`)
- models/parser/테스트, RadarDevice(ABC)+SimulatorDevice, Flet UI 전체
  (레이더·수치 패널·세션 타임라인·로그 콘솔), QorvoSerialDevice(pyserial),
  안정성(자동 재연결·수신 워치독·포트 점유 처리·로그 저장)
