# ComfyUI-H3-Latent-Relay

🌐 **中文（本页，默认与唯一真相源）** · [English (condensed)](README_EN.md)

MiniMax-H3 多段续接的 **latent 桥**（零重编码）—— 一个可独立使用、**零第三方节点包依赖**的 ComfyUI 节点包。
只依赖 ComfyUI 自带的 `torch` 与 `safetensors`，不与任何第三方 H3 节点包耦合。

| 项 | 值 |
|---|---|
| 版本 | **0.6.16**（8 个节点，复合桥 `H3RelayCopyBridge` = **唯一桥**） |
| 许可 | **MIT**（第三方出处见 [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)） |
| 宿主 | **ComfyUI ≥ 0.37.0，且带 MiniMax-H3 支持**（宿主自身为 GPL-3.0，见 §许可与出处） |

> 🔴 **一条铁律**：**每个功能都有两条用法 —— 画布手动（多数用户）与 API 提交图 JSON，二者必须走同一套节点实现。**
> 只写在脚本里的功能不算本包的功能（判据见 [`CONTRIBUTING.md`](CONTRIBUTING.md)）。

**它解决什么**：H3 分段生成时，"续接"要回答"新的一段怎么知道上一段结束在什么状态"。
主流做法是**像素续接**（解码上一段 mp4 → VAE 重编码 → 当 anchor），代价是多一次 VAE 往返（有损）、
anchor 与采样 latent 不同源（会漂）、只能锚第 0 帧。本包直接从上一段的 AV latent 里切尾段、
按真实位置写进 `minimax_keyframes`：**不重编码**，也**不在磁盘上缓存解码帧**（每段只在
`output/relay_kit/<run_id>/` 多一份约 12 MB 的 latent 边车）。原理与实测见 [`docs/01`](docs/01-mechanism.md)。

---

## 核心功能

**8 个节点分三层 —— 一个最小工作流只用得到前 3 个**（后 5 个是"删掉照样跑"的可选项）：

| 层 | 节点 | 说明 |
|---|---|---|
| **必备 3** | Latent 存 · 拷贝桥（复合桥）· 裁重叠 | 少一个就不叫续接 |
| **可选 4** | 后处理 Post · 音频缝 · 连跑 Chain · 潜空间分块放大 | 各自独立、**默认全关 = 逐位直通** |
| **手动接线才用 1** | Latent 读 | 桥自己会从磁盘取源；只在要**显式换源**时才手动接 |

| 节点 | 作用 |
|---|---|
| 🔗 **H3 续接 Latent 存** | 本段采样后把 AV latent 落盘到 `output/relay_kit/<run_id>/stage_NNNNN.safetensors` |
| 🔗 **H3 续接 Latent 读** | 读回上一段（段号 −1），断点续跑可指定 `explicit_path` 换源 |
| 🔗 **H3 续接 拷贝桥（复合桥）** | 上一段尾 AV latent **逐位拷贝**进本段初始 latent + 噪声掩码（钉住区不重绘）；接 `[16] conditioning` 时**并联**钉帧（管取景）。`mask_mode` 默认 `hard` |
| 🔗 **H3 续接裁重叠** | 裁掉本段头部的重生成帧（**音画同裁**）——不裁就会在拼接处重播/跳变；`[3]` 输出 `prev_tail` |
| 🔗 **H3 续接后处理 Post** | **画质域**：跨段统计匹配 / 低频残差 / 色调 / 反卷积 / 高频迁移 / 糊区锐化，全部默认关 |
| 🔗 **H3 续接音频缝** | **音频域**：把上一段环境声补进本段头部，**长度守恒**（零 A/V 位移），默认关 |
| 🔍 **H3 潜空间分块放大** | **画质域 · latent 层**：拆 AV 打包 latent → 逐块过学习式 3D 放大器 → 回包。**零去噪、时间维一帧不动、音频原样带回**（需可选上游依赖，见 §安装） |
| 🔗 **H3 续接连跑 Chain** | 自动连跑控制器：自动推进段号并排队；0.6.7 起还能**换词**（`prompts` 按 `---` 分块）与**拼片**（🧩） |

> **三个域别混挂**：时间轴 = `H3RelayTrimAV`（冻结）／画质域 = `H3RelayPost`／音频域 = `H3RelayAudioSeam`。

---

## 环境依赖

