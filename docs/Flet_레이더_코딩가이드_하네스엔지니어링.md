# Flet 360° 레이더 테스트앱 — 코딩 가이드 (하네스 엔지니어링)

> **이 가이드의 위치:** 「Flet_레이더_테스트앱_요구사항정의서」가 **무엇을 만들지**라면, 이 문서는 **어떻게(어떤 구조·규칙으로) 만들지**다. Claude Code로 코드를 생성할 때 이 규칙을 함께 물려준다.
>
> **3대 목표**
> 1. **사람이 같이 디버깅한다** → 생성 코드는 읽고 고치기 쉬워야 한다.
> 2. **Qorvo 단말 인터페이스를 class로 추상화** → UI는 class 메서드만 호출.
> 3. **하네스 엔지니어링** → 실물 보드 없이 **시뮬레이터로 교체**해 개발·테스트.
>
> 작성 기준일: 2026-06-24 · 버전 v0.1

---

## 0. 왜 이렇게 하나 (3줄 요약)

- UWB 보드는 **항상 곁에 없고**, 있어도 **값이 잘 안 나온다**(OOB·전파 문제). 그래서 **가짜 데이터로 돌려보는 하네스**가 없으면 개발이 멈춘다.
- 시리얼·파싱·스레드 같은 **하드웨어 잡일을 한 class 안에 가두면**, UI는 화면만 신경 쓰면 되고 사람이 보기 쉬워진다.
- 실물과 시뮬레이터가 **같은 인터페이스**를 쓰면, **코드 한 줄(어떤 class를 쓸지)만 바꿔** 보드 유무를 오간다.

---

## 1. 사람이 디버깅하기 쉬운 코드 규칙 (필수)

생성되는 모든 `.py`는 아래를 지킨다. (Claude Code 프롬프트에도 명시)

| 규칙 | 내용 |
|---|---|
| **타입 힌트** | 모든 함수 인자·반환에 type hint. (`def connect(self, port: str, baud: int = 115200) -> None`) |
| **독스트링** | 각 class·주요 메서드 상단에 **한국어 한 줄 설명**(이 함수가 뭘 하는지). |
| **작은 함수** | 한 함수는 한 가지 일만. 30줄 넘으면 쪼갠다. |
| **상수화** | `115200`, `300`(최대거리), `2.0`(타임아웃) 같은 값은 매직넘버 금지 → 파일 상단 상수. |
| **한 파일 한 책임** | 시리얼/파싱/UI/모델을 한 파일에 섞지 않는다(2장 구조). |
| **구조적 로깅** | `print` 대신 `logging` 사용. 레벨(DEBUG/INFO/WARN/ERROR) 구분. |
| **주석은 '왜'** | 코드가 '무엇'은 이미 말하므로, 주석은 **이유·주의점**(예: "스레드에서 직접 UI 갱신 금지") 위주. |
| **예외 삼키지 않기** | `except: pass` 금지. 잡으면 로그를 남기고 의미 있게 처리. |

---

## 2. 아키텍처 — 3계층 분리

UI는 **추상 인터페이스에만 의존**하고, 실물/시뮬레이터는 그 뒤에 숨는다. (의존성 역전)

```
┌───────────────────────────────────────────────┐
│  UI 계층  (main.py, radar_view.py)             │
│   · 화면 그리기, 콜백 등록만                    │
│   · RadarDevice "인터페이스"만 안다 ───────────┐│
└───────────────────────────────────────────────┘│
                    ▼ (메서드 호출 / 콜백 수신)     │ 의존
┌───────────────────────────────────────────────┐│
│  디바이스 추상화 계층  (radar_device.py)        ││
│   · RadarDevice (ABC, 인터페이스)  ◀───────────┘│
│     ├─ QorvoSerialDevice  (실물: pyserial)      │
│     └─ SimulatorDevice    (가짜: 랜덤 데이터)   │
└───────────────────────────────────────────────┘
                    ▼
┌───────────────────────────────────────────────┐
│  보조  (parser.py, models.py)                   │
│   · 라인 → Measurement/DeviceEvent 변환         │
└───────────────────────────────────────────────┘
```

**핵심 원칙:** UI 코드 어디에도 `import serial`이 등장하면 안 된다. 시리얼은 디바이스 계층 안에만 존재한다.

### 파일 구성

```
radar_test_console/
├── main.py            # UI 진입점 + 디바이스 선택(실물/시뮬) 1줄
├── radar_view.py      # Canvas 360° 레이더 위젯 (UI 전용)
├── radar_device.py    # RadarDevice(ABC) + QorvoSerialDevice + SimulatorDevice
├── parser.py          # 라인 파싱 (순수 함수, 단위테스트 대상)
├── models.py          # Measurement, DeviceEvent, SessionState
├── tests/test_parser.py
├── requirements.txt
└── README.md
```

