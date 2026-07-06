# uci/ — Vendored Qorvo UCI 라이브러리

- 출처: https://github.com/sasodoma/uwb-ranging @ commit `aad72a0` (2025-05-28)
  - 경로: `new_python_script/uci/`
  - 원출처: Qorvo DW3_QM33_SDK 1.0.2 (sasodoma 리포에 동봉된 사본)
- 라이선스: 각 파일 상단 SPDX 헤더 참조 (`LicenseRef-QORVO-2`, `custom.py`만 `LicenseRef-QORVO-1`)
- 수정 사항: **없음.** 각 파일 상단에 출처 주석 4줄만 추가했다.
- 용도: `UciSerialDevice`(radar_device.py)가 DWM3001CDK UCI 펌웨어와 통신할 때 사용하는
  UCI 프레이밍·명령·NTF 파싱 계층.
- 이 디렉터리는 vendored 서드파티 코드이므로 ruff/black/mypy 검사 대상에서 제외한다
  (pyproject.toml 참조).

주의: 반드시 sasodoma 리포의 사본을 기준으로 갱신할 것 — 원본 Qorvo SDK 스크립트와
기본값이 다르다 (Android `CONFIG_UNICAST_DS_TWR` 프로필에 맞게 수정돼 있음).
