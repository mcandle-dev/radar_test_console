# AGENTS.md — 360° 레이더 테스트 콘솔

## 프로젝트 목적

이 저장소는 Windows 11 PC에서 Qorvo DWM3001CDK 보드를 USB-UART로 제어하고 UWB 거리 데이터를 실시간 레이더로 표시하는 Flet 기반 테스트 콘솔이다. 상용 제품이 아니라 초도 기능 검증(Bring-up Test)과 장애 진단을 위한 도구이므로, 기능 수보다 빠르고 재현 가능한 기본 동작 확인과 원문 로그 보존을 우선한다.

현재 주 검증 조합은 다음과 같다.

- PC 콘솔: `D:\dev\radar_test_console` — UCI 호스트, controller/initiator
- Android 앱: `D:\dev\uwb_controlee_app` — Galaxy용 controlee/responder
- 보드: DWM3001CDK, DW3_QM33 SDK UCI 바이너리 펌웨어

Android 앱은 별도 저장소다. 현재 작업 범위가 이 콘솔 저장소라면 사용자의 명시적 요청 없이 `D:\dev\uwb_controlee_app`을 수정하지 않는다. 다만 세션 파라미터나 BLE OOB 계약을 변경하거나 검토할 때는 상대 저장소의 구현과 문서를 반드시 대조한다.

## 작업 전 확인할 기준 문서

- 기능, UART 포맷, UI, NFR, 검수 기준: `docs/Flet_레이더_테스트앱_요구사항정의서.md`
- 아키텍처와 코딩 규칙: `docs/Flet_레이더_코딩가이드_하네스엔지니어링.md`
- UCI 직접 구동 절차: `docs/6단계_UCI_직접구동_테스트_가이드.md`
- BLE OOB 마스터 계약: `docs/oob/BLE_OOB_인터페이스_사양서.md`
- 콘솔 OOB 변경 요구: `docs/oob/변경요구_radar_test_console.md`
- 현재 상태와 남은 작업: `docs/TODO.md`
- 변경 이력: `docs/CHANGELOG.md`
- vendored UCI 코드의 출처와 취급: `uci/README.md`

기능 요구사항과 코딩 방식이 충돌하면 코딩가이드를 따른다. 실행 계약은 현재 소스의 `uci_params.py`, `oob_params.py`와 상대 앱의 `UwbDefaults.kt`를 기준으로 바이트 단위 대조한다. 날짜가 붙은 작업 기록과 테스트 가이드는 당시 상태를 기록한 문서일 수 있으므로, 현재 상태 판단에는 `docs/TODO.md`, 최근 변경 이력, 현재 코드와 테스트 결과를 함께 사용한다.

## 고정 기술 스택과 실행 모드

- Python 3.11+ / Flet / `flet.canvas`
- pyserial 기반 USB-UART
- bleak 기반 BLE central
- `threading` + `queue.Queue`
- 추가 서버, 외부 차트 라이브러리, 불필요한 프레임워크를 도입하지 않는다.
- 의존성 버전은 `requirements.txt`에 고정한다.

`main.py` 상단의 두 스위치가 디바이스 경로를 결정한다.

| 설정 | 디바이스 | 용도 |
|---|---|---|
| `USE_SIMULATOR = True` | `SimulatorDevice` | 보드 없이 UI, 다중 타겟, 실패 시나리오 검증 |
| `USE_SIMULATOR = False`, `USE_UCI = True` | `UciSerialDevice` | 현재 기본값. UCI 펌웨어 보드를 직접 구동하며 Android controlee와 레인징 |
| `USE_SIMULATOR = False`, `USE_UCI = False` | `QorvoSerialDevice` | ASCII CLI/UART 라인 출력 펌웨어 수신 |

실행은 저장소 루트에서 한다.

```powershell
py -m pip install -r requirements.txt
flet run main.py
```

모드 변경은 테스트 목적과 보드 펌웨어 종류를 확인한 뒤 최소 범위로 수행한다. UCI 펌웨어는 바이너리 프레임을 사용하므로 ASCII CLI 경로와 혼용하지 않는다.

