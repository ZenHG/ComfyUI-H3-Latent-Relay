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

# ============================ 27 · 画质域 AV latent 分块放大 ============================
# 上游学习式 3D 放大器只吃普通 [B,C,T,H,W]（接不住 NestedTensor）且分块写死 32 帧，
# 故本包自持「块数自选 + 重叠加权拼接」。fake 放大器 = nearest ×2（逐帧独立 ⇒ 可求解析解）。

def _sr2(seg):
    return torch.nn.functional.interpolate(seg, scale_factor=(1, 2, 2), mode="nearest")


_T27, _H27, _W27 = 57, 8, 6
_v27 = (torch.linspace(0.0, 1.0, _T27).view(1, 1, _T27, 1, 1)
        * torch.linspace(0.0, 1.0, _W27 * _H27).view(1, 1, 1, _H27, _W27)).to(torch.float32)

_p1 = CORE.upscale_chunk_plan(_T27, chunks=1)
_p2 = CORE.upscale_chunk_plan(_T27, chunks=2)
_p9 = CORE.upscale_chunk_plan(_T27, chunks=9, overlap=0)   # 9 块要 overlap=0 才合规（每块 7 帧 < 2*5+1）
check("27.1 块数→块长：chunks=1→1 块；=2→29 帧/2 块；=9→7 帧/9 块（ceil 不多出空块）",
      (_p1["n_chunks"], _p1["chunk_frames"]) == (1, _T27)
      and (_p2["n_chunks"], _p2["chunk_frames"]) == (2, 29)
      and _p9["n_chunks"] == 9 and _p9["chunk_frames"] == 7,
      "p1=%s p2=%s p9=%s/%s" % (_p1["n_chunks"], _p2["chunk_frames"], _p9["n_chunks"], _p9["chunk_frames"]))

check("27.2 spans 无缝覆盖：首块从 0 起、末块到 T 止、相邻块的 out_start == 前块 out_end 之前（有重叠）或 == 前块 out_end（overlap=0）",
      _p2["spans"][0]["out_start"] == 0 and _p2["spans"][-1]["out_end"] == _T27
      and all(b["out_start"] <= a["out_end"] for a, b in zip(_p2["spans"], _p2["spans"][1:], strict=False)))

_ov0 = CORE.upscale_chunk_plan(_T27, chunks=4, overlap=0)
check("27.3 overlap=0 ⇒ 各块正好平铺（Σ 帧数 == T，blend 全 0）",
      sum(s["out_end"] - s["out_start"] for s in _ov0["spans"]) == _T27
      and all(s["blend_head"] == 0 and s["blend_tail"] == 0 for s in _ov0["spans"]))

check("27.4 合法最大块数 = T // (2·overlap+1)：T=57 overlap=5 → 5",
      CORE.max_upscale_chunks(57, 5) == 5 and CORE.max_upscale_chunks(57, 0) == 57)

expect_raise("27.5 超上限的块数必须 raise 并给出合法上限",
             lambda: CORE.upscale_chunk_plan(57, chunks=6, overlap=5), "最大合法块数 = 5")
expect_raise("27.6 chunks<1 / overlap<0 / total<1 一律 raise（不静默改参数）",
             lambda: CORE.upscale_chunk_plan(57, chunks=0))

_o1 = CORE.temporal_tile_upscale(_v27, _sr2, _p1)
_whole = _sr2(_v27)
check("27.7 单块 == 整段直接放大（恒等，逐位）", torch.equal(_o1, _whole),
      "max|d|=%.3e" % float((_o1 - _whole).abs().max()))

_chunked = CORE.temporal_tile_upscale(_v27, _sr2, CORE.upscale_chunk_plan(_T27, chunks=4))
check("27.8 分 4 块 == 整段一次过（nearest 逐帧独立 ⇒ 线性加权必须精确复原）",
      _chunked.shape == _whole.shape
      and float((_chunked - _whole).abs().max()) < 1e-4,
      "shape=%s max|d|=%.3e" % (tuple(_chunked.shape), float((_chunked - _whole).abs().max())))

check("27.9 只放大空间、时间维不动：T 保持 57、H/W 翻倍",
      tuple(_chunked.shape) == (1, 1, _T27, _H27 * 2, _W27 * 2), str(tuple(_chunked.shape)))

expect_raise("27.10 放大器改变帧数 ⇒ 当场 raise（否则拼接静默错位）",
             lambda: CORE.temporal_tile_upscale(
                 _v27, lambda s: s[:, :, :-1], CORE.upscale_chunk_plan(_T27, chunks=3)),
             "改变了帧数")
expect_raise("27.11 plan 与 latent 帧数不符 ⇒ raise（不许拿错尺子拼）",
             lambda: CORE.temporal_tile_upscale(_v27[:, :, :20], _sr2, _p2), "与 latent 的")
expect_raise("27.12 秩不对（3D）⇒ raise",
             lambda: CORE.temporal_tile_upscale(_v27.squeeze(0), _sr2,
                                                 CORE.upscale_chunk_plan(_T27, chunks=2)))

_s4 = _v27[:, :, 0]                                  # [B,C,H,W] 单帧
check("27.15 4D 单帧进 → 4D 出（与上游同口径），且空间确实翻倍",
      tuple(_s4.shape) == (1, 1, _H27, _W27)
      and tuple(CORE.temporal_tile_upscale(_s4, _sr2,
              CORE.upscale_chunk_plan(1, chunks=1, overlap=0)).shape) == (1, 1, _H27 * 2, _W27 * 2))

check("27.13 上游口径常量锁死（换实现不许换接缝）：overlap=5(=temporal_kernel) / 默认块长=32",
      CORE.UPSCALE_OVERLAP_DEFAULT == 5 and CORE.UPSCALE_CHUNK_FRAMES_DEFAULT == 32)

_r27 = CORE.upscale_chunk_plan(_T27, chunks=3)
check("27.14 plan 回报字段齐备（给用户核数值：requested/实际块数/块长/overlap）",
      all(k in _r27 for k in ("requested_chunks", "n_chunks", "chunk_frames", "overlap", "spans"))
      and _r27["requested_chunks"] == 3 and _r27["n_chunks"] == 3)


# --- 适配层本身：注入假上游类，验拆包 / 回包 / 音频守恒 / 报错文案 ---
#     ⚠ 不打 ComfyUI 注册表（测试环境里 `import nodes` 会绑到本包自己的 nodes.py，
#        正是 `_comfy_registry()` 要防的影子情形）⇒ 直接换掉本包那个接缝函数。


class _FakeUp:
    """替身上游放大节点：只做 nearest ×2（时间维不动），并记录被怎么调用。"""
    calls = []

    @classmethod
    def execute(cls, latent, model_name, mode, align, enable_temporal_chunking,
                force_unload, device, precision):
        cls.calls.append({"model_name": model_name, "mode": mode, "align": align,
                              "chunking": enable_temporal_chunking, "device": device})
        s = latent["samples"]
        return ({"samples": torch.nn.functional.interpolate(s, scale_factor=(1, 2, 2), mode="nearest")},)


