# ComfyUI-H3-Latent-Relay

MiniMax-H3 多段续接的 **latent 桥**（零重编码）—— 一个可独立使用、**零第三方节点包依赖**的 ComfyUI 节点包。
只依赖 ComfyUI 自带的 `torch` 与 `safetensors`，不与任何第三方 H3 节点包耦合。

| 项 | 值 |
|---|---|
| 版本 | **0.6.8**（8 个节点，复合桥 `H3RelayCopyBridge` = **唯一桥**） |
| 许可 | **MIT**（第三方出处见 [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)） |
| 宿主 | 需要**带 MiniMax-H3 支持的 ComfyUI**（其自身为 GPL-3.0，见「11. 许可与出处」） |

> **本文约定（结构锁死）**：12 节顺序固定，编号即目录。
> ① 槽位号一律 **`[N]` = 0 起算**（UI 上第 N 个口 = `[N-1]`）；
> ② 表格只放结论，长推导全部外链 `docs/`；
> ③ 前三节读完就能跑，4–8 节是查表，9 节以后是验收与背景；
> ④ 🔴 **每个功能都有两条用法 —— 画布手动（多数用户）与 API 提交图 JSON，二者必须是同一套节点实现。**
> 只写在脚本里的功能不算本包的功能（判据见 [`CONTRIBUTING.md`](CONTRIBUTING.md) 「代码纪律·铁律一」）。

## 0. 目录

