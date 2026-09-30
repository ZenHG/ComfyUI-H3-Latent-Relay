#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一条命令，把本仓**同时**推到两个渠道（GitHub 仓库 + Comfy Registry），并交叉验证。

🔴 **为什么必须有它**：本仓有两个发布渠道，而它们**各自都会静默地成功一半** ——
   · 只 push 不 publish ⇒ GitHub 上是新版、Manager 里还是旧版（用户装到的和文档写的不是一个东西）
   · 只 publish 不 push ⇒ registry 包的 `repository` 指向的代码里没有这一版（无法复核、issue 无从查起）
   · push 了但 CI 红 ⇒ 已经公开的提交没通过自检
   三件事都要同时成立才算"发布完成"，靠人记就会漏。本脚本把它们串成一条**带交叉验证**的流水线：
   推 → **等 CI 真的绿** → 发 registry → **回头查两边是不是真的是这一版**。

规范正文 = 仓库根 `RELEASING.md`（**唯一真相源**）；本文件是它的执行体，两边必须一致。
改了流程 ⇒ 改 `RELEASING.md`，再看这里的步骤要不要跟着动。

用法：
    python tools/release.py                  # 预演：只查前置与改动面，不推不发
    python tools/release.py --go             # 真发布（推 + 等 CI + 发 registry + 交叉验）
    python tools/release.py --verify-only    # 只回答「两边现在同步吗」（纯只读，不推不发）
    python tools/release.py --go --skip-ci-wait   # 有急事时跳过等 CI（**默认不跳**）

环境变量（都可省，省了走默认）：
    COMFY_REGISTRY_PAT        直接给 Token（优先级最高）
    COMFY_REGISTRY_PAT_FILE   Token 文件路径（默认 `<仓根>/.comfy_registry_token`，该文件已 gitignore）
    COMFY_CLI                 comfy 可执行文件（默认 PATH 里找 `comfy`）
    H3RELAY_GIT               指定 git 可执行（⚠️ 便携版 git 没有 remote-https，需指向完整版）
    H3RELAY_DEPLOY            部署副本目录（给了就顺手同步，并复核一致）
    H3RELAY_REPO             owner/name（默认从 origin 推断）

退出码：0 = 两个渠道都成功且已交叉验证；非 0 = 有一步没过（**会在最后点名是哪一步**）。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY_API = "https://api.comfy.org"
TOKEN_FILE_DEFAULT = os.path.join(REPO, ".comfy_registry_token")

FAILED: list[str] = []
# 🔴 命令回显要**脱敏**：`say("$ " + cmd)` 会把 `--token <PAT>` 原样打进日志/终端
#    （2026-09-30 实跑时真的把 PAT 打出来了）。命令行的透明性不该以泄密为代价。
SECRETS: list[str] = []


# --------------------------------------------------------------------------- 小工具
def say(msg=""):
    print(msg, flush=True)


def _mask(s: str) -> str:
    for sec in SECRETS:
        if sec:
            s = str(s).replace(sec, "***REDACTED***")
    return str(s)


def die(msg, code=1):
    say("\n🔴 " + _mask(msg))
    sys.exit(code)


def run(cmd, env=None, cwd=None, quiet=False):
    if not quiet:
        say("  $ " + " ".join(_mask(c) for c in cmd))
    return subprocess.run(cmd, cwd=cwd or REPO, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)


def git(*args, **kw):
    return run([GIT, "-C", REPO, *args], **kw)


def http_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "h3-relay-release"})
    with urllib.request.urlopen(req, timeout=timeout) as r:      # noqa: S310 - 固定 https 域名
        return json.loads(r.read().decode("utf-8"))


# --------------------------------------------------------------------------- 事实采集
def git_cfg(key, default=""):
    r = git("config", "--get", key, quiet=True)
    return (r.stdout or "").strip() or default


def repo_slug() -> str:
    if os.environ.get("H3RELAY_REPO"):
        return os.environ["H3RELAY_REPO"]
    url = git_cfg("remote.origin.url")
    m = re.search(r"github\.com[:/]+([^/]+)/([^/\s]+?)(\.git)?$", url)
    if not m:
        die("从 origin 推断不出 owner/name（`git config remote.origin.url` = %r）；"
            "用 H3RELAY_REPO=owner/name 指定。" % url)
    return "%s/%s" % (m.group(1), m.group(2))


