# tools — 自检与合规取证

**17 个脚本**：**12 个自检/取证**（零 GPU、秒级，提 PR 前至少跑前四个）+ 拼接 CLI（给不开画布的用户）+ 打包器（产出最小分发集）+ 发布器（两个渠道成对发布 + 交叉验证）+ 声库采集器（多说话人长片的声锚）+ **浏览器冒烟门**（`ui_smoke.cjs`，**只能在有活宿主的机器上跑**）。

## 三档：本地 / CI / 只能宿主 + 浏览器

🔴 **三个「通过」互不相等**（本地全绿 ≠ CI 绿 ≠ 宿主生效）。跑完一档要说清**另外两档跑没跑**。

| 档 | 包含 | 说明 |
|---|---|---|
| **本地** | 本文件表格里的**十个自检/取证** + 四个 `node tests/*.mjs` | 零 GPU、秒级。`user_smoke.py` 与打包器**需要宿主 ComfyUI 根**（`COMFYUI_PATH`） |
| **CI** | 同本地（顺序见 [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)） | 干净宿主环境。**只跑 py3.11**，而 `requires-python = ">=3.10"` ⇒ 3.10 那一格**没有覆盖** |
| **宿主 + 浏览器** | `ui_smoke.cjs` + 宿主重载复检（`/h3relay/health`、`/object_info`） | **故意不进 CI**：要一个活着的 ComfyUI + 一个浏览器，CI 两者都没有 ⇒ 加进去只会得到永远 SKIP 的假门 |

**为什么必须留第三档**：`ui_smoke.cjs` 的头部记着两个实例 —— 后端方法全绿、Python 断言全绿、
三个 JS 单测全绿、`/h3relay/health` 正常，而同期前端有**两个功能根本不可用**（`⛔ 连跑` 必抛
`TypeError`；`run_id` 同步钩子**从未挂上**）。两者都**不在任何 CI 期望值的覆盖范围内** ——
它们是「**绿着但坏**」那一类，只有真点一次画布才看得见。

```bash
node tools/ui_smoke.cjs                    # 默认 http://127.0.0.1:8188
node tools/ui_smoke.cjs --base-url http://127.0.0.1:8189
```
⚠️ 找不到浏览器 / 连不上宿主 ⇒ 它**打印醒目 SKIP 并以 0 退出**（不假装通过）。
**SKIP 就是没跑** —— 别把 SKIP 读成通过。

⚠️ `playwright` 按**脚本所在目录向上**找 `node_modules`。装在别处（例如 agent 自己的 workspace、
与本仓不同盘）时用 `H3RELAY_NODE_MODULES=<装了 playwright 的 node_modules 目录>` 指过去 ——
**不设的话它只会说"没装 playwright"，而真相是"装在别处"**。

## 双环境自检（改了「读环境」的判据或工具之后必跑）

本仓最高频的返工病因 = **判据 / 工具只在一种环境下被验证过**。跑一遍脏环境：

```bash
# 干净环境（≈ CI）
python tools/review_050.py && python tools/user_smoke.py
# 脏环境：故意塞一个假 PYTHONPATH —— 调用者环境不该影响结果
PYTHONPATH=/nonexistent python tools/review_050.py && PYTHONPATH=/nonexistent python tools/user_smoke.py
```

**两次结果必须逐项相同。** 不同 ⇒ 有工具在读调用者的环境，而 CI 恰好没那个量 ⇒
**CI 绿、本机红**（`tools/user_smoke.py` 2026-10-07 就栽在这里：子进程用
`setdefault("PYTHONPATH", …)`，调用者已设时是**静默 no-op**）。
宿主相关那条路用 `tools/ci_env_repro.py` 模拟（见上表）。

⚠️ 上面是 **bash** 写法。PowerShell 用 `$env:PYTHONPATH="/nonexistent"`，
cmd 用 `set PYTHONPATH=\nonexistent`（同一行内跑，别开新窗口）。

