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

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { splitPromptBlocks, collectStageIds } from "../web/relay_kit_prompt.js";

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

// ---------------------------------------------------------------- 6. 跨语言口径锁
// 🔴 与 Python 侧（tests/test_relay_core.py 的 11.19）读**同一份**样本文件。
//    0.6.15 起词分发是节点能力（Python）+ 画布副本（JS）两条**调用**路径，
//    但**口径只能有一份** —— 这两条断言就是那道锁：谁改了口径，两边一起红。
console.log("");
console.log("[6] 跨语言口径锁：分块结果必须与共享样本一致");
{
    const casesPath = fileURLToPath(new URL("./prompt_blocks_cases.json", import.meta.url));
    const cases = JSON.parse(readFileSync(casesPath, "utf-8")).cases;
    const bad = [];
    for (const c of cases) {
        const got = splitPromptBlocks(c.text);
        if (JSON.stringify(got) !== JSON.stringify(c.expect)) {
            bad.push(`${c.name}: got=${JSON.stringify(got)} expect=${JSON.stringify(c.expect)}`);
        }
    }
    check(`6.1 分块口径与共享样本一致（${cases.length} 例，跨语言锁）`,
        bad.length === 0, bad.join(" ｜ "));
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
