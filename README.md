# ComfyUI-H3-Relay-Kit

MiniMax-H3 多段续接的 **latent 桥**（零重编码）—— 一个可独立使用、零第三方依赖的 ComfyUI 节点包。

## 它解决什么问题

H3 分段生成时，"续接"要回答一件事：**新的一段怎么知道上一段结束在什么状态？**

主流做法是**像素续接**：把上一段的 mp4 解码成帧 → VAE 重编码成 latent → 当锚塞进条件。
问题在于：

- 多一次 VAE 往返，**有量化损失**；
- 重编码的 latent 与采样时那份**不是同一个东西**，锚点会漂移；
- 只能锚一个整块，位置固定在第 0 帧。

本包做的是**latent 桥**：直接从上一段的 AV latent 里切出尾段，切成逐 token 的块，
按真实位置写进 `minimax_keyframes`。**不重编码**，锚点与采样同源。

## 🚀 5 分钟跑通（最小可用）

> 懒人路线：直接打开 `examples/minimal_relay_official.json`（全官方节点 + 本包，16 个节点），
> 把 4 个加载器的下拉改成你本机的模型文件，然后按下面三步跑。
> 图的画布上有一个**注释框**写着同样的步骤，不用回来翻 README。

**第 1 段**

1. 把「① 段号」那个数字填成 `0`。
2. 在官方条件节点（`MiniMaxH3ImageToVideo` / `MiniMaxH3ReferenceToVideo`）里填好 prompt。
3. 点 Queue。

判据（日志里应看到）：

```
[H3 Relay] 无 context_latent → 直通（独立段，不续接）。
[H3 Relay] 裁 0 帧 → 不裁（独立段或纯首段）。
```

**第 2 段**

4. 把「① 段号」改成 `1` —— **只改这一个数**（桥和落盘都由它驱动，不用改第二处）。
5. prompt 换成第 2 段要拍的内容（开头直接接住上一段的动作/机位，别重新起手）。
6. 再点 Queue。

判据（三行，缺一不可）：

```
[H3 Relay] latent 桥续接：钉住 22 帧 / 7 步，锚位 0..18，裁首 23 帧（含沉降 1），音频 37 步
[H3 Relay] 裁首 23 帧 = 钉住 22 + 沉降 1 ｜ 自动检测：窗内最大帧差 203.0 / 段内基线 3.0
[H3 Relay] 接缝自检：前 40 帧无突变（最大帧差 6.00，段内基线 3.00）→ 起点干净。
```

| 看到 | 说明 |
|---|---|
| `钉住 22 帧` | 上一段尾部**已接进来** |
| `裁首 N 帧 = 钉住 22 + 沉降 Y` | 接缝处理**已生效**（Y 由本段画面量出，逐段不同） |
| `起点干净` | 没有跳变，可以拼 |

**关键参数两张表**

| 参数 | 第 1 段 | 第 2 段起 | 在哪填 |
|---|---|---|---|
| 段号 `stage_index` | `0` | `1`、`2`、`3`… | 桥 + 落盘（**必须一样大**；示例图用同一个「段号」节点驱动，所以只改一处） |
| `run_id` | 同一个片子名，比如 `myfilm` | **与第 1 段一字不差** | 桥 + 落盘（两边一致） |
| `trim_frames` | `22` | `22`（不用动） | 桥（钉住窗口长度，只能 5/22/39/56/73/90/107/124） |
| `settle_frames` | —（首段不裁） | 保持 `-1`（自动，不用管） | **裁重叠**节点 |

**三个最常见的翻车点**

| 症状 | 原因 | 处置 |
|---|---|---|
| 日志写「静默直通」并直接报错 | 段号填了 ≥1，但 `run_id` 与上一段不一致，或上一段文件不在 | 两处 `run_id` 必须一字不差 |
| 画面从第 1 帧就开始重播上一段 | 「裁重叠」节点没接上，或它的 `trim_frames` 没接桥的**第 3 路输出** | 按下面接线图检查 |
| 换了新片子，结果接的是旧片的尾巴 | 没换 `run_id`（同名会覆盖/复读同段号文件） | 换新名字 |

