# CHANGES

## 0.5.0 — 2026-09-17

> 本版为**节点重构版**：拆出 `H3RelayPost`（画质域后处理独立成节点），新增
> `H3RelayAudioSeam`（音频域），并补齐调研文档 §2/§4 两个方向。

### 🔴 节点 UI 收口 + 参数收口 + 体检器补盲点（2026-09-19 夜）

#### ① 画布 UI 收口：39 个旋钮标 `advanced: True`

**症状**：节点在画布上「有点大」，**能放大、不能缩小**。

**根因**（前端源码判据，非推测）：Vue 节点模式的 resize 是**硬夹取** ——
`GraphView-*.js` 的 `useNodeResize` 回调里 `Math.max(node.size.width, 225)`（**宽有 225px 死下限**），
高度则不能小于**内容高度**（由 widget 行数决定）。⇒ 节点下限 = 内容行数，
**只有少渲染几行才降得下来**。

**修法**（官方机制，不是自创）：在 `INPUT_TYPES` 的选项字典里加 `{"advanced": True}`。
前端读 `widget.options.advanced`：为真则该 widget **默认不进画布渲染**，收进节点底部的
「advanced inputs」展开区，并出现在右栏 “Advanced Inputs” 分组。官方
`comfy_extras/nodes_model_advanced.py` 就是这么用（`"zsnr": ("BOOLEAN", {"default": False, "advanced": True})`）。

**明确不改**：widget **顺序**、`widgets_values` 的**逐位取值**、默认值、API 提交
（API 格式按输入名传参，与画布显示无关）。⇒ 存档图、产线 API 图**都不受影响**。

| 节点 | 画布上保留 | 折进 advanced |
|---|---|---|
| `H3RelayTrimAV` | `trim_frames` / `fps` / `settle_frames` | 17 项（15 个画质域旋钮 + `seam_ghost` / `seam_ghost_alpha`） |
| `H3RelayPost` | 8 个**主强度**旋钮（match_prev / lowfreq_pull / hist_match / wb_match / deconv_strength / detail_borrow / settle_sharpen / head_zone_frames） | 9 项细分与护栏（*_frames / *_gain_max / *_offset_max / stats_frames / *_blur / radius） |
| `H3RelayCopyBridge` | `context_frames` / `mask_mode` / `pin_audio` | 7 项模式专属参数（taper/ramp/blend 三族） |
| `H3RelayMotionContext` | `trim_frames` / `run_id` / `stage_index` | `audio_frames` / `anchor_stage` / `anchor_frames` |
| `H3RelayAudioSeam` | `patch_seconds` / `fade_seconds` | `tile_seconds` / `bed_stage` / `note` |

想看全部旋钮三种办法：节点底部「advanced inputs」展开；右栏 **Advanced Inputs** 分组；
或全局设置 `Comfy.Node.AlwaysShowAdvancedWidgets`（默认关）。

#### ② 参数收口：`H3RelayPost` 新增 `head_zone_frames`

**问题**：`H3RelayPost` 组 2（段内色档对齐：直方图 / 白平衡）与组 3（补高频：反卷积 / 段体高频迁移）
的**四个强度旋钮**，它们的作用区长度一直**借**的是组 4 的 `settle_sharpen_frames`
—— 名字叫「糊区锐化帧数」，用户在 UI 上根本看不出这四个的「作用多少帧」受谁管。

**修法**：新增 widget `head_zone_frames`（默认 **24**，与 `settle_sharpen_frames` 默认同值
⇒ 没显式设过值的图**行为逐位不变**），组 2/组 3 四项改用它；`settle_sharpen_frames` 只管组 4
（tooltip 已写明）；报告行逐项打印实际帧数（例：「直方图匹配 0.50（段头↔段体，前 24 帧）」）。

**为什么这一次可以「就地插入」而不是追加末位**（本仓铁律是只追加）：
先全扫 `I:\ComfyUI\user`（工作流 + 产线图）确认**零存量图含 `H3RelayPost`** —— 该节点 0.5.0 才出生、
示例图由生成器一条命令重建 ⇒ 就地插入不会撞任何存档的取值位置。**这条例外只对本节点成立。**

#### ③ 口径补正：`prev_tail` 在 cond 桥下**不是**上段末帧（nodes.py 漏补的那半）

`prev_tail = images[pin-1]`。**拷贝桥**下前 `pin` 帧是逐位拷贝的上段尾 ⇒ 它**就是**上段末帧；
**Latent 桥（cond）**下前 `pin` 帧是**本段重画**的 ⇒ 只是**近似**（2026-09-17 实测代理误差
**0.006**，比它要修的缝阶跃 **0.0007** 还大 8 倍）⇒ **对错参照，越对齐越糟**。
README §552–555 当时已补，但 `nodes.py` 的类 docstring / `guide` 槽 / `lowfreq_pull` 槽三处仍写死
「= 上一段末帧」。本次补齐（**纯注释与 tooltip，零逻辑改动**）。

#### ④ 体检器 `tools/check_ui_workflow.py`：本地定义优先 + 补一个真盲点

**本地定义优先**（与生成器同一条教训）：本包 8 个节点改为**现读包内 `nodes.py` 的 `INPUT_TYPES()`**，
服务端只用来取非本包节点（官方/第三方）的 schema。理由：只看服务端的话，
**一个还没重启的后端会让体检器拿旧 schema 去判新文件** ⇒ 误报，甚至「自证式假绿」
（0.5.0 复查抓到的两处假绿，根子都是校验器与被校验物共用同一份过期依据）。
另外会把「服务端加载的定义 vs 代码里的定义」的差异**直接报出来**（提示该重启后端），
服务端不可达时**不致命**：本包节点照查，非本包节点那部分明确标注「未查」。

**补盲点（新发现的假绿）**：原实现只查「`widgets_values` **多了**」+「逐位取值类型/范围」，
比槽位**短**时**一路静默放行** —— 而 0.5.0 那次事故（`SaveVideo` 只写 1 格、实际要 4 格）
正是这一类的镜像。现在按 schema 扣掉「被连线转成输入口的 widget」（连线后值走 link、
序列化里不再占槽）后比长度，缺项**记 warn 并点名**缺哪个：
`node 14 H3RelayPost: widgets_values 只有 16 项、schema 推导要 17 项（缺 match_prev_stats_frames）`。
判 **warn 不判硬错**是有意的：文件侧无法 100% 分辨「该有的少了」与「前端没序列化」，误杀比漏报更坏。

#### ⑤ README 重排：安装 / 用法前置，原理后置

原来是「先讲原理、`安装` 一节排在 628 行」——开源用户打开 README 第一屏看不到怎么装。
现在结构：

1. 定位 → **它解决什么问题**
2. **用法区**（前六节）：`🚀 安装（2 分钟）` → `🚀 5 分钟跑通` → `节点` → `接线`（含 `advanced` 折叠说明、
   第三方出词接法、拷贝桥接法、显式接「Latent 读」）→ `🔗 Chain 自动连跑` → `参数` → `排障` →
   `工作流文件自检` → `离线自测`
3. 分隔线 → **# 🧠 原理与机制（进阶 · 不看也能把片子跑出来）**：两条路线怎么选 / 协议出处 /
   与像素续接的关系 / 时序网格 / 为什么必须裁头 / 裁多少帧 / 接缝处出词与音频纪律 /
   运行时契约 / 音频窗口径 / 许可与出处

顺带**去重与同步**：删掉「接线」里那个只剩指针的 `### 可选：Chain 自动连跑`（正文另有一节）；
标题去掉 `（0.2.0 新增）` 这类版本噪声；`check_ui_workflow.py` 那节的「需要 ComfyUI 正在运行」
改成新的两源口径（本包节点本地定义优先、服务端不可达不致命）；旋钮计数 16 → 17 同步。

#### ⑥ 自检与钉子（都验过「能失败」）

| 项 | 结果 |
|---|---|
| `tests/test_relay_core.py` | **257 / 0** |
| `tools/review_050.py` | **65 / 0**（新增 K8 / K8b / K9） |
| `tools/check_ui_workflow.py examples/minimal_relay_official.json` | 有问题 **0** 个 |
| K8 反证 | 把 `head_zone_frames` 换回 `settle_sharpen_frames` → **64 / 1** |
| K9 反证 | 摘掉 TrimAV 的 `seam_ghost` 标记 → **64 / 1** |
| 体检器少写盲点 反证 | 把示例图 Post 的 `widgets_values` 砍掉末位 → 报出缺 `match_prev_stats_frames` |

### 🔴 示例模板修复：生成的节点长出一排空插槽（2026-09-19，面向普通用户）

**症状**：打开 `examples/minimal_relay_official.json`，本包的节点会显示**一排空的输入圆点**
（`H3RelayPost` 16 个 / `H3RelayTrimAV` 20 个 / `H3RelayMotionContext` 6 个），
而**官方节点（`PrimitiveInt` 等）显示正常**。文件能开、能跑、不报任何错。

**根因**：`examples/make_minimal_workflow.py` 的 `_ty()` 只认 `list`：

```python
if not isinstance(spec, list) or not spec:   # ← 旧实现
    return None
```

