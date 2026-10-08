# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
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
import types as _types


class _Routes:
    """装饰器透传 stub：注册路由时用哪个 HTTP 方法都认。

    ⚠️ 别写死方法名 —— 2026-09-25 踩过**两次**：新增 `GET /h3relay/health` 后，
    本文件（还有 test_relay_core）的 stub 只实现 `post` ⇒ 导入本包即
    `AttributeError: '_Routes' object has no attribute 'get'`。
    `tools/assert_default_exit.py` 与 `tools/make_minimal_bundle.py` 的同类桩**本来就有 get**，
    所以当时只改了报错的那一个 ⇒ **本文件漏了**，而它恰好不在一键校验套件里 ⇒ 假绿。
    ⇒ 用 `__getattr__` 兜底，以后加任何方法（get/put/delete…）都不用回来改这里。
    """

    def __getattr__(self, _name):
        def _reg(_path):
            def _deco(fn):
                return fn
            return _deco
        return _reg


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
_HOST_NODES_IMPORT_OK = False
try:
    import nodes as _host_nodes
    _HOST_NODES_IMPORT_OK = getattr(_host_nodes, "NODE_CLASS_MAPPINGS", None) is not None
except Exception as _e:
    # 🔴 这句话以前写的是"⇒ H3RelayLatentUpscale 的 schema 会被跳过" —— **是假的**（2026-09-30 实测）：
    #    宿主 import 抛的是 **RuntimeError**（如 CI 无 NVIDIA 驱动）时，`_upscaler_module()`
    #    **按设计吞掉 RuntimeError** ⇒ INPUT_TYPES 照样成功 ⇒ 该节点**在场**。
    #    只有抛 **ImportError**（如 `sys.modules["nodes"]=None`）才真的被跳过。
    #    ⇒ 探针只报事实，**不再替 2.1 下结论**（判据在 2.1 处由节点自己回答）。
    print("  提示：宿主 nodes 模块未能加载（%s: %s）。"
          "⚠ 这**不等于** Upscale 会缺席 —— 它是否在场由自己的 INPUT_TYPES() 决定（见 2.1）。"
          % (type(_e).__name__, _e))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  " + detail) if detail else ""))


# 目录名含 "-" ⇒ 不能直接 import，用包方式按文件位置加载
_spec = importlib.util.spec_from_file_location(
    "h3latentrelay", os.path.join(_KIT, "__init__.py"),
    submodule_search_locations=[_KIT])
KIT = importlib.util.module_from_spec(_spec)
sys.modules["h3latentrelay"] = KIT
_spec.loader.exec_module(KIT)

