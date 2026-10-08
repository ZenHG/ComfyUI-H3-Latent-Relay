# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md

"""🧪 实验层单测（`relay_core/metrics.py` §「实验层」的断言集合）——零 GPU、零模型、秒级

跑法（在包目录下）：
    python tests/test_experimental.py

覆盖调研文档 ``RESEARCH_seam_frontier.md`` 里四个**尚未投产**的手段
（``E1``~``E5``，见 `relay_core/metrics.py` 的实验层注释）：

  E3 DTW 残留量   dtw_residual / head_repeat_dtw
  E4 漂移曲线     segment_appearance_stats / drift_curve

  （E1 多尺度历史 / E2 conditioning 参考噪声 / E5 床声去重复 已于 2026-09-22
    连同实现一起删除 —— 三者实测对靶无效：E1 跨段漂移不减反增（单变量 A/B，
    同 seed 同 latent，|s5−s3| 亮度 ×2.9）；E2 与硬锁互斥（把参考弄脏）；
    E5 三缝差 0.1–1.3 dB 无可听收益。结项证据见当日交接与 memory。）

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
            "       设环境变量 COMFYUI_PATH=<你的 ComfyUI 根目录> 后重试\n" % _COMFY)
    if "pytest" in sys.modules:
        import pytest
        pytest.skip("找不到 ComfyUI 根目录（试过：%s）；设 COMFYUI_PATH 后重试" % _COMFY,
                    allow_module_level=True)
    sys.stderr.write(_MSG)
    raise SystemExit(2)
sys.path.insert(0, _COMFY)
sys.path.insert(0, _KIT_DIR)

import comfy.nested_tensor as NT

import relay_core as CORE

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
_legal = True
for _r in (0, 1, 3):
    _w = _window(_r)
    _cost, _ = CORE.dtw_residual(CORE._dtw_feat(_w), CORE._dtw_feat(_pin))
    if not (0.0 <= _cost and _cost == _cost):      # 合法 + 有限（NaN 自查）
        _legal = False
    if _prev is not None and _cost > _prev + 1e-6:
        _mono = False
    _prev = _cost
check("E3.1 复现 0/1/3 帧：代价合法且有限（观测层只读，不进裁量）", _legal,
      "r=3 末值 %.4f｜全异源基准 %.4f" % (_prev, _COST_REF))
# 🔴 2026-09-21 性质锁：dtw_residual 对调换参数**不变**（局部代价 |a−b| 对称 ⇒ DP 总代价
#   对称 + 回溯 tie-break 镜像 ⇒ 步数相等、平均代价恒等）。原先在循环里逐档断言
#   「两方向一致」（旧 E3.1–3），那是把**数学必然**当待测性质、还白算一半 DTW。
#   ⇒ 改成一次显式性质锁；head_repeat_dtw 已删掉那次冗余的反向调用（E3.12b 守返回值）。
_fx, _fy = CORE._dtw_feat(_window(1)), CORE._dtw_feat(_pin)
_c_xy, _ = CORE.dtw_residual(_fx, _fy)
_c_yx, _ = CORE.dtw_residual(_fy, _fx)
check("E3.2 性质锁：dtw_residual 双向对称（(a,b) 与 (b,a) 代价恒等）",
      abs(_c_xy - _c_yx) < 1e-12,
      "a→b %.6f｜b→a %.6f" % (_c_xy, _c_yx))
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
check("E3.12 head_repeat_dtw 报出代价 + 帧数 + 特征形状",
      _d1 and _d1["align_cost"] > 0.0
      and _d1["frames"] == [6, 4] and len(_d1["feat"]) == 2,
      str(_d1))
check("E3.12b 返回值**不含** reverse_cost（对称 ⇒ 反向是纯冗余，2026-09-21 删）",
      "reverse_cost" not in _d1, str(sorted(_d1)))
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
print("0.6.28｜声锚尾窗吸附 anchor_window_start —— 盲取文件尾会把「停顿」钉成「已说过的声音」")
print("=" * 78)
print("  背景：`_voice_anchor_tail` 一直 `narrow(-1, total-take, take)`（**盲取文件尾**），")
print("        而 `auto_audio_ref_seconds` 的 `sec = (n1 - start) / _gps` 是**一直算到文件末尾**")
print("        ⇒ 素材末尾有停顿时**停顿留在窗里**。生产声库实测三段全部 `tail_clean=false`。")
print("  纪律：只在**真的更好时**才挪；判不出有声格就不动；**不 raise**（只修复 + 报告）。")
print()


def _anchor(spans, total_s, amp=0.06, quiet=0.0):
    """造音频 latent：`spans` 用**逐格交替幅度**（差分判据认得出），其余为 `quiet`。"""
    n = int(total_s * CORE.AUDIO_HZ)
    x = torch.full((1, 1, 2, n), float(quiet))
    p = torch.tensor([0.02, 0.06]) * (amp / 0.05)
    for t0, t1 in spans:
        i0, i1 = int(t0 * CORE.AUDIO_HZ), int(t1 * CORE.AUDIO_HZ)
        m = max(0, i1 - i0)
        if m:
            x[..., i0:i1] = p.repeat(m // 2 + 1)[:m].view(1, 1, 1, m)
    return x


_a_busy = _anchor([(0.0, 6.0)], 6.0)                       # 全程有声（正常素材）
_s0, _i0 = CORE.anchor_window_start(_a_busy, 37)           # 窗 = 0.925 s = 37 步
check("0.6.28 正常素材：窗里大部分是人声 ⇒ **不动**（逐位同旧版）",
      _i0["repaired"] is False and _s0 == 6 * 40 - 37 and _i0["frac_before"] > 0.9,
      "start=%d ｜ 有声中 %.2f" % (_s0, _i0["frac_before"]))

# 🔴 「窗里**已经过半**是人声」也要**不动** —— 这条专门守「不许顺手多挪」那个早退分支。
#    voice 止于 4.75 s ⇒ 默认窗 [163,200) 里 28/37 有声（≥ 0.5）⇒ 应当**不动**。
#    **去掉早退**（⑨ 注入）后：终点会挪到 4.75+0.10 ⇒ start 158 ⇒ 有声 33/37 ⇒ 真的会动
#    ⇒ 所以这条能判别（旧版测"全程有声"时，`start` 被 clamp 回默认 ⇒ 注入测不出 = 假门）。
_a_mix = _anchor([(0.0, 4.75)], 5.0)
_s_mix, _i_mix = CORE.anchor_window_start(_a_mix, 37)
check("0.6.28 窗里**已过半**是人声 ⇒ 不动（不许顺手多挪）",
      _i_mix["repaired"] is False and _s_mix == 5 * 40 - 37 and _i_mix["frac_before"] >= 0.5,
      "start=%d ｜ 有声中 %.2f" % (_s_mix, _i_mix["frac_before"]))

# 生产声库的形态：窗里**过半是停顿**（`tail_clean=false`：尾部 0.925 s 含停顿）
_a_pause = _anchor([(0.0, 4.4)], 5.0)
_s1, _i1 = CORE.anchor_window_start(_a_pause, 37)
check("0.6.28 窗里过半是停顿 ⇒ 往前挪，终点落在「最后一个有声格 + 0.10 s」",
      _i1["repaired"] is True and _s1 < 5 * 40 - 37 and _i1["voiced_frac"] > 0.8
      and abs((_s1 + 37) - (4.4 * 40 + int(round(0.10 * 40)))) <= 2,
      "start %d（旧 %d）｜ 有声中 %.2f→%.2f ｜ 终点 %d"
      % (_s1, 5 * 40 - 37, _i1["frac_before"], _i1["voiced_frac"], _s1 + 37))

_s2, _i2 = CORE.anchor_window_start(torch.zeros(1, 1, 2, 8 * 40), 37)
check("0.6.28 全静音 ⇒ **不动** + 把 `reason` 交出来（不静默、不猜）",
      _i2["repaired"] is False and bool(_i2["reason"]) and _s2 == 8 * 40 - 37,
      _i2["reason"])

_s3, _i3 = CORE.anchor_window_start(_a_busy, 6 * 40)
check("0.6.28 窗 = 整段 ⇒ 没有可挪的余地（不动）",
      _i3["repaired"] is False and _s3 == 0, "start=%d" % _s3)

_a_pair = _anchor([(0.0, 0.5), (4.0, 4.5)], 5.0)           # 头尾各一小段有声
_s4, _i4 = CORE.anchor_window_start(_a_pair, 40, min_frac=0.9)
check("0.6.28 往前挪**不会更好** ⇒ 保持原样 + 出声（不硬挪）",
      _i4["repaired"] is False and _s4 == 5 * 40 - 40 and "不会更好" in _i4["note"],
      _i4["note"])

_s5, _i5 = CORE.anchor_window_start(_a_pause, 37, sr=16000)
check("0.6.28 网格长 ≠ 1 步（sr 不是 `AUDIO_HZ`）⇒ 明说不适用并保持原样（不猜）",
      _i5["repaired"] is False and "不适用" in _i5["reason"], _i5["reason"])

# 端到端：`_voice_anchor_tail` 现在返回 **4 元组**，且修好的窗里真的有话音
_tail, _take, _raw, _win = CORE._voice_anchor_tail({"samples": _a_pause}, 22)
_old = _a_pause[..., 5 * 40 - 37:]
_tail_live = int((_tail.abs().amax(dim=(0, 1, 2)) > 0).sum())     # 逐**步**统计（不是逐通道）
_old_live = int((_old.abs().amax(dim=(0, 1, 2)) > 0).sum())
check("0.6.28 `_voice_anchor_tail` 返回 4 元组，info 带 `repaired`（旧口径会拿到停顿）",
      _take == 37 and isinstance(_win, dict) and _win["repaired"] is True,
      "take=%d repaired=%s" % (_take, _win.get("repaired")))
check("0.6.28 修好的窗：**有话音**（旧口径这里过半是停顿）",
      _tail_live >= 30 and _old_live < 20,
      "修后 %d/37 步有信号 ｜ 旧口径 %d/37 步（%.0f%% 是停顿）"
      % (_tail_live, _old_live, (37 - _old_live) * 100.0 / 37))

# 判据单一真相源：`auto_audio_ref_seconds` 与 `anchor_window_start` 共用 `_voiced_grids`
check("0.6.28 判据单一真相源：`_voiced_grids` 对同一素材给出同一组有声格（两处共用）",
      len(CORE._voiced_grids(_a_pause, CORE.AUDIO_HZ)[0]) == len(CORE._voiced_grids(_a_pause, 40)[0])
      and CORE._voiced_grids(torch.zeros(1, 1, 2, 400), 40)[5] != "",
      "有声格 %d ｜ 静音素材 reason=%s"
      % (len(CORE._voiced_grids(_a_pause, CORE.AUDIO_HZ)[0]),
         CORE._voiced_grids(torch.zeros(1, 1, 2, 400), 40)[5]))

# ============================================================================
# VA 适配层（0.6.28）：声锚门的**原因必须外泄到 notes** ——
# `nodes.py` 把每条 VA note 原样拼进节点 `report` ⇒ 少了这层，用户只会在
# 「接了声锚、还写了台账」时看到 VA **静默不生效**，而无从察觉。
# 🔴 为什么必须在这一层测：`voice_accum._selftest` 只测 `anchor_gate`（纯函数），
#    测不到「适配层有没有把这条 note 传出去」——那是真正的断裂点。
import json as _json
import shutil as _shutil
import tempfile as _tempfile

from exp.voice_accum import h3_adapter as VAAD

_vdir = _tempfile.mkdtemp(prefix="h3relay_va_")
try:
    def _va_path(_run, _k):
        return os.path.join(_vdir, "stage_%05d.safetensors" % _k)

    def _va_load(_p):
        return {"audio": torch.zeros(1, 1, 2, 400)}

    def _va_audio(_lat):
        return _lat["audio"]

    with open(os.path.join(_vdir, "_va.json"), "w", encoding="utf-8") as _fh:
        _json.dump({"stages": {"1": ["A"], "2": ["A"]}}, _fh)

    _a0, _n0 = VAAD.build_audio("r", 2, stage_path_fn=_va_path, load_fn=_va_load,
                                audio_fn=_va_audio, has_anchor=False)
    _a1, _n1 = VAAD.build_audio("r", 2, stage_path_fn=_va_path, load_fn=_va_load,
                                audio_fn=_va_audio, has_anchor=True)
    # ⚠️ 别用 "跳过" 当判据：段文件缺失那条 note 里也有「跳过」二字（本轮自己踩到一次假红）
    #    ⇒ 只认声锚门那条 note 独有的字样。
    check("0.6.28 VA 适配层：**无声锚** ⇒ 声锚门不涉及，notes 里**不许**出现声锚门提示（不制造噪音）",
          not any(("声锚已接" in _n) or ("with_anchor" in _n) for _n in _n0),
          " ｜ ".join(_n0))
    check("0.6.28 VA 适配层：接了声锚 + 默认 ⇒ 跳过，且**原因外泄到 notes**（= report 看得到）",
          _a1 is None and any("跳过" in _n and "with_anchor" in _n for _n in _n1),
          " ｜ ".join(_n1))

    with open(os.path.join(_vdir, "_va.json"), "w", encoding="utf-8") as _fh:
        _json.dump({"config": {"with_anchor": True}, "stages": {"1": ["A"], "2": ["A"]}}, _fh)
    _a2, _n2 = VAAD.build_audio("r", 2, stage_path_fn=_va_path, load_fn=_va_load,
                                audio_fn=_va_audio, has_anchor=True)
    check("0.6.28 VA 适配层：`with_anchor=true` ⇒ **放行**（notes 写明叠加未实测）",
          any("叠加" in _n for _n in _n2), " ｜ ".join(_n2))
finally:
    try:
        _shutil.rmtree(_vdir)
    except OSError:
        pass

# 🔴 契约：节点侧必须把 VA 的 notes 拼进 `report`（否则上面三条白测 —— 用户还是看不到）
with open(os.path.join(_KIT_DIR, "nodes.py"), encoding="utf-8") as _nf:
    _nsrc = _nf.read()
check("0.6.28 VA 契约：`nodes.py` 把 VA 的 notes 拼进节点 `report`（否则跳过原因只在宿主日志里）",
      "_va_notes" in _nsrc and "注记·VA" in _nsrc and "extra.append" in _nsrc, "")

# ============================================================================
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