本脚本有两个 schema 来源，容器类型**不一样**：
服务端 `/object_info`（JSON）给 `list`，而「本地定义优先」走的本包 `nodes.py` 的
`INPUT_TYPES()` 给 **`tuple`** ⇒ `_ty()` 对本包每个输入都返回 `None` ⇒ 调用点
`_ty(spec) or "*"` 把类型退化成 `"*"`，`t in WIDGET_TYPES` 恒假 ⇒ **widget 全部被当成普通插槽**：

- 生成的 `inputs` 缺 `{"widget": {"name": …}}` 标记；
- `widgets_values` 算出来是空列表 ⇒ 写成 `null`。

**为什么这会让节点变坏**（前端源码判据，非推测）：

- `renderer/extensions/vueNodes/utils/nodeDataUtils.ts`：
  `nonWidgetedInputs()` = `inputs.filter(i => !('widget' in i && i.widget))`
  ⇒ **没有标记的输入被当普通插槽渲染成空圆点**；
- `extensions/core/widgetInputs.ts` 的 `onGraphConfigured` **只删不补**：
  它只清理「带标记但找不到同名 widget」的输入，**绝不会**给缺标记的输入补上标记。

**修法**：`_ty()` 与 `default_value()` 的候选解析改为同时接受 `list` / `tuple`。
修完示例图与前端**自己存出来**的同类型工作流逐节点对齐（槽数 4/4、10/10、22/22，
`widgets_values` 长度全同，仅 `run_id` 与 `settle_frames` 取值不同）。

**顺带修**：

- Note 注释框里的「15 个旋钮」改为**从 schema 现算**（实际 16 个 —— 0.5.0 加了
  `match_prev_stats_frames` 后没同步，README 写 16、注释框写 15，用户对不上）。
- `tools/check_ui_workflow.py` / `tools/review_050.py` 里同类的
  `isinstance(spec[0], list)`（COMBO 候选校验）一并放宽到 `tuple`。

**回归钉子（都验过「能失败」）**：

| 位置 | 判据 | 反证 |
|---|---|---|
| `examples/make_minimal_workflow.py` `validate()` | 每个 widget 型输入必须带 `widget` 标记，且顺序与 schema 一致 | 把 `_ty()` 换回旧实现 → 报 5 条 |
| `tools/review_050.py` **I3** | 示例图 widget 标记与 schema 一致 | 换成修复前的示例图 → 61/1 |
| `tools/check_ui_workflow.py` | 文件级 widget 标记检查（**先于** `widgets_values` 的 null 判定） | 旧示例图 → 5 条硬错 |

> ⚠️ 两条踩坑教训（值得记）：
> 1. **`check_ui_workflow.py` 原本在这件事上是假绿**：旧代码 `if not isinstance(wv, list): continue`
>    把 `widgets_values: null` 的节点**整节点跳过**，而本包节点当时全是 `null` ⇒ 一个都没查过。
> 2. **第一版回归钉子自己就是假绿**：期望集与实收集**共用同一个 `_ty()`**，它一坏两边同时变空、
>    比较恒相等 ⇒ 旧实现下依旧报 0 问题。改为独立的 `spec_type_strict()` 后才能真正失败。
>    **判据不能与被判对象共用同一个函数。**

### 🔴 示例模板修复（续）：动态 COMBO 子参数漏槽（SaveVideo 的 `format` / `codec`）

> 与上一节同一类「白名单漏一项、`want`/`got` 一起漏」的自证式假绿；这里单独列，
> 因为根因是**另一种** widget 类型没进白名单。

**症状**：用旧版 `make_minimal_workflow.py` 生成的示例图，SaveVideo 节点的 `widgets_values`
只有 **1 项**（`filename_prefix`），而 ComfyUI 前端实际要 **4 项**
（`filename_prefix` / `format` / `format.codec` / `codec`）。槽位整体前移、文件能开能提交
不报错——典型的「测试是虚假的」。

**根因**：SaveVideo 的 `format` / `codec` 是 `COMBO_DYNAMICCOMBO_V3`（**动态 COMBO**），
选中后会**派生子 widget**（如 `format=auto` → `format.codec`，插在父项之后）。旧代码的
白名单 `WIDGET_TYPES = {INT, FLOAT, STRING, BOOLEAN, COMBO}` **不认动态 COMBO**
⇒ 这类输入被整格跳过，且 `format.codec` 这种子参数根本不在 `/object_info` 顶层 schema 里
（只在 `options[key].inputs` 中）⇒ 槽位彻底错位。

**修法**：

- 新增 `combo_default_key()`（取默认候选项 key，旧式选项数组 / 新式 `{"key",…}` 字典两种
  声明都覆盖）与 `dynamic_subwidgets()`（按 `options[key].inputs` 展开子参数，required 在前
  optional 在后，顺序与前端 `dynamicWidgets.ts` 一致）。
- 生成器 `iter_widget_inputs()` / 体检器 `frontend_slots()` **共用同一套展开**，不再各写各的。
- `local_kit_defs()` 不再写死作者本机路径 `I:\ComfyUI`——改为靠装在 `custom_nodes` 下自动
  上溯，或 `COMFYUI_PATH` 显式指定（与 `review_050.py` / 单测同一约定），**不绑本地配置**。

**验证**：重新生成的 `examples/minimal_relay_official.json` 中 SaveVideo 的 `inputs` 与
`widgets_values` 与 5/6 份前端真实存档逐位一致（`format.codec` 顺序 = 父项 `format` 之后、
`codec` 之前）；`tools/check_ui_workflow.py` 与 `tools/review_050.py` **I 段**均 0 问题。

### 🔴 新增节点：`H3RelayAudioSeam`（音频域——把音频缝从组装层搬进节点）

**为什么搬**：音频接缝此前靠**组装层**（外部 ffmpeg）的 crossfade / room tone 头部补丁 /
床环铺三件套。两个问题：

1. **效果不落进段文件**。那是"渲染之后的第二步"——UI 里播放、逐段验收、交付中间产物
   看到的都还是没补丁的音频，必须再跑一步外部脚本才有；同一部片子在不同入口跑，结果取决于
   那一步有没有做对。
2. **组装层 crossfade 有 A/V 位移**（本次实测，见下）。`ffmpeg acrossfade` 的语义是
   **裁掉后段的头部 X 秒**（交叉区消耗的是后段内容），于是每条缝让缝之后的**音频相对画面
   提前 X 秒**：实测 2 段、X=0.25 时音乐峰从 8.75s 移到 8.50s，而**总时长不变**（尾部被补平）
   ⇒ 4 段链累计 0.75s 的 A/V 相对位移。判据口径也要小心：那是"内容位移"，不是"丢帧"。

**新节点做什么**：把本段头部 `patch_seconds` 秒**等长替换**为上一段的最静窗环境声
（`tile_seconds>0` 时改取 W 秒瓦片自叠化环铺），到第 N 秒处交叉淡变回本段自身音频，
**N 之后一个采样点都不动**。

- **长度守恒**（音频采样点不增不减）⇒ 组装层那 0.25s/缝 的位移在 patch 路线上**不存在**。
- 每段（含第 1 段）落盘 `output/relay_kit/<run_id>/audio_NNNNN.safetensors`（与 latent 同目录、
  `audio_` 前缀），后段读它当床源。**第 1 段也必须接**，否则第 2 段没有床源。
- 守卫：床源段号 ≥ 本段 → `raise`；床源文件不存在 → `raise` 并提示先跑第几段；
  `patch_seconds` 超出段长 → **夹到段长−1 + 报告告警**（一个环境声补丁不值得中断 30 分钟的渲染，
  这是**有意偏离**"非法值直接 raise"的地方，已在节点 docstring 写明）。
- `bed_stage` 默认 0（= 第 1 段）。同场景环境声是 stationary 的，取第 1 段最稳；
  这与组装层"床源恒取第 1 段"口径一致。

**⚠️ 做不到的事（写清楚，别指望）**：**真 crossfade（两段三角窗叠化）无法在单段内长度守恒地
实现**——它的语义要裁掉**上一段**的尾巴，而单段节点只能改本段。所以：
- 等长路线（推荐）→ 本节点 `patch_seconds`；
- 需要真叠化 → 仍归组装层（外部手段），但要接受上面那条 A/V 位移，或自行在片尾补时间。

**实测（零 GPU，同源同 seed 音频，唯一变量 = 音频配方）**：

| 配方 | 缝邻域最静点 | Δ vs 缝前 |
|---|---|---|
| 全关（crossfade/补丁/环铺 = 0） | **+20ms @ −71.7 dB** | **−26.4 dB** ⛔ 听感"抽一下" |
| 组装层 head-patch 2.0s | −40ms @ −54.7 dB（**已不在缝上**） | −1.1 dB ✅ |
| 组装层 acrossfade 0.25s | +40ms @ −56.2 dB | −0.5 dB ✅ |
| 组装层三件全开 | −120ms @ −49.9 dB | −3.1 dB ✅ |
| 09-15 生产配方 4 段链三条缝 | — | −3.7 / −3.0 / −0.8 dB ✅ |

⇒ **缝上的洞是"解码 priming + 无补丁"造成的**，补丁一上就消失；本节点复刻该算法，
但把它落在渲染时（等长、零位移、进段文件）。
⚠️ 上表是**组装层同算法**的实测量（节点版为 1:1 移植；节点自身由单测 22 组锁行为，
真渲染验证待跑）。

