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

# ============================================================================
# 23. 音画同步守恒（0.6.2）—— `joined` 只在**给了画面裁量**时才能交叉
# ============================================================================
# 事故：`AudioSeam` 第 3 路 `joined` 的**默认档**（join_cross_ms=50 且 join_align_seconds=0）
#   走「旧缩短语义」—— 交叉淡变是 overlap-add ⇒ **每缝使后段音频提前 50 ms**，且逐段累积
#   （第 3 段起口型明显对不上）。而它只在一行 report 里带个 ⚠ ⇒ 属于「静默产出错位片」。
# 处置：① 默认 join_cross_ms 归 0（默认 = 等长拼接，与段文件首尾相接等价）；
#       ② cross>0 而 align=0 ⇒ **直接 raise**（硬错误不静默降级）；
#       ③ TrimAV 报告里直接算出 `join_align_seconds` 建议值，免去用户心算 fps。
import inspect as _insp23

_it23 = NODES.H3RelayAudioSeam.INPUT_TYPES()["optional"]
_sig23 = _insp23.signature(NODES.H3RelayAudioSeam.seam).parameters
check("23.1 `joined` 默认档不再缩短时间轴：join_cross_ms 默认 0（widget 与函数签名一致）",
      _it23["join_cross_ms"][1]["default"] == 0.0
      and _sig23["join_cross_ms"].default == 0.0,
      "widget=%r sig=%r" % (_it23["join_cross_ms"][1]["default"],
                            _sig23["join_cross_ms"].default))

_o23, _r23 = CORE.join_audio_segments([_A22, _B22], prime_samples=_PRJ, cross_samples=0)
_w23 = _o23["waveform"].reshape(1, 1, -1)
_b23 = _B22["waveform"].reshape(1, 1, -1)[..., _PRJ:]
_L023 = int(_A22["waveform"].shape[-1]) - _PRJ
# ⚠ 后段会被**跨段响度匹配**整体缩一个标量（±6 dB 限幅 + 峰值护栏，见 relay_core 2753~2768）
#   ⇒ 断言「逐位相同」是错的；要断言的命题是「**零时间轴位移**」：后段与源段**逐样本位置对齐**，
#   比值处处相等（= 只有幅度差、没有搬运）。这正是「不做交叉 ⇒ 不缩短」的直接取证。
_m23 = _b23.abs() > 0.05
_rr23 = _w23[..., _L023:][_m23] / _b23[_m23]
_g23 = float((_rr23 - _rr23.median()).abs().max())
check("23.2 cross=0 ⇒ 等长 + **零时间轴位移**（后段只被整段增益缩放，样本位置一一对齐）",
      int(_w23.shape[-1]) == _L023 + int(_b23.shape[-1])
      and torch.equal(_w23[..., :_L023], _A22["waveform"].reshape(1, 1, -1)[..., _PRJ:])
      and _g23 < 1e-5
      and "等长拼接" in _r23,
      "%d vs %d ｜ 后段比值离散度 %.2e ｜ 增益 %.6f ｜ mode=%s"
      % (int(_w23.shape[-1]), _L023 + int(_b23.shape[-1]), _g23,
         float(_rr23.median()), _r23.split("｜")[1].strip()[:22]))

_N23 = NODES.H3RelayAudioSeam()
_D23 = {"waveform": torch.zeros(1, 1, 16000), "sample_rate": _SRJ}
expect_raise("23.3 cross>0 而 align=0 ⇒ 节点必须 raise（不静默拼出一条错位音轨）",
             lambda: _N23.seam(_D23, "unit_n23", 0,
                               join_cross_ms=50.0, join_align_seconds=0.0),
             "overlap-add")

_al23, _ss23 = 0.25, 1.0
_o23j, _r23j = CORE.join_audio_segments(
    [_A22, _B22], prime_samples=0, cross_samples=int(0.05 * _SRJ),
    segment_seconds=_ss23, align_seconds=_al23)
_exp23j = int(round(_ss23 * _SRJ)) * 2 - int(round(_al23 * _SRJ))
check("23.4 J-cut 守恒路：输出 == n·段有效时长 − (n−1)·裁量（与裁后视频严格等长）",
      int(_o23j["waveform"].shape[-1]) == _exp23j and "守恒 OK" in _r23j,
      "%d vs %d" % (int(_o23j["waveform"].shape[-1]), _exp23j))

_g23t = torch.rand(30, 8, 8, 3)
_o23t, _a23t, _r23t, _t23t = unwrap(NODES.H3RelayTrimAV().trim(
    _g23t, trim_frames=22, fps=24.0, audio=None))
check("23.5 TrimAV 报告**直接算出** join_align_seconds 建议值（= 裁首帧数 ÷ fps）",
      "join_align_seconds" in _r23t and "0.9167" in _r23t,
      [l.strip() for l in _r23t.splitlines() if "join_align_seconds" in l][:1][0][:80])

# ---------------------------------------------------------------------------
# 24. 床源选窗：语音规避必须覆盖**全部**取床源路径（0.6.5）
#     🔴 起因（2026-09-21，真人耳检阳性 → 根修）：
#       规避原先只加在 `tile_samples <= 0` 分支，而 `tile > 0` 恰恰是产线脚本的**默认档**
#       （`TILE_W=1.2`）⇒ 默认档走的是**没被保护**的那条路；瓦片还会被自叠化环铺 **k 遍**
#       （实测 patch=2.0 + tile=1.2 ⇒ k=2）⇒ 窗内任何一段语音都会被听成 k 遍。
#       修法：所有路径共用 `pick_bed_window`；瓦片档阈值收紧到 **0**（任何有声帧都不许）。
#     ⚠ 判据能力边界：能量型（帧 RMS 超全源中位 3×）⇒ 只能抓「安静底噪里冒出来的语音」；
#       **素材本身以语音为底噪时不报** ⇒ 过判据 ≠「对白重叠」绝迹。
_SR24 = 32000


def _mkbed24(voice=((3.0, 4.0),), total_s=4.5, quiet=0.02, loud=0.40):
    """稳态底噪 + 若干段「台词」的合成床源（2D ``[C,T]``，与产线 `_audio_parts` 同形）。"""
    g = torch.Generator().manual_seed(24)
    w = torch.rand(2, int(total_s * _SR24), generator=g) * quiet
    for a, b in voice:
        w[..., int(a * _SR24):int(b * _SR24)] = loud
    return w


_b24 = _mkbed24()
_W24 = int(1.2 * _SR24)
_n24 = int(1.2 * _SR24)
_f0_24 = int(_b24.shape[-1]) - _W24            # 尾部瓦片窗起点（旧代码的盲点）
_vf0_24 = CORE.bed_window_voiced_fraction(_b24, _f0_24, _W24)
check("24.1 判据自证：尾部瓦片窗确实撞语音（占比 > 单窗阈值）",
      _vf0_24 > CORE.AUDIO_SEAM_BED_VOICED_FRAC,
      "@%.3fs 占比 %.0f%%" % (_f0_24 / _SR24, 100 * _vf0_24))

_s24, _ch24, _v1_24, _v2_24 = CORE.pick_bed_window(
    _b24, _W24, select="tail", prefer=None, floor=0.0,
    frac=CORE.AUDIO_SEAM_BED_TILE_VOICED_FRAC)
check("24.2 瓦片档 tail：首选窗撞语音 ⇒ 换到非语音窗（旧代码此处**不换窗**）",
      _ch24 and _s24 != _f0_24 and _v1_24 > CORE.AUDIO_SEAM_BED_VOICED_FRAC and _v2_24 == 0.0,
      "@%.3fs vf %.0f%%→%.0f%%" % (_s24 / _SR24, 100 * _v1_24, 100 * _v2_24))
check("24.3 瓦片档阈值(0) 比单窗档(10%) 更严：窗内**任何**有声帧都判撞（会被铺 k 遍）",
      CORE.AUDIO_SEAM_BED_TILE_VOICED_FRAC == 0.0
      and CORE.AUDIO_SEAM_BED_TILE_VOICED_FRAC < CORE.AUDIO_SEAM_BED_VOICED_FRAC,
      "tile %.2f vs 单窗 %.2f" % (CORE.AUDIO_SEAM_BED_TILE_VOICED_FRAC,
                                  CORE.AUDIO_SEAM_BED_VOICED_FRAC))

# 24.6 同族函数的 rank 约定必须一致（此前 quietest_window 写死 sum(dim=0) ⇒ 3D 崩）
check("24.6 rank 一致：2D 与 3D 床源选窗完全相同（quietest / pick 各验一遍）",
      CORE.quietest_window(_b24, int(0.5 * _SR24), floor=0.0)[0]
      == CORE.quietest_window(_b24.unsqueeze(0), int(0.5 * _SR24), floor=0.0)[0]
      and CORE.pick_bed_window(_b24, _W24, select="quiet", frac=0.0)[0]
      == CORE.pick_bed_window(_b24.unsqueeze(0), _W24, select="quiet", frac=0.0)[0],
      "2D/3D 同点")

# 24.7 无候选（全源皆撞）⇒ 退回首选窗、**不 raise**（「素材本身以语音为底噪」是合法素材）
_b24d = _mkbed24(voice=tuple((i * 0.75, i * 0.75 + 0.30) for i in range(12)), total_s=9.0)
_nc24 = len(CORE.nonvoiced_candidates(_b24d, _W24, 1600, 0.0))
_s24d, _ch24d, _v1_24d, _v2_24d = CORE.pick_bed_window(
    _b24d, _W24, select="tail", prefer=None, floor=0.0,
    frac=CORE.AUDIO_SEAM_BED_TILE_VOICED_FRAC)
