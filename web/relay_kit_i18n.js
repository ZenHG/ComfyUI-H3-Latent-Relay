// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
// 第三方出处与许可见 THIRD-PARTY-NOTICES.md
//
// H3 Latent Relay · 画布侧中英切换（0.6.28）—— **纯函数层**（可 node 单测，零 DOM）
//
// 为什么单独一个文件：词表与改写逻辑是这个功能的**全部风险**所在
// （改错一个 key 会静默把用户的图标签弄坏），必须能在没有浏览器的情况下测。
// 挂钩/按钮在 `relay_kit_i18n_ui.js`。
//
// 🔴 铁律（三条，抄自 `relay_kit_refs.js` 的同类约束）：
//   ① **只改 `label` / `title`，绝不改 `name`** —— `name` 是后端取参与序列化的依据，
//      改它等于改图（工作流 JSON 会脏、连线会丢、下一个版本的读回会错位）。
//   ② **只改显示**，绝不改 `widget.options.values`（那是**存档值**：模型文件名、
//      段号、路径、用户自定义名一律不翻）。枚举的中文显示走官方
//      `options.getOptionLabel` 钩子（函数不进 JSON ⇒ 分享出去不污染别人的图）。
//   ③ **用户的自定义标题要保留** —— 只在标题「还是任一语言的默认名」时才替换。
//   ④ **悬浮提示：中文只在后端写一份**（`nodes.py` 的 `tooltip`），本表**只存 `en`**。
//      ⇒ 同一个参数不可能出现两套中文说法（历史上本表存过 `zh`，于是两边漂开）。
//      切中文时本模块**什么都不写**（`tipFor` 返回 `null`）⇒ 前端自动回落后端原文；
//      切英文时才写 `w.tooltip`（前端取 `w.tooltip || 后端原文` ⇒ 覆盖那一次）。
//      存档序列化只写 `widget.value` ⇒ 提示翻不了图、分享不出去。
//      词表里没有的组合 ⇒ 一个字节都不动（宁缺勿错）。
//
// 🔴 与 `relay_kit_refs_ui.js` 的**冲突消解**（两个模块都写 `node.title`，必须共存）：
//   额度提示是**追加在标题末尾**的（`budgetTitle` 拼字符串）⇒ 若本模块整段替换标题，
//   会把别人的提示抹掉；顺序反了也可能互相覆盖。
//   ⇒ `swapTitle()` 只替换**前缀**（默认名部分），**原样保留后缀** ⇒ 谁先谁后都对。

/** 语言存取键（**刻意不与别的包共用**：两包各管各的，避免互相覆盖 localStorage）。 */
export const LANG_KEY = "h3relay_lang";
/** 语言变化广播事件名（本包私有，不污染 window 上的公共事件）。 */
export const LANG_EVENT = "h3relay:lang-changed";

/** 归一化：只认 `zh` / `en`，其余（含 undefined）⇒ 落回 `en`。 */
export function normLang(v) {
    const s = String(v == null ? "" : v).trim().toLowerCase();
    return s === "zh" || s.startsWith("zh-") || s.startsWith("zh_") ? "zh" : "en";
}

/**
 * 默认语言 = **宿主界面语言**（由调用方去读，见 `relay_kit_i18n_ui.js::readHostLang`：
 * 先 `<html lang>`，旧前端再试 `Comfy.Locale`）。
 * 🔴 为什么不默认中文：本机（和很多用户）装了 Global Translation 之类的界面汉化插件，
 * 界面是英文时若本包硬把节点翻成中文 ⇒ 界面英文 + 节点中文 = 更乱。
 * ⇒ 跟随宿主，用户点节点上的「中/EN」才覆盖（存 localStorage）。
 */
export function defaultLang(readHostLang) {
    try {
        const raw = typeof readHostLang === "function" ? readHostLang() : "";
        const s = String(raw || "").trim().toLowerCase();
        if (s) return normLang(s);
    } catch (e) { /* 读不到就按下面的兜底 */ }
    try {
        const nav = (globalThis.navigator && globalThis.navigator.language) || "";
        if (nav) return normLang(nav);
    } catch (e) { /* 无 navigator（node 单测）*/ }
    return "en";
}

// ---------------------------------------------------------------------------
// 词表 · 节点标题（class_type → 标题）
// 🔴 中文名沿用 `docs/04` 的「术语 ↔ 节点标签对照」表，不另造词。
// ---------------------------------------------------------------------------
export const H3_NODES = {
    H3RelayLatentSave: { zh: "🔗 H3 Relay · 潜空间落盘", en: "🔗 H3 Relay · Latent Save" },
    H3RelayLatentLoad: { zh: "🔗 H3 Relay · 读上段潜空间", en: "🔗 H3 Relay · Latent Load" },
    H3RelayCopyBridge: { zh: "🔗 H3 Relay · 复合桥", en: "🔗 H3 Relay · Copy Bridge" },
    H3RelayTrimAV: { zh: "🔗 H3 Relay · 裁重叠", en: "🔗 H3 Relay · Trim AV" },
    H3RelayPost: { zh: "🔗 H3 Relay · 后处理", en: "🔗 H3 Relay · Post" },
    H3RelayAudioSeam: { zh: "🔗 H3 Relay · 音频缝", en: "🔗 H3 Relay · Audio Seam" },
    H3RelayChain: { zh: "🔗 H3 Relay · 连跑控制", en: "🔗 H3 Relay · Chain" },
    H3RelayLatentUpscale: { zh: "🔍 H3 Relay · 潜空间分块放大", en: "🔍 H3 Relay · Latent Upscale" },
};

