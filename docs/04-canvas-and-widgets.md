# 画布外观 · advanced 折叠 · 旧图迁移注意

> 本文件由 README（0.6.0 梳理）拆出。
> 📐 **槽位记号**：本文一律 `[N]` = **0 起算**（UI 上第 N 个口 = `[N-1]`）。
> 搬运自旧稿的「第 N 路」是 1-based 的历史写法，已在关键处改为 `[N]`。

---

### 🔧 节点在画布上为什么这么"小/大"——`advanced` 折叠（0.5.0）

本包节点**默认只把常用的几个旋钮画在画布上**，其余标了
`"advanced": True`，收进节点底部的展开区。这不是藏功能，是**为了节点别撑得那么大**：

- ComfyUI 前端的节点 resize 是**硬夹取**的：宽有 **225px 死下限**
  （`GraphView-*.js` 的 `useNodeResize` → `Math.max(size.width, 225)`），
  高也不能小于**内容高度**（widget 行数）⇒ **只有少渲染几行，节点才缩得下去**。
- `advanced` 是官方机制（`comfy_extras/nodes_model_advanced.py` 同款），
  **不改** widget 顺序、`widgets_values` 逐位取值、默认值，也不影响 API 提交。

想看到全部旋钮，三选一：
1. 点节点底部的 **advanced inputs** 展开条；
2. 右侧栏 → **Advanced Inputs** 分组（会列全所有节点的高级项）；
3. 设置里打开 **`Comfy.Node.AlwaysShowAdvancedWidgets`**（默认关 ⇒ 打开后全画布都展开）。

| 节点 | 画布上保留（主旋钮） | 折进 advanced |
|---|---|---|
| Trim AV | `trim_frames` / `fps` / `settle_frames` | 15 个画质域旋钮 + `seam_ghost` / `seam_ghost_alpha` |
| Post | 9 个主旋钮（8 个**主强度**含 `head_zone_frames` + `cross_seg_ack`） | 10 项细分与护栏（`*_frames` / `*_gain_max` / `*_offset_max` / `*_blur` / `radius` / `stats_frames` / `baseline`） |
| 拷贝桥（复合桥） | `context_frames` / `mask_mode` / `pin_audio` / `anchor_blend` / `ref_anchor_stage` | 7 项模式专属参数（taper / ramp / blend 三族）+ 复合折叠槽（conditioning / run_id / stage_index / ref_anchor_*） |
| 音频缝 | `patch_seconds` / `fade_seconds` | `tile_seconds` / `bed_stage` / `note` |

> 连线口（`images` / `audio` / `guide` / `latent` / `conditioning` …）不受影响，一直画在节点上。

### 术语 ↔ 节点标签对照（0.6.18 起节点标签是**纯英文**）

节点在画布/菜单里显示的名字是英文（`NODE_DISPLAY_NAME_MAPPINGS`），而本仓的中文文档按
**功能**称呼它们 —— 下表是两边的对应关系：

| 中文术语（本文档用法） | 节点标签（画布上看到的） | 类型名（`class_type`） |
|---|---|---|
| 裁重叠 | `🔗 H3 Relay · Trim AV` | `H3RelayTrimAV` |
| 拷贝桥 / 复合桥 | `🔗 H3 Relay · Copy Bridge` | `H3RelayCopyBridge` |
| 落盘 / Latent 存 | `🔗 H3 Relay · Latent Save` | `H3RelayLatentSave` |
| 读上段 latent / Latent 读 | `🔗 H3 Relay · Latent Load` | `H3RelayLatentLoad` |
| 后处理 / Post | `🔗 H3 Relay · Post` | `H3RelayPost` |
| 音频缝 | `🔗 H3 Relay · Audio Seam` | `H3RelayAudioSeam` |
| 连跑 / 连跑控制 | `🔗 H3 Relay · Chain` | `H3RelayChain` |
| 潜空间分块放大 | `🔍 H3 Relay · Latent Upscale` | `H3RelayLatentUpscale` |

> ⚠️ **改标签不动老图**：工作流里存的是**类型名**（上表第三列）与用户自己设的**标题**，
> 所以改显示名**不影响已存的图**（连线、槽位、执行全都照旧），只是新拖出来的节点换了名字。
> 中文文档正文保留中文词，**指名道姓时用 `「Trim AV」` 这样的短名** —— 与标签是子串关系，一眼能对上。

