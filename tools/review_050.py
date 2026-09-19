# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Relay-Kit contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
r"""审查脚本：确认 0.5.0 改造全部生效、无遗漏、无回归。

逐项核（对照历史错误）：
  A. 节点清单三处一致（NODE_CLASS_MAPPINGS / __init__.py 头注释 / README 节点表）
  B. 返回契约：每个节点的 RETURN_TYPES 长度 == 所有 return 语句的路数
  C. widget 追加位置：受存量 UI 工作流约束的节点（TrimAV / MotionContext）新 widget 必须在末位
  D. 默认全关：所有新件默认值必须为 0/False/off
  E. mask_mode 三处一致（relay_core.MASK_MODES / 节点选项 / 权重函数存在）
  F. 新函数可调用（import 不报、签名匹配）
  G. 历史错误对照（v0.4.2 的 17 项是否被我的改动破坏）

用法：COMFYUI_PATH=/path/to/ComfyUI python tools/review_050.py
（装在 `<ComfyUI>/custom_nodes/<本包>/` 下时可省略 COMFYUI_PATH，自动上溯定位）
"""
import json
import math
import os
import re
import sys

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 不写死本机路径：本包通常装在 <ComfyUI>/custom_nodes/<本包>/ ⇒ 往上两级即 ComfyUI 根；
# 装在别处时用 COMFYUI_PATH 显式指定（与 tests/test_relay_core.py 同一约定）。
COMFY = os.environ.get("COMFYUI_PATH") or os.path.dirname(os.path.dirname(KIT))
sys.path.insert(0, COMFY)
sys.path.insert(0, KIT)

OK, BAD = [], []


