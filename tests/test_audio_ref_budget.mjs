// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
// 第三方出处与许可见 THIRD-PARTY-NOTICES.md
//
// H3 Latent Relay · **前端纯函数**的离线单测：音频参考额度
//     node tests/test_audio_ref_budget.mjs
//
// 被测对象 = `web/relay_kit_refs.js`（纯模块，与浏览器里跑的是**同一份源码**）。
// 浏览器侧的挂钩在 `web/relay_kit_refs_ui.js`，那一份 import 了 `app` ⇒ 跑不了单测，
// 所以本文件用**静态扫描**兜住它（见第 5 组）—— 本仓同款做法见 `test_prompt_dispatch.mjs`。
//
// 🔴 本组的存在理由（两条都是 2026-10-04 当场踩出来的真 bug，必须钉死）：
//   ① 槽位正则写成 `/^ref_audio_\d+$/`，而**真实输入名是 `ref_audios.ref_audio_0`**
//      ⇒ 永不匹配 ⇒ 计数恒 0 ⇒ **警告永远不出现**（最坏的静默失效：看着像有保护）。
//   ② 标题后缀的剥离正则没覆盖「正好用满」那条文案 ⇒ 每次刷新把后缀**再叠一遍**
//      ⇒ 标题越滚越长。

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import {
    OFFICIAL_AUDIO_REF_MAX, REF_HOST, isRefAudioSlot, countRefAudioSlots,
    audioRefBudget, refBudgetFromScan, isScanUnknown, stripBudgetSuffix, budgetTitle,
} from "../web/relay_kit_refs.js";

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

/** 造一个官方参考节点：`n` 个音频槽，其中 `links` 里的下标是已接线的。 */
function hostNode(id, n, links = [], extraInputs = []) {
    const inputs = extraInputs.slice();
    for (let i = 0; i < n; i += 1) {
        // 🔴 **真实形态**：Autogrow 的输入名带前缀（母版图实测）。
        inputs.push({ name: `ref_audios.ref_audio_${i}`, type: "AUDIO", link: links.includes(i) ? 100 + i : null });
    }
    return { id, type: REF_HOST, inputs };
}

// ------------------------------------------------------- 1. 槽位名识别
console.log("[1] isRefAudioSlot：输入名识别（① 的回归锁）");
check("1.1 官方 Autogrow 真实名 `ref_audios.ref_audio_0` **必须**命中（① 的回归锁）",
    isRefAudioSlot("ref_audios.ref_audio_0") === true);
check("1.2 三个槽位 0/1/2 都命中",
    [0, 1, 2].every((i) => isRefAudioSlot(`ref_audios.ref_audio_${i}`)));
check("1.3 兼容裸名 `ref_audio_2`（另一形态，别写死一种）",
    isRefAudioSlot("ref_audio_2") === true);
check("1.4 `ref_video_audios.ref_video_audio_0` **不许**命中（视频自带音不是官方音频槽）",
    isRefAudioSlot("ref_video_audios.ref_video_audio_0") === false);
check("1.5 `ref_audios`（无数字后缀）不命中",
    isRefAudioSlot("ref_audios") === false && isRefAudioSlot("") === false);

// ------------------------------------------------------- 2. 计数
console.log("");
console.log("[2] countRefAudioSlots：数占用（判据 = `link != null`）");
{
    const s3 = countRefAudioSlots([hostNode("1078", 3, [0, 1, 2])]);
    check("2.1 3 槽全接 ⇒ used=3 / slots=3 / hosts=1",
        s3.used === 3 && s3.slots === 3 && s3.hosts === 1 && s3.hostsRead === 1,
        JSON.stringify(s3));
    const s1 = countRefAudioSlots([hostNode("1078", 3, [1])]);
    check("2.2 3 槽只接中间那个 ⇒ used=1（按 link 不按下标）", s1.used === 1, JSON.stringify(s1));
    const s0 = countRefAudioSlots([hostNode("1078", 3, [])]);
    check("2.3 一个都没接 ⇒ used=0", s0.used === 0 && s0.slots === 3, JSON.stringify(s0));
    const sz = countRefAudioSlots([hostNode("1078", 0, [])]);
    check("2.4 Autogrow 收到 0 槽（合法）⇒ slots=0 且**不是**未知",
        sz.slots === 0 && sz.hosts === 1 && isScanUnknown(sz) === false, JSON.stringify(sz));
    const s2 = countRefAudioSlots([
        hostNode("1078", 3, [0]),
        hostNode("1079", 3, [0, 1]),
    ]);
    check("2.5 两个参考节点 ⇒ hosts=2、used 相加",
        s2.hosts === 2 && s2.used === 3 && s2.slots === 6, JSON.stringify(s2));
    const sb = countRefAudioSlots([
        { id: 1, type: "H3RelayCopyBridge", inputs: [{ name: "ref_audio_0", type: "AUDIO", link: 7 }] },
    ]);
    check("2.6 不按类型过滤就数（只要名字像槽位就数）⇒ 别的节点接了同名输入也算占用",
        sb.used === 1, JSON.stringify(sb));
    check("2.7 畸形输入（null / 无 inputs / 非数组）不炸",
        countRefAudioSlots(null).used === 0
        && countRefAudioSlots([null, {}, { type: REF_HOST }]).hosts === 1
        && countRefAudioSlots([{ inputs: "nope" }]).used === 0);
    // 🔴 `link === 0` 是**合法 link id**（`0` 是假值！）⇒ 必须用 `!= null` 判，不许写成 `if (inp.link)`
    check("2.8 `link = 0`（合法 id、但是假值）也算**占用**（防「写成 if(link)」的静默漏计）",
        countRefAudioSlots([{ type: REF_HOST, inputs: [{ name: "ref_audio_0", link: 0 }] }]).used === 1);
}

