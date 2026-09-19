# examples — 最小可用演示

## `minimal_relay_official.json`

**全官方节点 + 本包**的最小续接工作流（17 个功能节点 + 1 个注释框），只回答一个问题：
**「第 2 段要接对哪些线？」**

```
4 个官方加载器 → 官方出词节点(MiniMaxH3ImageToVideo) → 🔗 续接 Latent 桥
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

> **后处理 Post（`#17`）可整节点删掉**：17 个旋钮全部默认 0 = **逐位直通**，
> 不接它行为与 0.4.x 一致。也**可以不接 `guide`**——只有 `match_prev` 与 `lowfreq_pull`
> 两项需要 guide（不接时自动跳过，report 里会写「跳过（未接 guide）」）。
> 最省的一次试法：把 `match_prev` 调到 `0.5`（段头色档/曝光对齐上段末帧，治缝上的亮度阶跃）。

> 本示例走 **MotionContext 桥**（conditioning 路线）。0.4.0 起可选**拷贝桥**
> `H3RelayCopyBridge`（输出接 KSampler 的 `latent_image`，钉住区不重绘），
> 机制与取舍见主 README「接缝处的对话规避与音频处理」与 `CHANGES.md` 0.4.0。

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
| 本包 `nodes.py` 的 `INPUT_TYPES()`（「本地定义优先」） | **本包 8 个节点** | **`tuple`** |

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