// ---------------------------------------------------------------------------
// 词表 · 参数（widget name → 标签）。**扁平一张表**（同名参数在多个节点里同义）。
// 🔴 `tools/review_050.py` 的 J-i18n 项会核对「nodes.py 的 INPUT_TYPES 键 ⊆ 本表」
//    ⇒ 以后新增参数忘了翻译 ⇒ 机检当场红。
// ---------------------------------------------------------------------------
export const H3_WIDGETS = {
    // 通用 / 段管理
    latent: { zh: "潜空间", en: "latent" },
    run_id: { zh: "片名", en: "film name" },
    stage_index: { zh: "段号", en: "stage" },
    note: { zh: "备注", en: "note" },
    explicit_path: { zh: "显式路径", en: "explicit path" },
    // 桥
    context_latent: { zh: "上段潜空间", en: "context latent" },
    context_frames: { zh: "拷贝窗帧数", en: "copy window" },
    conditioning: { zh: "条件", en: "conditioning" },
    mask_mode: { zh: "掩码模式", en: "mask mode" },
    taper_tokens: { zh: "渐缩 token 数", en: "taper tokens" },
    seam_min: { zh: "缝端掩码下限", en: "seam floor" },
    pin_audio: { zh: "钉住音频", en: "pin audio" },
    ramp_top: { zh: "斜坡上限", en: "ramp top" },
    ramp_tokens: { zh: "斜坡 token 数", en: "ramp tokens" },
    // ⚠️ 命名历史坑（2026-10-14 修正显示名）：`anchor_*` 是 0.5.0 的**统计纠偏基准**（走 latent 统计量），
    //   `ref_anchor_*` 才是 0.6.0 的**真外观锚**（走 conditioning 的 minimax_refs）。
    //   旧显示名把两者叫反了（统计纠偏叫「外观锚」、真外观锚叫「参考锚」）⇒ 画布上一眼分不清。
    anchor_latent: { zh: "统计纠偏基准", en: "statistical reference" },
    anchor_blend: { zh: "纠偏强度", en: "correction strength" },
    blend_top: { zh: "混合上限", en: "blend top" },
    blend_tokens: { zh: "混合 token 数", en: "blend tokens" },
    blend_shape: { zh: "混合窗形", en: "blend shape" },
    window_top: { zh: "窗峰值", en: "window peak" },
    window_shape: { zh: "窗函数", en: "window shape" },
    ref_anchor_latent: { zh: "外观锚（接线）", en: "appearance anchor" },
    ref_anchor_stage: { zh: "外观锚段号", en: "appearance anchor stage" },
    ref_anchor_frames: { zh: "外观锚帧数", en: "appearance anchor frames" },
    audio_ref_seconds: { zh: "音频参考窗", en: "audio ref window" },
    voice_anchor: { zh: "声锚", en: "voice anchor" },
    voice_anchor_stage: { zh: "声锚段号", en: "voice anchor stage" },
    exp_anchor: { zh: "实验开关", en: "experiment switch" },
    // 裁重叠
    images: { zh: "图像", en: "images" },
    trim_frames: { zh: "裁剪帧数", en: "trim frames" },
    fps: { zh: "帧率", en: "fps" },
    audio: { zh: "音频", en: "audio" },
    settle_frames: { zh: "沉降帧数", en: "settle frames" },
    seam_ghost: { zh: "缝帧重影", en: "seam ghost" },
    seam_ghost_alpha: { zh: "重影强度", en: "ghost strength" },
    settle_sharpen: { zh: "糊区锐化", en: "soft-zone sharpen" },
    settle_sharpen_frames: { zh: "锐化帧数", en: "sharpen frames" },
    lowfreq_pull: { zh: "低频残差", en: "low-freq transfer" },
    lowfreq_frames: { zh: "低频帧数", en: "low-freq frames" },
    lowfreq_blur: { zh: "低频尺度", en: "low-freq scale" },
    deconv_strength: { zh: "反卷积强度", en: "deconv strength" },
    deconv_radius: { zh: "反卷积半径", en: "deconv radius" },
    detail_borrow: { zh: "段体高频迁移", en: "high-freq transfer" },
    detail_blur: { zh: "高频分离尺度", en: "high-freq scale" },
    hist_match: { zh: "直方图匹配", en: "hist match" },
    wb_match: { zh: "白平衡匹配", en: "wb match" },
    match_prev: { zh: "跨段统计匹配", en: "cross-segment match" },
    match_prev_frames: { zh: "匹配帧数", en: "match frames" },
    match_prev_gain_max: { zh: "增益上限", en: "gain cap" },
    match_prev_offset_max: { zh: "偏移上限", en: "offset cap" },
    save_pcm: { zh: "存无损音轨", en: "save lossless" },
    diagnostics: { zh: "诊断", en: "diagnostics" },
    // 后处理
    guide: { zh: "缝另一侧帧", en: "seam-opposite frame" },
    head_zone_frames: { zh: "段头作用帧数", en: "head zone" },
    settle_auto: { zh: "自适应糊区补偿", en: "adaptive soft-zone" },
    match_prev_stats_frames: { zh: "统计取几帧", en: "stats frames" },
    baseline: { zh: "段体参考", en: "body reference" },
    cross_seg_ack: { zh: "跨段总闸", en: "cross-segment switch" },
    // 放大
    model_name: { zh: "模型", en: "model" },
    mode: { zh: "放大方式", en: "mode" },
    scale: { zh: "放大倍数", en: "scale" },
    width: { zh: "宽", en: "width" },
    height: { zh: "高", en: "height" },
    megapixels: { zh: "兆像素", en: "megapixels" },
    chunks: { zh: "时间分块数", en: "time chunks" },
    overlap: { zh: "块重叠", en: "overlap" },
    align: { zh: "对齐网格", en: "align" },
    precision: { zh: "精度", en: "precision" },
    device: { zh: "设备", en: "device" },
    force_unload: { zh: "跑完卸载", en: "unload after" },
    spatial_tiles: { zh: "空间分块数", en: "spatial tiles" },
    spatial_overlap: { zh: "空间重叠", en: "spatial overlap" },
    seam_feather: { zh: "接缝羽化", en: "seam feather" },
    // 音频缝
    patch_seconds: { zh: "头部补丁秒数", en: "head patch" },
    tile_seconds: { zh: "床环瓦片秒数", en: "bed tile" },
    fade_seconds: { zh: "淡变秒数", en: "fade seconds" },
    bed_stage: { zh: "床声段号", en: "bed stage" },
    bed_select: { zh: "床声从哪取", en: "bed source" },
    join_curve: { zh: "拼接曲线", en: "join curve" },
    join_prime_ms: { zh: "丢前置毫秒", en: "drop priming" },
    join_cross_ms: { zh: "交叉淡变毫秒", en: "cross-fade ms" },
    join_segment_seconds: { zh: "每段有效秒数", en: "segment seconds" },
    join_align_seconds: { zh: "画面裁量秒数", en: "picture trim s" },
    patch_guard: { zh: "台词守卫", en: "dialogue guard" },
    patch_guard_layers: { zh: "台词守卫档位", en: "guard level" },
    declick_ratio: { zh: "去咔判据", en: "declick ratio" },
    declick_quiet_dbfs: { zh: "去咔静噪门限", en: "quiet floor" },
    declick_max_len_ms: { zh: "去咔最长时长", en: "max click ms" },
    cut_head_frames: { zh: "裁头帧数", en: "cut head frames" },
    loudness_normalize: { zh: "响度均衡", en: "loudness normalize" },
    loudness_target_dbfs: { zh: "响度目标", en: "loudness target" },
    // 连跑
    segments: { zh: "跑到第几段", en: "stop at segment" },
    status: { zh: "状态", en: "status" },
    prompts: { zh: "提示词", en: "prompts" },
    prompt_target: { zh: "已废弃", en: "deprecated" },
    auto_concat: { zh: "自动拼接", en: "auto concat" },
    concat_name: { zh: "成片名", en: "film name" },
    audio_out: { zh: "成片音轨", en: "film audio" },
    video_crf: { zh: "成片画质", en: "film quality" },
    concat_result: { zh: "成片路径", en: "film path" },
};

