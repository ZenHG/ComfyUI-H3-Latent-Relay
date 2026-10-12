# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
#
# ruff: noqa: F821  —— 本分片**不是独立模块**。
#   它由 `tests/test_relay_core.py` 用 `exec(compile(src, path, "exec"), globals())`
#   顺序执行：与拆分前的单文件脚本**同一命名空间、同一顺序**。
#   所以 `check` / `CORE` / `NT` / `NODES` / `av_latent` 都看得见，但不是本文件定义的 ⇒ F821 必报。
#   真正的兜底是**执行**：这条脚本每次全量跑，任何未定义名会当场 NameError，不会静默。

# ============ 36 · 空间分块放大（长边单轴切条 + 核心区独占窗 + 余弦羽化）============
# 背景（2026-10-12，《追痕》18 段实盘）：§22 的时间分块是**唯一**的显存旋钮，而它恰恰改画面
# —— 3D 放大器的 TemporalConv（时间核 5）在块边界换上下文。低显存用户没有「只省显存」的档。
# 空间分块沿**长边单轴**切条：每块的**时间轴完整**（时间上下文一点不丢），代价换成空间边界
# 的感受野变窄 ⇒ 用重叠 + 核心区独占窗 + 余弦羽化把它压进过渡带。
#
# 🔴 本组测的是**几何与拼接**，不是画质。画质必须人工目检（节点 docstring 已写死这条）。

print()
print("=" * 78)
print("36) 空间分块放大：不动时间上下文的显存旋钮（0.6.33 新增）")
print("=" * 78)


def _tri36(seg, th, tw):
    """假放大器：三线性插值到**请求的**尺寸（可解析、逐格可比）。"""
    return torch.nn.functional.interpolate(
        seg, size=(seg.shape[2], th, tw), mode="trilinear", align_corners=False)


# ---- 36.1 尺寸口径：三档 mode 与上游 execute 逐行同口径（手算值钉死）----
check("36.1 尺寸口径三档：×2.0(44×80)→88×160；×1.1→48×88；目标 1280×704→80×44（宽高不交换）",
      CORE.upscale_out_size(44, 80, "scale by multiplier", scale=2.0) == (88, 160)
      and CORE.upscale_out_size(44, 80, "scale by multiplier", scale=1.1) == (48, 88)
      and CORE.upscale_out_size(44, 80, "target dimensions", width=1280, height=704) == (44, 80),
      "×2=%s ×1.1=%s target=%s"
      % (CORE.upscale_out_size(44, 80, "scale by multiplier", scale=2.0),
         CORE.upscale_out_size(44, 80, "scale by multiplier", scale=1.1),
         CORE.upscale_out_size(44, 80, "target dimensions", width=1280, height=704)))

# ---- 36.2 长边判定 + 核心区恰好平铺 ----
_HI36, _WI36 = 44, 80
_HO36, _WO36 = CORE.upscale_out_size(_HI36, _WI36, "scale by multiplier", scale=2.0)
_pl36 = CORE.spatial_tile_plan(_HI36, _WI36, _HO36, _WO36, tiles=3, overlap=8, feather=4)
_ys36 = [t["p"] + t["lo"] for t in _pl36["tiles"]] + [_pl36["tiles"][-1]["p"] + _pl36["tiles"][-1]["hi"]]
check("36.2 横幅切 W：核心区边界恰好平铺 [0,160)、严格递增（首块从 0 起、末块到轴长止）",
      _pl36["axis"] == "W" and _pl36["n_tiles"] == 3 and _ys36[0] == 0
      and _ys36[-1] == _WO36 and all(b > a for a, b in zip(_ys36, _ys36[1:], strict=False)),
      "axis=%s ys=%s" % (_pl36["axis"], _ys36))

_pl36h = CORE.spatial_tile_plan(_WI36, _HI36, _WO36, _HO36, tiles=3, overlap=8, feather=4)
check("36.3 竖幅切 H（长边换轴，判据只认长边、不写死 W）",
      _pl36h["axis"] == "H" and _pl36h["n_tiles"] == 3
      and [t["p"] + t["lo"] for t in _pl36h["tiles"]] + [
          _pl36h["tiles"][-1]["p"] + _pl36h["tiles"][-1]["hi"]] == [0, 54, 108, 160],
      "axis=%s" % _pl36h["axis"])

