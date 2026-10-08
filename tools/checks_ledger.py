#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据台账 —— `review_050.py` 的机检**逐条登记**（编号 / 类别 / 盯的口径 / 变异测试 / 读的环境量）。

【为什么要有它】（2026-10-08 立）
本仓机检从 19 条长到 100+ 条，但**没有任何一处回答「我们到底有哪些判据、每条管什么、谁盯口径数字」**。
后果实测过两次：
  · 「口径数字」里 **JS 测试的 4 个期望数**（`43/0` / `29/0` / `45/0` / `33/0`）**从来没有机检**
    ⇒ 实测 `ci.yml` 头注释写 `22/0`、执行行写 `29/0`、真值 `29/0` —— **同文件自相矛盾而无人发现**；
  · `CONTRIBUTING.md` 里也写着一个口径数字，而它不在任何扫描集里。
⇒ 台账把「有哪些判据 / 每条盯什么 / 有没有变异测试 / 读哪些环境量」变成**一份可机检的表**。

【三个用法】
    python tools/checks_ledger.py --write   # 抽编号与标题，重写 `tools/CHECKS.md`
    python tools/checks_ledger.py --check   # 核：台账 ↔ `review_050.py` 编号**双向一致**；`CHECKS.md` 与台账一致
    python tools/checks_ledger.py --list [--caliber]   # 列台账（`--caliber` 只列盯口径数字的）

