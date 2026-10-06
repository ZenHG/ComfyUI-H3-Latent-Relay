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
 * 把语言应用到节点：标题 + 参数标签 + 端口标签 + 枚举显示。
 * @returns {{title:boolean, widgets:number, slots:number}} 实际改了什么（便于测试与调试）
 */
export function applyLang(node, lang) {
    const out = { title: false, widgets: 0, slots: 0 };
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
        }
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