// ---------------------------------------------------------------------------
// 词表 · 悬浮提示（`class_type.参数名` → 精简双语的 tooltip）
// ---------------------------------------------------------------------------
// 🔴 为什么键是 `class_type.参数名`、不是光参数名：同名参数在不同节点里**语义不同**
//    （`run_id` 在 Save 是「必填片子名」、在 Chain 是「续跑用，可留空」；
//      `stage_index` 在 5 个节点各有一套）。合并成一条 = 丢语义。
// 🔴 为什么精简：`nodes.py` 的原版 tooltip 最长 1084 字符（含历史沿革、踩坑叙事、
//    已废弃分支说明）。悬浮提示是**速查**，不是手册 —— 每格只留
//    ① 这句参数干什么 ② 默认/推荐填什么。长篇依据在 docs/ 与 CHANGES.md。
// 🔴 `tools/review_050.py` 的 H3p 项会核对「每个 (节点,参数) 组合 ⊆ 本表」
//    ⇒ 以后新增参数忘了写提示 ⇒ 机检当场红。
// ⚠ 本表**只写 `en`**：中文的唯一源是后端 `nodes.py` 的 tooltip 原文（见 `tipFor`）。
//    历史上这里存过 `zh`，于是同一个参数出现两套中文说法 ⇒ 已删掉 `zh` 这条路。
// ⚠ 前端把这一格写进 `widget.tooltip`（**覆盖**后端原文，不落工作流 JSON）
//    ⇒ 分享出去的图不带翻译、别人的图也不受影响。
// ⚠ 文案里**不许出现字面 `**`**：悬浮框是纯文本渲染（Vue `createTextVNode`），
//    星号会被原样看到。写法规范见 `CONTRIBUTING.md`，机检 `tools/check_tooltips.py`。
export const H3_TIPS = {
    // ===== 潜空间落盘 =====
    "H3RelayLatentSave.latent": {
        en: "【Wiring】Take this segment's sampler latent output; it is saved as the baton for the next segment.\n⚠ In a sample → upscale → resample flow take the first sampling output, not the upscaled one.",
    },
    "H3RelayLatentSave.run_id": {
        en: "【What to enter】The film name, e.g. myfilm.\n⚠ It must match Copy Bridge exactly, or the file is not found. Use a new name per film (the same name overwrites).",
    },
    "H3RelayLatentSave.stage_index": {
        en: "【What to enter】Segment 1 = 0, segment 2 = 1, …\n⚠ Must equal Copy Bridge (the Chain node can sync it).",
    },
    "H3RelayLatentSave.note": {
        en: "【Optional】A short note to tell versions apart later.",
    },
    // ===== 读上段潜空间 =====
    "H3RelayLatentLoad.run_id": {
        en: "【What to enter】The same film name as Latent Save. A mismatch means no file is found — hard error.",
    },
    "H3RelayLatentLoad.stage_index": {
        en: "【What to enter】This segment's index (same as the bridge and save). The node reads segment − 1.\n0 = segment 1, no predecessor ⇒ it hands over an empty context and the bridge passes through. That is normal.",
    },
    "H3RelayLatentLoad.explicit_path": {
        en: "【Optional】Empty = find it by film name + segment; set it to read that exact file.",
    },
    // ===== 裁重叠 =====
    "H3RelayTrimAV.images": {
        en: "【Wiring】Take this segment's VAEDecode output.\nIt arrives with a duplicated head; this node trims it.",
    },
    "H3RelayTrimAV.trim_frames": {
        en: "【Leave it alone】Wire Copy Bridge's 3rd output here.\n(`trim_frames`) Segment 1 ⇒ 0; from segment 2 ⇒ the pinned frame count.\nTyping a number by hand easily disagrees with the bridge.",
    },
    "H3RelayTrimAV.fps": {
        en: "【Leave it alone】H3 is fixed at 24 fps; audio is trimmed on the same timeline.",
    },
    "H3RelayTrimAV.audio": {
        en: "【Wire it】Take the audio decoder output (VAEDecodeAudio).\nPicture and sound are trimmed by the same frame count so they stay in sync.\nWithout it the audio ends up longer than the picture.",
    },
    "H3RelayTrimAV.settle_frames": {
        en: "【Keep it at 0】Whether to also trim the settle zone: after the pinned region the model replays a few frames of the previous segment.\nThose frames are soft, but trimming them is what causes the seam jump (measured: 0 frames trimmed jumps 0.020, 16 frames 0.055).\n· 0 (default) = trim the pinned region only, no jump; -1 = measure it; N = always trim N extra frames.",
    },
    "H3RelayTrimAV.seam_ghost": {
        en: "【Keep it at 0】Replaces the first trimmed frame with a blend of the previous last frame and this first frame.\nMeasured, it only splits one jump into two without reducing the displacement ⇒ two stutters, more visible than a single jump.\n· 0 (default) = off; above 0 = on (only when you know it helps).",
    },
    "H3RelayTrimAV.seam_ghost_alpha": {
        en: "【Ghost mode only】Weight of the previous last frame (0.5 = even split). No effect in the default mode.",
    },
    "H3RelayTrimAV.settle_sharpen": {
        en: "【Enable as needed】Sharpen the soft zone: the first frames after the trim, strongest at the seam, fading to 0.\n· 0 (default) = off; 0.4–1.0 recommended (try 0.6, then look for halos or noise).\n⚠ Restores contrast only, not lost detail.",
    },
    "H3RelayTrimAV.settle_sharpen_frames": {
        en: "【With the soft-zone sharpening】How many frames it acts on (from the first trimmed frame, fading to 0).\nDefault 24 frames ≈ 1 s; measured, the soft zone recovers within 16–20 frames.",
    },
    "H3RelayTrimAV.lowfreq_pull": {
        en: "【Enable as needed】Low-frequency transfer: pulls the low frequencies of the first trimmed frames towards the previous last frame.\nColour, brightness and lighting follow; this segment's own detail and pose stay.\n· 0 (default) = off; 0.7 = medium; 1.0 = strongest (measured seam step down to 0.0011).\nLow frequencies only ⇒ the previous pose is not copied ⇒ no ghosting.",
    },
    "H3RelayTrimAV.lowfreq_frames": {
        en: "【With the low-frequency transfer】How many frames it acts on (fading to 0).\nThe reference implementation uses 12; measured, the seam zone is affected within the first 12 frames.",
    },
    "H3RelayTrimAV.lowfreq_blur": {
        en: "【With the low-frequency transfer】Low-frequency scale (blur kernel).\nLarger aligns only broad colour and lighting; the reference implementation uses 64.",
    },
    "H3RelayTrimAV.deconv_strength": {
        en: "【Enable as needed】Deconvolution of the head: inverts an assumed blur kernel to recover the signal.\nUnlike sharpening (which just amplifies high frequencies, noise included), it can in principle restore detail.\n0 (default) = off; 0.5–1.0 = blend ratio. Too high rings.",
    },
    "H3RelayTrimAV.deconv_radius": {
        en: "【With the deconvolution】Assumed blur radius in pixels. Larger for a softer head; too large rings.",
    },
    "H3RelayTrimAV.detail_borrow": {
        en: "【Enable as needed】Body high-frequency transfer: the head keeps its own low frequencies and takes the body's high-frequency structure.\nComplements the low-frequency transfer. 0 (default) = off.\n⚠ Brings texture misalignment when head and body differ a lot.",
    },
    "H3RelayTrimAV.detail_blur": {
        en: "【With the high-frequency transfer】Separation scale: larger moves coarser high frequencies.",
    },
    "H3RelayTrimAV.hist_match": {
        en: "【Enable as needed】Histogram match: aligns the head's tonal distribution to the body's.\nStronger than the low-frequency transfer, which aligns only the mean.\n0 (default) = off; 1 = full alignment.",
    },
    "H3RelayTrimAV.wb_match": {
        en: "【Enable as needed】Grey-world white balance: aligns the head's R:G:B ratio (colour temperature) to the body's.\nOrthogonal to the low-frequency transfer (which handles overall brightness). Gain is clamped to ±25%.\n0 (default) = off.",
    },
    "H3RelayTrimAV.match_prev": {
        en: "【Enable as needed】Cross-segment statistics match: aligns the head's colour and exposure to the previous last frame.\nStatistics only, no pose copying ⇒ no ghosting.\n0 (default) = off; start around 0.5.",
    },
    "H3RelayTrimAV.match_prev_frames": {
        en: "【With the cross-segment match】How many frames it acts on: from the first trimmed frame, fading to 0.",
    },
    "H3RelayTrimAV.match_prev_gain_max": {
        en: "【Guard rail】Cap on the per-channel contrast gain, so content that already matched is not spoiled.",
    },
    "H3RelayTrimAV.match_prev_offset_max": {
        en: "【Guard rail】Cap on the per-channel brightness/chroma offset, so the whole segment does not change colour.",
    },
    "H3RelayTrimAV.run_id": {
        en: "【Diagnostics】Optional: the same film name as Latent Save.\nOnly then are per-segment appearance statistics written to <run>/appearance_log.jsonl.",
    },
    "H3RelayTrimAV.save_pcm": {
        en: "【Keep it on】On (default) = also save the trimmed audio as lossless PCM.\nConcat reads it directly ⇒ one encode only; off = not written (about 2 MB per segment) and that segment falls back to mp4 decoding.",
    },
    "H3RelayTrimAV.diagnostics": {
        en: "【Off by default】Three read-only observations that take part in no decision; off changes no output (printing only):\n· cross-segment alignment cost; · trim amount → jump curve; · appearance triple + drift curve (needs `run_id`).\nOn = about 0.4 s per segment of CPU, zero GPU.",
    },
    // ===== 复合桥 =====
    "H3RelayCopyBridge.latent": {
        en: "【Wiring】This segment's initial AV latent (from a latent-building node).\nIt writes the previous segment's tail into the head and attaches a noise mask.",
    },
    "H3RelayCopyBridge.context_latent": {
        en: "【Wiring】The previous segment's full AV latent.\nTaken from Latent Load; required, segment 1 included.\n⚠ At segment 1 it hands over an empty context and the bridge passes through — do not unplug it, a missing input fails submission.",
    },
    "H3RelayCopyBridge.context_frames": {
        en: "【Copy-window length】Frames written into this segment's head.\nOnly the 5+17k grid (5/22/39/56/73/90/107/124) and it must be shorter than the segment.\nA longer window steadies the seam but leaves fewer frames for new content.",
    },
    "H3RelayCopyBridge.mask_mode": {
        en: "【Mask mode】Output = model × m + previous tail × (1−m).\n· hard (recommended, default) = whole window pinned, zero redraw — use it for real continuation.\n· ramp / window = loosen the seam; framing may be rewritten.\n· taper / blend = comparison only.\nm=0 is what pins; measured, all four non-hard modes jump.",
    },
    "H3RelayCopyBridge.taper_tokens": {
        en: "【taper only】How many seam-end tokens take part in the linear transition.",
    },
    "H3RelayCopyBridge.seam_min": {
        en: "【taper only】Mask floor at the seam.\n0 = fully pinned; 0.3 = 30% redraw freedom there.\nThe head is always fully redrawn; this does not reach it.",
    },
    "H3RelayCopyBridge.pin_audio": {
        en: "【Pin audio】On (default) = the previous audio tail is copied in too.\nTurn it off to pin video only. The audible join still belongs to Trim AV and the concat step.",
    },
    "H3RelayCopyBridge.ramp_top": {
        en: "【ramp only】Largest m at the seam.\n0 = degenerates to hard. Higher blends more but makes the noise drop steeper.\nLengthen the copy window if you raise it.",
    },
    "H3RelayCopyBridge.ramp_tokens": {
        en: "【ramp only】Seam-end tokens taking part in the ramp.\n0 = whole copy window; 2–3 = loosen only the seam, motion stays locked.",
    },
    "H3RelayCopyBridge.anchor_latent": {
        en: "【Optional colour reference】The AV latent of the global appearance anchor (usually segment 1).\nThe trimmed prefix is pulled towards the anchor's colour and variance, so new content returns to the global grade. It fixes colour drift as the chain grows.",
    },
    "H3RelayCopyBridge.anchor_blend": {
        en: "【Correction strength】0–1: how much of the prefix is pulled towards the anchor's statistics. 1 = full (default), 0.5 = half.\nLower it when the anchor and this segment are meant to differ. No effect without `anchor_latent`.",
    },
    "H3RelayCopyBridge.blend_top": {
        en: "【blend only】Cap on the model's share at the seam.\n0 = degenerates to hard; higher trusts this segment's own prediction.\nStart around 0.5.",
    },
    "H3RelayCopyBridge.blend_tokens": {
        en: "【blend only】Seam-end tokens taking part in the blend.\n0 = whole copy window; 2–3 = blend only the seam.",
    },
    "H3RelayCopyBridge.blend_shape": {
        en: "【blend only】Blend window shape.\nsmoothstep = polynomial (default); hann = cosine.\nBoth have zero slope at the ends ⇒ softer transition.",
    },
    "H3RelayCopyBridge.window_top": {
        en: "【window only】Peak of the window (most redraw freedom at the centre). 0 = degenerates to hard.\nUnlike ramp it falls back at the seam, so the seam stays pinned. Too high hands framing back to the model.",
    },
    "H3RelayCopyBridge.window_shape": {
        en: "【window only】Window shape.\nsine = low but non-zero at the ends (default); hann = exactly zero at the ends (both ends pinned).",
    },
    "H3RelayCopyBridge.conditioning": {
        en: "【Optional】When wired it also appends pinned frames to the conditioning.\nIt governs framing and works in parallel with the latent pin window; unwired = copy bridge only.\n⚠ The 4th output must go to the sampler's positive.",
    },
    "H3RelayCopyBridge.run_id": {
        en: "【What to enter】The film name, same as Latent Save.\nIt is what makes the two \"stage number only\" anchors below work (they use it to find the files).",
    },
    "H3RelayCopyBridge.stage_index": {
        en: "【What to enter】This segment's index (segment 1 = 0).\nWith `run_id` it drives anchor auto-read, and it must match the save.",
    },
    "H3RelayCopyBridge.ref_anchor_latent": {
        en: "【Appearance anchor · wired】The AV latent of the global appearance anchor (usually segment 1), fed in by a wire.\nEvery step rides on it as a near-noise-free reference, holding long-range drift in check.",
    },
    "H3RelayCopyBridge.ref_anchor_stage": {
        en: "【Appearance anchor · no wiring】Enter a stage number, no wire needed.\n≥ 0 = when `ref_anchor_latent` is unwired, read stage_<this> as the anchor; -1 (default) = off.\n⚠ Must be lower than this segment's index.",
    },
    "H3RelayCopyBridge.ref_anchor_frames": {
        en: "【Appearance anchor · how much】Frames taken from the anchor's head.\nDefault 5; more drags the anchor's motion in too.",
    },
    "H3RelayCopyBridge.audio_ref_seconds": {
        en: "【Audio reference window】0 (default) = automatic.\nWalk back from the previous tail until 2 s of voiced audio accumulate (6 s max).\nThe window may hold several speakers while using only 1 of the official 3 audio slots.\n⚠ It is that dialogue, so the model may repeat it; ignored when a voice anchor is wired.",
    },
    "H3RelayCopyBridge.voice_anchor": {
        en: "【Voice anchor · wired】The speaker's audio anchor (LoadAudio → VAEEncodeAudio).\nWith it, the pinned audio prefix and the audio reference both use the anchor's tail window instead of the previous tail.\n· The anchor is the first person who speaks across the seam; not needed for the same speaker.\n⚠ Ignored when `pin_audio=False`; an all-zero / constant / silent anchor errors out.",
    },
    // ===== 后处理 =====
    "H3RelayPost.images": {
        en: "【Wiring】Take Trim AV's `images` output (the trimmed picture).\nIt only changes picture quality; frame count and audio are untouched.",
    },
    "H3RelayPost.guide": {
        en: "【Wiring】Take Trim AV's 4th output, `prev_tail`.\nIt is the last frame of the pinned region, i.e. the other side of the seam; only the cross-segment match and the low-frequency transfer need it.\n⚠ Whether it really is the previous last frame depends on the bridge: yes with the copy bridge, only an approximation with the Latent bridge.",
    },
    "H3RelayPost.match_prev": {
        en: "【Enable as needed, 0 by default】Cross-segment statistics match.\nAligns the head's colour and exposure (per-channel mean + standard deviation) to `guide`.\nStatistics only, no pose copying ⇒ no ghosting. Needs `guide`.\n⚠ Keep it at 0 on the Latent bridge: `guide` there is only an approximation and aligning to it is worse.\n⚠ The cross-segment master switch must also be ticked.",
    },
    "H3RelayPost.match_prev_frames": {
        en: "【With the cross-segment match】How many frames it acts on: from the first frame, fading to 0.",
    },
    "H3RelayPost.match_prev_gain_max": {
        en: "【Guard rail】Cap on the per-channel contrast gain, so content that already matched is not spoiled.",
    },
    "H3RelayPost.match_prev_offset_max": {
        en: "【Guard rail】Cap on the per-channel brightness/chroma offset, so the whole segment does not change colour.",
    },
    "H3RelayPost.lowfreq_pull": {
        en: "【Enable as needed, 0 by default】Low-frequency transfer.\nAligns only the head's low-frequency colour to `guide`, leaving detail and pose alone ⇒ no ghosting.\nIts scope overlaps the cross-segment match, so pick one.\n⚠ Needs `guide` and is governed by the master switch below.",
    },
    "H3RelayPost.lowfreq_frames": {
        en: "【With the low-frequency transfer】How many frames it acts on (fading to 0).",
    },
    "H3RelayPost.lowfreq_blur": {
        en: "【With the low-frequency transfer】Low-frequency scale (blur kernel); the reference uses 64.",
    },
    "H3RelayPost.head_zone_frames": {
        en: "【Head zone】The four items below act over this many frames.\nHistogram match / white balance / deconvolution / body high-frequency transfer.\n⚠ The soft-zone sharpening does not use this; it has its own frame count.",
    },
    "H3RelayPost.hist_match": {
        en: "【Within a segment】Histogram match: aligns the head's tonal distribution to the body's.\nIt fixes tonal drift between head and body (inside one segment; no `guide` needed).\nEnable as needed; 0 by default. Acts over `head_zone_frames`.",
    },
    "H3RelayPost.wb_match": {
        en: "【Within a segment】Grey-world white balance: aligns the head's R:G:B ratio to the body's.\nOrthogonal to the histogram match (that one handles brightness, this one colour temperature).\nEnable as needed; 0 by default.",
    },
    "H3RelayPost.deconv_strength": {
        en: "【Restore high frequencies】Deconvolution: lifts the head's high frequencies. Enable as needed; 0 by default.\nToo high rings (lower the strength then). Acts over `head_zone_frames`.",
    },
    "H3RelayPost.deconv_radius": {
        en: "【With the deconvolution】Blur kernel radius.",
    },
    "H3RelayPost.detail_borrow": {
        en: "【Restore high frequencies】Body high-frequency transfer: replaces the head's high frequencies with the body's structure.\nMore convincing than deconvolution, but it may not match the head's content.\nEnable as needed; 0 by default.",
    },
    "H3RelayPost.detail_blur": {
        en: "【With the high-frequency transfer】High-frequency separation scale.",
    },
    "H3RelayPost.settle_sharpen": {
        en: "【Finishing touch】Soft-zone sharpening: the first frames, with a fade. Enable as needed; 0 by default.\nNothing is trimmed and the timeline is untouched ⇒ it cannot introduce a jump. Try 0.4–1.0.",
    },
    "H3RelayPost.settle_auto": {
        en: "【Recommended】Adaptive soft-zone compensation (default 0.5 = on).\nIt measures each frame's sharpness shortfall against the body baseline and sharpens in proportion; frames on target stay untouched.\n· 0 = off; 0.5–1.0 is the recommended range.\n· With the fixed sharpening also on ⇒ only this one acts (the other abstains).\n⚠ Contrast only, lost detail does not come back.",
    },
    "H3RelayPost.settle_sharpen_frames": {
        en: "【With the soft-zone sharpening】How many frames it acts on (fading to 0).\n⚠ Only the soft-zone sharpening; the four items above use `head_zone_frames`.",
    },
    "H3RelayPost.match_prev_stats_frames": {
        en: "【With the cross-segment match】How many frames the statistics come from — the easiest field to get wrong.\n· 1 (default) = only the frame right at the seam, same basis as `guide` ⇒ the correction is exactly the seam step.\n· 0 = aggregate over the zone ⇒ with a brightness gradient the first frame overshoots `guide` and creates a step. Comparison only.",
    },
    "H3RelayPost.baseline": {
        en: "【How the body reference is taken】\n· robust (default) = take the middle 50% of frames by brightness, compute the statistics and report the spread.\n  Above 0.08 the baseline is judged unreliable and the items abstain.\n· legacy = the old basis (whole-segment mean), for comparison only.",
    },
    "H3RelayPost.cross_seg_ack": {
        en: "【Cross-segment master switch】Tick it only once you know `guide` is a true reference.\n· On = the cross-segment match and the low-frequency transfer can act (provided they are not 0 themselves).\n· Off (default) = both abstain and say so in the report.\n· Why off: on the Latent bridge `prev_tail` is only an approximation and aligning to it moves the first frame away from the truth.",
    },
    // ===== 连跑控制 =====
    "H3RelayChain.segments": {
        en: "【Stop after which segment】Applies to chain / resume only.\n3 means run up to segment 3 (not \"three more\"); 0 = keep going until you press stop.\nWith `prompts` filled in, do not exceed the number of blocks.",
    },
    "H3RelayChain.status": {
        en: "【Leave it alone】Shows chain status: which segment, whether it is queued, and why it failed.\nIf a button seems to do nothing, read this cell first.",
    },
    "H3RelayChain.prompts": {
        en: "【Optional】Per-segment prompts.\nSplit them with a line containing only `---`: block 1 for segment 1, and so on. Empty = keep the same prompt.\n⚠ Too few blocks ⇒ it does not queue and tells you. You can also wire it in from another node.",
    },
    "H3RelayChain.prompt_target": {
        en: "【Deprecated】The old \"frontend writes the prompt\" path is gone and this field does nothing.\nPrompts now come from this node's `prompt` output.\nIt is kept only so old graphs keep their slot positions.",
    },
    "H3RelayChain.auto_concat": {
        en: "【On by default】On = after the chain finishes, join these segments into one film automatically (picture untouched, a few seconds).\nOff = you get N segment mp4 files and can press \"join into one\" when you want.\n⚠ Joining needs a video-save node in the graph (that is where the files come from).",
    },
    "H3RelayChain.concat_name": {
        en: "【Optional】What to call the finished film (no .mp4 needed). Empty = the `run_id` used when saving.\nIt goes to ComfyUI's output/ directory.",
    },
    "H3RelayChain.audio_out": {
        en: "【Finished-film audio】Affects only the joined film, never the segment files.\n· aac_256k (default) = general purpose: plays in browsers, accepted by editors.\n· aac_192k = plays the same, about 25% smaller.\n· pcm_lossless = a lossless track for editing and archiving. ⚠ Much larger, and browser previews are silent.",
    },
    "H3RelayChain.video_crf": {
        en: "【Finished-film picture quality】Default 16; you normally never need it.\nThe picture is copied (lossless, no re-encode); this applies only when segments do not match and a re-encode is unavoidable: lower is sharper.",
    },
    "H3RelayChain.stage_index": {
        en: "【Which segment runs now】Indices start at 0 (segment 1 = 0).\nIt selects which block of `prompts` is output.\n· On the canvas the run buttons update it together with the bridge, the save and the latent load.\n· Scripted runs: set this and the bridge/save indices to the same number.\n⚠ Larger than the number of blocks ⇒ a hard error.",
    },
    "H3RelayChain.run_id": {
        en: "【Optional】For resuming: use the same `run_id` as the bridge / save.\nWhen set, progress is written to `_progress.json` ⇒ after a crash or restart, resume picks up there.\nEmpty = no progress file.",
    },
    "H3RelayChain.concat_result": {
        en: "【Leave it alone】Film path: after a successful join it shows the file under output/.\nOn failure it shows the reason (the full report is in the console). Status messages never overwrite it.",
    },
    // ===== 音频缝 =====
    "H3RelayAudioSeam.audio": {
        en: "【Wiring】Take Trim AV's `audio` output.\nFor segment 1, with no Trim AV node, take the audio decoder directly.",
    },
    "H3RelayAudioSeam.run_id": {
        en: "【What to enter】The film name, exactly matching Latent Save / the bridge.\nIt uses it to find the previous segment's saved audio as the bed source.",
    },
    "H3RelayAudioSeam.stage_index": {
        en: "【What to enter】This segment's index (segment 1 = 0).\nSegment 1 has no seam to patch and passes through (its audio is still saved as segment 2's bed source).",
    },
    "H3RelayAudioSeam.patch_seconds": {
        en: "【Head patch】Replaces the first N seconds with the previous segment's ambience.\nDefault 1.2: ambience and background noise no longer restart every segment.\n· Higher covers more of the head (above 1.2 it risks swallowing dialogue); lower is safer.\n⚠ It replaces everything in those N seconds, so the head should not contain dialogue. 0 = off.",
    },
    "H3RelayAudioSeam.tile_seconds": {
        en: "【Bed tiling】Takes tiles of this length and cross-fades them to fill the patch.\n0 (default) = take the quietest stretch from the whole window. Use it when the previous segment's clean ambience is shorter than the patch; 1.2 is a good value.",
    },
    "H3RelayAudioSeam.fade_seconds": {
        en: "【Patch boundary】How wide a cross-fade between the patch and what follows.\n0 = hard cut (an audible click); 0.25 is a measured-good value.",
    },
    "H3RelayAudioSeam.bed_stage": {
        en: "【What to enter】Which segment's audio to use as the bed (0 = segment 1).\nAmbience in the same scene barely changes, so segment 1 is steadiest. ⚠ Must be lower than this segment's index.",
    },
    "H3RelayAudioSeam.note": {
        en: "【Optional】A note, stored in the file's metadata to tell versions apart.",
    },
    "H3RelayAudioSeam.bed_select": {
        en: "【Where the bed comes from】\n· tail (default) = a stretch of the bed's tail as long as the patch: right at the seam, so level and timbre continue naturally.\n· quiet = the globally quietest window. Measured, it makes the patch quieter, turning the seam dip into a silent hole ⇒ comparison only.",
    },
    "H3RelayAudioSeam.join_curve": {
        en: "【Join curve】Shape of the cross-fade at the seam.\n· qsin (default, equal power) = no level dip when the two sides are uncorrelated.\n· tri (linear) = measured 4.7 dB dip, heard as the volume dropping and coming back ⇒ comparison only.",
    },
    "H3RelayAudioSeam.join_prime_ms": {
        en: "【Join only】Encoder priming to drop from each head, in milliseconds.\nDefault 33 ms (measured for AAC). It is not content — dropping it is an alignment fix; 0 = keep it.",
    },
    "H3RelayAudioSeam.join_cross_ms": {
        en: "【Join only】Cross-fade length at the seam, in milliseconds. Default 0 = none.\nA cross-fade shortens the timeline ⇒ the audio runs ahead of the picture, accumulating.\nSo it only makes sense together with `join_align_seconds`.\n⚠ Filling this alone errors out instead of silently producing a misaligned track.",
    },
    "H3RelayAudioSeam.join_segment_seconds": {
        en: "【Join only】Each segment's effective audio length in seconds = frames ÷ fps.\nThe node's PCM is usually longer than the mp4; without truncation the join runs long.\n0 = no truncation. Example: 90 frames at 24 fps = 3.75.",
    },
    "H3RelayAudioSeam.join_align_seconds": {
        en: "【Join only】Picture trim per seam in seconds = trimmed frames ÷ fps.\nAbove 0 = timeline conserving: the output is exactly as long as the trimmed picture, with no level dip at the seam.\n0 = the old shortening behaviour (comparison only). Must be at least `join_cross_ms`.",
    },
    "H3RelayAudioSeam.patch_guard": {
        en: "【Dialogue guard】On (default) = before patching, detect whether this segment's head contains dialogue.\nIf it does, the patch shrinks to 0.40 s before the line; if the line starts too early the patch is switched off.\nOff = old behaviour: the patch may replace dialogue at the head.",
    },
    "H3RelayAudioSeam.patch_guard_layers": {
        en: "【Dialogue guard level】0 (default) = the energy test alone.\n1 = add an energy step; 2 = add spectral centroid; 3 = add spectral flatness (all three lower the sensitivity).\n⚠ Levels 1–3 go against a guard that should rather over-detect, and were calibrated on few samples ⇒ off by default.\n⚠ Do not rest dialogue safety on this field: keep `patch_seconds` at or below 1.2 instead.",
    },
    "H3RelayAudioSeam.declick_ratio": {
        en: "【Isolated-transient suppression】Test = local peak ÷ background noise. 0 = off; 4.0 recommended.\nIt removes a short click that pops out where the background is very quiet.\n⚠ The test looks at how quiet the surroundings are, not how loud the click is — loudness alone would cut speech consonants too.\n⚠ Only that spot changes (2 ms fades both sides); the rest stays bit-identical.",
    },
    "H3RelayAudioSeam.declick_quiet_dbfs": {
        en: "【Isolated-transient suppression】Only act when the background is below this level (dBFS). Default −50.\nMeasured: −56 dBFS at an isolated transient ⇒ acted on; about −35 dBFS at a speech consonant ⇒ not acted on.\n⚠ Higher is more aggressive and may touch soft consonants; lower is more conservative.",
    },
    "H3RelayAudioSeam.declick_max_len_ms": {
        en: "【Isolated-transient suppression】How wide still counts as one click, in milliseconds. Default 50.\n· 10 = only pulses up to 10 ms (measured isolated transients are 8 ms wide, so still caught).\n· 50 (default) = also treats 15–30 ms taps, at the cost of possibly shaving real short sound effects.\n· 100 or more = aggressive, not recommended.",
    },
    "H3RelayAudioSeam.cut_head_frames": {
        en: "【Isolated-transient suppression】Frames this segment will lose (= the bridge's `context_frames`).\nWhy: this node works on untrimmed audio while the output is trimmed ⇒ a seam peak looks \"not isolated\" here, yet nothing precedes it in the finished film.\nGiven this value the suppressor treats that spot as a fresh file head.\n⚠ 0 = the fix is off; a script normally fills it.",
    },
    "H3RelayAudioSeam.loudness_normalize": {
        en: "【Loudness balance】On (default) = aligns this segment's speech loudness to a common target.\nWhy on: the model follows the anchor's loudness — with an anchor it is normalised, without one nothing governs it.\nEach segment's level is then left to the model (measured 7.97 dB spread). Off = the old behaviour.",
    },
    "H3RelayAudioSeam.loudness_target_dbfs": {
        en: "【Loudness balance】Target level (median RMS over voiced windows, dBFS). Default −23.0.\n⚠ Not the whole-segment RMS and not the peak — silence and ambience are excluded, so it describes speech loudness.",
    },
    // ===== 潜空间分块放大 =====
    "H3RelayLatentUpscale.latent": {
        en: "【Wiring】Take this segment's sampler latent output.\n⚠ The copy saved for the next segment must be the pre-upscale one, or it upscales twice.",
    },
    "H3RelayLatentUpscale.model_name": {
        en: "【What to pick】H3 latent upscale weights, placed in models/latent_upscale_models/ (restart to see them).\nDownload: https://huggingface.co/LBH-123-AI/Minimax_h3_latent_upscaler\n⚠ Plain ESRGAN models do not work.",
    },
    "H3RelayLatentUpscale.mode": {
        en: "【Which one】Three ways to size the output; only the selected one applies:\n· scale by multiplier — give a factor (most common);\n· target dimensions — type width × height;\n· megapixels — pick a pixel budget.",
    },
    "H3RelayLatentUpscale.scale": {
        en: "【Multiplier mode】Upscale factor. 1.0 = unchanged, 2.0 = both sides doubled.\n⚠ Upscale only: below 1 it errors out.",
    },
    "H3RelayLatentUpscale.width": {
        en: "【Target-size mode】Target width in pixels; snapped to a multiple of `align`.",
    },
    "H3RelayLatentUpscale.height": {
        en: "【Target-size mode】Target height in pixels; snapped to a multiple of `align`.",
    },
    "H3RelayLatentUpscale.megapixels": {
        en: "【Megapixel mode】Target total pixels. 1.0 is roughly 720p; keep it ≤1.5 on a 12 GB card.",
    },
    "H3RelayLatentUpscale.chunks": {
        en: "【VRAM · split time】How many chunks along the time axis. 1 = whole segment in one pass (fastest, hungriest).\n⚠ Splitting changes the picture ⇒ re-check the seam. The ceiling follows the overlap; above it errors out.",
    },
    "H3RelayLatentUpscale.overlap": {
        en: "【Chunk overlap】Frames blended on each side when chunking. Default 5.\nOnly active when `chunks` > 1; 0 = hard cut, no blending.",
    },
    "H3RelayLatentUpscale.align": {
        en: "【Alignment grid】Output size snaps to a multiple of this. 32 is a hard requirement; do not lower it.",
    },
    "H3RelayLatentUpscale.precision": {
        en: "【Precision】bf16 / fp16 save VRAM; fp32 is most stable but slowest. Match the weight file's own precision.",
    },
    "H3RelayLatentUpscale.device": {
        en: "【Where to compute】cuda / rocm run normally; cpu is for troubleshooting only and is far slower.",
    },
    "H3RelayLatentUpscale.force_unload": {
        en: "【Unload after run】On = return the model to system memory to free VRAM; off (default) = keep it loaded.\n⚠ With chunking it reloads once per chunk and is clearly slower.",
    },
    "H3RelayLatentUpscale.spatial_tiles": {
        en: "【VRAM · split space】Cut the frame into tiles along its long edge. 1 = no split (default).\nBetter than splitting time: each tile keeps the whole time axis, so no temporal context is lost; only the boundary narrows (overlap + feathering handle it).\n⚠ Splitting changes the picture ⇒ re-check the seam. 2–4 tiles is usually enough.",
    },
    "H3RelayLatentUpscale.spatial_overlap": {
        en: "【Spatial overlap】Overlap width between tiles (latent cells; 1 cell ≈ 16 px). Default 8.\nOverlap is context only and cores are exclusive ⇒ no ghosting.\nIt costs compute: an 80-cell edge with overlap 8 makes 3 tiles ~40% dearer. Must still fit `seam_feather`.",
    },
    "H3RelayLatentUpscale.seam_feather": {
        en: "【Seam feather】Transition-band width (latent cells). Default 4; 0 = hard transition.\nRaise it to 8–12 if a faint colour line shows, and raise the spatial overlap with it.",
    },
    "H3RelayCopyBridge.voice_anchor_stage": {
        en: "【Voice anchor · no wiring】Enter a stage number, no wire needed.\n≥ 0 = when `voice_anchor` is unwired, read the audio of stage_<this> as the anchor; -1 (default) = off.\n⚠ Must be lower than this segment's index; ignored when `voice_anchor` is wired.",
    },
    "H3RelayCopyBridge.exp_anchor": {
        en: "【Experiment · default auto】One switch for the two conditioning experiments: per-speaker voice accumulation, and the time-invariant history anchor.\n· auto (default) = the old behaviour: read the run-directory config files, on if present.\n· off = off for good (ignored and reported). The rest = explicitly on; a missing file errors out.\n⚠ Both are untested experiments."
    },
};