## UCI 세션 계약

보드와 Android 앱의 값은 바이트 단위로 일치해야 한다. 불일치는 명확한 오류 대신 세션 Active 상태에서 측정이 0건인 무증상 실패로 나타날 수 있다.

| 항목 | 콘솔 기본값 | Android 대응 |
|---|---|---|
| 역할 | controller / initiator | controlee / responder |
| Config | FiRa DS-TWR deferred, unicast | `CONFIG_UNICAST_DS_TWR` |
| Session ID | 42 | `sessionId` 기본 42 |
| 보드 short MAC | `00:00` | 보드 MAC 입력 기본값 |
| 채널 / 프리앰블 | 9 / 9 | `UwbComplexChannel(9, 9)` |
| Static STS | 와이어 `08 07 01 02 03 04 05 06` | `STATIC_STS_KEY` 8B |
| 갱신 주기 | 120ms | `RANGING_UPDATE_RATE_FREQUENT` |
| 토폴로지 | 주소 1개면 unicast | 현재 Android 구현은 unicast |

세션 상수는 `uci_params.py` 한 곳에서 관리한다. 상대 값은 `D:\dev\uwb_controlee_app\app\src\main\java\com\mcandle\uwbcontrolee\uwb\UwbDefaults.kt`와 `UwbRepository.kt`에서 확인한다. Session ID와 목적지 폰 주소 외의 값을 임의로 변경하지 않는다.

폰 주소 표기와 바이트 순서를 특히 주의한다. 예를 들어 화면 주소 `5F:DD`는 무선 구간에서도 `5F DD`가 되도록 `dest_mac_to_uci()`가 UCI 정수로 변환한다. 엔디언 처리를 호출부에 중복 구현하지 말고 `uci_params.py`의 순수 함수를 재사용한다.

콘솔은 쉼표로 최대 5개 주소를 받아 one-to-many 세션을 구성할 수 있지만, 상대 Android 앱도 multicast 프로파일을 지원하고 선택한 경우에만 실제 다중 레인징이 성립한다. 현재 상대 구현은 `CONFIG_UNICAST_DS_TWR`이므로 코드 지원과 실기기 검증 완료를 구분해 보고한다.

실물 레인징 순서는 다음을 지킨다.

1. Android 앱에서 UWB 가용성, 보드 MAC `00:00`, Session ID `42`, 내 주소를 확인한다.
2. Android controlee를 먼저 Start하여 WAITING 상태로 둔다.
3. 콘솔에서 UCI 보드 COM 포트에 연결하고 폰 주소를 반영한다.
4. 콘솔의 시작을 눌러 `SESSION_INIT → SET_APP_CONFIG → RANGE_START`를 수행한다.
5. 종료는 콘솔 정지로 `RANGE_STOP → SESSION_DEINIT`을 수행한 뒤 앱을 Stop한다.

## BLE OOB 계약

OOB는 폰 주소와 Session ID 전달을 자동화하는 부가 경로다. 기본값은 수동 입력이며, BLE 어댑터 부재·연결 실패·파싱 실패가 수동 UWB 레인징 경로를 막아서는 안 된다.

| 항목 | 값 |
|---|---|
| 역할 | 폰=GATT peripheral, PC=central |
| Service UUID | `5F1D0001-9A8B-4C7D-B2E3-6F4A5D8C9B0A` |
| OOB_INFO UUID | `5F1D0002-9A8B-4C7D-B2E3-6F4A5D8C9B0A` |
| 속성 | Read + Notify, Write 없음 |
| 페이로드 | 7B: version 1B + UWB address 2B + session ID 4B |

- `uwb_address`는 표시 순서 그대로이며 반전하지 않는다.
- `session_id`는 uint32 little-endian이다. 42는 `2A 00 00 00`이다.
- 파서는 7B 이상을 허용하고 추가 바이트를 무시한다.
- 상위 프로토콜 버전은 v1 규칙으로 파싱을 시도하고 경고하며, 바로 하드 실패시키지 않는다.
- UUID, 타임아웃, 버전 상수는 `oob_params.py`에서만 관리한다.
- 페이로드 변환은 순수 함수 `oob_parser.py`에 두고 바이트 순서 테스트를 유지한다.

