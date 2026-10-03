// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
// 第三方出处与许可见 THIRD-PARTY-NOTICES.md
//
// H3 Latent Relay · 音频参考额度的**画布侧挂钩**（0.6.22 新增）
//
// 判据与文案全在 `relay_kit_refs.js`（纯函数、可 node 单测）；本文件只做三件事：
//   ① 把「画布上的节点」喂给纯函数（`app.graph._nodes` 的两种形状都吃）
//   ② 在**会改变占用**的时机重算：连线变化 / 节点增删 / 图切换 / 段号改动
//   ③ 把结果写到桥节点标题（`node.title`，画布上 always 可见）
//
// 🔴 铁律：**本文件的任何异常都不许掀画布**。每一处 `app` 交互都单独 try/catch，
//    失败时静默降级 —— 后端的 report 仍会写权威数字（`relay_core.count_official_audio_refs`）。
//
// 🔴 为什么不只在 `setup()` 里算一次：`setup()` 在打开工作流时可能**早于图加载完成**
//    （那时 `_nodes` 还空）⇒ 只算一次 = 提示永远不出现（静默失效）。
//    所以既挂 `onGraphChanged`，也在**每个节点创建时**重算一次。

import { app } from "../../scripts/app.js";
import {
    REF_HOST, countRefAudioSlots, refBudgetFromScan, isScanUnknown, budgetTitle,
} from "./relay_kit_refs.js";

const BRIDGE = "H3RelayCopyBridge";

/** 图里的节点列表（跨版本：`_nodes` 可能是数组或 Map；取不到就空数组）。 */
function nodesOf() {
    const raw = app && app.graph && app.graph._nodes;
    if (!raw) return [];
    try {
        return Array.from(raw instanceof Map ? raw.values() : raw);
    } catch (e) {
        return [];
    }
}

/** 桥节点的 `stage_index`（取不到按 1 算 —— 那是"会注入"的那一侧，宁可多提醒）。 */
function bridgeStageIndex(node) {
    const ws = (node && node.widgets) || [];
    for (const w of ws) {
        if (w && w.name === "stage_index") return Number(w.value) || 0;
    }
    return 1;
}

/** 重算所有桥节点的标题。返回 `{flagged, unknown, scan}`（供调试读）。 */
function refreshTitles() {
    const nodes = nodesOf();
    const scan = countRefAudioSlots(nodes);
    // 🔴 未知判据只取一个出处（`isScanUnknown`）—— 标题与统计必须同源，
    //    否则会出现「统计说 1 处未知、标题却写成立即用满」这种自相矛盾。
    const unknownScan = isScanUnknown(scan);
    let flagged = 0, unknown = 0, changed = false;
    for (const node of nodes) {
        if (!node || node.type !== BRIDGE) continue;
        const stage = bridgeStageIndex(node);
        const budget = unknownScan ? null : refBudgetFromScan(scan, stage);
        if (budget == null) unknown += 1;
        else if (budget.over) flagged += 1;
        // 未知 ⇒ 传 -1（纯函数据此写「⚠ 额度未知」）；已知 ⇒ 传**扫描到的原始占用数**。
        const want = budgetTitle(node.title || BRIDGE, unknownScan ? -1 : scan.used, stage);
        if (node.title !== want) {
            node.title = want;
            changed = true;
        }
    }
    if (changed) {
        // 标题改了要重绘；不同前端版本入口不同 ⇒ 逐个 try。
        try { app.canvas?.setDirty(true, true); } catch (e) { /* 不掀画布 */ }
    }
    return { flagged, unknown, scan };
}

/** 安全包装：任何异常都不许冒泡到画布。 */
function safeRefresh() {
    try {
        return refreshTitles();
    } catch (e) {
        return null;
    }
}

/** 给参考节点挂「连线变化」钩子（占用变动的**主要**触发点）。 */
function hookRefHost(node) {
    if (!node || node.__h3RefHooked) return;
    const orig = node.onConnectionsChange;
    node.onConnectionsChange = function () {
        const r = (typeof orig === "function") ? orig.apply(this, arguments) : undefined;
        safeRefresh();
        return r;
    };
    node.__h3RefHooked = true;
}

app.registerExtension({
    name: "H3RelayKit.AudioRefBudget",

    /** 老版本没有 setup 签名 ⇒ 用 try 包住，缺了也不报错。 */
    async setup() {
        try {
            if (app.graph && typeof app.graph.onGraphChanged === "function") {
                app.graph.onGraphChanged(() => safeRefresh());
            }
        } catch (e) { /* 图 API 变了 ⇒ 靠 nodeCreated 那条路 */ }
        // 🔴 `setup()` 可能早于「工作流加载完成」（那时 `_nodes` 还空）⇒ 补几拍重算。
        //    不补 = 打开工作流后提示根本不出现（静默失效）。三拍覆盖"本地快 / 远端慢"两档。
        safeRefresh();
        for (const ms of (0, 250, 1000, 2500)) setTimeout(safeRefresh, ms);
    },

    /**
     * 每个节点创建时重算一次：`nodeCreated` 时 widgets 已就位（晚于 `onNodeCreated`）。
     * 参考节点还要挂钩子（连线变化不会触发 `nodeCreated`）。
     */
    nodeCreated(node) {
        if (!node) return;
        if (node.type === REF_HOST) hookRefHost(node);
        if (node.type === REF_HOST || node.type === BRIDGE) {
            safeRefresh();
            // 少数前端版本此刻 widget 还没建好 ⇒ 下一拍重试一次（宁可晚一拍，不要静默不挂钩）。
            setTimeout(safeRefresh, 0);
        }
    },
});

export { refreshTitles as __refreshRelayRefTitles };