| 依赖 | 必需？ | 说明 |
|---|---|---|
| **带 MiniMax-H3 支持的 ComfyUI ≥ 0.37.0** | ✅ | 需要 `comfy_extras/nodes_minimax_h3.py` 与消费 `minimax_keyframes` / `minimax_refs` 的 `comfy/model_base.py`。装在旧版上节点能注册但**续接静默无效** |
| `torch` | ✅ | **顶层 import**（缺了整包注册失败），ComfyUI 自带 |
| `safetensors` | ✅ | 延迟 import（缺了只在落盘那步报错），ComfyUI 自带 |
| `av >= 17` | 拼接时 | 「拼成一条」与 `tools/concat_segments.py` 需要（宿主自带）。缺模块会**明确报错**并给出安装指引；PyAV 太旧、拿不到流拷贝模板时会**报出原因并退回重编码**（报告里写明），不静默 |
| 社区节点包 `Comfyui_Minimax_h3_latent_Upscaler` + 其权重 | 🔍 放大节点需要 | 见 §安装·可选 |

`minimax_keyframes` / `minimax_refs` / `resolved_frame_index` 全是 ComfyUI **原生协议**：
零 monkey patch、**零 patch 别人的包**。

---

## 安装

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/ZenHG/ComfyUI-H3-Latent-Relay.git
```

或下载 ZIP → 解压 → 文件夹改名为 `ComfyUI-H3-Latent-Relay` → 放进 `custom_nodes/`。

或 **ComfyUI Manager → Custom Nodes Manager → 搜 `h3-latent-relay`**
（节点页：<https://registry.comfy.org/nodes/h3-latent-relay>）。包名与仓库名不同是官方要求：
包名不允许带 "ComfyUI"。

**装完必须重启 ComfyUI 后端**（ComfyUI-Manager 点 *Restart*；没装就重启 Python 进程）——
仅刷新浏览器不会加载新节点。节点列表里搜 `🔗 H3 续接`（7 个）+ `🔍 H3 潜空间分块放大` 即为全部 8 个。

**可选：用 🔍 潜空间分块放大才需要装**（不装不影响其他 7 个节点，用时会报一条写明仓库与权重地址的错）：

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler.git   # 作者 LBH-123-AI（MIT）
```
再从 <https://huggingface.co/LBH-123-AI/Minimax_h3_latent_upscaler> 下**权重**放进
`ComfyUI/models/latent_upscale_models/`，重启后端。⚠️ 要的是 **H3 latent 放大权重**（24 通道专用），
**不是** ESRGAN 那类像素放大模型。

**节点 API 出口：V3（默认）/ V1**（`H3RELAY_NODE_API=v1` 一行回退，改后需重启）。
两条出口的节点名、输入输出顺序、默认值全部相同（机检逐字段一致）⇒ **切换不需要改图**。

> **为什么只能二选一**：宿主加载顺序是 `if 模块有 NODE_CLASS_MAPPINGS → return` / `elif 模块有 comfy_entrypoint`
> ⇒ 两套一起导出时 **V3 永不生效**；所以 V3 模式下本包把 `NODE_CLASS_MAPPINGS` 显式置 `None`。
> **V3 的已知差异**：schema 在**包加载时**构造（V1 是惰性的）⇒ 某个节点的参数表构造失败时只会
> **跳过那一个节点**并打日志，其余照常加载（不会整包失效）。
> **下限依据**：`web/` 挂载靠宿主 `WEB_DIRECTORY`、元数据靠 `comfy_config` 解析 `pyproject.toml` ——
> 两条都在 **0.37.0** 上实测通过；`requires-python = ">=3.10"` 是刻意保守
> （本包未使用 3.10+ 独有语法，真要放宽到 3.9 亦无语法障碍）。

---

## 使用方法

### 5 分钟跑通

> 懒人路线：打开 [`examples/minimal_relay_official.json`](examples/README.md)，把 4 个加载器的下拉改成你本机的模型文件。

**第 1 段**：① 「段号」填 `0` → ② 在官方出词节点填 prompt → ③ 点 Queue。
（**什么都不用做**：段号 0 时桥自动直通。别为了"没有上一段"去拔线或旁路——`context_latent` 是必填。）

**第 2 段起**：④ 「段号」改成 `1`（**只改这一个数**）→ ⑤ prompt 换成第 2 段内容（开头接住上一段的动作/机位，别重新起手）→ ⑥ 再点 Queue。
日志里应看到 `钉住 22 帧` / `裁首 N 帧 = 钉住 22 + 沉降 0` / `起点干净`。

