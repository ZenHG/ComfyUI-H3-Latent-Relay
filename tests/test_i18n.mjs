// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
// 第三方出处与许可见 THIRD-PARTY-NOTICES.md
//
// H3 Latent Relay · **中英切换（i18n）纯函数**的离线单测（零依赖、零浏览器）：
//     node tests/test_i18n.mjs
//
// 覆盖 `web/relay_kit_i18n.js`（前端与单测共用同一份）。测的是**风险点**，不是覆盖率：
//   ① 铁律一：只改 `label`/`title`，**绝不改 `name`**（改了 = 改图）
//   ② 铁律三：用户的自定义标题**不许**被覆盖
//   ③ 与 `relay_kit_refs_ui.js` 的**共存**：标题只换前缀、后缀（额度提示）原样保留
//   ④ 枚举值**白名单外不翻**（模型文件名/段号/路径翻了 = 找不到文件）
//   ⑤ 幂等：同一语言应用两次，第二次**零改动**
//   ⑥ 🔴 悬浮提示只写 `widget.tooltip`，**不碰** name / value / options.values（不落 JSON）
//   ⑦ 自引用：ci.yml / docs/08 里写的「N/0」== 本文件实跑数（改测试必须同步这三处）
//
// ⚠️ 词表**完整性**不由本文件负责（那是 Python 侧的事）：`tools/review_050.py` 的
//    H3n（标签）/ H3p（提示）项直接拿 `nodes.py` 的 `INPUT_TYPES` 键去核对
//    ⇒ 「新增参数忘了翻译」在机检那一层红，不必在这里维护第二份名单。

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import {
    H3_NODES, H3_WIDGETS, H3_SLOTS, H3_TIPS, LANG_KEY, LANG_EVENT,
    normLang, defaultLang, comboLabel, isDefaultTitle, swapTitle, applyLang, ownNodes, tipFor, pickTip,
} from "../web/relay_kit_i18n.js";

let pass = 0;
const fails = [];

function check(name, cond, detail = "") {
    if (cond) {
        pass += 1;
        console.log("  [OK]   " + name + (detail ? "  " + detail : ""));
    } else {
        fails.push(name);
        console.log("  [FAIL] " + name + (detail ? "  " + detail : ""));
    }
}

/** 造一个"像 litegraph 节点"的纯对象（**不碰 DOM**）。 */
function mkNode(type, opts = {}) {
    return Object.assign({
        type,
        title: opts.title !== undefined ? opts.title : (H3_NODES[type] || {}).en,
        widgets: (opts.widgets || []).map((w) => Object.assign({ options: { values: w.values || [] } }, w)),
        inputs: (opts.inputs || []).map((s) => Object.assign({}, s)),
        outputs: (opts.outputs || []).map((s) => Object.assign({}, s)),
    }, opts.extra || {});
}

console.log("H3 Latent Relay · i18n 纯函数单测");
console.log("=".repeat(70));

// ---- 1. 语言归一 ----
check("1.1 `normLang` 只认 zh / en：`zh-CN` `zh_TW` `ZH` ⇒ zh，其余 ⇒ en",
    normLang("zh-CN") === "zh" && normLang("zh_TW") === "zh" && normLang("ZH") === "zh"
    && normLang("en-US") === "en" && normLang("ja") === "en" && normLang(null) === "en"
    && normLang(undefined) === "en" && normLang("") === "en");

// ⚠️ 「宿主给不出语言」时**跟随 navigator.language**（node 22 上它可能是 zh-CN，
//    浏览器里通常也是用户语言）⇒ 断��必须跟 navigator 对齐，**不许写死 en**。
{
    const nav = (globalThis.navigator && globalThis.navigator.language) || "";
    const want = normLang(nav);
    check("1.2 `defaultLang` 跟随宿主语言；宿主给不出/抛错 ⇒ 跟随 `navigator.language`（**不默认中文**）",
        defaultLang(() => "zh-CN") === "zh" && defaultLang(() => "en-US") === "en"
        && defaultLang(() => "") === want
        && defaultLang(() => { throw new Error("no settings"); }) === want,
        `navigator.language=${JSON.stringify(nav)} ⇒ ${want}`);
}