**默认关**：`patch_seconds=0` ⇒ 音频**逐位直通**（`audio_seam_patch` 直接返回原对象，不拷贝），
不接线时行为与 0.5.0 之前完全一致。

### 🔴 拆节点：`H3RelayPost`（画质域后处理独立成节点）

**为什么拆**：ComfyUI 的 UI 工作流把 `widgets_values` **按位置**存，所以「新 widget 只能追加末位」
是硬约束。后处理 15 个旋钮陆续塞进 `H3RelayTrimAV` 后，它被顶到 **22 个 widget**——
UI 上是一堆按"加入时间"排序的旋钮，**没有任何逻辑分组**，而且**每加一个后处理件都要往末尾塞**。

**拆法（加法，不破坏）**：

| 节点 | 管什么 | widget |
|---|---|---|
| `H3RelayTrimAV`（**冻结**） | **时间轴**：裁重叠 / 沉降 / 重影 | 22 个，**一个不动** ⇒ 旧工作流逐位不变 |
| **`H3RelayPost`（新增）** | **画质域**：跨段统计匹配 / 低频残差 / 段内直方图+白平衡 / 反卷积 / 段体高频迁移 / 糊区锐化 | `images`（唯一必填）+ `guide` + 15 个作用项，**全部默认关** |

- `H3RelayTrimAV` **追加第 4 路输出 `prev_tail`**（= 钉住区最后一帧 = **上一段的末帧**，
  节点手里本来就有，无需额外输入）。**追加在末位**，旧工作流不受影响。
- `H3RelayPost` 的 `guide` 就接这个 `prev_tail`——跨段项（`match_prev` / `lowfreq_pull`）
  才有"缝的另一侧"可对齐；**不接 guide 时这两项自动跳过并在 report 里写明**，其余照常。
- **执行顺序**：跨段色调对齐 → 段内色调对齐 → 补高频 → 锐化收口。
- **纪律**：**以后画质域新功能一律加在 `H3RelayPost`**，`H3RelayTrimAV` 不再长。
- **迁移**：老工作流**无需改动**（TrimAV 签名与取值位置逐位不变，新输出在末位）。
  想用后处理：加一个 `H3RelayPost`，`images` 接 TrimAV 的 `images`，`guide` 接 `prev_tail`。
- ⚠️ **但「无需改动」只对执行成立，对 UI 不成立**：ComfyUI 前端 `LGraphNode.configure()`
  照单全收序列化里的 `outputs` 数组（`this.outputs = cloneObject(info.outputs)`）⇒
  **0.5.0 之前存的图里，那个「裁重叠」节点只有 3 路输出，打开后看不到 `prev_tail`**，
  也就接不上 `guide`（执行不受影响）。解法：重加节点，或给 JSON 的 `outputs` 末尾补
  `{"name":"prev_tail","type":"IMAGE","links":[]}`。`tools/review_050.py` J 节会扫出这类图。
  脚本产线（`l1_api.py`）不受影响——图每次现构造。

### 新增「跨段统计匹配」：`H3RelayPost.match_prev*`（调研 §2 方向二）

**治什么**：成片缝上的**亮度/色档阶跃**（拷贝桥实测 0.0402 → accept FAIL）。

**为什么掩码治不了它**：掩码语义是 `输出 = 模型生成·m + 上段尾·(1−m)`，作用在**钉住窗内部**；
而钉住窗在成片里**被 `H3RelayTrimAV` 裁掉了** ⇒ 成片的缝 = 上段末帧 vs 本段第一个新生成帧。
实测印证：四个 `ramp_top` 值下阶跃 0.0402 / 0.0407 / 0.0421 / 0.0370 **几乎不动**，而跳帧 29.6→14.9→30.0 大幅变。
⇒ 阶跃的真身是**两次独立生成的分布差**，即自回归视频扩散的**曝光偏差（exposure bias）**。

**怎么做**：逐通道 `out = (x − μs)/σs · σt + μt`（**Reinhard 式一阶+二阶矩**），
再按**缝端最强 → 尾端 0**的线性权重与原帧混合；统计量**一次性从 guide 与段头聚合算出**
（不逐帧）⇒ 时间上平滑。
**护栏**：增益截断 `[1/gain_max, gain_max]`、偏移截断 `±offset_max`（防"整段换色"）。
**只对齐统计量，不复制姿态 ⇒ 无重影**（单测用逐通道相关性 ≈1 钉住）。
文献：Reinhard《Color transfer between images》IEEE CG&A 21(5):34-41, 2001 ·
WCT NeurIPS 2017 arXiv:1705.08086 · TTC arXiv:2602.05871。

⚠ 与拷贝桥既有的 `anchor_latent`（**采样前**、Reinhard 矩匹配钉住前缀）是**两层互补**：
那个影响本段**生成**，这个直接压**成片缝阶跃**。

### 新增 `mask_mode="blend"`：重叠区双向融合（调研 §4 方向四）

**怎么做**：与 `ramp` **同一套掩码语义、不同的权重曲线**——
`ramp` 线性升（`top·i/(n−1)`），`blend` 用**窗形**升（`smoothstep` = `x²(3−2x)` 或
`hann` = `(1−cos πx)/2`，**两端导数为 0**）。
依据：重叠区由**两个独立估计**加权平均时，权重取**窗函数**（FlowLong / Unified Long Video
Inpainting 的滑窗 Hamming 混合，arXiv:2511.03272）；SEINE(2310.20700) 把过渡段当 inpainting；
SynCoS(2503.08605) 多段同步耦合共享首尾锚帧。
新 widget `blend_top`（默认 0.5）/ `blend_tokens`（0 = 整窗；>0 = 「只融缝、锁运动」）/ `blend_shape`。

⚠ **诚实标注**：在本仓库的掩码表述下，`blend` 与 `ramp` 是**同一族的不同曲线**，不是新机制；
能否胜过 `ramp@0.5`（跳帧 14.9×）是**实测问题**，本版未验证。

### 新增「复现残留」检测：`detect_settle` 第 4 路（调研 §13 D7）

**为什么需要**：现有三路（硬跳 / 锐度 / 色档）**没有一路能看见复现残留**——
复现帧**清晰**（锐度路盲）、**色档一致**（色档路盲）、**无硬跳**（硬跳路盲）。
**判据**：窗内第 i 帧与**钉住区（前 pin 帧）**的**最小 MAE**（实测分离度 0-255 灰度下
重复 2.81 / 非重复 11.3、45.2 ⇒ 阈值 5/255，分离比 >2×）。新函数 `scan_head_repeat()`。
**🔴 但默认关闭**（`SETTLE_REPEAT_PATH = False`）：本路**只会让裁量变大**，而既有契约是
「宁可维持旧行为也不赌」；实测发现它在**低纹理 / 周期内容**上会误报（合成夹具 `seam_seg`
的 3 帧周期下，`pin` 之后的帧必然与钉住区某帧逐位相同 ⇒ 必报；真实静态镜头同理）。
**启用前必须用真实渲染验证误报率。**

### 测试

新增第 18 组（复现残留 10 项）/ 第 19 组（跨段统计匹配 10 项）/
第 20 组（拆节点 9 项）/ 第 21 组（blend 9 项）。
**共 22 个方面、257 项断言全绿**（`python tests/test_relay_core.py`）；
`python tools/review_050.py` **61 项静态复查全绿**（新增 L 节音频缝 9 项）。

### 复查补正（2026-09-17 第二轮：完整复查抓到的问题）

拆节点本身的功能、参数透传、槽位对齐都是对的（`tools/review_050.py` 48 项全过），
但**「改了代码、文档没跟上」这一类**又犯了几处，逐条修掉：

| # | 问题 | 类型 | 处理 |
|---|---|---|---|
| 1 | `nodes.py` 模块头仍写「六个节点」，漏 `H3RelayPost`；两个类的 docstring 还写着「`settle_frames` 保持 -1」 | 文档不同步 | 改「七个节点」+ 补 Post 行 + 补分工说明；两处 -1 → 0 |
| 2 | `README.md` 离线自测一节仍写「**171 项断言 / 十七个方面**」，分组表只列到 17 组 | 文档不同步 | 改 235 / 二十一 + 补 18–21 四行（`review_050.py` H3c 已加自动对账） |
| 3 | `README.md` 的 `seam_ghost` 表把 `1` 写成「默认」，与同页「默认关」和代码默认 `0` 三处打架 | 文档自相矛盾 | 表头档位改 `0`（默认）、`1` 标注「数值好但观感更差」 |
| 4 | `examples/README.md` 写「16 个节点」 | 文档不同步 | 改 17 + 接线图补 Post |
| 5 | **`examples/make_minimal_workflow.py` 不认识 `H3RelayPost`** —— 重跑生成器会把示例图里的 Post 节点**静默删掉** | 潜在回归 | 生成器补 Post 节点与接线；Note 文案同步（沉降 0 / Post 默认关）；`miss` 清单补 `H3RelayPost` |
| 6 | `tools/add_post_to_example.py` 一次性、写死 `#13/#14`、不幂等 | 工具缺陷 | 重写为**幂等 + 按类型定位**，槽位从 `INPUT_TYPES` 现推（不手抄）= 重跑生成器后可再补 |
| 7 | `tools/review_050.py` 自身有两处**假绿**：`C4 ... or True`、`D3` 比的是两次独立 `rand()` 且 `or True`；H3 只查「有没有数字」不查「数字对不对」；I 只扫示例图不扫存量工作流 | 复查工具失效 | C4/D3 改真判据；新增 **H3b/H3c**（实跑单测取真值，再与 CONTRIBUTING/README 对账）、**H3d**、**J**（扫存量图的过期 outputs）、**K**（走节点做端到端行为抽查）。48 项全绿 |
| 8 | `relay_core.deconv_head_zone` 的 docstring 有两处行内代码被吃掉（「实测： 在  时趋于 0」） | 文本损坏 | 补回语义完整的句子 |
| 9 | **存量 UI 图 `MiniMax-H3官方续接版-latent桥.json` 里两个「裁重叠」的 `outputs` 只有 3 路** —— 前端 `configure()` 照单全收序列化数组 ⇒ 打开后**看不到** `prev_tail`，接不上 `guide` | 存量图未同步 | 补第 4 路输出（已备份 `.bak-20260917-prev_tail`）；README/CHANGES 补「0.5.0 迁移注意」 |
| 10 | 同图 `#9517`：`trim_frames` **未接线且 = 0** ⇒ 这个裁节点**什么都不裁**（接缝会整段重播）；`settle_frames` 存的是 `-1` ⇒ 0.5.0 新默认 0 **对它不生效** | 存量图取值（**需人工定夺**） | 不改（是取值决策不是 bug）；`review_050.py` J 节会一直报出来 |

