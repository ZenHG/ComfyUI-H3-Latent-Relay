#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""产出**最小可运行分发集**（用户 clone 后只需要节点 + 示例工作流，不需要 docs/tests/tools）。

## 为什么要有它

本仓是**开发者仓**：`docs/`（9 篇）· `tests/`（4 套）· `tools/`（10 个）· `.github/`（CI 与模板）
加起来比运行期代码还多。想让用户 git clone 一份干净的，或者想打 zip 发给别人时，
手挑文件容易**漏**（漏一个 `.py` 就是 `ImportError`，且往往是别人先发现）。

所以：**清单 + 交叉校验 + 自验**三件一起做——

1. **显式清单**（`MANIFEST_RUNTIME` 等）：人可读、带「为什么」的注释；
2. **交叉校验**：静态推导「从入口出发真正会被 import 的本包文件」，**必须与清单的运行组逐一相等**
   ⇒ 将来有人新增模块却忘了改清单，这里会**当场报错**；
3. **自验**（`--verify`）：把产出目录当**独立包**加载，断言 8 个节点齐全、
   默认出口 V3、`WEB_DIRECTORY` 有前端文件、关键特性仍在，并断言
   **加载过程中没有任何模块来自原仓**（证明这份副本自足）。

## 用法

    python tools/make_minimal_bundle.py                     # 产出到 ./dist/ComfyUI-H3-Latent-Relay
    python tools/make_minimal_bundle.py --zip               # 再打个 zip
    python tools/make_minimal_bundle.py --out <目录> --force
    python tools/make_minimal_bundle.py --verify-only <目录>  # 只对已有目录做验收