check("24.7 全源皆撞语音 ⇒ 不 raise，tail 退到最静窗（语音残留最少）且占比不升",
      _nc24 == 0 and _ch24d and _s24d != _b24d.shape[-1] - _W24 and _v2_24d <= _v1_24d,
      "候选 %d 个 ⇒ 尾窗@%.3fs(vf %.0f%%) → 最静窗@%.3fs(vf %.0f%%)"
      % (_nc24, (_b24d.shape[-1] - _W24) / _SR24, 100 * _v1_24d,
         _s24d / _SR24, 100 * _v2_24d))

# 24.8 默认关不变量：尾部窗**不撞语音**时 ⇒ 起点与「直接切尾窗」逐位相同
_b24q = _mkbed24(voice=((0.5, 1.0),))            # 语音在中段，尾部干净
_bq24, _stq24, _ = CORE.build_bed(_b24q, _n24, 0, int(0.25 * _SR24), select="tail")
check("24.8 默认关不变量：尾部不撞语音 ⇒ 起点=尾部且床声逐位相同（干净素材零行为变化）",
      _stq24 == int(_b24q.shape[-1]) - _n24
      and torch.equal(_bq24, _b24q[..., _stq24:_stq24 + _n24]),
      "起点 %.3fs" % (_stq24 / _SR24))

# 24.9 性能回归锁：候选遍历必须先算一次帧 RMS（原实现每个候选重算整段 ⇒ O(T²)）
_calls24 = [0]
_orig_fr24 = CORE._frame_rms


def _count_fr24(*a, **kw):
    _calls24[0] += 1
    return _orig_fr24(*a, **kw)


CORE._frame_rms = _count_fr24
try:
    CORE.nonvoiced_candidates(_b24, _W24, 1600, 0.0)
finally:
    CORE._frame_rms = _orig_fr24
check("24.9 候选遍历只算一次帧 RMS（原实现 O(T²)：60s 床源会卡住主线程）",
      _calls24[0] == 1, "调用 %d 次" % _calls24[0])

# 24.10~24.11 报告：说清「为什么换窗」+ 报出阈值；E5 档窗位只印一次
_cur24 = {"waveform": torch.zeros(1, 2, int(3.5 * _SR24)), "sample_rate": _SR24}
_cur24["waveform"][..., int(0.5 * _SR24):int(1.0 * _SR24)] = 0.3
_bed24 = {"waveform": _b24.unsqueeze(0), "sample_rate": _SR24}
_, _rep24 = CORE.audio_seam_patch(_cur24, _bed24, patch=2.0, tile=1.2, fade=0.25,
                                  select="tail", stage_index=1)
check("24.10 瓦片档报告：阈值 + 「首选窗原撞语音 ⇒ 已换到非语音窗」都在（不静默）",
      "判据阈值 0%" in _rep24 and "首选窗原撞语音（占比" in _rep24
      and "已换到非语音窗 @" in _rep24,
      next((l for l in _rep24.splitlines() if "🎙" in l), "")[:150])
# 24.12 持续帧滤波（P0）：单帧瞬态不计入，连续台词无损（瓦片档 0% 阈值的瞬态防线）
_b24t = _mkbed24()                                   # 尾窗 58% 台词（连续块）
_b24x = _mkbed24(voice=())                           # 纯底噪
_b24x[..., _b24x.shape[-1] - int(0.3 * _SR24) - 1600: _b24x.shape[-1] - int(0.3 * _SR24) - 1600 + 1600] = 0.6
_vt24 = CORE.bed_window_voiced_fraction(_b24t, _b24t.shape[-1] - _W24, _W24)
_vx24 = CORE.bed_window_voiced_fraction(_b24x, _b24x.shape[-1] - _W24, _W24)
check("24.12 持续帧滤波：连续台词占比无损（58% 级）且单帧瞬态不计入（0%）",
      _vt24 > 0.5 and _vx24 == 0.0,
      "台词 %.0f%% / 瞬态 %.0f%%" % (100 * _vt24, 100 * _vx24))

# 24.13 退化输入不炸：prefer 越界钳制 / 全零床源 / 超短床源（<2 帧无法判定 ⇒ 放行不 raise）
_b24z = torch.zeros(2, int(1.0 * _SR24))
_ok24 = []
try:
    _s24p, _, _, _ = CORE.pick_bed_window(_b24, _W24, select="tail", prefer=10 ** 9, frac=0.0)
    _ok24.append(_s24p <= _b24.shape[-1] - _W24)
    _s24m, _, _, _ = CORE.pick_bed_window(_b24, _W24, select="tail", prefer=-5, frac=0.0)
    _ok24.append(_s24m == 0)
    CORE.pick_bed_window(_b24z, int(0.5 * _SR24), select="tail", frac=0.0)
    _ok24.append(True)
    _st24s, _, _, _ = CORE.pick_bed_window(torch.zeros(2, 3000), 2000, select="tail", frac=0.0)
    _ok24.append(_st24s == 3000 - 2000)          # tail 语义：超短床源放行但起点仍在尾部
    _ok24.append(True)
except Exception as _e24:
    _ok24 = ["raise: %s" % _e24]
check("24.13 退化输入不炸：prefer 越界钳制 / 全零 / 超短床源（<2 帧放行不判定）",
      all(x is True for x in _ok24), str(_ok24))

# 24.14 前缀和能量选窗 == 逐候选参照实现（0.6.4 性能修补的正确性锁）
_c24 = CORE.nonvoiced_candidates(_b24, _W24, 1600, CORE.AUDIO_SEAM_BED_TILE_VOICED_FRAC)
_ct24 = torch.tensor(_c24, dtype=torch.long)
_ch24n = 2
_p24 = (_b24 ** 2).reshape(-1, _b24.shape[-1]).sum(dim=0)
_c24c = torch.cat([_p24.new_zeros(1), torch.cumsum(_p24, dim=0)])
_wr24 = torch.sqrt((_c24c[_ct24 + _W24] - _c24c[_ct24]).clamp_min(0.0) / (_W24 * _ch24n))
_naive24 = max(_c24, key=lambda c: float(_b24[..., c:c + _W24].pow(2).mean().sqrt()))
check("24.14 候选能量前缀和 == 逐候选参照（性能修补不许改结果）",
      int(_ct24[int(torch.argmax(_wr24))]) == int(_naive24),
      "前缀和 @%.3fs / 参照 @%.3fs" % (_ct24[int(torch.argmax(_wr24))] / _SR24, _naive24 / _SR24))

# ---------------------------------------------------------------------------
# 25. 🛡 patch 台词守卫（0.6.5）：patch 不许吃本段自己的台词
#     🔴 起因（2026-09-21 本仓作者 耳检 + 词级时间戳实锤）：patch=2.0 把 okBed2 段2 的
#       「这家店」0.60–1.70s 整句吃掉（撑 跨淡变残缺）—— patch 只管"换床声"，
#       根本不看本段头上有没有话。守卫 = 探测本段头部台词起点 ⇒ patch 收缩到
#       onset − 0.25s；onset ≤ 0.10s 可用 ⇒ patch 关闭。判据 **宁枉勿纵**（2×P10）。
_SR25 = 32000


def _mktarget25(onset=0.6, dur_s=4.0, quiet=0.02, loud=0.40):
    g = torch.Generator().manual_seed(25)
    w = torch.rand(2, int(dur_s * _SR25), generator=g) * quiet
    w[..., int(onset * _SR25):int((onset + 1.6) * _SR25)] = loud
    return w


_g25bed = torch.rand(2, int(6.0 * _SR25), generator=torch.Generator().manual_seed(26)) * 0.015
_t25 = {"waveform": _mktarget25(), "sample_rate": _SR25}
_b25d = {"waveform": _g25bed, "sample_rate": _SR25}
_X25 = int(0.25 * _SR25)

# 25.1 头部带台词 ⇒ 收缩 + 台词区逐位保留
_o25a, _r25a = CORE.audio_seam_patch(_t25, _b25d, patch=2.0, tile=0.0, fade=0.25,
                                     select="tail", stage_index=1)
_on25 = CORE._speech_onset_in_head(CORE._audio_parts(_t25)[0], int(2.0 * _SR25))
_w25o = CORE._audio_parts(_o25a)[0]
_w25r = CORE._audio_parts(_t25)[0]
check("25.1 头部台词 @%.2fs ⇒ patch 自动收缩且台词起逐位无损" % (_on25 / _SR25),
      _on25 is not None and "避让台词" in _r25a
      and torch.equal(_w25o[..., _on25:], _w25r[..., _on25:]),
      "report: " + [l for l in _r25a.splitlines() if "🛡" in l][0][-70:])

# 25.2 头部干净 ⇒ 守卫开 == 守卫关（逐位，零副作用）
_t25q = {"waveform": _mktarget25(onset=99), "sample_rate": _SR25}
_o25g, _ = CORE.audio_seam_patch(_t25q, _b25d, patch=2.0, tile=0.0, fade=0.25,
                                 select="tail", stage_index=1)
_o25n, _ = CORE.audio_seam_patch(_t25q, _b25d, patch=2.0, tile=0.0, fade=0.25,
                                 select="tail", stage_index=1, patch_guard=False)
check("25.2 头部无台词 ⇒ 守卫开与关输出逐位一致（干净素材零行为变化）",
      torch.equal(CORE._audio_parts(_o25g)[0], CORE._audio_parts(_o25n)[0]))

# 25.3 台词太靠前（onset − margin < 0.10s 可用）⇒ patch 关闭 + 原样返回 + report 说明
_t25e = {"waveform": _mktarget25(onset=0.05), "sample_rate": _SR25}
_o25e, _r25e = CORE.audio_seam_patch(_t25e, _b25d, patch=2.0, tile=0.0, fade=0.25,
                                     select="tail", stage_index=1)
