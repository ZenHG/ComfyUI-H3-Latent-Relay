// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-H3-Relay-Kit contributors
// 第三方出处与许可见 THIRD-PARTY-NOTICES.md
// H3 Relay Kit · Chain 前端
// 在 H3RelayChain 节点上提供按钮：Run / Approve / 连跑 / Stop / Reset / 拼成一条。
// 作用：自动推进同一张图里「拷贝桥（复合桥）+ 落盘」两个节点的 stage_index，免去每段手动改数字。
//
// 0.6.7 加两件事（**都默认关 = 老图行为逐位不变**）：
//   · **词分发**：`prompts` 填了词（`---` 分块）时，跑第 k 段前把第 k 块写进出词节点
//     —— 连跑不再"反复提交同一份词"。块数不够 ⇒ **不排队**并报错。
//   · **拼接成片**：`auto_concat` 开着 ⇒ 连跑结束自动拼；平时也可以点「🧩 拼成一条」。
//     拼接在包内实现（不需要外部 ffmpeg），画面流拷贝无损、音频按段去 priming 对齐。
//
// 找节点规则：优先取与本 Chain 节点**同一个分组框**里的桥+落盘；
// 没有分组就全图找唯一的一对；找到多对则提示先分组。
// 本文件行为对标 H3-Motion-Context 的 Chain 思路，代码为独立实现。

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { splitPromptBlocks, resolveTarget, writePrompt, describeTarget, collectStageIds }
    from "./relay_kit_prompt.js";

function nodeCenter(n) {
    return [n.pos[0] + (n.size?.[0] ?? 0) / 2, n.pos[1] + (n.size?.[1] ?? 0) / 2];
}

function inGroup(n, g) {
    const [cx, cy] = nodeCenter(n);
    const b = g.bounding;
    return cx >= b[0] && cx <= b[0] + b[2] && cy >= b[1] && cy <= b[1] + b[3];
}

function findMemberNodes(chainNode, typeName) {
    const all = app.graph._nodes.filter((n) => n.type === typeName && n.mode === 0);
    if (!all.length) return [];
    // 与 Chain 同分组的优先
    for (const g of app.graph.groups ?? []) {
        const members = all.filter((n) => inGroup(n, g));
        const inIt = inGroup(chainNode, g);
        if (inIt && members.length) return members;
    }
    return all; // 没有分组兜底：全图
}

function setStatus(chainNode, text) {
    const w = (chainNode.widgets ?? []).find((x) => x.name === "status");
    if (w) {
        w.value = text;
        chainNode.setDirtyCanvas?.(true, true);
    } else {
        // 后端没声明 status widget 时，提示就只进控制台、界面上看不到。
        // 这里显式 warn 一次，免得"按钮点了没反应"变成无声故障。
        console.warn(
            "[H3 Relay Chain] 节点上没有 status widget（后端 nodes.py 的 H3RelayChain.INPUT_TYPES " +
                "应声明一个可选的 status: STRING）——状态只写进控制台：" + text
        );
    }
    console.info("[H3 Relay Chain]", text);
}

function widgetValue(n, name, fallback = "") {
    const w = (n?.widgets ?? []).find((x) => x.name === name);
    return w ? w.value : fallback;
}

function getStage(n) {
    const w = (n.widgets ?? []).find((x) => x.name === "stage_index");
    return w ? Math.max(0, Math.round(w.value ?? 0)) : 0;
}

function setStage(n, v) {
    const w = (n.widgets ?? []).find((x) => x.name === "stage_index");
    if (w) w.value = Math.max(0, Math.round(v));
}

function findPair(chainNode) {
    const bridges = findMemberNodes(chainNode, "H3RelayCopyBridge");
    const saves = findMemberNodes(chainNode, "H3RelayLatentSave");
    if (bridges.length !== 1 || saves.length !== 1) {
        setStatus(
            chainNode,
            `⚠ 找到 桥×${bridges.length} / 落盘×${saves.length}，需要恰好各 1 个。` +
                `请把 Chain、桥、落盘放进同一个分组框（右键 → 添加分组）。`
        );
        return null;
    }
    return { bridge: bridges[0], save: saves[0] };
}

async function queuePrompt(chainNode, state, stage) {
    // 宿主 API 兼容性：`app.queuePrompt` 属于前端内部接口，不同 ComfyUI 版本/嵌入式前端可能有差异。
    //   缺了就别静默——**画布上直接说明**，并指向仍然可用的路径（节点本身与「🧩 拼成一条」不受影响）。
    if (typeof app?.queuePrompt !== "function") {
        setStatus(
            chainNode,
            "⚠ 这个 ComfyUI 前端没有 app.queuePrompt ⇒ ⏩ 连跑 / 自动拼接排队用不了。" +
                "「🧩 拼成一条」（拼已跑过的段）仍可用；脚本用户见 README §7.4 的 CLI/库调用路。"
        );
        return null;
    }
    try {
        const res = await app.queuePrompt(0, 1);
        const id = res?.prompt_id ?? null;
        // 按**段号**记（不是 push）：同一段重跑时后一次覆盖前一次 ⇒ 拼接拿到的是"每段最新那一次"，
        // 顺序也天然按段号排；push 会把重跑的段算成两段、拼出一条带重复的片。
        if (id && state) state.stageIds[stage] = id;
        setStatus(chainNode, `已排队，采样中…（第 ${stage + 1} 段）`);
        return id;
    } catch (err) {
        setStatus(chainNode, "⚠ 排队失败：" + err.message);
        throw err;
    }
}


