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
    OFFICIAL_AUDIO_REF_MAX, REF_HOST, isRefAudioSlot, refSlotIndex, countRefAudioSlots,
    audioRefBudget, refBudgetFromScan, isScanUnknown, stripBudgetSuffix, budgetTitle,
    ordinalHint,
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

// ------------------------------------------------------- 6b. 「已接线顺序」编号（`<Audio j>`）
// 🔴 本组的来源（2026-10-04 读宿主源码确证，不是猜的）：
//    宿主 `comfy_api/latest/_io.py:1198-1211` 按 `ref_audio_0..max` **索引升序**枚举，
//    但 `if expected_id in live_inputs` **只把已接线的槽放进 dict**；官方节点再用
//    `for audio in (ref_audios or {}).values()` 交给 tokenizer，tokenizer 按**枚举顺序**
//    发 `<Audio 1..N>`（`comfy/text_encoders/minimax.py:178`）。
// ⇒ `<Audio j>` 的 j = **第 j 个已接线的槽**（按槽号升序），**不是槽号本身**。
//    只接 `ref_audio_2` ⇒ 标签是 `<Audio 1>`；用户照槽号写 `<Audio 3>` 会**指空且不报错**。
console.log("");
console.log("[6b] 音频槽的「已接线顺序」编号（ordinalHint）—— 编号会骗人时必须开口");
{
    check("6b.1 refSlotIndex：解析槽号；非槽位 ⇒ null",
        refSlotIndex("ref_audios.ref_audio_2") === 2
        && refSlotIndex("ref_audio_0") === 0
        && refSlotIndex("ref_video_audios.ref_video_audio_1") === null
        && refSlotIndex("") === null && refSlotIndex(null) === null);

    const cont = countRefAudioSlots([hostNode("1078", 3, [0, 1, 2])]);
    check("6b.2 从 0 连续接满 3 个 ⇒ 编号与槽号一致，`ordinalHint` **必须**为空（不制造噪声）",
        cont.perHost.length === 1
        && JSON.stringify(cont.perHost[0].wired) === JSON.stringify(
            [{ slot: 0, ordinal: 1 }, { slot: 1, ordinal: 2 }, { slot: 2, ordinal: 3 }])
        && ordinalHint(cont.perHost) === "",
        JSON.stringify(cont.perHost[0].wired));

    const only2 = countRefAudioSlots([hostNode("1078", 3, [2])]);
    check("6b.3 只接 `ref_audio_2` ⇒ 它的官方编号是 **<Audio 1>**（不是 3），且必须开口提示",
        JSON.stringify(only2.perHost[0].wired) === JSON.stringify([{ slot: 2, ordinal: 1 }])
        && ordinalHint(only2.perHost).includes("ref_audio_2→<Audio 1>"),
        ordinalHint(only2.perHost).slice(0, 70));

    const gap = countRefAudioSlots([hostNode("1078", 3, [0, 2])]);
    check("6b.4 跳号（接 0 与 2）⇒ <Audio 1> / <Audio 2>，两个映射都要点出来",
        ordinalHint(gap.perHost).includes("ref_audio_0→<Audio 1>")
        && ordinalHint(gap.perHost).includes("ref_audio_2→<Audio 2>"),
        ordinalHint(gap.perHost).slice(0, 90));

    const from1 = countRefAudioSlots([hostNode("1078", 3, [1, 2])]);
    check("6b.5 从 1 起连续（接 1、2）也**算会骗人**（<Audio 1>/<Audio 2> ≠ 槽号+1）",
        ordinalHint(from1.perHost) !== "", ordinalHint(from1.perHost).slice(0, 80));

    // 乱序的 inputs 也要按**槽号升序**（宿主的枚举顺序），否则编出来的号与真实呈现错位。
    const shuffled = countRefAudioSlots([
        { id: 1, type: REF_HOST, inputs: [
            { name: "ref_audios.ref_audio_2", link: 9 },
            { name: "ref_audios.ref_audio_0", link: 8 },
        ] },
    ]);
    check("6b.6 `inputs` 顺序打乱 ⇒ `wired` 仍按**槽号升序**（与宿主枚举同序）",
        JSON.stringify(shuffled.perHost[0].wired) === JSON.stringify(
            [{ slot: 0, ordinal: 1 }, { slot: 2, ordinal: 2 }]),
        JSON.stringify(shuffled.perHost[0].wired));

    check("6b.7 未接线 / 无参考节点 ⇒ 没有编号可提示（空串，不是噪声）",
        ordinalHint(countRefAudioSlots([hostNode("1078", 3, [])]).perHost) === ""
        && ordinalHint(countRefAudioSlots([]).perHost) === ""
        && ordinalHint(null) === "");

    check("6b.8 `refBudgetFromScan` 把提示挂在 `hint` 上（额度 `text` 本身不含它 —— 两者独立）",
        (() => {
            const s = countRefAudioSlots([hostNode("1078", 3, [0, 2])]);
            const b = refBudgetFromScan(s, 1);
            return b.hint.includes("<Audio 2>") && !b.text.includes("已接线顺序");
        })());

    const tGap = budgetTitle("桥", 2, 1, ordinalHint(gap.perHost));
    check("6b.9 标题里「编号提示」也**幂等**（反复调用不叠加）",
        tGap.includes("已接线顺序")
        && budgetTitle(tGap, 2, 1, ordinalHint(gap.perHost)) === tGap
        && budgetTitle(budgetTitle(tGap, 2, 1, ordinalHint(gap.perHost)), 2, 1,
                       ordinalHint(gap.perHost)) === tGap);
    check("6b.10 未知态（额度读不到）⇒ 只写「额度未知」，**不**硬塞编号提示",
        budgetTitle("桥", -1, 1, ordinalHint(gap.perHost)).includes("额度未知")
        && !budgetTitle("桥", -1, 1, ordinalHint(gap.perHost)).includes("已接线顺序"));

    // 🔴 最可能撞上的真实场景：**只接了 `ref_audio_2` 一个槽、且没越界** —— 此时额度文案是**空**的，
    //    于是标题里**只有编号提示**这一条后缀。剥离正则必须照样吃掉它，否则每刷新一次就叠一行。
    const only2T = budgetTitle("桥", 1, 1, ordinalHint(only2.perHost));
    check("6b.11 「额度无文案 + 只有编号提示」时标题也要**幂等**（这条后缀是第一段，剥离必须吃它）",
        only2T.includes("已接线顺序")
        && budgetTitle(only2T, 1, 1, ordinalHint(only2.perHost)) === only2T
        && stripBudgetSuffix(only2T) === "桥",
        only2T.slice(0, 60));
}