// ---------------------------------------------------------------------------
// 词表 · 端口（输入/输出名 → 标签）
// ---------------------------------------------------------------------------
export const H3_SLOTS = {
    latent: { zh: "潜空间", en: "latent" },
    path: { zh: "路径", en: "path" },
    context_latent: { zh: "上段潜空间", en: "context latent" },
    info: { zh: "信息", en: "info" },
    report: { zh: "报告", en: "report" },
    trim_frames: { zh: "裁剪帧数", en: "trim frames" },
    conditioning: { zh: "条件", en: "conditioning" },
    images: { zh: "图像", en: "images" },
    audio: { zh: "音频", en: "audio" },
    prev_tail: { zh: "上段末帧", en: "prev tail" },
    joined: { zh: "拼接结果", en: "joined" },
    prompt: { zh: "提示词", en: "prompt" },
};

// ---------------------------------------------------------------------------
// 词表 · 枚举值（**只收白名单**）
// 🔴 绝不翻的：模型文件名 / 段号 / 路径 / 用户自定义名 —— 翻了 = 找不到文件。
//    `model_name` 刻意**不在**本表里（标签可译，值不可译）。
// ---------------------------------------------------------------------------
export const H3_COMBOS = {
    mask_mode: { hard: "硬边", taper: "渐缩", ramp: "斜坡", blend: "混合", window: "窗" },
    blend_shape: { smoothstep: "平滑阶跃", hann: "汉宁窗" },
    window_shape: { sine: "正弦", hann: "汉宁窗" },
    bed_select: { tail: "床源尾部", quiet: "最静段" },
    join_curve: { qsin: "四分之一正弦", tri: "三角" },
    mode: { "scale by multiplier": "按倍数", "target dimensions": "目标尺寸", megapixels: "兆像素" },
    precision: { fp16: "fp16 · 半精度", bf16: "bf16 · 稳", fp32: "fp32 · 全精度" },
    device: { cuda: "cuda · 显卡", cpu: "cpu · 处理器" },
};

