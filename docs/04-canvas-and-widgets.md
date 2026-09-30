# 画布外观 · advanced 折叠 · 旧图迁移注意

> 本文件由 README（0.6.0 梳理）拆出。
> 📐 **槽位记号**：本文一律 `[N]` = **0 起算**（UI 上第 N 个口 = `[N-1]`）。
> 搬运自旧稿的「第 N 路」是 1-based 的历史写法，已在关键处改为 `[N]`。
> 主 README 只留快速阅读必需的结论与指针。

---

### 🔧 节点在画布上为什么这么"小/大"——`advanced` 折叠（0.5.0）

本包节点**默认只把常用的几个旋钮画在画布上**，其余（细分参数与护栏）标了
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
**功能**称呼它们 —— 下表是两边的对应关系（本文档正文里的中文词都指左边的节点）：

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
> 中文文档正文保留中文词（可读性），**指名道姓时用 `「Trim AV」` 这样的短名** —— 与标签是子串关系，一眼能对上。

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