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

// 🔴 **官方设置读取**（2026-10-06 真机定位到正确入口）。
//    入口是 `app.extensionManager.setting`，API 是 `.get(id)` / `.set(id,v)`。
//    真机实测：`setting.get("Comfy.Locale")` 返回 `"zh"` / `"en"`。
//    这是**权威源**——官方下拉框、官方翻译、我们的节点，都该以它为准。
//    ⚠️ 旧文档写的另一个入口「app.点 uiSettings」**实测已不存在**，别照抄旧文档。
export function readOfficialLocale() {
    try {
        const st = app && app.extensionManager && app.extensionManager.setting;
        if (st && typeof st.get === "function") {
            const v = st.get("Comfy.Locale");
            if (v) return String(v);
        }
    } catch (e) { /* 官方接口变了 ⇒ 走下面的兜底，绝不掀画布 */ }
    return "";
}

/** 官方 locale → 变化时通知（无障碍/多标签共用）。取不到入口就静默失效。 */
function watchOfficialLocale(onChange) {
    try {
        const st = app && app.extensionManager && app.extensionManager.setting;
        // Pinia store：$subscribe 是官方 store 的标准订阅口。有的版本叫 subscribe。
        const sub = st && (st.$subscribe || st.subscribe);
        if (typeof sub === "function") {
            sub.call(st, () => { try { onChange(); } catch (e) { /* 回调里自己兜 */ } });
            return true;
        }
    } catch (e) { /* 版本差异 */ }
    return false;
}

function readHostLang() {
    // ① **首选官方设置 `Comfy.Locale`**（权威源；见上）。
    const off = readOfficialLocale();
    if (off) return off;
    // ② `<html lang>` —— 🔴 **只在官方取不到时**才用，且它**可能不可信**：
    //    2026-10-06 真机实测，同一时刻 `setting.get("Comfy.Locale")` = `"zh"` 而
    //    `<html lang>` = `"en"`（官方只把它当作浏览器语种的声明，**不随语言设置更新**）。
    //    ⇒ 只要官方入口可用就**绝不**读它，否则会出现"节点英文、官方界面中文"。
    try {
        const l = document && document.documentElement && document.documentElement.lang;
        if (l) return String(l);
    } catch (e) { /* 无 document（不该发生）*/ }
    return "";
}

function readStored() {
    try { return localStorage.getItem(LANG_KEY) || ""; } catch (e) { return ""; }
}

// 🔴 两层模型（2026-10-06 改为**单向跟随**官方语言）：
//    ① **跟随层** = 官方 `Comfy.Locale`（首选）/ `<html lang>` / `navigator.language`
//    ② **覆盖层** = 本包 localStorage（用户点过节点上的「中/EN」才存在）
//    覆盖层为空 ⇒ 就是"跟随官方"（默认，也是官方切语言时该有的表现）。
//    🔴 单向：**官方改了语言，我们跟着变**；我们的按钮**只改本包节点**，不回写官方设置 ——
//       因为官方 watch 一旦看到 `Comfy.Locale` 变化就会 `refreshNodeDefinitions()` +
//       `reloadCurrentWorkflow()`（真机读源码确认），点一下按钮重载整个工作流是不能接受的代价。
//
// 探测路径有两条（官方切语言时**两条都可能动**，各装各的、任一命中就重刷）：
//   ① 官方 store 订阅（`$subscribe`）—— 最直接。
//   ② `<html lang>` 属性变化 —— 官方的等价副作用，store 订阅拿不到时的兜底。
// 🔴 只在**有覆盖层之外**的语言上生效（即用户没手动覆盖时）：
//    用户手动点过按钮 ⇒ 他就是要那个语言，官方切语言不该把他的选择冲掉。
function onOfficialLocaleChanged() {
    try {
        if (readStored()) return;        // 用户手动覆盖过 ⇒ 尊重他，不跟随
        _lang = null;                    // 丢缓存 ⇒ 重读官方
        safeApply();
    } catch (e) { /* 不掀画布 */ }
}

/** 装官方 locale 订阅（幂等）。🔴 必须在 `app.extensionManager` 就绪后调 —— 模块顶层时它不存在。 */
let _localeWatchInstalled = false;
function installOfficialLocaleWatch() {
    if (_localeWatchInstalled) return true;
    // ① 官方 store 订阅（`$subscribe`）—— 最直接。
    if (watchOfficialLocale(onOfficialLocaleChanged)) {
        _localeWatchInstalled = true;
        return true;
    }
    // ② `<html lang>` 属性变化 —— 官方 store 订阅拿不到时的兜底（官方切语言会同时改它）。
    try {
        if (typeof MutationObserver === "function") {
            new MutationObserver(onOfficialLocaleChanged)
                .observe(document.documentElement, { attributes: true, attributeFilter: ["lang"] });
            _localeWatchInstalled = true;
            return true;
        }
    } catch (e) { /* 老环境 */ }
    return false;
}

export function currentLang() {
    if (_lang == null) _lang = normLang(readStored() || defaultLang(readHostLang));
    return _lang;
}

/** 丢弃缓存 ⇒ 下次 `currentLang()` 重读官方语言（官方切语言/测试用）。 */
export function resetLangCache() {
    _lang = null;
    return currentLang();
}

/**
 * 用户**手动**指定语言 ⇒ 写覆盖层（localStorage）+ 广播。
 * 🔴 只影响本包节点；**不动官方 `Comfy.Locale`**（见文件头「单向跟随」）。
 * 覆盖层一旦存在，官方再切语言**不会**冲掉这个选择 —— 直到用户清掉它。
 */
