# CHANGES

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

### 测试

`tests/test_relay_core.py` 新增第 12 组，覆盖：`settle` 只动 crop 不动 pin/锚位/音频窗、
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