V1 = sys.modules["h3latentrelay.nodes"]

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
# 🔴 节点数为什么**只有一个值**（2026-09-25 首测 / 2026-09-30 修正机制 / **2026-10-07 收敛**）：
#
#   `H3RelayLatentUpscale.INPUT_TYPES()` 要 `_upscale_model_names()` → `_upscaler_module()`
#   → `_upscaler_cls()` → `_comfy_registry()`（= 去**宿主**节点注册表里找上游包）：
#   · 注册表**拿得到**（宿主 `nodes` 已加载）⇒ 里面没有上游节点 ⇒ `_upscaler_cls()` 抛
#     **RuntimeError** ⇒ `_upscaler_module()` **捕获它**（它只 `except RuntimeError`）
#     ⇒ 退回 `folder_paths.get_filename_list("latent_upscale_models")`
#     （**宿主自带注册该目录**，`folder_paths.py:43`）⇒ 正常返回 ⇒ INPUT_TYPES 不抛。
#   · 注册表**拿不到**（宿主 `nodes` 导不进来 / 被本包目录遮成影子模块）⇒ 同样收敛成
#     **RuntimeError** ⇒ 同样被吞 ⇒ **节点照样在场**。
#   ⇒ **两条路都是 8 节点 / 122 input / 72 项**（数字会随加参数变，别再往这里抄）。
#
#   🔴 2026-10-07 之前不是这样：`_comfy_registry()` 里的 `import nodes` 若抛 **ImportError**
#     会**穿透**出去（`_upscaler_module()` 只吞 RuntimeError）⇒ INPUT_TYPES 抛 ⇒ 本文件会
#     打印"它会被逐节点容错跳过" ⇒ **7 节点 / 63 项**。那个"第三档"只在
#     `sys.modules["nodes"] = None` 这种**人工硬挡**下出现 —— 而 **CI 抛的是 RuntimeError、
#     被吞掉 ⇒ CI 一直是 8 节点**。⇒ 旧文档把一个人造态写成了"CI 的环境"，那是错的。
#     修法在 `nodes.py::_comfy_registry`（先扫 `sys.modules`；再 `import nodes` 且兜住任何异常
#     ⇒ 失败一律收敛成 RuntimeError）⇒ **这一档不再可达**。
#
#   ⚠️ **别用"只把上游节点从注册表里摘掉"来模拟"没有注册表"** —— 那复现的是
#      「装了宿主、没装上游包」，那种情况**仍然是 8 节点**。复现"没有宿主注册表"：
#      `sys.modules["nodes"] = None` 后跑本文件
#      （工具版：`python tools/ci_env_repro.py tests/test_v3_schema.py`）⇒ **同样 8 节点**。
# 🔴 判据必须与被测对象**同源**（2026-09-30 修，CI 实测踩到）：
#    "Upscale 在不在"只取决于**它自己的 `INPUT_TYPES()` 会不会抛** ——
#    不是"宿主 nodes 能不能 import"。两者曾恰好**分叉**：
#      · 宿主 import 抛 **RuntimeError**（无 NVIDIA 驱动）⇒ 探针说"不可用"；
#      · 而 `_upscaler_module()` **按设计吞掉 RuntimeError** ⇒ INPUT_TYPES 成功 ⇒ 节点在场。
#    用探针去**推**节点数 ⇒ 期望 7、实际 8 ⇒ **假红**。
#    现在直接问节点自己；节点数也不写死（加节点时不用回来改这里）。
try:
    V1.NODE_CLASS_MAPPINGS["H3RelayLatentUpscale"].INPUT_TYPES()
    _UPSCALE_OK = True
except Exception as _e:
    _UPSCALE_OK = False
    print("  提示：H3RelayLatentUpscale.INPUT_TYPES() 抛了（%s: %s）⇒ 它会被逐节点容错跳过。"
          % (type(_e).__name__, _e))
_EXPECT_NODES = len(V1.NODE_CLASS_MAPPINGS) - (0 if _UPSCALE_OK else 1)
check("2.1 get_node_list() 返回 %d 个节点（判据 = Upscale 自己的 INPUT_TYPES() %s）"
      % (_EXPECT_NODES, "不抛 ⇒ 外壳全在场" if _UPSCALE_OK else "抛 ⇒ 它被容错跳过"),
      len(V3_NODES) == _EXPECT_NODES, "实际 %d" % len(V3_NODES))

V3_BY_ID = {}
for _n in V3_NODES:
    _s = _n.define_schema() if hasattr(_n, "define_schema") else _n.GET_SCHEMA()
    V3_BY_ID[_s.node_id] = (_n, _s)

_missing = sorted(set(V1.NODE_CLASS_MAPPINGS) - set(V3_BY_ID))
_extra = sorted(set(V3_BY_ID) - set(V1.NODE_CLASS_MAPPINGS))
check("2.2 node_id 集合 == V1 键集合（仅允许 Upscale 因自己的 INPUT_TYPES() 抛而被跳过）",
      not _extra and (not _missing
                      or (_missing == ["H3RelayLatentUpscale"] and not _UPSCALE_OK)),
      "多出 %s ／ 少了 %s" % (_extra, _missing))

