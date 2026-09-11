# -*- coding: utf-8 -*-
"""check_ui_workflow.py — 校验 ComfyUI **UI 格式**工作流的 widget 槽位是否错位。

【为什么需要它】
ComfyUI 前端的 widget 列表 ≠ 节点 ``INPUT_TYPES`` 里声明的 widget 列表：

  * 带 ``control_after_generate: True`` 的 widget（典型 ``seed``）后面，
    前端会**自动插一格**下拉 —— 它只出现在 ``widgets_values`` 里，**不进 ``inputs`` 列表**；
  * ``LoadImage`` 的 ``image`` 后面会追加 ``upload``；
  * 自定义节点的 ``addDOMWidget`` 会追加在**末尾**。

用脚本按 ``INPUT_TYPES`` 顺序写 ``widgets_values`` 时，只要漏掉**中间**那一格，
之后所有取值会**整体前移一位**。文件照样能打开、能提交、**不报任何错**，
但采样参数全是错的。

实测（2026-09-11）：``SelfLiftH3Sampler`` 漏掉 seed 后那一格 →
``cfg`` 5(应 1)、``transition_step`` 0.5(应 5)、``lowres_scale`` 0(应 0.5)、
``w_max`` 收到 ``"xxx.safetensors"``、``upscaler_model`` 收到 ``false``。

【判据】
按前端槽位顺序**逐位**把取值喂给节点 schema 做类型 / 范围 / 候选项校验。
错位必然留下硬伤。**不要**拿 ``widgets_values_named`` 当槽位映射依据 ——
它是第三方 UI 扩展写的，某些节点类型下本身就是坏的。

【用法】
    python check_ui_workflow.py --all
    python check_ui_workflow.py path/to/workflow.json [更多路径...]
    python check_ui_workflow.py --all --comfyui /path/to/ComfyUI

需要一个**正在运行**的 ComfyUI（用来取 ``/object_info`` 的真实 schema）；
用 ``--comfyui`` 或环境变量 ``COMFYUI_PATH`` 指定 ComfyUI 根目录，
不指定时按本脚本位置自动上溯（本包装在 ``<ComfyUI>/custom_nodes/`` 下时可用）。
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import sys
import urllib.request

DEFAULT_API = "http://127.0.0.1:8188"
WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"}

# 前端追加在**末尾**、不进 INPUT_TYPES 的 widget（末尾追加不会造成错位）
TAIL_INJECTED = {"upload", "lora面板", "视频上传", "音频上传", "image_upload"}


def guess_comfyui_root() -> str:
    """本脚本在 <ComfyUI>/custom_nodes/<pkg>/tools/ 下时，往上三级就是 ComfyUI 根。"""
    here = os.path.dirname(os.path.abspath(__file__))
    for up in (3, 2, 1):
        cand = here
        for _ in range(up):
            cand = os.path.dirname(cand)
        if os.path.isdir(os.path.join(cand, "comfy")) and os.path.isdir(os.path.join(cand, "custom_nodes")):
            return cand
    return ""


def load_object_info(api: str) -> dict:
    return json.loads(urllib.request.urlopen(f"{api}/object_info", timeout=180).read().decode("utf-8"))


def _ty(spec):
    if not isinstance(spec, list) or not spec:
        return None
    return "COMBO" if isinstance(spec[0], list) else spec[0]


def spec_of(defn, name):
    for sec in ("required", "optional"):
        if name in (defn.get("input", {}).get(sec) or {}):
            return defn["input"][sec][name]
    return None


def frontend_slots(defn) -> list:
    """推算前端实际槽位顺序（含 control_after_generate 注入）。"""
    out = []
    for sec in ("required", "optional"):
        for name, spec in (defn.get("input", {}).get(sec) or {}).items():
            if _ty(spec) not in WIDGET_TYPES:
                continue
            out.append(name)
            extra = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            if extra.get("control_after_generate"):
                out.append("control_after_generate")
    return out


def check_value(defn, name, value):
    """返回 (问题描述, 是否硬错误)。

    COMBO 的候选项常是**动态文件列表**（input/ 下的图/视频、loras 目录），
    此刻不在列表 ≠ 非法，故只作提示；**类型不对**才是错位的特征，作硬错误。
    """
    spec = spec_of(defn, name)
    if spec is None:
        return "", False
    t = _ty(spec)
    extra = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
    if t == "COMBO":
        if not isinstance(value, str):
            return "%s=%r 不是合法 COMBO 值（期望字符串）" % (name, value), True
        opts = spec[0] if isinstance(spec[0], list) else extra.get("options")
        if opts and value not in opts:
            return "%s=%r 不在当前候选项内（动态文件列表？请确认文件在）" % (name, value), False
        return "", False
    if t == "INT" and (not isinstance(value, int) or isinstance(value, bool)):
        return "%s 期望 INT 收到 %r" % (name, value), True
    if t == "FLOAT" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        return "%s 期望 FLOAT 收到 %r" % (name, value), True
    if t == "STRING" and not isinstance(value, str):
        return "%s 期望 STRING 收到 %r" % (name, value), True
    if t == "BOOLEAN" and not isinstance(value, bool):
        return "%s 期望 BOOLEAN 收到 %r" % (name, value), True
    if t in ("INT", "FLOAT") and isinstance(value, (int, float)) and not isinstance(value, bool):
        if extra.get("min") is not None and value < extra["min"]:
            return "%s=%r < min %s" % (name, value, extra["min"]), True
        if extra.get("max") is not None and value > extra["max"]:
            return "%s=%r > max %s" % (name, value, extra["max"]), True
    return "", False


def check_file(path: str, oi: dict, verbose: bool = True) -> int:
    name = os.path.basename(path)
    try:
        wf = json.load(io.open(path, encoding="utf-8"))
    except Exception as e:
        print("  [ERROR] %s 解析失败：%s" % (name, e))
        return 1
    if not isinstance(wf, dict) or "nodes" not in wf:
        print("  [SKIP]  %s 不是 UI 格式工作流（缺 nodes）" % name)
        return 0

    problems, warns = [], []
    for n in wf.get("nodes") or []:
        if n.get("mode") == 4:      # bypass
            continue
        defn = oi.get(n.get("type"))
        if defn is None:
            warns.append("node %s 类型 %s 服务端没有（前端虚拟节点或未装该包）"
                         % (n["id"], n.get("type")))
            continue
        wv = n.get("widgets_values")
        if not isinstance(wv, list):
            continue

        exp = frontend_slots(defn)
        for k, v in zip(exp, wv):
            msg, hard = check_value(defn, k, v)
            if msg:
                (problems if hard else warns).append("node %s %s: %s" % (n["id"], n["type"], msg))

        if len(wv) > len(exp):
            nm = n.get("widgets_values_named")
            nk = list(nm.keys()) if isinstance(nm, dict) else []
            tail = nk[len(exp):] if nk else []
            if not tail or any(t not in TAIL_INJECTED for t in tail):
                problems.append(
                    "node %s %s: widgets_values 比前端槽位多 %d 项（尾部 %s）—— "
                    "若不是 upload/DOM 面板，就是多写了"
                    % (n["id"], n["type"], len(wv) - len(exp), tail or "未知"))

    if verbose or problems or warns:
        print("")
        print("  %s  —— 节点 %d / 连线 %d"
              % (name, len(wf.get("nodes") or []), len(wf.get("links") or [])))
        for p in problems:
            print("     ✗ " + p)
        for w in warns:
            print("     ⚠ " + w)
        if not problems and not warns:
            print("     ✓ 槽位与取值全部合法")
    return len(problems)


def main():
    ap = argparse.ArgumentParser(description="校验 ComfyUI UI 工作流的 widget 槽位是否错位")
    ap.add_argument("paths", nargs="*", help="工作流 JSON 路径（--all 时忽略）")
    ap.add_argument("--all", action="store_true", help="扫描 ComfyUI 的 workflows 目录")
    ap.add_argument("--api", default=DEFAULT_API, help="ComfyUI 地址（默认 %s）" % DEFAULT_API)
    ap.add_argument("--comfyui", default=os.environ.get("COMFYUI_PATH", ""),
                    help="ComfyUI 根目录（默认按本脚本位置上溯，或环境变量 COMFYUI_PATH）")
    ap.add_argument("--quiet", action="store_true", help="只打印有问题的")
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    root = a.comfyui or guess_comfyui_root()
    wf_dir = os.path.join(root, "user", "default", "workflows") if root else ""

    print("=" * 100)
    print("ComfyUI UI 工作流 · widget 槽位校验")
    print("  schema 来源：%s/object_info" % a.api.rstrip("/"))
    print("  工作流目录：%s" % (wf_dir or "(未定位，请用 --all 时指定 --comfyui)"))
    print("=" * 100)

    try:
        oi = load_object_info(a.api.rstrip("/"))
    except Exception as e:
        print("\n[FAIL] 取不到 object_info：%r" % (e,))
        print("       请先启动 ComfyUI，或用 --api 指定地址。")
        return 2

    if a.all:
        if not wf_dir or not os.path.isdir(wf_dir):
            print("\n[FAIL] 找不到 workflows 目录。请用 --comfyui 指定 ComfyUI 根目录。")
            return 2
        paths = sorted(glob.glob(os.path.join(wf_dir, "*.json")))
    else:
        paths = a.paths
    if not paths:
        print("\n没给路径。用 --all 扫全目录，或直接给文件路径。")
        return 2

    total = bad = 0
    for p in paths:
        n = check_file(p, oi, verbose=not a.quiet)
        total += n
        bad += 1 if n else 0

    print("")
    print("=" * 100)
    print("文件 %d 个，有问题 %d 个，问题合计 %d 条" % (len(paths), bad, total))
    print("=" * 100)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
