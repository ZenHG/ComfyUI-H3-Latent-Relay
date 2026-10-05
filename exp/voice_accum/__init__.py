# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""VA（Voice Accumulate）· 跨段「按人累积」音色参考（实验层，默认关）。

- 算法与自检：`voice_accum.py`（零本包依赖，`python voice_accum.py` 可独立跑自检）
- H3 适配层：`h3_adapter.py`（读 `_va.json`、找历史段、包 `minimax_refs` 块）
"""

from . import voice_accum  # noqa: F401