export function setLang(v) {
    const L = normLang(v);
    _lang = L;
    try { localStorage.setItem(LANG_KEY, L); } catch (e) { /* 隐私模式 */ }
    try { window.dispatchEvent(new CustomEvent(LANG_EVENT, { detail: { lang: L } })); } catch (e) { /* 老浏览器 */ }
    return L;
}

/** 清掉覆盖层 ⇒ 回到「跟随官方语言」（按钮第 3 态 / 排障用）。 */
export function clearLangOverride() {
    _lang = null;
    try { localStorage.removeItem(LANG_KEY); } catch (e) { /* 隐私模式 */ }
    const L = currentLang();
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
    btn.title = "切换本包节点的中 / EN 标签（H3 Latent Relay · UI-only）"
        + "｜默认跟随官方 Language 设置；点这儿只改本包节点，不改官方语言";
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

// ---- ③.5 防覆盖兜底（🔴 与界面汉化插件共存的关键） ----
// 背景（2026-10-06 真机定位）：`ComfyUI-Chinese-Translation` 那类界面汉化插件有一个
// **每 1000 ms 的守护轮询**，会按它自己的词典把节点槽位标签/提示**改回中文**。
// 它的源码注释原话：「兜底补刷**被第三方扩展覆盖**/重建的节点槽位标签」——
// 也就是说它**本来就是设计来覆盖我们的**。而它的总开关只看自己的设置，
// **不对"界面是英文"做任何屏蔽**（`utils.js` 里写明「英文同样需要翻译」）。
// ⇒ 两个模块以 1 s 为周期抢同一批字段，表现为**同一节点上部分英文、部分中文**。
//
// 对策：我们在 `applyLang` 里把"期望值"记在 widget 上（`__h3I18nWant` /
// `__h3I18nWantTip`），这里每帧比对，不一致就写回。前端渲染每帧读 `widget.label`
// ⇒ **只要在渲染前写对，显示就是对的**，而且不需要改汉化插件一行代码。
//
// ⚠ 只在**值真的被改掉**时才写（无谓赋值会打脏画布）；值是内存标记、不进 JSON。
let _guardRaf = 0;
let _guardOn = false;

function guardPass() {
    const L = currentLang();
    for (const n of ownNodes(nodesOf)) {
        for (const w of (n.widgets || [])) {
            try {
                if (w && w.__h3I18nWant != null && w.label !== w.__h3I18nWant) {
                    w.label = w.__h3I18nWant;
                }
                if (w && w.__h3I18nWantTip != null && w.tooltip !== w.__h3I18nWantTip) {
                    w.tooltip = w.__h3I18nWantTip;
                }
            } catch (e) { /* 单格失败不影响其它 */ }
        }
    }
    for (const b of _BTNS) styleBtn(b, L);
}

/** 启动帧级兜底（幂等）。只在宿主提供 requestAnimationFrame 时启用。 */
export function startGuard() {
    if (_guardOn) return;
    if (typeof requestAnimationFrame !== "function") return;   // 老环境：靠 setup 的多拍重试
    _guardOn = true;
    const tick = () => {
        try { guardPass(); } catch (e) { /* 不掀画布 */ }
        _guardRaf = requestAnimationFrame(tick);
    };
    _guardRaf = requestAnimationFrame(tick);
}

/** 停掉兜底（测试/卸载用）。 */
export function stopGuard() {
    _guardOn = false;
    try { if (_guardRaf && typeof cancelAnimationFrame === "function") cancelAnimationFrame(_guardRaf); } catch (e) { /* 忽略 */ }
    _guardRaf = 0;
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
        // 🔴 `setup()` 可能**早于官方 store 就绪**（那时 `setting.get("Comfy.Locale")` 取不到）
        //    ⇒ 后面几拍**重读官方语言**把跟随层补对，并**装上官方语言订阅**（模块顶层时装不上，
        //    那时 `app.extensionManager` 还不存在 —— 2026-10-06 真机实测到过这个静默失效）。
        for (const ms of [0, 250, 1000, 2500]) {
            setTimeout(() => {
                try { installOfficialLocaleWatch(); resetLangCache(); safeApply(); }
                catch (e) { /* 不掀画布 */ }
            }, ms);
        }
        try {
            if (app.graph && typeof app.graph.onGraphChanged === "function") {
                app.graph.onGraphChanged(() => safeApply());
            }
        } catch (e) { /* 图 API 变了 ⇒ 靠上面的多拍重试 */ }
        safeApply();
        // 🔴 帧级防覆盖兜底：界面汉化插件有 1 s 守护轮询，会把我们写的标签/提示改回中文。
        //    不启这个 = 装了汉化插件的用户看到的是「部分英文部分中文」（真机实测）。
        startGuard();
    },

    nodeCreated(node) {
        if (!node || !H3_NODES[node.type]) return;
        // 少数版本此刻 widget 还没建好 ⇒ 下一拍补刷一次（宁可晚一拍，别静默不生效）。
        setTimeout(safeApply, 0);
    },
});

export {
    currentLang as __h3I18nCurrentLang,
    applyLangAll as __h3I18nApplyAll,
    startGuard as __h3I18nStartGuard,
    stopGuard as __h3I18nStopGuard,
    readOfficialLocale as __h3I18nReadOfficialLocale,
    resetLangCache as __h3I18nResetLangCache,
    clearLangOverride as __h3I18nClearOverride,
};