print()
print("[3] 逐字段对齐（顺序错 = 用户参数静默错位）")
_N_TOTAL, _N_INPUT, _N_DECL = 0, 0, 0
# 🔴 2026-10-07 加：把"V3 未承载的 option 键"与"V1 用到的全部 option 键"**记下来**，
#   交给第 [8] 节判红。原先 `_ok not in _d` 是**裸 continue** ⇒ 将来给某个参数加一个
#   `v3/_compat._OPTS` 没有映射的 option 键时，这里静默跳过、三条测试全绿，
#   而 V3 出口**悄悄少了那个选项**（老图参数错位）。
_USED_OPTS = set()
_NOT_CARRIED = []
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
    # 🔴 2026-09-25：`_N_DECL` = **V1 声明**的 input 数；`_N_INPUT` = V3 里**真比对到**的。
    #   原来只统计后者 ⇒ 一旦某个 input 在 V3 缺席，**总数静默变小**（少 1 就少 1），
    #   而结果行照样打印"逐项比对的 input N 个" —— 数字变小了却看不出是"缺失"还是"本来就这么少"。
    #   现在两者都算，并在 [7] 里断言相等 ⇒ 缺失会让它**报红**，而不是悄悄改小计数。
    _N_DECL += len(_want_ids)
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
            except Exception as _e:
                _bad.append("%s.as_dict 失败(%s)" % (_k, _e))
                continue
            for _ok, _ov in _v1opts.items():
                _USED_OPTS.add(_ok)
                if _ok not in _d:
                    # 🔴 原来这里是裸 `continue`（只写了一句注释"交给下面单列"，而**下面并没有单列**）
                    #   ⇒ 键被 V3 丢掉时**不报红**。现在记进 `_NOT_CARRIED`，由第 [8] 节判。
                    _NOT_CARRIED.append((_name, _k, _ok))
                    continue
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
from h3latentrelay.v3 import _compat as C
_ui_out = C.node_output({"ui": {C.V1.CORE.PCM_UI_KEY: [{"filename": "x", "subfolder": "s", "type": "output"}]},
                         "result": ("a", "b")})
check("5.1 V1 的 {'ui','result'} → io.NodeOutput(*result, ui={...})",
      getattr(_ui_out, "ui", None) == {C.V1.CORE.PCM_UI_KEY: [{"filename": "x", "subfolder": "s", "type": "output"}]}
      and tuple(_ui_out.result) == ("a", "b"), repr(getattr(_ui_out, "ui", None)))
check("5.2 裸 tuple → io.NodeOutput(*raw)", tuple(C.node_output(("a", "b")).result) == ("a", "b"))
check("5.3 {} （无输出节点）→ io.NodeOutput()", C.node_output({}).result is None)

print()
print("[6] 加载摘要 / health 用的节点清单（`__init__._node_names`）")
# 🔴 2026-09-25 新增。这里判的是「**从哪条分支取的**」，不是「取到几个」。
#   背景：该函数原先写 `from .v3 import nodes`（真实文件是 `v3/nodes_v3.py`）⇒ 每次都抛
#   ModuleNotFoundError、被 `except: pass` 吞掉 ⇒ **一直静默退回 V1 映射**。当时看不出来，
#   因为两套清单 1:1、退回去算出来还是 8 个、名字也对（**结果正确 ≠ 代码正确**）。
#   判据 = **顺序**：V3 分支给 NODES 序（Upscale→Save→Load→TrimAV→…），V1 兜底给**字典序**。
#   两者一致 ⇒ 才说明真走了 V3 分支，而不是"碰巧算对了"。
from h3latentrelay.v3.nodes_v3 import NODES as _V3_NODES_SRC
_expect_order = [c.__name__ for c in _V3_NODES_SRC]
_got_order = KIT._node_names()
check("6.1 `_node_names()` 真走 V3 分支（判据 = 返回 **NODES 序**，而非 V1 兜底的**字典序**）",
      _got_order == _expect_order,
      "得 %s ｜ 期望 %s" % (_got_order, _expect_order))
check("6.2 清单非空且与 `v3.nodes_v3.NODES` 等长（banner 的节点数由此而来）",
      len(_got_order) == len(_V3_NODES_SRC) > 0,
      "%d vs %d" % (len(_got_order), len(_V3_NODES_SRC)))

print()
print("[7] 计数自洽（这两个数会被写进 ci.yml / docs / README ⇒ 必须与实跑严格一致）")
# 🔴 2026-09-25 新增。为什么单列一节：结果行会打印「节点 N 个，逐项比对的 input M 个」，
#   而**这两个数被抄进了 4 个文件**（`ci.yml` · `docs/08-testing.md` · `README.md` · `__init__.py`）。
#   过去它们只靠人工同步 ⇒ 实测漂过：`ci.yml` 写着"CI 7 节点 / 98 input"，
#   真值却是 **8 节点 / 112 input**（那个 7 来自一个**错误前提**，见 2.1 的注释）。
#   ⇒ 现在：① 计数自身先自洽；② `tools/review_050.py` 的 H3h 再去核那 4 个文件的声明。
check("7.1 参与比对的节点数 == get_node_list() 实际返回的节点数（与环境无关的自洽）",
      _N_TOTAL == len(V3_NODES) == len(V3_BY_ID),
      "比对 %d ／ 清单 %d ／ V1 注册 %d"
      % (_N_TOTAL, len(V3_BY_ID), len(V1.NODE_CLASS_MAPPINGS)))