| 节 | 内容 | 什么时候看 |
|---|---|---|
| [1](#1-它解决什么问题) | 它解决什么问题 | 第一次来 |
| [2](#2-安装) | 安装 | 装包时 |
| [3](#3-5-分钟跑通) | 5 分钟跑通 | 装完立刻 |
| [4](#4-接线唯一桥) | 接线（唯一桥）· **§4.1 全流程档：8 个节点全在场** | 每次搭图 |
| [5](#5-节点速览) | 节点速览（8 个） | 找节点时 |
| [6](#6-参数主旋钮) | 参数（主旋钮） | 要调参时 |
| [7](#7-音频缝) | 音频缝 · **多段拼接** · **API/脚本用户怎么用**（§7.4） | 出片有咔哒声 / 段接不起来 / 不开画布 |
| [8](#8-离线自测) | 离线自测 | 改完代码 / 提 PR |
| [9](#9-排障) | 排障 | 跑不通时 |
| [10](#10-常见疑问faq) | **常见疑问（FAQ）** | 上片前 / 遇到怪现象 |
| [11](#11-原理与机制) | 原理与机制（概述） | 想搞懂为什么 |
| [12](#12-许可与出处) | 许可与出处 | 再发布前 |
| [13](#13-文档索引) | 文档索引 | 找细节 |

---

## 1. 它解决什么问题

H3 分段生成时，"续接"要回答一件事：**新的一段怎么知道上一段结束在什么状态？**

主流做法是**像素续接**：上一段 mp4 解码成帧 → VAE 重编码成 latent → 当锚塞进条件。问题在于：

- 多一次 VAE 往返，**有量化损失**；
- 重编码的 latent 与采样时那份**不是同一个东西**，锚点会漂移；
- 只能锚一个整块，位置固定在第 0 帧。

本包做的是 **latent 桥**：直接从上一段的 AV latent 里切出尾段，切成逐 token 的块，
按真实位置写进 `minimax_keyframes`。**不重编码**，锚点与采样同源。

**一个常被忽略的优势：续接不落解码帧，磁盘几乎没有额外膨胀。**
latent 桥在潜空间完成，从不在磁盘上解码并缓存像素帧——续接只在 `output/relay_kit/<run_id>/`
多一份 **latent 边车**（`stage_NNNNN.safetensors`，单段约十几 MB），段文件本身是 H3 已
压缩的 mp4，音频另有 `~2 MB` 无损 PCM 边车（见 §5）。没有"解码帧缓存"这一层，单段
不会凭空多出几百 MB 的裸帧，也顺带杜绝了"有损重编码静默腐蚀接缝"这一整类问题。

---

## 2. 安装

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/ZenHG/ComfyUI-H3-Latent-Relay.git
```

或下载 ZIP → 解压 → 文件夹改名为 `ComfyUI-H3-Latent-Relay` → 放进 `custom_nodes/`。

装好后**重启 ComfyUI 后端**（ComfyUI-Manager 点 *Restart*；没装就重启 Python 进程）——
仅刷新浏览器不会加载新节点。节点列表里搜 `🔗 H3 续接`（7 个）+ `🔍 H3 潜空间分块放大` 即可看到全部 8 个节点。

依赖只用 `torch`（**顶层 import**，缺了整包注册失败）与 `safetensors`（延迟 import，缺了只在落盘那步报错）。

**宿主要求**：**ComfyUI ≥ 0.37.0**（已在 `pyproject.toml` 的 `[tool.comfy] requires-comfyui` 用
**官方字段**声明 —— 宿主 `comfy_config/config_parser.py` 启动时真的会读它，比只写在这里强）。
本包硬依赖带 MiniMax-H3 支持的 ComfyUI（需要 `comfy_extras/nodes_minimax_h3.py`
与消费 `minimax_keyframes` / `minimax_refs` 的 `comfy/model_base.py`）。装到不含 H3 的旧版
ComfyUI 上，节点能注册但**续接静默无效**——用前先确认 ComfyUI 版本。

> 版本下限的依据（不是拍脑袋）：本包的 `web/` 前端靠宿主 `WEB_DIRECTORY` 机制挂载、
> 元数据靠 `comfy_config` 解析 `pyproject.toml` —— 这两条在 **0.37.0 上实测通过**（零 GPU 探针）。
> Python 侧写的是 `requires-python = ">=3.10"`，但本包**未使用** 3.10+ 独有语法
> （无 `match`、无新式联合类型），真要放宽到 3.9 亦无语法障碍 —— `>=3.10` 是刻意取的保守下限。

**除一个可选节点外，不依赖任何第三方 H3 节点包**：`minimax_keyframes` / `minimax_refs` /
`resolved_frame_index` 全是 ComfyUI **原生**协议，零 monkey patch、**零 patch 别人的包**。

### 2.1 用 🔍 潜空间分块放大才需要装的东西（可选）

`H3RelayLatentUpscale` 是社区节点 **`MinimaxH3LatentUpscaler3D`**（作者 `LBH-123-AI`，**MIT**）的
**AV 打包 latent 适配层**——本包**不复制**它的模型结构与代码，运行时从 ComfyUI 注册表取它的类来调。
所以要用这个节点，两样东西都从**作者处**拿：

```bash
# ① 节点包（作者仓库）
cd ComfyUI/custom_nodes
git clone https://github.com/LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler.git
```

② **放大权重**：从作者的 HuggingFace 仓库下载
**<https://huggingface.co/LBH-123-AI/Minimax_h3_latent_upscaler>**，
放进 `ComfyUI/models/latent_upscale_models/`（`.safetensors` / `.pth` 均可），然后重启后端。

- **权重目录与清单直接沿用作者的 `scan_models()`** ⇒ 装了上游节点就**共用同一目录同一份文件**，
  本包不另立目录、不复制第二份权重。
- ⚠️ 这里要的是 **H3 潜空间放大权重**（24 通道 latent 专用），**不是** ESRGAN 那类像素放大模型——
  架构不匹配，选了会报错。
- **没装也不影响其他 7 个节点**：整包照常加载，只有在使用本节点时抛一条**写明仓库地址与权重地址**
  的报错（不静默降级、不假成功）。

随包的 [`examples/`](examples/README.md) 有两份可直接打开的工作流：**最小续接演示**（18 节点，
只用官方节点 + 本包）与**全流程示例**（45 节点：0.3MP 一采 → 🔍 潜空间放大 → 2 步二采 → 拷贝桥续接，
**本包 8 个节点全在场**，含「放大之后哪些线要留在原生域」的接线纪律）。

---

### 2.2 节点 API 版本：V1（默认）/ V3

本包同时提供两套节点定义，**同一时间只启用一套**（用环境变量切换，**改后需重启 ComfyUI**）：

| 出口 | 怎么开 | 说明 |
|---|---|---|
| **V3**（**默认**，2026-09-24 起） | 不设该变量，或 `H3RELAY_NODE_API=v3` | 新版 `io.ComfyNode` + `comfy_entrypoint()`（官方 schema） |
| **V1**（回退用） | `H3RELAY_NODE_API=v1` | 传统 `NODE_CLASS_MAPPINGS`。与 0.6.x **逐位一致** |

**节点名、输入输出的顺序与取值、默认值、组合项、显示名全部相同**
（8 个节点、112 个 input 逐项机检一致，见 `tests/test_v3_schema.py`；CI 里因宿主 `nodes`
导不进来会少一个 `H3RelayLatentUpscale` ⇒ 那边是 7 节点 / 99 input，属**设计内降级**）⇒ 已有工作流、脚本、API 图
**两条出口都能直接跑**，切换**不需要改图**。

**为什么默认切到 V3**：官方明确「今后节点功能的扩展也只会添加到 V3 架构中」，V1 拿不到新能力；
且本包 V3 出口已过两道实测闸门 —— 69 项逐字段机检 ＋ 2 段真实链（362/362 帧守恒、流拷贝无损、
PCM 边车被拼接路由取到）。

```bash
H3RELAY_NODE_API=v1 python main.py     # 万一 V3 有问题，一行回退到 V1（两条出口代码都在）
```

⚠️ **为什么必须"二选一"而不是同时导出**：宿主加载器是
`if 模块有 NODE_CLASS_MAPPINGS … return True` / `elif 模块有 comfy_entrypoint` ——
V1 分支命中即返回，两套一起导出时 **V3 永不生效**。所以 V3 模式下本包把
`NODE_CLASS_MAPPINGS` 显式置 `None`。

> **V3 出口的已知差异（如实说明）**
> * schema 在**包加载时**构造（V1 是惰性的，按需才调）⇒ 某个节点的参数表构造失败时，
>   只会**跳过那一个节点**并打日志，其余节点照常加载（不会整包失效）。
> * 前端 `web/` 与后端拼接路由 `POST /h3relay/concat` 在**两条出口下都照常工作**（已零 GPU 实测）。

## 3. 5 分钟跑通

> 懒人路线：打开 `examples/minimal_relay_official.json`，把 4 个加载器的下拉改成你本机的模型文件，
> 然后按下面三步跑。画布上的注释框写着同样的步骤。

**第 1 段**

1. 「① 段号」填 `0`；
2. 在官方出词节点（`MiniMaxH3ImageToVideo` / `MiniMaxH3ReferenceToVideo`）填 prompt；
3. 点 Queue。

```
[H3 Relay] 无 context_latent → 直通（独立段，不续接）。
[H3 Relay] 裁 0 帧 → 不裁（独立段或纯首段）。
```

**第 2 段起**

4. 「① 段号」改成 `1` —— **只改这一个数**（桥与落盘都由它驱动）；
5. prompt 换成第 2 段内容（开头直接接住上一段的动作/机位，别重新起手）；
6. 再点 Queue。

| 日志里应看到 | 说明 |
|---|---|
| `钉住 22 帧` | 上一段尾部**已接进来** |
| `裁首 N 帧 = 钉住 22 + 沉降 0` | 接缝处理**已生效**（`settle_frames=0` 起默认不裁沉降；旧文档的「沉降 1」是 0.5.0 前口径） |
| `起点干净` | 没有跳变，可以拼 |

**关键参数**

| 参数 | 第 1 段 | 第 2 段起 | 填在哪 |
|---|---|---|---|
| `stage_index`（段号） | `0` | `1`、`2`、`3`… | 桥 + 落盘（**必须一样大**） |
| `run_id` | 同一个片子名，如 `myfilm` | **与第 1 段一字不差** | 桥 + 落盘 |
| `context_frames` | `22` | `22`（不用动） | 桥（钉住窗，只认 5/22/39/56/73/90/107/124） |
| `settle_frames` | —（首段不裁） | 保持 `0` | **裁重叠** |
| `seam_ghost` | —（首段不裁） | 保持 `0`（默认关） | **裁重叠** |
| `settle_sharpen` | —（首段不裁） | 保持 `0`（需要时再开） | **后处理 Post** |

**三个最常见的翻车点**

| 症状 | 原因 | 处置 |
|---|---|---|
| 报错「说清是第 N 段却拿不到上一段」 | 段号 ≥1 但 `run_id` 与上一段不一致 / 上一段文件不在 | 两处 `run_id` 必须一字不差 |
| 画面从第 1 帧就开始重播上一段 | 「裁重叠」没接上，或它的 `trim_frames` 没接桥的 `[2]` 输出 | 按「4. 接线」检查 |
| 换新片子却接了旧片尾巴 | 没换 `run_id`（同名会覆盖同段号文件） | 换新名字 |

> **跑完 N 段之后**：每段各出一个 mp4。0.6.7 起 Chain 能顺手把 N 段拼成一条
> （填了 `prompts` 还能一段一个词；开 `auto_concat` 或点 **🧩 拼成一条**），
> 详见 [§7.1 多段拼接](#71-多段拼接n-个-mp4-怎么接成一条)。

---

## 4. 接线（唯一桥）

0.6.0 起只有**一个桥**：`🔗 H3 续接 拷贝桥（复合桥）`。接上 `conditioning` 时它同时做两件事——
`[0] latent` 钉住窗管**运动**、输出 `[3] conditioning` 钉帧管**取景**，两者改的是不同对象 ⇒ 并联生效。
不接 `conditioning` 则只做拷贝桥。

```
出词节点（官方 / 第三方）
  ├─ positive ─────────────────→ 桥 [16] conditioning
  └─ LATENT ──────────────────→ 桥 [0] latent
                                 桥 [1] context_latent  ← 留空（自动读上一段）
                                 桥 [2] context_frames  = 22
                                 桥 [17] run_id / [18] stage_index
                                        │
              桥 [0] latent ───────────┼──→ 采样器 latent_image   ★必须过桥
              桥 [3] conditioning ─────┼──→ 采样器 positive
              桥 [2] trim_frames ──────┼──→ 裁重叠 [1] trim_frames ★必须接
                                        │
                                  采样器 → LATENT ──→ 🔗 续接 Latent 存 [0]
                                                  └──→ VAEDecode / VAEDecodeAudio
                                                            ↓
                                🔗 续接裁重叠 [0] images ← IMAGE
                                              [3] audio  ← VAEDecodeAudio  ★输入
                                              [1] trim_frames ← 桥 [2]
                                        │
          ┌─────────────────────────────┤
          │                             │
          │            [1] audio ───────┴──→ 🔗 音频缝 [0] audio
          │                                  （可选；第 1 段也要接，见 §7）
          │                                        │
          │                                   [0] audio
          │                                        │
          └→ [0] images ────────────┬──────────────┴──→ CreateVideo → SaveVideo
                                    │                    ↑
                                    │              ★ audio 必须接这一条
                                    │                （不是 VAEDecodeAudio 那条）
                                    │
                       [3] prev_tail ──→（可选）后处理 Post [1] guide
```

> 🔴 **音频线只有一条是对的**：`裁重叠 [1] audio`（或再经 `音频缝 [0] audio`）→ `CreateVideo.audio`。
> **直接把 `VAEDecodeAudio` 接到 `CreateVideo` 会音画不同步** —— 画面裁掉了头部重叠帧、音频没裁，
> 每缝差 ~0.9s，且**逐段累积**（第 3 段起口型明显对不上）。节点只能发现"输入没接"，
> **发现不了"输出被悬空"**，所以这条得自己盯住。

### 4.1 全流程档：**8 个节点全在场**（`examples/fullflow_second_pass_latent_upscale_ui.json`）

上面那张是**最小必备**接法。产线现役的「一采 → 🔍 放大 → 二采 → 续接」把 8 个节点都用上了，
顺序与两条硬纪律如下（★ = 交付支路，☆ = 续接契约，两者**不是一条线**）：

```
① 出词（官方 MiniMaxH3ReferenceToVideo）        positive + LATENT 两路都出
② 🔗 续接 Latent 读   ☆ 上一段落盘（段号 −1，自动）
③ 🔗 续接 拷贝桥      ← ①LATENT + ②context_latent；[3]conditioning → **只喂一采的 guider**
④ 一采 SamplerCustomAdvanced（原生分辨率，guider ← ③[3]）
⑤ 🔗 续接 Latent 存   ☆ ← ④ 输出            ★必须接在放大**之前**
⑥ 🔍 潜空间分块放大   ← ④ → + SetLatentNoiseMask（钉住头 7 个 latent 帧）
⑦ 二采 SamplerCustomAdvanced（在高分辨率上）guider ← **① positive**（🔴 不是 ③[3]，见 §10.9）
⑧ VAEDecode ← ⑦ 画面 ／ VAEDecodeAudio ← ④ 音频   ★音频全程不走 SR 与二采
⑨  续接 音频缝 ← ⑧音频 → 🔗 续接 裁重叠（音画同裁 + 落 PCM 边车）← ⑧画面
⑩ 🔗 续接 后处理 Post ← ⑨（guide ← 裁重叠[3] prev_tail；20 个旋钮默认全 0 = 逐位直通）
⑪ CreateVideo → SaveVideo（单文件带音轨）
⑫ 🔗 续接 连跑 Chain：`prompts` 用 `---` 分块 ⇒ 连跑逐段换词；跑完点 🧩 拼成一条 直接出成片
```

- 🔴 **二采不接桥的 `conditioning`**：钉帧锚（`minimax_keyframes`）与本段目标**必须同网格**，
  一采之后画面已放大 ⇒ 接上当场炸（机理与实测数字见 §10.9）。latent 侧钉住照旧生效。
- 🔴 **契约取原生域**：`Latent 存` 在放大之前；存成放大后的 ⇒ 下一段再过一次 SR = 双倍漂移。
- 出段分辨率 = 原生 × 放大系数（本示例 416×736 → 480×864）；**放大属交付支路，不进续接契约**。

**槽位速查**（0 起算）
| 节点 | 输入 | 输出 |
|---|---|---|
| `H3RelayCopyBridge`（复合桥） | `[0] latent` `[1] context_latent` `[2] context_frames` `[16] conditioning` `[17] run_id` `[18] stage_index` | `[0] latent` `[1] report` `[2] trim_frames` `[3] conditioning` |
| `H3RelayLatentSave` | `[0] latent` `[1] run_id` `[2] stage_index` `[3] note` | `[0] latent` `[1] path` |
| `H3RelayTrimAV` | `[0] images` `[1] trim_frames` `[2] fps` `[3] audio` `[4] settle_frames` | `[0] images` `[1] audio` `[2] report` `[3] prev_tail` |
| `H3RelayPost` | `[0] images` `[1] guide` + 20 个旋钮 | `[0] images` `[1] report` |
| `H3RelayAudioSeam` | `[0] audio` `[1] run_id` `[2] stage_index` `[3] patch_seconds` `[5] fade_seconds` | `[0] audio` `[1] report` `[2] joined` |
| `H3RelayLatentLoad`（可选） | `[0] run_id` `[1] stage_index` `[2] explicit_path` | `[0] context_latent` `[1] info` |

**第 1 段**：桥 `[2]` 输出 `0` → 裁重叠原样通过；桥不读上下文（直通）。
**第 2 段起**：填好 `run_id` + `stage_index`，桥自己从
`output/relay_kit/<run_id>/stage_NNNNN.safetensors` 读上一段。
想在图上把来源画出来（或断点续跑换源），就接 `🔗 H3 续接 Latent 读`（`[0] context_latent` → 桥 `[1]`）。

只要某节点**输出 `CONDITIONING` + `LATENT`**，接法就一样——把它替掉图里的出词节点即可。

| 用到的节点 | 来自 |
|---|---|
| `MiniMaxH3ReferenceToVideo` / `MiniMaxH3ImageToVideo` / `MiniMaxH3AddGuide` | ComfyUI **内置** |
| `CSGlideCastCS`（出词 + 规格） | `ComfyUI-Banzhang-All`（第三方，**非本包依赖**） |
| `SelfLiftH3Sampler`（AV 采样器） | `comfyui-SelfLift`（第三方，**非本包依赖**） |

可选节点（默认全关 = 逐位直通，删掉照样跑）：**后处理 Post**（画质域）、**音频缝**（音频域）、
**Chain**（自动连跑，见 [`docs/07-chain.md`](docs/07-chain.md)）。

> 节点在画布上默认只画主旋钮、其余折进 `advanced` 区；旧图看不到 `prev_tail` 输出的处理见
> [`docs/04-canvas-and-widgets.md`](docs/04-canvas-and-widgets.md)。

---

## 5. 节点速览

**8 个节点分三层——一个最小工作流只用得到前 3 个**（后 5 个都是"删掉照样跑"的可选项；
产线现役的全流程示例把 **8 个全接上了**，顺序与纪律见 §4.1）：

| 层 | 节点 | 说明 |
|---|---|---|
| **必备 3** | Latent 存 · 拷贝桥（复合桥）· 裁重叠 | 少一个就不叫续接 |
| **可选 4** | 后处理 Post · 音频缝 · 连跑 Chain · 潜空间分块放大 | 各自独立、默认全关 = 逐位直通 |
| **手动接线才用 1** | Latent 读 | 桥自己会从磁盘取源；只有要**显式换源**时才手动接 |

| 节点 | 作用 |
|---|---|
| 🔗 **H3 续接 Latent 存** | 本段采样后把 AV latent 落盘到 `output/relay_kit/<run_id>/stage_NNNNN.safetensors` |
| 🔗 **H3 续接 Latent 读** | 读回上一段（段号 −1），断点续跑可指定 `explicit_path` 换源 |
| 🔗 **H3 续接 拷贝桥（复合桥）** | 上一段尾 AV latent **逐位拷贝**进本段初始 latent + 噪声掩码（钉住区不重绘）。接 `[16] conditioning` 时并联钉帧（管取景）与 `[0] latent` 拷贝（管运动）；不接则只做拷贝。（`mask_mode` 各档见 [`docs/02`](docs/02-parameters.md)） |
| 🔗 **H3 续接裁重叠** | 裁掉本段头部的重生成帧（音画同裁）——不裁就会在拼接处重播/跳变；`[3]` 输出 `prev_tail` = 上一段末帧 |
| 🔗 **H3 续接后处理 Post** | **画质域**：跨段统计匹配 / 低频残差 / 色调 / 反卷积 / 高频迁移 / 糊区锐化，全部默认关 |
| 🔗 **H3 续接音频缝** | **音频域**：把上一段环境声补进本段头部，**长度守恒**（零 A/V 位移），默认关 |
| 🔍 **H3 潜空间分块放大** | **画质域 · latent 层**：拆 H3 的 AV 打包 latent → 逐块调学习式 3D 放大器 → 回包。**零去噪**（不重采样、不改表演与口型）、**时间维一帧不动**（帧网格与 `裁重叠` 裁量不受影响）、**音频流原样带回**。`chunks` 是显存旋钮（1 = 整段一次过），`mode` 支持 ×倍数 / 目标尺寸 / 兆像素。⚠️ 需装可选依赖见 §2.1；⚠️ `chunks>1` 会改画面见 §9 |
| 🔗 **H3 续接连跑 Chain** | 自动连跑控制器：同分组框内自动推进「桥 + 落盘」段号并排队。**0.6.7 起还会换词、还会拼片**：填 `prompts`（`---` 分块，第 k 块喂第 k 段）⇒ 连跑自动换词；开 `auto_concat` 或点 **🧩 拼成一条** ⇒ 跑完直接得到成片（包内实现，不需外部 ffmpeg）。留空/关 = 老行为逐位不变 |

> **三个域，别混挂**：时间轴 = `H3RelayTrimAV`（冻结）／画质域 = `H3RelayPost`／音频域 = `H3RelayAudioSeam`。
>
> **♪ 音频边车（0.6.7）**：`H3RelayTrimAV` 与 `H3RelayAudioSeam` 都会把**自己的音频输出**另存一份
> 无损 PCM 边车（各约 2 MB/段），让「拼成一条」能**直读无损音频**（音频不再二次编码）。
> 音频链上有两个时，拼接按「**与 mp4 音频长度最接近**」挑 ⇒ 拿到的是**真进 mp4 的那份**。
> 想省磁盘：`H3RelayTrimAV.save_pcm` 关掉即可（拼接自动退回解码，并在报告里写明）。
> 节点数不是复杂度，**"哪些线必须接对"**才是。

---

## 6. 参数（主旋钮）

默认值就是**实测过的推荐值**，不是"待你优化的起点"。

| 节点 | 参数 | 默认 | 说明 |
|---|---|---|---|
| 桥 | `context_frames` | `22` | 钉住窗帧数，只认 `5+17k`（5/22/39/…/124），须小于本段帧数 |
| 桥 | `mask_mode` | `hard` | 🟢 生产档 `hard`（钉住区零重绘）｜🟡 对照 `ramp`/`window`｜🔴 实验 `taper`/`blend`（`taper` **不钉住**） |
| 桥 | `ref_anchor_stage` | `-1` | ≥0 时自动读该段作**全局外观锚**，防长程漂移 |
| 裁重叠 | `settle_frames` / `diagnostics` | `0` / `False` | 不裁沉降；`seam_ghost` 同样默认 `0`。`diagnostics` = 三路**只读观测**总闸，**默认关**（对外省 ~0.4 s/段 CPU），**本地产线入口显式传 `True`** |
| 后处理 Post | 19 个旋钮 | `0` | 全关 = 逐位直通；跨段两项需另打勾 `cross_seg_ack` |
| 音频缝 | `patch_seconds` | `0` | 逐位直通；`fade_seconds` 默认 `0.25` |

> 全量参数（含每个 advanced 项、`Post` 的 20 项与 `AudioSeam` 的 13 项）见
> [`docs/02-parameters.md`](docs/02-parameters.md)。
> 采样链怎么选、本包为什么"该量的不让用户配"见 [`docs/03-sampling-and-design.md`](docs/03-sampling-and-design.md)。

---

## 7. 音频缝

音频缝**必须在节点里做**（不能靠组装层补）：组装层补会引入 AAC priming 造成的 A/V 位移，
而本节点是**长度守恒**的——音频采样点一个不增不减 ⇒ 零位移。

- 第 1 段也要接（它会把第 1 段音频落盘，第 2 段的床源就是它）；段号顺序跑，别跳段。
- 不接它或 `patch_seconds=0`，音频逐位直通。
- **输出接法**：`音频缝 [0] audio` → 落盘节点的 `audio`（`CreateVideo.audio`）。**别让 `VAEDecodeAudio` 直连落盘节点** —— 那是未裁的原始音频，会音画不同步（见 §4 的红色警示）。
- ⚠️ `patch_seconds > 0` 会把**本段头 `patch_seconds` 秒整段换成上一段的环境声**。若本段头部本来有台词，这 N 秒的台词会被换掉 ⇒ 段首留白是硬纪律（见 [`docs/06`](docs/06-continuity-scripting.md)）。节点会自动**避开床源里的有声区**（挑窗时跳过含语音的位置），并在日志里报 `🎙 窗内有声帧占比`。
- 🛡 **patch 台词守卫（默认开）**：patch 也会吃掉**本段自己**落在头部的台词（实测「这家店」0.60–1.70s 被 2.0s patch 整句吞掉）⇒ 节点自动探测本段头部台词起点，**patch 收缩到台词前 0.40s**（2×全源中位能量判据 + 持续帧滤波）；台词太靠前（起点 < 0.10s 可用）则 patch 整个关闭、头部原样保留。report 显式打印判定与收缩前后值。守卫只保台词 —— 缝处保护相应变弱，**出词侧段首留白仍是根治**。`patch_guard=0` 回旧行为。真实渲染两场验证：台词 0.90s 起 ⇒ patch 2.0→0.65s 一字未损；台词从第 0 帧开始 ⇒ patch 自动关闭零吞字。**BGM 类连续音乐不触发**（BGM 峰 < 2×中位；首版 P10 基线在 BGM 上误触发，已被压力矩阵打回）。每个旋钮的推荐值与开关理由见 §7.3。

出词层面的配套纪律（段首缓冲、台词安全时刻、末帧锚链）见
[`docs/06-continuity-scripting.md`](docs/06-continuity-scripting.md)。

### 7.1 多段拼接：N 个 mp4 怎么接成一条

**先记住一条契约**：`裁重叠` 把**视频和音频同裁**，`音频缝` 又是**长度守恒**的
⇒ **每个段文件的音频长度 = 视频长度**，段与段之间**没有任何重叠**。
所以正确拼法就是**纯粹的首尾相接**，不需要任何"聪明的拼接器"。

> ⚠️ **【连跑 ≠ 成片】** 跑完你会得到 N 个 mp4。0.6.7 起 `H3RelayChain` 能顺手拼（见下），
> 但**必须点它**（开 `auto_concat` 或按 🧩）—— 它不会偷偷帮你拼。

**① 首选：包内一键（0.6.7）**

Chain 节点上：开 `auto_concat` ⇒ 连跑结束自动拼；或随时点 **🧩 拼成一条**（把已经跑过的段拼起来）。
画面**流拷贝（无损）**、音频逐段去 priming 对齐、拼完立刻自检四项
（帧数守恒 / PTS 无洞 / DTS 递增 / A·VΔ ≤ 1 帧），实测 4 段 29 s 成片 **1.3 秒**出片。
自检不过会把片留着取证，并在状态里写明**别当成品用**。

**①.5 音轨档：默认 AAC 256k，另有无损母版（0.6.7）**

Chain 上的 `audio_out` 三档（只影响**成片音轨**，画面一律流拷贝）：

| 档位 | 容器/编码 | 实测 SNR（vs 无损源，真实 H3 段） | 说明 |
|---|---|---|---|
| `aac_256k`（默认） | mp4 + AAC 256k | **40.5 ~ 50.0 dB** | 兼容第一，浏览器能放 |
| `aac_192k` | mp4 + AAC 192k | 38.4 ~ 44.6 dB | 省约 25% 体积 |
| `pcm_lossless` | mp4 + PCM f32 | **逐位一致（∞）** | 母版档；**浏览器预览没声音**，文件 ≈4.4 MB/s |

**为什么默认档就能从"两代"降到"一代"**：段文件的音轨是用户落盘时编的 AAC（第 1 代）。
拼接若从 mp4 解码再编 = 第 2 代。0.6.7 起「裁重叠」顺手把本段音频另存一份**无损 PCM 边车**
（`save_pcm`，默认开）；拼成片直接读边车 ⇒ **不再解 AAC、也不再摸 priming** ⇒ 音频只编一代；
选 `pcm_lossless` 则**一代都不新增**（成片音轨与边车逐位相同，实测 max|Δ| = 0）。

> 边车缺失（旧图、关掉 `save_pcm`、或那次提交没跑「裁重叠」）⇒ 该段自动退回 mp4 解码，
> 并在拼接报告里**逐段写明音频源**（`PCM` / `AAC`）。代价：该段仍是"二代"。
>
> **⚠ 音频链上「裁重叠」之后还有别的音频节点怎么办**（典型 = 本包的 **音频缝**；产线接线就是
> `裁重叠 → 音频缝 → 落盘`）：真进 mp4 的是**链上最后那个**的输出。所以：
> ① 「音频缝」也落自己的边车（就是它送进落盘的那份音频，见 §5 节点表）；
> ② 拼接在多个候选里按「**与 mp4 音频长度最接近**」挑（数据说话，不靠接线假设）；
> ③ 挑不出同源的（如 J-cut 让音频缝输出短了 0.9 s）⇒ **拒收边车、退回解码**并在报告里说明。
>
> 边车落盘（约 2 MB/段）失败**不影响本段渲染**，只在日志里提示。

**参数：`video_crf`（默认 16）** 只在画面**必须重编码**时生效（各段规格不一致、或流拷贝路断言不过）；
默认的流拷贝路是无损的，这一格用不到。⚠ `crf 0` **不是无损**（RGB→YUV 4:2:0 先丢，天花板 46.5 dB）。

**② 不想要这一步发生在 ComfyUI 里**（要走外部工具）：用 concat filter 单遍重编码 ——
N 个输入按顺序列举（`list.txt` 里按段号顺序写绝对路径，每行 `file '/abs/path/s0.mp4'`）：

```bash
# 自检：帧数应 = 各段帧数之和，且 A/V 时长差 ≤ 1 帧
ffprobe -v error -select_streams v:0 -count_frames -show_entries stream=nb_read_frames -of csv=p=0 out.mp4
ffprobe -v error -select_streams a:0 -show_entries stream=duration -of csv=p=0 out.mp4

# 拼（时间轴最干净；N 个输入按顺序列举）
ffmpeg -y -i s0.mp4 -i s1.mp4 -i s2.mp4 -i s3.mp4 \
  -filter_complex "[0:v][0:a][1:v][1:a][2:v][2:a][3:v][3:a]concat=n=4:v=1:a=1[v][a]" \
  -map "[v]" -map "[a]" -c:v libx264 -crf 16 -pix_fmt yuv420p -c:a aac -b:a 192k out.mp4
```

| 别这么拼 | 症状（2026-09-22 实测） | 为什么 |
|---|---|---|
| `ffmpeg -f concat -c copy`（最省事） | 4 段：视频时长 **29.346 s**（应 29.250，虚增 96 ms）、音频逐段落点 +64 / −25 / −33 ms（NCC 掉到 0.2） | 段文件音频流比视频长（AAC 编码器 priming，每段 +0.032 s）；流拷贝没法在样本级校准 |
| `acrossfade`（看着最专业） | **每缝偷 0.25s** ⇒ 第 3 段起音画错位、**逐段累积** | crossfade 是 overlap-add，而视频**没有**对应的重叠可裁 |

> 拼完**一定要自检**（上面那两条 ffprobe）：帧数守恒 + A/V 时长差 ≤ 1 帧。不过就换 ① 或换素材。

### 7.2 床窗语音规避：判据能抓什么、抓不住什么（务必先读）

`patch_seconds > 0` 时节点会**自动避开床源里的有声区**。判据是**能量型**的
（帧 RMS 超**全源中位 3×** 记"有声帧"；窗内占比超阈值判撞；瓦片档阈值 0），
**不是语音识别** —— 它能抓什么、抓不住什么，实测如下，**请按你的素材对号入座**：

| 素材类型 | 判据表现（实测） |
|---|---|
| 稳态底噪 + 台词（雨声/室内环境声 + 对白，最常见） | ✅ 稳定抓到（台词窗占比 58% ⇒ 换到 0% 窗） |
| 极短人声（~0.1s 叹词/喘息） | ✅ 能抓（占比 12.5%，压线） |
| 单帧瞬态（关门/砰响） | ✅ 不误报（占比 4% < 10% 阈值，不会白白换窗） |
| **低电平人声**（只比环境高 ~6 dB：远场对白、气声） | ❌ **漏检**（够不到 3× 阈值） |
| **语音当底噪 / BGM 型**（播客、连续旁白、嘈杂酒馆） | ❌ **漏检**（人声抬高全源中位 ⇒ 阈值自失效） |

**兜底措施（按成本从低到高）**：

1. **先看日志**：`patch_seconds>0` 的每次运行都打 `🎙 取样窗内有声帧占比 X%（判据阈值 Y%）`；
   判撞时显式 `⚠` 并给出换窗前后位置 —— **一切判定可审计，无静默**。
2. **拿不准就关**：`patch_seconds=0` ⇒ 补丁分支完全不进，音频逐位直通（零风险开关）。
3. **降一档复杂度**：`tile_seconds=0` ⇒ 走单窗档（10% 阈值 + 语音规避），比瓦片档保守。
4. **素材侧根治**：让台词避开床源尾部（`bed_select=tail` 默认取上一段末窗）——
   段首/段尾留白纪律见 [`docs/06`](docs/06-continuity-scripting.md)。
5. **全源皆有人声时**：节点**不报错**，自动退回**最静窗**（语音残留最少）+ 日志 ⚠ ——
   不会把"以语音为底噪"的合法素材判死。
6. **最终判定是人耳**：机器只负责指位置（缝后 `patch_seconds` 秒），听完再定。

> 判据已内置**持续帧滤波**（连续 ≥2 帧 = 100ms 才算人声起振 ⇒ 单帧瞬态不触发换窗；
> 连续台词占比实测无损）。仍**未实现**：ASR 语义复核（救上表 BGM 型漏检；已登记 `CONTRIBUTING`）。

---

### 7.3 调参指南：每个旋钮的推荐值与开关理由（0.6.5 实测定稿）

> 原则：**默认值就是推荐值**。下表回答的是"我到底什么时候该动它"——每一条都给
> 实测证据；没有证据的项明确标注"别动"。

| 旋钮 | 推荐值 | 什么时候动、理由（全部实测） |
|---|---|---|
| `patch_seconds` | 产线 **2.0**；不确定就 **0.2–1.2**；**0** = 关 | 2.0 完整盖住段首 priming 静默 + 生成瞬态（okBed 系列真渲染验证）。0.6.5 有守卫兜台词后 2.0 不再吞字（旧版 2.0 曾整句吞掉「这家店」）。**场景对白前置（段首 1s 内开口）保持 2.0 也安全** —— 守卫会自动收缩/关闭（s2bgm2：台词从第 0 帧开始 ⇒ patch 自动关闭零吞字）。 |
| `tile_seconds` | 床源尾部干净时 **0**（单窗直取）；**床源尾部有台词/BGM 乐句时 1.2** | 瓦片从床源更早的干净区取材。床窗判据对「BGM+台词」床源稳抓（台词 +14 dB ⇒ 窗占比 66.7% ⇒ 换窗）。S2 场景（BGM 一直有）实测生成侧 BGM 天然连续，patch 价值本身下降，tile 主要是护台词。 |
| `fade_seconds` | **0.25，别动** | 0 = 硬切，缝上可闻接点；产线实测值。 |
| `bed_select` | **tail，别动** | `quiet`（0.5.0 旧行为）实测把补丁换成更静的内容（−42 vs −12.8 dBFS）⇒ 缝上从「凹陷」变「静音洞」、起拍被压平。仅对照复现用。 |
| `bed_stage` | **0**（第 1 段） | 同场景环境声 stationary，取第 1 段最稳；只在前段是特殊声场（爆炸→安静室内）时才考虑换。⚠ 必须 < 本段段号。 |
| `patch_guard` | **开（默认），别关** | 两场真渲染 + 零 GPU 压力矩阵（S2-1~5）验证：台词 0.90s 起 ⇒ patch 2.0→0.65s 一字未损；台词第 0 帧起 ⇒ 自动关闭；头部纯环境声 ⇒ 与关闭逐位一致（零副作用）；BGM 素材不误触发。**唯一该关的场景：`patch_seconds=0` 时**（守卫根本不进补丁分支，开关无差异）。 |
| ~~`exp_bed_jitter`~~ | — | 🔴 **该参数已在 2026-09-22 移除**（`1c87aff`：E1/E2/E5 三层实验档实测对靶无效，一并删除）⇒ **本行不是可设参数**，设了会被宿主**静默忽略**（多余输入键不报错）。**原结论存档**（将来若开 BGM / 用有内容床源可能重启）：E5 多段补丁取同一床窗、听出「重复感」时才试 1.2（按段号错开床窗起点）；**2026-09-21 耳测归档：稳态床源下无收益** —— j0（=0）三缝听不出重复（靶不存在），j12（=1.2 错开窗）反而在缝后引入 ~0.7s **电平坑**（有伤）⇒ 稳态环境床源不必开；届时下限 `jitter ≥ patch`、上限 `(床长−补丁)/(段数−1)` |
| `settle_frames`（裁重叠） | **0** | −1 自动档治糊但**会引入跳帧**；旧固定 `trim=22` 口径已作废。「糊」的大头是链路整段细节损失，不是段头沉降区（2026-09-19 采样链 A/B 定案）。 |

**判据边界（诚实声明）**：床窗判据与台词守卫都是**能量型**（帧 RMS vs 全源中位），
不是语音识别 —— **人声占全源 >50% 帧的素材**（播客/连续旁白）两套判据都会漏，
那种素材 patch 本来就无意义；低电平人声（只比环境高 ~6 dB）也可能漏。
一切判定日志显式打印、可审计；**最终判定是人耳**（四件套 ① 原速原片）。

---

### 7.4 API / 脚本用户：不开画布怎么用（⚠ 先看"哪些是 UI 特性"）

本包有一半新能力是**前端 JS 实现**的（画布按钮）。用 `/prompt` 提交 JSON 的脚本，先看这张表：

| 能力 | UI 用户 | API / 脚本用户 |
|---|---|---|
| 续接本体（桥 / 裁重叠 / 后处理 / 音频缝） | ✅ 连线 | ✅ **一样能用**（纯节点） |
| 音频 PCM 边车（`save_pcm`，音频代际 2→1） | ✅ 默认开 | ✅ **一样能用**（节点落盘 + 把路径回显进 history） |
| 词分发（第 k 段喂第 k 块词） | ✅ 填 `prompts` | ❌ **`prompts` 不生效**（前端实现）——脚本里自己逐段改词再排队 |
| 连跑 / 自动成片（⏩ / `auto_concat` / 🧩） | ✅ 点按钮 | ❌ 同上（按钮与 `auto_concat` 都是前端实现） |
| **把 N 段拼成一条成片** | ✅ 🧩 按钮 | ✅ **三条非 UI 路径**（见下） |

> ⚠️ `status` / `prompts` / `prompt_target` / `auto_concat` / `concat_name` / `audio_out` / `video_crf`
> 这几格**只在画布里有用**（前两者供显示与前端分发，后五者由前端读出来再发给后端）。
> 脚本提交 JSON 时它们会被忽略（不报错），**不要靠它们**。

**拼接的三条非 UI 路径**（都走同一份核心代码 `relay_core.assemble_mp4_segments`，行为完全一致）：

① **命令行**（最省事；用装了 torch+av 的 python，通常就是 ComfyUI 的解释器）：

```bash
python tools/concat_segments.py s1.mp4 s2.mp4 s3.mp4 -o film.mp4 --audio aac256
# 可选：--audio aac192|lossless   --crf 16   --json
#        --pcm p1.safetensors p2.safetensors - -     ← 逐段 PCM 边车，顺序=段序，`-` 表示该段没有
```
退出码 **0 = 过四项断言**，1 = 没过（片可能已写出，留作取证）。

② **库调用**（嵌进你自己的流水线）：

```python
import sys; sys.path.insert(0, "<ComfyUI>/custom_nodes/ComfyUI-H3-Latent-Relay")
from relay_core import assemble_mp4_segments
rep = assemble_mp4_segments(["s1.mp4", "s2.mp4"], "film.mp4",
                            audio_codec="aac", audio_bitrate="256k",
                            pcm_paths=["p1.safetensors", None])   # 边车可选，缺项填 None
print(rep["ok"], rep["asserts"], rep["report"])
```

③ **后端路由**（你已经在跑 ComfyUI，想让**服务端**自己从 history 取段）：

```bash
curl -X POST http://127.0.0.1:8188/h3relay/concat -H "Content-Type: application/json" \
  -d '{"prompt_ids":["<id1>","<id2>"],"count":0,"out_name":"film","audio_out":"aac_256k","video_crf":16}'
```
`prompt_ids` 不给时回扫 history（取最近 `count` 次「图里带该 Chain 节点」的提交）；
`audio_out` 三档 = `aac_256k`（默认）/ `aac_192k` / `pcm_lossless`。

**边车（PCM）路径怎么拿**：脚本里两条路 —— 按段序自己传给 `--pcm`；
或从 history 取（与路由同一判据，键名固定 `h3relay_pcm`）：

```bash
curl -s http://127.0.0.1:8188/history/<prompt_id> | \
  python -c "import json,sys;d=json.load(sys.stdin);\
  print([o for n in d.values() for o in ((n[\"outputs\"].get(\"18\") or {}).get(\"h3relay_pcm\") or [])])"
```
（`18` 换成你图里「裁重叠」/「音频缝」的节点 id；两个都有时优先「音频缝」那份。）

**依赖**：拼接需要 `av`（宿主 ComfyUI 自带；缺了 `pip install "av>=17"`）；
其余功能只依赖 torch + safetensors。**边车是纯增量**：它不改变段文件的任何一个字节，
外部工具（ffmpeg 等）照旧读段文件即可。

## 8. 离线自测

```bash
python tests/test_relay_core.py         # 期望「失败 0」（本版 392 项）
node   tests/test_prompt_dispatch.mjs   # 期望「失败 0」（Chain 词分发纯函数，node 跑）
python tools/review_050.py              # 期望「失败 0」（文档—代码一致性）
python tools/smoke_nodes.py             # 期望「失败 0」（节点层冒烟）
```

> ⚠️ 校验器的**项数**只有被机检盯着的才写具体值：`test_relay_core`（H3f）· `review_050`（H3g 自引用，
> 覆盖 `ci.yml` / `docs/08` / `tools/README` / 本文件）· `test_v3_schema` 的节点数·input 数·项数（H3h）·
> `assert_default_exit`（H3i）。**其余一律用「失败 0」表述** —— 写死数字**必然漂移**
> （2026-09-25 实测：同一个数字在四处声明，只有一处有机检，另外两处漂了很久没人发现）。
> ⚠️ **写更正说明时别复述旧的错误数字** —— 那会污染上面这些机检的正则（踩过）。

命令行拼接入口（**给不开画布的用户**，见 §7.4）也在同一份单测里冒烟（26.43/26.44）：

```bash
python tools/concat_segments.py s1.mp4 s2.mp4 -o film.mp4 --json   # 退出码 0 = 过四项断言
```

脚本会自动上溯定位 ComfyUI 根目录；装在别处时用
`COMFYUI_PATH=/path/to/ComfyUI python tests/test_relay_core.py`。

**392 项断言，零 GPU、不加载模型**，覆盖二十七个方面 —— 例如：

| 组 | 覆盖 |
|---|---|
| 21 | 重叠区双向融合 blend：窗形权重（smoothstep / hann），两端导数为 0 |
| 26 | **多段拼接成片（0.6.7）**：探测 / 体检（「音频绕过裁重叠」判据）/ 画面流拷贝无损 / 音频逐段去 priming 对齐 / 退路 / history 条目筛选 |
| 27 | **潜空间分块放大（0.6.8）**：块数自选的合规边界（每块 ≥ 2·overlap+1）/ 单块恒等 / **逐帧独立算子下分块==整段**（替身 nearest×2）/ 时间维不许动 / 上游口径常量锁 —— ⚠️ 真模型含 3D 体积注意力 ⇒ **分块会改画面**（实测见 §9）；本组只锁拼接数学，不宣称「分块无损」 |

26 组明细与 `tools/` 清单见 [`docs/08-testing.md`](docs/08-testing.md)。

---

## 9. 排障

| 症状 | 原因 | 处置 |
|---|---|---|
| 段号 ≥1 却报错拿不到上一段 | `run_id` 不一致 / 上一段没落盘 | 两处 `run_id` 一字不差；确认第 1 段跑过 |
| 节点列表里一个都没有 | 装的不是带 H3 的 ComfyUI，或没重启后端 | 更新 ComfyUI + 重启 Python 进程。**诊断两步走**：① 看启动日志有没有 `[H3 Relay] v… 已加载｜节点 N 个（出口 …）｜时序契约 …` —— **没有这行 = 包根本没加载**（最常见是 `custom_nodes/` 下嵌套了两层）；② 有这行就 `curl -s 127.0.0.1:8188/h3relay/health`，一次拿到版本/出口模式/节点注册数/时序契约/上游依赖/Python+torch/OS |
| 续接"看起来没生效" | ComfyUI 版本不含 H3 支持 | 确认 `comfy_extras/nodes_minimax_h3.py` 存在 |
| 成片接缝处重播上一段 | 裁重叠的 `trim_frames` 没接桥 `[2]` | 按「4. 接线」补线 |
| **音画越到后面越不同步**（第 3 段起口型明显对不上） | **音频线没走「裁重叠」**：`VAEDecodeAudio` 直连了落盘节点，画面裁了音频没裁 ⇒ 每缝差 ~0.9s 且累积 | 把落盘节点的 `audio` 改接 `裁重叠 [1] audio`（或经 `音频缝 [0] audio`）——见 §4 |
| **连跑好几段，每段台词却一模一样** | 没填 `prompts`（连跑的默认行为就是**反复提交同一张图**、只改段号 ⇒ 词不换） | 在 Chain 上填 `prompts`（`---` 分块，第 k 块喂第 k 段）再点 ⏩ 连跑；只跑一段时手改 prompt 也行 |
| 拼成一条后每缝有 ~0.87s 静止画 / 声音整体前移 | 用了 `ffmpeg -f concat` 或 `acrossfade` 拼 | 见 §7.1 的拼法 + 自检 |
| 拼接报「第 k 段音频比视频长 0.X s ⇒ 很可能是音频线绕过了裁重叠」 | 落盘节点的 `audio` 接的是 `VAEDecodeAudio`（未裁原始音频） | 改接「裁重叠 `[1] audio`」（或经「音频缝 `[0] audio`」）后重跑该段，再拼 |
| 拼接报告说某段「音频源=AAC」 | 该段没有 PCM 边车（旧图 / `save_pcm` 关了 / 这次提交没跑「裁重叠」） | 想拿满音频质量：确认图中「裁重叠」的 `save_pcm` 开着、且音频从它出线，重跑该段再拼 |
| 🔍 放大节点报「需要先装作者的节点包（MIT）」 | 没装 `Comfyui_Minimax_h3_latent_Upscaler`（本包只是它的适配层，不复制其代码） | 按 §2.1 装包 + 从作者 HF 仓库下权重，重启后端 |
| 🔍 `model_name` 下拉里没有权重 | 权重没进 `ComfyUI/models/latent_upscale_models/`，或放进去了没重启 | 从 <https://huggingface.co/LBH-123-AI/Minimax_h3_latent_upscaler> 下载后**重启后端**（清单在加载时扫一次） |
| 🔍 把 `chunks` 调大以后**画面变了**（不只是显存降了） | 放大模型含 **3D 体积注意力**：分块切断跨块时间上下文，`overlap` 渐变只能缓解不能抵消。实测同 seed 同参 `chunks=1` vs `4`：逐帧 MAE **5.62/255**、0/192 帧相同（峰值 1483→1120 MB） | **`chunks=1` 才是与上游「整段推理」一致的唯一路径**；只在 OOM 时才加大，并当作「画质换显存」重新目检 |
| 开了 `lowfreq_pull` 后**每段要等 20 多秒** | 盒式模糊耗时 **∝ 核半径²**，而 `lowfreq_blur` 默认 **64**：实测 2.07MP/12 帧 **22.1 s**（0.80MP 是 8.6 s）—— 与**分辨率强相关**，且只在开了 `lowfreq_pull` 时才付（默认关 = 0 s） | 把 `lowfreq_blur` 降到 **16–32**：同条件实测 **k=32 → 5.6 s · k=16 → 1.6 s · k=9 → 0.6 s**。质量代价极小 —— 实测缝点阶跃 32/64 = **0.0011 vs 0.0024**（`docs/02`）⇒ **高分辨率下默认就该降档** |
| 选了 `pcm_lossless` 后**浏览器里放不出声音** | 成片音轨是 PCM f32，浏览器不放 PCM | 这是**母版档**的预期行为：拿给剪辑/归档。要能预览就用默认的 `aac_256k` |
| 日志有「⚠ PCM 边车写入失败」 | 磁盘满 / 目录不可写 / safetensors 缺失 | **不影响本段产物**；拼接会自动退回解码。清出空间或 `pip install safetensors` 即可 |
| 日志里**没有** 🧪 DTW / 裁量→跳跃曲线 / 外观三元组了 | 「裁重叠」的 `diagnostics` **默认关**（三路是纯打印、不参与裁量；对外省 ~0.4 s/段 CPU） | 要看就把 `diagnostics` 打开 —— 或走跑批入口传 `diagnostics=True`（本产线 `l1_api` 就是这么传的） |

完整排障表、工作流文件自检工具、API 提交方式见
[`docs/05-troubleshooting.md`](docs/05-troubleshooting.md)。

---

## 10. 常见疑问（FAQ）

> 都是被实际问到的问题，答案带**实测数字**（测法与复现命令见 `CHANGES.md` 与 `tools/`）。

### 10.1 自动连跑会不会越跑越占显存？会不会跑到后面硬件不够？
**显存不累加。** 每次提交是**独立一次执行**（连跑只是"再排一次队"），模型每段重新 stage ——
实测 4 段逐段 staged 量**完全一致**（`MiniMaxH3 21099MB / TEModel 17034MB / VideoVAE 2677MB / AudioVAE 576MB`）。
**会累加的是磁盘**：`output/relay_kit/<run_id>/` ≈ 12 MB/段（latent 8.9 + 音频 4）+ PCM 边车 ≈2 MB/段（可关）
+ 段 mp4 本身 ⇒ **约 25 MB/段、100 段 ≈ 2.5 GB**，建议按 `run_id` 归档。

### 10.2 跑长了画质会不会越来越差？
**不会因为"跑到后面"而自动劣化**：段间接力是 **latent 张量逐位传递**，不经像素、不重编码，
没有「解码→再编码」那种世代累加；每段只 VAE 解一次。
但**跨段外观漂移是真实存在的**（属于生成侧，不是管线损耗）：实测 4 段链的外观日志，
段2→段4 亮度均值 `0.3697 → 0.3107`（≈ −16%）、std −0.008、锐度 +0.0007（后两项非单调）。
要对症就开 `H3RelayPost` 的 `match_prev`（跨段统计匹配）/ `lowfreq_pull`
（⚠ 高分辨率下先把 `lowfreq_blur` 降到 32 —— 默认 64 在 2MP 上要 22 s/段，见 §9 排障表）。

### 10.3 走 PCM 边车，音画同步还能成立吗？
**成立，而且比老路更稳。** 边车是「裁重叠」**裁后**的音频 —— 与它输出的画面**同一刀**，天然等长同源。
拼接时按 `want = 该段帧数 ÷ fps × 采样率` **精确截/补**（长了裁尾、短了补静音并告警）
⇒ 每段音频长度 == 其视频长度，段间**零累积漂移**。实测 4 段 702 帧：四段落点 **0 样本**、
A·VΔ **0.0011 s**；`pcm_lossless` 档成片音轨与边车**逐位一致**。
⚠️ 边界：边车对齐的是**片段有效时长**，不是字幕级对位 —— 它保证的是"接缝不漂"。

### 10.4 不用自动拼接（只手动连节点）时，边车会不会影响别的流程？
**纯增量，段文件一个字节都不改。** 边车只是**多写一个文件**（≈2 MB/段）；
不点「🧩 拼成一条」、不开 `auto_concat` ⇒ 后端拼接路由根本不被调用。
**外部工具（ffmpeg、你自己的组装脚本）读的是 mp4，不看边车。**
不想留边车：把 `H3RelayTrimAV.save_pcm` 关掉即可（拼接会自动退回解码并在报告里写明）。

### 10.5 能不能用 MKV / 无损音轨？
**能存 MKV**（宿主 `SaveVideo` 的 `format` 有 `mp4/mkv/webm`），**但没收益**：
它的音轨编码是写死的（`libopus if WEBM else aac`；`CreateVideo` 也写死 aac）⇒ **从宿主拿不到无损音轨**。
我们的拼接走 PyAV、容器自己说了算，实测 **PCM（`pcm_s16le` / `pcm_f32le`）直接写进 mp4 就行**
⇒ 默认 mp4、无损档也是 mp4，**不引入 MKV 依赖**。
（实测：`flac` 写进 mp4/mov/mkv 一律失败、只能进原生 `.flac`，所以无损档选 PCM 不选 FLAC。）

### 10.6 拼接要花多久？会不会卡在"找段文件"上？
**都不耗时。** 实测 4 段 29.25 s 成片：探测 0.12 s + 拼接 **1.26 s**（画面流拷贝 + 音频重编码）+ 四项断言 **0.019 s**。
**找路径不扫目录、不猜文件名**：只在 ComfyUI 已给的 `/history` 里做字典扫描 + `os.path.isfile`
（微秒级、零 GPU、零磁盘遍历）；缺文件在**进编码之前**就报出来（不会"等半天才发现取不到"）。
**不会把路径定死在官方 `output/`** —— 那会造出反向的"只有本地能跑"：第三方落盘节点用「自定义保存路径」、
用户启动带 `--output-directory`、容器挂载盘，写死就**直接失败**。本包口径 = **只信节点自己报的路径**
（`abs_path` 优先，缺了才按 `type`+`subfolder` 拼；键名与字段名都不写死白名单）。

### 10.7 节点表里配了脚本，但按钮点了没反应？
`app.queuePrompt` / `api.fetchApi` 属**前端内部接口**，不同 ComfyUI 版本可能是别的形状。
本包在缺了的时候会在 Chain 的 `status` 格里**明说**（不是静默失效），并指向仍然可用的路径 —— 见 §7.4。

### 10.8 出片里段与段之间"整幅等幅左右摆"是管线问题吗？
**多数不是**，而是**出词层的运动指令自相矛盾**：把同一方向的位移拆给了不等价的自由度 ——
例如同时要求「镜头横向连续平移」**和**「画面里某块景物放大」（后者是**推近**）。
模型在两种运动解读之间逐帧来回选 ⇒ 整幅等幅抖动（幅度恒定、不衰减）。
**修法**：一段只给**一种**主动运动；给**速度基准**（"与步伐同速"）或**开放端**（"永不到达某位置为止"）；
背景一律写**结果**（"在身后滑出画框"）不写缩放目标；主体不同向时别写"随某某一起移动"。

### 10.9 一采 → 🔍 放大 → 二采 的链路里，二采的 guider 为什么不能接桥的 `conditioning`？
**因为钉帧走的是 `minimax_keyframes`，而打包器假定 keyframe 与本段目标同网格。**
放大之后目标网格变了（例：26×46 → 30×54 latent），桥的锚仍是原生网格 ⇒ 打包时对不上号，
一采跑完、二采第一步当场炸：

```
RuntimeError: shape mismatch: value tensor of shape [2392, 96]
              cannot be broadcast to indexing result of shape [3134, 96]
（comfy/ldm/minimax/model.py: all_video_rows[~img_update] = cond_video_rows）
```

**接法**：只有**一采**（原生域）的 `BasicGuider` 接桥的第 4 路；**二采**接出词节点的 `positive`。
二采照样不会重绘缝区 —— **latent 侧的钉住（拷贝前缀 + 噪声掩码）与网格无关，仍然生效**，
被放弃的只是 conditioning 侧那半条"取景钉帧"。
对照：外观锚走的是另一条通道 `minimax_refs`，它**允许**与目标异分辨率（`ref_anchor_latent` 就是靠这个
跨分辨率当锚的）—— 别把两条通道当成一回事。完整接线见 [`examples/`](examples/README.md) 的全流程示例。

---

## 11. 原理与机制

不看也能把片子跑出来。要点三句话：

- **为什么不发糊**：拷贝桥在 `hard` 档下让钉住区**零重绘**（掩码 0 区每步被钉回拷贝 latent），
  复现伪影这一类从机制上消失；
- **为什么只认特定帧数**：H3 的 latent 帧跨度按 token 相位（k%5）决定，窗口只认 `5+17k`；
  起止必须落在周期边界，越界一律 raise，不静默吸附；
- **为什么必须裁头**：钉住前缀在成片里是**重播**，不裁就会在拼接处重播/跳变；裁多少由节点当场量。

完整推导（两条路线的历史与取舍、协议出处、时序网格、裁多少帧怎么量、运行时契约护栏、音频窗口径）
见 [`docs/01-mechanism.md`](docs/01-mechanism.md)。

---

## 12. 许可与出处

- **本包 = MIT**（见 [`LICENSE`](LICENSE)）。可商用、可修改、可再发布，保留版权声明即可。
- **第三方出处与署名 → [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)**：
  机制/契约层面参考了 `ComfyUI_MiniMaxH3_Director`（**Apache-2.0**，已一手核对）与
  `comfyui-minimax-h3-audio-T8`（⚠️ **GPL-3.0-or-later**，仅以其用法佐证原生掩码契约、**未复制其代码**）；
  算法出处见论文表。
- ⚠️ **运行时宿主 ComfyUI 是 GPL-3.0**（本包在进程内 import 其模块，**但不复制其代码**；
  ComfyUI 的 LICENSE 内无自定义节点/插件例外条款）。事实、我方立场与未决事项见
  [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md) **§一·C**。
- ⚠️ **关于 `ComfyUI-H3-Motion-Context`（GPL-3.0）**：`H3RelayMotionContext` 的锚位合成段
  在 **v0.2.1–v0.5.0 的公开历史**里曾与该包构成**表达层重合**，已于 2026-09-19 **整体重写**，
  当前版本不含其派生表达。历史提交**未改写**，可检出复核（NOTICES §一·B）。
  ⇒ **严格合规请使用 ≥ 0.6.0。**
- ⚠️ **可选运行时依赖 `Comfyui_Minimax_h3_latent_Upscaler`（MIT，作者 `LBH-123-AI`）**：
  `H3RelayLatentUpscale` 运行时调用其节点类并**沿用其权重目录**（`models/latent_upscale_models/`）；
  本包**不复制其代码**，但分块拼接的**数学口径**与其一致 ⇒ 出处与署名见
  [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md) **§一·D**。权重请从作者处下载（§2.1）。
- **模型权重不含在本包内**：MiniMax-H3 等权重需自备，其许可与商用条件由提供方决定。
- **使用合规**：本包是通用视频生成工具，使用者须自行遵守当地法律与所用模型/素材的许可；
  不得用于伪造他人肖像、传播虚假信息或侵犯他人权利。MIT 不含任何用途担保。
- **本包非 MiniMax 官方作品**，与 MiniMax、ComfyUI 官方均无隶属或背书关系。
- 再发布时请一并保留 `LICENSE`、`THIRD-PARTY-NOTICES.md` 与 `licenses/`。

---

## 13. 文档索引

| 文件 | 内容 |
|---|---|
| [`docs/01-mechanism.md`](docs/01-mechanism.md) | 两条续接路线的历史与取舍 · 协议出处 · **放大之后哪条线留在原生域（keyframes 与 refs 的分辨率态度相反）** · 时序网格 · 为什么必须裁头 · 裁多少帧怎么量 · 运行时契约 · 音频窗口径 |
| [`docs/02-parameters.md`](docs/02-parameters.md) | 参数全量手册（桥 / 裁重叠 / 后处理 Post / 音频缝 / **🔍 潜空间分块放大**） |
| [`docs/03-sampling-and-design.md`](docs/03-sampling-and-design.md) | 采样链取舍 · 本包的设计取向（该量的不让用户配） |
| [`docs/04-canvas-and-widgets.md`](docs/04-canvas-and-widgets.md) | 画布外观 · `advanced` 折叠 · 旧图看不到 `prev_tail` 的处理 |
| [`docs/05-troubleshooting.md`](docs/05-troubleshooting.md) | 完整排障表 · 工作流文件自检 · API 提交 |
| [`docs/06-continuity-scripting.md`](docs/06-continuity-scripting.md) | 出词纪律：段首缓冲 · 台词安全时刻 · 末帧锚链 · 音频缝配套 |
| [`docs/07-chain.md`](docs/07-chain.md) | Chain 自动连跑 |
| [`docs/08-testing.md`](docs/08-testing.md) | 离线自测：27 组断言明细 · `tools/` 清单 |
| [`docs/09-metrics.md`](docs/09-metrics.md) | 观测量参考区间（DTW 残留 / 外观漂移）· 怎么自校准 |
| [`CHANGES.md`](CHANGES.md) | 版本史与每次实测证据 |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | 开发环境 · 自测纪律 · 许可条款 |
| [`SECURITY.md`](SECURITY.md) | 密钥 / 依赖 / 网络行为声明 |
| [`tools/README.md`](tools/README.md) | 七个脚本的用途与期望值（六个自检/取证 + 一个拼接 CLI） |
| [`examples/README.md`](examples/README.md) | 两份可直接打开的工作流：**最小续接演示**（18 节点）与**全流程示例**（45 节点，8 个节点全在场）· 生成器 |