// ---- 2. 铁律一：只改 label，绝不改 name ----
{
    const n = mkNode("H3RelayTrimAV", {
        widgets: [{ name: "trim_frames", label: "trim frames" }, { name: "run_id" }],
        inputs: [{ name: "images" }], outputs: [{ name: "prev_tail" }],
    });
    applyLang(n, "zh");
    check("2.1 参数 `name` 一字未改（改了 = 工作流脏、连线会错位）",
        n.widgets[0].name === "trim_frames" && n.widgets[1].name === "run_id"
        && n.inputs[0].name === "images" && n.outputs[0].name === "prev_tail",
        n.widgets.map((w) => w.name).join(","));
    check("2.2 只换 `label`：中文标签已生效",
        n.widgets[0].label === "裁剪帧数" && n.widgets[1].label === "运行 ID"
        && n.inputs[0].label === "图像" && n.outputs[0].label === "上段末帧",
        `${n.widgets[0].label} / ${n.widgets[1].label} / ${n.outputs[0].label}`);
}

// ---- 3. 铁律三 + 标题：默认名互换、自定义标题不动 ----
check("3.1 标题双语互换（英文默认名 ⇒ 中文默认名）",
    swapTitle("🔗 H3 Relay · Copy Bridge", "H3RelayCopyBridge", "zh") === "🔗 H3 Relay · 复合桥"
    && swapTitle("🔗 H3 Relay · 复合桥", "H3RelayCopyBridge", "en") === "🔗 H3 Relay · Copy Bridge",
    swapTitle("🔗 H3 Relay · Copy Bridge", "H3RelayCopyBridge", "zh"));
check("3.2 标题为空 ⇒ 视为默认名，可直接填译文（画布上常见）",
    isDefaultTitle("", "H3RelayPost") && swapTitle("", "H3RelayPost", "zh") === "🔗 H3 Relay · 后处理");
check("3.3 🔴 **用户自定义标题原样保留**（不许覆盖别人的命名）",
    !isDefaultTitle("我的关键节点", "H3RelayPost")
    && swapTitle("我的关键节点", "H3RelayPost", "zh") === "我的关键节点",
    swapTitle("我的关键节点", "H3RelayPost", "zh"));
check("3.4 未知 class_type ⇒ 标题一个字都不动（别乱改别人的节点）",
    swapTitle("SomeOtherNode", "SomeOtherNode", "zh") === "SomeOtherNode"
    && swapTitle("x", null, "zh") === "x");

// ---- 4. 与额度提示共存（两个模块都写 title）----
{
    const withHint = "🔗 H3 Relay · Copy Bridge ⚠ 额度已用满 3/3";
    const zh = swapTitle(withHint, "H3RelayCopyBridge", "zh");
    check("4.1 🔴 标题**只换前缀**：额度提示后缀原样保留（否则抹掉 `relay_kit_refs_ui.js` 的活）",
        zh === "🔗 H3 Relay · 复合桥 ⚠ 额度已用满 3/3", zh);
    const back = swapTitle(zh, "H3RelayCopyBridge", "en");
    check("4.2 切回英文也保留后缀（双向对称 ⇒ 谁先谁后都对）",
        back === withHint, back);
    check("4.3 中文态的标题被认成默认名（能被再次刷新，不卡在中英混合态）",
        isDefaultTitle("🔗 H3 Relay · 复合桥 ⚠ 额度已用满 3/3", "H3RelayCopyBridge"));
}

// ---- 5. 枚举白名单 ----
check("5.1 白名单内的枚举值有中文显示（硬边 / 汉宁窗）",
    comboLabel("mask_mode", "hard", "zh") === "硬边"
    && comboLabel("window_shape", "hann", "zh") === "汉宁窗");