## 与像素续接的关系

两者写的是**同一个 conditioning 协议**，因此可以互换：

| | 像素续接 | 本包（latent 桥） |
|---|---|---|
| 数据来源 | 上一段 mp4 → 解码帧 | 上一段 AV latent |
| VAE 往返 | 有（一次 encode） | **无** |
| keyframe 块数 | 1（整段锚 @frame0） | **逐 token 多块，按位置排** |
| 音频 | 挂在同一个 keyframe 上 | 走 `minimax_refs`，与视频独立窗口 |
| 逐位可复现 | 否 | **是** |

> 走本包时，上游节点（如 H3 导演台）的**像素续接字段必须留空**，
> 否则两套锚会同时写进 `minimax_keyframes`，画面互相打架。

## 协议出处（ComfyUI 原生，无需 monkey patch）

```
comfy/ldm/minimax/model.py:376
    cond_t = cursor + FRAME_RESCALE * kf["resolved_frame_index"]
comfy/model_base.py:2186-2196
    keyframes = kwargs.get("minimax_keyframes") → payload["keyframes"]
    refs      = kwargs.get("minimax_refs")      → payload["refs"]
```

原生即支持任意位置的 keyframe 锚，本包不需要修改 ComfyUI。

## 时序网格（为什么只有特定帧数可用）

H3 VAE 的时序跨度为 `(1,4,4,4,4)`：每 5 个 latent token 覆盖 17 像素帧。
只有能**整步**切出的窗口才有意义：

```
合法窗口：5, 22, 39, 56, 73, 90, 107, 124 ...   （= 5 + 17k）
22 帧 =  7 个 latent token
39 帧 = 12 个 latent token
```

帧数不在网格上会渲染出**整体位移的接缝**，因此本包在越界时直接报错，
**不静默吸附到邻近值**。

另有一条硬约束：切出的尾段起点必须落在 5-token 周期边界，
否则各 token 的帧跨度与写入位置对不上 —— 同样会位移，同样报错。

## 节点

| 节点 | 作用 |
|---|---|
| 🔗 **H3 续接 Latent 存** | 本段采样后，把 AV latent 落盘到 `output/relay_kit/<run_id>/stage_NNNNN.safetensors` |
| 🔗 **H3 续接 Latent 读** | 读回上一段（段号 - 1），断点续跑时可指定 `explicit_path` 换源 |
| 🔗 **H3 续接 Latent 桥** | 上一段尾段钉进本段 conditioning；`context_latent` 不接则直通 |
| 🔗 **H3 续接裁重叠** | **裁掉本段头部的重生成帧 + 自动裁掉紧随其后的「复现帧」（视频音频同裁）** —— 不裁就会在拼接处重播/跳变 |
| 🔗 **H3 续接连跑 Chain** | 自动连跑控制器：同分组框内自动推进「桥 + 落盘」段号并排队（详见下方「Chain 自动连跑」） |

## 接线

### 官方节点路线（推荐，`examples/minimal_relay_official.json` 就是这个）

