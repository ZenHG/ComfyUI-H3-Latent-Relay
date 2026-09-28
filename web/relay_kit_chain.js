// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
// 第三方出处与许可见 THIRD-PARTY-NOTICES.md
// H3 Relay Kit · Chain 前端
// 在 H3RelayChain 节点上提供按钮：Run / Approve / 连跑 / Stop / Reset / 拼成一条。
// 作用：自动推进同一张图里「读上段 latent + 拷贝桥（复合桥）+ 落盘」的 stage_index，
//       免去每段手动改数字。
//
// 0.6.7 加两件事（**都默认关 = 老图行为逐位不变**）：
//   · **词分发**：`prompts` 填了词（`---` 分块）时，跑第 k 段前把第 k 块写进出词节点。
//     块数不够 ⇒ **不排队**并报错。
//   · **拼接成片**：`auto_concat` 开着 ⇒ 连跑结束自动拼；平时也可以点「🧩 拼成一条」。
//     拼接在包内实现（不需要外部 ffmpeg），画面流拷贝无损、音频按段去 priming 对齐。
//
// 0.6.12 的三处修复（前两条是**静默失效**，第三条是**行为纠正**）：
//   · 🔴 **按钮点了完全没反应**（前端 1.53+ 重写了 litegraph）：旧代码读 `group.bounding`，
//     而新版 `LGraphGroup` **没有这个属性**（只有 `boundingRect` getter 与 `getBounding()`），
//     于是 `b[0]` 抛 TypeError —— 它发生在**每个按钮回调的第一行**（findPair），
//     6 个按钮**全部**静默失效、画布上一个字都不显示。现改为跨版本兼容取包围盒，
//     取不到就跳过该分组（不抛错）。
//   · 🔴 **`prompts` 由连线提供时读错源**：`easy positive → Chain.prompts` 是常见接法，
//     此时**生效的是连线值**，而 Chain 那一格可能还留着上一版的旧词。旧代码只读本机
//     `widget.value` ⇒ **静默把旧词写进出词节点**（连跑"看着在跑、词其实没换"）。
//     现在有连线一律沿 link 取上游文本格；取不到就**报错**，绝不静默退回残留值。
//   · 🔴 **第 1 段什么都不用做**：「读上段 latent」在 `stage_index == 0` 时会**自己交一个
//     「空上下文」**，桥识别后自动直通（latent 原样过、裁剪帧数输出 0）。
//     ⚠ **旁路它反而会让整个 prompt 被拒** —— 宿主提交前会把 bypass 的节点"溶解"掉、
//     把它的输入接到下游；这个节点没有 LATENT 输入 ⇒ 桥的 required `context_latent`
//     会从 prompt 里消失 ⇒ `Required input is missing`。⇒ Chain **反向接管**：
//     谁把它旁路了就自动恢复成启用。
//   · 段号推进从「桥 + 落盘」扩到**「读上段 latent」**：它的 `stage_index` 语义是
//     **本段段号**（节点内按 `stage_index - 1` 取文件），老版本不推它 ⇒ 段 ≥ 1 时它恒停在 0。
//     现在**三处同步推进**。
//   · 🔴 **连跑跑完第 1 段就静默停住**（0.6.12 修）：旧代码在 `executing` 事件里读 `detail.node`，
//     而宿主 `api.js` 的派发是 `dispatchCustomEvent("executing", data.display_node || data.node)`
//     —— **detail 本身就是一个 id**，不是 `{node, display_node}` 对象 ⇒ `detail.node` 恒为
//     `undefined` ⇒ 「本轮跑过本组桥/落盘」永远记不上 ⇒ `stepDone()` 一进门就
//     `if (!sawMine) return;` ⇒ **段号永远不推进**（连跑看起来"只跑了第 1 段"）。
//     现在两种形态都认（`executingNodeId()`）。
//   · 🔴 **连跑跑完一段就静默停住，另有三处独立原因**（0.6.13 修，浏览器实测）：
//     ① 新版前端 `app.queuePrompt` 返回 **布尔 `true`**（没有 `prompt_id`）⇒ 取
//        `res?.prompt_id` 得 undefined ⇒ 旧代码 `return id` = null ⇒ 连跑 handler 的
//        `if (ok === null) state.mode = "idle"` 把状态机**静默**打回 idle（无任何提示）。
//     ② **整轮命中缓存**时（图没变、同一轮复跑）宿主**不逐节点发 `executing`**，只发一条
//        `execution_cached {nodes:[…]}` + 几条 `executed` ⇒ 「本轮跑过本组」恒判否。
//     ③ **最根本、也最隐蔽的一个：id 类型错配** —— `pairIds` 里是**字符串**
//        （新版前端 `node.id` 是字符串，实测 `pairIds: ["961","902"]`），比对却写
//        `has(Number(id))` ⇒ **恒 false**。①②是必要条件，但全被这一层挡住。
//     ⇒ 现在：排队成败只看**异常**与 `res === false`；id 一律 `String()` 归一；
//     「跑过本组」的证据源三路（`executing` / `execution_cached.nodes` / `executed`）；
//     收尾信号加了 `awaiting` 闸门（队列提交瞬间宿主会先发一条空 `executing(NULL)`），
//     且 `execution_success` 与 `executing(null)` **只认其一**（免得段号一次跳 2）。
//     自助排查：控制台 `__h3Relay.debug()` 直接看状态机内部。
//   · 所有按钮回调统一包 `guard()`：任何异常都写进 `status` 格 + 控制台，
//     **不再有"点了没反应"这种无声故障**。
//   · 按钮 widget 补 `{ serialize: false }` —— 旧写法会被序列化进 `widgets_values`，
//     让节点凭空多出 6 个 `null` 槽位（旧图槽位解读会因此错位）。
//
// 效能（0.6.13）：
//   · **全局监听只注册一份**（模块级 `CHAINS` 集合）。旧写法在每个 Chain 实例的
//     `onNodeCreated` 里各注册 3 个 `api` 监听 ⇒ 多实例时开销成倍，且**节点被删/复制后
//     监听器不会注销**（内存与回调都留在那里）。现在配 `onRemoved` 注销。
//   · `executing` 事件**每执行一个节点都会触发**：本文件只做 `Set.has(id)` 比对，
//     **不重扫全图**（桥/落盘的 id 集合在连跑开始时算一次，每段结束重建）。
//   · 分组包围盒 `groupBounds()` 提到循环外算一次：新版前端的 `getBounding()` 内部会
//     `syncBoundsFromStore()`，逐节点重算是白花的。
//
// 找节点规则：优先取与本 Chain 节点**同一个分组框**里的目标节点；
// 没有分组就全图找；找到多对则提示先分组。
// 本文件行为对标 H3-Motion-Context 的 Chain 思路，代码为独立实现。

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { splitPromptBlocks, resolveTarget, writePrompt, describeTarget, collectStageIds }
    from "./relay_kit_prompt.js";