check("5.2 🔴 白名单外**一律不猜**（`model_name` 是文件名、段号是数字）",
    comboLabel("model_name", "RealESRGAN_x4plus.pth", "zh") === null
    && comboLabel("bed_stage", "2", "zh") === null
    && comboLabel("mask_mode", "不存在的值", "zh") === null
    && comboLabel("__不存在的参数__", "x", "zh") === null);
check("5.3 英文态回落显示原值（不留中文钩子）",
    comboLabel("mask_mode", "hard", "en") === "hard" && comboLabel("mask_mode", null, "zh") === null);
{
    const n = mkNode("H3RelayCopyBridge", {
        widgets: [{ name: "mask_mode", values: ["hard", "blend"] }],
    });
    applyLang(n, "zh");
    const hasZh = typeof n.widgets[0].options.getOptionLabel === "function";
    const shown = hasZh ? n.widgets[0].options.getOptionLabel("hard") : null;
    applyLang(n, "en");
    const removed = typeof n.widgets[0].options.getOptionLabel !== "function";
    check("5.4 钩子装上能显示中文、切回英文**卸掉钩子**（共享 options 对象，别人的节点不受影响）",
        hasZh && shown === "硬边" && removed,
        `zh:${shown} / en已卸:${removed}`);
    check("5.5 🔴 枚举**值**一个都没被改（values 是存档值）",
        JSON.stringify(n.widgets[0].options.values) === JSON.stringify(["hard", "blend"]));
}

// ---- 6. 幂等 + 统计 ----
{
    const n = mkNode("H3RelayChain", {
        widgets: [{ name: "stage_index" }, { name: "concat_name" }],
        outputs: [{ name: "report" }],
    });
    applyLang(n, "zh");
    const once = { ...applyLang(n, "zh") };            // 第二遍应当零改动
    check("6.1 幂等：同一语言再应用一次**零改动**（否则每次重刷都动一次画布）",
        once.widgets === 0 && once.slots === 0 && once.title === false,
        `widgets=${once.widgets} slots=${once.slots} title=${once.title}`);
    const back = applyLang(n, "en");
    check("6.2 切回英文：全部改回（不是单向的）",
        back.widgets === 2 && back.slots === 1 && back.title === true
        && n.widgets[0].label === "stage" && n.outputs[0].label === "report",
        `w=${back.widgets} s=${back.slots} t=${back.title}`);
}

// ---- 7. 未知 key 静默放过（开源用户千奇百怪的节点也要活着）----
{
    const n = mkNode("H3RelayPost", { widgets: [{ name: "某个未来参数" }, { name: "guide" }] });
    let threw = null;
    try { applyLang(n, "zh"); } catch (e) { threw = e; }
    check("7.1 词表里没有的参数**不崩、不改**（只改认识的）",
        threw === null && n.widgets[0].label === undefined && n.widgets[1].label === "参考图",
        threw ? String(threw) : `未知项原样 / 已知项=${n.widgets[1].label}`);
    check("7.2 `ownNodes` 只挑本包的节点（数组 / Map / 脏数据都吃）",
        ownNodes(() => [mkNode("H3RelayPost"), mkNode("KSampler")]).length === 1
        && ownNodes(() => new Map([[1, mkNode("H3RelayPost")], [2, mkNode("KSampler")]])).length === 1
        && ownNodes(() => null).length === 0 && ownNodes(() => [null, undefined]).length === 0);
}

