#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
r"""en_sync.py — 英文文档同步闸（**中文 = 唯一源，`*_EN.md` = 派生物**）。

## 为什么需要它

本仓的英文文档是**人手（或 agent）写**的：机翻会把「0.0007 / 22 帧 / −3~−4 dBFS」这类
硬事实和 `settle_frames` 这类标识符**静默改掉**，而 `review_050` 的机检**只扫中文源**
⇒ 英文版会变成**监管盲区**（错了没人知道，还是发给用户的那一份）。
所以本工具**不做翻译**，只做三件事：

1. **盯同步**：源改了、英文没跟上 ⇒ **红**（节级定位，不是一句 hash 不匹配）；
2. **自动重写机械面**：版本号、文件头标记、索引表缺行 —— 这些不需要翻译，别让它漂；
3. **生成任务书**：把「变了哪节 + 该节原文 + 术语表 + 硬约束」吐出来，交给译者或 LLM，
   让「再翻一次」变成一条命令，而不是一次人工考古。

## 三条铁律（设计约束，改这个脚本前先读）

1. **单一源**：中文文件是唯一可编辑的真相源；`*_EN.md` 一律是派生物。
   ⇒ 英文版里**不许出现**中文源没有的断言数字（那会变成第二个真相源）。
2. **默认必须译**：源里**新增**的节默认 `mode=todo` ⇒ **闸红**。要跳译必须在清单里
   显式登记 `mode=omit` + 理由（`--omit`）。**「漏译」因此从人眼问题变成机检问题。**
3. **fail-closed**：清单缺失/损坏、源文件读不到、编码错 ⇒ **退出码 2** 并明说
   「闸没生效」，**绝不返回 0**。宁可挡住提交，也不放过一个假绿。

## 退出码

| 码 | 含义 | 谁能放行 |
|---|---|---|
| 0 | 全部同步 | —— |
| 1 | 有未同步/未登记（节级列出） | 翻译后 `--stamp`，或 `--ack` 登记暂缓（有台账、到期重新变红） |
| 2 | **闸失效**（清单缺/坏、源缺、编码错） | 修环境，不要绕过 |

## 触发方式（三层，各司其职）

| 层 | 触发 | 行为 |
|---|---|---|
| CI | `review_050`（CI 已跑）里的 `en-sync` 一项 | **阻塞**（唯一硬闸；不另建 CI 步骤，避免同一口径两处声明） |
| 本地 | `tools/review_050.py`（本仓离线自测四件套之一，见 `docs/08`） | **同一口径**：本地红 = CI 红 |
| 打包 | `tools/make_minimal_bundle.py` 写出前 | **fail-closed**：英文过期就不出包 |

**刻意不做 pre-commit 阻塞钩子**：本仓多会话共用工作树（别人的在飞文件随时在），
阻塞式钩子会绊住别人；提交前请自己跑 `python tools/en_sync.py`（CONTRIBUTING 有写）。

## 用法

    python tools/en_sync.py                    # = --check（CI/本地闸）
    python tools/en_sync.py --status           # 人读报告（每节新鲜/过期/ack）
    python tools/en_sync.py --brief 7.3        # 生成某节的增量翻译任务书（交译者/LLM）
    python tools/en_sync.py --apply            # 只重写机械面（版本号/标记/索引缺行），不碰散文
    python tools/en_sync.py --stamp            # 翻译完成后登记（源 hash + 英文 hash）
    python tools/en_sync.py --omit 0. 目录 --why "导航表对英文版无意义"
    python tools/en_sync.py --ack 7.3 --why "本轮先合中文，英文稍后补" --until 2026-10-15

零第三方依赖（仅标准库），零 GPU。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sys

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(KIT, "tools", "en_sync.json")
GLOSSARY = os.path.join(KIT, "tools", "en_glossary.json")

# 🔴 加一对文档 = 在这里加一行（清单状态由 --stamp 生成，不用手写 JSON）
#    默认档：full = 逐节全文译；condensed = 允许精简（节仍在，信息可压缩）
PAIRS = [
    ("README.md", "README_EN.md", "condensed"),
    ("docs/01-mechanism.md", "docs/01-mechanism_EN.md", "full"),
    ("docs/02-parameters.md", "docs/02-parameters_EN.md", "full"),
    ("docs/03-sampling-and-design.md", "docs/03-sampling-and-design_EN.md", "full"),
    ("docs/04-canvas-and-widgets.md", "docs/04-canvas-and-widgets_EN.md", "full"),
    ("docs/05-troubleshooting.md", "docs/05-troubleshooting_EN.md", "full"),
    ("docs/06-continuity-scripting.md", "docs/06-continuity-scripting_EN.md", "full"),
    ("docs/07-chain.md", "docs/07-chain_EN.md", "full"),
    ("docs/08-testing.md", "docs/08-testing_EN.md", "full"),
    ("docs/09-metrics.md", "docs/09-metrics_EN.md", "full"),
    ("docs/10-audio-seam-and-concat.md", "docs/10-audio-seam-and-concat_EN.md", "full"),
]

EXIT_OK, EXIT_STALE, EXIT_BROKEN = 0, 1, 2

SEC_RE = re.compile(r"^## (.+?)\s*$", re.M)
NUM_RE = re.compile(r"^(\d+(?:\.\d+)*)")
VER_ZH = re.compile(r"^\|\s*版本\s*\|\s*\*\*([\d.]+)\*\*", re.M)
VER_EN = re.compile(r"^\|\s*Version\s*\|\s*\*\*([\d.]+)\*\*", re.M)
# 🔴 `^` 锚定（2026-10-05 修）：原正则**不锚行首** ⇒ 文件正文里只要**出现过这个字样**
#   （`docs/08-testing_EN.md` 的表格里就有 `` `<!-- EN-SYNC -->` `` 一行）就被判成「标记已在」
#   ⇒ 那份文件**根本没有标记**却一路绿。判据必须落在「文件头那一行」，不是「文本里出现过」。
MARK_RE = re.compile(r"^<!--\s*EN-SYNC(?P<body>[^>]*)-->", re.M)
LINK_RE = re.compile(r"\]\(([^)\s#:]+\.md)\)")


def die(msg):
    print("❌ en_sync 闸失效：%s" % msg)
    print("   ⇒ 退出码 2（fail-closed：闸没生效时**不返回 0**）")
    return EXIT_BROKEN


def read(path):
    with open(os.path.join(KIT, path), encoding="utf-8") as fh:
        return fh.read()


def sha(text):
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()[:16]


def split_sections(text):
    """按 `## ` 切节 ⇒ [(标题, 本节正文含标题)]，正文前的部分记作 '(preamble)'。"""
    hits = list(SEC_RE.finditer(text))
    out = []
    if not hits:
        return [("(preamble)", text)]
    if hits[0].start() > 0:
        out.append(("(preamble)", text[:hits[0].start()]))
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
        out.append((m.group(1).strip(), text[m.start():end]))
    return out


def num_of(head):
    m = NUM_RE.match(head)
    return m.group(1) if m else None


def en_variant(target):
    """链接目标归一：`x_EN.md` 与 `x.md` 视为同一个文档。"""
    return target.replace("_EN.md", ".md")


def load_manifest():
    if not os.path.isfile(MANIFEST):
        return None
    with open(MANIFEST, encoding="utf-8") as fh:
        return json.load(fh)


def save_manifest(doc):
    with open(MANIFEST, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")


def load_glossary():
    if not os.path.isfile(GLOSSARY):
        return {}
    with open(GLOSSARY, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# 节映射的种子推定（--stamp 用；推定结果会被写进清单，带 seeded 来源标记）
# ---------------------------------------------------------------------------
def seed_mapping(zh_secs, en_secs, has_prev):
    """返回 {zh标题: (en标题|None, 依据)}。

    ① 先按标题前导编号配对（7.3 ↔ 7.3、13 ↔ 13）；
    ② 剩下的两边各自按出现顺序配对（`docs/` 那两对的标题本来就没编号）；
    ③ 还是配不上的 ⇒ None（+`--stamp` 会把它标成 todo 或 orphan，逼人做决定）。
    """
    mapping, used_en = {}, set()
    en_by_num = {}
    for h, _ in en_secs:
        n = num_of(h)
        if n and n not in en_by_num:
            en_by_num[n] = h
    for zh_head, _ in zh_secs:
        if zh_head in has_prev:                      # 已登记过 ⇒ 沿用（含 omit / 改名）
            en_head = has_prev[zh_head].get("en")
            mapping[zh_head] = (en_head, "kept")
            if en_head:
                used_en.add(en_head)
            continue
        n = num_of(zh_head)
        if n and en_by_num.get(n) not in used_en and n in en_by_num:
            mapping[zh_head] = (en_by_num[n], "by-number")
            used_en.add(en_by_num[n])
    left_zh = [h for h, _ in zh_secs if h not in mapping]
    left_en = [h for h, _ in en_secs if h not in used_en]
    for zh_head, en_head in zip(left_zh, left_en):
        mapping[zh_head] = (en_head, "by-position")
        used_en.add(en_head)
    for zh_head in left_zh[len(left_en):]:
        mapping[zh_head] = (None, "todo")
    return mapping


# ---------------------------------------------------------------------------
# 机械面（不需要翻译、由 --apply 自动重写）
# ---------------------------------------------------------------------------
IDX_HINT = ("文档索引", "Documentation index")


def mechanical(zh, en, zh_secs, en_secs):
    """返回 (问题列表, 自动修复动作列表)。问题=需人处理；动作=--apply 能直接改的。"""
    problems, fixes = [], []

    # M1 版本号（英文首部表若写着版本，必须与中文一致）
    mz, me = VER_ZH.search(zh), VER_EN.search(en)
    if mz and me and mz.group(1) != me.group(1):
        fixes.append(("version", "英文首部版本 %s → %s" % (me.group(1), mz.group(1))))
    elif mz and not me:
        problems.append("英文首部缺 `| Version | **x.y.z** |` 行（中文有版本声明）")

    # M2 文件头同步标记（记录源文件与上次同步日；缺了就是"派生物丢了出处"）
    if not MARK_RE.search(en):
        problems.append("英文缺 `<!-- EN-SYNC … -->` 标记（跑 `--apply` 可补）")
        fixes.append(("marker", "补 `<!-- EN-SYNC … -->` 标记（记录源文件与上次同步日）"))

    # M3 链接目标完整性：中文里链到的每个 .md（在英文里要么原样、要么 `_EN` 变体）都应出现
    zh_links = {en_variant(t) for t in LINK_RE.findall(zh)}
    en_links = {en_variant(t) for t in LINK_RE.findall(en)}
    miss = sorted(t for t in zh_links - en_links if not t.endswith("_EN.md"))
    if miss:
        fixes.append(("index", "英文缺这些文档的入口行：%s" % ", ".join(miss)))
    return problems, fixes


def apply_fixes(zh_path, en_path, zh, en, fixes):
    """只改机械面：版本号 / 标记 / 索引表缺行。返回 (新英文正文, 动作日志)。"""
    log = []
    for kind, what in fixes:
        if kind == "version":
            mz = VER_ZH.search(zh)
            en = VER_EN.sub("| Version | **%s**" % mz.group(1), en, count=1)
            log.append("版本号：" + what)
        elif kind == "marker":
            stamp = datetime.date.today().isoformat()
            line = ("<!-- EN-SYNC src=%s stamped=%s mode=%s -->"
                    % (zh_path, stamp, "see tools/en_sync.json"))
            if en.startswith("# "):
                nl = en.index("\n")
                en = en[:nl] + "\n\n" + line + en[nl:]
            else:
                en = line + "\n" + en
            log.append("标记：" + what)
        elif kind == "index":
            targets = re.findall(r"\]\(([^)\s#:]+\.md)\)", zh)
            have = {en_variant(t) for t in re.findall(r"\]\(([^)\s#:]+\.md)\)", en)}
            add = []
            for t in targets:
                if en_variant(t) in have or t.endswith("_EN.md"):
                    continue
                add.append(t)
            if add:
                rows = "".join(
                    "| [`%s`](%s) | (Chinese only — no English version yet) |\n" % (t, t)
                    for t in add)
                # 插到英文「文档索引」那节的**最后一行表格行**之后
                for head, body in split_sections(en):
                    if any(k in head for k in IDX_HINT):
                        lines = body.rstrip("\n").split("\n")
                        last = max(i for i, ln in enumerate(lines) if ln.startswith("| "))
                        body_new = "\n".join(lines[:last + 1]) + "\n" + rows.rstrip("\n")
                        en = en.replace(body, body_new)
                        break
                else:
                    en = en.rstrip("\n") + "\n\n" + rows
                log.append("索引表补行：%s" % ", ".join(add))
    return en, log


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def status_of_pair(pair, man_pair, zh, en):
    """返回 (problems, notes, stats)。"""
    zh_secs, en_secs = split_sections(zh), split_sections(en)
    problems, notes = [], []
    prev = {s["zh"]: s for s in (man_pair or {}).get("sections", [])}
    mapping = seed_mapping(zh_secs, en_secs, prev)
    en_heads = {h for h, _ in en_secs}
    body = dict(zh_secs)
    en_body = dict(en_secs)
    now = datetime.date.today()
    acks = {a["zh"]: a for a in (man_pair or {}).get("acks", [])}
    stats = {"n": 0, "fresh": 0, "stale": 0, "todo": 0, "omit": 0, "acked": 0, "orphan": 0}

    for zh_head, _ in zh_secs:
        stats["n"] += 1
        en_head, how = mapping.get(zh_head, (None, "unknown"))
        rec = prev.get(zh_head)
        if how == "todo" or en_head is None:
            mode = (rec or {}).get("mode")
            if mode == "omit":
                stats["omit"] += 1
                continue
            stats["todo"] += 1
            problems.append("源节「%s」**没有英文对应**且未登记（登记：`--omit \"%s\" --why …`）"
                            % (zh_head, zh_head))
            continue
        if en_head not in en_heads:
            stats["todo"] += 1
            problems.append("源节「%s」映射到英文节「%s」，但英文里没有这个标题（改名了？）"
                            % (zh_head, en_head))
            continue
        h_src_now = sha(body[zh_head])
        h_en_now = sha(en_body[en_head])
        need_stamp = rec is None or rec.get("h_src") != h_src_now or rec.get("h_en") != h_en_now
        if how == "by-position":
            notes.append("「%s」↔「%s」是**按序推定**（`--stamp` 后可删掉 seeds 标记复核）"
                         % (zh_head, en_head))
        ack = acks.get(zh_head)
        if ack and now <= datetime.date.fromisoformat(ack["until"]):
            stats["acked"] += 1
            notes.append("⏸ 「%s」已登记暂缓（到 %s）：%s" % (zh_head, ack["until"], ack["why"]))
            continue
        if ack:
            problems.append("源节「%s」的暂缓已于 %s 到期，请翻译后 `--stamp`（原因：%s）"
                            % (zh_head, ack["until"], ack["why"]))
            stats["stale"] += 1
        elif need_stamp:
            stats["stale"] += 1
            why = ("源已改（英文是上一版）" if rec and rec.get("h_src") != h_src_now
                   else "英文被改动但未登记" if rec and rec.get("h_en") != h_en_now
                   else "从未登记")
            problems.append("源节「%s」↔ 英文节「%s」：%s ⇒ 跑 `--brief \"%s\"` 拿任务书，改完 `--stamp`"
                            % (zh_head, en_head, why, num_of(zh_head) or zh_head))
        else:
            stats["fresh"] += 1
    mapped_en = {mapping[h][0] for h in mapping if mapping[h][0]}
    for head, _ in en_secs:
        if head not in mapped_en:
            stats["orphan"] += 1
            problems.append("英文多出「%s」：源里没有对应节（要么源漏了，要么英文自己加戏）" % head)
    return problems, notes, stats


def cmd_check(verbose=False):
    man = load_manifest()
    if man is None:
        return die("清单 %s 不存在（先跑 --stamp 初始化）" % os.path.relpath(MANIFEST, KIT))
    total = {"n": 0, "fresh": 0, "stale": 0, "todo": 0, "omit": 0, "acked": 0, "orphan": 0}
    all_prob, all_note, all_fix = [], [], []
    for zh_path, en_path, default_mode in PAIRS:
        try:
            zh, en = read(zh_path), read(en_path)
        except OSError as exc:
            return die("读不到 %s（%s）" % (zh_path, exc))
        except UnicodeDecodeError as exc:
            return die("%s 不是 UTF-8（%s）" % (zh_path, exc))
        mp = (man.get("pairs") or {}).get(zh_path)
        probs, notes, stats = status_of_pair((zh_path, en_path, default_mode), mp, zh, en)
        mprob, mfix = mechanical(zh, en, split_sections(zh), split_sections(en))
        probs = mprob + probs
        for k in total:
            total[k] += stats.get(k, 0)
        all_prob += ["[%s] %s" % (zh_path, p) for p in probs]
        all_note += ["[%s] %s" % (zh_path, n) for n in notes]
        all_fix += [(zh_path, en_path, f) for f in mfix]

    print("=" * 78)
    print("en_sync · 英文文档同步闸（中文 = 源，*_EN.md = 派生物）")
    print("=" * 78)
    print("  节：%d（新鲜 %d / 未同步 %d / 未登记 %d / 暂缓 %d / 有意不译 %d / 英文多出 %d）"
          % (total["n"], total["fresh"], total["stale"], total["todo"],
             total["acked"], total["omit"], total["orphan"]))
    for n in all_note:
        print("  ℹ️ " + n)
    if all_fix:
        print("  🛠 机械面可自动修（跑 `--apply`，**不碰散文**）：")
        for zh_path, _en, (k, w) in all_fix:
            print("     · [%s] %s" % (zh_path, w))
    if all_prob:
        print("  ❌ 需人处理（%d 项）：" % len(all_prob))
        for p in all_prob:
            print("     - " + p)
        print("  ⇒ 退出码 1：翻译/登记后 `--stamp`；确要延后请 `--ack <节> --why … --until YYYY-MM-DD`")
        return EXIT_STALE
    print("  ✅ 英文文档与中文源同步")
    return EXIT_OK


def cmd_status():
    man = load_manifest()
    if man is None:
        return die("清单不存在（先跑 --stamp 初始化）")
    for zh_path, en_path, default_mode in PAIRS:
        mp = (man.get("pairs") or {}).get(zh_path) or {}
        print("─" * 78)
        print("%s  →  %s   [默认档 %s ｜ 上次登记 %s]"
              % (zh_path, en_path, default_mode, mp.get("stamped", "—")))
        try:
            zh = read(zh_path)
            read(en_path)          # 顺带验证英文文件可读（缺失/编码错要在这里就报出来）
        except OSError as exc:
            print("   ❌ 读不到：%s" % exc)
            continue
        prev = {s["zh"]: s for s in mp.get("sections", [])}
        for head, bodyt in split_sections(zh):
            rec = prev.get(head)
            h = sha(bodyt)
            if rec is None:
                mark = "🆕 未登记"
            elif rec.get("mode") == "omit":
                mark = "⛔ 有意不译（%s）" % rec.get("why", "")
            elif rec.get("h_src") == h:
                mark = "✅ 新鲜"
            else:
                mark = "⚠️ 源已改（英文是 %s 版）" % (rec.get("h_src") or "?")
            print("   %-28s %s" % (head[:28], mark))
        for a in mp.get("acks", []):
            print("   ⏸ ack：%s（到 %s）%s" % (a["zh"], a["until"], a["why"]))
    return EXIT_OK


def cmd_brief(key):
    man = load_manifest() or {}
    gl = load_glossary()
    hit = None
    for zh_path, en_path, default_mode in PAIRS:
        for head, bodyt in split_sections(read(zh_path)):
            if head.startswith(key) or key == num_of(head):
                hit = (zh_path, en_path, head, bodyt)
                break
        if hit:
            break
    if not hit:
        return die("没有匹配 `%s` 的节" % key)
    zh_path, en_path, head, bodyt = hit
    mp = (man.get("pairs") or {}).get(zh_path) or {}
    rec = {s["zh"]: s for s in mp.get("sections", [])}.get(head) or {}
    en_head = rec.get("en")
    cur = ""
    if en_head:
        for h2, b2 in split_sections(read(en_path)):
            if h2 == en_head:
                cur = b2.strip()
                break
    terms = "\n".join("- %s → %s" % (k, v) for k, v in (gl.get("terms") or {}).items())
    print("# 增量翻译任务书 · %s §%s → %s §%s" % (zh_path, head, en_path, en_head or "(新节)"))
    print()
    print("## 硬约束（违反即闸红）")
    print("- **数字、单位、百分比、帧数、dBFS、时间码原样照抄**（含负号与全角字符照原文）")
    print("- **反引号内的标识符、代码块、表格结构与列数、emoji 一律不译不改**")
    print("- 中文里的「」『』译成英文直引号；**不得引入源文没有的断言数字**（英文不是第二个真相源）")
    print("- 结论的方向（默认关/别动/慎用/已作废）必须与中文一致 —— 这是本仓最容易翻错的地方")
    print()
    print("## 术语表（%s）" % os.path.relpath(GLOSSARY, KIT))
    print(terms or "（空）")
    print()
    print("## 源文（中文，已变更）")
    print(bodyt.strip())
    if cur:
        print()
        print("## 当前英文（旧版，供参考改法）")
        print(cur)
    print()
    print("## 收尾（两步都要）")
    print("1. `python tools/en_sync.py --apply`    # 机械面（版本号/标记/索引缺行）")
    print("2. `python tools/en_sync.py --stamp`    # 登记 hash")
    return EXIT_OK


def cmd_apply(write=True):
    # 机械面只看文件本身，**不依赖清单**（首次初始化时清单还不存在）
    changed = 0
    for zh_path, en_path, default_mode in PAIRS:
        zh, en = read(zh_path), read(en_path)
        _p, fixes = mechanical(zh, en, split_sections(zh), split_sections(en))
        if not fixes:
            continue
        new, log = apply_fixes(zh_path, en_path, zh, en, fixes)
        if new != en:
            if write:
                with open(os.path.join(KIT, en_path), "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(new)
            changed += 1
            for ln in log:
                print("  🛠 %s：%s" % (en_path, ln))
    print("  ✅ 机械面%s（%d 个文件）" % ("已重写" if write else "可重写", changed))
    return EXIT_OK


def cmd_stamp():
    man = load_manifest() or {"schema": 1, "pairs": {}}
    man.setdefault("pairs", {})
    today = datetime.date.today().isoformat()
    cmd_apply(write=True)
    todo = []
    for zh_path, en_path, default_mode in PAIRS:
        zh = read(zh_path)
        en = read(en_path)
        zh_secs, en_secs = split_sections(zh), split_sections(en)
        mp = man["pairs"].setdefault(zh_path, {"en": en_path, "sections": [], "acks": []})
        mp["en"] = en_path
        prev = {s["zh"]: s for s in mp.get("sections", [])}
        mapping = seed_mapping(zh_secs, en_secs, prev)
        en_body = dict(en_secs)
        rows = []
        for head, bodyt in zh_secs:
            en_head, how = mapping.get(head, (None, "unknown"))
            old = prev.get(head) or {}
            row = {"zh": head, "en": en_head, "mode": old.get("mode") or default_mode}
            if row["mode"] == "omit":
                row["en"] = None
                row["h_en"] = None
                row["why"] = old.get("why", "（未写理由）")
            elif en_head is None or en_head not in en_body:
                # 🔴 默认必须译：配不上英文的源节 = todo ⇒ 闸红，直到译出来或显式 --omit
                row["mode"] = "todo"
                row["h_en"] = None
                todo.append("%s §%s（%s）" % (zh_path, head, how))
            else:
                row["h_en"] = sha(en_body[en_head])
            row["h_src"] = sha(bodyt)
            if how in ("by-number", "by-position") and old.get("seeded") is None:
                row["seeded"] = how
            elif old.get("seeded"):
                row["seeded"] = old["seeded"]
            if old.get("why") and row["mode"] != "omit":
                row["why"] = old["why"]
            rows.append(row)
        mp["sections"] = rows
        mp["stamped"] = today
        mp["src_sha"] = sha(zh)
    save_manifest(man)
    print("  📝 已写 %s（%s）" % (os.path.relpath(MANIFEST, KIT), today))
    if todo:
        print("  ❌ 这些节**没有英文对应且未登记**（闸会红）：")
        for t in todo:
            print("     - %s" % t)
        print("     处置：译出来，或显式登记 `--omit \"<节标题>\" --why …`")
        return EXIT_STALE
    return EXIT_OK


def cmd_omit(head, why):
    man = load_manifest()
    if man is None:
        return die("清单不存在（先跑 --stamp）")
    hit = False
    for zh_path in [p[0] for p in PAIRS]:
        mp = (man.get("pairs") or {}).get(zh_path) or {}
        for s in mp.get("sections", []):
            if s["zh"] == head or s["zh"].startswith(head):
                s["mode"], s["en"], s["h_en"], s["why"] = "omit", None, None, why
                s.pop("seeded", None)
                print("  ⛔ %s §%s 登记为「有意不译」：%s" % (zh_path, s["zh"], why))
                hit = True
    if not hit:
        return die("清单里没有标题以 `%s` 开头的节" % head)
    save_manifest(man)
    return cmd_check()


def cmd_ack(head, why, until):
    man = load_manifest()
    if man is None:
        return die("清单不存在（先跑 --stamp）")
    for zh_path in [p[0] for p in PAIRS]:
        mp = (man.get("pairs") or {}).get(zh_path) or {}
        for s in mp.get("sections", []):
            if s["zh"] == head or s["zh"].startswith(head):
                acks = [a for a in mp.setdefault("acks", []) if a["zh"] != s["zh"]]
                acks.append({"zh": s["zh"], "why": why, "until": until,
                             "at": datetime.date.today().isoformat()})
                mp["acks"] = acks
                print("  ⏸ %s §%s 暂缓到 %s：%s" % (zh_path, s["zh"], until, why))
                save_manifest(man)
                return cmd_check()
    return die("清单里没有标题以 `%s` 开头的节" % head)


def main():
    ap = argparse.ArgumentParser(description="英文文档同步闸（中文=源，*_EN.md=派生物）")
    ap.add_argument("--check", action="store_true", help="闸：不一致返 1；闸失效返 2（默认动作）")
    ap.add_argument("--status", action="store_true", help="人读报告（逐节新鲜/过期）")
    ap.add_argument("--brief", metavar="节", help="生成增量翻译任务书（节号或标题前缀）")
    ap.add_argument("--apply", action="store_true", help="只重写机械面（版本号/标记/索引缺行）")
    ap.add_argument("--stamp", action="store_true", help="登记（源 hash + 英文 hash）")
    ap.add_argument("--omit", metavar="节", help="登记「有意不译」（必须配 --why）")
    ap.add_argument("--ack", metavar="节", help="登记暂缓（必须配 --why --until）")
    ap.add_argument("--why", default="", help="--omit / --ack 的理由")
    ap.add_argument("--until", default="", help="--ack 的到期日 YYYY-MM-DD")
    a = ap.parse_args()

    if a.status:
        return cmd_status()
    if a.brief:
        return cmd_brief(a.brief)
    if a.apply:
        return cmd_apply(write=True)
    if a.stamp:
        return cmd_stamp()
    if a.omit:
        if not a.why:
            return die("--omit 必须给理由（--why）：跳译是有记录的决策，不是默认行为")
        return cmd_omit(a.omit, a.why)
    if a.ack:
        if not (a.why and a.until):
            return die("--ack 必须给 --why 与 --until（暂缓要有期限）")
        return cmd_ack(a.ack, a.why, a.until)
    return cmd_check()


if __name__ == "__main__":
    sys.exit(main())
