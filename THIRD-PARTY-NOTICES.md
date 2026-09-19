# 第三方出处与许可声明（THIRD-PARTY NOTICES）

本包（ComfyUI-H3-Relay-Kit）以 **MIT** 发布（见 [`LICENSE`](LICENSE)）。
下列第三方作品在**设计阶段被阅读、参考或对照**，按「机制 / 契约 / 论文」三类分别说明。
**本仓库不包含任何 GPL / AGPL / LGPL 代码**（见 §三）。

---

## 一、机制与契约参考（无代码逐字复制）

| 第三方 | 许可 | 被参考的内容 | 我们的实现 | 性质 |
|---|---|---|---|---|
| **AIMixer / `ComfyUI_MiniMaxH3_Director`** | Apache-2.0（*待一手复核，见 §四*） | ① 拷贝桥：上一段 AV latent 硬拷贝 + 噪声掩码 + 音频尾拷贝 + NestedTensor 双流打包；② 段头低频残差对齐（`_lowfreq_appearance_pull` / `match_export_opening_grade`）；③ 批量化盒式模糊（把 guide 的模糊提到循环外、逐帧模糊合成一次 `avg_pool2d`） | `relay_core.build_continue_latent` / `lowfreq_pull` / `_box_blur_hwc`；节点 `H3RelayCopyBridge`、`H3RelayPost.lowfreq_pull` | **读源码后按机制重写**：公式层面对齐（差分式低频残差传递本身是**唯一写法**），但控制流、参数面、权重曲线、边界与降级处理均为本仓库独立设计（本包另加：逐帧线性权重斜坡、画布不一致即不动刀、掩码按上游原生契约走） |
| **`comfyui-minimax-h3-audio-T8`** | **MIT** | 掩码语义：走 ComfyUI **原生 H3 契约**（`MiniMaxH3.scale_latent_inpaint` / `mask_row_values`），与具体采样器无关；全 0 硬锁 = 钉住区零重绘 | `relay_core.prefix_*_weights` + `build_continue_latent` 的 `noise_mask` 构造 | 该契约来自**上游 ComfyUI 宿主源码**（Apache-2.0，运行时 import，不复制）；T8 作为用法佐证 |

> ⚠️ **「机制不受版权保护，表达受保护」**：上表两行均为**机制层**参考。
> 若后续有维护者发现任何段落与第三方源码构成**表达层**重合，请按 §四 流程处理
> （本仓库主张 MIT、且**不得**混入 copyleft 代码）。
> 👉 **§一·B 就是这样一次发现，以及它的处理结果——请一并阅读。**

### B. ⚠️ 已消除的历史重合：`ComfyUI-H3-Motion-Context`（GPL-3.0）

**这一条不是"参考"，是"曾经重合、现已重写"。单独成节，是为了让它可被独立检索到。**

| 项 | 内容 |
|---|---|
| 对方 | `ComfyUI-H3-Motion-Context` ｜ **GPL-3.0** ｜ `Copyright (C) 2026 NikoDemon80` ｜ `github.com/NikoDemon80/ComfyUI-H3-Motion-Context` |
| 重合位置 | **三簇**：<br>① `relay_core.apply_relay` 的**锚位合成段**（合并既有 keyframes + 丢弃落在钉住区内的锚）<br>② 网格辅助 `relay_core.step_offsets` / `steps_for_frames`<br>③ `relay_core.audio_tail_from_latent` 的音频尾段切法（外溢量与尾部切片） |
| 重合形态 | **表达层** —— 变量名（`kept`/`dropped`/`prior`/`head_end`、`acc`/`covered`、`overhang`）、控制流顺序、注释的论证结构一致。<br>其**机制**（钉住区去重、跨度前缀和、按外溢量夹取尾段）在逻辑上近乎必然、不受版权保护；受保护的是**表达** |
| 存在于 | **v0.2.1（首次开源 `59ccd36`，2026-09-11）～ v0.5.0** 的公开提交历史 |
| 处置 | **2026-09-19 全部重写**（三簇）。行为**逐位等价**，由 [`tools/verify_rewrite_equivalence.py`](tools/verify_rewrite_equivalence.py) 做差分验证：旧实现从 `git show <rev>` 捞取（不手工转录），137 个用例逐位比对 + **变异体自证**（故意做坏的实现必须被抓到） |
| 历史 | **未改写 git 历史**。重写只影响当前版本；`59ccd36` 起的公开历史里这三簇仍在，任何人可检出复核 |

**⚠️ 核验方法（留给后续维护者，别重复踩）**

第一轮核查用的是「**两边函数/类名交集为 0 ⇒ 同源只有一段**」——**这个判据是无效的**。
函数名可以完全不同而**函数体逐字节相同**（本包 `steps_for_frames` 与对方 `_steps_for_frames` 即如此），
所以第一轮漏掉了第二、三簇。正确做法是**逐函数比对函数体**，并且**先做归因测试**：
这些重合行是不是来自上游 ComfyUI 核心？

- 经归因**排除**（两边都源自官方，不算同源）：`pixel_frames`（官方
  `comfy_extras/nodes_minimax_h3.py` 里就是同一行 `sum(FRAME_PER_TOKEN[k % 5] for k in range(...))`）、
  `FRAME_PER_TOKEN[k % 5]`、`acc +=`、`total_t`、`resolved_frame_index`、
  `cond_t = cursor + FRAME_RESCALE * kf[...]`。