```
UNETLoader ────────────── MODEL ─────────────┐
CLIPLoader ──── CLIP ─┐                      │
VAELoader(视频) ─ VAE ─┤                      │
VAELoader(音频) ─ VAE ─┤                      │
MiniMaxH3ImageToVideo ─┤                      │
  (= 官方出词节点，输出   │                      │
   positive + LATENT)   │                      │
   │                    │                      │
   ├─ positive ─────────┴→ 🔗 续接 Latent 桥 [0] conditioning ─┐
   ├─ LATENT ─────────────→ 🔗 续接 Latent 桥 [1] latent ─────┤
   │                       （[2] context_latent 留空，自动读）  │
   │                                                            │
   ├─ positive → ConditioningZeroOut ──────────────→ KSampler negative
   └─ LATENT ──────────────────────────────────────→ KSampler latent_image
                                                     KSampler positive ← 桥 [0]
                                                     KSampler ──────→ LATENT
                                                                        │
                    ┌───────────────────────────────────────────────────┤
                    │                                                   │
        🔗 续接 Latent 存 [0] latent（另外 [2] stage_index 与桥一致）    │
                    │                                                   │
                    └────────────→ VAEDecode ── IMAGE ──┐               │
                    └────────────→ VAEDecodeAudio ── AUDIO ──┐          │
                                                             │          │
        🔗 续接裁重叠 [0] images ←────────────────────────────┘          │
                     [1] audio ←───────────────────────────────────────┘
                     [2] trim_frames ← 🔗 桥 [2]  ★必需，别漏
                     [4] settle_frames = -1（自动，不用动）
                     │
                     ├─ [0] images ─┐
                     └─ [1] audio ──┴→ CreateVideo → SaveVideo
```

**第 1 段**：桥 [2] 输出 `0` → 裁重叠原样通过；桥不读上下文（直通）。
**第 2 段起**：填好 `run_id` + `stage_index`，桥自己从
`output/relay_kit/<run_id>/stage_NNNNN.safetensors` 读上一段。

### 第三方出词 / 采样节点也一样

只要那个节点**输出 `CONDITIONING` + `LATENT`**，就是同样的接法 —— 把上面图里的
`MiniMaxH3ImageToVideo` 换成它即可。常见组合：

| 用到的节点 | 来自哪个包 |
|---|---|
| `CSGlideCastCS`（出词 + 规格） | `ComfyUI-Banzhang-All`（第三方，**不在本包依赖里**） |
| `SelfLiftH3Sampler`（AV 采样器） | `comfyui-SelfLift`（第三方，**不在本包依赖里**） |
| `MiniMaxH3ReferenceToVideo` / `MiniMaxH3ImageToVideo` / `MiniMaxH3AddGuide` | ComfyUI **内置**（`comfy_extras/nodes_minimax_h3.py`） |

> 本包**只依赖 ComfyUI 原生协议**（`minimax_keyframes` / `minimax_refs` /
> `resolved_frame_index`），不 import 也不要求任何第三方 H3 节点包。
> 上面两个第三方包只是"能用"的例子，不是"要用"的前提。

### 可选：显式接「续接 Latent 读」

`context_latent` 留空时桥会自己按 `run_id` + `stage_index - 1` 推导路径。
想在图上把来源画出来（或断点续跑换源），就接 `🔗 H3 续接 Latent 读`：

```
🔗 续接 Latent 读 ── context_latent ──→ 🔗 续接 Latent 桥 [2]
   run_id 同桥；stage_index = 本段段号 - 1（第 2 段填 1）
   可选 explicit_path：直接指定某个 safetensors
```

### 可选：Chain 自动连跑

`🔗 H3 续接连跑 Chain` 是**纯控制节点，不参与连线**（没有输入输出口）。
它靠**前端**去找同一张图里的桥和落盘，所以：

**必须把 Chain、桥、落盘三个节点拉进同一个分组框**（框选 → 右键 → 添加分组），
否则按钮点了没反应 —— 状态会写在 Chain 的 `status` 格子里（点之前先看那一格）。


## ⚠️ 为什么必须裁头

钉住区的前 `trim_frames` 帧并不是新内容 —— 它们是模型**对上一段尾部的重生成**
（实测逐帧 MAE ≈ 6/255，与"原帧"是同一画面的两次采样）。

实测证据：段 B 的首帧最相似的帧是段 A 的倒数第 22 帧（MAE 3.09），
而次优候选的 MAE 是 17.5 —— 孤立尖峰，说明匹配是真实的，不是巧合。

不裁的后果：**拼接处约 0.92 秒的画面重播**（22 帧 @24fps）。

裁的是 **decode 之后的像素帧，不是 latent** —— latent 的帧跨度由 token 在序列里的
相位（`k % 5`）决定，砍头会让相位错位。音频必须**同裁**，否则音画失步。

