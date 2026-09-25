# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""check_ui_workflow.py — 校验 ComfyUI **UI 格式**工作流的 widget 槽位是否错位。

【为什么需要它】
ComfyUI 前端的 widget 列表 ≠ 节点 ``INPUT_TYPES`` 里声明的 widget 列表：

  * 带 ``control_after_generate: True`` 的 widget（典型 ``seed``）后面，
    前端会**自动插一格**下拉 —— 它只出现在 ``widgets_values`` 里，**不进 ``inputs`` 列表**；
  * ``LoadImage`` 的 ``image`` 后面会追加 ``upload``；
  * 自定义节点的 ``addDOMWidget`` 会追加在**末尾**。

用脚本按 ``INPUT_TYPES`` 顺序写 ``widgets_values`` 时，只要漏掉**中间**那一格，
之后所有取值会**整体前移一位**。文件照样能打开、能提交、**不报任何错**，
但采样参数全是错的。

实测（2026-09-11）：``SelfLiftH3Sampler`` 漏掉 seed 后那一格 →
``cfg`` 5(应 1)、``transition_step`` 0.5(应 5)、``lowres_scale`` 0(应 0.5)、
``w_max`` 收到 ``"xxx.safetensors"``、``upscaler_model`` 收到 ``false``。

【判据】
1. 按前端槽位顺序**逐位**把取值喂给节点 schema 做类型 / 范围 / 候选项校验；
2. **A/V 同步接线**（2026-09-21）：落盘节点的 `audio` 不许接未裁的原始音频（`check_av_link`）；
3. **Chain 词分发/拼接**（2026-09-22）：`prompts` 块数 < `segments` / `prompt_target` 指向不存在的节点 /
   开了 `auto_concat` 却无视频落盘节点 —— 三样都只在"跑到第 N 段"才暴露（`check_chain_prompts`）。
错位必然留下硬伤。**不要**拿 ``widgets_values_named`` 当槽位映射依据 ——
它是第三方 UI 扩展写的，某些节点类型下本身就是坏的。

【用法】
    python check_ui_workflow.py --all
    python check_ui_workflow.py path/to/workflow.json [更多路径...]
    python check_ui_workflow.py --all --comfyui /path/to/ComfyUI

【schema 从哪来（2026-09-19 改：**本包节点用本地定义**）】
  * **本包的 8 个节点** → 现读包内 ``nodes.py`` 的 ``INPUT_TYPES()``（**本地定义优先**）；
  * **其余节点**（官方 / 第三方）→ 服务端 ``/object_info``。

