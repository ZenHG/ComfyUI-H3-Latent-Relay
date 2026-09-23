// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
// 第三方出处与许可见 THIRD-PARTY-NOTICES.md
//
// Chain 词分发**纯函数**的离线单测（零依赖、零浏览器）：
//     node tests/test_prompt_dispatch.mjs
//
// 覆盖：`---` 分块边界 / 词格探测与优先级 / prompt_target 三种写法 / JSON 格只改 prompt 字段
//      / 畸形输入一律**拒绝写入**（绝不把用户的 JSON 覆盖掉）。
// 对账口径写在 tests/test_relay_core.py 的第 26 组之外 —— 这组只跑 JS，不占 GPU、不进 Python 计数。

import { splitPromptBlocks, detectPromptTargets, resolveTarget, writePrompt, describeTarget,
         collectStageIds } from "../web/relay_kit_prompt.js";

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

function mkNode(id, type, widgets, mode = 0) {
    return { id, type, mode, widgets: widgets.map(([name, value]) => ({ name, value })) };
}

// ---------------------------------------------------------------- 1. 分块
console.log("[1] splitPromptBlocks：`---` 分块");
check("1.1 空文本 ⇒ 0 块（= 老行为，一个字都不动）",
    JSON.stringify(splitPromptBlocks("")) === "[]" && JSON.stringify(splitPromptBlocks(null)) === "[]");
check("1.2 单块（无分隔行）⇒ 1 块",
    JSON.stringify(splitPromptBlocks("一段词")) === JSON.stringify(["一段词"]));
check("1.3 三块（标准写法）",
    JSON.stringify(splitPromptBlocks("A\n---\nB\n---\nC")) === JSON.stringify(["A", "B", "C"]));
check("1.4 五个短横线也算分隔（`-{3,}`）",
    JSON.stringify(splitPromptBlocks("A\n-----\nB")) === JSON.stringify(["A", "B"]));
check("1.5 两个短横线**不是**分隔（避免误伤正文）",
    JSON.stringify(splitPromptBlocks("A\n--\nB")) === JSON.stringify(["A\n--\nB"]));
check("1.6 行尾空格/CRLF 也算分隔行（Windows 粘贴友好）",
    JSON.stringify(splitPromptBlocks("A\r\n  ---  \r\nB")) === JSON.stringify(["A", "B"]));
check("1.7 空块被丢掉（首尾多余分隔不会多出一段）",
    JSON.stringify(splitPromptBlocks("---\nA\n---\n\n---\nB\n---")) === JSON.stringify(["A", "B"]));
check("1.8 多行词内部换行保留",
    JSON.stringify(splitPromptBlocks("A1\nA2\n---\nB1")) === JSON.stringify(["A1\nA2", "B1"]));

// ---------------------------------------------------------------- 2. 探测
console.log("[2] detectPromptTargets：词格探测与优先级");
const gJson = mkNode(6, "CSGlideCastCS", [["h3_data", '{"prompt":"旧词","width":672}']]);
const gOff = mkNode(7, "MiniMaxH3ImageToVideo", [["prompt", "官方词"]]);
const gOther = mkNode(8, "CLIPTextEncode", [["prompt", "通用词"]]);
check("2.1 h3_data 优先于官方 prompt，官方 prompt 优先于通用 prompt",
    JSON.stringify(detectPromptTargets([gOther, gOff, gJson]).map((c) => c.field))
        === JSON.stringify(["h3_data", "prompt", "prompt"])
    && detectPromptTargets([gOther, gOff, gJson]).map((c) => c.priority).join(",") === "0,1,2");
check("2.2 bypass 的节点不计入（mode=4）",
    detectPromptTargets([mkNode(9, "CSGlideCastCS", [["h3_data", '{"prompt":"x"}']], 4)]).length === 0);
check("2.3 h3_data 不是 JSON ⇒ 不当词格（不猜）",
    detectPromptTargets([mkNode(10, "CSGlideCastCS", [["h3_data", "随便一段文字"]])]).length === 0);
check("2.4 非字符串 widget 不计入",
    detectPromptTargets([mkNode(11, "X", [["prompt", 3]], 0)]).length === 0);

// ---------------------------------------------------------------- 3. 解析 target
console.log("[3] resolveTarget：空 / 节点 id / 节点 id.字段");
const graph = [gJson, gOff, gOther];
const rAuto = resolveTarget(graph, "");
check("3.1 留空 ⇒ 自动探测到唯一的最高优先候选（h3_data）",
    rAuto.ok && rAuto.target.node.id === 6 && rAuto.target.field === "h3_data"
    && rAuto.target.kind === "json");