/** litegraph 的节点模式：0 = Always（启用），4 = Bypass（旁路）。 */
const MODE_ALWAYS = 0;
const MODE_BYPASS = 4;

// ─────────────────────────── 图访问（跨前端版本兼容） ───────────────────────────

/** 图上的节点表。新版前端 `LGraph` 同时有 `_nodes` 与 `nodes`（getter），旧版只有 `_nodes`。 */
function graphNodes() {
    return app?.graph?._nodes ?? app?.graph?.nodes ?? app?.rootGraph?._nodes ?? [];
}

/** 图上的分组表。同上：新版 `_groups` / `groups` 都在。 */
function graphGroups() {
    return app?.graph?._groups ?? app?.graph?.groups ?? [];
}

/** 按名字取一个 widget（找不到返回 `undefined`）。 */
function findWidget(n, name) {
    return (n?.widgets ?? []).find((x) => x.name === name);
}

function nodeCenter(n) {
    const p = n?.pos ?? [0, 0];
    const s = n?.size ?? n?.renderingSize ?? [0, 0];
    return [
        Number(p[0] ?? 0) + Number(s?.[0] ?? 0) / 2,
        Number(p[1] ?? 0) + Number(s?.[1] ?? 0) / 2,
    ];
}

/**
 * 取分组的包围盒 `[x, y, w, h]`；取不到返回 `null`。
 *
 * 🔴 跨版本兼容（0.6.12）：前端 1.53+ 重写了 litegraph，`LGraphGroup` 上**只有**
 * `boundingRect`(getter) 与 `getBounding()`，**没有 `bounding`**（旧版才有）。
 * 直接读 `g.bounding` 会得到 `undefined`，随后 `b[0]` 抛 TypeError —— 而调用链是
 * `findPair → findMemberNodes → inGroup`，即**每个按钮回调的第一行**，
 * 于是所有按钮都变成"点了没反应"（异常只在控制台）。
 *
 * ⚡ 调用方应**每次分组只调一次**（见 `nodesInSameGroup`）：新版的 `getBounding()`
 * 内部会 `syncBoundsFromStore()`，逐节点重算纯属白花。
 */
function groupBounds(g) {
    let b = null;
    try {
        if (typeof g?.getBounding === "function") b = g.getBounding();
        if (b == null && g?.boundingRect != null) b = g.boundingRect;
        if (b == null) b = g?.bounding;
    } catch (err) {
        console.warn("[H3 Relay Chain] 读分组包围盒失败（已跳过该分组）：", err);
        return null;
    }
    if (!b) return null;
    const x = Number(b[0] ?? b.x);
    const y = Number(b[1] ?? b.y);
    const w = Number(b[2] ?? b.width);
    const h = Number(b[3] ?? b.height);
    return [x, y, w, h].every(Number.isFinite) ? [x, y, w, h] : null;
}

/** 节点中心是否落在包围盒 `b` 内。`b` 由 `groupBounds()` 预先算好（避免重复取值）。 */
function inBounds(n, b) {
    const [cx, cy] = nodeCenter(n);
    return cx >= b[0] && cx <= b[0] + b[2] && cy >= b[1] && cy <= b[1] + b[3];
}

