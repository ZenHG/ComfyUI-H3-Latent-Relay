# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Relay-Kit contributors
"""差分等价测试：`relay_core` 若干函数**重写前后是否行为逐位一致**。

【为什么需要它】
2026-09-19 为消除与第三方 GPL 包的**表达层重合**，重写了三处：
  ① `apply_relay` 锚位合成段 ② 网格辅助 `step_offsets` / `steps_for_frames`
  ③ `audio_tail_from_latent` 的音频尾段切法。

重写的风险不是"跑不起来"——单测能发现——而是**行为悄悄变了**却没被断言覆盖。
所以这里不比单测，而是**把旧实现原样捞回来，与新实现喂同一批输入，逐位比对**。

【旧实现从哪来】
`git show <rev>:relay_core.py` —— **不手工转录**，避免抄错。
重写已提交后请显式传父提交：
    python tools/verify_rewrite_equivalence.py --old-rev <重写前的提交>

【自证：检查器必须能失败】
一个永远说 OK 的检查器等于没有检查器。每个目标都配**变异体**（故意做坏的实现），
必须被抓住，否则本工具自身判 FAIL。
> 实战记录：首版别名检查只比 conditioning、漏掉 `plan.keyframes` 的引用泄漏，
> 正是被这一步揪出来的。别删这一步。

用法：
    set COMFYUI_PATH=<你的 ComfyUI 根目录>
    python tools/verify_rewrite_equivalence.py [--old-rev HEAD]
"""
from __future__ import annotations

import argparse
import copy
import io
import itertools
import math
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_COMFY = os.environ.get("COMFYUI_PATH") or os.environ.get("COMFYUI_ROOT")
if _COMFY and _COMFY not in sys.path:
    sys.path.insert(0, _COMFY)
sys.path.insert(0, REPO)


def _extract(source: str, name: str, required: bool = True) -> str:
    """从源码里切出某个顶层函数的完整定义。

    ``required=False`` 时找不到就返回空串 —— 重写会**新增**辅助函数，
    旧版本里当然没有，加载旧源码不能因此报错。
    """
    lines = source.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if re.match(r"def %s\s*\(" % re.escape(name), ln):
            start = i
            break
    if start is None:
        if required:
            raise SystemExit("[FAIL] 源码里找不到函数 %s" % name)
        return ""
    # ⚠ 先吃掉**可能跨行**的签名：`def f(\n  a, b\n) -> T:` 的右括号是顶格的，
    #   直接找"下一行顶格"会把签名腰斩（本工具第一版就栽在这）。数括号到归零。
    depth, i = 0, start
    while i < len(lines):
        depth += lines[i].count("(") - lines[i].count(")")
        if depth <= 0 and lines[i].rstrip().endswith(":"):
            break
        i += 1
    end = i + 1
    while end < len(lines):
        ln = lines[end]
        if ln.strip() and not ln[:1].isspace():
            break
        end += 1
    return "\n".join(lines[start:end])


def _load(source: str, names, ns: dict) -> dict:
    """把若干函数 exec 进给定命名空间（缺的全局由调用方提供）。"""
    out = dict(ns)
    bodies = [b for b in (_extract(source, n, required=False) for n in names) if b]
    blob = "from __future__ import annotations\n" + "\n\n".join(bodies)
    exec(compile(blob, "<extracted>", "exec"), out)
    return out


# --------------------------------------------------------------- 目标一：apply_relay
class FakePlan:
    """`apply_relay` 只读这几个属性，用最小替身即可，不必造真的 RelayPlan。"""

    def __init__(self, applied=True, span=22, keyframes=None,
                 audio_ref=None, anchor_ref=None):
        self.applied = applied
        self.span = span
        self.keyframes = [] if keyframes is None else keyframes
        self.audio_ref = audio_ref
        self.anchor_ref = anchor_ref
        self.notes = []


