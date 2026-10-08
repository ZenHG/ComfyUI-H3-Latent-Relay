# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
"""外部开源用户视角冒烟：别人 `git clone` 进 `custom_nodes/` 之后**能不能真的用**。

【它补的是哪一层（为什么本仓已有的一堆自检都不等价）】
  · `tests/` 与 `tools/*` 全都**在原仓里**跑 ⇒ 证明不了"clone 出来的那份**自足**"。
    本仓踩过：分发集漏文件（`relay_core/` 拆包后尤其危险）只有**真的按 clone 复制一份**才发现。
  · `tools/make_minimal_bundle.py` 验的是 `dist/`（registry / zip 那条渠道）。但 **git clone 那条
    渠道拿的不是 `dist/`**（是整仓受控文件）⇒ 两条渠道的"缺文件"方式**不同**，必须各验一次。

【两条路都要过（GG 要求：UI 版与 API 版都正常）】
  A. **干净副本**：按 `git ls-files` 复制出 `<tmp>/H3Pack`（不带 `.git` / `__pycache__` / 未跟踪文件）
     ① 默认出口 = V3：`NODE_CLASS_MAPPINGS is None` + `comfy_entrypoint().get_node_list()` 出 N 节点
     ② `H3RELAY_NODE_API=v1`（**独立子进程**）：`NODE_CLASS_MAPPINGS` 出 N 节点，
        且每个节点的 `INPUT_TYPES()` 可求值、含 `required`
     ③ 两个出口的**节点 id 集合完全相同** —— 铁律一（UI 与 API 同一套实现）的机器版
     ④ `WEB_DIRECTORY` 落在**副本里**，且全部前端 JS 在位（画布路拿得到）
     ⑤ **没有任何模块来自副本之外** —— 这条直接对"本地能跑、clone 下来缺文件"下判据
  B. **最小分发集**（`dist/`）：不只 import，还要**真跑一次节点方法**（证明运行时可用，
     不是"schema 能构造"而已）。

【判据为什么这么写】
  · 节点数**不写死**：它由 `NODE_CLASS_MAPPINGS` 自己回答（加节点不用回来改本文件）；
    两个出口之间**互相比**才是"同一套实现"的真判据。
  · ⑤ 的"来自副本之外"用 `__file__` 前缀判 —— 本仓有过"import 到 I 盘部署副本"的假绿。

【跑法】（在包目录下；CI 里 `COMFYUI_PATH` 已由 workflow 给出）
    COMFYUI_PATH=<ComfyUI 根> python tools/user_smoke.py

【范围纪律】用户侧环境不归我们管（缺模型 / 缺上游包 / 素材不在）—— 本工具只保证
「标准 ComfyUI + 本包自己」这两条路走得通。

🔴 【PYTHONPATH 纪律】（2026-10-07 修）本工具**自己**把 ComfyUI 根前置进子进程的
   `PYTHONPATH`，**不依赖调用者**。修之前用的是 `setdefault` 到 `PYTHONPATH`（**不要那样写**）——
   调用者已经设了 PYTHONPATH 时那是**静默 no-op**，子进程 import 不到 `folder_paths`，
   报错却指向本包（"No module named 'folder_paths'" 看着像仓库坏了）。
   CI 里 workflow 恰好把 PYTHONPATH 设成了 ComfyUI 根 ⇒ **CI 绿、本地红**，
   而本机默认带一个 PYTHONPATH ⇒ 这个 bug 只在本地复现。判据见 `_child_env()`，
   机检 = `tools/review_050.py` 的 **H3x**。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMFY = (os.environ.get("COMFYUI_PATH") or "").strip() or os.path.dirname(os.path.dirname(KIT))
PY = sys.executable
FAIL, NPASS = [], [0]


def _child_env(extra=None):
    """子进程环境：**显式**把 ComfyUI 根前置进 `PYTHONPATH`（见文件头的 PYTHONPATH 纪律）。

    · 用前置拼接，**不用** `setdefault` —— 后者在调用者已有 PYTHONPATH 时静默失效。
    · 不丢弃调用者原有的 PYTHONPATH（只让 ComfyUI 优先），故 CI 与本机同一条路。
    """
    env = dict(os.environ, COMFYUI_PATH=COMFY)
    env["PYTHONPATH"] = COMFY + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.update(extra or {})
    return env


if not os.path.isfile(os.path.join(COMFY, "folder_paths.py")):
    print("⚠️ %s 下找不到 folder_paths.py —— 本工具需要**宿主 ComfyUI 根**。" % COMFY)
    print("   设 COMFYUI_PATH=<ComfyUI 根> 再跑；否则下面凡涉及 import 的项都会失败。")


def ck(name, cond, detail=""):
    if cond:
        NPASS[0] += 1
    else:
        FAIL.append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  " + detail) if detail else ""))


def tracked_files():
    r = subprocess.run(["git", "-C", KIT, "ls-files"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print("⛔ 取不到 `git ls-files`（%s）：本工具必须在一个 git 工作树里跑。" % r.stderr.strip()[:120])
        raise SystemExit(2)
    return [f for f in (r.stdout or "").split("\n") if f.strip()]


def make_clean_clone(dst_root):
    """把**受版本控制**的文件复制到 dst_root/H3Pack —— 等价于别人 `git clone` 下来的那份。"""
    pkg = os.path.join(dst_root, "H3Pack")
    n = 0
    for rel in tracked_files():
        src = os.path.join(KIT, rel.replace("/", os.sep))
        if not os.path.isfile(src):
            continue
        dst = os.path.join(pkg, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        n += 1
    for root, dirs, _f in os.walk(pkg):
        for d in list(dirs):
            if d == "__pycache__":
                shutil.rmtree(os.path.join(root, d), ignore_errors=True)
    return pkg, n


# ---------------------------------------------------------------- 子进程探针
#   为什么用子进程：① 两种出口靠**环境变量**切换，同进程里切不干净（本仓踩过）；
#                  ② 顺手拿到"模块来自哪里"的干净快照。
_SRV_STUB = """
class _R:
    def post(self, _p):
        def d(f): return f
        return d
    def get(self, _p):
        def d(f): return f
        return d
