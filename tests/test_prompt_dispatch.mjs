// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
// 第三方出处与许可见 THIRD-PARTY-NOTICES.md
//
// H3 Latent Relay · **前端纯函数**的离线单测（零依赖、零浏览器）：
//     node tests/test_prompt_dispatch.mjs
//
// 覆盖两组纯函数（都在 `web/`，前端与单测共用同一份）：
//   · `relay_kit_prompt.js` —— 词块分块 / 按段号收集段记录
//   · `relay_kit_sync.js`   —— run_id 跨节点同步（三态裁决 / 广播规划 / 表 ↔ nodes.py 对账）
// 另有两组**静态扫描**（`chain.js` 依赖浏览器，跑不了单测 ⇒ 用扫描兜住静默失效）：
//   阶段表 ↔ 相位串、会提交的按钮是否都过 run_id 闸。
// 对账口径写在 tests/test_relay_core.py 的第 26 组之外 —— 这组只跑 JS，不占 GPU、不进 Python 计数。

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { splitPromptBlocks, collectStageIds } from "../web/relay_kit_prompt.js";
import {
    RUN_ID_TYPES, isRunIdType, resolveRunId, planRunIdSync, describeRunIdConflict,
} from "../web/relay_kit_sync.js";

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

// ---------------------------------------------------------------- 8. run_id 跨节点同步
// 🔴 为什么必须机检：`run_id` 有**六类节点**各存一份（表在 `web/relay_kit_sync.js`）。
//   漏掉任何一类 ⇒ 那个节点永远用别的目录名 ⇒ 桥去错的目录找段文件。
//   这与 2026-09-30 那场真实事故（段号推进漏了「音频缝」⇒ 成片音画错段）**是同一个病**。
//   所以这里不只测纯函数，还**扫 nodes.py 反查那张表全不全** —— 表是手写的，会漏。
console.log("");
console.log("[8] run_id 跨节点同步：三态裁决 / 广播规划 / 表 ↔ nodes.py 对账");
check("8.1 isRunIdType：认表里那六类，不认无关类型",
    RUN_ID_TYPES.every((s) => isRunIdType(s.type))
    && !isRunIdType("KSampler") && !isRunIdType(undefined) && !isRunIdType("H3RelayPost"),
    RUN_ID_TYPES.length + " 类：" + RUN_ID_TYPES.map((s) => s.type).join("/"));

const M = (...xs) => xs.map(([id, runId], i) => ({ id, type: "H3RelayLatentSave", runId }));
check("8.2 全组都是同一个名字 ⇒ ok（无需动作）",
    JSON.stringify(resolveRunId(M(["1", "myfilm"], [2, "myfilm"]))) .startsWith('{"state":"ok"')
    && resolveRunId(M(["1", "myfilm"], [2, "myfilm"])).ids.length === 2
    && resolveRunId(M(["1", "myfilm"], [2, "myfilm"])).empty.length === 0);
check("8.3 唯一非空 + 有空格 ⇒ ok，且点出**哪几格是空的**（这些格要补齐）",
    JSON.stringify(resolveRunId(M(["1", "myfilm"], [2, "  "], [3, ""])).empty) === "[2,3]"
    && resolveRunId(M(["1", "myfilm"], [2, ""])).value === "myfilm");
check("8.4 两个不同的非空名 ⇒ **conflict**（不猜、不自动挑一个）",
    resolveRunId(M(["1", "a"], [2, "b"])).state === "conflict"
    && JSON.stringify([...resolveRunId(M(["1", "a"], [2, "b"])).byValue.keys()]) === '["a","b"]');
check("8.5 全空 ⇒ empty（后端 `_stage_path` 会 raise，前端提前说清）",
    resolveRunId(M(["1", ""], [2, "   "])).state === "empty"
    && resolveRunId(M(["1", ""], [2, " "])).empty.length === 2);
check("8.6 畸形输入不炸（null / undefined / 无 id）",
    resolveRunId(null).state === "empty" && resolveRunId(undefined).empty.length === 0
    && resolveRunId([null, { runId: "x" }]).state === "empty");
check("8.7 空值**不扩散**（清空格子 ≠ 想把全组清空）—— 这正是「误删一个字符」的护栏",
    JSON.stringify(planRunIdSync(M(["1", "old"], [2, "old"]), "1", "")) ===
    '{"targets":[],"value":"","reason":"empty"}'
    && planRunIdSync(M(["1", "old"]), "1", "   ").reason === "empty");
check("8.8 非空 ⇒ 只列「值不同的」、且**跳过发起者自己**",
    JSON.stringify(planRunIdSync(M(["1", "old"], [2, "new"], [3, "old"]), "2", "new").targets)
    === '["1",3]');
check("8.9 已经全一致 ⇒ reason=`none`（不刷屏、不做无谓写入）",
    planRunIdSync(M(["1", "same"], [2, "same"]), "1", "same").reason === "none");
check("8.10 id 一律 `String()` 归一：数字 id 与字符串 id 必须认作同一个",
    JSON.stringify(planRunIdSync(M([961, "old"], ["902", "old"]), 961, "new").targets) === '["902"]');
check("8.11 冲突提示点名**哪个节点是哪个名字**（用户能照着改）",
    describeRunIdConflict(resolveRunId(M(["1", "a"], [2, "b"])), (id) => "落盘#" + id)
        .includes("落盘#1") && describeRunIdConflict(resolveRunId(M(["1", "a"], [2, "b"])),
        (id) => "落盘#" + id).includes("落盘#2"));

