# CLAUDE.md — 360° 레이더 테스트 콘솔 (radar_test_console)

## 프로젝트 개요
Qorvo DWM3001CDK 보드가 USB-UART로 보내는 UWB 거리(cm)·각도(°) 데이터를
실시간 360° 레이더로 표시하고, 값이 안 나올 때 로그와 세션 타임라인으로 원인을 진단하는
Windows 11용 Flet 데스크톱 앱. 상용 앱이 아닌 **초도 기능 검증(Bring-up Test) 도구**다.
많은 기능보다 "빠른 기본 동작 확인"이 최우선.

## Ground Truth 문서 (반드시 먼저 읽을 것)
- `../Flet_레이더_테스트앱_요구사항정의서.md` — 무엇을 만들지 (FR/NFR, UART 포맷, UI, 검수 기준)
- `../Flet_레이더_코딩가이드_하네스엔지니어링.md` — 어떻게 만들지 (아키텍처, 코딩 규칙)

**충돌 해결 규칙:** 두 문서가 다르면 **코딩가이드가 우선**한다. 확정된 결정:
1. 파일 구조는 코딩가이드 2장을 따른다 → `serial_worker.py`는 만들지 않는다.
   시리얼 수신은 `radar_device.py`의 `QorvoSerialDevice` 내부 스레드가 담당.
2. `parser.parse_line()` 반환은 정의서 9.3의 dict가 아니라 **dataclass `ParseResult`**로 한다.
   필드: `kind`("measurement"|"state"|"invalid"), `measurement: Measurement|None`,
   `event: DeviceEvent|None`, `raw: str`, `error: str|None`, `ts: float`
3. 동시성은 콜백 → `queue.Queue` → 메인 스레드 타이머(50ms) 방식 (코딩가이드 5장).

## 기술 스택 (고정 — 변경 금지)
- Python 3.11+ / Flet(UI) / pyserial(시리얼) / flet.canvas(레이더 직접 렌더)
- threading + queue.Queue (시리얼=백그라운드 스레드, UI=메인 스레드)
- 외부 차트 라이브러리 금지. 추가 서버 금지. 실행: `flet run main.py`

## 파일 구조 (고정)
```
radar_test_console/
├── main.py            # UI 진입점 + USE_SIMULATOR 전환 1줄
├── radar_view.py      # Canvas 360° 레이더 위젯 (UI 전용)
├── radar_device.py    # RadarDevice(ABC) + QorvoSerialDevice + SimulatorDevice
├── parser.py          # 라인 파싱 (순수 함수, 단위테스트 대상)
├── models.py          # Measurement, DeviceEvent, SessionState, ParseResult
├── tests/test_parser.py
├── requirements.txt   # 버전 고정 (flet==x.y, pyserial==x.y)
└── README.md
```

## 아키텍처 필수 규칙
- UI는 `RadarDevice` 인터페이스에만 의존. **UI 코드에 `import serial` 절대 금지.**
- `RadarDevice` 계약: `connect(port, baud=115200)` / `disconnect()` / `is_connected()` /
  `start_ranging()` / `stop_ranging()` / `list_ports()` +
  콜백 `on_measurement` / `on_state` / `on_log`
- 콜백은 백그라운드 스레드에서 발생 → 큐에만 넣고, 메인 스레드가 꺼내 `page.update()`.
  스레드에서 Flet 위젯 직접 갱신 금지.
- `main.py`의 `USE_SIMULATOR = True/False` 한 줄로 실물/시뮬레이터 전환.
- `SimulatorDevice`: 세션 단계(BLE_ADV→BLE_CONN→OOB_DONE→RANGING)를 흘린 뒤
  거리/각도 랜덤 워크 10Hz 생성. 실패 재현 토글(`STATE:ERR,REASON:OOB_TIMEOUT`) 포함.
- `start_ranging`/`stop_ranging`은 펌웨어 미지원 가능성 있음 → 실패해도 앱이 죽지 않게 no-op+로그.

## 데이터 계약 (UART, LF 종단, 한 줄 한 메시지)
- 측정: `DIST:85,ANGLE:-12` — DIST=cm(0~5000), ANGLE=°(-90~+90, 0=정면, 음수=좌)
- 키 순서/대소문자 무관. ANGLE 없으면 거리만 표시(angle=None, UI는 'N/A').
- 세션: `STATE:<SLEEP|BLE_ADV|BLE_CONN|OOB_DONE|RANGING|ERR>` (ERR은 `,REASON:<사유>` 동반)
- `RSSI:<dBm>`은 신호 세기로 인식해 `Measurement.rssi_dbm`에 채운다(2026-07-14 확장). 그 외
  알 수 없는 추가 필드(Q 등)는 무시하고 파싱 성공 처리 (전방 호환).
- 깨진 라인은 앱을 죽이지 말고 `kind="invalid"` + 원문을 로그에 남길 것.
- 범위 초과 값은 파싱 성공 처리하되 UI에서 경고색 표시.

## 레이더 좌표계
- 중심=보드, 반지름=거리, 0°=화면 위(12시), 좌측 음수/우측 양수, 표시 범위 −90°~+90°
- `x = cx + r_px*sin(θ)`, `y = cy - r_px*cos(θ)`, `r_px = dist/max_dist * radius_px`
- 동심원 눈금 50/100/200/300cm (max_dist 기본 300, 상수로), 각도선 −90/−45/0/+45/+90°

## 동작 기준 (NFR)
- 표시 지연 ≤100ms, 갱신 ≥10fps, 2초 무수신 시 '수신없음', 케이블 분리 시 '끊김'+로그
- 연결 해제/앱 종료 시 스레드 안전 종료 (플래그+join, 좀비 스레드 금지)

## 코딩 규칙
- 모든 함수에 type hint. class·주요 메서드에 한국어 한 줄 독스트링.
- 매직넘버 금지(파일 상단 상수화). `print` 금지 → `logging`. `except: pass` 금지.
- 한 함수 30줄 이내, 한 파일 한 책임. 주석은 '왜' 위주.
- ruff/black/mypy 통과 기준. `parser.py`는 순수 함수 + pytest 단위테스트.

## 구현 순서 (한 단계씩, 각 단계 끝에서 멈춰 사용자 확인)
1. models.py + parser.py + tests/test_parser.py (pytest 통과 확인)
2. radar_device.py — RadarDevice(ABC) + SimulatorDevice
3. UI 전체(main.py, radar_view.py: 레이더·수치패널·세션타임라인·로그콘솔)를 시뮬레이터로 완성
4. QorvoSerialDevice (실물 pyserial 구현)
5. 안정성: 자동 재연결, 수신 워치독, 포트 점유 에러 처리, 로그 파일 저장

## 검증 명령
```bash
pip install -r requirements.txt
pytest tests/ -v
flet run main.py      # USE_SIMULATOR=True로 보드 없이 확인
```