/**
 * 在 `all` 里挑出「与 Chain 同一个分组框」的那些；没有分组命中就原样返回 `all`。
 *
 * ⚡ `groupBounds(g)` 在**循环外**算一次 —— 见 `groupBounds` 的说明。
 */
function nodesInSameGroup(chainNode, all) {
    if (!all.length) return all;
    for (const g of graphGroups()) {
        const b = groupBounds(g);
        if (!b || !inBounds(chainNode, b)) continue;
        const members = all.filter((n) => inBounds(n, b));
        if (members.length) return members;
    }
    return all; // 没有分组兜底：全图
}

/** 按类型取同分组的节点。`liveOnly` = 只要启用中的（默认 true）。 */
function findMemberNodes(chainNode, typeName, { liveOnly = true } = {}) {
    const all = graphNodes().filter(
        (n) => n.type === typeName && (!liveOnly || n.mode === MODE_ALWAYS)
    );
    return nodesInSameGroup(chainNode, all);
}

/**
 * 同分组内的「读上段 latent」节点。
 *
 * 为什么 Chain 要管它（0.6.12）：它的 `stage_index` 语义是**本段段号**
 * （`load()` 内部按 `stage_index - 1` 去读文件，见 nodes.py `H3RelayLatentLoad.load`），
 * 所以它必须和桥 / 落盘**同步推进**。老版本只推桥与落盘 ⇒ 它一直停在 0
 * ⇒ 从第 2 段起每段都报「stage_index=0 是第 1 段，没有上一段可续」。
 * 不过滤 `mode`：被旁路的也要一起管（见 `ensureLoadsEnabled`）。
 */
function findLoads(chainNode) {
    return findMemberNodes(chainNode, "H3RelayLatentLoad", { liveOnly: false });
}

// ─────────────────────────── 状态显示 ───────────────────────────

function setStatus(chainNode, text) {
    const w = findWidget(chainNode, "status");
    if (w) {
        w.value = text;
        chainNode.setDirtyCanvas?.(true, true);
    } else {
        // 后端没声明 status widget 时，提示就只进控制台、界面上看不到。
        // 这里显式 warn 一次，免得"按钮点了没反应"变成无声故障。
        console.warn(
            "[H3 Relay Chain] 节点上没有 status widget（后端 nodes.py 的 H3RelayChain.INPUT_TYPES " +
                "应声明一个可选的 status: STRING）——状态只写进控制台：" + text
        );
    }
    console.info("[H3 Relay Chain]", text);
}

/**
 * 按钮回调整体兜底（0.6.12）：任何异常都**写进 status 格**，而不是只在控制台里无声消失。
 * 这是「点了没反应」这类故障的根治手段 —— 以前一个 TypeError 就能让按钮看起来完全是死的。
 */
function guard(chainNode, label, fn) {
    return async (...args) => {
        try {
            return await fn(...args);
        } catch (err) {
            const msg = `⚠ ${label} 内部出错：${err?.message ?? err}`;
            setStatus(chainNode, msg);
            console.error("[H3 Relay Chain]", msg, err);
        }
    };
}

// ─────────────────────────── 段号 / 配对 ───────────────────────────

function widgetValue(n, name, fallback = "") {
    const w = findWidget(n, name);
    return w ? w.value : fallback;
}

function getStage(n) {
    const w = findWidget(n, "stage_index");
    return w ? Math.max(0, Math.round(w.value ?? 0)) : 0;
}

function setStage(n, v) {
    const w = findWidget(n, "stage_index");
    if (w) w.value = Math.max(0, Math.round(v));
}

/**
 * 确保「读上段 latent」处于**启用**状态。
 *
 * 🔴 为什么第 1 段**不能**旁路它（2026-09-28 实测定案）：
 *   宿主在提交前会把 **bypass 的节点"溶解"掉**、把它的输入直接接到下游。
 *   这个节点没有 LATENT 输入 ⇒ 一旦被旁路，桥的 required `context_latent`
 *   会**从 prompt 里消失** ⇒ 整个 prompt 被
 *   `prompt_outputs_failed_validation / Required input is missing` 拒掉。
 *   （`stage_index == 0` 时该节点**自己会交一个「空上下文」**，桥识别后自动直通、不裁帧。）
 *
 * ⇒ Chain 的职责正好**反过来**：保证它启用；用户手动旁路了就替他恢复。
 * 返回被改动的节点 id 列表（给 status 用）。
 */
function ensureLoadsEnabled(pair) {
    const changed = [];
    for (const n of pair.loads ?? []) {
        if (n.mode !== MODE_ALWAYS) {
            n.mode = MODE_ALWAYS;
            n.setDirtyCanvas?.(true, true);
            changed.push(n.id);
        }
    }
    if (changed.length) app?.graph?.change?.();
    return changed;
}