_REG = {"MinimaxH3LatentUpscaler3D": _FakeUp}
_ORIG_REGFN = NODES._comfy_registry
NODES._comfy_registry = lambda: _REG
try:
    _lat27 = av_latent(24, a_ticks=16, seed=7, w=6, h=8)
    _a0 = CORE.audio_from_latent(_lat27).clone()
    _FakeUp.calls.clear()
    _out27, _rep27 = NODES.H3RelayLatentUpscale().upscale(
        _lat27, "fake_h3_3d.safetensors", "scale by multiplier", 2.0, 1280, 704, 1.0,
        chunks=2, overlap=5, align=32, precision="fp32", device="cpu", force_unload=False)
    _v27b = CORE.video_from_latent(_out27)
    _a27b = CORE.audio_from_latent(_out27)
    check("27.16 适配器：拆包→放大→回包双流，音频**逐位不变**、T 不变、宽高翻倍",
          torch.equal(_a27b, _a0) and tuple(_v27b.shape) == (1, 4, 24, 16, 12),
          "video=%s audio=%s" % (tuple(_v27b.shape), tuple(_a27b.shape)))
    check("27.17 分块由本包做 ⇒ 传上游必须 enable_temporal_chunking=False（调了 2 次 = 2 块）",
          len(_FakeUp.calls) == 2 and all(c["chunking"] is False for c in _FakeUp.calls)
          and _FakeUp.calls[0]["mode"] == {"mode": "scale by multiplier", "scale": 2.0}
          and _FakeUp.calls[0]["model_name"] == "fake_h3_3d.safetensors",
          str(_FakeUp.calls))
    check("27.18 report 数字自证（请求/实际块数、T、音频形状都在）",
          "块数 请求2→实际2" in _rep27 and "T=24" in _rep27 and "(1, 2, 2, 16)" in _rep27,
          _rep27[:120])
    expect_raise("27.19 非法块数 ⇒ raise 且报错给出最大合法块数（绝不偷改参数）",
                 lambda: NODES.H3RelayLatentUpscale().upscale(
                     _lat27, "fake", "scale by multiplier", 2.0, 1280, 704, 1.0,
                     chunks=5, overlap=5, align=32, precision="fp32", device="cpu", force_unload=False),
                 "最大合法块数")
    NODES._comfy_registry = dict
    expect_raise("27.20 未装上游 ⇒ 报错把作者仓库与 HF 权重地址都写出来（不静默、不假成功）",
                 lambda: NODES.H3RelayLatentUpscale().upscale(
                     _lat27, "fake", "scale by multiplier", 2.0, 1280, 704, 1.0,
                     chunks=1, overlap=5, align=32, precision="fp32", device="cpu", force_unload=False),
                 "huggingface.co/LBH-123-AI")
finally:
    NODES._comfy_registry = _ORIG_REGFN


print()
print("=" * 78)
print("28) 音频出口 dtype 契约：交出去的 AUDIO 必须收敛到 float32")
print("=" * 78)
# 背景（0.6.11）：本包音频是 fp16，而 `VHS_VideoCombine` 合成音轨时写死 `-f f32le` 且不转 dtype
# ⇒ fp16 字节被当 f32 解读 ⇒ 编码后逐样本全零（画面/边车/日志都正常，只有成片没声音）。
# 本组同时锁「出口契约」与「事故复现」——后者防有人在别处又 `return audio` 原始 dtype。
import numpy as _np28

_a16 = {"waveform": torch.rand(1, 2, 40000) * 0.3, "sample_rate": 32000}
_a16["waveform"] = _a16["waveform"].to(torch.float16)
_a32 = {"waveform": _a16["waveform"].to(torch.float32), "sample_rate": 32000}

_r32 = CORE.audio_to_fp32(_a32)
check("28.1 已是 f32 ⇒ 返回**原对象**（零拷贝、零开销；保住「直通不改」的既有语义）",
      _r32 is _a32)
_r16 = CORE.audio_to_fp32(_a16)
check("28.2 fp16 ⇒ 波形转 f32，且与 `.float()` **逐位相等**（加宽无损，不改值）",
      _r16["waveform"].dtype == torch.float32
      and torch.equal(_r16["waveform"], _a16["waveform"].float()),
      "%s → %s" % (_a16["waveform"].dtype, _r16["waveform"].dtype))
check("28.3 其余键与形状原样保留（sample_rate / 前导维都不动）",
      int(_r16["sample_rate"]) == 32000
      and tuple(_r16["waveform"].shape) == tuple(_a16["waveform"].shape)
      and set(_r16.keys()) == set(_a16.keys()),
      "shape=%s keys=%s" % (tuple(_r16["waveform"].shape), sorted(_r16.keys())))
check("28.4 None ⇒ None（图里 audio 是可选输入，这里不许 raise）",
      CORE.audio_to_fp32(None) is None)
_b16 = {"waveform": (torch.rand(1, 1, 100) * 0.05).to(torch.bfloat16), "sample_rate": 32000}
_f16 = {"waveform": (torch.rand(1, 1, 100) * 0.05).to(torch.float64), "sample_rate": 32000}
check("28.5 其它浮点 dtype（bf16 / f64）也一律收敛到 f32",
      CORE.audio_to_fp32(_b16)["waveform"].dtype == torch.float32
      and CORE.audio_to_fp32(_f16)["waveform"].dtype == torch.float32)
check("28.6 非张量 / 缺键不在这里拦（交给下游自己报，保持函数单一职责）",
      CORE.audio_to_fp32({"sample_rate": 32000}) == {"sample_rate": 32000})

# —— 🔴 事故复现锁：逐字模拟 VHS 的封装路（`-f f32le` + 不转 dtype）——
def _vhs_mux_bytes(a):  # (VHS nodes.py 的原式)
    return a["waveform"].squeeze(0).transpose(0, 1).numpy().tobytes()

_bad = _np28.frombuffer(_vhs_mux_bytes(_a16), dtype="<f4").reshape(-1, 2)
_good = _np28.frombuffer(_vhs_mux_bytes(_r16), dtype="<f4").reshape(-1, 2)
check("28.7 ⛔ 事故复现：fp16 直接进 VHS ⇒ 峰值掉到 ~1e-9（= 编码后**数字静音**）",
      float(_np28.abs(_bad).max()) < 1e-6,
      "absmax=%.3e（%.0f dB）" % (float(_np28.abs(_bad).max()),
                                 20 * _np28.log10(max(float(_np28.abs(_bad).max()), 1e-30))))
check("28.8 ✅ 走本包出口 ⇒ 同一封装路**逐位还原**真波形（回归锁：不许再退化）",
      _np28.allclose(_good, _a32["waveform"].squeeze(0).transpose(0, 1).numpy(), atol=0)
      and float(_np28.abs(_good).max()) > 0.1,
      "absmax=%.4f" % float(_np28.abs(_good).max()))

# —— 节点出口契约：两条路都要转（防以后再有人在别处 `return audio`）——
_img28 = torch.rand(40, 8, 8, 3)
_p0_28 = unwrap(NODES.H3RelayTrimAV().trim(_img28, trim_frames=0, fps=24.0,
                                           audio=_a16, save_pcm=False))
check("28.9 「Trim AV」pin<=0 早退路：第 2 路 audio 出口是 f32（实跑事故就走这条）",
      _p0_28[1]["waveform"].dtype == torch.float32, str(_p0_28[1]["waveform"].dtype))
_pd_28 = unwrap(NODES.H3RelayTrimAV().trim(_img28, trim_frames=22, fps=24.0,
                                           settle_frames=0, audio=_a16, save_pcm=False))
check("28.10 「Trim AV」裁头主路：第 2 路 audio 出口同样是 f32",
      _pd_28[1]["waveform"].dtype == torch.float32, str(_pd_28[1]["waveform"].dtype))