// ------------------------------------------------------- 7. 自引用：断言数只准有一个真相源
// 同款做法见 `tests/test_prompt_dispatch.mjs` 的第 9 组。
// ⚠️ 只认**提到本测试文件**的行（`.mjs` 名字必须逐字出现），否则会把别的测试的期望值抓进来。
// 🔴 这个数**必须等于本文件实跑的通过数（含本行这条自检）**。
//    第一版写成 33（比实跑少 1）⇒ 自检"通过"了，而声明值与真值其实**不一致** ——
//    自引用机制最怕的就是这种"差一个数还绿"的假绿。改断言后请同步 ci.yml 与 docs/08。
const EXPECTED_CHECKS = 45;
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
    // 🔴 三边相等才算过：**声明的两个文件** == 常量 == **本文件此刻实跑的通过数 + 1（本行自己）**。
    //    只比「声明 == 常量」是不够的：那样改了断言却忘了同步常量时，检查照样绿
    //    （「差一个数还绿」正是这段注释警告过的假绿），必须把**实跑数**也拉进来当判据。
    //    ⚠️ 2026-10-04 实测有效：加了一条 6b.11 却忘了改常量，这条**当场报红**。
    check("7.1 ci.yml / docs/08 里写的「N/0」== 常量 == **本文件实跑数**（三边相等，改测试必须同步）",
        decl.length >= 2 && decl.every((d) => d.n === EXPECTED_CHECKS)
        && pass + 1 === EXPECTED_CHECKS,
        `${shown} ｜ 常量=${EXPECTED_CHECKS} ｜ 实跑=${pass + 1}`);
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
