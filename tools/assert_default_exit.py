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

判据（三条，全过才退出码 0）：
  1. `NODE_CLASS_MAPPINGS` 是 **None** —— 宿主加载器（ComfyUI/nodes.py:2295-2337）的判据含
     `is not None`，为 None 才会落到 `elif comfy_entrypoint` 分支；
  2. `comfy_entrypoint` **存在且可调用**；
  3. `WEB_DIRECTORY` 仍在（前端 JS 靠它挂载，V3 出口下宿主照样处理）。

跑法：
    COMFYUI_PATH=<ComfyUI 根> python tools/assert_default_exit.py
"""
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
            print("❌ 默认出口不是 V3（缺 %s）。检查 __init__.py 的 NODE_API_DEFAULT。" % "、".join(fails))
        return 1
    print("✅ 默认出口 = V3（4/4：契约 1 + 行为 3）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