/**
 * 开始第 `stage` 段（0 起算）前把词写进目标节点。
 * 没填 `prompts` ⇒ 直接放行（老行为，一个字都不动）。
 */
function dispatchStage(chainNode, stage) {
    const blocks = splitPromptBlocks(widgetValue(chainNode, "prompts", ""));
    if (!blocks.length) return { ok: true, message: "" };
    if (stage >= blocks.length) {
        return {
            ok: false,
            message:
                `⚠ prompts 只有 ${blocks.length} 块词，第 ${stage + 1} 段没有对应词 ⇒ **没有排队**。` +
                `（要么补齐第 ${stage + 1} 块，要么把 segments 改成 ${blocks.length} 或更小。）`,
        };
    }
    const r = resolveTarget(app.graph._nodes, widgetValue(chainNode, "prompt_target", ""));
    if (!r.ok) return { ok: false, message: "⚠ " + r.message };
    const w = writePrompt(r.target, blocks[stage]);
    if (!w.ok) return { ok: false, message: "⚠ " + w.message };
    return { ok: true, message: `第 ${stage + 1}/${blocks.length} 段词 → ${describeTarget(r.target)}` };
}

/** 状态行里带上词分发结果（没有就什么都不加）。 */
function withMsg(msg) {
    return msg ? `｜ ${msg}` : "";
}

/** 开始第 `stage` 段：先分词写格，再排队。词分发失败就**不排队**（返回 null 已写状态）。 */
async function startStage(node, state, stage, label) {
    const d = dispatchStage(node, stage);
    if (!d.ok) {
        setStatus(node, d.message);
        return null;
    }
    setStatus(node, `${label}：stage=${stage} 排队中…` + withMsg(d.message));
    return queuePrompt(node, state, stage);
}

/** 拼接成片（走后端 /h3relay/concat；包内实现，不依赖外部 ffmpeg）。 */
async function concatFilm(chainNode, state, auto = false) {
    const pair = findPair(chainNode);
    const runId = pair ? String(widgetValue(pair.bridge, "run_id", "")) : "";
    const seg = Math.round(widgetValue(chainNode, "segments", 0) || 0);
    const outName = String(widgetValue(chainNode, "concat_name", "")).trim();
    const { ids, holes } = collectStageIds(state.stageIds);
    if (!ids.length && seg <= 0) {
        setStatus(
            chainNode,
            "⚠ 拼接：既没有本轮跑过的段记录，segments 也不是正数 ⇒ 不知道该拼哪几段。" +
                "把 segments 填成正数，或用 ▶/⏩ 跑过一轮再来。"
        );
        return;
    }
    setStatus(
        chainNode,
        `🧩 拼接中…（${ids.length ? `本轮 ${ids.length} 段` : `按最近 ${seg} 段落盘记录`}）` +
            `｜音轨 ${String(widgetValue(chainNode, "audio_out", "aac_256k"))}` +
            (holes.length ? `｜⚠ 第 ${holes.join("、")} 段没有本轮记录，可能漏段` : "")
    );
    if (typeof api?.fetchApi !== "function") {
        setStatus(chainNode, "⚠ 这个 ComfyUI 前端没有 api.fetchApi ⇒ 画布内拼接用不了。" +
            "脚本用户请用 tools/concat_segments.py 或直接调 relay_core（README §7.4）。");
        return;
    }
    try {
        const res = await api.fetchApi("/h3relay/concat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                prompt_ids: ids,
                chain_node_id: String(chainNode.id),
                count: seg,
                out_name: outName,
                run_id: runId,
                // 成片档：音轨（aac_256k / aac_192k / pcm_lossless）与重编码 crf。
                // 后端按这两个值决定音轨编码器与码率；默认档与老行为一致（AAC 256k）。
                audio_out: String(widgetValue(chainNode, "audio_out", "aac_256k")),
                video_crf: Math.round(widgetValue(chainNode, "video_crf", 16) ?? 16),
            }),
        });
        const data = await res.json();
        setStatus(chainNode, (data.ok ? "✅ " : "🔴 ") + (data.report || data.error || "拼接失败"));
    } catch (err) {
        setStatus(chainNode, (auto ? "⚠ 自动拼接请求失败：" : "⚠ 拼接请求失败：") + err.message);
    }
}