// ------------------------------------------------------- 3. 额度与文案
console.log("");
console.log("[3] audioRefBudget：3+1 越界 / 2+1 用满 / 首段不注入");
{
    const over = audioRefBudget(3, 1);
    check("3.1 官方 3 + 本包 1 = 4 ⇒ over，文案含「4 个」与「官方上限 3」",
        over.over === true && over.total === 4 && over.mine === 1
        && over.text.includes("4 个") && over.text.includes(`官方上限 ${OFFICIAL_AUDIO_REF_MAX}`),
        over.text.slice(0, 40));
    check("3.2 越界文案**不许**教用户「拔掉声锚就退额度」（不接声锚时仍占 1 个）",
        over.text.includes("没有") || over.text.includes("无法"),
        "文案须说清本包这 1 个关不掉");
    const full = audioRefBudget(2, 1);
    check("3.3 官方 2 + 本包 1 = 3 ⇒ 不 over，文案含「正好用满」",
        full.over === false && full.total === 3 && full.text.includes("正好用满"));
    const first = audioRefBudget(3, 0);
    check("3.4 首段（stageIndex=0）本包不注入 ⇒ 官方 3 也不 over 且**无文案**",
        first.mine === 0 && first.over === false && first.text === "");
    check("3.5 无官方占用 + 首段 ⇒ 不报警",
        audioRefBudget(0, 0).total === 0 && audioRefBudget(0, 1).total === 1);
}

// ------------------------------------------------------- 4. 三态：越界 / 安全 / 未知
console.log("");
console.log("[4] refBudgetFromScan：三态（未知**不许**退化成 0）");
{
    const noHost = countRefAudioSlots([]);
    const b1 = refBudgetFromScan(noHost, 1);
    check("4.1 图上连参考节点都没有 ⇒ **不是未知**，官方占用 0",
        b1 != null && b1.total === 1 && b1.over === false, JSON.stringify(b1));
    const collapsed = countRefAudioSlots([hostNode("1078", 0, [])]);
    check("4.2 参考节点在、槽收成 0 个（Autogrow min=0）⇒ 也是 0，不报未知",
        refBudgetFromScan(collapsed, 1).total === 1);
    const unreadable = countRefAudioSlots([{ type: REF_HOST }]);   // 没有 inputs
    check("4.3 参考节点在、但**读不到输入表** ⇒ null（未知），**不许**退化成 0",
        isScanUnknown(unreadable) === true && refBudgetFromScan(unreadable, 1) === null,
        JSON.stringify(unreadable));
    const overScan = countRefAudioSlots([hostNode("1078", 3, [0, 1, 2])]);
    check("4.4 正常 3 槽全接 ⇒ 判定为越界",
        refBudgetFromScan(overScan, 1).over === true);
}

