// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
// 第三方出处与许可见 THIRD-PARTY-NOTICES.md
//
// H3 Latent Relay · Chain 词分发的**纯函数**（前端与离线单测共用同一份）
// 不依赖 ComfyUI 运行时 ⇒ 离线单测（node）直接跑这一份，浏览器里也跑这一份。
//
// 🔴 0.6.15 起这个模块**只剩两个函数**。原来还有一套"把词写进出词节点"的辅助
//   （`detectPromptTargets` / `resolveTarget` / `writePrompt` / `describeTarget`）——
//   那是"前端替你写格子"那条老路。0.6.15 把词分发做成 **Chain 节点的能力**
//   （节点按 `stage_index` 输出第 k 块词），老路**整条删除** ⇒ 那几个函数一并删掉。
//   理由：留着就有两套实现（铁律一禁止"两条路各写一遍"），而且两条路的口径迟早漂。
//
// 现在保留的：
//   · `splitPromptBlocks` —— **前端做"词块数预检"**（跑之前就知道词够不够）要用；
//     也是跨语言一致性锁的一半（另一半在 `relay_core.split_prompt_blocks`）。
//   · `collectStageIds`   —— 拼接时按**段号**取回每段的 `prompt_id`（重跑同段不重复算）。

/** 分块分隔行：单独一行、三个及以上短横线。 */
const SEPARATOR = /^\s*-{3,}\s*$/;

/** 把多行文本按 `---` 分块。返回**已去掉空块**的词数组；空文本返回 `[]`。 */
export function splitPromptBlocks(text) {
    if (typeof text !== "string" || !text.trim()) return [];
    const blocks = [];
    let cur = [];
    for (const line of text.split(/\r?\n/)) {
        if (SEPARATOR.test(line)) {
            blocks.push(cur.join("\n"));
            cur = [];
        } else {
            cur.push(line);
        }
    }
    blocks.push(cur.join("\n"));
    return blocks.map((b) => b.trim()).filter((b) => b.length > 0);
}

/**
 * 按**段号**收集本轮的段记录（`stageIds[k]` = 第 k 段那一次的 `{id, seq}`）。
 *
 * 为什么不是"来一个 push 一个"：同一段重跑（不满意再点一次 ▶）会得到两个 id，
 * push 会把重跑的段算成**两段** ⇒ 拼出一条带重复片段的成片。按段号存则后一次覆盖前一次。
 *
 * 🔴 为什么**不再压实**（0.6.18，2026-09-30 日志实证的段序错乱）：
 *   旧版把稀疏数组里的真值压紧成 `ids` —— 段号→位置的映射就在那一步丢了。
 *   叠加后端"history 里查不到就丢 id"之后：**后面的段被当成前面的段拼进成片**。
 *   ⇒ 现在返回 `stages`（**带段号**，与位置一一对应），空洞单独列在 `holes` 里。
 *
 * 🔴 空洞只在**首尾之间**找（0.6.18 修正，与后端 `_stage_gaps` 同口径）：
 *   从第 k 段**续跑**是本包的正常用法（`⏭ 续跑` / 直接改段号后 ▶），那时数组开头本来就空着
 *   —— 从下标 0 起找会把这种正常用法判成"缺第 1 段" ⇒ **自动拼接被误拒**（假阳性）。
 *
 * 返回 `{stages, holes}`：`stages` = `[{stage, id, seq}]` 按段号升序（`seq` = 该记录属于第几轮，
 * 由调用方用来标"这一段来自更早一轮"）；`holes` = 首尾之间没有记录的段号（**1 起算**）。
 * 兼容旧形状：`stageIds[k]` 直接是字符串 id 时按 `{id, seq: 0}` 读。
 */
export function collectStageIds(stageIds) {
    const stages = [];
    const arr = Array.isArray(stageIds) ? stageIds : [];
    for (let k = 0; k < arr.length; k += 1) {
        const rec = arr[k];
        const id = rec && typeof rec === "object" ? rec.id : rec;
        if (!id) continue;
        stages.push({ stage: k, id: String(id),
                      seq: (rec && typeof rec === "object" && rec.seq != null) ? rec.seq : 0 });
    }
    const holes = [];
    if (stages.length > 1) {
        const have = new Set(stages.map((s) => s.stage));
        for (let k = stages[0].stage; k <= stages[stages.length - 1].stage; k += 1) {
            if (!have.has(k)) holes.push(k + 1);
        }
    }
    return { stages, holes };
}
