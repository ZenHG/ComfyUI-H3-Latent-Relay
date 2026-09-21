# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Relay-Kit contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md

"""🧪 实验层单测（relay_core.py §「实验层」的断言集合）——零 GPU、零模型、秒级

跑法（在包目录下）：
    python tests/test_experimental.py

覆盖调研文档 ``RESEARCH_seam_frontier.md`` 里四个**尚未投产**的手段
（``E1``~``E5``，见 relay_core.py 的实验层注释）：

  E1 多尺度历史   build_history_refs / segment 分级抽稀
  E2 SDEdit 软钉入 sdeedit_noise
  E3 DTW 残留量   dtw_residual / head_repeat_dtw
  E4 漂移曲线     segment_appearance_stats / drift_curve
  E5 床声去重复   bed_jitter_start

纪律（与 test_relay_core.py 同一套）：
  · **默认全关 ⇒ 逐位不变**——每个手段都有一条「默认值原样返回」断言；
  · **改动机理可证伪**——不用「看起来对」，用构造样例把数值钉死；
  · 硬错误一律 raise，不测「静默降级」。
"""

import os
import sys

import torch

# ---------------------------------------------------------------- 定位 ComfyUI
_KIT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COMFY = os.environ.get("COMFYUI_PATH") or os.path.dirname(os.path.dirname(_KIT_DIR))
if not os.path.isdir(os.path.join(_COMFY, "comfy")):
    _MSG = ("\n[FAIL] 找不到 ComfyUI 根目录（试过：%s）\n"
            "       设 COMFYUI_PATH=I:/ComfyUI 后重试\n" % _COMFY)
    if "pytest" in sys.modules:
        import pytest
        pytest.skip("找不到 ComfyUI 根目录（试过：%s）；设 COMFYUI_PATH 后重试" % _COMFY,
                    allow_module_level=True)
    sys.stderr.write(_MSG)
    raise SystemExit(2)
sys.path.insert(0, _COMFY)
sys.path.insert(0, _KIT_DIR)

import comfy.nested_tensor as NT  # noqa: E402

import relay_core as CORE  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  " + detail) if detail else ""))


def expect_raise(name, fn, needle=""):
    try:
        fn()
    except Exception as e:
        ok = (needle in str(e)) if needle else True
        check(name, ok, "→ %s: %s" % (type(e).__name__, str(e).splitlines()[0][:90]))
        return
    check(name, False, "→ 未抛异常（应当 raise）")


def make_latent(frames, seed=0):
    """合成一段 H3 AV latent（与 test_relay_core.py 同口径）。"""
    steps = CORE.steps_for_frames(frames)
    assert steps is not None, "%d 帧不是合法网格窗口" % frames
    g = torch.Generator().manual_seed(seed)
    v = torch.randn(1, 24, steps, 28, 48, generator=g)
    a = torch.randn(1, 32, 2, steps, generator=g)
    return {"samples": NT.NestedTensor([v, a])}


def frames_seq(n, seed=0):
    """构造一段 [n,H,W,3] 的帧序列（每帧独立噪声，彼此可区分）。"""
    g = torch.Generator().manual_seed(seed)
    return torch.rand(n, 24, 32, 3, generator=g)


# ============================================================================
print("=" * 78)
print("E1｜多尺度历史 build_history_refs")
print("=" * 78)

hist = [make_latent(192, seed=i + 1) for i in range(3)]

check("E1.1 depth=0（默认）⇒ 空列表，不引入任何 ref",
      CORE.build_history_refs(hist, 22, 0, 4) == [])
check("E1.2 空历史 ⇒ 空列表（不 raise，首段就是这样）",
      CORE.build_history_refs([], 22, 2, 4) == [])
_e1 = CORE.build_history_refs(hist, 22, 2, 4)
check("E1.3 抽稀真的抽：两级步数不同（22帧→5帧）",
      len(_e1) == 2 and _e1[0]["latent_t"] > _e1[1]["latent_t"],
      "latent_t = %s" % [r["latent_t"] for r in _e1])
