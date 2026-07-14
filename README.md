# 360° 레이더 테스트 콘솔 (radar_test_console)

Qorvo **DWM3001CDK** 보드가 USB-UART로 보내는 UWB **거리(cm)·각도(°)** 데이터를
실시간 360° 레이더로 표시하고, 값이 안 나올 때 **로그와 세션 타임라인으로 원인을 진단**하는
Windows 11용 Flet 데스크톱 앱입니다. 상용 앱이 아닌 **초도 기능 검증(Bring-up Test) 도구**입니다.

> 상세 사양은 `docs/Flet_레이더_테스트앱_요구사항정의서.md`,
> 코딩 규칙은 `docs/Flet_레이더_코딩가이드_하네스엔지니어링.md` 참조.

---

## 1. 설치

요구 환경: **Windows 11, Python 3.11+**

```bash
cd radar_test_console
pip install -r requirements.txt
```

`requirements.txt`에는 실행용(flet, pyserial)과 개발용(pytest, ruff, black, mypy)이
버전 고정으로 들어 있습니다.

## 2. 실행

```bash
flet run main.py     # 또는: python main.py
```

## 3. 시뮬레이터 ↔ 실물 보드 전환 (한 줄)

보드가 없어도 전체 UI를 검증할 수 있습니다. `main.py` 상단의 **한 줄**만 바꿉니다.

```python
# main.py
USE_SIMULATOR = True   # 보드 없이 가짜 데이터로 실행 (개발·데모)
USE_SIMULATOR = False  # 실제 DWM3001CDK 연결 (기본값)
```

| 모드 | 포트 목록 | 데이터 |
|---|---|---|
| `True` (시뮬레이터) | `SIM` | 세션 4단계 재생 후 거리/각도 랜덤 워크 10Hz |
| `False` (실물) | PC의 COM 포트 자동 탐색 | 보드 UART 수신 |

시뮬레이터 모드에서는 연결바에 **"OOB 실패 재현"** 스위치가 나타납니다.
켜고 연결하면 `STATE:ERR,REASON:OOB_TIMEOUT`을 재현해 에러 표시(타임라인 적색)를 테스트할 수 있습니다.

## 4. 사용법 (화면 구성)

```
┌──────────────────────────────────────────────────────────────┐
│ [연결 바] 포트[COM3▼] ↻  속도[115200▼] [시작][정지]  ●정상 [연결/해제] │
├──────────────────────────────────────────────────────────────┤
│ [세션 타임라인]  SLEEP → BLE_ADV → BLE_CONN → OOB_DONE → RANGING │
├───────────────────────────────────┬──────────────────────────┤
│                                   │ [수치 패널]               │
│        360° 레이더 뷰              │  거리 / 각도              │
│   (동심원 50/100/200/300cm,        │  수신율(건/초)            │
│    각도선 −90~+90°, 0°=12시)       │  최종수신 / 세션 / 상태    │
├───────────────────────────────────┴──────────────────────────┤
│ [로그 콘솔]  timestamp + RX/TX + 원문 + 파싱결과   [저장][지우기] │
└──────────────────────────────────────────────────────────────┘
```

1. **연결**: 포트 선택(안 보이면 ↻ 새로고침) → 속도 확인(기본 115200) → [연결].
2. **레이더**: 점 = 태그 위치. 중심이 보드, 반지름이 거리, 0°가 화면 위(12시), 좌측 음수.
   범위 초과 값(거리>5000cm, 각도>±90°)은 **주황 경고색**으로 표시됩니다.
3. **세션 타임라인**: 보드가 보내는 `STATE:` 메시지 기준.
   완료=**녹** / 진행중=**청** / 대기=**회** / 실패=**적**+사유.
   거리값이 안 나올 때 **어느 단계에서 멈췄는지**가 여기서 보입니다.
4. **[시작]/[정지]** (FR-8): 보드에 `START`/`STOP` 명령을 보냅니다.
   펌웨어가 미지원이면 앱은 죽지 않고 로그만 남습니다(no-op).
5. **로그 [저장]** (FR-6): 세션 로그 전체를 `logs/radar_log_YYYYMMDD_HHMMSS.txt`로 저장.
   [지우기]는 화면만 비우며 저장용 히스토리는 유지됩니다.

### 상태 표시 (LED·수치 패널 공통)

