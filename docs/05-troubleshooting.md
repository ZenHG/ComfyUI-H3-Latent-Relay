# 排障 · 工作流自检 · API 提交

> 本文件由 README（0.6.0 梳理）拆出。
> 📐 **槽位记号**：本文一律 `[N]` = **0 起算**（UI 上第 N 个口 = `[N-1]`）。
> 搬运自旧稿的「第 N 路」是 1-based 的历史写法，已在关键处改为 `[N]`。
> 主 README 只留快速阅读必需的结论与指针。

---

## 排障

| 现象 | 原因 | 处置 |
|---|---|---|
| `不是 H3 的整步续接窗口` | 帧数不在 5+17k 上 | 改成合法值 |
| `context_latent 是 WxH，本段是 WxH` | 两段分辨率不同 | 统一分辨率重跑 |
| `起始于周期位置 N（非 0）` | 段长使尾段起点不对齐 | 调整段长，令 `(总步数 - 尾段步数) % 5 == 0` |
| `钉住 N 帧、本段只有 M 帧` | 窗口 ≥ 段长 | 缩短窗口或加长段 |
| `要裁 N 帧，但本段只有 M 帧` | `trim_frames` 误填成整段长度 | 接桥的 `[2]` 输出（trim_frames），别手填 |
| 日志出现 `⚠ 未接 audio` | 裁重叠节点没接音频 | 把 `VAEDecodeAudio` 的输出接到它的 `audio` |
| 接缝处约 0.9s 重播 | 没接裁重叠节点 | 按上面的接线图把 `🔗 续接裁重叠` 串在 VideoCombine 前 |
| 拼接处 1 帧硬跳 / 上一段的字幕文字串进本段开头 | 钉住区之后的「复现帧」没被裁掉（切换点逐段不同） | **0.3.0 起默认自动处理**，不用改任何参数；日志会写 `裁首 N 帧 = 钉住 X + 沉降 Y` |
| 连跑好几段，每段台词却一模一样 | 没填 `prompts`（连跑的默认行为是反复提交同一张图、只改段号） | Chain 上填 `prompts`（`---` 分块，第 k 块喂第 k 段）再点 ⏩；`tools/check_ui_workflow.py` 会查块数是否够 |
| 点 ⏩ 连跑，第一段之后就不排队了，status 说「第 k 段没有对应词」 | `prompts` 的块数 < `segments` | 补齐第 k 块，或把 `segments` 改成 ≤ 块数（**故意不静默复用上一块**，免得你以为换了词） |
| 词没写进去，status 说「自动探测到 N 个候选」 | 图里有多处可写的词格（多个 `prompt` / 多个 `h3_data`） | 在 `prompt_target` 填节点 id（设置里打开「节点 ID 徽章」就能看到 id），如 `683` 或 `683.h3_data` |
| 点 🧩 拼成一条，报「一段视频文件都没找到」 | 本轮没有可用的段记录，且 `segments` 不是正数 | 用 ▶/⏩ 跑过一轮再拼；或把 `segments` 填成正数；或确认图里有 `SaveVideo` 一类落盘节点 |
| 拼接报「第 k 段音频比视频长 0.X s ⇒ 很可能是音频线绕过了裁重叠」 | 落盘节点的 `audio` 接的是 `VAEDecodeAudio`（未裁原始音频） | 改接「裁重叠 `[1] audio`」（或经「音频缝 `[0] audio`」）后重跑该段，再拼 |
| 拼完每缝 ~0.87s 静止画 / 声音整体前移 | 用了 `ffmpeg -f concat` 或 `acrossfade` 拼 | 用包内的 🧩；要走外部工具就上 concat filter 重编码，并**必做**帧数守恒 + A/V 差 ≤ 1 帧自检 |
| 各段成片长度差一两帧 | 自动模式逐段量出的沉降量不同 | 想等长就把 `settle_frames` 全片填同一个正数（如 9） |
| 日志写 `未检出显著切换点` | 本段切换点就在钉住区内，或画面太平/太糊 | 正常，按只裁钉住区处理；确实还跳就手填 `settle_frames` |
| 日志写 `但位置超出自动沉降上限（12 帧）…不动刀` | 这是本段自己的切镜，不是接缝 | 不用管；确实要裁就手填 `settle_frames` |
| 画面两套续接打架 | 上游像素续接没关 | 清空上游的 cont / 参考视频字段 |
| 台词起音被裁 / 缝区有"无理由伪音" | 对话排进了续接段头部；或音频瞬态没补 | 按「台词安全时刻公式」后移台词；接 🔗 续接音频缝并把 `patch_seconds` 设 2.0（0.5.0 起在**节点内**补，见「接缝处的对话规避与音频处理」一节） |
| **`stage_index=N 表示本段是第 N+1 段，但没有可续接的上一段`** | 手改段号时只改了桥或只改了落盘；或换了片子没填 `run_id` | 两处 `stage_index` 必须一样大、`run_id` 两边一致；确实是独立段就把段号改回 0 |
| 节点列表里**一个 `🔗 H3 续接` 都没有** | 环境的 `torch` 缺失/损坏（本包顶层 import torch） | 看 ComfyUI 启动日志里本包的 IMPORT FAILED；修复 Python 环境的 torch |
| 节点能注册但续接毫无效果、日志出现「未找到上游节点文件」 | 宿主 ComfyUI 过旧，不含 MiniMax-H3 支持 | 更新 ComfyUI 到支持 MiniMax-H3 的版本（含 `comfy_extras/nodes_minimax_h3.py`） |
| **升级后节点签名没变**（例：`H3RelayTrimAV` 在 `/object_info` 里仍只有 3 路输出、没有 `prev_tail`），而盘上源码明明是新版 | 🔴 **`custom_nodes/` 里有本包的第二份副本**（典型：同步时留下的 `ComfyUI-H3-Latent-Relay.bak-<日期>`）——两份都带 `__init__.py` ⇒ **ComfyUI 全都加载**，节点定义被后加载的那份**覆盖**。⚠️ 官方只忽略 **`.disabled` 结尾**的目录（`nodes.py` 的 `if module_path.endswith(".disabled"): continue`），**`.bak` 不认** | 把非现役那份**移出 `custom_nodes/`**（或改名以 `.disabled` 结尾）后重启后端。自检：`python tools/review_050.py` 的 **J3** 节会扫出同包副本 |
| 日志出现 `上游时序网格已变` | ComfyUI 更新后 H3 网格与本版不匹配 | 按提示到本仓库提 Issue；在修复版发布前别用续接 |
| **示例图里本包节点显示一排空的输入圆点**（官方节点正常），而功能其实没坏 | 工作流文件的 `inputs` 缺 `{"widget": {"name": …}}` 标记。前端 `nonWidgetedInputs()` 把没有该标记的输入**当普通插槽渲染**；`widgetInputs.ts` 的 `onGraphConfigured` **只删不补**，不会自动帮你补上 | 用当前版重新生成：`python examples/make_minimal_workflow.py`。自检：`python tools/check_ui_workflow.py <文件>`（本判据 2026-09-19 加入；见 `CHANGES.md` 0.5.0「示例模板修复」） |
| **音画越到后面越不同步**（第 3 段起口型明显对不上） | 落盘节点的 `audio` 接的是 **`VAEDecodeAudio`（未裁原始音频）**，而画面走的是「裁重叠」的裁后帧 ⇒ 每缝差 ~0.9s 且逐段累积 | 把 `audio` 改接「裁重叠 `[1] audio`」（用了音频缝则接它的 `[0] audio`）。自检：`python tools/check_ui_workflow.py <文件>` —— **本判据 2026-09-21 加入**（节点只能发现「输入没接」，发现不了「输出被悬空」，所以必须由工具查） |