check("E1.4 每级都是合法 refs 块（kind/latent_t/latent_h/latent_w 齐备）",
      all(r["kind"] == "video" and r["latent_t"] > 0 and r["ref_audio_t"] == 0
          and r["audio_latent"] is None for r in _e1),
      str([(r["kind"], r["latent_t"]) for r in _e1]))
check("E1.5 抽稀单调：越远的级越短",
      [r["latent_t"] for r in CORE.build_history_refs(hist, 90, 3, 4)]
      == sorted([r["latent_t"] for r in CORE.build_history_refs(hist, 90, 3, 4)],
                reverse=True),
      str([r["latent_t"] for r in CORE.build_history_refs(hist, 90, 3, 4)]))
check("E1.6 第 0 级 = 基准帧数原档（不抽稀）",
      CORE.build_history_refs(hist, 22, 1, 4)[0]["latent_t"]
      == CORE.steps_for_frames(22))
expect_raise("E1.7 基准帧数不在合法网格 ⇒ raise（不夹取到邻近档）",
             lambda: CORE.build_history_refs(hist, 23, 1, 4), "不在合法网格")
expect_raise("E1.8 depth 超过历史长度 ⇒ raise（不静默截断）",
             lambda: CORE.build_history_refs(hist, 22, 4, 4), "只给了")
expect_raise("E1.9 stride<1 ⇒ raise",
             lambda: CORE.build_history_refs(hist, 22, 2, 0), "步长必须")
expect_raise("E1.10 抽不出层级（5 帧起抽 ⇒ 两级同为 2 步）⇒ raise，不造假多尺度",
             lambda: CORE.build_history_refs(hist, 5, 2, 4), "分不出层级")
expect_raise("E1.11 stride=1 ⇒ 每级等长 ⇒ raise（等价于重复同一个锚）",
             lambda: CORE.build_history_refs(hist, 22, 2, 1), "分不出层级")

# ============================================================================
print()
print("=" * 78)
print("E2｜SDEdit 式软钉入 sdeedit_noise")
print("=" * 78)

_blk = frames_seq(6, seed=7)
_out0 = CORE.sdeedit_noise(_blk, 0.0)
check("E2.1 σ=0（默认）⇒ **逐位返回同一张量**（不是副本）",
      _out0 is _blk, "is 同一对象：%s" % (_out0 is _blk))

_g = torch.Generator().manual_seed(11)
_out = CORE.sdeedit_noise(_blk, 0.5, generator=_g)
check("E2.2 σ>0 ⇒ 形状/ dtype/ 设备不变",
      _out.shape == _blk.shape and _out.dtype == _blk.dtype)
_delta = float((_out - _blk).abs().mean())
_std = float(_blk.std())
check("E2.3 注入量 ≈ σ×块自身 std（量纲无关，docstring 的承诺）",
      0.35 * _std < _delta < 0.65 * _std,
      "注入 %.4f vs σ·std=%.4f" % (_delta, 0.5 * _std))

_out_same = CORE.sdeedit_noise(_blk, 0.5, generator=torch.Generator().manual_seed(11))
check("E2.4 同 seed ⇒ 可复现（实验要能重跑）",
      torch.equal(_out, _out_same))
_out_other = CORE.sdeedit_noise(_blk, 0.5, generator=torch.Generator().manual_seed(12))
check("E2.5 不同 seed ⇒ 不同实现（否则 σ 是确定性偏移而非噪声）",
      not torch.equal(_out, _out_other))
expect_raise("E2.6 σ<0 ⇒ raise（不加噪不是负向旋钮）",
             lambda: CORE.sdeedit_noise(_blk, -0.1), "不得为负")

# 宿主契约核对（2026-09-20 读 I:/ComfyUI/comfy/ldm/minimax/model.py）：
#   _cond_video_rows 对每个 cond latent 自己做 aug*r+(1-aug)*noise（默认 aug=0.999）
#   ⇒ 本函数注入的噪声**几乎无衰减地穿过**宿主那一层 ⇒ 旋钮真实有效。
#   ⚠ 但 cond 行的时间戳标签恒为 max(t_v, 0.999)（model.py:635-637）
#   ⇒ 「噪声进了、标签仍是干净」≠ 论文 SDEdit 的「噪声量与 t 匹配」。
#   这条差异必须在文档里写清（README 实验节已写），否则会按论文预期解读结果。
print("    ℹ 宿主契约：cond 行过 model.py:_cond_video_rows 时 aug=0.999 "
      "⇒ 注入噪声基本不被冲淡；但其 t 标签恒为 0.999 ⇒ 与论文 SDEdit "
      "「噪声量↔时间戳匹配」不同，解读时别套论文结论。")