| 상태 | 색 | 의미 |
|---|---|---|
| 미연결 | 회 | 연결 전 |
| 정상 | 녹 | 데이터 수신 중 |
| 수신없음 | 회 | **2초** 이상 아무 라인도 안 들어옴 (NFR-3) → 점 숨김 + 로그 경고 |
| 끊김 | 적 | 케이블 분리 등으로 수신 루프 중단 → **포트 복귀 시 2초 주기 자동 재연결** (NFR-4) |

### 진단 요령 (이 앱의 핵심 가치)

| 증상 | 해석 |
|---|---|
| 로그에 아무것도 안 흐름 (`RX 0건`) | 펌웨어가 출력 자체를 안 함 |
| 붉은 `✘ 파싱 실패` 라인이 흐름 | 포맷/Baudrate 불일치 (원문이 그대로 보임) |
| `STATE:`는 오는데 DIST가 없음 | 타임라인에서 멈춘 단계 확인: `BLE_ADV`에서 멈춤=폰이 안 깨어남, `BLE_CONN`까지=OOB 실패, `RANGING`인데 DIST 없음=PHY 문제 |
| `⚠ 연결 오류` (포트 점유) | TeraTerm/putty 등 다른 프로그램을 닫고 재시도 |

## 5. 아키텍처 (3계층 분리)

UI는 `RadarDevice` **추상 인터페이스에만 의존**하고, 실물/시뮬레이터는 그 뒤에 숨습니다.
**UI 코드에는 `import serial`이 없습니다** — 시리얼은 디바이스 계층에만 존재합니다.

```
┌───────────────────────────────────────────────┐
│  UI 계층  (main.py, radar_view.py)             │
│   · 화면 그리기, 콜백 등록만                    │
│   · RadarDevice "인터페이스"만 안다             │
└───────────────┬───────────────────────────────┘
                ▼ 메서드 호출 / 콜백(큐 경유) 수신
┌───────────────────────────────────────────────┐
│  디바이스 추상화 계층  (radar_device.py)        │
│   · RadarDevice (ABC)                          │
│     ├─ QorvoSerialDevice  (실물: pyserial)      │
│     └─ SimulatorDevice    (가짜: 랜덤 워크)     │
└───────────────┬───────────────────────────────┘
                ▼
┌───────────────────────────────────────────────┐
│  보조  (parser.py, models.py)                   │
│   · 라인 → Measurement/DeviceEvent 변환 (순수)  │
└───────────────────────────────────────────────┘
```

### 동시성 모델 (스레드 규칙 — 위반 금지)

```
[디바이스 스레드]                     [메인(UI) 스레드]
readline/생성 루프                    50ms 펌프 루프 (RadarApp.pump_loop)
  → parse_line                         → queue에서 꺼냄
  → on_measurement/on_state/on_log      → 위젯 갱신
  → queue.Queue에 put만 한다            → page.update()
```

- 디바이스 콜백은 **백그라운드 스레드**에서 불린다. 콜백 안에서 Flet 위젯을 만지지 말 것.
- UI 갱신은 **펌프 루프에서만**. 이 규칙 하나가 화면 깨짐의 90%를 막는다.
- 연결 해제/앱 종료 시 플래그 → `join(timeout=2)` → `close()` 순서로 안전 종료 (좀비 스레드 금지).

## 6. 소스 구성

| 파일 | 책임 | 주요 내용 |
|---|---|---|
| `main.py` | UI 진입점·배선 | `USE_SIMULATOR` 스위치, `SessionTimeline`/`NumericPanel`/`LogConsole` 위젯, `RadarApp`(큐 펌프·워치독·자동 재연결·상태 판정) |
| `radar_view.py` | 레이더 위젯 (UI 전용) | flet.canvas 폴라 렌더. 좌표 변환 `x=cx+r·sin θ, y=cy−r·cos θ`, 동심원·각도선 배경 + 점 1개 |
| `radar_device.py` | 디바이스 계층 | `RadarDevice`(ABC 계약), `QorvoSerialDevice`(pyserial readline 루프, 친절한 포트 에러), `SimulatorDevice`(세션 재생+랜덤 워크, 실패 토글) |
| `parser.py` | 라인 파싱 (순수 함수) | `parse_line(line, ts) -> ParseResult`, 범위 판별 `is_out_of_range()`. 하드웨어·UI 없이 단위테스트되는 계층 |
| `models.py` | 데이터 모델 | `Measurement`, `DeviceEvent`, `SessionState`(enum), `ParseResult` |
| `tests/test_parser.py` | 파서 단위테스트 | 정상/키순서/소문자/깨진 라인/STATE 5종/ERR/범위초과/미지필드 등 19케이스 |