| 脚本 | 判什么 | 期望 | 怎么跑 |
|---|---|---|---|
| `review_050.py` | **文档—代码一致性**：节点清单 / 参数表 / 断言数 / 版本号 / **全部示例图**槽位（I1-I4：槽位 / 节点数 / widget 标记） / **L13 英文文档同步闸** / **H3j smoke 期望值** / **H3k registry 元数据** / **H3l 发布规范在位** / **H3m 无凭据字面量** / **H3n i18n 词表完整性** / **H3p i18n 悬浮提示双语完整性** / **H3q JS 逗号表达式** / **H3r i18n 防覆盖兜底** / **H3s link 端点字段两代兼容** / **H3t 官方语言入口** / **H3o 文档悬空引用** / **H3u registry 包文件数真值** / **H3v tools 脚本数真值** / **H3w 未跟踪文件** /  **H3x `setdefault` 到 `PYTHONPATH`** / **H3y tools 脚本登记** / **H3z release.py flag 齐全** / **H4a Python 子进程显式 env** / **H4b 判据表自检** / **H4c 台账一致** / **H4d JS 期望数真值** / **H4e dist 白名单真值** | **116/0** | `python tools/review_050.py` |
| `smoke_nodes.py` | **节点层功能冒烟**：续接七件真跑一遍（不是只看 INPUT_TYPES；🔍 放大节点要上游权重，不在冒烟内） | **16/0** | `python tools/smoke_nodes.py` |
| `checks_ledger.py` | **判据台账**：`review_050.py` 的机检**逐条登记**（编号 / 类别 / 盯的口径数字 / 变异测试 / 读的环境量）。`--write` 生成 `tools/CHECKS.md`，`--check` 核「台账 ↔ `review_050` 编号**双向一致**」⇒ **新加判据不登记就红**（`review_050` 的 **H4c** 跑的就是它）。为什么要有：机检从 19 条长到 100+ 条，此前**没有任何一处回答「我们有哪些判据、每条盯什么」** —— 实测 JS 的 4 个期望数因此**从未被盯过** —— `ci.yml` 的**头注释与执行行对不上**（22 vs 29）而无人发现（现已由 **H4d** 盯住） | 退出码 `0` | `python tools/checks_ledger.py --check`（另有 `--write` / `--list [--caliber]`） |
| `check_ui_workflow.py` | **UI 格式工作流 JSON**：槽位下标、连线两端、类型相容、widgets_values 项数、**音画接线**（落盘节点的 `audio` 是否走了「裁重叠/音频缝」的裁后输出） | 问题合计 **0** 条 | `python tools/check_ui_workflow.py examples/minimal_relay_official.json` |
| `env_matrix.py` | **环境矩阵复跑**：同一批闸在 3 个合成环境（干净 / 脏 `PYTHONPATH` / 空 `PYTHONPATH`）下各跑一遍，要求结果**逐项相同**。为什么要有：本仓最高频的返工病因就是「**判据 / 工具只在一种环境下被验证过**」（`CONTRIBUTING.md` 坑⑨，实测 15 次）—— `tools/user_smoke.py` 2026-10-07 的 bug 正是它。⚠️ **只覆盖有确定期望的环境轴**：`COMFYUI_PATH` **没进来**（它对某些闸是**必需**的，那是设计意图；要收它就得配「合法差异」白名单，而白名单本身会腐 —— **宁可不做**） | 退出码 `0` | `python tools/env_matrix.py`（`--gate <名>` 只跑一条） |
| `scan_expression_overlap.py` | 🔍 **合规取证**：与第三方包逐函数「表达层重合」扫描 | 人工判读（**只报数、不定性**） | `python tools/scan_expression_overlap.py <对方仓库路径>` |
| `verify_rewrite_equivalence.py` | 🔍 **合规取证**：三簇重写前后**逐位等价**差分验证（旧实现从 `git show <rev>` 捞，不手工转录） | **137/0**（⚠️ 该工具**不在 CI 里**：2026-10-07 实测它此前**根本跑不起来**（命名空间手写清单漏了 `apply_relay` 的两个依赖），而文档一直写着 137/0。本次修三处：`--old-rev` 自动定位 + 命名空间自动补齐 + **契约补上 0.6.23 新增的「音频参考说明 note」**，并加了内容校验 —— 比原来的「只数条数」更严） | `python tools/verify_rewrite_equivalence.py` |
| `sync_deploy_check.py` | **部署副本同步**：比对 `custom_nodes` 下的副本与指定提交的**提交态**（忽略 CRLF 行尾差异） | 全 `OK` | `python tools/sync_deploy_check.py <副本目录>` |
| `assert_default_exit.py` | **默认出口断言**：不设 `H3RELAY_NODE_API` 时必须走 V3（`NODE_CLASS_MAPPINGS is None` + `comfy_entrypoint` 可调用 + `WEB_DIRECTORY` 在 + **V3 `get_node_list()` 注册的节点集合 == `nodes.py` 的键集合**）。为什么需要：2026-09-24 默认从 v1 切到 v3，而**转移前那套测试全都在验 v1 或强制 v3，没有任何一条断言锁住"默认"** | **5/5** | `COMFYUI_PATH=<根> python tools/assert_default_exit.py` |
| `ci_env_repro.py` | **在本机造出「没有宿主注册表」那条路径**跑任意校验脚本（`sys.modules["nodes"] = None`）。为什么需要：只跑本机**证明不了另一条代码路径也没问题** —— 2026-09-29 就这么放过一次 CI 红。⚠️ 2026-10-07 起它造的这条路**数字与直跑相同**了（那个「7 节点」是它自己造出来的**人造态**，已根修），**但它照旧值得跑**：数字相同 ≠ 路径相同 | 被跑脚本的退出码直传 | `python tools/ci_env_repro.py tests/test_v3_schema.py` |
| `en_sync.py` | **英文文档同步闸**：中文 = 源、`*_EN.md` = 派生物 ⇒ 节级 hash 比对。**源里新增的节默认必须译**（未登记即红；跳译要 `--omit` 写理由）。`--apply` 只重写**机械面**（版本号 / 文件头标记 / 索引缺行）、`--brief` 出增量翻译任务书交给译者或 LLM、`--ack` 暂缓**带期限**。**刻意不机翻**：机翻会静默改掉数字与标识符，而机检只扫中文源 ⇒ 英文版成监管盲区 | 退出码 `0`（不一致 `1`、**闸失效 `2`**） | `python tools/en_sync.py`（另有 `--status` / `--brief 7.3` / `--apply` / `--stamp`） |

