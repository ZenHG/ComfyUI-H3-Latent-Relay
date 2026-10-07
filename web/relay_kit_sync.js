// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
// 第三方出处与许可见 THIRD-PARTY-NOTICES.md
//
// H3 Latent Relay · **跨节点 `run_id` 同步**的纯函数（前端与离线单测共用同一份）
// 不依赖 ComfyUI 运行时 ⇒ 离线单测（node）直接跑这一份，浏览器里也跑这一份。
//
// ── 为什么需要它 ────────────────────────────────────────────────────────────
// `run_id` 是「这部片子叫什么」，它决定段文件落在 `output/relay_kit/<run_id>/`。
// 一张图里有 **5 类节点**各存一份 `run_id`（见 `RUN_ID_TYPES`），它们**必须一字不差**：
// 不一致 ⇒ 桥去找 `output/relay_kit/<这个名>/stage_XXXXX` 时**找不到落盘节点写的那个目录**
// ⇒ 报错（好情况）或读到上一轮的旧段（坏情况）。
//
// 0.6.18 之前全靠 tooltip 写一句「⚠ 必须和 XX 一字不差」——**改一处要手动改五处**。
// 这与 `stage_index` 的老毛病同构，而后者 2026-09-30 已经用「表驱动 + 一处推进」
// （`STAGE_TYPES` / `setStageAll`）治过一次，还当场抓到「漏了音频缝 ⇒ 成片音画错段」。
// ⇒ run_id 复用同一套办法：**表驱动 + 一处改动广播到全组**。
//
// ── 三条设计决定（都是为了避免"静默做错事"）────────────────────────────────
// 1. **空值不广播**：用户把一个格子清空 ≠ 想把全组清空。空值只提示、不扩散。
//    （否则在某个格子上误删一个字符就会把整部片子的目录名清掉。）
// 2. **冲突不猜**：同组里出现两个不同的非空 run_id 时**不自动挑一个**，
//    而是把清单交给人 —— 自动挑错的那次会让段文件落进错的目录，比报错难查得多。
//    这正是 `resolveRunId` 只有 ok / empty / conflict 三态、没有"选一个"的原因。
// 3. **同步范围 = 同一个分组框**（与 `stage_index` 同一套 `nodesInSameGroup` 语义）：
//    一张图里放两部片子（两组）是正常用法，跨组同步会把另一部片子改掉。
//
// 挂钩与提示在 `relay_kit_chain.js`；本模块**只做判断**，不碰画布、不碰 DOM。

/**
 * 带 `run_id` widget 的节点类型 —— **单一真相源**。
 *
 * 对账口径：`nodes.py` 里声明了 `run_id` 的一共**六处** —— Chain（`H3RelayChain`:1248）、
 * 桥（`H3RelayCopyBridge`:1472）、落盘（`H3RelayLatentSave`:545）、读上段 latent（`H3RelayLatentLoad`:592）、
 * 裁重叠（`H3RelayTrimAV`:840）、音频缝（`H3RelayAudioSeam`:1970）。
 * **漏掉任何一类，那个节点就会一直用别的目录名**。
 * `tests/test_prompt_dispatch.mjs` 的第 8 组会**扫 `nodes.py` 反查**这张表全不全（静态机检）。
 *
 * 🔴 为什么必须收进表里 + 为什么必须有那道机检：这是 `STAGE_TYPES` 踩过的同一个坑 ——
 *   手写清单迟早漏一类，而漏掉的那类是**静默失效**（节点照跑，只是名字不一样）。
 *   本表**第一次写就漏了 `H3RelayAudioSeam`**（当时按 `grep run_id | head -40` 数的手工清单，
 *   输出被截断 ⇒ 少看了一处），是 8.12 那条机检当场抓出来的 —— 这行注释就是那次的结果。
 */
export const RUN_ID_TYPES = [
    { type: "H3RelayChain", label: "连跑" },
    { type: "H3RelayCopyBridge", label: "桥" },
    { type: "H3RelayLatentSave", label: "落盘" },
    { type: "H3RelayLatentLoad", label: "读上段 latent" },
    { type: "H3RelayTrimAV", label: "裁重叠" },
    { type: "H3RelayAudioSeam", label: "音频缝" },
];

