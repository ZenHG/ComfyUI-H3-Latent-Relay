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

> ⚠️ **「机制不受版权保护，表达受保护」**：上表两行均为**机制层**参考。本仓库未发现、也未引入第三方源码的逐字段落。
> 若后续有维护者发现任何段落与第三方源码构成**表达层**重合，请按 §四 流程处理（本仓库主张 MIT、且**不得**混入 copyleft 代码）。

## 二、论文与公开算法（仅算法出处，无代码）

| 出处 | 用在哪 |
|---|---|
| Reinhard et al., *Color transfer between images*, IEEE CG&A 21(5):34–41, 2001 | `match_prev_stats` 的逐通道一阶+二阶矩匹配 |
| *WCT: Universal Style Transfer via Feature Transforms*, NeurIPS 2017（arXiv:1705.08086） | 同上（矩匹配族） |
| *TTC: Pathwise Test-Time Correction*（arXiv:2602.05871） | 同上（跨段统计纠偏的思路） |
| *SEINE*（arXiv:2310.20700）、*SynCoS*（arXiv:2503.08605）、*GLC-Diffusion*（arXiv:2501.05484）、FlowLong / Unified Long Video Inpainting（arXiv:2511.03272） | `blend` 掩码的窗形权重（重叠区由两个独立估计加权平均 ⇒ 权重取窗函数） |
| *Diffusion Forcing*（arXiv:2407.01392）、*SDEdit*（arXiv:2108.01073） | `ramp` 掩码：把连续掩码值读作「逐 token 噪声等级 / 按强度重绘」 |
| RePaint（arXiv:2201.09865） | 每步 `out = out·m + anchor·(1−m)` 的逐步回锚 |

## 三、明确**未**引入的 copyleft 作品

| 作品 | 许可 | 状态 |
|---|---|---|
| `h3_drift.py`（改编自 **Contex-Loop**） | GPL-3.0（二次改编 ⇒ 传染性更强） | **❌ 未进入本包**。本包不含其任何代码、注释结构或派生表达；相关思路**未被采用**。Relay-Kit 为 MIT，**不得**混入 GPL 代码 |

## 四、复核状态与待办（发布前必读）

| 项 | 状态 |
|---|---|
| 本包自身许可 | ✅ MIT（`LICENSE`） |
| 依赖许可 | ✅ `torch`（BSD-3-Clause）/ `safetensors`（Apache-2.0）—— 均为宽松许可，与 MIT 兼容 |
| T8 许可 | ✅ 已一手复核 = MIT（+ 内容层 CC BY 4.0，本包未使用其内容库） |
| **Director 许可** | ⚠️ **尚未一手复核**：本包注释里记为 Apache-2.0，但**发布机上没有该包的安装可供核对**。<br>已按**保守口径**随包附上 [`licenses/Apache-2.0.txt`](licenses/Apache-2.0.txt) 与上表署名。<br>**发布前动作**：到其仓库确认 `LICENSE`。若确为 Apache-2.0 ⇒ 现状即可；若为宽松但不同 ⇒ 改上表许可字段；**若无可用的开源许可**（默认「保留所有权利」）⇒ **必须**把 §一 对应实现改为不依赖该参考的独立方案（低频残差那一行公式属机制，可保留；批量化模糊属常规范式）。 |

---

## 五、若你要基于本包二次开发

- 保留 `LICENSE` 与本文件；
- 你新增的代码自行决定许可，但**不得**因引入本包而把 GPL 代码带进来；
- 若你从本包反向借鉴了本仓库的设计，欢迎在 README 里注明——这不作强制要求。