마스터 사양서는 두 저장소에 같은 사본이 있다. 계약을 바꾸면 버전을 올리고 `radar_test_console/docs/oob/BLE_OOB_인터페이스_사양서.md`와 `uwb_controlee_app/docs/oob/BLE_OOB_인터페이스_사양서.md`, 양쪽 상수와 테스트를 한 작업 단위로 동기화한다. 다른 저장소 수정 권한이 없으면 필요한 상대 변경을 결과에 명시한다.

## 아키텍처 경계

- UI는 `RadarDevice` 추상 인터페이스에만 의존한다. `main.py`와 `radar_view.py`에 `serial` import를 추가하지 않는다.
- BLE 구현은 `ble_oob.py`에 격리한다. UI 파일에 `bleak` import를 추가하지 않는다.
- 디바이스와 BLE 콜백은 백그라운드 스레드에서 발생한다. 콜백에서는 `queue.Queue`에 이벤트만 넣고 Flet 위젯을 직접 갱신하지 않는다.
- 위젯 변경과 `page.update()`는 메인 UI의 50ms 펌프 경로에서만 수행한다.
- 연결 해제와 앱 종료는 작업 플래그, 제한 시간 `join`, 세션 정리, 자원 닫기 순서를 지켜 좀비 스레드와 좀비 UCI 세션을 남기지 않는다.
- `start_ranging()`과 `stop_ranging()`의 명령 응답 대기는 UI 스레드를 막지 않도록 워커에서 처리한다.
- 깨진 UART 라인, 알 수 없는 UCI NTF, BLE 오류는 앱을 종료시키지 말고 원문과 사람이 읽을 수 있는 원인을 로그로 남긴다.

## 주요 코드 위치

- `main.py`: Flet UI 진입점, 모드 선택, 큐 펌프, 워치독, 재연결, OOB/UI 배선
- `radar_view.py`: 360° Canvas 렌더링 전용
- `radar_device.py`: `RadarDevice`, 시뮬레이터, ASCII serial, UCI serial 구현
- `uci_params.py`: Android 앱과 맞춰야 하는 UCI 세션 계약의 단일 출처
- `ble_oob.py`: BLE OOB 추상화, 시뮬레이터, bleak 실물 구현
- `oob_params.py`: OOB UUID, 버전, 타임아웃 상수
- `parser.py`, `oob_parser.py`: 하드웨어와 UI에 의존하지 않는 순수 파서
- `models.py`: 측정, 상태 이벤트, 파싱 결과 데이터 모델
- `tests/`: parser, OOB, UCI 디바이스, 타겟 우선순위 회귀 테스트
- `uci/`: Qorvo/sasodoma 기반 vendored UCI 라이브러리

`uci/`는 제3자 vendored 코드이므로 일반적인 포맷·린트·타입 수정 대상에서 제외한다. 꼭 수정해야 할 때는 상위 래퍼로 해결할 수 없는지 먼저 확인하고, 출처와 로컬 변경 이유를 `uci/README.md` 또는 관련 문서에 남긴다.

## 데이터와 표시 규칙

- ASCII 측정: `DIST:85,ANGLE:-12,RSSI:-60`; 키 순서와 대소문자는 무관하다.
- 거리 단위는 cm다. AndroidX 앱 내부 값은 m이므로 상대 앱에서 cm로 환산한다.
- 보드의 안테나는 하나이므로 UCI 보드 측 각도는 `None`/`N/A`가 정상이다. 각도 검증은 폰 측에서 한다.
- 여러 타겟은 `Measurement.target_id`로 분리하고, 최근접 거리를 우선 강조하며 거리 동률이면 RSSI가 강한 타겟을 우선한다.
- 거리 0~5000cm, 각도 -90~+90° 범위 초과는 파싱 자체를 실패시키지 말고 UI 경고로 처리한다.
- 알 수 없는 확장 필드는 전방 호환을 위해 가능한 경우 무시하고 기존 필드를 처리한다.
- 2초 이상 무수신은 `수신없음`, 링크/케이블 단절은 `끊김`으로 구분한다.

