# SPDX-FileCopyrightText: Copyright (c) 2024 Qorvo US, Inc.
# SPDX-License-Identifier: LicenseRef-QORVO-2
#
# Vendored from: https://github.com/sasodoma/uwb-ranging @ commit aad72a0
#   (path: new_python_script/uci/custom_params.py) — Qorvo DW3_QM33_SDK UCI 라이브러리 사본, 무수정.

"""
This library is handling custom parameters for QM33SDK customization option.
"""

from . import fira
from .fira import *
from .utils import DynIntEnum

__all__ = ["config_params"]


class Config(DynIntEnum):
    pass


fira.config_params.pop(fira.Config.LowPowerMode)
fira.config_params.pop(fira.Config.Traces)
fira.config_params.pop(fira.Config.PmMinInactivityS4)