const RUN_ID_TYPE_SET = new Set(RUN_ID_TYPES.map((s) => s.type));

/** 这个节点类型是否带 `run_id`。 */
export function isRunIdType(type) {
    return RUN_ID_TYPE_SET.has(type);
}

/** 表里那一项（取 label 用）；不在表里返回 `undefined`。 */
export function runIdSpec(type) {
    return RUN_ID_TYPES.find((s) => s.type === type);
}

/** 节点快照里的 `run_id`（容忍 `undefined` / 数字 / 前后空格）。 */
function rid(m) {
    return String(m?.runId ?? "").trim();
}

/**
 * 这一格的 `run_id` 是否**被连线接管**。
 *
 * 🔴 为什么必须单独标出来（2026-10-06 修，实图 `Cobijada-官方采样-一采-全流程PREVIEW`）：
 *   `run_id` 可以被**转成输入并连线** —— 本包文档 §`run_id` 同步只写「六个格子改一处同步」，
 *   没写这种接法，于是有一张实图把**六类节点的 run_id 全接到同一个 `PrimitiveString`**。
 *   那是**比广播更干净**的做法（一处改、六处动），却会因为
 *   ① 快照读本机残留值 ⇒ 六格读成六个不同值 ⇒ **误判冲突、拦住连跑**；
 *   ② 广播把值写进本机格子 ⇒ **写了也不生效**（生效的是连线值）⇒ 假同步。
 *   ⇒ 有连线时一律**沿 link 取上游**；本机格子是"死值"，既不参与裁决、也不许被广播写。
 */
function linked(m) {
    return m?.linked === true;
}

/** 可以被广播写入的成员（**有连线的排除掉** —— 写它的 widget 不生效）。 */
function writable(list) {
    return (Array.isArray(list) ? list : []).filter((m) => m && m.id != null && !linked(m));
}

/**
 * 裁决同组 `run_id` 是否一致。**不猜**。
 *
 * @param members `[{id, type, runId}]` —— 同组内带 `run_id` 的节点快照
 * @returns `{state, value?, values, ids?, empty, byValue}`
 *   · `state: "ok"`       —— 非空值**只有一个**；`value` = 它，`ids` = 已有该值的节点 id，
 *                            `empty` = 还是空的节点 id（**这些格要补齐**）
 *   · `state: "empty"`    —— 全组都空（后端 `_stage_path` 会直接 raise「run_id 不能为空」）
 *   · `state: "conflict"` —— 有 ≥2 个不同的非空值；`values` = 各值，`byValue` = 值 → 节点 id
 *
 * ⚠️ `ids` / `empty` / `byValue` 里报出去的 id 一律 `String()` 归一（与 `planRunIdSync` 同一规矩）：
 *    调用方拿它们去 `nodeById()` 里比，混着 `"961"` 与 `902` 两种写法就会静默找不到节点
 *    （2026-09-28 状态机栽在同一个坑上）。**别只归一其中一处** —— 同一个清单里两种写法最难查。
 */
export function resolveRunId(members) {
    const list = (Array.isArray(members) ? members : []).filter((m) => m && m.id != null);
    const byValue = new Map();
    const empty = [];
    for (const m of list) {
        const id = String(m.id);
        const v = rid(m);
        if (!v) {
            empty.push(id);
            continue;
        }
        if (!byValue.has(v)) byValue.set(v, []);
        byValue.get(v).push(id);
    }
    const values = [...byValue.keys()];
    if (values.length === 0) return { state: "empty", value: "", values, ids: [], empty, byValue };
    if (values.length === 1) {
        return { state: "ok", value: values[0], values,
                 ids: byValue.get(values[0]).slice(), empty, byValue };
    }
    return { state: "conflict", value: "", values, ids: [], empty, byValue };
}