/** 该 widget 的值是否在白名单里（不在 ⇒ 只显示原值，绝不猜）。 */
export function comboLabel(widgetName, value, lang) {
    const table = H3_COMBOS[widgetName];
    if (!table || value == null) return null;
    const zh = table[String(value)];
    if (zh == null) return null;                      // 🔴 白名单外 ⇒ 不翻（不许猜）
    if (normLang(lang) !== "zh") return String(value);
    return zh;
}

// ---------------------------------------------------------------------------
// 标题：只换前缀、保留后缀（与额度提示共存）
// ---------------------------------------------------------------------------

/** 该 class_type 的**两种语言**默认标题（供「是不是默认名」判定）。 */
export function defaultTitles(type) {
    const c = H3_NODES[String(type || "")];
    return c ? [c.zh, c.en] : [];
}

/** 标题当前是否仍是**默认名**（空标题按默认处理）。 */
export function isDefaultTitle(title, type) {
    const t = String(title == null ? "" : title);
    if (!t) return true;
    return defaultTitles(type).some((d) => t === d || t.startsWith(d));
}

/**
 * 把标题换成 `lang` 版本，**保留后缀**。
 * - 用户自定义标题（既不是 zh 也不是 en 默认名）⇒ 原样返回，一个字都不动。
 * - 默认名 + 追加后缀（额度提示）⇒ 只换掉默认名那一段。
 * @returns {string} 新标题
 */