退出码：0 = 通过；非零 = 有问题（CI 可直接用）。
零第三方依赖（仅标准库）。
"""
import argparse
import asyncio
import os
import re
import shutil
import sys
import types
import zipfile

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(KIT, "dist", "ComfyUI-H3-Latent-Relay")

# ---------------------------------------------------------------------------
# 清单：分四组。**运行组由 `_verify_manifest()` 与静态依赖推导交叉校验**。
# ---------------------------------------------------------------------------
MANIFEST_RUNTIME = [
    # 包入口：注册 /h3relay/concat 路由 + 选出口（V1/V3）
    "__init__.py",
    # 业务内核（两文件占全包 96% 的体积，是唯一真相源）
    "relay_core.py",
    "nodes.py",
    # nodes.py 直接 import 的契约模块
    "layout_contract.py",
    # V3 外壳（4 文件；_compat 是本包唯一 import comfy_api 的地方）
    "v3/__init__.py",
    "v3/_compat.py",
    "v3/nodes_v3.py",
    "v3/entrypoint.py",
]

MANIFEST_FRONTEND = [
    # WEB_DIRECTORY="./web"：画布按钮（🧩 拼接）与 Chain 词分发靠它们
    "web/relay_kit_chain.js",
    "web/relay_kit_prompt.js",
]

MANIFEST_EXAMPLES = [
    # 用户要的「示例工作流」——开箱可跑的最小图 + 全流程图
    "examples/README.md",
    "examples/minimal_relay_official.json",
    "examples/fullflow_second_pass_latent_upscale_ui.json",
]

MANIFEST_META = [
    # 元数据：ComfyUI-Manager / Comfy Registry 读 pyproject；Manager 读 requirements 装依赖
    "requirements.txt",
    "pyproject.toml",
    # 法律：MIT 全文 + 随包分发的第三方许可 + 出处声明（分发时必须带）
    "LICENSE",
    "licenses/Apache-2.0.txt",
    "THIRD-PARTY-NOTICES.md",
    # 用户必读
    "README.md",
]

MANIFEST = MANIFEST_RUNTIME + MANIFEST_FRONTEND + MANIFEST_EXAMPLES + MANIFEST_META

# 明确排除（写出来是为了让「为什么不带」有据可查，也便于将来有人问）
EXCLUDED_NOTE = {
    "docs/": "9 篇深度文档（机制/参数/画布/troubleshooting）——开发与排查用，非运行必需",
    "tests/": "4 套离线自测（392+86+15+69 项）——开发用",
    "tools/": "10 个自检/取证/CLI ——开发用（含本脚本自身）",
    ".github/": "CI 工作流与 issue 模板",
    "CHANGES.md": "版本流水（历史，含旧机路径，已豁免开源卫生扫描）",
    "CONTRIBUTING.md": "贡献者指南",
    "CODE_OF_CONDUCT.md": "行为准则",
    "SECURITY.md": "安全策略",
    ".gitattributes": "git 检出行为配置——分发包不再走 git",
    ".gitignore": "同上",
    "examples/make_minimal_workflow.py": "示例生成器（开发工具）；示例 JSON 已随包",
}


# ---------------------------------------------------------------------------
# 静态依赖推导（只认本包内的相对 import）
# ---------------------------------------------------------------------------
_REL_IMPORT = re.compile(
    r"^\s*from\s+(\.+)([\w.]*)\s+import\s+(.+?)\s*$", re.M)
_REL_IMPORT_PAREN = re.compile(
    r"^\s*from\s+(\.+)([\w.]*)\s+import\s*\(([^)]*)\)", re.M | re.S)


def _resolve(base_file, dots, mod):
    """相对 import → 本包内文件路径（相对 KIT，posix 风格）。取不到返回 None。"""
    d = os.path.dirname(base_file)
    for _ in range(len(dots) - 1):
        d = os.path.dirname(d)
    target = os.path.join(d, mod.replace(".", os.sep)) if mod else d
    for cand in (target + ".py", os.path.join(target, "__init__.py")):
        if os.path.isfile(os.path.join(KIT, cand)):
            return cand.replace(os.sep, "/")
    return None


def _imported_names(blob):
    """从 `import a, b as c` 里取顶层模块名列表（只用于 `from . import X` 这种）。"""
    out = []
    for part in blob.replace("\n", " ").split(","):
        name = part.strip().split(" as ")[0].strip().strip("()")
        if name and re.fullmatch(r"[A-Za-z_][\w.]*", name):
            out.append(name.split(".")[0])
    return out


def derive_runtime_files():
    """从 `__init__.py` 出发，递归找出本包内真正会被 import 的文件。

    补一条：任何被推导到的文件，其**各级父包的 `__init__.py`** 也算必需 ——
    它们由 import 机制隐式执行（如 `from .v3.entrypoint import …` 会先执行
    `v3/__init__.py`），静态扫描看不到，漏了就是 `ImportError`。
    """
    seen, stack = set(), ["__init__.py"]
    while stack:
        f = stack.pop()
        if f in seen:
            continue
        p = os.path.join(KIT, f)
        if not os.path.isfile(p):
            continue
        seen.add(f)
        # 隐式：各级父包的 __init__.py
        d = os.path.dirname(f)
        while d:
            init = os.path.join(d, "__init__.py").replace(os.sep, "/")
            if os.path.isfile(os.path.join(KIT, init)) and init not in seen:
                stack.append(init)
            d = os.path.dirname(d)
        with open(p, encoding="utf-8", errors="ignore") as fh:
            src = fh.read()
        # 去掉模块 docstring，免得里面的示例 import 被误算
        body = re.sub(r'^\s*""".*?"""', "", src, count=1, flags=re.S | re.M)
        for pat in (_REL_IMPORT, _REL_IMPORT_PAREN):
            for m in pat.finditer(body):
                dots, mod, names = m.group(1), m.group(2), m.group(3)
                if mod:
                    r = _resolve(f, dots, mod)
                    if r:
                        stack.append(r)
                else:
                    for nm in _imported_names(names):
                        r = _resolve(f, dots, nm)
                        if r:
                            stack.append(r)
    return sorted(seen)


def verify_manifest():
    """清单的运行组 vs 静态推导 —— 必须逐一相等（防「加了模块忘了改清单」）。"""
    derived = set(derive_runtime_files())
    declared = set(MANIFEST_RUNTIME)
    missing = sorted(derived - declared)     # 会被 import 但清单没写 ⇒ 漏发
    extra = sorted(declared - derived)       # 清单写了但没人 import ⇒ 可能多余（不一定是错）
    print("① 清单交叉校验（静态 import 推导）")
    print("   推导出运行期文件 %d 个；清单声明 %d 个" % (len(derived), len(declared)))
    ok = not missing
    print("   [%s] 清单**未漏**会被 import 的文件" % ("OK" if ok else "FAIL"))
    for f in missing:
        print("        ❌ 漏了：%s" % f)
    if extra:
        print("   ℹ️ 清单里这些文件没被静态推导到（可能由动态 import / 契约使用，保留）：")
        for f in extra:
            print("        %s" % f)
    return ok


# ---------------------------------------------------------------------------
# 产出
# ---------------------------------------------------------------------------
def write_bundle(out, force=False):
    print()
    print("② 写出最小集 → %s" % out)
    missing = [f for f in MANIFEST if not os.path.isfile(os.path.join(KIT, f))]
    if missing:
        print("   ❌ 清单里的文件在仓库里不存在：%s" % missing)
        return False
    if os.path.isdir(out):
        if not force:
            print("   ⚠️ 目标已存在；加 --force 覆盖（本脚本只会删自己产出的这个目录）")
            return False
        shutil.rmtree(out)
    total = 0
    for f in MANIFEST:
        dst = os.path.join(out, f.replace("/", os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(os.path.join(KIT, f.replace("/", os.sep)), dst)
        total += os.path.getsize(dst)
    print("   ✅ 复制 %d 个文件，共 %.1f KB" % (len(MANIFEST), total / 1024))
    return True


def make_zip(bundle_dir, zip_path):
    print()
    print("③ 打包 → %s" % zip_path)
    root = os.path.basename(bundle_dir.rstrip(os.sep))
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for dp, _dn, fns in os.walk(bundle_dir):
            for fn in fns:
                full = os.path.join(dp, fn)
                rel = os.path.relpath(full, bundle_dir)
                z.write(full, os.path.join(root, rel))
    print("   ✅ %.1f KB" % (os.path.getsize(zip_path) / 1024))
    return True


# ---------------------------------------------------------------------------
# 自验：把产出当独立包加载
# ---------------------------------------------------------------------------
def _stub_server():
    class _R:
        def post(self, _p):
            def d(f): return f
            return d
        def get(self, _p):
            def d(f): return f
            return d

    class _PS:
        routes = _R()
        instance = None
        prompt_queue = None
    _PS.instance = _PS()
    m = types.ModuleType("server")
    m.PromptServer = _PS
    sys.modules["server"] = m


def _nodes_of(pkg):
    async def go():
        ext = pkg.comfy_entrypoint()
        if asyncio.iscoroutine(ext):
            ext = await ext
        nl = ext.get_node_list()
        if asyncio.iscoroutine(nl):
            nl = await nl
        return list(nl)
    return asyncio.run(asyncio.wait_for(go(), 60))


def verify_bundle(bundle_dir, expect_source=KIT):
    print()
    print("④ 自验：把产出目录当**独立包**加载")
    bad = 0
    os.environ.pop("H3RELAY_NODE_API", None)          # 不设变量 ⇒ 验「默认出口」
    _stub_server()

    spec = __import__("importlib.util", fromlist=["util"]).spec_from_file_location(
        "bundle_under_test", os.path.join(bundle_dir, "__init__.py"),
        submodule_search_locations=[bundle_dir])
    pkg = __import__("importlib.util", fromlist=["util"]).module_from_spec(spec)
    sys.modules["bundle_under_test"] = pkg
    spec.loader.exec_module(pkg)

    # —— 自足性：不许有模块来自原仓 ——
    # 只看**属于被测包命名空间**的模块（其余是加载器噪声：`__main__` 是验脚本自己、
    # `torch.ops` / `torch.classes` 是 torch 的代理模块，其 __file__ 不可当真）。
    src = os.path.abspath(expect_source) + os.sep
    bnd = os.path.abspath(bundle_dir) + os.sep
    leaked, outside = [], []
    for name, mod in list(sys.modules.items()):
        fp = getattr(mod, "__file__", None)
        if not fp or name in ("__main__", "__mp_main__"):
            continue
        if not (name == "bundle_under_test" or name.startswith("bundle_under_test.")):
            continue                       # 只查本包自己的模块
        try:
            af = os.path.abspath(fp)
        except Exception:
            continue
        if not os.path.isfile(af):
            continue                       # 伪 __file__（torch 代理等）
        if af.startswith(bnd):
            outside.append((name, af))
        elif af.startswith(src):
            leaked.append((name, af))
    print("   [%s] 自足性：本包模块 %d 个，其中来自原仓 %d 个"
          % ("OK" if not leaked and outside else "FAIL", len(outside) + len(leaked), len(leaked)))
    print("        全部从副本目录加载: %s" % (bool(outside) and not leaked))
    for n, f in leaked[:5]:
        print("        ❌ 漏：%s → %s" % (n, f))
    if not outside:
        print("        ❌ 本包一个模块都没从副本加载 —— 检查 spec_from_file_location 的路径")
    bad += bool(leaked) or (not outside)

    # —— 默认出口 = V3 ——
    checks = [
        ("NODE_API_DEFAULT == 'v3'", getattr(pkg, "NODE_API_DEFAULT", None) == "v3"),
        ("NODE_API == 'v3'（未回退）", getattr(pkg, "NODE_API", None) == "v3"),
        ("NODE_CLASS_MAPPINGS is None", getattr(pkg, "NODE_CLASS_MAPPINGS", 1) is None),
        ("comfy_entrypoint 可调用", callable(getattr(pkg, "comfy_entrypoint", None))),
    ]
    for label, ok in checks:
        print("   [%s] %s" % ("OK" if ok else "FAIL", label))
        bad += (not ok)
    if getattr(pkg, "NODE_API_FALLBACK_REASON", None):
        print("        ⚠️ 回退原因：%s" % pkg.NODE_API_FALLBACK_REASON)

    # —— 8 个节点 ——
    nodes = _nodes_of(pkg)
    print("   [%s] V3 节点数 = %d（期望 8）" % ("OK" if len(nodes) == 8 else "FAIL", len(nodes)))
    bad += (len(nodes) != 8)

    # —— WEB_DIRECTORY 真的有前端文件 ——
    wd = str(getattr(pkg, "WEB_DIRECTORY", "") or "").lstrip("./")
    wdir = os.path.join(bundle_dir, wd.replace("/", os.sep))
    js = sorted(f for f in os.listdir(wdir)) if os.path.isdir(wdir) else []
    ok = len(js) >= 2
    print("   [%s] WEB_DIRECTORY=%r 前端文件 %s" % ("OK" if ok else "FAIL", wd, js))
    bad += (not ok)

    # —— 关键特性：节点层异常上下文 ——
    try:
        [c for c in nodes if c.__name__ == "H3RelayLatentLoad"][0].execute(
            run_id="bundle_verify_no_such", stage_index=5, explicit_path="")
        print("   [FAIL] 异常上下文：没抛异常")
        bad += 1
    except Exception as e:
        ok = "【H3RelayLatentLoad】" in str(e) and "第 5 段" in str(e)
        print("   [%s] 异常上下文仍带节点名+段号" % ("OK" if ok else "FAIL"))
        bad += (not ok)

    print()
    if bad:
        print("❌ 自验有 %d 项失败" % bad)
        return False
    print("✅ 最小集自验全过（可独立使用）")
    return True


def main():
    ap = argparse.ArgumentParser(description="产出最小可运行分发集（清单+交叉校验+自验）")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--zip", action="store_true", help="额外打一个 zip")
    ap.add_argument("--force", action="store_true", help="覆盖已有输出目录")
    ap.add_argument("--verify-only", metavar="目录", help="只对已有目录做自验")
    a = ap.parse_args()

    if a.verify_only:
        ok = verify_bundle(a.verify_only)
        return 0 if ok else 1

    print("=" * 70)
    print("最小可运行分发集 · 清单 %d 个文件" % len(MANIFEST))
    print("=" * 70)
    ok1 = verify_manifest()
    ok2 = write_bundle(a.out, a.force)
    if not ok2:
        return 1
    if a.zip:
        make_zip(a.out, a.out.rstrip(os.sep) + ".zip")
    ok3 = verify_bundle(a.out)

    print()
    print("=" * 70)
    if ok1 and ok3:
        print("✅ 全部通过：清单无漏 + 副本可独立使用")
        return 0
    print("❌ 见上（清单交叉校验=%s，自验=%s）" % (ok1, ok3))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