### 데이터 계약 (UART, LF 종단, 한 줄 한 메시지)

```
DIST:85,ANGLE:-12                → 측정 (키 순서·대소문자 무관, ANGLE 없으면 N/A)
DIST:85,ANGLE:-12,RSSI:-60       → RSSI(dBm)까지 인식, 그 외 미지 필드는 무시 (전방 호환)
STATE:RANGING                    → 세션 이벤트 (SLEEP/BLE_ADV/BLE_CONN/OOB_DONE/RANGING/ERR)
STATE:ERR,REASON:OOB_TIMEOUT     → 실패 + 사유
(그 외 깨진 라인)                 → kind="invalid", 앱 무중단 + 원문 로그
```

## 7. 디바이스 연결 모듈 개발 가이드 (새 RadarDevice 구현)

BLE 직접 제어, 다중 보드, 다른 펌웨어 등 새 연결 방식이 생기면
**`RadarDevice`를 상속한 클래스 하나만 추가**하면 됩니다. UI는 그대로입니다.

### 7.1 계약 (반드시 전부 구현)

```python
class MyDevice(RadarDevice):
    def connect(self, port: str, baud: int = 115200) -> None: ...
    def disconnect(self) -> None: ...
    def is_connected(self) -> bool: ...
    def start_ranging(self) -> None: ...   # 미지원이면 no-op + 로그
    def stop_ranging(self) -> None: ...    # 미지원이면 no-op + 로그
    @staticmethod
    def list_ports() -> List[str]: ...
```

콜백 3개는 베이스 클래스가 no-op으로 초기화해 두므로 **호출만** 하면 됩니다:

- `self.on_measurement(Measurement(...))` — 측정 1건
- `self.on_state(DeviceEvent(...))` — 세션 상태 변화
- `self.on_log(str)` — 원시 수신 라인(디버깅용 패스스루)

### 7.2 지켜야 할 규칙 (체크리스트)

- [ ] 수신은 **백그라운드 스레드**에서. 콜백에서 UI를 직접 만지지 않는다 (호출만).
- [ ] `connect()` 실패 시 **예외를 밖으로 던지지 말 것** — `on_log`에 사람이 읽을 사유를 남기고
      복귀한다 (`is_connected()`가 False면 UI가 알아서 처리).
      연결 계층 오류 로그는 `LINK_LOG_PREFIX`(`"[LINK]"`)로 시작하면 UI가 적색 "⚠ 연결 오류"로 표시.
- [ ] `disconnect()`는 플래그 → `join(timeout)` → 자원 해제 순서. **좀비 스레드 금지.**
- [ ] 라인 해석은 직접 하지 말고 `parser.parse_line()`을 재사용한다
      (`kind`에 따라 `on_measurement`/`on_state` 라우팅, invalid는 `on_log`만).
- [ ] 깨진 데이터·수신 오류로 **앱이 죽으면 안 된다** (NFR-5). 예외는 잡아서 로그.
- [ ] 매직넘버는 파일 상단 상수로, `print` 대신 `logging`, 모든 함수에 type hint.

### 7.3 UI에 연결

```python
# main.py
USE_SIMULATOR = False
# RadarApp._create_device()에서 새 구현을 반환하도록 한 줄 변경
return MyDevice()
```

참고 구현: 실물은 `QorvoSerialDevice._reader()`, 하네스는 `SimulatorDevice._run()`을 보세요.
자동 재연결·워치독·상태 판정은 전부 UI(`RadarApp`) 쪽에 있으므로 디바이스는 신경 쓸 필요 없습니다.

## 8. 테스트·코드 품질

```bash
pytest tests/ -v                  # 파서 단위테스트 (19케이스)
ruff check .                      # 린트
black .                           # 포맷
mypy --ignore-missing-imports .   # 타입 체크 (pyserial은 스텁 미제공)
```

## 9. 검수 기준 요약 (요구사항정의서 8장)

1. 보드 연결 후 5초 내 거리값 표시
2. 태그 이동 시 점이 거리·각도에 맞게 이동
3. 케이블 분리 시 2초 내 '끊김/수신없음' 표시 (+포트 복귀 시 자동 재연결)
4. 깨진 데이터에도 앱 무중단, 원문 로그 보존
5. '데이터 없음'과 '파싱 실패'를 로그로 구분 가능
6. 세션 타임라인으로 멈춘 단계 식별
7. `STATE:ERR` 수신 시 해당 단계 적색+사유
8. 로그 파일 저장(`logs/*.txt`)으로 사후 분석
