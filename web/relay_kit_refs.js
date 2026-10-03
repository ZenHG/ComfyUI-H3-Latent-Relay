// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
// 第三方出处与许可见 THIRD-PARTY-NOTICES.md
//
// H3 Latent Relay · **音频参考额度**的判据与文案（**纯函数**，0.6.22 新增）
//
// ⚠️ 本文件**不许 import `app` / `api`** —— 它是被 `tests/test_audio_ref_budget.mjs`
//    直接 import 的纯模块（本仓约定：纯函数与被测对象同一份源码，见 `relay_kit_prompt.js`）。
//    浏览器侧的挂钩在 `relay_kit_refs_ui.js`。
//
// ── 为什么要有这套东西 ──────────────────────────────────────────────────────
// H3 官方节点 `MiniMaxH3ReferenceToVideo` 的 `ref_audios` 是 **Autogrow，min=0 / max=3**
// （`comfy_extras/nodes_minimax_h3.py`）—— 那 3 个槽是**官方给用户留的**。
// 本包在 `stage_index ≥ 1` 时会**恒定注入 1 个音频参考块**
// （`relay_core.apply_relay` 的 `plan.audio_ref`：接了声锚就是声锚尾窗，没接就是上一段音频尾），
// 走 conditioning 里的 `minimax_refs` 通道，**与用户的 3 个槽并存、不是顶替它们**。
// ⇒ 用户填满 3 个槽 = 模型侧看到 **4 个**音频参考。模型层**不拦**
// （`comfy/ldm/minimax/model.py:368` 是 `for blk in refs: cursor += _ref_t_span(blk)`
// —— 变长累加、不截断、不校验个数），但**超出官方口径、我们未实测** ⇒ 必须**主动显性告知**
// （🔴 只写 report 不够 —— 大量用户不展开节点输出；这条是"我们让用户越界了"，不能靠他自己去翻）。
//
// ── 三条设计决定 ────────────────────────────────────────────────────────────
// 1. **不自动断、不静默降级**：官方 3 个槽被占满是**用户自己的选择**。我们擅自把声锚丢掉
//    = 静默改变音色行为（本仓铁律：不做静默降级）⇒ 只提示、不代替决策。
// 2. **提示挂在节点标题上**（不是 tooltip、不是 console）：标题是画布上 always 可见的位置。
//    用 `node.title` 而不是裸 DOM / `addDOMWidget` —— 后者跨前端版本极易失效。
// 3. **取不到就明说**：宿主节点在、但读不到它的槽位 ⇒ 写「⚠ 额度未知」而不是假装没事。
//    **静默不提示 = 又一次让人以为没超。**

/** 官方 `ref_audios` 的槽数（`MiniMaxH3ReferenceToVideo` 的 Autogrow max）。 */
export const OFFICIAL_AUDIO_REF_MAX = 3;

/** 官方参考节点的类型名（宿主实现类名，改了要跟着改）。 */
export const REF_HOST = "MiniMaxH3ReferenceToVideo";

/**
 * 官方音频槽的输入名正则。
 *
 * 🔴 **必须容忍 Autogrow 的前缀形式**：实例化后真实输入名是
 * `ref_audios.ref_audio_0` / `ref_audios.ref_audio_1` / …（`io.Autogrow.TemplatePrefix(prefix="ref_audio_")`）。
 * 2026-10-04 我第一版写成 `/^ref_audio_\d+$/` ⇒ **永不匹配** ⇒ 计数恒 0
 * ⇒ 警告永远不出现 —— 这正是本功能最怕的**静默失效**（比不写还坏：看着像有保护）。
 * 用「末尾锚定 + 允许 `.` 前缀」两种形式都吃下，同时**不会**误吞
 * `ref_video_audios.ref_video_audio_0`（那串里没有 `ref_audio_` 这个子串）。
 */
export const REF_SLOT_RE = /(^|\.)ref_audio_\d+$/;

/** 提示后缀：所有由本模块追加到标题末尾的片段都以此开头（用于幂等剥离）。 */
export const SUFFIX_MARK = " · ";

/**
 * 标题里「本模块追加的后缀」剥离正则 —— **幂等性的关键**。
 *
 * 🔴 必须覆盖**所有**可能的后缀形态（警告 `⚠`、未知 `⚠`、用满提示 `音频参考 n/3`），
 *    否则刷新两次就把后缀叠成两遍（2026-10-04 第一版只剥 `⚠`/`ℹ`，
 *    「正好用满」那条会被叠起来 —— 标题越来越长）。
 */
const SUFFIX_RE = /\s*·\s*(?:⚠|ℹ|音频参考)[\s\S]*$/;

/** 判一个输入名是不是官方音频参考槽。 */
export function isRefAudioSlot(name) {
    return REF_SLOT_RE.test(String(name || ""));
}

/**
 * 数官方音频槽的占用（**纯函数**，吃节点数组，不碰 `app`）。
 *
 * 判据 = 这类输入 **`link != null`**（接了东西才算占用）。
 * 返回 `{ used, slots, hosts }`：
 *   · `used`  = 已接线的槽数
 *   · `slots` = 图上存在的这类槽位总数（= 0 说明**图里根本没有官方音频槽**）
 *   · `hosts` = 官方参考节点实例数
 * 三者分开返回，是为了让「没有槽」与「有槽但读不到」能区分（见 `refBudgetFromScan`）。
 */
export function countRefAudioSlots(nodes) {
    let used = 0, slots = 0, hosts = 0, hostsRead = 0;
    for (const n of nodes || []) {
        if (!n) continue;
        const isHost = n.type === REF_HOST;
        if (isHost) hosts += 1;
        const ins = n.inputs;
        if (!Array.isArray(ins)) continue;        // 读不到输入表 ⇒ 这个节点不贡献任何判据
        if (isHost) hostsRead += 1;
        for (const inp of ins) {
            if (!isRefAudioSlot(inp && inp.name)) continue;
            slots += 1;
            if (inp.link != null) used += 1;
        }
    }
    return { used, slots, hosts, hostsRead };
}