def _kf(pos, tag="x"):
    return {"resolved_frame_index": pos, "latent": "L-%s-%s" % (tag, pos)}


def _cond(*groups):
    return [[{"emb": i}, {"minimax_keyframes": list(g)}]
            for i, g in enumerate(groups)]


def _apply_relay_cases():
    out = []
    out.append(("空 keyframes", _cond([]), FakePlan()))
    out.append(("全在钉住区内（0/5/21, span=22）",
                _cond([_kf(0), _kf(5), _kf(21)]), FakePlan()))
    out.append(("全在钉住区外（22/100/191）",
                _cond([_kf(22), _kf(100), _kf(191)]), FakePlan()))
    out.append(("边界双侧：21 在内 / 22 在外",
                _cond([_kf(21), _kf(22)]), FakePlan()))
    out.append(("span=5 小窗：4 在内 / 5 在外",
                _cond([_kf(4), _kf(5)]), FakePlan(span=5)))
    out.append(("span=0 退化：无钉住区",
                _cond([_kf(0), _kf(3)]), FakePlan(span=0)))
    out.append(("多条 conditioning：落点跨条累积",
                _cond([_kf(1), _kf(30)], [_kf(2), _kf(40)]), FakePlan()))
    out.append(("缺 resolved_frame_index → 按 0 ⇒ 出局",
                _cond([{"latent": "no-index"}, _kf(99)]), FakePlan()))
    out.append(("重复落点 → report 去重",
                _cond([_kf(3), _kf(3), _kf(3)]), FakePlan()))
    out.append(("新增锚为空", _cond([_kf(7)]), FakePlan(keyframes=[])))
    out.append(("带新增锚 7 条", _cond([_kf(0), _kf(191)]),
                FakePlan(keyframes=[_kf(i, "new") for i in range(7)])))
    out.append(("applied=False → 直通",
                _cond([_kf(0)]), FakePlan(applied=False)))
    out.append(("audio_ref 单挂", _cond([_kf(50)]),
                FakePlan(audio_ref={"kind": "audio"})))
    out.append(("anchor_ref 单挂", _cond([_kf(50)]),
                FakePlan(anchor_ref={"kind": "anchor"})))
    out.append(("audio + anchor 双挂（顺序 audio 在前）", _cond([_kf(50)]),
                FakePlan(audio_ref={"kind": "audio"},
                         anchor_ref={"kind": "anchor"})))
    out.append(("双挂 + 有锚出局（三件事一起走）",
                _cond([_kf(0), _kf(9), _kf(200)]),
                FakePlan(keyframes=[_kf(i, "new") for i in range(3)],
                         audio_ref={"kind": "audio"},
                         anchor_ref={"kind": "anchor"})))
    return out