// ------------------------------------------------------- 5. 标题：幂等 + 剥离（② 的回归锁）
console.log("");
console.log("[5] budgetTitle / stripBudgetSuffix：幂等（② 的回归锁）");
{
    const BASE = "H3RelayCopyBridge";
    const t1 = budgetTitle(BASE, 3, 1);
    check("5.1 越界 ⇒ 标题含「⚠ 音频参考 4 个」",
        t1.startsWith(BASE) && t1.includes("⚠ 音频参考 4 个"), t1.slice(0, 60));
    check("5.2 越界文案**幂等**（反复调用不叠加）",
        budgetTitle(t1, 3, 1) === t1 && budgetTitle(budgetTitle(t1, 3, 1), 3, 1) === t1);
    const t2 = budgetTitle(BASE, 2, 1);
    check("5.3 「正好用满」⇒ 含该文案",
        t2.includes("正好用满"), t2.slice(0, 60));
    check("5.4 🔴「正好用满」也**幂等**（② 的回归锁：第一版剥不掉它 ⇒ 会叠）",
        budgetTitle(t2, 2, 1) === t2 && budgetTitle(budgetTitle(t2, 2, 1), 2, 1) === t2);
    const t3 = budgetTitle(t1, -1, 1);        // 越界 → 未知
    const t4 = budgetTitle(t3, 3, 1);         // 未知 → 又越界
    check("5.5 状态来回切换也**不会残留旧后缀**（未知 ↔ 越界）",
        t3.includes("额度未知") && !t3.includes("音频参考 4 个") && t4 === t1,
        t4.slice(0, 60));
    check("5.6 无文案时标题**原样返回**（不追加任何东西）",
        budgetTitle(BASE, 0, 1) === BASE && budgetTitle(BASE, 1, 1) === BASE);
    check("5.7 stripBudgetSuffix 对三种后缀都剥干净、且对干净串无副作用",
        stripBudgetSuffix(t1) === BASE && stripBudgetSuffix(t2) === BASE
        && stripBudgetSuffix(budgetTitle(BASE, -1, 1)) === BASE
        && stripBudgetSuffix(BASE) === BASE);
    check("5.8 节点标题被用户改过时，只**追加**后缀、不改他的前缀",
        budgetTitle("我的桥 A", 3, 1).startsWith("我的桥 A") === true);
}

// ------------------------------------------------------- 6. 壳的静态扫描（跑不了单测的那半）
console.log("");
console.log("[6] 静态扫描：纯模块不许依赖 app；壳不许自己重写判据");
{
    const read = (rel) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf-8");
    const pure = read("../web/relay_kit_refs.js");
    const shell = read("../web/relay_kit_refs_ui.js");
    check("6.1 纯模块**不许** import `scripts/app.js`（否则本单测根本跑不起来）",
        !/from\s+["'][^"']*scripts\/app\.js["']/.test(pure));
    check("6.2 壳文件**必须**从纯模块取判据（`refBudgetFromScan` / `budgetTitle`），不许自己再写一套",
        shell.includes("refBudgetFromScan") && shell.includes("budgetTitle")
        && shell.includes("isScanUnknown") && /from\s+["']\.\/relay_kit_refs\.js["']/.test(shell));
    check("6.3 壳里的每个 app 交互都在 try 里（前端报错不许掀画布）",
        !/\n\s*(?:app\.|const\s+\w+\s*=\s*app\.)/.test(shell.split("app.registerExtension")[0].replace(/try\s*\{[\s\S]*?\}\s*catch[\s\S]*?\}/g, "")));
}

// ------------------------------------------------------- 7. 自引用：断言数只准有一个真相源
// 同款做法见 `tests/test_prompt_dispatch.mjs` 的第 9 组。
// ⚠️ 只认**提到本测试文件**的行（`.mjs` 名字必须逐字出现），否则会把别的测试的期望值抓进来。
// 🔴 这个数**必须等于本文件实跑的通过数（含本行这条自检）**。
//    第一版写成 33（比实跑少 1）⇒ 自检"通过"了，而声明值与真值其实**不一致** ——
//    自引用机制最怕的就是这种"差一个数还绿"的假绿。改断言后请同步 ci.yml 与 docs/08。
const EXPECTED_CHECKS = 34;
console.log("");
console.log("[7] 断言数自引用（ci.yml / docs/08 里写的「期望 N/0」必须等于本文件实跑数）");
{
    const decl = [];
    for (const rel of ["../.github/workflows/ci.yml", "../docs/08-testing.md"]) {
        const s = readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf-8");
        for (const line of s.split(/\r?\n/)) {
            if (!line.includes("test_audio_ref_budget")) continue;
            const m = /(\d+)\s*\/\s*0/.exec(line);
            if (m) decl.push({ rel, n: Number(m[1]) });
        }
    }
    const shown = decl.map((d) => `${d.rel.split("/").pop()}=${d.n}`).join(" ");
    check("7.1 ci.yml / docs/08 里写的「N/0」== 本文件实跑数（改测试必须同步那两处）",
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
