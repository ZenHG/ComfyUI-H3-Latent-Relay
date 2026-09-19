# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Relay-Kit contributors
"""节点层功能冒烟：**真调用** 8 个节点的方法，逐个断言。

【补的是哪一层】
`tests/test_relay_core.py` 测的是 `relay_core`（纯张量算法层），
`tools/review_050.py` 测的是**schema**（槽位/契约/一致性）。
**两层都没真跑过 `nodes.py` 里的节点方法。** 本工具补这一层：
按每个节点自己声明的 `INPUT_TYPES` 造参、按其默认值调用、断言返回路数。

【判据】
  1. 能 import、能实例化
  2. 方法能被调用且不抛异常
  3. 返回**路数** == `RETURN_TYPES` 长度（历史上出过"直通分支少返回一路"的 bug）
  4. 返回的 report 是非空字符串
  5. 保存/读回**往返一致**（LatentSave ↔ LatentLoad）

⚠️ 本工具**不判画面质量**（那要 GPU + 人眼），只判"功能通不通"。

用法：
    set COMFYUI_PATH=I:/ComfyUI
    I:/python/python.exe tools/smoke_nodes.py
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import types

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COMFY = os.environ.get("COMFYUI_PATH") or os.environ.get("COMFYUI_ROOT")
if not _COMFY or not os.path.isdir(_COMFY):
    sys.exit("[FAIL] 需要 COMFYUI_PATH 指向 ComfyUI 根目录")
sys.path.insert(0, _COMFY)
sys.path.insert(0, KIT)

import torch  # noqa: E402
import comfy.nested_tensor as NT  # noqa: E402

# 目录名含连字符，不能直接当包名 → 伪造包壳再按文件加载（与 review_050 同法）
_pkg = types.ModuleType("h3relay_kit")
_pkg.__path__ = [KIT]
sys.modules["h3relay_kit"] = _pkg
_spec = importlib.util.spec_from_file_location("h3relay_kit.nodes",
                                               os.path.join(KIT, "nodes.py"))
N = importlib.util.module_from_spec(_spec)
sys.modules["h3relay_kit.nodes"] = N
_spec.loader.exec_module(N)

import relay_core as CORE  # noqa: E402

RUN = "_smoke_nodes"
PASS, FAIL = [], []


def ck(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("OK" if cond else "FAIL", name,
                           ("  " + detail) if detail else ""))


def make_latent(frames, seed=0):
    steps = CORE.steps_for_frames(frames)
    assert steps is not None, "%d 帧不在网格上" % frames
    g = torch.Generator().manual_seed(seed)
    v = torch.randn(1, 24, steps, 28, 48, generator=g)
    a = torch.randn(1, 32, 2, int(round(CORE.FRAME_RESCALE * frames)), generator=g)
    return {"samples": NT.NestedTensor([v, a])}


def make_images(n=40):
    g = torch.Generator().manual_seed(3)
    return torch.rand(n, 64, 48, 3, generator=g)


def make_audio(n=400000, sr=44100):
    """音频要**够长**：TrimAV 会按帧数裁采样点（22 帧 @24fps ≈ 40425 点），
    太短会正确地 raise。第一版给了 40000 点，误判成节点故障。"""
    g = torch.Generator().manual_seed(4)
    return {"waveform": torch.rand(1, 2, n, generator=g), "sample_rate": sr}


def make_cond():
    return [[torch.zeros(1, 8), {"minimax_keyframes": []}]]


def _spec_default(spec):
    """从 INPUT_TYPES 的一条声明里取「声明的默认值」。"""
    if not isinstance(spec, tuple):
        return None
    opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
    if "default" in opts:
        return opts["default"]
    return None


def build_kwargs(cls, ctx):
    """按节点自己声明的槽位造参：能给的给真值，其余用声明的默认值。"""
    it = cls.INPUT_TYPES()
    kw, missing = {}, []
    for group in ("required", "optional"):
        for name, spec in (it.get(group) or {}).items():
            if name in ctx:
                kw[name] = ctx[name]
                continue
            t = spec[0] if isinstance(spec, tuple) else spec
            d = _spec_default(spec)
            if isinstance(t, list):          # COMBO
                kw[name] = d if d is not None else t[0]
            elif t == "INT":
                kw[name] = int(d if d is not None else 1)
            elif t == "FLOAT":
                kw[name] = float(d if d is not None else 1.0)
            elif t == "STRING":
                kw[name] = str(d if d is not None else "")
            elif t == "BOOLEAN":
                kw[name] = bool(d if d is not None else False)
            elif t in ("LATENT", "IMAGE", "AUDIO", "CONDITIONING"):
                if group == "required" and d is None:
                    missing.append(name)
                kw[name] = None
            else:
                kw[name] = None
    return kw, missing


def run_node(cls_name, ctx, expect_raise=False):
    cls = getattr(N, cls_name)
    fn_name = getattr(cls, "FUNCTION", None)
    if not fn_name:
        ck("%s 有 FUNCTION" % cls_name, False, "缺 FUNCTION 属性")
        return None
    kw, missing = build_kwargs(cls, ctx)
    try:
        out = getattr(cls(), fn_name)(**kw)
    except Exception as e:
        if expect_raise:
            ck("%s 调用（预期内 raise）" % cls_name, True,
               "%s: %s" % (type(e).__name__, str(e).splitlines()[0][:60]))
            return None
        ck("%s 调用不抛异常" % cls_name, False,
           "%s: %s ｜ 缺参 %s" % (type(e).__name__,
                                  str(e).splitlines()[0][:80], missing))
        return None
    if expect_raise:
        ck("%s 调用（预期内 raise）" % cls_name, False, "未 raise")
        return None
    n_expect = len(getattr(cls, "RETURN_TYPES", ()))
    # ComfyUI 约定：无输出节点返回 `{}`（或 `()`）—— 那是 **0 路**，不是 1 路。
    # （本工具第一版把 `{}` 数成 1 路，误报 H3RelayChain 路数不符。）
    if isinstance(out, dict):
        got = 0
    elif isinstance(out, tuple):
        got = len(out)
    else:
        got = 1
    ck("%s 返回路数 == RETURN_TYPES(%d)" % (cls_name, n_expect),
       got == n_expect, "实得 %d 路" % got)
    # report 路必须是非空字符串
    names = list(getattr(cls, "RETURN_NAMES", ()))
    if "report" in names:
        rep = out[names.index("report")]
        ck("%s report 非空" % cls_name, isinstance(rep, str) and len(rep) > 0,
           "长度 %d" % (len(rep) if isinstance(rep, str) else -1))
    return out


def main() -> int:
    outdir = os.path.join(_COMFY, "output", "relay_kit", RUN)
    if os.path.isdir(outdir):
        shutil.rmtree(outdir, ignore_errors=True)

    prev = make_latent(192, seed=11)
    cur = make_latent(192, seed=12)
    ctx = {"latent": cur, "context_latent": prev, "images": make_images(),
           "audio": make_audio(), "conditioning": make_cond(),
           "run_id": RUN, "stage_index": 1, "trim_frames": 22,
           "context_frames": 22, "fps": 24.0, "mask_mode": "hard",
           "note": "smoke", "segments": 2, "explicit_path": ""}

    print("=" * 78)
    print("节点层功能冒烟（真调用，非 schema 检查）")
    print("=" * 78)

    print("\n── 1. 存 / 读 往返 ──")
    # ⚠ 存的必须是**要和读回比对的那一个**：第 1 版存了 cur（seed 12）
    #   却拿 prev（seed 11）比对，误报"往返不一致"。这里统一用 prev。
    out = run_node("H3RelayLatentSave", dict(ctx, latent=prev, stage_index=0))
    saved_path = None
    if out:
        names = list(N.H3RelayLatentSave.RETURN_NAMES)
        saved_path = out[names.index("path")]
        ck("落盘文件真的存在", os.path.isfile(saved_path or ""), str(saved_path))
    out = run_node("H3RelayLatentLoad", ctx)
    if out and saved_path:
        names = list(N.H3RelayLatentLoad.RETURN_NAMES)
        back = out[names.index("context_latent")]
        bv, ba = CORE.streams_from_latent(back)
        pv, pa = CORE.streams_from_latent(prev)
        ck("读回的视频流逐位一致", torch.equal(bv, pv))
        ck("读回的音频流逐位一致", torch.equal(ba, pa))

    print("\n── 2. 两个桥 ──")
    run_node("H3RelayCopyBridge", ctx)
    run_node("H3RelayMotionContext", ctx)

    print("\n── 3. 裁重叠 / 后处理 / 音频缝 ──")
    run_node("H3RelayTrimAV", ctx)
    run_node("H3RelayPost", ctx)
    run_node("H3RelayAudioSeam", ctx)

    print("\n── 4. 控制节点 ──")
    run_node("H3RelayChain", ctx)

    print("\n── 5. 反证：缺上一段必须报错（不能静默出坏片） ──")
    bad = dict(ctx, run_id="_smoke_does_not_exist", stage_index=1,
               context_latent=None)
    run_node("H3RelayLatentLoad", bad, expect_raise=True)

    shutil.rmtree(outdir, ignore_errors=True)

    print()
    print("=" * 78)
    print("结果：通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("   失败：%s" % f)
    print("=" * 78)
    print("⚠️ 本工具只判「功能通不通」，不判画面质量（那要 GPU + 人眼）。")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