**拼接 CLI（不是自检工具，给用户用）**

| 脚本 | 干什么 | 怎么跑 |
|---|---|---|
| `concat_segments.py` | 把 N 个段文件拼成一条成片（**与画布上 🧩 按钮同一份核心代码**）：画面流拷贝无损 + 音频逐段对齐 + 四项断言 + 退路 | `python tools/concat_segments.py s1.mp4 s2.mp4 -o film.mp4 [--audio aac256\|aac192\|lossless] [--crf 16] [--pcm p1.st - ...] [--json]` |

退出码 0 = 过四项断言。给 **API / 无头 / 批处理** 用户用，见 `docs/10` §7.4。

**打包器（不是自检工具，给分发用）**

| 脚本 | 干什么 | 怎么跑 |
|---|---|---|
| `make_minimal_bundle.py` | **产出最小可运行分发集**（**42 文件 / 1260 KB，zip 446 KB**；⓪ 先跑英文文档同步闸——不过就不出包 ① 清单与**静态 import 推导**交叉校验 ② 写出 `dist/` ③ **自验**把产出当独立包加载：8 节点 / 默认 V3 / 前端在 / 异常上下文 / **零模块来自原仓**）：只带运行期 25 文件（含 `relay_core/` 包 11 个）+ 前端 7 + 示例 3 + 元数据/法律/必读 7，**不带** `docs/` `tests/` `tools/` `.github/` | `python tools/make_minimal_bundle.py --zip` |

**发布器（不是自检工具，给发版用）**