// —— 静态对账：表（手写）↔ nodes.py（真相源）
{
    const pyPath = fileURLToPath(new URL("../nodes.py", import.meta.url));
    const src = readFileSync(pyPath, "utf-8");
    const declared = [];
    let cur = null;
    for (const line of src.split(/\r?\n/)) {
        const m = /^class (\w+)/.exec(line);
        if (m) { cur = m[1]; continue; }
        // 只认**widget 声明行**（缩进后紧跟 `"run_id": (`）：函数默认参数 `run_id=""` 不算
        if (cur && /^\s+"run_id":\s*\(/.test(line) && !declared.includes(cur)) declared.push(cur);
    }
    const inTable = RUN_ID_TYPES.map((s) => s.type);
    const missing = declared.filter((c) => !inTable.includes(c));
    const extra = inTable.filter((c) => !declared.includes(c));
    check(`8.12 表 ↔ nodes.py 双向对账（nodes.py 声明了 ${declared.length} 个，表里 ${inTable.length} 个）`,
        declared.length > 0 && missing.length === 0 && extra.length === 0,
        `nodes.py 有而表里没有：${missing.join("/") || "无"} ｜ 表里有而 nodes.py 没有：${extra.join("/") || "无"}`);
}

// —— 静态闸：**会提交**的按钮必须都过 `runIdPreflight`
{
    const chainSrc = readFileSync(fileURLToPath(new URL("../web/relay_kit_chain.js", import.meta.url)), "utf-8");
    // 🔴 **白名单式，不是列举式**：列举式（只查已知那四个）对**新加的按钮是瞎的** ——
    //   那正是"手写清单会漏"的老病（2026-09-30 段号推进漏了音频缝）。这里反过来：
    //   默认**每个按钮都必须有闸**，只有下面这三个明确不需要（它们不排队提交）。
    //   用**前缀**匹配：按钮文案末段常会微调（「⏹ Stop（本轮跑完即停）」），前缀不受影响。
    const NO_GATE_PREFIX = ["⏹ Stop", "🧩 拼成一条", "↺ Reset"];
    const labels = [...chainSrc.matchAll(/addBtn\("([^"]+)"/g)].map((m) => m[1]);
    const bad = [];
    for (const label of labels) {
        const at = chainSrc.indexOf(`addBtn("${label}"`);
        const end = chainSrc.indexOf("addBtn(", at + 6);
        // `end < 0` = 这是最后一个按钮 ⇒ 截到文件尾（`slice(at, -1)` 会少一个字符，别那么写）
        const body = chainSrc.slice(at, end < 0 ? undefined : end);
        const needsGate = !NO_GATE_PREFIX.some((p) => label.startsWith(p));
        if (needsGate && !body.includes("runIdPreflight")) bad.push(label);
    }
    check(`8.13 每个「会提交」的按钮都过了 run_id 闸（白名单式：新按钮默认必须有闸；共 ${labels.length} 个按钮）`,
        labels.length >= 6 && bad.length === 0,
        bad.length ? "没闸：" + bad.join("、") : labels.join("、"));
    check("8.14 挂钩入口在（`nodeCreated` 里给带 run_id 的节点钩回调）",
        /nodeCreated\s*\(node\)\s*\{[\s\S]{0,400}hookRunIdWidget/.test(chainSrc));
}

// ---------------------------------------------------------------- 9. 自引用：断言数只准有一个真相源
// 🔴 为什么要这一组：**同一个数字写在三个文件里就一定会漂**（本仓 2026-10-02 亲历：
//   `LOCAL-维护规范` 的 §六 门槛表漂了 7 天）。`ci.yml` 与 `docs/08` 都要给用户写"期望 N/0"，
//   而 N 的真正来源是**这份文件实跑出来的数** ⇒ 让它自引用：谁改了测试而没同步那两处，这里就红。
//   （同款做法见 `tools/review_050.py` 的 H3g 自引用机检。）
const EXPECTED_CHECKS = 35;   // 含本行这条自检自己
console.log("");
console.log("[9] 断言数自引用（ci.yml / docs/08 里写的「期望 N/0」必须等于本文件实跑数）");
{
    const decl = [];
    for (const rel of ["../.github/workflows/ci.yml", "../docs/08-testing.md"]) {
        const s = readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf-8");
        // ⚠️ 只认**提到本测试文件**的那些行：ci.yml 里还有 `test_relay_core` 432/0 等别的期望值，
        //    用全文件正则抓 `期望 N/0` 会一股脑抓进来（第一版就是这么假红的）。
        for (const line of s.split(/\r?\n/)) {
            if (!line.includes("test_prompt_dispatch")) continue;
            const m = /(\d+)\s*\/\s*0/.exec(line);
            if (m) decl.push({ rel, n: Number(m[1]) });
        }
    }
    const shown = decl.map((d) => `${d.rel.split("/").pop()}=${d.n}`).join(" ");
    check("9.1 ci.yml / docs/08 里写的「N/0」== 本文件实跑数（改测试必须同步这三处）",
        decl.length >= 2 && decl.every((d) => d.n === EXPECTED_CHECKS),
        `${shown} ｜ 本文件=${EXPECTED_CHECKS}`);
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
