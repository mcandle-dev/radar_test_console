# SPDX-FileCopyrightText: Copyright (c) 2024 Qorvo US, Inc.
# SPDX-License-Identifier: LicenseRef-QORVO-2
#
# Vendored from: https://github.com/sasodoma/uwb-ranging @ commit aad72a0
#   (path: new_python_script/uci/fira_conf.py) — Qorvo DW3_QM33_SDK UCI 라이브러리 사본, 무수정.

__all__ = ["Config", "config_params"]

from .utils import DynIntEnum, Int8
from .fira_enums import DeviceState


class Config(DynIntEnum):
    State = 0x0
    LowPowerMode = 0x1


Config.defs = [
    (Config.State, 1),
    (Config.LowPowerMode, 1),
]

config_params = {
    # Enum Value,               parameter type, Read Only, description)
    Config.State: (DeviceState, 1, "get Device State."),
    Config.LowPowerMode: (Int8, 0, "get/set low power mode. 0: disable / 1: enable."),
}