/**
 * 规划一次广播：把 `value` 写到**同组里还没有这个值的**节点上。
 *
 * @param members  `[{id, type, runId, linked?}]`
 *                 `linked: true` = 这一格被连线接管 ⇒ **跳过**（见 `linked()` 的说明：
 *                 写它的 widget 不生效，写了就是"假同步"，比不做更坏）
 * @param originId 发起改动／提供权威值的节点 id（跳过它 —— 它已经是这个值了）
 * @param value    要广播的值
 * @returns `{targets, value, reason, skipped}`；`reason` = `"empty"`（空值不扩散）/ `"sync"` /
 *          `"none"`（已全一致）/ `"linked"`（目标全是连线接管的、无处可写）；
 *          `skipped` = 因连线被跳过的节点 id（给提示用，**不静默**）
 *
 * ⚠️ id 一律用 `String()` 归一后再比：新版前端 `node.id` 是**字符串**（`"961"`），
 *    直接 `===` 比数字会恒 false ⇒ 该同步的没同步（2026-09-28 状态机上踩过同款）。
 */
export function planRunIdSync(members, originId, value) {
    const v = String(value ?? "").trim();
    if (!v) return { targets: [], value: "", reason: "empty", skipped: [] };
    const origin = originId == null ? null : String(originId);
    const rest = (Array.isArray(members) ? members : [])
        .filter((m) => m && m.id != null && String(m.id) !== origin);
    // ⚠️ 报出去的 id 一律 `String()` 归一（与 `targets` 同一条规矩）——
    //    否则同一份清单里会混着 `"961"` 与 `902` 两种写法，调用方拿去 `nodeById` 对比时
    //    再翻一次车（2026-09-28 状态机上就是栽在"数字 id vs 字符串 id"上）。
    const skipped = rest.filter((m) => linked(m) && rid(m) !== v).map((m) => String(m.id));
    const targets = writable(rest).filter((m) => rid(m) !== v).map((m) => String(m.id));
    if (!targets.length && skipped.length) {
        return { targets, value: v, reason: "linked", skipped };
    }
    return { targets, value: v, reason: targets.length ? "sync" : "none", skipped };
}

/**
 * 把 `conflict` 的裁决写成人话（给 status 格用）。
 *
 * 为什么要单独一个函数：冲突提示是本模块**唯一的"拒绝自动处理"出口**，
 * 它的措辞决定了用户能不能一眼改对 —— 必须**点名哪个节点是哪个值**，
 * 否则用户只知道"不一致"，还得自己逐个格子去对。
 *
 * @param verdict `resolveRunId()` 的返回值
 * @param nameOf  `(id) => "显示名"`；调用方给（前端有 `title` / `type` 可挑）
 * @param linkedIds 被连线接管、**改不动**的节点 id（可选）。
 *        为什么要给它们单独一句话：这些格子的 `run_id` 由**上游节点**决定，
 *        用户在自己的格子里怎么删怎么填都不生效 ⇒ 不说清楚就会一直在错的格子上改。
 */
export function describeRunIdConflict(verdict, nameOf = (id) => `#${id}`, linkedIds = []) {
    const linkedSet = new Set((linkedIds ?? []).map((x) => String(x)));
    const mark = (id) => (linkedSet.has(String(id)) ? `${nameOf(id)}（连线接管）` : nameOf(id));
    const lines = (verdict?.values ?? []).map((v) => {
        const ids = (verdict.byValue.get(v) ?? []).map(mark).join("、");
        return `  · 「${v}」← ${ids}`;
    });
    let msg = "⚠ 同组的 `run_id` 不一致（有 " + lines.length + " 个不同的名字）：\n"
        + lines.join("\n")
        + "\n  ⇒ 段文件会落到不同目录、桥找不到文件。请把它们改成**同一个名字**后重试。";
    if (linkedSet.size) {
        msg += "\n  · 标了**（连线接管）**的：它的值来自上游那个节点，"
            + "改它自己的格子没用 ⇒ 请改**上游**，或把其余格子的值填成和上游一致。";
    }
    return msg;
}