def _apply_relay_cmp(old, new, name, cond, plan):
    c_old, c_new = copy.deepcopy(cond), copy.deepcopy(cond)
    p_old, p_new = copy.deepcopy(plan), copy.deepcopy(plan)
    o_out, n_out = old(c_old, p_old), new(c_new, p_new)
    bad = []

    def shape(out):
        return [(e, [int(k.get("resolved_frame_index", 0))
                     for k in x.get("minimax_keyframes") or []])
                for e, x in out]

    def refs(out):
        return [list(x.get("minimax_refs") or []) for _e, x in out]

    if shape(o_out) != shape(n_out):
        bad.append("位置序列不同：旧 %s / 新 %s" % (shape(o_out), shape(n_out)))
    if refs(o_out) != refs(n_out):
        bad.append("refs 不同：旧 %s / 新 %s" % (refs(o_out), refs(n_out)))
    if len(p_old.notes) != len(p_new.notes):
        bad.append("note 条数不同：旧 %d / 新 %d"
                   % (len(p_old.notes), len(p_new.notes)))

    # 交叉验证：report 落点 == 契约定义的出局集合（落点 < span）。
    # ⚠ 不能拿「输入位置集 − 输出位置集」算：新增锚可能与出局锚撞同一落点。
    zone_end = int(plan.span)
    in_pos = [int(k.get("resolved_frame_index", 0))
              for g in cond for k in g[1]["minimax_keyframes"]]
    expect = sorted({p for p in in_pos if p < zone_end})
    if not plan.applied:
        if p_new.notes:
            bad.append("applied=False 却加了 note：%r" % (p_new.notes,))
    elif expect:
        if len(p_new.notes) != 1:
            bad.append("应有出局 note，实得 %d 条" % len(p_new.notes))
        else:
            m = re.search(r"\[([0-9,\s]*)\]", p_new.notes[0])
            got = ([int(x) for x in m.group(1).split(",") if x.strip()]
                   if m else None)
            if got != expect:
                bad.append("report 落点与契约不符：报 %s / 应 %s" % (got, expect))
    elif p_new.notes:
        bad.append("无锚出局却加了 note：%r" % (p_new.notes,))

    # 别名：输出锚不得与 conditioning 或 plan.keyframes 共享引用。
    # ⚠ 只比 conditioning 会漏掉"新增锚不做副本"——那泄漏的是 plan 的引用。
    if plan.applied:
        srcs = [k for g in c_new for k in g[1]["minimax_keyframes"]]
        srcs += list(p_new.keyframes or [])
        for _e, x in n_out:
            if any(k is s for k in x["minimax_keyframes"] for s in srcs):
                bad.append("输出锚与输入/计划共享引用（别名泄漏）")
                break
    if not plan.applied and n_out is not c_new:
        bad.append("applied=False 时未返回原对象")
    return bad, shape(n_out)


def _apply_relay_mutants(src):
    out = []
    a = src.replace("outside + [dict(a) for a in plan.keyframes]",
                    "outside + [a for a in plan.keyframes]")
    if a != src:
        out.append(("新增锚不做副本（别名泄漏）", a))
    b = src.replace("% (zone_end - 1, sorted(set(redundant)),",
                    "% (zone_end - 1, [],")
    if b != src:
        out.append(("report 落点写空", b))
    return out


# --------------------------------------------------- 目标二：网格辅助（纯整数函数）
def _grid_cases():
    return [("steps_for_frames n=%d" % n, n)
            for n in list(range(-2, 60)) + [73, 90, 107, 124, 191, 192, 200, 362]]


def _grid_cmp(old, new, name, n):
    bad = []
    try:
        o = old(n)
    except Exception as e:
        o = ("raise", type(e).__name__)
    try:
        w = new(n)
    except Exception as e:
        w = ("raise", type(e).__name__)
    if o != w:
        bad.append("steps_for_frames(%r)：旧 %r / 新 %r" % (n, o, w))
    # 一致性：算出的步数必须真能覆盖 n 帧（自洽交叉验证，不依赖旧实现）
    if isinstance(w, int) and w >= 0:
        covered = from_relay.pixel_frames(w)
        if covered != n:
            bad.append("自洽失败：pixel_frames(%d)=%d ≠ %d" % (w, covered, n))
    return bad, "→ %r" % (w,)


def _grid_mutants(src):
    out = []
    a = src.replace("return 0 if n == 0 else None", "return None")
    if a != src:
        out.append(("丢掉 n=0 → 0 的退化分支", a))
    # ⚠ 别用「break 改 continue」当变异体 —— 那是**等价变异**：
    #   total 严格递增，越界后 continue 到循环尾，结果与 break 相同。
    #   （本工具第一版就选了它，然后误以为"检查器瞎"。）
    b = src.replace("return steps if total == n else None", "return steps")
    if b != src:
        out.append(("非网格值不返回 None（丢掉网格判据）", b))
    return out


def _offsets_cases():
    return [("step_offsets n=%d" % n, n) for n in list(range(0, 40)) + [57, 90, 124]]