| 11 | 🔴 **`match_prev` 的目标函数写错了**（真渲染抓到）：统计量对**整个作用区聚合**，而 `guide` 是**单帧** ⇒ 段头**内部有亮度梯度**时修正量变成「段头平均 vs guide」的差，**首帧被推过 guide**、缝上凭空多出一个阶跃。真渲染两臂（唯一变量 = `match_prev` 0↔0.7）实测：纯末→首阶跃 **0.0007 → 0.0088（×12.6）** | **算法口径 bug** | 新增 `stats_frames`：**默认 1 = 统计量只取紧贴缝的那一帧**（与单帧 guide 同口径 ⇒ 修正量恰是"缝上的阶跃"，首帧被拉向 guide 而**不会越过**）；`0` 保留旧口径仅供对照；`>1` 取前 N 帧聚合。单测 19.9–19.12 把事故钉成回归（239 项断言），`review_050.py` K7 复现钉子。详见下方专节 |

### 🔴 `match_prev` 统计量取样窗修正（0.5.0 真渲染验证的产物）

**怎么发现的**：0.5.0 首次真渲染（2 段 cond 链，两臂唯一变量 = `match_prev` 取值）。
对照臂（Post 全关）缝处**纯末→首阶跃 0.0007**（几乎无感）；实验臂（`match_prev=0.7`）**0.0088**，
反而越过 WARN 线。离线用对照臂的帧复现，确认不是编码噪声、不是采样不可复现
（两臂 `stage_00001` latent **张量流逐位相同**），而是**口径错配**：

```
guide(上段末帧) luma = 0.2523
段头 12 帧 luma      = 0.2516, 0.2432, 0.2428, ...   ← 首帧已贴住 guide，第 2 帧起掉 0.008
段头聚合均值          = 0.2451                        ← 被后续帧拉低
⇒ offs ≈ +0.0072（"聚合均值 → guide"）而非 0.0007（"首帧 → guide"）
⇒ 首帧 0.2516 → 0.2573，**越过 guide**，缝上造出新阶跃
```

**我们要的目标函数是「首帧 ≈ guide」，不是「段头均值 ≈ guide」。**

**修法**：`match_prev_stats(..., stats_frames=1)` —— 统计量改为取自**紧贴缝的那一帧**。
`H3RelayPost` 追加 widget `match_prev_stats_frames`（INT，默认 1；**追加在末位**，旧图取值不前移）。
`>1` 用于「段头前几帧整体就是一个色档」的情形；`0` = 旧口径（**仅对照**）。

**回归钉子**：单测 19.9（取紧贴缝那帧 ⇒ 首帧落到 guide）／19.10（旧口径复现"推过 guide"，×3.6）／
19.11（无梯度段头两种口径**必须等价**，修正不许改变正常情形）／19.12（新 widget 在末位）；
`tools/review_050.py` **K7** 离线复现同一机制。

> **三条纪律（本轮教训）**：
> 1. **改默认值救不了存量 UI 图** —— `widgets_values` 里显式存下的值优先于新默认；
>    「换默认」只在**新建节点/新建图**时生效。要动存量图必须显式改值。
> 2. **复查脚本自己也会假绿** —— 恒真表达式（`or True`）、两次独立随机数比较、
>    只查"有没有"不查"对不对"、只扫示例不扫存量，这四种写法都出现过。写完复查脚本**要反过来问：它现在能不能失败？**
> 3. 🔴 **写「对齐 guide」这类修正前，先问：对齐的是谁与谁？** —— `match_prev` 的 bug 不在实现、
>    在**目标函数**：它对齐「段头聚合均值 ↔ guide」，而缝的判据要的是「**紧贴缝那一帧 ↔ guide**」。
>    两者在「段头内部无梯度」时等价（单测 19.11 守着这条），一有梯度就分道扬镳。
>    **离线复现（拿现成帧直接跑节点）能零 GPU 把这类口径错判死** —— 别急着烧渲染。

### 🔴 默认行为修正：`settle_frames` 由 `-1`（自动）改为 `0`（不裁沉降）

**依据**（同一条段、只改裁量；反推各 run 实际 settle）：

| settle | 缝处最大单帧跳（归一） | 目检 |
|---|---|---|
| **0** | **0.020** | **「几乎无感」** ✅ |
| 8 | 0.044 | 「跳了」 |
| 16 | 0.055 | 「跳」 |

⇒ **「裁沉降」才是缝处跳帧的源头**（裁切 = 时间跳跃，裁得越多跳得越大）；
而留下的那几帧只是**清晰度的渐变**（先略糊、再恢复）——人眼对这种渐变的容忍度极高。
⇒ **拿「可容忍的渐变」换「不可容忍的突变」不划算**，故默认不裁。
`-1`（自动）保留为可选项，tooltip 已明确标注它会引入跳帧。
`max` 同步由 34 提到 36（= `SETTLE_CAP`）。

### 新增「糊区锐化」：`settle_sharpen` / `settle_sharpen_frames`（画质域修复）

**动机**：默认不裁沉降后，成片段头会保留几帧「模型重绘导致的糊」。
时间轴两条路都不能走（**裁它 → 跳帧**；**不裁 → 留糊**）
⇒ 改在**画质域**修：**不裁、不动时间轴，只提升糊区高频 → 从原理上不可能引入跳帧**。

- `relay_core`：新常量 `SETTLE_SHARPEN` / `SETTLE_SHARPEN_FRAMES` / `SETTLE_SHARPEN_MAX`；
  新纯函数 `unsharp_frames(images, amount)`（3×3 高斯 unsharp mask，纯 torch、NHWC 进出、
  **replicate pad**——zero pad 会在画面边界吃出假响应）与
  `sharpen_head_zone(images, frames, amount)`（对开头 N 帧做**渐变**锐化：缝端最强 → 尾端 0，
  因为糊本身就是渐变的；**只做一次卷积**，再按权重与原帧混合）。
- `H3RelayTrimAV`：新 widget `settle_sharpen`（FLOAT，默认 **0 = 关**，0–1.5）与
  `settle_sharpen_frames`（INT，默认 24），**追加在 optional 末位**；
  接入点 = 裁切之后、重影之前；report 加一行 `✦ 糊区锐化`。
- **帧数守恒、不动音频、零采样开销。**

**真实成片验证**（`minimal_cond4.mp4` = settle 0 的节点隔离成片；零 GPU）：

| amount | 糊区首帧锐度 | 相对基准 | 糊区第 8 帧 | 最大像素改动 |
|---|---|---|---|---|
| 0.00（原） | 0.002207 | 0.873 | 0.001294 | 0 |
| 0.60 | 0.002408 | 0.953 | 0.001370 | 0.091 |
| 1.00 | 0.002547 | **1.008** | 0.001422 | 0.151 |
| 1.50 | 0.002728 | 1.080 | 0.001489 | 0.227 |

⇒ amount ≈ 1.0 时糊区首帧拉回基准；但**深塌陷帧（第 8 帧）只提升 ~10%**。

> **诚实边界**：**锐化只能恢复「对比度」，不能恢复「已丢失的真实细节」。**
> 对「结构还在、只是软」的重绘糊有效；细节彻底丢了救不回来。
> 副作用：放大噪声、强边缘可能出 halo → `amount` 上限设 1.5（防呆）。

### 缝帧重影（极短交叉溶）：`H3RelayTrimAV` 新增 `seam_ghost` / `seam_ghost_alpha`

**动机**：钉住区之后的「沉降区」必须裁掉，否则留下「先模糊再清晰」的复现尾巴；
但**裁切 = 时间跳跃**——裁得越多、缝处跳得越大（`trim_jump_curve` 已把该代价量化）。
实测裁 16 帧 → 跳帧归一 **0.055**（标定：≥0.030 肉眼可见「跳」）。

**解法**：把裁后**首帧**替换为「上段末帧 ⊕ 本段首帧」的加权混合
（`a·prev_last + (1−a)·head[0]`，默认 `a=0.5`）。
机理 = 把缝处**一跳拆成两个半跳跨两格**，一闪而过 → 体感约等于一镜到底。