/** 把段号同步推进到三处（读上段 latent / 桥 / 落盘），并保证读节点启用。 */
function setStageAll(pair, v) {
    setStage(pair.bridge, v);
    setStage(pair.save, v);
    for (const n of pair.loads ?? []) setStage(n, v);
    return ensureLoadsEnabled(pair);
}

function findPair(chainNode) {
    const bridges = findMemberNodes(chainNode, "H3RelayCopyBridge");
    const saves = findMemberNodes(chainNode, "H3RelayLatentSave");
    if (bridges.length !== 1 || saves.length !== 1) {
        // 区分「图里就没有」与「有但被旁路/静音」—— 后者是最常见的误操作（照老文档把桥也旁路了），
        // 提示必须不一样，否则用户会去翻分组，而真正要做的只是按一下 Ctrl+B。
        const allB = graphNodes().filter((n) => n.type === "H3RelayCopyBridge");
        const allS = graphNodes().filter((n) => n.type === "H3RelayLatentSave");
        const muted = (list, label) => {
            const ids = list.filter((n) => n.mode !== MODE_ALWAYS).map((n) => n.id);
            return ids.length ? `${label} #${ids.join("、")}` : "";
        };
        let msg = `⚠ 找到 桥×${bridges.length} / 落盘×${saves.length}，需要**恰好各 1 个且都启用**。`;
        const mutedList = [muted(allB, "桥"), muted(allS, "落盘")].filter(Boolean);
        if (mutedList.length) {
            msg += `\n  · 被旁路/静音的：${mutedList.join(" / ")}` +
                ` ⇒ 选中它们按 Ctrl+B 恢复。（第 1 段**不需要**旁路桥：它会自己直通。）`;
        }
        const missing = [
            allB.length ? "" : "「续接 拷贝桥」",
            allS.length ? "" : "「续接 Latent 存」",
        ].filter(Boolean);
        if (missing.length) {
            msg += `\n  · 图里还缺：${missing.join("、")}` +
                ` ⇒ 请把 Chain、桥、落盘放进**同一个分组框**（右键 → 添加分组）。`;
        }
        setStatus(chainNode, msg);
        return null;
    }
    return { bridge: bridges[0], save: saves[0], loads: findLoads(chainNode) };
}

// ─────────────────────────── 排队 / 词分发 ───────────────────────────

async function queuePrompt(chainNode, state, stage) {
    // 宿主 API 兼容性：`app.queuePrompt` 属于前端内部接口，不同 ComfyUI 版本/嵌入式前端可能有差异。
    //   缺了就别静默——**画布上直接说明**，并指向仍然可用的路径（节点本身与「🧩 拼成一条」不受影响）。
    if (typeof app?.queuePrompt !== "function") {
        setStatus(
            chainNode,
            "⚠ 这个 ComfyUI 前端没有 app.queuePrompt ⇒ ⏩ 连跑 / 自动拼接排队用不了。" +
                "「🧩 拼成一条」（拼已跑过的段）仍可用；脚本用户见 README §7.4 的 CLI/库调用路。"
        );
        return null;
    }
    try {
        const res = await app.queuePrompt(0, 1);
        const id = typeof res === "string" ? res : (res?.prompt_id ?? null);
        // 按**段号**记（不是 push）：同一段重跑时后一次覆盖前一次 ⇒ 拼接拿到的是"每段最新那一次"，
        // 顺序也天然按段号排；push 会把重跑的段算成两段、拼出一条带重复的片。
        if (id && state) state.stageIds[stage] = id;
        setStatus(chainNode, `已排队，采样中…（第 ${stage + 1} 段）`);
        // 🔴 0.6.13 修（2026-09-28 实测：新版前端 app.queuePrompt 返回 **true**，
        //   旧版返回 {prompt_id}，都没有统一形状）：旧代码 `return id` ⇒ boolean 返回时
        //   恒为 null ⇒ 连跑 handler 的 `if (ok === null) state.mode = "idle"` 把状态机
        //   **静默**打回 idle ⇒ 第 1 段跑完就永远停住（status 冻结、无任何报错）。
        //   排队失败的信号是**异常**（下方 catch 已转 ⚠ status）；`res === false` 也算失败。
        //   拿不到 prompt_id 就不记 stageIds ⇒ 拼接自动走「按最近 N 段落盘记录」的兜底路。
        if (res === false) return null;
        // 排队**成功** ⇒ 本组进入「等一轮跑完」态：`stepDone()` 只认置位后的收尾信号，
        // 免得队列提交瞬间宿主先发的空事件（实测 `executing(NULL)` 会紧跟着来）把段号误推。
        if (state) state.awaiting = true;
        return id ?? "queued";
    } catch (err) {
        setStatus(chainNode, "⚠ 排队失败：" + err.message);
        throw err;
    }
}

/**
 * 沿一条 link 找上游节点，从它身上取一个文本格的值。取不到返回 `{ok:false, message}`。
 * 新版前端 `graph.links` 是 Map、旧版是数组/对象，且两代都有 `getLink(id)` ⇒ 三路兜底。
 */