| 关键参数 | 第 1 段 | 第 2 段起 | 填在哪 |
|---|---|---|---|
| `stage_index`（段号） | `0` | `1`、`2`… | **读上段 latent + 桥 + 落盘**（三处必须一样大；用 Chain 会自动同步） |
| `run_id` | 同一个片子名，如 `myfilm` | **与第 1 段一字不差** | 读上段 latent + 桥 + 落盘 |
| `context_frames` | `22` | `22`（不用动） | 桥（钉住窗，只认 5/22/39/56/73/90/107/124） |
| `settle_frames` | —（首段不裁） | 保持 `0` | 裁重叠（`0` = 不裁沉降，默认即推荐） |
| `seam_ghost` / `settle_sharpen` | — | 保持 `0` | 裁重叠 / 后处理 Post |

| 最常见的三个翻车点 | 处置 |
|---|---|
| 报错「说清是第 N 段却拿不到上一段」 | 两处 `run_id` 必须一字不差；确认第 1 段跑过 |
| 画面从第 1 帧就开始重播上一段 | 「裁重叠」没接上，或它的 `trim_frames` 没接桥的 `[2]` |
| 换新片子却接了旧片尾巴 | 没换 `run_id`（同名会覆盖同段号文件） |

### 最小接线

```
出词节点（官方 / 第三方）
  ├─ positive ─────────────────→ 桥 [16] conditioning
  └─ LATENT ──────────────────→ 桥 [0] latent
                                桥 [1] context_latent  ← 留空（自动读上一段）
                                桥 [2] context_frames  = 22
                                桥 [17] run_id / [18] stage_index
                                        │
          桥 [0] latent ───────────────┼──→ 采样器 latent_image   ★必须过桥
          桥 [3] conditioning ─────────┼──→ 采样器 positive
          桥 [2] trim_frames ──────────┼──→ 裁重叠 [1] trim_frames  ★必须接
                                        │
                                 采样器 → LATENT ──→ 🔗 续接 Latent 存 [0]
                                                 └──→ VAEDecode / VAEDecodeAudio
                                                           ↓
                               🔗 续接裁重叠 [0] images ← IMAGE ／ [3] audio ← VAEDecodeAudio
                                     │
                               [1] audio（可选经 🔗 音频缝）──────→ CreateVideo → SaveVideo
                               [0] images ───────────────────────↗
```

> 🔴 **音频线只有一条是对的**：`裁重叠 [1] audio`（或再经 `音频缝 [0] audio`）→ `CreateVideo.audio`。
> **直接把 `VAEDecodeAudio` 接到 `CreateVideo` 会音画不同步** —— 画面裁了头部重叠帧、音频没裁，
> 每缝差 ~0.9 s 且**逐段累积**（第 3 段起口型明显对不上）。节点只能发现"输入没接"，
> **发现不了"输出被悬空"**，这条得自己盯住。
> 只要某节点**输出 `CONDITIONING` + `LATENT`**，接法就一样 —— 把它替掉图里的出词节点即可。
> 产线现役的「一采 → 🔍 放大 → 二采 → 续接」8 节点全接法见 [`docs/03`](docs/03-sampling-and-design.md) §4.1。

### 多段拼接成一条

每段各出一个 mp4。Chain 节点上**开 `auto_concat`** 或点 **🧩 拼成一条**（画面流拷贝无损、
音频逐段去 priming 对齐、拼完自检四项）；不开画布也能拼：
`python tools/concat_segments.py s1.mp4 s2.mp4 -o film.mp4`（退出码 0 = 过四项断言）。
⚠️ 别用 `ffmpeg -f concat -c copy` 或 `acrossfade`（实测会虚增时长 / 每缝偷 0.25 s 且累积）。
细节与音轨档位（AAC 256k 默认 / PCM 无损母版）见 [`docs/10`](docs/10-audio-seam-and-concat.md) §7.1。

---

## 目录结构

