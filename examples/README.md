# examples — 可直接打开的演示工作流

| 文件 | 回答的问题 | 规模 |
|---|---|---|
| `minimal_relay_official.json` | 第 2 段要接对**哪些线**（全官方节点 + 本包） | 18 节点 |
| `fullflow_second_pass_latent_upscale_ui.json` | **一采 → 🔍 潜空间放大 → 二采 → 续接** 整条链路怎么接 | 43 节点 |

## `minimal_relay_official.json`

**全官方节点 + 本包**的最小续接工作流（17 个功能节点 + 1 个注释框），只回答一个问题：
**「第 2 段要接对哪些线？」**

```
4 个官方加载器 → 官方出词节点(MiniMaxH3ImageToVideo) → 🔗 续接 拷贝桥（复合桥）
              → KSampler → 🔗 续接 Latent 存
              → VAEDecode / VAEDecodeAudio → 🔗 续接裁重叠 → 🔗 续接后处理 Post
                                                  ↘ 🔗 续接音频缝
              → CreateVideo → SaveVideo
```

图里有一个 `段号`（`PrimitiveInt`）同时喂给「桥」和「落盘」的 `stage_index`，
所以**跑下一段只需要改这一个数**；画布上还有一个注释框写着步骤，不用回来翻 README。

**怎么用**
1. 把 JSON 丢进 `ComfyUI/user/default/workflows/`，在 ComfyUI 里打开（或直接拖进画布）。
2. 把 4 个加载器的下拉改成你本机的模型文件（UNET / CLIP / 视频 VAE / 音频 VAE）。
3. 段号 = 0 → 填 prompt → Queue（第 1 段）。
4. 段号 = 1 → 换 prompt → Queue（第 2 段）。
5. 日志出现 `钉住 22 帧` + `裁首 22 帧 = 钉住 22 + 沉降 0` + `起点干净` = 接通了。

> **后处理 Post（`#14`）可整节点删掉**：17 个旋钮全部默认 0 = **逐位直通**，
> 不接它行为与 0.4.x 一致。也**可以不接 `guide`**——只有 `match_prev` 与 `lowfreq_pull`
> 两项需要 guide（不接时自动跳过，report 里会写「跳过（未接 guide）」）。
> 最省的一次试法：把 `match_prev` 调到 `0.5`（段头色档/曝光对齐上段末帧，治缝上的亮度阶跃）。

> 本示例走 **复合桥** `H3RelayCopyBridge`（0.6.0 起**唯一桥**）：第 0 路 `latent` 接 KSampler 的
> `latent_image`、第 4 路 `conditioning` 接 `positive`，两条并联生效（钉住区不重绘 + 取景钉帧）。
> 机制与取舍见主 README「接缝处的对话规避与音频处理」与 `CHANGES.md` 0.4.0 / 0.6.0。

## `fullflow_second_pass_latent_upscale_ui.json`

**产线现役的全流程图**（0.3MP 一采 → 🔍 潜空间放大 0.4MP → 2 步二采 → 拷贝桥续接下一段），
只回答一个问题：**「加了画质域之后，续接的哪些线要留在原生域？」**

```
加载器(UNET/CLIP/视频VAE/音频VAE/LoRA) + 模型补丁链(attention 后端/BSA/chunkFFN/sigma shift)
  → 出词 MiniMaxH3ReferenceToVideo(0.3MP · 6s→158 帧 · 1 张参考图)
  → 🔗 续接 拷贝桥       第 2 段起：拷上段尾 22 帧 + 钉帧 conditioning
  → 一采 SamplerCustomAdvanced(5 步)
  → 🔗 续接 Latent 存     ★ 存的是**一采终态 = 原生域**，这才是续接契约
  → 🔍 潜空间分块放大     26×46 → 30×54 latent（chunks=1，音频流原样带回）
  → CreateFadeMaskAdvanced + SetLatentNoiseMask   钉住前 7 个 latent 帧(=22 像素帧)
  → 二采 SamplerCustomAdvanced(2 步 @ denoise 0.20)
  → VAEDecode(画面) / VAEDecodeAudio(音频 ★ 接**一采**输出：音频全程不走 SR 与二采)
  → 🔗 续接 音频缝 → 🔗 续接 裁重叠(音视频同裁 22 帧 + 落 PCM 边车，第 4 路 prev_tail 喂 Post 的 guide)
  → 🔗 续接 后处理 Post   画质域 20 个旋钮，默认全 0 = 逐位直通（整节点删掉等价）
  → CreateVideo(24fps) → SaveVideo               ★ 官方节点，单文件自带音轨
  🔗 续接 连跑 Chain      4 段 · 词分发(`---` 分块) · 🧩 拼成一条(包内流拷贝)
```