/**
 * 算额度与文案。
 *
 * @param {number} officialUsed 官方槽占用数
 * @param {number} stageIndex   桥的 `stage_index`（<1 ⇒ 首段 ⇒ 本包不注入音频参考）
 * @returns {{mine:number,total:number,over:boolean,text:string}}
 */
export function audioRefBudget(officialUsed, stageIndex) {
    const used = Math.max(0, Number(officialUsed) || 0);
    // 🔴 本包**恒定占 1 个**（声锚 or 上一段音频尾，二选一，但都是 1 个）
    const mine = (Number(stageIndex) || 0) >= 1 ? 1 : 0;
    const total = used + mine;
    const over = total > OFFICIAL_AUDIO_REF_MAX;
    let text = "";
    if (over) {
        // ⚠️ 文案不能说「拔掉 voice_anchor 就退额度」—— **那是错的**：
        //    不接声锚时本包注入的是**上一段音频尾**（`plan.audio_ref` 的 else 分支），
        //    额度照样占 1 个。要真退回官方额度，只能**自己把参考节点的槽位减下来**。
        text = `⚠ 音频参考 ${total} 个（官方上限 ${OFFICIAL_AUDIO_REF_MAX}）：` +
               `你的 ${used} + 本包 ${mine}。模型不拦，但超出官方口径、我们未实测。` +
               `本包这 ${mine} 个是**续接音频**（声锚 / 上一段尾窗，二选一，没有"完全不用"的开关）` +
               `⇒ 要腾额度，请自行减少参考节点的 ref_audio 槽位。`;
    } else if (total === OFFICIAL_AUDIO_REF_MAX && mine > 0) {
        text = `音频参考 ${total}/${OFFICIAL_AUDIO_REF_MAX}（你的 ${used} + 本包 ${mine}）—— 正好用满。`;
    }
    return { mine, total, over, text };
}

/**
 * 由「扫描结果 + 段号」给额度判定。
 *
 * @returns {?{mine:number,total:number,over:boolean,text:string}} `null` = **未知**
 *
 * 三态（这是本模块最容易写错的地方，单测锁死）：
 *   · 图上**没有**官方音频槽 ⇒ 官方占用 **0**（能确定，不是"未知"）
 *   · 有槽位、也读到了    ⇒ 按已接线条数算
 *   · **宿主节点在、但一个槽位都没读到** ⇒ `null`（未知）—— 输入名/结构跟我们对不上，
 *     这时**不许返回 0**（返回 0 就会静默放过真越界）。
 */
/**
 * 由「扫描结果 + 段号」给额度判定。
 *
 * @returns {?{mine:number,total:number,over:boolean,text:string}} `null` = **未知**
 *
 * 三态（本模块最容易写错的地方，单测锁死）：
 *   · 图上**没有**官方音频槽（也没参考节点）⇒ 官方占用 **0**（能确定，不是"未知"）
 *   · 参考节点在读得到、槽位是 0 个（Autogrow 收起来了）⇒ 官方占用 **0**
 *   · **参考节点在、但读不到它的输入表** ⇒ `null`（未知）——
 *     这时**不许返回 0**（返回 0 就会静默放过真越界）。
 */
export function refBudgetFromScan(scan, stageIndex) {
    if (isScanUnknown(scan)) return null;
    return audioRefBudget(scan.used, stageIndex);
}

/** 剥掉本模块追加过的后缀（幂等 —— 可对同一串反复调用）。 */
export function stripBudgetSuffix(title) {
    return String(title == null ? "" : title).replace(SUFFIX_RE, "");
}

/**
 * 扫描结果是否**未知**。
 *
 * 判据 = **有官方参考节点、但至少一个读不到它的输入表**（`inputs` 不是数组）。
 * 🔴 这里刻意**不**用「slots === 0」当未知：
 *    `ref_audios` 是 Autogrow **`min=0`** ⇒ 用户把槽收成 0 个是**合法状态**，
 *    那时 slots 也是 0，但占用确实就是 0 —— 报「未知」会在每个桥节点上刷假警报。
 *    真该报未知的只有一种：**我们看不懂这个节点的结构**（`inputs` 拿不到），
 *    这时不许返回 0（返回 0 就会静默放过真越界）。
 */
export function isScanUnknown(scan) {
    return !scan || (scan.hosts > 0 && scan.hostsRead < scan.hosts);
}

/** 未知态的后缀文案（宿主在、槽位读不到）。 */
export const UNKNOWN_SUFFIX = SUFFIX_MARK + "⚠ 音频参考额度未知（读不到参考节点的输入）";

/**
 * 纯函数：`标题 + 额度` → **应显示的完整标题**。
 *
 * 🔴 幂等：`budgetTitle(budgetTitle(t, n, s), n, s) === budgetTitle(t, n, s)`。
 *    调用方（画布事件）可能一秒内调很多次 ⇒ 不幂等就会把后缀叠成一长串。
 *
 * @param {string} title       节点当前标题（可能已带旧后缀）
 * @param {number} officialUsed 官方占用；**`-1` 或 `null` = 未知**
 * @param {number} stageIndex  桥的 `stage_index`
 */
export function budgetTitle(title, officialUsed, stageIndex) {
    const base = stripBudgetSuffix(title) || "";
    if (officialUsed == null || Number(officialUsed) < 0) return base + UNKNOWN_SUFFIX;
    const b = audioRefBudget(officialUsed, stageIndex);
    return b.text ? base + SUFFIX_MARK + b.text : base;
}