function upstreamText(linkId) {
    const graph = app?.graph;
    const link = graph?.getLink?.(linkId) ?? graph?.links?.get?.(linkId) ?? graph?.links?.[linkId];
    if (!link) return { ok: false, message: `找不到连线 ${linkId}（宿主前端接口可能变了）` };
    const origin = graph?.getNodeById?.(link.origin_id)
        ?? graphNodes().find((n) => String(n.id) === String(link.origin_id));
    if (!origin) return { ok: false, message: `找不到上游节点 #${link.origin_id}` };
    const strs = (origin.widgets ?? []).filter((w) => typeof w?.value === "string");
    let pick = null;
    for (const name of ["positive", "prompt", "text", "string"]) {
        pick = strs.find((w) => String(w.name) === name);
        if (pick) break;
    }
    if (!pick && strs.length === 1) pick = strs[0]; // 上游只有一格文本 ⇒ 无歧义，就用它
    if (!pick) {
        const names = strs.map((w) => String(w.name)).join("、") || "（一个文本格都没有）";
        return { ok: false, message: `上游 #${origin.id}（${origin.type}）上没有可判定的词格（候选：${names}）` };
    }
    return { ok: true, text: String(pick.value ?? ""), from: `#${origin.id} ${origin.type}.${pick.name}` };
}

/**
 * 取**实际会参与执行**的 `prompts` 文本。
 *
 * 🔴 为什么不能只读 `widget.value`（0.6.12 修）：`prompts` 可以**被转成输入并连线**
 * （典型接法：`easy positive` → Chain.prompts）。此时提交给宿主的是**连线值**，
 * 而 `widget.value` 只是那一格自己的残留文本 —— 两者可以**完全不同**。
 * 读错源的后果不是报错，而是**静默把旧词写进出词节点**（连跑"看着在跑、词其实没换"）。
 * ⇒ 有连线时一律沿 link 取上游文本；取不到就**报错**，绝不静默退回本机残留值。
 */
function readPrompts(chainNode) {
    const inp = (chainNode.inputs ?? []).find((i) => i.name === "prompts");
    if (!inp || inp.link == null) {
        return { ok: true, text: String(widgetValue(chainNode, "prompts", "") ?? "") };
    }
    return upstreamText(inp.link);
}

/**
 * 开始第 `stage` 段（0 起算）前把词写进目标节点。
 * 没填 `prompts` ⇒ 直接放行（老行为，一个字都不动）。
 */
function dispatchStage(chainNode, stage) {
    const src = readPrompts(chainNode);
    if (!src.ok) {
        return {
            ok: false,
            message:
                `⚠ prompts 是从上游连线来的，但读不到上游的词：${src.message} ⇒ **没有排队**。\n` +
                `两个办法：① 把词**直接粘进 Chain 的 prompts 格**、断开这条连线（最稳，推荐）；` +
                `② 保持连线，但让上游节点带一个 positive / prompt / text 文本格。`,
        };
    }
    const blocks = splitPromptBlocks(src.text);
    if (!blocks.length) return { ok: true, message: "" };
    if (stage >= blocks.length) {
        return {
            ok: false,
            message:
                `⚠ prompts 只有 ${blocks.length} 块词，第 ${stage + 1} 段没有对应词 ⇒ **没有排队**。` +
                `（要么补齐第 ${stage + 1} 块，要么把 segments 改成 ${blocks.length} 或更小。）`,
        };
    }
    const r = resolveTarget(graphNodes(), widgetValue(chainNode, "prompt_target", ""));
    if (!r.ok) return { ok: false, message: "⚠ " + r.message };
    const w = writePrompt(r.target, blocks[stage]);
    if (!w.ok) return { ok: false, message: "⚠ " + w.message };
    return {
        ok: true,
        message: `第 ${stage + 1}/${blocks.length} 段词 → ${describeTarget(r.target)}`
            + (src.from ? `（词源 ${src.from}）` : ""),
    };
}

/** 状态行里带上词分发结果（没有就什么都不加）。 */
function withMsg(msg) {
    return msg ? `｜ ${msg}` : "";
}

/** 开始第 `stage` 段：先保证读节点启用，再分词写格，最后排队。任一步没过都**不排队**。 */
async function startStage(node, state, stage, label, pair) {
    const restored = ensureLoadsEnabled(pair);
    const d = dispatchStage(node, stage);
    if (!d.ok) {
        setStatus(node, d.message);
        return null;
    }
    const note = restored.length
        ? `｜ 已恢复「读上段 latent」#${restored.join("、")} 为启用（旁路它会导致提交校验失败）`
        : "";
    setStatus(node, `${label}：stage=${stage} 排队中…` + withMsg(d.message) + note);
    return queuePrompt(node, state, stage);
}

