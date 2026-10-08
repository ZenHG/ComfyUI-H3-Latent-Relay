#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""环境矩阵复跑 —— 同一批闸在**多个合成环境**下各跑一遍，要求结果**逐项相同**。

【为什么要有它】（2026-10-08 立）
本仓最高频的返工病因 = 「**判据 / 工具只在一种环境下被验证过**」（`CONTRIBUTING.md` 坑⑨，
实测 15 次）。`tools/user_smoke.py` 2026-10-07 那个 bug 就是它：
子进程用 `setdefault` 到 `PYTHONPATH` ⇒ 调用者已设时**静默 no-op** ⇒
**CI 绿、本机红**（CI 的 workflow 恰好把它设成了宿主根）。

【它做什么】
把 `GATES` 里的每条闸在 `ENVS` 里每个环境下各跑一遍，比较「退出码 + 结果行」。
**全部逐项相同 ⇒ 通过**；有任何一处不同 ⇒ **红**，并列出差异表。

【范围纪律（诚实写清）】
· 只覆盖**已知的**环境轴。本文件当前只跑 `PYTHONPATH` 一条 —— 因为它有**确定的期望**
  （**任何一条闸都不该被调用者的 `PYTHONPATH` 影响**）。
· `COMFYUI_PATH` **没有**进来：它对某些闸是**必需**的（`user_smoke` 没它就必然失败），
  那是**设计意图**而不是缺陷 ⇒ 要放进来的话得配一张"合法差异"白名单，而白名单本身会腐。
  ⇒ **宁可不做，也不做一个需要维护白名单的判据。**
· 想扩轴（PATH / LANG / TZ / 无 node…）之前，先回答：「这条轴上，什么结果是**不该**变的？」

【跑法】
    python tools/env_matrix.py              # 全部闸 × 全部环境
    python tools/env_matrix.py --gate review_050    # 只跑一条闸
    COMFYUI_PATH=<根> python tools/env_matrix.py    # 宿主根由调用者给（本工具不改它）
"""
from __future__ import annotations

import os
import subprocess
import sys

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable

# 环境轴：名字 → 额外环境变量（叠加在调用者环境之上）
# 🔴 每一条都要有**确定的期望**：这条轴上，结果**不该**变。
ENVS = {
    "clean": {},                                          # 干净：照调用者的环境
    "dirty-pythonpath": {"PYTHONPATH": "/nonexistent"},   # 脏：塞一个假 PYTHONPATH
    "empty-pythonpath": {"PYTHONPATH": ""},               # 空串（与"未设"不同，历史上踩过）
}

# 闸：名字 → 命令（相对仓库根）。只放**零 GPU、秒级**的。
GATES = {
    "review_050": [PY, "tools/review_050.py"],
    "user_smoke": [PY, "tools/user_smoke.py"],
}


def _result_line(out: str) -> str:
    for ln in (out or "").splitlines():
        if ln.startswith("结果："):
            return ln.strip()
    return (out or "").strip().splitlines()[-1][:120] if (out or "").strip() else "(无输出)"


def main() -> int:
    only = None
    if "--gate" in sys.argv:
        only = sys.argv[sys.argv.index("--gate") + 1]
    gates = {k: v for k, v in GATES.items() if only is None or k == only}
    if not gates:
        print("⛔ 没有匹配的闸：%r（可选：%s）" % (only, " / ".join(GATES)))
        return 2

    rows, bad = [], []
    for gname, cmd in gates.items():
        seen = {}
        for ename, extra in ENVS.items():
            env = dict(os.environ)
            env.update(extra)
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", cwd=KIT, env=env)
            key = (r.returncode, _result_line(r.stdout))
            seen[ename] = key
            rows.append((gname, ename, key))
        vals = set(seen.values())
        if len(vals) > 1:
            bad.append((gname, seen))

    w = max(len(g) for g in gates) + 1
    print("环境矩阵（同一批闸 × %d 个环境；要求**逐项相同**）" % len(ENVS))
    print("=" * 78)
    for gname, ename, (rc, line) in rows:
        print("  %-*s %-18s rc=%d  %s" % (w, gname, ename, rc, line))
    print("=" * 78)
    if bad:
        print("🔴 有闸**随环境变化** —— 说明它读了调用者的环境（CI 与本机就会不一致）：")
        for gname, seen in bad:
            print("   · %s：" % gname)
            for ename, (rc, line) in seen.items():
                print("       %-18s rc=%d  %s" % (ename, rc, line))
        print("   ⇒ 修法：让工具**自己**构造子进程环境（见 `tools/user_smoke.py` 的 `_child_env()`），")
        print("      或显式声明「本工具依赖 X」。别让结果取决于调用者。")
        return 1
    print("✅ 全部闸在所有环境下**逐项相同**（%d 条闸 × %d 个环境）" % (len(gates), len(ENVS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