---

## 3. Qorvo 단말 추상화 — `RadarDevice` 인터페이스 (핵심)

UI가 호출할 **계약(약속)**을 먼저 정의한다. 실물이든 가짜든 이 약속만 지키면 된다.

### 3.1 데이터 모델 (`models.py`)

```python
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class SessionState(str, Enum):
    """BLE OOB ~ UWB 세션 단계 (요구사항정의서 5.4의 STATE 값)."""
    SLEEP = "SLEEP"
    BLE_ADV = "BLE_ADV"
    BLE_CONN = "BLE_CONN"
    OOB_DONE = "OOB_DONE"
    RANGING = "RANGING"
    ERR = "ERR"
    UNKNOWN = "UNKNOWN"


@dataclass
class Measurement:
    """한 번의 거리/각도 측정값."""
    dist_cm: Optional[int]      # 거리(cm). 없으면 None
    angle_deg: Optional[int]    # 각도(°, 0=정면). 미지원이면 None
    raw: str                    # 원시 수신 라인 (디버깅용)
    ts: float                   # 수신 시각(epoch)


@dataclass
class DeviceEvent:
    """세션 상태/에러 이벤트."""
    state: SessionState
    reason: Optional[str]       # ERR일 때 사유 (예: "OOB_TIMEOUT")
    raw: str
    ts: float
```

### 3.2 추상 인터페이스 (`radar_device.py`)

```python
from abc import ABC, abstractmethod
from typing import Callable, List
from models import Measurement, DeviceEvent

# 콜백 타입: 디바이스가 이벤트가 생기면 UI가 등록한 함수를 호출한다.
MeasurementCallback = Callable[[Measurement], None]
StateCallback = Callable[[DeviceEvent], None]
LogCallback = Callable[[str], None]


class RadarDevice(ABC):
    """Qorvo 단말 추상화. UI는 오직 이 인터페이스에만 의존한다.

    사용 흐름:
        dev = QorvoSerialDevice()      # 또는 SimulatorDevice()
        dev.on_measurement = ...       # 콜백 등록
        dev.connect("COM3")
        ...
        dev.disconnect()
    """

    def __init__(self) -> None:
        # UI가 채워 넣는 콜백 (기본은 아무것도 안 함)
        self.on_measurement: MeasurementCallback = lambda m: None
        self.on_state: StateCallback = lambda e: None
        self.on_log: LogCallback = lambda s: None

    # --- 연결 관리 ---
    @abstractmethod
    def connect(self, port: str, baud: int = 115200) -> None:
        """단말에 연결하고 백그라운드 수신을 시작한다."""

    @abstractmethod
    def disconnect(self) -> None:
        """수신 스레드를 안전하게 종료하고 연결을 닫는다."""

    @abstractmethod
    def is_connected(self) -> bool:
        """현재 연결 여부."""

    # --- (선택) 명령 ---
    @abstractmethod
    def start_ranging(self) -> None: ...
    @abstractmethod
    def stop_ranging(self) -> None: ...

    # --- 유틸 ---
    @staticmethod
    @abstractmethod
    def list_ports() -> List[str]:
        """선택 가능한 포트 목록 (시뮬레이터는 ['SIM'] 반환)."""
```

> **콜백 계약:** 디바이스는 데이터를 받으면 `on_measurement`/`on_state`를, 로그는 `on_log`를 호출만 한다. **화면을 직접 건드리지 않는다.** UI가 그 콜백 안에서 화면을 갱신한다. (4.3 스레드 주의 참고)

---

## 4. 하네스 엔지니어링 — 실물 ↔ 시뮬레이터 교체

같은 인터페이스의 두 구현. UI는 어느 쪽인지 모른다.

### 4.1 실물 구현 `QorvoSerialDevice` (요지)

```python
import serial, threading, time
from serial.tools import list_ports
from parser import parse_line   # 순수 파싱 함수

class QorvoSerialDevice(RadarDevice):
    """실제 DWM3001CDK와 USB-UART로 통신하는 구현."""

    def connect(self, port: str, baud: int = 115200) -> None:
        self._ser = serial.Serial(port, baud, timeout=1)
        self._running = True
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    def _reader(self) -> None:
        """백그라운드: 한 줄 읽어 파싱 → 콜백 호출. (UI 직접 호출 금지)"""
        while self._running:
            line = self._ser.readline().decode(errors="replace").strip()
            if not line:
                continue
            self.on_log(line)                 # 원시 로그
            result = parse_line(line)          # parser가 종류 판별
            if result.kind == "measurement":
                self.on_measurement(result.measurement)
            elif result.kind == "state":
                self.on_state(result.event)

    def disconnect(self) -> None:
        self._running = False                  # 스레드 종료 플래그
        self._thread.join(timeout=2)
        self._ser.close()
    # ... is_connected / start_ranging / stop_ranging / list_ports
```