> ⚠️ 最后一条是 **0.2.1 起新增的硬拦**。旧版遇到这种情况会**静默按独立段直通**——
> 界面一路绿灯，产出的却是没有续接的接缝，只有目检才发现。现在直接报错。

## 🔧 工作流文件被改坏的自检（随包工具）

工作流的 `widgets_values` 是**按位置**对应前端 widget 槽位的。用脚本生成 UI 工作流时，
若漏掉 ComfyUI 前端**自动注入**的那一格（典型：带 `control_after_generate: True` 的 `seed`
后面会多一个下拉），其后所有取值会整体前移一位 —— 文件能打开、能提交，
但采样参数全错且**没有任何报错**（实测：`SelfLiftH3Sampler` 的
`w_max` 收到文件名、`upscaler_model` 收到 `false`）。

随包带一个体检脚本，拿真实 schema 逐位校验取值：

```bash
python tools/check_ui_workflow.py --all
python tools/check_ui_workflow.py /path/to/ComfyUI/user/default/workflows/xxx.json
```

**schema 来自两处**（2026-09-19 起）：**本包的 8 个节点现读包内 `nodes.py`**（代码才是权威），
其余节点才走正在运行的 ComfyUI 的 `/object_info`；顺带会把「服务端加载的定义与代码不一致」
报出来（提示该重启后端了）。**服务端连不上也不致命**：本包节点照样校验，只是非本包节点那部分标为「未查」。
ComfyUI 不在默认位置时用 `--comfyui /path/to/ComfyUI` 或环境变量 `COMFYUI_PATH`。
（判据：只看服务端的话，一个**还没重启的后端**会让它拿旧 schema 判新文件 ⇒ 误报或假绿。）

