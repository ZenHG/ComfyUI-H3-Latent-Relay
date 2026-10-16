# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""10 个节点的 V3 外壳。

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
    """🔍 H3 Relay · Latent Upscale：H3 AV 打包 latent 的适配层（零去噪、时间维不动）。"""

    _V1_NAME = "H3RelayLatentUpscale"


class H3RelayLatentSave(_Shell):
    """🔗 H3 Relay · Latent Save：本段 latent 落盘，它是下一段的「接力棒」。"""

    _V1_NAME = "H3RelayLatentSave"


class H3RelayLatentLoad(_Shell):
    """🔗 H3 Relay · Latent Load：手动连线时读上一段（桥自动取源时不必用）。"""

    _V1_NAME = "H3RelayLatentLoad"


class H3RelayTrimAV(_Shell):
    """🔗 H3 Relay · Trim AV：裁掉钉住区重播帧（音画同裁 + 接缝自检），并交出 prev_tail。"""

    _V1_NAME = "H3RelayTrimAV"


class H3RelayCopyBridge(_Shell):
    """🔗 H3 Relay · Copy Bridge：上一段尾段逐位拷进本段 latent + 噪声掩码（钉住区不重绘）。"""

    _V1_NAME = "H3RelayCopyBridge"


class H3RelayPost(_Shell):
    """🔗 H3 Relay · Post：画质域（跨段统计匹配 / 低频残差 / 直方图 / 锐化 …）。"""

    _V1_NAME = "H3RelayPost"


class H3RelayAudioSeam(_Shell):
    """🔗 H3 Relay · Audio Seam：上一段环境声补本段头 + joined 整片拼接（J-cut 时间轴守恒）。"""

    _V1_NAME = "H3RelayAudioSeam"


class H3RelayChain(_Shell):
    """🔗 H3 Relay · Chain：UI 自动连跑（段号自动推进 + 词分发 + 自动拼接成片）。"""

    _V1_NAME = "H3RelayChain"


class H3RelayJoinAV(_Shell):
    """🧩 H3 Relay · Join AV：把本段裁后画面/音频接到累计后面（张量域拼条）。"""

    _V1_NAME = "H3RelayJoinAV"


class H3RelayFiniteRun(_Shell):
    """⏩ H3 Relay · Finite Run：一次提交跑完 N 段（服务端展开，无头可用）。

    ⚠️ 与另外 9 个外壳的差别：
      · **V3_EXPAND=True** ⇒ ``define_schema`` 自动带上 ``enable_expand=True``；
      · **多一个** ``validate_inputs``（V3 宿主找的是小写名）—— 少了它，默认出口下
        入队前校验就**静默失效**，错误会拖到执行期（那时上游加载器已经跑过）。
    """

    _V1_NAME = "H3RelayFiniteRun"

    @classmethod
    def validate_inputs(cls, prompt=None, prompts=None, segments=None, run_id=None):
        """转调 V1 的 ``VALIDATE_INPUTS``（唯一真相源）。

        ⚠️ 参数表必须与 ``nodes.py`` 那个**同名同集**：宿主用 ``getfullargspec`` 读名字，
        被声明的入参会跳过宿主自带的 min/max/枚举检查 ⇒ 多写一个名字 = 放松那一项。
        （机检：``tests/test_v3_schema.py`` 逐字比对两套签名。）
        """
        return C.call_v1_validate(cls._V1_NAME, {
            "prompt": prompt, "prompts": prompts,
            "segments": segments, "run_id": run_id})


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
    H3RelayJoinAV,
    H3RelayFiniteRun,
]