class _PS:
    routes = _R(); instance = None; prompt_queue = None
_PS.instance = _PS()
_srv = types.ModuleType("server"); _srv.PromptServer = _PS; sys.modules["server"] = _srv
"""

PROBE = """
import asyncio, importlib.util, json, os, sys, types
PKG, API = sys.argv[1], sys.argv[2]
""" + _SRV_STUB + """
spec = importlib.util.spec_from_file_location(
    "h3userprobe", os.path.join(PKG, "__init__.py"), submodule_search_locations=[PKG])
m = importlib.util.module_from_spec(spec)
sys.modules["h3userprobe"] = m
spec.loader.exec_module(m)

res = {"api": API, "is_none": m.NODE_CLASS_MAPPINGS is None}
wd = getattr(m, "WEB_DIRECTORY", None)
res["web_dir"] = wd
res["web_dir_abs"] = os.path.join(os.path.dirname(os.path.abspath(m.__file__)), wd) if wd else None
res["web_js"] = sorted(f for f in os.listdir(res["web_dir_abs"])
                       if f.endswith(".js")) if res["web_dir_abs"] else []

if API == "v1":
    ncm = m.NODE_CLASS_MAPPINGS or {}
    res["ids"] = sorted(ncm)
    bad = []
    for k, cls in ncm.items():
        try:
            it = cls.INPUT_TYPES()
            if not isinstance(it, dict) or "required" not in it:
                bad.append("%s: INPUT_TYPES 形状不对" % k)
        except Exception as e:
            bad.append("%s: %r" % (k, e))
    res["bad_input_types"] = bad
else:
    ext = m.comfy_entrypoint()
    if asyncio.iscoroutine(ext):
        ext = asyncio.run(ext)
    nl = ext.get_node_list()
    if asyncio.iscoroutine(nl):
        nl = asyncio.run(nl)
    ids = []
    for n in nl:
        s = n.define_schema() if hasattr(n, "define_schema") else n.GET_SCHEMA()
        ids.append(s.node_id)
    res["ids"] = sorted(ids)

res["mods"] = sorted(k for k in sys.modules if k.startswith("h3userprobe"))
res["leak"] = sorted(k for k in res["mods"]
                     if not (getattr(sys.modules[k], "__file__", None) or "").startswith(PKG))
print("@@JSON@@" + json.dumps(res))
"""

# 分发集：**真跑一次节点方法**（`H3RelayTrimAV.trim` 是纯张量、零模型、零 GPU）
BUNDLE_PROBE = """
import importlib.util, json, os, sys, types
PKG = sys.argv[1]
""" + _SRV_STUB + """
spec = importlib.util.spec_from_file_location(
    "h3bundleprobe", os.path.join(PKG, "__init__.py"), submodule_search_locations=[PKG])
m = importlib.util.module_from_spec(spec)
sys.modules["h3bundleprobe"] = m
spec.loader.exec_module(m)
res = {}
core = os.path.join(PKG, "relay_core")
res["core_files"] = sorted(f for f in os.listdir(core) if f.endswith(".py")) if os.path.isdir(core) else []
try:
    import torch
    from importlib import import_module
    N = import_module("h3bundleprobe.nodes")
    out = N.H3RelayTrimAV().trim(torch.rand(30, 8, 8, 3), trim_frames=22, fps=24.0,
                                 audio=None, settle_frames=0, save_pcm=False)
    res["ok"] = True
    res["detail"] = "返回 %s 路；首路形状 %s" % (
        len(out) if isinstance(out, (list, tuple)) else 1,
        tuple(out[0].shape) if isinstance(out, (list, tuple)) and hasattr(out[0], "shape") else "?")