# ============================================================================
print()
print("=" * 78)
print("E3｜DTW 残留量 dtw_residual / head_repeat_dtw")
print("=" * 78)

# 构造：钉住区 4 帧 + 窗 6 帧，其中窗的前 r 帧是钉住区末段的复现
_pin = frames_seq(4, seed=21)


def _window(repeat):
    """窗 = 钉住区末 repeat 帧的复现 + (6-repeat) 帧新内容。"""
    new = frames_seq(6 - repeat, seed=99)
    return torch.cat([_pin[4 - repeat:], new], dim=0) if repeat else new


# 全异源基准代价（同分辨率、不同 seed）——「复现」必须比它便宜
_COST_REF = CORE.dtw_residual(
    CORE._dtw_feat(frames_seq(6, seed=123)),
    CORE._dtw_feat(_pin))[0]
check("E3.0 基准自洽：全异源代价 > 0（否则下面的「低于基准」无意义）",
      _COST_REF > 0.0, "基准 %.4f" % _COST_REF)


_prev = None
_mono = True
for _r in (0, 1, 3):
    _w = _window(_r)
    _fa = CORE._dtw_feat(_w)
    _fb = CORE._dtw_feat(_pin)
    _cost, _ = CORE.dtw_residual(_fa, _fb)
    _rev, _ = CORE.dtw_residual(_fb, _fa)
    if _prev is not None and _cost > _prev + 1e-6:
        _mono = False
    _prev = _cost
    check("E3.%d 复现 %d 帧：代价合法、两方向一致"
          % (_r + 1, _r),
          0.0 <= _cost and abs(_cost - _rev) < 1e-9,
          "窗→钉住区 %.4f｜反向 %.4f｜全异源基准 %.4f"
          % (_cost, _rev, _COST_REF))
check("E3.3b 代价随复现帧数**单调不增**（复现越多越便宜）", _mono,
      "观察序列见上（r=0,1,3）")
check("E3.3c 复现 3 帧显著低于全异源基准（残留确实让路径变便宜）",
      CORE.dtw_residual(CORE._dtw_feat(_window(3)), CORE._dtw_feat(_pin))[0]
      < 0.7 * _COST_REF,
      "得到 %.4f vs 基准 %.4f" % (
          CORE.dtw_residual(CORE._dtw_feat(_window(3)), CORE._dtw_feat(_pin))[0],
          _COST_REF))

check("E3.4 全复现窗 ⇒ 代价≈0（对齐全走对角步）",
      CORE.dtw_residual(CORE._dtw_feat(_pin), CORE._dtw_feat(_pin))[0] < 1e-6)
check("E3.5 代价方向：同源序列代价 < 异源序列代价",
      CORE.dtw_residual(CORE._dtw_feat(_pin), CORE._dtw_feat(_pin))[1]
      < CORE.dtw_residual(CORE._dtw_feat(_pin),
                          CORE._dtw_feat(frames_seq(4, seed=77)))[1])

# 关键反证（2026-09-20）：旧版返回的 stay_b 计数由**长度差**主导，
#   a=6,b=4 全异源时恒等于 2 ⇒ 已删除。现在验证代价量**不**随长度差假性抬升。
_c_same = CORE.dtw_residual(CORE._dtw_feat(_pin), CORE._dtw_feat(_pin))[0]
_c_ext = CORE.dtw_residual(
    CORE._dtw_feat(torch.cat([_pin, frames_seq(3, seed=5)], dim=0)),
    CORE._dtw_feat(_pin))[0]
check("E3.5b 代价量反证：同源自比≈0，且追加新内容后代价显著上升（不是长度差假象）",
      _c_same < 1e-6 and _c_ext > 0.1,
      "同源 %.6f｜a=pin+3新帧 %.4f" % (_c_same, _c_ext))

