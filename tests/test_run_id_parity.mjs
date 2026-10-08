// run_id 同步语义的**跨层对账**（前端侧）—— 0.6.31 新增。
//
// 为什么要有这个文件：前端 `web/relay_kit_sync.js` 与后端 `relay_core/plan.py` 是
// **两份实现**（规范 §二·D「跨层孪生」）。两份实现"看起来一样"不算对齐 ——
// 唯一可靠的机制是：**一份 fixture、两侧各跑一遍、都要过**。
// ⇒ 谁改了语义而没改另一侧，这里当场红。
//
// 期望值只有一份：`tests/parity/run_id_cases.json`（Python 侧读同一个文件）。
//
// 跑法：node tests/test_run_id_parity.mjs
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { resolveRunId, planRunIdSync, RUN_ID_TYPES } from "../web/relay_kit_sync.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const FX = JSON.parse(readFileSync(resolve(HERE, "parity/run_id_cases.json"), "utf-8"));

let total = 0, failed = 0;
function check(name, ok, detail = "") {
    total += 1;
    if (ok) {
        console.log(`  [OK]   ${name}`);
    } else {
        failed += 1;
        console.log(`  [FAIL] ${name}${detail ? "  —— " + detail : ""}`);
    }
}
const sameSet = (a, b) => {
    const x = [...(a ?? [])].map(String).sort();
    const y = [...(b ?? [])].map(String).sort();
    return x.length === y.length && x.every((v, i) => v === y[i]);
};

console.log("[1] 裁决 + 广播规划：逐条对账 fixture");
for (const c of FX.cases) {
    const v = resolveRunId(c.members);
    const e = c.expect.verdict;
    // 🔴 `by_value` 也必须断言（审核 P0-3）：它是**唯一**用来「点名哪个节点是哪个名字」的数据
    //   （`__init__.py::_describe_run_id` 与前端 `describeRunIdConflict` 都靠它写提示）。
    //   只比 state/value/ids/empty 的话，把 by_value 删掉两侧照样全绿。
    const bv = e.by_value
        ? Object.entries(e.by_value).every(([k, ids]) => sameSet((v.byValue.get(k) ?? []), ids))
            && sameSet([...v.byValue.keys()], Object.keys(e.by_value))
        : true;
    const okV = v.state === e.state && String(v.value) === String(e.value)
        && sameSet(v.ids, e.ids) && sameSet(v.empty, e.empty) && bv
        && (e.values === undefined || sameSet(v.values, e.values));
    check(`${c.name} · verdict`, okV,
        `实得 state=${v.state} value=${JSON.stringify(v.value)} ids=${JSON.stringify(v.ids)} empty=${JSON.stringify(v.empty)} byValue=${JSON.stringify([...v.byValue])}`);

    const ep = c.expect.plan;
    if (ep === null) {
        // 冲突 / 全空 ⇒ 调用方（`runIdPreflight`）必须**直接拦住**，压根不走到广播
        check(`${c.name} · 前端会拦住（不该产出广播计划）`, true);
        continue;
    }
    const p = planRunIdSync(c.members, c.origin, c.value === null ? v.value : c.value);
    const okP = sameSet(p.targets, ep.targets)
        && p.reason === ep.reason && sameSet(p.skipped, ep.skipped);
    check(`${c.name} · plan`, okP,
        `实得 targets=${JSON.stringify(p.targets)} reason=${p.reason} skipped=${JSON.stringify(p.skipped)}`);
}

console.log("\n[2] 表与 nodes.py 对账（六类一个都不能少 —— 少一个 = 那一类永远用别的目录名）");
{
    const src = readFileSync(resolve(HERE, "../nodes.py"), "utf-8");
    // nodes.py 里出现 `"run_id": (` 的类 = 声明了这一格
    const declared = new Set();
    const clsRe = /^class\s+(\w+)/gm;
    let m;
    while ((m = clsRe.exec(src)) !== null) {
        const start = m.index;
        const next = src.indexOf("\nclass ", start + 1);
        const body = src.slice(start, next === -1 ? src.length : next);
        if (/["']run_id["']\s*:/.test(body)) declared.add(m[1]);
    }
    const table = new Set(RUN_ID_TYPES.map((s) => s.type));
    const missing = [...declared].filter((c) => !table.has(c)).sort();
    const extra = [...table].filter((c) => !declared.has(c)).sort();
    check(`nodes.py 声明 run_id 的类 ⊆ RUN_ID_TYPES（声明 ${declared.size} / 表 ${table.size}）`,
        missing.length === 0, missing.length ? "表里没有：" + missing.join("、") : "");
    check(`RUN_ID_TYPES ⊆ nodes.py 声明的类（不许有幽灵条目）`,
        extra.length === 0, extra.length ? "表里多出：" + extra.join("、") : "");
}

console.log("\n[3] 契约：范围判据 = 「单 Chain ⇒ 全图」（0.6.31 的核心改动）");
{
    const src = readFileSync(resolve(HERE, "../web/relay_kit_chain.js"), "utf-8");
    check("3.1 runIdScope 按**启用中的 Chain 数量**决定范围，不是按分组框",
        /function runIdScope\(originNode\)\s*\{[\s\S]{0,400}chains\.length\s*<=\s*1/.test(src));
    check("3.2 只有多条 Chain 才可能按框隔离（group 分支在计数之后）",
        /chains\.length\s*<=\s*1[\s\S]{0,400}return\s+"group"/.test(src));
    check("3.3 runIdMembers 真的用了 runIdScope（否则改了也不生效 —— 同款「加了但没调用」）",
        /function runIdMembers\([\s\S]{0,400}runIdScope\(originNode\)/.test(src)
        && /scope\s*===\s*"group"\s*\?\s*nodesInSameGroup/.test(src));
    check("3.4 多 Chain 且不在任何框里 ⇒ graph-multi 且**必须出声**（scopeNote 有告警）",
        /return\s+"graph-multi"/.test(src) && /graph-multi[\s\S]{0,300}⚠/.test(src));
    // 🔴 3.5（审核 P0-1 的低成本兜底）：`CHAINS` 是 **Set**，必须先展开再迭代。
    //   0.6.12 那个 bug 正是 `CHAINS.filter(...)` ⇒ 任何 `.filter/.map/.forEach` 直接挂在
    //   `CHAINS` 上都会 TypeError。⚠️ 这条锁的是**写法**不是行为 —— 真要锁行为得把
    //   `chainStateNear` 抽成可注入的纯函数（已记进交接的遗留项，本轮不做重构）。
    // ⚠ 先剥掉注释再找：`web/relay_kit_chain.js` 里**修复说明本身**写了 `CHAINS.filter(...)`
    //   （"原来这里却用 CHAINS.filter"）⇒ 不剥注释就会命中自己那行说明。
    //   （第一版没剥，当场把修复注释当成违规报了出来 —— 这也是"判据落在哪一层"的问题。）
    const codeOnly = src
        .split('\n')
        .filter((l) => !/^\s*(\/\/|\*)/.test(l))
        .join('\n');
    const badChains = codeOnly.match(/CHAINS\s*\.\s*(filter|map|forEach|reduce)\s*\(/g) || [];
    check("3.5 CHAINS（Set）不直接迭代：必须先 [...CHAINS] 展开（判据不看注释）",
        badChains.length === 0, badChains.join("、"));
}

console.log(`\n======================================================================`);
console.log(`结果：通过 ${total - failed} / 失败 ${failed}`);
process.exit(failed ? 1 : 0);