- `relay_core`：新常量 `SEAM_GHOST_FRAMES=1` / `SEAM_GHOST_ALPHA=0.5`；
  新纯函数 `seam_ghost_blend(head, prev_last, frames, alpha)`。
- **节点手里就有上段末帧**——decode 的前 `pin` 帧本来就是上段尾的复现，
  故 `images[pin-1]` 即上段末帧，**无需任何额外输入**。
- **帧数守恒**（替换而非插入）⇒ 音频不必跟着动 ⇒ **无 A/V 漂移**；
  **零采样开销**（纯张量后处理，不碰采样器）。
- widget **追加在 optional 末位**（`seam_ghost` INT 默认 1 / 0–6；
  `seam_ghost_alpha` FLOAT 默认 0.5），旧工作流取值逐位不变。
- 参数不接线时行为与 0.4.3 完全一致（除默认开 1 帧重影）。

**生产验证**（受控 A/B：同 latent、同 seed 88161002、同裁量 38 帧，**唯一变量 = 重影开关**）：

| 指标 | 重影 OFF | 重影 ON |
|---|---|---|
| 跳帧（归一，阈值 0.030） | **0.055** ⛔ 跳 | **0.029** ✅ 不跳 |
| 缝处逐帧帧差 | f191=**14.08** | f191=**7.22** f192=**7.51** |
| 段头 20 帧 ÷ 段体 | 1.06 | 1.08（无退化） |
| 成片帧数 | 346 | 346（守恒） |

⇒ 一跳 14.08 拆成 7.22 + 7.51 跨两格，与离线预测（7.25 + 7.50）几乎逐位吻合。
代价：重影帧自身锐度 0.41×（两帧混合的固有代价，**仅 1 帧**）。

### `H3RelayTrimAV` report 升级：边界跳帧 / 多通道观测剖面 / 裁量→跳跃曲线

- **边界跳帧观测** `boundary_jump_ratio(images, pin, max_settle)`（+ 常量
  `BOUNDARY_JUMP_WARN=6.0`）：量「钉住区末帧 → 首帧新内容」的帧差 ÷ 段内基线。
  动机：沉降公式 `settle = j + 1 - pin` 在 `j == pin-1`（跳变正好落在钉住区边界）时
  **返回 0**——语义没错（没有"多余复现"可裁），但**掩盖了「边界本身是断的」**。
  实测：cond 旧 2.2× 正常 / condfix 8.4× 报警 / copy hard 33.8× 报警，与目检一致。
- **多通道观测剖面** `observation_profile(images, pin, scan)`：锐度 / 亮度 / 色阶(RGB)
  / 帧差五路，各自带基准。沉降检测本来就是三路（帧差 / 锐度 / 色档），
  只报一路 = 只开了三分之一的窗。
- **裁量→跳跃曲线** `trim_jump_curve(images, pin, max_settle)`：裁 N 帧会跳多少，
  **裁之前就把它算出来**供权衡。实测曲线预测「裁 12 帧 ≈ 9.0×」、
  真跑「裁 16 帧 = 9.0×」精准命中。
- 锐度路改为**裁到恢复点**（`SETTLE_RECOVER_TARGET=0.8`）：不再停在「最后一个塌陷帧」，
  而是裁到锐度回到 0.8×ref 的首帧——把「先模糊再清晰」的恢复尾巴切掉。
  `SETTLE_SCAN` / `SETTLE_CAP` 24 → 36。

### 梯度类指标的分辨率无关化：L1 域归一 + L4 跨尺度仲裁

**问题**：3×3 Laplacian 是固定像素核 → 锐度指标**分辨率相关**。
实测同一份内容在 464×800 / 928×1600 之间绝对基准跨度 **37.4×**；
高分辨率下扣测最细噪声级细节、**对中频的「糊」迟钝** → 深塌陷漏检。

- **L1 域归一** `_canonicalize(images, short_side)`：所有梯度类指标先归一到
  `ANALYSIS_SHORT_SIDE=256`（`mode="area"`，**只缩不放**）。
  实测绝对基准跨度 37.4× → **5.2×**，比值跨尺度差 0.213 → **0.071**。
- **L4 跨尺度仲裁** `_blur_collapse(images, pin, short_side)` + `SETTLE_CROSS_SCALE`：
  同一现象要在 **256 与 384 两个尺度上都出现**才算真塌陷；
  只在单一尺度出现 = 尺度伪影 → 不动刀。928×1600 塌陷 0.45 → **0.33**（修好高分辨率漏检）。

### 测试

新增 12.23（重影帧数守恒）/ 12.24（首帧 = 上段末帧 ⊕ 裁后首帧，其余帧逐位不动）、
13.14 / 13.15（解耦后拓窗行为）、`boundary_jump_ratio` / `trim_jump_curve` /
`observation_profile` / `_canonicalize` 相关断言。**共 171 项全绿。**

## 0.4.3 — 2026-09-15

### 拷贝桥新增 `mask_mode="ramp"`（噪声斜坡「软证据」接缝）

前沿调研（Diffusion Forcing arXiv:2407.01392 / SDEdit arXiv:2108.01073 /
RePaint arXiv:2201.09865）指出硬掩码接缝是噪声谱的两端奇异点（钉住区 σ=0、
新内容 σ=σ_max），交界成为分布不连续面，色档/曝光在缝处"台阶化"。读宿主源码
实证 **ComfyUI 原生 H3 契约本来就支持连续掩码值**：

- `comfy/ldm/minimax/model.py` forward()：*"masked rows run at their own
  strength: mask value m puts a row at sigma = m * sigma_stream"* —— 逐 token
  的 timestep 标签 = 1 − m·σ；
- `comfy/model_base.py` `MiniMaxH3.scale_latent_inpaint()` 的 x_blend_weight
  按连续 m 混合，外层每步输出 blend `out·m + anchor·(1−m)`（RePaint 式逐步回锚）。

因此 ramp 档无需任何采样器钩子：`noise_mask` 的连续值就是 Diffusion-Forcing 式
的逐 token σ 标签。**语义与 taper 相反**：taper 头部 m=1.0 无锚全重绘；ramp 全程
m≤ramp_top<1，每一步都被 (1−m) 权重锚回拷贝尾——"软证据"，不是"没证据"。

- `relay_core`：新常量 `SEAM_RAMP_TOP=0.25`（+MIN/MAX），`MASK_MODES` 追加
  `"ramp"`；新纯函数 `prefix_ramp_weights(steps, ramp_top, ramp_tokens)`——
  整窗铺开时远端严格 0（硬钉）、缝端严格 ramp_top；`ramp_tokens>0` 只松缝端
  k 个 token（"只松缝、锁运动"）；ramp_top 越界按 [0,1] 端点截断，0 退化为 hard。
- `H3RelayCopyBridge`：`mask_mode` 追加 `"ramp"`，新 widget `ramp_top`（默认
  0.25，max 0.95 防呆）与 `ramp_tokens`（默认 0 = 整窗铺开）**追加在 optional
  末位**——旧工作流 widgets_values 按位置对应，行为逐位不变（测试 17.13 钉住）。
- 测试组 17（13 项断言，共 167 项全绿）：权重对齐 / 首 token 硬钉且严格单调 /
  全程 m<1（有锚）/ 拷贝不受掩码模式影响 / 非前缀区恒 1 / 窄斜坡 / 截断防呆 /
  ramp_top=0 退化 hard / 设备 batch 一致 / widget 追加铁律。
- 文档：README 拷贝桥导语与参数表补 ramp 行；CONTRIBUTING 计数 154/十六→167/十七。

## 0.4.2 — 2026-09-15

### 使用缺陷修复（全面审查 17 项：高危 2 / 中危 7 / 低危 8）

**高危（必崩路径）**
- `audio_tail_from_latent` 导出音频尾段复用分支返回 3 元组，两个调用方按 4 值解包
  → 只要 latent 带 `apt_h3_export_tail_audio_latent` 键（Apt 链快路径）必崩
  `not enough values to unpack`。补齐第 5 路 `grid_off` 返回值，两个调用点同步。
- `save_av_latent` 元数据用 `ord(c)` 逐字符塞 uint8：note 含任何非 ASCII（中文备注
  是 tooltip 鼓励的用法）即 `value cannot be converted to type uint8` 崩溃。
  改为 UTF-8 字节流（旧 ASCII 文件读回完全兼容）。

**中危**
- 「Latent 读」stage_index=0 的引导报错是死代码（`_stage_path` 先抢抛「不能为负」）
  → 校验顺序前移，用户看到正确引导。
- 音频栅格偏差告警被 `if overhang:`（0.0 假值）永久吞掉 → 新增 `grid_off` 标志，
  上层 notes 出「⚠ 音频栅格与视频帧数偏差超出半整步」告警；偏差口径同时订正为
  **源段总帧数**（原按尾窗量恒为巨值，属调用方误用）。
- 拷贝桥 `pin_audio=False` 仍无条件要求 target 有音频流 → 关音频即可走纯视频 latent。
- `_LAP_KERNEL` 进程级可变全局按 device 抖动重建 → 改 per-device dict 缓存。
- 内存峰值：`scan_head_jump` 对整段物化全量帧差（与同文件自述的小窗优化矛盾）
  → 先切窗再做差；色档路 `images.abs().max()` 全量拷贝 → 用已切小窗估计；
  落盘 `.to(cpu).contiguous().clone()` 三重拷贝 → 去冗余 clone。