except Exception as e:                       # noqa: BLE001
    import traceback
    res["ok"] = False
    res["detail"] = "%s: %s" % (type(e).__name__, str(e)[:200])
    res["tb"] = traceback.format_exc()[-700:]
print("@@JSON@@" + json.dumps(res))
"""


def _run_probe(src, args, extra_env=None):
    env = _child_env(extra_env)
    fd, path = tempfile.mkstemp(suffix=".py", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(src)
        r = subprocess.run([PY, path] + args, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env)
        for line in (r.stdout or "").splitlines():
            if line.startswith("@@JSON@@"):
                return json.loads(line[len("@@JSON@@"):]), ""
        return None, ((r.stderr or "") + (r.stdout or ""))[-700:]
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def main():
    tmp = tempfile.mkdtemp(prefix="h3user_")
    try:
        pkg, n = make_clean_clone(tmp)
        print("=" * 78)
        print("A. 干净副本（模拟 `git clone`）→ %s ｜ 复制 %d 个受控文件" % (pkg, n))
        print("=" * 78)
        runs = {}
        for api in ("v3", "v1"):
            got, err = _run_probe(PROBE, [pkg, api], {"H3RELAY_NODE_API": api})
            if got:
                runs[api] = got
            else:
                ck("A 副本可 import（%s 出口）" % api, False, err)
        v3, v1 = runs.get("v3"), runs.get("v1")
        if v3:
            ck("A1 干净副本可 import，且**默认出口 = V3**（`NODE_CLASS_MAPPINGS is None`）",
               v3.get("is_none") is True, "节点=%s" % v3.get("ids"))
            ck("A2 V3 出口注册节点数 == V1 的注册数（同一套实现）",
               len(v3.get("ids") or []) > 0 and
               (v1 is None or len(v3["ids"]) == len(v1.get("ids") or [])),
               "%d 个" % len(v3.get("ids") or []))
            ck("A3 `WEB_DIRECTORY` 在**副本内**，且前端 JS 全部在位",
               bool(v3.get("web_dir_abs")) and v3["web_dir_abs"].startswith(pkg)
               and len(v3.get("web_js") or []) > 0,
               "%s → %d 个 JS" % (v3.get("web_dir"), len(v3.get("web_js") or [])))
            ck("A4 🔴 没有任何模块来自副本之外（clone 下来不缺文件）",
               not v3.get("leak"), "泄漏=%s" % (v3.get("leak"),))
        if v1:
            ck("A5 V1 出口（`H3RELAY_NODE_API=v1`）注册节点、且 `NODE_CLASS_MAPPINGS` 非 None",
               len(v1.get("ids") or []) > 0 and v1.get("is_none") is False,
               "%d 个" % len(v1.get("ids") or []))
            ck("A6 V1 出口每个节点的 `INPUT_TYPES()` 可求值且含 `required`",
               not v1.get("bad_input_types"), "%s" % (v1.get("bad_input_types"),)[:200])
        if v3 and v1:
            same = v3.get("ids") == v1.get("ids")
            ck("A7 🔴 两个出口的节点 id 集合**完全相同**（铁律一：UI 与 API 同一套实现）",
               same, "" if same else "v3=%s\n           v1=%s" % (v3.get("ids"), v1.get("ids")))

        print()
        print("=" * 78)
        print("B. 最小分发集（`dist/`，用户下载的那份）—— import + **真跑一次节点方法**")
        print("=" * 78)
        r = subprocess.run([PY, os.path.join(KIT, "tools", "make_minimal_bundle.py"),
                            "--zip", "--force"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           env=_child_env(), cwd=KIT)
        ck("B1 分发集能构建且自验通过（清单无漏 + 副本可独立使用）",
           r.returncode == 0 and "全部通过" in (r.stdout or ""),
           "" if r.returncode == 0 else ((r.stderr or "") + (r.stdout or ""))[-400:])
        dist = os.path.join(KIT, "dist", "ComfyUI-H3-Latent-Relay")
        if os.path.isdir(dist):
            d, err = _run_probe(BUNDLE_PROBE, [dist])
            if d:
                ck("B2 🔴 从分发集**真跑**一次节点方法（`H3RelayTrimAV.trim`，纯张量、零 GPU）",
                   d.get("ok") is True, (d.get("detail", "") + d.get("tb", ""))[:400])
                ck("B3 分发集里的 `relay_core/` 包与仓库里的同名文件**一一对应**",
                   d.get("core_files") == sorted(f for f in os.listdir(os.path.join(KIT, "relay_core"))
                                                 if f.endswith(".py")),
                   "%d 个" % len(d.get("core_files") or []))
            else:
                ck("B2 🔴 从分发集真跑一次节点方法", False, err)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("=" * 78)
    print("结果：通过 %d / 失败 %d%s" % (NPASS[0], len(FAIL), ("（%s）" % FAIL) if FAIL else ""))
    print("=" * 78)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