/** 拼接成片（走后端 /h3relay/concat；包内实现，不依赖外部 ffmpeg）。 */
async function concatFilm(chainNode, state, auto = false) {
    const pair = findPair(chainNode);
    const runId = pair ? String(widgetValue(pair.bridge, "run_id", "")) : "";
    const seg = Math.round(widgetValue(chainNode, "segments", 0) || 0);
    const outName = String(widgetValue(chainNode, "concat_name", "")).trim();
    const { ids, holes } = collectStageIds(state.stageIds);
    if (!ids.length && seg <= 0) {
        setStatus(
            chainNode,
            "⚠ 拼接：既没有本轮跑过的段记录，segments 也不是正数 ⇒ 不知道该拼哪几段。" +
                "把 segments 填成正数，或用 ▶/⏩ 跑过一轮再来。"
        );
        return;
    }
    setStatus(
        chainNode,
        `🧩 拼接中…（${ids.length ? `本轮 ${ids.length} 段` : `按最近 ${seg} 段落盘记录`}）` +
            `｜音轨 ${String(widgetValue(chainNode, "audio_out", "aac_256k"))}` +
            (holes.length ? `｜⚠ 第 ${holes.join("、")} 段没有本轮记录，可能漏段` : "")
    );
    if (typeof api?.fetchApi !== "function") {
        setStatus(chainNode, "⚠ 这个 ComfyUI 前端没有 api.fetchApi ⇒ 画布内拼接用不了。" +
            "脚本用户请用 tools/concat_segments.py 或直接调 relay_core（README §7.4）。");
        return;
    }
    try {
        const res = await api.fetchApi("/h3relay/concat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                prompt_ids: ids,
                chain_node_id: String(chainNode.id),
                count: seg,
                out_name: outName,
                run_id: runId,
                // 成片档：音轨（aac_256k / aac_192k / pcm_lossless）与重编码 crf。
                // 后端按这两个值决定音轨编码器与码率；默认档与老行为一致（AAC 256k）。
                audio_out: String(widgetValue(chainNode, "audio_out", "aac_256k")),
                video_crf: Math.round(widgetValue(chainNode, "video_crf", 16) ?? 16),
            }),
        });
        const data = await res.json();
        setStatus(chainNode, (data.ok ? "✅ " : "🔴 ") + (data.report || data.error || "拼接失败"));
    } catch (err) {
        setStatus(chainNode, (auto ? "⚠ 自动拼接请求失败：" : "⚠ 拼接请求失败：") + err.message);
    }
}

/**
 * 从 `executing` 事件的 detail 里取出节点 id。
 *
 * 🔴 宿主 `api.js` 的派发是 `dispatchCustomEvent("executing", data.display_node || data.node)`
 * —— 传出来的**就是一个 id**（字符串或数字），**不是 `{node, display_node}` 对象**。
 * 旧代码写的是 `detail.node` ⇒ 恒为 `undefined` ⇒ 「本轮跑过本组节点」永远记不上 ⇒
 * **连跑跑完第 1 段就静默停住**（`stepDone` 里 `if (!sawMine) return;` 直接返回）。
 * 这里两种形态都认，免得跟着前端版本漂。
 */
function executingNodeId(detail) {
    if (detail == null) return null;
    if (typeof detail === "object") return detail.display_node ?? detail.node ?? null;
    return detail;
}

// ─────────────────── 全局事件：所有 Chain 实例共享一份监听 ───────────────────
//
// ⚡ 旧写法在每个实例的 `onNodeCreated` 里各注册 3 个 `api` 监听：
//   · 多实例时开销成倍（`executing` 每个节点都触发，N 个实例 = N 倍回调）；
//   · 节点被删除/复制后**监听器不会注销**，回调与闭包一直留在那里。
// 现在模块级只注册一次，实例通过 `CHAINS` 集合进出（配 `onRemoved` 注销）。

/** 活着的 Chain 实例状态集合。 */
const CHAINS = new Set();
let listenersBound = false;

// 🔴 0.6.13：本前端**是否发 `execution_success`**（一条就够全轮收尾）。
//   发了就只认它 —— 否则 `execution_success` 与 `executing(null)` 会各触发一次
//   `stepDone()` ⇒ 段号一次跳 2、拼接漏段。两者取其一，绝不双认。
let hostSendsSuccess = false;

// 诊断出口（**只读快照**，不参与逻辑）：浏览器控制台敲 `__h3Relay.debug()` 就能看到
// 每个 Chain 组的状态机内部（mode / awaiting / sawMine / pairIds / remaining）。
// 连跑"停住"这类只在真前端才复现的问题，没有这层内部视图只能靠猜——0.6.13 的三处原因
// （detail 形状 / queuePrompt 返回形状 / id 类型）全靠它挖出来。
try {
    window.__h3Relay = {
        debug: () => [...CHAINS].map((s) => ({
            mode: s.mode, awaiting: s.awaiting, sawMine: s.sawMine, remaining: s.remaining,
            pairIds: [...s.pairIds], stageIds: [...(s.stageIds ?? [])],
        })),
    };
} catch { /* 非浏览器环境（测试里 import 本文件）忽略 */ }