| 脚本 | 干什么 | 怎么跑 |
|---|---|---|
| `release.py` | **两个渠道成对发布 + 交叉验证**：前置（干净工作树 / 版本四处 / `review_050` / `en_sync`）→ push → **等本轮 CI 真绿** → `comfy node validate` + 打包 + registry publish → 回头查两边是不是这一版 → 同步部署副本。规范正文 = [`RELEASING.md`](../RELEASING.md) | 见右栏（**四种跑法含义不同，别混**）：<br>`python tools/release.py` = **预演**（跑 ① 前置 + 改动面，不推不发）<br>`--go` = 真发<br>`--verify-only` = **只查两渠道现在同步吗**（纯只读，**不跑 ① 前置**）<br>`--release-only` = 只补建 GitHub Release（幂等，不 push、不发 registry） |

**浏览器冒烟门（不是自检工具：要一个活着的 ComfyUI + 一个浏览器）**

| 脚本 | 干什么 | 期望 | 怎么跑 |
|---|---|---|---|
| `ui_smoke.cjs` | **前端交互冒烟**：7 个按钮**逐个真点**（先制造 `run_id` 冲突 ⇒ 会提交 prompt 的按钮必须被闸拦住 ⇒ 全程 `/queue` 恒空、**零 GPU**）、`run_id` 同步范围、无未捕获 JS 异常。为什么必须有：后端与 API 全绿、518 条 Python 断言全绿时，前端仍可能有**根本不可用**的功能（「绿着但坏」） | **14/0** | `node tools/ui_smoke.cjs`（`--base-url` 换宿主；`H3RELAY_NODE_MODULES` 指定 playwright） |

**声库采集器（不是自检工具，给多说话人长片用）**

| 脚本 | 干什么 | 怎么跑 |
|---|---|---|
| `voice_bank.py` | **从已渲染段采集「某角色的声锚」**，喂给 Copy Bridge 的可选输入 `voice_anchor`（跨说话人续接锁定音色，见 [`docs/07`](../docs/07-chain.md) §声锚）。有声检测（≥0.6 s）+ 峰值归一 0.9 + 声库 `voices.json`（**首个稳定锚锁定复用**，手动 `<角色名>.wav` 永远优先）。可选 **ASR 台词守卫**：装了 `funasr` 才启用（验台词 CER ≤ 0.35 + 逐字时间戳精确裁锚）；没装 ⇒ 打印警告后回退纯能量法，**零额外依赖**。另有 **`advise`**（一段内多人时算「该不该接锚 / 该接谁」——规则 = 锚给**缝上第一个开口说话的人**；同人续接不必接、换人才必须接）与 **`selftest`**（说话人解析规则的自检，可证伪） | `python tools/voice_bank.py collect <段产物.mp4> --name <角色名> --bank <声库目录> [--line "台词原文"]`（另有 `lookup` / `list` / `advise` / `selftest`） |
| `user_smoke.py` | **外部开源用户视角冒烟**（`git clone` 那条渠道）：按 `git ls-files` 复制干净副本 ⇒ 两个出口注册同一批节点 / `INPUT_TYPES()` 可求值 / 前端 JS 在位 / **模块零外泄**；再从 `dist/` 真跑一次节点方法 | **10/0** | `COMFYUI_PATH=<根> python tools/user_smoke.py` |

前四个失败时会以非零退出码退出（CI 直接可用），见 `.github/workflows/ci.yml`。

## 两个取证工具为什么留在仓库里

`scan_expression_overlap.py` 与 `verify_rewrite_equivalence.py` 是一次性核查的产物，
但它们是「三簇重写已消除与 `ComfyUI-H3-Motion-Context`（GPL-3.0）的表达层重合」
这一结论的**可复验证据链**。删掉它们 = 结论失去可验证性。
背景与核验方法见 [`THIRD-PARTY-NOTICES.md`](../THIRD-PARTY-NOTICES.md) §一·B。

⚠️ 跑 `scan_expression_overlap.py` 需要对方仓库路径；**不要把对方代码复制进本仓库**，
工具只在进程内读取比对。若对方包已从本机移除，传任意检出的副本目录路径即可。

## 判据纪律

- **只报数、不定性**：重合率高 ≠ 派生。短小数学循环、ComfyUI 强制 API、Python 惯用法都会天然重合。
  定性必须看具体行与上下文，**由人判定**。
- **判据不能与被判对象共用同一个函数**（曾因此出现过恒真的假绿）。
- 改判据后要**反向自证**：故意做坏的实现必须被抓到。
