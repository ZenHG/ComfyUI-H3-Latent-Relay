# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
#
# ruff: noqa: F821  —— 本分片**不是独立模块**。
#   它由 `tests/test_relay_core.py` 用 `exec(compile(src, path, "exec"), globals())`
#   顺序执行：与拆分前的单文件脚本**同一命名空间、同一顺序**。
#   所以 `check` / `CORE` / `NT` / `make_latent`、以及**上游分片定义的夹具**都看得见，
#   但不是本文件定义的 ⇒ F821 必报。
#   真正的兜底是**执行**：这条脚本每次全量跑，任何未定义名会当场 NameError，不会静默。

# ============ 组13：模糊型沉降（v0.3.1）——帧差法盲区的高频能量补判 ============

# —— 极短交叉溶（2026-09-16 重做：两侧都是**连续运动序列**）——
# 旧实现把「上段末帧」复制 k 次去混合 → k>1 时内容冻结（重复帧）；本组专测"不冻结"。
_cf = torch.zeros(40, 4, 4, 3)
for _i in range(40):
    _cf[_i] = _i / 40.0          # 每帧不同 → 序列本身有"运动"
ok, _c0 = arity(NODES.H3RelayTrimAV, {"images": _cf, "trim_frames": 22, "fps": 24.0, "audio": None,
                                          "settle_frames": 0, "seam_ghost": 0})
ok, _c3 = arity(NODES.H3RelayTrimAV, {"images": _cf, "trim_frames": 22, "fps": 24.0, "audio": None,
                                          "settle_frames": 0, "seam_ghost": 3})
check("12.30 交叉溶 k=3：帧数守恒（40→18）+ 权重按 1/4 递进（首帧 = 3/4·A₃ + 1/4·B₁）",
      int(_c3[0].shape[0]) == 18
      and torch.allclose(_c3[0][0], 0.75 * _cf[21] + 0.25 * _cf[22], atol=1e-6)
      and torch.allclose(_c3[0][2], 0.25 * _cf[19] + 0.75 * _cf[24], atol=1e-6),
      "首帧 %.4f 期望 %.4f" % (float(_c3[0][0][0, 0, 0]), float((0.75 * _cf[21] + 0.25 * _cf[22])[0, 0, 0])))
check("12.31 交叉溶 k=3：**不冻结**（前三帧互不相同 —— 旧实现会把上段末帧复制 3 次）",
      not torch.allclose(_c3[0][0], _c3[0][1]) and not torch.allclose(_c3[0][1], _c3[0][2]))
check("12.32 交叉溶 k=3：作用区外（第 4 帧起）逐位不动",
      torch.equal(_c3[0][3:], _cf[25:]))
check("12.33 交叉溶 k=1：退化为对半单帧（与旧 seam_ghost 行为一致，向后兼容）",
      torch.allclose(_c3[0][0], _c3[0][0]) and torch.equal(_c0[0], _cf[22:]))

# —— 后处理层补全（P3/P5/P6/P7，2026-09-16）——
# 合成素材：段头「糊 + 偏暖 + 偏暗」，段体「锐 + 中性 + 较亮」。
_pg = torch.Generator().manual_seed(23)
_ph, _pw = 48, 48
_pbase = (0.40 + 0.18 * torch.sin(6.283 * torch.arange(_pw).float().view(1, -1) / _pw)).expand(_ph, _pw)
_pbody = torch.stack([_pbase * 1.05, _pbase * 1.00, _pbase * 0.95], -1) + 0.06 * torch.randn(_ph, _pw, 3, generator=_pg)
_phead = torch.stack([_pbase * 1.15, _pbase * 0.95, _pbase * 0.80], -1)
_ppost = torch.cat([_phead.unsqueeze(0).expand(12, -1, -1, -1), _pbody.unsqueeze(0).expand(52, -1, -1, -1)], 0)


def _post_common(fn, name, kw):
    _o0 = fn(_ppost, 12, 0.0, **kw)
    _o1 = fn(_ppost, 12, 1.0, **kw)
    check("12.4x %s：帧数守恒 + 参数 0 逐位不动" % name,
          int(_o1.shape[0]) == 64 and torch.equal(_o0, _ppost))
    check("12.4x %s：作用区外（段体）逐位不动" % name,
          torch.equal(_o1[40:], _ppost[40:]))
    return _o0, _o1


# 12.40 频域高频增强：**必须先有高频可增强** ⇒ 用例改为「对锐图先做高斯模糊」再造段头。
#   （原用例的段头是纯低频正弦，无高频可增强 → 测不出效果，属用例缺陷而非实现缺陷）
_dblur = CORE._box_blur_hwc(_pbody.unsqueeze(0), 9)[0]        # 锐图 → 糊图
_pblur = torch.cat([_dblur.unsqueeze(0).expand(12, -1, -1, -1), _pbody.unsqueeze(0).expand(52, -1, -1, -1)], 0)
_d0 = CORE.deconv_head_zone(_pblur, 12, 0.0)
_d1 = CORE.deconv_head_zone(_pblur, 12, 1.0)
check("12.40 频域高频增强：帧数守恒 + 参数 0 逐位不动",
      int(_d1.shape[0]) == 64 and torch.equal(_d0, _pblur))
check("12.40 频域高频增强：作用区外逐位不动", torch.equal(_d1[40:], _pblur[40:]))
check("12.40 频域高频增强：糊段头的锐度被抬起来（>1.2× 原值）",
      float(CORE._sharpness(_d1[:12]).median()) > 1.2 * float(CORE._sharpness(_pblur[:12]).median()),
      "%.6f → %.6f" % (float(CORE._sharpness(_pblur[:12]).median()), float(CORE._sharpness(_d1[:12]).median())))

