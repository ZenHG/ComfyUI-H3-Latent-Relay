// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
// 第三方出处与许可见 THIRD-PARTY-NOTICES.md
// H3 Latent Relay · Chain 前端
// 在 H3RelayChain 节点上提供按钮：Run / Approve / 连跑 / Stop / Reset / 拼成一条。
// 作用：自动推进同一张图里**所有带 `stage_index` 的节点**（Chain 自己 + `STAGE_TYPES` 表：
//       拷贝桥 / 落盘 / 读上段 latent / 音频缝）的段号，免去每段手动改数字。
//
// 0.6.7 加两件事（**都默认关 = 老图行为逐位不变**）：
//   · **词分发**：`prompts` 填了词（`---` 分块）时，跑第 k 段前把第 k 块写进出词节点。
//     块数不够 ⇒ **不排队**并报错。
//   · **拼接成片**：`auto_concat` 开着 ⇒ 连跑结束自动拼；平时也可以点「🧩 拼成一条」。
//     拼接在包内实现（不需要外部 ffmpeg），画面流拷贝无损、音频按段去 priming 对齐。
//
// 0.6.12 的三处修复（前两条是**静默失效**，第三条是**行为纠正**）：
//   · 🔴 **按钮点了完全没反应**（前端 1.53+ 重写了 litegraph）：旧代码读 `group.bounding`，
//     而新版 `LGraphGroup` **没有这个属性**（只有 `boundingRect` getter 与 `getBounding()`），
//     于是 `b[0]` 抛 TypeError —— 它发生在**每个按钮回调的第一行**（findPair），
//     6 个按钮**全部**静默失效、画布上一个字都不显示。现改为跨版本兼容取包围盒，
//     取不到就跳过该分组（不抛错）。
//   · 🔴 **`prompts` 由连线提供时读错源**：`easy positive → Chain.prompts` 是常见接法，
//     此时**生效的是连线值**，而 Chain 那一格可能还留着上一版的旧词。旧代码只读本机
//     `widget.value` ⇒ **静默把旧词写进出词节点**（连跑"看着在跑、词其实没换"）。
//     现在有连线一律沿 link 取上游文本格；取不到就**报错**，绝不静默退回残留值。
//   · 🔴 **第 1 段什么都不用做**：「Latent Load」在 `stage_index == 0` 时会**自己交一个
//     「空上下文」**，桥识别后自动直通（latent 原样过、裁剪帧数输出 0）。
//     ⚠ **旁路它反而会让整个 prompt 被拒** —— 宿主提交前会把 bypass 的节点"溶解"掉、
//     把它的输入接到下游；这个节点没有 LATENT 输入 ⇒ 桥的 required `context_latent`
//     会从 prompt 里消失 ⇒ `Required input is missing`。⇒ Chain **反向接管**：
//     谁把它旁路了就自动恢复成启用。
//   · 段号推进从「桥 + 落盘」扩到**「Latent Load」**：它的 `stage_index` 语义是
//     **本段段号**（节点内按 `stage_index - 1` 取文件），老版本不推它 ⇒ 段 ≥ 1 时它恒停在 0。
//     现在**三处同步推进**。
//   · 🔴 **连跑跑完第 1 段就静默停住**（0.6.12 修）：旧代码在 `executing` 事件里读 `detail.node`，
//     而宿主 `api.js` 的派发是 `dispatchCustomEvent("executing", data.display_node || data.node)`
//     —— **detail 本身就是一个 id**，不是 `{node, display_node}` 对象 ⇒ `detail.node` 恒为
//     `undefined` ⇒ 「本轮跑过本组桥/落盘」永远记不上 ⇒ `stepDone()` 一进门就
//     `if (!sawMine) return;` ⇒ **段号永远不推进**（连跑看起来"只跑了第 1 段"）。
//     现在两种形态都认（`executingNodeId()`）。
//   · 🔴 **连跑跑完一段就静默停住，另有三处独立原因**（0.6.13 修，浏览器实测）：
//     ① 新版前端 `app.queuePrompt` 返回 **布尔 `true`**（没有 `prompt_id`）⇒ 取
//        `res?.prompt_id` 得 undefined ⇒ 旧代码 `return id` = null ⇒ 连跑 handler 的
//        `if (ok === null) state.mode = "idle"` 把状态机**静默**打回 idle（无任何提示）。
//     ② **整轮命中缓存**时（图没变、同一轮复跑）宿主**不逐节点发 `executing`**，只发一条
//        `execution_cached {nodes:[…]}` + 几条 `executed` ⇒ 「本轮跑过本组」恒判否。
//     ③ **最根本、也最隐蔽的一个：id 类型错配** —— `pairIds` 里是**字符串**
//        （新版前端 `node.id` 是字符串，实测 `pairIds: ["961","902"]`），比对却写
//        `has(Number(id))` ⇒ **恒 false**。①②是必要条件，但全被这一层挡住。
//     ⇒ 现在：排队成败只看**异常**与 `res === false`；id 一律 `String()` 归一；
//     「跑过本组」的证据源三路（`executing` / `execution_cached.nodes` / `executed`）；
//     收尾信号加了 `awaiting` 闸门（队列提交瞬间宿主会先发一条空 `executing(NULL)`），
//     且 `execution_success` 与 `executing(null)` **只认其一**（免得段号一次跳 2）。
//     自助排查：控制台 `__h3Relay.debug()` 直接看状态机内部。
//   · 所有按钮回调统一包 `guard()`：任何异常都写进 `status` 格 + 控制台，
//     **不再有"点了没反应"这种无声故障**。
//   · 按钮 widget 补 `{ serialize: false }` —— 旧写法会被序列化进 `widgets_values`，
//     让节点凭空多出 6 个 `null` 槽位（旧图槽位解读会因此错位）。
//
// 0.6.15 —— 词分发**节点化**（铁律一：UI 与 API 必须同一套实现，且必须基于节点）：
//   · `H3RelayChain` 从「无输出」变成「按自己的 `stage_index` 输出 `prompts` 的第 k 块」
//     （新输出口 `prompt` + 新输入口 `stage_index`）。把 `Chain.prompt → 出词节点.prompt`
//     接上之后，**画布手点与 API 提交图 JSON 走的是同一个节点** —— 脚本用户也拿到了词分发
//     （此前它只存在于本文件里，`docs/10 §7.4` 因此把 API 侧标成 ❌）。
//   · 本文件**保留**「把词写进目标格」的老路（老图不受影响），但在没接线时于 `status`
//     里提示一次，引导走节点化那条路。
//   · 段号推进从三处扩到**四处**（加 Chain 自己）：它决定输出第几块词。
//
// 0.6.18 —— Chain（连跑控制）的两处**静默**错误（2026-09-30 真实事故：拼接成片音画错段）：
//   · 🔴 **段号推进漏了「Audio Seam」**：旧代码手写「Chain + 桥 + 落盘 + 读上段 latent」四处，
//     而 `nodes.py` 里带 `stage_index` 的一共**五处**。漏掉的那一类（音频缝）于是永远以
//     第 1 段自居 ⇒ 连跑第 2 段时它把第 1 段的床文件 `audio_00000.safetensors`
//     **覆盖成了第 2 段的音频** ⇒ 拼接时第 1 段拿到第 2 段的音轨 ⇒ 成片音画错段。
//     ⇒ 段号推进改成**表驱动**（`STAGE_TYPES`，单一真相源），并在 status 里点明
//     「段号同步 #…」，漏驱动哪一类一眼能看出来。手写清单迟早再漏一次。
//   · 🔴 **段记录跨轮残留 / 空洞被压实**：`stageIds` 只在「⏩ 连跑开始」和「↺ Reset」清空，
//     ▶ Run / ✔ Approve / ⏭ 续跑都不清 ⇒ 跨轮残留；`collectStageIds` 又把稀疏数组**压紧**
//     ⇒ 段号→位置的映射丢掉 ⇒ **后面的段被当成前面的段拼进成片**。
//     ⇒ 记录改成 `{id, seq}`；开跑第 k 段时丢掉 `stage > k` 的记录（后段必 stale）；
//     上报带**真段号**的 `stages` 字段；**自动拼接遇空洞直接拒拼**（缺段的成片比没有成片更坏），
//     手动拼接允许但报告里点明缺哪段、哪几段来自更早一轮。
//   · 🔴 **「点了不知道点上没有」**（GG 2026-09-30 点名）：连跑是几十秒到几分钟的慢过程，
//     而本节点历史上出现过多次"点了没反应"。⇒ 三条独立通道同时表达阶段：
//     ① 节点**标题栏顶部色带 + 阶段文字**（canvas 绘制，`drawPhaseBadge`）；
//     ② `status` 格文字前的**阶段字形**（`say`）；
//     ③ **按钮文字**：点击瞬间换「⏳ … 已点击」（`flashPressed`），连跑中换带进度条的文案。
//     另加**防抖**：同一按钮 0.4 秒内重复点击只认第一次（双击 = 白烧一轮 GPU）；
//     「⏹ Stop」「↺ Reset」不防抖 —— 那是用户的刹车，任何时候都该立刻响应。
//
// 0.6.19 —— `run_id` 也改成**表驱动 + 一处改动广播到全组**（与段号同一套办法）：
//   · 🔴 问题：`run_id`（这部片子叫什么，决定段文件落 `output/relay_kit/<run_id>/`）
//     在**六类节点**上各存一份，必须一字不差。此前只靠 tooltip 写「⚠ 必须和 XX 一字不差」
//     ⇒ 改一处要手动改五处；漏改的那一处会让桥去找**另一个目录**（报错，或更坏：读到上一轮的旧段）。
//     这正是 `stage_index` 的老病，而它在 0.6.18 已经用表驱动治过 ⇒ 这里复用同一套。
//     （表在 `web/relay_kit_sync.js`；我第一版手写清单**就漏了「音频缝」**，被 8.12 机检当场抓住。）
//   · 三条设计决定（都写进了 `relay_kit_sync.js` 的模块注释）：
//     ① **空值不广播** —— 清空一格 ≠ 想把全组清空（否则误删一个字符就把整部片子的目录名清掉）；
//     ② **冲突不猜** —— 同组出现两个不同的非空名时列清单给人看，**不自动挑一个**
//        （自动挑错那次会让段文件落进错的目录，比报错难查得多）；
//     ③ **同步范围 = 同一个分组框**（一张图放两部片子是正常用法，跨组同步会把另一部改掉）。
//   · **双保险**（防某一版前端把 widget 回调吃掉 ⇒ 静默失效）：
//     ① 快路径：包 `run_id` 格的 `callback`，改一处当场广播；
//     ② 兜底：`▶ Run` / `✔ Approve` / `⏩ 连跑` / `⏭ 续跑` 这四个**会提交**的按钮，
//        在排队前跑一次 `runIdPreflight()` —— 不是静默补齐，而是**冲突直接不排队**并列出清单。
//        （现状是排队跑到桥才报"找不到文件"，那时已经白烧了几十秒。）
//   · Chain 的 `run_id` 默认是空的（可选·续跑用），而桥/落盘的默认是 `relay`
//     ⇒ **不碰 run_id 的老图一字不变**：preflight 只在"同组内有非空值"时才可能动作。
//
// 效能（0.6.13）：
//   · **全局监听只注册一份**（模块级 `CHAINS` 集合）。旧写法在每个 Chain 实例的
//     `onNodeCreated` 里各注册 3 个 `api` 监听 ⇒ 多实例时开销成倍，且**节点被删/复制后
//     监听器不会注销**（内存与回调都留在那里）。现在配 `onRemoved` 注销。
//   · `executing` 事件**每执行一个节点都会触发**：本文件只做 `Set.has(id)` 比对，
//     **不重扫全图**（桥/落盘的 id 集合在连跑开始时算一次，每段结束重建）。
//   · 分组包围盒 `groupBounds()` 提到循环外算一次：新版前端的 `getBounding()` 内部会
//     `syncBoundsFromStore()`，逐节点重算是白花的。
//
// 找节点规则：优先取与本 Chain 节点**同一个分组框**里的目标节点；
// 没有分组就全图找；找到多对则提示先分组。
// 本文件行为对标 H3-Motion-Context 的 Chain 思路，代码为独立实现。

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
// 🔴 0.6.15：只 import **还需要的**两个纯函数。
//   `resolveTarget` / `writePrompt` / `describeTarget` 随"前端写词"那条老路一起删了 ——
//   词现在由 Chain 节点的 `prompt` 输出口给（铁律一：不允许"两条路各写一遍"）。
//   `splitPromptBlocks` 仍要（前端做**词块数预检**）；`collectStageIds` 仍要（拼接取段记录）。
import { splitPromptBlocks, collectStageIds } from "./relay_kit_prompt.js";
// 🔴 0.6.19：`run_id` 跨节点同步的**纯函数**（表 + 三态裁决 + 广播规划）。
//   与 `stage_index` 的 `STAGE_TYPES` 同构 —— 那套机制 2026-09-30 治过一次
//   「手写清单漏一类 ⇒ 成片音画错段」，这里照抄，不再手写。
import {
    isRunIdType, runIdSpec, resolveRunId, planRunIdSync, describeRunIdConflict,
} from "./relay_kit_sync.js";

