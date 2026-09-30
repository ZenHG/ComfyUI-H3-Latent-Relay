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
console.log("[5] collectStageIds：同一段重跑不许算成两段；段号与位置**永不错位**");
check("5.1 顺序跑 3 段 ⇒ 3 条记录、按段号升序、无缺口",
    JSON.stringify(collectStageIds(["a", "b", "c"]).stages)
    === '[{"stage":0,"id":"a","seq":0},{"stage":1,"id":"b","seq":0},{"stage":2,"id":"c","seq":0}]'
    && collectStageIds(["a", "b", "c"]).holes.length === 0);
check("5.2 第 2 段重跑（后一次覆盖前一次）⇒ 仍是 3 段，不是 4 段",
    JSON.stringify(collectStageIds(["a", "b2", "c"]).stages.map((s) => s.id))
    === '["a","b2","c"]');
// 🔴 0.6.18 的核心回归：**不许压实**。旧实现返回 `ids: ["a","c"]`（第 3 段的 id 顶到第 2 段槽）
//   ⇒ 后端按位置拼 ⇒ 段序错乱（2026-09-30 日志实证）。
check("5.3 跳段（只跑了 1、3 段）⇒ **保留真段号**（stage=0/2）+ 报出缺第 2 段",
    JSON.stringify(collectStageIds(["a", , "c"]))
    === '{"stages":[{"stage":0,"id":"a","seq":0},{"stage":2,"id":"c","seq":0}],"holes":[2]}');
check("5.4 空/畸形输入 ⇒ 空结果不炸",
    collectStageIds([]).stages.length === 0 && collectStageIds(undefined).stages.length === 0
    && collectStageIds(null).holes.length === 0);
check("5.5 `{id, seq}` 记录：seq 原样带出（拼接时用来标「来自更早一轮」）",
    JSON.stringify(collectStageIds([{ id: "a", seq: 7 }, null, { id: "c", seq: 9 }]).stages)
    === '[{"stage":0,"id":"a","seq":7},{"stage":2,"id":"c","seq":9}]');
check("5.6 无 id 的记录（id 为空串）不算一段",
    JSON.stringify(collectStageIds([{ id: "", seq: 1 }, { id: "b", seq: 1 }]))
    === '{"stages":[{"stage":1,"id":"b","seq":1}],"holes":[]}');
// 🔴 5.7 本组第 5 轮的修正：**续跑**（从第 k 段起跑）时数组开头本来就是空的 ——
//   空洞只从**首条记录**起找，否则会把正常续跑判成"缺第 1 段" ⇒ 自动拼接被误拒（假阳性）。
check("5.7 续跑（第 1 段没跑过）⇒ **不报**「缺第 1 段」（与后端 `_stage_gaps` 同口径）",
    JSON.stringify(collectStageIds([, { id: "b", seq: 1 }, { id: "c", seq: 1 }]))
    === '{"stages":[{"stage":1,"id":"b","seq":1},{"stage":2,"id":"c","seq":1}],"holes":[]}');
check("5.8 首尾**之间**真的缺一段 ⇒ 照报（续跑不掩盖中间的空洞）",
    JSON.stringify(collectStageIds([, { id: "b", seq: 1 }, , { id: "d", seq: 1 }]).holes)
    === '[3]');

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

// ---------------------------------------------------------------- 7. 阶段表一致性
// 🔴 为什么必须机检：`chain.js` 里 `say(node, state, "<相位>", …)` 的相位串写错时，
//   `PHASES[phase]` 取不到 ⇒ 字形前缀变空、标题栏色带不画 —— **不报任何错**，
//   用户只看到"状态格还是那句话，就是看不出阶段"。这正是本仓最怕的静默失效。
//   `chain.js` 依赖浏览器（import 宿主 scripts/app.js），跑不了单测 ⇒ 用**静态扫描**兜住。
console.log("");
console.log("[7] chain.js 阶段表 ↔ say() 相位串 一致性（静态扫描）");
{
    const chainPath = fileURLToPath(new URL("../web/relay_kit_chain.js", import.meta.url));
    const src = readFileSync(chainPath, "utf-8");
    const keys = new Set([...src.matchAll(/^\s{4}(\w+):\s*\{\s*glyph:/gm)].map((m) => m[1]));
    const flat = src.replace(/\s+/g, " ");
    const arg3 = [...flat.matchAll(/say\([^,]+,[^,]+,\s*([^,]+?),/g)].map((m) => m[1]);
    const used = new Set();
    for (const a of arg3) for (const m of a.matchAll(/"(\w+)"/g)) used.add(m[1]);
    const missing = [...used].filter((p) => !keys.has(p));
    check(`7.1 阶段表含全部 7 个相位（实际 ${keys.size} 个：${[...keys].join("/")}）`,
        ["idle", "queued", "running", "done", "stopped", "warn", "error"].every((k) => keys.has(k)),
        [...keys].join("/"));
    check(`7.2 say() 用到的相位串（${[...used].join("/")}）全部在阶段表里`,
        used.size > 0 && missing.length === 0, "表里没有：" + missing.join("/"));
    check("7.3 除 idle 外每个相位都有颜色（空闲不画色带，其余必须画）",
        [...keys].every((k) => k === "idle"
            ? /^\s{4}idle: \{ glyph: "[^"]+", color: null/m.test(src)
            : new RegExp(`^\\s{4}${k}: \\{ glyph: "[^"]+", color: "#[0-9a-fA-F]{6}"`, "m").test(src)));
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