def pyproject() -> dict:
    try:
        import tomllib
    except ImportError:                        # py3.10 兜底
        try:
            import tomli as tomllib            # type: ignore
        except ImportError:
            die("本脚本要 Python ≥3.11（或 `pip install tomli`）来读 pyproject.toml")
    with open(os.path.join(REPO, "pyproject.toml"), "rb") as fh:
        return tomllib.load(fh)


def local_facts() -> dict:
    """版本号**四处**（与 review_050 的 H1 同一口径）+ 本地 HEAD。"""
    cfg = pyproject()
    pj, tk = cfg["project"], cfg["tool"]["comfy"]
    def _read(rel):
        with io.open(os.path.join(REPO, rel), encoding="utf-8") as fh:
            return fh.read()
    v = {
        "__init__.py": re.search(r'__version__\s*=\s*"([^"]+)"', _read("__init__.py")),
        "pyproject.toml": re.search(r'^version\s*=\s*"([^"]+)"', _read("pyproject.toml"), re.M),
        "CHANGES.md": re.search(r"^## ([\d.]+)", _read("CHANGES.md"), re.M),
        "README.md": re.search(r"^\|\s*版本\s*\|\s*\*\*([\d.]+)\*\*", _read("README.md"), re.M),
    }
    vals = {k: (m.group(1) if m else None) for k, m in v.items()}
    head = (git("rev-parse", "HEAD", quiet=True).stdout or "").strip()
    return {"name": pj["name"], "publisher": tk.get("PublisherId"), "version": pj["version"],
            "versions": vals, "head": head}