export function swapTitle(title, type, lang) {
    const c = H3_NODES[String(type || "")];
    if (!c) return title;                              // 未知类型 ⇒ 不碰
    const t = String(title == null ? "" : title);
    const L = normLang(lang);
    if (!t) return c[L];                               // 空标题（画布上常见）⇒ 直接填译文
    for (const d of defaultTitles(type)) {
        if (t === d) return c[L];
        if (t.startsWith(d)) return c[L] + t.slice(d.length);
    }
    return t;                                          // 自定义标题 ⇒ 不碰
}

// ---------------------------------------------------------------------------
// 应用到节点（**纯对象操作**，不依赖 DOM ⇒ 可直接单测）
// ---------------------------------------------------------------------------

/** 给一个下拉 widget 装/卸中文显示钩子。英文态**删掉钩子** ⇒ 回落显示原值。 */
export function applyCombo(node, widgetName, lang) {
    const ws = (node && node.widgets) || [];
    for (const w of ws) {
        if (!w || w.name !== widgetName) continue;
        if (!w.options || !Array.isArray(w.options.values)) continue;
        if (normLang(lang) === "zh") {
            w.options.getOptionLabel = (v) => (v == null ? "" : (comboLabel(widgetName, v, "zh") ?? String(v)));
        } else {
            try { delete w.options.getOptionLabel; } catch (e) { /* 只读对象 ⇒ 跳过 */ }
        }
    }
}

