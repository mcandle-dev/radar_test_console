# 6단계 — UCI 직접 구동 테스트 가이드 (레이더 콘솔 ↔ Pixel/Galaxy controlee)

- 작성일: 2026-07-06
- 상태: **코드 + 단위 테스트까지만 완료. 실보드 미검증** (보드 확보 후 이 가이드로 검증)
- 배경: `docs/QA_2026-07-03_UCI_CLI_펌웨어와_Pixel_인터롭.md`의 "향후 계획 2번"
- 상대: Android controlee 앱 `uwb_controlee_app`
  (https://github.com/mcandle-dev/uwb_controlee_app, 로컬 `D:\dev\uwb_controlee_app`)

## 0. 무엇이 어떻게 바뀌었나

| 항목 | CLI 모드 (기존) | UCI 모드 (이번 추가) |
|---|---|---|
| 보드 펌웨어 | DW3_QM33 SDK **CLI** (ASCII 텍스트) | DW3_QM33 SDK **UCI** (바이너리) |
| 콘솔 디바이스 | `QorvoSerialDevice` (무변경) | `UciSerialDevice` (신규) |
| 상대 | 없음 (보드 단독 출력) | 폰 = controlee/responder |
| 전환 | `main.py`의 `USE_UCI = False` | `USE_UCI = True` (기본) |

- UCI 프레이밍/명령/NTF 파싱은 vendored `uci/` 라이브러리
  (sasodoma/uwb-ranging @ `aad72a0`, Qorvo 제공 — `uci/README.md` 참조).
- 세션 파라미터는 **`uci_params.py` 한 파일**에 고정. 폰 쪽
  `uwb_controlee_app`의 `UwbDefaults.kt`와 바이트 단위로 일치해야 하며,
  대조표는 `D:\dev\uwb_controlee_app\docs\파라미터_대조_4단계.md`.
  **SESSION_ID(42)와 폰 주소(dest MAC)만 가변, 나머지는 수정 금지.**
- 역할: 보드 = controller/initiator (이 콘솔이 UCI 호스트), 폰 = controlee.
  → **폰 앱을 먼저 Start시킨 뒤 콘솔에서 시작을 눌러야 한다.**

## 1. 전제 — sasodoma 스크립트로 먼저 브링업 성공할 것

콘솔로 바로 가지 말 것. 변수를 하나씩 줄이기 위해 **검증된 레퍼런스 경로부터** 통과한다.

1. 보드에 UCI 펌웨어 플래시:
   `uwb-ranging/new_firmware/DWM3001CDK-DW3_QM33_SDK_UCI-FreeRTOS.hex` (J-Link)
2. 폰 앱 절차는 **`D:\dev\uwb_controlee_app\docs\5단계_보드_테스트_가이드.md`**
   (준비물 1장, 폰 준비 2-3장, 절차 3장, 트러블슈팅 6장)를 그대로 따른다.
3. 폰 앱 실행 → 화면의 **내 주소**(예 `5F:DD`) 확인 → 보드 MAC `00:00`,
   Session ID `42` 확인 → **Start**
4. PC (sasodoma 리포의 `new_python_script/`):
   ```
   python run_fira_twr.py -p <COMx> --mac 00:00 --dest-mac 5F:DD -t -1
   ```
5. 10초 내 폰이 RANGING으로 전환되고 양쪽에 거리가 찍히면 **전제 통과**.
   실패하면 5단계 가이드의 트러블슈팅 A~D로 해결 후 진행 (이 콘솔로 넘어와도
   같은 문제가 그대로 재현될 뿐이다).

## 2. 콘솔로 전환하는 절차

1. sasodoma 스크립트를 **완전히 종료**한다 (COM 포트 점유 해제 확인).
   보드를 한 번 리셋(USB 재연결)해 스크립트가 남긴 세션을 비우는 것을 권장.
2. `main.py` 상단 확인: `USE_SIMULATOR = False`, `USE_UCI = True`
3. 실행: `flet run main.py`
4. 연결바에서 보드 COM 포트 선택, 속도 115200 그대로 → **연결**
   - 로그 콘솔에 `[UCI] UCI 트랜스포트 열림`이 보이면 정상
5. **폰 주소** 입력 필드에 폰 앱 화면의 내 주소(예 `5F:DD`)를 입력하고 **Enter**
   - 로그에 `[UCI] 폰 주소(DST_MAC) = 5F:DD` 확인
6. **폰 앱에서 먼저 Start** (폰이 WAITING 상태로 대기)
7. 콘솔에서 **시작** 버튼 → 로그에 다음 시퀀스가 순서대로 찍히는지 확인:
   ```
   TX [UCI] SESSION_INIT (id=42)
   TX [UCI] SET_APP_CONFIG (dest_mac=5F:DD, ch=9, interval=120ms)
   TX [UCI] RANGE_START
   [UCI] 세션 상태: Active (...)
   DIST:<거리>          ← 이후 초당 ~8건
   ```
8. 종료 시: 콘솔 **정지**(RANGE_STOP → SESSION_DEINIT까지 자동) → 폰 앱 Stop →
   콘솔 **해제**. 해제만 눌러도 세션 정리는 자동으로 수행된다.

## 3. 검수 체크리스트

| # | 항목 | 기대 결과 | 통과 |
|---|---|---|---|
| 1 | 연결 후 [UCI] 트랜스포트 로그 | `[UCI] UCI 트랜스포트 열림` | ☐ |
| 2 | 시작 → 세션 시퀀스 로그 | INIT→SET_APP_CONFIG→RANGE_START 모두 성공 | ☐ |
| 3 | 세션 타임라인/수치 패널 | `RANGING` 표시 (녹색) | ☐ |
| 4 | **레이더 화면에 거리 표시** | 수치 패널 거리 갱신 (초당 ~8건, 120ms 주기) | ☐ |
| 5 | **각도 = N/A 정상** | 보드 안테나 1개 → 각도 'N/A', 레이더 점 숨김이 정상 동작 | ☐ |
| 6 | 거리 상식 검증 | 1m 거리에서 대략 100±30cm | ☐ |
| 7 | 정지 → 재시작 | 정지 후 다시 시작하면 세션이 다시 붙음 (좀비 세션 없음) | ☐ |
| 8 | **폰 주소 변경 시 dest MAC 갱신** | 폰 앱 재시작으로 주소가 바뀌면(랜덤 발급) 콘솔 필드만 고쳐 Enter → 재시작으로 복구 | ☐ |
| 9 | 무수신 워치독 | 폰 Stop 시 2초 후 '수신없음' 표시 | ☐ |

## 4. 트러블슈팅

- **세션은 Active인데 DIST가 0건** (에러 없이 무증상 실패 — 이 도메인의 전형):
  1. 폰 주소 확인 — 폰 앱 재시작마다 주소가 바뀔 수 있다. 필드 갱신 후 재시작.
  2. 폰 앱이 Start 상태(WAITING)였는지 — 순서는 항상 폰 먼저.
  3. `uci_params.py` ↔ `UwbDefaults.kt` 대조 (특히 STS 키 와이어 바이트
     `08 07 / 01 02 03 04 05 06`, 주소 바이트 순서 — 대조표 문서의 '주소 바이트
     순서 메모' 참조).
- **SESSION_INIT부터 실패 / 응답 타임아웃**: CLI 펌웨어가 플래시돼 있을 가능성.
  CLI 펌웨어는 텍스트 프로토콜이라 UCI 프레임에 응답하지 않는다. 1장의 UCI
  펌웨어(hex)를 다시 플래시.
- **`[UCI] SET_APP_CONFIG 실패: InvalidParam ...`**: 로그에 실패한 파라미터
  목록이 함께 찍힌다. `uci_params.py`에서 해당 값 확인.
- **ERR (MaxRangingRoundRetryCountReached 등)**: 펌웨어가 세션을 내린 것.
  로그의 사유(enum 이름)로 검색 — 대부분 상대 무응답(주소/파라미터 불일치) 계열.
- **연결 실패 (포트 점유)**: sasodoma 스크립트/TeraTerm이 아직 포트를 잡고 있음.
- 그 외 폰 쪽 증상은 5단계 가이드의 트러블슈팅 A~D 참조.

## 5. 참고

- 파라미터 대조표: `D:\dev\uwb_controlee_app\docs\파라미터_대조_4단계.md`
- 폰 쪽 테스트 가이드: `D:\dev\uwb_controlee_app\docs\5단계_보드_테스트_가이드.md`
- 기준 리포: https://github.com/sasodoma/uwb-ranging @ `aad72a0`
- 단위 테스트: `tests/test_uci_device.py` — 가짜 트랜스포트로 UCI 프레임
  인코딩(TLV 와이어 바이트)과 RANGE_DATA NTF 디코딩을 검증. 보드 없이
  `pytest tests/ -v`로 회귀 확인 가능.