_RID28 = "_unit_audio28"
try:
    _s28, _l28, _j28 = unwrap(NODES.H3RelayAudioSeam().seam(_a16, _RID28, 0))
    check("28.11 「Audio Seam」两路 AUDIO 出口（audio / joined）都是 f32",
          _s28["waveform"].dtype == torch.float32
          and _j28["waveform"].dtype == torch.float32,
          "audio=%s joined=%s" % (_s28["waveform"].dtype, _j28["waveform"].dtype))
    check("28.12 落盘的那一份**保持原 dtype**（fp16，省一半磁盘；组装层读回自己转 f32）",
          CORE.load_audio(NODES._audio_stage_path(_RID28, 0))["waveform"].dtype
          == torch.float16,
          str(CORE.load_audio(NODES._audio_stage_path(_RID28, 0))["waveform"].dtype))
finally:
    _d28 = os.path.dirname(NODES._audio_stage_path(_RID28, 0))
    if os.path.isdir(_d28):
        shutil.rmtree(_d28, ignore_errors=True)


# ============ 组29：声锚 voice_anchor（0.6.1）—— audio_ref/音频前缀源替换 ============
# 背景（2026-10-02 实测）：audio_ref = 上段音频尾 ⇒ 上段说话人的嗓音成为本段
# 嗓音的生成条件 ⇒ 台词逐段换人时音色交叉污染（实测某角色 F0 114→131、谱质心 1028→1308）。
# 声锚 = 显式提供"本段说话人"的音频 latent，两处（audio_ref + 钉住的音频前缀）同源替换。

print()
print("=" * 78)
print("29) 声锚 voice_anchor（audio_ref / 音频前缀源替换）")
print("=" * 78)

_va_video = torch.randn(1, 4, 6, 8, 8)
_va_audio = torch.randn(1, 2, 2, 37)          # 22 帧 ⇒ 37 步，正好一个窗
_va_plain = {"samples": _va_audio.clone()}    # VAEEncodeAudio 形态：纯 4 维张量
_va_nested = {"samples": CORE._nested_pair(_va_video, _va_audio)}

_tgt29 = av_latent(12, seed=11)
_prv29 = av_latent(12, seed=12)

# 29.1 回归：不接声锚 ⇒ 与旧行为逐位一致
_outA, _trimA, _repA = CORE.build_continue_latent(_tgt29, _prv29, 22)
_outB, _trimB, _repB = CORE.build_continue_latent(_tgt29, _prv29, 22, voice_anchor=None)
check("29.1 不接 voice_anchor ⇒ 输出逐位不变（回归）",
      torch.equal(CORE.video_from_latent(_outA), CORE.video_from_latent(_outB))
      and torch.equal(CORE.audio_from_latent(_outA), CORE.audio_from_latent(_outB)))

# 29.2 纯音频形态（VAEEncodeAudio）：audio_ref 用声锚尾窗
_plA = CORE.plan_relay(_tgt29, _prv29, trim_frames=22, voice_anchor=_va_plain)
_arA = _plA.audio_ref["audio_latent"]
check("29.2 声锚（纯 4 维张量形态）⇒ audio_ref 尾窗 = 声锚本身（37 步）",
      _arA.shape[-1] == 37 and torch.equal(_arA, _va_audio[:1, ..., -37:]),
      "take=%d" % _plA.audio_ref["ref_audio_t"])
check("29.3 声锚生效写进 notes（不静默）",
      any("声锚生效" in n for n in _plA.notes))

# 29.4 AV 联合形态：取第 2 条流
_plB = CORE.plan_relay(_tgt29, _prv29, trim_frames=22, voice_anchor=_va_nested)
check("29.4 声锚（NestedTensor 形态）⇒ audio_ref 同样取到声锚尾窗",
      torch.equal(_plB.audio_ref["audio_latent"], _va_audio[:1, ..., -37:]))

# 29.5 钉住的音频前缀同源替换（两处只改一处会打架）
_outC, _trimC, _repC = CORE.build_continue_latent(_tgt29, _prv29, 22, voice_anchor=_va_plain)
_auC = CORE.audio_from_latent(_outC)
check("29.5 音频前缀 = 声锚尾窗（不再取上一段音频尾）",
      torch.equal(_auC[..., :37], _va_audio[:1, ..., -37:]),
      _repC[-70:])
check("29.6 report 里写明「音频前缀改用声锚」（不静默）",
      "声锚" in _repC)

# 29.7 plan_relay 与 build_continue_latent 同源（同一声锚 ⇒ 同一尾窗）
check("29.7 audio_ref 与钉住的音频前缀取的是**同一份**声锚尾窗",
      torch.equal(_plA.audio_ref["audio_latent"], _auC[..., :37]))

# 29.8 声锚短于窗口 ⇒ 全长取用 + notes 警告（不静默、不崩溃）
_va_short = {"samples": _va_audio[:1, :, :, :10].clone()}
_plC = CORE.plan_relay(_tgt29, _prv29, trim_frames=22, voice_anchor=_va_short)
check("29.8 声锚 10 步 < 窗 37 步 ⇒ take=10 全长取用 + 警告进 notes",
      _plC.audio_ref["ref_audio_t"] == 10
      and any("声锚音频只有" in n for n in _plC.notes))

# 29.9 pin_audio=False + 声锚 ⇒ 声锚被忽略且 report 写明（不静默）
_outD, _trimD, _repD = CORE.build_continue_latent(_tgt29, _prv29, 22,
                                                  pin_audio=False, voice_anchor=_va_plain)
check("29.9 pin_audio=False + 声锚 ⇒ report 显式写「被忽略」",
      "忽略" in _repD)

# 29.10 非法形态 ⇒ raise（报错要能看懂）
try:
    CORE.plan_relay(_tgt29, _prv29, trim_frames=22, voice_anchor="not-a-latent")
    check("29.10 声锚形态非法 ⇒ raise", False, "未抛异常")
except ValueError as _e29:
    check("29.10 声锚形态非法 ⇒ raise（文案可读）",
          "voice_anchor" in str(_e29), str(_e29).splitlines()[0][:80])

# 29.11~29.15 内容守卫（0.6.20 补）：**没有内容的锚必须当场 raise**，不许静默生效。
# 依据：`MiniMaxH3AudioVAE.encode` 返回**归一化** latent（`(z−mean)/std`）⇒「有没有内容」
# 看**时域方差**、不看幅值。实测踩点（2026-10-02）：把出词节点的 LATENT（空 AV latent）
# 接到 voice_anchor ⇒ 全零锚静默生效（报错节点不报，症状只在成片里）。
_va_zero = {"samples": torch.zeros(1, 2, 2, 37)}
try:
    CORE.plan_relay(_tgt29, _prv29, trim_frames=22, voice_anchor=_va_zero)
    check("29.11 全零锚 ⇒ raise（不许静默当作有锚）", False, "未抛异常")
except ValueError as _e29b:
    check("29.11 全零锚 ⇒ raise，且文案点名「全为零」+ 指路 voice_anchor",
          "全为零" in str(_e29b) and "voice_anchor" in str(_e29b),
          str(_e29b).splitlines()[0][:90])

_va_const = {"samples": torch.full((1, 2, 2, 37), 0.7)}       # 非零，但时域上毫无变化
try:
    CORE.plan_relay(_tgt29, _prv29, trim_frames=22, voice_anchor=_va_const)
    check("29.12 常量锚（非零但时域零变化）⇒ raise", False, "未抛异常")
