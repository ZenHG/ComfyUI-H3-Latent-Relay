# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Relay-Kit contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""部署副本同步校验 —— 副本是不是就是「那个提交」的内容。

【为什么需要它】
`ComfyUI/custom_nodes/` 下的部署副本是**没有 .git 的拷贝**：它自己无法证明自己是哪个版本。
手工 `cp` 铺过去时漏掉一两个文件、或者某个文件铺的是旧提交态，
副本照样能加载、能跑、不报错 —— 但产线跑的其实不是你以为的那份代码。
2026-09-19 就用 `diff -rq` 误判过一次：本机 `core.autocrlf=true` 让工作树是 CRLF、
提交态是 LF，`diff` 报出 10 个"不一致"，实际只有 3 个文件真旧 + 2 个文件缺失。

所以这个工具：
  1. 比对对象固定为 **git 提交态**（`git show <rev>:<path>`），不是工作树；
  2. **忽略 CRLF/LF 行尾差异**（先 `tr -d '\\r'` 再比）；
  3. 只报 `OK / DIFF / MISS`，不改任何文件。

用法：
    python tools/sync_deploy_check.py "<ComfyUI>/custom_nodes/ComfyUI-H3-Relay-Kit"
    python tools/sync_deploy_check.py <副本目录> --rev 7060109
    python tools/sync_deploy_check.py <副本目录> --rev HEAD --verbose

退出码：0 = 全部 OK；1 = 有 DIFF 或 MISS。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _git(*args: str) -> str:
    p = subprocess.run(["git", *args], cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        sys.exit("[FAIL] git 命令失败：%s\n%s" % (" ".join(args), p.stderr.decode("utf-8", "replace")))
    return p.stdout.decode("utf-8", "replace")


def _norm(b: bytes) -> bytes:
    """抹掉 CR，让 CRLF 与 LF 视为同一内容。"""
    return b.replace(b"\r\n", b"\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="部署副本 vs git 提交态 同步校验")
    ap.add_argument("deploy", help="部署副本目录（custom_nodes 下的那个）")
    ap.add_argument("--rev", default="HEAD", help="要比对的提交（默认 HEAD）")
    ap.add_argument("--verbose", action="store_true", help="DIFF 时打印前若干差异行")
    args = ap.parse_args()

    if not os.path.isdir(args.deploy):
        sys.exit("[FAIL] 副本目录不存在：%s" % args.deploy)

    files = [f for f in _git("ls-files").splitlines() if f.strip()]
    ok, diff, miss = [], [], []
    for f in files:
        src = _git("show", "%s:%s" % (args.rev, f))
        want = _norm(src.encode("utf-8", "surrogateescape"))
        path = os.path.join(args.deploy, f.replace("/", os.sep))
        if not os.path.isfile(path):
            miss.append(f)
            continue
        with open(path, "rb") as fh:
            got = _norm(fh.read())
        (ok if got == want else diff).append(f)

    print("=" * 78)
    print("部署副本：%s" % args.deploy)
    print("比对基准：git %s（提交态，忽略 CRLF 行尾差异）" % args.rev)
    print("=" * 78)
    for f in miss:
        print("  MISS  %s" % f)
    for f in diff:
        print("  DIFF  %s" % f)
    print("-" * 78)
    print("OK %d / DIFF %d / MISS %d（共 %d 个受版本控制的文件）" % (len(ok), len(diff), len(miss), len(files)))

    if args.verbose and diff:
        for f in diff:
            print("\n--- %s ---" % f)
            p = subprocess.run(
                ["git", "--no-pager", "diff", "--no-index", "--", "-", os.path.join(args.deploy, f)],
                cwd=REPO, input=_git("show", "%s:%s" % (args.rev, f)).encode("utf-8"),
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
            print(p.stdout.decode("utf-8", "replace")[:2000])

    if diff or miss:
        print("\n[FAIL] 副本与 %s 不一致。用整文件 `git show %s:<file> > <副本路径>` 重铺（不要 cp -r）。"
              % (args.rev, args.rev))
        return 1
    print("\n[OK] 副本与 %s 完全一致。" % args.rev)
    return 0


if __name__ == "__main__":
    sys.exit(main())
