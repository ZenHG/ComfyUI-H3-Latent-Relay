#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""前端静态一致性机检（零 GPU、零浏览器、秒级）。

## 为什么要有它（2026-10-15 立，有实测依据）

这一轮改前端时，**本地全套 15/15 全绿、`review_050` 119/0、`check_tooltips` 1217/0**，
却仍然漏了 3 类问题 —— 它们都只在「**中英两侧对读**」时才看得见：

| 漏掉的 | 为什么既有判据看不见 |
|---|---|
| 英文提示**落后于中文**（C1 把中文改成「走向外推 + 噪声闸」，英文还写着 `previous last frame`） | `check_tooltips` 的 C5 只查「**有没有** `en`」，不查「说的是不是同一件事」 |
| 英文提示里混着**全角 `【】`**（129/129 条） | C5 的「不含汉字」正则只扫 `[\\u4e00-\\u9fff]`，**扫不到 `【】`** |
| `web/*.js` 里**新写的可见中文**（英文界面下直接露出来：7 个按钮 / 状态文案 / 标题后缀） | 没有任何判据盯 `web/*.js` 的**字面量** |

⇒ 本工具只做这三件事。**它不替代** `check_tooltips.py`（写法硬规矩）与
`tests/test_*.mjs`（纯函数行为），三者互补。

## 判据

- **F1** 未翻译的可见中文**只许减不许增**（基线在 `UNTRANSLATED_BASELINE`，翻一条就减 1）
- **F2** `H3_TIPS` 的英文形状：无全角括号 / 无汉字 / `]` 后**留一个空格**
- **F3** 中英**行数**一致（不等 ⇒ 必须在 `EXEMPT` 里写明理由）

## 用法

    python tools/check_frontend.py            # 期望 通过 3 / 失败 0
