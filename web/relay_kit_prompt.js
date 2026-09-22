// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-H3-Relay-Kit contributors
// 第三方出处与许可见 THIRD-PARTY-NOTICES.md
//
// H3 Relay Kit · Chain 词分发的**纯函数**（0.6.7）
// 不依赖 ComfyUI 运行时 ⇒ 离线单测（node）直接跑这一份，浏览器里也跑这一份。
//
// 口径（与 nodes.py 的 tooltip 一字对应）：
//   · `prompts` 留空 = 老行为，**一个字都不动**；
//   · 块数不够要跑的段数 ⇒ 调用方**不排队**并报错（绝不静默复用上一块词）；
//   · 自动探测**找到多个候选就报错**（让人工指定），不猜。

/** 分块分隔行：单独一行、三个及以上短横线。 */
const SEPARATOR = /^\s*-{3,}\s*$/;

/** 把多行文本按 `---` 分块。返回**已去掉空块**的词数组；空文本返回 `[]`。 */
export function splitPromptBlocks(text) {
    if (typeof text !== "string" || !text.trim()) return [];
    const blocks = [];
    let cur = [];
    for (const line of text.split(/\r?\n/)) {
        if (SEPARATOR.test(line)) {
            blocks.push(cur.join("\n"));
            cur = [];
        } else {
            cur.push(line);
        }
    }
    blocks.push(cur.join("\n"));
    return blocks.map((b) => b.trim()).filter((b) => b.length > 0);
}

/** 值看起来像 JSON 对象（`h3_data` 那种）。 */
function looksLikeJson(v) {
    const s = String(v ?? "").trim();
    return s.startsWith("{") && s.endsWith("}");
}

/** 节点是否是"活的"（没被 bypass/mute）。 */
function isLive(n) {
    return !!n && (n.mode === undefined || n.mode === 0);
}

/**
 * 在画布节点列表里找出所有**词格**候选。
 * 返回 `[{node, field, kind, priority, type}]`，priority 越小越优先：
 *   0 = `h3_data`（第三方出词节点，JSON 字符串）
 *   1 = 官方 H3 出词节点的 `prompt`
 *   2 = 其它节点的 `prompt`
 */
export function detectPromptTargets(nodes) {
    const out = [];
    for (const n of nodes || []) {
        if (!isLive(n)) continue;
        for (const w of n.widgets || []) {
            if (!w || typeof w.value !== "string") continue;
            const name = String(w.name ?? "");
            const type = String(n.type ?? "");
            if (name === "h3_data" && looksLikeJson(w.value)) {
                out.push({ node: n, field: name, kind: "json", priority: 0, type });
            } else if (name === "prompt") {
                out.push({ node: n, field: name, kind: "text",
                           priority: type.startsWith("MiniMaxH3") ? 1 : 2, type });
            }
        }
    }
    return out.sort((a, b) => a.priority - b.priority);
}

/** 节点上按优先级挑一个词格（给 `prompt_target` 只写了节点 id 时用）。 */
function targetInNode(node) {
    const cands = detectPromptTargets([node]);
    return cands.length ? cands[0] : null;
}

/**
 * 解析 `prompt_target` 写法。
 *   `""`            → 自动探测
 *   `"683"`         → 该节点上优先级最高的词格
 *   `"683.h3_data"` → 点名到字段
 * 返回 `{ok, target, message}`；`ok=false` 时 `message` 是给用户看的处置说明。
 */