app.registerExtension({
    name: "H3RelayKit.Chain",

    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "H3RelayChain") return;

        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const r = origOnNodeCreated?.apply(this, arguments);
            const node = this;
            // sawMine：本轮执行里是否真的跑过本组桥/落盘。executing(null) 是全局事件，
            // 多个 Chain 组共存时，别的组跑完一轮不能推进本组的段号。
            // stageIds：**按段号**存本轮各段排队拿到的 prompt_id（拼接时按它取回每段落盘的 mp4）。
            const state = { mode: "idle", remaining: 0, sawMine: false, stageIds: [] };

            const stepDone = () => {
                if (state.mode !== "chain") return;
                if (!state.sawMine) return; // 本轮没执行过本组的桥/落盘 → 别的组的收尾，忽略
                state.sawMine = false;
                const pair = findPair(node);
                if (!pair) {
                    state.mode = "idle";
                    return;
                }
                const next = getStage(pair.bridge) + 1;
                setStage(pair.bridge, next);
                setStage(pair.save, next);
                const seg = Math.round(widgetValue(node, "segments", 0) || 0);
                const infinite = seg <= 0;
                const more = infinite || state.remaining > 1;
                if (state.remaining > 0) state.remaining -= 1;
                if (!more) {
                    state.mode = "idle";
                    setStatus(node, `✅ 连跑结束，当前段号 ${next}（已落盘，可直接继续 Approve）。`);
                    if (widgetValue(node, "auto_concat", false)) concatFilm(node, state, true);
                    return;
                }
                const d = dispatchStage(node, next);
                if (!d.ok) {
                    state.mode = "idle";
                    setStatus(node, d.message);
                    return;
                }
                setStatus(node, `连跑中：第 ${next + 1} 段排队…（剩余 ${infinite ? "∞" : state.remaining}）`
                    + withMsg(d.message));
                queuePrompt(node, state, next).catch(() => (state.mode = "idle"));
            };

            api.addEventListener("executing", ({ detail }) => {
                if (detail === null) { stepDone(); return; }
                // 记录「本组节点确实在这轮执行」：只有跑过本组桥/落盘，收尾才归本组
                if (detail.node != null && state.mode === "chain") {
                    const id = Number(detail.node);
                    const pair = findPair(node);
                    if (pair && (id === pair.bridge.id || id === pair.save.id)) {
                        state.sawMine = true;
                    }
                }
            });
            api.addEventListener("execution_error", () => {
                if (state.mode === "chain") {
                    state.mode = "idle";
                    setStatus(node, "⚠ 执行出错，连跑已停（stage_index 保持当前值，可直接重跑）。");
                }
            });
            api.addEventListener("execution_success", () => {
                // 成功事件先于 executing(null) 到达时无需处理；此处仅兜底日志
            });

            node.addWidget("button", "▶ Run（按当前段号跑一次）", null, async () => {
                const pair = findPair(node);
                if (!pair) return;
                state.mode = "idle";
                await startStage(node, state, getStage(pair.bridge), "Run");
            });

            node.addWidget("button", "✔ Approve（段号+1 并跑下一段）", null, async () => {
                const pair = findPair(node);
                if (!pair) return;
                const next = getStage(pair.bridge) + 1;
                const d = dispatchStage(node, next);
                if (!d.ok) { setStatus(node, d.message); return; }
                setStage(pair.bridge, next);            // 先确认词能写上，再推进段号
                setStage(pair.save, next);
                state.mode = "idle";
                setStatus(node, `Approve：段号推进到 ${next}，排队中…` + withMsg(d.message));
                await queuePrompt(node, state, next);
            });

            node.addWidget("button", "⏩ 连跑（按 segments 自动循环）", null, async () => {
                const pair = findPair(node);
                if (!pair) return;
                const seg = Math.round(widgetValue(node, "segments", 0) || 0);
                state.mode = "chain";
                state.remaining = seg;
                state.stageIds = [];
                setStatus(node, `连跑开始：segments=${seg <= 0 ? "∞" : seg}，首段排队中…`);
                const ok = await startStage(node, state, getStage(pair.bridge), "连跑");
                if (ok === null) state.mode = "idle";   // 词分发没过 ⇒ 别停在 chain 态
            });

            node.addWidget("button", "⏹ Stop（本轮跑完即停）", null, () => {
                if (state.mode === "chain") {
                    state.mode = "idle";
                    setStatus(node, "已请求停止：当前采样跑完后不再推进。");
                } else {
                    setStatus(node, "当前没有在连跑。");
                }
            });

            node.addWidget("button", "🧩 拼成一条（把已跑的 N 段拼成成片）", null, async () => {
                await concatFilm(node, state, false);
            });

            node.addWidget("button", "↺ Reset（段号归 0，从第 1 段重来）", null, () => {
                const pair = findPair(node);
                if (!pair) return;
                setStage(pair.bridge, 0);
                setStage(pair.save, 0);
                state.stageIds = [];
                setStatus(node, "已归 0：下一段将作为第 1 段（不续接，只落盘）。");
            });

            return r;
        };
    },
});
