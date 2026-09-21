# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Relay-Kit contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""ComfyUI-H3-Relay-Kit

MiniMax-H3 多段续接的 **latent 桥**（零重编码）。

取作者链路的续接方式：上一段的 AV latent 直接切尾段，
以 minimax_keyframes（按位置排位）+ minimax_refs（音频）注入本段 conditioning，
不经过 mp4 解码与 VAE 重编码。

本包不依赖任何第三方 H3 节点包：所用协议（minimax_keyframes / minimax_refs /
resolved_frame_index）由 ComfyUI 原生消费。

节点：
    🔗 H3 续接 Latent 存    H3RelayLatentSave     本段 latent 落盘（下一段的接力棒）
    🔗 H3 续接 Latent 读    H3RelayLatentLoad     手动连线时读上一段（桥自动取源时不用）
    🔗 H3 续接 拷贝桥       H3RelayCopyBridge     上一段尾段逐位拷进本段 latent + 噪声掩码（钉住区不重绘）
                                                  **0.6.0 起 = 复合桥**：接上 conditioning 时同时追加钉帧（管取景），
                                                  与 latent 钉住窗（管运动）并联；不接则行为与旧版完全一致
    🔗 H3 续接裁重叠        H3RelayTrimAV         裁掉钉住区重播帧（音画同裁+接缝自检），并交出 prev_tail
    🔗 H3 续接后处理 Post   H3RelayPost           画质域后处理（跨段统计匹配/低频残差/直方图+白平衡/反卷积/高频迁移/糊区锐化/自适应糊区补偿；互斥组 + 逐层可审计）
    🔗 H3 续接音频缝        H3RelayAudioSeam      音频域：上一段环境声补本段头（床声电平对齐，长度守恒，零 A/V 位移）+ joined 整片拼接（J-cut 时间轴守恒）
    🔗 H3 续接连跑 Chain    H3RelayChain          UI 自动连跑（段号自动推进 + 自动排队）

手把手（UI 三步跑一条链）：
    1. 桥和落盘的 stage_index 填 0，点 Chain 的 ▶ Run —— 第 1 段落盘
    2. 点 ⏩ 连跑（或每段点 ✔ Approve）—— 段号自动 +1，自动续接
    3. 每段之间改 prompt/seed；换新片子换 run_id
"""

from .nodes import (
    NODE_CLASS_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS,
)

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

__version__ = "0.6.3"
