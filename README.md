# ComfyUI-H3-Relay-Kit

MiniMax-H3 多段续接的 **latent 桥**（零重编码）—— 一个可独立使用、**零第三方节点包依赖**的 ComfyUI 节点包。
只依赖 ComfyUI 自带的 `torch` 与 `safetensors`，不与任何第三方 H3 节点包耦合。

| 项 | 值 |
|---|---|
| 版本 | **0.6.0**（7 个节点，复合桥 `H3RelayCopyBridge` = **唯一桥**） |
| 许可 | **MIT**（第三方出处见 [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)） |
| 宿主 | 需要**带 MiniMax-H3 支持的 ComfyUI**（其自身为 GPL-3.0，见「11. 许可与出处」） |

> **本文约定（结构锁死）**：12 节顺序固定，编号即目录。
> ① 槽位号一律 **`[N]` = 0 起算**（UI 上第 N 个口 = `[N-1]`）；
> ② 表格只放结论，长推导全部外链 `docs/`；
> ③ 前三节读完就能跑，4–8 节是查表，9 节以后是验收与背景。

## 0. 目录

| 节 | 内容 | 什么时候看 |
|---|---|---|
| [1](#1-它解决什么问题) | 它解决什么问题 | 第一次来 |
| [2](#2-安装) | 安装 | 装包时 |
| [3](#3-5-分钟跑通) | 5 分钟跑通 | 装完立刻 |
| [4](#4-接线唯一桥) | 接线（唯一桥） | 每次搭图 |
| [5](#5-节点速览) | 节点速览（7 个） | 找节点时 |
| [6](#6-参数主旋钮) | 参数（主旋钮） | 要调参时 |
| [7](#7-音频缝) | 音频缝 | 出片有咔哒声 |
| [8](#8-离线自测) | 离线自测 | 改完代码 / 提 PR |
| [9](#9-排障) | 排障 | 跑不通时 |
| [10](#10-原理与机制) | 原理与机制（概述） | 想搞懂为什么 |
| [11](#11-许可与出处) | 许可与出处 | 再发布前 |
| [12](#12-文档索引) | 文档索引 | 找细节 |

---

## 1. 它解决什么问题

H3 分段生成时，"续接"要回答一件事：**新的一段怎么知道上一段结束在什么状态？**

主流做法是**像素续接**：上一段 mp4 解码成帧 → VAE 重编码成 latent → 当锚塞进条件。问题在于：

- 多一次 VAE 往返，**有量化损失**；
- 重编码的 latent 与采样时那份**不是同一个东西**，锚点会漂移；
- 只能锚一个整块，位置固定在第 0 帧。

本包做的是 **latent 桥**：直接从上一段的 AV latent 里切出尾段，切成逐 token 的块，
按真实位置写进 `minimax_keyframes`。**不重编码**，锚点与采样同源。

---

## 2. 安装

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/ZenHG/ComfyUI-H3-Relay-Kit.git
```

或下载 ZIP → 解压 → 文件夹改名为 `ComfyUI-H3-Relay-Kit` → 放进 `custom_nodes/`。

装好后**重启 ComfyUI 后端**（ComfyUI-Manager 点 *Restart*；没装就重启 Python 进程）——
仅刷新浏览器不会加载新节点。节点列表里搜 `🔗 H3 续接` 即可看到全部 7 个节点。

依赖只用 `torch`（**顶层 import**，缺了整包注册失败）与 `safetensors`（延迟 import，缺了只在落盘那步报错）。

**宿主要求**：本包硬依赖带 MiniMax-H3 支持的 ComfyUI（需要 `comfy_extras/nodes_minimax_h3.py`
与消费 `minimax_keyframes` / `minimax_refs` 的 `comfy/model_base.py`）。装到不含 H3 的旧版
ComfyUI 上，节点能注册但**续接静默无效**——用前先确认 ComfyUI 版本。

**不依赖任何第三方 H3 节点包**：`minimax_keyframes` / `minimax_refs` / `resolved_frame_index`
全是 ComfyUI **原生**协议，零 monkey patch。

随包的 [`examples/`](examples/README.md) 有一份可直接打开的最小演示工作流（18 个节点，含 1 个画布注释框）。

---

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
                                              [3] audio  ← AUDIO
                                              [1] trim_frames ← 桥 [2]
                                        │
                          [0] images ───┴──→ CreateVideo → SaveVideo
                          [3] prev_tail ──→（可选）后处理 Post [1] guide
```

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

**7 个节点分三层——一个工作流只用得到前 3 个**（后 4 个都是"删掉照样跑"的可选项）：

| 层 | 节点 | 说明 |
|---|---|---|
| **必备 3** | Latent 存 · 拷贝桥（复合桥）· 裁重叠 | 少一个就不叫续接 |
| **可选 3** | 后处理 Post · 音频缝 · 连跑 Chain | 各自独立、默认全关 = 逐位直通 |
| **手动接线才用 1** | Latent 读 | 桥自己会从磁盘取源；只有要**显式换源**时才手动接 |

| 节点 | 作用 |
|---|---|
| 🔗 **H3 续接 Latent 存** | 本段采样后把 AV latent 落盘到 `output/relay_kit/<run_id>/stage_NNNNN.safetensors` |
| 🔗 **H3 续接 Latent 读** | 读回上一段（段号 −1），断点续跑可指定 `explicit_path` 换源 |
| 🔗 **H3 续接 拷贝桥（复合桥）** | 上一段尾 AV latent **逐位拷贝**进本段初始 latent + 噪声掩码（钉住区不重绘）。接 `[16] conditioning` 时并联钉帧（管取景）与 `[0] latent` 拷贝（管运动）；不接则只做拷贝。（`mask_mode` 各档见 [`docs/02`](docs/02-parameters.md)） |
| 🔗 **H3 续接裁重叠** | 裁掉本段头部的重生成帧（音画同裁）——不裁就会在拼接处重播/跳变；`[3]` 输出 `prev_tail` = 上一段末帧 |
| 🔗 **H3 续接后处理 Post** | **画质域**：跨段统计匹配 / 低频残差 / 色调 / 反卷积 / 高频迁移 / 糊区锐化，全部默认关 |
| 🔗 **H3 续接音频缝** | **音频域**：把上一段环境声补进本段头部，**长度守恒**（零 A/V 位移），默认关 |
| 🔗 **H3 续接连跑 Chain** | 自动连跑控制器：同分组框内自动推进「桥 + 落盘」段号并排队 |

> **三个域，别混挂**：时间轴 = `H3RelayTrimAV`（冻结）／画质域 = `H3RelayPost`／音频域 = `H3RelayAudioSeam`。
> 节点数不是复杂度，**"哪些线必须接对"**才是。

---

## 6. 参数（主旋钮）

默认值就是**实测过的推荐值**，不是"待你优化的起点"。

| 节点 | 参数 | 默认 | 说明 |
|---|---|---|---|
| 桥 | `context_frames` | `22` | 钉住窗帧数，只认 `5+17k`（5/22/39/…/124），须小于本段帧数 |
| 桥 | `mask_mode` | `hard` | 🟢 生产档 `hard`（钉住区零重绘）｜🟡 对照 `ramp`/`window`｜🔴 实验 `taper`/`blend`（`taper` **不钉住**） |
| 桥 | `ref_anchor_stage` | `-1` | ≥0 时自动读该段作**全局外观锚**，防长程漂移 |
| 裁重叠 | `settle_frames` | `0` | 不裁沉降；`seam_ghost` 同样默认 `0` |
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

出词层面的配套纪律（段首缓冲、台词安全时刻、末帧锚链）见
[`docs/06-continuity-scripting.md`](docs/06-continuity-scripting.md)。

---

## 8. 离线自测

```bash
python tests/test_relay_core.py     # 期望 281/0
python tools/review_050.py          # 期望 77/0（文档—代码一致性）
python tools/smoke_nodes.py         # 期望 15/0（节点层冒烟）
```

脚本会自动上溯定位 ComfyUI 根目录；装在别处时用
`COMFYUI_PATH=/path/to/ComfyUI python tests/test_relay_core.py`。

**281 项断言，零 GPU、不加载模型**，覆盖二十二个方面 —— 例如：

| 组 | 覆盖 |
|---|---|
| 21 | 重叠区双向融合 blend：窗形权重（smoothstep / hann），两端导数为 0 |

22 组明细与 `tools/` 清单见 [`docs/08-testing.md`](docs/08-testing.md)。

---

## 9. 排障

| 症状 | 原因 | 处置 |
|---|---|---|
| 段号 ≥1 却报错拿不到上一段 | `run_id` 不一致 / 上一段没落盘 | 两处 `run_id` 一字不差；确认第 1 段跑过 |
| 节点列表里一个都没有 | 装的不是带 H3 的 ComfyUI，或没重启后端 | 更新 ComfyUI + 重启 Python 进程 |
| 续接"看起来没生效" | ComfyUI 版本不含 H3 支持 | 确认 `comfy_extras/nodes_minimax_h3.py` 存在 |
| 成片接缝处重播上一段 | 裁重叠的 `trim_frames` 没接桥 `[2]` | 按「4. 接线」补线 |

完整排障表、工作流文件自检工具、API 提交方式见
[`docs/05-troubleshooting.md`](docs/05-troubleshooting.md)。

---

## 10. 原理与机制

不看也能把片子跑出来。要点三句话：

- **为什么不发糊**：拷贝桥在 `hard` 档下让钉住区**零重绘**（掩码 0 区每步被钉回拷贝 latent），
  复现伪影这一类从机制上消失；
- **为什么只认特定帧数**：H3 的 latent 帧跨度按 token 相位（k%5）决定，窗口只认 `5+17k`；
  起止必须落在周期边界，越界一律 raise，不静默吸附；
- **为什么必须裁头**：钉住前缀在成片里是**重播**，不裁就会在拼接处重播/跳变；裁多少由节点当场量。

完整推导（两条路线的历史与取舍、协议出处、时序网格、裁多少帧怎么量、运行时契约护栏、音频窗口径）
见 [`docs/01-mechanism.md`](docs/01-mechanism.md)。

---

## 11. 许可与出处

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
- **模型权重不含在本包内**：MiniMax-H3 等权重需自备，其许可与商用条件由提供方决定。
- **使用合规**：本包是通用视频生成工具，使用者须自行遵守当地法律与所用模型/素材的许可；
  不得用于伪造他人肖像、传播虚假信息或侵犯他人权利。MIT 不含任何用途担保。
- **本包非 MiniMax 官方作品**，与 MiniMax、ComfyUI 官方均无隶属或背书关系。
- 再发布时请一并保留 `LICENSE`、`THIRD-PARTY-NOTICES.md` 与 `licenses/`。

---

## 12. 文档索引

| 文件 | 内容 |
|---|---|
| [`docs/01-mechanism.md`](docs/01-mechanism.md) | 两条续接路线的历史与取舍 · 协议出处 · 时序网格 · 为什么必须裁头 · 裁多少帧怎么量 · 运行时契约 · 音频窗口径 |
| [`docs/02-parameters.md`](docs/02-parameters.md) | 参数全量手册（桥 / 裁重叠 / 后处理 Post / 音频缝） |
| [`docs/03-sampling-and-design.md`](docs/03-sampling-and-design.md) | 采样链取舍 · 本包的设计取向（该量的不让用户配） |
| [`docs/04-canvas-and-widgets.md`](docs/04-canvas-and-widgets.md) | 画布外观 · `advanced` 折叠 · 旧图看不到 `prev_tail` 的处理 |
| [`docs/05-troubleshooting.md`](docs/05-troubleshooting.md) | 完整排障表 · 工作流文件自检 · API 提交 |
| [`docs/06-continuity-scripting.md`](docs/06-continuity-scripting.md) | 出词纪律：段首缓冲 · 台词安全时刻 · 末帧锚链 · 音频缝配套 |
| [`docs/07-chain.md`](docs/07-chain.md) | Chain 自动连跑 |
| [`docs/08-testing.md`](docs/08-testing.md) | 离线自测：22 组断言明细 · `tools/` 清单 |
| [`CHANGES.md`](CHANGES.md) | 版本史与每次实测证据 |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | 开发环境 · 自测纪律 · 许可条款 |
| [`SECURITY.md`](SECURITY.md) | 密钥 / 依赖 / 网络行为声明 |
| [`tools/README.md`](tools/README.md) | 五个自检脚本的用途与期望值 |
| [`examples/README.md`](examples/README.md) | 最小演示工作流与生成器 |