- 经归因**保留为候选**：`covered`、`overhang`、`out.append(acc)`、`steps_for_frames` —— 官方核心**均无**。
- 扫描工具：[`tools/scan_expression_overlap.py`](tools/scan_expression_overlap.py)
  （`python tools/scan_expression_overlap.py <对方仓库路径>`）。
  ⚠️ 它**只报数不定性**，且过滤器**不要按 `return`/`if` 前缀排除**——那会把短函数整段滤掉，
  造成"看起来干净"的假象。

**为什么不 force-push 抹掉历史**：本仓已公开且已有第三方 fork / PR，重写历史抹不掉已存在的副本，
反而会让「事实是否被隐瞒」本身成为问题。**如实记录 + 当前版本已消除**，是这里选择的处理方式。

**未决事项（本仓库无法单方判定，不替下游下结论）**：

- **派生方向未定**：对方公开史早于本包（其 0.2.0 可追至 2026-08-09），但本包可能经
  **`ComfyUI-Apt_Preset`** 中转 —— `relay_core.KEY_EXPORT_FRAMES = "apt_h3_export_context_frames"`
  是 Apt 的键。Apt_Preset 整包已于 2026-09-13 从本机移除，**其 LICENSE 已无从复核**。
- 因此**不能排除**上述三簇最终可追溯至 GPL 系谱。本包已按最保守口径处理（重写 + 披露），
  但**尚未取得对方作者的书面确认**。若你是下游使用者且需要确定性，请自行评估或联系对方作者。
- 另有两处**行为/设计层面**的对标（非代码搬运），一并列出以示完整：
  `H3RelayChain` + `web/relay_kit_chain.js`（行为对标其 Chain，代码独立实现，见 `CHANGES.md`）；
  `layout_contract.py`（"读上游源码 + 不一致即拒绝运行"的**思路**相同，检查对象与实现不同）。

## 二、论文与公开算法（仅算法出处，无代码）

| 出处 | 用在哪 |
|---|---|
| Reinhard et al., *Color transfer between images*, IEEE CG&A 21(5):34–41, 2001 | `match_prev_stats` 的逐通道一阶+二阶矩匹配 |
| *WCT: Universal Style Transfer via Feature Transforms*, NeurIPS 2017（arXiv:1705.08086） | 同上（矩匹配族） |
| *TTC: Pathwise Test-Time Correction*（arXiv:2602.05871） | 同上（跨段统计纠偏的思路） |
| *SEINE*（arXiv:2310.20700）、*SynCoS*（arXiv:2503.08605）、*GLC-Diffusion*（arXiv:2501.05484）、FlowLong / Unified Long Video Inpainting（arXiv:2511.03272） | `blend` 掩码的窗形权重（重叠区由两个独立估计加权平均 ⇒ 权重取窗函数） |
| *Diffusion Forcing*（arXiv:2407.01392）、*SDEdit*（arXiv:2108.01073） | `ramp` 掩码：把连续掩码值读作「逐 token 噪声等级 / 按强度重绘」 |
| RePaint（arXiv:2201.09865） | 每步 `out = out·m + anchor·(1−m)` 的逐步回锚 |

## 三、copyleft 作品清单（含一次已消除的历史重合）

| 作品 | 许可 | 状态 |
|---|---|---|
| `h3_drift.py`（改编自 **Contex-Loop**） | GPL-3.0（二次改编 ⇒ 传染性更强） | **❌ 未进入本包**。本包不含其任何代码、注释结构或派生表达；相关思路**未被采用** |
| **`ComfyUI-H3-Motion-Context`** | **GPL-3.0**（NikoDemon80） | ⚠️ **曾重合，已重写** —— `apply_relay` 锚位合成段在 v0.2.1–v0.5.0 公开历史中构成表达层重合，2026-09-19 整体重写。**详见 §一·B**（含范围核验、未决事项、历史未改写的原因） |

## 四、复核状态与待办（发布前必读）

| 项 | 状态 |
|---|---|
| 本包自身许可 | ✅ MIT（`LICENSE`） |
| 依赖许可 | ✅ `torch`（BSD-3-Clause）/ `safetensors`（Apache-2.0）—— 均为宽松许可，与 MIT 兼容 |
| T8 许可 | ✅ 已一手复核 = MIT（+ 内容层 CC BY 4.0，本包未使用其内容库） |
| **Motion-Context 重合** | ✅ **已核验范围并重写**（§一·B）：重合仅 `apply_relay` 一段，2026-09-19 整体重写，差分验证通过。<br>⚠️ **未决**：派生方向未定（可能经已消失的 Apt_Preset 中转）；**尚未取得对方作者书面确认**。<br>**待办**：联系 NikoDemon80 说明情况。**在此完成前，请勿对外主张"本包与 GPL 无关"** |
| **Director 许可** | ⚠️ **尚未一手复核**：本包注释里记为 Apache-2.0，但**发布机上没有该包的安装可供核对**。<br>已按**保守口径**随包附上 [`licenses/Apache-2.0.txt`](licenses/Apache-2.0.txt) 与上表署名。<br>**发布前动作**：到其仓库确认 `LICENSE`。若确为 Apache-2.0 ⇒ 现状即可；若为宽松但不同 ⇒ 改上表许可字段；**若无可用的开源许可**（默认「保留所有权利」）⇒ **必须**把 §一 对应实现改为不依赖该参考的独立方案（低频残差那一行公式属机制，可保留；批量化模糊属常规范式）。 |

---

## 五、若你要基于本包二次开发

- 保留 `LICENSE` 与本文件；
- 你新增的代码自行决定许可，但**不得**因引入本包而把 GPL 代码带进来；
- 若你从本包反向借鉴了本仓库的设计，欢迎在 README 里注明——这不作强制要求。
