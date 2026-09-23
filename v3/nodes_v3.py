# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Relay-Kit contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""8 个节点的 V3 外壳。

每个外壳只有两件事：``define_schema``（**从 V1 类自动生成**）与 ``execute``（转调 V1 实现）。
业务逻辑一行不重写 ⇒ V3 迁移的风险全部集中在"协议转换"这一薄层，算法层零改动。

⚠️ 维护纪律：**不要**在这里手写 input/output 定义。要加改参数请改 ``nodes.py`` 的
``INPUT_TYPES`` / ``RETURN_TYPES``，本层自动跟随。手写会造出「V1/V3 两套参数表」——
那正是老工作流参数静默错位的来源（``widgets_values`` 按位置存）。
"""
from __future__ import annotations

from . import _compat as C
from ._compat import V1, io


class _Shell(io.ComfyNode):
    """外壳基类：schema 由 V1 类生成，execute 转调 V1 的 ``FUNCTION``。

    官方允许多层继承（只要链顶是 ``ComfyNode``）。本类**不注册**，只作 8 个节点的共同骨架。
    """

    _V1_NAME = ""

    @classmethod
    def define_schema(cls) -> io.Schema:
        return C.schema_from_v1(getattr(V1, cls._V1_NAME))

    @classmethod
    def execute(cls, **kwargs) -> io.NodeOutput:
        # 宿主按 schema 的参数名以 kwargs 传参 ⇒ 用 **kwargs 全接住，不挑参数名
        # （也就不会因为将来加参数而漏接）。
        return C.call_v1(cls._V1_NAME, kwargs)


class H3RelayLatentUpscale(_Shell):
    """🔍 H3 潜空间分块放大：H3 AV 打包 latent 的适配层（零去噪、时间维不动）。"""

    _V1_NAME = "H3RelayLatentUpscale"


class H3RelayLatentSave(_Shell):
    """🔗 H3 续接 Latent 存：本段 latent 落盘，它是下一段的「接力棒」。"""

    _V1_NAME = "H3RelayLatentSave"


class H3RelayLatentLoad(_Shell):
    """🔗 H3 续接 Latent 读：手动连线时读上一段（桥自动取源时不必用）。"""

    _V1_NAME = "H3RelayLatentLoad"


class H3RelayTrimAV(_Shell):
    """🔗 H3 续接裁重叠：裁掉钉住区重播帧（音画同裁 + 接缝自检），并交出 prev_tail。"""

    _V1_NAME = "H3RelayTrimAV"


class H3RelayCopyBridge(_Shell):
    """🔗 H3 续接 拷贝桥：上一段尾段逐位拷进本段 latent + 噪声掩码（钉住区不重绘）。"""

    _V1_NAME = "H3RelayCopyBridge"


class H3RelayPost(_Shell):
    """🔗 H3 续接后处理 Post：画质域（跨段统计匹配 / 低频残差 / 直方图 / 锐化 …）。"""

    _V1_NAME = "H3RelayPost"


class H3RelayAudioSeam(_Shell):
    """🔗 H3 续接音频缝：上一段环境声补本段头 + joined 整片拼接（J-cut 时间轴守恒）。"""

    _V1_NAME = "H3RelayAudioSeam"


class H3RelayChain(_Shell):
    """🔗 H3 续接连跑 Chain：UI 自动连跑（段号自动推进 + 词分发 + 自动拼接成片）。"""

    _V1_NAME = "H3RelayChain"


# 注册顺序与 V1 的 NODE_CLASS_MAPPINGS 一致（非必需，便于人工双向核对）
NODES = [
    H3RelayLatentUpscale,
    H3RelayLatentSave,
    H3RelayLatentLoad,
    H3RelayTrimAV,
    H3RelayCopyBridge,
    H3RelayPost,
    H3RelayAudioSeam,
    H3RelayChain,
]