function bindGlobalListeners() {
    if (listenersBound) return;
    listenersBound = true;
    api.addEventListener("executing", ({ detail }) => {
        for (const s of CHAINS) s.onExecuting(detail);
    });
    // 全轮收尾的**首选**信号：带 prompt_id，最干净。
    api.addEventListener("execution_success", ({ detail }) => {
        hostSendsSuccess = true;
        for (const s of CHAINS) s.onExecutionSuccess(detail);
    });
    // 🔴 0.6.13 修（2026-09-28 实测）：**整轮全命中缓存**时（图没变、种子固定 ⇒ 复跑同一段），
    //   宿主**不逐节点发 `executing`**，只发一条 `execution_cached {nodes:[…]}` +
    //   几条 `executed`，然后直接 `execution_success` → `executing(null)`。
    //   ⇒ 只靠 `executing` 判定「本轮跑过本组」会恒为 false ⇒ `stepDone()` 静默 return
    //     ⇒ 连跑又冻住（status 停在「已排队，采样中…」）。缓存命中同样是「本组跑了」，要认。
    api.addEventListener("execution_cached", ({ detail }) => {
        for (const s of CHAINS) s.onCached(detail);
    });
    api.addEventListener("executed", ({ detail }) => {
        for (const s of CHAINS) s.onExecuted(detail);
    });
    api.addEventListener("execution_error", () => {
        for (const s of CHAINS) s.onExecutionError();
    });
}

