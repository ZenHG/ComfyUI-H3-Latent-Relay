# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""V3 外壳的公共助手 —— 本包**唯一** import ``comfy_api`` 的地方。

## 为什么 V3 层是「自动转换」而不是手抄 schema

V3 要求 input 的 **id 名与顺序、默认值、min/max/step、组合项全序** 与 V1 逐字段相同 ——
因为老工作流的 ``widgets_values`` 是**按位置**存进 JSON 的（``nodes.py`` 的 INPUT_TYPES 里
也留了同样的告诫：新 widget 必须追加在末位）。**插错一个位置，用户已有图的参数就会静默错位**：
不报错、不提示，只是数值跑到别的旋钮上。

本包 8 个节点共 **100+ 个参数**，每个还带多行中文 tooltip。手抄 = 一次搬运上百个字段，
错一个是静默的。所以这里**从 V1 的 ``INPUT_TYPES()`` 直接转换**：数据源唯一
⇒ 结构上不可能与 V1 错位（由 ``tests/test_v3_schema.py`` 机检兜住）。

代价（如实说明）：``define_schema`` 不再是字面量，读起来不如官方示例直观。
取舍是**正确性 > 字面可读性**；附带收益是以后新增参数会自动跟随，不会漏。

## 为什么 ``comfy_api`` 只在这里 import