check("7.2 逐项比对的 input 数 == V1 声明的 input 数（缺失必须报红，不许静默改小计数）",
      _N_INPUT == _N_DECL and _N_INPUT > 0,
      "V3 比对 %d ／ V1 声明 %d" % (_N_INPUT, _N_DECL))

print()
print("[8] option 键的对账（V1 用到的键必须被 V3 承载，否则**静默丢该选项**）")
# 🔴 2026-10-07 新增（外部审核发现，见 `LOCAL-维护规范与已知缺陷.md` §七-20）。
#   病：`v3/_compat._kwargs_of()` 只搬 `_OPTS` 里列出的键，**其余静默丢**；而 [3] 的判据是
#   "单向包含"，键不在 V3 的 `as_dict()` 里时 `continue` ⇒ **不报红**。
#   ⇒ 给某个参数加一个 `_OPTS` 没映射的 option 键 = V3 出口悄悄少一个选项（老图参数错位），
#     而当时全套测试仍然绿。现在两层判据，**任一层命中就报红**：
#     ① 静态覆盖：`nodes.py` 用到的每个 option 键都在 `_OPTS` 里（或在下表的例外里）；
#     ② 运行期兜底：[3] 里"V3 未承载"而跳过的键，也必须落在同一份例外表里。
_FROM_COMPAT = None
try:
    from h3latentrelay.v3 import _compat as _C
    _FROM_COMPAT = set(_C._OPTS)
except Exception as _e:
    print("  ⚠ 取不到 `v3/_compat._OPTS`（%s: %s）⇒ 本节点无法对账" % (type(_e).__name__, _e))

# V3 的 `io.*.Input` **本来就不承载**的 V1 option 键。⚠️ 白名单必须**逐项写明理由** ——
# 否则它迟早变成"新增键时顺手往里塞"的垃圾桶，而那样就没人会去补映射了。
_V3_NO_CARRY = {
    # V1 的 `display` 是给前端看的展示提示（如 "hidden"），V3 的 io.Input 没有对应字段。
    # 当前 `nodes.py` **没有使用**它；一旦真用上，说明有参数靠它改前端行为 ⇒ 那时必须回来补映射。
    "display": "V3 的 io.Input 无对应字段（当前未使用；一旦使用要回来补映射）",
}
_leak = sorted(_USED_OPTS - (_FROM_COMPAT or set()) - set(_V3_NO_CARRY))
check("8.1 nodes.py 用到的 option 键都被 `v3/_compat._OPTS` 承载（用了 %d 个键）"
      % len(_USED_OPTS), _FROM_COMPAT is not None and not _leak,
      "未映射=%s" % _leak)
_bad_nc = sorted({_ok for _n, _k, _ok in _NOT_CARRIED} - set(_V3_NO_CARRY))
check("8.2 [3] 里「V3 未承载」而跳过的键都在上面的例外表里（跳过 %d 处）"
      % len(_NOT_CARRIED), not _bad_nc,
      "跳出例外表=%s ｜ 明细=%s" % (_bad_nc, _NOT_CARRIED[:4]))

print()
print("=" * 78)
print("结果：通过 %d / 失败 %d   （节点 %d 个，逐项比对的 input %d 个）"
      % (len(PASS), len(FAIL), len(V3_BY_ID), _N_INPUT))
print("=" * 78)
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  · " + f)
# 脚本式退出码：失败 1 / 成功 0。
# ⚠️ 在 pytest 下必须**跳过**退出：模块级 `SystemExit` 会让 `pytest tests/` 报
#    `INTERNALERROR` 整个崩掉（连全绿都崩）—— 2026-10-07 实测：
#    `test_relay_core.py` / `test_experimental.py` 早已加了这个保护，**本文件漏了**
#    ⇒ `pytest tests/` 一直是坏的，只是没人这么跑过。
if "pytest" not in sys.modules:
    sys.exit(1 if FAIL else 0)