## 🔍 裁多少帧：不让你配，由节点当场量

**固定裁帧数不总对。** 实测（2026-09-11，低规格 448×768）：

| 段长 | 只裁钉住区（22 帧）后的首帧 | 说明 |
|---|---|---|
| **73 帧** | ❌ 有突变 | 切换点在**原第 22→23 帧之间**（MAE 6.52 → **102.27**），22 恰好把突变**前**那帧留在裁剪后第 0 帧 |
| **107 帧** | ✅ 干净 | 切换点更靠前，被完整裁掉 |

原因：模型在续接段开头会先复现上一段尾部（钉住区），**之后才切到按 prompt 生成**。
这个切换点落在哪一帧，取决于段长与 prompt —— 它是个**逐段不同**的量。

⇒ 所以它不该是让你填的配置，而是**当场量出来的量**：`H3RelayTrimAV` 收到的就是
完整解码画面，真实切换点此刻就在手上。默认 `settle_frames = -1`（自动）时，
节点会贴着钉住区开一个窄窗找出切换点，把那几帧「复现帧」一并裁掉。

```
[H3 Relay] 裁首 23 帧 = 钉住 22 + 沉降 1 ｜ 自动检测：窗内最大帧差 203.0 / 段内基线 3.0
           画面 73 → 50 帧（3.042s → 2.083s）；音频 97333 → 66666 采样点
[H3 Relay] 接缝自检：前 40 帧无突变（最大帧差 6.00，段内基线 3.00）→ 起点干净。
```

**默认什么都不用做。** 三种情况：

| 你想要 | 怎么设 `settle_frames` |
|---|---|
| 接缝最干净（默认） | 保持 `-1`（自动） |
| 各段**等长** | 全片填同一个正数，例如 `9`（代价：每段成片短 9 帧） |
| 回到 0.2.x 旧行为 | 填 `0` |

判定口径：窗口紧贴钉住区（`pin-1 .. pin+12`），基线取自**钉住区内部**的相邻帧差中位数，
`帧差 > 4 × max(基线, 1.0)` 即认定为切换点；检出位置超出上限（12 帧）一律不动刀。
纯 CPU、只算需要的那一小段（每段约 40 ms），零额外依赖。

> **段内自己的切镜不会被误判。** 老实现扫「前 40 帧全局最大」，本段第 25 帧的真实切镜
> 会被当成接缝、并建议把新内容整段裁掉；现在窄窗根本看不到它。裁后自检也只在
> 位置足够靠前时才给"再裁 N 帧"的建议，否则只报位置不动刀。

## 参数

**续接 Latent 桥**

| 参数 | 默认 | 说明 |
|---|---|---|
| `trim_frames` | 22 | 钉住的像素帧数。合法值 5/22/39/56/73/90/107/124。22 是设计中心 |
| `audio_frames` | 0 | 音频钉住窗口（像素帧口径）。0 = 与视频同窗 |
| `context_latent` | — | 上一段 AV latent。不接 = 直通 |

**续接裁重叠**

| 参数 | 默认 | 说明 |
|---|---|---|
| `images` | — | 本段解码后的全部画面（含头部重叠区） |
| `trim_frames` | 0 | 裁掉的**钉住区**帧数。**接桥的第 3 路输出即可自动同步**，不要手填 |
| `fps` | 24.0 | 帧率，用于把帧数换算成音频采样点 |
| `audio` | — | 本段音频。接了才与画面同裁，否则会音画不同步 |
| `settle_frames` | **-1** | `-1` = 自动量出切换点并一并裁掉（推荐，见上节）；`0` = 只裁钉住区（0.2.x 行为）；`N` = 固定多裁 N 帧 |

**硬约束**：`context_latent` 与本段必须**同分辨率、同通道数**。
latent 无法缩放，不一致只能让上一段重跑或从本段重启链 —— 会明确报错。

## 安装