except ValueError as _e29c:
    check("29.12 常量锚（非零但时域零变化）⇒ raise，文案点名「常量」",
          "常量" in str(_e29c), str(_e29c).splitlines()[0][:90])

_va_nan = {"samples": torch.full((1, 2, 2, 37), float("nan"))}
try:
    CORE.plan_relay(_tgt29, _prv29, trim_frames=22, voice_anchor=_va_nan)
    check("29.13 NaN 锚 ⇒ raise", False, "未抛异常")
except ValueError as _e29d:
    check("29.13 NaN 锚 ⇒ raise，文案点名 NaN",
          "NaN" in str(_e29d), str(_e29d).splitlines()[0][:90])

_va_one = {"samples": torch.randn(1, 2, 2, 1)}    # 单步：**无偏** std 是 NaN ⇒ 会静默漏过
try:
    CORE.plan_relay(_tgt29, _prv29, trim_frames=22, voice_anchor=_va_one)
    check("29.14 单步锚 ⇒ raise（无偏 std 的 NaN 陷阱）", False, "未抛异常")
except ValueError as _e29e:
    check("29.14 单步锚 ⇒ raise（无偏 std 的 NaN 陷阱）",
          "声锚" in str(_e29e), str(_e29e).splitlines()[0][:90])

# 29.15 反向自证：守卫不能误伤**真实尺度**的锚 —— 取实测真实产物尾窗 std 的下限附近（0.4）
_va_real = {"samples": torch.randn(1, 2, 2, 37) * 0.4}
check("29.15 真实产物尺度（std≈0.4，实测下限附近）不被守卫误伤（反向自证）",
      CORE.plan_relay(_tgt29, _prv29, trim_frames=22,
                      voice_anchor=_va_real).audio_ref["ref_audio_t"] == 37,
      "std=%.3f" % float(_va_real["samples"].std(dim=-1, unbiased=False).max()))


print()
print("=" * 78)
print("30) 孤立瞬态抑制 declick（0.6.22 新增）")
print("=" * 78)

_SR30 = 32000                       # 与产线实测同采样率
_N30 = _SR30 * 3                    # 3 秒
_BG30 = 0.0016                      # 背景（≈ −56 dBFS，与实测「孤立瞬态」处背景同量级）


def _mk30(spikes, speech_from=None, dtype=torch.float32, shape=(1, 1, -1)):
    """造一段「安静背景 + 若干孤立峰 + 可选台词区」的合成音频（零外部依赖）。

    `spikes` = [(起点毫秒, 峰值)]；`speech_from` 起之后是**台词区**（背景不静 ⇒ 不该被碰）。
    """
    g = torch.Generator().manual_seed(20261004)
    x = (torch.rand(_N30, generator=g) - 0.5) * 2.0 * _BG30
    for ms, amp in spikes:
        i = int(ms / 1000.0 * _SR30)
        x[i:i + int(0.002 * _SR30)] = amp
    if speech_from is not None:
        j = int(speech_from * _SR30)
        x[j:] = (torch.rand(_N30 - j, generator=g) - 0.5) * 0.8
    w = x.to(dtype)
    if shape != (-1,):
        w = w.reshape(*(s if s != -1 else _N30 for s in shape))
    return {"waveform": w, "sample_rate": _SR30}


def _flat(a):
    return a["waveform"].reshape(-1)


_LIM30 = int(1.2 * _SR30)           # 产线纪律上界（前 1.2s 无词）

# 30.1 关 ⇒ 逐位直通（老图不受影响的最小保证）
_a30 = _mk30([(0.0, 0.97), (930.0, 0.0095)], speech_from=1.3)
_b30, _r30 = CORE.declick_transients(_a30, ratio=0.0)
check("30.1 ratio=0 ⇒ 逐位直通（空报告）",
      _r30 == "" and bool(torch.equal(_b30["waveform"], _a30["waveform"])))

# 30.2~30.4 真凶（段首孤立峰）与「孤立瞬态」被压到背景；台词区**逐位不动**
_c30, _rep30 = CORE.declick_transients(_a30, ratio=4.0, quiet_dbfs=-50.0, limit_n=_LIM30)
check("30.2 报告可观测（抑制了几处 + 位置）", "孤立瞬态抑制" in _rep30, _rep30.splitlines()[0][:70])
_p0 = float(_flat(_a30)[:64].abs().max())
_p1 = float(_flat(_c30)[:64].abs().max())
check("30.3 段首孤立峰 ⇒ 压到背景量级", _p1 < _p0 * 0.05, "%.5f → %.5f" % (_p0, _p1))
_i930 = int(0.930 * _SR30)
_q0 = float(_flat(_a30)[_i930:_i930 + 64].abs().max())
_q1 = float(_flat(_c30)[_i930:_i930 + 64].abs().max())
check("30.4 0.930s 那个孤立峰 ⇒ 压到背景量级", _q1 < _q0 * 0.5, "%.5f → %.5f" % (_q0, _q1))
_s0 = int(1.3 * _SR30)
_d30 = float((_flat(_a30)[_s0:] - _flat(_c30)[_s0:]).abs().max())
check("30.5 台词区（1.3s 起）**逐位不动**", _d30 == 0.0, "max|Δ|=%.8f" % _d30)

# 30.6 长度守恒 + 形状还原（节点里实测是 [1,1,T]；[T] 也不能被撑成 [1,T]）
for _sh in ((_N30,), (1, _N30), (1, 1, _N30)):
    _x = _mk30([(0.0, 0.97)], shape=_sh)
    _y, _ = CORE.declick_transients(_x, ratio=4.0, limit_n=_LIM30)
    check("30.6 形状 %s 保住 + 长度守恒" % (_sh,),
          tuple(_y["waveform"].shape) == _sh, "→ %s" % (tuple(_y["waveform"].shape),))

# 30.7 dtype 保真（fp16 是产线默认落盘 dtype）
_x = _mk30([(0.0, 0.97)], dtype=torch.float16)
_y, _ = CORE.declick_transients(_x, ratio=4.0, limit_n=_LIM30)
check("30.7 fp16 进 ⇒ fp16 出（不改 dtype）", _y["waveform"].dtype == torch.float16,
      str(_y["waveform"].dtype))

# 30.8 范围闸：范围外的峰**不许碰**（这一条挡的是「顺手把全片的瞬态都掐了」）
_x = _mk30([(0.0, 0.97), (2500.0, 0.05)])
_y, _ = CORE.declick_transients(_x, ratio=4.0, limit_n=_LIM30)
_i25 = int(2.5 * _SR30)
check("30.8 范围闸外的峰逐位不动",
      float(_flat(_x)[_i25:_i25 + 64].abs().max()) == float(_flat(_y)[_i25:_i25 + 64].abs().max()))

# 30.9~30.10 🔴 裁帧边界（本节点工作在**未裁**音频上，产物是裁后的）：
#   ① 边界必须淡入（否则「裁出来的半波形」在成片里就是一处孤立瞬态）
#   ② 边界之后的峰必须**还在范围内**被命中 —— 范围 fallback 要从**边界**起算，
#      否则 pin 长（22 帧 ⇒ 0.917s）时边界后只剩 0.28s ⇒ 治不到真凶（2026-10-04 修）。
_cut30 = int(round(22 / 24.0 * _SR30))
_x = _mk30([(917.0 + 930.0, 0.0095)])
_y, _rep30b = CORE.declick_transients(_x, ratio=4.0, quiet_dbfs=-50.0,
                                      limit_n=_cut30 + _LIM30, cut_head_n=_cut30)
