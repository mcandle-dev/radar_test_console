# Q&A 노트 — UCI/CLI 펌웨어와 Android Pixel 인터롭 (2026-07-03)

보드에 올라간 펌웨어와 이 테스트 콘솔의 호환성에 대해 문의·답변한 내용 정리.

## Q1. 보드에 `DWM3001CDK-UCI-FreeRTOS.hex`가 올라가 있는데 프로그램이 잘 도나?

**앱은 실행되지만, 이 펌웨어 단독으로는 측정값이 표시되지 않는다.**

- 이 앱은 LF 종단 ASCII 텍스트 라인(`DIST:85,ANGLE:-12`, `STATE:RANGING`)을 전제한다
  (`radar_device.py`의 `readline().decode("utf-8")` → `parser.parse_line`).
- UCI 펌웨어는 **FiRa UCI 바이너리 프로토콜**을 쓴다. 텍스트가 아니므로 들어와도 전부
  `invalid`로 로그에만 쌓인다 (포맷 불일치).
- 더 근본적으로, UCI 펌웨어는 **스스로 데이터를 내보내지 않는다**. 호스트가 바이너리
  UCI 명령으로 세션을 설정·시작해줘야 레인징이 돈다. 그래서 포트를 열어도
  "RX 0건 / 수신없음" 상태가 된다 (동작 방식 불일치).
- 앱 자체는 설계대로 죽지 않는다: 깨진 라인 → `kind="invalid"` + 로그, 무수신 → 워치독 표시.

## Q2. `DWM3001CDK-CLI-FreeRTOS.hex`와 차이는? UCI면 다 되어야 하는 것 아닌가?

**한 이미지에 하나의 인터페이스만 들어 있다. UCI는 CLI의 상위 집합이 아니다.**

| | CLI 펌웨어 | UCI 펌웨어 |
|---|---|---|
| 대상 | 사람 (터미널 타이핑) | 호스트 소프트웨어 (PC/폰 UWB 스택) |
| 프로토콜 | ASCII 텍스트 명령/응답 | FiRa 표준 바이너리 패킷 (UCI) |
| 사용 방식 | `initf`/`respf` 치면 결과를 텍스트로 계속 출력 | 호스트가 세션 생성→설정→시작을 전부 수행 |
| 짝 | PuTTY/Tera Term, 이 테스트 콘솔 | Qorvo One, 자체 UCI 호스트 스택 |
| 용도 | 단독 평가·데모·브링업 | 제품 통합 (보드 = 수동적 UWB 서브시스템) |

UCI는 FiRa가 정한 **기계 간 인터페이스**라서 사람이 읽을 출력을 일부러 내지 않는다.
UWB 레인징 자체는 두 펌웨어가 동일하게 수행하며, 차이는 "누가 어떤 언어로 보드에게
말을 거는가"뿐이다.

## 조치: parser.py에 CLI 펌웨어 JSON 포맷 지원 추가 (이번 커밋)

보드↔보드 CLI 시나리오 대비로, CLI 펌웨어(DW3_QM33 SDK)의 JSON 블록 출력을
기존 `DIST:` 포맷과 공존 파싱하도록 확장했다.

```
{"Block":123,"results":[{"Addr":"0x0001","Status":"Ok","D_cm":85,"LAoA_deg":-12.50,...}]}
```

- 라인이 `{`로 시작하면 JSON 분기. `results` 배열(없으면 최상위)에서 `D_cm`를 가진
  첫 항목을 측정으로 채택. 키 대소문자 무관.
- `D_cm` → `dist_cm`, `LAoA_deg` → `angle_deg` (실수는 반올림 정수). `LAoA_deg` 없으면
  `angle_deg=None` → UI 'N/A'.
- 측정 없는 블록(`"Status":"Rx timeout"` 등)은 `invalid`로 분류하되 Status를 error
  사유에 보존 (로그 진단용). 깨진 JSON도 앱을 죽이지 않음 (NFR-5).
- 테스트 9개 추가, 전체 28개 통과. ruff/mypy 클린.

## Q3. controlee가 Android Pixel 폰이라면?

**반전: 이미 올라가 있는 UCI 펌웨어가 오히려 맞는 펌웨어다.** CLI 교체 권고는
보드↔보드 기준이었고, 폰 인터롭은 UCI 경로가 검증돼 있다.

- Pixel 8 Pro + DWM3001CDK(UCI-FreeRTOS) 조합에서 **보드=controller / 폰=controlee**
  방향은 "매우 좋은 결과"로 보고됨. 반대 방향(보드=controlee)은 타임아웃이 잦아 비권장.
- 단, PC가 **UCI 호스트 역할**을 해야 한다. 검증된 레퍼런스:
  [sasodoma/uwb-ranging](https://github.com/sasodoma/uwb-ranging) —
  PC측 `run_fira_twr.py`(UCI 호스트) + 짝이 되는 Android 앱.
  파라미터: 레인징 주기 120ms, slots/round 6, preamble index 9, hopping on,
  주소 수동 입력(BLE OOB 생략). 펌웨어↔스크립트 버전 조합은 리포 권장대로 맞출 것.
- 이번에 추가한 CLI JSON 파서는 UCI 경로에서는 쓰이지 않는다 (UCI는 바이너리).
- DWM3001CDK는 안테나 1개라 AoA(각도)는 어느 경로든 안 나온다 → 레이더에는 거리만
  (12시 방향) 찍히고 각도 'N/A'가 정상.

## 향후 계획 (보드 확보 후 — 현재 보드 없음)

1. 앱과 무관하게 sasodoma 스크립트 + Android 앱으로 보드↔Pixel 레인징 동작 확인 (브링업).
2. 확인되면 그 UCI 시퀀스를 `radar_device.py`의 **`UciSerialDevice`(신규)** 로 이식 —
   UCI 프레이밍 + 세션 설정/시작 + `SESSION_INFO_NTF` 파싱을 내부에서 처리하고
   기존 `on_measurement` 콜백으로 흘리면 UI/파서/시뮬레이터는 무변경. (6단계 예정)

## 참고 링크

- [sasodoma/uwb-ranging — UWB Ranging with DWM3001CDK and Android](https://github.com/sasodoma/uwb-ranging)
- [Qorvo Forum — DWM3001CDK and Google Pixel 8 Pro, ranging only works one way](https://forum.qorvo.com/t/dwm3001cdk-and-google-pixel-8-pro-ranging-only-works-one-way/18083)
- [Qorvo Forum — DWM3001CDK Controller And Android Controllee for UWB App](https://forum.qorvo.com/t/dwm3001cdk-controller-and-android-controllee-for-uwb-app/21151)