export function resolveTarget(nodes, spec) {
    const list = (nodes || []).filter(isLive);
    const s = String(spec ?? "").trim();
    if (!s) {
        const cands = detectPromptTargets(list);
        if (!cands.length) {
            return { ok: false, target: null,
                     message: "没有找到能写词的格子（既没有 h3_data，也没有 prompt）⇒ 请在 prompt_target 里填出词节点的 id。" };
        }
        const top = cands.filter((c) => c.priority === cands[0].priority);
        if (top.length > 1) {
            const names = top.map((c) => `#${c.node.id} ${c.type}.${c.field}`).join("、");
            return { ok: false, target: null,
                     message: `自动探测到 ${top.length} 个候选（${names}）⇒ 请把要写的那个 id 填进 prompt_target。` };
        }
        return { ok: true, target: cands[0], message: "" };
    }
    const dot = s.indexOf(".");
    const idPart = dot < 0 ? s : s.slice(0, dot);
    const fieldPart = dot < 0 ? "" : s.slice(dot + 1);
    const node = list.find((n) => String(n.id) === idPart);
    if (!node) {
        return { ok: false, target: null,
                 message: `prompt_target 写的节点 ${idPart} 不在这张图上（或已被 bypass）。` };
    }
    if (!fieldPart) {
        const t = targetInNode(node);
        return t ? { ok: true, target: t, message: "" }
                 : { ok: false, target: null, message: `节点 ${idPart}（${node.type}）上没有可写的词格。` };
    }
    const w = (node.widgets || []).find((x) => String(x.name) === fieldPart);
    if (!w || typeof w.value !== "string") {
        return { ok: false, target: null,
                 message: `节点 ${idPart} 上没有名为 ${fieldPart} 的文本格。` };
    }
    return { ok: true,
             target: { node, field: fieldPart, kind: looksLikeJson(w.value) ? "json" : "text",
                       priority: 0, type: String(node.type ?? "") },
             message: "" };
}

/**
 * 把一段词写进目标格。`h3_data` 类是 JSON 字符串 ⇒ **解析 → 只改 prompt 字段 → 重序列化**
 * （其余字段原样保留；JSON 解析失败就报错，绝不把整坨 JSON 覆盖掉）。
 * 返回 `{ok, message}`。
 */
export function writePrompt(target, text) {
    const w = (target?.node?.widgets || []).find((x) => String(x.name) === target.field);
    if (!w) return { ok: false, message: `找不到格 ${target?.field}（节点可能已被删/改）。` };
    const where = `#${target.node.id} ${target.type}.${target.field}`;
    if (target.kind === "json") {
        let obj;
        try {
            obj = JSON.parse(String(w.value));
        } catch (e) {
            return { ok: false, message: `${where} 不是合法 JSON（${e.message}）⇒ 请手改一格，或把 prompt_target 指到别处。` };
        }
        if (!obj || typeof obj !== "object" || Array.isArray(obj)) {
            return { ok: false, message: `${where} 不是 JSON 对象 ⇒ 不肯覆盖，请手改。` };
        }
        obj.prompt = String(text);
        w.value = JSON.stringify(obj);
    } else {
        w.value = String(text);
    }
    target.node.setDirtyCanvas?.(true, true);
    return { ok: true, message: `第 ${target.stage ?? "?"} 段词 → ${where}` };
}

/**
 * 按**段号**收集本轮的 prompt_id（`stageIds[k]` = 第 k 段那一次的 id）。
 *
 * 为什么不是"来一个 push 一个"：同一段重跑（不满意再点一次 ▶）会得到两个 id，
 * push 会把重跑的段算成**两段** ⇒ 拼出一条带重复片段的成片。按段号存则后一次覆盖前一次。
 * 返回 `{ids, holes}`：`ids` 按段号升序；`holes` = 没有记录的段号（1 起算，跳段/只跑了后段时出现）。
 */
export function collectStageIds(stageIds) {
    const ids = [];
    const holes = [];
    const arr = Array.isArray(stageIds) ? stageIds : [];
    for (let k = 0; k < arr.length; k += 1) {
        if (arr[k]) ids.push(arr[k]);
        else holes.push(k + 1);
    }
    return { ids, holes };
}

/** 给状态栏用的一行目标描述。 */
export function describeTarget(target) {
    if (!target) return "（无）";
    return `#${target.node.id} ${target.type}.${target.field}`;
}