const twoJson = [gJson, mkNode(12, "CSGlideCastCS", [["h3_data", '{"prompt":"y"}']])];
const rAmbig = resolveTarget(twoJson, "");
check("3.2 同优先级有 2 个候选 ⇒ **报错**并列出来（不猜）",
    !rAmbig.ok && rAmbig.message.includes("#6") && rAmbig.message.includes("#12")
    && rAmbig.message.includes("prompt_target"));
const rNone = resolveTarget([mkNode(13, "Y", [["image", 1]])], "");
check("3.3 图里没有任何词格 ⇒ 报错（不静默）",
    !rNone.ok && rNone.message.includes("没有找到"));
const rId = resolveTarget(graph, " 8 ");
check("3.4 只写节点 id ⇒ 取该节点的最优格（通用 prompt）",
    rId.ok && rId.target.node.id === 8 && rId.target.field === "prompt" && rId.target.kind === "text");
const rField = resolveTarget(graph, "6.h3_data");
check("3.5 `id.字段` 精确点名", rField.ok && rField.target.field === "h3_data");
check("3.6 节点不存在 ⇒ 报错", !resolveTarget(graph, "999").ok);
check("3.7 字段不存在 ⇒ 报错", !resolveTarget(graph, "6.nope").ok);
check("3.8 describeTarget 给出可读定位",
    describeTarget(rField.target).includes("#6") && describeTarget(null) === "（无）");

// ---------------------------------------------------------------- 4. 写入
console.log("[4] writePrompt：写文本格 / 只改 JSON 的 prompt 字段 / 畸形一律拒写");
const wText = { node: mkNode(20, "CLIPTextEncode", [["prompt", "旧"]]), field: "prompt",
                kind: "text", type: "CLIPTextEncode" };
const wr1 = writePrompt(wText, "新词");
check("4.1 文本格：整格替换", wr1.ok && wText.node.widgets[0].value === "新词");

const nJson = mkNode(21, "CSGlideCastCS", [["h3_data", '{"prompt":"旧词","width":672,"seed":7}']]);
const wJson = { node: nJson, field: "h3_data", kind: "json", type: "CSGlideCastCS" };
const wr2 = writePrompt(wJson, "新词\n第二行");
const obj2 = JSON.parse(nJson.widgets[0].value);
check("4.2 JSON 格：只改 prompt，其余字段原样保留",
    wr2.ok && obj2.prompt === "新词\n第二行" && obj2.width === 672 && obj2.seed === 7);

const nBad = mkNode(22, "CSGlideCastCS", [["h3_data", "{不是 JSON"]]);
const wr3 = writePrompt({ node: nBad, field: "h3_data", kind: "json", type: "CSGlideCastCS" }, "x");
check("4.3 JSON 解析失败 ⇒ **拒写**且原值不动（绝不覆盖用户那一坨）",
    !wr3.ok && nBad.widgets[0].value === "{不是 JSON" && wr3.message.includes("JSON"));

const nArr = mkNode(23, "CSGlideCastCS", [["h3_data", "[1,2,3]"]]);
const wr4 = writePrompt({ node: nArr, field: "h3_data", kind: "json", type: "CSGlideCastCS" }, "x");
check("4.4 JSON 是数组（非对象）⇒ 拒写", !wr4.ok && nArr.widgets[0].value === "[1,2,3]");

check("4.5 目标格不存在 ⇒ 报错不炸",
    !writePrompt({ node: mkNode(24, "X", []), field: "prompt", kind: "text" }, "x").ok);

// ---------------------------------------------------------------- 5. 段记录收集
console.log("[5] collectStageIds：同一段重跑不许算成两段");
check("5.1 顺序跑 3 段 ⇒ 按段号升序给 3 个 id、无缺口",
    JSON.stringify(collectStageIds(["a", "b", "c"])) === '{"ids":["a","b","c"],"holes":[]}');
check("5.2 第 2 段重跑（后一次覆盖前一次）⇒ 仍是 3 段，不是 4 段",
    JSON.stringify(collectStageIds(["a", "b2", "c"]).ids) === '["a","b2","c"]'
    && collectStageIds(["a", "b2", "c"]).ids.length === 3);
check("5.3 跳段（只跑了 1、3 段）⇒ 报出缺的段号（1 起算）",
    JSON.stringify(collectStageIds(["a", , "c"])) === '{"ids":["a","c"],"holes":[2]}');
check("5.4 空/畸形输入 ⇒ 空结果不炸",
    collectStageIds([]).ids.length === 0 && collectStageIds(undefined).ids.length === 0
    && collectStageIds(null).holes.length === 0);

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