expect_raise("E3.6 一维输入 ⇒ raise",
             lambda: CORE.dtw_residual(torch.zeros(5), torch.zeros(5)), "二维")
expect_raise("E3.7 特征维度不一致 ⇒ raise",
             lambda: CORE.dtw_residual(torch.zeros(3, 4), torch.zeros(3, 5)), "维度不一致")
expect_raise("E3.8 序列超长 ⇒ raise（O(n²)，实验档只在≤64 帧小窗用）",
             lambda: CORE.dtw_residual(torch.zeros(65, 2), torch.zeros(3, 2)), "序列过长")
check("E3.9 空序列 ⇒ (0.0, 0.0) 不炸",
      CORE.dtw_residual(torch.zeros(0, 4), torch.zeros(3, 4)) == (0.0, 0.0))

# 回溯边界（2026-09-20 修的静默 bug）：单边归零时路径长度必须仍然算对
_b_a = CORE._dtw_feat(frames_seq(8, seed=31))
_b_b = CORE._dtw_feat(frames_seq(2, seed=32))
_c_ab, _ = CORE.dtw_residual(_b_a, _b_b)
check("E3.10 单边归零（8 vs 2）不越界：代价落在合法区间且是有限值",
      0.0 <= _c_ab and _c_ab == _c_ab, "得到 %.4f" % _c_ab)
_c_ba, _ = CORE.dtw_residual(_b_b, _b_a)
check("E3.11 反向（2 vs 8）：代价同为有限值且不因边界读错 mv 而异常",
      0.0 <= _c_ba and _c_ba == _c_ba, "得到 %.4f" % _c_ba)

# —— head_repeat_dtw：接 IMAGE 的只读观测层 ——
_img = torch.cat([_pin, _window(3)], dim=0)
_d1 = CORE.head_repeat_dtw(_img, 4)
check("E3.12 head_repeat_dtw 报出代价对 + 帧数 + 特征形状",
      _d1 and _d1["align_cost"] > 0.0 and _d1["reverse_cost"] >= 0.0
      and _d1["frames"] == [6, 4] and len(_d1["feat"]) == 2,
      str(_d1))
check("E3.13 pin<1 ⇒ 空 dict（调用方跳过该行，不 raise）",
      CORE.head_repeat_dtw(_img, 0) == {})
check("E3.14 窗太短（不足 REPEAT_MIN_RUN）⇒ 空 dict",
      CORE.head_repeat_dtw(torch.cat([_pin, _pin[:1]], dim=0), 4) == {})
check("E3.15 只读：不改输入张量",
      (lambda _x: (CORE.head_repeat_dtw(_x, 4), torch.equal(_x, _img))[1])(_img.clone()))

# ============================================================================
print()
print("=" * 78)
print("E4｜漂移曲线 segment_appearance_stats / drift_curve")
print("=" * 78)

torch.manual_seed(41)
_segs = []
for _i in range(4):
    _s = torch.rand(80, 24, 32, 3, generator=torch.Generator().manual_seed(100 + _i)) \
        * 0.2 + 0.4 + 0.02 * _i              # mean 逐段 +0.02 的线性漂移
    _segs.append(_s)
_stats = [CORE.segment_appearance_stats(s) for s in _segs]
check("E4.1 单段自报三元组（mean/std/sharpness，只取体区）",
      len(_stats) == 4 and all(len(t) == 3 for t in _stats),
      str([(round(m, 4), round(s, 4), round(q, 5)) for m, s, q in _stats]))
_dc = CORE.drift_curve(_stats)
check("E4.2 mean_shift 线性（构造的就是 +0.02/段）",
      all(abs(v - 0.02 * i) < 1e-3 for i, v in enumerate(_dc["mean_shift"])),
      str([round(v, 5) for v in _dc["mean_shift"]]))
check("E4.3 slope ≈ 0.02（最小二乘吃出构造斜率）",
      abs(_dc["slope"] - 0.02) < 1e-3, "得到 %.5f" % _dc["slope"])
check("E4.4 三个斜率字段齐备（n≥2 全给）",
      {"slope", "std_slope", "sharp_slope"} <= set(_dc)
      and all(k in _dc for k in ("slope", "std_slope", "sharp_slope")),
      "std_slope=%.5f sharp_slope=%.5f" % (_dc["std_slope"], _dc["sharp_slope"]))
