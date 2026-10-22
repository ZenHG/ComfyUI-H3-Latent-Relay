#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""断言「**不设环境变量时默认出口 = V3**」（零 GPU、零模型、秒级）。

## 为什么需要它

2026-09-24 把默认出口从 `v1` 切到 `v3`（`__init__.py` 的 `H3RELAY_NODE_API` 默认值）。
切完之后：

* `tests/test_relay_core.py` 的 26.15 会**离线加载本包**，但**不断言走的是哪个出口** ——
  默认重新变回 v1 它照样绿；
* `tests/test_v3_schema.py` 覆盖 V3 很充分，但它**自己强制** `H3RELAY_NODE_API=v3`
  （`os.environ[...] = "v3"`），且它没接进 CI。

⇒ 也就是说：**"默认是 V3" 这件事在 CI 里一条断言都没有**。本仓纪律「无断言即未验收」，
所以补这一条。它必须在**独立进程**里跑、且**不预设任何环境变量**（同进程里
`test_v3_schema` 已经把环境变量写死了）。

判据（五条，全过才退出码 0）：
  1. `NODE_CLASS_MAPPINGS` 是 **None** —— 宿主加载器（ComfyUI/nodes.py:2295-2337）的判据含
     `is not None`，为 None 才会落到 `elif comfy_entrypoint` 分支；
  2. `comfy_entrypoint` **存在且可调用**；
  3. `WEB_DIRECTORY` 仍在（前端 JS 靠它挂载，V3 出口下宿主照样处理）；
  4. `NODE_API_DEFAULT == "v3"`（源码里的默认值这个**事实**，不是"本次跑中的值"）；
  5. 🔴 **V3 出口实际注册的节点 id 集合 == `nodes.py` 的 `NODE_CLASS_MAPPINGS` 键集合**
     （仅容忍 `H3RelayLatentUpscale` 因**它自己的** `INPUT_TYPES()` 抛而被逐节点容错跳过）。

     ⚠️ 第 5 条是 2026-10-07 补的。原来只有前四条 ⇒ 「**V3 注册了 0 个 / 注册错节点**」
     照样 **4/4 全绿**（本条记在 `LOCAL-维护规范与已知缺陷.md` §七-19）。当时的说法是
     "由 `test_v3_schema` 的 2.1/2.2 兜底" —— 但那个测试**不在这条链上**，而且它自己
     也只看"数目对不对"。**判据必须落在同一个进程里**，否则又是一处"看起来有人管"。

跑法：
    COMFYUI_PATH=<ComfyUI 根> python tools/assert_default_exit.py
