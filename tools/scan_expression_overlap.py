# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Relay-Kit contributors
"""逐函数「表达层重合度」扫描 —— 与第三方 GPL 包比对。

【为什么需要它】
2026-09-19 第一次合规核查靠"函数/类名交集为 0"就下了"同源只有一段"的结论，
**这是错的**：函数名可以不同而函数体逐字节相同。真正该比的是**函数体**。

本工具按函数切块，逐块算「我方该函数里有多少行在对方源码中**逐字出现**」，
给出重合率与命中的具体行，供人工判定"是巧合 / 是惯例 / 是派生"。

【判据纪律】
- **高重合率 ≠ 派生**。短小的数学循环、ComfyUI 强制 API、Python 惯用法都会天然重合。
- 本工具**只报数、不定性**。定性必须看具体行 + 上下文，由人判定。
- 输出按重合率降序，先看头部。

用法：
    python tools/scan_expression_overlap.py <对方仓库路径>
    python tools/scan_expression_overlap.py <对方仓库路径> --min-lines 6
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 只排除「**光秃秃**的关键字行」—— 它们不含任何表达。
# ⚠ 不要按前缀排除 return/if 等：`return k if covered == n else None` 这种
#   带表达式的行恰恰是最有信号的。第一版就是按前缀排除，把短函数整段滤没了。
_GENERIC = re.compile(
    r"^(?:"
    r"return|pass|break|continue|else|try|finally|"
    r"import\s|from\s+\S+\s+import|"
    r"if\s+__name__|"
    r"[\W\d_]+"
    r")$"
)
_MIN_LEN = 14


def _norm(line: str) -> str:
    """去掉空白与注释后的"表达指纹"；空串表示该行不参与比对。"""
    s = line.strip()
    if s.startswith(("#", "//", "*", '"""', "'''")):
        return ""
    s = re.sub(r"\s+", "", s)
    if len(s) < _MIN_LEN:
        return ""
    if _GENERIC.match(s):
        return ""
    return s


def _load_lines(paths) -> set:
    out = set()
    for p in paths:
        try:
            for ln in io.open(p, encoding="utf-8", errors="replace"):
                n = _norm(ln)
                if n:
                    out.add(n)
        except OSError:
            pass
    return out


def _py_files(root):
    got = []
    for r, dirs, fs in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
        got += [os.path.join(r, f) for f in fs if f.endswith((".py", ".js"))]
    return got


def _functions(path):
    """粗切顶层函数块（(名字, 起始行, 行列表)）。够用即可，不求 AST 完备。"""
    with io.open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    out, cur, start = [], None, 0
    for i, ln in enumerate(lines):
        m = re.match(r"\s*def\s+(\w+)\s*\(", ln)
        if m and not ln[:1].isspace():
            if cur:
                out.append((cur, start, lines[start:i]))
            cur, start = m.group(1), i
    if cur:
        out.append((cur, start, lines[start:]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("theirs", help="对方仓库路径")
    ap.add_argument("--min-lines", type=int, default=5,
                    help="只报代码行数 >= 该值的函数（默认 5）")
    ap.add_argument("--top", type=int, default=25, help="只打印前 N 条")
    args = ap.parse_args()

    if not os.path.isdir(args.theirs):
        print("[SKIP] 对方仓库不在：%s" % args.theirs)
        return 0

    their_lines = _load_lines(_py_files(args.theirs))
    print("对方有表达行：%d 条" % len(their_lines))
    print("我方扫描根：%s" % REPO)
    print("=" * 78)

    rows = []
    for p in _py_files(REPO):
        rel = os.path.relpath(p, REPO)
        for name, start, body in _functions(p):
            mine = [_norm(l) for l in body]
            mine = [m for m in mine if m]
            if len(mine) < args.min_lines:
                continue
            hit = [m for m in mine if m in their_lines]
            rows.append((len(hit) / len(mine), len(hit), len(mine), rel, name, hit))

    rows.sort(reverse=True)
    flagged = 0
    for ratio, hit, total, rel, name, hits in rows[: args.top]:
        if hit == 0:
            continue
        # 「全命中」比高比例更值得看：短函数整段一致 = 强信号，
        # 而 5% 也可能是巧合。两者都要露出。
        full = "🔴全命中" if hit == total else ""
        mark = "🔴" if ratio >= 0.5 else ("⚠️ " if ratio >= 0.25 else "  ")
        if ratio >= 0.25:
            flagged += 1
        print("%s %5.0f%%  %2d/%-2d  %s :: %s() %s"
              % (mark, ratio * 100, hit, total, rel, name, full))
        if ratio >= 0.25 or hit == total:
            for h in hits[:8]:
                print("           %s" % h[:104])
    print("=" * 78)
    print("重合率 >=25%% 的函数：%d 个（共扫 %d 个函数）" % (flagged, len(rows)))
    print("⚠️ 本工具只报数不定性 —— 短小数学循环 / ComfyUI 强制 API / Python 惯用法")
    print("   都会天然重合。是否构成派生，必须看具体行与上下文，由人判定。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
