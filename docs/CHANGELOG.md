# CHANGELOG — 360° 레이더 테스트 콘솔

> 날짜별 변경 이력. 새 작업을 커밋할 때마다 **맨 위에** 항목을 추가한다.
> 상세 배경·검증 과정은 각 날짜의 `작업기록_*.md` / `작업일지_*.md` 참고.

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
