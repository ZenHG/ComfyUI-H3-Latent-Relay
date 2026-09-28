# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""E1' 时不变历史锚（TIHA）—— 见同目录 ``history_anchor.py`` 的模块头（含完整理论依据）。

对外入口只有一个：``h3_adapter.build_refs()``（由 ``nodes.py`` 调用）。
本 ``__init__`` **刻意不 import 任何子模块** —— 保持 `import` 廉价、无副作用。
"""

__all__ = ["history_anchor", "h3_adapter", "build_refs"]


def __getattr__(name):
    """懒加载：``from .exp.history_anchor_v2 import build_refs`` 也能用。"""
    if name in ("history_anchor", "h3_adapter"):
        import importlib
        return importlib.import_module("." + name, __name__)
    if name == "build_refs":
        from .h3_adapter import build_refs
        return build_refs
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
