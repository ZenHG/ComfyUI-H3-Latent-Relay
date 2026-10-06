// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
// 第三方出处与许可见 THIRD-PARTY-NOTICES.md
//
// H3 Latent Relay · 画布侧中英切换 · **挂钩层**（0.6.28）
//
// 判据与词表全在 `relay_kit_i18n.js`（纯函数、零 DOM、可 node 单测）；本文件只做四件事：
//   ① 语言桥：`window.H3RELAY_LANG` 做成 accessor（写 localStorage + 广播事件）
//   ② 给每个本包节点挂一个「中/EN」按钮（`addDOMWidget`，**serialize:false**）
//   ③ 在会改变显示的时机重刷：节点创建 / 图变化 / 语言切换 / 工作流载入
//   ④ 🔴 任何异常都不许掀画布（逐处 try/catch，失败静默降级）
//
// 🔴 UI-only 能力：**脚本提交 JSON 的用户拿不到**（`/prompt` 直接执行后端，不经过画布）。
//    这与本包另几个前端能力（🧩 拼接按钮、Chain 面板、run_id 一处改全组）同性质，
//    README 已列明。
//
// 🔴 为什么不与界面汉化插件（Global Translation 等）共用开关：那些插件按**DOM 文本**翻，
//    本模块改的是 `label`/`title` **数据**；两边都动会互相覆盖。本模块**只管本包自己的节点**，
//    且默认**跟随宿主语言**（界面是英文就别把节点翻成中文）⇒ 天然不打架。

import { app } from "../../scripts/app.js";
import {
    H3_NODES, LANG_KEY, LANG_EVENT, applyLang, defaultLang, normLang, ownNodes,
} from "./relay_kit_i18n.js";

// ---- ① 语言桥（幂等）----
let _lang = null;                       // null = 还没读过（懒读，避免过早碰 localStorage）

function readHostLang() {
    // ① 官方前端把**界面语言**写在 `<html lang>` 上。
    // 🔴 2026-10-06 真机实测（ComfyUI 0.39 前端）：**`app.uiSettings` 已经不存在了**
    //    （`window.app` 上没有它、`Comfy.Locale` 在 localStorage 里也没有）⇒ 只读旧接口的话
    //    永远拿不到界面语言，会掉到 `navigator.language`（本机是 zh-CN，界面却是英文）
    //    ⇒ 出现"界面英文 + 节点中文"，正是本功能要避免的情况。`<html lang>` 是实测有效的那个。
    try {
        const l = document && document.documentElement && document.documentElement.lang;
        if (l) return String(l);
    } catch (e) { /* 无 document（不该发生）*/ }
    // ② 旧前端：官方设置项 `Comfy.Locale`（保留兼容，不是当前路径）
    try {
        const s = app && app.uiSettings && app.uiSettings.get
            ? app.uiSettings.get("Comfy.Locale") : "";
        if (s) return String(s);
    } catch (e) { /* 取不到就按下面的兜底 */ }
    return "";
}

function readStored() {
    try { return localStorage.getItem(LANG_KEY) || ""; } catch (e) { return ""; }
}

export function currentLang() {
    if (_lang == null) _lang = normLang(readStored() || defaultLang(readHostLang));
    return _lang;
}

export function setLang(v) {
    const L = normLang(v);
    _lang = L;
    try { localStorage.setItem(LANG_KEY, L); } catch (e) { /* 隐私模式 */ }
    try { window.dispatchEvent(new CustomEvent(LANG_EVENT, { detail: { lang: L } })); } catch (e) { /* 老浏览器 */ }
    return L;
}

export function toggleLang() {
    return setLang(currentLang() === "zh" ? "en" : "zh");
}

// ---- ② 节点按钮 ----
const _BTNS = new Set();

// 🔴 按钮文字走 **CSS 伪元素**，不用 `textContent`。
//   理由（2026-10-06 真机实测）：本机装了 Global Translation，它按 **DOM textNode** 翻译界面，
//   把按钮的 "EN" 翻成了「英语」—— 连 `translate="no"` / `notranslate` 都不认。
//   CSS `content` 不是 textNode ⇒ 翻译插件碰不到它（实测有效）。
//   `textContent` 留空；万一样式注入失败，按钮是空的（可见但无字）—— 比"显示错误的语言"好。
const STYLE_ID = "h3-i18n-btn-style";

function ensureStyle() {
    try {
        if (document.getElementById(STYLE_ID)) return;
        const st = document.createElement("style");
        st.id = STYLE_ID;
        // 「中」写成 \4e2d 转义：样式表本身与界面语言无关，英文界面下也稳定
        st.textContent = ".h3-i18n-btn::before{content:'EN'}"
            + ".h3-i18n-btn.zh::before{content:'\\4e2d'}";
        (document.head || document.documentElement).appendChild(st);
    } catch (e) { /* 注入失败 ⇒ 按钮无字但功能不受影响 */ }
}

function styleBtn(btn, lang) {
    if (!btn) return;
    btn.classList.toggle("zh", normLang(lang) === "zh");
    btn.style.background = lang === "zh" ? "#2d6cdf" : "#c0392b";
}