**本包 8 个节点全在场**（`LatentUpscale` 与 `Post` 都在真实位置上，不是摆设）：
桥 / 存 / 读 / 裁重叠 / 音频缝 / 后处理 Post / 连跑 Chain / 潜空间放大。

**第三方依赖只有两个**：本包 + **KJNodes**（`CreateFadeMaskAdvanced` 与 `MiniMaxChunkFeedForward`）。
其余全是官方 `comfy_extras`：`nodes_minimax_h3`（出词 + sigma shift）· `nodes_video`（CreateVideo/SaveVideo）
· `nodes_math`（`ComfyMathExpression`：帧数与掩码尺寸图内驱动）· `nodes_sparse_attention` ·
`nodes_model_advanced` · `ResolutionSelector` · `SetLatentNoiseMask`。缺哪个该处会红，换等价节点即可。

> **为什么落盘不用 VideoHelperSuite**：VHS 在带音频时写的是**两个文件** —— `X.mp4`（**只有画面**）
> + `X-audio.mp4`（画面+音轨）。拿 `X.mp4` 去拼接会得到**哑片**，而拼接的四项断言在无音轨时
> `av_delta_ok` 恒真 = **假绿**（2026-09-22 自己踩过）。官方 `CreateVideo + SaveVideo` 单文件带音轨、
> 零第三方依赖。**代价**：编码档位不可调（想控 crf/码率就换回 VHS，但段文件必须取 `-audio` 那份）。

**怎么用**：① 装齐本包 8 个节点 + KJNodes + 🔍 的放大权重（主 README §2.1）；② 把 `LoadImage` 换成自己的
参考图、把 Chain 的 `prompts` 格换成自己的 N 段词（**段数与 `segments` 一致**）；
③ 点 Chain 上的 **⏩ 连跑** 出 N 段，再点 **🧩 拼成一条**。手动跑就改段号逐段 Queue。

> 🔴 **本图最重要的一条接线纪律：二采的 `BasicGuider` 接的是出词节点的 `positive`，不是桥的第 4 路
> `conditioning`** —— 只有**一采**接桥。原因：桥的钉帧走 ComfyUI 原生 `minimax_keyframes` 协议，
> 而打包器假定 **keyframe 与本段目标同网格**（`comfy/ldm/minimax/model.py` 的
> `all_video_rows[~img_update] = cond_video_rows`）。一采之后画面已放大 ⇒ 目标网格变了，
> 接上当场炸：`shape mismatch: value tensor of shape [2392, 96] cannot be broadcast to indexing
> result of shape [3134, 96]`（2026-09-22 实测；3134 = 7×405 + 299，2392 = 7×299 + 299）。
> **锚（`minimax_refs`）允许与目标异分辨率，钉帧（`minimax_keyframes`）不允许**，两条通道不是一回事。
> latent 侧的钉住不受影响：拷贝前缀 + 噪声掩码仍在，二采照样不许重绘缝区。

> 出段分辨率 = 原生 × SR 系数（目检时确认）。**放大属交付支路，不进续接契约** ——
> 落盘节点在放大**之前**，所以第 2 段的桥读到的仍是原生网格。

**实测口径**（2026-09-22，单卡 12GB，**用示例图本身**跑 2 段，run_id `otui5`）：
段1（stage0，含权重加载）150s → 480×864 / 158 帧 / 6.583s；段2（走桥 + 裁 22 帧）240s → 136 帧 / 5.667s；
两段各自动落一份 PCM 边车。成片 **294 帧 / 12.25s 单文件带音轨**，四项断言全绿
（帧数守恒 / PTS 无洞 / DTS 递增 / **A·VΔ = 0.0001s**），`pcm_segments = 2` ⇒ **音频代际 2 → 1**。
缝处：亮度阶跃 **0.0013**、缝前/缝后中位运动 0.707→0.478、warp 7.22→6.50、方向余弦 **+0.294**（不倒退）、
缝后单帧尖峰 **1.3×**（阈值 3× ⇒ 无跳帧签名）。
唯一告警 = 边车比视频短 267/266 样本（≈8ms，AAC 帧量化）⇒ 拼接按尾补静音，听不出。
> 拼接的段文件**必须带音轨**：官方 `SaveVideo` 单文件天然满足；若换回 VHS，取 `X-audio.mp4` 那份，
> 否则边车会被 `relay_core` 的 100ms 同源护栏整段拒收（`pcm_segments = 0`，且无音轨时断言照样"绿"）。