## 코딩과 작업 규칙

- 모든 함수에 type hint를 작성하고 class와 주요 메서드에는 짧은 한국어 docstring을 둔다.
- 매직 넘버는 파일 상단 또는 책임이 맞는 파라미터 모듈의 이름 있는 상수로 옮긴다.
- `print` 대신 `logging`과 기존 사용자 로그 콜백을 사용한다. `except: pass`로 오류를 숨기지 않는다.
- 함수는 가능하면 30줄 이내로 유지하고 UI, 프로토콜, 변환 책임이 섞이면 분리한다.
- 주소 바이트 순서, 엔디언, 단위 변환은 코드와 테스트에서 명시적으로 드러낸다.
- 요구사항에 없는 기능으로 범위를 넓히지 않고 기존 단순 구조를 유지한다.
- 변경 전에 관련 기준 문서와 현재 구현을 함께 읽는다.
- 사용자의 기존 수정 사항을 덮어쓰거나 되돌리지 않는다. 특히 dirty worktree에서는 관련 diff를 먼저 확인한다.
- 계약이나 동작을 변경하면 테스트와 관련 문서를 함께 갱신한다.
- 자동 테스트 통과, 시뮬레이터 육안 확인, 실제 BLE 어댑터 확인, Android/보드 E2E 검증을 서로 구분해 보고한다. 실물로 확인하지 않은 동작을 검증 완료라고 단정하지 않는다.

## 변경 시 주의할 함정

- 세션 파라미터 불일치는 오류 없이 측정 0건으로 보일 수 있다.
- 폰의 UWB 주소는 앱/세션 재시작 후 바뀔 수 있으므로 콘솔 목적지 주소를 다시 확인한다.
- 폰을 먼저 Start하지 않으면 controller가 타임아웃 또는 retry count 오류로 세션을 내릴 수 있다.
- CLI 펌웨어는 UCI 바이너리 명령에 응답하지 않는다. 응답 타임아웃이면 펌웨어 종류와 모드 스위치를 먼저 확인한다.
- sasodoma 스크립트, TeraTerm, putty가 COM 포트를 점유하면 콘솔 연결이 실패한다.
- BLE 광고/GATT 연결 수명과 UWB 세션 수명은 독립적이다. OOB 연결 종료가 레인징을 자동 중단시키지 않게 한다.
- OOB 자동 모드를 수정할 때 기본 수동 모드에서 BLE 코드가 실행되지 않는 회귀 조건을 확인한다.
- UI 코드에서 장시간 블로킹 I/O나 명령 응답 대기를 실행하지 않는다.

## 검증 명령

Windows PowerShell, 저장소 루트 기준:

```powershell
py -m pytest tests -v
py -m ruff check .
py -m black --check .
py -m mypy --ignore-missing-imports .
```

변경 범위에 비례해 검증한다.

- 파서/OOB/UCI 계약 변경: 관련 단위 테스트와 전체 `pytest`
- 일반 Python 변경: `pytest`, ruff, black check, mypy
- Flet UI 변경: 자동 검증 후 시뮬레이터 모드로 창을 열어 레이아웃과 상호작용을 육안 확인
- serial/UCI 변경: 가짜 트랜스포트 테스트 후 가능하면 UCI 보드 절차 수행
- BLE OOB 변경: 시뮬레이터 테스트 후 BLE 어댑터, Android peripheral, 최종 UWB 세션 순서로 단계별 확인

실기기 E2E에서는 `docs/6단계_UCI_직접구동_테스트_가이드.md`와 상대 저장소의 `docs/5단계_보드_테스트_가이드.md`를 함께 사용한다.