// ---- 8. 词表形状自检（防手滑：漏语言 / 空串 / 键对不上）----
{
    const bad = [];
    for (const [k, v] of Object.entries(H3_NODES)) {
        if (!v || !v.zh || !v.en) bad.push(`H3_NODES.${k}`);
        if (v && v.zh === v.en) bad.push(`H3_NODES.${k}(zh==en)`);
    }
    for (const [k, v] of Object.entries({ ...H3_WIDGETS, ...H3_SLOTS })) {
        if (!v || !v.zh || !v.en) bad.push(`${k}`);
    }
    check("8.1 词表里没有空项 / 漏语言 / zh==en 的条目", bad.length === 0, bad.slice(0, 5).join(","));
    check("8.2 八个节点的标题词条齐全（少一个就有一个节点切不了）",
        Object.keys(H3_NODES).length === 8, String(Object.keys(H3_NODES).length));
    check("8.3 语言键与事件名带包前缀（**不与别的包共用**，免得互相覆盖 localStorage）",
        LANG_KEY === "h3relay_lang" && LANG_EVENT === "h3relay:lang-changed"
        && LANG_KEY !== "xh_node_lang" && LANG_EVENT !== "xh:lang-changed");
}

// ---- 9. 悬浮提示（H3_TIPS）----
{
    // 9.1 按 class_type+参数名 取值；同名参数在不同节点要**取到各自那条**（这是本表建表的理由）
    const zhRunId = tipFor("H3RelayChain", "run_id", "zh");
    const zhRunIdSave = tipFor("H3RelayLatentSave", "run_id", "zh");
    check("9.1 提示按 `class_type.参数名` 分节点取值（同名参数不许串台）",
        typeof zhRunId === "string" && typeof zhRunIdSave === "string"
        && zhRunId !== zhRunIdSave
        && zhRunId.includes("续跑") && zhRunIdSave.includes("片名"),
        `chain=${(zhRunId || "").slice(0, 18)}… / save=${(zhRunIdSave || "").slice(0, 18)}…`);

    // 9.2 中英两版都取得到，且**不相等**（只有一边 = 另一种界面下露错语言）
    const enRunId = tipFor("H3RelayChain", "run_id", "en");
    check("9.2 同一条提示中英都能取到、且内容不同",
        typeof enRunId === "string" && enRunId !== zhRunId && /[A-Za-z]/.test(enRunId),
        `en=${(enRunId || "").slice(0, 24)}…`);

    // 9.3 🔴 词表里没有的组合 ⇒ null（决定"一个字节都不动"的那一步）
    check("9.3 词表里没有的 (节点,参数) ⇒ `null`（**保留后端原文**，宁缺勿错）",
        tipFor("H3RelayChain", "不存在的参数", "zh") === null
        && tipFor("KSampler", "run_id", "zh") === null
        && tipFor(null, "run_id", "zh") === null
        && tipFor("H3RelayChain", null, "zh") === null);

    // 9.4 写进 `w.tooltip`；且**不碰** label / name / value / options.values
    const n = mkNode("H3RelayChain", {
        widgets: [{ name: "run_id", label: "run id", value: "myfilm", options: { values: ["a", "b"] } }],
    });
    const before = JSON.stringify(n.widgets[0].options.values);
    applyLang(n, "zh");
    const w = n.widgets[0];
    check("9.4 🔴 提示写进 `widget.tooltip`，**不碰** name / value / options.values",
        w.tooltip === zhRunId && w.name === "run_id" && w.value === "myfilm"
        && JSON.stringify(w.options.values) === before,
        `tooltip=${String(w.tooltip).slice(0, 16)}… name=${w.name} value=${w.value}`);

    // 9.5 切英文 ⇒ 提示跟着换（不是单向的）
    applyLang(n, "en");
    check("9.5 切回英文时提示也换成英文版",
        n.widgets[0].tooltip === enRunId, String(n.widgets[0].tooltip).slice(0, 24) + "…");

    // 9.6 词表里没有的参数 ⇒ tooltip **保持 undefined**（不许被填成空串）
    const n2 = mkNode("H3RelayPost", { widgets: [{ name: "某个未来参数" }] });
    applyLang(n2, "zh");
    check("9.6 词表里没有的参数 ⇒ 不写 tooltip（undefined，不是空串 ⇒ 前端回落后端原文）",
        !("tooltip" in n2.widgets[0]) || n2.widgets[0].tooltip === undefined);

    // 9.7 幂等：同一语言应用两次，提示零改动
    const n3 = mkNode("H3RelayChain", { widgets: [{ name: "run_id" }] });
    applyLang(n3, "zh");
    const again = applyLang(n3, "zh");
    check("9.7 幂等：第二次应用提示零改动（否则每次重刷都动一次画布）",
        again.tips === 0, `tips=${again.tips}`);

    // 9.8 表形状：键都带 `类型.参数` 点号、无空项、无 zh==en
    const bad = [];
    for (const [k, v] of Object.entries(H3_TIPS)) {
        if (!k.includes(".")) bad.push(k + ":nokey");
        if (!v || !v.zh || !v.en) bad.push(k + ":missing");
        if (v && v.zh === v.en) bad.push(k + ":zh==en");
    }
    check("9.8 H3_TIPS 形状自检（键带点号 / 无空项 / 无 zh==en）", bad.length === 0, bad.slice(0, 5).join(","));

    // 9.9 🔴 查找优先级：精确键 > `*.` 通配；缺一边语言时**当没命中**（不许只翻一半）
    //     ⚠ 今天表里一条通配都没有 ⇒ 用一张**假表**测这条纯逻辑（`pickTip` 不碰模块状态）
    const fake = {
        "NewNode.foo": { zh: "精确中文", en: "exact en" },
        "*.foo": { zh: "通配中文", en: "fallback en" },
        "*.bar": { zh: "只有中文" },                       // 缺 en ⇒ 应当**不算命中**
    };
    check("9.9 查找优先级：精确键 > `*.` 通配；缺一边语言 ⇒ 当没命中",
        pickTip(fake, "NewNode", "foo").zh === "精确中文"
        && pickTip(fake, "OtherNode", "foo").zh === "通配中文"
        && pickTip(fake, "OtherNode", "bar") === null
        && pickTip(fake, "", "foo") === null
        && pickTip(fake, "OtherNode", "") === null
        && pickTip(fake, "OtherNode", "nope") === null,
        `exact=${pickTip(fake, "NewNode", "foo").zh} / fallback=${pickTip(fake, "OtherNode", "foo").zh} / half-lang=${pickTip(fake, "OtherNode", "bar")}`);
}