# ---- 36.4 每块请求尺寸：是 grid 的倍数、且覆盖「核心 + 羽化带」----
#    ⚠ 末块右边**没有邻居** ⇒ 不做右羽化 ⇒ 它的核心可以正好顶到自己的输出末端（不算违规）。
_bad36 = []
for _i36, _t36 in enumerate(_pl36["tiles"]):
    _n36 = _t36["o"]
    _last36 = (_i36 == len(_pl36["tiles"]) - 1)
    if _n36 % _pl36["grid"]:
        _bad36.append("o=%d 不是 grid 倍数" % _n36)
    if _t36["lo"] < 0 or (_t36["hi"] + (0 if _last36 else _pl36["half"])) > _n36:
        _bad36.append("核心/羽化带装不下：%s" % _t36)
    if _t36["req_h"] != _HO36 or _t36["req_w"] != _n36:
        _bad36.append("请求尺寸对不上：%s" % _t36)
check("36.4 每块请求尺寸 = grid 的倍数、非切轴取整段尺寸、且装得下「核心 + 羽化带」",
      _bad36 == [], "%s" % (_bad36[:2],))

# ---- 36.5 常量 mock：核心区逐格 == 本块常量（落位精确）、过渡单调 ----
_v36 = torch.randn(1, 4, 24, _HI36, _WI36, generator=torch.Generator().manual_seed(36))


def _const36(vals):
    it = iter(vals)

    def f(seg, th, tw):
        return torch.full((seg.shape[0], seg.shape[1], seg.shape[2], th, tw),
                          float(next(it)), dtype=torch.float32)
    return f


_out36 = CORE.spatial_tile_upscale(
    _v36, _const36([t["in0"] + 1 for t in _pl36["tiles"]]), _pl36)