# 12.41 段体高频迁移：段头锐度上升
_b0, _b1 = _post_common(CORE.borrow_detail_from_body, "段体高频迁移", {"blur": 9, "body_start": 40})
check("12.41 段体高频迁移：段头锐度上升",
      float(CORE._sharpness(_b1[:12]).median()) > 1.5 * float(CORE._sharpness(_ppost[:12]).median()),
      "%.6f → %.6f" % (float(CORE._sharpness(_ppost[:12]).median()), float(CORE._sharpness(_b1[:12]).median())))

# 12.42 直方图匹配：段头均值向段体靠拢
_h0, _h1 = _post_common(CORE.match_hist_head_to_body, "直方图匹配", {"body_start": 40})
_gap_before = abs(float(_ppost[:12].mean()) - float(_ppost[40:].mean()))
_gap_after = abs(float(_h1[:12].mean()) - float(_ppost[40:].mean()))
check("12.42 直方图匹配：段头均值向段体靠拢",
      _gap_after < _gap_before, "gap %.4f → %.4f" % (_gap_before, _gap_after))

# 12.43 白平衡校正：段头通道比例向段体靠拢
_w0, _w1 = _post_common(CORE.match_white_balance, "白平衡校正", {"body_start": 40})


def _ratio(t):
    m = t.mean(dim=(0, 1, 2))
    return m / m.mean()


_r_body = _ratio(_ppost[40:])
_e_before = float((_ratio(_ppost[:12]) - _r_body).abs().max())
_e_after = float((_ratio(_w1[:12]) - _r_body).abs().max())
check("12.43 白平衡校正：段头 R:G:B 比例向段体靠拢",
      _e_after < _e_before, "偏差 %.4f → %.4f" % (_e_before, _e_after))

# 12.44 分位统计的 numel 上限（2026-09-25 实测钉死）
#   torch.quantile 硬上限 = 2^24；段体/段头是**整帧 flatten** ⇒ 高分辨率必撞：
#   0.796MP(672x1184) 每通道只容 21.1 帧、1.03MP(768x1344) 只有 16.3 帧，
#   而 90 帧段的段体（robust_body 筛后 ~25 帧 = 19.9M）必然超限 ⇒ hist_match 一开就抛。
#   0.3MP 下测不出来（那里每通道 54.6 帧）—— 是**分辨率红利**，不是设计。
_qs_t = torch.linspace(0.0, 1.0, 16)
_small = torch.rand(500)
check("12.44 分位包装：未超限时与 torch.quantile **逐位一致**（不改既有行为）",
      torch.equal(CORE._quantile_capped(_small, _qs_t), torch.quantile(_small, _qs_t)))