> 🔴 **0.5.0 迁移注意：老图里的「Trim AV」看不见 `prev_tail`。**
> ComfyUI 前端 `LGraphNode.configure()` 是**照单全收**序列化里的 `outputs` 数组
> （`litegraph/src/LGraphNode.ts`：`this.outputs = cloneObject(info.outputs)`），
> 所以**0.5.0 之前存的工作流**里那个节点的 `outputs` 只有 3 个 ⇒ 打开后**看不到第 4 个 `[3]`
> `prev_tail`**，也就没法手动接 `guide`。**执行不受影响**（新增输出没人连），
> 只是接不了后处理。两个解法，任选：
> 1. **重加节点**：删掉老的「Trim AV」再拖一个新的，把线接回去（推荐，顺手刷新所有 widget）；
> 2. **补 JSON**：给该节点的 `outputs` 末尾补 `{"name": "prev_tail", "type": "IMAGE", "links": []}`
>    （改前先备份；`python tools/review_050.py` 的 J 节会扫出这类「输出数组过期」的图）。
>
> 走脚本产线（作者产线仓库的 `l1_api.py`，**不在本包内**）不受影响——图是每次现构造的，永远取当前节点定义。

### `run_id` 一处改全组（0.6.19，**画布专属**）

`run_id`（这部片子叫什么，决定段文件落在 `output/relay_kit/<run_id>/`）在**六个节点**上各存一份
（落盘 / 桥 / 读上段 latent / 裁重叠 / 音频缝 / 连跑），必须一字不差。
0.6.19 起 **改任一格 ⇒ 同组的其余格自动跟随**：

- 范围 = **同一个分组框**。没画分组框时退化为**全图**，此时 `status` 会以 ⚠ **明说**范围是全图
  （同一张图上放两部片子是正常用法，那种情况请画分组框）。
- 改一格**非空**名字 ⇒ 其余格跟随，`status` 报「`run_id` 已统一为「…」（同步 N 个节点）」。
- **清空一格不扩散**（清空 ≠ 想把全组清空），但会提示「别处还有名字 —— 不一致会找不到段文件」。
- 🔴 **两个不同的非空名字 ⇒ 不自动挑**：点 `▶ Run` / `✔ Approve` / `⏩ 连跑` / `⏭ 续跑` 会被拦下，
  `status` 里列出「哪个节点是哪个名字」，按它改齐再跑。
- 顺带：**唯一非空、其余还空着** ⇒ 点那四个按钮时**自动补齐** —— 所以你只需要改一处。

> ⚠️ 这是**纯前端**行为：脚本提交图 JSON 时**不生效**（脚本把这一格写成同一个变量即可，
> 见 [`docs/10`](10-audio-seam-and-concat.md) §7.4）。
> ⚠️ **改名之后旧目录不会跟着搬**：段文件仍留在旧名字下（「目录名 = 片子名」是一贯语义）。

### 节点中英切换（0.6.28，**画布专属**）

本包 8 个节点上各多了一个「**中 / EN**」按钮。点任意一个 ⇒ **全包**的标题 / 参数标签 / 端口名
一起切换（不是"只切这一个节点"）。

| | 行为 |
|---|---|
| 默认语言 | 🔴 **跟随官方语言设置**（`Comfy.Locale`，即设置里的 `Language`）；官方读不到才退到 `<html lang>`、再退到浏览器语种。点按钮才覆盖 |
| 覆盖 | 覆盖后**官方再切语言不冲掉你的选择**；清掉 `localStorage` 键即回到跟随 |
| 方向 | 🔴 **单向**：官方改语言 ⇒ 节点跟着变；我们的按钮**只改本包节点**、**不写官方设置** |
| 记忆 | 覆盖值存在浏览器 `localStorage`（键 `h3relay_lang`）⇒ 关掉页面再打开还在 |
| 改了什么 | 节点 `title`、widget 的 `label`、端口的 `label`、下拉框的**显示文字**、**悬浮提示** |
| 没改什么 | 🔴 **`name`、下拉框的 `values`、工作流 JSON** —— 一个字节都不动 |