/** litegraph 的节点模式：0 = Always（启用），4 = Bypass（旁路）。 */
const MODE_ALWAYS = 0;
const MODE_BYPASS = 4;

/**
 * 带「本段段号」（`stage_index` widget）的节点类型 —— **单一真相源**。
 *
 * `nodes.py` 里声明了 `stage_index` 的一共五处：Chain 自己（1239）、LatentSave（552）、
 * LatentLoad（597）、CopyBridge（1476）、AudioSeam（1976）。Chain 自己由 `setStageAll`
 * 直接推进，其余四类走这张表 —— **漏掉任何一类，那个节点就会永远以第 1 段自居**。
 *
 * 🔴 为什么必须收进表里（2026-09-30 真实事故）：旧代码手写三处（桥 / 落盘 / 读上段 latent），
 *   **漏了音频缝** ⇒ 连跑第 2 段时音频缝仍以 0 号自居，把第 1 段的床文件
 *   （`audio_00000.safetensors`）**覆盖成了第 2 段的音频** ⇒ 拼接时第 1 段拿到第 2 段的音轨
 *   ⇒ **成片音画错段**。手写清单迟早再漏一次，所以改成表驱动。
 *
 * 字段：`required` = 必须**恰好 1 个**（否则连跑无从下手）；`liveOnly` = 是否只算启用中的
 *   （「Latent Load」与「Audio Seam」都要管被旁路的：前者旁路会让提交校验失败，Chain 要反向接管）。
 */
const STAGE_TYPES = [
    { type: "H3RelayCopyBridge", label: "桥", required: true, liveOnly: true,
      missing: "「Copy Bridge」" },
    { type: "H3RelayLatentSave", label: "落盘", required: true, liveOnly: true,
      missing: "「Latent Save」" },
    { type: "H3RelayLatentLoad", label: "读上段 latent", required: false, liveOnly: false },
    { type: "H3RelayAudioSeam", label: "音频缝", required: false, liveOnly: false },
];

// ─────────────────────────── 状态可视化（一眼看出「点上了没有 / 在跑 / 停了 / 错了」） ───────────────────────────
//
// 为什么需要它（GG 2026-09-30 点名：「按钮 UI 动效和视觉效果不算明显，不能让用户非常明了地知道
// 自己已经点击了」）：本节点上「点了没反应」这类问题**出现过多次**，而连跑是**几十秒到几分钟**的
// 慢过程 —— 用户必须能立刻分辨三件事：① 我这一下点上了没有；② 现在在跑还是停了；③ 正常结束还是出错。
//
// ⇒ 用**三条互相独立**的通道表达同一件事（任一通道被前端版本差异吃掉，另两条还在）：
//   ① 节点**标题栏顶部的状态色带 + 阶段文字**（canvas 绘制，见 `drawPhaseBadge`）；
//   ② `status` 格文字前的**阶段字形**（🟦/🟩/🟥…，见 `say`）；
//   ③ **按钮文字**（点击瞬间换「⏳ …已点击」，连跑中换带进度条的文案，见 `flashPressed`）。
const PHASES = {
    idle: { glyph: "⚪", color: null, label: "空闲" },
    queued: { glyph: "🟦", color: "#2563eb", label: "已排队" },
    running: { glyph: "🟩", color: "#16a34a", label: "连跑中" },
    done: { glyph: "✅", color: "#0891b2", label: "完成" },
    stopped: { glyph: "🟧", color: "#d97706", label: "已停止" },
    warn: { glyph: "⚠️", color: "#d97706", label: "需处理" },
    error: { glyph: "🟥", color: "#dc2626", label: "出错" },
};

/** litegraph 标题栏高度（跨版本取值；取不到按 30）。 */
function titleHeight() {
    try {
        return (typeof LiteGraph !== "undefined" && LiteGraph.NODE_TITLE_HEIGHT) || 30;
    } catch {
        return 30;
    }
}

/**
 * 在**标题栏**上画阶段色带 + 阶段文字。
 *
 * 为什么画标题栏：① 标题栏是 canvas 画的 —— 新版前端把节点体里的 widget 换成 **DOM 层**，
 * 画在节点体里会被盖住；② 标题栏在最上面，一眼就能看出这个节点在跑 / 停了 / 错了。
 *
 * 🔴 为什么**不用 `node.color`**（虽然那是 litegraph 官方字段）：`color` 会被**序列化进工作流文件**
 * ⇒ 改它等于悄悄改用户的图（脏标记 + 存盘后颜色留在文件里）。画布绘制零副作用。
 */
function drawPhaseBadge(ctx, node, phase) {
    const p = PHASES[phase] ?? PHASES.idle;
    const w = Number(node?.size?.[0] ?? 0);
    if (!p.color || !w) return;                    // 空闲 = 什么都不画（不打扰）
    const th = titleHeight();
    ctx.save();
    // ① 色带画在标题栏**下沿**：直边（不越节点的圆角）、又紧贴节点体上边
    //    —— 新版前端把节点体里的 widget 换成 DOM 层（从 y=0 起），画在 y<0 不会被盖住。
    ctx.fillStyle = p.color;
    ctx.fillRect(0, -5, w, 5);
    // ② 阶段文字右对齐在标题栏里；**放不下就不画**（标题长的节点上绝不叠字）。
    ctx.font = "bold 12px sans-serif";
    const badge = `${p.glyph} ${p.label}`;
    const bw = ctx.measureText(badge).width + 10;
    ctx.font = "bold 14px sans-serif";              // 与 litegraph 的 NODE_TEXT_SIZE 同档（宁大勿小）
    const tw = ctx.measureText(String(node?.title ?? "")).width;
    if (10 + tw + 6 + bw <= w) {
        ctx.font = "bold 12px sans-serif";
        ctx.textAlign = "right";
        ctx.textBaseline = "middle";
        ctx.lineWidth = 3;                          // 先描边再填充 ⇒ 任何标题底色上都读得清
        ctx.strokeStyle = "rgba(0, 0, 0, 0.7)";
        ctx.strokeText(badge, w - 6, -th / 2);
        ctx.fillStyle = "#ffffff";
        ctx.fillText(badge, w - 6, -th / 2);
    }
    ctx.restore();
}

/** 切阶段：写进 `state.phase` 并重绘节点（**只在真的变了才重绘**）。 */
function setPhase(node, state, phase) {
    const next = PHASES[phase] ? phase : "idle";
    if (state) state.phase = next;
    if (node && node.__h3Phase !== next) {
        node.__h3Phase = next;
        node.setDirtyCanvas?.(true, true);
    }
}

/**
 * 写状态行：**阶段字形前缀** + 阶段色带。状态格是单行，前缀是最省位的"一眼可辨"。
 * 其他不属于某个阶段的临时消息继续用 `setStatus`（不加前缀）。
 */
function say(node, state, phase, text) {
    setPhase(node, state, phase);
    setStatus(node, `${PHASES[phase]?.glyph ?? ""} ${text}`.trim());
}

// ─────────────────────────── 图访问（跨前端版本兼容） ───────────────────────────

/** 图上的节点表。新版前端 `LGraph` 同时有 `_nodes` 与 `nodes`（getter），旧版只有 `_nodes`。 */
function graphNodes() {
    return app?.graph?._nodes ?? app?.graph?.nodes ?? app?.rootGraph?._nodes ?? [];
}

/** 图上的分组表。同上：新版 `_groups` / `groups` 都在。 */
function graphGroups() {
    return app?.graph?._groups ?? app?.graph?.groups ?? [];
}

/** 按名字取一个 widget（找不到返回 `undefined`）。 */
function findWidget(n, name) {
    return (n?.widgets ?? []).find((x) => x.name === name);
}

function nodeCenter(n) {
    const p = n?.pos ?? [0, 0];
    const s = n?.size ?? n?.renderingSize ?? [0, 0];
    return [
        Number(p[0] ?? 0) + Number(s?.[0] ?? 0) / 2,
        Number(p[1] ?? 0) + Number(s?.[1] ?? 0) / 2,
    ];
}