### 方式一：git clone（推荐）

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/ZenHG/ComfyUI-H3-Relay-Kit.git
```

### 方式二：下载 ZIP

在仓库页面点 **Code → Download ZIP**，解压后把文件夹改名为
`ComfyUI-H3-Relay-Kit`，放进 `ComfyUI/custom_nodes/` 下。

装好后**重启 ComfyUI 后端**（装了 ComfyUI-Manager 就点 *Restart*；没装就重启 Python 进程）——
仅刷新浏览器不会加载新节点。节点列表里搜 `🔗 H3 续接` 即可看到本包的全部节点。

随包还带 **`examples/`**：一份可以直接打开的**最小演示工作流**
（`minimal_relay_official.json`，全官方节点 + 本包）和它的生成器脚本 ——
见 [`examples/README.md`](examples/README.md)。

**依赖**：只用 `torch` 与 `safetensors`（ComfyUI 自带；清单见 `requirements.txt`）。
二者都是**函数内延迟 import**，所以缺了也不会导致节点注册失败，只在真正用到时提示。

**不依赖任何第三方 H3 节点包**：所用的 `minimax_keyframes` / `minimax_refs` /
`resolved_frame_index` 全是 ComfyUI **原生**协议，零 monkey patch。

## 离线自测（零 GPU，秒级）

```bash
python tests/test_relay_core.py
```

脚本会自动上溯定位 ComfyUI 根目录；装在别处时用
`COMFYUI_PATH=/path/to/ComfyUI python tests/test_relay_core.py`。

**103 项断言，零 GPU、不加载模型**，覆盖十二个方面：

| 组 | 覆盖 |
|---|---|
| 1 | 时序网格自洽（22 帧=7 步 / 39 帧=12 步 / 192 帧=57 步） |
| 2 | 尾段切片逐位正确 + 起始必须落在 5 步周期边界 |
| 3 | 硬错误：分辨率不一致 / 非网格帧数 / 窗口 ≥ 段长 → 必须 raise |
| 4 | conditioning 注入：keyframes 合并、钉住区旧锚丢弃、音频 ref 追加 |
| 5 | AV latent 落盘往返逐位一致 |
| 6 | 裁头重叠：画面音频同裁逐位正确、时长对齐、trim=0 直通、越界 raise |
| 7 | 节点返回值契约：每个分支的返回路数 == `len(RETURN_TYPES)` |
| 8 | 接缝自检 `find_head_jump` / `describe_head_jump` |
| 9 | 段号声明与取源矛盾必须 raise（不得静默直通） |
| 10 | `streams_from_latent` 不得把普通张量按 batch 维误拆成伪音频流 |
| 11 | Chain 的 `status` 槽位与前端 JS 一致（旧工作流少一格不前移） |
| 12 | 沉降帧：pin 与 crop 解耦、自动检测四个场景、裁节点三条分支 |

## 排障

| 现象 | 原因 | 处置 |
|---|---|---|
| `不是 H3 的整步续接窗口` | 帧数不在 5+17k 上 | 改成合法值 |
| `context_latent 是 WxH，本段是 WxH` | 两段分辨率不同 | 统一分辨率重跑 |
| `起始于周期位置 N（非 0）` | 段长使尾段起点不对齐 | 调整段长，令 `(总步数 - 尾段步数) % 5 == 0` |
| `钉住 N 帧、本段只有 M 帧` | 窗口 ≥ 段长 | 缩短窗口或加长段 |
| `要裁 N 帧，但本段只有 M 帧` | `trim_frames` 误填成整段长度 | 接桥的第 3 路输出，别手填 |
| 日志出现 `⚠ 未接 audio` | 裁重叠节点没接音频 | 把 `VAEDecodeAudio` 的输出接到它的 `audio` |
| 接缝处约 0.9s 重播 | 没接裁重叠节点 | 按上面的接线图把 `🔗 续接裁重叠` 串在 VideoCombine 前 |
| 拼接处 1 帧硬跳 / 上一段的字幕文字串进本段开头 | 钉住区之后的「复现帧」没被裁掉（切换点逐段不同） | **0.3.0 起默认自动处理**，不用改任何参数；日志会写 `裁首 N 帧 = 钉住 X + 沉降 Y` |
| 各段成片长度差一两帧 | 自动模式逐段量出的沉降量不同 | 想等长就把 `settle_frames` 全片填同一个正数（如 9） |
| 日志写 `未检出显著切换点` | 本段切换点就在钉住区内，或画面太平/太糊 | 正常，按只裁钉住区处理；确实还跳就手填 `settle_frames` |
| 日志写 `但位置超出自动沉降上限（12 帧）…不动刀` | 这是本段自己的切镜，不是接缝 | 不用管；确实要裁就手填 `settle_frames` |
| 画面两套续接打架 | 上游像素续接没关 | 清空上游的 cont / 参考视频字段 |
| **`stage_index=N 表示本段是第 N+1 段，但没有可续接的上一段`** | 手改段号时只改了桥或只改了落盘；或换了片子没填 `run_id` | 两处 `stage_index` 必须一样大、`run_id` 两边一致；确实是独立段就把段号改回 0 |

> ⚠️ 最后一条是 **0.2.1 起新增的硬拦**。旧版遇到这种情况会**静默按独立段直通**——
> 界面一路绿灯，产出的却是没有续接的接缝，只有目检才发现。现在直接报错。

## 🔧 工作流文件被改坏的自检（随包工具）

工作流的 `widgets_values` 是**按位置**对应前端 widget 槽位的。用脚本生成 UI 工作流时，
若漏掉 ComfyUI 前端**自动注入**的那一格（典型：带 `control_after_generate: True` 的 `seed`
后面会多一个下拉），其后所有取值会整体前移一位 —— 文件能打开、能提交，
但采样参数全错且**没有任何报错**（实测：`SelfLiftH3Sampler` 的
`w_max` 收到文件名、`upscaler_model` 收到 `false`）。

随包带一个体检脚本，拿 live `/object_info` 的真实 schema 逐位校验取值：

```bash
python tools/check_ui_workflow.py --all
python tools/check_ui_workflow.py /path/to/ComfyUI/user/default/workflows/xxx.json
```

需要 ComfyUI 正在运行（用来取 schema）；ComfyUI 不在默认位置时用
`--comfyui /path/to/ComfyUI` 或环境变量 `COMFYUI_PATH`。

## 许可

MIT。协议与网格常量来自 MiniMax-H3 的公开实现，本包为独立实现。

## 🔗 Chain 自动连跑（0.2.0 新增）

在图里放一个 `🔗 H3 续接连跑 Chain` 节点，把 **Chain + 桥 + 落盘** 三个节点
拉进同一个分组框（框选 → 右键 → 添加分组），按钮就长在 Chain 节点上：

| 按钮 | 干什么 |
|---|---|
| ▶ Run | 按当前段号跑一次（不满意再点一次，覆盖同段号文件） |
| ✔ Approve | 段号 +1（桥/落盘同步改），排队跑下一段 |
| ⏩ 连跑 | 按 segments 自动循环：跑完一段 → 段号+1 → 再跑（0 = 无限） |
| ⏹ Stop | 当前采样跑完后停止推进 |
| ↺ Reset | 段号归 0，从第 1 段重来 |

跑每一段之间改 prompt / seed 即可；换新片子换 run_id。

## 🛡 运行时契约（0.2.0 新增）

桥 / 裁节点每次执行前，把本包的时序网格常量 `FRAME_PER_TOKEN` 与 live ComfyUI 的
`comfy_extras/nodes_minimax_h3.py` 源码对照：一致才放行（进程内缓存只查一次）；
上游更新改了网格则**拒绝运行**并报出两边各是什么——宁可不跑，不出坏片子。

## 🔊 音频窗对齐口径（0.2.0 起）

`audio_frames` 换算到 40Hz 音频栅格时**向上拓宽**到最近整步（原来是四舍五入）：
音频窗是给模型"已播声音"的上下文，多带半步安全，截短半步可能丢节拍点。
拓宽行为会写进 report 注记。