【纪律】
· 🔴 **新增一条带编号的判据，必须在本文件里登记**，否则 `--check` 红（`review_050.py` 的 **H4c** 跑的就是它）。
· `mutation` 为空 = 该条**没有变异测试**。`--check` 会把它列出来，**但不判红** —— 覆盖率是目标，不是门槛。
· `caliber` 非空 = 这条判据盯的是**口径数字**（`--caliber` 只列这些）。
· `env` 非空 = 这条判据读了**环境里的量**（给 `env_matrix` 用；见 `CONTRIBUTING.md` 坑⑨）。
"""
from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R50 = os.path.join(KIT, "tools", "review_050.py")
DOC = os.path.join(KIT, "tools", "CHECKS.md")
_ID_RE = re.compile(r"^(H1|H3[a-z]|H4[a-z]|I[0-9]|J[0-9]|L[0-9]+|E[0-9]+)")

# ── 类别 ──────────────────────────────────────────────────────────────────
# consistency = 一致性判据（同一数字多处声明 ⇒ 核各处相等）
# truth       = 真值判据（从被测对象**真算一遍**再与声明比）
# hygiene     = 卫生 / 静态扫描（读码或读文本，找不该出现的形态）
# contract    = 契约 / 行为（跑真代码，核返回形状与不变量）
# gate        = 外部闸（转发另一个工具/服务的退出码）
KIND = {
    "H1": "consistency", "H3b": "consistency", "H3c": "consistency", "H3d": "consistency",
    "H3f": "consistency", "H3g": "consistency", "H3h": "consistency", "H3i": "consistency",
    "H3j": "consistency", "H3z": "consistency",
    "H3u": "truth", "H3v": "truth", "H3y": "truth", "H4b": "truth",
    "H4c": "truth", "H4d": "truth", "H4e": "truth",
    "H4f": "hygiene",
    "H3e": "hygiene", "H3l": "hygiene", "H3m": "hygiene", "H3n": "hygiene", "H3o": "hygiene",
    "H3p": "hygiene", "H3q": "hygiene", "H3r": "hygiene", "H3s": "hygiene", "H3t": "hygiene",
    "H3w": "hygiene", "H3x": "hygiene", "H4a": "hygiene",
    "H3k": "gate", "L13": "gate",
    "I1": "hygiene", "I2": "hygiene", "I3": "hygiene", "I4": "consistency",
    "J1": "hygiene", "J3": "hygiene",
    "E1": "consistency", "E2": "consistency", "E3": "hygiene",
    "L1": "contract", "L2": "contract", "L3": "contract", "L4": "contract", "L5": "contract",
    "L6": "contract", "L7": "contract", "L8": "contract", "L9": "hygiene", "L10": "hygiene",
    "L11": "hygiene", "L12": "hygiene",
}

# ── 盯的口径数字（口径 = 「描述某个可数事实、且被机检盯着」的数）────────────────
# 值为「这个数字是什么」，供 `--caliber` 打印与人读
CALIBER = {
    "H1": "版本号（`__init__.py` / `pyproject.toml` / `CHANGES.md` 顶部 / `README.md` 首部 四处）",
    "H3b": "`CONTRIBUTING.md` 的方面数 + 断言数",
    "H3c": "`docs/08-testing.md` 的断言数 + 方面数 + 分组表条数",
    "H3d": "`nodes.py` 模块头注释声明的节点数",
    "H3f": "`ci.yml` 里 `test_relay_core` 的期望数（头注释 + 执行行**每一处**）",
    "H3g": "`review_050` 自己的期望数（ci.yml / docs/08 / tools/README / README）",
    "H3h": "V3 真值三元组：节点数 / input 数 / 逐字段项数",
    "H3i": "`assert_default_exit` 的期望数",
    "H3j": "`smoke_nodes` 的期望数",
    "H3u": "registry 包文件数（`.comfyignore` 注释里声明）",
    "H3v": "`tools/` 脚本数（`N 个脚本`）",
    "I4": "示例图节点数（`examples/README.md` 的「N 节点」）",
    "H4d": "**JS 测试期望数 ×4**（`43/0` / `29/0` / `45/0` / `33/0`；ci.yml + docs/08）",
    "H4e": "**dist 白名单文件数**（`make_minimal_bundle.py` 的 `MANIFEST` 总长 vs 文档声明）",
}

# ── 变异测试（注入什么 ⇒ 这一条**必须**变红）────────────────────────────────
# 只覆盖**高危**的那批；其余留空 = 未覆盖（`--check` 会列出来）
MUTATION = {
    "H3e": "在任一受控文本里插一行本机盘符路径（如 `X:/tmp/a`）",
    "H3o": "把某个受控 `*.md` 里的仓库内链接改成不存在的路径",
    "H3u": "把 `.comfyignore` 注释里的包文件数改成 47",
    "H3v": "把 `tools/README.md` 的「15 个脚本」改成「14 个脚本」",
    "H3w": "在 `tools/` 下造一个未跟踪的探针脚本（`tools/<probe>.py`）",
    "H3x": "在任一受控 `*.py` 里写 `setdefault` 到 `PYTHONPATH`",
    "H3y": "把 `tools/README.md` 里某个脚本名的所有出现删掉",
    "H3z": "把 `RELEASING.md` 里某个 `release.py` 的 flag 改名",
    "H4a": "加一处 `subprocess.run([sys.executable, \"-c\", \"pass\"])`（不传 `env=`）",
    "H4b": "注入 `ck(\"X\", True)`（恒真）或两条同名 `ck()`",
    "H4c": "从本台账删掉一个编号（或 `review_050` 里加一条未登记的编号）",
    "H4d": "把 `ci.yml` 头注释里 `test_run_id_parity` 的期望数改成 22",
    "H4e": "把 `make_minimal_bundle.py` 的 `MANIFEST_RUNTIME` 删一项（42 → 41）",
    "H4f": "把任一受控 `*.py` 改成「`return` 后直接跟 `open(`」的形态",
    "H3f": "把 `ci.yml` 里 `test_relay_core` 的期望数改成 562",
    "H3g": "把 `docs/08-testing.md` 里 `review_050` 的期望数改成 105",
    "H3h": "把 `ci.yml` 里 V3 的 input 数改成 121",
    "H3i": "把 `tools/README.md` 里 `assert_default_exit` 的期望数改成 4/4",
    "H3j": "把 `docs/08-testing.md` 里 `smoke_nodes` 的期望数改成 15/0",
    "H3k": "把 `pyproject.toml` 的 `license` 改成裸 SPDX 字符串",
    "L13": "改一个中文源节而不动对应 `*_EN.md`",
}

# ── 读的环境量（给 `env_matrix` 用）──────────────────────────────────────────
# git   = 读工作树/索引状态（`git ls-files` / `git status`）
# node  = 需要 `node` 可执行文件（PATH）
# host  = 需要宿主 ComfyUI（`COMFYUI_PATH` / 真宿主）
# browser = 需要浏览器（CI 没有）
ENV = {
    "H3e": "git", "H3m": "git", "H3o": "git", "H3u": "git", "H3w": "git",
    "H4c": "git", "H3y": "git",
    "H4d": "node", "H3q": "git",
    "H3h": "host", "H3i": "host", "L13": "none",
    # 🔴 2026-10-08 补：**I2 读宿主资源**（放大权重的候选清单）。
    #   宿主**没有**那个权重时（CI 就是），候选项会塌缩成单点兜底串 ⇒ 「值不在候选内」是
    #   **前提失效**而不是错位。实测：CI 报 `I2 红`、本机绿 —— **典型的「本地绿 CI 红」**。
    #   已改为「前提失效时**跳过并出声**」；`tools/ci_env_repro.py` 那一套可复现这一档。
    "I2": "host",
}


def ids_in_review_050() -> dict:
    """从 `review_050.py` 抽「编号 → 标题」（AST，不执行它）。"""
    tree = ast.parse(Path(R50).read_text(encoding="utf-8"))
    out = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "ck"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        s = node.args[0].value
        m = _ID_RE.match(s)
        if m:
            out.setdefault(m.group(1), s.split("（")[0].split("(")[0].strip())
    return out


def render(titles: dict) -> str:
    ids = sorted(titles, key=lambda x: (x[0], x[1:].zfill(2)))
    n_cal = sum(1 for i in ids if i in CALIBER)
    n_mut = sum(1 for i in ids if i in MUTATION)
    L = []
    L.append("# `review_050.py` 判据台账")
    L.append("")
    L.append("> 🔴 **本文件由 `tools/checks_ledger.py --write` 生成，别手改**（改台账 → 重新生成）。")
    L.append("> 机检：`review_050.py` 的 **H4c** 核「本台账 ↔ `review_050.py` 的编号**双向一致**」。")
    L.append("")
    L.append("共 **%d** 条带编号的判据 ｜ 其中盯**口径数字**的 **%d** 条 ｜ 有**变异测试**的 **%d** 条。"
             % (len(ids), n_cal, n_mut))
    L.append("")
    L.append("## 一、盯「口径数字」的判据（**这些数字不许裸写**）")
    L.append("")
    L.append("| 编号 | 类别 | 盯什么数字 | 变异测试 | 读环境 |")
    L.append("|---|---|---|---|---|")
    for i in ids:
        if i in CALIBER:
            L.append("| **%s** | %s | %s | %s | %s |"
                     % (i, KIND.get(i, "hygiene"), CALIBER[i],
                        MUTATION.get(i, "—"), ENV.get(i, "—")))
    L.append("")
    L.append("## 二、其余判据")
    L.append("")
    L.append("| 编号 | 类别 | 管什么 | 变异测试 | 读环境 |")
    L.append("|---|---|---|---|---|")
    for i in ids:
        if i not in CALIBER:
            L.append("| **%s** | %s | %s | %s | %s |"
                     % (i, KIND.get(i, "hygiene"), titles[i].split(" ", 1)[-1][:80],
                        MUTATION.get(i, "—"), ENV.get(i, "—")))
    L.append("")
    L.append("## 三、未覆盖变异测试的编号（**目标，不是门槛**）")
    L.append("")
    miss = [i for i in ids if i not in MUTATION]
    L.append("共 %d 条：`%s`" % (len(miss), "` `".join(miss)))
    L.append("")
    L.append("> 变异测试 = 「注入必违规 ⇒ 这条必须变红」。没有它的判据，**强弱未被验证过** ——")
    L.append("> 实测 2026-10-08：`H4a` 第一版只认 `subprocess.*` / `_sp.*` 别名，**换名就绕过**，")
    L.append("> 是变异测试打回来的；`H3v` 第一版因为文档写中文数字而**整条空转**，也是变异测试抓的。")
    L.append("")
    return "\n".join(L)


def cmd_check() -> int:
    titles = ids_in_review_050()
    bad = []
    not_reg = sorted(i for i in titles if i not in KIND and i not in CALIBER)
    not_in_r50 = sorted((set(KIND) | set(CALIBER) | set(MUTATION)) - set(titles))
    if not_reg:
        bad.append("`review_050.py` 里这些编号**没在本台账登记**：%s" % not_reg)
    if not_in_r50:
        bad.append("本台账里这些编号**在 `review_050.py` 里不存在**（判据被删了？）：%s" % not_in_r50)
    if not os.path.isfile(DOC):
        bad.append("缺 `tools/CHECKS.md`（跑 `--write` 生成）")
    else:
        want = render(titles)
        have = Path(DOC).read_text(encoding="utf-8")
        if want.strip() != have.strip():
            bad.append("`tools/CHECKS.md` 与台账不一致（跑 `--write` 重新生成）")
    if bad:
        print("❌ 台账不一致：")
        for b in bad:
            print("   - " + b)
        return 1
    n_mut = sum(1 for i in titles if i in MUTATION)
    print("✅ 台账一致：%d 条编号判据 ｜ 变异测试覆盖 %d 条（未覆盖 %d 条）"
          % (len(titles), n_mut, len(titles) - n_mut))
    print("   （未覆盖的：%s）" % " ".join(i for i in sorted(titles) if i not in MUTATION))
    return 0


def main() -> int:
    a = sys.argv[1:]
    titles = ids_in_review_050()
    if "--write" in a:
        # ⚠️ 显式 `newline="\n"`：本仓 `.gitattributes` 是 `* text=auto eol=lf`，
        #   而 Windows 上 Python 文本模式默认写 CRLF ⇒ 生成物会与 CI checkout 的 LF 不一致。
        #   （`--check` 靠文本模式读回时会归一化，所以**不会误报**；但生成物本身该是 LF。）
        with open(DOC, "w", encoding="utf-8", newline="\n") as _fh:
            _fh.write(render(titles))
        print("✅ 已写 %s（%d 条判据）" % (os.path.relpath(DOC, KIT), len(titles)))
        return 0
    if "--check" in a:
        return cmd_check()
    if "--list" in a:
        for i in sorted(titles, key=lambda x: (x[0], x[1:].zfill(2))):
            if "--caliber" in a and i not in CALIBER:
                continue
            print("%-5s %-12s %-4s %s" % (i, KIND.get(i, "hygiene"),
                                          "MUT" if i in MUTATION else "   ",
                                          CALIBER.get(i, titles[i])))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