app.registerExtension({
    name: "H3RelayKit.Chain",

    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "H3RelayChain") return;

        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const r = origOnNodeCreated?.apply(this, arguments);
            const node = this;

            // sawMine：本轮执行里是否真的跑过本组桥/落盘。executing(null) 是全局事件，
            // 多个 Chain 组共存时，别的组跑完一轮不能推进本组的段号。
            // stageIds：**按段号**存本轮各段排队拿到的 prompt_id（拼接时按它取回每段落盘的 mp4）。
            // pairIds：本组「桥/落盘」的 id **集合**，连跑开始时算一次就够 ——
            //   executing 事件每执行一个节点都会触发，在里面重算 findPair 等于每节点扫一遍全图。
            const state = {
                mode: "idle", remaining: 0, sawMine: false, awaiting: false,
                stageIds: [], pairIds: new Set(),

                /** 本组"正在等一轮跑完"（排队成功后置位；`stepDone` 消费掉）。 */
                armed() { return this.mode === "chain" && this.awaiting; },

                /** 惰性算一次本组「桥 + 落盘」的 id 集合（下段第一个事件会用新图重建）。 */
                refreshPairIds() {
                    if (this.pairIds.size) return this.pairIds;
                    const pair = findPair(node);
                    // 🔴 0.6.13：id 一律**字符串化**再存。新版前端 `node.id` 是字符串
                    //   （实测 pairIds=["961","902"]），旧写法 `has(Number(id))` 做的是
                    //   数字比对 ⇒ 恒 false ⇒ `sawMine` 永远记不上 ⇒ 连跑跑完第 1 段静默停住。
                    //   （0.6.12 修的 `detail.node`、0.6.13 修的返回形状都对，全被这一层挡住。）
                    this.pairIds = new Set(pair ? [String(pair.bridge.id), String(pair.save.id)] : []);
                    return this.pairIds;
                },

                /**
                 * 记「本轮确实跑过本组」——**三种来源都认**：
                 *   · `executing`（逐节点执行；真跑时用）
                 *   · `execution_cached`（整轮命中缓存时宿主**只发这一条**，里面带节点清单）
                 *   · `executed`（产出事件，兼作兜底）
                 * 🔴 只认 `executing` 是 0.6.13 之前的死路：缓存轮次不逐节点发事件 ⇒
                 *    `sawMine` 恒 false ⇒ 连跑跑完一轮就静默停住。
                 */
                markMine(id) {
                    if (id == null) return;
                    // 比之前先看 `armed()`：**放弃**本轮之前（上一轮残留/别组）的事件的干扰。
                    if (!this.armed()) return;
                    if (this.refreshPairIds().has(String(id))) this.sawMine = true;
                },

                /** 每个节点执行时都会调到这里（由模块级监听分发）。 */
                onExecuting(detail) {
                    // detail 为空 = 本轮队列跑完。宿主若另发 `execution_success`，收尾**只认那条**，
                    // 否则两条都触发 ⇒ 段号一次跳 2。
                    if (detail == null) { if (!hostSendsSuccess) this.stepDone(); return; }
                    if (this.mode !== "chain") return;
                    this.markMine(executingNodeId(detail));
                },

                /** `execution_success` = 本轮真的跑完了（首选收尾信号，见上）。 */
                onExecutionSuccess() { this.stepDone(); },

                /** 整轮命中缓存（宿主不发 per-node executing）时的证据。 */
                onCached(detail) {
                    if (this.mode !== "chain") return;
                    for (const id of detail?.nodes ?? []) this.markMine(id);
                },

                /** 产出事件——兼作兜底（个别前端在缓存轮只发 executed）。 */
                onExecuted(detail) {
                    if (this.mode !== "chain") return;
                    this.markMine(detail?.display_node ?? detail?.node ?? null);
                },

                onExecutionError() {
                    if (this.mode !== "chain") return;
                    this.mode = "idle";
                    this.awaiting = false;
                    setStatus(node, "⚠ 执行出错，连跑已停（stage_index 保持当前值，可直接重跑）。");
                },

                /** 一段跑完：推进段号，决定继续还是收尾。 */
                stepDone() {
                    if (this.mode !== "chain") return;
                    if (!this.awaiting) return;   // 没在等一轮 ⇒ 旧事件/别组的收尾，不理
                    if (!this.sawMine) return;    // 本轮没执行过本组的桥/落盘 → 别的组的收尾，忽略
                    this.awaiting = false;
                    this.sawMine = false;
                    this.pairIds = new Set();  // 下段第一个 executing 事件会重建（图改了也自动跟上）

                    const pair = findPair(node);
                    if (!pair) { this.mode = "idle"; return; }

                    const next = getStage(pair.bridge) + 1;
                    setStageAll(pair, next);

                    const seg = Math.round(widgetValue(node, "segments", 0) || 0);
                    const infinite = seg <= 0;
                    const more = infinite || this.remaining > 1;
                    if (this.remaining > 0) this.remaining -= 1;

                    if (!more) {
                        this.mode = "idle";
                        setStatus(node, `✅ 连跑结束，当前段号 ${next}（已落盘，可直接继续 Approve）。`);
                        if (widgetValue(node, "auto_concat", false)) concatFilm(node, this, true);
                        return;
                    }
                    const d = dispatchStage(node, next);
                    if (!d.ok) {
                        this.mode = "idle";
                        setStatus(node, d.message);
                        return;
                    }
                    setStatus(node, `连跑中：第 ${next + 1} 段排队…（剩余 ${infinite ? "∞" : this.remaining}）`
                        + withMsg(d.message));
                    queuePrompt(node, this, next).catch(() => (this.mode = "idle"));
                },
            };

            CHAINS.add(state);
            bindGlobalListeners();
            // 节点被删/复制时把状态摘出去 —— 否则监听回调会一直留着这个闭包。
            const origOnRemoved = node.onRemoved;
            node.onRemoved = function () {
                CHAINS.delete(state);
                return origOnRemoved?.apply(this, arguments);
            };

            // ⚠️ 一律带 `{ serialize: false }`：按钮不该进 widgets_values（否则旧图凭空多出空槽位）。
            const addBtn = (label, fn) =>
                node.addWidget("button", label, null, guard(node, label, fn), { serialize: false });

            addBtn("▶ Run（按当前段号跑一次）", async () => {
                const pair = findPair(node);
                if (!pair) return;
                state.mode = "idle";
                state.awaiting = false;
                await startStage(node, state, getStage(pair.bridge), "Run", pair);
            });

            addBtn("✔ Approve（段号+1 并跑下一段）", async () => {
                const pair = findPair(node);
                if (!pair) return;
                const next = getStage(pair.bridge) + 1;
                const d = dispatchStage(node, next);
                if (!d.ok) { setStatus(node, d.message); return; }
                setStageAll(pair, next);            // 先确认词能写上，再推进段号
                state.mode = "idle";
                state.awaiting = false;
                setStatus(node, `Approve：段号推进到 ${next}，排队中…` + withMsg(d.message));
                await queuePrompt(node, state, next);
            });

            addBtn("⏩ 连跑（按 segments 自动循环）", async () => {
                const pair = findPair(node);
                if (!pair) return;
                const seg = Math.round(widgetValue(node, "segments", 0) || 0);
                state.mode = "chain";
                state.remaining = seg;
                state.stageIds = [];
                state.pairIds = new Set();
                state.sawMine = false;
                state.awaiting = false;   // 由 queuePrompt 成功后再置位
                setStatus(node, `连跑开始：segments=${seg <= 0 ? "∞" : seg}，首段排队中…`);
                const ok = await startStage(node, state, getStage(pair.bridge), "连跑", pair);
                if (ok === null) state.mode = "idle";   // 词分发没过 ⇒ 别停在 chain 态
            });

            addBtn("⏹ Stop（本轮跑完即停）", () => {
                if (state.mode === "chain") {
                    state.mode = "idle";
                state.awaiting = false;
                    setStatus(node, "已请求停止：当前采样跑完后不再推进。");
                } else {
                    setStatus(node, "当前没有在连跑。");
                }
            });

            addBtn("🧩 拼成一条（把已跑的 N 段拼成成片）", async () => {
                await concatFilm(node, state, false);
            });

            addBtn("↺ Reset（段号归 0，从第 1 段重来）", () => {
                const pair = findPair(node);
                if (!pair) return;
                setStageAll(pair, 0);
                state.stageIds = [];
                setStatus(node, "已归 0：下一段将作为第 1 段（不续接，只落盘）。");
            });

            return r;
        };
    },
});