🔴 **动态 COMBO（如 `SaveVideo` 的 `format` / `codec`）也要查**：这类输入选中后会
**派生子 widget**（如 `format=auto` → `format.codec`），前端实际槽位是
`filename_prefix` / `format` / `format.codec` / `codec` 共 4 格。本包的生成器与体检器
都按 schema 的 `options[key].inputs` 把子参数展开，**不会**把动态 COMBO 当普通连线漏掉
——这是 2026-09-19 修过的自证式假绿重灾区（白名单漏一项、`want`/`got` 一起漏 ⇒ 错图也报 0）。
示例图 `examples/minimal_relay_official.json` 用当前版重新生成即可与前端存档逐位对齐。

## 🤖 API 提交（脚本 / 程序化）

本包 8 个节点**全部可经 `/prompt` API 提交**（与 UI 图同一套节点，无 UI-only 逻辑）。三条路：

1. **前端导出**：ComfyUI 菜单「工作流 → 导出（API）」得到 API 格式（每个节点是
   `{"<id>": {"class_type": ..., "inputs": {...}}}`，输入按**命名参数**而非 `widgets_values` 顺序）。
2. **编程构造**：每个输入的名字/类型/默认值都可从 `GET /object_info` 查到
   （`oi["H3RelayPost"]["input"]["optional"]["settle_auto"]` 这样逐键读）——按名字填即可，
   连线值写 `[源节点id字符串, 输出序号]`。例：

   ```json
   "904": {"class_type": "H3RelayPost",
            "inputs": {"images": ["903", 0], "settle_auto": 1.0, "baseline": "robust"}}
   ```

3. **校验**：提交前跑 `python tools/check_ui_workflow.py 你的图.json`——本包节点用**本地定义**
   逐位校验（后端没重启也能查），取值错位/缺槽在这里现形。

⚠ 两个纪律：① `advanced: True` 的折叠参数**照常参与 API 提交**（折叠只是画布显示）；
② 改了节点参数名/顺序的版本升级，老 API 脚本要先对一遍 `object_info`（本包所有破坏性
变更都记录在 `CHANGES.md`）。