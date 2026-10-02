# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""ci_env_repro.py — 在**本机复现 CI 环境**跑任意校验脚本。

【为什么必须有它】
  本仓的判据分两套环境，数字**不一样**：

  | 环境 | 节点数 | 逐项 input |
  |---|---|---|
  | 本机（宿主 `nodes` 可导入） | 8 | 116 |
  | **CI**（宿主 `nodes` 导不进来 ⇒ `H3RelayLatentUpscale` 缺席） | **7** | **104** |

  ⇒ 只在本机跑一遍，**只能证明"本机的数在声明里"**，证明不了"CI 的数也在"。
  实测 2026-09-29：`review_050` 的 H3h 就是干这个的，而本地套件只跑了本机那一半 ——
  本地「✅ 7/7 全绿」，**CI 当场红**（`节点数真值 7 不在声明 [8, 9] 里`）。
  ⇒ 凡「声明要覆盖两种环境」的检查（H3h 这类），**必须两边都跑**。

【怎么复现 CI】
  CI 的条件是「宿主 `nodes` 模块**根本导不进来**」。本机装了宿主，所以直接跑永远是本机环境。
  唯一正确的姿势（2026-09-27 定案）：
      sys.modules["nodes"] = None
  ⇒ 让 `import nodes` 抛 `ImportError`，与 CI 里 `PYTHONPATH` 不含宿主的情形**等价**。
  ⚠️ 别用"只把上游节点从注册表里摘掉"来模拟 —— 那复现的是「装了宿主、没装上游包」，
  那种情况**仍然是 8 节点**，与 CI 不同。

【用法】
    python tools/ci_env_repro.py tools/review_050.py
    python tools/ci_env_repro.py tests/test_v3_schema.py     # 期望它报 7 节点（这是对的）
  退出码 = 被跑脚本的退出码。
"""
from __future__ import annotations

import os
import runpy
import sys

_KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COMFY = os.environ.get("COMFYUI_PATH") or os.environ.get("COMFYUI_ROOT") or ""


def main() -> int:
    if len(sys.argv) < 2:
        print("[ci_env_repro] 用法：python tools/ci_env_repro.py <脚本相对路径>")
        return 2
    target = sys.argv[1]
    if not os.path.isabs(target):
        target = os.path.join(_KIT, target)
    if not os.path.isfile(target):
        print("[ci_env_repro] 找不到脚本：%s" % target)
        return 2

    if _COMFY:
        os.environ["COMFYUI_PATH"] = _COMFY
        os.environ["PYTHONPATH"] = _COMFY
        if _COMFY not in sys.path:
            sys.path.insert(0, _COMFY)
    if _KIT not in sys.path:
        sys.path.insert(0, _KIT)

    # 🔴 这一行就是"CI 环境"的全部 —— 宿主 `nodes` 导不进来。
    sys.modules["nodes"] = None

    print("[ci_env_repro] CI 环境复现：sys.modules['nodes'] = None ⇒ 跑 %s"
          % os.path.relpath(target, _KIT))
    try:
        runpy.run_path(target, run_name="__main__")
    except SystemExit as e:                                   # 被跑脚本自己 exit
        return int(e.code or 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