/**
 * 取分组的包围盒 `[x, y, w, h]`；取不到返回 `null`。
 *
 * 🔴 跨版本兼容（0.6.12）：前端 1.53+ 重写了 litegraph，`LGraphGroup` 上**只有**
 * `boundingRect`(getter) 与 `getBounding()`，**没有 `bounding`**（旧版才有）。
 * 直接读 `g.bounding` 会得到 `undefined`，随后 `b[0]` 抛 TypeError —— 而调用链是
 * `findPair → findMemberNodes → inGroup`，即**每个按钮回调的第一行**，
 * 于是所有按钮都变成"点了没反应"（异常只在控制台）。
 *
 * ⚡ 调用方应**每次分组只调一次**（见 `nodesInSameGroup`）：新版的 `getBounding()`
 * 内部会 `syncBoundsFromStore()`，逐节点重算纯属白花。
 */
function groupBounds(g) {
    let b = null;
    try {
        if (typeof g?.getBounding === "function") b = g.getBounding();
        if (b == null && g?.boundingRect != null) b = g.boundingRect;
        if (b == null) b = g?.bounding;
    } catch (err) {
        console.warn("[H3 Relay Chain] 读分组包围盒失败（已跳过该分组）：", err);
        return null;
    }
    if (!b) return null;
    const x = Number(b[0] ?? b.x);
    const y = Number(b[1] ?? b.y);
    const w = Number(b[2] ?? b.width);
    const h = Number(b[3] ?? b.height);
    return [x, y, w, h].every(Number.isFinite) ? [x, y, w, h] : null;
}

/** 节点中心是否落在包围盒 `b` 内。`b` 由 `groupBounds()` 预先算好（避免重复取值）。 */
function inBounds(n, b) {
    const [cx, cy] = nodeCenter(n);
    return cx >= b[0] && cx <= b[0] + b[2] && cy >= b[1] && cy <= b[1] + b[3];
}

/**
 * 在 `all` 里挑出「与 Chain 同一个分组框」的那些；没有分组命中就原样返回 `all`。
 *
 * ⚡ `groupBounds(g)` 在**循环外**算一次 —— 见 `groupBounds` 的说明。
 */
function nodesInSameGroup(chainNode, all) {
    if (!all.length) return all;
    for (const g of graphGroups()) {
        const b = groupBounds(g);
        if (!b || !inBounds(chainNode, b)) continue;
        const members = all.filter((n) => inBounds(n, b));
        if (members.length) return members;
    }
    return all; // 没有分组兜底：全图
}

/** 按类型取同分组的节点。`liveOnly` = 只要启用中的（默认 true）。 */
function findMemberNodes(chainNode, typeName, { liveOnly = true } = {}) {
    const all = graphNodes().filter(
        (n) => n.type === typeName && (!liveOnly || n.mode === MODE_ALWAYS)
    );
    return nodesInSameGroup(chainNode, all);
}

/**
 * 同分组内、某一类型的「带段号」节点。
 *
 * ⚡ `nodesInSameGroup` 内部对每个分组只取一次包围盒；没命中分组就退回全图
 *   —— 那张兜底正是 `#902 落盘`、`#931 音频缝` 落在分组框**外**时还能被找到的原因。
 */
function findStageNodes(chainNode, spec) {
    return findMemberNodes(chainNode, spec.type, { liveOnly: spec.liveOnly });
}

// ─────────────────────────── 状态显示 ───────────────────────────

/** 进度条文本（**纯字符**，零渲染开销）。`ratio` 0~1。
 *  ⚠️ 宽度默认 **6 格**：它要塞进**按钮文字**里，节点窄时 10 格会被截断成半截条。 */
function bar(ratio, width = 6) {
    const r = Math.max(0, Math.min(1, Number(ratio) || 0));
    const n = Math.round(r * width);
    return "█".repeat(n) + "░".repeat(width - n);
}

/**
 * 节流：`progress` 事件**每采样步都触发**（8 步就是 8 次）。
 * 每次都写 UI 会让画布每步重绘 —— 200ms 一次足够人眼看出在动，也不拖采样。
 */
function throttle(fn, ms = 200) {
    let last = 0, timer = null, pending = null;
    return (...args) => {
        pending = args;
        const now = Date.now();
        if (now - last >= ms) { last = now; fn(...pending); return; }
        if (!timer) {
            timer = setTimeout(() => { timer = null; last = Date.now(); fn(...pending); }, ms);
        }
    };
}

/** 「⏩ 连跑」按钮的原始文字（运行中会被换成带进度的）。 */
const CHAIN_BTN = "⏩ 连跑（按 segments 自动循环）";