"""

import io
import os
import re
import sys

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(KIT, "web")
I18N_JS = os.path.join(WEB, "relay_kit_i18n.js")
NODES = os.path.join(KIT, "nodes.py")

CJK = re.compile(r"[\u4e00-\u9fff]")

# ---------------------------------------------------------------------------
# F1 基线：**只许减不许增**。
#
# 这些是「英文界面下仍会露中文」的运行时文案（状态格 / 通知 / 报错）。它们**带变量**
# （段号 / 名字 / 错误信息），键没法是整串，所以没用 `H3_TIPS` 那类词表，而是就地写两份
# （`T(中文, 英文)`）。本基线 = **还没写成 `T(...)` 的那些**。
#
# 🔴 修法：把它包进 `T("中文", "English")`（`relay_kit_chain.js` 顶部有这个助手），
#    然后把这个数**减 1**。判据是 `<=`，所以只减不增会被放过、只增会被当场拦下。
# ---------------------------------------------------------------------------
UNTRANSLATED_BASELINE = 115

# F1 白名单：**看起来像漏网、其实已经覆盖**的（必须写清理由，否则白名单本身会腐）。
ALLOW_F1 = {
    # `PHASES` 是**数据表**，取值走 `phaseLabel(p) -> T(p.label, p.labelEn)`
    # （`drawPhaseBadge` 每帧调）⇒ 表里的 `label` 天生是中文，不算漏网。
    "空闲": "PHASES.idle.label —— 经 phaseLabel()/T() 取值",
    "已排队": "PHASES.queued.label —— 同上",
    "连跑中": "PHASES.running.label —— 同上",
    "完成": "PHASES.done.label —— 同上",
    "已停止": "PHASES.stopped.label —— 同上",
    "需处理": "PHASES.warn.label —— 同上",
    "出错": "PHASES.error.label —— 同上",
}

# ---------------------------------------------------------------------------
# F3 豁免：中英**行数**不等但**内容已对齐**的（必须写清理由）。
# ---------------------------------------------------------------------------
EXEMPT_ALIGN = {
    "H3RelayCopyBridge.exp_anchor":
        "英文把中文第 3+4 行并成一行（内容逐条对应，只是断行不同）",
}


def read(path):
    # ⚠️ 不能写成「`return` 后面紧跟 `io.open(...).read()`」那种形态 —— 那是 SIM115 的**已知盲点**，
    #    由 `review_050` 的 **H4f** 专门盯着（本仓真踩过两次）。用 `with` 关句柄。
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def strip_comments_and_literals(src):
    """返回 `[(字面量文本, 起始下标), …]`（注释已剥掉）。

    手写扫描器而不是正则：要正确跳过 `//`、`/* */`、以及字符串里的转义与引号。
    """
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c in "\"'`":
            q, j, buf = c, i + 1, []
            while j < n:
                if src[j] == "\\":
                    buf.append(src[j:j + 2])
                    j += 2
                    continue
                if src[j] == q:
                    break
                buf.append(src[j])
                j += 1
            out.append(("".join(buf), i))
            i = j + 1
        else:
            i += 1
    return out


def t_call_regions(src):
    """所有 `T(` 调用的**参数区间**（按括号配平；跳过字符串内的括号）。

    F1 用它判定「这条中文字面量已经被 `T(中文, 英文)` 覆盖了」。
    """
    regions = []
    for m in re.finditer(r"(?<![\w$.])T\(", src):
        start = m.end() - 1          # 指向 `(`
        depth, i, n = 0, start, len(src)
        while i < n:
            c = src[i]
            if c in "\"'`":
                q, j = c, i + 1
                while j < n:
                    if src[j] == "\\":
                        j += 2
                        continue
                    if src[j] == q:
                        break
                    j += 1
                i = j + 1
                continue
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        regions.append((start, i))
    return regions


def _console_regions(src):
    """`console.*(...)` 的**参数区间** —— 那是开发者日志，用户看不到，不算「可见文案」。"""
    out = []
    for m in re.finditer(r"console\.(?:warn|error|log|info|debug)\(", src):
        start = m.end() - 1
        depth, i, n = 0, start, len(src)
        while i < n:
            c = src[i]
            if c in "\"'`":
                q, j = c, i + 1
                while j < n:
                    if src[j] == "\\":
                        j += 2
                        continue
                    if src[j] == q:
                        break
                    j += 1
                i = j + 1
                continue
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        out.append((start, i))
    return out


def f1_untranslated():
    """未翻译的可见中文字面量（不含 i18n 词表自身 / `console.*` / 白名单）。"""
    hits = []
    for name in sorted(os.listdir(WEB)):
        if not name.endswith(".js"):
            continue
        path = os.path.join(WEB, name)
        if os.path.abspath(path) == os.path.abspath(I18N_JS):
            continue                      # 这张表**就是**词表（`zh:` 值天生是中文）
        src = read(path)
        regions = t_call_regions(src) + _console_regions(src)
        for text, pos in strip_comments_and_literals(src):
            if not CJK.search(text):
                continue
            if text in ALLOW_F1:
                continue                  # 已知覆盖（白名单带理由）
            if any(a <= pos < b for a, b in regions):
                continue                  # 已写进 `T(中文, 英文)` 或属开发者日志
            hits.append((name, src.count("\n", 0, pos) + 1, text.replace("\n", " ⏎ ")[:60]))
    return hits


def f2_en_shape():
    """`H3_TIPS` 英文形状。"""
    src = read(I18N_JS)
    i = src.index("export const H3_TIPS = {")
    j = src.index("\nexport const H3_SLOTS", i)
    blk = src[i:j]
    bad = []
    for m in re.finditer(r'en: "((?:[^"\\]|\\.)*)"', blk):
        v = m.group(1)
        if "【" in v or "】" in v:
            bad.append("全角括号：" + v[:40])
        if CJK.search(v):
            bad.append("含汉字：" + v[:40])
        if re.match(r"^\[[^\]]*\]\S", v):
            bad.append("`]` 后缺空格：" + v[:40])
        if v.startswith("[") and "]" not in v[:80]:
            bad.append("`[` 没有配对的 `]`：" + v[:40])
    return bad, len(re.findall(r'en: "', blk))


def zh_tips():
    """`nodes.py` 的中文提示（AST 取，能正确合并隐式拼接的多行字符串）。"""
    import ast
    tree = ast.parse(read(NODES))
    out = {}

    class V(ast.NodeVisitor):
        node = None

        def visit_ClassDef(self, n):
            old = self.node
            self.node = n.name
            self.generic_visit(n)
            self.node = old

        def visit_Dict(self, n):
            for k, v in zip(n.keys, n.values, strict=True):
                if isinstance(v, ast.Tuple) and len(v.elts) >= 2 and isinstance(v.elts[1], ast.Dict):
                    for kk, vv in zip(v.elts[1].keys, v.elts[1].values, strict=True):
                        if (isinstance(kk, ast.Constant) and kk.value == "tooltip"
                                and isinstance(vv, ast.Constant) and isinstance(k, ast.Constant)):
                            out["%s.%s" % (self.node, k.value)] = vv.value
            self.generic_visit(n)

    V().visit(tree)
    return out


def en_tips():
    """`H3_TIPS` 的英文（反转义后的真实文本）。"""
    src = read(I18N_JS)
    i = src.index("export const H3_TIPS = {")
    j = src.index("\nexport const H3_SLOTS", i)
    out, cur = {}, None
    for line in src[i:j].splitlines():
        m = re.match(r'^    "([^"]+)": \{$', line)
        if m:
            cur = m.group(1)
            continue
        if cur and line.strip().startswith("en:"):
            raw = line.strip()[3:].strip().rstrip(",").strip().strip('"')
            out[cur] = raw.replace("\\n", "\n").replace('\\"', '"')
            cur = None
    return out


def f3_alignment():
    """中英行数不等（且不在豁免表里）的条目。"""
    zh, en = zh_tips(), en_tips()
    bad = []
    for k, t in zh.items():
        e = en.get(k)
        if e is None:
            bad.append((k, "英文缺失", 0, 0))
            continue
        a, b = len(t.split("\n")), len(e.split("\n"))
        if a != b and k not in EXEMPT_ALIGN:
            bad.append((k, "行数不等", a, b))
    return bad, len(zh)


def main():
    fails = []
    print("=" * 78)
    print("前端静态一致性机检（零 GPU / 零浏览器）")
    print("=" * 78)

    hits = f1_untranslated()
    print("\n[F1] 未翻译的可见中文（`web/*.js`，不含 i18n 词表自身）")
    print("     实得 %d ｜ 基线 %d（只许减不许增）" % (len(hits), UNTRANSLATED_BASELINE))
    if len(hits) > UNTRANSLATED_BASELINE:
        fails.append("F1 未翻译中文 %d > 基线 %d" % (len(hits), UNTRANSLATED_BASELINE))
        print("     🔴 新增了未翻译的可见中文（英文界面下会露出来）：")
        for name, ln, text in hits[:12]:
            print("        %s:%d  %s" % (name, ln, text))
        print("     修法：包进 `T(\"中文\", \"English\")`，再把基线减 1。")
    else:
        print("     ✅ 未超基线（可减：把某条包进 `T(…)` 后把基线减 1）")

    bad2, n2 = f2_en_shape()
    print("\n[F2] `H3_TIPS` 英文形状（%d 条）" % n2)
    if bad2:
        fails.append("F2 英文形状 %d 处" % len(bad2))
        for b in bad2[:12]:
            print("     🔴 " + b)
    else:
        print("     ✅ 无全角括号 / 无汉字 / `]` 后都留了空格")

    bad3, n3 = f3_alignment()
    print("\n[F3] 中英行数一致（%d 条；豁免 %d 条）" % (n3, len(EXEMPT_ALIGN)))
    if bad3:
        fails.append("F3 行数不等 %d 条" % len(bad3))
        for k, why, a, b in bad3[:12]:
            print("     🔴 %s：%s（zh %d 行 / en %d 行）" % (k, why, a, b))
        print("     若是**断行不同、内容已对齐** ⇒ 加进 `EXEMPT_ALIGN` 并写明理由；")
        print("     若是**英文落后于中文** ⇒ 改英文（这才是它要抓的东西）。")
    else:
        print("     ✅ 全部一致（或已在豁免表里）")

    print("\n" + "=" * 78)
    if fails:
        print("结果：失败 %d 项 —— %s" % (len(fails), "；".join(fails)))
        return 1
    print("结果：通过 3 / 失败 0")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