### 4.2 시뮬레이터 구현 `SimulatorDevice` (보드 없이 개발)

```python
import threading, time, random

class SimulatorDevice(RadarDevice):
    """보드 없이 가짜 거리/각도와 세션 상태를 만들어 내는 하네스."""

    def connect(self, port: str = "SIM", baud: int = 115200) -> None:
        self._running = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        # 1) OOB 세션 단계를 순서대로 흘려보낸다 (T-0~T-3 재현)
        for st in ["BLE_ADV", "BLE_CONN", "OOB_DONE", "RANGING"]:
            self.on_state(DeviceEvent(SessionState(st), None, f"STATE:{st}", time.time()))
            time.sleep(0.5)
        # 2) RANGING 진입 후 거리/각도를 랜덤 워크로 생성
        dist, angle = 200, 0
        while self._running:
            dist = max(20, min(300, dist + random.randint(-10, 10)))
            angle = max(-90, min(90, angle + random.randint(-5, 5)))
            raw = f"DIST:{dist},ANGLE:{angle}"
            self.on_log(raw)
            self.on_measurement(Measurement(dist, angle, raw, time.time()))
            time.sleep(0.1)   # 10Hz
    # ...
```

> 시뮬레이터는 **정상 흐름뿐 아니라 실패도 재현**할 수 있다. 예: `OOB_DONE` 대신 `ERR,REASON:OOB_TIMEOUT`을 흘려 UI의 에러 표시를 테스트. (테스트 시나리오 토글 권장)

### 4.3 교체는 1줄 — UI는 안 바뀐다 (`main.py`)

```python
# 보드가 없으면 시뮬레이터, 있으면 실물. 이 한 줄만 바꾸면 된다.
USE_SIMULATOR = True
device: RadarDevice = SimulatorDevice() if USE_SIMULATOR else QorvoSerialDevice()
```

이게 **하네스 엔지니어링의 핵심 이득**: UI·레이더·로그 코드를 보드 없이 100% 완성·디버깅한 뒤, 마지막에 `USE_SIMULATOR = False`로 바꿔 실물 검증으로 넘어간다.

---

## 5. UI에서의 사용법 — class만 호출

UI는 디바이스의 **메서드 호출 + 콜백 등록**만 한다. 시리얼·스레드를 전혀 모른다.

```python
import queue
ui_events = queue.Queue()   # 스레드 → UI 안전 전달용

def build_ui(page):
    # 콜백은 백그라운드 스레드에서 불릴 수 있으므로, 큐에만 넣는다.
    device.on_measurement = lambda m: ui_events.put(("meas", m))
    device.on_state       = lambda e: ui_events.put(("state", e))
    device.on_log         = lambda s: ui_events.put(("log", s))

    def on_connect_click(e):
        device.connect(selected_port)     # ← class만 호출

    # 메인 스레드 타이머가 큐를 비우며 화면 갱신 (50ms)
    def pump():
        while not ui_events.empty():
            kind, data = ui_events.get()
            if kind == "meas":  radar_view.update_point(data)
            elif kind == "state": timeline.update(data)
            elif kind == "log":   log_console.append(data)
        page.update()
```

> **스레드 주의(사람이 자주 트러블):** 디바이스 콜백은 **백그라운드 스레드**에서 발생한다. 거기서 Flet 위젯을 직접 만지면 깨질 수 있다. 그래서 **큐에 넣고 메인 스레드(타이머)가 꺼내 그린다.** 이 규칙 하나가 화면 깨짐의 90%를 막는다.

---

## 6. 디버깅을 돕는 장치 (하네스의 일부)

| 장치 | 효과 |
|---|---|
| **시뮬레이터 모드** | 보드 없이 UI 전체 검증 (4장) |
| **실패 시나리오 토글** | `OOB_TIMEOUT` 등 에러를 일부러 발생시켜 예외 표시 확인 |
| **raw 패스스루** | 모든 원시 라인을 로그에 그대로 → 파싱 실패 원인 즉시 확인 |
| **로그 레벨 스위치** | DEBUG로 켜면 파싱 과정·스레드 상태까지 출력 |
| **parser 단위테스트** | `tests/test_parser.py` — 정상/깨짐/STATE/범위초과 케이스. 보드·UI 없이 파싱만 검증 |
| **로그 파일 저장** | 세션을 .csv로 남겨 사후 분석 (요구사항 FR-6) |

`parser.py`를 **순수 함수(입력 라인 → 결과 객체)**로 두는 이유가 여기 있다: 하드웨어·UI 없이 가장 빠르게 테스트되는 부분이라, 버그를 여기서 먼저 잡는다.

---

