# 离线自测：27 组断言明细与工具清单

> 本文件由 README（0.6.0 梳理）拆出。主 README 只留命令与结论。
> 📐 **槽位记号**：`[N]` = 0 起算。

## 单测（零 GPU、秒级）

```bash
python tests/test_relay_core.py
```

脚本会自动上溯定位 ComfyUI 根目录；装在别处时用
`COMFYUI_PATH=/path/to/ComfyUI python tests/test_relay_core.py`。也支持 `pytest tests/`
（找不到 ComfyUI 根目录时自动 skip，不会崩）。

**384 项断言，零 GPU、不加载模型**，覆盖二十七个方面：

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
| 13 | 模糊型沉降 + 锐度路三量解耦：塌陷宽于旧窗但**有恢复** → 应裁；无恢复仍 0 |
| 14 | 拷贝桥：位级拷贝 / 掩码结构 / 音频尾 / 跨分辨率 / 前缀占满 / 相位失配 / 节点契约 |
| 15 | 色档收敛信号：注噪 / taper 收敛尾巴的观测端检出 |
| 16 | 0.4.2 回归：导出音频分支 / 中文 note / 服务端校验 / 掩码设备 / 契约降级缓存 |
| 17 | 噪声斜坡 ramp：软证据接缝——连续掩码 = 逐 token sigma 标签 |
| 18 | 复现残留：第 4 种检测手段（非槽位；现有三种量都看不见复现帧，**默认关闭**） |
| 19 | 跨段统计匹配：Reinhard 式一阶+二阶矩，段头↔上段末帧；护栏与无重影 |
| 20 | 拆节点：`H3RelayPost` 独立 + TrimAV 追加 `[3]` 输出 `prev_tail` |
| 21 | 重叠区双向融合 blend：窗形权重（smoothstep / hann），两端导数为 0 |
| 22 | 音频缝：长度守恒、床声选窗两档、电平对齐 + 峰值护栏、边界 blend、床环铺无台阶、落盘往返、节点两道守卫、`joined` 拼接复合 |
| 23 | **音画同步守恒（0.6.2）**：`joined` 只在给了画面裁量时才能交叉（否则 raise）；默认等长 + **零时间轴位移**；J-cut 守恒路长度公式；TrimAV 直出 `join_align_seconds` 建议值 |
| 24 | **床源选窗语音规避·全路径（0.6.4）**：瓦片档（tile>0）也必须过判据且阈值收紧到 0、E5 错开不再被静默忽略、rank 约定一致（2D/3D 同点）、全源皆撞时不 raise 退最静窗、默认关逐位不变、O(T²) 回归锁、报告不静默、持续帧滤波（瞬态不计入）、退化输入不炸、前缀和能量选窗 == 参照 |
| 25 | **🛡 patch 台词守卫（0.6.5）**：本段头部台词起点 ⇒ patch 自动收缩（onset−0.25s）/关闭；台词区逐位无损；干净素材零副作用；守卫关=旧行为 |
| 26 | **多段拼接成片（0.6.7）**：`probe_mp4` 容器事实、体检四条（含「音频绕过裁重叠」判据）、流拷贝**逐位无损**、音频逐段去 priming 对齐（主频判据）、退路重编码、空输入不产出、history 条目筛选、断言快路 == 深路、**后端路由的段发现逻辑**（stub server 离线跑）；**音频代际 2 → 1**：默认 AAC **256k**（可 192k）/ PCM 边车**直读 ⇒ 与成片逐位一致** / 混档逐段标注来源 / 采样率不符弃用 / 短边车补静音告警 / 生产者回显 ui 键 + 纯函数取回 + `save_pcm=0` 退化成老返回；**音频链多生产者**（候选按离落盘远近排序、选最接近 mp4 音频的那份、差 >100 ms 拒收）；**PyAV 版本兼容**（缺模板流 ⇒ 明确报错 + 自动退重编码）；**CLI 入口真跑**（`tools/concat_segments.py`：出片退出码 0 / 坏输入退出码 1）（合成 mp4 + 合成边车，零 GPU） |
| 27 | **画质域 AV latent 分块放大（0.6.8）**：块数→块长换算（ceil 不多出空块）、spans 无缝覆盖（首块 0 起 / 末块 T 止 / 相邻块必重叠或正好平铺）、`overlap=0` 平铺、**每块 ≥ 2·overlap+1 的 fail-closed**（超限直接报错并给出**最大合法块数**，绝不偷改参数）、**分块 == 整段**（max\|Δ\| = 5.96e-08）、`chunks=1` 与整段放大逐位相同、**时间维一帧不许变**、秩不对/拿错尺子一律 raise、上游口径常量（`overlap=5` / 默认块长 32）不许漂移；**适配层 5 条**：AV 打包 latent 拆包→逐块→回包、音频流原样带回不碰、NestedTensor 输入不许炸、报告字段（请求块数→实际块数/T/显存峰值/耗时）、未装上游时报可照做的安装指引（不静默降级）。fake 放大器 = nearest×2（逐帧独立 ⇒ 有解析解），零 GPU |