```
ComfyUI-H3-Latent-Relay/
├── __init__.py            # 包入口：注册 /h3relay/concat 路由、选 V1/V3 出口、启动横幅
├── relay_core.py          # 业务内核（唯一真相源）：时序网格 / 拷贝桥 / 裁重叠 / 音频缝 / 拼接
├── nodes.py               # 节点层（8 个节点的输入输出与人话报告），直接 import relay_core
├── layout_contract.py     # 布局契约：找不到上游时放行 + 留痕，只在真的不一致时 raise
├── v3/                    # V3 外壳（io.ComfyNode + comfy_entrypoint）；V1 走 NODE_CLASS_MAPPINGS
├── exp/history_anchor_v2/ # E1' 时不变历史锚（顶层 import ⇒ 必需；无 _tiha.json 即不生效）
├── web/                   # 前端 JS：🧩 拼接按钮、Chain 词分发（画布用；脚本用户见 docs/10 §7.4）
├── examples/              # 两个可直接打开的工作流：最小续接（19 节点）与全流程（45 节点）
├── docs/                  # 深度文档 01–10（原理 / 参数 / 采样链 / 画布 / 排障 / 脚本 / Chain / 测试 / 观测 / 音频）
├── tests/                 # 离线自测（零 GPU）：413 项断言 + V3 逐字段 + 词分发纯函数
├── tools/                 # 自检 / 取证 / 拼接 CLI / 打包器（含英文文档同步闸 en_sync.py）
├── licenses/              # 随包分发的第三方许可全文
├── dist/                  # 打包器的产出（最小分发集 + zip，不入库）
├── pyproject.toml         # 元数据（宿主真读 requires-comfyui/依赖；Comfy Registry 读 name/Icon/PublisherId）
├── .comfyignore           # `comfy node publish` 打什么包（不写 ⇒ 把 docs/tests/tools 一起推给用户）
├── icon.png / icon.svg    # Registry / Manager 卡片图标（400×400；`.svg` 是源，`.png` 是发布物）
├── requirements.txt       # 供 ComfyUI-Manager 安装依赖
└── （顶层还有 README_EN.md · CHANGES.md · CONTRIBUTING.md · SECURITY.md ·
      CODE_OF_CONDUCT.md · THIRD-PARTY-NOTICES.md · LICENSE · .github/）
```

安装后**运行期真正会被加载的**只有 `__init__.py` · `relay_core.py` · `nodes.py` · `layout_contract.py` ·
`v3/`（4 文件）· `exp/history_anchor_v2/`（3 文件，被 `nodes.py` 顶层 import，所以必需）· `web/`（2 个 JS）
—— `dist/` 里的最小分发集就是这些 + 示例 + 元数据；`docs/` `tests/` `tools/` 都是开发件。

---

## 配置与注意事项