## 7. 이후 추가 권장 사항 (Additional Considerations)

요청하신 "이후 추가사항"을 우선순위로 정리한다.

### 7.1 바로 적용 (코드 품질·협업)
1. **포맷터·린터** — `ruff`(린트) + `black`(포맷) 적용. 생성 코드도 일관된 스타일로 → 사람이 읽기 쉬움.
2. **타입 체크** — `mypy`로 type hint 검증. 인터페이스 위반을 컴파일 단계에서 발견.
3. **requirements 고정** — `flet==x.y`, `pyserial==x.y` 버전 핀. "내 PC에선 됐는데" 방지.
4. **README** — 실행법(`pip install -r requirements.txt`, `flet run`), 시뮬/실물 전환 방법 명시.
5. **`tests/` + pytest** — 최소 parser 단위테스트. CI에 붙이면 회귀 방지.

### 7.2 안정성 (실물 단계에서 중요)
6. **자동 재연결** — 케이블 분리 후 재삽입 시 포트 재탐색·재연결.
7. **스레드 안전 종료** — 앱 종료 시 `disconnect()` 보장(좀비 스레드·포트 점유 방지).
8. **포트 점유 처리** — 다른 프로그램(예: TeraTerm)이 포트를 잡고 있으면 친절한 에러.
9. **수신 워치독** — 2초 무수신 시 '수신없음' (요구사항 NFR-3).

### 7.3 설정·확장
10. **설정 파일**(`config.toml`) — 기본 포트·Baudrate·최대거리·시뮬 여부를 외부화. 코드 수정 없이 바꾸기.
11. **프로토콜 버전 필드** — 펌웨어 포맷이 바뀔 때 대비해 `VER:` 같은 식별자 합의.
12. **인터페이스 안정성** — 나중에 BLE 직접 제어나 다중 보드가 생겨도, `RadarDevice`만 새 구현 추가하면 UI는 그대로.
13. **로깅 표준화** — 파일·콘솔 동시 출력, 회전 로그(RotatingFileHandler).

### 7.4 (검토 필요) 팀 합의 항목
- 펌웨어가 `STATE:` 세션 메시지를 실제로 내보낼 수 있는가 (요구사항 5.4 — OOB 관찰 전제).
- 코드 주석·문서를 **한국어/영어** 중 무엇으로 통일할지 (현재 가이드는 독스트링 한국어 권장).

---

## 8. Claude Code에 넘길 빌드 지시 (이 가이드 반영판)

> 요구사항정의서 10장 프롬프트에 **아래 단락을 덧붙여** 사용한다.

```
[코딩 구조 — 하네스 엔지니어링]
 - Qorvo 단말 접근을 RadarDevice(ABC) 인터페이스로 추상화하라.
   구현은 두 개: QorvoSerialDevice(실물 pyserial)와 SimulatorDevice(가짜 데이터).
   둘은 동일 인터페이스(connect/disconnect/is_connected/start_ranging/stop_ranging/list_ports
   + on_measurement/on_state/on_log 콜백)를 가진다.
 - UI 코드에는 절대 'import serial'을 넣지 마라. 시리얼은 디바이스 계층에만.
 - main.py의 USE_SIMULATOR 한 줄로 실물/시뮬을 전환할 수 있게 하라.
 - SimulatorDevice는 OOB 세션 단계(BLE_ADV→BLE_CONN→OOB_DONE→RANGING)를 흘린 뒤
   거리/각도를 랜덤 워크로 생성하고, 실패(STATE:ERR,REASON:OOB_TIMEOUT) 재현 토글도 둬라.
 - 콜백은 백그라운드 스레드에서 발생하므로 queue로 메인 스레드에 넘겨 UI를 갱신하라.

[가독성 — 사람이 함께 디버깅]
 - 모든 함수에 type hint, 각 class/주요 메서드에 한국어 한 줄 독스트링.
 - 매직넘버 금지(상수화), print 대신 logging, except: pass 금지.
 - parser.py는 순수 함수로 두고 tests/test_parser.py(pytest) 포함.
 - ruff/black/mypy 통과 기준으로 작성하고 requirements.txt 버전 고정, README 포함.

[순서] 1) models/parser + 단위테스트 → 2) RadarDevice + SimulatorDevice →
 3) UI(레이더·로그·타임라인)를 시뮬레이터로 완성 → 4) QorvoSerialDevice 연결 →
 5) 자동재연결·워치독 등 안정성. 각 단계 끝에서 멈춰 확인하자.
```

---

### 한 줄 요약
**"인터페이스 하나, 구현 둘(실물·가짜), UI는 인터페이스만."** 이 구조면 사람이 보드 없이도 코드를 읽고 고치고 돌려볼 수 있고, 실물 전환은 마지막에 한 줄로 끝난다.