def _offsets_cmp(old, new, name, n):
    o, w = old(n), new(n)
    bad = []
    if o != w:
        bad.append("step_offsets(%r)：旧 %s / 新 %s" % (n, o[:8], w[:8]))
    if w:
        if w[0] != 0:
            bad.append("首位应为 0，实得 %r" % (w[0],))
        if w != sorted(w) or len(set(w)) != len(w):
            bad.append("位置序列应严格递增：%s" % (w[:8],))
    if len(w) != n:
        bad.append("长度应等于 token 数 %d，实得 %d" % (n, len(w)))
    return bad, "%d 项" % len(w)


def _offsets_mutants(src):
    out = []
    a = src.replace("return [0, *itertools.accumulate(spans)][:n]",
                    "return [0, *itertools.accumulate(spans)]")
    if a != src:
        out.append(("排他前缀和忘了截断 [:n]", a))
    return out


# ------------------------------------------- 目标三：音频尾段（含 tensor 切片语义）
def _audio_cases():
    return [
        ("a_frames=22 / src=192（标准）", 22, 192),
        ("a_frames=7  / src=192", 7, 192),
        ("a_frames=1  / src=192（最小窗）", 1, 192),
        ("a_frames=192 / src=192（正好取满）", 192, 192),
        ("a_frames=300 / src=192（要的比有的多 ⇒ 夹住）", 300, 192),
        ("a_frames=0  ⇒ 应 raise", 0, 192),
        ("src=100 ⇒ 栅格外溢超半整步（grid_off）", 22, 100),
        ("src=190 ⇒ 外溢在带内", 22, 190),
    ]


def _audio_cmp(old, new, name, a_frames, src_total):
    bad = []

    def run(fn):
        try:
            t, take, slack, raw, off = fn(_LATENT, a_frames, src_total)
            return ("ok", t, take, slack, raw, off)
        except Exception as e:
            return ("raise", type(e).__name__, str(e)[:60])

    o, w = run(old), run(new)
    if o[0] != w[0]:
        bad.append("一个 raise 一个不 raise：旧 %r / 新 %r" % (o[0], w[0]))
    elif o[0] == "raise":
        pass  # 两边都 raise 即等价（文案不参与比对）
    else:
        if not torch.equal(o[1], w[1]):
            bad.append("tail 张量不等（形状 旧 %s / 新 %s）"
                       % (tuple(o[1].shape), tuple(w[1].shape)))
        for i, label in ((2, "take"), (3, "grid_slack"), (4, "raw_steps"), (5, "grid_off")):
            if o[i] != w[i]:
                bad.append("%s 不同：旧 %r / 新 %r" % (label, o[i], w[i]))
    detail = "两边都 raise" if w[0] == "raise" else "tail %s" % (tuple(w[1].shape),)
    return bad, detail


def _audio_mutants(src):
    out = []
    a = src.replace("take = min(want, total_t)", "take = want")
    if a != src:
        out.append(("超长请求不夹到源段长度", a))
    b = src.replace("tail = audio[:1].narrow(-1, total_t - take, take).clone()",
                    "tail = audio[:1].narrow(-1, 0, take).clone()")
    if b != src:
        out.append(("切的是头不是尾", b))
    return out


# --------------------------------------------------------------------------- 主流程
_LATENT = None
torch = None
from_relay = None