- 拷贝桥 `noise_mask` 硬编码 CPU + batch=1 → 与 latent 同设备、batch 维随 target；
  taper 权重张量同步建在掩码设备上（GPU latent 时原写法跨设备赋值报错）。
- 布局契约「找不到上游 → 放行」的降级结论被永久缓存 → 降级不写缓存，下次重试。

**低危**
- `fps` 服务端校验（API 提交 0/NaN → 可读报错，不再除零）；
- run_id 非法字符改**替换为 `_`**（消除 "my/film"≡"myfilm" 静默撞目录）+
  Windows 保留名（NUL/CON…）避让；
- 落盘改**同目录 .tmp + os.replace 原子写**（崩溃不留截断文件）；
- Chain 前端多分组共存时 `executing(null)` 互相推进段号 → 按「本轮是否执行过
  本组节点」过滤。

**文档**
- 🔴 **掩码语义显式化（taper 陷阱）**：原文「taper = 头部 1.0 线性降到缝端 seam_min」没写清
  **1.0 是什么意思**，导致下游把 taper 当成「软一点的硬锁」当默认跑了 23 次（实测：
  某 4 段链三条缝的段首偏差 0.844/0.761/0.754，硬锁应为 0.00000 ⇒ 观感「续不上」，且 TrimAV 仍按
  窗口帧数照裁 ⇒ 顺带裁掉真实剧情）。现于 `nodes.py` tooltip（`mask_mode`/`seam_min`）、
  `relay_core.prefix_taper_weights` docstring、`H3RelayCopyBridge` 类文档串、README 参数表与
  节点表**五处**写明：`denoised = 模型生成 * m + 上段尾 * (1-m)`，**m=0 才钉住、m=1 是重绘**；
  taper 档每帧留 `seam_min`~100% 重绘自由度，**钉住区没有被钉住**，仅作对照实验档。
  纯文案，零行为变更（测试计数不变）。
- requirements/README 订正「import 期零第三方依赖」不实陈述（torch 是顶层 import，
  缺失=整包注册失败）；README 节点表补拷贝桥行 + 参数表补全 5 参数；
  `__init__` 节点清单 5→6；排障表补「节点没出现→查 torch」「宿主太旧→续接静默无效」
  「上游网格已变」三条；补宿主 ComfyUI 版本要求说明；陈旧日志示例文案同步当前口径；
  CONTRIBUTING 计数与「测试必须能 import ComfyUI」的实情订正。

测试 134 → **154 全绿**（新增组 16：导出音频分支 / 中文 note / 服务端校验 /
掩码设备 / 契约降级缓存 / run_id 清洗 / 纯视频拷贝桥；**组 14 补掩码语义钉子 2 条**：
hard 钉住区全 0 = 真钉住 / taper 钉住区无一处为 0 = 不钉住）。

## 文档补全 — 2026-09-14（无代码变更，仍为 0.4.1）

- README 新增「🎬 接缝处的对话规避与音频处理」一节：段首缓冲纪律（续接段开头
  ≥1.2s 不排台词）、**台词安全时刻公式**（prompt ≥ 沉降裁头 + 音频补丁 N + 余量，
  默认 ≥3.2s）、末帧锚链出词纪律、组装层音频缝三件（crossfade / room tone 头部补丁 /
  床环铺 + 床声尾补）与频带判据（60-250Hz：补丁前 4×环境声 → 补丁后 ≈1.0×）、
  长片段数无上限（stage 链 9999）与逐缝验收口径。
- 自测计数订正：103 → **134 项 / 十五个方面**（补组 13 模糊型沉降、组 14 拷贝桥、
  组 15 色档收敛三行——0.3.1/0.4.0/0.4.1 三次加组均漏更本表）。
- 补记 0.4.0 / 0.4.1 版本条目（此前只发代码未记 CHANGES，见下）。
- 排障表新增行：台词起音被裁 / 缝区伪音的处置指引。

## 0.4.1 — 2026-09-13

### 色档收敛信号（沉降检测路径 3）

注噪/taper 拷贝桥的「收敛尾巴」会漏进可见区（首跑实测首帧重影）——锐度与帧差
对此盲。新增路径 3：体区 RGB 中位数 + MAD 自适应阈值，窗内须观察到「偏离→回归」
才动刀；taper 收敛尾巴 3 帧检出。三路（硬跳/锐度/色档）取最大。
测试 130 → **134 全绿**。

## 0.4.0 — 2026-09-13

### H3RelayCopyBridge 拷贝桥（节点 5→6）

上一段尾 AV latent **逐位拷贝**进本段初始 latent + 噪声掩码——钉住区**不重绘**，
复现发糊/漂移这一类伪影从机制上消失。掩码两档：hard（默认）/ taper（锥形实验档）；
音频尾拷贝仅作采样上下文；帧网格与相位对齐复用既有三重 raise 防线；
SelfLift 原生消费 noise_mask 实测确认——**零 patch、不绑采样器**。
测试 116 → **130 全绿**（组 14：位级拷贝/掩码结构/音频尾/跨分辨率/前缀占满/
相位失配/节点契约）。

## 0.3.3 — 2026-09-13

### 沉降检测升级：段体自参考稳健统计 + 假跳验身（多维度/无量纲方案）

实测暴露的结构性问题：分辨率/步数/LoRA/场景内容都会整体移动锐度与帧差的绝对量级，
任何依赖绝对量纲或单点参考的判定都会在某个参数组合下失效。v0.3.3 三层重构：

1. **参考分布 = 段体自身**（窗外 ≥8 帧，median + 1.4826×MAD）——4 步软渲染、运动模糊、
   平坦场景自动进基准，零配置；z 分数负责「是不是异常」，比值门槛（本就无量纲）
   负责「是不是深到值得动刀」，两道闸缺一不可。
2. **帧差基线量纲修复**：旧 BASELINE_FLOOR=1.0（0-255 量纲）把 0-1 产线数据的
   帧差路径整个抬死（基线 0.0076 被抬到 1.0）——帧差法在产线上**从未生效过**；
   改为随数据量纲缩放（0.004×满量程 ≈ 1/255）。
3. **锐度度量换 Laplacian 均方**（replicate pad）：mean-abs 版对 mild 塌陷钝 3 倍
   （HD 实测 0.29-0.45× 在它眼里是 0.79-0.83×）；方差度量放大高对比边缘损失。
4. **硬跳验身**：跳点须过「体 z>8 + 比值>4×体中位」双门槛，且跳后 2-5 帧锐度
   回到体分布才采信——跳完仍糊 = 假跳/闪烁，否决后由锐度路定界。
5. **深度证据闸**：塌陷最深 <0.35×基准 才算真模糊——防「段体天生比头锐」的
   正常渐变被误裁（13.11/13.4 双向回归）。

真实数据双案验证：SD 深塌陷 settle=9（与手推一致）、HD mild settle=4（深线 0.29<0.35）。
测试 111 → **116 全绿**（13.11 一致偏软不误裁 / 13.12 渐进模糊 / 13.13 假跳否决 /
13.9-13.10 量纲不变性）。

## 0.3.2 — 2026-09-13

### 修复：锐度基准改双参考——mild 塌陷对「软复现基准」不可见

HD 正规参数实测（L1 剧本A）：缝后 f0-f3 清晰度只有源画面的 0.29-0.45×，
但**复现区自身也已发软**——0.3.1 拿复现区当基准，这点塌陷根本不过 0.35× 阈值
（目检「第二帧马上糊了一点点」）。

**双基准**：`ref = max(复现区锐度, 窗外新内容区锐度)`（win 之后 40 帧的中位）——
窗外区才是「这台模型此刻能画多锐」的真基准。HD 实测量出 settle=4，裁后缝区全锐。

测试 109 → **111 全绿**（13.7 HD-mild 动机案例 / 13.8 双基准语义断言）。

## 0.3.1 — 2026-09-13

### 修复：沉降检测对「模糊型沉降」盲（帧差法看不见重绘发虚）

L1 剧本A 两段实测：缝后 f2-f7 清晰度塌到正常值的 15%，画面人物全对但发糊。
`detect_settle` 的帧差法只认「硬跳」——模糊切换的帧差不抬头，恒盲（返回 0）。

**补第二信号（高频能量塌陷-恢复）**：以钉住区锐度（≈源画面锐度）为基准，
窗内出现「深塌陷（<0.35×基准）且可见恢复（>0.5×基准）」→ 沉降 = 最后塌陷帧 − pin + 1。
三重防误伤：无深塌陷不动刀 / 窗内看不到恢复宁少勿多 / 上限仍是 MAX_SETTLE。
帧差法保持第一优先（硬跳场景行为不变，组 12 全部回归通过）。

测试 103 → **109 全绿**（新增组 13：模糊沉降 / 糊过窗不裁 / 无塌陷不裁 / 长塌陷贴满上限 / 换种子稳定）。

## 0.3.0 — 2026-09-11

### 修复：拼接处 1 帧硬跳 + 上一段字幕/文字串进本段开头（`crop` 被钉死等于 `pin`）

