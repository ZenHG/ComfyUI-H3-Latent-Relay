# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Relay-Kit contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""make_minimal_workflow.py — 生成「最小官方节点 + 本包」的续接演示工作流。

【为什么要用脚本生成，而不是手写一份 JSON 交上去】
UI 格式工作流的 ``widgets_values`` 是**按位置**对应前端 widget 槽位的，而且前端会
**自动注入**一些不出现在 ``INPUT_TYPES`` 里的格子（典型：带
``control_after_generate: True`` 的 ``seed`` 后面会多一格下拉）。手写只要漏掉中间任意一格，
其后所有取值**整体前移一位** —— 文件照样能打开、能提交、**不报任何错**，但参数全是错的。
（实测记录见 CHANGES 0.2.1。随包的 ``tools/check_ui_workflow.py`` 就是为查这个而写的。）

本脚本从**正在运行的 ComfyUI** 的 ``/object_info`` 读真实 schema 来拼，槽位顺序由 schema 推导，
所以天然不会错位；上游升级后重跑一次即可。生成完请用 ``check_ui_workflow.py`` 复核。

【用法】
    # 1) 先启动 ComfyUI（另开一个终端）
    python main.py

    # 2) 生成
    python examples/make_minimal_workflow.py                   # 默认写到 examples/
    python examples/make_minimal_workflow.py --out my.json --api http://127.0.0.1:8188

    # 3) 复核（必须 0 问题）
    python tools/check_ui_workflow.py examples/minimal_relay_official.json

【生成的是什么】
    4 个官方加载器 → 官方条件节点（出词 + AV latent）
                  → 🔗 续接 拷贝桥（复合桥）→ KSampler → 🔗 续接 Latent 存
                  → VAEDecode / VAEDecodeAudio → 🔗 续接裁重叠 → CreateVideo → SaveVideo

    图里有一个 `段号`（PrimitiveInt）同时喂给「桥」和「落盘」的 stage_index，
    所以**跑下一段只需要改这一个数**（这才是"正确连线"最省心的形态）。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import urllib.request

DEFAULT_API = "http://127.0.0.1:8188"

# 与 tools/check_ui_workflow.py 保持一致
WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"}

# 前端自带的虚拟节点：不在 /object_info 里，但前端一定认识。
# （`Note` 是注释框，用它把说明写进图里 —— 比只写在 README 里更容易被看见。）
FRONTEND_ONLY = {"Note"}

# 本机实测可用的模型文件名（都来自 ComfyUI/output 里现成的工作流）。
# 不在候选列表里时会自动退回该节点的第一个候选项。
PREFERRED = {
    "unet_name": "minimax_h3_hybrid_fl2va_ref2va_b25-49-int8.safetensors",
    "clip_name": "qwen3vl_32b_minimax_h3_nvfp4.safetensors",
    "type": "minimax",                      # CLIPLoader 的 type
    "vae_name": [
        "minimax_h3_video_vae_int8_convrot.safetensors",   # 视频 VAE（第 1 个 VAELoader）
        "minimax_h3_audio_vae_fp32.safetensors",           # 音频 VAE（第 2 个 VAELoader）
    ],
}

PROMPT_PLACEHOLDER = (
    "日式赛璐璐 2D 动画，暖琥珀主光，超现实咖啡厨房。\n"
    "[Shot 1] 中近景，Yuki 与 Kara 近身缠斗，一记右直拳擦过耳侧，白发随拳风甩起……\n"
    "【本段是续接段：开头请直接接住上一段结尾的动作与机位，不要重新起手】\n"
    "文字：全片画面无任何文字与符号。\n"
    "overall_soundscape: 拳风破空、拳掌相交的闷响、两人急促的呼吸、厨房低频底噪。无音乐。"
)