**为什么是"单向"（读官方、不写官方）**：官方前端一旦发现 `Comfy.Locale` 变化，
会执行 `Comfy.RefreshNodeDefinitions` 并**重载当前工作流**（读官方前端源码确认）。
点一下节点按钮就重载整个工作流、丢掉未保存的改动，代价不可接受 ⇒
本包只做"**读官方**"，写官方请用官方的 `Language` 下拉。

**官方入口是哪一个（踩过的坑）**：`app.extensionManager.setting.get("Comfy.Locale")`。
🔴 **`<html lang>` 不可信** —— 真机实测同一时刻 `Comfy.Locale = "zh"` 而 `<html lang> = "en"`
（官方只把它当浏览器语种的声明，**不随语言设置更新**）⇒ 只要官方入口读得到，就**绝不**读它，
否则会出现"官方界面中文 + 节点英文"。这两条由 **H3t** 机检守着。

**三条设计约束（都是"静默失效"级别的坑）**：

1. 🔴 **只改显示，绝不改图**。`name` 是后端取参与序列化的依据，改它等于改图。
   枚举的中文显示走官方 `options.getOptionLabel` 钩子（函数不进 JSON ⇒ 分享出去不污染别人的图）。
2. 🔴 **与「音频参考额度」提示共存**。额度提示是**追加在标题末尾**的（`relay_kit_refs_ui.js`），
   两个模块都写 `title` ⇒ 本模块**只替换名字那一段、后缀原样保留** ⇒ 谁先谁后都对。
3. 🔴 **不猜没把握的翻译**。只有**白名单里的枚举值**才给中文显示（`hard` → 硬边）；
   `model_name`（模型文件名）、段号、路径、你自己写的名字**一律不翻** —— 翻了 = 找不到文件。

**悬浮提示（`H3_TIPS`，0.6.28）**：鼠标停在参数上显示的是**精简双语**提示 ——
一句话说清这句参数干什么 + 默认/推荐填什么。`nodes.py` 里的原文最长 1084 字符
（历史沿革、踩坑叙事、已废弃分支）；悬浮提示是**速查**不是手册，长篇依据留在 `docs/` 与 `CHANGES.md`。

- 键是 **`class_type.参数名`**、不是光参数名：`run_id` 在落盘节点是「必填片子名」、
  在连跑节点是「续跑用、可留空」—— 合并成一条就丢了语义。
- 写进 **`widget.tooltip`** —— 前端优先读的那一格（`widget.tooltip || nodeData.inputs[name].tooltip`），
  且**序列化从不写它**（存档只写 `widget.value`）⇒ 分享出去的图不带翻译、别人的图也不受影响。
- 表里**没有的 (节点,参数) 组合一个字节都不动**（保留后端中文原文，宁缺勿错）。
  新增参数忘了写提示 ⇒ `review_050.py` 的 **H3p** 当场红。

**与界面汉化插件的关系**：Global Translation / ComfyUI-Chinese-Translation 之类插件按 **DOM 文本**
翻界面，本功能改的是 `label`/`title`/`tooltip` **数据**、且**只管本包自己的节点**。
🔴 **但两边会抢同一批字段**：那类插件有一个**每 1 秒的守护轮询**，会按它自己的词典把节点
标签/提示**改回中文**（它源码注释原话：「兜底补刷**被第三方扩展覆盖**的节点槽位标签」），
且**不对英文界面做任何屏蔽**。本包的对策 = 写值时记下期望值、**每帧**比对写回
（帧率远高于它的 1 Hz）⇒ 显示归我们，**不需要改它一行代码**（由 **H3r** 机检守着成对存在）。
词表里没有的键**静默跳过**（不崩、不改），新增参数忘了翻译 ⇒ `review_050.py` 的
**H3n**（标签）与 **H3p**（提示）会当场红。

> ⚠️ **纯前端**：脚本提交图 JSON 时**不生效**（也不需要 —— 标签只是给人看的）。
> ⚠️ 按钮用 `addDOMWidget(serialize:false)` 挂（不进工作流 JSON）；若某个前端版本没有这个 API，
> 按钮会退化成节点上的「中/EN」**角标**（能看见状态、不能点），**不影响任何执行**。