_line36 = _out36[0, 0, 0, _HI36 // 2, :]
_bad36b = []
for _t36 in _pl36["tiles"]:
    _seg36 = _line36[_t36["p"] + _t36["lo"] + 1: _t36["p"] + _t36["hi"] - 1]
    if _seg36.numel() and not torch.allclose(
            _seg36, torch.full_like(_seg36, float(_t36["in0"] + 1))):
        _bad36b.append((_t36["in0"], float(_seg36.min()), float(_seg36.max())))
check("36.5 常量 mock：每块**核心区逐格 == 本块常量**（落位/裁剪精确到格，不是大致对）",
      _bad36b == [], "%s" % (_bad36b[:2],))
check("36.6 过渡带单调（权重互补 ⇒ 不会来回震荡；余弦过渡最大单格跃迁 = 半个台阶）",
      bool((( _line36[1:] - _line36[:-1]) >= -1e-6).all())
      and float((_line36[1:] - _line36[:-1]).abs().max()) <= (47 - 1) * 0.5 + 1e-6,
      "最大单格跃迁=%.4f" % float((_line36[1:] - _line36[:-1]).abs().max()))

# ---- 36.7 比例可精确表示 ⇒ 与「整段一次过」**逐位相同**（只差浮点尾数）----
_same36 = []
for _tl36 in (2, 3, 4):
    _p = CORE.spatial_tile_plan(_HI36, _WI36, _HO36, _WO36, tiles=_tl36, overlap=8, feather=4)
    _got = CORE.spatial_tile_upscale(_v36, _tri36, _p)
    _ref = _tri36(_v36, _HO36, _WO36)
    _md = float((_got - _ref).abs().max())
    if _md > 1e-6:
        _same36.append("%d块 max|d|=%.3e" % (_tl36, _md))
check("36.7 🔴 比例可精确表示（×2.0：整段 160/80、块内同样 2:1）⇒ 分块结果与整段**逐位相同**"
      "（max|Δ| ≤ 1e-6，只差浮点尾数）",
      _same36 == [], "%s" % (_same36,))

# ---- 36.8 比例不可精确表示 ⇒ 偏差小，且**缝带误差不大于非缝带**（没有额外阶跃）----
_hi8, _wi8 = 44, 80
_ho8, _wo8 = CORE.upscale_out_size(_hi8, _wi8, "scale by multiplier", scale=1.1)
_x8 = torch.linspace(0, 1, _wi8).view(1, 1, 1, 1, _wi8)
_v8 = ((_x8 * 2.0 - 1.0).expand(1, 4, 24, _hi8, _wi8).contiguous()
       + 0.1 * torch.sin(6.0 * torch.linspace(0, 1, _hi8).view(1, 1, 1, _hi8, 1)))
_worst8, _detail8 = 0.0, []
for _tl8 in (2, 3, 4):
    _p8 = CORE.spatial_tile_plan(_hi8, _wi8, _ho8, _wo8, tiles=_tl8, overlap=8, feather=4)
    _o8 = CORE.spatial_tile_upscale(_v8, _tri36, _p8)
    _f8 = _tri36(_v8, _ho8, _wo8)
    _d8 = (_o8 - _f8).abs()[0, 0, 0, _hi8 // 2]
    _seam8 = torch.zeros(_wo8, dtype=torch.bool)
    for _t8 in _p8["tiles"][1:]:
        _seam8[max(0, _t8["p"] + _t8["lo"] - _p8["half"]):
                min(_wo8, _t8["p"] + _t8["lo"] + _p8["half"])] = True
    _worst8 = max(_worst8, float(_d8.max()))
    if _seam8.any() and (~_seam8).any() and float(_d8[_seam8].max()) > float(_d8[~_seam8].max()):
        _detail8.append("%d块 缝带 %.4f > 非缝带 %.4f"
                        % (_tl8, float(_d8[_seam8].max()), float(_d8[~_seam8].max())))
check("36.8 ×1.1（比例除不尽）⇒ 与整段的内容差 ≤1.5% 满量程（实测上限，非推导）",
      _worst8 < 0.03, "max|d|=%.5f（信号满量程 2.0）" % _worst8)
check("36.9 🔴 缝带内误差 **不大于**非缝带（拼接没有制造额外阶跃；否则说明羽化带在打架）",
      _detail8 == [], "%s" % (_detail8,))

# ---- 36.10 非法参数一律 raise（铁律七：不静默改参数）----
expect_raise("36.10 tiles<1 / overlap<0 / feather<0 ⇒ raise",
             lambda: CORE.spatial_tile_plan(_HI36, _WI36, _HO36, _WO36, tiles=0))
expect_raise("36.11 align 与 downsample 不匹配（align=24）⇒ raise 并指路 align=32",
             lambda: CORE.spatial_tile_plan(_HI36, _WI36, _HO36, _WO36, tiles=2, align=24),
             "align 设回 32")
expect_raise("36.12 重叠太窄装不下羽化带 ⇒ raise 并指路 spatial_overlap",
             lambda: CORE.spatial_tile_plan(_HI36, _WI36, _HO36, _WO36, tiles=4, overlap=1, feather=8),
             "spatial_overlap")
expect_raise("36.13 未知 mode ⇒ raise（不许猜一个默认档）",
             lambda: CORE.upscale_out_size(_HI36, _WI36, "nope"), "未知 mode")

# ---- 36.14 拼接侧的防线：帧数/尺寸/秩/输入尺寸/非张量 ----
expect_raise("36.14 放大器改了帧数 ⇒ 当场 raise（空间分块只允许放大空间）",
             lambda: CORE.spatial_tile_upscale(
                 _v36, lambda s, th, tw: _tri36(s, th, tw)[:, :, :-1], _pl36), "帧数不许变")
expect_raise("36.15 上游返回的空间尺寸与请求不符 ⇒ raise（否则拼接静默错位）",
             lambda: CORE.spatial_tile_upscale(
                 _v36, lambda s, th, tw: _tri36(s, th, tw - 2), _pl36), "与请求的")
expect_raise("36.16 秩不对（4D）⇒ raise",
             lambda: CORE.spatial_tile_upscale(_v36[0], _tri36, _pl36))
expect_raise("36.17 plan 与 latent 的输入尺寸不符 ⇒ raise（不许拿错尺子拼）",
             lambda: CORE.spatial_tile_upscale(_v36[:, :, :, :, :40], _tri36, _pl36), "与 latent 的")
expect_raise("36.18 放大器不返回张量 ⇒ raise",
             lambda: CORE.spatial_tile_upscale(_v36, lambda s, th, tw: "x", _pl36))

# ---- 36.19 常量锁 + 大块数仍合规（不静默退化、也不静默崩）----
check("36.19 默认值锁死：空间重叠 8 格 / 接缝羽化 4 格（换实现不许换默认）",
      CORE.UPSCALE_TILE_OVERLAP_DEFAULT == 8 and CORE.UPSCALE_TILE_FEATHER_DEFAULT == 4,
      "ov=%d feather=%d" % (CORE.UPSCALE_TILE_OVERLAP_DEFAULT, CORE.UPSCALE_TILE_FEATHER_DEFAULT))
_pl36b = CORE.spatial_tile_plan(_HI36, _WI36, _HO36, _WO36, tiles=12, overlap=8, feather=4)
_ys36b = [t["p"] + t["lo"] for t in _pl36b["tiles"]] + [
    _pl36b["tiles"][-1]["p"] + _pl36b["tiles"][-1]["hi"]]
check("36.20 12 块（远超 stride）仍给出**合规**几何：核心区平铺不重叠、每块请求尺寸合法",
      _pl36b["n_tiles"] >= 2 and _ys36b[0] == 0 and _ys36b[-1] == _WO36
      and all(b > a for a, b in zip(_ys36b, _ys36b[1:], strict=False))
      and all(t["o"] % _pl36b["grid"] == 0
              and t["hi"] + (0 if i == len(_pl36b["tiles"]) - 1 else _pl36b["half"]) <= t["o"]
              for i, t in enumerate(_pl36b["tiles"])),
      "n=%d ys=%s" % (_pl36b["n_tiles"], _ys36b[:6]))

# ---- 36.21~36.24 节点层：真的按块调上游、音频逐位不动、关掉时逐位同旧版 ----
class _FakeUp36:
    """替身上游：按请求尺寸三线性插值（时间维不动），并记录被怎么调用。"""
    calls = []

    @classmethod
    def execute(cls, latent, model_name, mode, align, enable_temporal_chunking,
                force_unload, device, precision):
        cls.calls.append({"mode": dict(mode), "align": align, "chunking": enable_temporal_chunking})
        s = latent["samples"]
        if mode.get("mode") == "target dimensions":
            th, tw = int(mode["height"]) // 16, int(mode["width"]) // 16
            out = torch.nn.functional.interpolate(s, size=(s.shape[2], th, tw), mode="nearest")
        else:
            out = torch.nn.functional.interpolate(s, scale_factor=(1, 2, 2), mode="nearest")
        return ({"samples": out},)


_REG36 = {"MinimaxH3LatentUpscaler3D": _FakeUp36}
_ORIG36 = NODES._comfy_registry
NODES._comfy_registry = lambda: _REG36
try:
    _lat36 = av_latent(24, a_ticks=16, seed=17, w=80, h=44)
    _a36 = CORE.audio_from_latent(_lat36).clone()
    _FakeUp36.calls.clear()
    _out36n, _rep36 = NODES.H3RelayLatentUpscale().upscale(
        _lat36, "fake_h3_3d.safetensors", "scale by multiplier", 2.0, 1280, 704, 1.0,
        chunks=1, overlap=5, align=32, precision="fp32", device="cpu", force_unload=False,
        spatial_tiles=3, spatial_overlap=8, seam_feather=4)
    _v36n = CORE.video_from_latent(_out36n)
    _a36n = CORE.audio_from_latent(_out36n)
    check("36.21 节点层：spatial_tiles=3 ⇒ 调上游 **3 次**，每次走 target dimensions 且显式尺寸",
          len(_FakeUp36.calls) == 3
          and all(c["mode"]["mode"] == "target dimensions" for c in _FakeUp36.calls)
          and all(c["mode"]["width"] % 32 == 0 and c["mode"]["height"] % 32 == 0
                  for c in _FakeUp36.calls),
          "%d 次 %s" % (len(_FakeUp36.calls), _FakeUp36.calls[0]["mode"] if _FakeUp36.calls else "-"))
    check("36.22 节点层：输出尺寸 == 整段口径（88×160），时间维不动，**音频逐位不变**",
          tuple(_v36n.shape) == (1, 4, 24, 88, 160) and torch.equal(_a36n, _a36),
          "video=%s" % (tuple(_v36n.shape),))
    check("36.23 report 写明空间分块（块数/重叠/羽化/轴）——不静默",
          "空间分块" in _rep36 and "羽化" in _rep36, _rep36.split("|")[-1][:80])
    _FakeUp36.calls.clear()
    _out36c, _rep36c = NODES.H3RelayLatentUpscale().upscale(
        _lat36, "fake_h3_3d.safetensors", "scale by multiplier", 2.0, 1280, 704, 1.0,
        chunks=1, overlap=5, align=32, precision="fp32", device="cpu", force_unload=False,
        spatial_tiles=1, spatial_overlap=8, seam_feather=4)
    check("36.24 🔴 spatial_tiles=1 ⇒ 逐位同旧版（1 次调用、用**用户自己的 mode**，不走空间路）",
          len(_FakeUp36.calls) == 1
          and _FakeUp36.calls[0]["mode"] == {"mode": "scale by multiplier", "scale": 2.0}
          and "空间分块" not in _rep36c,
          "%d 次 %s" % (len(_FakeUp36.calls), _FakeUp36.calls[0]["mode"] if _FakeUp36.calls else "-"))
finally:
    NODES._comfy_registry = _ORIG36

# ---- 36.25~36.30 多维度自查（2026-10-12）：退化 / 极端 / 开销 / 叠加 ----
# 背景：这六条是**自查**（不是验收）抓出来的 —— 抓到的两条真 bug 已修，各留一条反向锁。

# 36.25 4D 单帧 latent 也必须吃 `spatial_tiles`（不许静默跳过用户显式设的旋钮）
_FakeUp36.calls.clear()
NODES._comfy_registry = lambda: _REG36
try:
    _lat36d = {"samples": torch.randn(1, 4, _HI36, _WI36,
                                      generator=torch.Generator().manual_seed(360))}
    # ⚠ 单帧 ⇒ T=1 ⇒ 时间分块那条重叠约束（每块 ≥ 2·overlap+1 帧）要求 overlap=0
    #   —— 这是**已有**的时间分块契约（与 test 27.15 同口径），不是空间分块引入的。
    _out36d, _rep36d = NODES.H3RelayLatentUpscale().upscale(
        _lat36d, "fake_h3_3d.safetensors", "scale by multiplier", 2.0, 1280, 704, 1.0,
        chunks=1, overlap=0, align=32, precision="fp32", device="cpu", force_unload=False,
        spatial_tiles=2, spatial_overlap=8, seam_feather=4)
    # ⚠ 看**原始输出**：`video_from_latent` 对 4D 会当「缺 B」再补一维（既知约定），
    #   而本节点与上游同口径 = **4D 进 4D 出**。
    check("36.25 4D 单帧 latent 也走空间路（2 块 ⇒ 调上游 2 次、4D 进 4D 出、空间翻倍）",
          len(_FakeUp36.calls) == 2
          and tuple(_out36d["samples"].shape) == (1, 4, _HO36, _WO36),
          "%d 次 / %s" % (len(_FakeUp36.calls), tuple(_out36d["samples"].shape)))
finally:
    NODES._comfy_registry = _ORIG36

# 36.26 🔴 反向锁：**块数太多**要报「调小 spatial_tiles」，**不许**报「调大 spatial_overlap」
#   ⚠ 必须用**比例 < grid**（这里 ×1.1：轴长 88 格、每条至少 2 格 ⇒ 最多 44 条）的配置 ——
#     ×2.0 时轴长 160 格、最多 80 条 == 输入轴长 ⇒ 这条判据**永远触发不到**（会测成假绿）。
expect_raise("36.26 块数超过长边容得下的条数 ⇒ 报错处方是**调小 spatial_tiles**（调大重叠只会更糟）",
             lambda: CORE.spatial_tile_plan(_hi8, _wi8, _ho8, _wo8, tiles=60, overlap=8, feather=4),
             "spatial_tiles 调到")

# 36.27 退化重叠（两条裁同一块 = 显存不省、算力翻倍）⇒ 当场报错
expect_raise("36.27 重叠大到「两条裁的是同一块」⇒ raise（不许静默白跑一遍）",
             lambda: CORE.spatial_tile_plan(_HI36, _WI36, _HO36, _WO36, tiles=3, overlap=60, feather=4),
             "同一块")

# 36.28 🔴 反向锁：末几条的**输出自然溢出画面**是**合法**的（超出部分会被丢弃），
#      旧判据用「p+o ≤ 轴长」直接拒了它、还给了错处方 ⇒ 这里钉住它必须**能过**。
_p36x = CORE.spatial_tile_plan(_hi8, _wi8, _ho8, _wo8, tiles=40, overlap=8, feather=4)
_ys36x = [t["p"] + t["lo"] for t in _p36x["tiles"]] + [
    _p36x["tiles"][-1]["p"] + _p36x["tiles"][-1]["hi"]]
check("36.28 高块数下「末几条输出溢出画面」仍给合法几何（核心区平铺、羽化带都在画面内）",
      _p36x["n_tiles"] == 40 and _ys36x[0] == 0 and _ys36x[-1] == _wo8
      and all(b > a for a, b in zip(_ys36x, _ys36x[1:], strict=False))
      and any(t["p"] + t["o"] > _wo8 for t in _p36x["tiles"]),
      "n=%d 末核心=%d 溢出条数=%d"
      % (_p36x["n_tiles"], _ys36x[-1], sum(1 for t in _p36x["tiles"] if t["p"] + t["o"] > _wo8)))

# 36.29 💰 开销回归锁：默认档（重叠 8）下「各块输入覆盖总和 / 长边」必须 ≤ 1.6
#      （重叠区每块都要重跑 ⇒ 这是本功能的**真实代价**；改实现不许让它悄悄涨上去）
_cov36 = {}
for _tl36c in (2, 3, 4):
    _pc = CORE.spatial_tile_plan(_HI36, _WI36, _HO36, _WO36, tiles=_tl36c, overlap=8, feather=4)
    _cov36[_tl36c] = sum(t["in1"] - t["in0"] for t in _pc["tiles"]) / float(_WI36)
check("36.29 默认重叠 8 下：2/3/4 块的输入覆盖 ≤ 1.6× 长边（重叠的算力代价有上界）",
      max(_cov36.values()) <= 1.6,
      "覆盖倍数 %s" % {k: round(v, 3) for k, v in _cov36.items()})

# 36.30 两个旋钮可叠加：chunks=2 × spatial_tiles=2 ⇒ 上游 4 次调用（时间外 / 空间内）
_FakeUp36.calls.clear()
NODES._comfy_registry = lambda: _REG36
try:
    _out36s, _ = NODES.H3RelayLatentUpscale().upscale(
        av_latent(24, a_ticks=16, seed=18, w=80, h=44), "fake_h3_3d.safetensors",
        "scale by multiplier", 2.0, 1280, 704, 1.0,
        chunks=2, overlap=5, align=32, precision="fp32", device="cpu", force_unload=False,
        spatial_tiles=2, spatial_overlap=8, seam_feather=4)
    check("36.30 时间分块 × 空间分块可叠加：chunks=2 + spatial_tiles=2 ⇒ 上游 4 次调用、输出尺寸不变",
          len(_FakeUp36.calls) == 4
          and tuple(CORE.video_from_latent(_out36s).shape) == (1, 4, 24, _HO36, _WO36),
          "%d 次 / %s" % (len(_FakeUp36.calls),
                          tuple(CORE.video_from_latent(_out36s).shape)))
finally:
    NODES._comfy_registry = _ORIG36

# ---- 36.31~36.32 开源用户卡点（2026-10-12 收尾自查）----
# ① 老图**按位置存**的 widget 值会在本版新增参数后错位（前端自加的 `h3_i18n_bar` 空串落到
#    新参数上）⇒ 直接 `int("")` 会当场炸。判据 = **不许炸、退回默认值、并出声**。
_FakeUp36.calls.clear()
NODES._comfy_registry = lambda: _REG36
try:
    _out36e, _rep36e = NODES.H3RelayLatentUpscale().upscale(
        av_latent(24, a_ticks=16, seed=19, w=80, h=44), "fake_h3_3d.safetensors",
        "scale by multiplier", 2.0, 1280, 704, 1.0,
        chunks=1, overlap=5, align=32, precision="fp32", device="cpu", force_unload=False,
        spatial_tiles="", spatial_overlap="", seam_feather="")
    check("36.31 老图槽位错位（三个新参数读到空串）⇒ 不炸、退回默认值、**按旧路径**跑（1 次调用、用用户 mode）",
          len(_FakeUp36.calls) == 1
          and _FakeUp36.calls[0]["mode"] == {"mode": "scale by multiplier", "scale": 2.0}
          and "空间分块" not in _rep36e,
          "%d 次 / %s" % (len(_FakeUp36.calls), _FakeUp36.calls[0]["mode"] if _FakeUp36.calls else "-"))
finally:
    NODES._comfy_registry = _ORIG36

# ② 上游 `width`/`height` 的 `min=64`（像素）⇒ 块比它小，请求连发都发不出去（会报上游的校验错）。
expect_raise("36.32 块太窄（输出 < 上游 `width`/`height` 的最小 64 像素）⇒ 报错点名上游下限与处方",
             lambda: CORE.spatial_tile_plan(6, 6, 6, 6, tiles=3, overlap=0, feather=0),
             "最小是 64 像素")
