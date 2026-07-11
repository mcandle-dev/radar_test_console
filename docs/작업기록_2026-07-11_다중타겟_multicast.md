# 작업 기록 — 2026-07-11 (2차): 다중 타겟 표시 + UCI multicast

## 작업 목적
- 폰 주소를 입력해 연결하면 **타겟(주소)별로 거리·각도·수신율이 각각 표시**되도록 UI를 개편한다.
- 폰 여러 대와 동시에 레인징할 수 있게 **UCI one-to-many(multicast) 세션**을 지원한다.
- 남은 작업(TODO)을 정리해 다음 세션이 바로 이어갈 수 있게 한다.

## 구현 요약 (이 커밋에 포함)

### 1) 타겟별 행 패널 (가변형 — main.py)
- 우측 패널이 타겟별 행 구조로 변경됨. 행마다 `● 주소  거리` + `각도 · 수신율 · 최종수신` 두 줄.
- **수신율·최종수신을 타겟별로 집계** (`_target_ts` 타임스탬프 창, `_target_rate()`).
- 타겟별 2초 무수신 시 해당 행만 회색(수신없음) 처리, 레이더에서 제외 (`_snapshot_target()`).
- 새 주소 관측 시 자동 행 추가, 최대 5개(`MAX_TARGETS`) 유지 — 초과 시 가장 오래 안 보인 타겟 제거.
- 5색 고정 팔레트(`TARGET_COLORS`)를 등장 순서로 배정 — 테이블 ●와 레이더 표시가 같은 색.
- 스냅숏 비교(`TargetRow` NamedTuple / `RadarTarget` frozen dataclass)로 변경 시에만 다시 그림.
- **주의(사용자 결정)**: 5행 고정 컬럼형 테이블로는 바꾸지 않고 **가변형 유지**.
  이 보류로 `main.py`의 `TABLE_FONT_SIZE`/`COL_*_W`/`TARGET_STATUS_*` 상수와
  `uci_params.split_dest_macs()`는 **현재 미사용 잔여물**이나, 소스는 그대로 두기로 함
  (OOB 2차나 테이블 재논의 시 재사용 가능). `PANEL_WIDTH`는 470으로 넓어진 상태.

### 2) 레이더 다중 표시 (radar_view.py)
- `update_points(List[RadarTarget])` — 각도 있는 타겟은 **점+라벨**, 각도 없는 타겟(UCI 보드,
  안테나 1개)은 **그 거리 반지름의 링+라벨**로 표시. 범위 초과는 주황으로 덮어씀.

### 3) 시뮬레이터 다중 타겟 (radar_device.py)
- `SimulatorDevice(num_targets=3)` 기본 3개 타겟("1","2","3")을 서로 다른 초기 위치에서 10Hz 생성.
  raw 라인은 `DIST:..,ANGLE:..,TARGET:n` — parser의 TARGET 경로도 함께 검증됨.

### 4) UCI multicast (uci_params.py, radar_device.py)
- 폰 주소 입력칸에 쉼표/공백으로 여러 개 입력 (최대 `MAX_CONTROLEES=5`, 예: `5F:DD, A1:B2`).
- `parse_dest_macs()` → 주소 수에 따라 `MULTI_NODE_MODE` 자동 선택:
  1개=unicast(0, 기존과 와이어 바이트 동일), 2개 이상=one-to-many(1) + `NUMBER_OF_CONTROLEES=N`
  + `DST_MAC_ADDRESS` 리스트 인코딩.
- 검증 실패(형식/중복/초과)는 ValueError → 기존 값 유지 + 로그.

### 5) 테스트 (56개 통과)
- `tests/test_uci_device.py`: multicast TLV 와이어 검증(`test_start_ranging_multicast_sets_one_to_many`),
  `parse_dest_macs` 정상/오류 케이스, 쉼표 목록 set_dest_mac 등 7개 추가.
- 기존 unicast TLV 바이트 테스트가 그대로 통과 → 폰 인터롭 무변경 보장.

## 검증 결과
- `py -m pytest -q` → **56 passed** (2026-07-11).
- **실기기 테스트 성공**: 폰 주소 수동 입력(1차 방식, OOB 없음) → UCI ranging → 타겟 행/거리 표시 확인.

## TODO (다음 작업, 우선순위 제안)

1. **OOB 2차 설계·구현** — 주소 수동 입력을 자동화.
   - 아키텍처 초안: PC 콘솔 = BLE central(Python `bleak`) ↔ 폰 앱(uwb_controlee_app) =
     BLE GATT peripheral로 UWB 주소/세션 파라미터 제공 → 콘솔이 자동으로 주소 채우고 세션 시작.
   - 세션 타임라인(BLE_ADV→BLE_CONN→OOB_DONE)이 이때 실제 상태와 연동됨.
   - **선행 확인**: 폰 앱 수정 가능 여부, GATT 서비스/특성 스키마 합의 (양쪽 프로젝트 작업).
2. **multicast 실측 검증** — 폰 2대로 one-to-many 동시 레인징 확인.
   폰 쪽도 multicast 프로파일(CONFIG_MULTICAST_DS_TWR 계열)이어야 함. 측정 누락 시
   `SLOTS_PER_RR=6` 튜닝 검토 (현재는 폰 인터롭 유지를 위해 고정).
3. **진단 도구 강화** — 거리 시계열 그래프, CSV 내보내기, 타겟별 통계(평균/표준편차/드롭률).
4. **CLI JSON 경로 target_id** — `_parse_json_line()`에서 `Addr` 필드를 target_id로 채우기 (현재 None).
5. **잔여물 정리 여부 결정** — 미사용 TABLE_*/TARGET_STATUS_*/COL_* 상수와 `split_dest_macs()`
   (OOB에서 재사용 or 제거).

## 참고
- 이전 기록: [작업기록_2026-07-11_주소별거리표시.md](작업기록_2026-07-11_주소별거리표시.md)
- 작업 시작 전 `git status`/`git diff`로 실제 상태를 재확인할 것.