| # | 硬纪律 |
|---|---|
| 1 | **`run_id` 三处一字不差**（读上段 latent / 桥 / 落盘），段号顺序跑、**别跳段** |
| 2 | **第 1 段什么都不用做**：桥会自动直通；`context_latent` 是必填，**别拔线、别旁路** |
| 3 | 🔴 **音频必须走裁重叠（或音频缝）的输出**，不能把 `VAEDecodeAudio` 直连落盘节点（否则每缝差 ~0.9 s 且累积） |
| 4 | **`context_frames` 只认 `5+17k`**（5/22/39/56/73/90/107/124），须小于本段帧数；越界**直接报错不吸附** |
| 5 | **`mask_mode` 生产档 = `hard`**（钉住区零重绘）；`taper` **不钉住**，仅对照实验用 |
| 6 | **`settle_frames` 保持 `0`**：实测「裁沉降」才是缝处跳帧的源头；要治糊用 Post 的 `settle_sharpen`/`settle_auto`（画质域修复，不碰时间轴 ⇒ 原理上不会引入跳帧） |
| 7 | **`patch_seconds ≤ 1.2`**（音频缝）：盖住段首 10 ms 解码静默已足够；台词贴头的素材用 `0` 关掉。**台词安全的主防线是缩短 patch，守卫只是兜底**（守卫对"整段人声铺底"的素材原理性无效） |
| 8 | **`🔍 放大在一条链里只做一次**：本包放大节点与第三方渐进采样器（SelfLift 等）内置的那次**是同一件事**，都开 = 同一段 latent 被放大两次 |
| 9 | **`chunks=1` 才是与上游整段推理一致的唯一路径**：`chunks>1` 会**改画面**（3D 体积注意力被切断），只在 OOM 时升，升完**必须重看缝** |
| 10 | 🔴 **续接契约取原生域**：`Latent 存` 接在放大**之前**；**二采的 guider 不接桥的 `conditioning`**（网格不同 ⇒ 当场炸） |
| 11 | `diagnostics` 默认关（三路纯打印、**不参与裁量**）；想看 DTW / 裁量→跳跃曲线 / 外观漂移就打开它。⚠️ 网上旧文里的「沉降 1」是 0.5.0 前口径 —— **以本页与节点报告为准** |
| 12 | 🔴 **每个功能的画布路与脚本路必须是同一套节点实现**（见文首铁律）；纯前端能力（🧩 按钮、Chain 面板格）脚本提交 JSON 时**会被忽略**，脚本用户走 [`docs/10`](docs/10-audio-seam-and-concat.md) §7.4 的三条非 UI 路径 |

---

## 许可与出处

- **本包 = MIT**（[`LICENSE`](LICENSE)）：可商用、可修改、可再发布，保留版权声明即可。
- 第三方出处与署名见 [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)（含"运行时宿主 ComfyUI 是 GPL-3.0"的
  事实与立场）。
- ⚠️ **关于 `H3RelayMotionContext`（GPL-3.0）**：其锚位合成段在 **v0.2.1–v0.5.0 的公开历史**里曾与该包构成
  **表达层重合**，已于 2026-09-19 **整体重写**，当前版本不含其派生表达；历史提交**未改写**、可检出复核
  （NOTICES §一·B）⇒ **严格合规请使用 ≥ 0.6.0**。
- **模型权重不含在本包内**：MiniMax-H3 等权重需自备，许可与商用条件由提供方决定。
- 使用合规：本包是通用视频生成工具，使用者须遵守当地法律与所用模型/素材许可，不得用于伪造肖像、
  传播虚假信息或侵犯他人权利。MIT 不含任何用途担保。
- **本包非 MiniMax 官方作品**，与 MiniMax、ComfyUI 官方均无隶属或背书关系。
- 再发布时请一并保留 [`LICENSE`](LICENSE) · [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md) · `licenses/`。

---

## 文档索引

中文文档是唯一真相源；`README_EN.md` 与 `docs/01·02` 的英文版是**派生物**，由
[`tools/en_sync.py`](tools/en_sync.py) 按节机检同步（改了中文必须同步英文，否则门槛红）。

| 文档 | 内容 |
|---|---|
| [`docs/01-mechanism.md`](docs/01-mechanism.md) · 🇬🇧 [`_EN`](docs/01-mechanism_EN.md) | 两条续接路线的历史与取舍 · 协议出处 · 放大之后哪条线留在原生域 · 时序网格 · 为什么必须裁头 · 运行时契约 |
| [`docs/02-parameters.md`](docs/02-parameters.md) · 🇬🇧 [`_EN`](docs/02-parameters_EN.md) | **参数全量手册**（桥 / 裁重叠 / Post / 音频缝 / 🔍 放大，含每个 advanced 项与默认值） |
| [`docs/03-sampling-and-design.md`](docs/03-sampling-and-design.md) | 采样链取舍（该量的不让用户配）· **§4.1 全流程档：8 节点全在场** |
| [`docs/04-canvas-and-widgets.md`](docs/04-canvas-and-widgets.md) | 画布外观 · `advanced` 折叠 · 旧图看不到 `prev_tail` 的处理 |
| [`docs/05-troubleshooting.md`](docs/05-troubleshooting.md) | **完整排障表** · **常见疑问 FAQ** · 工作流文件自检 · API 提交 |
| [`docs/06-continuity-scripting.md`](docs/06-continuity-scripting.md) | 出词纪律：段首缓冲 · 台词安全时刻 · 末帧锚链 · 音频缝配套 |
| [`docs/07-chain.md`](docs/07-chain.md) | Chain 自动连跑（换词 / 拼片 / 断点续跑） |
| [`docs/08-testing.md`](docs/08-testing.md) | 离线自测：28 组断言明细（413 项）· 工具清单 |
| [`docs/09-metrics.md`](docs/09-metrics.md) | 观测量参考区间（DTW 残留 / 外观漂移）· 怎么自校准 |
| [`docs/10-audio-seam-and-concat.md`](docs/10-audio-seam-and-concat.md) | **音频缝与多段拼接**：缝的口径与调参指南 · **台词保护** · 判据能力边界 · 音轨档位 · 边车 · **不开画布的脚本用法** |
| [`CHANGES.md`](CHANGES.md) | 版本史与每次实测证据 |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | 开发环境 · 自测纪律 · **英文文档同步纪律** · 许可条款 |
| [`SECURITY.md`](SECURITY.md) | 密钥 / 依赖 / 网络行为声明 |
| [`tools/README.md`](tools/README.md) | 十一个脚本的用途与期望值（九个自检/取证 + 拼接 CLI + 打包器） |
| [`examples/README.md`](examples/README.md) | 两份可直接打开的工作流（最小续接 19 节点 / 全流程 45 节点）· 生成器 |
| [`README_EN.md`](README_EN.md) | **英文精简版**（安装 / 接线 / 节点 / 参数 / 排障 / FAQ）—— 深度推导一律外链本文件与 `docs/` |