_f0 = float(_flat(_x)[_cut30:_cut30 + 64].abs().max())
_f1 = float(_flat(_y)[_cut30:_cut30 + 4].abs().max())
check("30.9 裁帧边界已淡入（治「裁出来的孤立峰」）", _f1 < _f0 * 0.5, "%.5f → %.5f" % (_f0, _f1))
check("30.10 边界之后的孤立峰**仍被命中**（fallback 范围从边界起算）",
      "孤立瞬态抑制** 1 处" in _rep30b, _rep30b.splitlines()[0][:70])
_y2, _rep30c = CORE.declick_transients(_x, ratio=4.0, quiet_dbfs=-50.0,
                                       limit_n=_LIM30, cut_head_n=_cut30)
check("30.11 同一峰在**旧口径**（范围从头算）下确实治不到 ⇒ 证明 30.10 不是白测",
      _rep30c == "", "旧口径报告=%r" % (_rep30c[:40],))

# 30.12~30.13 静默失效的两条老坑（都真的踩过）
_x = _mk30([(0.0, 0.97)])
_y, _rep30d = CORE.declick_transients(_x, ratio=4.0, limit_n=0)
check("30.12 limit_n=0 ⇒ 不许静默失效（仍有报告）", "孤立瞬态抑制" in _rep30d)
_z30 = {"waveform": torch.zeros(1, 1, _N30), "sample_rate": _SR30}
_y, _rep30e = CORE.declick_transients(_z30, ratio=4.0)
check("30.13 全零 ⇒ 无事件、逐位相同",
      _rep30e == "" and bool(torch.equal(_y["waveform"], _z30["waveform"])))

# 30.14~30.16 🔴 **顺序**：declick 必须排在 `audio_seam_patch` **之后**
#   背景：patch 是「整段替换头部 N 秒」⇒ 若 declick 先做，它对头部的处理会被 patch
#   整段盖掉，**而报告看着像治了**。更隐蔽的一面：**patch 换进来的床源内容从未被
#   declick 看过**（declick 当时还没跑）⇒ 床源里的孤立峰会原样留在成片里。
#   ⇒ 正确顺序（先 patch 后 declick）反而**多治一段**。这三条把这个差别钉成可证伪的。
_LIM30B = int(2.9 * _SR30)          # 本段无台词 ⇒ 范围闸放到 2.9s
_x = _mk30([(0.0, 0.97), (1500.0, 0.02)])          # 无台词区：整段都是安静背景
_bed = (torch.rand(1, 1, _N30, generator=torch.Generator().manual_seed(7)) - 0.5) * 2.0 * _BG30
# 🔴 峰必须埋在**尾部**：`bed_select="tail"` 取的是「床源尾部同长窗」
#    （埋在上半段 ⇒ 压根没进替换区，测不到任何东西 —— 我第一版就这么错的）。
_bed[0, 0, int(2.6 * _SR30):int(2.6 * _SR30) + 64] = 0.05
_bed30 = {"waveform": _bed, "sample_rate": _SR30}
_pat, _prep = CORE.audio_seam_patch(_x, _bed30, 1.2, 0.0, 0.25,
                                   patch_guard=False, stage_index=1)
_wrong, _ = CORE.declick_transients(_x, ratio=4.0, limit_n=_LIM30B)     # ❌ 先 declick…
_wrong, _ = CORE.audio_seam_patch(_wrong, _bed30, 1.2, 0.0, 0.25,
                                  patch_guard=False, stage_index=1)     #    …后 patch
_right, _rrep30 = CORE.declick_transients(_pat, ratio=4.0, limit_n=_LIM30B)   # ✅ 先 patch…
check("30.14 顺序真的有影响（bed 的峰：错序漏治 / 对序被治）",
      not torch.equal(_wrong["waveform"], _right["waveform"]),
      "max|Δ|=%.6f" % float((_wrong["waveform"] - _right["waveform"]).abs().max()))
# 🔴 床源 2.6s 落在尾窗 [1.8,3.0] 的偏移 0.8s ⇒ **贴到输出头部 0.8s**
#    （patch 是「取窗后搬到头部」，坐标要这么换算 —— 我第一版直接拿 2.6s 去读，
#     读的是原始音频的位置，于是两条路径看起来一样、断言测不到任何东西）。
_bi = int(0.8 * _SR30)
check("30.15 对序下，**patch 换进来的床源峰也被治**（错序时它从未被 declick 看过）",
      float(_flat(_right)[_bi:_bi + 64].abs().max())
      < float(_flat(_wrong)[_bi:_bi + 64].abs().max()) * 0.5,
      "错序 %.5f → 对序 %.5f" % (float(_flat(_wrong)[_bi:_bi + 64].abs().max()),
                                  float(_flat(_right)[_bi:_bi + 64].abs().max())))
_i = int(1.5 * _SR30)
check("30.16 patch 开着时，窗外那一下**仍被治**（patch 不该把 declick 关掉）",
      float(_flat(_right)[_i:_i + 64].abs().max())
      < float(_flat(_pat)[_i:_i + 64].abs().max()) * 0.5,
      "%.5f → %.5f" % (float(_flat(_pat)[_i:_i + 64].abs().max()),
                        float(_flat(_right)[_i:_i + 64].abs().max())))

# 30.17~30.22 🔴 **节点层政策**（`declick_gate` / `declick_on_segment`，2026-10-04 从
#   `nodes.py` 的内联闭包搬进核心层）：这三条分支此前**零断言覆盖**（闭包不在任何门槛里，
#   `smoke_nodes.py` 也不碰 declick），而失败模式**全是静默的** —— 范围算错 ⇒ 该治的没治，
#   而日志看起来一切正常。判据里的绝对数字**与宿主实测对账**：
#   真实 GPU 报告「范围 = 前 1.408s（⚠ 台词起点不可测）」= 纪律 1.2s + 裁帧边界 5 帧/24fps。


def _gate30(speech_s=None):
    """专用夹具：安静背景 + 可选一段**短**台词。

    🔴 台词必须**短**（占 3s 探测窗 <20%）：`_speech_onset_in_head` 的基线是「全源帧 RMS
       中位」，台词占比 >50% 时中位落到语音侧 ⇒ 2×中位门限抬到语音之上 ⇒ **测不到起点**
       （这正是常量区写明的既知失效域）⇒ 夹具会假阴、把「测到」那条测成失败。
    """
    g = torch.Generator().manual_seed(20261005)
    x = (torch.rand(_N30, generator=g) - 0.5) * 2.0 * _BG30
    if speech_s is not None:
        j = int(speech_s * _SR30)
        n = int(0.6 * _SR30)
        x[j:j + n] = (torch.rand(n, generator=g) - 0.5) * 0.8
    return x.reshape(1, 1, _N30)


_dc_off, _note_off = CORE.declick_on_segment(_a30, 0.0)
check("30.17 节点层关（ratio=0）⇒ 逐位直通 + **一个字都不说**（note 为空）",
      _note_off == "" and bool(torch.equal(_dc_off["waveform"], _a30["waveform"])))