_saved_qmax = CORE.QUANTILE_MAX_ELEMS
_saved_qsamp = CORE.QUANTILE_SAMPLE_ELEMS
try:
    CORE.QUANTILE_MAX_ELEMS = 20000                      # 人为压低 ⇒ 模拟高分辨率
    CORE.QUANTILE_SAMPLE_ELEMS = 20000                   # 采样目标同步压低（否则 step=1 不采样）
    _big = torch.rand(200000)
    _capped = CORE._quantile_capped(_big, _qs_t, "夹具")   # 超限 ⇒ 走子采样，不该抛
    _exact = torch.quantile(_big, _qs_t)
    _dmax = float((_capped - _exact).abs().max())
    check("12.45 分位包装：超限时**自动子采样、不抛**（原版在真尺寸下必抛）",
          int(_capped.numel()) == int(_qs_t.numel()), "numel=%d" % int(_capped.numel()))
    # 子采样 2e4 点估计分位，标准误 ~sqrt(0.25/2e4)=3.5e-3；3σ ≈ 0.011 ⇒ 判据取 0.03
    check("12.46 分位包装：子采样结果贴近精确分位（max|d| < 0.03，理论量级内）",
          _dmax < 0.03, "max|d|=%.4f（采样 2e4 点，理论 3σ≈0.011）" % _dmax)
    # 12.47 🔴 关键性质：**采样点数与输入规模脱钩** ⇒ 耗时可控、分辨率无关。
    #   这是 2.0MP+ 场景不"卡住"的依据（采到刚好 2^24 会单段 18 s，见 relay_core 注释）。
    #   （编号 2026-09-25 与下面那条「端到端」对调 ⇒ 文件内编号按出现顺序递增。）
    _pts = []
    for _n in (200000, 2000000, 20000000):
        _t = torch.rand(_n)
        _pts.append(len(_t[::(-(-_n // CORE.QUANTILE_SAMPLE_ELEMS))]))
    check("12.47 分位包装：采样点数与输入规模**脱钩**（分辨率无关 ⇒ 2MP/4MP 耗时同级）",
          len(set(_pts)) == 1 and _pts[0] == CORE.QUANTILE_SAMPLE_ELEMS,
          "2e5/2e6/2e7 点输入 → 采样 %s" % _pts)
finally:
    CORE.QUANTILE_MAX_ELEMS = _saved_qmax
    CORE.QUANTILE_SAMPLE_ELEMS = _saved_qsamp

# 12.48 端到端：直方图匹配在「人为压到超限」时仍能出结果（真尺寸下原版必抛）
try:
    CORE.QUANTILE_MAX_ELEMS = 5000
    CORE.QUANTILE_SAMPLE_ELEMS = 5000
    _hs = torch.rand(64, 24, 24, 3)
    _hs[40:] += 0.15                                     # 段体亮一档
    _ho = CORE.match_hist_head_to_body(_hs, 12, 1.0, 40)
    check("12.48 直方图匹配：人为压到超限时正常出结果、帧数守恒",
          tuple(_ho.shape) == tuple(_hs.shape), str(tuple(_ho.shape)))
finally:
    CORE.QUANTILE_MAX_ELEMS = _saved_qmax
    CORE.QUANTILE_SAMPLE_ELEMS = _saved_qsamp

# —— 低频残差传递（2026-09-16，借鉴 Director 的段间引导低频对齐）——
# 合成"两段不同亮度"的序列：上段暗、下段亮（或反之），且**各带高频噪声**。
# 要钉住的核心性质：**缝点色档被拉近，但高频（锐度）不掉** ——
# 这正是它与「全 RGB 混合」的分水岭（后者实测把作用区锐度砍掉 49%）。
_g = torch.Generator().manual_seed(11)
_lf_pre = 0.80 + 0.04 * torch.rand(22, 8, 8, 3, generator=_g)
_lf_post = 0.20 + 0.04 * torch.rand(40, 8, 8, 3, generator=_g)
_lf = torch.cat([_lf_pre, _lf_post], dim=0)
ok, _l0 = arity(NODES.H3RelayTrimAV, {"images": _lf, "trim_frames": 22, "fps": 24.0, "audio": None,
                                          "settle_frames": 0, "lowfreq_pull": 0.0})
ok, _l1 = arity(NODES.H3RelayTrimAV, {"images": _lf, "trim_frames": 22, "fps": 24.0, "audio": None,
                                          "settle_frames": 0, "lowfreq_pull": 1.0,
                                          "lowfreq_frames": 12, "lowfreq_blur": 9})
_gap0 = abs(float(_l0[0][0].mean().item()) - float(_lf[21].mean().item()))
_gap1 = abs(float(_l1[0][0].mean().item()) - float(_lf[21].mean().item()))
check("12.34 低频残差：帧数守恒（62→40）+ w=0 时逐位不动（默认关 = 旧行为）",
      int(_l1[0].shape[0]) == 40 and torch.equal(_l0[0], _lf[22:]))
check("12.35 低频残差：缝点色档被拉向上段末帧（|Δ| 显著下降）",
      _gap1 < _gap0 * 0.5, "gap %.4f → %.4f" % (_gap0, _gap1))
check("12.36 低频残差：**高频（锐度）基本不掉** —— 区别于全 RGB 混合（后者砍 49%）",
      float(CORE._sharpness(_l1[0][:12]).median().item())
      > 0.85 * float(CORE._sharpness(_l0[0][:12]).median().item()),
      "锐度 %.6f → %.6f" % (float(CORE._sharpness(_l0[0][:12]).median().item()),
                            float(CORE._sharpness(_l1[0][:12]).median().item())))

# —— 糊区锐化（画质域修复，2026-09-16 本仓作者 定方向）——
# 动机：settle_frames 默认改 0（不裁沉降）后，成片段头保留几帧「重绘糊」；
#   裁它 → 跳帧（裁 16 帧跳 0.055）；不裁 → 留糊。
#   **第三条路 = 画质域修**：不裁、不动时间轴，只提升糊区高频 → 从原理上不可能引入跳帧。
# ⚠ 必须用 trim_frames=22 触发裁切分支：trim_frames=0 是「首段/独立段」，走早返回、不裁也不锐化。
_gimg = torch.rand(60, 8, 8, 3, generator=torch.Generator().manual_seed(3))
ok, _o0 = arity(NODES.H3RelayTrimAV, {"images": _gimg, "trim_frames": 22, "fps": 24.0, "audio": None,
                                          "settle_frames": 0, "settle_sharpen": 0.0})
ok, _o8 = arity(NODES.H3RelayTrimAV, {"images": _gimg, "trim_frames": 22, "fps": 24.0, "audio": None,
                                          "settle_frames": 0, "settle_sharpen": 0.8,
                                          "settle_sharpen_frames": 12})
check("12.27 糊区锐化：帧数守恒（60→38，只裁钉住区）+ amount=0 时逐位不动",
      int(_o0[0].shape[0]) == 38 and int(_o8[0].shape[0]) == 38
      and torch.equal(_o0[0], _gimg[22:]))
check("12.28 糊区锐化：开头帧高频上升、作用范围外（输出第 20 帧）逐位不动",
      float(CORE._sharpness(_o8[0][:1]).item()) > float(CORE._sharpness(_gimg[22:23]).item())
      and torch.allclose(_o8[0][20], _gimg[42], atol=1e-6),
      "锐度 %.6f → %.6f" % (float(CORE._sharpness(_gimg[22:23]).item()),
                            float(CORE._sharpness(_o8[0][:1]).item())))
check("12.29 糊区锐化：衰减正确（输出第 11 帧 = 作用区末帧，权重 0 → 与原帧重合）",
      torch.allclose(_o8[0][11], _gimg[33], atol=1e-6))

def blur_seg(n=40, pin=22, blur=(22, 28), amp=20.0, dev=15.0, seed=7):
    """模糊型沉降合成。钉住区 = 纹理A（复现，帧差中等）；[blur0,blur1) = 重绘发虚
    （常数帧：锐度≈0、帧差≈0）；其后 = 纹理B（新内容，均值同、振幅略小）。
    边界帧差刻意 < 4×基线 → 帧差法必然盲，只有锐度法能救。"""
    g = torch.Generator().manual_seed(seed)
    texA = 100.0 + torch.rand(8, 8, 3, generator=g) * amp
    texB = 100.0 + amp / 2 + (torch.rand(8, 8, 3, generator=g) - 0.5) * 2 * dev
    base_mean = float(texA.mean())
    im = torch.zeros(n, 8, 8, 3)
    for i in range(n):
        if i < pin:
            im[i] = texA + (i % 3) * 3.0
        elif blur[0] <= i < blur[1]:
            im[i] = base_mean
        else:
            im[i] = texB + (i % 3) * 3.0
    return im


sE, jE, bE = CORE.detect_settle(blur_seg(), 22)
check("13.1 模糊沉降（塌陷 f24-29、恢复 f30）→ 锐度法量出 settle≈8-9",
      6 <= sE <= CORE.MAX_SETTLE, "settle=%d dip=%.1f ref=%.1f" % (sE, jE, bE))
check("13.2 模糊路径的返回值语义：val=塌陷谷底（<0.35×基准）、base=复现区锐度基准",
      jE < CORE.BLUR_COLLAPSE_RATIO * bE and bE > 0.0,
      "dip=%.1f ref=%.1f" % (jE, bE))
sF, _, _ = CORE.detect_settle(blur_seg(blur=(22, 40)), 22)   # 糊过窗：22+18 > 22+12
check("13.3 塌陷越过检测窗且窗内无恢复 → 0（宁少勿多）", sF == 0, "settle=%d" % sF)
sG, _, _ = CORE.detect_settle(blur_seg(blur=(22, 22)), 22)   # 无模糊区
check("13.4 全程带纹理、无塌陷 → 0（不误伤）", sG == 0, "settle=%d" % sG)
sH, _, _ = CORE.detect_settle(blur_seg(blur=(22, 34)), 22)   # 糊 22..33（12 帧贴满窗）、34 起新内容
check("13.5 长塌陷(12帧)窗内可见恢复 → settle=12（贴满上限）",
      sH == 12, "settle=%d" % sH)
sI, _, _ = CORE.detect_settle(blur_seg(seed=11), 22)         # 换 seed 回归
check("13.6 换随机种子结论稳定（6 ≤ settle ≤ 12）",
      6 <= sI <= CORE.MAX_SETTLE, "settle=%d" % sI)

# —— v0.3.2 双基准：复现区自身发软时，窗外新内容区才是真基准 ——

def blur_seg_mild(n=40, pin=22, blur=(22, 26), seed=9):
    """HD-mild 动机案例：复现区本身半软（振幅 20），塌陷区 0.3-0.5×源
    （对软复现基准不可见），新内容全锐（振幅 60）。
    0.3.1 单基准（复现区）→ dip>0.35×ref 返回 0；0.3.2 双基准 → 量出 settle。"""
    g = torch.Generator().manual_seed(seed)
    texA = 100.0 + torch.rand(8, 8, 3, generator=g) * 20.0
    texB = 110.0 + (torch.rand(8, 8, 3, generator=g) - 0.5) * 60.0
    meanA = float(texA.mean())
    im = torch.zeros(n, 8, 8, 3)
    for i in range(n):
        if i < pin:
            im[i] = texA + (i % 2) * 6.0
        elif blur[0] <= i < blur[1]:
            im[i] = 0.5 * texA + 0.5 * meanA + (i % 3) * 1.5
        else:
            im[i] = texB + (i % 3) * 3.0
    return im


sM, jM, bM = CORE.detect_settle(blur_seg_mild(), 22)
check("13.7 HD-mild：对软复现基准不可见 → 双基准量出 settle=4",
      sM == 4, "settle=%d dip=%.1f ref=%.1f" % (sM, jM, bM))
_imM = blur_seg_mild()
_shM = CORE._sharpness(_imM[:36])
_faM = CORE._sharpness(_imM[36:76])
_expM = max(float(_shM[:22].median()), float(_faM.median()))
check("13.8 双基准语义：ref = max(复现区锐度, 窗外新内容区锐度)",
      abs(bM - _expM) < 1e-4, "ref=%.2f expect=%.2f" % (bM, _expM))
# —— v0.3.2 量纲不变性：0-1 产线数据与 0-255 测试数据必须同一结论 ——

check("13.9 量纲不变（帧差路）：0-255 的 settle=1 在 0-1 数据上同为 1（BASELINE_FLOOR 量纲 bug 回归）",
      CORE.detect_settle(seam_seg(73, 23) / 255.0, 22)[0] == 1,
      "settle=%d" % CORE.detect_settle(seam_seg(73, 23) / 255.0, 22)[0])
check("13.10 量纲不变（锐度路）：0-1 的 HD-mild 同样量出 settle=4",
      CORE.detect_settle(blur_seg_mild() / 255.0, 22)[0] == 4,
      "settle=%d" % CORE.detect_settle(blur_seg_mild() / 255.0, 22)[0])

# —— v0.3.3 段体自参考 + 假跳否决 ——

def soft_uniform_seg(n=40, pin=22, seed=5):
    """全段一致偏软（4 步低清风格）：头与体都是弱纹理，整体量级低但无塌陷差。"""
    g = torch.Generator().manual_seed(seed)
    im = torch.zeros(n, 8, 8, 3)
    for i in range(n):
        base = 100.0 + torch.rand(8, 8, 3, generator=g) * (6.0 if i < pin else 9.0)
        im[i] = base + (i % 3) * 1.0
    return im


sS, _, _ = CORE.detect_settle(soft_uniform_seg(), 22)
check("13.11 全段一致偏软（整体量级低、头体无差）→ 0（量纲无关的决策）",
      sS == 0, "settle=%d" % sS)

def ramp_blur_seg(n=40, pin=22, seed=3):
    """渐进模糊：f22-25 纹理振幅按 t=0.4/0.55/0.7/0.85 递减到深塌陷，f26 起新内容。"""
    g = torch.Generator().manual_seed(seed)
    texA = 100.0 + torch.rand(8, 8, 3, generator=g) * 20.0
    texB = 110.0 + (torch.rand(8, 8, 3, generator=g) - 0.5) * 30.0
    meanA = float(texA.mean())
    im = torch.zeros(n, 8, 8, 3)
    ts = [0.4, 0.55, 0.7, 0.85]
    for i in range(n):
        if i < pin:
            im[i] = texA + (i % 3) * 3.0
        elif i < pin + 4:
            tt = ts[i - pin]
            im[i] = (1 - tt) * texA + tt * meanA
        else:
            im[i] = texB + (i % 3) * 3.0
    return im


sR, vR, _ = CORE.detect_settle(ramp_blur_seg(), 22)
check("13.12 渐进模糊（缓坡到底才过深线）→ settle=4（覆盖整个塌陷前缀）",
      sR == 4, "settle=%d" % sR)

def fake_jump_seg(n=40, pin=22, seed=4):
    """假跳：f22 既是深塌陷又是一次大跳（亮常帧），f23 起新内容——
    跳点必须被验身否决，由锐度路定界（返回值 = 塌陷谷底而非跳变帧差）。"""
    g = torch.Generator().manual_seed(seed)
    texA = 100.0 + torch.rand(8, 8, 3, generator=g) * 20.0
    texB = 110.0 + (torch.rand(8, 8, 3, generator=g) - 0.5) * 30.0
    im = torch.zeros(n, 8, 8, 3)
    for i in range(n):
        if i < pin:
            im[i] = texA + (i % 3) * 3.0
        elif i == pin:
            im[i] = 250.0
        else:
            im[i] = texB + (i % 3) * 3.0
    return im


sF, vF, bF = CORE.detect_settle(fake_jump_seg(), 22)
check("13.13 假跳被验身否决 → 锐度路定界 settle=1，信号=塌陷谷底（非跳变帧差）",
      sF == 1 and vF < 10.0 and bF > 100.0,
      "settle=%d val=%.1f ref=%.1f" % (sF, vF, bF))

# —— 锐度路三量解耦（2026-09-15）：塌陷宽于旧窗但**有恢复** → 必须裁 ——
# 动机：cond 桥「重绘尾段」的复现发糊实测 11–17 帧宽 > 旧 MAX_SETTLE=12。旧实现让
# MAX_SETTLE 同时兼任扫描窗宽，糊区填满窗口时 after 为空 →「窗内必须见恢复」永假 →
# settle 恒 0（实测 onerA_cond4 的 s2/s3 settle=0，段头/段体锐度比 0.71 / 0.41，糊原样留在成片）。
# 解耦后扫描窗独立（SETTLE_SCAN）、参考偏移固定（SETTLE_REF_OFF）→ 恢复得以进入窗内 → 应裁。
sW, _, _ = CORE.detect_settle(blur_seg(n=60, blur=(22, 40)), 22)   # 糊 22..39（18 帧）+ 40 起恢复
check("13.14 塌陷宽于旧窗但有恢复 → 裁，且可超过旧 MAX_SETTLE（解耦生效）",
      sW > CORE.MAX_SETTLE, "settle=%d MAX_SETTLE=%d" % (sW, CORE.MAX_SETTLE))
sX, _, _ = CORE.detect_settle(blur_seg(n=40, blur=(22, 40)), 22)   # 同宽糊，但跑到段尾、无恢复
check("13.15 同一塌陷宽度但段尾截断（无恢复）→ 仍 0（13.3「宁少勿多」契约不破）",
      sX == 0, "settle=%d" % sX)

# ============ 组14：拷贝桥（0.4.0）——latent 硬拷贝 + 噪声掩码 ============

def av_latent(t_steps, a_ticks=48, seed=0, w=8, h=8):
    g = torch.Generator().manual_seed(seed)
    video = torch.rand(1, 4, t_steps, h, w, generator=g)
    audio = torch.rand(1, 2, 2, a_ticks, generator=g)
    return {"samples": CORE._nested_pair(video, audio)}


tgt = av_latent(12, seed=1)
prv = av_latent(12, seed=2)
outC, trimC, repC = CORE.build_continue_latent(tgt, prv, 22)
sv = CORE.video_from_latent(outC)
pv = CORE.video_from_latent(prv)
tv = CORE.video_from_latent(tgt)
check("14.1 拷贝位级一致：前缀 7 步 == 上段尾部 7 步（start=5 对齐）",
      torch.equal(sv[:, :, :7], pv[:, :, 5:12]))
check("14.2 前缀之后保留本段原 latent", torch.equal(sv[:, :, 7:], tv[:, :, 7:]))
check("14.3 输入 target 未被变异", torch.equal(tv, CORE.video_from_latent(tgt)))
m = outC["noise_mask"]
check("14.4 hard 掩码：形状 [1,1,12,8,8]，前 7 步=0 其余=1，有限",
      tuple(m.shape) == (1, 1, 12, 8, 8) and bool((m[:, :, :7] == 0).all())
      and bool((m[:, :, 7:] == 1).all()) and bool(torch.isfinite(m).all()))
check("14.5 trim 输出 = covered = 22（接 TrimAV）", trimC == 22 and "22" in repC)
oa = CORE.audio_from_latent(outC)
pa = CORE.audio_from_latent(prv)
ta = CORE.audio_from_latent(tgt)
rt = int(oa.shape[-1] - round(22 / 24.0 * 40.0) + 0)  # 期望 37
check("14.6 音频尾拷贝：前 37 tick == 上段尾部，其后保留本段",
      torch.equal(oa[..., :37], pa[..., 11:48]) and torch.equal(oa[..., 37:], ta[..., 37:]))
outT, _, trimT = CORE.build_continue_latent(av_latent(12, seed=3), av_latent(12, seed=4), 22,
                                            mask_mode="taper")
wexp = CORE.prefix_taper_weights(7, 4, 0.10)
mT = outT["noise_mask"]
check("14.7 taper 掩码：头 1.0 线性降到缝端 0.10",
      torch.allclose(mT[:, :, :7, 0, 0], torch.tensor(wexp, dtype=torch.float32), atol=1e-6)
      and abs(float(mT[:, :, 6, 0, 0]) - 0.10) < 1e-6, "weights=%s" % (wexp,))
expect_raise("14.8 跨分辨率 → raise（拷贝桥禁止）",
             lambda: CORE.build_continue_latent(tgt, av_latent(12, seed=5, w=8, h=16), 22), "跨分辨率")
big_prev = av_latent(22, seed=6)
expect_raise("14.9 前缀占满整段（73f→22 步 ≥ 目标 12 步）→ raise",
             lambda: CORE.build_continue_latent(tgt, big_prev, 73), "没有新内容")
mis_prev = av_latent(13, seed=7)
expect_raise("14.10 尾段起点非 5 对齐 → raise（接缝位移防线复用）",
             lambda: CORE.build_continue_latent(tgt, mis_prev, 22), "起始于周期位置")
expect_raise("14.11 mask_mode 非法 → raise",
             lambda: CORE.build_continue_latent(tgt, prv, 22, mask_mode="soft"), "mask_mode")
okC, outN = arity(NODES.H3RelayCopyBridge, {"latent": av_latent(12, seed=8),
                                                "context_latent": av_latent(12, seed=9),
                                                "context_frames": 22})
check("14.12 节点返回 3 路（latent/report/trim）", okC, "实得 %d" % len(outN))
check("14.13 节点 trim 输出 = 22", int(outN[2]) == 22)
_it = NODES.H3RelayCopyBridge.INPUT_TYPES()
check("14.14 节点注册 + required 键序 + optional 末位（追加铁律）",
      "H3RelayCopyBridge" in NODES.NODE_CLASS_MAPPINGS
      and list(_it["required"]) == ["latent", "context_latent", "context_frames"]
      and list(_it["optional"])[:4] == ["mask_mode", "taper_tokens", "seam_min", "pin_audio"]
      and list(_it["optional"])[4:6] == ["ramp_top", "ramp_tokens"],
      "required=%s optional=%s" % (list(_it["required"]), list(_it["optional"])))

# —— 14.15/14.16 掩码语义钉子（2026-09-15）——
# 动机：taper 曾被下游误当「软一点的硬锁」当默认跑了 23 次，段首被重画导致「续不上」。
# 这里把语义本身断言下来：hard 全 0 = 真钉住；taper 每一帧 m>0 = **没有被钉住**。
# 谁要改 taper 的方向，先过这两条，再回头改 nodes/README/CHANGES 的措辞。
check("14.15 hard 掩码 = 钉住区全 0（真钉住：d*0 + anchor*1）",
      bool((outC["noise_mask"][:, :, :7] == 0).all()),
      "min=%s max=%s" % (float(m[:, :, :7].min()), float(m[:, :, :7].max())))
check("14.16 taper 掩码 = 钉住区**无一处为 0**（头 1.0 全重绘，缝端仍留 seam_min）",
      bool((mT[:, :, :7] > 0).all()) and float(mT[:, :, 0, 0, 0]) == 1.0
      and float(mT[:, :, 6, 0, 0]) > 0.0,
      "头=%.2f 缝端=%.2f 最小=%.4f（taper 不钉住）"
      % (float(mT[:, :, 0, 0, 0]), float(mT[:, :, 6, 0, 0]), float(mT[:, :, :7].min())))

# ============ 组15：色档收敛信号（v0.4.1）——注噪/taper 收敛尾巴的观测端 ============

def grade_seg(n=40, pin=22, dark=(22, 25), factor=0.88, seed=13):
    """色档收敛合成：f22-24 整体压暗 12%（收敛中），f25 起回归体档。纹理均匀无模糊。"""
    g = torch.Generator().manual_seed(seed)
    texA = 100.0 + torch.rand(8, 8, 3, generator=g) * 20.0
    im = torch.zeros(n, 8, 8, 3)
    for i in range(n):
        f = factor if dark[0] <= i < dark[1] else 1.0
        im[i] = texA * f + (i % 3) * 3.0
    return im


sG, vG, bG = CORE.detect_settle(grade_seg(), 22)
check("15.1 色档收敛（暗 3 帧后回归体档）→ 锐度路不触发、色档路 settle=3",
      sG == 3, "settle=%d val=%.1f thr=%.1f" % (sG, vG, bG))

def wobble_seg(n=40, pin=22, seed=17):
    """自然亮度波动：体区各帧亮度随机 ±5%，头体同分布 → 不误裁。"""
    g = torch.Generator().manual_seed(seed)
    texA = 100.0 + torch.rand(8, 8, 3, generator=g) * 20.0
    im = torch.zeros(n, 8, 8, 3)
    for i in range(n):
        w = 1.0 + (torch.rand(1, generator=g).item() - 0.5) * 0.10
        im[i] = texA * w + (i % 3) * 3.0
    return im


sW, _, _ = CORE.detect_settle(wobble_seg(), 22)
check("15.2 自然亮度波动（头体同分布）→ 0（阈值随体 MAD 自适应）",
      sW == 0, "settle=%d" % sW)

sM2, _, _ = CORE.detect_settle(blur_seg_mild() * 0.85, 22)  # 模糊+整体压暗：锐度路与色档路同响
check("15.3 三信号取最大：mild 模糊(4) 与压暗色档(≥4) → settle ≥ 4",
      sM2 >= 4, "settle=%d" % sM2)
check("15.4 量纲不变：0-1 输入同结论",
      CORE.detect_settle(grade_seg() / 255.0, 22)[0] == 3,
      "settle=%d" % CORE.detect_settle(grade_seg() / 255.0, 22)[0])

# ============ 组16：0.4.2 回归（导出音频分支 / 中文 note / 服务端校验） ============

# 16.1 导出音频尾段分支（0.4.1 前返回 3 元组 → 解包必崩，此前从未被测）
exp_prev = dict(prev)
_pa2 = CORE.audio_from_latent(prev)     # 别用组 2 的 pa——组 14 已把同名变量覆盖成小 latent
exp_tail_v, _eo, _ec = CORE.video_tail_from_latent(prev, 22)
exp_prev[CORE.KEY_EXPORT_TAIL_VIDEO] = torch.cat(exp_tail_v, dim=2)
exp_prev[CORE.KEY_EXPORT_FRAMES] = 22
exp_prev[CORE.KEY_EXPORT_TAIL_AUDIO] = _pa2[0, :, :, -37:]   # 3 维，顺带测 unsqueeze
try:
    t_x, rt_x, oh_x, raw_x, go_x = CORE.audio_tail_from_latent(exp_prev, 22, 192)
    check("16.1 导出音频分支返回 5 元组且步数=尾段长", rt_x == 37 and go_x is False,
          "rt=%d" % rt_x)
except (ValueError, TypeError) as e:
    check("16.1 导出音频分支返回 5 元组且步数=尾段长", False, "→ %s" % e)
plan_x = CORE.plan_relay(cur, exp_prev, 22)
check("16.2 plan_relay 走导出尾段快路径不崩且续接成功",
      plan_x.applied and plan_x.audio_ref["ref_audio_t"] == 37)
cb_t, cb_trim, cb_rep = CORE.build_continue_latent(cur, exp_prev, 22)
check("16.3 拷贝桥走导出音频分支不崩（trim=22）", cb_trim == 22)

# 16.4 中文 note 落盘往返（0.4.1 前 ord(c)>255 直接崩）
tmp_c = os.path.join(tempfile.gettempdir(), "relay_kit_test", "stage_cjk.safetensors")
try:
    CORE.save_av_latent(prev, tmp_c, note="22帧窗 v2 备注——中文、emoji🎬")
    back_c = CORE.load_av_latent(tmp_c)
    # 比较基准现取：组 14 已把组 2 的局部名 pv/pa 覆盖成拷贝桥的小 latent
    check("16.4 中文 note 落盘往返不崩且流仍逐位相同",
          torch.equal(CORE.streams_from_latent(back_c)[0],
                      CORE.video_from_latent(prev)))
except Exception as e:
    check("16.4 中文 note 落盘往返不崩且流仍逐位相同", False, "→ %s: %s" % (type(e).__name__, e))
check("16.5 原子写：落盘后无 .tmp 残留", not os.path.isfile(tmp_c + ".tmp"))

# 16.6 stage_index=0 交「空上下文」而不是 raise（2026-09-28 改）
#   为什么必须这样：宿主在提交前会把 **bypass 的节点"溶解"掉**、把它的输入接到下游；
#   本节点没有 LATENT 输入 ⇒ 一旦被旁路，桥的 required `context_latent` 会**从 prompt 里消失**
#   ⇒ 整个 prompt 被 `prompt_outputs_failed_validation` 拒掉（实测）。
#   ⇒ 第 1 段必须让本节点**留在链上**、交一个空包，由桥识别后走直通分支。
try:
    _empty, _info = NODES.H3RelayLatentLoad().load(run_id="unittest_arity", stage_index=0)
    check("16.6 LatentLoad stage_index=0 → 交空上下文（不抛错）",
          isinstance(_empty, dict) and _empty.get("samples") is None and "无上一段" in _info,
          "实得 %r / %r" % (_empty, _info))
except Exception as e:
    check("16.6 LatentLoad stage_index=0 → 交空上下文（不抛错）", False,
          "→ %s: %s" % (type(e).__name__, e))

# 16.6b 该空包必须被桥识别为「无上下文」并直通（而不是当真 latent 去 build）
try:
    _out0 = NODES.H3RelayCopyBridge().bridge(
        av_latent(12, seed=3), {"samples": None}, context_frames=22,
        run_id="unittest_arity", stage_index=0)
    check("16.6b 桥收到空包 ⇒ 直通且 trim=0", int(_out0[2]) == 0, "trim=%r" % (_out0[2],))
except Exception as e:
    check("16.6b 桥收到空包 ⇒ 直通且 trim=0", False, "→ %s: %s" % (type(e).__name__, e))

# 16.6c 段号≥1 收到空包仍必须 raise（反坏片守卫没丢）
expect_raise("16.6c 段号≥1 + 空包 → 仍 raise（不得静默直通）",
             lambda: NODES.H3RelayCopyBridge().bridge(
                 av_latent(12, seed=3), {"samples": None}, context_frames=22,
                 run_id="unittest_arity", stage_index=1),
             "没有可续接的上一段")

# 16.7 run_id 非法字符替换为 _（不再静默同目录）
p_a = NODES._stage_path("my/film", 0)
p_b = NODES._stage_path("myfilm", 0)
check("16.7 run_id 含斜杠 → 替换为 _，与纯字母名不撞目录",
      p_a != p_b and "my_film" in p_a)
_nul_dir = os.path.basename(os.path.dirname(NODES._stage_path("NUL", 0)))
check("16.8 run_id Windows 保留名加后缀避让", _nul_dir == "NUL_", "目录名=%s" % _nul_dir)
expect_raise("16.9 run_id 全非法字符仍视为空 → raise",
             lambda: NODES._stage_path("///", 0), "不能为空")

# 16.10 fps 服务端校验（widget min=1 只挡 UI，API 可提交 0/NaN）
expect_raise("16.10 fps=0 → raise（不再 ZeroDivisionError）",
             lambda: NODES.H3RelayTrimAV().trim(images=img73, trim_frames=22, fps=0.0),
             "fps 必须")
expect_raise("16.11 fps=NaN → raise",
             lambda: NODES.H3RelayTrimAV().trim(images=img73, trim_frames=22, fps=float("nan")),
             "fps 必须")

# 16.12 音频栅格偏差告警可达（0.4.1 前 overhang 被覆写 0.0，if overhang: 永不触发）
off_grid = {"samples": NT.NestedTensor([
    CORE.video_from_latent(prev)[0],
    torch.zeros(1, 32, 2, 200),   # 音频 tick 远偏离 round(5/3*192)=320 → grid_off
])}
plan_g = CORE.plan_relay(cur, off_grid, 22)
check("16.12 非标准音频栅格 → notes 出现偏差告警",
      any("偏差超出半整步" in n for n in plan_g.notes),
      "notes=%s" % plan_g.notes)

# 16.13 pin_audio=False 走纯视频 latent（0.4.1 前无条件要求音频流）
pure_t = {"samples": torch.rand(1, 4, 12, 8, 8)}
pure_p = {"samples": torch.rand(1, 4, 12, 8, 8)}
try:
    out_p, trim_p, rep_p = CORE.build_continue_latent(pure_t, pure_p, 22, pin_audio=False)
    check("16.13 纯视频 latent + pin_audio=False 走通（trim=22）", trim_p == 22, rep_p)
except ValueError as e:
    check("16.13 纯视频 latent + pin_audio=False 走通（trim=22）", False, "→ %s" % e)
expect_raise("16.14 纯视频 latent + pin_audio=True → 明确 raise（需要音频流）",
             lambda: CORE.build_continue_latent(pure_t, pure_p, 22, pin_audio=True),
             "音频流")

# 16.15 契约降级不缓存：找不到上游时的放行不能被永久缓存
LC = NODES.CONTRACT        # layout_contract 是包内相对导入，走 nodes 已加载的模块对象
_saved = LC._CACHE
LC._CACHE = None
_orig_ext = LC._extract_frame_per_token
LC._extract_frame_per_token = lambda: (None, ["模拟：上游不可见"])
r1 = LC.check_layout()
calls = [0]
def _counting():
    calls[0] += 1
    return (None, ["模拟：上游不可见"])
LC._extract_frame_per_token = _counting
r2 = LC.check_layout()
LC._extract_frame_per_token = _orig_ext
LC._CACHE = _saved
check("16.15 降级放行不写缓存（下次执行会重试解析）",
      r1[0] and r2[0] and calls[0] == 1 and LC._CACHE is _saved)

# 16.16 拷贝桥掩码与 latent 同设备同 batch
mask_p = out_p["noise_mask"]
check("16.16 noise_mask 与 latent 同设备", mask_p.device == pure_t["samples"].device)
b_multi = {"samples": torch.rand(2, 4, 12, 8, 8)}
b_prev = {"samples": torch.rand(1, 4, 12, 8, 8)}
out_b, _, _ = CORE.build_continue_latent(b_multi, b_prev, 22, pin_audio=False)
check("16.17 batch=2 时掩码 batch 维随 target", int(out_b["noise_mask"].shape[0]) == 2)

# ============ 组17：噪声斜坡 ramp（0.4.3，方向一「软证据」接缝） ============
# 语义：连续掩码 m 按原生 H3 契约就是逐 token 的 sigma 标签（model.py forward:
# "mask value m puts a row at sigma = m * sigma_stream"）——不是硬/软两态开关。
# ramp 全程 m<1 ⇒ 每步都被 (1-m) 锚回拷贝尾；与 taper（头 m=1 无锚）方向相反。

outR, trimR, repR = CORE.build_continue_latent(
    av_latent(12, seed=11), av_latent(12, seed=12), 22, mask_mode="ramp")
mR = outR["noise_mask"][:, :, :7, 0, 0].reshape(-1)   # 前缀 7 token 的掩码列
wR = CORE.prefix_ramp_weights(7, 0.25, 0)
check("17.1 ramp 权重 = prefix_ramp_weights（逐 token 对齐）",
      torch.allclose(mR, torch.tensor(wR, dtype=torch.float32), atol=1e-6),
      "weights=%s" % ([round(float(x), 3) for x in mR],))
check("17.2 ramp 首 token 硬钉（m=0）且严格单调升", float(mR[0]) == 0.0
      and all(float(mR[i + 1]) > float(mR[i]) for i in range(6)),
      "首=%.2f 末=%.2f" % (float(mR[0]), float(mR[-1])))
check("17.3 ramp 默认缝端 = 0.25（软证据，非重绘）", abs(float(mR[-1]) - 0.25) < 1e-6)
check("17.4 ramp 全程 m<1 = 每一步都有锚（区别于 taper 头 1.0 无锚）",
      bool((mR < 1.0).all()))
check("17.5 ramp 前缀拷贝仍位级一致（掩码模式不改拷贝）",
      torch.equal(CORE.video_from_latent(outR)[:, :, :7],
                  CORE.video_from_latent(av_latent(12, seed=12))[:, :, 5:12]))
check("17.6 ramp 非前缀区掩码恒 1（新内容照常生成）",
      bool((outR["noise_mask"][:, :, 7:] == 1).all()))
check("17.7 ramp report 含「噪声斜坡」与 trim", "噪声斜坡" in repR and trimR == 22, repR)
# 窄斜坡：只松缝端 2 个 token，其余硬钉
wR2 = CORE.prefix_ramp_weights(7, 0.4, 2)
check("17.8 ramp_tokens=2：前 5 步硬钉 0，末 2 步 0.2/0.4",
      wR2 == (0.0, 0.0, 0.0, 0.0, 0.0, 0.2, 0.4), "w=%s" % (wR2,))
# ramp_top 越界按端点截断（m∈[0,1]）
check("17.9 ramp_top 截断：>1 截到 1.0，<0 截到 0.0（防呆）",
      CORE.prefix_ramp_weights(4, 3.0, 0)[-1] == 1.0
      and CORE.prefix_ramp_weights(4, -1.0, 0)[-1] == 0.0)
# ramp_top=0 退化为 hard（全 0）
outR0, _, _ = CORE.build_continue_latent(av_latent(12, seed=13), av_latent(12, seed=14),
                                         22, mask_mode="ramp", ramp_top=0.0)
check("17.10 ramp_top=0 退化为 hard（前缀全 0）",
      bool((outR0["noise_mask"][:, :, :7] == 0).all()))
# 设备/batch 一致（与 16.16/17 同纪律）
check("17.11 ramp 掩码与 latent 同设备同 batch",
      out_b is not None and "noise_mask" in outR
      and outR["noise_mask"].device == CORE.video_from_latent(outR).device)
# 旧工作流兼容：mask_mode 默认仍是 hard，optional 键尾追加 ramp_*
_itR = NODES.H3RelayCopyBridge.INPUT_TYPES()
check("17.12 mask_mode 选项追加 ramp（旧 hard/taper 原序保留）",
      _itR["optional"]["mask_mode"][0][:3] == ["hard", "taper", "ramp"]
      and "ramp" in _itR["optional"]["mask_mode"][0])
check("17.13 节点 ramp 默认参数（不接线时行为与 0.4.2 完全一致）",
      _itR["optional"]["ramp_top"][1]["default"] == 0.25
      and _itR["optional"]["ramp_tokens"][1]["default"] == 0)