/**
 * 表查找的**纯逻辑**（可单测：把任意表传进来，不碰模块状态）。
 * @param {object} table 形如 `H3_TIPS`
 * @returns {?{zh:string,en:string}} 命中的条目（精确键优先于 `*.` 通配）
 */
export function pickTip(table, type, widgetName) {
    const t = String(type || "");
    const n = String(widgetName || "");
    if (!t || !n || !table) return null;
    const e = table[t + "." + n] || table["*." + n];
    return e && e.en ? e : null;
}

/**
 * 该 (节点类型, 参数名) 组合的双语提示（没有就 null ⇒ **不动**原 tooltip）。
 * 🔴 键先试 `class_type.参数名`，再退到 `*.参数名` 通配。
 *    ⚠ **通配今天一条都没有**（122 条全是显式键）—— 留着是给「**新节点的同名参数语义
 *    与某个已有节点完全一致**」这种情况的逃生口，也让「先加通配、后补精确键」不至于要改代码。
 *    它**不是**为了省事：`class_type.参数名` 永远优先 ⇒ 加通配不会静默顶掉已有精确条。
 *    有测试盯着（`tests/test_i18n.mjs` 9.9：精确键优先、通配能兜底、缺一边语言 ⇒ 当没命中）。
 */
export function tipFor(type, widgetName, lang) {
    const e = pickTip(H3_TIPS, type, widgetName);
    if (!e) return null;
    // 🔴 **中文只有一个源 = 后端 `nodes.py` 的 tooltip 原文**（本表不再存 `zh`）。
    //    所以中文一律返回 `null` ⇒ 调用方"一个字节都不动" ⇒ 前端回落后端原文。
    //    这样同一个参数不可能出现"两套中文说法"（本表历史上就是这样漂开的）。
    if (normLang(lang) !== "en") return null;
    return e.en == null ? null : String(e.en);
}