# 🔵 30.18~30.21 测的是 **head 政策**（`AUDIO_DECLICK_SCOPE_ALL=False` 的**回退路径**）。
#    默认政策 2026-10-04 已改成「全段」（见 30.21b/c）。回退路径**必须仍有断言守着** ——
#    它存在的意义就是"出事能一键退回"，没人守的退路等于没有。
_lim30g, _fb30g, _cut30g = CORE.declick_gate(_gate30(), _SR30, scope_all=False)
check("30.18 【head 政策】测不到台词起点 ⇒ 退产线纪律上界（**不许**退化成「不限范围」）；前导维已展平",
      _fb30g is True and _lim30g == int(1.2 * _SR30) and _cut30g == 0,
      "limit=%.3fs fb=%s cut=%d" % (_lim30g / _SR30, _fb30g, _cut30g))

_lim30c, _fb30c, _cut30c = CORE.declick_gate(_gate30(), _SR30, cut_head_frames=5, scope_all=False)
check("30.19 【head 政策】裁帧边界 5 帧 ⇒ limit = 边界 + 1.2s = **1.408s**（与宿主实测报告逐字对齐）",
      _fb30c is True and abs(_lim30c / _SR30 - 1.40833) < 0.001
      and _cut30c == int(round(5 / 24.0 * _SR30)),
      "limit=%.5fs cut_n=%d" % (_lim30c / _SR30, _cut30c))

_lim30p, _fb30p, _cut30p = CORE.declick_gate(_gate30(), _SR30, cut_head_frames=22, scope_all=False)
check("30.20 【head 政策】帧→样本按 **24 fps** 换算（22 帧 = 0.917s ⇒ 1.2+0.917 = 2.117s）",
      _cut30p == int(round(22 / 24.0 * _SR30)) and abs(_lim30p / _SR30 - 2.11667) < 0.001,
      "limit=%.5fs cut_n=%d" % (_lim30p / _SR30, _cut30p))

_lim30s, _fb30s, _cut30s = CORE.declick_gate(_gate30(speech_s=1.0), _SR30, cut_head_frames=22,
                                             scope_all=False)
check("30.21 【head 政策】**测到**起点 ⇒ 用它、且**不**做边界补偿（1.0s ± 一帧窗 50ms）",
      _fb30s is False and abs(_lim30s / _SR30 - 1.0) <= 0.06
      and _lim30s < _cut30p + int(1.2 * _SR30),
      "limit=%.3fs fb=%s" % (_lim30s / _SR30, _fb30s))

# 30.21b~c 🔴 **默认政策 = 全段**（2026-10-04 修订）。依据见 `AUDIO_DECLICK_SCOPE_ALL`：
#   旧政策的前提「孤立瞬态恒在台词之前」被端到端实测**证伪**（10s 三人段 17 处孤立峰，
#   15 处在旧的 1.408s 闸之外 ⇒ 一律没治，而报告看着一切正常）。
_l30all, _f30all, _c30all = CORE.declick_gate(_gate30(), _SR30, cut_head_frames=5)
check("30.21b **默认政策 = 全段**：limit = 输入全长（旧政策此处只给 1.408s ⇒ 闸外的孤立瞬态全漏治）",
      _f30all is False and _l30all == _N30 and _c30all == int(round(5 / 24.0 * _SR30)),
      "limit=%.3fs（全长 %.3fs） fb=%s cut_n=%d"
      % (_l30all / _SR30, _N30 / _SR30, _f30all, _c30all))

#   🔴 **本轮的靶子**：静背景里、**远离段首（4.4s）**的一声孤立瞬态 —— 旧政策下它在 1.408s
#      闸外、**一个样本都不碰**（这就是「治好的孤立瞬态又出现了」的真因）；默认政策下必须被治。
_g30c = torch.Generator().manual_seed(20261004)
_x30c = (torch.rand(_N30 * 2, generator=_g30c) - 0.5) * 2.0 * _BG30
_i30c = int(4.4 * _SR30)
_x30c[_i30c:_i30c + int(0.004 * _SR30)] = 0.03            # 4ms 孤立瞬态，落在成片可见区
_a30c = {"waveform": _x30c.reshape(1, 1, -1), "sample_rate": _SR30}
_y30c, _r30c = CORE.declick_on_segment(_a30c, 4.0, -50.0)
_p30c = float(_x30c[_i30c:_i30c + 128].abs().max())
_q30c = float(_y30c["waveform"].reshape(-1)[_i30c:_i30c + 128].abs().max())
check("30.21c 🔴 4.4s 处的孤立瞬态（head 政策闸外 ⇒ 逐位直通）在默认政策下**被治**",
      _q30c < _p30c * 0.5, "%.5f → %.5f" % (_p30c, _q30c))

_clean30 = {"waveform": _gate30(), "sample_rate": _SR30}
_n50 = CORE.declick_on_segment(_clean30, 4.0, None)[1]
_n40 = CORE.declick_on_segment(_clean30, 4.0, -40.0)[1]
check("30.22 未命中也要报一行（铁律 15）+ `quiet_dbfs=None` 走**常量**（默认值只一个出处）",
      "未命中事件" in _n50 and "-50 dBFS" in _n50 and "-40 dBFS" in _n40,
      _n50.splitlines()[0][:58])

# 30.23~30.24 🔴 **范围闸必须盖过裁帧边界**（2026-10-04；零 GPU 复现逼出来的真凶）
#   背景：钉在 `cut` 处那一记孤立瞬态 = 裁帧切出来的半波形（交接 2026-10-04 §2.3 定性）。
#   而范围闸原来用「头部首个有声 run」划线 —— 那个 run 在 **pin 区（上一段尾巴）里**
#   ⇒ 线落在 `cut` **之前** ⇒ 孤立瞬态被 `cand[lim_idx:] = False` 静默排除
#   （探针 C1 实测：pin 语音 0.05s 起 ⇒ 范围=前 0.050s ⇒ cut(0.208s) 峰值 0.5000 → 0.5000，
#     而报告只写「未命中」⇒ **看着一切正常**）。
#   ⇒ 修法：`limit` 抬到 ≥ `cut + AUDIO_DECLICK_CUT_GUARD_S`（`cut` 之后才是新段内容，
#     「上一段语音在哪」不该用来关掉它）。这两条把「诊断」与「疗效」各钉一个。
_lim23, _fb23, _cut23 = CORE.declick_gate(_gate30(speech_s=0.30), _SR30, cut_head_frames=22)
check("30.23 起点落在裁帧边界**之前** ⇒ 范围抬到 ≥ 边界 + 50ms（否则孤立瞬态被静默漏治）",
      _fb23 is False
      and _lim23 >= _cut23 + int(round(CORE.AUDIO_DECLICK_CUT_GUARD_S * _SR30)),
      "limit=%.3fs cut=%.3fs" % (_lim23 / _SR30, _cut23 / _SR30))

_g24 = torch.Generator().manual_seed(24)
_x24 = (torch.rand(_N30, generator=_g24) - 0.5) * 2.0 * 3e-4          # 极静背景（≈−70 dBFS）
_s24, _n24 = int(0.30 * _SR30), int(0.60 * _SR30)
_x24[_s24:_s24 + _n24] = (torch.rand(_n24, generator=_g24) - 0.5) * 0.8   # pin 区语音：0.30s 起
_cut24 = int(round(22 / 24.0 * _SR30))
_k24 = int(0.0010 * _SR30)                                            # 1ms（跨 2 格，≤ MAX_LEN 2ms）
_x24[_cut24:_cut24 + _k24] = torch.linspace(0.5, 0.0, _k24)           # 裁帧切出来的半波形
_a24 = {"waveform": _x24.reshape(1, 1, _N30), "sample_rate": _SR30}
_y24, _r24 = CORE.declick_on_segment(_a24, 4.0, -50.0, 22)
_p24 = float(_flat(_a24)[_cut24:_cut24 + 64].abs().max())
_q24 = float(_flat(_y24)[_cut24:_cut24 + 64].abs().max())
check("30.24 pin 区语音晚起 ⇒ `cut` 处的孤立瞬态**仍被治**（修前此处逐位直通）",
      _q24 < _p24 * 0.5, "%.5f → %.5f" % (_p24, _q24))