**与产线原图的四处归一**（行为不变，写在这里免得对不上）：
① `run_id` 由片名改成 `demo`；② 四段词从 `easy positive` 节点的连线改成**直填 Chain 的 `prompts` 格**；
③ 落盘从 `VHS_VideoCombine` 换成官方 `CreateVideo + SaveVideo`（理由见上）；
④ 补上 `🔗 续接 后处理 Post`（默认直通 ⇒ 与不接逐位等价，但示例图该让 8 个节点都在场）。

## `make_minimal_workflow.py`

生成上面那份 JSON 的脚本。

**为什么不直接手写一份 JSON 交上去？**

UI 格式工作流的 `widgets_values` 是**按位置**对应前端 widget 槽位的，而前端还会
**自动注入**一些不在 `INPUT_TYPES` 里的格子（典型：带 `control_after_generate: True`
的 `seed` 后面会多一格下拉）。手写只要漏掉中间任意一格，其后所有取值**整体前移一位** ——
文件照样能打开、能提交、**不报任何错**，但参数全是错的（实测记录见 `CHANGES.md` 0.2.1）。

所以这里从**正在运行的 ComfyUI** 的 `/object_info` 读真实 schema 来拼，槽位顺序由 schema
推导；另外输入数组的排列也按 ComfyUI 保存时的真实约定（可连线输入在前、widget 在后），
links 的目标下标才不会错位。

```bash
# 1) 另开一个终端启动 ComfyUI
python main.py

# 2) 生成（默认写到本目录）
python examples/make_minimal_workflow.py
python examples/make_minimal_workflow.py --api http://127.0.0.1:8188 \
    --length 73 --width 448 --height 768 --out examples/minimal_relay_official.json

# 3) 复核：必须「问题合计 0 条」（Note 是前端虚拟节点，会有 1 条提示，正常）
python tools/check_ui_workflow.py examples/minimal_relay_official.json
```

脚本自己也会做一遍结构自检（节点/连线 id 唯一、连线两端存在、槽位下标不越界、
类型相容、每个节点的 `widgets_values` 项数与 schema 推导出的槽位数一致），
不通过就不写文件。

**上游升级后重跑一次即可**（比如 ComfyUI 改了官方节点端口）。

### ⚠️ 改这个生成器前必读：两个 schema 来源的容器类型不一样

本脚本有**两个** schema 来源，同一个「输入定义」在两边是**不同的 Python 容器**：

| 来源 | 用在 | 容器 |
|---|---|---|
| 服务端 `/object_info`（JSON） | 官方 / 第三方节点 | `list` |
| 本包 `nodes.py` 的 `INPUT_TYPES()`（「本地定义优先」） | **本包 8 个节点**（0.6.0 起） | **`tuple`** |

所以任何形如 `isinstance(spec, list)` / `isinstance(spec[0], list)` 的写法，
**对本包节点一律为假**。2026-09-19 的事故就是这么来的：`_ty()` 只认 `list` ⇒
本包节点全部退化成 `type="*"` 且丢掉 `{"widget": {"name": …}}` 标记 ⇒
生成的示例图里，本包节点会**长出一排空的输入圆点**（官方节点正常），
**文件能开、能跑、不报任何错**。修法与前端判据见 `CHANGES.md` 0.5.0「示例模板修复」。

改动后请跑这三步，缺一不可：

```bash
python examples/make_minimal_workflow.py                      # 生成（自带结构自检）
python tools/check_ui_workflow.py examples/minimal_relay_official.json   # 期望「问题合计 0 条」
python tools/review_050.py                                    # 期望 I3 通过
```

> **自检能不能失败？** 加判据时把 `_ty()` 临时换回 `isinstance(spec, list)` 的旧写法，
> 确认自检会**报错**。第一版回归钉子就是因为「期望集与实收集共用同一个 `_ty()`」而恒真，
> 旧实现下依旧报 0 问题 —— 判据不能与被判对象共用同一个函数。

> 🤖 要走 **API 提交**（脚本/程序化）？见主 README「🤖 API 提交」小节；示例图同样可从前端
> 「导出（API）」得到 API 格式。Post 的 `settle_auto`（自适应糊区补偿）与 AudioSeam 的
> `joined`（整片拼接）是 0.5.0 后期新增能力，详见主 README 参数节。