官方文档：``comfy_api.latest`` 指向**仍在开发中**的版本，``v0_0_2`` 会「无预警变更」，
而「latest 之前的版本才算稳定」。上游一漂，只改这一行即可换 pin，不必翻 8 个文件。
"""
from __future__ import annotations

from comfy_api.latest import ComfyExtension, io, ui      # noqa: F401

from .. import nodes as V1                               # 业务逻辑与 schema 的唯一真相源

__all__ = ["ComfyExtension", "io", "ui", "V1", "node_output", "call_v1", "schema_from_v1"]

# V1 类型字符串 → V3 类型类（本包实际用到的全集：INT/FLOAT/STRING/BOOLEAN/IMAGE/LATENT/AUDIO/CONDITIONING）
_TYPES = {
    "INT": io.Int,
    "FLOAT": io.Float,
    "STRING": io.String,
    "BOOLEAN": io.Boolean,
    "IMAGE": io.Image,
    "LATENT": io.Latent,
    "AUDIO": io.Audio,
    "MASK": io.Mask,
    "CONDITIONING": io.Conditioning,
}

# V1 选项键 → V3 构造参数名（值语义相同）。
# 本包实测未用 display / forceInput / dynamicPrompts（已 grep 确认 0 处），
# 但映射表留全：将来加参数时不会因为缺映射而静默丢选项。
_OPTS = {
    "default": "default",
    "min": "min",
    "max": "max",
    "step": "step",
    "tooltip": "tooltip",
    "multiline": "multiline",
    "placeholder": "placeholder",
    "forceInput": "force_input",
    "socketless": "socketless",
    "advanced": "advanced",
    "lazy": "lazy",
    "rawLink": "raw_link",
    "dynamicPrompts": "dynamic_prompts",
    "label_on": "label_on",
    "label_off": "label_off",
}


def _kwargs_of(opts):
    return {_OPTS[k]: v for k, v in (opts or {}).items() if k in _OPTS}


def _one_input(name, spec, optional):
    if isinstance(spec, tuple):
        t, opts = spec[0], (spec[1] if len(spec) > 1 else {})
    else:
        t, opts = spec, {}
    kw = _kwargs_of(opts)
    if optional:
        kw["optional"] = True
    if isinstance(t, list):                       # V1 的组合列表 → Combo（全序保留）
        return io.Combo.Input(name, options=list(t), **kw)
    cls = _TYPES.get(str(t).upper())
    if cls is None:                               # 未知类型兜底（宁可自定义，也不静默丢）
        return io.Custom(str(t)).Input(name, **kw)
    return cls.Input(name, **kw)


def v1_inputs(input_types):
    """``INPUT_TYPES()`` 的 dict → V3 ``inputs`` 列表。

    **保序**：required 全部在前、optional 随后 —— 与 V1 的 dict 遍历顺序一致
    （``widgets_values`` 按位置存，顺序错 = 参数静默错位）。
    """
    out = []
    for section, optional in (("required", False), ("optional", True)):
        for name, spec in (input_types.get(section) or {}).items():
            out.append(_one_input(name, spec, optional))
    return out


def v1_outputs(return_types, return_names):
    """``RETURN_TYPES`` / ``RETURN_NAMES`` → V3 ``outputs``（顺序与显示名逐位对应）。"""
    names = list(return_names or ())
    out = []
    for i, t in enumerate(return_types or ()):
        label = names[i] if i < len(names) else None
        cls = _TYPES.get(str(t).upper())
        out.append(io.Custom(str(t)).Output(display_name=label) if cls is None
                   else cls.Output(display_name=label))
    return out


def schema_from_v1(v1_cls) -> "io.Schema":
    """V1 节点类 → V3 ``io.Schema``。**唯一数据源 = V1 类本身**。

    ``node_id`` 就用 V1 类名（**不改名**）⇒ 用户已存的工作流零改动可用。
    官方建议 V3 新节点加前缀，但改名要走 ``io.NodeReplace``，而它的 ``old_widget_ids``
    是**按位置索引**映射旧参数的 —— 本包节点动辄 20+ 个 widget，一旦错位就是用户参数静默错乱，
    比"不能加载"更糟。所以本轮选**保持同名**。
    """
    name = v1_cls.__name__
    return io.Schema(
        node_id=name,
        display_name=V1.NODE_DISPLAY_NAME_MAPPINGS.get(name, name),
        category=getattr(v1_cls, "CATEGORY", V1.CATEGORY),
        description=getattr(v1_cls, "DESCRIPTION", "") or "",
        # V1 的 OUTPUT_NODE=True（LatentSave / AudioSeam）→ V3 is_output_node
        is_output_node=bool(getattr(v1_cls, "OUTPUT_NODE", False)),
        inputs=v1_inputs(v1_cls.INPUT_TYPES()),
        outputs=v1_outputs(getattr(v1_cls, "RETURN_TYPES", ()),
                           getattr(v1_cls, "RETURN_NAMES", ())),
    )


def node_output(raw):
    """V1 返回值 → ``io.NodeOutput``。

    * 裸 tuple                            → ``io.NodeOutput(*raw)``
    * ``{"ui": {K: [...]}, "result": r}`` → ``io.NodeOutput(*r, ui={K: [...]})``
    * ``None`` / ``{}``（无输出节点）      → ``io.NodeOutput()``

    ``ui`` 用**原始 UI 字典**（官方允许：``io.NodeOutput(ui={"images": results})``）。
    ⚠️ 不许改用 ``ui.PreviewAudio`` 之类的助手 —— 那会把自定义键 ``h3relay_pcm`` 变成宿主标准键，
    **直接切断拼接路由**（路由靠 ``history.outputs[node_id]["h3relay_pcm"]`` 取 PCM 边车路径）。

    形状等价性已由零 GPU 探针实测：宿主 ``execution.py`` 的 ``get_output_from_returns()`` 里，
    V1 的 ``uis.append(r['ui'])`` 与 V3 的 ``uis.append(r.ui)``（dict 时）**汇入同一个列表**。
    """
    if raw is None or raw == {}:
        return io.NodeOutput()
    if isinstance(raw, dict) and "ui" in raw:
        res = raw.get("result")
        res = res if isinstance(res, tuple) else (() if res is None else (res,))
        return io.NodeOutput(*res, ui=raw["ui"])
    res = raw if isinstance(raw, tuple) else (raw,)
    return io.NodeOutput(*res)


def call_v1(class_name, kwargs):
    """按 V1 类的 ``FUNCTION`` 名调它的实现，返回值转 ``io.NodeOutput``。

    V3 外壳**只做协议转换**，业务逻辑一行不重写 ⇒ ``nodes.py`` / ``relay_core.py``
    的行为与那 384 项断言完全不被动到（这也是本轮迁移的全部风险来源）。
    """
    cls = getattr(V1, class_name)
    return node_output(getattr(cls(), cls.FUNCTION)(**kwargs))
