# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""ci_env_repro.py — 在**本机复现 CI 环境**跑任意校验脚本。

【为什么必须有它】
  本仓有判据要覆盖**两条代码路径**，而不是两个数字：

  | 路径 | 怎么造 | 节点数 / input |
  |---|---|---|
  | 宿主注册表**拿得到**（本机装了 ComfyUI） | 直接跑 | 8 / 122 |
  | 宿主注册表**拿不到** | `sys.modules["nodes"] = None` | 8 / 122 |

  🔴 2026-10-07 起两行的数字**相同**了，所以本工具的用途从"证明两个数各自在声明里"
  变成"**把另一条路径也真跑一遍**" —— 数字相同 ≠ 路径相同，而路径才是会各自腐烂的东西。
  ⚠️ 2026-10-07 之前这里写的是「CI = **7** 节点 / **104** input」，**那是错的**：
  那一档只在人工硬挡下出现，CI 真正进入的是"RuntimeError 被吞 ⇒ 8 节点"。详见
  `docs/08-testing.md` 的「V3 外壳」一节与 `nodes.py::_comfy_registry` 的注释。

  ⇒ 只在本机跑一遍，只能证明**那一条路径**没问题。实测 2026-09-29：`review_050` 的 H3h
  就是干这个的，而本地套件只跑了本机那一半 —— 本地全绿，**CI 当场红**（那条红就出在
  上面这个"7 节点"的错误前提上）。⇒ 凡「声明要覆盖两种环境」的检查，**仍然要两边都跑**。

【怎么造出"没有宿主注册表"】
  CI 的条件是「宿主 `nodes` 模块**根本导不进来**」。本机装了宿主，所以直接跑永远是另一种。
  唯一的姿势（2026-09-27 定案）：
      sys.modules["nodes"] = None
  ⇒ 让 `import nodes` 抛 `ImportError` —— 这就是 `_comfy_registry()` 的**失败分支**
  （2026-10-07 起它会被收敛成 RuntimeError 并被 `_upscaler_module()` 吞掉 ⇒ 节点仍在场）。
  ⚠️ 别用"只把上游节点从注册表里摘掉"来模拟 —— 那复现的是「装了宿主、没装上游包」，
  那种情况**仍然是 8 节点**，与"没有注册表"不是一回事。

【用法】
    python tools/ci_env_repro.py tools/review_050.py
    python tools/ci_env_repro.py tests/test_v3_schema.py     # 现在也是 8 节点 / 72 项（2026-10-07 起两条路径同数）
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
