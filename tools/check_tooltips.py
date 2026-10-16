# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
"""悬浮提示（tooltip）文案机检：**写法规范 + 中英一一对应**。

【为什么要它】
悬浮提示是用户唯一的"现场说明书"。历史上它有四个毛病，全是肉眼才发现、改完又会回退：
  ① **两份各写一套**：后端 `nodes.py` 与前端 `web/relay_kit_i18n.js` 讲得不一样
     ⇒ 同一个参数两种解释。现在**中文只在后端写一份**，前端只留英文。
  ② **越写越长**：最长一条 1084 字符，用户悬停看到一屏字。
  ③ **夹带内部黑话**：母版 / 产线 / 跑批 / 落盘 / 台账 / 机检 / 断言 / 章节号 / 版本号
     —— 包外的人看不懂，还看不出"开了到底会怎样"。
  ④ **不答关键问题**：开关没说开/关各有什么效果；数值没说大了小了往哪走。

【判据（全部可机检）】
  结构
    S1 中文必须以 `【标签】` 开头，标签 ≤ 10 字
    S2 中文第一行 ≤ 44 字（悬停时第一眼看到的那行）
    S3 中文任一行 ≤ 64 字；且**不许有空行**（禁止长文分节）
    S4 长度上限：中文 ≤ 150 字符，英文 ≤ 380 字符
  内容
    C1 布尔参数：中文必须同时出现「开」与「关」（说清两个状态各是什么）
    C2 数值参数且 0 在取值范围内：中文必须出现 `0`（0 是"关/直通/自动"里最常被误解的一档）
    C3 枚举参数：中文必须逐个提到每个候选值
    C4 禁用词：内部黑话 / 文件路径 / 章节号 / 版本号 / 日期 / 分级 emoji（见 BAN）
    C5 英文必须存在、非空、**不含汉字**、且与中文不同
  对应
    P1 后端参数集合 == 前端 `H3_TIPS` 键集合（不许有孤儿、不许有漏网）
    P2 前端条目**只许有 `en`**（中文单源在后端；留 `zh` 就是又开了第二条路）

⚠️ 本工具**不判文案好不好懂**（那是人的事），只判上面这些能机检的硬规矩。
   文案的"写作模板"写在 `CONTRIBUTING.md` §提示文案。

用法：
    set COMFYUI_PATH=<你的 ComfyUI 根目录>
    python tools/check_tooltips.py
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
import types

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COMFY = os.environ.get("COMFYUI_PATH") or os.environ.get("COMFYUI_ROOT")
if not _COMFY or not os.path.isdir(_COMFY):
    sys.exit("[FAIL] 需要 COMFYUI_PATH 指向 ComfyUI 根目录")
sys.path.insert(0, _COMFY)
sys.path.insert(0, KIT)

I18N_JS = os.path.join(KIT, "web", "relay_kit_i18n.js")

NODE_ORDER = [
    "H3RelayLatentSave", "H3RelayLatentLoad", "H3RelayCopyBridge", "H3RelayTrimAV",
    "H3RelayPost", "H3RelayAudioSeam", "H3RelayChain", "H3RelayLatentUpscale",
    "H3RelaySeedFromVideo",
]

#: 长度上限（字符）。中文按"一眼看完"，英文按同样信息量的自然膨胀留 2 倍余量。
MAX_ZH = 150
MAX_EN = 380
MAX_FIRST_LINE = 44
MAX_ANY_LINE = 64
MAX_TAG = 10

#: 🔴 禁用词 = 包外的人看不懂的东西。分五类，改提示时若命中就是"把内部语境漏出去了"。
BAN = [
    # ① 内部黑话（我们的说法，不是通用说法）
    (r"本仓|本包|本节点|本地产线|产线|跑批|母版|宿主|落盘|存盘|台账|机检|断言|示例图|铁律|排障表|一键套件|口径|上游同款",
     "内部黑话"),
    (r"一采|二采", "内部黑话（改「第一次采样 / 第二次采样」）"),
    (r"组\s*[1-9]\s*·", "内部编号（改功能性说法，如「音频缝」）"),
    # ② 本地项目/人名/脚本名
    (r"\bGG\b|l2cai|coffee|POV17|追痕|TIHA|E1'?|E3|E4|E5", "本地项目代号"),
    (r"l1_api|chain_auto|voice_bank|precheck_\w+|check_ui_workflow|h3_local_gates", "本地脚本名"),
    # ③ 指路（章节号会漂，路径会断）
    (r"§|docs/\d|CHANGES\.md|README_EN", "内部章节/文件指路"),
    # ④ 版本号与日期（提示里不该背历史）
    (r"\b\d+\.\d+\.\d+\b", "版本号"),
    (r"\b20\d\d-\d\d-\d\d\b", "日期"),
    # ⑤ 分级 emoji（🔴🟡🟢🧪🟣🔵 是我们的严重度记号，不是通用语义）
    (r"[🔴🟡🟢🧪🟣🔵]", "内部分级 emoji（改用文字，如「实验」「仅供对照」）"),
    # ⑥ 字面 `**`：悬浮框是纯文本渲染 ⇒ 用户看到的是星号本身，不是强调
    (r"\*\*", "字面星号（悬浮框按纯文本渲染，星号会被原样看到）"),
]

PASS: list[str] = []
FAIL: list[str] = []


def ck(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name if ok else ("%s%s" % (name, ("  ← " + detail) if detail else "")))


def load_nodes():
    """按文件加载节点包（目录名含连字符，不能直接当包名 —— 与 review_050 同法）。"""
    pkg = types.ModuleType("h3latentrelay")
    pkg.__path__ = [KIT]
    sys.modules["h3latentrelay"] = pkg
    spec = importlib.util.spec_from_file_location(
        "h3latentrelay.nodes", os.path.join(KIT, "nodes.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["h3latentrelay.nodes"] = mod
    spec.loader.exec_module(mod)
    return mod


def read_tips_js(path: str) -> dict:
    """从 `relay_kit_i18n.js` 里取出 `H3_TIPS` 的键与 `en`。

    刻意**不用 node**（本工具要能在纯 Python 环境跑）：H3_TIPS 的条目形状是固定的
    `"键": { en: "……", }`，用「扫描字符串字面量」的方式解析，不依赖缩进与换行。
    """
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(r"export const H3_TIPS\s*=\s*\{", src)
    if not m:
        raise SystemExit("[FAIL] 在 %s 里找不到 `export const H3_TIPS = {`" % path)
    i = m.end()
    out: dict[str, dict[str, str]] = {}
    key_re = re.compile(r'"([A-Za-z0-9_]+\.[A-Za-z0-9_]+)"\s*:\s*\{')
    while i < len(src):
        km = key_re.search(src, i)
        if not km:
            break
        # 该键的条目体：从 `{` 到配对 `}`（en 值里可能有 `}`，所以按"下一个键或本段结束"界定）
        j = km.end()
        nxt = key_re.search(src, j)
        end = nxt.start() if nxt else src.find("\n};", j)
        if end < 0:
            end = len(src)
        body = src[j:end]
        ent: dict[str, str] = {}
        for lm in re.finditer(r"(?:^|[{,\s])(zh|en)\s*:\s*", body):
            k = lm.group(1)
            p = lm.end()
            if p >= len(body) or body[p] != '"':
                ent[k] = "<非字符串>"
                continue
            p += 1
            buf = []
            while p < len(body):
                c = body[p]
                if c == "\\":
                    nxtc = body[p + 1] if p + 1 < len(body) else ""
                    buf.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(nxtc, "\\" + nxtc))
                    p += 2
                    continue
                if c == '"':
                    break
                buf.append(c)
                p += 1
            ent[k] = "".join(buf)
        out[km.group(1)] = ent
        i = km.end()
    return out


def read_widget_labels(path: str) -> dict:
    """从 `H3_WIDGETS` 块取 `{参数名: 中文标签}`（条目都是单行）。"""
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(r"export const H3_WIDGETS\s*=\s*\{", src)
    if not m:
        raise SystemExit("[FAIL] 在 %s 里找不到 `export const H3_WIDGETS = {`" % path)
    end = src.index("\n};", m.end())
    return dict(re.findall(r'^\s{4}([A-Za-z0-9_]+):\s*\{ zh: "([^"]*)"', src[m.end():end], re.M))


def read_glossary(path: str) -> tuple[dict, list]:
    """从 `CONTRIBUTING.md` 的**术语表**取两张表（规范即源头，本工具只读不写）。

    表 A（`TERMS-LABEL`）：`| 参数键 | 规范标签 |` ⇒ 标签必须**精确相等**。
    表 B（`TERMS-BAN`）  ：`| 规范词 | 不许写成 |` ⇒ 禁用变体不许出现在任何中文提示里。
    为什么把表放在文档里而不是工具里：**规范是给人看的**；工具读它 ⇒ 表与代码不可能两套。
    """
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    label, ban = {}, []
    for tag in ("TERMS-LABEL", "TERMS-BAN"):
        m = re.search(r"<!-- %s:BEGIN -->(.*?)<!-- %s:END -->" % (tag, tag), src, re.S)
        if not m:
            raise SystemExit("[FAIL] CONTRIBUTING.md 里找不到 %s 区块" % tag)
        for line in m.group(1).splitlines():
            cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
            if len(cells) != 2 or cells[0] in ("参数键", "规范词") or set(cells[0]) <= set("-: "):
                continue
            if tag == "TERMS-LABEL":
                label[cells[0]] = cells[1]
            else:
                ban.append((cells[0], cells[1]))
    return label, ban


def main() -> int:
    N = load_nodes()
    params: dict[str, tuple] = {}
    for cls_name in NODE_ORDER:
        cls = getattr(N, cls_name)
        it = cls.INPUT_TYPES()
        for sec in ("required", "optional"):
            for pname, spec in it.get(sec, {}).items():
                if not (isinstance(spec, tuple) and len(spec) >= 2 and isinstance(spec[1], dict)):
                    continue
                params["%s.%s" % (cls_name, pname)] = (spec[0], spec[1])

    tips = read_tips_js(I18N_JS)

    print("=" * 78)
    print("悬浮提示机检：后端参数 %d 条 ｜ 前端 H3_TIPS %d 条" % (len(params), len(tips)))
    print("=" * 78)

    # ---------- 结构 + 内容 ----------
    for key, (ty, cfg) in params.items():
        zh = cfg.get("tooltip") or ""
        ent = tips.get(key) or {}
        en = ent.get("en") or ""
        tag = "【%s】" % zh[1:zh.index("】")] if zh.startswith("【") and "】" in zh[:18] else ""

        # S1 标签
        ck("%s S1 以【标签】开头" % key, bool(tag), zh[:16])
        if tag:
            ck("%s S1b 标签 ≤ %d 字且不是句子" % (key, MAX_TAG),
               len(tag) - 2 <= MAX_TAG and "。" not in tag, tag)
        # S2 / S3 行
        lines = zh.split("\n")
        ck("%s S2 首行 ≤ %d 字" % (key, MAX_FIRST_LINE), len(lines[0]) <= MAX_FIRST_LINE,
           "%d 字：%s" % (len(lines[0]), lines[0][:40]))
        ck("%s S3 任一行 ≤ %d 字且无空行" % (key, MAX_ANY_LINE),
           all(len(x) <= MAX_ANY_LINE for x in lines) and "" not in lines,
           "最长 %d 字" % max(len(x) for x in lines))
        # S4 长度
        ck("%s S4 中文 ≤ %d 字符" % (key, MAX_ZH), 0 < len(zh) <= MAX_ZH, "%d 字" % len(zh))
        ck("%s S4b 英文 ≤ %d 字符" % (key, MAX_EN), 0 < len(en) <= MAX_EN, "%d 字" % len(en))
        # C1 布尔：开/关都说
        if ty == "BOOLEAN":
            ck("%s C1 布尔说明「开」与「关」" % key, "开" in zh and "关" in zh, zh[:40])
        # C2 数值默认 0 ⇒ 必须说明 0 是什么（0 是"关/直通/自动"里最常被误解的一档）
        if isinstance(ty, str) and ty in ("INT", "FLOAT"):
            if cfg.get("default") in (0, 0.0):
                ck("%s C2 默认 0 ⇒ 说明 0 档" % key, "0" in zh, zh[:40])
        # C3 枚举：每个候选都提到
        if isinstance(ty, (list, tuple)) and all(len(str(v)) <= 12 and "." not in str(v) for v in ty):
            # 只对「短符号枚举」要求逐档说明；模型文件名那类动态列表不要求（它们就是下拉里的值）
            miss = [v for v in ty if str(v) not in zh]
            ck("%s C3 枚举逐个说明（%d 档）" % (key, len(ty)), not miss, "缺 %s" % miss[:3])
        # C4 禁用词
        hit = [(p, why) for p, why in BAN if re.search(p, zh)]
        ck("%s C4 无禁用词" % key, not hit,
           "；".join("%s（%s）" % (re.search(p, zh).group(0), why) for p, why in hit)[:80])
        # C5 英文
        ck("%s C5 英文存在且不含汉字" % key,
           bool(en) and not re.search(r"[\u4e00-\u9fff]", en), en[:40])
        ck("%s C5b 英文与中文不同" % key, bool(en) and en != zh, en[:24])

    # ---------- 对应 ----------
    orphan = sorted(set(tips) - set(params))
    missing = sorted(set(params) - set(tips))
    ck("P1 前后端键一一对应（孤儿 %d / 漏网 %d）" % (len(orphan), len(missing)),
       not orphan and not missing, "孤儿 %s 漏网 %s" % (orphan[:3], missing[:3]))
    with_zh = sorted(k for k, v in tips.items() if "zh" in v)
    ck("P2 前端条目只留 `en`（中文单源在后端）", not with_zh, "还带 zh 的：%s" % with_zh[:5])

    # ---------- P3 命名家族契约（2026-10-14 加）----------
    # 背景（有提交证据）：0.6.28 的 `6bde923` **一次性手写了 113 条标签**，锚族是
    #   **按键名字面直译**的 —— `anchor_latent` → 「外观参考」、`ref_anchor_latent` → 「参考锚」，
    #   而机制**正好相反**（`anchor_*` 是 0.5.0 的统计纠偏、`ref_anchor_*` 才是真外观参考）。
    #   错的标签"看着像个正常词"，所以 8 天没人发现：当时的判据（H3n）只查**有没有**标签，
    #   不查**对不对**；那次提交的验证目标是"切语言不坏图"，也不是"译名对不对"。
    # ⇒ 把**语义决定**写成契约：真外观参考这一族必须叫「外观参考」，另一族**不许占用**这个词。
    #   ⚠️ 纯机械判据只能覆盖**被写进契约的家族**；没进契约的语义错位仍要靠人读。
    labels = read_widget_labels(I18N_JS)
    for keys, word, must in (
        (("ref_anchor_latent", "ref_anchor_stage", "ref_anchor_frames"), "外观参考", True),
        (("anchor_latent", "anchor_blend"), "外观参考", False),
    ):
        bad = [(k, labels.get(k)) for k in keys if (word in (labels.get(k) or "")) != must]
        ck("P3 家族契约：%s %s「%s」"
           % ("/".join(keys), "必须含" if must else "不许含", word),
           not bad, "违反：%s" % bad)

    # ---------- P4~P6 术语表（唯一真相源 = `CONTRIBUTING.md` 的两张表）----------
    # 为什么要它：术语漂过一次（锚族标签按键名直译写反、8 天没人发现）。
    # 规矩：**先改表、再改代码**；表与代码不一致当场红。
    g_label, g_ban = read_glossary(os.path.join(KIT, "CONTRIBUTING.md"))
    ck("P6 术语表自身完整（表 A ≥ 20 条 / 表 B ≥ 10 条，且无空项）",
       len(g_label) >= 20 and len(g_ban) >= 10
       and all(k and v for k, v in g_label.items()) and all(a and b for a, b in g_ban),
       "表 A %d 条 ｜ 表 B %d 条" % (len(g_label), len(g_ban)))

    wrong = [(k, labels.get(k), want) for k, want in g_label.items()
             if k in labels and labels.get(k) != want]
    missing = [k for k in g_label if k not in labels]
    ck("P4 术语表·表 A：这些标签必须与规范**精确相等**（%d 条）" % len(g_label),
       not wrong and not missing,
       "不符 %s ｜ 表里有但代码没有 %s" % (wrong[:4], missing[:4]))

    hits = []
    for norm, bad in g_ban:
        for key, (_ty, cfg) in params.items():
            zh = cfg.get("tooltip") or ""
            if bad in zh:
                hits.append("%s（该写「%s」）" % (key, norm))
        # 2026-10-19：**标签也要查**。此前只查 tooltip ⇒ 六处标签（声锚 / 外观锚 / 床声…）
        #   与提示用词不一致，机检全绿、人一眼看出。范围见 CONTRIBUTING 术语表。
        for key, lab in labels.items():
            if bad in (lab or ""):
                hits.append("标签 %s（该写「%s」）" % (key, norm))
    ck("P5 术语表·表 B：禁用变体在任何中文提示**与标签**里 0 命中（%d 条）" % len(g_ban),
       not hits, "命中 %s" % hits[:4])

    print("结果：通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("   失败：%s" % f)
    print("=" * 78)
    print("⚠️ 本工具只判写法硬规矩，不判「好不好懂」（那要人读）。")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