🔴 为什么必须这样：只看服务端的话，**一个还没重启的后端会让本体检器拿旧 schema 去判新文件**
——轻则误报，重则「自证式假绿」（0.5.0 复查就是栽在这类同源缺陷上：生成器与体检器
双双用着同一份过期/有缺陷的白名单，两边一起错、对比永远"通过"）。
本包节点本来就跑在这份代码里 ⇒ **代码才是权威**；服务端只用来取非本包节点的 schema。
另外会把「服务端定义 vs 本地定义」的差异直接报出来（提示该重启后端了）。
服务端连不上时**不致命**：本包节点照样用本地定义校验，非本包节点那部分会明确标注「未查」。
"""
from __future__ import annotations

import argparse
import glob
import importlib.util
import io
import json
import os
import sys
import types
import urllib.request

DEFAULT_API = "http://127.0.0.1:8188"
WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"}

# 前端追加在**末尾**、不进 INPUT_TYPES 的 widget（末尾追加不会造成错位）
# 已知会被前端注入在 widgets_values **尾部**的槽位名。
# ⚠ 这只是「已知名」，不是判据本身：2026-09-20 起改按性质判（尾部名字不在后端 schema 里
#    ⇒ 前端自定义/DOM widget，值合法，只记 warn）。见 check_one 里的注释与实证两例。
TAIL_INJECTED = {"upload", "lora面板", "视频上传", "音频上传", "image_upload"}

# ── A/V 同步接线自检用的类型集合（2026-09-21）──────────────────────────────
#   落盘类：带 `audio` 输入的成片落盘节点（官方 CreateVideo/SaveVideo 与常见的第三方合并器）
SAVE_LIKE = {"CreateVideo", "SaveVideo", "SaveWEBM", "SaveAnimatedWEBP",
             "VHS_VideoCombine", "banzhangVideoCombine"}
#   原始音频源：**未裁** —— 它们的音频长度 = 整段 video latent 对应的全长，
#   而画面会被「裁重叠」砍掉头部 ⇒ 直连落盘节点必然音画不同步。
RAW_AUDIO_SRC = {"VAEDecodeAudio", "LoadAudio", "VHS_LoadAudio", "LoadAudioUpload",
                 "AudioUpload", "LoadAudioFromPath"}


def guess_comfyui_root() -> str:
    """本脚本在 <ComfyUI>/custom_nodes/<pkg>/tools/ 下时，往上三级就是 ComfyUI 根。"""
    here = os.path.dirname(os.path.abspath(__file__))
    for up in (3, 2, 1):
        cand = here
        for _ in range(up):
            cand = os.path.dirname(cand)
        if os.path.isdir(os.path.join(cand, "comfy")) and os.path.isdir(os.path.join(cand, "custom_nodes")):
            return cand
    return ""


def load_object_info(api: str) -> dict:
    return json.loads(urllib.request.urlopen(f"{api}/object_info", timeout=180).read().decode("utf-8"))


def try_object_info(api: str):
    """取服务端 schema；取不到**不算致命**（还有本地定义可用）。返回 (oi or None, 错误文本)。"""
    try:
        return load_object_info(api), ""
    except Exception as e:                                    # noqa: BLE001
        return None, repr(e)


def pack_root() -> str:
    """本包根目录（本脚本在 <包>/tools/ 下）。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def local_pack_defs(comfyui_root: str = "") -> dict:
    """从**包内 nodes.py** 现读本包 8 个节点的 schema（不依赖服务端）。

    🔴 为什么（2026-09-19，与生成器同一条教训）：只看服务端的话，**一个还没重启的后端
    会让体检器拿旧 schema 去判新文件** —— 轻则误报，重则「自证式假绿」
    （0.5.0 复查抓到的两处假绿，根子都是校验器与被校验物共用同一份过期依据）。
    本包节点本来就跑在这份代码里 ⇒ **代码才是权威**；服务端只用来取非本包节点的 schema。

    取不到就返回 ``{}``（服务端仍可独立工作），不抛异常——这是**体检**工具，不该因环境缺件而中断。
    """

    kit = pack_root()
    cands = []
    if comfyui_root:
        cands.append(comfyui_root)
    if os.environ.get("COMFYUI_PATH"):
        cands.append(os.environ["COMFYUI_PATH"])
    d = kit
    for _ in range(4):                # 装在 <ComfyUI>/custom_nodes/<本包>/ 时往上找 folder_paths.py
        d = os.path.dirname(d)
        cands.append(d)
    for c in cands:
        if c and os.path.isfile(os.path.join(c, "folder_paths.py")):
            if c not in sys.path:
                sys.path.insert(0, c)
            break
    else:
        return {}
    try:
        pkg = types.ModuleType("h3latentrelay_local_check")
        pkg.__path__ = [kit]
        sys.modules["h3latentrelay_local_check"] = pkg
        spec = importlib.util.spec_from_file_location("h3latentrelay_local_check.nodes",
                                                      os.path.join(kit, "nodes.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["h3latentrelay_local_check.nodes"] = mod
        spec.loader.exec_module(mod)
    except Exception as e:                                    # noqa: BLE001
        print("  ⚠ 读不到本包本地定义（%r）⇒ 本包节点将退回服务端 schema。" % (e,))
        return {}
    out = {}
    for name, cls in mod.NODE_CLASS_MAPPINGS.items():
        it = cls.INPUT_TYPES()
        out[name] = {
            "input": {"required": dict(it.get("required") or {}),
                      "optional": dict(it.get("optional") or {})},
            "output": list(cls.RETURN_TYPES),
            "output_name": list(getattr(cls, "RETURN_NAMES", cls.RETURN_TYPES)),
        }
    return out


def merge_defs(oi: dict, local: dict) -> list:
    """本包节点用**本地**定义覆盖服务端，并报出服务端是否过期（过期 = 该重启后端了）。"""
    stale = []
    for name, d in local.items():
        srv = oi.get(name)
        if srv is None:
            stale.append("%s（服务端没有）" % name)
        elif (list(srv.get("output") or []) != d["output"]
              or set((srv.get("input") or {}).get("optional") or {}) != set(d["input"]["optional"])):
            stale.append("%s（服务端定义过期）" % name)
    oi.update(local)
    return stale


def _ty(spec):
    # 同时接受 list（服务端 /object_info 的 JSON）与 tuple（本地 INPUT_TYPES()）
    if not isinstance(spec, (list, tuple)) or not spec:
        return None
    return "COMBO" if isinstance(spec[0], (list, tuple)) else spec[0]


def combo_default_key(spec, preferred=None):
    """取 COMBO / 动态 COMBO 的默认候选项 key（两种 options 声明都覆盖）。"""
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
    """动态 COMBO 选中 ``key`` 后派生的子 widget：(子参数名, 子spec) 列表（req→opt）。"""
    t0 = spec[0] if isinstance(spec, (list, tuple)) and spec else None
    extra = spec[1] if isinstance(spec, (list, tuple)) and len(spec) > 1 and isinstance(spec[1], dict) else {}
    if t0 not in ("COMBO", "COMFY_DYNAMICCOMBO_V3"):
        return []
    opt = next((o for o in (extra.get("options") or []) if o.get("key") == key), None)
    if not opt:
        return []
    subs = []
    for sec in ("required", "optional"):
        for sk, sv in ((opt.get("inputs") or {}).get(sec) or {}).items():
            subs.append((sk, sv))
    return subs



def spec_of(defn, name):
    for sec in ("required", "optional"):
        if name in (defn.get("input", {}).get(sec) or {}):
            return defn["input"][sec][name]
    return None


def frontend_slots(defn) -> list:
    """推算前端实际槽位顺序（含 control_after_generate 注入与动态 COMBO 子参数）。

    🔴 2026-09-19 修复：原实现只认 ``WIDGET_TYPES``，把 ``COMFY_DYNAMICCOMBO_V3``
    （动态 COMBO，如 SaveVideo 的 ``format``/``codec``）当普通连线 ⇒ 漏掉
    ``format``/``format.codec``/``codec`` 三格，把正确文件反判成「widgets_values 多 3 项」。
    必须与 ``examples/make_minimal_workflow.py`` 的 ``iter_widget_inputs`` 用同一套展开算法。
    """
    out = []
    for sec in ("required", "optional"):
        for name, spec in (defn.get("input", {}).get(sec) or {}).items():
            t = _ty(spec)
            if t in WIDGET_TYPES:
                out.append(name)
                extra = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
                if extra.get("control_after_generate"):
                    out.append("control_after_generate")
            elif t == "COMFY_DYNAMICCOMBO_V3":
                out.append(name)
                for sub, _ in dynamic_subwidgets(spec, combo_default_key(spec)):
                    out.append("%s.%s" % (name, sub))
    return out


def widget_input_names(defn) -> list:
    """schema 里**是 widget** 的输入名（不含 control_after_generate 伪槽）。"""
    return [k for k in frontend_slots(defn) if k != "control_after_generate"]


def check_widget_markers(n, defn) -> list:
    """🔴 2026-09-19 新增：输入槽的 `widget` 标记。

    前端把**没有 `widget` 标记**的输入当普通插槽渲染成**空圆点**：
    ``renderer/extensions/vueNodes/utils/nodeDataUtils.ts`` 的 ``nonWidgetedInputs()``
    就是 ``inputs.filter(i => !('widget' in i && i.widget))``；
    而 ``extensions/core/widgetInputs.ts`` 的 ``onGraphConfigured`` **只删不补**——
    它只清理「带标记但找不到同名 widget」的输入，绝不会给缺标记的输入补上标记。

    ⇒ 缺标记的后果：每个 widget 在画布上多一个空插槽（Post 16 个 / TrimAV 22 个），
      **文件能开、能跑、不报错**，但节点一眼就是坏的。

    历史（2026-09-19）：``examples/make_minimal_workflow.py`` 的 ``_ty()`` 只认 list，
    而「本地定义优先」走的是 ``nodes.py`` 的 ``INPUT_TYPES()``（tuple）⇒
    本包全部节点退化成 ``type="*"`` 且丢标记，生成的示例图正是这个样子。
    """
    want = widget_input_names(defn)
    ins = n.get("inputs") or []
    got = [i.get("name") for i in ins
           if isinstance(i, dict) and i.get("widget")]
    if got[:len(want)] == want:
        return []
    return ["输入槽的 widget 标记与 schema 对不上：文件里是 %s，schema 推导是 %s"
            "（缺标记的会被前端渲染成空插槽）" % (got or "（一个都没有）", want)]


def check_value(defn, name, value):
    """返回 (问题描述, 是否硬错误)。

    COMBO 的候选项常是**动态文件列表**（input/ 下的图/视频、loras 目录），
    此刻不在列表 ≠ 非法，故只作提示；**类型不对**才是错位的特征，作硬错误。
    """
    spec = spec_of(defn, name)
    if spec is None:
        return "", False
    t = _ty(spec)
    extra = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
    if t == "COMBO":
        if not isinstance(value, str):
            return "%s=%r 不是合法 COMBO 值（期望字符串）" % (name, value), True
        opts = spec[0] if isinstance(spec[0], (list, tuple)) else extra.get("options")
        if opts and value not in opts:
            return "%s=%r 不在当前候选项内（动态文件列表？请确认文件在）" % (name, value), False
        return "", False
    if t == "INT" and (not isinstance(value, int) or isinstance(value, bool)):
        return "%s 期望 INT 收到 %r" % (name, value), True
    if t == "FLOAT" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        return "%s 期望 FLOAT 收到 %r" % (name, value), True
    if t == "STRING" and not isinstance(value, str):
        return "%s 期望 STRING 收到 %r" % (name, value), True
    if t == "BOOLEAN" and not isinstance(value, bool):
        return "%s 期望 BOOLEAN 收到 %r" % (name, value), True
    if t in ("INT", "FLOAT") and isinstance(value, (int, float)) and not isinstance(value, bool):
        if extra.get("min") is not None and value < extra["min"]:
            return "%s=%r < min %s" % (name, value, extra["min"]), True
        if extra.get("max") is not None and value > extra["max"]:
            return "%s=%r > max %s" % (name, value, extra["max"]), True
    return "", False


def check_av_link(wf):
    """A/V 同步接线自检（2026-09-21）：落盘节点的 `audio` 必须是**裁后**音频。

    【为什么必须由**工具**来查，节点查不了】
    `H3RelayTrimAV` 只能发现「我的 `audio` **输入**没接」（会打警告），
    **发现不了「我的 `audio` 输出被悬空」** —— ComfyUI 不会告诉节点"我的输出有没有被消费"。
    于是最常见的错法是：`VAEDecodeAudio → CreateVideo.audio`（= **未裁**的原始音频），
    而 `裁重叠 [1] audio` 那根线悬空 ⇒ **画面裁了、音频没裁** ⇒ 每缝差 ~0.9s、**逐段累积**
    （第 3 段起口型明显对不上）。用户按接线图最自然的手就会这么接。

    【判据】
    图里存在**续接桥**（`H3RelayCopyBridge` ⇒ stage≥1 时 `trim_frames` 非 0）时：
      · 落盘节点的 `audio` 上游 = 原始音频源（`VAEDecodeAudio` / LoadAudio 家族） ⇒ **报错**；
      · 上游 = `裁重叠 [1]` 或 `音频缝 [0]` ⇒ 通过（这两条都是长度守恒的裁后音频）。
    没有桥（单段图）时两者都不裁 ⇒ **不报**（避免误伤）。
    """
    nodes = {n["id"]: n for n in (wf.get("nodes") or []) if n.get("mode") != 4}
    src = {}
    for L in (wf.get("links") or []):
        if isinstance(L, list) and len(L) >= 3:
            src[L[0]] = (L[1], L[2])            # link_id -> (origin_id, origin_slot)
    has_bridge = any(n.get("type") in ("H3RelayCopyBridge", "H3RelayMotionContext")
                     for n in nodes.values())
    if not has_bridge:
        return []
    out = []
    for n in nodes.values():
        if n.get("type") not in SAVE_LIKE:
            continue
        tgt = None
        for i in (n.get("inputs") or []):
            if i.get("name") == "audio":
                tgt = i.get("link")
        if tgt is None:
            continue                            # 没接音频（无声片）不算错
        o = src.get(tgt)
        if not o:
            continue
        ot = (nodes.get(o[0]) or {}).get("type")
        if ot in RAW_AUDIO_SRC:
            out.append("node %s %s: `audio` 接的是 **%s**（未裁的原始音频），"
                       "而图里有续接桥 ⇒ 画面裁了音频没裁 ⇒ 音画不同步（每缝 ~0.9s、逐段累积）。"
                       "改接「裁重叠」的 `[1] audio`（若用了音频缝，则接它的 `[0] audio`）"
                       % (n["id"], n.get("type"), ot))
        elif ot == "H3RelayTrimAV" and o[1] != 1:
            out.append("node %s %s: `audio` 接的是「裁重叠」的第 %s 路 —— 那不是音频"
                       "（第 1 路才是 `audio`）" % (n["id"], n.get("type"), o[1]))
    return out


# ── Chain 词分发/拼接自检（2026-09-22，0.6.7）──────────────────────────────
#   视频**落盘**类：`auto_concat` 要靠它们的回显取回每段文件 ⇒ 图里一个都没有就拼不成
VIDEO_SAVE_LIKE = ("SaveVideo", "SaveWEBM", "SaveAnimatedWEBP", "VHS_VideoCombine",
                   "banzhangVideoCombine")


def prompt_blocks(text):
    """按「单独一行、三个及以上短横线」分块（与前端 `splitPromptBlocks` 同一口径）。"""
    blocks, cur = [], []
    for line in str(text or "").splitlines():
        if len(line.strip()) >= 3 and set(line.strip()) == {"-"}:
            blocks.append("\n".join(cur))
            cur = []
        else:
            cur.append(line)
    blocks.append("\n".join(cur))
    return [b.strip() for b in blocks if b.strip()]


def check_chain_prompts(wf, oi):
    """Chain 词分发 / 自动拼接自检（0.6.7）。

    这三样错法**都只在"跑到第 N 段"或"连跑结束时"才暴露**，节点自己看不见（那几个格子只给前端读）：
      · `prompts` 块数 < `segments` ⇒ 跑到第 k 段拒绝排队；
      · `prompt_target` 写了具体节点 id、但该 id 不在这张图里；
      · 开了 `auto_concat`，图里却没有视频落盘节点（拼的时候一段都找不到）。
    """
    nodes = wf.get("nodes") or []
    defn = (oi or {}).get("H3RelayChain")
    chains = [n for n in nodes if n.get("type") == "H3RelayChain" and n.get("mode") != 4
              and isinstance(n.get("widgets_values"), list)]
    if not chains or not defn:
        return []
    slots = frontend_slots(defn)
    has_save = any(any(t in str(n.get("type") or "") for t in VIDEO_SAVE_LIKE) for n in nodes)
    out = []
    for n in chains:
        v = dict(zip(slots, n["widgets_values"]))
        seg = v.get("segments")
        seg = int(seg) if isinstance(seg, (int, float)) and not isinstance(seg, bool) else 0
        blocks = prompt_blocks(v.get("prompts"))
        if blocks and seg > 0 and len(blocks) < seg:
            out.append("node %s H3RelayChain: `prompts` 只有 %d 块词、`segments`=%d ⇒ 跑到第 %d 段会"
                       "**拒绝排队**（补齐第 %d 块，或把 segments 改成 ≤ %d）"
                       % (n["id"], len(blocks), seg, len(blocks) + 1, len(blocks) + 1, len(blocks)))
        tgt = str(v.get("prompt_target") or "").strip()
        if tgt and not any(str(x.get("id")) == tgt.split(".")[0].strip() for x in nodes):
            out.append("node %s H3RelayChain: `prompt_target=%s` 指向的节点不在这张图上 "
                       "⇒ 每段都会报错、不会排队" % (n["id"], tgt))
        if v.get("auto_concat") and not has_save:
            out.append("node %s H3RelayChain: 开了 `auto_concat`，但图里没有视频落盘节点"
                       "（SaveVideo / SaveWEBM / VHS_VideoCombine…）⇒ 拼接时一段都找不到" % n["id"])
    return out


def check_file(path: str, oi: dict, verbose: bool = True) -> int:
    name = os.path.basename(path)
    try:
        wf = json.load(io.open(path, encoding="utf-8"))
    except Exception as e:
        print("  [ERROR] %s 解析失败：%s" % (name, e))
        return 1
    if not isinstance(wf, dict) or "nodes" not in wf:
        print("  [SKIP]  %s 不是 UI 格式工作流（缺 nodes）" % name)
        return 0

    problems, warns = [], []
    problems.extend(check_av_link(wf))
    problems.extend(check_chain_prompts(wf, oi))
    for n in wf.get("nodes") or []:
        if n.get("mode") == 4:      # bypass
            continue
        defn = oi.get(n.get("type"))
        if defn is None:
            warns.append("node %s 类型 %s 本地与服务端都没有（本包只提供自己的 8 个节点；"
                         "这类多半是前端虚拟节点或未装该包）"
                         % (n["id"], n.get("type")))
            continue
        # 🔴 标记检查必须在 widgets_values 的 null 判定**之前**做：
        #   原实现在 widgets_values 非 list 时整节点 continue ⇒ 这类问题全被漏掉（假绿）。
        for msg in check_widget_markers(n, defn):
            problems.append("node %s %s: %s" % (n["id"], n["type"], msg))

        exp = frontend_slots(defn)
        wv = n.get("widgets_values")
        if not isinstance(wv, list):
            if exp:
                warns.append("node %s %s: 有 %d 个 widget 槽位，但 widgets_values 不是数组"
                             "（前端会全部回落到默认值）"
                             % (n["id"], n["type"], len(exp)))
            continue
        for k, v in zip(exp, wv):
            msg, hard = check_value(defn, k, v)
            if msg:
                (problems if hard else warns).append("node %s %s: %s" % (n["id"], n["type"], msg))

        if len(wv) > len(exp):
            nm = n.get("widgets_values_named")
            nk = list(nm.keys()) if isinstance(nm, dict) else []
            tail = nk[len(exp):] if nk else []
            if not tail or any(t not in TAIL_INJECTED for t in tail):
                # 🔴 2026-09-20 修正：硬编码白名单拦不住下一个第三方包，改按**性质**判。
                #    前端 addDOMWidget 注册的自定义 widget（后端 schema 里没有同名输入）
                #    会合法地把值追加在 widgets_values 尾部 —— 不是「多写了」。
                #    实证：ComfyUI-Banzhang-All 的 `guhai_ig`（js/banzhang_ignore_groups.js）
                #    与 `painter_preview`（js/painter_video.js）都被本判据误报过。
                #    ⚠ 仍旧报 problem 的情形（杀伤力必须保留）：
                #      · 尾部在 widgets_values_named 里**没有名字**（对不上号）；
                #      · 或尾部的名字**能在后端 schema 的 input 里找到**（那是真重复写了一格）。
                backend = set(defn.get("input", {}).get("required", {}) or {}) \
                    | set(defn.get("input", {}).get("optional", {}) or {})
                dom_extra = bool(tail) and all(
                    (t in TAIL_INJECTED) or (t not in backend) for t in tail)
                if dom_extra:
                    warns.append(
                        "node %s %s: 尾部 %d 项是**前端自定义 widget**（%s；后端 schema 无此输入，"
                        "值合法地序列化在末尾）——非多写，无需处理"
                        % (n["id"], n["type"], len(wv) - len(exp), ", ".join(tail)))
                else:
                    problems.append(
                        "node %s %s: widgets_values 比前端槽位多 %d 项（尾部 %s）—— "
                        "若不是 upload/DOM 面板，就是多写了"
                        % (n["id"], n["type"], len(wv) - len(exp), tail or "未知"))

        # 🔴 2026-09-19 补：**少写**方向的盲点。
        #   原实现只查「多了」+「逐位取值」，`widgets_values` 比槽位**短**时一路静默放行
        #   ——而 0.5.0 那次事故（SaveVideo 只写 1 格、实际要 4 格）正是这一类的镜像。
        #   判据要先扣掉**被转成输入口的 widget**（连线后值走 link、序列化里不再占槽），
        #   否则会把正常图误判成缺项。缺项一律记 **warn 而非硬错**：我们无法从文件侧
        #   100% 分辨「该有的少了」与「前端没序列化」，误杀比漏报更坏（本仓纪律）。
        _linked = set()
        for _i in (n.get("inputs") or []):
            if isinstance(_i, dict) and isinstance(_i.get("widget"), dict) \
                    and _i.get("link") is not None:
                _linked.add(_i["widget"].get("name"))
        _exp2 = [k for k in exp if k not in _linked]
        if len(wv) < len(_exp2):
            warns.append(
                "node %s %s: widgets_values 只有 %d 项、schema 推导要 %d 项"
                "（缺 %s）——若这些 widget 被连线转成了输入口则正常；"
                "否则末位旋钮会静默回落到默认值"
                % (n["id"], n["type"], len(wv), len(_exp2),
                   "、".join(_exp2[len(wv):]) or "?"))

    if verbose or problems or warns:
        print("")
        print("  %s  —— 节点 %d / 连线 %d"
              % (name, len(wf.get("nodes") or []), len(wf.get("links") or [])))
        for p in problems:
            print("     ✗ " + p)
        for w in warns:
            print("     ⚠ " + w)
        if not problems and not warns:
            print("     ✓ 槽位与取值全部合法")
    return len(problems)


def main():
    ap = argparse.ArgumentParser(description="校验 ComfyUI UI 工作流的 widget 槽位是否错位")
    ap.add_argument("paths", nargs="*", help="工作流 JSON 路径（--all 时忽略）")
    ap.add_argument("--all", action="store_true", help="扫描 ComfyUI 的 workflows 目录")
    ap.add_argument("--api", default=DEFAULT_API, help="ComfyUI 地址（默认 %s）" % DEFAULT_API)
    ap.add_argument("--comfyui", default=os.environ.get("COMFYUI_PATH", ""),
                    help="ComfyUI 根目录（默认按本脚本位置上溯，或环境变量 COMFYUI_PATH）")
    ap.add_argument("--quiet", action="store_true", help="只打印有问题的")
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    root = a.comfyui or guess_comfyui_root()
    wf_dir = os.path.join(root, "user", "default", "workflows") if root else ""

    local = local_pack_defs(root)                 # 本包 8 个节点：代码才是权威
    oi_srv, srv_err = try_object_info(a.api.rstrip("/"))

    print("=" * 100)
    print("ComfyUI UI 工作流 · widget 槽位校验")
    print("  schema 来源：本包节点 = 本地 nodes.py（%d 个）；其余节点 = %s/object_info"
          % (len(local), a.api.rstrip("/")))
    print("  工作流目录：%s" % (wf_dir or "(未定位，请用 --all 时指定 --comfyui)"))
    print("=" * 100)

    if oi_srv is None and not local:
        print("\n[FAIL] 服务端取不到 object_info（%s），也读不到本包本地定义。" % srv_err)
        print("       请先启动 ComfyUI，或用 --api 指定地址。")
        return 2

    oi = dict(oi_srv or {})
    if oi_srv is None:
        print("\n⚠ 服务端不可达（%s）——**非本包节点这一部分未查**；"
              "本包节点仍按本地定义校验。" % srv_err)
    if local:
        _stale = merge_defs(oi, local)
        if _stale:
            print("⚠ 服务端加载的本包节点与代码不一致：%s" % "；".join(_stale))
            print("   → 现在校验用的是**代码里的新定义**（正确）；要让 ComfyUI 真跑起来需重启后端。")

    if a.all:
        if not wf_dir or not os.path.isdir(wf_dir):
            print("\n[FAIL] 找不到 workflows 目录。请用 --comfyui 指定 ComfyUI 根目录。")
            return 2
        paths = sorted(glob.glob(os.path.join(wf_dir, "*.json")))
    else:
        paths = a.paths
    if not paths:
        print("\n没给路径。用 --all 扫全目录，或直接给文件路径。")
        return 2

    total = bad = 0
    for p in paths:
        n = check_file(p, oi, verbose=not a.quiet)
        total += n
        bad += 1 if n else 0

    print("")
    print("=" * 100)
    print("文件 %d 个，有问题 %d 个，问题合计 %d 条" % (len(paths), bad, total))
    print("=" * 100)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
