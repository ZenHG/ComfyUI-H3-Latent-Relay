# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Relay-Kit contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""V3 扩展入口：``ComfyExtension`` + ``comfy_entrypoint()``。

宿主用 ``hasattr(module, "comfy_entrypoint")`` 发现 V3 扩展（``ComfyUI/nodes.py`` 的
``load_custom_node``）。⚠️ 与本包的 V1 ``NODE_CLASS_MAPPINGS`` **互斥**：宿主是
``if V1 … return True / elif comfy_entrypoint``，两条出口同时存在时 **V1 会吃掉 V3**、
V3 永不生效 ⇒ 切换只在上层 ``__init__.py`` 用环境变量做，一次只导出一条。
"""
from __future__ import annotations

from ._compat import ComfyExtension, io
from .nodes_v3 import NODES


class H3RelayExtension(ComfyExtension):
    """把 8 个 V3 外壳交给宿主。

    ⚠️ 为什么逐个 try（V1 没有这个问题）：宿主对 V3 是**加载时**就调 ``GET_SCHEMA()``
    （``ComfyUI/nodes.py`` 的 ``load_custom_node`` 在 ``get_node_list()`` 之后逐个注册），
    而 V1 的 ``INPUT_TYPES()`` 是**惰性**的（按需才调）。⇒ V3 下某个节点的 schema 构造
    一旦抛错，**整个扩展注册失败、8 个节点全没**；V1 下最多是那个节点用时报错。
    ``H3RelayLatentUpscale`` 的 ``INPUT_TYPES()`` 会去宿主节点注册表找上游包
    （作者的 ``MinimaxH3LatentUpscaler3D``），属可预期的失败点 ⇒ 必须隔离成**只丢这一个**，
    与本包「未装上游包时本包照常加载」的既有承诺一致。
    """

    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        """必须声明为 async（官方硬要求：``get_node_list`` 必须异步）。"""
        ok = []
        for cls in NODES:
            try:
                cls.GET_SCHEMA()                   # 走官方缓存；构造失败即在此暴露
            except Exception as exc:               # noqa: BLE001
                print("[H3 Relay] V3 节点 %s 的 schema 构造失败 ⇒ 已跳过它，其余节点照常加载。\n"
                      "            原因：%s: %s" % (cls.__name__, type(exc).__name__, exc))
                continue
            ok.append(cls)
        return ok


async def comfy_entrypoint() -> H3RelayExtension:
    """``comfy_entrypoint`` 可同步可异步；这里用 async 与官方示例一致。"""
    return H3RelayExtension()