function makeLangBtn() {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.title = "切换本包节点的中 / EN 标签（H3 Latent Relay · UI-only）";
    // 🔴 `translate="no"`：界面汉化插件（Global Translation 等）会按 DOM 文本翻译界面，
    //   实测它把本按钮的 "EN" 翻成了「英语」⇒ 按钮表意被改坏、还多一次无谓的重绘。
    //   这两行是给翻译插件的**标准跳过标记**（多数实现认 `translate` 属性与 `notranslate` 类名）。
    btn.setAttribute("translate", "no");
    btn.classList.add("notranslate", "h3-i18n-btn");
    ensureStyle();
    btn.style.cssText = "color:#fff;border:1px solid rgba(0,0,0,.35);border-radius:4px;"
        + "width:36px;height:22px;font-size:12px;font-weight:bold;cursor:pointer;"
        + "display:flex;align-items:center;justify-content:center;padding:0;margin:0;"
        + "flex:0 0 auto;user-select:none;transition:filter .15s;";
    btn.onmouseenter = () => (btn.style.filter = "brightness(1.2)");
    btn.onmouseleave = () => (btn.style.filter = "none");
    btn.onclick = (e) => { e.stopPropagation(); try { toggleLang(); } catch (err) { /* 不掀画布 */ } };
    styleBtn(btn, currentLang());
    _BTNS.add(btn);
    return btn;
}

function mountBtn(node) {
    if (!node || node.__h3I18nBar) return;
    const bar = document.createElement("div");
    bar.style.cssText = "display:flex;align-items:center;gap:6px;padding:3px 4px;";
    const btn = makeLangBtn();
    bar.appendChild(btn);
    for (const ev of ["pointerdown", "mousedown", "dblclick", "keydown", "click"]) {
        bar.addEventListener(ev, (e) => e.stopPropagation());   // 别让点按钮变成拖节点
    }
    try {
        node.addDOMWidget("h3_i18n_bar", "custom", bar, {
            serialize: false,                                    // 🔴 绝不进工作流 JSON
            hideOnZoom: false,
            getMinHeight: () => 28,
        });
        node.__h3I18nBar = bar;
    } catch (e) {
        // 跨前端版本可能没有 addDOMWidget ⇒ **不掀画布**，功能降级为「标题角标」。
        try {
            const t = document.createElement("span");
            t.textContent = currentLang() === "zh" ? "\u4e2d" : "EN";
            t.style.cssText = "color:#888;font-size:10px;";
            bar.appendChild(t);
        } catch (e2) { /* 算了 */ }
    }
}

function unmountBtn(node) {
    if (node && node.__h3I18nBar) {
        for (const b of Array.from(_BTNS)) {
            if (node.__h3I18nBar.contains(b)) _BTNS.delete(b);
        }
        node.__h3I18nBar = null;
    }
}

// ---- ③ 刷新 ----
function nodesOf() {
    try {
        const raw = app && app.graph && app.graph._nodes;
        if (!raw) return [];
        return Array.from(raw instanceof Map ? raw.values() : raw);
    } catch (e) {
        return [];
    }
}

function repaint() {
    try { app.canvas?.setDirty(true, true); } catch (e) { /* 不掀画布 */ }
    try {
        for (const n of nodesOf()) {
            // 端口标签在新前端走快照渲染 ⇒ 补一个官方事件；老前端没有这个 trigger 就静默跳过。
            try { n.graph?.trigger?.("node:slot-label:changed", { nodeId: n.id, slotType: 1 }); } catch (e) { /* 版本差异 */ }
            try { n.graph?.trigger?.("node:slot-label:changed", { nodeId: n.id, slotType: 2 }); } catch (e) { /* 版本差异 */ }
        }
    } catch (e) { /* 版本差异 */ }
}

/** 全图应用语言。返回改了哪些（调试用；异常一律吞掉）。 */
export function applyLangAll() {
    const L = currentLang();
    const stat = { nodes: 0, widgets: 0, slots: 0, titles: 0 };
    for (const n of ownNodes(nodesOf)) {
        try {
            const r = applyLang(n, L);
            stat.nodes += 1;
            stat.widgets += r.widgets;
            stat.slots += r.slots;
            if (r.title) stat.titles += 1;
        } catch (e) { /* 单个节点失败不影响其它 */ }
    }
    for (const b of _BTNS) styleBtn(b, L);
    repaint();
    return stat;
}

function safeApply() {
    try { return applyLangAll(); } catch (e) { return null; }
}

// 语言一变：所有节点 + 所有按钮一起跟着变
if (typeof window !== "undefined" && window.addEventListener) {
    window.addEventListener(LANG_EVENT, () => safeApply());
}

app.registerExtension({
    name: "H3RelayKit.I18n",

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (!H3_NODES[nodeData && nodeData.name]) return;
        const origCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const r = origCreated ? origCreated.apply(this, arguments) : undefined;
            try { mountBtn(this); } catch (e) { /* 不掀画布 */ }
            try { applyLang(this, currentLang()); } catch (e) { /* 不掀画布 */ }
            return r;
        };
        const origRemoved = nodeType.prototype.onRemoved;
        nodeType.prototype.onRemoved = function () {
            try { unmountBtn(this); } catch (e) { /* 不掀画布 */ }
            return origRemoved ? origRemoved.apply(this, arguments) : undefined;
        };
    },

    async setup() {
        currentLang();                                  // 尽早读一次（锁定默认语言）
        try {
            if (app.graph && typeof app.graph.onGraphChanged === "function") {
                app.graph.onGraphChanged(() => safeApply());
            }
        } catch (e) { /* 图 API 变了 ⇒ 靠下面的多拍重试 */ }
        // 🔴 `setup()` 可能早于工作流加载完成（那时图里一个节点都没有）⇒ 补几拍。
        //    不补 = 打开工作流后节点仍是英文（静默失效），本仓已因此踩过（见 relay_kit_refs_ui.js）。
        safeApply();
        for (const ms of (0, 250, 1000, 2500)) setTimeout(safeApply, ms);
    },

    nodeCreated(node) {
        if (!node || !H3_NODES[node.type]) return;
        // 少数版本此刻 widget 还没建好 ⇒ 下一拍补刷一次（宁可晚一拍，别静默不生效）。
        setTimeout(safeApply, 0);
    },
});

export { currentLang as __h3I18nCurrentLang, applyLangAll as __h3I18nApplyAll };