# ---------------------------------------------------------------- schema 工具
def _ty(spec):
    """取输入的声明类型（``"INT"`` / ``"FLOAT"`` / ``"COMBO"`` / ``"IMAGE"`` …）。

    🔴 **必须同时接受 ``list`` 与 ``tuple``**（2026-09-19 修复）。
    本脚本有两个 schema 来源，容器类型**不一样**：
      · 服务端 ``/object_info``（JSON）→ ``list``；
      · 本包节点的**本地** ``nodes.py`` 的 ``INPUT_TYPES()`` → ``tuple``。
    原实现只认 ``list`` ⇒ 本地来源的每个输入都返回 ``None`` ⇒ 调用点 ``_ty(spec) or "*"``
    把类型退化成 ``"*"``，且 ``t in WIDGET_TYPES`` 恒假 ⇒ **widget 全部被当成普通插槽**：
      · 生成的 ``inputs`` 缺 ``{"widget": {"name": …}}`` 标记 ⇒ 前端
        ``nonWidgetedInputs()`` 把每个 widget 都渲染成**空的输入圆点**
        （Post 16 个 / TrimAV 22 个 / 拷贝桥 N 个），示例图一眼就是坏的；
      · ``widgets_values`` 算出来是空列表 ⇒ 写成 ``null``。
    核心节点走服务端来源所以显示正常 —— 这正是「只有本包自己的节点坏」的原因。
    """
    if not isinstance(spec, (list, tuple)) or not spec:
        return None
    return "COMBO" if isinstance(spec[0], (list, tuple)) else spec[0]


def spec_of(defn, name):
    for sec in ("required", "optional"):
        if name in (defn.get("input", {}).get(sec) or {}):
            return defn["input"][sec][name]
    return None


def all_inputs(defn):
    """(name, spec) 列表，required 在前 optional 在后 —— 与前端一致。"""
    out = []
    for sec in ("required", "optional"):
        for name, spec in (defn.get("input", {}).get(sec) or {}).items():
            out.append((name, spec))
    return out


# ---------------------------------------------------------------- 动态 COMBO（COMFY_DYNAMICCOMBO_V3）
def combo_default_key(spec, preferred=None):
    """取一个 COMBO / 动态 COMBO 的默认候选项 key。

    两种声明都覆盖：旧式 ``options`` 是字符串数组、新式是 ``{"key":…,"inputs":…}``
    字典数组；``COMBO`` 与 ``COMFY_DYNAMICCOMBO_V3`` 同款 options 结构。
    优先 PREFERRED 覆盖（本机实测可用文件名）；否则取第一个选项。
    """
    t0 = spec[0] if isinstance(spec, (list, tuple)) and spec else None
    extra = spec[1] if isinstance(spec, (list, tuple)) and len(spec) > 1 and isinstance(spec[1], dict) else {}
    if isinstance(t0, (list, tuple)):
        opts = t0
    elif t0 in ("COMBO", "COMFY_DYNAMICCOMBO_V3"):
        opts = extra.get("options") or []
    else:
        return ""
    if not opts:
        return ""
    keys = [o.get("key") if isinstance(o, dict) else o for o in opts]
    if preferred is not None and preferred in keys:
        return preferred
    first = opts[0]
    return first.get("key") if isinstance(first, dict) else first


def dynamic_subwidgets(spec, key):
    """动态 COMBO 选中 ``key`` 后**派生**的子 widget：[(子参数名, 子spec), ...]。

    顺序与前端一致：``options[key].inputs`` 的 required 在前 optional 在后。
    子 widget 名 = ``父名.子名``（前端 ``dynamicWidgets.ts`` L110/139：插在父项之后）。
    选中项没有声明子参数时返回空列表（例：SaveVideo 顶层 ``codec=auto`` 无子参）。
    """
    t0 = spec[0] if isinstance(spec, (list, tuple)) and spec else None
    extra = spec[1] if isinstance(spec, (list, tuple)) and len(spec) > 1 and isinstance(spec[1], dict) else {}
    if t0 not in ("COMBO", "COMFY_DYNAMICCOMBO_V3"):
        return []
    opts = extra.get("options") or []
    opt = next((o for o in opts if o.get("key") == key), None)
    if not opt:
        return []
    subs = []
    for sec in ("required", "optional"):
        for sk, sv in ((opt.get("inputs") or {}).get(sec) or {}).items():
            subs.append((sk, sv))
    return subs