check("25.3 台词太靠前 ⇒ patch 自动关闭、音频原样返回、report 说明",
      torch.equal(CORE._audio_parts(_o25e)[0], CORE._audio_parts(_t25e)[0])
      and "patch 自动关闭" in _r25e,
      _r25e.splitlines()[0][:80])

# 25.4 守卫关 ⇒ 旧行为（头部确实被替换 ⇒ 可能吞字，这是用户显式选择的语义）
_o25f, _r25f = CORE.audio_seam_patch(_t25, _b25d, patch=2.0, tile=0.0, fade=0.25,
                                     select="tail", stage_index=1,
                                     patch_guard=False)
_w25f = CORE._audio_parts(_o25f)[0]
check("25.4 守卫关 ⇒ 旧行为（头部被替换、report 无 🛡）",
      "🛡" not in _r25f
      and not torch.equal(_w25f[..., :int(0.5 * _SR25)], _w25r[..., :int(0.5 * _SR25)]))

# ============ 组26：多段拼接成片（0.6.7）——探测/体检/无损拷贝/音频对齐 ============
# 全部在**合成段文件**上跑（PyAV 现造 mp4），零 GPU；宿主 ComfyUI 自带 av，缺了就整组跳过。
print()
print("[26] 多段拼接成片：探测 → 体检 → 拼接（画面流拷贝无损 + 音频逐段对齐）→ 断言")


def _concat_env():
    try:
        import av
        import numpy as np
        return av, np
    except Exception:
        return None, None


_av26, _np26 = _concat_env()
if _av26 is None:
    check("26.0 PyAV 可用（拼接功能的运行前提；宿主 ComfyUI 自带）", False,
          "没装 av ⇒ 本组跳过（pip install 'av>=17'）")