def changelog_text(version: str) -> str:
    """取 CHANGES.md **顶部那一节**当 registry changelog（版本不匹配就报出来，不静默发错内容）。"""
    with io.open(os.path.join(REPO, "CHANGES.md"), encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(r"^## ([0-9][^ \n]*)[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        die("CHANGES.md 里找不到 `## x.y.z` 版本节")
    if m.group(1) != version:
        die("CHANGES.md 顶部是 %s，pyproject 是 %s —— 先把 CHANGES 顶上那一节补上/对齐"
            % (m.group(1), version))
    return re.sub(r"\n*-{3,}\s*$", "", m.group(2).strip()).strip()


def token() -> str:
    t = (os.environ.get("COMFY_REGISTRY_PAT") or "").strip()
    if t:
        _remember(t)
        say("  Token：%d 字符（来源：环境变量，不回显）" % len(t))
        return t
    p = os.environ.get("COMFY_REGISTRY_PAT_FILE") or TOKEN_FILE_DEFAULT
    if not os.path.isfile(p):
        die("找不到 registry Token（%s）。\n"
            "    怎么来：registry.comfy.org 用 GitHub 登录 → 建 Publisher → 生成 API Key。\n"
            "    这两步**只能在网页上做**（第三方 OAuth，没有任何 API 能代做）。\n"
            "    存法：把 Key 一行写进上面那个文件（该文件已被 .gitignore + .comfyignore 双重忽略）。" % p)
    with io.open(p, encoding="utf-8-sig") as fh:
        t = fh.read().strip()
    if len(t) < 16:
        die("Token 文件内容太短（%d 字符）—— 是不是存错了？%s" % (len(t), p))
    _remember(t)
    # 密钥纪律：发布前**真查**一遍它不会被上传（"我记得加过 .gitignore"不算证据）
    if p == TOKEN_FILE_DEFAULT:
        rel = os.path.relpath(p, REPO).replace("\\", "/")
        if git("ls-files", "--error-unmatch", rel, quiet=True).returncode == 0:
            die("🔴 Token 文件**已被 git 跟踪**！先 `git rm --cached` 它，并立刻作废重发这个 Key。")
        if git("check-ignore", rel, quiet=True).returncode != 0:
            die("🔴 Token 文件**没有命中任何忽略规则**（.gitignore 被改坏了？）：%s" % p)
    say("  Token：%d 字符（来源 %s，不回显）" % (len(t), p))
    return t


def _remember(t: str) -> None:
    """把 Token 记进 SECRETS ⇒ 之后所有命令回显都会被脱敏。"""
    if t and t not in SECRETS:
        SECRETS.append(t)


# --------------------------------------------------------------------------- 步骤
def preflight(f: dict):
    say("\n① 前置（本地）")
    st = git("status", "--short", quiet=True).stdout or ""
    if st.strip():
        say(st.rstrip())
        die("工作树不干净 —— 发出去的东西必须是**提交态**（否则包里混着未提交的改动）。先提交。")
    say("  ✅ 工作树干净")
    bad = {k: v for k, v in f["versions"].items() if v != f["version"]}
    if bad:
        die("版本号不是**四处一致**（H1 口径）：pyproject=%s，不一致的：%s" % (f["version"], bad))
    say("  ✅ 版本号四处一致：%s" % f["version"])
    say("  ✅ 发布目标：%s/%s" % (f["publisher"], f["name"]))
    # 快门槛（全量门槛由 h3relay_check_all 负责；这里只拦最会翻车的那两项）
    for name, cmd in (("review_050（文档—代码一致性）", [sys.executable, "tools/review_050.py"]),
                      ("en_sync（英文文档同步闸）", [sys.executable, "tools/en_sync.py"])):
        r = run(cmd, env=dict(os.environ, COMFYUI_PATH=os.environ.get("COMFYUI_PATH", "")))
        tail = [ln for ln in (r.stdout or "").splitlines() if ln.startswith("结果")][-1:] \
            or (r.stdout or "").splitlines()[-1:]
        say("  [%s] %s%s" % ("OK" if r.returncode == 0 else "FAIL", name,
                             ("  " + tail[0]) if tail else ""))
        if r.returncode != 0:
            die("前置门槛没过：%s ⇒ 先修它（全量 12 项见 RELEASING.md §2 第 4 条）" % name)


def push() -> None:
    say("\n② 渠道一 · GitHub：push")
    tries = [
        ([], "普通"),
        (["-c", "http.version=HTTP/1.1"], "HTTP/1.1"),
        (["-c", "http.sslBackend=schannel"], "schannel"),
        ([], "去代理直连"),
    ]
    env_noproxy = {k: v for k, v in os.environ.items()
                   if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
    for extra, why in tries:
        cmd = [GIT, "-C", REPO, *extra, "push", "origin", "HEAD:refs/heads/main"]
        env = env_noproxy if why == "去代理直连" else None
        r = run(cmd, env=env)
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 0:
            say("  ✅ push 成功（%s）" % why)
            return
        say("  ⚠️ %s 失败：%s" % (why, out.strip().splitlines()[-1][:140] if out.strip() else ""))
        if "remote-https" in out:
            die("当前 git 缺 `git-remote-https` 助手（便携版常见）⇒ 设 "
                "H3RELAY_GIT 指向完整版 git 再跑。")
        time.sleep(3)
    die("push 四次都没成功 —— 网络层问题（本仓踩过：代理对 CONNECT/POST 打回 502，去代理直连可解）")


def wait_ci(slug: str) -> None:
    say("\n③ 等 CI（必须是**这次提交**的那一轮绿）")
    gh = shutil.which("gh")
    if not gh:
        FAILED.append("CI 未验证（环境里没有 gh）")
        say("  ⚠️ 没有 gh，跳过 —— 但**发布并未完成**：请手动确认 CI 绿")
        return
    head = (git("rev-parse", "HEAD", quiet=True).stdout or "").strip()
    rid = None
    for _ in range(20):
        r = run([gh, "run", "list", "--repo", slug, "--limit", "10",
                 "--json", "databaseId,headSha,status"], quiet=True)
        try:
            for it in json.loads(r.stdout or "[]"):
                if it.get("headSha") == head:
                    rid = it["databaseId"]
                    break
        except Exception:                                          # noqa: BLE001
            pass
        if rid:
            break
        time.sleep(6)
    if not rid:
        FAILED.append("CI 未验证（找不到本次提交的运行）")
        say("  ⚠️ 没找到 headSha=%s 的运行" % head[:10])
        return
    r = run([gh, "run", "watch", str(rid), "--repo", slug, "--exit-status", "--interval", "15"])
    if r.returncode == 0:
        say("  ✅ CI 绿（run %s）" % rid)
    else:
        die("🔴 CI 红（run %s）—— **已经推上去了**，但不能就此发 registry：\n"
            "    `gh run view %s --repo %s --log-failed`\n"
            "    修完再跑一次本脚本（registry 那步还没做，版本仍是旧的，安全）。" % (rid, rid, slug))


def publish(f: dict) -> None:
    say("\n④ 渠道二 · Comfy Registry：publish")
    comfy = os.environ.get("COMFY_CLI") or shutil.which("comfy")
    if not comfy:
        die("找不到 comfy-cli：`pip install comfy-cli`，或用 COMFY_CLI 指定其路径。\n"
            "    ⚠️ 它必须能跑（早前实测：隔离 venv 要用系统 Python 3.12 建，3.13 建的在清华源上装不上）")
    r = run([comfy, "node", "validate"])
    tail = [ln for ln in (r.stdout or "").splitlines() if ln.strip()][-1:]
    say("  " + (tail[0] if tail else "(无输出)"))
    if r.returncode != 0:
        die("`comfy node validate` 没过 —— 别发")
    say("  ✅ 官方校验通过")
    # 打包预览：把「发出去到底有什么」摊开看，开发件混入就拦
    run([comfy, "node", "pack"], quiet=True)
    zp = os.path.join(REPO, "node.zip")
    if os.path.isfile(zp):
        import zipfile
        names = sorted(zipfile.ZipFile(zp).namelist())
        leaked = [n for n in names if n.startswith(("docs/", "tests/", "tools/", ".github/"))]
        say("  包内 %d 个条目%s" % (len(names), "；🔴 开发件混入：%s" % leaked[:5] if leaked else "，开发件已排干净"))
        os.remove(zp)
        if leaked:
            die(".comfyignore 坏了（开发件进包）—— 别发")
    t = token()
    cl = changelog_text(f["version"])
    r = run([comfy, "node", "publish", "--token", t, "--changelog", cl])
    out = ((r.stdout or "") + (r.stderr or "")).replace(t, "***REDACTED***")
    say("  " + "\n  ".join(l for l in out.strip().splitlines()[-6:]))
    if os.path.isfile(zp):                      # comfy-cli 的 publish 会留下 node.zip，它自己不清
        os.remove(zp)
    if r.returncode != 0:
        die("registry 发布失败（退出码 %d）" % r.returncode)
    say("  ✅ 已上传")


def verify(f: dict, slug: str) -> None:
    """🔴 这一步是「不遗漏」的关键：**回头查**两边是不是真的这一版，而不是信命令的退出码。"""
    say("\n⑤ 交叉验证（两个渠道 + 本机）")
    ok = True
    # 5.1 GitHub：远端 main 是否**包含**本地 HEAD
    #     （判据不是"远端 sha == 本地 HEAD"：发布后继续提交开发件是正常的，那不该判红。）
    run([GIT, "-C", REPO, "fetch", "origin", "main", "--quiet"], quiet=True)
    anc = git("merge-base", "--is-ancestor", "HEAD", "origin/main", quiet=True).returncode == 0
    r = run(["gh", "api", "repos/%s/commits/main" % slug, "--jq", ".sha"], quiet=True)
    remote = (r.stdout or "").strip()
    say("  [%s] GitHub main = %s ｜ 本地 HEAD = %s%s"
        % ("OK" if anc else "FAIL", remote[:10] or "?", f["head"][:10],
           "" if anc else "（本地 HEAD 不是 origin/main 的祖先 ⇒ 渠道一没跟上）"))
    ok &= anc
    # 5.2 Registry：这个版本真的在线上了
    try:
        vers = http_json("%s/nodes/%s/versions" % (REGISTRY_API, f["name"]))
        got = [v.get("version") for v in vers] if isinstance(vers, list) else []
    except Exception as e:                                     # noqa: BLE001
        got = []
        say("  [FAIL] 读 registry 失败：%s: %s" % (type(e).__name__, e))
        ok = False
    on = f["version"] in got
    say("  [%s] registry 上的版本：%s%s" % ("OK" if on else "FAIL", got or "（空）",
                                          "（含本版 %s）" % f["version"] if on else
                                          "（**缺本版 %s**）" % f["version"]))
    ok &= on
    # 5.3 本机运行时副本（**不是**公开渠道之一 ⇒ 只提示、不判红；`--go` 会顺手同步）
    deploy = os.environ.get("H3RELAY_DEPLOY")
    if deploy and os.path.isdir(deploy):
        r = run([sys.executable, "tools/sync_deploy_check.py", deploy, "--rev", f["head"]])
        line = next((l.strip() for l in (r.stdout or "").splitlines()
                     if l.strip().startswith("OK ")), "")
        if r.returncode == 0:
            say("  [OK] 部署副本与本地 HEAD 一致（%s）" % line)
        else:
            say("  [⚠] 部署副本与本地 HEAD 不一致（%s）—— 本机运行时还在用旧代码；"
                "`--go` 会自动同步" % (line or "DIFF"))
    elif deploy:
        say("  ⚠️ H3RELAY_DEPLOY 指了但目录不存在，跳过")
    if not ok:
        FAILED.append("交叉验证未全过")
    say("\n" + ("=" * 74))
    if FAILED:
        say("🔴 未完成：%s" % " · ".join(FAILED))
    else:
        say("✅ 两个渠道都已发布并交叉验证：GitHub %s ｜ registry %s/%s v%s"
            % (f["head"][:10], f["publisher"], f["name"], f["version"]))
    return ok


def sync_deploy(deploy: str, head: str = "HEAD") -> None:
    """把**本机部署副本**铺到指定提交。

    为什么它是发布的一部分：ComfyUI 运行时加载的是 `custom_nodes/` 下那份副本 ——
    不同步它，等于"registry 上是新版、你机器上跑的还是旧代码"。
    ⚠️ 判据用 `git show <rev>:<file>` 的**提交态**逐文件比（忽略 CRLF），**不要 `cp -r` 整包**：
    `custom_nodes` 里留第二份副本会静默覆盖节点定义。
    """
    say("\n⑥ 同步本机部署副本")
    n = 0
    for rel in (git("ls-files", quiet=True).stdout or "").split():
        blob = subprocess.run([GIT, "-C", REPO, "show", "%s:%s" % (head, rel)],
                              capture_output=True).stdout
        dst = os.path.join(deploy, rel.replace("/", os.sep))
        cur = open(dst, "rb").read() if os.path.isfile(dst) else None
        if cur is None or cur.replace(b"\r\n", b"\n") != blob.replace(b"\r\n", b"\n"):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as fh:
                fh.write(blob)
            n += 1
    say("  铺入 %d 个文件（`sync_deploy_check` 会在第 ⑤ 步复核）" % n)


# --------------------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description="H 仓发布器：GitHub + Comfy Registry 同步发布并交叉验证")
    ap.add_argument("--go", action="store_true", help="真发布（默认只预演）")
    ap.add_argument("--verify-only", action="store_true", help="只做交叉验证（纯只读）")
    ap.add_argument("--skip-ci-wait", action="store_true", help="不等 CI（默认等）")
    a = ap.parse_args()

    global GIT
    GIT = os.environ.get("H3RELAY_GIT") or shutil.which("git") or "git"

    f = local_facts()
    slug = repo_slug()
    say("=" * 74)
    say("H 仓发布器 ｜ 目标 GitHub %s ｜ registry %s/%s ｜ 版本 %s ｜ HEAD %s"
        % (slug, f["publisher"], f["name"], f["version"], f["head"][:10]))
    say("（规范正文见 RELEASING.md；本脚本是它的执行体）")
    say("=" * 74)

    if a.verify_only:
        verify(f, slug)
        return 1 if FAILED else 0

    preflight(f)
    if not a.go:
        say("\n[预演结束] 上面都过了；加 --go 才会：push → 等 CI → registry publish → 交叉验证。")
        return 0

    push()
    if a.skip_ci_wait:
        say("\n③ 跳过等 CI（--skip-ci-wait）")
    else:
        wait_ci(slug)
    publish(f)
    deploy = os.environ.get("H3RELAY_DEPLOY")
    if deploy and os.path.isdir(deploy):
        sync_deploy(deploy, f["head"])
    verify(f, slug)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