def iter_widget_inputs(defn):
    """按前端保存顺序迭代所有「占 widget 槽」的输入：(name, spec, is_pseudo)。

    - 普通 widget（INT/FLOAT/STRING/BOOLEAN/COMBO）→ 占槽；
    - 带 ``control_after_generate`` 的（典型 seed）→ 在其后插一格伪槽（只占
      ``widgets_values``，不进 ``inputs`` 列表）；
    - **``COMFY_DYNAMICCOMBO_V3``**（动态 COMBO，如 SaveVideo 的 ``format``/``codec``）
      → 占槽，并展开选中项的子 widget（如 ``format.codec``）插在父项之后。
    这是「槽位顺序只由 schema 推导」的唯一权威实现；生成器与体检器必须共用同一套。

    🔴 2026-09-19 修复：原实现把 ``COMFY_DYNAMICCOMBO_V3`` 当普通连线 ⇒
       SaveVideo 只生成 1 个 widget 槽（filename_prefix），实际应有 4 个
       （filename_prefix / format / format.codec / codec），其后取值整体前移、
       文件能开能提交不报错——典型的「测试是虚假的」。白名单必须含动态 COMBO。
    """
    for name, spec in all_inputs(defn):
        t = _ty(spec) or "*"
        if t in WIDGET_TYPES:
            yield name, spec, False
            extra = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            if extra.get("control_after_generate"):
                yield "control_after_generate", None, True
        elif t == "COMFY_DYNAMICCOMBO_V3":
            yield name, spec, False
            for sub, subspec in dynamic_subwidgets(spec, combo_default_key(spec)):
                yield "%s.%s" % (name, sub), subspec, False


def widget_slots(defn):
    """前端实际 widget 槽位顺序（含 control_after_generate 伪槽与动态子参数）。"""
    return [n for n, _, _ in iter_widget_inputs(defn)]


def widget_input_names(defn):
    """占 ``inputs`` 列表的 widget 名（不含 control_after_generate 伪槽）。"""
    return [n for n, _, p in iter_widget_inputs(defn) if not p]


def slot_default(defn, name, spec):
    """widget 槽的默认值：动态 COMBO 用选中项的默认 key，普通走 default_value。"""
    if name == "control_after_generate":
        return "fixed"
    t = _ty(spec) or "*"
    if t == "COMFY_DYNAMICCOMBO_V3" or "." in name:
        return combo_default_key(spec) or ""
    return default_value(defn, name)


def default_value(defn, name):
    spec = spec_of(defn, name)
    if spec is None:
        return None
    extra = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
    t = _ty(spec)
    if t == "COMBO":
        opts = spec[0] if isinstance(spec[0], (list, tuple)) else extra.get("options") or []
        pref = PREFERRED.get(name)
        cands = pref if isinstance(pref, list) else [pref]
        for c in cands:
            if c is not None and c in opts:
                return c
        return opts[0] if opts else ""
    if name == "control_after_generate":
        return "fixed"
    if name == "prompt":
        return PROMPT_PLACEHOLDER
    if name == "run_id":
        return "relay_demo"
    if name == "note" and t == "STRING":
        return ""
    if name == "filename_prefix":
        return "video/H3_relay_demo"
    return extra.get("default", "")


