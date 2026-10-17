# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""给 sync_deploy_check.py 加「活体验证」：字节一致 ≠ 生效。

背景（2026-10-14 GG 指正）：我只 curl 了静态 JS 就宣称"前端零重启即生效" ——
那只是**文件已就位**这个前置条件，**不是前端行为验证**。真正会让新 widget /
新 tooltip 进画布的，是宿主内存里的那份 nodes.py ⇒ 必须重启。

根治：把「忘了重启」变成**机检会红**。字节比对通过后，再问一次活宿主的
`/h3relay/health`：里面的 `version` 与仓库 `__version__` 不一致 ⇒ FAIL。
宿主没运行 ⇒ 只提醒、**退出 0**（那种环境判不了，不该记失败）。
"""
import io
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _repo_version() -> str:
    """从包入口读仓库版本号（不 import，避免拉起 torch/comfy）。"""
    path = os.path.join(REPO, "__init__.py")
    with io.open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("__version__"):
                return line.split("=", 1)[1].strip().strip("\"'")
    return ""


def _health_url() -> str:
    return os.environ.get("H3RELAY_HEALTH_URL") or "http://127.0.0.1:8188/h3relay/health"


def live_check(expected: str) -> int:
    """问活宿主跑的是哪个版本。0 = 一致或问不到（都退出 0）/ 1 = 不一致（红）。"""
    url = _health_url()
    try:
        import json
        import urllib.request

        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:
        print("\n⚠️ 活体未验证：问不到 %s（%s）" % (url, type(exc).__name__))
        print("    ⇒ 本机此刻没有跑着的宿主，**无法**判断它加载的是哪一版。")
        print("    ⇒ 部署后请自行确认：重启宿主 + 刷新浏览器页面。")
        return 0

    live = str(data.get("version") or "")
    if live != expected:
        print("\n🔴 活体版本不一致：宿主跑的是 %s，仓库是 %s。" % (live or "（读不到）", expected))
        print("    ⇒ 新 widget / 新 tooltip / `health.version` 都在**宿主内存**里，")
        print("      光同步文件不改这里：画布上看不见任何新东西，前端行为验证也是假的。")
        print("    ⇒ 处置：**重启 ComfyUI 宿主**，然后刷新浏览器页面，再跑一次本工具。")
        return 1
    print("\n[OK] 活体版本一致：宿主跑的就是 %s（新 widget / 新 tooltip 已生效）。" % expected)
    print("    ⚠️ 仍要**刷新浏览器页面**（前端 JS 有缓存）。")
    return 0


if __name__ == "__main__":
    print("本模块由 sync_deploy_check.py 调用（也可单独 `python tools/deploy_live_check.py`）。")
    sys.exit(0 if _repo_version() else 2)