function setStatus(chainNode, text) {
    const w = findWidget(chainNode, "status");
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

/**
 * 按钮回调整体兜底（0.6.12）：任何异常都**写进 status 格**，而不是只在控制台里无声消失。
 * 这是「点了没反应」这类故障的根治手段 —— 以前一个 TypeError 就能让按钮看起来完全是死的。
 */
function guard(chainNode, label, fn) {
    return async (...args) => {
        try {
            return await fn(...args);
        } catch (err) {
            const msg = `⚠ ${label} 内部出错：${err?.message ?? err}`;
            setStatus(chainNode, msg);
            console.error("[H3 Relay Chain]", msg, err);
        }
    };
}

// ─────────────────────────── 段号 / 配对 ───────────────────────────

function widgetValue(n, name, fallback = "") {
    const w = findWidget(n, name);
    return w ? w.value : fallback;
}

function getStage(n) {
    const w = findWidget(n, "stage_index");
    return w ? Math.max(0, Math.round(w.value ?? 0)) : 0;
}

function setStage(n, v) {
    const w = findWidget(n, "stage_index");
    if (w) w.value = Math.max(0, Math.round(v));
}

/**
 * 确保「Latent Load」处于**启用**状态。
 *
 * 🔴 为什么第 1 段**不能**旁路它（2026-09-28 实测定案）：
 *   宿主在提交前会把 **bypass 的节点"溶解"掉**、把它的输入直接接到下游。
 *   这个节点没有 LATENT 输入 ⇒ 一旦被旁路，桥的 required `context_latent`
 *   会**从 prompt 里消失** ⇒ 整个 prompt 被
 *   `prompt_outputs_failed_validation / Required input is missing` 拒掉。
 *   （`stage_index == 0` 时该节点**自己会交一个「空上下文」**，桥识别后自动直通、不裁帧。）
 *
 * ⇒ Chain 的职责正好**反过来**：保证它启用；用户手动旁路了就替他恢复。
 * 返回被改动的节点 id 列表（给 status 用）。
 */
function ensureLoadsEnabled(pair) {
    const changed = [];
    for (const n of pair.loads ?? []) {
        if (n.mode !== MODE_ALWAYS) {
            n.mode = MODE_ALWAYS;
            n.setDirtyCanvas?.(true, true);
            changed.push(n.id);
        }
    }
    if (changed.length) app?.graph?.change?.();
    return changed;
}

/**
 * 把段号推进到**所有**带 `stage_index` 的节点（Chain 自己 + `STAGE_TYPES` 表里的每一类），
 * 并保证「Latent Load」启用。
 *
 * 🔴 表驱动（0.6.18）：旧版手写「Chain + 桥 + 落盘 + 读上段」四处，**漏了音频缝**
 *   ⇒ 连跑第 2 段时音频缝仍以 0 号自居、覆盖第 1 段的床文件 ⇒ 拼接音画错段。
 * 老图没有某一格时 `setStage` 静默跳过（不报错、不影响旧行为）。
 *
 * 返回 `{restored, driven}`：`restored` = 被恢复启用的节点 id，`driven` = 实际写了段号的节点 id
 * （给 status 与 `__h3Relay.debug()` 用 —— 漏驱动哪一类，一眼能看出来）。
 */
function setStageAll(chainNode, pair, v) {
    setStage(chainNode, v);
    for (const n of pair.stageNodes ?? []) setStage(n, v);
    return { restored: ensureLoadsEnabled(pair), driven: (pair.stageNodes ?? []).map((n) => n.id) };
}

// ─────────────────────────── run_id 同步（0.6.19） ───────────────────────────
// 判断逻辑全在 `relay_kit_sync.js`（纯函数、离线可测）；这里只做**画布侧**三件事：
// 收集同组快照 / 把值写下去 / 把结果说给人听。

/** 广播进行中标志：我们自己写 widget 时压住回调，防自触发回环。 */
let RUN_ID_SYNCING = false;

/**
 * 同组内所有带 `run_id` 的节点快照（**同一分组框**，没有分组就全图）。
 *
 * 范围与段号推进同源（都走 `nodesInSameGroup`）：一张图里放两部片子是正常用法
 * （`stage_index` 也是按分组隔离的），跨组同步会把另一部片子的目录名改掉。
 */
function runIdMembers(originNode) {
    const all = graphNodes().filter((n) => isRunIdType(n.type));
    return nodesInSameGroup(originNode, all)
        .map((n) => ({ id: n.id, type: n.type, runId: widgetValue(n, "run_id", "") }));
}

/** 按 id 找回节点（新版前端 id 是字符串、旧版是数字 ⇒ 一律 `String()` 归一）。 */
function nodeById(id) {
    return graphNodes().find((n) => String(n.id) === String(id));
}

/** 冲突提示里的节点称呼：「落盘 #902」。 */
function runIdNodeName(id) {
    const n = nodeById(id);
    if (!n) return `#${id}`;
    return `${runIdSpec(n.type)?.label ?? n.type} #${n.id}`;
}

/** 把广播规划真正写下去。返回被改动的 id 列表（便于排查"到底同步了谁"）。 */
function applyRunIdPlan(plan) {
    const driven = [];
    for (const id of plan.targets ?? []) {
        const n = nodeById(id);
        const w = n ? findWidget(n, "run_id") : null;
        if (!w) continue;                       // 节点已被删 / 没有这一格 ⇒ 跳过，不影响其余
        w.value = plan.value;
        n.setDirtyCanvas?.(true, true);
        driven.push(id);
    }
    if (driven.length) app?.graph?.change?.();  // 标记图已改（触发前端的"未保存"提示）
    return driven;
}

/**
 * 找同组的 Chain 状态（用来把提示写进那个节点的 `status` 格）。
 *
 * 🔴 别用 `nodesInSameGroup(n, [s.chainNode]).length` 当判据（0.6.19 修）：
 *    该函数在**没有分组命中**时会兜底返回**入参本身**（`return all`）⇒ 传 `[chainNode]`
 *    进去，长度**恒 ≥ 1** ⇒ 判据恒真 ⇒ 永远命中 `CHAINS` 里的**第一个** Chain。
 *    一张图放两部片子（两组）时，提示会写到**另一部片子的 Chain** 上 ——
 *    与「同步范围 = 同一个分组框」这条设计决定直接矛盾（那正是本机制要防的越界）。
 *    ⇒ 同组判定必须**双向显式**（两个节点都落在同一个分组框内）。
 *
 * 兜底：没有任何分组框、且图上只有**一条** Chain 时无歧义 ⇒ 仍写它的 status
 *      （否则单链用户的提示会掉进控制台）；多条 Chain 又分不出组 ⇒ 返回 `null`，
 *      由 `notifyRunId` 退到控制台（**不猜**）。
 */
function chainStateNear(n) {
    const live = CHAINS.filter((s) => s?.chainNode);
    for (const s of live) {
        if (s.chainNode === n) return s;
        for (const g of graphGroups()) {
            const b = groupBounds(g);
            if (b && inBounds(n, b) && inBounds(s.chainNode, b)) return s;
        }
    }
    return live.length === 1 ? live[0] : null;
}

/**
 * 同步范围：`"group"` = 命中了分组框；`"graph"` = 没命中 ⇒ 退化为**全图**
 * （与段号推进同一套兜底语义 —— 但 run_id 改错的代价是**段文件落进另一部片子的目录**，
 *  所以这一档必须**明说**，不能只写在代码注释里）。
 */
function runIdScope(originNode) {
    for (const g of graphGroups()) {
        const b = groupBounds(g);
        if (b && inBounds(originNode, b)) return "group";
    }
    return "graph";
}

/** 范围说明的尾巴（命中分组就说"本组"，否则**点明是全图**）。 */
function scopeNote(scope) {
    return scope === "group" ? "" : "；⚠ 没找到分组框 ⇒ 范围是**全图**（若这张图上还有别的片子，请先画分组框）";
}

/**
 * 把一句话说给用户：**优先写进同组 Chain 的 `status` 格**（用户已经在看那里），
 * 同组没有 Chain 就退到控制台 —— 绝不静默吞掉。
 */
function notifyRunId(originNode, phase, text) {
    const st = chainStateNear(originNode);
    if (st?.chainNode) say(st.chainNode, st, phase, text);
    else console.info("[H3 Relay Chain] " + text);
}

/**
 * 快路径：某格的 `run_id` 被改 ⇒ 非空就广播给同组其余节点。
 *
 * 为什么读回调的**第一个参数**而不是 `widget.value`：ComfyUI 的 widget 回调约定首参即新值；
 * 而"赋值 vs 回调"的先后在个别前端版本里不一样，读 `widget.value` 有拿到**上一个值**的风险
 * （那会把旧名广播出去）。首参为 `null/undefined` 时才回读 widget。
 */
function broadcastRunId(originNode, value) {
    if (RUN_ID_SYNCING) return null;            // 我们自己写下去的那一次，不递归
    const members = runIdMembers(originNode);
    const plan = planRunIdSync(members, originNode.id, value);
    if (plan.reason === "empty") {
        // 空值不扩散；但"这一格空了、别处还有名字"这件事不能无声无息。
        const verdict = resolveRunId(members);
        if (verdict.state === "ok") {
            notifyRunId(originNode, "warn",
                `这一格清空了，但同组还有 ${verdict.ids.length} 个节点是「${verdict.value}」`
                + " ⇒ 名字不一致会找不到段文件。要清就全部清，要留就填回同一个名字。");
        }
        return null;
    }
    if (plan.reason === "none") return null;    // 已经全一致：不刷屏
    RUN_ID_SYNCING = true;
    let driven = [];
    try {
        driven = applyRunIdPlan(plan);
    } finally {
        RUN_ID_SYNCING = false;
    }
    const scope = runIdScope(originNode);
    notifyRunId(originNode, scope === "group" ? "done" : "warn",
        `\`run_id\` 已统一为「${plan.value}」（同步 ${driven.length} 个节点${scopeNote(scope)}）。`);
    return driven;
}

/**
 * 兜底闸：四个**会提交**的按钮在排队前调一次，返回 `true` 才允许排队。
 *
 * ① **冲突一律拦住** —— run_id 不一致时跑起来一定坏（桥去错的目录找段文件）；
 *    拦在排队前，比跑到桥再报错省几十秒，也免得用户以为是模型的问题。
 * ② 唯一非空但还有空格 ⇒ **补齐**（用户只需要改一处）。
 * ③ 全空 ⇒ 拦（后端 `_stage_path` 本来就会 raise「run_id 不能为空」，提前把话说清）。
 */
function runIdPreflight(node, state) {
    const members = runIdMembers(node);
    if (!members.length) return true;           // 图上没有带 run_id 的节点 ⇒ 不管
    const verdict = resolveRunId(members);
    if (verdict.state === "conflict") {
        say(node, state, "warn", describeRunIdConflict(verdict, runIdNodeName));
        return false;
    }
    if (verdict.state === "empty") {
        say(node, state, "warn", "同组的 `run_id` 全是空的 ⇒ 段文件没有目录可落"
            + "（后端会直接报「run_id 不能为空」）。请在桥 / 落盘 / 读上段 latent / 裁重叠"
            + " / 本节点任一处填上这部片子的名字。");
        return false;
    }
    if (verdict.empty.length) {                 // 唯一非空 + 还有空格 ⇒ 补齐
        const driven = applyRunIdPlan(planRunIdSync(members, null, verdict.value));
        if (driven.length) {
            const scope = runIdScope(node);
            notifyRunId(node, scope === "group" ? "done" : "warn",
                `\`run_id\` 还没填的 ${driven.length} 个节点已补齐为「${verdict.value}」`
                + `${scopeNote(scope)}。`);
        }
    }
    return true;
}

/**
 * 给一个节点的 `run_id` 格挂上同步回调（幂等）。
 *
 * 包一层而不是**换掉**原回调：ComfyUI 自己也在这条链上挂了东西
 * ⇒ 换掉等于把它们丢了。
 */
function hookRunIdWidget(node) {
    if (!isRunIdType(node?.type)) return false;
    const w = findWidget(node, "run_id");
    if (!w) return false;                       // widgets 还没建好 ⇒ 交给调用方重试
    if (w.__h3RunIdHook) return true;           // 幂等：重复挂钩会让一次改动触发 N 次广播
    w.__h3RunIdHook = true;
    const orig = w.callback;
    w.callback = function (value, ...rest) {
        const r = typeof orig === "function" ? orig.apply(this, [value, ...rest]) : undefined;
        if (!RUN_ID_SYNCING) {
            try {
                const now = value == null ? widgetValue(node, "run_id", "") : value;
                broadcastRunId(node, now);
            } catch (err) {
                // 同步失败**不许影响这一格的值** —— 那是用户刚敲进去的东西。
                console.warn("[H3 Relay Chain] run_id 同步出错（这一格的值不受影响）：", err);
            }
        }
        return r;
    };
    return true;
}

function findPair(chainNode) {
    const found = {};
    for (const spec of STAGE_TYPES) found[spec.type] = findStageNodes(chainNode, spec);
    const bad = STAGE_TYPES.filter((s) => s.required && found[s.type].length !== 1);
    if (bad.length) {
        setStatus(chainNode, pairError(found, bad));
        return null;
    }
    const stageNodes = [];
    for (const spec of STAGE_TYPES) stageNodes.push(...found[spec.type]);
    return { bridge: found.H3RelayCopyBridge[0], save: found.H3RelayLatentSave[0],
             loads: found.H3RelayLatentLoad, stageNodes };
}

/**
 * 配对失败时给用户的话。区分「图里就没有」与「有但被旁路/静音」——
 * 后者是最常见的误操作（照老文档把桥也旁路了），提示必须不一样，
 * 否则用户会去翻分组，而真正要做的只是按一下 Ctrl+B。
 */
function pairError(found, bad) {
    const counts = STAGE_TYPES.filter((s) => s.required)
        .map((s) => `${s.label}×${found[s.type].length}`).join(" / ");
    let msg = `⚠ 找到 ${counts}，需要**恰好各 1 个且都启用**。`;
    const mutedList = bad.map((s) => {
        const ids = graphNodes()
            .filter((n) => n.type === s.type && n.mode !== MODE_ALWAYS).map((n) => n.id);
        return ids.length ? `${s.label} #${ids.join("、")}` : "";
    }).filter(Boolean);
    if (mutedList.length) {
        msg += `\n  · 被旁路/静音的：${mutedList.join(" / ")}` +
            ` ⇒ 选中它们按 Ctrl+B 恢复。（第 1 段**不需要**旁路桥：它会自己直通。）`;
    }
    const missing = bad.filter((s) => !graphNodes().some((n) => n.type === s.type))
        .map((s) => s.missing).filter(Boolean);
    if (missing.length) {
        msg += `\n  · 图里还缺：${missing.join("、")}` +
            ` ⇒ 请把 Chain、桥、落盘放进**同一个分组框**（右键 → 添加分组）。`;
    }
    return msg;
}

// ─────────────────────────── 排队 / 词分发 ───────────────────────────

async function queuePrompt(chainNode, state, stage) {
    // 宿主 API 兼容性：`app.queuePrompt` 属于前端内部接口，不同 ComfyUI 版本/嵌入式前端可能有差异。
    //   缺了就别静默——**画布上直接说明**，并指向仍然可用的路径（节点本身与「🧩 拼成一条」不受影响）。
    if (typeof app?.queuePrompt !== "function") {
        say(
            chainNode, state, "warn",
            "这个 ComfyUI 前端没有 app.queuePrompt ⇒ ⏩ 连跑 / 自动拼接排队用不了。" +
                "「🧩 拼成一条」（拼已跑过的段）仍可用；脚本用户见 docs/10 §7.4 的 CLI/库调用路。"
        );
        return null;
    }
    try {
        const res = await app.queuePrompt(0, 1);
        const id = typeof res === "string" ? res : (res?.prompt_id ?? null);
        // 按**段号**记（不是 push）：同一段重跑时后一次覆盖前一次 ⇒ 拼接拿到的是"每段最新那一次"，
        // 顺序也天然按段号排；push 会把重跑的段算成两段、拼出一条带重复的片。
        // 🔴 0.6.18：记录是 `{id, seq}`（`seq` = 属于第几轮）—— 拼接时用它标"这一段来自更早一轮"。
        if (id && state) state.stageIds[stage] = { id, seq: state.runSeq };
        say(chainNode, state, "queued", `已排队，采样中…（第 ${stage + 1} 段）`);
        // 🔴 0.6.13 修（2026-09-28 实测：新版前端 app.queuePrompt 返回 **true**，
        //   旧版返回 {prompt_id}，都没有统一形状）：旧代码 `return id` ⇒ boolean 返回时
        //   恒为 null ⇒ 连跑 handler 的 `if (ok === null) state.mode = "idle"` 把状态机
        //   **静默**打回 idle ⇒ 第 1 段跑完就永远停住（status 冻结、无任何报错）。
        //   排队失败的信号是**异常**（下方 catch 已转 ⚠ status）；`res === false` 也算失败。
        //   拿不到 prompt_id 就不记 stageIds ⇒ 拼接自动走「按最近 N 段落盘记录」的兜底路。
        if (res === false) {
            say(chainNode, state, "error", "排队被宿主拒了（app.queuePrompt 返回 false）⇒ 没有排队。");
            return null;
        }
        // 排队**成功** ⇒ 本组进入「等一轮跑完」态：`stepDone()` 只认置位后的收尾信号，
        // 免得队列提交瞬间宿主先发的空事件（实测 `executing(NULL)` 会紧跟着来）把段号误推。
        if (state) state.awaiting = true;
        return id ?? "queued";
    } catch (err) {
        say(chainNode, state, "error", "排队失败：" + err.message);
        throw err;
    }
}

/**
 * 沿一条 link 找上游节点，从它身上取一个文本格的值。取不到返回 `{ok:false, message}`。
 * 新版前端 `graph.links` 是 Map、旧版是数组/对象，且两代都有 `getLink(id)` ⇒ 三路兜底。
 */
function upstreamText(linkId) {
    const graph = app?.graph;
    const link = graph?.getLink?.(linkId) ?? graph?.links?.get?.(linkId) ?? graph?.links?.[linkId];
    if (!link) return { ok: false, message: `找不到连线 ${linkId}（宿主前端接口可能变了）` };
    const origin = graph?.getNodeById?.(link.origin_id)
        ?? graphNodes().find((n) => String(n.id) === String(link.origin_id));
    if (!origin) return { ok: false, message: `找不到上游节点 #${link.origin_id}` };
    const strs = (origin.widgets ?? []).filter((w) => typeof w?.value === "string");
    let pick = null;
    for (const name of ["positive", "prompt", "text", "string"]) {
        pick = strs.find((w) => String(w.name) === name);
        if (pick) break;
    }
    if (!pick && strs.length === 1) pick = strs[0]; // 上游只有一格文本 ⇒ 无歧义，就用它
    if (!pick) {
        const names = strs.map((w) => String(w.name)).join("、") || "（一个文本格都没有）";
        return { ok: false, message: `上游 #${origin.id}（${origin.type}）上没有可判定的词格（候选：${names}）` };
    }
    return { ok: true, text: String(pick.value ?? ""), from: `#${origin.id} ${origin.type}.${pick.name}` };
}

/**
 * Chain 的 `prompt` 输出口有没有接线。
 *
 * 🔴 0.6.15 起「第 k 段喂第 k 块词」是**节点能力**：Chain 按自己的 `stage_index`
 * 从 `prompts` 取出第 k 块，从 `prompt` 输出口交出去。
 * 把 `Chain.prompt → 出词节点.prompt` 接上之后，**画布手点与 API 提交图 JSON
 * 走的是同一个节点**（铁律一：UI 与 API 必须同一套实现）。
 *
 * 没接线时下面那条老路（前端把词写进目标格）仍然会兜住 —— 老图不受影响；
 * 但那条路**只有画布能用**，脚本用户拿不到 ⇒ 在 status 里点一次，让人知道该接哪根线。
 */
function promptOutputLinked(chainNode) {
    const out = (chainNode.outputs ?? []).find((o) => o.name === "prompt");
    return !!(out && out.links && out.links.length);
}

/** 没接线时的一次性提示（同一实例只提示一次，免得每段都刷屏）。 */
function nodeHintOnce(state, chainNode) {
    if (!state || state.hinted || promptOutputLinked(chainNode)) return "";
    state.hinted = true;
    return "｜ ⚠ 建议接线：把本节点 `prompt` 输出接到出词节点的 `prompt` 输入" +
        "（这样脚本提交 JSON 也能自动换词；现在这条老路只有画布能用）";
}

/**
 * 取**实际会参与执行**的 `prompts` 文本。
 *
 * 🔴 为什么不能只读 `widget.value`（0.6.12 修）：`prompts` 可以**被转成输入并连线**
 * （典型接法：`easy positive` → Chain.prompts）。此时提交给宿主的是**连线值**，
 * 而 `widget.value` 只是那一格自己的残留文本 —— 两者可以**完全不同**。
 * 读错源的后果不是报错，而是**静默把旧词写进出词节点**（连跑"看着在跑、词其实没换"）。
 * ⇒ 有连线时一律沿 link 取上游文本；取不到就**报错**，绝不静默退回本机残留值。
 */
function readPrompts(chainNode) {
    const inp = (chainNode.inputs ?? []).find((i) => i.name === "prompts");
    if (!inp || inp.link == null) {
        return { ok: true, text: String(widgetValue(chainNode, "prompts", "") ?? "") };
    }
    return upstreamText(inp.link);
}

/**
 * 检查这一段该用的词是否就绪 —— **不再把词写进任何 widget**。
 *
 * 🔴 0.6.15 起「第 k 段喂第 k 块词」是 **Chain 节点的能力**（节点按 `stage_index` 从
 * `prompts` 取第 k 块，从 `prompt` 输出口交给下游）。前端那条"替你写进目标格"的老路
 * **已删除** —— 保留它就会有两套实现（铁律一禁止"两条路各写一遍"）。
 *
 * ⇒ 因此这里只做两件事：**① 没接线就直接拦下**（否则词根本没地方去，会静默不换词）；
 *   **② 段号超出词块数就拦下**（不排队，绝不静默复用上一块）。
 *
 * 返回 `{ok, message}`；`ok=false` 时调用方**不排队**。
 */
function checkStagePrompt(chainNode, stage) {
    if (!promptOutputLinked(chainNode)) {
        return {
            ok: false,
            message: "⚠ 本节点的 `prompt` 输出**没有接线** ⇒ 词分发不会生效。\n"
                + "0.6.15 起词由**节点**输出（前端不再替你写格子）：把 `Chain.prompt` "
                + "连到出词节点的 `prompt` 输入即可。\n"
                + "（只跑一段、不需要换词的话，把 `prompts` 留空就不会拦你。）",
        };
    }
    const src = readPrompts(chainNode);
    if (!src.ok) {
        return {
            ok: false,
            message: `⚠ prompts 读不到：${src.message}\n`
                + "⇒ 把词**直接粘进 Chain 的 `prompts` 格**（最稳），"
                + "或让上游节点带一个 positive / prompt / text 文本格。",
        };
    }
    const blocks = splitPromptBlocks(src.text);
    if (!blocks.length) return { ok: true, message: "" };   // 留空 = 不换词
    if (stage >= blocks.length) {
        return {
            ok: false,
            message: `⚠ prompts 只有 ${blocks.length} 块词，第 ${stage + 1} 段没有对应词 ⇒ **没有排队**。`
                + `（要么补齐第 ${stage + 1} 块，要么把 segments 改成 ${blocks.length} 或更小。）`,
        };
    }
    return {
        ok: true,
        message: `第 ${stage + 1}/${blocks.length} 段词由节点输出`
            + (src.from ? `（词源 ${src.from}）` : ""),
    };
}

/** 往一个 widget 写值（找不到就静默跳过 —— 老图可能没这一格）。 */
function setWidgetValue(n, name, value) {
    const w = findWidget(n, name);
    if (!w) return false;
    w.value = value;
    n.setDirtyCanvas?.(true, true);
    return true;
}

/**
 * 给用户一个**明显**提示（拼接是"后台做完"的事，只写 status 容易被错过）。
 *
 * 各版本前端的提示 API 不一样 ⇒ **逐级退**，而不是绑死一个：
 * 新版 `extensionManager.toast` → 旧版 `ui.dialog` → 都没有就只剩
 * `concat_result` 格 + status（**仍不会静默**，因为那两处一定会写）。
 */
function notifyUser(summary, detail, severity = "success") {
    try {
        const em = app?.extensionManager;
        if (typeof em?.toast?.add === "function") {
            em.toast.add({ severity, summary, detail, life: 10000 });
            return true;
        }
        if (typeof app?.ui?.dialog?.show === "function") {
            app.ui.dialog.show(`${summary}\n${detail}`);
            return true;
        }
    } catch (err) {
        console.warn("[H3 Relay Chain] 提示 API 不可用（不影响拼接结果）：", err);
    }
    return false;
}

/** 状态行里带上词检查结果（没有就什么都不加）。 */
function withMsg(msg) {
    return msg ? `｜ ${msg}` : "";
}

/**
 * 开跑第 `stage` 段 ⇒ **丢掉所有 stage > k 的段记录**，返回被丢掉的段号（1 起算）。
 *
 * 为什么：那些记录是"上一轮跑到更后面"留下的，而**续接链上重跑前段 ⇒ 后段必然 stale**
 * （内容已经不是那条接续链了）。不丢就会拼出"第 1 段是新的、第 2 段是旧的"的片 —— 而且
 * **一声不响**（这正是 2026-09-30 那次段序错乱的同一类病）。
 * 保留下来的 k 之前那几段仍然有效（它们没被重跑）⇒ 从第 3 段起连跑不必重跑前两段。
 *
 * `length` 赋值即截断；稀疏数组留下的空洞交给 `collectStageIds` 报 `holes`。
 * 返回值给调用方写进 status —— 丢了什么要说出来，别让用户到拼接时才发现缺段。
 */
function dropStaleStages(state, stage) {
    const dropped = [];
    for (let k = state.stageIds.length - 1; k > stage; k -= 1) {
        if (state.stageIds[k]) dropped.push(k + 1);
    }
    state.stageIds.length = Math.min(state.stageIds.length, stage + 1);
    return dropped.reverse();
}

/**
 * 开始第 `stage` 段：先丢陈旧段记录，再保证读节点启用，再查词，最后排队。
 * 任一步没过都**不排队**。
 */
async function startStage(node, state, stage, label, pair) {
    const dropped = dropStaleStages(state, stage);
    const restored = ensureLoadsEnabled(pair);
    const d = checkStagePrompt(node, stage);
    if (!d.ok) {
        say(node, state, "warn", d.message);
        return null;
    }
    const note = restored.length
        ? `｜ 已恢复「Latent Load」#${restored.join("、")} 为启用（旁路它会导致提交校验失败）`
        : "";
    const dropNote = dropped.length
        ? `｜已丢弃第 ${dropped.join("、")} 段的旧记录（本轮从第 ${stage + 1} 段重跑）`
        : "";
    say(node, state, "queued",
        `${label}：stage=${stage} 排队中…` + withMsg(d.message) + note + dropNote
        + stageNote(pair) + nodeHintOnce(state, node));
    return queuePrompt(node, state, stage);
}

/** 状态行里点明**实际被驱动了段号的节点**（漏驱动哪一类，一眼能看出来）。 */
function stageNote(pair) {
    const ids = (pair?.stageNodes ?? []).map((n) => `#${n.id}`);
    return ids.length ? `｜段号同步 ${ids.join("、")}` : "";
}

/** 拼接成片（走后端 /h3relay/concat；包内实现，不依赖外部 ffmpeg）。 */
async function concatFilm(chainNode, state, auto = false) {
    const pair = findPair(chainNode);
    const runId = pair ? String(widgetValue(pair.bridge, "run_id", "")) : "";
    const seg = Math.round(widgetValue(chainNode, "segments", 0) || 0);
    const outName = String(widgetValue(chainNode, "concat_name", "")).trim();
    const { stages, holes } = collectStageIds(state.stageIds);
    const ids = stages.map((s) => s.id);
    if (!ids.length && seg <= 0) {
        say(
            chainNode, state, "warn",
            "拼接：既没有本轮跑过的段记录，segments 也不是正数 ⇒ 不知道该拼哪几段。" +
                "把 segments 填成正数，或用 ▶/⏩ 跑过一轮再来。"
        );
        return;
    }
    // 哪些段的记录来自**更早一轮**（同一次拼接里混轮 ⇒ 段与段之间可能不接续）。
    const newest = stages.reduce((m, s) => Math.max(m, s.seq), 0);
    const older = stages.filter((s) => s.seq !== newest).map((s) => s.stage + 1);
    // 🔴 0.6.18：**自动拼接遇空洞直接拒拼** —— 缺段的成片比没有成片更坏（会被当成成品发出去）。
    //   手动「🧩 拼成一条」仍允许拼（用户可能就是要拼现有的这几段），但报告里必须点明缺哪段。
    if (auto && ids.length && holes.length) {
        say(chainNode, state, "warn",
            `自动拼接已跳过：第 ${holes.join("、")} 段没有本轮记录 ⇒ 拼出来会缺段。`
            + "（要拼现有段请点「🧩 拼成一条」，它会列出缺哪几段。）");
        return;
    }
    say(
        chainNode, state, "running",
        `🧩 拼接中…（${ids.length ? `本轮 ${ids.length} 段` : `按最近 ${seg} 段落盘记录`}）` +
            `｜音轨 ${String(widgetValue(chainNode, "audio_out", "aac_256k"))}` +
            (holes.length ? `｜⚠ 缺第 ${holes.join("、")} 段` : "") +
            (older.length ? `｜⚠ 第 ${older.join("、")} 段来自更早一轮` : "")
    );
    if (typeof api?.fetchApi !== "function") {
        say(chainNode, state, "warn",
            "这个 ComfyUI 前端没有 api.fetchApi ⇒ 画布内拼接用不了。" +
            "脚本用户请用 tools/concat_segments.py 或直接调 relay_core（docs/10 §7.4）。");
        return;
    }
    try {
        const res = await api.fetchApi("/h3relay/concat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                // 0.6.18：带**真段号**上报（段号与位置永不错位）；`prompt_ids` 保留给老后端。
                stages: stages.map((s) => ({ stage: s.stage, prompt_id: s.id, seq: s.seq })),
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
        // 🔴 status 是**单行** widget：多行 report 塞进去会被压扁、成片路径反而看不见。
        // 所以：路径进**专用格** `concat_result`（不会被状态消息冲掉、可选中复制），
        // 完整报告进控制台，再给一次**明显提示**（toast）。
        const rep = String(data.report || data.error || "拼接失败");
        const first = rep.split("\n").map((s) => s.trim()).filter(Boolean)[0] || rep;
        const resultText = data.ok ? (data.out || "(路径见控制台)") : `失败：${first}`;
        setWidgetValue(chainNode, "concat_result", resultText);
        say(chainNode, state, data.ok ? "done" : "error",
            data.ok ? `拼接成功：${resultText}` : first);
        notifyUser(data.ok ? "🎬 拼接完成" : "⚠ 拼接失败",
                   data.ok ? `成片：${resultText}` : first,
                   data.ok ? "success" : "error");
        console.info("[H3 Relay Chain] 拼接完整报告：\n" + rep);
    } catch (err) {
        setWidgetValue(chainNode, "concat_result", `失败：${err.message}`);
        say(chainNode, state, "error",
            (auto ? "自动拼接请求失败：" : "拼接请求失败：") + err.message);
    }
}

/**
 * 开始一轮连跑（「⏩ 连跑」与「⏭ 续跑」共用同一段逻辑，只有文案不同）。
 *
 * 返回 `true` = 已成功排队；`false` = 被前置检查拦下（**没有排队**）。
 */
async function startChainRun(node, state, pair, label = "连跑") {
    const seg = Math.round(widgetValue(node, "segments", 0) || 0);
    // 🔴 0.6.14 预检：有限段模式下，把会跑到的**最后一段**提前验词——
    //   别跑到一半才发现词不够（首段有词、末段没有时旧代码会白跑前几段）。
    if (seg > 0) {
        const src = readPrompts(node);
        if (src.ok) {
            const blocks = splitPromptBlocks(src.text);
            const start = getStage(pair.bridge);
            const last = start + seg - 1;
            if (blocks.length && last >= blocks.length) {
                const fit = Math.max(1, blocks.length - start);
                say(node, state, "warn",
                    `prompts 只有 ${blocks.length} 块词，连跑会跑到第 ${last + 1} 段 ⇒ 不够。` +
                    `要么补齐到 ${last + 1} 块，要么把 segments 改成 ${fit}。`);
                return false;
            }
        }
        // src 读不到（连线情形）不拦：startStage 里会给出更具体的指引。
    }
    state.mode = "chain";
    state.remaining = seg;
    // 🔴 0.6.18：**不再清空** `stageIds`。旧代码在这里 `= []` ⇒ 从第 3 段开始连跑（前两段已跑过）
    //   会把第 1、2 段的记录一起抹掉 ⇒ 拼出来的片只有后半段。陈旧的记录由 `startStage` 的
    //   "丢掉 stage > k" 规则负责 —— 它保住了 k 之前**仍然有效**的那几段。
    state.runSeq += 1;
    state.resetPair();          // pairIds 与 pairReady 必须成对重置（只清一个 ⇒ 缓存永不失效）
    state.sawMine = false;
    state.awaiting = false;   // 由 queuePrompt 成功后再置位
    say(node, state, "running", `${label}开始：segments=${seg <= 0 ? "∞" : seg}，首段排队中…`);
    state.onStageChanged?.();          // 按钮立刻变「⏳ 连跑中 …」（明显反馈）
    let ok = null;
    try {
        ok = await startStage(node, state, getStage(pair.bridge), label, pair);
    } catch {
        // 排队抛错（`queuePrompt` 已写 ⚠ status）—— 这里必须**把模式收回 idle**：
        // 否则状态机卡在 chain 态，之后每个按钮都被「正在连跑中」挡住，用户只能刷新页面。
        ok = null;
    }
    if (ok === null) { state.mode = "idle"; state.onStageChanged?.(); }   // 词分发没过 ⇒ 别停在 chain 态
    return ok !== null;
}

/**
 * 把连跑进度画到「⏩ 连跑」按钮的**文字**上（明显反馈 + 辨识度）。
 *
 * 为什么画在按钮上而不是 `status` 格：`status` 要留给"排队中 / 词分发 / 报错"这类
 * **事件消息**，而进度是**周期性**的 —— 两者写同一个格子会互相覆盖。
 * 按钮文字是独立显示位，而且运行中它一眼就能看出"现在在跑、跑到第几段"。
 *
 * ⚡ 效能：**只在文字真的变了才 `setDirtyCanvas`**（否则每步采样都重绘画布）。
 */
function refreshChainLabel(state, node) {
    const w = state?.btns?.[CHAIN_BTN];
    if (!w) return;
    let text = CHAIN_BTN;
    if (state.mode === "chain") {
        const seg = Math.round(widgetValue(node, "segments", 0) || 0);
        const doneN = seg > 0 ? Math.max(0, seg - state.remaining) : 0;
        const pos = seg > 0 ? `${bar(doneN / seg)} ${doneN}/${seg}` : "∞";
        text = `⏳ 连跑中 ${pos}${state.lastProgress ? " · " + state.lastProgress : ""}`;
    }
    if (w.label === text) return;
    w.label = text;
    node.setDirtyCanvas?.(true, false);
}

/**
 * 点击反馈：**立刻**把按钮文字换成「⏳ … 已点击」，让"我到底点上了没有"当场有答案。
 *
 * 用**文字比对**决定是否还原：期间状态机可能已经改过这句（如「⏩ 连跑中 ██ 2/5 · 采样 3/8」），
 * 只有文字仍是我们写的那句时才还原 —— **绝不覆盖状态机的输出**。
 */
function flashPressed(node, w, label) {
    if (!w) return;
    const token = `⏳ ${label} · 已点击`;
    w.label = token;
    node.setDirtyCanvas?.(true, false);
    setTimeout(() => {
        if (w.label !== token) return;          // 已被状态机接管 ⇒ 不抢
        w.label = label;
        node.setDirtyCanvas?.(true, false);
    }, 700);
}

/**
 * 从 `executing` 事件的 detail 里取出节点 id。
 *
 * 🔴 宿主 `api.js` 的派发是 `dispatchCustomEvent("executing", data.display_node || data.node)`
 * —— 传出来的**就是一个 id**（字符串或数字），**不是 `{node, display_node}` 对象**。
 * 旧代码写的是 `detail.node` ⇒ 恒为 `undefined` ⇒ 「本轮跑过本组节点」永远记不上 ⇒
 * **连跑跑完第 1 段就静默停住**（`stepDone` 里 `if (!sawMine) return;` 直接返回）。
 * 这里两种形态都认，免得跟着前端版本漂。
 */
function executingNodeId(detail) {
    if (detail == null) return null;
    if (typeof detail === "object") return detail.display_node ?? detail.node ?? null;
    return detail;
}

// ─────────────────── 全局事件：所有 Chain 实例共享一份监听 ───────────────────
//
// ⚡ 旧写法在每个实例的 `onNodeCreated` 里各注册 3 个 `api` 监听：
//   · 多实例时开销成倍（`executing` 每个节点都触发，N 个实例 = N 倍回调）；
//   · 节点被删除/复制后**监听器不会注销**，回调与闭包一直留在那里。
// 现在模块级只注册一次，实例通过 `CHAINS` 集合进出（配 `onRemoved` 注销）。

/** 活着的 Chain 实例状态集合。 */
const CHAINS = new Set();
let listenersBound = false;

// 🔴 0.6.13：本前端**是否发 `execution_success`**（一条就够全轮收尾）。
//   发了就只认它 —— 否则 `execution_success` 与 `executing(null)` 会各触发一次
//   `stepDone()` ⇒ 段号一次跳 2、拼接漏段。两者取其一，绝不双认。
let hostSendsSuccess = false;

// 诊断出口（**只读快照**，不参与逻辑）：浏览器控制台敲 `__h3Relay.debug()` 就能看到
// 每个 Chain 组的状态机内部（mode / awaiting / sawMine / pairIds / remaining / 段记录）。
// 连跑"停住"这类只在真前端才复现的问题，没有这层内部视图只能靠猜——0.6.13 的三处原因
// （detail 形状 / queuePrompt 返回形状 / id 类型）全靠它挖出来。
try {
    window.__h3Relay = {
        debug: () => [...CHAINS].map((s) => ({
            mode: s.mode, awaiting: s.awaiting, sawMine: s.sawMine, remaining: s.remaining,
            runSeq: s.runSeq, pairIds: [...s.pairIds],
            // 段记录按段号展开（空洞单列）——"哪段有记录、属于哪一轮"一眼可见。
            // ⚠ 不在这里调 `findPair`（它会写 status），驱动了哪几处看 status 行「段号同步 #…」。
            ...collectStageIds(s.stageIds),
        })),
    };
} catch { /* 非浏览器环境（测试里 import 本文件）忽略 */ }

function bindGlobalListeners() {
    if (listenersBound) return;
    listenersBound = true;
    api.addEventListener("executing", ({ detail }) => {
        for (const s of CHAINS) s.onExecuting(detail);
    });
    // 全轮收尾的**首选**信号：带 prompt_id，最干净。
    api.addEventListener("execution_success", ({ detail }) => {
        hostSendsSuccess = true;
        for (const s of CHAINS) s.onExecutionSuccess(detail);
    });
    // 🔴 0.6.13 修（2026-09-28 实测）：**整轮全命中缓存**时（图没变、种子固定 ⇒ 复跑同一段），
    //   宿主**不逐节点发 `executing`**，只发一条 `execution_cached {nodes:[…]}` +
    //   几条 `executed`，然后直接 `execution_success` → `executing(null)`。
    //   ⇒ 只靠 `executing` 判定「本轮跑过本组」会恒为 false ⇒ `stepDone()` 静默 return
    //     ⇒ 连跑又冻住（status 停在「已排队，采样中…」）。缓存命中同样是「本组跑了」，要认。
    api.addEventListener("execution_cached", ({ detail }) => {
        for (const s of CHAINS) s.onCached(detail);
    });
    api.addEventListener("executed", ({ detail }) => {
        for (const s of CHAINS) s.onExecuted(detail);
    });
    api.addEventListener("execution_error", () => {
        for (const s of CHAINS) s.onExecutionError();
    });
    // 采样进度（**每步都触发**）⇒ 只用来更新「⏩ 连跑」按钮上的进度文字，走节流。
    api.addEventListener("progress", ({ detail }) => {
        for (const s of CHAINS) s.onProgress(detail);
    });
}

app.registerExtension({
    name: "H3RelayKit.Chain",

    /**
     * 0.6.19：给每个带 `run_id` 的节点（六类，见 `relay_kit_sync.js` 的 `RUN_ID_TYPES`）挂上同步回调。
     *
     * 为什么在这里而不是 `beforeRegisterNodeDef`：后者是**按类型**调的，拿不到具体实例的 widget；
     * `nodeCreated` 时该实例的 widgets 已经建好（它晚于 `onNodeCreated`）。
     * 少数前端版本若仍取不到 widget，下一拍重试一次 —— 宁可晚一拍，也不要静默不挂钩。
     */
    nodeCreated(node) {
        if (!isRunIdType(node?.type)) return;
        if (hookRunIdWidget(node)) return;
        setTimeout(() => {
            try {
                hookRunIdWidget(node);
            } catch (err) {
                console.warn("[H3 Relay Chain] 挂 run_id 同步钩子失败（这一格仍能正常填）：", err);
            }
        }, 0);
    },

    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "H3RelayChain") return;

        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const r = origOnNodeCreated?.apply(this, arguments);
            const node = this;

            // sawMine：本轮执行里是否真的跑过本组桥/落盘。executing(null) 是全局事件，
            // 多个 Chain 组共存时，别的组跑完一轮不能推进本组的段号。
            // stageIds：**按段号**存本轮各段排队拿到的 prompt_id（拼接时按它取回每段落盘的 mp4）。
            // pairIds：本组「桥/落盘」的 id **集合**，连跑开始时算一次就够 ——
            //   executing 事件每执行一个节点都会触发，在里面重算 findPair 等于每节点扫一遍全图。
            const state = {
                mode: "idle", remaining: 0, sawMine: false, awaiting: false, hinted: false,
                // stageIds：**按段号**存 `{id, seq}`（`seq` = 该记录属于第几轮）——
                //   `id` = 那段排队拿到的 prompt_id（拼接时按它取回落盘的 mp4）；
                //   `seq` 用来标"这一段来自更早一轮"（同一次拼接里混轮 ⇒ 段间可能不接续）。
                //   🔴 是**稀疏数组**：`stageIds[2]` 有值不代表第 1、2 段也有（空洞由
                //   `collectStageIds` 报出来，绝不压实 —— 压实就是段序错乱的成因）。
                stageIds: [], runSeq: 0,
                // phase：阶段（`PHASES` 的键）—— 决定**标题栏色带**与 `status` 字形前缀。
                phase: "idle",
                pairIds: new Set(), pairReady: false,
                btns: {}, lastProgress: "",          // 按钮引用 / 最近一次采样进度文案
                chainNode: node,

                /** 本组"正在等一轮跑完"（排队成功后置位；`stepDone` 消费掉）。 */
                armed() { return this.mode === "chain" && this.awaiting; },

                /** 清掉本组「桥/落盘 id 集合」的缓存（下段第一个事件会用新图重建）。 */
                resetPair() {
                    this.pairIds = new Set();
                    this.pairReady = false;
                },

                /** 惰性算一次本组「桥 + 落盘」的 id 集合（下段第一个事件会用新图重建）。 */
                refreshPairIds() {
                    // 🔴 用**独立标志**判"算过没有"，不能拿 `pairIds.size` 当判据：
                    //   配对失败时集合本来就是空的 ⇒ 每个节点事件都重跑一次 `findPair`
                    //   （4 趟全图 + 逐分组包围盒），而失败路径还会 `setStatus` ⇒
                    //   `setDirtyCanvas(true, true)` ⇒ **每个节点事件全画布重绘一次**。
                    if (this.pairReady) return this.pairIds;
                    this.pairReady = true;
                    const pair = findPair(node);
                    // 🔴 0.6.13：id 一律**字符串化**再存。新版前端 `node.id` 是字符串
                    //   （实测 pairIds=["961","902"]），旧写法 `has(Number(id))` 做的是
                    //   数字比对 ⇒ 恒 false ⇒ `sawMine` 永远记不上 ⇒ 连跑跑完第 1 段静默停住。
                    //   （0.6.12 修的 `detail.node`、0.6.13 修的返回形状都对，全被这一层挡住。）
                    this.pairIds = new Set(pair ? [String(pair.bridge.id), String(pair.save.id)] : []);
                    return this.pairIds;
                },

                /**
                 * 记「本轮确实跑过本组」——**三种来源都认**：
                 *   · `executing`（逐节点执行；真跑时用）
                 *   · `execution_cached`（整轮命中缓存时宿主**只发这一条**，里面带节点清单）
                 *   · `executed`（产出事件，兼作兜底）
                 * 🔴 只认 `executing` 是 0.6.13 之前的死路：缓存轮次不逐节点发事件 ⇒
                 *    `sawMine` 恒 false ⇒ 连跑跑完一轮就静默停住。
                 */
                markMine(id) {
                    if (id == null) return;
                    // 比之前先看 `armed()`：**放弃**本轮之前（上一轮残留/别组）的事件的干扰。
                    if (!this.armed()) return;
                    if (this.refreshPairIds().has(String(id))) this.sawMine = true;
                },

                /** 每个节点执行时都会调到这里（由模块级监听分发）。 */
                onExecuting(detail) {
                    // detail 为空 = 本轮队列跑完。宿主若另发 `execution_success`，收尾**只认那条**，
                    // 否则两条都触发 ⇒ 段号一次跳 2。
                    if (detail == null) { if (!hostSendsSuccess) this.stepDone(); return; }
                    if (this.mode !== "chain") return;
                    this.markMine(executingNodeId(detail));
                },

                /** `execution_success` = 本轮真的跑完了（首选收尾信号，见上）。 */
                onExecutionSuccess() { this.stepDone(); },

                /** 整轮命中缓存（宿主不发 per-node executing）时的证据。 */
                onCached(detail) {
                    if (this.mode !== "chain") return;
                    for (const id of detail?.nodes ?? []) this.markMine(id);
                },

                /** 产出事件——兼作兜底（个别前端在缓存轮只发 executed）。 */
                onExecuted(detail) {
                    if (this.mode !== "chain") return;
                    this.markMine(detail?.display_node ?? detail?.node ?? null);
                },

                onExecutionError() {
                    if (this.mode !== "chain") return;
                    this.mode = "idle";
                    this.awaiting = false;
                    this.onStageChanged();
                    say(node, this, "error",
                        "执行出错，连跑已停（stage_index 保持当前值，可直接重跑）。");
                },

                /** `progress` = 采样步数（**每步都触发** ⇒ 走节流）。 */
                onProgress(detail) {
                    if (this.mode !== "chain") return;
                    const v = Number(detail?.value ?? 0), m = Number(detail?.max ?? 0);
                    if (!m) return;
                    this.lastProgress = `采样 ${v}/${m}`;
                    this.paint();
                },

                /** 段号推进 / 收尾时刷新按钮文字（这些是**低频**事件，直接刷）。 */
                onStageChanged() {
                    this.lastProgress = "";
                    refreshChainLabel(this, node);
                },

                /** 一段跑完：推进段号，决定继续还是收尾。 */
                stepDone() {
                    if (this.mode !== "chain") {
                        // 单段模式（▶ Run / ✔ Approve）没有连跑状态机，但"这一段跑完了"同样要给反馈 ——
                        // 否则色带会一直停在「🟦 已排队」，用户以为还在跑。
                        if (this.awaiting && this.phase === "queued") {
                            this.awaiting = false;
                            say(node, this, "done", "这一段跑完了（▶ / ✔ 单段模式）。");
                        }
                        return;
                    }
                    if (!this.awaiting) return;   // 没在等一轮 ⇒ 旧事件/别组的收尾，不理
                    if (!this.sawMine) return;    // 本轮没执行过本组的桥/落盘 → 别的组的收尾，忽略
                    this.awaiting = false;
                    this.sawMine = false;
                    this.resetPair();   // 下段第一个 executing 事件会重建（图改了也自动跟上）

                    const pair = findPair(node);
                    if (!pair) { this.mode = "idle"; return; }

                    const seg = Math.round(widgetValue(node, "segments", 0) || 0);
                    const infinite = seg <= 0;
                    const more = infinite || this.remaining > 1;
                    if (this.remaining > 0) this.remaining -= 1;

                    if (!more) {
                        // 收尾路径段号还是要推（"已落盘，可直接继续 Approve" 的语义不变）
                        setStageAll(node, pair, getStage(pair.bridge) + 1);
                        const done = getStage(pair.bridge);
                        this.mode = "idle";
                        this.onStageChanged();          // 按钮还原（明显反馈）
                        say(node, this, "done",
                            `连跑结束，当前段号 ${done}（已落盘，可直接继续 Approve）。`);
                        if (widgetValue(node, "auto_concat", false)) concatFilm(node, this, true);
                        return;
                    }
                    // 🔴 0.6.14：与 Approve 对齐——**先确认词能写上，再推进段号**。
                    //   旧顺序（先 setStageAll 再查词）在词不够时会留下"段号推上去了、
                    //   但那一段没跑"的悬空状态。词分发失败 ⇒ 段号保持当前值，补词后 Approve 继续。
                    const next = getStage(pair.bridge) + 1;
                    const d = checkStagePrompt(node, next);
                    if (!d.ok) {
                        this.mode = "idle";
                        say(node, this, "warn", d.message
                            + `\n（段号保持 ${getStage(pair.bridge)}，补词后可 Approve 继续。）`);
                        return;
                    }
                    setStageAll(node, pair, next);
                    say(node, this, "running",
                        `连跑中：第 ${next + 1} 段排队…（剩余 ${infinite ? "∞" : this.remaining}）`
                        + withMsg(d.message));
                    queuePrompt(node, this, next).catch(() => (this.mode = "idle"));
                },
            };

            // 采样进度**节流**刷新（`progress` 每步都触发；200ms 一次足够看出在动）。
            state.paint = throttle(() => refreshChainLabel(state, node), 200);

            CHAINS.add(state);
            bindGlobalListeners();
            // 节点被删/复制时把状态摘出去 —— 否则监听回调会一直留着这个闭包。
            const origOnRemoved = node.onRemoved;
            node.onRemoved = function () {
                CHAINS.delete(state);
                return origOnRemoved?.apply(this, arguments);
            };

            // 阶段色带 / 阶段文字（canvas 绘制，见 `drawPhaseBadge`）。
            // ⚠ 必须 `apply` 原有的钩子 —— 别的扩展（或前端自身）可能也挂在上面。
            const origOnDrawForeground = node.onDrawForeground;
            node.onDrawForeground = function (ctx) {
                const r = origOnDrawForeground?.apply(this, arguments);
                try {
                    drawPhaseBadge(ctx, node, state.phase);
                } catch { /* 画不出来也绝不能影响节点本身 */ }
                return r;
            };

            // ⚠️ 一律带 `{ serialize: false }`：按钮不该进 widgets_values（否则旧图凭空多出空槽位）。
            // 同时把 widget 存进 `state.btns`：连跑中要把「⏩ 连跑」的文字换成进度（明显反馈）。
            //
            // 🔴 每个按钮回调先做两件**反馈**的事（GG 2026-09-30 点名"点了不知道点上没有"）：
            //   ① `flashPressed`：文字立刻变「⏳ … 已点击」⇒ 点没点上当场有答案；
            //   ② 防抖：0.4 秒内的第二次点击**直接忽略**（连跑一次几十秒，双击 = 白烧一轮 GPU）。
            //      「⏹ Stop」「↺ Reset」**不防抖** —— 那是用户的"刹车"，任何时候都该立刻响应。
            const addBtn = (label, fn, { debounce = true } = {}) => {
                let w = null;
                let lastClick = 0;
                const run = async (...args) => {
                    if (debounce) {
                        const now = Date.now();
                        if (now - lastClick < 400) {
                            say(node, state, "warn", `「${label}」在 0.4 秒内被点了两次 ⇒ `
                                + "第二次已忽略（防白跑一轮）。");
                            return;
                        }
                        lastClick = now;
                    }
                    flashPressed(node, w, label);
                    return fn(...args);
                };
                w = node.addWidget("button", label, null, guard(node, label, run),
                                   { serialize: false });
                state.btns[label] = w;
                return w;
            };

            addBtn("▶ Run（按当前段号跑一次）", async () => {
                const pair = findPair(node);
                if (!pair) return;
                if (state.mode === "chain") {          // 防误点：连跑中再点 Run 会把状态机打回 idle
                    say(node, state, "warn", "正在连跑中 —— 先点「⏹ Stop」，或等这一段跑完。");
                    return;
                }
                if (!runIdPreflight(node, state)) return;   // 0.6.19：名字不一致不许排队
                state.mode = "idle";
                state.awaiting = false;
                state.runSeq += 1;                  // ▶ = 新一轮（拼接时用来标"更早一轮"）
                await startStage(node, state, getStage(pair.bridge), "Run", pair);
            });

            addBtn("✔ Approve（段号+1 并跑下一段）", async () => {
                const pair = findPair(node);
                if (!pair) return;
                if (state.mode === "chain") {
                    say(node, state, "warn", "正在连跑中 —— 段号会自动推进，不用点 Approve。"
                        + "想停下来点「⏹ Stop」。");
                    return;
                }
                if (!runIdPreflight(node, state)) return;   // 0.6.19：名字不一致不许排队
                const next = getStage(pair.bridge) + 1;
                const d = checkStagePrompt(node, next);
                if (!d.ok) { say(node, state, "warn", d.message); return; }
                setStageAll(node, pair, next);      // 先确认词能写上，再推进段号
                state.mode = "idle";
                state.awaiting = false;
                // 走 `startStage`（同一入口）⇒ 陈旧段记录、启用态、词检查、排队口径全都一致。
                await startStage(node, state, next, "Approve", pair);
            });

            addBtn("⏩ 连跑（按 segments 自动循环）", async () => {
                const pair = findPair(node);
                if (!pair) return;
                if (!runIdPreflight(node, state)) return;   // 0.6.19：名字不一致不许排队
                await startChainRun(node, state, pair, "连跑");
            });

            // 🔴 0.6.15 断点续跑：宿主卡死 / 被看门狗杀掉之后，不用从头再来。
            //   进度由 Chain 节点自己**每段写一次**（`output/relay_kit/<run_id>/_progress.json`，
            //   `run_id` 填了才写 ⇒ 老图零副作用）。
            //   语义 = 「第 k 段**已开始**」⇒ 从 k 段重跑：**宁可重跑一段，绝不跳段**。
            addBtn("⏭ 续跑（从上次跑到的那段继续）", async () => {
                const pair = findPair(node);
                if (!pair) return;
                // 0.6.19：先跑闸 —— 它顺带把本节点（Chain）的 `run_id` 从同组的唯一非空值补齐，
                // 否则下面那句"续跑要先在 run_id 格里填上"会白拦一次（默认是空的）。
                if (!runIdPreflight(node, state)) return;
                const runId = String(widgetValue(node, "run_id", "")).trim();
                if (!runId) {
                    say(node, state, "warn", "续跑要先在 `run_id` 格里填上（与桥 / 落盘的 run_id 填一样）"
                        + " —— 它用来找上次的进度文件。");
                    return;
                }
                if (typeof api?.fetchApi !== "function") {
                    say(node, state, "warn", "这个 ComfyUI 前端没有 api.fetchApi ⇒ 读不了进度。"
                        + "（进度文件在 `output/relay_kit/<run_id>/_progress.json`，可以直接看）");
                    return;
                }
                let prog = null;
                try {
                    const res = await api.fetchApi(
                        `/h3relay/progress?run_id=${encodeURIComponent(runId)}`);
                    prog = await res.json();
                } catch (err) {
                    say(node, state, "warn", "读进度失败：" + err.message);
                    return;
                }
                if (!prog || !prog.ok) {
                    say(node, state, "warn", (prog && prog.error) || "读不到进度");
                    return;
                }
                const k = Math.max(0, Math.round(prog.stage_index ?? 0));
                const when = prog.updated
                    ? new Date(prog.updated * 1000).toLocaleString() : "时间未知";
                setStageAll(node, pair, k);
                await startChainRun(node, state, pair, `⏭ 续跑（上次第 ${k + 1} 段，${when}）`);
            });

            // ⚠ 「刹车」不防抖：用户点 Stop 就该立刻响应。
            addBtn("⏹ Stop（本轮跑完即停）", () => {
                if (state.mode === "chain") {
                    state.mode = "idle";
                    state.awaiting = false;
                    state.onStageChanged();
                    say(node, state, "stopped", "已请求停止：当前采样跑完后不再推进。");
                } else {
                    say(node, state, "idle", "当前没有在连跑。");
                }
            }, { debounce: false });

            addBtn("🧩 拼成一条（把已跑的 N 段拼成成片）", async () => {
                await concatFilm(node, state, false);
            });

            addBtn("↺ Reset（段号归 0，从第 1 段重来）", () => {
                const pair = findPair(node);
                if (!pair) return;
                setStageAll(node, pair, 0);
                state.stageIds = [];
                say(node, state, "idle", "已归 0：下一段将作为第 1 段（不续接，只落盘）。");
            }, { debounce: false });

            return r;
        };
    },
});
