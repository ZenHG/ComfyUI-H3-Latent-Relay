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
//   ④ **悬浮提示只覆盖 `widget.tooltip`**（前端优先取它），**不改档案里的原文**
//      —— 存档序列化只写 `widget.value` ⇒ 提示翻不了图、分享不出去。
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
    run_id: { zh: "运行 ID", en: "run ID" },
    stage_index: { zh: "段号", en: "stage" },
    note: { zh: "备注", en: "note" },
    explicit_path: { zh: "显式路径", en: "explicit path" },
    // 桥
    context_latent: { zh: "上段潜空间", en: "context latent" },
    context_frames: { zh: "钉住帧数", en: "pinned frames" },
    conditioning: { zh: "条件", en: "conditioning" },
    mask_mode: { zh: "遮罩模式", en: "mask mode" },
    taper_tokens: { zh: "渐缩段数", en: "taper steps" },
    seam_min: { zh: "缝阈值", en: "seam min" },
    pin_audio: { zh: "钉住音频", en: "pin audio" },
    ramp_top: { zh: "斜坡上限", en: "ramp top" },
    ramp_tokens: { zh: "斜坡段数", en: "ramp steps" },
    anchor_latent: { zh: "外观锚", en: "look anchor" },
    anchor_blend: { zh: "外观锚混合", en: "anchor blend" },
    blend_top: { zh: "混合上限", en: "blend top" },
    blend_tokens: { zh: "混合段数", en: "blend steps" },
    blend_shape: { zh: "混合曲线", en: "blend shape" },
    window_top: { zh: "窗上限", en: "window top" },
    window_shape: { zh: "窗函数", en: "window shape" },
    ref_anchor_latent: { zh: "参考锚潜空间", en: "ref anchor latent" },
    ref_anchor_stage: { zh: "参考锚段号", en: "ref anchor stage" },
    ref_anchor_frames: { zh: "参考锚帧数", en: "ref anchor frames" },
    audio_ref_seconds: { zh: "音频参考秒数", en: "audio ref seconds" },
    voice_anchor: { zh: "声锚", en: "voice anchor" },
    // 裁重叠
    images: { zh: "图像", en: "images" },
    trim_frames: { zh: "裁剪帧数", en: "trim frames" },
    fps: { zh: "帧率", en: "fps" },
    audio: { zh: "音频", en: "audio" },
    settle_frames: { zh: "沉降帧数", en: "settle frames" },
    seam_ghost: { zh: "重影帧数", en: "ghost frames" },
    seam_ghost_alpha: { zh: "重影强度", en: "ghost strength" },
    settle_sharpen: { zh: "沉降锐化", en: "settle sharpen" },
    settle_sharpen_frames: { zh: "锐化帧数", en: "sharpen frames" },
    lowfreq_pull: { zh: "低频回拉", en: "low-freq pull" },
    lowfreq_frames: { zh: "低频帧数", en: "low-freq frames" },
    lowfreq_blur: { zh: "低频模糊", en: "low-freq blur" },
    deconv_strength: { zh: "去卷积强度", en: "deconv strength" },
    deconv_radius: { zh: "去卷积半径", en: "deconv radius" },
    detail_borrow: { zh: "细节借用", en: "detail borrow" },
    detail_blur: { zh: "细节模糊", en: "detail blur" },
    hist_match: { zh: "直方图匹配", en: "hist match" },
    wb_match: { zh: "白平衡匹配", en: "wb match" },
    match_prev: { zh: "匹配上段", en: "match prev" },
    match_prev_frames: { zh: "匹配帧数", en: "match frames" },
    match_prev_gain_max: { zh: "最大增益", en: "max gain" },
    match_prev_offset_max: { zh: "最大偏移", en: "max offset" },
    save_pcm: { zh: "存无损音轨", en: "save lossless" },
    diagnostics: { zh: "诊断", en: "diagnostics" },
    // 后处理
    guide: { zh: "参考图", en: "guide" },
    head_zone_frames: { zh: "头部区帧数", en: "head-zone frames" },
    settle_auto: { zh: "自动沉降", en: "auto settle" },
    match_prev_stats_frames: { zh: "统计帧数", en: "stats frames" },
    baseline: { zh: "基线", en: "baseline" },
    cross_seg_ack: { zh: "跨段确认", en: "cross-seg ack" },
    // 放大
    model_name: { zh: "模型", en: "model" },
    mode: { zh: "放大方式", en: "mode" },
    scale: { zh: "放大倍数", en: "scale" },
    width: { zh: "宽", en: "width" },
    height: { zh: "高", en: "height" },
    megapixels: { zh: "兆像素", en: "megapixels" },
    chunks: { zh: "分块数", en: "chunks" },
    overlap: { zh: "块重叠", en: "overlap" },
    align: { zh: "对齐倍数", en: "align" },
    precision: { zh: "精度", en: "precision" },
    device: { zh: "设备", en: "device" },
    force_unload: { zh: "用后卸载", en: "unload after" },
    // 音频缝
    patch_seconds: { zh: "补丁秒数", en: "patch seconds" },
    tile_seconds: { zh: "平铺秒数", en: "tile seconds" },
    fade_seconds: { zh: "淡变秒数", en: "fade seconds" },
    bed_stage: { zh: "床声段号", en: "bed stage" },
    bed_select: { zh: "床声取法", en: "bed select" },
    join_curve: { zh: "交叉曲线", en: "join curve" },
    join_prime_ms: { zh: "前置毫秒", en: "prime ms" },
    join_cross_ms: { zh: "交叉毫秒", en: "cross ms" },
    join_segment_seconds: { zh: "分段秒数", en: "segment seconds" },
    join_align_seconds: { zh: "对齐秒数", en: "align seconds" },
    patch_guard: { zh: "台词守卫", en: "dialogue guard" },
    patch_guard_layers: { zh: "守卫层数", en: "guard layers" },
    declick_ratio: { zh: "去咔阈值", en: "declick ratio" },
    declick_quiet_dbfs: { zh: "静噪底", en: "quiet floor" },
    declick_max_len_ms: { zh: "去咔最长毫秒", en: "declick max ms" },
    cut_head_frames: { zh: "裁头帧数", en: "cut head frames" },
    // 连跑
    segments: { zh: "段数", en: "segments" },
    status: { zh: "状态", en: "status" },
    prompts: { zh: "提示词", en: "prompts" },
    prompt_target: { zh: "词输出目标", en: "prompt target" },
    auto_concat: { zh: "自动拼接", en: "auto concat" },
    concat_name: { zh: "拼接名", en: "concat name" },
    audio_out: { zh: "音轨", en: "audio track" },
    video_crf: { zh: "视频画质", en: "video quality" },
    concat_result: { zh: "拼接结果", en: "concat result" },
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
// ⚠ 只写 zh/en 两版，**没有第三条路**：缺一条就红（见 H3p）。
// ⚠ 前端把这一格写进 `widget.tooltip`（**覆盖**后端原文，不落工作流 JSON）
//    ⇒ 分享出去的图不带翻译、别人的图也不受影响。
// ⚠ 文本里的 `**强调**` 是**本仓既有风格**（`nodes.py` 原版 tooltip 里有 400+ 处），
//    不代表 Markdown —— 前端的悬浮框是**纯文本**渲染（Vue `createTextVNode`），
//    ⇒ 用户看到的就是字面上的 `**`。**刻意与后端原文保持一致**（表外的参数仍显原文，
//    若本表去掉了记号，同一个框里会一半有星号一半没有）。要改就**两边一起改**。
export const H3_TIPS = {
    // ===== 潜空间落盘 =====
    "H3RelayLatentSave.latent": {
        zh: "本段采样器（或二采）的 latent 输出。存成文件给下一段当接力棒。走「一采→放大→二采」时接**一采**的输出，别接二采的（双倍漂移）。",
        en: "Latent output of this segment's sampler (or the second pass). Saved to disk as the baton for the next segment. In the two-pass flow (sample → upscale → resample), wire the **first** pass, not the second (double drift).",
    },
    "H3RelayLatentSave.run_id": {
        zh: "片名（如 myfilm、ep01）。必须与 «Copy Bridge» 一字不差，否则桥找不到文件。换片必须换名：同名重跑同段号会覆盖旧文件。存到 output/relay_kit/<run_id>/。",
        en: "Film name (e.g. myfilm, ep01). Must match «Copy Bridge» exactly, or the bridge cannot find the file. Use a new name for a new film: re-running the same stage under the same name overwrites the old file. Saved to output/relay_kit/<run_id>/.",
    },
    "H3RelayLatentSave.stage_index": {
        zh: "本段是全片第几段。第 1 段填 0，第 2 段填 1……必须与 «Copy Bridge» 一样大（用 Chain 节点可自动改）。",
        en: "Which segment of the film. Segment 1 = 0, segment 2 = 1, … Must equal «Copy Bridge» (the Chain node can sync it automatically).",
    },
    "H3RelayLatentSave.note": {
        zh: "可留空。随手写个备注（如「22帧窗 v2」），存进文件里便于事后分辨版本。",
        en: "Optional. A short note (e.g. \"22-frame window v2\") stored in the file to tell versions apart later.",
    },
    // ===== 读上段潜空间 =====
    "H3RelayLatentLoad.run_id": {
        zh: "与 «Latent Save» 一致的片名。不一致 = 找不到上一段的文件，直接报错。",
        en: "Same film name as «Latent Save». A mismatch means the previous segment's file is not found — hard error.",
    },
    "H3RelayLatentLoad.stage_index": {
        zh: "填**本段**段号（与桥、落盘一致）。节点按「本段 − 1」读上一段：填 1 → 读 stage 0。填 0 = 第 1 段，没有上一段 ⇒ 交空上下文、桥自动直通（正常，别手动旁路）。",
        en: "Enter **this** segment's index (same as the bridge and save). The node reads segment − 1: enter 1 → reads stage 0. Enter 0 for the first segment: no predecessor, so it hands over an empty context and the bridge passes through (normal — do not bypass manually).",
    },
    "H3RelayLatentLoad.explicit_path": {
        zh: "留空则按 run_id + 段号自动推导。填了就直接读这个文件（用于断点续跑换源）。",
        en: "Empty = derive from run_id + stage. Set it to read that exact file (e.g. resuming from another source).",
    },
    // ===== 裁重叠 =====
    "H3RelayTrimAV.images": {
        zh: "本段画面解码节点（VAEDecode）的输出。收到的是含重复开头的完整画面，本节点把重复部分裁掉。",
        en: "Output of this segment's VAEDecode. It arrives with a duplicated head; this node trims the duplicate.",
    },
    "H3RelayTrimAV.trim_frames": {
        zh: "不用填。从 «Copy Bridge» 第 3 路输出（trim_frames）拉线，全自动：第 1 段自动 0、第 2 段起自动 = 钉住帧数。手填容易和桥对不上。",
        en: "No need to fill. Wire it from «Copy Bridge» output #3 (trim_frames) — fully automatic: 0 for segment 1, = pinned frames from segment 2 on. Hand-filling easily desyncs from the bridge.",
    },
    "H3RelayTrimAV.fps": {
        zh: "不用动。H3 固定 24 帧/秒，音频按这个换算着一起裁。",
        en: "Leave as is. H3 is fixed at 24 fps; audio is trimmed on the same scale.",
    },
    "H3RelayTrimAV.audio": {
        zh: "务必接线。从音频解码节点（VAEDecodeAudio）拉线。画面与声音按同一帧数一起裁，音画不串位；不接声音会比画面长出一截。",
        en: "Must be wired. Connect from VAEDecodeAudio. Video and audio are trimmed by the same frame count so A/V stays in sync; skipping it leaves audio longer than video.",
    },
    "H3RelayTrimAV.settle_frames": {
        zh: "保持 0。裁不裁「沉降区」：实测裁它**才是缝处跳帧的源头**（裁 0→跳 0.020 几乎无感／裁 8→0.044／裁 16→0.055），留下只是清晰度渐变。0=不裁（推荐）；-1=自动量（治糊但引跳，须目检）；N=固定多裁 N 帧。",
        en: "Keep 0. Whether to trim the \"settle zone\": measurements show trimming it **is the source of the seam jump** (trim 0 → jump 0.020 ≈ invisible / trim 8 → 0.044 / trim 16 → 0.055); what remains is only a sharpness gradient. 0 = no trim (recommended); -1 = auto-measure (fixes blur but adds a jump — verify by eye); N = trim a fixed extra N frames.",
    },
    "H3RelayTrimAV.seam_ghost": {
        zh: "保持 0。缝帧重影（极短交叉溶）。实测只把「一跳」拆成「两跳」、异常帧数翻倍 ⇒ 观感更明显。要消除跳帧请**减少 settle 裁切量**，或在画质域修复糊区。",
        en: "Keep 0. Seam-frame ghosting (a very short cross-dissolve). Measured: it only splits one jump into two and doubles the anomaly frames, so it looks worse. To kill the jump, **reduce the settle trim**, or repair the blur in the image domain.",
    },
    "H3RelayTrimAV.seam_ghost_alpha": {
        zh: "仅重影档用：上段末帧的权重（0.5 = 对半）。默认档下无效。",
        en: "Ghost mode only: weight of the previous segment's last frame (0.5 = even split). Inactive by default.",
    },
    "H3RelayTrimAV.settle_sharpen": {
        zh: "先保持 0，按需开。糊区锐化（unsharp）：对裁后开头若干帧做锐化，强度从缝端线性衰减到 0。推荐 0.4–1.0（先试 0.6 再目检 halo）。帧数守恒、不动音频 ⇒ 不会引入跳帧。⚠ 只能恢复对比度，救不回彻底丢失的细节。",
        en: "Keep 0, enable as needed. Blur-zone sharpening (unsharp): sharpens the first frames after the trim, strength decaying linearly to 0. Recommended 0.4–1.0 (try 0.6, then check for halos). Frame count and audio are untouched, so it cannot add a jump. ⚠ It restores contrast only; lost detail cannot be recovered.",
    },
    "H3RelayTrimAV.settle_sharpen_frames": {
        zh: "糊区锐化作用帧数（从裁后首帧起，强度线性衰减到 0）。默认 24 ≈ 1 秒；实测糊区约 16–20 帧内恢复，24 有余量。",
        en: "Frames the sharpening acts on (from the first trimmed frame, decaying linearly to 0). Default 24 ≈ 1 s; the blur zone recovers in ~16–20 frames, so 24 has margin.",
    },
    "H3RelayTrimAV.lowfreq_pull": {
        zh: "按需开，默认 0。低频残差传递：把裁后开头若干帧的低频（色档/亮度/布光）拉向上段末帧，保留本段姿态 ⇒ 无重影。0=关；0.7=上游同款（阶跃 0.0399→0.0110）；1.0=更强。优于全 RGB 混合（那个把锐度砍 49%）。",
        en: "As needed, default 0. Low-frequency residual transfer: pulls the low frequencies (tone/exposure/lighting) of the first frames toward the previous segment's last frame, keeping this segment's pose — no ghosting. 0 = off; 0.7 = upstream equivalent (step 0.0399 → 0.0110); 1.0 = stronger. Better than full-RGB blending, which cuts sharpness by 49%.",
    },
    "H3RelayTrimAV.lowfreq_frames": {
        zh: "低频对齐作用帧数（从裁后首帧起，权重线性衰减到 0）。上游同款用 12；实测缝区影响就在前 12 帧内。",
        en: "Frames the low-frequency alignment acts on (from the first trimmed frame, decaying linearly to 0). Upstream uses 12; the seam influence is within the first 12 frames.",
    },
    "H3RelayTrimAV.lowfreq_blur": {
        zh: "低频尺度（盒式模糊核）。越大越只对齐大尺度色档/布光。上游用 64；实测 32/64 差异很小。",
        en: "Low-frequency scale (box blur kernel). Larger aligns only coarse tone/lighting. Upstream uses 64; 32 and 64 differ very little in practice.",
    },
    "H3RelayTrimAV.deconv_strength": {
        zh: "按需开，默认 0。段头反卷积去模糊（Wiener 逆滤波）：按假设的 PSF 逆推原始信号，理论上能真正还原细节（不同于锐化只放大高频）。0=关；0.5~1.0 = 混合比。糊得越重 radius 越大。",
        en: "As needed, default 0. Head-zone deconvolution (Wiener inverse filter): inverts the assumed PSF to recover the original signal — unlike sharpening, which only amplifies high frequencies. 0 = off; 0.5–1.0 = blend. More blur needs a larger radius.",
    },
    "H3RelayTrimAV.deconv_radius": {
        zh: "反卷积假设的模糊半径（像素）。段头糊得越重越大；太大易出振铃（此时把 deconv_strength 降下来）。",
        en: "Assumed blur radius in pixels. Larger for a blurrier head; too large causes ringing — lower deconv_strength then.",
    },
    "H3RelayTrimAV.detail_borrow": {
        zh: "按需开，默认 0。段体高频迁移：段头保留自己的低频，高频换成段体的结构。与低频残差互补。⚠ 段头与段体内容差异大（人物位移大）时会带出纹理错位。",
        en: "As needed, default 0. Body high-frequency transfer: the head keeps its own low frequencies while its highs are replaced by the body's structure. Complements the low-frequency residual. ⚠ Large content differences (big subject motion) can cause texture misalignment.",
    },
    "H3RelayTrimAV.detail_blur": {
        zh: "高频迁移的分界尺度（盒式模糊核）：越大则被搬走的高频越粗。",
        en: "Split scale for high-frequency transfer (box blur kernel): larger moves coarser highs.",
    },
    "H3RelayTrimAV.hist_match": {
        zh: "按需开，默认 0。直方图匹配：把段头的色阶分布对齐到段体。比低频残差更强 —— 那个只对齐均值，这个对齐整条分布。0=关；1=完全对齐。",
        en: "As needed, default 0. Histogram matching: aligns the head's tonal distribution to the body. Stronger than the low-frequency residual, which matches only the mean. 0 = off; 1 = full match.",
    },
    "H3RelayTrimAV.wb_match": {
        zh: "按需开，默认 0。灰世界白平衡校正：把段头的 R:G:B 比例（色温/色调）对齐段体。对症「色温滑档」型漂移；增益限幅 ±25% 防偏色。",
        en: "As needed, default 0. Gray-world white balance: aligns the head's R:G:B ratio (temperature/tint) to the body. Targets color-temperature drift; gain is clamped at ±25% to prevent tinting.",
    },
    "H3RelayTrimAV.match_prev": {
        zh: "按需开，默认 0。跨段统计匹配：把段头的色档/曝光（逐通道均值+标准差）对齐**上段末帧**。比低频残差多了二阶（对比度）与逐通道色度。只对齐统计量、不复制姿态 ⇒ 无重影。建议从 0.5 起试。",
        en: "As needed, default 0. Cross-segment statistics matching: aligns the head's tone/exposure (per-channel mean + std) to the **previous segment's last frame**. Adds second-order (contrast) and per-channel chroma over the low-frequency residual. Statistics only, no pose copying — no ghosting. Start at 0.5.",
    },
    "H3RelayTrimAV.match_prev_frames": {
        zh: "配合 match_prev 用。作用帧数：从裁后首帧起算，权重线性衰减到 0（缝端最强 → 尾端不动）。",
        en: "Used with match_prev. Frames acted on: from the first trimmed frame, decaying linearly to 0 (strongest at the seam, zero at the tail).",
    },
    "H3RelayTrimAV.match_prev_gain_max": {
        zh: "护栏，一般不用动。逐通道对比度增益上限。调大允许更猛的对齐，但可能把已通过的内容改坏。",
        en: "Guard rail, normally untouched. Per-channel contrast gain cap. Raising it allows stronger alignment but can damage approved footage.",
    },
    "H3RelayTrimAV.match_prev_offset_max": {
        zh: "护栏，一般不用动。逐通道亮度/色度偏移上限。调大允许更大的色档修正，但过大易见「整段换色」。",
        en: "Guard rail, normally untouched. Per-channel luma/chroma offset cap. Raising it allows bigger tone corrections but too much looks like a wholesale color shift.",
    },
    "H3RelayTrimAV.run_id": {
        zh: "观测用，可留空。续接 run 标识（与 «Latent Save» 一致）。填了才会把每段外观统计写入 <run>/appearance_log.jsonl 并算漂移曲线。",
        en: "For observation, may be empty. Relay run id (same as «Latent Save»). When set, per-segment appearance stats go to <run>/appearance_log.jsonl and a drift curve is computed.",
    },
    "H3RelayTrimAV.save_pcm": {
        zh: "建议保持开。把本段（裁后）音频额外存一份 PCM 边车：段文件音轨是 AAC 有损的，拼片时若从 mp4 再解码就是第二次有损；存了边车 ⇒ 音频只编码一代。开=多写 ~2 MB/段 到 output/relay_kit/pcm/。",
        en: "Keep on. Also writes a lossless PCM sidecar for this segment's (trimmed) audio: the mp4 track is lossy AAC, so concatenating from it would be a second generation of loss. With the sidecar, audio is encoded only once. On = ~2 MB extra per segment under output/relay_kit/pcm/.",
    },
    "H3RelayTrimAV.diagnostics": {
        zh: "默认关，本地产线才开。三路只读观测（DTW 对齐代价 / 裁量→跳跃曲线 / 外观三元组与漂移曲线），都不参与任何裁量。开 = 多约 0.4 s/段 CPU，零 GPU。",
        en: "Off by default; enable on the local pipeline. Three read-only probes (DTW alignment cost / trim-vs-jump curve / appearance triple + drift curve) that never affect any trim. On = ~0.4 s/segment CPU extra, zero GPU.",
    },
    // ===== 复合桥 =====
    "H3RelayCopyBridge.latent": {
        zh: "本段初始 AV latent（CSGlideCastCS / EmptyH3LatentAV 的 latent 输出）。上一段尾部会逐位写进它的开头，并附噪声掩码。",
        en: "This segment's base AV latent (output of CSGlideCastCS / EmptyH3LatentAV). The previous segment's tail is written bit-exact into its head, with a noise mask.",
    },
    "H3RelayCopyBridge.context_latent": {
        zh: "上一段的完整 AV latent（从「🔗 读上段潜空间」接过来）。**第 1 段也要接** —— 那个节点会交空上下文、本桥自动直通（正常）。⚠ 别拔这根线或旁路上游：本输入必填，缺了整张图会被提交校验拒掉。",
        en: "The previous segment's full AV latent (from «🔗 H3 Relay · Latent Load»). **Wire it for segment 1 too** — that node hands over an empty context and this bridge passes through (normal). ⚠ Do not unplug it or bypass the upstream: this input is required, and the whole graph is rejected by prompt validation without it.",
    },
    "H3RelayCopyBridge.context_frames": {
        zh: "拷贝窗口帧数，只认 5+17k 网格（5/22/39/56/73/90/107/124）。须小于本段帧数（前缀必须给新内容留位置）。",
        en: "Copy window in frames; only the 5+17k grid is accepted (5/22/39/56/73/90/107/124). Must be less than this segment's frame count (the prefix must leave room for new content).",
    },
    "H3RelayCopyBridge.mask_mode": {
        zh: "🔴 生产档 hard（真续接唯一推荐，保持默认）。掩码语义：每步输出 = 模型生成 × m + 上段尾 × (1−m)，m=0 才钉住。ramp/window = 对照档，taper/blend = 实验档。⚠ 实测四档在缝处全都跳，只是失败模式不同 ⇒ 单靠掩码调参治不好。",
        en: "🔴 Production tier: hard (the only recommendation for real continuation; keep the default). Mask semantics: output = model × m + previous tail × (1−m); only m=0 pins. ramp/window = comparison tiers, taper/blend = experimental. ⚠ All four jump at the seam in measurements — only the failure mode differs, so mask tuning alone does not fix it.",
    },
    "H3RelayCopyBridge.taper_tokens": {
        zh: "🔴 实验档 taper 专属。缝端前多少个 token 参与线性过渡。",
        en: "🔴 Experimental tier taper only. How many seam-side tokens take part in the linear transition.",
    },
    "H3RelayCopyBridge.seam_min": {
        zh: "🔴 实验档 taper 专属。缝端掩码下限（m 值）。0 = 缝端完全硬锁；0.3 = 缝端仍留 30% 重绘自由度。头部恒为 1.0 全重绘。",
        en: "🔴 Experimental tier taper only. Seam-side mask floor (m). 0 = fully locked at the seam; 0.3 leaves 30% repaint freedom. The head is always 1.0 (full repaint).",
    },
    "H3RelayCopyBridge.pin_audio": {
        zh: "把上一段音频尾也拷进本段音频 latent 开头（采样上下文用）。掩码只做视频流；可见的声画拼接仍归「裁重叠」与组装层。",
        en: "Also copies the previous segment's audio tail into this segment's audio latent head (sampling context). The mask covers the video stream only; visible A/V splicing still belongs to «Trim AV» and the assembly layer.",
    },
    "H3RelayCopyBridge.ramp_top": {
        zh: "🟡 对照档 ramp 专属。缝端最大 m（= 该 token 参与去噪的 sigma 比例）。0 = 退化为 hard；0.25 默认。理论最优是 1.0（断崖归零），但台阶随之变大 ⇒ 想不恶化必须同时加长窗口。",
        en: "🟡 Comparison tier ramp only. Maximum m at the seam (= sigma fraction that token denoises at). 0 degenerates to hard; default 0.25. Theory favors 1.0 (zero cliff), but the step grows with it — lengthen the window at the same time or quality degrades.",
    },
    "H3RelayCopyBridge.ramp_tokens": {
        zh: "🟡 对照档 ramp 专属。参与斜坡的缝端 token 数；0 = 整个拷贝窗铺开。小值（2~3）= 「只松缝、锁运动」的窄斜坡。",
        en: "🟡 Comparison tier ramp only. Seam-side tokens on the ramp; 0 = spread across the whole copy window. Small values (2–3) = a narrow ramp that loosens the seam but locks motion.",
    },
    "H3RelayCopyBridge.anchor_latent": {
        zh: "0.5.0 统计纠偏基准，可选。全局锚段（通常第 1 段）的 AV latent。拷贝前缀的均值/方差被拉向锚段；纠偏只作用于被裁掉的钉住前缀，不碰上一段成片。用「读上段潜空间」+ explicit_path 读第 1 段即可。",
        en: "0.5.0 statistics anchor, optional. The global anchor segment's (usually segment 1) AV latent. The copy prefix's mean/variance is pulled toward the anchor; the correction touches only the trimmed pin prefix, never the previous segment's footage. Read segment 1 via «Latent Load» + explicit_path.",
    },
    "H3RelayCopyBridge.anchor_blend": {
        zh: "0.5.0 纠偏强度，0~1。1 = 全量对齐（默认，锚段就是审美基准时用）；锚段与本段允许有意风格差异时调低。不接 anchor_latent 时无效。",
        en: "0.5.0 correction strength, 0–1. 1 = full alignment (default, when the anchor is the intended look); lower it if the anchor and this segment may legitimately differ in style. Inactive without anchor_latent.",
    },
    "H3RelayCopyBridge.blend_top": {
        zh: "🔴 实验档 blend 专属。缝端模型占比上限（对应 ramp 的 ramp_top）。0 = 退化成 hard；越大越信任本段自己的预测。建议 0.5 起试。",
        en: "🔴 Experimental tier blend only. Cap on the model's share at the seam (ramp's ramp_top equivalent). 0 degenerates to hard; larger trusts this segment's own prediction more. Start at 0.5.",
    },
    "H3RelayCopyBridge.blend_tokens": {
        zh: "🔴 实验档 blend 专属。参与融合的缝端 token 数；0 = 整个拷贝窗铺开。小值（2~3）= 「只融缝、锁运动」。",
        en: "🔴 Experimental tier blend only. Seam-side tokens blended; 0 = spread across the whole copy window. Small values (2–3) = blend the seam only, lock motion.",
    },
    "H3RelayCopyBridge.blend_shape": {
        zh: "仅 blend 模式。窗形：smoothstep = x²(3−2x) 多项式 S 曲线（默认）；hann = (1−cos πx)/2 余弦 S 曲线。两者两端导数都为 0 ⇒ 与窗外衔接无折角。",
        en: "blend mode only. Window shape: smoothstep = x²(3−2x) polynomial S-curve (default); hann = (1−cos πx)/2 cosine S-curve. Both have zero slope at the ends, so the join to outside the window has no kink.",
    },
    "H3RelayCopyBridge.window_top": {
        zh: "🟡 对照档 window 专属。窗函数峰值（中心处允许的最大重绘自由度）。0 = 退化为 hard。与 ramp 的关键差别：这是**对称窗**，缝端回落到低位 ⇒ 缝端重新钉牢。",
        en: "🟡 Comparison tier window only. Window peak (max repaint freedom at the center). 0 degenerates to hard. Key difference from ramp: this is a **symmetric** window, so the seam end falls back low and is pinned again.",
    },
    "H3RelayCopyBridge.window_shape": {
        zh: "仅 window 模式。窗形：sine = sin(π(i+0.5)/n) 端点低但不为 0（默认，留一点自由度防死钉）；hann = (1−cos 2πx)/2 端点严格 0（两端完全硬钉）。",
        en: "window mode only. Window shape: sine = sin(π(i+0.5)/n), low but non-zero at the ends (default; a little freedom avoids dead-pinning); hann = (1−cos 2πx)/2, exactly 0 at the ends (fully pinned).",
    },
    "H3RelayCopyBridge.conditioning": {
        zh: "可选·复合桥。接上后本节点**同时**在 conditioning 上追加钉帧（管取景/构图），与 latent 钉住窗（管运动）并联生效。不接 = 只做拷贝桥（与旧版一致）。实测缝处亮度阶跃 0.0009 vs 单 cond 桥 0.0097（10.8× 更好）。",
        en: "Optional, composite bridge. When wired, this node **also** appends pinned frames to the conditioning (framing/composition), in parallel with the latent pin window (motion). Unwired = copy bridge only (identical to older versions). Measured seam luma step 0.0009 vs 0.0097 for a cond-only bridge (10.8× better).",
    },
    "H3RelayCopyBridge.run_id": {
        zh: "可选·复合桥。片子名，用来自动读外观锚段（ref_anchor_stage ≥ 0 时）。",
        en: "Optional, composite bridge. Film name, used to auto-read the look-anchor segment (when ref_anchor_stage ≥ 0).",
    },
    "H3RelayCopyBridge.stage_index": {
        zh: "可选·复合桥。本段段号；与 ref_anchor_stage 一起用于自动读锚。",
        en: "Optional, composite bridge. This segment's index; used with ref_anchor_stage to auto-read the anchor.",
    },
    "H3RelayCopyBridge.ref_anchor_latent": {
        zh: "可选·复合桥。全局外观锚（通常第 1 段）的 AV latent。近零噪声全程骑乘每一步 = attention sink，防长程漂移。",
        en: "Optional, composite bridge. The global look anchor's (usually segment 1) AV latent. Rides every step at near-zero noise as an attention sink, preventing long-range drift.",
    },
    "H3RelayCopyBridge.ref_anchor_stage": {
        zh: "≥ 0 = 没接 ref_anchor_latent 时，自动读 output/relay_kit/<run_id>/stage_<该值> 当锚。",
        en: "≥ 0 = when ref_anchor_latent is unwired, auto-read output/relay_kit/<run_id>/stage_<value> as the anchor.",
    },
    "H3RelayCopyBridge.ref_anchor_frames": {
        zh: "外观锚取该段**开头**多少帧（取头不取尾）。",
        en: "How many frames from the **start** of that segment form the look anchor (head, not tail).",
    },
    "H3RelayCopyBridge.audio_ref_seconds": {
        zh: "可选，音频参考窗长度（秒）。0 = 自动（推荐）：从上一段尾部往前累计够 2 秒有声内容就停（上限 6 秒）——比手填稳。⚠ 接了「声锚」则本项不生效。",
        en: "Optional, audio reference window in seconds. 0 = auto (recommended): accumulate 2 s of audible content backwards from the previous tail, capped at 6 s — more robust than hand-filling. ⚠ Ignored when «voice anchor» is wired.",
    },
    "H3RelayCopyBridge.voice_anchor": {
        zh: "可选·声锚。**本段说话人**的音频锚（LoadAudio → VAEEncodeAudio 的 latent）。提供时钉住前缀与 audio_ref 都改用声锚尾窗，不再取上一段音频尾。**缝上换人才必须接**；同人续接不必接。想关掉请拔线，不要接空 latent（会当场报错）。",
        en: "Optional voice anchor. Audio anchor for **this segment's speaker** (latent from LoadAudio → VAEEncodeAudio). When supplied, both the pinned prefix and audio_ref use the anchor's tail window instead of the previous segment's audio. **Required only when the speaker changes at the seam**; same-speaker continuation does not need it. To disable, unplug it — do not wire an empty latent (hard error).",
    },
    // ===== 后处理 =====
    "H3RelayPost.images": {
        zh: "接 «🔗 H3 Relay · 裁重叠» 的 images 输出（裁后的画面）。本节点只改画质，不改帧数、不动音频。",
        en: "Connect «🔗 H3 Relay · Trim AV»'s images output (the trimmed picture). This node only changes image quality; frame count and audio are untouched.",
    },
    "H3RelayPost.guide": {
        zh: "接 «🔗 H3 Relay · 裁重叠» 第 4 路输出 prev_tail（= 钉住区最后一帧，缝的另一侧）。只有跨段统计匹配与低频残差传递需要它。⚠ 它是否**真**是上段末帧取决于走哪条桥：拷贝桥下是逐位拷贝 ⇒ 就是；Latent 桥下是本段重画 ⇒ 只是近似（对错参照）。",
        en: "Connect «🔗 H3 Relay · Trim AV» output #4, prev_tail (the last pinned frame — the other side of the seam). Only cross-segment statistics matching and low-frequency residual transfer need it. ⚠ Whether it **really** is the previous last frame depends on the bridge: under the copy bridge it is bit-exact (yes); under the latent bridge it is this segment's repaint — an approximation, i.e. the wrong reference.",
    },
    "H3RelayPost.match_prev": {
        zh: "组 1·跨段。跨段统计匹配：把段头的色档/曝光对齐 guide。只对齐统计量、不复制姿态 ⇒ 无重影。🔴 在 Latent 桥路线上请保持 0（那里缝已到 0.0007，而 guide 只是近似 ⇒ 对齐它反而把首帧推离真参照）。",
        en: "Group 1, cross-segment. Cross-segment statistics matching: aligns the head's tone/exposure to guide. Statistics only, no pose copying — no ghosting. 🔴 Keep 0 on the latent-bridge route: the seam there is already 0.0007 while guide is only an approximation, so aligning to it pushes the first frame away from the true reference.",
    },
    "H3RelayPost.match_prev_frames": {
        zh: "配合 match_prev。作用帧数：从首帧起算，权重线性衰减到 0。",
        en: "Used with match_prev. Frames acted on: from frame 0, decaying linearly to 0.",
    },
    "H3RelayPost.match_prev_gain_max": {
        zh: "护栏。逐通道对比度增益上限（防把已通过的内容改坏）。",
        en: "Guard rail. Per-channel contrast gain cap (protects approved footage).",
    },
    "H3RelayPost.match_prev_offset_max": {
        zh: "护栏。逐通道亮度/色度偏移上限（防「整段换色」）。",
        en: "Guard rail. Per-channel luma/chroma offset cap (prevents a wholesale color shift).",
    },
    "H3RelayPost.lowfreq_pull": {
        zh: "组 1·跨段。低频残差传递：只把段头低频色档对齐 guide，不动细节与姿态 ⇒ 无重影。与 match_prev 作用域重叠，建议二选一。需要 guide。",
        en: "Group 1, cross-segment. Low-frequency residual transfer: aligns only the head's coarse tone to guide, leaving detail and pose alone — no ghosting. Overlaps match_prev's scope; pick one. Needs guide.",
    },
    "H3RelayPost.lowfreq_frames": {
        zh: "配合 lowfreq_pull。作用帧数（权重线性衰减到 0）。",
        en: "Used with lowfreq_pull. Frames acted on (decaying linearly to 0).",
    },
    "H3RelayPost.lowfreq_blur": {
        zh: "配合 lowfreq_pull。低频尺度（盒式模糊核，上游用 64）。",
        en: "Used with lowfreq_pull. Low-frequency scale (box blur kernel; upstream uses 64).",
    },
    "H3RelayPost.head_zone_frames": {
        zh: "组 2+3 共用。段头作用区长度（帧）：直方图匹配 / 白平衡 / 反卷积 / 段体高频迁移四项都按这个帧数作用。⚠ 组 4 的糊区锐化不看这个。",
        en: "Shared by groups 2+3. Head-zone length in frames: histogram matching / white balance / deconvolution / body high-frequency transfer all act over this many frames. ⚠ Group 4's sharpening does not use it.",
    },
    "H3RelayPost.hist_match": {
        zh: "组 2·段内。直方图匹配：把段头的色阶**分布**对齐到本段段体。治「段头↔段体」色阶漂移，不需要 guide。",
        en: "Group 2, intra-segment. Histogram matching: aligns the head's tonal **distribution** to this segment's body. Fixes head-to-body tonal drift; no guide needed.",
    },
    "H3RelayPost.wb_match": {
        zh: "组 2·段内。灰世界白平衡：把段头 R:G:B 比例对齐段体。与直方图匹配正交（那个管亮度总量，这个管色温）。",
        en: "Group 2, intra-segment. Gray-world white balance: aligns the head's R:G:B ratio to the body. Orthogonal to histogram matching (that one handles overall luma, this one handles temperature).",
    },
    "H3RelayPost.deconv_strength": {
        zh: "组 3·补高频。反卷积去模糊（Wiener）：提升段头高频。太大易出振铃（此时把强度降下来）。",
        en: "Group 3, high-frequency recovery. Deconvolution (Wiener): boosts head high frequencies. Too much rings — lower the strength then.",
    },
    "H3RelayPost.deconv_radius": {
        zh: "配合 deconv_strength。模糊核半径。",
        en: "Used with deconv_strength. Blur kernel radius.",
    },
    "H3RelayPost.detail_borrow": {
        zh: "组 3·补高频。段体高频迁移：把段头的高频换成段体的结构。比反卷积更「像真的」，但可能与段头内容不符。",
        en: "Group 3, high-frequency recovery. Body high-frequency transfer: replaces the head's highs with the body's structure. Looks more real than deconvolution, but may not match the head's content.",
    },
    "H3RelayPost.detail_blur": {
        zh: "配合 detail_borrow。高频分离尺度。",
        en: "Used with detail_borrow. High-frequency split scale.",
    },
    "H3RelayPost.settle_sharpen": {
        zh: "组 4·收口。糊区锐化（unsharp）：对开头 N 帧做渐变锐化。不裁、不动时间轴 ⇒ 不可能引入跳帧。建议 0.4–1.0。",
        en: "Group 4, finishing. Blur-zone sharpening (unsharp): gradually sharpens the first N frames. No trimming, no timeline change, so a jump is impossible. Recommended 0.4–1.0.",
    },
    "H3RelayPost.settle_auto": {
        zh: "推荐替代上面的 settle_sharpen。自适应糊区补偿：节点**当场量**每帧清晰度对段体基线的亏空，按亏空比例锐化；已达标的帧不动。0 = 关（默认）；0.5–1.0 = 推荐起点。与 settle_sharpen 同开时只作用本项。",
        en: "Recommended over settle_sharpen above. Adaptive blur compensation: the node **measures on the spot** how far each frame falls short of the body baseline and sharpens in proportion; frames already on target stay untouched. 0 = off (default); 0.5–1.0 = recommended start. If both are on, only this one acts.",
    },
    "H3RelayPost.settle_sharpen_frames": {
        zh: "配合 settle_sharpen。作用帧数（渐变衰减到 0）。⚠ 只管糊区锐化这一项；组 2/组 3 用 head_zone_frames。",
        en: "Used with settle_sharpen. Frames acted on (decaying to 0). ⚠ Governs sharpening only; groups 2/3 use head_zone_frames.",
    },
    "H3RelayPost.match_prev_stats_frames": {
        zh: "配合 match_prev。统计量取几帧。1（默认）= 只取紧贴缝的那一帧 ⇒ 修正量恰是缝上的阶跃，首帧被拉向 guide 而不越过它；0 = 旧口径（整段聚合，首帧被推过 guide，实测 ×12.6，仅对照）。",
        en: "Used with match_prev. How many frames the statistics use. 1 (default) = only the frame touching the seam, so the correction equals the seam step and the first frame is pulled toward guide, never past it; 0 = legacy (whole-zone aggregate, pushes the first frame past guide — ×12.6 in measurements; comparison only).",
    },
    "H3RelayPost.baseline": {
        zh: "组 2+3 的分母。段体参考怎么取：robust（默认）= 逐帧亮度取中央 50% 的帧再算统计，并报离散度；离数度 > 0.08 ⇒ 判「基准不可信」，组 2/3 自动弃权。legacy = 0.5.0 旧口径（整段均值，不筛不弃权），仅对照复现。",
        en: "Denominator for groups 2+3. How the body reference is taken: robust (default) = take the middle 50% of frames by per-frame luma, then compute stats and report dispersion; dispersion > 0.08 marks the reference unreliable and groups 2/3 abstain. legacy = the 0.5.0 whole-segment mean (no filtering, no abstaining), for comparison only.",
    },
    "H3RelayPost.cross_seg_ack": {
        zh: "跨段两项的总闸。确认 guide 是**真参照**才打勾。不打勾（默认）⇒ match_prev 与 lowfreq_pull 自动弃权。cond 桥下 prev_tail 只是近似 ⇒ 对齐它反而把首帧推离真参照。",
        en: "Master switch for the two cross-segment options. Tick only when you have confirmed guide is a **true** reference. Unticked (default) makes match_prev and lowfreq_pull abstain. Under the cond bridge, prev_tail is only an approximation, so aligning to it pushes the first frame away from the true reference.",
    },
    // ===== 连跑控制 =====
    "H3RelayChain.segments": {
        zh: "连跑几段。只对「⏩ 连跑」按钮有效：填 2 = 跑 2 段就停；填 0 = 一直跑直到点「⏹ Stop」。填了 prompts 时段数不要超过词块数。",
        en: "How many segments the chain run covers. Only affects the «⏩ Chain» button: 2 = stop after 2 segments; 0 = keep going until you press «⏹ Stop». With prompts filled, do not exceed the block count.",
    },
    "H3RelayChain.status": {
        zh: "不用填。显示连跑状态：跑到第几段 / 有没有在排队 / 出错原因。🔴 按钮点了没反应时先看这一格。",
        en: "No need to fill. Shows run status: current segment / queued or not / error reason. 🔴 If the button seems dead, read this cell first.",
    },
    "H3RelayChain.prompts": {
        zh: "可选，每一段各自的词。用单独一行 --- 把词分成几块：第 1 块给第 1 段、第 2 块给第 2 段……留空 = 不换词。也可以从别的节点连线进来，这时以连线为准。",
        en: "Optional, per-segment prompts. Split with a line containing only ---: block 1 → segment 1, block 2 → segment 2, … Empty = no prompt swap. Can also be wired from another node, in which case the link wins.",
    },
    "H3RelayChain.prompt_target": {
        zh: "已废弃（0.6.15），保留只为不让旧工作流的槽位错位。现在词由本节点的 prompt 输出口给 —— 把它连到出词节点的 prompt 输入即可。",
        en: "Deprecated (0.6.15); kept only so older workflows' widget slots do not shift. Prompts now come from this node's prompt output — connect it to the prompt input of your prompt node.",
    },
    "H3RelayChain.auto_concat": {
        zh: "可选。打开 = 「⏩ 连跑」跑完自动把这几段拼成一条成片（画面无损，几秒钟）。关着（默认）= 只得到 N 个分段 mp4。",
        en: "Optional. On = the «⏩ Chain» run automatically concatenates the segments into one film (visually lossless, seconds). Off (default) = you get N segment mp4s only.",
    },
    "H3RelayChain.concat_name": {
        zh: "可选。成片叫什么名字（不用写 .mp4）。留空 = 用落盘时的 run_id。成片存在 output/ 目录，路径会显示在 status 那一格。",
        en: "Optional. Name of the film (no .mp4 needed). Empty = the run_id used at save time. Written to output/; the path shows up in the status cell.",
    },
    "H3RelayChain.audio_out": {
        zh: "成片音轨用哪种。只影响拼出来的成片，不影响分段文件。aac_256k（默认）= 通用档；aac_192k = 一样能放、小约 25%；pcm_lossless = 无损母版（文件大很多，浏览器预览无声，别当预览档）。",
        en: "Audio codec for the concatenated film. Affects the film only, never the segment files. aac_256k (default) = general purpose; aac_192k = plays the same, ~25% smaller; pcm_lossless = lossless master (much larger, silent in browser preview — do not use as a preview format).",
    },
    "H3RelayChain.video_crf": {
        zh: "成片画面质量，默认 16，平时用不到。成片默认走画面直接拷贝 = 无损，不重编码。只有各段规格对不上而必须重编码时才生效：数字越小越清晰。",
        en: "Film image quality, default 16, rarely needed. The film is stream-copied by default (lossless, no re-encode). It applies only when segment specs mismatch and a re-encode is forced: lower = sharper, larger.",
    },
    "H3RelayChain.stage_index": {
        zh: "现在跑第几段。段号从 0 开始（第 1 段 = 0），它决定本节点输出 prompts 里的第几块词。画布上点 ▶/✔/⏩ 会自动和桥、落盘、读上段潜空间一起改。填得比词块数大 ⇒ 执行时直接报错。",
        en: "Which segment is running. Indices start at 0 (segment 1 = 0); this picks which prompt block this node outputs. On canvas, ▶/✔/⏩ sync it with the bridge, save, and latent-load nodes automatically. Larger than the block count = hard error at execution.",
    },
    "H3RelayChain.run_id": {
        zh: "可选，续跑用。和桥 / 落盘上填的 run_id 填成一样。填了之后每跑一段会把「跑到第几段」记到段文件同目录的 _progress.json ⇒ 中途卡死/重启后点「⏭ 续跑」就能接着跑。留空 = 不记进度。",
        en: "Optional, for resuming. Match the run_id on the bridge / save nodes. When set, each run records progress to _progress.json next to the segment files, so «⏭ Resume» picks up after a crash or restart. Empty = no progress file.",
    },
    "H3RelayChain.concat_result": {
        zh: "不用填。成片路径：拼接成功后这里显示 output/ 下的成片文件（可选中复制）。它不会被状态消息覆盖；失败时显示失败原因（完整报告在控制台）。",
        en: "No need to fill. Film path: after a successful concat it shows the film under output/ (selectable). Status messages never overwrite it; on failure it shows the reason (full report in the console).",
    },
    // ===== 音频缝 =====
    "H3RelayAudioSeam.audio": {
        zh: "从「裁重叠」的 audio 输出口拉线过来。第 1 段没有「裁重叠」节点时，直接接音频解码 VAEDecodeAudio。",
        en: "Wire from «Trim AV»'s audio output. For segment 1, with no «Trim AV» node, connect straight to VAEDecodeAudio.",
    },
    "H3RelayAudioSeam.run_id": {
        zh: "片名。必须和「落盘 / 桥」上的 run_id 一字不差 —— 本节点要靠它找到上一段落盘的音频当床源。",
        en: "Film name. Must match the run_id on the save / bridge nodes exactly — this node uses it to find the previous segment's saved audio as the bed source.",
    },
    "H3RelayAudioSeam.stage_index": {
        zh: "本段是全片第几段。第 1 段填 0，第 2 段填 1……第 1 段无缝可补会直接直通（但仍会落盘音频，供第 2 段当床源）。",
        en: "Which segment of the film. Segment 1 = 0, segment 2 = 1, … Segment 1 has no seam and passes through (its audio is still saved for segment 2's bed).",
    },
    "H3RelayAudioSeam.patch_seconds": {
        zh: "组 1·音频缝。头部补丁长度（秒）。0 = 关（默认，逐位直通）。建议 2.0：把段首 2 秒的生成瞬态整段换成上一段的环境声。⚠ 前提是段首本来就不该有台词。",
        en: "Group 1, audio seam. Head patch length in seconds. 0 = off (default, bit-exact passthrough). 2.0 recommended: replaces the first 2 s of generation transients with the previous segment's ambience. ⚠ Assumes the head is not supposed to contain dialogue.",
    },
    "H3RelayAudioSeam.tile_seconds": {
        zh: "组 2·床环铺。床源改取这么长的瓦片，自叠化环铺满补丁长度。0 = 整窗直取最静 N 秒（默认）。上一段最长干净环境窗短于补丁长度时用它（建议 1.2）。",
        en: "Group 2, bed loop. Take a tile this long from the bed source and crossfade-loop it to fill the patch. 0 = take the N quietest seconds directly (default). Use it when the previous segment's longest clean ambience is shorter than the patch (1.2 suggested).",
    },
    "H3RelayAudioSeam.fade_seconds": {
        zh: "组 3。补丁边界（第 N 秒处）的交叉淡变宽度。0 = 硬切（会有可闻的接点）；0.25 是产线实测值。",
        en: "Group 3. Crossfade width at the patch boundary (at N seconds). 0 = hard cut (an audible seam); 0.25 is the measured pipeline value.",
    },
    "H3RelayAudioSeam.bed_stage": {
        zh: "用第几段的音频当床源（默认 0 = 第 1 段）。同场景环境声是 stationary 的，取第 1 段最稳。⚠ 必须小于本段段号。",
        en: "Which segment's audio serves as the bed (default 0 = segment 1). Same-scene ambience is stationary, so segment 1 is the most stable. ⚠ Must be less than this segment's index.",
    },
    "H3RelayAudioSeam.note": {
        zh: "可留空。备注，存进落盘文件的元数据里便于事后分辨版本。",
        en: "Optional. A note stored in the saved file's metadata to tell versions apart later.",
    },
    "H3RelayAudioSeam.bed_select": {
        zh: "默认 tail，别改。床声从床源哪里取：tail（默认）= 取床源尾部与补丁等长的一段，紧邻缝 ⇒ 电平音色天然连续，再整体对齐到缝前电平（限幅 ±6 dB）。quiet = 0.5.0 旧行为（取全局最静窗），实测会把补丁换成更静的内容 ⇒ 缝上出现「静音洞」，仅对照复现用。",
        en: "Keep tail. Where the bed is taken from: tail (default) = the bed source's tail, matching the patch length, right next to the seam, so level and timbre are naturally continuous; then aligned to the pre-seam level (±6 dB clamp). quiet = the 0.5.0 behavior (globally quietest window), which measurements show swaps in quieter content and creates a silent hole at the seam — comparison only.",
    },
    "H3RelayAudioSeam.join_curve": {
        zh: "joined 用。缝处交叉曲线：qsin（默认，等功率）—— 两段内容不相关时不掉电平；tri（线性，ffmpeg acrossfade 默认）—— 实测中缝 −4.7 dB，听感「音量先小再恢复」，仅对照复现。",
        en: "For joined. Crossfade curve at the seam: qsin (default, equal-power) keeps level when the two parts are uncorrelated; tri (linear, ffmpeg acrossfade's default) measures −4.7 dB mid-seam and sounds like a dip and recovery — comparison only.",
    },
    "H3RelayAudioSeam.join_prime_ms": {
        zh: "joined 用。每段头要丢掉的编码器 priming（毫秒）。默认 33 ms（AAC 实测值 @32k = 1056 样本）。它不是内容，丢掉是对齐修正；0 = 不丢（对照用）。",
        en: "For joined. Encoder priming to drop from each segment head, in ms. Default 33 ms (measured AAC @32k = 1056 samples). It is not content, so dropping it is an alignment fix; 0 = keep (comparison only).",
    },
    "H3RelayAudioSeam.join_cross_ms": {
        zh: "joined 用。拼接缝的交叉淡变长度（毫秒）。默认 0 = 不做交叉。🔴 交叉淡变必然缩短时间轴 ⇒ 每缝后段音频提前、且逐段累积（第 3 段起口型对不上）⇒ 只在同时给了 join_align_seconds 时才有意义。只填 cross 不填 align ⇒ 直接报错。",
        en: "For joined. Crossfade length at the join, in ms. Default 0 = no crossfade. 🔴 A crossfade necessarily shortens the timeline, so audio runs ahead after each seam and the error accumulates (lip-sync breaks from segment 3 on), so it is meaningful only together with join_align_seconds. cross without align = hard error.",
    },
    "H3RelayAudioSeam.join_segment_seconds": {
        zh: "joined 用。每段音频的有效时长（秒）= 该段视频帧数/fps。节点 PCM 通常比 mp4 长（实测 7.5s vs 3.75s），不截断则拼接超长。0 = 不截（旧行为）。产线 = 90帧/24fps = 3.75。",
        en: "For joined. Each segment's effective audio duration in seconds = video frames / fps. Node PCM is usually longer than the mp4 (measured 7.5 s vs 3.75 s), and without truncation the join runs long. 0 = no truncation (legacy). Pipeline = 90 frames / 24 fps = 3.75.",
    },
    "H3RelayAudioSeam.join_align_seconds": {
        zh: "joined 用。每缝的画面裁量（秒）= 裁掉的视频帧数/fps。>0 = J-cut 时间轴守恒：后段音频从裁量处进入，输出与裁后视频严格等长、缝上无电平凹陷。0 = 旧缩短语义（仅兼容）。要求 ≥ join_cross_ms。",
        en: "For joined. Per-seam picture trim in seconds = trimmed frames / fps. >0 = J-cut timeline conservation: the next segment's audio enters at the trim point, output is exactly as long as the trimmed video, and there is no level dip at the seam. 0 = legacy shorten semantics (compat only). Must be ≥ join_cross_ms.",
    },
    "H3RelayAudioSeam.patch_guard": {
        zh: "patch 台词守卫（默认开，0.6.5）。patch_seconds > 0 会整段替换本段头部 —— 本段自己的台词落在里面就被吞（实测「这家店」0.60–1.70s 被 2.0s patch 吃掉）。开 ⇒ 自动探测头部台词起点，patch 收缩到台词前 0.40s；头部本来无台词 ⇒ 与关闭时逐位一致（零副作用）。",
        en: "Patch dialogue guard (on by default, 0.6.5). patch_seconds > 0 replaces this segment's head wholesale, swallowing any of its own dialogue inside (measured: \"这家店\" at 0.60–1.70 s eaten by a 2.0 s patch). On = auto-detect the head dialogue onset and shrink the patch to 0.40 s before it; if the head has no dialogue the result is bit-identical to off (zero side effects).",
    },
    "H3RelayAudioSeam.patch_guard_layers": {
        zh: "台词守卫·判据档位（默认 0 = 0.6.9 行为，逐位一致）。⚠ 档 1~3 是**降低灵敏度**，与「宁枉勿纵」的设计意图相反，且阈值只在 16 条素材 + patch=2.0 下标定 ⇒ 依据不足，默认关。🔴 别把台词安全寄托在本开关上：主防线是 patch_seconds ≤ 1.2。",
        en: "Dialogue guard criteria tier (default 0 = 0.6.9 behavior, bit-identical). ⚠ Tiers 1–3 **reduce sensitivity**, opposite to the guard's \"better safe than sorry\" intent, and the thresholds were calibrated on only 16 clips at patch=2.0 — insufficient evidence, hence off by default. 🔴 Do not rely on this switch for dialogue safety: the main defense is patch_seconds ≤ 1.2.",
    },
    "H3RelayAudioSeam.declick_ratio": {
        zh: "组 4·孤立瞬态抑制。判据：局部峰值 / 背景底噪 的倍数。0 = 关（逐位直通）。建议 4.0。它判的是**周围有多静**，不是它本身多响 —— 因为语音辅音也是短促脉冲，只看响度会连台词一起掐。只动那一处（两端各 2ms 淡变），长度守恒。",
        en: "Group 4, isolated transient suppression. Criterion: local peak / background floor ratio. 0 = off (bit-exact passthrough). 4.0 recommended. It judges **how quiet the surroundings are**, not how loud the event is, because speech consonants are short pulses too and loudness alone would clip dialogue. Only that spot is touched (2 ms fade at each end); length is preserved.",
    },
    "H3RelayAudioSeam.declick_quiet_dbfs": {
        zh: "组 4。背景必须低于这个电平（dBFS）才动手。默认 −50。实测孤立瞬态处背景 −56 ⇒ 命中；语音辅音处 ≈ −35 ⇒ 不命中（这就是不误伤台词的原因）。调高更激进、调低更保守。",
        en: "Group 4. The background must be below this level (dBFS) to act. Default −50. Measured: −56 at isolated transients (hit) vs ≈ −35 at speech consonants (no hit) — that is why dialogue is not damaged. Higher is more aggressive, lower more conservative.",
    },
    "H3RelayAudioSeam.declick_max_len_ms": {
        zh: "组 4。多宽算「一声」（毫秒）。默认 50。10 ≈ 严格（只治 ≤10ms 极短脉冲）；50 = 宽泛（连 15~30ms 的「嗒/啪」也治，代价是可能削到真实音效）；100+ = 激进，不建议。⚠ 三道闸里 ratio / quiet_dbfs 才是主角，本值只是安全阀。",
        en: "Group 4. How wide still counts as one \"pop\", in ms. Default 50. 10 ≈ strict (only ≤10 ms pulses); 50 = broad (also treats 15–30 ms clicks, at the cost of possibly clipping real effects); 100+ = aggressive, not recommended. ⚠ ratio and quiet_dbfs are the main gates; this is only a safety valve.",
    },
    "H3RelayAudioSeam.cut_head_frames": {
        zh: "组 4。本段会被裁掉多少帧（= 桥的 context_frames）。本节点工作在未裁音频上，而产物是裁后的 ⇒ 裁出来的孤立峰在它眼里不孤立（前面还有上一段语音）。填了它抑制器就会把该处当「新文件头」来判 ⇒ 命中。0 = 不启用该修正。一般由跑批脚本自动填。",
        en: "Group 4. How many frames get trimmed from this segment (= the bridge's context_frames). This node works on untrimmed audio while the output is trimmed, so a post-trim isolated peak does not look isolated here (the previous segment's speech precedes it). Filling it makes the suppressor judge that spot as a \"new file head\", so it hits. 0 = disable the correction. Normally filled automatically by the batch script.",
    },
    // ===== 潜空间分块放大 =====
    "H3RelayLatentUpscale.latent": {
        zh: "本段采样器（或二采）的 latent 输出，原样拉线 —— 打包 AV latent 与普通 latent 都吃。⚠ 续接契约（落盘给下一段的 latent）应仍取放大前的原生态：桥拷的是原生域数据，拷放大后的等于再过一次放大（必漂）。",
        en: "Latent output of this segment's sampler (or the second pass), wired straight through — packed AV and plain latents both work. ⚠ The relay contract (the latent saved for the next segment) should still come from pre-upscale native data: the bridge copies native-domain data, so copying an upscaled latent upscales it twice (guaranteed drift).",
    },
    "H3RelayLatentUpscale.model_name": {
        zh: "用哪个放大模型（宿主注册表里的 list）。",
        en: "Which upscale model to use (from the host's registry list).",
    },
    "H3RelayLatentUpscale.mode": {
        zh: "怎么选：① 按倍数 —— 给个系数（最常用）；② 目标尺寸 —— 直填宽×高（平台规格，如 720×1280）；③ 兆像素 —— 按预算定档（1.0 / 2.0 / 4.0 MP）。三种都由下面**对应那一行**参数决定，其余忽略。",
        en: "How to choose: ① multiplier — give a factor (most common); ② target dimensions — type width × height (platform specs, e.g. 720×1280); ③ megapixels — pick a budget tier (1.0 / 2.0 / 4.0 MP). Each mode is driven by its **matching** parameter below; the others are ignored.",
    },
    "H3RelayLatentUpscale.scale": {
        zh: "【按倍数模式】放大系数，1.0 = 不变。⚠ 本节点只放大不缩小（<1 会由上游报错）。",
        en: "[Multiplier mode] Upscale factor; 1.0 = unchanged. ⚠ This node only upscales (values < 1 error out upstream).",
    },
    "H3RelayLatentUpscale.width": {
        zh: "【目标尺寸模式】目标像素宽（自动收边到 align 的倍数）。",
        en: "[Target dimensions mode] Target width in pixels (trimmed to a multiple of align).",
    },
    "H3RelayLatentUpscale.height": {
        zh: "【目标尺寸模式】目标像素高（自动收边到 align 的倍数）。",
        en: "[Target dimensions mode] Target height in pixels (trimmed to a multiple of align).",
    },
    "H3RelayLatentUpscale.megapixels": {
        zh: "【兆像素模式】目标总像素（MP）。12GB 卡建议 ≤1.5；显存吃紧就调小。",
        en: "[Megapixels mode] Target total pixels (MP). On a 12 GB card keep it ≤ 1.5; lower it if VRAM is tight.",
    },
    "H3RelayLatentUpscale.chunks": {
        zh: "显存旋钮。沿时间维分几块跑。1 = 整段一次过（最快，最吃显存）。🔴 显存不足才加大它 —— chunks>1 会改画面（切块 = 换跨块时间上下文，overlap 只能缓解不能抵消）。上限受重叠约束（每块至少 2×overlap+1 帧），超了直接报错不偷改。",
        en: "VRAM knob. How many chunks to split the time axis into. 1 = whole segment in one go (fastest, hungriest). 🔴 Raise it only when VRAM runs short — chunks > 1 changes the picture (splitting swaps the cross-chunk temporal context; overlap mitigates but does not cancel it). The ceiling is bounded by overlap (each chunk needs ≥ 2×overlap+1 frames); exceeding it errors out rather than silently adjusting.",
    },
    "H3RelayLatentUpscale.overlap": {
        zh: "块间重叠。两侧各留几帧做渐变混合。默认 5 = 上游模型的 3D 时间核（temporal_kernel）。分块（chunks>1）时才起作用；0 = 硬切不混合（只在你自己确认无接缝问题时用）。",
        en: "Inter-chunk overlap. How many frames on each side blend gradually. Default 5 = the upstream model's 3D temporal kernel. Only matters when chunking (chunks > 1); 0 = hard cut with no blending (use only when you have verified there is no seam).",
    },
    "H3RelayLatentUpscale.align": {
        zh: "对齐网格。输出宽高收边到此的倍数。32 是 H3 放大模型的硬要求，别改小。",
        en: "Alignment grid. Output width/height are trimmed to a multiple of this. 32 is a hard requirement of the H3 upscale model — do not lower it.",
    },
    "H3RelayLatentUpscale.precision": {
        zh: "精度。bf16/fp16 省显存；fp32 最稳但慢。与权重自身精度一致最保险。",
        en: "Precision. bf16/fp16 save VRAM; fp32 is the most stable but slow. Matching the weights' own precision is safest.",
    },
    "H3RelayLatentUpscale.device": {
        zh: "算在哪。cpu 档只用于排查，实际慢得多。",
        en: "Where to compute. The cpu option is for troubleshooting only — far slower.",
    },
    "H3RelayLatentUpscale.force_unload": {
        zh: "跑完卸载。把放大模型退回 CPU 腾显存。⚠ 分多块时开启会反复装卸（更慢），所以默认关；单块且后面还要接采样器时才开。",
        en: "Unload after run. Returns the upscale model to CPU to free VRAM. ⚠ With multiple chunks this reloads repeatedly (slower), hence off by default; enable it for a single chunk when a sampler follows.",
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
    return e && e.zh && e.en ? e : null;
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
    const L = normLang(lang);
    const want = e[L];
    return want == null ? null : String(want);
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
                if (w.tooltip !== tip) { w.tooltip = tip; out.tips += 1; }
                w.__h3I18nWantTip = tip;      // 同上：兜底的比对基准
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