def ck(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print("  [%s] %s%s" % ("OK" if cond else "FAIL", name, ("  " + detail) if detail else ""))


import importlib.util  # noqa: E402
import types  # noqa: E402

# 目录名含连字符，不能直接当包名 → 伪造一个包壳再按文件加载 nodes.py（与单测同法）
_pkg = types.ModuleType("h3relay_kit")
_pkg.__path__ = [KIT]
sys.modules["h3relay_kit"] = _pkg
_spec = importlib.util.spec_from_file_location("h3relay_kit.nodes",
                                              os.path.join(KIT, "nodes.py"))
N = importlib.util.module_from_spec(_spec)
sys.modules["h3relay_kit.nodes"] = N
_spec.loader.exec_module(N)

import relay_core as CORE  # noqa: E402

print("=" * 78)
print("A. 节点清单三处一致")
print("=" * 78)
_reg = list(N.NODE_CLASS_MAPPINGS)
_disp = list(N.NODE_DISPLAY_NAME_MAPPINGS)
ck("A1 注册表与显示名一一对应", sorted(_reg) == sorted(_disp),
   "reg=%s" % _reg)

_init = open(os.path.join(KIT, "__init__.py"), encoding="utf-8").read()
_missing = [n for n in _reg if n not in _init]
ck("A2 __init__.py 头注释/清单含全部节点", not _missing, "缺=%s" % _missing)

_readme = open(os.path.join(KIT, "README.md"), encoding="utf-8").read()
# ⚠ README 的「节点」表用**显示名**，且常写成 `🔗 **H3 续接裁重叠**`（加粗把字符串拆开）
#   ⇒ 必须**去掉 markdown 标记**再比，否则是假阳性。
_readme_flat = _readme.replace("*", "").replace("`", "")
_miss_rm = [n for n in _reg if N.NODE_DISPLAY_NAME_MAPPINGS[n] not in _readme_flat]
ck("A3 README 节点表含全部节点（去 markdown 后按显示名查）", not _miss_rm, "缺=%s" % _miss_rm)

print()
print("=" * 78)
print("B. 返回契约：RETURN_TYPES 长度 == 所有 return 的路数")
print("=" * 78)
_src = open(os.path.join(KIT, "nodes.py"), encoding="utf-8").read()
for cls in _reg:
    body = re.search(r"\nclass %s\b.*?(?=\nclass |\Z)" % cls, _src, re.S)
    if not body:
        ck("B.%s 找到类体" % cls, False)
        continue
    b = body.group(0)
    n_ret = len(re.findall(r"RETURN_TYPES\s*=\s*\(([^)]*)\)", b)[0].split(",")) if \
        re.search(r"RETURN_TYPES\s*=\s*\(([^)]*)\)", b) else -1
    # 收集所有 `return (...)` 的顶层路数（按逗号数，粗但够用；排除嵌套调用）
    bad = []
    for m in re.finditer(r"\n\s+return \(([^()]*)\)", b):
        inner = m.group(1)
        cnt = len([x for x in inner.split(",") if x.strip()])
        if cnt != n_ret:
            bad.append((cnt, inner.strip()[:60]))
    ck("B.%s 返回路数 == RETURN_TYPES(%d)" % (cls, n_ret), not bad, "异常=%s" % bad)

print()
print("=" * 78)
print("C. widget 追加位置（受存量 UI 工作流约束的节点）")
print("=" * 78)
_trim = list(N.H3RelayTrimAV.INPUT_TYPES()["optional"])
ck("C1 TrimAV：core 项前缀原序保留",
   _trim[:5] == ["audio", "settle_frames", "seam_ghost", "seam_ghost_alpha",
                 "settle_sharpen"],
   "前 5=%s" % _trim[:5])
ck("C2 TrimAV：新件 match_prev* 在**末位**",
   _trim[-4:] == ["match_prev", "match_prev_frames", "match_prev_gain_max",
                  "match_prev_offset_max"],
   "末 4=%s" % _trim[-4:])
ck("C3 TrimAV：新增第 4 路输出 prev_tail 在末位",
   N.H3RelayTrimAV.RETURN_NAMES[:3] == ("images", "audio", "report")
   and N.H3RelayTrimAV.RETURN_NAMES[3] == "prev_tail",
   "%s" % (N.H3RelayTrimAV.RETURN_NAMES,))

_mc = list(N.H3RelayMotionContext.INPUT_TYPES()["optional"])
# ⚠ 第一版写成 `... or True` ⇒ **恒真**，等于没查（假绿）。现按真实判据：anchor_* 必须**追加在末位**。
ck("C4 MotionContext：anchor_* 在末位", _mc[-3:] == ["anchor_latent", "anchor_stage",
                                                    "anchor_frames"],
   "末 3=%s" % _mc[-3:])

_cb = list(N.H3RelayCopyBridge.INPUT_TYPES()["optional"])
ck("C5 CopyBridge：blend_* 在末位",
   _cb[-3:] == ["blend_top", "blend_tokens", "blend_shape"], "末 3=%s" % _cb[-3:])

print()
print("=" * 78)
print("D. 默认全关（不接线时行为不变）")
print("=" * 78)
_off_cb = {"blend_top": 0.5}          # blend_top 是「缝端上限」，其自身非 0 但**模式未选则不生效**
# ⚠ 第一版把「所有默认值必须为 0」当判据 → **假阳性**：下面这些是**既有**且**只在特定模式下生效**
#   的默认值，本来就该非 0（taper_tokens/seam_min 仅 taper 模式；ramp_top 仅 ramp 模式；
#   pin_audio=True 是正确默认；anchor_blend=1.0 在未接 anchor_latent 时无效）。
#   真正的判据是：**新增件在默认 mask_mode 下不生效** ⇒ 只查新件的默认是否"关"。
_LEGACY_NEUTRAL = {"taper_tokens", "seam_min", "pin_audio", "ramp_top", "ramp_tokens",
                   "anchor_latent", "anchor_blend", "context_frames", "audio_frames"}
_ok_d = True
for k, v in N.H3RelayCopyBridge.INPUT_TYPES()["optional"].items():
    if not isinstance(v, tuple) or not isinstance(v[1], dict) or "default" not in v[1]:
        continue
    if k in _LEGACY_NEUTRAL or k.startswith("blend_"):
        continue                      # 既有项 / 仅 blend 模式生效
    if v[1]["default"] not in (0, 0.0, False, "hard", "smoothstep"):
        _ok_d = False
        print("      · %s 默认=%r" % (k, v[1]["default"]))
ck("D1 CopyBridge：**新件**在默认模式下不生效（既有项豁免）", _ok_d)

_itp = N.H3RelayPost.INPUT_TYPES()["optional"]
_off = all(_itp[k][1]["default"] == 0.0 for k in
           ("match_prev", "lowfreq_pull", "hist_match", "wb_match",
            "deconv_strength", "detail_borrow", "settle_sharpen"))
ck("D2 H3RelayPost 全部作用项默认 0", _off)
# ⚠ 第一版写成 `torch.equal(A, B) or True` 且两侧是**两次独立的 rand** ⇒ 恒真且必不相等，纯假绿。
#   真实判据：**同一份输入**过一遍后处理节点，默认档下必须**逐位相同**（不是"差不多"）。
_imp_t = __import__("torch")
_x = _imp_t.rand(6, 8, 8, 3)
_y = N.H3RelayPost().apply(_x)[0]
ck("D3 H3RelayPost 默认直通（逐位不变）", _imp_t.equal(_y, _x),
   "max|Δ|=%r" % (float((_y - _x).abs().max()),))

print()
print("=" * 78)
print("E. mask_mode 三处一致")
print("=" * 78)
ck("E1 relay_core.MASK_MODES", CORE.MASK_MODES == ("hard", "taper", "ramp", "blend"),
   "%s" % (CORE.MASK_MODES,))
ck("E2 节点选项与 MASK_MODES 一致",
   list(N.H3RelayCopyBridge.INPUT_TYPES()["optional"]["mask_mode"][0]) == list(CORE.MASK_MODES),
   "%s" % (N.H3RelayCopyBridge.INPUT_TYPES()["optional"]["mask_mode"][0],))
ck("E3 四个权重函数齐备",
   all(hasattr(CORE, f) for f in ("prefix_taper_weights", "prefix_ramp_weights",
                                  "prefix_blend_weights", "_window_shape")))

print()
print("=" * 78)
print("F. 新函数可调用 + 边界安全")
print("=" * 78)
import torch  # noqa: E402
ck("F1 scan_head_repeat 存在且签名对",
   len(CORE.scan_head_repeat(torch.rand(40, 8, 8, 3), 22)) == 3)
ck("F2 match_prev_stats 存在且签名对",
   CORE.match_prev_stats(torch.rand(6, 8, 8, 3), torch.rand(1, 8, 8, 3), 6, 0.5).shape[0] == 6)
ck("F3 prefix_blend_weights 边界（n=0/1）",
   CORE.prefix_blend_weights(0) == () and len(CORE.prefix_blend_weights(1)) == 1)
ck("F4 SETTLE_REPEAT_PATH 默认关闭",
   CORE.SETTLE_REPEAT_PATH is False, "%r" % (CORE.SETTLE_REPEAT_PATH,))

print()
print("=" * 78)
print("G. 历史错误对照（v0.4.2 的 17 项是否被破坏）")
print("=" * 78)
ck("G1 契约缓存降级不缓存（#9 仍在）",
   "not cache" in open(os.path.join(KIT, "layout_contract.py"), encoding="utf-8").read()
   or "不写" in open(os.path.join(KIT, "layout_contract.py"), encoding="utf-8").read())
ck("G2 TrimAV fps 服务端校验（#10 仍在）", "fps" in _src and "有限正数" in _src)
ck("G3 noise_mask 与 latent 同设备（#8 仍在）",
   "device=tv.device" in open(os.path.join(KIT, "relay_core.py"), encoding="utf-8").read())
ck("G4 原子写（#12 仍在）",
   "os.replace" in open(os.path.join(KIT, "relay_core.py"), encoding="utf-8").read())
ck("G5 run_id 清洗（#11 仍在）", "_sanitize" in _src or "非法字符" in _src)

print()
print("=" * 78)
print("H. 文档同步清单（v0.4.2 计划「四、文档修正」列的六项，逐项核）")
print("=" * 78)
import re as _re  # noqa: E402

# H1 版本号三处一致
_v_init = _re.search(r'__version__\s*=\s*"([^"]+)"',
                     open(os.path.join(KIT, "__init__.py"), encoding="utf-8").read())
_v_py = _re.search(r'^version\s*=\s*"([^"]+)"',
                   open(os.path.join(KIT, "pyproject.toml"), encoding="utf-8").read(), _re.M)
_v_ch = _re.search(r"^## ([\d.]+)", open(os.path.join(KIT, "CHANGES.md"),
                                         encoding="utf-8").read(), _re.M)
print("      __init__=%s pyproject=%s CHANGES 顶=%s"
      % (_v_init.group(1) if _v_init else "?",
         _v_py.group(1) if _v_py else "?",
         _v_ch.group(1) if _v_ch else "?"))
ck("H1 版本号三处一致（含 CHANGES 顶部条目）",
   _v_init and _v_py and _v_ch
   and _v_init.group(1) == _v_py.group(1) == _v_ch.group(1))

# H2 __init__.py 头注释节点清单齐备
_miss_init = [n for n in _reg if n not in _init]
ck("H2 __init__.py 头注释列出全部节点（含 H3RelayPost）", not _miss_init,
   "缺=%s" % _miss_init)

# H3 CONTRIBUTING / README 的「方面数 / 断言数」与**实跑**一致
# ⚠ 第一版只查「有没有写数字」，不查「数字对不对」——而 v0.4.2 踩的正是「清单不同步」。
#   现在改成：**实跑一次单测**取真值（零 GPU、秒级），再拿三份文档逐一对账。
_CN = "零一二三四五六七八九"


def _zh(n):
    if n < 10:
        return _CN[n]
    if n < 20:
        return "十" + (_CN[n % 10] if n % 10 else "")
    return _CN[n // 10] + "十" + (_CN[n % 10] if n % 10 else "")


import subprocess  # noqa: E402
_tst = open(os.path.join(KIT, "tests", "test_relay_core.py"), encoding="utf-8").read()
_r = subprocess.run([sys.executable, os.path.join(KIT, "tests", "test_relay_core.py")],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    env=dict(os.environ, COMFYUI_PATH=COMFY))
_m_res = _re.search(r"通过\s*(\d+)\s*/\s*失败\s*(\d+)", _r.stdout or "")
_n_assert = int(_m_res.group(1)) if _m_res else -1
# 「方面数」以 **tests 头注释的覆盖清单**为准（那正是 CONTRIBUTING/README 抄的那份清单）。
# ⚠ 不要用 `第 N 组` 去数：只有 8–12 / 18–21 组带这个标记，1–7 与 13–17 是别的写法 ⇒ 会数成 9。
_head8 = _tst[:_tst.index('"""', _tst.index('"""') + 3) + 3]
_nums = [int(x) for x in _re.findall(r"^\s*(\d+)\. ", _head8, _re.M)]
_n_ngroups = len(_nums)
print("      实跑：通过 %s（失败 %s）／tests 头注释列了 %d 个方面（%s…%s，连续=%s）"
      % (_n_assert, _m_res.group(2) if _m_res else "?", _n_ngroups,
         _nums[0] if _nums else "?", _nums[-1] if _nums else "?",
         _nums == list(range(1, _n_ngroups + 1))))
ck("H3 单测实跑全绿（通过数与失败数为 0 对齐）",
   bool(_m_res) and _m_res.group(2) == "0" and _n_assert > 0)

_con = open(os.path.join(KIT, "CONTRIBUTING.md"), encoding="utf-8").read()
_m_asp = _re.search(r"覆盖(.{1,4})个方面", _con)
_m_assert = _re.search(r"实测执行\s*\**\s*(\d+)\s*\**\s*项断言", _con)
print("      CONTRIBUTING：%s 个方面 / %s 项断言"
      % (_m_asp.group(1) if _m_asp else "?", _m_assert.group(1) if _m_assert else "?"))
ck("H3b CONTRIBUTING 的方面数/断言数 == 实跑真值",
   bool(_m_asp and _m_assert)
   and _m_asp.group(1).strip("* ") == _zh(_n_ngroups)
   and int(_m_assert.group(1)) == _n_assert,
   "期望 %s 个方面 / %d 项断言" % (_zh(_n_ngroups), _n_assert))

# README 的「离线自测」一节同样要对账（v0.5.0 复查抓到过：README 停在 171/十七个方面）
_m_r_assert = _re.search(r"\*\*(\d+) 项断言[^*]*\*\*，覆盖(.{1,4})个方面", _readme)
_m_r_group = _re.search(r"^\| 21 \|", _readme, _re.M)
print("      README：%s 项断言 / %s 个方面%s"
      % (_m_r_assert.group(1) if _m_r_assert else "?",
         _m_r_assert.group(2) if _m_r_assert else "?",
         "（含第 21 组行）" if _m_r_group else "（**缺第 21 组行**）"))
ck("H3c README 的断言数/方面数 == 实跑真值，且分组表列到 21",
   bool(_m_r_assert) and int(_m_r_assert.group(1)) == _n_assert
   and _m_r_assert.group(2) == _zh(_n_ngroups) and bool(_m_r_group),
   "期望 %d 项断言 / %s 个方面" % (_n_assert, _zh(_n_ngroups)))

# nodes.py 模块头注释的节点数（同样踩过「改了清单漏了头注释」）
_m_doc = _re.search(r'"""H3 Relay Kit · 节点层\s*\n\s*\n(.+?)\n', _src)
ck("H3d nodes.py 模块头注释的节点数 == 注册数",
   bool(_m_doc) and _zh(len(_reg)) in _m_doc.group(1),
   "头注释=%r，注册 %d 个" % (_m_doc.group(1)[:24] if _m_doc else "?", len(_reg)))

# H4 tests 头注释的覆盖清单包含最新几组
_head = _tst[:_tst.index('"""', _tst.index('"""') + 3) + 3]
_need = ["18.", "19.", "20.", "21."]
ck("H4 tests 头注释覆盖清单含 18/19/20/21 组",
   all((" %s" % n) in _head for n in _need),
   "缺=%s" % [n for n in _need if (" %s" % n) not in _head])

# H5 requirements.txt 如实
_req = open(os.path.join(KIT, "requirements.txt"), encoding="utf-8").read()
ck("H5 requirements.txt 存在且非空", bool(_req.strip()), "%d 字节" % len(_req.strip()))

# H6 CHANGES 顶部条目不是「版本号待定」
ck("H6 CHANGES 顶部条目已是正式版本号（非「待定」）",
   _v_ch and "待定" not in open(os.path.join(KIT, "CHANGES.md"),
                                encoding="utf-8").read()[:400],
   "顶=%s" % (_v_ch.group(1) if _v_ch else "?"))

print()
print("=" * 78)
print("I. UI 工作流槽位对齐（**静态版**，不依赖服务端）")
print("=" * 78)
# 铁律 10：ComfyUI 的 widgets_values 是**按位置**对槽位的；前端还会为带
# `control_after_generate` 的 widget（典型 seed）**自动插一格**。漏掉 ⇒ 其后取值整体前移。
# I 盘原工具 `check_ui_workflow.py` 要连 `/object_info`；本检查直接读 INPUT_TYPES，**离线可跑**。


def _widget_slots(cls):
    """按 INPUT_TYPES 顺序给出「占 widget 槽」的名字（连线口不占）。"""
    it = cls.INPUT_TYPES()
    out = []
    for sec in ("required", "optional"):
        for name, spec in (it.get(sec) or {}).items():
            if not isinstance(spec, tuple):
                continue
            t = spec[0]
            if isinstance(t, str) and t in ("IMAGE", "AUDIO", "LATENT", "MODEL",
                                            "CONDITIONING", "VAE", "CLIP", "VIDEO",
                                            "SAMPLER", "SIGMAS", "GUIDER"):
                continue                      # 连线口，不占 widget
            out.append(name)
            if isinstance(spec[1], dict) and spec[1].get("control_after_generate"):
                out.append("<control_after_generate>")   # 前端注入的伪槽位
    return out


_wf = json.load(open(os.path.join(KIT, "examples", "minimal_relay_official.json"),
                     encoding="utf-8"))
_unknown, _mismatch, _map = [], [], []
for n in _wf["nodes"]:
    t = n["type"]
    cls = getattr(N, t, None)
    if cls is None:
        continue                          # 非本包节点（官方 UNETLoader 等）
    slots = _widget_slots(cls)
    wv = n.get("widgets_values") or []
    # ⚠ 断言口径：ComfyUI **允许** widgets_values **短于** widget 列表（缺的用默认值补），
    #   只有**多出来**、或**中段错位**才是问题。第一版按「必须等长」判 ⇒ 假阳性。
    if len(wv) > len(slots):
        _mismatch.append((t, len(wv), len(slots), slots))
    _map.append((t, list(zip(slots, wv))))
    # 顺带做范围/候选校验：能查出"整体前移一格"这类静默错位
    for i, (name, val) in enumerate(zip(slots, wv)):
        it = cls.INPUT_TYPES()
        spec = (it.get("required") or {}).get(name) or (it.get("optional") or {}).get(name)
        if not (isinstance(spec, tuple) and isinstance(spec[1], dict)):
            continue
        cfg = spec[1]
        if isinstance(spec[0], (list, tuple)) and val not in spec[0]:
            _unknown.append("%s.%s=%r 不在候选项 %s" % (t, name, val, spec[0]))
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            lo, hi = cfg.get("min"), cfg.get("max")
            if lo is not None and val < lo:
                _unknown.append("%s.%s=%s < min %s" % (t, name, val, lo))
            if hi is not None and val > hi:
                _unknown.append("%s.%s=%s > max %s" % (t, name, val, hi))

ck("I1 示例图 widgets_values **未超出** schema 槽位数（短数组合法）", not _mismatch,
   "超长=%s" % [(a, b, c) for a, b, c, _ in _mismatch])
for _t, _pairs in _map:
    print("      %s：%s" % (_t, ", ".join("%s=%r" % kv for kv in _pairs[:5])))
ck("I2 取值都在候选/范围内（错位会在这里露出来）", not _unknown,
   "异常=%s" % _unknown[:4])

# 🔴 I3（2026-09-19 新增）：示例图的 widget 输入必须带 `widget` 标记。
#   前端 `nonWidgetedInputs()`（renderer/.../nodeDataUtils.ts）把**没有标记**的输入
#   当普通插槽渲染成**空圆点**；而 `widgetInputs.ts` 的 `onGraphConfigured` **只删不补**
#   ⇒ 缺标记 = 每个 widget 在画布上多一个空插槽（Post 16 / TrimAV 20），
#     **文件能开、能跑、不报任何错**，但节点一眼就是坏的。
#   历史事故：`examples/make_minimal_workflow.py` 的 `_ty()` 只认 list，
#   而「本地定义」的 schema 是 tuple ⇒ 本包节点全部丢标记。
_mark = []
for n in _wf["nodes"]:
    _t = n["type"]
    _cls = getattr(N, _t, None)
    if _cls is None:
        continue
    _want = [s for s in _widget_slots(_cls) if s != "<control_after_generate>"]
    _got = [i.get("name") for i in (n.get("inputs") or []) if i.get("widget")]
    if _got[:len(_want)] != _want:
        _mark.append("%s: 文件=%s 期望=%s" % (_t, _got or "（无）", _want))
ck("I3 示例图 widget 输入带 `widget` 标记（缺标记会被前端渲染成空插槽）",
   not _mark, "不符=%s" % _mark[:2])

print()
print("=" * 78)
print("J. 存量 UI 工作流的「节点定义过期」扫描（0.5.0 新增输出/新增 widget 的影响面）")
print("=" * 78)
# 为什么单列一节点：ComfyUI 前端 `LGraphNode.configure()` 是**照单全收**序列化里的
# `outputs`（litegraph/src/LGraphNode.ts：`this.outputs = cloneObject(info.outputs)`）
# ⇒ **加过输出的节点，老图打开后看不到新输出**（执行不受影响，但接不上线）。
# 这不是本包能自动修的东西（改了人家的图可能和对方手里的版本打架），故只**报出来**。
_WFDIR = os.environ.get("WORKFLOWS_DIR") or os.path.join(
    COMFY, "user", "default", "workflows")
_stale, _scanned = [], 0
if os.path.isdir(_WFDIR):
    import glob as _glob
    for _p in sorted(_glob.glob(os.path.join(_WFDIR, "*.json"))):
        try:
            _d = json.load(open(_p, encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(_d, dict) or not isinstance(_d.get("nodes"), list):
            continue
        _scanned += 1
        for _n in _d["nodes"]:
            _cls = getattr(N, str(_n.get("type")), None)
            if _cls is None:
                continue
            _no = len(_n.get("outputs") or [])
            if _no and _no != len(_cls.RETURN_TYPES):
                _stale.append("%s #%s %s：存了 %d 路输出，节点现有 %d 路（缺 %s）"
                              % (os.path.basename(_p), _n.get("id"), _n.get("type"),
                                 _no, len(_cls.RETURN_TYPES),
                                 list(_cls.RETURN_NAMES[_no:])))
print("      扫了 %d 份工作流（%s）" % (_scanned, _WFDIR))
if _stale:
    print("      ⚠ 需人工处理（重加该节点，或按 README「0.5.0 迁移注意」补 outputs）：")
    for _s in _stale:
        print("         · " + _s)
else:
    print("      未发现过期的 outputs 数组")
ck("J1 存量工作流无『输出数组过期』的节点", not _stale, "计数=%d" % len(_stale))

# J3 —— 部署地「同包第二份副本」扫描。
# 🔴 2026-09-17 实盘事故：custom_nodes 里留了 `ComfyUI-H3-Relay-Kit.bak-<日期>`（v0.4.3 旧副本，
#   带 __init__.py）⇒ ComfyUI **两份都加载**，节点定义被后加载的那份**覆盖** ⇒
#   服务端 /object_info 报的 `H3RelayTrimAV` 只有 3 路输出（没有 prev_tail），
#   而 l1_api 已经引用 `["903", 3]` ⇒ 真渲染第一段就炸。
#   ⚠️ ComfyUI 官方只忽略 **`.disabled` 结尾**（`nodes.py:2369`），`.bak` 不认。
_CNODES = os.environ.get("CUSTOM_NODES_DIR") or os.path.join(COMFY, "custom_nodes")
_shadow = []
if os.path.isdir(_CNODES):
    _self = os.path.basename(KIT)
    for _nm in sorted(os.listdir(_CNODES)):
        _p = os.path.join(_CNODES, _nm)
        if not os.path.isdir(_p) or _nm == _self:
            continue
        if not os.path.exists(os.path.join(_p, "__init__.py")):
            continue
        _nj = os.path.join(_p, "nodes.py")
        if not os.path.exists(_nj):
            continue
        try:
            _body = open(_nj, encoding="utf-8", errors="replace").read(8192)
        except Exception:
            continue
        if "H3Relay" in _body:
            _shadow.append("%s（%s，版本 %s）" % (
                _nm, "会让 ComfyUI 忽略" if _nm.endswith(".disabled") else "🔴 会覆盖节点定义",
                (re.search(r'__version__\s*=\s*"([^"]+)"',
                           open(os.path.join(_p, "__init__.py"), encoding="utf-8",
                                errors="replace").read()) or [None, "?"])[1]
                if os.path.exists(os.path.join(_p, "__init__.py")) else "?"))
print("      custom_nodes=%s" % _CNODES)
if _shadow:
    for _s in _shadow:
        print("         · " + _s)
    print("         → 处置：把非现役那份**移出 custom_nodes**（或改名以 `.disabled` 结尾）")
else:
    print("      只有一份本包副本")
ck("J3 custom_nodes 无「同包第二份副本」（副本会静默覆盖节点定义）",
   not [s for s in _shadow if "覆盖" in s], "%d 份副本" % len(_shadow))
# J2 = 纯信息：**老图里显式存下的 widget 取值会盖掉新默认**（改默认值救不了存量图）。
#   典型：settle_frames 存了 -1 ⇒ 0.5.0 的「默认不裁沉降」对这张图**不生效**；
#        trim_frames 没接线且为 0 ⇒ 这个裁节点**什么都不裁**（接缝会重播）。
_warn2 = []
if os.path.isdir(_WFDIR):
    import glob as _glob2
    for _p in sorted(_glob2.glob(os.path.join(_WFDIR, "*.json"))):
        try:
            _d = json.load(open(_p, encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(_d, dict) or not isinstance(_d.get("nodes"), list):
            continue
        for _n in _d["nodes"]:
            if _n.get("type") != "H3RelayTrimAV":
                continue
            _wv = _n.get("widgets_values") or []
            if isinstance(_wv, dict) or len(_wv) < 3:
                continue
            _linked = {i.get("name") for i in (_n.get("inputs") or []) if i.get("link") is not None}
            if _wv[2] == -1:
                _warn2.append("%s #%s：settle_frames 存了 -1（自动裁沉降）⇒ 0.5.0 的新默认 0 对这张图不生效"
                              % (os.path.basename(_p), _n.get("id")))
            if _wv[0] == 0 and "trim_frames" not in _linked:
                _warn2.append("%s #%s：trim_frames 未接线且 =0 ⇒ 这个裁节点什么都不裁（接缝会重播）"
                              % (os.path.basename(_p), _n.get("id")))
for _w in _warn2:
    print("      · " + _w)
if not _warn2:
    print("      存量图里的 TrimAV 取值无异常")

print()

print()
print("=" * 78)
print("K. 端到端行为抽查（走**节点**而不是只调 relay_core）")
print("=" * 78)
# 为什么单列：A–F 全是「结构对不对」，J 是「存量图有没有过期」，都不回答「功能真的生效了吗」。
_T = __import__("torch")
_T.manual_seed(20260917)
_tgt = {"samples": _T.rand(1, 4, 12, 2, 2)}
_prev = {"samples": _T.rand(1, 4, 12, 2, 2)}
_steps = CORE.steps_for_frames(22)
_okm = True
for _mode, _kw, _lo, _hi in (("hard", {}, 0.0, 0.0), ("blend", {"blend_top": 0.5}, 0.0, 0.5),
                             ("ramp", {"ramp_top": 0.25}, 0.0, 0.25),
                             ("taper", {}, 0.1, 1.0)):
    _o, _cov, _ = CORE.build_continue_latent(_tgt, _prev, 22, mask_mode=_mode,
                                             pin_audio=False, **_kw)
    _m = _o["noise_mask"][0, 0, :, 0, 0]
    _good = (abs(float(_m[:].min() if _mode == "taper" else _m[:_steps].min()) - _lo) < 1e-6
             and abs(float(_m[:_steps].max()) - _hi) < 1e-6
             and bool((_m[_steps:] == 1).all()))
    _okm = _okm and _good
    print("      %-6s 钉住窗 [%.3f, %.3f]，窗外全 1 → %s"
          % (_mode, float(_m[:_steps].min()), float(_m[:_steps].max()), "OK" if _good else "FAIL"))
ck("K1 四种 mask_mode 的掩码值域与窗外行为都对（22 帧 = %d 步）" % _steps, _okm)

# H3RelayPost：跨段统计匹配真的在动画面，且被护栏夹住、窗外逐位不动
_img = _T.cat([_T.rand(12, 8, 8, 3) * 0.10 + 0.25, _T.rand(20, 8, 8, 3) * 0.20 + 0.50], 0)
_guide = _T.rand(1, 8, 8, 3) * 0.20 + 0.50
_y, _ = N.H3RelayPost().apply(_img, _guide, match_prev=1.0, match_prev_frames=12,
                              cross_seg_ack=True)
_d_head = float(_y[:12].mean()) - float(_img[:12].mean())
ck("K2 match_prev 生效且方向正确（段头均值向 guide 移动）", 0.0 < _d_head <= 0.06 + 1e-6,
   "Δmean=%.4f（护栏 off_max=0.06）" % _d_head)
ck("K3 match_prev 作用窗以外逐位不变", _T.equal(_y[12:], _img[12:]),
   "帧数 %d→%d" % (int(_img.shape[0]), int(_y.shape[0])))
_flat = _T.full((12, 8, 8, 3), 0.2)
_big, _ = N.H3RelayPost().apply(_flat, _T.full((1, 8, 8, 3), 0.8),
                                match_prev=1.0, match_prev_frames=12,
                                cross_seg_ack=True)
ck("K4 偏移护栏夹得住（Δ=0.6 只允许改 0.06）",
   abs(float(_big[:12].mean()) - 0.23) < 5e-3, "0.2 → %.4f" % float(_big[:12].mean()))
_ng, _ = N.H3RelayPost().apply(_img, None, match_prev=1.0, lowfreq_pull=1.0,
                               cross_seg_ack=True)
ck("K5 未接 guide ⇒ 跨段项跳过且逐位直通", _T.equal(_ng, _img))
# 统计纠偏：拷贝前缀的均值应被拉向锚段
_anc = {"samples": _T.rand(1, 4, 12, 2, 2) * 0.05 + 0.9}
_av = float(CORE.video_from_latent(_anc).mean())
_pv = float(CORE.video_from_latent(_prev).mean())
_o2, _, _ = CORE.build_continue_latent(_tgt, _prev, 22, mask_mode="hard",
                                       anchor_latent=_anc, pin_audio=False)
_s2 = _o2["samples"]
_pre = float((_s2.video if hasattr(_s2, "video") else _s2[0])[:, :, :7].mean())
ck("K6 anchor 统计纠偏生效（前缀均值向锚段靠）", abs(_pre - _av) < abs(_pv - _av),
   "|prev−anchor|=%.4f → |prefix−anchor|=%.4f" % (abs(_pv - _av), abs(_pre - _av)))

# K7 —— 🔴 2026-09-17 真渲染事故的回归钉子：统计量取样窗。
#   旧口径（对整个作用区聚合）配单帧 guide ⇒ 段头**内部有亮度梯度**时，首帧被推过 guide、
#   缝上凭空多出一个阶跃（真渲染实测纯末→首阶跃 0.0007 → 0.0088）。这里离线复现该机制。
_gi = _T.full((1, 8, 8, 3), 0.50)
_grad = _T.cat([_T.full((1, 8, 8, 3), 0.495), _T.full((5, 8, 8, 3), 0.470)], 0)
_gap0 = float((_grad[0].mean() - _gi[0].mean()).abs())
_gs = float((N.H3RelayPost().apply(_grad, _gi, match_prev=1.0, match_prev_frames=6,
                                   cross_seg_ack=True)[0][0]
             .mean() - _gi[0].mean()).abs())
_gl = float((N.H3RelayPost().apply(_grad, _gi, match_prev=1.0, match_prev_frames=6,
                                   match_prev_stats_frames=0, cross_seg_ack=True)[0][0].mean()
             - _gi[0].mean()).abs())
print("      段头内部梯度场景：原阶跃 %.4f ｜ 取紧贴缝那帧 → %.4f ｜ 旧口径 → %.4f"
      % (_gap0, _gs, _gl))
ck("K7 统计量取紧贴缝那帧 ⇒ 首帧落到 guide；旧口径会推过 guide（事故复现）",
   _gs < _gap0 * 0.05 and _gl > _gap0 * 2.0)
ck("K7b H3RelayPost 新 widget match_prev_stats_frames 默认 1（修正后口径）",
   N.H3RelayPost.INPUT_TYPES()["optional"]["match_prev_stats_frames"][1]["default"] == 1)

# K10~K12 —— 2026-09-19 视频侧三项改造（稳健基准 / 互斥组 / R4 跨段弃权）
_img10 = _T.cat([_T.rand(12, 8, 8, 3) * 0.10 + 0.25,
                 _T.rand(20, 8, 8, 3) * 0.20 + 0.50], 0)
_g10 = _T.rand(1, 8, 8, 3) * 0.20 + 0.50
_o10a, _rr10a = N.H3RelayPost().apply(_img10, _g10, match_prev=1.0)
_o10b, _rr10b = N.H3RelayPost().apply(_img10, _g10, match_prev=1.0, cross_seg_ack=True)
ck("K10 跨段项默认弃权（逐位直通），打勾后才作用",
   _T.equal(_o10a, _img10) and "自动弃权" in _rr10a and not _T.equal(_o10b, _img10),
   "默认报告=%s" % _rr10a[:60])
ck("K10b cross_seg_ack 默认 False / baseline 默认 robust（末位追加）",
   N.H3RelayPost.INPUT_TYPES()["optional"]["cross_seg_ack"][1]["default"] is False
   and N.H3RelayPost.INPUT_TYPES()["optional"]["baseline"][1]["default"] == "robust")

# K11 互斥组：组 2 只作用直方图、组 3 只作用反卷积
_x11 = _T.cat([_T.rand(24, 8, 8, 3) * 0.10 + 0.20,
               _T.rand(66, 8, 8, 3) * 0.20 + 0.60], 0)
_o11, _r11 = N.H3RelayPost().apply(_x11, None, hist_match=1.0, wb_match=1.0,
                                   deconv_strength=1.0, detail_borrow=1.0)
ck("K11 组 2/组 3 互斥只作用主项并在报告点名",
   "组 2 互斥" in _r11 and "组 3 互斥" in _r11
   and "白平衡校正" not in _r11 and "尺度" not in _r11, "报告=%s" % _r11[:110])

# K12 段体离散度超阈 ⇒ 弃权（稳健基准的合同）；legacy 不弃权（可复现旧口径）
_x12 = _T.cat([_T.full((24, 4, 4, 3), 0.30), _T.full((33, 4, 4, 3), 0.80),
               _T.full((33, 4, 4, 3), 0.20)], 0)
_o12a, _r12a = N.H3RelayPost().apply(_x12, None, hist_match=1.0)
_o12b, _r12b = N.H3RelayPost().apply(_x12, None, hist_match=1.0, baseline="legacy")
ck("K12 段体基准离散度超阈 ⇒ 弃权；baseline=legacy ⇒ 不弃权",
   _T.equal(_o12a, _x12) and "弃权" in _r12a
   and not _T.equal(_o12b, _x12) and "弃权" not in _r12b,
   "robust=%s" % _r12a[:70])
ck("K12b 逐层审计：报告含「↳ …后：段头亮度 … 高频 …」",
   "↳ 直方图匹配 后：段头亮度" in _r11 and "高频" in _r11)

# K8 —— 2026-09-19 参数收口：组 2/3 的作用帧数归 `head_zone_frames`。
#   事故背景：组 2（色档对齐）与组 3（高频补）的四个强度旋钮，作用区长度一直**偷偷借**
#   组 4 的 `settle_sharpen_frames`（名字叫「糊区锐化帧数」）⇒ UI 上看不出谁管作用区。
#   本钉子锁两件事：① 作用区确实跟着 head_zone_frames 走；② 报告里把帧数显式写出来。
_hi = _T.cat([_T.full((24, 8, 8, 3), 0.20), _T.full((40, 8, 8, 3), 0.70)], 0)
_A8, _repA8 = N.H3RelayPost().apply(_hi, None, hist_match=1.0,
                                    head_zone_frames=4, settle_sharpen_frames=20)
_B8, _ = N.H3RelayPost().apply(_hi, None, hist_match=1.0,
                               head_zone_frames=20, settle_sharpen_frames=4)
print("      head_zone_frames=4 → 前 4 帧 %s，第 5 帧起逐位不动 %s｜=20 → 第 4–19 帧被改 %s"
      % ("动了" if not _T.equal(_A8[:4], _hi[:4]) else "没动",
         _T.equal(_A8[4:], _hi[4:]),
         not _T.equal(_B8[4:20], _hi[4:20])))
ck("K8 组 2/3 的作用帧数跟着 head_zone_frames（不再偷用 settle_sharpen_frames）+ 报告写明帧数",
   not _T.equal(_A8[:4], _hi[:4]) and _T.equal(_A8[4:], _hi[4:])
   and not _T.equal(_B8[4:20], _hi[4:20]) and "前 4 帧" in _repA8,
   "窗口 = 4 时窗口外逐位不变")
ck("K8b head_zone_frames 已注册且默认 24（与旧的 settle_sharpen_frames 默认同值 ⇒ 未设值的图行为不变）",
   N.H3RelayPost.INPUT_TYPES()["optional"]["head_zone_frames"][1]["default"] == 24)

# K9 —— 2026-09-19 节点 UI 收口：`advanced: True` 只收「细分/护栏」，**主强度旋钮必须留在画布上**。
#   官方机制（`comfy_extras/nodes_model_advanced.py` 同款）：advanced widget 默认不渲染，
#   收进节点底部展开区 / 右栏 “Advanced Inputs”。它**不改**取值位置与默认值。
#   前后端依据：前端 `GraphView` 判 `widget.options.advanced`；节点 resize 宽有 225px 死下限、
#   高不能小于内容行数 ⇒ 少渲染几行 = 节点能变小。
_itT9 = N.H3RelayTrimAV.INPUT_TYPES()
_itP9 = N.H3RelayPost.INPUT_TYPES()["optional"]
_adv_T = [k for k, v in _itT9["optional"].items() if v[1].get("advanced")]
_adv_P = [k for k, v in _itP9.items() if v[1].get("advanced")]
_keep_T = ["trim_frames", "fps", "audio", "settle_frames"]
_keep_P = ["match_prev", "lowfreq_pull", "hist_match", "wb_match",
           "deconv_strength", "detail_borrow", "settle_sharpen", "head_zone_frames",
           "cross_seg_ack"]
_must_T = ["hist_match", "wb_match", "deconv_strength", "detail_borrow",
           "lowfreq_pull", "match_prev", "seam_ghost"]
_must_P = ["match_prev_frames", "lowfreq_frames", "deconv_radius",
           "detail_blur", "settle_sharpen_frames", "match_prev_stats_frames",
           "baseline"]
print("      裁重叠：画布留 %s ／ 折叠 %d 项；后处理：画布留 %d 项 ／ 折叠 %d 项"
      % (_keep_T, len(_adv_T), len(_keep_P), len(_adv_P)))
ck("K9 advanced 标记方向正确（主旋钮留在画布上、细分与护栏项折叠）",
   all(k not in _adv_T for k in _keep_T) and all(k in _adv_T for k in _must_T)
   and all(k not in _adv_P for k in _keep_P) and all(k in _adv_P for k in _must_P))

print("=" * 78)
print("L. 音频缝节点（0.5.0 新增：音频域必须由**节点**实现，不靠组装层 ffmpeg）")
print("=" * 78)
import shutil as _sh                                                     # noqa: E402
import tempfile as _tf                                                   # noqa: E402

_it_l = N.H3RelayAudioSeam.INPUT_TYPES()
_ol = _it_l["optional"]
ck("L1 H3RelayAudioSeam 已注册（8 节点）+ 显示名以 🔗 开头",
   N.NODE_CLASS_MAPPINGS.get("H3RelayAudioSeam") is N.H3RelayAudioSeam
   and N.NODE_DISPLAY_NAME_MAPPINGS["H3RelayAudioSeam"].startswith("🔗")
   and len(N.NODE_CLASS_MAPPINGS) == 8,
   "%d 节点" % len(N.NODE_CLASS_MAPPINGS))
ck("L2 默认全关（patch=0 / tile=0 / fade=0.25）+ OUTPUT_NODE（否则第 1 段床源永不落盘）",
   _ol["patch_seconds"][1]["default"] == 0.0 and _ol["tile_seconds"][1]["default"] == 0.0
   and abs(_ol["fade_seconds"][1]["default"] - 0.25) < 1e-9
   and N.H3RelayAudioSeam.OUTPUT_NODE is True)

_SR2 = 32000
_T.manual_seed(11)
_curw = _T.rand(1, 1, _SR2 * 3) * 0.30
_curw[..., :int(0.032 * _SR2)] = 0.0                                  # 复刻解码 priming
_ca = {"waveform": _curw, "sample_rate": _SR2}
_bw = _T.cat([_T.rand(1, 1, _SR2 * 2) * 0.50, _T.rand(1, 1, _SR2 * 2) * 0.05], -1)
_ba = {"waveform": _bw, "sample_rate": _SR2}

_pass0, _ = CORE.audio_seam_patch(_ca, _ba, patch=0.0)
ck("L3 patch=0 ⇒ 音频逐位直通（同一对象，零拷贝）", _pass0 is _ca)

_N2, _X2 = int(2.0 * _SR2), int(0.25 * _SR2)
_keep2 = _N2 - _X2
_o, _rp = CORE.audio_seam_patch(_ca, _ba, patch=2.0, fade=0.25)
_ow = _o["waveform"]
# 🔴 2026-09-19 行为变更：默认档 = 尾部窗 + 电平对齐（旧「最静窗」降为 select="quiet"）
_b2 = _bw.reshape(-1, _bw.shape[-1])
_bwin = _b2[..., int(_b2.shape[-1]) - _N2:]                       # 尾部窗
_tg2 = CORE.target_level(_b2)
_g2 = _tg2 / max(float(_bwin.pow(2).mean().sqrt()), 1e-12)
ck("L4 长度守恒 + N 之后逐位不动（零 A/V 位移的硬证据）",
   int(_ow.shape[-1]) == int(_curw.shape[-1])
   and _T.equal(_ow[..., _N2:], _curw[..., _N2:]))
ck("L5 替换区 = 床源尾部窗 × 单一增益（形状未变形；电平对齐到缝前目标 ±1.5 dB）",
   bool((_ow[..., :_keep2] - _bwin[..., :_keep2] * _g2).abs().max() < 1e-6)
   and abs(20 * math.log10(max(float(_ow[..., :_keep2].pow(2).mean().sqrt()), 1e-9)
                           / max(_tg2, 1e-9))) < 1.5,
   "增益 %+.2f dB ｜ %s" % (20 * math.log10(max(_g2, 1e-12)),
                            [ln for ln in _rp.splitlines() if "床声电平" in ln][:1]))
_wg = _T.linspace(0.0, 1.0, _X2)
_expb = (_bwin * _g2)[..., _keep2:_N2] * (1.0 - _wg) + _curw[..., _keep2:_N2] * _wg
ck("L6 边界窗是 blend（非硬切）",
   _T.allclose(_ow[..., _keep2:_N2], _expb, atol=1e-6)
   and not _T.equal(_ow[..., _keep2:_N2], (_bwin * _g2)[..., _keep2:_N2]))

_td = _tf.mkdtemp(prefix="h3relay_review_")
try:
    _p = os.path.join(_td, "a.safetensors")
    CORE.save_audio(_o, _p, note="review")
    _bk = CORE.load_audio(_p)
    _rid = "_unit_review_audio"
    _obj = N.H3RelayAudioSeam()
    _a0, _l0 = _obj.seam(_ba, _rid, 0)
    _a1, _l1 = _obj.seam(_ca, _rid, 1, patch_seconds=2.0)
    _want = CORE.load_audio(N._audio_stage_path(_rid, 0))["waveform"]
    _w2 = _want.reshape(-1, _want.shape[-1])
    _tail3 = _w2[..., int(_w2.shape[-1]) - _N2:]
    _g3 = CORE.target_level(_w2) / max(float(_tail3.pow(2).mean().sqrt()), 1e-12)
    ck("L7 音频落盘/读回逐位一致 + 节点 stage 0 直通但落盘 + stage 1 用上一段**尾部窗**当床（电平对齐）",
       _T.equal(_bk["waveform"], _o["waveform"]) and _a0 is _ba and "第 1 段无缝可补" in _l0
       and _T.allclose(_a1["waveform"][..., :_keep2], _tail3[..., :_keep2] * _g3, atol=1e-6)
       and "长度守恒" in _l1 and "尾部窗" in _l1,
       "落盘目录挂到 output/relay_kit/ 下")
    _errs = []
    try:
        _obj.seam(_ca, _rid, 1, patch_seconds=1.0, bed_stage=1)
    except Exception as _e:
        _errs.append("床源段号" in str(_e))
    try:
        _obj.seam(_ca, _rid, 3, patch_seconds=1.0, bed_stage=2)
    except Exception as _e:
        _errs.append("床源音频不存在" in str(_e))
    ck("L8 两道守卫都 raise（床源段号 ≥ 本段 / 床源文件缺失）",
       _errs == [True, True], "%s" % (_errs,))
finally:
    _sh.rmtree(os.path.join(os.path.dirname(N._audio_stage_path("_unit_review_audio", 0))),
               ignore_errors=True)
    _sh.rmtree(_td, ignore_errors=True)

_rd = open(os.path.join(KIT, "README.md"), encoding="utf-8").read()
ck("L9 README 的音频缝章节已改口径：节点实现（且不再写「要靠组装层补」）",
   "要靠组装层补" not in _rd and "续接音频缝" in _rd
   and "必须在节点里做" in _rd)

print("=" * 78)
print("结果：通过 %d / 失败 %d" % (len(OK), len(BAD)))
if BAD:
    print("失败项：")
    for b in BAD:
        print("   -", b)
print("=" * 78)
sys.exit(1 if BAD else 0)