/**
 * 把语言应用到节点：标题 + 参数标签 + 端口标签 + 枚举显示 + **悬浮提示**。
 * @returns {{title:boolean, widgets:number, slots:number, tips:number}} 实际改了什么
 *          （便于测试与调试；`tips` 只数**真改动了**的那几个）
 */
export function applyLang(node, lang) {
    const out = { title: false, widgets: 0, slots: 0, tips: 0 };
    if (!node) return out;
    const L = normLang(lang);
    try {
        const t = swapTitle(node.title, node.type, L);
        if (t !== node.title) { node.title = t; out.title = true; }
    } catch (e) { /* 标题改不了不影响标签 */ }

    for (const w of (node.widgets || [])) {
        const name = w && w.name;
        if (!name) continue;
        const t = H3_WIDGETS[name];
        if (t) {
            const want = t[L];
            if (w.label !== want) { w.label = want; out.widgets += 1; }
            // 🔴 归属标记：画布层的**防覆盖兜底**靠它识别"这一格是本包管的"。
            //    背景 = 界面汉化插件（ComfyUI-Chinese-Translation 等）有 1s 守护轮询，
            //    会按它自己的词典把我们写好的 label/tooltip **改回中文**
            //    （它的源码注释原话：「兜底补刷**被第三方扩展覆盖**/重建的节点槽位标签」）。
            //    标 `__h3I18nWant` = 期望值，兜底每帧比对、不一致就写回 ⇒ 我们赢。
            //    ⚠ 只是**内存标记**，`serializeValue` 只写 `value` ⇒ 绝不落进工作流 JSON。
            try { w.__h3I18nWant = want; } catch (e) { /* 冻结对象就算了 */ }
        }
        // 🔴 悬浮提示：写 `w.tooltip`（前端取 `w.tooltip || nodeData.inputs[name].tooltip`
        //    ⇒ 这一格**覆盖**后端原文）。序列化只写 `w.value`（见前端 `serializeValue`），
        //    ⇒ 绝不落进工作流 JSON、分享出去不污染别人的图。
        // ⚠ 词表里没有的 (节点,参数) ⇒ **一个字节都不动**（保留后端原文，宁缺勿错）。
        try {
            const tip = tipFor(node.type, name, L);
            if (tip != null) {
                // 写英文之前先把原值记下来（多半是 `undefined` ⇒ 前端回落后端原文）。
                // 记它是为了**切回中文时能还原** —— 中文的唯一源在后端，本表没有 zh 可写。
                if (!w.__h3I18nTipTouched) {
                    w.__h3I18nTipSaved = w.tooltip;
                    w.__h3I18nTipTouched = true;
                }
                if (w.tooltip !== tip) { w.tooltip = tip; out.tips += 1; }
                w.__h3I18nWantTip = tip;      // 同上：兜底的比对基准
            } else if (L !== "en" && w.__h3I18nTipTouched) {
                // 切回中文 ⇒ 还原（拿掉我们写的那一格，让前端回落后端原文）
                const saved = w.__h3I18nTipSaved;
                if (saved === undefined) {
                    if (w.tooltip !== undefined) { delete w.tooltip; out.tips += 1; }
                } else if (w.tooltip !== saved) {
                    w.tooltip = saved;
                    out.tips += 1;
                }
                delete w.__h3I18nWantTip;     // 停掉兜底，否则它会一直写回英文
                delete w.__h3I18nTipSaved;
                w.__h3I18nTipTouched = false;
            }
        } catch (e) { /* 提示失败不影响标签 */ }
        try { applyCombo(node, name, L); } catch (e) { /* 枚举钩子失败不影响别的 */ }
    }

    const slots = [].concat(node.inputs || [], node.outputs || []);
    for (const s of slots) {
        const name = s && s.name;
        if (!name) continue;
        const t = H3_SLOTS[name];
        if (!t) continue;
        if (s.label !== t[L]) { s.label = t[L]; out.slots += 1; }
    }
    return out;
}

/** 图里所有属于本包的节点（`_nodes` 可能是数组或 Map；取不到就空数组）。 */
export function ownNodes(nodesOf) {
    let raw;
    try { raw = typeof nodesOf === "function" ? nodesOf() : nodesOf; } catch (e) { return []; }
    if (!raw) return [];
    let list;
    try {
        // 🔴 跨版本：`_nodes` 可能是数组、Map、或带 `values()` 的容器（refs_ui 踩过这个坑）
        list = Array.from(raw instanceof Map ? raw.values() : raw);
    } catch (e) {
        return [];
    }
    const out = [];
    for (const n of list) {
        if (n && typeof n === "object" && H3_NODES[n.type]) out.push(n);
    }
    return out;
}
