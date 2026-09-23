# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Relay-Kit contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""V3 外壳子包（``io.ComfyNode`` + ``comfy_entrypoint``）。

⚠️ 只在 ``H3RELAY_NODE_API=v3`` 时被上层 ``__init__.py`` 使用；**默认仍走 V1**
（``NODE_CLASS_MAPPINGS``），行为与 0.6.8 逐位一致。切不切默认由使用者决定。

为什么要有这一层（而不是把 V3 定义直接改进 ``nodes.py``）：
  宿主加载器是 ``if V1 … return True / elif comfy_entrypoint``（``ComfyUI/nodes.py:2295-2337``）
  ⇒ 同一模块**双注册不可行**（V1 分支命中即 return），且"建第二个包并行"会因同 ``node_id``
  静默覆盖 ⇒ 唯一可行的灰度手段就是这里：两条出口在 ``__init__.py`` 里**互斥切换**。

本层只做**协议转换**：``define_schema`` 抄 V1 的 ``INPUT_TYPES``，``execute`` 转调
``nodes.py`` 的 V1 实现。``relay_core.py`` 的算法与那 384 项断言**完全不被动到**。

节点清单（8 个，``node_id`` 与 V1 **完全同名** ⇒ 用户已存的工作流零改动可用）：
    H3RelayLatentSave / H3RelayLatentLoad / H3RelayLatentUpscale / H3RelayTrimAV /
    H3RelayCopyBridge / H3RelayPost / H3RelayAudioSeam / H3RelayChain
"""

V3_NODE_IDS = [
    "H3RelayLatentUpscale",
    "H3RelayLatentSave",
    "H3RelayLatentLoad",
    "H3RelayTrimAV",
    "H3RelayCopyBridge",
    "H3RelayPost",
    "H3RelayAudioSeam",
    "H3RelayChain",
]