def _targets(src_old, src_new):
    """(标签, 旧函数, 新函数, 用例集, 比对器, 变异体, 需要的全局名)"""
    from relay_core import FRAME_PER_TOKEN, FRAME_RESCALE, FPS, AUDIO_HZ
    import relay_core as CORE

    common = {"itertools": itertools, "math": math, "torch": torch,
              "FRAME_PER_TOKEN": FRAME_PER_TOKEN, "FRAME_RESCALE": FRAME_RESCALE,
              "FPS": FPS, "AUDIO_HZ": AUDIO_HZ,
              "audio_from_latent": CORE.audio_from_latent,
              "KEY_EXPORT_TAIL_AUDIO": CORE.KEY_EXPORT_TAIL_AUDIO,
              "Any": object, "Tuple": tuple, "List": list, "Optional": object}

    t = []
    t.append(("apply_relay",
              ["_anchor_position", "_partition_anchors", "apply_relay"],
              "apply_relay",
              _apply_relay_cases, _apply_relay_cmp, _apply_relay_mutants, common))
    t.append(("steps_for_frames", ["steps_for_frames"], "steps_for_frames",
              _grid_cases, _grid_cmp, _grid_mutants, common))
    t.append(("step_offsets", ["step_offsets"], "step_offsets",
              _offsets_cases, _offsets_cmp, _offsets_mutants, common))
    t.append(("audio_tail_from_latent", ["audio_tail_from_latent"],
              "audio_tail_from_latent", _audio_cases, _audio_cmp,
              _audio_mutants, common))
    return t


def _run_target(label, old, new, cases, cmp_fn, verbose=True):
    passed = failed = 0
    for case in cases:
        name, args = case[0], case[1:]
        bad, detail = cmp_fn(old, new, name, *args)
        if bad:
            failed += 1
            if verbose:
                print("  [FAIL] %s" % name)
                for b in bad:
                    print("         %s" % b)
        else:
            passed += 1
            if verbose:
                print("  [OK]   %-46s %s" % (name, detail))
    return passed, failed


def main() -> int:
    global _LATENT, torch, from_relay
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-rev", default="HEAD")
    ap.add_argument("--skip-selftest", action="store_true")
    args = ap.parse_args()

    import torch as _t
    torch = _t
    import relay_core as CORE
    from_relay = CORE
    import comfy.nested_tensor as NT

    steps = CORE.steps_for_frames(192)
    g = torch.Generator().manual_seed(7)
    _LATENT = {"samples": NT.NestedTensor([
        torch.randn(1, 24, steps, 28, 48, generator=g),
        torch.randn(1, 32, 2, int(round(CORE.FRAME_RESCALE * 192)), generator=g),
    ])}

    old_src = subprocess.check_output(
        ["git", "show", "%s:relay_core.py" % args.old_rev], cwd=REPO).decode("utf-8")
    with io.open(os.path.join(REPO, "relay_core.py"), encoding="utf-8") as f:
        new_src = f.read()

    total_pass = total_fail = 0
    print("=" * 78)
    print("差分等价：relay_core 重写函数  old=%s  vs  new=工作树" % args.old_rev)
    print("=" * 78)

    for label, names, primary, cases_fn, cmp_fn, mut_fn, common in _targets(
            old_src, new_src):
        print()
        print("── %s ──" % label)
        old = _load(old_src, names, common)[primary]
        new = _load(new_src, names, common)[primary]
        p, f = _run_target(label, old, new, cases_fn(), cmp_fn, verbose=True)
        total_pass += p
        total_fail += f

        if not args.skip_selftest:
            muts = mut_fn(new_src)
            if not muts:
                print("  [FAIL] %s：一个变异体都没造出来 —— 源码结构与预期不符，"
                      "本工具已与实现脱节" % label)
                total_fail += 1
            for mname, msrc in muts:
                m = _load(msrc, names, common)[primary]
                _p, mf = _run_target(label, old, m, cases_fn(), cmp_fn,
                                     verbose=False)
                if mf:
                    print("  [OK]   自证 · %s → 被抓到（%d 例失败）" % (mname, mf))
                else:
                    print("  [FAIL] 自证 · %s → **没被抓到**：检查器对这类缺陷是瞎的"
                          % mname)
                    total_fail += 1

    print()
    print("=" * 78)
    print("结果：通过 %d / 失败 %d" % (total_pass, total_fail))
    print("=" * 78)
    return 1 if total_fail else 0


if __name__ == "__main__":
    sys.exit(main())