"""
import asyncio
import importlib.util
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_KIT = os.path.dirname(_HERE)
_COMFY = os.environ.get("COMFYUI_PATH", "").strip()
if _COMFY and _COMFY not in sys.path:
    sys.path.insert(0, _COMFY)

# ⚠️ 不许把 _KIT 插进 sys.path：本包目录里也有 `nodes.py`，会与宿主节点注册表撞成影子模块。
#    本包的加载走 spec_from_file_location（自带 submodule_search_locations）。

if os.environ.get("H3RELAY_NODE_API"):
    print("❌ 本脚本必须在**不设** H3RELAY_NODE_API 的环境下跑；当前值是 %r"
          % os.environ["H3RELAY_NODE_API"])
    raise SystemExit(2)


def _stub_server():
    """手法与 tests/test_relay_core.py 的 26.15 一致（离线跑，不启 ComfyUI）。"""
    class _Routes:
        def post(self, _path):
            def _deco(fn):
                return fn
            return _deco

        def get(self, _path):
            def _deco(fn):
                return fn
            return _deco

    class _FakePS:
        routes = _Routes()
        instance = None
        prompt_queue = None

    _FakePS.instance = _FakePS()
    mod = types.ModuleType("server")
    mod.PromptServer = _FakePS
    sys.modules["server"] = mod


def main():
    _stub_server()
    spec = importlib.util.spec_from_file_location(
        "h3latentrelay_default", os.path.join(_KIT, "__init__.py"),
        submodule_search_locations=[_KIT])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["h3latentrelay_default"] = pkg
    spec.loader.exec_module(pkg)

    fails = []

    # —— ① 契约层（与环境无关，必须硬过）：我们的默认值就是 v3 ——
    # `NODE_API` 在 V3 加载失败时会被兜底回改成 "v1"，所以不能只看它；
    # `NODE_API_DEFAULT` 才是「我们写的默认值」这个事实。
    default = getattr(pkg, "NODE_API_DEFAULT", "ATTR_MISSING")
    ok0 = default == "v3"
    print("  [%s] NODE_API_DEFAULT == 'v3'（源码里的默认值）    → %r"
          % ("OK" if ok0 else "FAIL", default))
    if not ok0:
        fails.append("NODE_API_DEFAULT")

    # —— ② 行为层：无环境变量时实际选中的出口 ——
    api = getattr(pkg, "NODE_API", "ATTR_MISSING")
    fell_back = (api == "v1" and default == "v3")
    ncm = getattr(pkg, "NODE_CLASS_MAPPINGS", "ATTR_MISSING")
    ok1 = ncm is None
    print("  [%s] NODE_CLASS_MAPPINGS is None（V1 分支会被跳过）  → %s"
          % ("OK" if ok1 else "FAIL",
             "None" if ok1 else ("返回的是 V1 的 %d 个节点字典" % len(ncm)
                                 if isinstance(ncm, dict) else repr(ncm))))
    if not ok1:
        fails.append("NODE_CLASS_MAPPINGS")

    ep = getattr(pkg, "comfy_entrypoint", None)
    ok2 = callable(ep)
    print("  [%s] comfy_entrypoint 存在且可调用             → %s"
          % ("OK" if ok2 else "FAIL", "callable" if ok2 else repr(ep)))
    if not ok2:
        fails.append("comfy_entrypoint")

    wd = getattr(pkg, "WEB_DIRECTORY", None)
    ok3 = bool(wd)
    print("  [%s] WEB_DIRECTORY 仍在（前端挂载）            → %r"
          % ("OK" if ok3 else "FAIL", wd))
    if not ok3:
        fails.append("WEB_DIRECTORY")

    # —— ③ V3 出口**实际注册了哪些节点**（2026-10-07 加，判据见文件头第 5 条） ——
    # 🔴 为什么非加不可：前四条全绿**也**兼容"V3 注册了 0 个节点"或"注册了别的名字"。
    #    缺的这一条正是「无断言即未验收」的现场（§七-19）。
    # `nodes.py` 离线加载：本文件**刻意不把 _KIT 插进 sys.path**，所以借 fake 包的
    # submodule_search_locations 走 `h3latentrelay_default.nodes`（相对导入要靠它）。
    v1_ids, v3_ids, why = set(), set(), ""
    nmod = None
    try:
        _nspec = importlib.util.spec_from_file_location(
            "h3latentrelay_default.nodes", os.path.join(_KIT, "nodes.py"))
        nmod = importlib.util.module_from_spec(_nspec)
        sys.modules["h3latentrelay_default.nodes"] = nmod
        _nspec.loader.exec_module(nmod)
        v1_ids = set(nmod.NODE_CLASS_MAPPINGS)
    except Exception as e:
        why = "读不到本包 nodes.py（%s: %s）" % (type(e).__name__, e)
    v3_sch = {}
    try:
        ext = asyncio.run(ep()) if asyncio.iscoroutinefunction(ep) else ep()
        for _n in asyncio.run(ext.get_node_list()):
            _s = _n.define_schema() if hasattr(_n, "define_schema") else _n.GET_SCHEMA()
            v3_ids.add(_s.node_id)
            v3_sch[_s.node_id] = _s
    except Exception as e:
        why = "取不到 V3 的 get_node_list()（%s: %s）" % (type(e).__name__, e)
    # 唯一容忍项：`H3RelayLatentUpscale` 的 `INPUT_TYPES()` 会去问宿主注册表 ⇒ 某些环境抛，
    # V3 外壳**逐节点容错**跳过它（与 `tests/test_v3_schema.py` 的 2.1/2.2 **同一判据来源**：
    # 问节点自己，而不是猜"宿主 nodes 能不能 import" —— 那两者在 CI 上会分叉）。
    up_ok, up_why = False, ""
    if nmod is not None and "H3RelayLatentUpscale" in v1_ids:
        try:
            nmod.NODE_CLASS_MAPPINGS["H3RelayLatentUpscale"].INPUT_TYPES()
            up_ok = True
        except Exception as e:
            up_why = "%s: %s" % (type(e).__name__, e)
    _missing = sorted(v1_ids - v3_ids)
    _extra = sorted(v3_ids - v1_ids)
    ok4 = bool(not why and not _extra and (not _missing
                                           or (_missing == ["H3RelayLatentUpscale"] and not up_ok)))
    print("  [%s] V3 get_node_list() 注册集合 == nodes.py 的 %d 个键%s"
          % ("OK" if ok4 else "FAIL",
             len(v1_ids), "（Upscale 因自身 INPUT_TYPES() 抛而跳过）" if _missing else ""))
    print("         → V3 注册 %d 个；多出 %s ／ 少了 %s%s"
          % (len(v3_ids), _extra or "无", _missing or "无",
             ("；%s" % up_why) if (up_why and not up_ok) else ""))
    if why:
        print("         → %s" % why)
    if not ok4:
        fails.append("V3 注册集合")

    # —— ④ 两个出口的 `dev_only` 标记**逐节点一致**（2026-10-22 加） ——
    # 🔴 为什么非加不可：`🧩 Join AV` 是**内部件**，靠 `DEV_ONLY=True` 从节点菜单里隐去
    #    （宿主语义：`dev_only ⇒ skip_list = !DevMode`，注册照旧 ⇒ API/展开图/已存工作流不受影响）。
    #   默认出口是 **V3**，所以只标 V1 等于没标 —— 用户照样在菜单里看到它、接上又不知道干什么。
    #   现在 V3 由 `schema_from_v1` **派生**（结构性不可能分叉），这条是**回归闸**：
    #   将来谁改 `schema_from_v1` 忘了带 `is_dev_only`，或谁手写 schema，立刻红。
    _dev_bad = []
    for _k in sorted(v1_ids & v3_ids):
        _v1d = bool(getattr(nmod.NODE_CLASS_MAPPINGS[_k], "DEV_ONLY", False))
        _v3d = bool(getattr(v3_sch.get(_k), "is_dev_only", False))
        if _v1d != _v3d:
            _dev_bad.append("%s（V1=%s / V3=%s）" % (_k, _v1d, _v3d))
    ok5 = bool(v3_sch) and not _dev_bad
    print("  [%s] 两个出口的 dev_only 标记逐节点一致（内部件不进节点菜单）"
          % ("OK" if ok5 else "FAIL"))
    print("         → V3 标记 %d 个内部件：%s"
          % (sum(1 for _s in v3_sch.values() if getattr(_s, "is_dev_only", False)),
             [k for k, _s in sorted(v3_sch.items()) if getattr(_s, "is_dev_only", False)] or "无"))
    if _dev_bad:
        print("         → 不一致：%s" % _dev_bad)
    if not ok5:
        fails.append("dev_only 标记")

    print()
    if fails:
        if fell_back:
            reason = getattr(pkg, "NODE_API_FALLBACK_REASON", None)
            print("❌ 默认值是对的（v3），但**本环境加载不了 V3 出口** ⇒ 已回退 V1。")
            print("   回退原因：%s" % reason)
            print("   这不是本包的默认值问题，是**宿主依赖不全**：V3 要 import `comfy_api`，"
                  "它的传递闭包需要")
            print("   宿主自己的全部 Python 依赖（实测缺过 packaging / comfy-aimdo / tqdm）。")
            print("   修法：把 ComfyUI 的 requirements.txt 装上（去掉 torch 与前端包即可）。")
        else:
            # ⚠ 别把每条失败都推给 NODE_API_DEFAULT：`dev_only 标记` 与「注册集合」这两条
            #   跟默认值无关（默认值可能完全正确）。按失败项分开说话，否则人被引向错方向。
            print("❌ 断言未过：%s" % "、".join(fails))
            if "dev_only 标记" in fails:
                print("   → dev_only 不一致跟默认值无关：查 `v3/_compat.schema_from_v1` 是否带了")
                print("     `is_dev_only=getattr(v1_cls, \"DEV_ONLY\", False)`，以及 V1 类上的 `DEV_ONLY`。")
            if ("契约 1" in fails) or ("契约 2" in fails):
                print("   → 契约项才要看 `__init__.py` 的 `NODE_API_DEFAULT`。")
        return 1
    print("✅ 默认出口 = V3（6/6：契约 2 + 行为 4）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