// ---- 10. 自引用：ci.yml / docs/08 的期望数 == 本文件实跑 ----
{
    const EXPECTED_CHECKS = 33;                 // 含本行这条自检自己（pass 在本行执行时还没算上自己 ⇒ 比的是 pass+1）
    const root = fileURLToPath(new URL("..", import.meta.url));
    const decl = [];
    for (const rel of [".github/workflows/ci.yml", "docs/08-testing.md"]) {
        let txt = "";
        try { txt = readFileSync(root + rel, "utf-8"); } catch (e) { /* 缺文件算不过 */ }
        for (const line of txt.split(/\r?\n/)) {
            if (line.includes("test_i18n.mjs")) {
                const m = /(\d+)\s*\/\s*0/.exec(line);
                if (m) decl.push({ rel, n: Number(m[1]) });
            }
        }
    }
    const shown = decl.map((d) => `${d.rel.split("/").pop()}=${d.n}`).join(" ");
    check("10.1 ci.yml / docs/08 里写的「N/0」== 本文件实跑数（改测试必须同步这三处）",
        decl.length >= 2 && decl.every((d) => d.n === EXPECTED_CHECKS) && pass + 1 === EXPECTED_CHECKS,
        `${shown} ｜ 本文件=${pass} ｜ 常量=${EXPECTED_CHECKS}`);
}

// ---------------------------------------------------------------- 结果
console.log("");
console.log("=".repeat(70));
console.log(`结果：通过 ${pass} / 失败 ${fails.length}`);
if (fails.length) {
    console.log("失败项：");
    for (const f of fails) console.log("   -", f);
}
console.log("=".repeat(70));
process.exit(fails.length ? 1 : 0);