check("E4.5 ratio 近 1（std/sharpness 构造上不动，0.2% 是均值估计量内的抖动）"
      " ⇒ 比值斜率近 0",
      all(abs(v - 1.0) < 0.01 for v in _dc["std_ratio"])
      and abs(_dc["std_slope"]) < 1e-3 and abs(_dc["sharp_slope"]) < 1e-3,
      "std_ratio=%s｜std_slope=%.5f｜sharp_slope=%.5f"
      % ([round(v, 4) for v in _dc["std_ratio"]], _dc["std_slope"], _dc["sharp_slope"]))

_dc1 = CORE.drift_curve(_stats[:1])
check("E4.6 单段：三条斜率全 0（无从判断趋势，不伪造）",
      _dc1["slope"] == 0.0 and _dc1["std_slope"] == 0.0 and _dc1["sharp_slope"] == 0.0
      and len(_dc1["mean_shift"]) == 1,
      str({k: _dc1[k] for k in ("slope", "std_slope", "sharp_slope")}))
_de = CORE.drift_curve([])
check("E4.7 空输入：字段齐备且全零（下游取键不 KeyError）",
      _de["mean_shift"] == [] and _de["slope"] == 0.0
      and _de["std_slope"] == 0.0 and _de["sharp_slope"] == 0.0, str(_de))
check("E4.8 基准段可指定（ref_idx=2 ⇒ mean_shift[2]==0）",
      abs(CORE.drift_curve(_stats, ref_idx=2)["mean_shift"][2]) < 1e-12)
check("E4.9 ref_idx 越界 ⇒ 退回第 0 段（不 raise，观测层要稳）",
      abs(CORE.drift_curve(_stats, ref_idx=99)["mean_shift"][0]) < 1e-12)
expect_raise("E4.10 非 [N,H,W,C] ⇒ raise",
             lambda: CORE.segment_appearance_stats(torch.zeros(10)), "[N,H,W,C]")

# ============================================================================
print()
print("=" * 78)
print("E5｜床声去重复 bed_jitter_start")
print("=" * 78)

_SR = 32000
check("E5.1 jitter=0（默认）⇒ 与 0.5.0 主干的尾部窗完全一致",
      CORE.bed_jitter_start(96000, 32000, 3, 0.0, _SR) == 64000,
      "得到 %d" % CORE.bed_jitter_start(96000, 32000, 3, 0.0, _SR))
check("E5.2 stage_index=0 ⇒ 平移 0 ⇒ 同默认档起点",
      CORE.bed_jitter_start(96000, 32000, 0, 0.5, _SR) == 64000)
check("E5.3 平移量 = stage×jitter×sr",
      CORE.bed_jitter_start(96000, 32000, 3, 0.5, _SR) == 64000 - int(3 * 0.5 * _SR),
      "得到 %d（期望 %d）" % (CORE.bed_jitter_start(96000, 32000, 3, 0.5, _SR),
                              64000 - int(3 * 0.5 * _SR)))
_wrap = CORE.bed_jitter_start(70000, 32000, 4, 2.0, _SR)
check("E5.4 回绕（shift > 可平移空间）⇒ 仍落在合法区间",
      0 <= _wrap and _wrap + 32000 <= 70000, "start=%d" % _wrap)
check("E5.5 不同段号起点不同（治「各段头部 N 秒完全相同」）",
      len({CORE.bed_jitter_start(96000, 32000, s, 0.5, _SR) for s in range(4)}) == 4,
      str([CORE.bed_jitter_start(96000, 32000, s, 0.5, _SR) for s in range(4)]))
expect_raise("E5.6 床源不长于补丁 ⇒ raise（无可平移空间，不静默取头部）",
             lambda: CORE.bed_jitter_start(32000, 32000, 3, 0.5, _SR), "没有可平移")
expect_raise("E5.7 床长 ≤0 ⇒ raise",
             lambda: CORE.bed_jitter_start(96000, 0, 3, 0.5, _SR), "床声长度必须为正")