else:
    import fractions as _fr26

    _TMP26 = tempfile.mkdtemp(prefix="h3relay_concat_")
    # 合成夹具优先用 libx264（生产默认）；精简 FFmpeg（如 CI 的 av 轮子）上没有它 ⇒ 退 mpeg4。
    #   本组测的是**拼接逻辑**，与编码器无关；真机跑的是 libx264。
    _VCODEC26 = "libx264" if "libx264" in set(_av26.codecs_available) else "mpeg4"

    def _mkclip(name, frames=48, fps=24, w=64, h=64, tone=440.0, audio_secs=None,
                sr=32000, noise_seed=0):
        """现造一个与真实段文件同构的 mp4：h264 + aac，音频比视频长（编码器 priming）。"""
        path = os.path.join(_TMP26, name)
        out = _av26.open(path, "w", format="mp4")
        vs = out.add_stream(_VCODEC26, rate=fps, options=(
            {"crf": "28", "preset": "ultrafast"} if _VCODEC26 == "libx264" else {"qscale": "5"}))
        vs.width, vs.height, vs.pix_fmt = w, h, "yuv420p"
        as_ = out.add_stream("aac", rate=sr)
        as_.layout = "stereo"
        rng = _np26.random.default_rng(noise_seed)
        for i in range(frames):
            arr = rng.integers(0, 255, (h, w, 3), dtype=_np26.uint8)
            fr = _av26.VideoFrame.from_ndarray(arr, format="rgb24").reformat(format="yuv420p")
            fr.pts = i
            fr.time_base = _fr26.Fraction(1, fps)
            for pkt in vs.encode(fr):
                out.mux(pkt)
        secs = audio_secs if audio_secs is not None else frames / float(fps)
        n = int(round(secs * sr))
        t = _np26.arange(n) / float(sr)
        sig = (0.3 * _np26.sin(2 * _np26.pi * tone * t)).astype("float32")
        pcm = _np26.stack([sig, sig])
        pos = 0
        for s in range(0, n, 1024):
            af = _av26.AudioFrame.from_ndarray(
                _np26.ascontiguousarray(pcm[:, s:s + 1024]), format="fltp", layout="stereo")
            af.sample_rate = sr
            af.pts = pos
            af.time_base = _fr26.Fraction(1, sr)
            pos += af.samples
            for pkt in as_.encode(af):
                out.mux(pkt)
        for st in (vs, as_):
            for pkt in st.encode(None):
                out.mux(pkt)
        out.close()
        return path

    _c1 = _mkclip("c1.mp4", tone=440.0, noise_seed=1)
    _c2 = _mkclip("c2.mp4", tone=880.0, noise_seed=2)
    _c3 = _mkclip("c3.mp4", tone=1320.0, noise_seed=3)

    # —— 26.1 探测：读出真实容器事实 ——
    # ⚠ 合成段的音频正好等于视频长（PyAV 封装会按 edit list 把编码器 priming 剪掉）；
    #   真实段文件比视频**长**（宿主落盘把裁后 PCM 原样写进去）——那种「多出来的样本」
    #   由 26.3 的 a_prime_samples 判据覆盖。这里只锁"读得准"。
    _p1 = CORE.probe_mp4(_c1)
    check("26.1 probe_mp4 读出帧数/fps/时长/音频（合成段）",
          _p1["frames"] == 48 and abs(_p1["fps"] - 24.0) < 1e-9
          and abs(_p1["v_seconds"] - 2.0) < 1e-3 and _p1["has_audio"]
          and abs(_p1["a_seconds"] - _p1["v_seconds"]) <= 1.0 / 24.0,
          "frames=%d fps=%s v=%.4f a=%.4f 差=%+.4f"
          % (_p1["frames"], _p1["fps"], _p1["v_seconds"], _p1["a_seconds"], _p1["a_v_delta"]))

    # —— 26.2 体检：priming 量级的差不算问题（否则每张正常图都会红） ——
    _h_ok = CORE.assert_segments_joinable([_p1, CORE.probe_mp4(_c2)])
    check("26.2 体检放过 priming 量级的音视频差（默认档不误报）", _h_ok["ok"],
          "problems=%s" % _h_ok["problems"])

    # —— 26.3 体检：音频**明显**长于视频 ⇒ 判「音频线绕过裁重叠」并拒绝拼 ——
    _c_long = _mkclip("c_long.mp4", frames=48, tone=440.0, audio_secs=2.0 + 0.5, noise_seed=4)
    _p_long = CORE.probe_mp4(_c_long)
    _h_bad = CORE.assert_segments_joinable([_p1, _p_long])
    check("26.3 体检抓到「音频绕过裁重叠」（> 1 帧）且给出可照做的处置",
          (not _h_bad["ok"]) and any("绕过" in p and "裁重叠" in p for p in _h_bad["problems"])
          and _p_long["a_prime_samples"] > 0,
          "a_prime=%+d samples ｜ problems=%s"
          % (_p_long["a_prime_samples"], _h_bad["problems"][:1]))

    # —— 26.4 体检：规格不一致（帧率/分辨率）不许拼 ——
    _c_fps = _mkclip("c_fps.mp4", frames=48, fps=30, noise_seed=5)
    _h_fps = CORE.assert_segments_joinable([_p1, CORE.probe_mp4(_c_fps)])
    check("26.4 体检抓到帧率不一致（拼出来必坏）",
          (not _h_fps["ok"]) and any("规格" in p for p in _h_fps["problems"]))

    # —— 26.5 体检不过 ⇒ 一段都不拼（坏片不许落地） ——
    _bad_out = os.path.join(_TMP26, "should_not_exist.mp4")
    _rep_bad = CORE.assemble_mp4_segments([_c1, _c_long], _bad_out)
    check("26.5 体检不过 ⇒ 不产出成片（不静默拼半条）",
          (not _rep_bad["ok"]) and (not os.path.exists(_bad_out))
          and "没有拼" in _rep_bad["report"])

    # —— 26.6 端到端：画面流拷贝（无损）+ 音频逐段对齐 + 四项断言 ——
    _out26 = os.path.join(_TMP26, "film.mp4")
    _rep26 = CORE.assemble_mp4_segments([_c1, _c2, _c3], _out26)
    _a26 = _rep26["asserts"] or {}
    check("26.6 三段端到端：帧数守恒 + PTS 无洞 + DTS 递增 + A·VΔ ≤ 1 帧",
          _rep26["ok"] and _a26.get("frames") == 144
          and _a26.get("frames_conserved") and _a26.get("pts_contiguous")
          and _a26.get("dts_strictly_increasing") and _a26.get("av_delta_ok"),
          "mode=%s asserts=%s" % (_rep26.get("mode"), _a26))
    check("26.7 默认走画面**流拷贝**（无损，不重编码）", _rep26.get("mode") == "copy")

    # —— 26.8 无损性：成片第 k 段首帧 与源段首帧**逐位相同** ——
    def _first_frames(path, wanted):
        got = {}
        with _av26.open(path) as c:
            v = c.streams.video[0]
            for i, f in enumerate(c.decode(v)):
                if i in wanted:
                    got[i] = f.to_ndarray()
                if i > max(wanted):
                    break
        return got

    _of = _first_frames(_out26, {0, 48, 96})
    _sf1 = _first_frames(_c1, {0})[0]
    _sf2 = _first_frames(_c2, {0})[0]
    _sf3 = _first_frames(_c3, {0})[0]
    check("26.8 流拷贝无损：三段首帧在成片里逐位一致",
          _np26.array_equal(_of[0], _sf1) and _np26.array_equal(_of[48], _sf2)
          and _np26.array_equal(_of[96], _sf3))

    # —— 26.9 音频对齐：段边界两侧的主频就是各自源段的音（错位/留洞都会破坏） ——
    def _dom_freq(path, start_s, dur_s=0.2, sr=32000):
        with _av26.open(path) as c:
            a = c.streams.audio[0]
            rs = _av26.audio.resampler.AudioResampler(format="fltp", layout="stereo", rate=sr)
            chunks = [o.to_ndarray() for f in c.decode(a) for o in rs.resample(f)]
        wav = _np26.concatenate(chunks, axis=1)
        i0 = int(start_s * sr)
        seg = wav[0, i0:i0 + int(dur_s * sr)]
        if seg.size < 64:
            return 0.0
        spec = _np26.abs(_np26.fft.rfft(seg * _np26.hanning(seg.size)))
        return float(_np26.fft.rfftfreq(seg.size, 1.0 / sr)[int(_np26.argmax(spec))])

    _pm = int(round(CORE.AUDIO_ENCODER_PRIME_MS / 1000.0 * 32000))
    _f_before = _dom_freq(_out26, (96 / 24.0) - 0.4 + _pm / 32000.0)   # 第三段边界前 0.4s
    _f_after = _dom_freq(_out26, (96 / 24.0) + _pm / 32000.0)          # 第三段开头 0.2s
    check("26.9 音频逐段对齐：边界前 = 第 2 段音(880Hz)、边界后 = 第 3 段音(1320Hz)",
          abs(_f_before - 880.0) < 40.0 and abs(_f_after - 1320.0) < 40.0,
          "before=%.1fHz after=%.1fHz（期望 880 / 1320）" % (_f_before, _f_after))

    # —— 26.10 退路：显式强制重编码路，同样要过四项断言 ——
    _out26e = os.path.join(_TMP26, "film_encode.mp4")
    _rep26e = CORE.assemble_mp4_segments([_c1, _c2], _out26e, video="encode")
    _ae = _rep26e["asserts"] or {}
    check("26.10 退路（video=\"encode\"）也过断言：帧数守恒 + A·VΔ ok",
          _rep26e["ok"] and _rep26e.get("mode") == "encode"
          and _ae.get("frames_conserved") and _ae.get("av_delta_ok"),
          "mode=%s asserts=%s" % (_rep26e.get("mode"), _ae))

    # —— 26.11 空输入不炸：报「没有任何段文件」，不写出东西 ——
    _rep_empty = CORE.assemble_mp4_segments([], os.path.join(_TMP26, "empty.mp4"))
    check("26.11 空段列表 ⇒ 明确报错、不产出", (not _rep_empty["ok"])
          and "没有任何段文件" in _rep_empty["report"])

    # —— 26.12 history 落盘条目筛选（纯函数；宿主 SaveVideo 写进 images 键） ——
    _picked = CORE.pick_video_outputs({
        "31": {"images": [{"filename": "a.mp4", "subfolder": "relay_kit/x", "type": "output"},
                          {"filename": "a.png", "subfolder": "relay_kit/x", "type": "output"}]},
        "32": {"text": ["noise"]},
        "33": {"images": [{"filename": "b.webm", "subfolder": "", "type": "output"}]},
    })
    check("26.12 pick_video_outputs 只挑视频类落盘（png 与无关键不误收）",
          [p["filename"] for p in _picked] == ["a.mp4", "b.webm"]
          and _picked[0]["subfolder"] == "relay_kit/x",
          "%s" % [p["filename"] for p in _picked])
    check("26.13 pick_video_outputs 对畸形输入不炸", CORE.pick_video_outputs(None) == []
          and CORE.pick_video_outputs({"1": {"images": [1, None, "x"]}}) == [])

    # —— 26.45 落盘节点**键名不设白名单**（2026-09-22 真实跑抓到） ——
    #   宿主 SaveVideo 写 `images`；第三方 `banzhangVideoCombine` 写 **`painter_output`**，
    #   且它的「自定义保存路径」在 ComfyUI output **之外**（subfolder 为空）⇒ 必须优先用 abs_path。
    _tp = CORE.pick_video_outputs({
        "709": {"painter_output": [
            {"filename": "au4_s1_N709_0001.mp4", "subfolder": "", "type": "output",
             "abs_path": r"D:/outside_output/au4_s1_N709_0001.mp4"},
            {"filename": "au4_s1_N709_0001_meta.png", "subfolder": "", "type": "output"}],
            "detail_info": ["📂 文件名称 : au4_s1_N709_0001.mp4\n📏 物理尺寸 : 480 x 864"]},
        "905": {"h3relay_pcm": [{"filename": "audio_00000.safetensors",
                                 "subfolder": "relay_kit\\au4", "type": "output"}]},
    })
    check("26.45 第三方落盘键名（painter_output）也能拾取：视频收下、png/文本不误收、abs_path 带出",
          [x["filename"] for x in _tp] == ["au4_s1_N709_0001.mp4"]
          and _tp[0]["abs_path"].replace("\\", "/").endswith("outside_output/au4_s1_N709_0001.mp4"),
          "%s" % ([(x["filename"], x["abs_path"]) for x in _tp],))
    # —— 26.47 落盘路径发现要**通用**（不认节点名 / 键名 / 字段名） ——
    #   各家节点回显各不相同：字符串路径 / `file` 键 / 相对带目录 / 同一文件多处报 —— 全都要能收下。
    _gen = CORE.pick_video_outputs({
        "1": {"videos": ["D:/x/str_path.mp4"]},                       # 只给一个路径字符串
        "2": {"out": [{"file": "sub/dir/f.mp4"}]},                    # `file` 键 + 相对目录
        "3": {"a": [{"filename": "dup.mp4", "subfolder": "s", "type": "output"}],
              "b": [{"filename": "dup.mp4", "subfolder": "s", "type": "output"}]},
        "4": {"txt": ["不是文件"], "img": [{"filename": "cover.png", "type": "output"}]},
    })
    check("26.47 通用归一化：字符串路径 / file 键 / 相对目录 / 去重 / png+文本不误收（共 3 条）",
          [x["filename"] for x in _gen] == ["str_path.mp4", "f.mp4", "dup.mp4"]
          and _gen[0]["abs_path"].replace("\\", "/") == "D:/x/str_path.mp4"
          and _gen[1]["subfolder"].replace("\\", "/") == "sub/dir",
          "%s" % ([(x["filename"], x["subfolder"], x["abs_path"]) for x in _gen],))

    # —— 26.47b 绝对性判据**不许依赖宿主平台**（Linux CI 抓到：`os.path.isabs` 不认盘符式
    #   ⇒ `abs_path` 丢空、相对分支把整串当目录 ⇒ 拼接拿不到文件）。两种风格在任意宿主都得认。
    check("26.47b 绝对性判据平台中立：盘符式/反斜杠式/斜杠式都判绝对，相对名与 `C:a.mp4` 仍相对",
          CORE._path_is_abs("D:/x/a.mp4") and CORE._path_is_abs(r"C:\x\a.mp4")
          and CORE._path_is_abs("/data/out/a.mp4") and CORE._path_is_abs(r"\\srv\share\a.mp4")
          and not CORE._path_is_abs("sub/dir/a.mp4") and not CORE._path_is_abs("C:a.mp4")
          and CORE._file_record({"abs_path": "/data/out/b.mp4"})["abs_path"] == "/data/out/b.mp4"
          and CORE._file_record({"abs_path": r"D:\out\b.mp4"})["filename"] == "b.mp4",
          "%s" % ([(s, CORE._path_is_abs(s)) for s in
                   ("D:/x/a.mp4", "/data/out/a.mp4", "sub/dir/a.mp4", "C:a.mp4")],))

    # —— 26.48 说明文本**不许**被当成文件（预检抓到的假阳性） ——
    #   真实形状：`detail_info` 里是 `📂 文件名称 : au4_s1_N709_0001.mp4\n📏 物理尺寸 : …`
    #   —— 末尾也是 .mp4，若被当成记录且排在真记录前面，就会拿错路径（白跑一轮）。
    _trap = CORE.pick_video_outputs({
        "709": {"detail_info": ["📂 文件名称 : au4_s1_N709_0001.mp4\n📏 物理尺寸 : 480 x 864"],
                "painter_output": [{"filename": "au4_s1_N709_0001.mp4", "subfolder": "",
                                    "type": "output", "abs_path": r"D:/outside_output/au4_s1_N709_0001.mp4"}]},
        "710": {"note": ["📂 文件名称 : x.mp4", "带\n换行的 y.mp4"]},
    })
    check("26.48 说明文本/非法文件名不误收（只有真记录那一条，且排第 1）",
          [x["filename"] for x in _trap][:1] == ["au4_s1_N709_0001.mp4"]
          and "x.mp4" not in [x["filename"] for x in _trap]
          and len(_trap) == 1,
          "%s" % ([x["filename"] for x in _trap],))

    check("26.45b 同一份 outputs 里，PCM 边车按 .safetensors 走 pick_pcm_outputs（与视频互不串）",
          [x["filename"] for x in CORE.pick_pcm_outputs({
              "905": {"h3relay_pcm": [{"filename": "audio_00000.safetensors",
                                       "subfolder": "relay_kit/au4", "type": "output"}]}})]
          == ["audio_00000.safetensors"])

    # ================================================================
    # 26.19~26.31 音频代际 2 → 1（2026-09-22 二轮）：**PCM 边车**
    # ================================================================
    # 动机：段文件的音轨是 AAC（用户落盘那一代）。拼成片若从 mp4 解码再编 = **第二代数损**。
    # 「Trim AV」手里那份音频**既与画面等长、又还没经过有损编码** ⇒ 它顺手存一份 PCM 边车，
    # 拼接直读 ⇒ 音频只编码一代；配无损档则**零新增代际**。
    import inspect as _ins26
    import folder_paths as _fp26

    _side_a = os.path.join(_TMP26, "pcm_a.safetensors")
    _side_b = os.path.join(_TMP26, "pcm_b.safetensors")
    _side_16k = os.path.join(_TMP26, "pcm_16k.safetensors")
    _side_short = os.path.join(_TMP26, "pcm_short.safetensors")
    _TMP26_SR = 32000
    _mk_n = 64000

    def _mk_side(path, tone, n=_mk_n, sr=_TMP26_SR):
        tt = _np26.arange(n) / float(sr)
        sig = (0.3 * _np26.sin(2 * _np26.pi * tone * tt)).astype("float32")
        CORE.save_audio({"waveform": torch.from_numpy(_np26.stack([sig, sig])).unsqueeze(0),
                         "sample_rate": sr}, path, note="unit")
        return path

    def _audio_of(path, sr=_TMP26_SR):
        with _av26.open(path) as c:
            a = c.streams.audio[0]
            rs = _av26.audio.resampler.AudioResampler(format="fltp", layout="stereo", rate=sr)
            ch = [o.to_ndarray() for f in c.decode(a) for o in rs.resample(f)]
            ch += [o.to_ndarray() for o in (rs.resample(None) or [])]
        return _np26.concatenate(ch, axis=1)

    _mk_side(_side_a, 300.0)
    _mk_side(_side_b, 900.0)

    # —— 26.19 默认档：AAC 256k（用户口径：默认 256K、192K 可覆盖） ——
    check("26.19 成片音轨默认档 = AAC **256k**（可显式覆盖）",
          _ins26.signature(CORE.concat_mp4_segments).parameters["audio_bitrate"].default == "256k",
          "default=%r" % _ins26.signature(CORE.concat_mp4_segments).parameters["audio_bitrate"].default)

    # —— 26.20 无损档 + 边车：成片音轨与边车**逐位一致**（零新增代际） ——
    _out_ll = os.path.join(_TMP26, "film_lossless.mp4")
    _rep_ll = CORE.assemble_mp4_segments([_c1, _c2], _out_ll, audio_codec="pcm_f32le",
                                         pcm_paths=[_side_a, _side_b])
    _ref = CORE.load_audio(_side_a)["waveform"].reshape(-1, 2, _mk_n)[0].to(torch.float32).numpy()
    _film = _audio_of(_out_ll)
    _delta = float(_np26.max(_np26.abs(_film[:, :_ref.shape[1]] - _ref)))
    check("26.20 无损档 + 边车 ⇒ 成片第 1 段音轨与边车**逐位一致**（音频零新增代际）",
          _rep_ll["ok"] and _rep_ll.get("pcm_segments") == 2 and _delta < 1e-6,
          "ok=%s pcm段=%s max|Δ|=%.3e" % (_rep_ll["ok"], _rep_ll.get("pcm_segments"), _delta))

    # —— 26.21 边车直读时**不做**去 priming 的位移（对齐记录要如实） ——
    check("26.21 走边车的段：prime_drop=0、tail_drop=0（边车本身已与画面等长）",
          all(a["source"] == "pcm" and a["prime_drop"] == 0 and a["tail_drop"] == 0
              for a in _rep_ll["alignment"]),
          "%s" % [(a["source"], a["prime_drop"], a["tail_drop"]) for a in _rep_ll["alignment"]])
    # 同一批段走 mp4 解码路时，第 1 段必须真的去掉了 priming（否则 26.20 的对比没意义）
    check("26.22 对照：不接边车时该段走 mp4 解码路（source=aac）",
          all(a["source"] == "aac" for a in _rep26["alignment"]),
          "%s" % [a["source"] for a in _rep26["alignment"]])

    # —— 26.23 编码码率可覆盖（192k）；仍出片、仍过四项断言 ——
    _out_192 = os.path.join(_TMP26, "film_192k.mp4")
    _rep_192 = CORE.assemble_mp4_segments([_c1, _c2], _out_192, audio_bitrate="192k")
    check("26.23 AAC 192k 档可覆盖且成片过断言",
          _rep_192["ok"] and (CORE.probe_mp4(_out_192)["has_audio"]),
          "ok=%s" % _rep_192["ok"])

    # —— 26.24 混档：只有部分段有边车 ⇒ 逐段标注来源，缺的自动退回解码（不静默） ——
    _out_mix = os.path.join(_TMP26, "film_mix.mp4")
    _rep_mix = CORE.assemble_mp4_segments([_c1, _c2], _out_mix, pcm_paths=[_side_a, None])
    _src = {a["index"]: a["source"] for a in _rep_mix["alignment"]}
    check("26.24 边车缺失的段自动退回 mp4 解码，且逐段如实标注来源",
          _rep_mix["ok"] and _src == {0: "pcm", 1: "aac"} and _rep_mix.get("pcm_segments") == 1,
          "sources=%s pcm段=%s" % (_src, _rep_mix.get("pcm_segments")))

    # —— 26.25 边车对不上（采样率不同）⇒ 弃用该段边车，不 raise、不炸 ——
    CORE.save_audio({"waveform": torch.zeros(1, 2, 1000), "sample_rate": 44100}, _side_16k,
                    note="unit")
    _out_mm = os.path.join(_TMP26, "film_mismatch.mp4")
    _rep_mm = CORE.assemble_mp4_segments([_c1, _c2], _out_mm, pcm_paths=[_side_16k, _side_b])
    check("26.25 边车采样率对不上 ⇒ 弃用该段边车（退回解码），不抛异常",
          _rep_mm["ok"] and _rep_mm["alignment"][0]["source"] == "aac"
          and _rep_mm["alignment"][1]["source"] == "pcm",
          "%s" % [a["source"] for a in _rep_mm["alignment"]])

    # —— 26.26 边车**轻微**偏短（容差内）⇒ 补静音并出警告（不假装无损） ——
    #   ⚠ 差太多（>100ms）由护栏直接**拒收**、不是补静音 —— 那才是安全行为（见 26.35）。
    _mk_side(_side_short, 700.0, n=_mk_n - 1000)
    _rep_sh = CORE.assemble_mp4_segments([_c1, _c2], os.path.join(_TMP26, "film_short.mp4"),
                                         pcm_paths=[_side_short, None])
    check("26.26 边车比视频短 ⇒ 补静音 + 出警告（缺多少就说多少）",
          _rep_sh["ok"] and any("补静音" in w for w in _rep_sh["warnings"]),
          "warnings=%s" % _rep_sh["warnings"])

    # —— 26.27 段规格不一致时，流拷贝路炸了也要有条走得通的路（退重编码） ——
    _out_spec = os.path.join(_TMP26, "film_spec.mp4")
    _rep_spec = CORE.assemble_mp4_segments([_c1, _c_fps], _out_spec, video="auto")
    check("26.27 规格不一致 ⇒ 体检就拦下（不拼），避免「拷贝不了又硬拼」",
          (not _rep_spec["ok"]) and any("规格" in p for p in _rep_spec["health"]["problems"]),
          "problems=%s" % _rep_spec["health"]["problems"][:1])

    # —— 26.28~26.31 边车的**生产者**：「Trim AV」回的 ui 键 + 纯函数取回 ——
    _opt_t = NODES.H3RelayTrimAV.INPUT_TYPES()["optional"]
    check("26.28 裁重叠新增 save_pcm（默认**开**：只多写一个文件，节点输出与成片逐位不变）",
          "save_pcm" in _opt_t and _opt_t["save_pcm"][1].get("default") is True,
          "save_pcm=%r" % (_opt_t.get("save_pcm"),))
    _img73b = torch.zeros(73, 2, 2, 3)
    _aud73 = {"waveform": torch.zeros(1, 2, 68000), "sample_rate": 32000}
    _r_on = NODES.H3RelayTrimAV().trim(images=_img73b, trim_frames=22, fps=24.0, audio=_aud73,
                                       save_pcm=True)
    _ent = (_r_on.get("ui") or {}).get(CORE.PCM_UI_KEY) if isinstance(_r_on, dict) else None
    # ⚠ subfolder 用宿主自己的分隔符（Windows 上是 `relay_kit\pcm`，与 SaveImage/SaveVideo 一致）
    #   ⇒ 断言前统一成 `/` 再比，免得测试被平台差异卡住。
    check("26.29 裁重叠把 PCM 边车路径回显进 ui（拼接方靠它取回，不猜文件名）",
          bool(_ent) and str(_ent[0]["filename"]).endswith(".safetensors")
          and str(_ent[0]["subfolder"]).replace("\\", "/") == "relay_kit/pcm",
          "ui=%s" % (_ent,))
    _made = os.path.join(_fp26.get_output_directory(), _ent[0]["subfolder"],
                         _ent[0]["filename"]) if _ent else ""
    try:
        if _made and os.path.isfile(_made):
            os.remove(_made)      # 单测不留垃圾在 output 里（删不掉也不该红，见 26.38）
    except OSError:
        pass
    check("26.30 pick_pcm_outputs：从 history.outputs 取回边车；视频/无关项不误收",
          [p["filename"] for p in CORE.pick_pcm_outputs({"7": {CORE.PCM_UI_KEY: _ent}})]
          == [_ent[0]["filename"]]
          and CORE.pick_pcm_outputs({"7": {"images": [{"filename": "a.mp4", "type": "output"}]}}) == []
          and CORE.pick_pcm_outputs(None) == [])

    # —— 26.59~26.62 提交图数据流定序（纯函数）——
    #   🔴 它是"哪份边车真进了 mp4"的**唯一证据**：旧代码靠 class_type 白名单猜顺序，
    #   图里接反就猜错且不报错（2026-09-30 音画错段）。这里锁死它的四条性质。
    _g_chain = {"1": {"class_type": "A", "inputs": {}},
                "2": {"class_type": "B", "inputs": {"x": ["1", 0]}},
                "3": {"class_type": "C", "inputs": {"x": ["2", 0], "y": ["1", 1]}}}
    _r = CORE.graph_downstream_rank(_g_chain, ["1", "2", "3"])
    check("26.59 深度定序：最下游排最前（3 → 2 → 1）",
          list(_r) == ["1", "2", "3"] and _r["3"] < _r["2"] < _r["1"],
          "%s" % (_r,))
    _r_rev = CORE.graph_downstream_rank(_g_chain, ["3", "1"])
    check("26.60 入参顺序不影响结论（只由数据流决定）",
          _r_rev["3"] < _r_rev["1"], "%s" % (_r_rev,))
    check("26.61 没有证据时**返回 None**（不编造顺序）：空图 / 候选不在图里 / 有环",
          CORE.graph_downstream_rank({}, ["1"]) is None
          and CORE.graph_downstream_rank(_g_chain, ["9"]) is None
          and CORE.graph_downstream_rank(
              {"1": {"class_type": "A", "inputs": {"x": ["2", 0]}},
               "2": {"class_type": "B", "inputs": {"x": ["1", 0]}}}, ["1", "2"]) is None)
    check("26.62 连线判据要窄：真值列表（如 images: [\"a.png\",\"b.png\"]）**不许**当连线",
          CORE._link_source(["1", 0]) == "1" and CORE._link_source([1, 2]) == "1"
          and CORE._link_source(["a.png", "b.png"]) is None
          and CORE._link_source(["1"]) is None and CORE._link_source("1") is None
          and CORE._link_source(None) is None)
    _g_tie = {"1": {"class_type": "A", "inputs": {}}, "2": {"class_type": "B", "inputs": {}}}
    check("26.63 并列（两条独立分支）⇒ rank 相等，不硬造先后；稳定排序保持入参序",
          CORE.graph_downstream_rank(_g_tie, ["2", "1"])["2"]
          == CORE.graph_downstream_rank(_g_tie, ["2", "1"])["1"]
          and sorted(["2", "1"],
                     key=lambda n: CORE.graph_downstream_rank(_g_tie, ["2", "1"])[n]) == ["2", "1"])
    # 🔴 26.68 第 2 轮的边界：畸形提交图（`inputs` 不是 dict）**不许抛** —— 它是纯函数，
    #   抛异常会把整条拼接路由打成 500（用户只看到"点了没反应"）。
    check("26.68 畸形节点（inputs 不是 dict / 节点不是 dict）⇒ 当作没有连线，不抛",
          CORE.graph_downstream_rank({"1": {"class_type": "A", "inputs": ["x"]},
                                      "2": {"class_type": "B", "inputs": None},
                                      "3": "not-a-dict"}, ["1", "2", "3"]) == dict.fromkeys("123", 0))
    _r_off = NODES.H3RelayTrimAV().trim(images=_img73b, trim_frames=22, fps=24.0, audio=_aud73,
                                        save_pcm=False)
    check("26.31 save_pcm=False ⇒ 不写边车，返回退化成老的四元组（旧图行为不变）",
          not isinstance(_r_off, dict) and len(_r_off) == 4,
          "%r" % (type(_r_off),))

    # —— 26.35~26.37 护栏：边车必须与**本段落盘音频同源**（音频链上还有别的处理时） ——
    #   为什么必须锁：产线接线是「裁重叠 → **音频缝** → 落盘」。音频缝若开 patch / J-cut，
    #   真进 mp4 的是**它的**输出；拿裁重叠那份边车去拼 = 绕过音频缝（J-cut 下还会错位）。
    #   护栏靠**长度**判同源，不靠接线假设。
    _side_short2 = os.path.join(_TMP26, "pcm_jcut.safetensors")
    _mk_side(_side_short2, 500.0, n=64000 - 28800)          # 模拟 J-cut：短 align(0.9s)
    _rep_jc = CORE.assemble_mp4_segments([_c1, _c2], os.path.join(_TMP26, "film_jcut.mp4"),
                                         pcm_paths=[_side_short2, None])
    check("26.35 边车比 mp4 音频短 ~0.9s（J-cut 型）⇒ 拒收、退回解码并出警告",
          _rep_jc["ok"] and _rep_jc["alignment"][0]["source"] == "aac"
          and any("拒收" in w for w in _rep_jc["warnings"]),
          "src=%s warns=%s" % (_rep_jc["alignment"][0]["source"], _rep_jc["warnings"][:1]))

    _side_good = os.path.join(_TMP26, "pcm_ok.safetensors")
    _mk_side(_side_good, 321.0)
    _rep_2c = CORE.assemble_mp4_segments([_c1, _c2], os.path.join(_TMP26, "film_2cand.mp4"),
                                         pcm_paths=[[_side_short2, _side_good], None])
    check("26.36 多候选 ⇒ 过护栏的那份被采用（不是「先来先用」）",
          _rep_2c["ok"] and _rep_2c["alignment"][0]["source"] == "pcm"
          and _rep_2c["alignment"][0]["pcm_file"] == os.path.basename(_side_good),
          "pick=%s" % (_rep_2c["alignment"][0]["pcm_file"],))

    # —— 26.57 🔴 本组最核心的回归：**并列同源候选时取「列表第一个」**（图序优先），
    #   不再取"与 mp4 音频长度最接近"的那个 —— 长度不是证据，数据流才是。
    #   真实事故：两份候选长度**完全相同**（段 2 的未裁音频覆盖了段 1 的床文件）
    #   ⇒ 护栏全放行 ⇒ 旧的 `min(|len − n_cont|)` 选中了错的那份 ⇒ 成片音画错段。
    #   这里造一个"长度能分辨、但会分辨错"的对照：B 更接近容器音频，A 才是下游那份。
    _side_prio_a = os.path.join(_TMP26, "pcm_prio_a.safetensors")
    _side_prio_b = os.path.join(_TMP26, "pcm_prio_b.safetensors")
    _mk_side(_side_prio_a, 111.0, n=_mk_n + 2500)      # 离容器音频 1444 样本
    _mk_side(_side_prio_b, 222.0, n=_mk_n + 500)       # 离容器音频  556 样本（旧的会选它）
    _rep_prio = CORE.assemble_mp4_segments([_c1, _c2], os.path.join(_TMP26, "film_prio.mp4"),
                                           pcm_paths=[[_side_prio_a, _side_prio_b], None])
    _al0 = _rep_prio["alignment"][0]
    check("26.57 并列同源候选 ⇒ 取**列表第一个**（提交图里最下游），不按长度最接近挑",
          _al0["source"] == "pcm" and _al0["pcm_file"] == os.path.basename(_side_prio_a),
          "pick=%s" % (_al0["pcm_file"],))
    check("26.58 并列时**落选的那份写进报告**（选错不再静默）",
          any(os.path.basename(_side_prio_b) in s and "并列" in s
              for s in _al0.get("pcm_others", [])),
          "others=%r" % (_al0.get("pcm_others"),))
    # 🔴 26.67 第 1 轮的可观测性：**读不出的候选**在"另一份能用"时也必须说出来 ——
    #   静默丢一个候选 = 把"配错段"的唯一线索抹掉。
    _rep_broken = CORE.assemble_mp4_segments(
        [_c1, _c2], os.path.join(_TMP26, "film_brokencand.mp4"),
        pcm_paths=[[_side_16k, _side_good], None])
    _al_b = _rep_broken["alignment"][0]
    check("26.67 有候选读不出、但另一份可用 ⇒ 仍写进报告（不静默丢）",
          _al_b["source"] == "pcm" and _al_b["pcm_file"] == os.path.basename(_side_good)
          and any(os.path.basename(_side_16k) in s for s in _al_b.get("pcm_others", [])),
          "pick=%s others=%r" % (_al_b["pcm_file"], _al_b.get("pcm_others")))
    check("26.37 单字符串形式仍受支持（旧调用方兼容）",
          CORE.assemble_mp4_segments([_c1, _c2], os.path.join(_TMP26, "film_str.mp4"),
                                     pcm_paths=[_side_good, None])["alignment"][0]["source"]
          == "pcm")

    # —— 26.38~26.39 音频缝也落边车（它输出才是真进 mp4 的那份） ——
    _seam_cls = NODES.H3RelayAudioSeam
    _seam_aud = {"waveform": torch.zeros(1, 2, 64000), "sample_rate": 32000}
    _sr_ret = _seam_cls().seam(_seam_aud, "relay", 0, 0.0)
    _sr_ent = (_sr_ret.get("ui") or {}).get(CORE.PCM_UI_KEY) if isinstance(_sr_ret, dict) else None
    check("26.38 音频缝也回显 PCM 边车（它落的那份 = 送进 mp4 的同一份音频）",
          bool(_sr_ent) and str(_sr_ent[0]["filename"]).startswith("audio_")
          and str(_sr_ent[0]["subfolder"]).replace("\\", "/").endswith("relay"),
          "ui=%s" % (_sr_ent,))
    # ⚠ 清理要**容错**：Windows 上刚写完的文件偶发被索引/杀软短暂占用（PermissionError）。
    #   单测不该因为"删不掉临时文件"而红 —— 这不是被测行为。
    for _f in (_sr_ent or []):
        _pp = os.path.join(_fp26.get_output_directory(), _f["subfolder"], _f["filename"])
        try:
            if os.path.isfile(_pp):
                os.remove(_pp)
        except OSError:
            pass
    _raw_dump = os.path.join(_fp26.get_output_directory(), "relay_kit", "relay",
                             "audio_raw_00000.safetensors")
    try:
        if os.path.isfile(_raw_dump):
            os.remove(_raw_dump)
    except OSError:
        pass
    check("26.39 音频缝边车路径能被 pick_pcm_outputs 取回",
          [p["filename"] for p in CORE.pick_pcm_outputs({"18": {CORE.PCM_UI_KEY: _sr_ent}})]
          == [_sr_ent[0]["filename"]])

    # —— 26.41~26.42 PyAV 版本兼容（开源用户环境差异）：缺模板流 ⇒ 明确报错 + 自动退重编码 ——
    #   为什么在意：画面流拷贝靠 `add_stream_from_template`（av>=17）。别的用户机器上 av 可能更老；
    #   我们不写"手工复制参数"的未验证退路（那会产出能播但内容错的片），而是**报错 + 退回重编码**，
    #   并让四项断言把关 —— 用户看到的是"能出片 + 一句为什么"，不是崩。
    class _NoTemplateOut:
        def add_stream(self, *a, **k):      # 老 API 上没有模板流，只有这个
            raise AssertionError("不该走到 add_stream（这里只验报错分支）")

    try:
        CORE._passthrough_video_stream(_NoTemplateOut(), None, _av26)
        check("26.41 老 PyAV 缺模板流 ⇒ 抛可照做的错（指明 pip 修法）", False, "没抛")
    except RuntimeError as _e41:
        check("26.41 老 PyAV 缺模板流 ⇒ 抛可照做的错（指明 pip 修法）",
              "add_stream_from_template" in str(_e41) and "pip install -U" in str(_e41),
              str(_e41).splitlines()[0][:64])

    _orig_pass26 = CORE._passthrough_video_stream
    CORE._passthrough_video_stream = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("模拟：本机 PyAV 太旧（缺 add_stream_from_template）"))
    try:
        _rep_fb = CORE.assemble_mp4_segments([_c1, _c2], os.path.join(_TMP26, "film_fb.mp4"),
                                             video="auto")
    finally:
        CORE._passthrough_video_stream = _orig_pass26
    check("26.42 拷贝路不可用 ⇒ 自动退单遍重编码（仍过四项断言 + 报告写明原因）",
          _rep_fb["ok"] and _rep_fb.get("mode") == "encode"
          and any("退回单遍重编码" in ln for ln in _rep_fb["report"].splitlines()),
          "mode=%s ok=%s" % (_rep_fb.get("mode"), _rep_fb.get("ok")))

    # —— 26.43~26.44 CLI 入口（API / 无头用户的非画布路径）**真跑** ——
    #   为什么放在核心单测里：CI 只跑本文件 ⇒ 不必再给 CI 加一步；而且它与画布按钮共用同一份核心，
    #   一起跑才能保证"两条路永远一样"。CLI 是用户入口，不是内部包，**必须冒烟**。
    _spec_cli = importlib.util.spec_from_file_location(
        "concat_cli26", os.path.join(_KIT_DIR, "tools", "concat_segments.py"))
    _cli = importlib.util.module_from_spec(_spec_cli)
    _spec_cli.loader.exec_module(_cli)
    _out_cli = os.path.join(_TMP26, "cli.mp4")
    _rc_ok = _cli.main([_c1, _c2, "-o", _out_cli, "--json", "--audio", "aac192"])
    check("26.43 CLI（tools/concat_segments.py）真能出片：退出码 0 + 文件存在",
          _rc_ok == 0 and os.path.isfile(_out_cli) and os.path.getsize(_out_cli) > 1000,
          "rc=%s exists=%s" % (_rc_ok, os.path.exists(_out_cli)))
    _rc_bad = _cli.main([os.path.join(_TMP26, "nope_a.mp4"), os.path.join(_TMP26, "nope_b.mp4"),
                         "-o", os.path.join(_TMP26, "cli_bad.mp4"), "--json"])
    check("26.44 CLI 对坏输入 **非零退出**（脚本能据此判失败，不会误当成功）",
          _rc_bad == 1, "rc=%s" % (_rc_bad,))

    # —— 26.14 断言快路（包级 demux + 排序）不许说谎：与深路（逐帧解码）结论必须一致 ——
    _fast14 = CORE.assert_assembled(_out26, 144, 24.0, deep=False)
    _deep14 = CORE.assert_assembled(_out26, 144, 24.0, deep=True)
    # —— 26.15~26.17 后端拼接路由的**段发现逻辑**（纯函数级：stub 掉 server，不启 ComfyUI） ——
    #   这是整条自动拼接链里最容易悄悄错的一环（"到底拼了哪几段"），必须锁住。
    try:
        import types as _types

        class _Routes:
            """装饰器透传 stub：注册路由时用哪个 HTTP 方法都认。

            ⚠ 别写死方法名 —— 2026-09-25 踩过：新增 `GET /h3relay/health` 后，
            原来只实现 `post` 的 stub 直接 `AttributeError` ⇒ 26.15 假红。
            用 `__getattr__` 兜底，以后加任何方法（get/put/delete…）都不用回来改这里。
            """
            def __getattr__(self, _name):
                def _reg(_path):
                    def _deco(fn):
                        return fn
                    return _deco
                return _reg

        class _FakePS:
            routes = _Routes()
            instance = None
            prompt_queue = None

        _FakePS.instance = _FakePS()
        _srvmod = _types.ModuleType("server")
        _srvmod.PromptServer = _FakePS
        sys.modules["server"] = _srvmod

        _spec = importlib.util.spec_from_file_location(
            "h3latentrelay_pkg", os.path.join(_KIT_DIR, "__init__.py"),
            submodule_search_locations=[_KIT_DIR])
        _pk = importlib.util.module_from_spec(_spec)
        sys.modules["h3latentrelay_pkg"] = _pk
        _spec.loader.exec_module(_pk)

        class _FakeQueue:
            def __init__(self, hist):
                self.hist = hist

            def get_history(self, prompt_id=None, max_items=None):
                if prompt_id is not None:
                    return {prompt_id: self.hist[prompt_id]} if prompt_id in self.hist else {}
                return dict(self.hist)

        def _entry(name):
            return {"prompt": [None, None, {"7": {"class_type": "H3RelayChain"}}],
                    "outputs": {
                        "31": {"images": [{"filename": name, "subfolder": "relay_kit/x",
                                           "type": "output"}]},
                        # 「Trim AV」回显的 PCM 边车（拼接路由靠它取回无损音频）
                        "18": {CORE.PCM_UI_KEY: [{"filename": name.replace(".mp4", ".safetensors"),
                                                  "subfolder": "relay_kit/pcm",
                                                  "type": "output"}]},
                    }}

        _hist = {"p1": _entry("a.mp4"), "p2": _entry("b.mp4"),
                 "p9": {"prompt": [None, None, {"5": {"class_type": "KSampler"}}],
                        "outputs": {}}}
        _q = _FakeQueue(_hist)

        _pairs, _n1, _m1 = _pk._pick_segments(_q, [(0, "p1"), (1, "p2")], "7", 0)
        check("26.15 按段号精确取段（段号=位置、能落到文件路径）",
              [p[0] for p in _pairs] == [1, 2] and [p[1] for p in _pairs] == ["p1", "p2"]
              and _m1 == [] and _pk._mp4_paths(_pairs[0][2])[0].endswith("a.mp4"),
              "pairs=%s path=%s" % ([(p[0], p[1]) for p in _pairs],
                                    _pk._mp4_paths(_pairs[0][2])[:1]))

        # 🔴 26.50 本组最核心的回归：**段号不许被压实**（2026-09-30 段序错乱的根因）。
        #   旧实现把稀疏 id 列表压紧 ⇒ 第 3 段的 id 顶到第 2 段槽 ⇒ 拼出来的片段序是错的。
        #   现在：段号原样带出，缺的那段单列在 `missing` 里。
        _p50, _n50, _m50 = _pk._pick_segments(_q, [(0, "p1"), (1, None), (2, "p2")], "7", 0)
        check("26.50 中间缺一段 ⇒ **不压实**：段 1、3 仍是 1、3，且报出缺第 2 段",
              [p[0] for p in _p50] == [1, 3] and _m50 == [2] and "第 2 段" in _n50,
              "stages=%s missing=%s note=%s" % ([p[0] for p in _p50], _m50, _n50))

        # 26.49 段文件不在盘上时，路由必须**立刻报**（不进入编码 ⇒ 不白等）
        _e_missing = {"prompt": [None, None, {"7": {"class_type": "H3RelayChain"}}],
                      "outputs": {"31": {"images": [{"filename": "gone.mp4", "subfolder": "",
                                                     "type": "output"}]}}}
        _paths = _pk._mp4_paths(_e_missing)
        check("26.49 路由能取出路径（存在性由拼之前的核对负责，避免「找不到还静默拼半条」）",
              len(_paths) == 1 and str(_paths[0]).replace("\\", "/").endswith("gone.mp4"),
              "%s" % (_paths,))

        _p2, _n2, _m2 = _pk._pick_segments(_q, [], "7", 5)
        check("26.16 没给段记录时回扫 history：只认「图里带本 Chain 节点」的提交",
              [p[1] for p in _p2] == ["p1", "p2"] and "2 次" in _n2, _n2)

        _p3, _n3, _m3 = _pk._pick_segments(_q, [], "7", 0)
        check("26.17 既没段记录、count 也 ≤ 0 ⇒ 明确说「不知道该拼哪几段」（不瞎拼）",
              _p3 == [] and _m3 == [] and "不知道该拼哪几段" in _n3, _n3)

        _p4, _n4, _m4 = _pk._pick_segments(_q, [(0, "nope")], "7", 0)
        check("26.18 给了 history 里没有的 prompt_id ⇒ 命中 0，并**报出缺第 1 段**（不静默丢）",
              _p4 == [] and _m4 == [1] and "0/1 段命中" in _n4, "%s ｜ missing=%s" % (_n4, _m4))

        # 26.51 回扫路也要按**真段号**排（不能只按时间序）—— `_chain_stage` 从提交图里读
        _hist2 = {
            "q1": {"prompt": [None, None, {"7": {"class_type": "H3RelayChain",
                                                 "inputs": {"stage_index": 2}}}],
                   "outputs": {}},
            "q2": {"prompt": [None, None, {"7": {"class_type": "H3RelayChain",
                                                 "inputs": {"stage_index": 0}}}],
                   "outputs": {}},
        }
        _p5, _n5, _m5 = _pk._pick_segments(_FakeQueue(_hist2), [], "7", 5)
        check("26.51 回扫按**真段号**排（history 里后写的 stage 0 也要排到前面）+ 报出缺第 2 段",
              [p[0] for p in _p5] == [1, 3] and _m5 == [2],
              "stages=%s missing=%s" % ([p[0] for p in _p5], _m5))
        check("26.52 请求里的 stages 归一化：按段号升序、畸形项丢掉、空串算「无记录」",
              _pk._normalize_stages({"stages": [{"stage": 3, "prompt_id": "c"},
                                                {"stage": 1, "prompt_id": ""},
                                                {"stage": "x", "prompt_id": "z"},
                                                "nope",
                                                {"stage": -1, "prompt_id": "d"}]})
              == [(1, None, None), (3, "c", None)],
              "%s" % (_pk._normalize_stages({"stages": [{"stage": 3, "prompt_id": "c"},
                                                        {"stage": 1, "prompt_id": ""},
                                                        {"stage": "x", "prompt_id": "z"},
                                                        "nope",
                                                        {"stage": -1, "prompt_id": "d"}]}),))
        check("26.53 段号区间缺口的判定：有 None（段号读不出来）⇒ 不瞎报缺口",
              _pk._stage_gaps([0, 2]) == [2] and _pk._stage_gaps([0, 1, 2]) == []
              and _pk._stage_gaps([0, None]) == [] and _pk._stage_gaps([]) == [])
        # 🔴 26.64 第 2 轮的边界：段号来自请求 ⇒ 畸形值不许把 `range` 撑爆（跨度上限 = _MAX_SEG）
        check("26.64 段号跨度 ≥ _MAX_SEG（畸形值）⇒ 判「无从判」，不建天文数字的 range",
              _pk._stage_gaps([0, 10 ** 9]) == []
              and _pk._stage_gaps([0, _pk._MAX_SEG]) == []
              and _pk._stage_gaps([0, _pk._MAX_SEG - 1]) == list(range(2, _pk._MAX_SEG)),
              "MAX_SEG=%d" % _pk._MAX_SEG)
        # 🔴 26.65 第 1 轮的语义：同一段号重复出现 ⇒ 后一次覆盖前一次（与前端 stageIds[k]=… 同语义），
        #   否则同一段会被拼两次。
        check("26.65 同段号重复 ⇒ 去重（后一次覆盖前一次），不拼两遍",
              _pk._normalize_stages({"stages": [{"stage": 0, "prompt_id": "old"},
                                                {"stage": 1, "prompt_id": "b"},
                                                {"stage": 0, "prompt_id": "new"}]})
              == [(0, "new", None), (1, "b", None)],
              "%s" % (_pk._normalize_stages({"stages": [{"stage": 0, "prompt_id": "old"},
                                                        {"stage": 1, "prompt_id": "b"},
                                                        {"stage": 0, "prompt_id": "new"}]}),))
        check("26.66 seq 原样带出（路由用它写「哪几段来自更早一轮」）",
              _pk._normalize_stages({"stages": [{"stage": 0, "prompt_id": "a", "seq": 7}]})
              == [(0, "a", 7)])

        # 26.32~26.34 音轨档映射与「每段边车」的取回（纯函数级）
        check("26.32 音轨档映射：默认 aac 256k；192k 可覆盖；无损档 = PCM f32（无码率参数）",
              _pk.AUDIO_OUT[_pk._DEFAULT_AUDIO_OUT] == ("aac", "256k")
              and _pk.AUDIO_OUT["aac_192k"] == ("aac", "192k")
              and _pk.AUDIO_OUT["pcm_lossless"][0] in CORE.LOSSLESS_AUDIO_CODECS,
              "AUDIO_OUT=%s" % (_pk.AUDIO_OUT,))
        # 期望路径**按宿主的真实输出目录推**（本文件 26.3/26.4 段同此约定）：
        #   写死**作者机器的输出目录**会让别的用户/Linux CI 必然假红 —— 代码没错，是尺子绑了机器。
        _pcm_expect = os.path.join(_fp26.get_output_directory(),
                                   "relay_kit", "pcm", "a.safetensors").replace("\\", "/")
        _c33, _n33 = _pk._pcm_candidates(_p2[0][2])
        check("26.33 路由能从 history 取回**该段的 PCM 边车**候选（不猜文件名）",
              [str(x).replace("\\", "/") for x in _c33] == [_pcm_expect] and _n33 == "",
              "cands=%s 期望=%s" % (_c33, _pcm_expect))
        check("26.34 老提交没有边车 ⇒ 候选为空（拼接自动退回 mp4 解码，不报错）",
              _pk._pcm_candidates(_hist["p9"]) == ([], "")
              and _pk._pcm_candidates({}) == ([], ""))

        # 26.40/26.54 音频链上有**多个**本包音频节点时：候选按**提交图的数据流**排
        #   （谁在下游谁优先）。🔴 顺序来自图、不来自 class_type 白名单 ——
        #   2026-09-30 事故就是白名单猜的顺序与图里接的相反 ⇒ 拿错边车 ⇒ 音画错段。
        def _multi(in_trim=None, in_seam=None):
            """两个音频节点（13=裁重叠、18=音频缝）+ 各自落一份边车；连线由参数给。"""
            return {
                "prompt": [None, None, {
                    "13": {"class_type": "H3RelayTrimAV", "inputs": dict(in_trim or {})},
                    "18": {"class_type": "H3RelayAudioSeam", "inputs": dict(in_seam or {})},
                }],
                "outputs": {
                    "13": {CORE.PCM_UI_KEY: [{"filename": "seg_00001_.safetensors",
                                              "subfolder": "relay_kit/pcm", "type": "output"}]},
                    "18": {CORE.PCM_UI_KEY: [{"filename": "audio_00001.safetensors",
                                              "subfolder": "relay_kit/relay", "type": "output"}]},
                },
            }

        check("26.46 路由取路径优先用节点自报 abs_path，缺了才按 type+subfolder 拼",
              _pk._abs_of({"filename": "a.mp4", "subfolder": "", "type": "output",
                           "abs_path": r"D://outside_out//a.mp4"})
              == r"D://outside_out//a.mp4"
              and _pk._abs_of({"filename": "b.mp4", "subfolder": "relay_kit/x",
                               "type": "output"}).replace("\\", "/")
              .endswith("/output/relay_kit/x/b.mp4"),
              "abs=%s" % (_pk._abs_of({"filename": "b.mp4", "subfolder": "relay_kit/x",
                                       "type": "output"}),))

        _cands, _ = _pk._pcm_candidates(_multi(in_seam={"audio": ["13", 0]}))
        check("26.40 图里 裁重叠(13) → 音频缝(18) ⇒ 音频缝（下游）排在裁重叠之前",
              len(_cands) == 2 and _cands[0].replace("\\", "/").endswith("relay/audio_00001.safetensors")
              and _cands[1].replace("\\", "/").endswith("pcm/seg_00001_.safetensors"),
              "cands=%s" % (_cands,))
        _cands_rev, _ = _pk._pcm_candidates(_multi(in_trim={"audio": ["18", 0]}))
        check("26.54 🔴 图里**接反**了（音频缝 → 裁重叠）⇒ 顺序跟着图走（裁重叠优先），"
              "不是跟着 class_type 白名单",
              len(_cands_rev) == 2
              and _cands_rev[0].replace("\\", "/").endswith("pcm/seg_00001_.safetensors")
              and _cands_rev[1].replace("\\", "/").endswith("relay/audio_00001.safetensors"),
              "cands=%s" % (_cands_rev,))
        # 🔴 多候选但**读不出图** ⇒ 拒收全部（宁可退回 mp4 解码，也绝不配错段）
        _e_nograph = {"prompt": None,
                      "outputs": _multi(in_seam={"audio": ["13", 0]})["outputs"]}
        _cn, _nn = _pk._pcm_candidates(_e_nograph)
        check("26.55 多候选但提交图读不出来 ⇒ **全部拒收**（安全侧）且写明原因",
              _cn == [] and "拒收" in _nn, "cands=%s note=%s" % (_cn, _nn))
        _e_cycle = _multi(in_trim={"audio": ["18", 0]}, in_seam={"audio": ["13", 0]})
        check("26.56 图有环（读不出下游）⇒ 同样拒收，不给假顺序",
              _pk._pcm_candidates(_e_cycle)[0] == [])
    except Exception as _e26:
        check("26.15 路由发现逻辑可离线加载（stub server）", False, repr(_e26))

    check("26.14 断言快路（包级）与深路（逐帧解码）结论一致",
          _fast14["frames"] == _deep14["frames"] == 144
          and _fast14["pts_contiguous"] and _deep14["pts_contiguous"]
          and _fast14["dts_strictly_increasing"] and _deep14["dts_strictly_increasing"],
          "fast=%d deep=%d" % (_fast14["frames"], _deep14["frames"]))