# ---------------------------------------------------------------- 本包节点的本地 schema
def local_kit_defs():
    """从**本仓库的 nodes.py** 现读本包节点 schema（不依赖服务端）。

    为什么要这一步：示例图必须反映**代码里真实存在**的节点。若只信服务端 `/object_info`，
    一个还没重启后端的服务端就会让生成器「静默**删掉**」刚加的节点（0.5.0 复查抓到过：
    `H3RelayPost` 被删）。本包节点本来就跑在这份代码里 ⇒ **本地定义才是权威**，
    服务端只用来取官方节点（CreateVideo / LoadImage …）的 schema。
    """
    import importlib.util
    import types

    kit = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # nodes.py 顶层 `import folder_paths` ⇒ 必须先把 ComfyUI 根目录放进 sys.path。
    # 定位顺序（不开源硬编码任何本机路径）：
    #   1) 装在 <ComfyUI>/custom_nodes/<本包>/ 下时，往上两级即 ComfyUI 根（自动）；
    #   2) 装在别处时，用 COMFYUI_PATH 环境变量显式指定（与 review_050.py / 单测同约定）。
    # ⚠ 不写死作者本机路径（如 I:\ComfyUI）——那会让别的用户误以为必须装在那里。
    cands = []
    if os.environ.get("COMFYUI_PATH"):
        cands.append(os.environ["COMFYUI_PATH"])
    d = kit
    for _ in range(4):
        d = os.path.dirname(d)
        cands.append(d)
    for c in cands:
        if c and os.path.isfile(os.path.join(c, "folder_paths.py")):
            if c not in sys.path:
                sys.path.insert(0, c)
            break
    else:
        raise SystemExit(
            "[FAIL] 找不到 ComfyUI 根目录（folder_paths.py）。\n"
            "       本包装在 <ComfyUI>/custom_nodes/ 下可自动定位；否则请设\n"
            "       COMFYUI_PATH=/path/to/ComfyUI 再跑。")
    pkg = types.ModuleType("h3relay_kit_local")
    pkg.__path__ = [kit]
    sys.modules["h3relay_kit_local"] = pkg
    spec = importlib.util.spec_from_file_location("h3relay_kit_local.nodes",
                                                  os.path.join(kit, "nodes.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["h3relay_kit_local.nodes"] = mod
    spec.loader.exec_module(mod)
    out = {}
    for name, cls in mod.NODE_CLASS_MAPPINGS.items():
        it = cls.INPUT_TYPES()
        out[name] = {
            "input": {"required": dict(it.get("required") or {}),
                      "optional": dict(it.get("optional") or {})},
            "output": list(cls.RETURN_TYPES),
            "output_name": list(getattr(cls, "RETURN_NAMES", cls.RETURN_TYPES)),
            "output_node": bool(getattr(cls, "OUTPUT_NODE", False)),
            "display_name": mod.NODE_DISPLAY_NAME_MAPPINGS.get(name, name),
        }
    return out


def merge_kit_defs(oi):
    """用本地 schema 覆盖服务端的本包节点，并报出服务端是否过期。"""
    local = local_kit_defs()
    stale = []
    for name, d in local.items():
        srv = oi.get(name)
        if srv is None:
            stale.append("%s（服务端没有）" % name)
        elif (list(srv.get("output") or []) != d["output"]
              or set((srv.get("input") or {}).get("optional") or {}) != set(d["input"]["optional"])):
            stale.append("%s（服务端定义过期）" % name)
    oi.update(local)
    return local, stale


# ---------------------------------------------------------------- 图构造
class Graph:
    def __init__(self, oi):
        self.oi = oi
        self.nodes = []
        self.links = []
        self._next_id = 1
        self._next_link = 1

    def defn(self, node_type):
        if node_type in FRONTEND_ONLY:
            # 前端虚拟节点：只有 widget，没有连线，自己造一份最小 schema
            return {"input": {"required": {"text": ["STRING", {"default": ""}]}},
                    "output": [], "output_name": []}
        d = self.oi.get(node_type)
        if d is None:
            raise SystemExit(
                "[FAIL] 服务端没有节点 %r（本包的 7 个节点要装在 custom_nodes 下并重启）。\n"
                "       当前识别到本包节点：%s"
                % (node_type, sorted(k for k in self.oi if k.startswith("H3Relay")))
            )
        return d

    def add(self, node_type, *, pos, values=None, title=None, links_in=None):
        """links_in: {输入名: (源节点对象, 源输出名)}；values: {widget 名: 覆盖值}。"""
        d = self.defn(node_type)
        node = {"id": self._next_id, "type": node_type, "pos": list(pos),
                "size": [320, 100], "flags": {}, "order": len(self.nodes), "mode": 0,
                "inputs": [], "outputs": [],
                "properties": {"Node name for S&R": node_type}}
        if title:
            node["title"] = title
        self._next_id += 1
        links_in = links_in or {}
        values = values or {}

        outs = list(d.get("output") or [])
        onames = list(d.get("output_name") or [])
        node["outputs"] = [
            {"name": (onames[i] if i < len(onames) else ot), "type": ot, "links": []}
            for i, ot in enumerate(outs)
        ]

        # ⚠ 输入槽位顺序必须与 ComfyUI 前端**保存**出来的格式一致，否则 links 的
        #   目标下标会错位。约定（对比真实工作流文件实测）：
        #     先列所有「可连线」输入（required 再 optional，按 schema 顺序），
        #     再列所有「widget」输入（同序，含动态 COMBO 展开出的子参数如 format.codec）。
        #   widget 名由 ``iter_widget_inputs`` 推导（权威），动态 COMBO 也占槽且带标记。
        witems = [(n, s) for n, s, p in iter_widget_inputs(d) if not p]  # 真实 widget（去伪槽）
        widget_names = {n for n, _ in witems}
        linkable = [(name, spec) for name, spec in all_inputs(d) if name not in widget_names]

        for name, spec in linkable + witems:
            is_widget = name in widget_names
            entry = {"name": name, "type": _ty(spec) or "*", "link": None}
            if is_widget:
                entry["widget"] = {"name": name}
            link = None
            if name in links_in:
                src, src_out = links_in[name]
                sidx = out_index_of(src, src_out)
                lid = self._next_link
                self._next_link += 1
                self.links.append([lid, src["id"], sidx,
                                   node["id"], len(node["inputs"]), entry["type"]])
                src["outputs"][sidx]["links"].append(lid)
                link = lid
            entry["link"] = link
            node["inputs"].append(entry)

        # widgets_values：逐槽取「用户覆盖值 → 槽默认值」，含 control_after_generate 伪槽
        # 与动态子参数（format.codec 等）。⚠ 动态 COMBO 的子参数结构按 schema **默认选中项**
        # 展开；本示例图不覆盖 format/codec，故默认项即实际项。若将来要覆盖动态 COMBO 取值，
        # 子参数默认也须按该取值展开（SaveVideo 各选项的 codec 子参数结构一致，无此问题）。
        node["widgets_values"] = [
            values.get(n, slot_default(d, n, spec)) for n, spec, _ in iter_widget_inputs(d)
        ] or None
        self.nodes.append(node)
        return node


def out_index_of(node, out_name):
    if isinstance(out_name, int):
        return out_name
    for i, o in enumerate(node["outputs"]):
        if o["name"] == out_name:
            return i
    raise KeyError("节点 %s 没有输出 %r（有：%s）"
                   % (node["type"], out_name, [o["name"] for o in node["outputs"]]))


def validate(g, oi):
    """结构自检：id 唯一、连线两端存在、槽位下标合法、类型相容、widget 槽位数对得上。

    这些是"文件能打开但跑起来报错"的常见来源；生成后先自己过一遍，别等用户撞。
    """
    bad = []
    ids = [n["id"] for n in g.nodes]
    if len(set(ids)) != len(ids):
        bad.append("节点 id 有重复")
    lids = [l[0] for l in g.links]
    if len(set(lids)) != len(lids):
        bad.append("连线 id 有重复")

    byid = {n["id"]: n for n in g.nodes}
    want_out = {}     # (node_id, slot) -> set(link ids)
    for lid, fn, fs, tn, ts, ty in g.links:
        if fn not in byid or tn not in byid:
            bad.append("连线 #%s 端点了不存在的节点" % lid)
            continue
        src, dst = byid[fn], byid[tn]
        if not (0 <= fs < len(src["outputs"])):
            bad.append("连线 #%s 源槽位 %s 越界（%s 只有 %d 个输出）"
                       % (lid, fs, src["type"], len(src["outputs"])))
            continue
        if not (0 <= ts < len(dst["inputs"])):
            bad.append("连线 #%s 目标槽位 %s 越界（%s 只有 %d 个输入）"
                       % (lid, ts, dst["type"], len(dst["inputs"])))
            continue
        want_out.setdefault((fn, fs), set()).add(lid)
        if dst["inputs"][ts].get("link") != lid:
            bad.append("连线 #%s 自称连到 %s[%s]，但该输入的 link 字段是 %r"
                       % (lid, dst["type"], ts, dst["inputs"][ts].get("link")))
        a = src["outputs"][fs]["type"]
        b = dst["inputs"][ts]["type"]
        if a != "*" and b != "*" and a != b:
            ta, tb = set(a.split(",")), set(b.split(","))
            if not (ta & tb):
                bad.append("连线 #%s 类型不相容：%s.%s(%s) → %s.%s(%s)"
                           % (lid, src["type"], src["outputs"][fs]["name"], a,
                              dst["type"], dst["inputs"][ts]["name"], b))
    for n in g.nodes:
        if n["type"] in FRONTEND_ONLY:
            continue
        if n["type"] not in oi:
            bad.append("节点 %s 在服务端不存在" % n["type"])
            continue
        d = oi[n["type"]]
        slots = widget_slots(d)
        wv = n.get("widgets_values") or []
        if len(wv) != len(slots):
            bad.append("%s 的 widgets_values 有 %d 项，schema 推导出 %d 个槽位 %s"
                       % (n["type"], len(wv), len(slots), slots))
        # 🔴 widget 标记（2026-09-19 新增）：前端把**没有 `widget` 标记**的输入渲染成空插槽
        #   （`nonWidgetedInputs()`），而 `onGraphConfigured` 只删不补 ⇒ 缺标记 = 每个 widget
        #   在画布上多一个空圆点，且**不报任何错**。历史事故：`_ty()` 只认 list 而本地 schema
        #   是 tuple ⇒ 本包节点全部丢标记。这条判据就是那次事故的回归钉子。
        # ⚠ 这里**必须用 `spec_type_strict` 而不是 `_ty`**：两边共用 `_ty` 会自证式假绿
        #   （`_ty` 坏掉 ⇒ 期望集与实收集同时变空 ⇒ 恒相等）。见 `spec_type_strict` 注释。
        # 🔴 widget 标记（2026-09-19 新增，2026-09-19 修动态 COMBO）：前端把**没有 `widget`
        #   标记**的输入渲染成空插槽（`nonWidgetedInputs()`），而 `onGraphConfigured` 只删不补
        #   ⇒ 缺标记 = 每个 widget 在画布上多一个空圆点，且**不报任何错**。
        #   want_w 用 ``widget_input_names``（= iter_widget_inputs 去伪槽），含动态 COMBO 展开出的
        #   子参数（format.codec）；与生成器共用同一套推导，避免「白名单漏一项、want/got 一起漏」
        #   的自证式假绿。got_w 取自实际 inputs 的 widget 标记，精确相等才算过。
        want_w = widget_input_names(d)
        got_w = [i.get("name") for i in (n.get("inputs") or []) if i.get("widget")]
        if got_w != want_w:
            bad.append("%s 的 widget 标记与 schema 对不上：生成的是 %s，schema 推导是 %s"
                       "（缺标记的会被前端渲染成空插槽）"
                       % (n["type"], got_w or "（一个都没有）", want_w))
        for i, o in enumerate(n["outputs"]):
            got = set(o.get("links") or [])
            exp = want_out.get((n["id"], i), set())
            if got != exp:
                bad.append("%s 输出[%d]%s 的 links=%s，实际引出 %s"
                           % (n["type"], i, o["name"], sorted(got), sorted(exp)))
    return bad


def build(oi, length=73, width=448, height=768):
    g = Graph(oi)

    unet = g.add("UNETLoader", pos=[40, 40])
    clip = g.add("CLIPLoader", pos=[40, 200])
    vae_v = g.add("VAELoader", pos=[40, 360], values={"vae_name": PREFERRED["vae_name"][0]})
    vae_a = g.add("VAELoader", pos=[40, 520], values={"vae_name": PREFERRED["vae_name"][1]})

    stage = g.add("PrimitiveInt", pos=[40, 680], values={"value": 0}, title="① 段号：第 1 段填 0，第 2 段填 1")

    cond = g.add("MiniMaxH3ImageToVideo", pos=[420, 40],
                 links_in={"clip": (clip, "CLIP"), "vae": (vae_v, "VAE")},
                 values={"prompt": PROMPT_PLACEHOLDER, "width": width, "height": height,
                         "length": length})

    neg = g.add("ConditioningZeroOut", pos=[420, 380],
                links_in={"conditioning": (cond, 0)})

    bridge = g.add("H3RelayCopyBridge", pos=[800, 40],
                   links_in={"conditioning": (cond, 0), "latent": (cond, "LATENT"),
                             "stage_index": (stage, "INT")},
                   values={"context_frames": 22, "mask_mode": "hard", "pin_audio": True,
                           "run_id": "relay_demo"},
                   title="🔗 续接 拷贝桥（复合桥：第 0 路 latent → 采样器 latent_image，第 4 路 conditioning → positive）")

    sampler = g.add("KSampler", pos=[1180, 40],
                    links_in={"model": (unet, "MODEL"), "positive": (bridge, 3),
                              "negative": (neg, 0), "latent_image": (bridge, 0)},
                    values={"seed": 88300001, "steps": 6, "cfg": 1.0,
                            "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0},
                    title="② 采样：seed / steps 在这里")

    save = g.add("H3RelayLatentSave", pos=[1180, 400],
                 links_in={"latent": (sampler, "LATENT"), "stage_index": (stage, "INT")},
                 values={"run_id": "relay_demo", "note": ""},
                 title="🔗 落盘本段 latent（下一段的接力棒）")

    dec_v = g.add("VAEDecode", pos=[1540, 40],
                  links_in={"samples": (sampler, "LATENT"), "vae": (vae_v, "VAE")})
    dec_a = g.add("VAEDecodeAudio", pos=[1540, 260],
                  links_in={"samples": (sampler, "LATENT"), "vae": (vae_a, "VAE")})

    trim = g.add("H3RelayTrimAV", pos=[1900, 40],
                 links_in={"images": (dec_v, "IMAGE"), "audio": (dec_a, "AUDIO"),
                           "trim_frames": (bridge, "trim_frames")},
                 values={"fps": 24.0, "settle_frames": 0},
                 title="🔗 裁重叠（settle_frames=0：只裁钉住区，缝处无跳）")

    # 0.5.0：画质域后处理独立成节点（TrimAV 只管时间轴）。全部作用项默认 0 = 逐位直通；
    # guide 接 TrimAV 的第 4 路输出 prev_tail（= 上段末帧），跨段两项才有「缝的另一侧」。
    post = g.add("H3RelayPost", pos=[2260, 40],
                 links_in={"images": (trim, "images"), "guide": (trim, "prev_tail")},
                 values={},
                 title="🔗 后处理 Post（全默认关 = 直通；要治缝阶跃就把 match_prev 调到 0.5）")

    # 0.5.0：音频域同样独立成节点（音频缝不再交给组装层 ffmpeg）。
    # patch_seconds 默认 0 = 逐位直通；第 1 段也必须接（它顺手把第 1 段音频落盘当后段床源）。
    asm = g.add("H3RelayAudioSeam", pos=[2260, 320],
                links_in={"audio": (trim, "audio")},
                values={},
                title="🔗 音频缝（默认关=直通；要治缝处「抽一下」就把 patch_seconds 调 2.0）")

    mk = g.add("CreateVideo", pos=[2620, 40],
               links_in={"images": (post, "images"), "audio": (asm, "audio")},
               values={"fps": 24.0})
    g.add("SaveVideo", pos=[2620, 300], links_in={"video": (mk, "VIDEO")})

    # 🔴 Note 里的旋钮数**从 schema 现算**，不写死。
    #   2026-09-19 修：这里原本硬编码「15 个旋钮」，而 H3RelayPost 早已是 16 个
    #   （0.5.0 修正加了 match_prev_stats_frames）—— 同一页 README 写 16、注释框写 15，
    #   用户按注释框对不上。计数一律现算，杜绝这类漂移。
    n_post = len(widget_slots(g.defn("H3RelayPost")))

    g.add("Note", pos=[420, 700], title="怎么用", values={"text":
        "【最小续接演示 · 官方节点 + ComfyUI-H3-Relay-Kit】\n"
        "\n"
        "第 1 段：\n"
        "  ① 段号 = 0，填好 prompt → 点 Queue。\n"
        "  日志会打印「无 context_latent → 直通（独立段，不续接）」，裁重叠不裁。\n"
        "\n"
        "第 2 段：\n"
        "  只改两处：\n"
        "  ① 段号 = 1（桥与落盘会自动跟着走，不用手改第二处）\n"
        "  ② prompt 换成第 2 段要拍的内容（开头直接接住上一段的动作/机位）\n"
        "  再点 Queue。\n"
        "  日志应出现：\n"
        "    [H3 Relay] latent 桥续接：钉住 22 帧 / 7 步 …\n"
        "    [H3 Relay] 裁首 22 帧 = 钉住 22 + 沉降 0 ｜ 手动指定\n"
        "    [H3 Relay] 后处理：全部关闭（直通）\n"
        "    [H3 Relay] 音频缝：第 1 段无缝可补 → 直通｜本段音频已落盘 …\n"
        "    [H3 Relay] 接缝自检：前 40 帧无突变 … → 起点干净。\n"
        "\n"
        "只看这三件事就算跑通：\n"
        "  · 「钉住 22 帧」= 上一段尾部已接进来\n"
        "  · 「裁首 N 帧 = 钉住 22 + 沉降 0」= 接缝处理已生效（0.5.0 起默认不裁沉降）\n"
        "  · 「起点干净」= 没有跳变，可以拼\n"
        "\n"
        "后处理 Post（0.5.0 新增，整节点可以删掉）：\n"
        f"  {n_post} 个旋钮**全部默认关 = 逐位直通**，不接它行为与 0.4.x 一致。\n"
        "  最省的一档试法：match_prev = 0.5（段头色档/曝光对齐上段末帧，治缝上亮度阶跃）。\n"
        "\n"
        "音频缝（0.5.0 新增，整节点也可以删掉）：\n"
        "  patch_seconds 默认 0 = 逐位直通。缝处音频「抽一下 / 静音一瞬」就调 2.0：\n"
        "  把本段头 2 秒换成上一段最静窗环境声（长度守恒，不动时间轴）。\n"
        "  ⚠ 第 1 段也要接（要它把第 1 段音频落盘，第 2 段的床源就是它）；段号顺序跑。\n"
        "\n"
        "没接对的两个典型症状：\n"
        "  · 日志写「静默直通」→ 段号填了 ≥1，但 run_id 与上一段不一致 / 文件不在\n"
        "  · 画面从第 1 帧就开始重播上一段 → 裁重叠节点没接上，或 trim_frames 没接桥的第 3 路\n"
        "\n"
        "模型文件：把上面 4 个加载器的下拉改成你本机的文件即可。"}),
    return g


def main():
    ap = argparse.ArgumentParser(description="生成最小官方续接演示工作流")
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "minimal_relay_official.json"))
    ap.add_argument("--length", type=int, default=73, help="段长（像素帧，须落在 5+17k）")
    ap.add_argument("--width", type=int, default=448)
    ap.add_argument("--height", type=int, default=768)
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    api = a.api.rstrip("/")
    print("取 schema：%s/object_info" % api)
    try:
        oi = json.loads(urllib.request.urlopen(api + "/object_info", timeout=180).read().decode("utf-8"))
    except Exception as e:
        raise SystemExit("[FAIL] 取不到 object_info：%r\n       请先启动 ComfyUI（python main.py）。" % (e,))

    # 本包节点用**本地定义**覆盖服务端（服务端没重启也不影响生成正确性）
    _local, _stale = merge_kit_defs(oi)
    print("本包节点（本地定义，%d 个）：%s" % (len(_local), ", ".join(sorted(_local))))
    if _stale:
        print("  ⚠ 服务端定义与本地不一致：%s" % "；".join(_stale))
        print("    示例图按**本地**定义生成（正确）；但要让 ComfyUI 真跑起来，必须重启后端加载新节点。")

    missing = [k for k in ("H3RelayCopyBridge", "H3RelayLatentSave", "H3RelayTrimAV",
                           "H3RelayPost", "H3RelayAudioSeam") if k not in _local]
    if missing:
        raise SystemExit("[FAIL] 本仓库 nodes.py 里缺这些节点：%s" % ", ".join(missing))

    g = build(oi, length=a.length, width=a.width, height=a.height)
    bad = validate(g, oi)
    if bad:
        print("\n[FAIL] 生成结果结构自检不过：")
        for b in bad:
            print("   ✗ " + b)
        raise SystemExit(1)
    print("结构自检：通过（节点 %d / 连线 %d，widget 槽位逐节点核对）" % (len(g.nodes), len(g.links)))

    wf = {
        "id": "h3-relay-minimal",
        "revision": 0,
        "last_node_id": g._next_id - 1,
        "last_link_id": g._next_link - 1,
        "nodes": g.nodes,
        "links": g.links,
        "groups": [],
        "config": {},
        "extra": {"ds": {"scale": 0.6, "offset": [0, 0]}},
        "version": 0.4,
    }
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    # 🔴 `newline="\n"` 必须显式给：Python 在 Windows 上默认把 `\n` 翻成 `\r\n`，
    #   会让同一份生成结果在 Windows / Linux 上**字节不同**，且与 `.gitattributes`
    #   的 `* text=auto eol=lf` 打架（git 会在 commit 时改回去 ⇒ 工作树与提交态不一致）。
    io.open(a.out, "w", encoding="utf-8", newline="\n").write(
        json.dumps(wf, ensure_ascii=False, indent=2))
    print("已写出：%s（节点 %d / 连线 %d）" % (a.out, len(g.nodes), len(g.links)))
    print("下一步复核：python tools/check_ui_workflow.py %s" % a.out)


if __name__ == "__main__":
    main()
