# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""relay_core._grid —— 网格常量与网格工具（H3 VAE 的 5+17k 时序网格、窗口吸附、常量导出键）。

本模块由 `relay_core.py` 拆分而来（2026-10-07）；代码**逐字搬运**，未改任何算式。
对外仍由 `relay_core/__init__.py` 全量再导出 ⇒ 旧调用点（`relay_core.xxx`）不变。
"""

from __future__ import annotations

from typing import List, Optional, Tuple
import itertools

# ---------------------------------------------------------------- 网格常量
# 与 H3 VAE 的时序跨度一致。改这里等于改 H3 的 VAE，必须同步。
FRAME_PER_TOKEN: Tuple[int, ...] = (1, 4, 4, 4, 4)
FPS: float = 24.0
FRAME_RESCALE: float = 5.0 / 3.0   # 像素帧 → 音频 latent 步的换算
AUDIO_HZ: float = 40.0             # 音频 latent 的采样率（步/秒）

# 合法窗口，降序。必须与 FRAME_PER_TOKEN 自洽（见 self_check）。
GUIDE_RUNS: Tuple[int, ...] = (124, 107, 90, 73, 56, 39, 22, 5, 1)

# Apt_Preset 在 latent 里留下的导出尾段键（有则优先复用，避免二次切片）
KEY_EXPORT_TAIL_VIDEO = "apt_h3_export_tail_latent"
KEY_EXPORT_TAIL_AUDIO = "apt_h3_export_tail_audio_latent"
KEY_EXPORT_FRAMES = "apt_h3_export_context_frames"

# ---------------------------------------------------------------- 网格工具
def pixel_frames(latent_t: int) -> int:
    """latent token 数 → 覆盖的像素帧数。"""
    return sum(FRAME_PER_TOKEN[k % 5] for k in range(int(latent_t)))


def step_offsets(latent_t: int) -> List[int]:
    """每个 latent token 的**起始像素帧位置**（首位恒为 0）。

    与 ``pixel_frames`` 是同一张表的两种读法：``pixel_frames(n)`` 给总量，
    本函数给每个 token 的入口。写成**排他前缀和** —— 第 i 个位置 = 它前面
    所有 token 的跨度之和。``cycle`` 让周期由 ``FRAME_PER_TOKEN`` 自己决定。
    """
    n = max(int(latent_t), 0)
    spans = itertools.islice(itertools.cycle(FRAME_PER_TOKEN), n)
    return [0, *itertools.accumulate(spans)][:n]


def steps_for_frames(n: int) -> Optional[int]:
    """像素帧数 → 整步的 latent token 数；不在网格上返回 None。

    跨度按 ``1,4,4,4,4`` 循环，所以**可达的帧数就是这些跨度的前缀和**：
    5 → 2 步、22 → 7 步、39 → 12 步、56 → 17 步。落在两格之间的值
    （4、6、7…）无解。0 帧视作 0 步。

    ⚠️ 这里**不硬编码周期**（虽然 5 步和为 17，可写成闭式解、再快一个数量级）：
    闭式解在上游改 ``FRAME_PER_TOKEN`` 时会**静默算错**，而 ``layout_contract``
    只对照常量、查不出公式里的硬编码。``cycle`` 跟着常量走，网格变了自动跟着变。
    """
    n = int(n)
    for steps, total in enumerate(
            itertools.accumulate(itertools.islice(
                itertools.cycle(FRAME_PER_TOKEN), max(n, 0))),
            start=1):
        if total >= n:
            return steps if total == n else None
    return 0 if n == 0 else None


def snap_guide_run(n: int) -> int:
    """向下吸附到最近的合法窗口（供 UI 侧提示用；核心路径仍会硬校验）。"""
    n = int(n)
    for g in GUIDE_RUNS:
        if g <= n:
            return g
    return 0


def self_check() -> List[str]:
    """自洽性检查：GUIDE_RUNS 必须全部落在网格上。返回问题列表（空=通过）。"""
    bad = []
    for g in GUIDE_RUNS:
        if steps_for_frames(g) is None:
            bad.append(f"GUIDE_RUNS 含非网格值 {g}（pixel_frames 无法整步覆盖）")
    return bad
