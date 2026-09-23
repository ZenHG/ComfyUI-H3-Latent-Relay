# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Relay-Kit contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""V3 外壳与 V1 的**逐字段一致性**机检（零 GPU、零模型）。

## 为什么必须机检

老工作流的 ``widgets_values`` 是**按位置**存进 JSON 的：V1/V3 参数表只要有**一处顺序**
或**一处取值**不同，用户已有图的参数就会**静默错位** —— 不报错、不提示，只是数值跑到别的旋钮上。
所以这里逐项比对，不靠肉眼。

判据（刻意宽松，避免假红）：
  * input **id 序列与顺序** 必须与 V1 的 ``required + optional`` **完全相等**（严格）
  * V1 每个选项的**键与值**都必须能在 V3 的 ``as_dict()`` 里找到相同值（**单向包含**）
    —— V3 会回填默认值（如 ``multiline=False`` 被显式写出来、输出节点自动补 ``hidden``），
    硬比"键集合相等"会假红。
  * Combo 的 ``options`` **全序**必须相等（顺序也是用户可见行为）

跑法：
    COMFYUI_PATH=<ComfyUI 根> H3RELAY_NODE_API=v3 python tests/test_v3_schema.py
"""
import asyncio
import importlib.util
import os
import sys

_KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COMFY = os.environ.get("COMFYUI_PATH", "").strip()
# ⚠️ **不要把 _KIT 插进 sys.path**：本包目录里也有 `nodes.py`，而 `nodes.py` 的
# `_comfy_registry()` 会 `import nodes` 去拿**宿主**的节点注册表。把本包目录排到前面
# 会让它绑到我们自己这份（影子模块），触发第二次 exec + 相对导入失败。
# 本包的加载走下面的 `spec_from_file_location`（自带 submodule_search_locations），不需要 sys.path。
if _COMFY and _COMFY not in sys.path:
    sys.path.insert(0, _COMFY)

os.environ["H3RELAY_NODE_API"] = "v3"          # 强制走 V3 出口（必须在 import 本包之前）

# --- stub 掉宿主 server（离线跑，不启 ComfyUI）---
# 手法与 tests/test_relay_core.py 的 26.15 一致：本包 __init__.py 导入时会注册
# /h3relay/concat 路由，需要 PromptServer.instance.routes —— 真实环境由 main.py 创建，
# 离线环境没有 ⇒ 不 stub 就会在导入本包时炸。
import types as _types                                 # noqa: E402


class _Routes:
    def post(self, _path):
        def _deco(fn):
            return fn
        return _deco


class _FakePS:
    routes = _Routes()
    instance = None
    prompt_queue = None


_FakePS.instance = _FakePS()
_srvmod = _types.ModuleType("server")
_srvmod.PromptServer = _FakePS
sys.modules["server"] = _srvmod

# --- 尽量把**真实宿主** nodes 模块拉进来 ---
# nodes.py 的 _comfy_registry() 会 `import nodes` 取宿主注册表（H3RelayLatentUpscale 的
# INPUT_TYPES 要找上游包）。真实宿主进程里它已在 sys.modules 里；离线环境这里主动拉一次。
# 拉不到也不假红 —— 见 2.1/2.2 的判据（V3 的 entrypoint 已做逐节点容错，只丢那一个）。
_HOST_REGISTRY_OK = False
try:
    import nodes as _host_nodes                        # noqa: E402
    _HOST_REGISTRY_OK = getattr(_host_nodes, "NODE_CLASS_MAPPINGS", None) is not None
except Exception as _e:                                # noqa: BLE001
    print("  提示：宿主 nodes 模块未能加载（%s: %s）⇒ H3RelayLatentUpscale 的 schema 会被跳过。"
          % (type(_e).__name__, _e))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  " + detail) if detail else ""))


# 目录名含 "-" ⇒ 不能直接 import，用包方式按文件位置加载
_spec = importlib.util.spec_from_file_location(
    "h3relay_kit", os.path.join(_KIT, "__init__.py"),
    submodule_search_locations=[_KIT])
KIT = importlib.util.module_from_spec(_spec)
sys.modules["h3relay_kit"] = KIT
_spec.loader.exec_module(KIT)

V1 = sys.modules["h3relay_kit.nodes"]

print("[1] 出口互斥（宿主加载器是 if V1 … return True / elif comfy_entrypoint）")
check("1.1 V3 模式下 NODE_CLASS_MAPPINGS 为 None（宿主才会走 V3 分支）",
      getattr(KIT, "NODE_CLASS_MAPPINGS", "缺失") is None,
      repr(getattr(KIT, "NODE_CLASS_MAPPINGS", "缺失")))
check("1.2 comfy_entrypoint 存在且可调用", callable(getattr(KIT, "comfy_entrypoint", None)))
check("1.3 WEB_DIRECTORY 仍在（V3 出口下前端 JS 照常挂载）",
      getattr(KIT, "WEB_DIRECTORY", None) == "./web", repr(getattr(KIT, "WEB_DIRECTORY", None)))

print()
print("[2] 扩展与节点清单")
_ep = KIT.comfy_entrypoint
_ext = asyncio.run(_ep()) if asyncio.iscoroutinefunction(_ep) else _ep()
V3_NODES = asyncio.run(_ext.get_node_list())
check("2.1 get_node_list() 返回 8 个节点（%s）"
      % ("宿主注册表可用" if _HOST_REGISTRY_OK else "宿主注册表不可用 ⇒ 允许 Upscale 缺席"),
      len(V3_NODES) == (8 if _HOST_REGISTRY_OK else 7), "实际 %d" % len(V3_NODES))

V3_BY_ID = {}
for _n in V3_NODES:
    _s = _n.define_schema() if hasattr(_n, "define_schema") else _n.GET_SCHEMA()
    V3_BY_ID[_s.node_id] = (_n, _s)

_missing = sorted(set(V1.NODE_CLASS_MAPPINGS) - set(V3_BY_ID))
_extra = sorted(set(V3_BY_ID) - set(V1.NODE_CLASS_MAPPINGS))
check("2.2 node_id 集合 == V1 键集合（仅允许 Upscale 因宿主注册表缺席被跳过）",
      not _extra and (not _missing
                      or (_missing == ["H3RelayLatentUpscale"] and not _HOST_REGISTRY_OK)),
      "多出 %s ／ 少了 %s" % (_extra, _missing))

print()
print("[3] 逐字段对齐（顺序错 = 用户参数静默错位）")
_N_TOTAL, _N_INPUT = 0, 0
for _name in sorted(V1.NODE_CLASS_MAPPINGS):
    if _name not in V3_BY_ID:
        continue
    _v1cls = V1.NODE_CLASS_MAPPINGS[_name]
    _cls, _sch = V3_BY_ID[_name]
    _it = _v1cls.INPUT_TYPES()
    _pairs = []
    for _sec, _opt in (("required", False), ("optional", True)):
        for _k, _spec in (_it.get(_sec) or {}).items():
            _pairs.append((_k, _spec, _opt))
    _want_ids = [p[0] for p in _pairs]
    _got_ids = [x.id for x in _sch.inputs]
    _N_TOTAL += 1
    check("3.%d %s input id 序列与顺序" % (_N_TOTAL, _name), _got_ids == _want_ids,
          "V3=%s｜V1=%s" % (_got_ids, _want_ids))

    _v3in = {x.id: x for x in _sch.inputs}
    _bad = []
    for _k, _spec, _opt in _pairs:
        _t = _spec[0] if isinstance(_spec, tuple) else _spec
        _v1opts = (_spec[1] if isinstance(_spec, tuple) and len(_spec) > 1 else {}) or {}
        _obj = _v3in.get(_k)
        if _obj is None:
            _bad.append("%s 缺失" % _k)
            continue
        _N_INPUT += 1
        if not isinstance(_t, list):
            try:
                _d = _obj.as_dict()
            except Exception as _e:                    # noqa: BLE001
                _bad.append("%s.as_dict 失败(%s)" % (_k, _e))
                continue
            for _ok, _ov in _v1opts.items():
                if _ok not in _d:
                    continue                               # V3 未承载该键（如 display）⇒ 交给下面单列
                if _d[_ok] != _ov:
                    _bad.append("%s.%s: V1=%r V3=%r" % (_k, _ok, _ov, _d[_ok]))
        else:
            _opts_v3 = list(getattr(_obj, "options", []) or [])
            if _opts_v3 != list(_t):                       # 组合项**全序**也要求一致
                _bad.append("%s.options: V1=%r V3=%r" % (_k, list(_t), _opts_v3))
        if bool(getattr(_obj, "optional", False)) != bool(_opt):
            _bad.append("%s.optional: V1=%r V3=%r" % (_k, _opt, getattr(_obj, "optional", None)))
    check("3.%d %s 每个 input 的取值与 V1 一致" % (_N_TOTAL, _name), not _bad, "；".join(_bad[:4]))

    _rts = list(getattr(_v1cls, "RETURN_TYPES", ()))
    _rns = list(getattr(_v1cls, "RETURN_NAMES", ()))
    _got_out = len(_sch.outputs)
    check("3.%d %s outputs 路数 == len(RETURN_TYPES)" % (_N_TOTAL, _name), _got_out == len(_rts),
          "V3=%d V1=%d" % (_got_out, len(_rts)))
    if _rns:
        _got_names = [getattr(o, "display_name", None) for o in _sch.outputs]
        check("3.%d %s outputs display_name == RETURN_NAMES" % (_N_TOTAL, _name), _got_names == _rns,
              "V3=%s V1=%s" % (_got_names, _rns))
    check("3.%d %s is_output_node == V1.OUTPUT_NODE" % (_N_TOTAL, _name),
          bool(_sch.is_output_node) == bool(getattr(_v1cls, "OUTPUT_NODE", False)),
          "V3=%r V1=%r" % (_sch.is_output_node, getattr(_v1cls, "OUTPUT_NODE", False)))
    check("3.%d %s display_name == V1 显示名" % (_N_TOTAL, _name),
          _sch.display_name == V1.NODE_DISPLAY_NAME_MAPPINGS.get(_name),
          "V3=%r" % (_sch.display_name,))
    check("3.%d %s category == V1.CATEGORY" % (_N_TOTAL, _name),
          _sch.category == getattr(_v1cls, "CATEGORY", None),
          "V3=%r V1=%r" % (_sch.category, getattr(_v1cls, "CATEGORY", None)))

print()
print("[4] execute 的接线（外壳必须转调 V1 的 FUNCTION，不许重写业务逻辑）")
_V1_FUNCS = {n: getattr(c, "FUNCTION", None) for n, c in V1.NODE_CLASS_MAPPINGS.items()}
check("4.1 8 个外壳的 execute 都是 classmethod 且函数名统一为 execute",
      all(hasattr(c, "execute") for c, _ in V3_BY_ID.values()))
check("4.2 外壳未覆盖 V1 的方法名（业务逻辑仍在 nodes.py）",
      all(f not in dir(cls) or f == "execute" for cls, _ in V3_BY_ID.values() for f in _V1_FUNCS.values()))

print()
print("[5] ui 回显形状（拼接路由靠 history.outputs[node_id]['h3relay_pcm'] 取 PCM 边车）")
from h3relay_kit.v3 import _compat as C                  # noqa: E402
_ui_out = C.node_output({"ui": {C.V1.CORE.PCM_UI_KEY: [{"filename": "x", "subfolder": "s", "type": "output"}]},
                         "result": ("a", "b")})
check("5.1 V1 的 {'ui','result'} → io.NodeOutput(*result, ui={...})",
      getattr(_ui_out, "ui", None) == {C.V1.CORE.PCM_UI_KEY: [{"filename": "x", "subfolder": "s", "type": "output"}]}
      and tuple(_ui_out.result) == ("a", "b"), repr(getattr(_ui_out, "ui", None)))
check("5.2 裸 tuple → io.NodeOutput(*raw)", tuple(C.node_output(("a", "b")).result) == ("a", "b"))
check("5.3 {} （无输出节点）→ io.NodeOutput()", C.node_output({}).result is None)

print()
print("=" * 78)
print("结果：通过 %d / 失败 %d   （节点 %d 个，逐项比对的 input %d 个）"
      % (len(PASS), len(FAIL), len(V3_NODE_IDS := V3_BY_ID), _N_INPUT))
print("=" * 78)
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  · " + f)
sys.exit(1 if FAIL else 0)