钉住区是干净硬条件，紧邻其后的自由 token 会先**复现**钉住内容，之后才切到按 prompt 生成。
切换点落在哪一帧**逐段不同**（实测：107 帧段落在钉住区之内；73 帧段落在原第 22→23 帧之间，
MAE 6.52 → 102.27）。而旧实现 `plan.trim ≡ pin`，切换点落在钉住区之外时**没有被裁掉** ——
裁后第 0 帧仍是"上一段的复现帧"，于是接缝处 1 帧硬跳、上段的字幕被带进来。

**修法：把裁决定从"配置端"搬到"观测端"。**

- 切换点是**逐段不同的量**，不该是要用户填的配置。裁节点收到的 `images` 就是完整解码画面，
  真实切换点此刻就在手上 —— 新增 `relay_core.detect_settle()`，**就在那里量**。
- `H3RelayTrimAV` 新增可选 `settle_frames`（追加在 `optional` **末位**）：
  `-1`（默认）= 自动量、`0` = 只裁钉住区（0.2.x 行为）、`N` = 固定多裁 N 帧（各段等长时用）。
- `plan_relay(..., settle_frames=0)` 与 `RelayPlan.settle` 同步提供（库 API；**桥不加 widget**，
  所以旧工作流的桥节点 zero 改动）。
- **失败安全**：量不出切换点 ⇒ `settle=0` ⇒ 与 0.2.1 逐位一致，最坏不会更差。
  位置超出上限（`MAX_SETTLE=12`，≈0.5 s）一律不动刀。
- **成本**：只对 `images[:pin+MAX+2]` 切片做帧差，448×768 实测 **39 ms/段**，零额外依赖、零显存。

### 修复：接缝自检把「段内真实切镜」当成接缝，建议把新内容裁掉

旧 `find_head_jump` 取**前 40 帧全局 argmax** 且不设上限：本段第 25 帧若有真实切镜，
会被判成接缝并建议 `trim += 26`。现在：

- 自动检测用**窄窗**（`pin-1 .. pin+MAX_SETTLE`），段内切镜根本不在窗内；
- 裁后自检只在位置 ≤ `MAX_SETTLE-1` 时才给"再裁 N 帧"的**可执行**建议，
  超出则只报位置与数值（明说"大概率是本段自己的切镜，不是接缝，不动刀"）；
- 新增 `scan_head_jump()` 作为"原始观测"原语，`find_head_jump` / `describe_head_jump`
  按各自口径解释（`find_head_jump` 行为与签名不变）。

### 修复：自检文案让用户去改一个**改不了的格子**

旧文案建议"把 `trim_frames` 从 22 改成 23"，但该 widget 被桥的第 3 路连线接管后，
ComfyUI 前端会把它**隐藏**，用户根本改不了；要改只能断线，断线又失去自动同步。
新文案指向 `settle_frames`，并明确点出别改 `trim_frames`。

### 兼容性

| 对象 | 结论 |
|---|---|
| 桥 `H3RelayMotionContext` | **未改动**（不加 widget、`plan.trim` 输出语义不变） |
| 旧工作流 JSON | 裁节点 `settle_frames` 尾缺 → 取默认 `-1` → **打开即生效，无需重连** |
| 已落盘的 `output/relay_kit/**/*.safetensors` | 不受影响（格式与内容不变） |
| 想维持旧行为 | `settle_frames = 0` |
| `plan_relay` 默认参数 | `settle_frames=0` → 库调用者不受影响 |

### 文档 / 上手体验

- **README 新增「🚀 5 分钟跑通」**：编号步骤（第 1 段 → 只改一个「段号」→ 第 2 段）、
  **跑通判据**（应看到哪三行日志）、**参数填值对照表**（`stage_index` / `run_id` 在第 1 段与
  第 2 段分别填什么）、以及三个最常见的翻车点。
- **接线一节重写**：主图改成**官方节点路线**（`MiniMaxH3ImageToVideo` /
  `MiniMaxH3ReferenceToVideo`，ComfyUI 内置），带全部端口下标；
  `CSGlideCastCS`（→ `ComfyUI-Banzhang-All`）与 `SelfLiftH3Sampler`（→ `comfyui-SelfLift`）
  明确标注为**第三方、非本包依赖**，并给出"只要求该节点输出 CONDITIONING + LATENT"的换法。
  另补 **Chain 必须与桥/落盘同分组框**的说明（此前只在 0.2.1 的一行注释里）。
- **新增 `examples/`**：`minimal_relay_official.json`（16 节点最小演示，画布内含步骤注释框，
  一个 `PrimitiveInt`「段号」同时驱动桥与落盘，所以跑下一段只改一个数）+ 生成器
  `make_minimal_workflow.py`。生成器从 live `/object_info` 读真实 schema 拼 JSON ——
  **手写 UI 工作流必然踩 `widgets_values` 按位置对应 + `control_after_generate` 注入的坑**
  （见 0.2.1 记录），生成器把这两件事变成 schema 推导。
- **计数口径更正**：0.2.1 写的"96 项断言"是按**源码行数**统计的（含互斥分支），
  **实测执行数是 80**；本版起一律报实测执行数（现 103）。

### 本版验证记录（2026-09-11）

| 验证 | 结果 |
|---|---|
| `tests/test_relay_core.py`（真实 ComfyUI 环境，`COMFYUI_PATH=I:\ComfyUI`） | **103 / 103 通过**；运行时契约打印「上游 FRAME_PER_TOKEN = (1, 4, 4, 4, 4)，与本包一致」 |
| `tools/check_ui_workflow.py examples/minimal_relay_official.json` | **问题合计 0 条**（`Note` 是前端虚拟节点，1 条提示属正常） |
| **向后兼容**：把 0.2.1 存的真实工作流放到 0.3.0 schema 下校验 | **「槽位与取值全部合法」** —— 裁节点该格尾缺 → 取默认 `-1`，旧工作流不用重连 |
| 生成器 vs 真实工作流的槽位顺序逐节点比对 | 桥 / 落盘**完全一致**；裁节点仅差新增的 `settle_frames`（预期） |

仍未做：真机 A/B（见下方「已知遗留」）。

### 测试

`tests/test_relay_core.py` 新增第 12 组，覆盖：
`settle` 只动 crop 不动 pin/锚位/音频窗、
`pin+settle ≥ 段长` 必须 raise、自动检测四个场景（切换点在钉住区内 / 恰在 pin→pin+1 /
段内有切镜 / 远超上限）、裁节点三条分支（自动 / 关闭 / 固定）、`trim_frames=0` 不触发检测、
`settle_frames` 位于 `optional` 末位。

**执行结果：103 项全绿。**

> 计数口径更正：0.2.1 写的"96 项"是按**源码行数**统计的（`check(` 90 − 1 定义 + `expect_raise` 7），
> 其中 16 项落在互斥分支里不会同时执行；**实际执行数是 80**。本文档起一律报实测执行数。

### 已知遗留（未修，文档化）

- **D4**：`apply_relay` 会丢弃落在钉住区内的旧锚（含 fl2v 的首帧锚）。这对续接是**正确**的
  （首帧本来就该是上一段尾部），但若用首帧锚放标题卡会"消失"。已在 `plan.notes` 留痕。
- **D5**：首段 `trim=0` ⇒ 首段成片比其他段长 `pin + settle` 帧。要各段等长需把首段生成长度
  设为"其余段长 − pin − settle"。
- **待 A/B（未验证）**：音频改挂关键帧的 `audio_latent`；`minimax_visual_cond_noise_aug` 作软锁旋钮。

---

## 0.2.1 — 2026-09-11

### 修复：`H3RelayMotionContext` 在「说清是第 N 段却拿不到上一段」时静默直通
`stage_index ≥ 1`（明说了这是第 2 段及以后）但 `context_latent` 没接、`run_id` 又是空串时，
旧行为是**悄悄按独立段直通**——工作流一路绿灯跑完，产出的却是没有续接的接缝。

这正是本工作流的常见误操作：手改段号时只改了「落盘」忘了改「桥」（或反之），
或换了新片子忘了填 `run_id`。旧行为下**没有任何提示**，只有目检才发现接不上。

改为**硬拦**（`RuntimeError`），消息里说清是哪一段、缺哪个字段、怎么改；
若确实是独立段，提示把 `stage_index` 改回 0。
（口径延续本包既有纪律：硬错误必 raise，不静默降级。）

### 开源就绪（可被他人直接复用）
- **`LICENSE`**（MIT）、**`requirements.txt`**、**`pyproject.toml`**、**`.gitignore`** 补齐。
  此前 README 声称 MIT 却没有 LICENSE 文件，依赖也没声明。
- **单测可移植**：`tests/test_relay_core.py` 不再硬编码本机 ComfyUI 路径，
  改为自动上溯定位 + `COMFYUI_PATH` 环境变量兜底；找不到时给出可读报错而非 `ImportError`。
  任意 CWD 下 `python tests/test_relay_core.py` 均可运行（已实测）。
- **随包体检工具** `tools/check_ui_workflow.py`：校验 UI 工作流的 widget 槽位是否错位，
  可自动定位 ComfyUI 根目录，支持 `--comfyui` / `COMFYUI_PATH` 覆盖。
- **默认值中性化**：`run_id` 默认由内部项目名改为通用的 `relay`，tooltip 示例改为 `myfilm` / `ep01`。
  （只影响新拖出的节点；已保存的工作流里是显式值，不受影响。）