# 30.25~30.27 🔴 `max_len_ms` 是**用户可调旋钮**（widget `declick_max_len_ms`，默认 50）
#   实测「孤立瞬态」宽 **8ms**；旧版把这个值**写死 2ms** ⇒ 把孤立瞬态判成「不是一声」**直接漏掉**
#   ⇒ 这才是「抑制始终不生效」的主因。这三条把「旋钮真的接线了 + 默认值只有一个出处」钉住。
_x25 = _mk30([(500.0, 0.02)])
_i25 = int(0.5 * _SR30)
_x25["waveform"][..., _i25:_i25 + int(0.008 * _SR30)] = 0.02        # 拉宽成 8ms
_L25 = int(2.0 * _SR30)
_y25n, _r25n = CORE.declick_transients(_x25, ratio=4.0, quiet_dbfs=-50.0,
                                       limit_n=_L25, max_len_ms=2.0)
check("30.25 `max_len_ms=2` ⇒ 8ms 事件被判「不是一声」⇒ 漏治（旧版行为；现在**明说**被丢）",
      "被丢弃" in _r25n and bool(torch.equal(_y25n["waveform"], _x25["waveform"])),
      _r25n.splitlines()[0][:64])
_y25w, _r25w = CORE.declick_transients(_x25, ratio=4.0, quiet_dbfs=-50.0,
                                       limit_n=_L25, max_len_ms=50.0)
check("30.26 `max_len_ms=50`（新默认）⇒ 同一个 8ms 事件**治得到**", "孤立瞬态抑制" in _r25w)
_y25d, _r25d = CORE.declick_transients(_x25, ratio=4.0, quiet_dbfs=-50.0, limit_n=_L25)
check("30.27 不传 `max_len_ms` ⇒ 取常量默认值（默认值**只有一个出处**）⇒ 与显式 50 逐位同",
      bool(torch.equal(_y25d["waveform"], _y25w["waveform"])) and _r25d == _r25w)

# 30.28~30.30 🔴 2026-10-04 **多维度代码审核**抓到的两条（都钉成回归）
#   ① **相邻事件互相污染**：填充的「左右邻」若伸进**另一个事件**，会把那个尖峰**原样搬进来**，
#      而副本不在邻居的坐标上 ⇒ **再也不会被治**（实测残留 **0.416 = 原峰 83%**，等于没治）
#      ⇒ 修法 = 两遍写法：先算全区间，左右邻**避开**其他事件（`_declick_suppress`）。
#   ② **「被超长闸丢掉」静默**：`ratio` 调得很小 ⇒ 候选连成一片 ⇒ 合并成超长段 ⇒ **全被丢** ⇒
#      一个都不治，而旧报告只写「未命中」⇒ 用户**无从归因** ⇒ 现在必须点明丢了几段（铁律 15）。
_a28 = _mk30([(500.0, 0.5), (507.0, 0.5)])            # 两个尖峰、相隔 7ms ⇒ 是**两个**事件
_i28 = int(0.5 * _SR30)
_b28, _r28 = CORE.declick_transients(_a28, ratio=4.0, quiet_dbfs=-50.0, limit_n=int(1.0 * _SR30))
_p28 = float(_flat(_b28)[_i28:_i28 + int(0.02 * _SR30)].abs().max())
check("30.28 相邻两峰：邻域**避开另一事件** ⇒ 都治干净（修前残留 83% = 等于没治）",
      _p28 < _BG30 * 3.0, "峰值 %.5f（背景 %.5f）" % (_p28, _BG30))
_b29, _r29 = CORE.declick_transients(_a28, ratio=0.001, quiet_dbfs=-50.0, limit_n=int(1.0 * _SR30))
check("30.29 候选因超长被丢 ⇒ 报告必须**点明**（不许静默「未命中」）",
      "被丢弃" in _r29 and bool(torch.equal(_b29["waveform"], _a28["waveform"])),
      _r29.splitlines()[0][:64])
_b30, _r30 = CORE.declick_transients(_b28, ratio=4.0, quiet_dbfs=-50.0,
                                     limit_n=int(1.0 * _SR30))
check("30.30 幂等：对**已治结果**再治一次 ⇒ 逐位不变",
      bool(torch.equal(_b30["waveform"], _b28["waveform"])),
      "max|Δ|=%.9f" % float((_b30["waveform"] - _b28["waveform"]).abs().max()))

