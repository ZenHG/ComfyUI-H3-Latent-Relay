# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
#
# ruff: noqa: F821  —— 本分片**不是独立模块**（与 `tests/cases/` 其余分片同款）：
#   由 `tests/test_relay_core.py` 用 `exec(compile(src, path, "exec"), globals())`
#   顺序执行 ⇒ `check` / `CORE` / `os` / `json` 都看得见，但不是本文件定义的。

"""组 35 · 响度归一（0.6.32 新增）· 与容器时间戳偏移的回归锁。

⚠️ 本片**不是**从原单文件切出来的：它是 2026-10-08 新写的（见 `tmp/do_split_tests.py` 的
   PARTS —— 拼回自证**不含**它，与 `_g33` / `_g34` 同性质）。

判据三件，都对着**真实踩过的坑**：
  35.1 口径与 `tools/voice_bank.py::speech_rms_db` **逐位一致**。为什么必须有这条：
       声锚走 `voice_bank`，本段走 `relay_core` —— 两处口径一旦漂，有锚段与无锚段就落到
       **不同目标**，段间台阶只是从一种换成另一种（本仓"两份实现会漂"的老病）。
  35.2 归一：偏离目标的音频被拉回目标（±0.5 dB 容忍带内视为"不动"）。
  35.3 **幂等**：连跑 2 / 3 次都报"不动" —— 这是**与声锚叠加不双重处理**的必要条件。
       旧写法按目标增益算完、超峰值护栏再乘一次 ⇒ 结果偏离目标（实测 −23.61 而非 −23.00）
       ⇒ 下一次又算出 +0.61 dB 再被压回 ⇒ **来回动**。
"""
import os
import sys

import numpy as np
import torch

# ⚠️ case 文件是 `exec(..., globals())` 进来的 ⇒ `__file__` 是 `tests/test_relay_core.py`
#   （**不是**本文件）⇒ **两次** dirname 就到仓库根。写成三次会静默指到仓库的**父目录**
#   ⇒ import 失败 ⇒ 35.1 退化成"自比"（**假绿**）。所以下面那条警告不是装饰。
_g35_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_g35_tools = os.path.join(_g35_root, "tools")
if _g35_tools not in sys.path:
    sys.path.insert(0, _g35_tools)
try:
    from voice_bank import speech_rms_db as _g35_vb_speech
except Exception as _g35_e:                     # 工具被移走 ⇒ 只跳过"口径一致"这一条
    _g35_vb_speech = None
    print("  ⚠ 组 35：读不到 tools/voice_bank.py（%r）⇒ 35.1 降级为自比" % (_g35_e,))

_g35_rng = np.random.default_rng(35)
_g35_cases = {
    "白噪": _g35_rng.standard_normal(32000 * 8).astype(np.float32) * 0.1,
    "静音+有声": np.concatenate([np.zeros(32000 * 3, np.float32),
                             _g35_rng.standard_normal(32000 * 5).astype(np.float32) * 0.3]),
    "常数": np.full(32000 * 8, 0.1, np.float32),
}
_g35_ok, _g35_diffs = True, []
for _g35_tag, _g35_x in _g35_cases.items():
    _g35_a = CORE.speech_rms_db(_g35_x, 32000)
    _g35_b = _g35_vb_speech(_g35_x, 32000) if _g35_vb_speech else _g35_a
    _g35_d = 0.0 if (_g35_a[0] is None or _g35_b[0] is None) else abs(_g35_a[0] - _g35_b[0])
    _g35_diffs.append("%s %.2e" % (_g35_tag, _g35_d))
    _g35_ok = _g35_ok and _g35_d < 1e-6 and abs(_g35_a[1] - _g35_b[1]) < 1e-9
check("35.1 `relay_core.speech_rms_db` 与 `tools/voice_bank.py` 口径**逐位一致**"
      "（有锚段 / 无锚段必须落到同一目标，否则只是把台阶换成另一种）",
      _g35_ok, "逐例差：" + "、".join(_g35_diffs))

_g35_au = {"waveform": torch.from_numpy(
    (_g35_rng.standard_normal(32000 * 6).astype(np.float32) * 0.06)), "sample_rate": 32000}
_g35_db0, _ = CORE.speech_rms_db(_g35_au["waveform"].numpy(), 32000)
_g35_o1, _g35_n1 = CORE.normalize_loudness(_g35_au)
_g35_db1, _ = CORE.speech_rms_db(_g35_o1["waveform"].numpy(), 32000)
check("35.2 归一把偏离目标的音频拉回目标（±0.5 dB 容忍带）",
      abs(_g35_db1 - CORE.AUDIO_LOUDNESS_TARGET_DBFS) <= CORE.AUDIO_LOUDNESS_TOL_DB + 0.05,
      "%.2f → %.2f dBFS（目标 %.1f）" % (_g35_db0, _g35_db1, CORE.AUDIO_LOUDNESS_TARGET_DBFS))

_g35_o2, _g35_n2 = CORE.normalize_loudness(_g35_o1)
_g35_o3, _g35_n3 = CORE.normalize_loudness(_g35_o2)
check("35.3 **幂等**：连跑 2 / 3 次都报「不动」（与声锚叠加不双重处理的必要条件）",
      ("不动" in _g35_n2) and ("不动" in _g35_n3),
      "2 次：%s ｜ 3 次：%s" % (_g35_n2[:26], _g35_n3[:26]))