- **`relay_core` 保持「纯算法层」**：不 import 任何 ComfyUI 模块，
  `import relay_core` 在无 ComfyUI 环境下也能工作（已实测），便于单独复用与单测。

### 修复：`H3RelayChain` 的状态提示前端**无处显示**（点了按钮没反应且无提示）
前端 `web/relay_kit_chain.js` 一直往名为 `status` 的 widget 写状态，但后端
`H3RelayChain.INPUT_TYPES` **只声明了 `segments`，根本没有这一格** ——
所有提示（"⚠ 分组没放对，需要恰好各 1 个"、"⚠ 排队失败"、"已排队，采样中…"）
**只进了浏览器控制台，界面上一个字都看不到**。这正是"按钮点了没反应"的典型成因。

- 后端补一个**可选 `status: STRING`**（放在**最后一个** widget：旧工作流里少这一格
  只会走默认值，不会让它前面的取值错位）；`noop` 改为吃 `**kwargs`。
- 前端找不到该 widget 时改为 `console.warn`，避免将来再变成无声故障。
- 单测第 11 组（4 项）守住"后端有这个格子 + 排在最后 + JS 找的名字一致"。

### 加固：`streams_from_latent` 不再把普通张量当 NestedTensor 拆 batch 维
旧写法用 `hasattr(samples, "unbind")` 判定"是不是 NestedTensor" ——
**任何 `torch.Tensor` 都有 `unbind`**，普通 `[B,C,T,H,W]` 会被沿 batch 维拆开：
`B=1` 时碰巧看不出错，`B>1` 时**静默产出 B 条"伪音频流"**。
改为显式按类型分支：`NestedTensor` → 按 `.tensors` 拆；list/tuple → 原样；
单个张量 → 当作**一条**流。单测第 10 组（6 项）覆盖 B=1 / B=4 / list / 非张量。

### 测试
`tests/test_relay_core.py` **96 项断言全绿**（+10：第 9 组 5 项、第 10 组 6 项、第 11 组 4 项，含合并计数）。

### 文档修正
- README「节点」表补全第 5 个节点 🔗 H3 续接连跑 Chain（此前仅在独立小节描述，未入表）。
- README / CHANGES / CONTRIBUTING 的断言数由 80 更正为 96（`check(` 90 − 1 定义 + `expect_raise` 7 = 96）。
- `nodes.py` 文件头注释节点数「三个」更正为「五个」。

---

## 0.2.0 — 2026-09-11

### 新增：🔗 H3 续接连跑 Chain（`H3RelayChain` + `web/relay_kit_chain.js`）
UI 自动连跑控制器：找到同一分组框里的「桥 + 落盘」，自动推进两处 `stage_index` 并排队。

- **▶ Run** 按当前段号跑一次（不满意重跑，覆盖同段号文件）
- **✔ Approve** 段号 +1（桥/落盘同步改）并跑下一段
- **⏩ 连跑** 按 `segments` 循环（0 = 无限直到 ⏹ Stop）
- **⏹ Stop / ↺ Reset** 中途停 / 段号归 0
- 找节点规则：优先同分组框；未分组时全图找唯一一对；多对则提示先分组
- 纯前端推进 + 前端事件驱动（`executing(null)` / `execution_error`），后端节点不在执行路径
- 行为对标 `ComfyUI-H3-Motion-Context` 的 Chain（GPLv3，仅参考行为规格，代码独立实现）

### 新增：运行时契约（`layout_contract.py`）
桥 / 裁节点执行前追着上游 import 链找 `FRAME_PER_TOKEN` 的真定义
（`comfy_extras/nodes_minimax_h3.py` → `comfy/ldm/minimax/model.py`）并与本包常量对照：

- 一致 → 通过（进程级缓存，只查一次）
- **不一致 → 拒绝运行**，报出两边各是什么（上游更新改了网格时，宁可不跑也不出坏片子）
- 找不到上游源码 → 放行但留痕提示

已正反双验证：真实源码下通过；篡改本包常量 → 正确拒绝。

### 行为变更：音频窗 off-grid 向上拓宽
`audio_tail_from_latent` 的换算从四舍五入改为**向上取整**到 40Hz 音频栅格的整步：

- 音频窗是给模型"已播声音"的上下文，多带半步安全，截短半步可能丢节拍点
- 非 48/36 整步的 `audio_frames`（如 7 帧 = 11.67 步）现在钉 12 步而非 11 步
- `plan.notes` 留痕「已拓宽到 N 整步」；返回值多一个 `raw_steps` 供审计

### 文案：全部节点说明傻瓜化
桥 / 存 / 裁 / Load / Chain 的每个 widget tooltip 重写为「手把手」格式：
填什么、接哪根线、不改会怎样、第 1 段/第 2 段分别怎么设、常见错误（run_id 不一致 / 忘改段号 / 分辨率不同）。

### 测试
`tests/test_relay_core.py` **65 项断言全绿**（+3：off-grid 拓宽 7→12 步 / raw_steps 审计值 /
22 帧窗 ceil 语义不变性确认）。

---

## 0.1.1 — 2026-09-11

### 新增：接缝自检（`find_head_jump` / `describe_head_jump`）
`H3RelayTrimAV` 裁完后**自动检测裁剪起点是否仍是突变帧**，并在日志与 `report`
输出里给出"trim 需再加 N 帧"的具体建议。

**为什么加**：实测（2026-09-11，73 帧段 / trim=22）发现「钉住区 → 新内容」的切换点
**不总落在 trim 值上**：
```
未裁段： [21]↔[22] MAE = 6.52   ← 正常
         [22]↔[23] MAE = 102.27 ← 突变（模型从"复现上一段"切到"按 prompt 生成"）
trim=22 后：裁后第 0 帧 = 原第 22 帧（突变前那帧）→ 续接段开头是一张"异类帧"
```
同样的 trim 值在 107 帧段上**无此现象**（切换点被完整裁掉）。
⇒ 固定 trim 值不可能对所有段长都对，必须按本段实际切换点定。

判定口径：段内相邻帧差中位数为基线，前 40 帧内若存在
`帧差 > 4 × max(基线, 1.0)` 的尖峰，即报出该帧位置并建议 `trim += (j+1)`。

### 测试
`tests/test_relay_core.py` **62 项断言全绿**（新增第 8 组接缝自检 7 项：
平稳序列不误报 / 首帧突变检出 / 中段突变检出（仿真实测的 j=22）/
报告文案含建议值 / 帧数过少不崩）。

---

## 0.1.0 — 2026-09-10

首个版本。取作者链路的续接方式（latent 桥），做成独立可开源节点包。

### 新增节点
- `H3RelayLatentSave`（🔗 H3 续接 Latent 存）—— AV latent 落盘，`OUTPUT_NODE`
- `H3RelayLatentLoad`（🔗 H3 续接 Latent 读）—— 读回上一段，支持 `explicit_path` 换源
- `H3RelayMotionContext`（🔗 H3 续接 Latent 桥）—— 尾段钉入 conditioning

### 核心行为
- 从上一段 AV latent 直接切尾段，**零 VAE 重编码**（像素续接要一次 encode 往返）
- 逐 latent token 切块，按 `resolved_frame_index` 真实位置排布（像素续接只有单块 @0）
- 音频与视频**独立窗口**，音频走 `minimax_refs` 追加
- 与上游既有 keyframes **合并**：落在钉住区内的旧锚丢弃，末帧锚保留
- `context_latent` 不接时**直通**（按独立段处理），不报错

### 硬校验（全部 raise，不静默降级）
- `trim_frames` 必须落在 5+17k 网格上；**不吸附**到邻近值，报错时提示最近合法值
- `context_latent` 与本段必须同分辨率、同通道数
- 尾段步数不得大于 latent 步数
- 尾段起点必须落在 5-token 周期边界，否则拒绝渲染（避免整体位移的接缝）
- 窗口不得 ≥ 段长

### 设计取舍记录
- **不用 monkey patch**：ComfyUI 原生已消费 `resolved_frame_index` 的通用公式
  （`model.py:376`）与 `minimax_keyframes` / `minimax_refs`
  （`model_base.py:2186-2196`），上游节点为兼容老版而装的 layout/payload
  patch 在本机是 no-op。故本包**零 patch、零第三方依赖**。
- **不吸附帧数**：一度用 `snap_guide_run` 把非网格值静默改成邻近合法值，
  离线单测暴露该行为会掩盖"窗口 ≥ 段长"等真错误，已改为直接 raise。

### 测试
`tests/test_relay_core.py` **55 项断言全绿**（零 GPU）：网格自洽 / 逐位切片 /
硬错误 / conditioning 注入 / 落盘往返 / **裁头重叠（画面音频同裁逐位正确 + 时长对齐 + 越界 raise）** /
**节点返回值契约**（每个分支返回路数 == `len(RETURN_TYPES)`）。

### 修复
- **`H3RelayMotionContext.apply` 直通分支只返回 2 个值**（`RETURN_TYPES` 已是 3 路
  `conditioning / report / trim_frames`）→ ComfyUI 取第 3 路输出时
  `EXEC ERROR list index out of range`，第 1 段直接跑不起来。
  修为 `return (conditioning, msg, 0)`（直通不裁），并补第 7 组契约测试防复发。