## Chain 词分发的纯函数单测（node 跑、零依赖）

```bash
node tests/test_prompt_dispatch.mjs      # 期望 29/0
```

覆盖前端那套词分发逻辑（**与浏览器里跑的是同一份代码** `web/relay_kit_prompt.js`）：
`---` 分块边界（两横线不算分隔 / CRLF / 空块丢弃）、词格探测与优先级（`h3_data` > 官方 `prompt` > 其它）、
`prompt_target` 三种写法、JSON 格**只改 prompt 字段**、畸形输入一律**拒写**，
以及「同一段重跑不许在成片里算成两段」（`collectStageIds` 按段号存 id）。

## V3 外壳与默认出口（零 GPU、秒级）

```bash
COMFYUI_PATH=<根> python tests/test_v3_schema.py        # 期望 65/0
COMFYUI_PATH=<根> python tools/assert_default_exit.py   # 期望 3/3
```

`test_v3_schema.py` 验的是 **V3 与 V1 的逐字段一致**：8 个节点、111 个 input 的
**id 序列与顺序** / 取值 / `optional` / Combo `options` **全序** / outputs 路数与显示名 /
`is_output_node` / 显示名 / category。为什么必须机检：老工作流的 `widgets_values` 是**按位置**存的，
参数表错一位就是**用户参数静默错位**（不报错）。

`assert_default_exit.py` 锁的是**"默认出口 = V3"这件事本身**。它必须**单跑一个进程**且
**不设任何环境变量** —— `test_v3_schema.py` 会把 `H3RELAY_NODE_API` 写死成 `v3`，
同进程里验不出"默认"。补它的理由：2026-09-24 把默认从 v1 切到 v3 时，
**转移前的所有测试要么在验 v1、要么强制 v3，没有一条断言锁住"默认"**。

> ⚠️ V3 出口依赖宿主 `comfy_api`，而 `comfy_api.internal.api_registry` 会
> `from packaging import version`。宿主缺 `packaging` 时 V3 出口会 `ModuleNotFoundError`
> （2026-09-24 Linux CI 实测）⇒ 本包已加「**V3 加载失败 ⇒ 回退 V1 + 大声警告**」的兜底
> （一个可选出口不许把整包搞挂），并把 `packaging` 写进 `requirements.txt` 与 CI 依赖。

## tools/ 下的另四个

| 工具 | 判什么 | 期望 |
|---|---|---|
| `tools/review_050.py` | 文档—代码一致性（节点清单 / 参数表 / 断言数 / 版本号 / 示例图槽位 / **三条铁律**） | **82/0** |
| `tools/smoke_nodes.py` | 节点层功能冒烟（续接七件真跑一遍；🔍 放大节点要上游权重，不在冒烟内） | **15/0** |
| `tools/assert_default_exit.py` | 默认出口 = V3（3 条） | **3/3** |
| `tools/sync_deploy_check.py` | 部署副本与指定提交的提交态一致 | 全 `OK` |

详见 [`tools/README.md`](../tools/README.md)。CI 会跑**五项**（`.github/workflows/ci.yml`）：
回归三件套 + 默认出口断言 + V3 逐字段机检，失败时均以非零退出码退出。