# 长度守恒（不变量）：穷举小规模组合，不允许任何越界起点
import itertools  # noqa: E402
_bad = []
for _tot, _n, _st, _j in itertools.product([32000, 32010, 70000, 96000],
                                           [8000, 16000, 32000], range(5),
                                           [0.0, 0.25, 0.5, 1.0, 2.0]):
    if _n > _tot:
        continue
    try:
        _s = CORE.bed_jitter_start(_tot, _n, _st, _j, _SR)
    except Exception:
        continue
    if not (0 <= _s and _s + _n <= _tot):
        _bad.append((_tot, _n, _st, _j, _s))
check("E5.8 不变量 0≤start 且 start+n≤total（穷举组合）", not _bad, str(_bad[:3]))

# ============================================================================
# ---- E5.9~E5.11：🔴 2026-09-21 真渲染阳性 —— 床窗**撞语音**与规避 ----
# 事故：expE5（jitter=1.2）把床窗挪到 @2.05s，**完整包住**床源段（stage 0）的台词
#   （该段台词覆盖 3.0–4.0s）⇒ patch 把整句台词搬进本段头部 ⇒ 再叠缝处 crossfade
#   ⇒ 听感「对白重叠」（成片 4.46–5.66s）。⚠ 产线默认（jitter=0、尾窗 @3.25s）**同样撞**。
# 覆盖范围：本条走 **audio_seam_patch 的完整调用路径** —— 此前只测 build_bed 内部，
#   2026-09-21 因此漏掉一个 NameError（日志行引用了 build_bed 的形参名 start_override）。
_BED_SR = 32000
# ⚠ 必须带非零底噪：全零波形会让帧 RMS 中位 = 0 ⇒ 判据短路返回 0（真实音频不会这样）
_g = torch.Generator().manual_seed(7)
_bed = torch.rand(1, 2, int(4.0 * _BED_SR), generator=_g) * 0.002
_bn = int(1.2 * _BED_SR)
_btot = int(_bed.shape[-1])
_bed[..., _btot - _bn + int(0.1 * _BED_SR): _btot - _bn + int(0.5 * _BED_SR)] = 0.5   # 尾窗内塞"台词"
_raw = torch.zeros(1, 2, int(3.5 * _BED_SR))
_raw[..., int(0.5 * _BED_SR): int(1.0 * _BED_SR)] = 0.3
_BED_D = {"waveform": _bed, "sample_rate": _BED_SR}
_RAW_D = {"waveform": _raw, "sample_rate": _BED_SR}

_vf_tail = CORE.bed_window_voiced_fraction(_bed, _btot - _bn, _bn)
check("E5.9 尾部窗撞语音时 voiced_fraction 超阈值（判据有效）",
      _vf_tail > CORE.AUDIO_SEAM_BED_VOICED_FRAC, "占比=%.2f" % _vf_tail)

_ok, _rep, _o = True, "", None
try:
    _o, _rep = CORE.audio_seam_patch(_RAW_D, _BED_D, 1.2, 0.0, 0.25, select="tail",
                                     target_audio=None, bed_jitter=0.0, stage_index=1)
except Exception as _e:
    _ok, _rep = False, "%s: %s" % (type(_e).__name__, _e)
check("E5.10 audio_seam_patch 尾部窗撞语音 ⇒ 不抛异常、长度守恒、日志含 🎙",
      _ok and int(_o["waveform"].shape[-1]) == int(_raw.shape[-1]) and "🎙" in _rep,
      (_rep.splitlines()[0][:120] if _rep else ""))

_lens = []
for _j in (0.5, 1.2):
    _o2, _r2 = CORE.audio_seam_patch(_RAW_D, _BED_D, 1.2, 0.0, 0.25, select="tail",
                                     target_audio=None, bed_jitter=_j, stage_index=1)
    _lens.append(int(_o2["waveform"].shape[-1]))
check("E5.11 jitter 撞语音后仍长度守恒（规避不改变时间轴）",
      _lens == [int(_raw.shape[-1])] * 2, str(_lens))

print()
print("=" * 78)
print("结果：通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("   -", f)
print("=" * 78)
if "pytest" not in sys.modules:
    sys.exit(1 if FAIL else 0)