# 30.31–30.32b · 邻域不够静 ⇒ **跳过**（2026-10-04 两轮实测逼出来的）
#   第一轮病象（真实产物 `t3p_dc2_r4` @0.0710s，节点坐标）：左邻 **0.02905（−30.7 dBFS）** /
#   事件 0.01434（−36.9）⇒ 旧写法治后 **0.02663（−31.5）** = **比事件本身还响 +5.4 dB**
#   —— 等于把上一段语音**搬**到了这一处。
#   第二轮病象（夹具 `_bed_a` @1.983s，音量断崖）：事件 0.4997、左邻同量级 ⇒ 旧判据
#   「邻域 max **>** 事件峰」**约 50% 概率放行**（两侧同量级，谁大只看那两窗的随机样本）
#   ⇒ 把断崖治成 **0.0489**（背景）。
#   ⇒ 判据定稿：**邻域 max × `AUDIO_DECLICK_EDGE_REL` > 事件峰 ⇒ 跳过**
#      （孤立伪影的前提是「周围比它低一个量级」，不是「周围不更大」）。
_x31 = _flat(_mk30([(500.0, 0.30)])).reshape(1, -1)
_x31[0, int(0.504 * _SR30):int(0.510 * _SR30)] = 0.90      # 紧邻事件的更响内容（模拟语音）
_w131 = int(0.001 * _SR30)
_e31, _B31, _h31 = CORE._declick_envelope(_x31, _w131, int(_x31.shape[-1]) // _w131)
_o31, _d31, _k31 = CORE._declick_suppress(_x31, _SR30, _w131, [(500, 501)], _e31, _B31)
check("30.31 邻域**比事件更响**（紧邻语音/音效）⇒ **跳过**（不碰）",
      len(_k31) == 1 and len(_d31) == 0 and bool(torch.equal(_o31, _x31)),
      "跳过 %d 处 / 已治 %d 处" % (len(_k31), len(_d31)))

# 🔴 30.31b **本轮改进的核心**：邻域**只比事件低一点**（0.8×）也必须跳过 ——
#   旧判据「邻域 max > 事件峰」在这里**会治**（0.8 < 1.0），但 0.8× 根本不是"静背景"
#   （真伪影的邻域要低一个量级，实测真实孤立瞬态 5.7×）⇒ 治它 = 削内容边缘/断崖。
#   新判据：0.8 × 2.0 = 1.6 > 1.0 ⇒ 跳过 ✅
_g31b = torch.Generator().manual_seed(31)
_x31b = (torch.rand(_N30, generator=_g31b) - 0.5) * 2.0 * 0.0005
_x31b[int(0.500 * _SR30):int(0.501 * _SR30)] = 0.10        # 事件峰 0.10
_x31b[int(0.480 * _SR30):int(0.500 * _SR30)] = 0.08        # 左邻 = 0.8×（不是静背景）
_e31b, _B31b, _h31b = CORE._declick_envelope(_x31b.reshape(1, -1), _w131,
                                             _N30 // _w131)
_o31b, _d31b, _k31b = CORE._declick_suppress(_x31b.reshape(1, -1), _SR30, _w131,
                                             [(500, 501)], _e31b, _B31b)
check("30.31b 邻域**仅低 20%**（0.8×）⇒ 也跳过（旧判据「邻域>事件」在此**会误治**音量断崖）",
      len(_k31b) == 1 and len(_d31b) == 0 and bool(torch.equal(_o31b, _x31b.reshape(1, -1))),
      "跳过 %d 处 / 已治 %d 处" % (len(_k31b), len(_d31b)))

_rep31 = CORE._declick_report(
    [(0.100, 0.010, 0.001)],
    CORE._DeclickCtx(sr=_SR30, total=1000, ratio=4.0, quiet_dbfs=-50.0, max_len_ms=50.0),
    1000, [(0.071, 0.0143, 0.0291)])
check("30.32 报告必须**点名跳过**且写明**倍数判据**（铁律 15：跳过不许静默）",
      "跳过 1 处" in _rep31 and "邻域不够静" in _rep31 and "2.0 倍" in _rep31,
      _rep31.splitlines()[-1][:70])

# 30.33~30.34 · 多维度审核补（2026-10-04）：空音频不许炸 / 非有限输入 fail-closed
for _nm33, _w33 in (("空 [1,1,0]", torch.zeros(1, 1, 0)),
                    ("前导维 0 [0,1,100]", torch.zeros(0, 1, 100))):
    _a33 = {"waveform": _w33, "sample_rate": _SR30}
    _o33, _r33 = CORE.declick_on_segment(_a33, 4.0, -50.0)
    check("30.33 %s ⇒ 不抛、原样返回（空音频合法；旧写法 `reshape(-1,…)` 会抛 RuntimeError）" % _nm33,
          _o33 is _a33 and "未命中" in _r33,
          "shape=%s rep=%s" % (tuple(_o33["waveform"].shape), _r33.splitlines()[0][:40]))

_bad33 = []
for _nm33, _v33 in (("NaN", float("nan")), ("Inf", float("inf"))):
    _x33 = torch.rand(_N30, generator=torch.Generator().manual_seed(33)) * 0.01
    _x33[100] = _v33
    try:
        CORE.declick_on_segment({"waveform": _x33.reshape(1, 1, -1), "sample_rate": _SR30},
                                4.0, -50.0)
        _bad33.append("%s 没 raise" % _nm33)
    except ValueError as _e33:
        _bad33.append("" if "NaN" in str(_e33) else "%s 文案没点名" % _nm33)
check("30.34 含 NaN / Inf 的输入 ⇒ **fail-closed**（不许把 NaN 顺着「邻域填充」传遍全段）",
      _bad33 == ["", ""], "%s" % (_bad33,))

print()
print("=" * 78)
def _apply31(n_audio, own=True):
    """跑一遍 apply_relay，返回带 report 的结果（额度提示写进 notes ⇒ report）。

    ⚠️ 用 duck-typed 的 plan（`types.SimpleNamespace`）而不是 `plan_relay` ——
    本组要验的是**额度计数与提示**，与 latent 形状/时序网格无关 ⇒ 不该被那些前置条件绑住。
    `own=False` = 本段不注入音频参考（首段形态），用来验「没动就不报」。
    """
    import types
    plan = types.SimpleNamespace(
        applied=True, span=5, keyframes=[],
        audio_ref=({"kind": "audio", "ref_audio_t": 37,
                    "audio_latent": torch.randn(1, 2, 2, 37)} if own else None),
        anchor_ref=None, notes=[])
    CORE.apply_relay(_cond31(n_audio), plan)
    return {"report": "\n".join(plan.notes), "plan": plan}


print("31) 音频参考额度（0.6.22：官方 3 槽 vs 本包恒占 1 个）")
print("=" * 78)

# 构造 conditioning：官方 ref_audios 写了 N 个音频块（+ 若干 video 块做干扰项）
def _cond31(n_audio, n_video=0):
    blocks = [{"kind": "image"}, {"kind": "video", "latent_t": 1}]
    blocks += [{"kind": "video", "latent_t": 1} for _ in range(n_video)]
    blocks += [{"kind": "audio", "ref_audio_t": 37, "audio_latent": torch.randn(1, 2, 2, 37)}
               for _ in range(n_audio)]
    return [[torch.zeros(1, 4), {"minimax_refs": blocks}]]      # conditioning = [[emb, extra]]


check("31.1 官方 3 槽 + 本包 1 个 = 4 ⇒ report **必须点名超上限**（不许静默）",
      CORE.count_official_audio_refs(_cond31(3)) == 3
      and "超过官方" in _apply31(3)["report"], "")
check("31.2 官方 2 槽 + 本包 1 = 3 ⇒ 刚好用满（提示但不报警）",
      "正好用满" in _apply31(2)["report"], "")
check("31.3 外部锚是 kind=video ⇒ **不占音频额度**（数音频块，不是数 ref 块总数）",
      CORE.count_official_audio_refs(_cond31(1, n_video=3)) == 1, "")
check("31.4 没有官方参考块 ⇒ 数到 0，且不报警",
      CORE.count_official_audio_refs(_cond31(0)) == 0
      and "超过官方" not in _apply31(0)["report"], "")

# —— 0.6.23：多锚调研的结论落进**权威口径**（report 是脚本/API 用户唯一的权威来源）——
# 三条判据都来自**读码确证**，正文见 docs/07 §声锚·一段内多人。
_r31_0 = _apply31(0)["report"]
_r31_2 = _apply31(2)["report"]
check("31.5 本包追加的块必须写明「文本侧没有 `<Audio j>` 标签 ⇒ **prompt 引不到它**」"
      "（不许让用户以为给了锚就能在 prompt 里引用）",
      "prompt 引不到它" in _r31_0 and "`<Audio j>`" in _r31_0, "")
check("31.6 报告里点名它在 DiT 侧的**序号 = 官方数 + 1**（官方 0 ⇒ 第 1 个；"
      "官方 2 ⇒ 第 3 个）；差一就是指错位置",
      "第 1 个" in _r31_0 and "第 3 个" in _r31_2
      and "排在官方 2 个之后" in _r31_2, "")
check("31.7 「一段内多人的正路」写进 report：官方 `ref_audios` 槽 + 编号按**已接线顺序**"
      "（不是槽号）+ `TrimAudioDuration` 裁窗",
      "ref_audios" in _r31_0 and "已接线顺序" in _r31_0
      and "不是槽号" in _r31_0 and "TrimAudioDuration" in _r31_0, "")
check("31.8 🔴 首段（本段不注入音频参考）⇒ **这三行一条都不许出现**"
      "（「没动就不报」—— 首段的 report 不许被这段文案污染）",
      _apply31(0, own=False)["report"] == "", _apply31(0, own=False)["report"][:60])

