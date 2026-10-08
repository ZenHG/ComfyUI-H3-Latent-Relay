# AGENTS.md — H3 Latent Relay 的作业入口

> 给**改这个仓库的 agent** 读。先读本文件，再读 [`CONTRIBUTING.md`](CONTRIBUTING.md)。
> 本文件是**索引 + 判据映射**；规则原文在 `CONTRIBUTING.md`，机制细节在
> [`README.md`](README.md) 与 [`docs/`](docs/)。
>
> 🔴 **别把本文件当规范源**。改规范改 `CONTRIBUTING.md`；本文件只做映射，映射由机检盯着（见 §4）。

---

## 1. 一次改动的闭环

流程原文 = [`CONTRIBUTING.md`](CONTRIBUTING.md) §「一次改动的闭环」。五步，缺一步不算完成：

1. **动手前** —— 横向 grep 孪生体；问「**它读了环境里的什么**」；写下「它之所以安全的**那一条**事实」并**跑代码证明它**。
2. **改动中** —— 单点改一行重跑；一个单元一个验证；判据先红再修。
3. **改动后** —— 三档检查（§2），缺哪档**点名说出来**。
4. **数字与文档** —— 先问「谁在盯这个数」；中英成对（中文是源）。
5. **收尾** —— 打包自验；**CI 绿了才算完成**；不可逆动作先问人。

---

## 2. 三档检查（**三个「通过」互不相等**）

| 档 | 怎么跑 | 覆盖什么 | 跑不了时 |
|---|---|---|---|
| **本地** | 见 §3 命令速查 | 全部零 GPU 的闸 | —— |
| **CI** | 推上去看 workflow | 同本地 + 干净宿主环境 | 推之前**必须**跑一次 CI 条件模拟 |
| **宿主 + 浏览器** | 真起 ComfyUI、真点画布 | 前端按钮 / i18n / run_id 同步 / 节点在画布上真能出活 | **点名说没跑**，不许当作已覆盖 |

🔴 **本机跑不了、必须在别处跑的（已知清单）**：

| 项 | 在哪跑 | 为什么本地跑不了 |
|---|---|---|
| `tools/ui_smoke.cjs` | 宿主 + 浏览器 | 需要真前端（CI 无浏览器，**故意不进 CI**） |
| 宿主重载复检 | 宿主 | 要 `curl` 本机 `127.0.0.1:8188/h3relay/health` + `/object_info` |
| py3.10 兼容 | 无 | CI 只跑 py3.11，而 `requires-python = ">=3.10"` ⇒ **这一格没有覆盖** |
| 上游依赖漂移 | 无 | CI 直接吃宿主 `requirements.txt` 的当前状态 |

---

## 3. 命令速查

```bash
# —— 静态检查 ——
ruff check . --exclude dist

# —— 零 GPU 全套（宿主 ComfyUI 根用 COMFYUI_PATH 指定）——
python tests/test_relay_core.py            # 算法层，断言数见 ci.yml
python tools/review_050.py                 # 文档—代码一致性 + 全部机检
python tools/smoke_nodes.py                # 节点层冒烟
python tests/test_experimental.py          # 实验层
python tests/test_v3_schema.py             # V3 与 V1 逐字段一致
python tools/ci_env_repro.py tests/test_v3_schema.py    # 🔴 复现 CI 环境那一档
python tools/assert_default_exit.py        # 默认出口契约 + 行为（必须单进程）
python tools/check_ui_workflow.py examples/minimal_relay_official.json
python tools/en_sync.py                    # 中英节级一致
python tools/user_smoke.py                 # 干净副本 + 分发集真跑（**需要宿主 ComfyUI 根**）
node tests/test_prompt_dispatch.mjs
node tests/test_run_id_parity.mjs
node tests/test_audio_ref_budget.mjs
node tests/test_i18n.mjs

# —— 打包（用户下载的就是它）——
python tools/make_minimal_bundle.py --zip --force

# —— 只在本机（需要真宿主）——
node tools/ui_smoke.cjs
curl -s 127.0.0.1:8188/h3relay/health
curl -s 127.0.0.1:8188/object_info/H3RelayTrimAV      # 确认新参数在

# —— 发布 ——
python tools/release.py            # 预演；--go 才真发；唯一真相源 = RELEASING.md
```

⚠️ **不要用 `| tail -N` 看校验器输出** —— 退出码与计数会被吃掉。要计数就
`grep -E "失败 [0-9]+"`，要退出码就重定向到文件后 `echo $?`。

---

## 4. 铁律 ↔ 执行体（**这张表是 `tools/review_050.py` 的机检在盯的东西**）

| 规则 | 原文位置 | 执行体 | 强度 |
|---|---|---|---|
| **铁律零** 不许把本机的东西写进仓库 | `CONTRIBUTING.md` | **H3e** | 机检，命中即红 |
| **铁律一** UI 与 API 同一套实现，且必须基于节点 | `CONTRIBUTING.md` | **L10**（在位）+ `tests/test_v3_schema.py`（两出口逐字段一致）+ `tools/user_smoke.py` A7（节点 id 集合相同） | 机检 |
| **铁律二** 音画同步不靠组装层兜 | `CONTRIBUTING.md` | `tests/test_relay_core.py` 第 23 组 | 机检 |
| **铁律三** 给建议必须把好与坏都写透 | `CONTRIBUTING.md` | **L11**（只查「在位」） | ⚠️ 半机检：某条建议是否四块俱全 = **判断题，靠人** |
| **铁律七** 硬错误必须 raise，绝不静默降级 | `CONTRIBUTING.md` | `tests/test_relay_core.py` 第 3 / 9 组 | 机检 |
| **铁律八** 对别人手写的东西一律兜底 + 出声 | `CONTRIBUTING.md` | ⛔ **无机检** | 🔴 见 §6 |

### 4.1 全部机检一览（`tools/review_050.py`，一项不缺）

| 编号 | 管什么 |
|---|---|
| **H1** | 版本号四处一致（`__init__.py` / `pyproject.toml` / `CHANGES.md` 顶部 / `README.md` 首部） |
| **H3b** | `CONTRIBUTING.md` 的方面数 / 断言数 == 实跑真值 |
| **H3c** | `docs/08-testing.md` 的断言数 / 方面数 == 真值，且分组表完整 |
| **H3d** | `nodes.py` 模块头注释的节点数 == 注册数 |
| **H3e** | 开源卫生：受控文本里无本机路径 |
| **H3f** | `ci.yml` 里 `test_relay_core` 的**每一处**期望数 == 真值 |
| **H3g** | `review_050` 自己的期望数在四处声明与实跑一致（**自引用，必须排最后**） |
| **H3h** | V3 机检真值的四处声明一致，且 (节点数, input 数) **配对**校验 |
| **H3i** | `assert_default_exit` 期望数四处一致 |
| **H3j** | `smoke_nodes` 期望数四处一致 |
| **H3k** | Comfy Registry 元数据（name / license / PublisherId / Icon / Banner）+ `.comfyignore` 排开发件而**不排运行件** |
| **H3l** | 发布规范成对（`RELEASING.md` 两渠道 + 指向执行体，执行体可编译） |
| **H3m** | 仓库内无凭据字面量（受控文件 + 最近若干条提交信息；命中只报位置） |
| **H3n** | i18n 词表完整性（参数名 / 输出端口 / 节点标题 / 无死词条） |
| **H3o** | 文档里的仓库内链接与反引号路径**都真实存在** |
| **H3p** | i18n 悬浮提示（每个 (节点, 参数) 双语俱全 / 无死条目 / 真写进 `widget.tooltip`） |
| **H3q** | 前端 JS（含 `dist` 副本）无 `for…of (数字,数字)` 逗号表达式 |
| **H3r** | 防覆盖兜底成对存在（汉化插件轮询会改回中文） |
| **H3s** | link 端点字段两代都读（新版 `originNodeId` / 旧版 `origin_id`） |
| **H3t** | 官方语言入口走 `app.extensionManager.setting` |
| **H3u** | `.comfyignore` 声明的 registry 包文件数 == **纯标准库真算**（2026-10-08 加；出现 `!`/`**` 即红，fail-closed） |
| **H3v** | 文档里「N 个脚本」== `tools/` 顶层 `*.py` + `*.cjs` 的**实际个数**（数字必须写阿拉伯数字） |
| **H3w** | 无未跟踪的受检类型文件（取样面 = `git ls-files` ⇒ 未跟踪 = 扫不到 = **本地绿 CI 红**） |
| **H3x** | 无「`setdefault` 到 `PYTHONPATH`」（调用者已设时静默 no-op） |
| **H3y** | `tools/` 顶层每个脚本都在 `tools/README.md` 里登记过 |
| **H3z** | `tools/release.py` 的每个 `--flag` 都在 `RELEASING.md` 里被写到 |
| **H4a** | 跑 Python 的子进程都**显式**给了 `env=`（**启发式**：按调用形状认，别名/变量绑定可绕） |
| **H4b** | `review_050.py` 自身判据表自检（cond 不许恒真 / 名字不许重复；**语义偏弱挡不住**） |
| **H4c** | 判据台账（`tools/checks_ledger.py`）与本文件编号**双向一致**，且 `tools/CHECKS.md` 未过期 ⇒ **新加判据不登记 = 红** |
| **H4d** | JS 测试（4 个 `.mjs`）的期望数 == **实跑**（`node` 找不到 ⇒ **红**，fail-closed） |
| **H4e** | dist 白名单文件数：`MANIFEST` 各段之和 == 文档声明（`dist/` 在时还核实际文件数） |

> ⚠️ **本表是摘要，可能滞后**。**权威清单 = `tools/CHECKS.md`**（由 `tools/checks_ledger.py --write` 生成，
> **H4c** 盯着它与 `review_050` 的编号双向一致）。要「有哪些判据 / 每条盯什么口径 / 有没有变异测试 /
> 读哪些环境量」，**看台账，别看这里**。

### 4.2 「口径数字」——**这些数字不许裸写**

**口径数字** = 「描述某个可数事实、**且有机检盯着**」的数。完整表见 **`tools/CHECKS.md`**
（由 `tools/checks_ledger.py --write` 生成，**别手改**）。当前 **14** 条判据在盯口径数字：

| 数字 | 谁盯 | 写在哪 |
|---|---|---|
| 版本号 | **H1** | `__init__.py` / `pyproject.toml` / `CHANGES.md` 顶部 / `README.md` 首部（**四处**） |
| 方面数 + 断言数 | **H3b** / **H3c** / **H3f** | `CONTRIBUTING.md` / `docs/08-testing.md` / `ci.yml` |
| `review_050` 期望数 | **H3g** | `ci.yml` / `docs/08` / `tools/README` / `README` |
| `smoke_nodes` 期望数 | **H3j** | 同上 |
| `assert_default_exit` 期望数 | **H3i** | 同上 |
| V3 三元组（节点数 / input 数 / 项数） | **H3h** | `ci.yml` / `docs/08` / `README` / `__init__.py` |
| `nodes.py` 头注释节点数 | **H3d** | `nodes.py` |
| 示例图节点数 | **I4** | `examples/README.md` |
| registry 包文件数 | **H3u** | `.comfyignore`（注释里） |
| `tools/` 脚本数 | **H3v** | `README.md` / `tools/README.md` |
| **JS 测试期望数 ×4** | **H4d** | `ci.yml` / `docs/08` |
| **dist 白名单文件数** | **H4e** | `docs/08` / `tools/README` |

🔴 **写作纪律（两条，都是被误报逼出来的）**：

1. **口径数字必须写阿拉伯数字**。实测：文档写「十五个脚本」，而判据的正则是 `(\d+) 个脚本`
   ⇒ **一条都匹配不到 ⇒ 整条判据空转**（2026-10-08 变异测试抓到）。要写 `15 个脚本`。
2. **叙述里别写「长得像口径」的数**。实测两次：
   · `tools/README.md` 里叙述「头注释写 `22/0`」⇒ 被 **H3g** 当成 `review_050` 的期望数 ⇒ 误报；
   · `tools/README.md` 里「运行期 `25 文件`」⇒ 被 **H4e** 的正则扫到 ⇒ 误报。
   ⇒ 口径数字**只在它的声明位写一次**；叙述里要提，就**别写成 `N/0` 或 `N 文件 /`**。

**新增一条口径数字 = 必须同时**：① 加判据；② 在 `tools/checks_ledger.py` 登记（否则 **H4c** 红）；
③ 按上面两条写。
| **I1–I4** | 示例图：widget 值不超槽位 / 取值在候选内 / 带 `widget` 标记 / `examples/README.md` 节点数与实际一致 |
| **J1 / J3** | 存量工作流无过期输出数组 / `custom_nodes` 无同包第二份副本 |
| **L2–L13** | 音频缝契约与文档口径（含 L10/L11 铁律在位、L13 英文同步闸） |
| **E1** | `mask_mode` 三处一致（`relay_core` 常量 / 节点选项 / 权重函数） |

---

## 5. 禁区

- ⛔ **不改包名**（`[project].name` = Registry 的安装 id，**发布后不可改**）。
- ⛔ **不把凭据写进任何受控文件或提交信息**（机检 **H3m**；值只允许存在于一个被 gitignore 的文件里）。
- ⛔ **不 `git add -A`**；不替人提交、不替人推送、不重写历史。
- ⛔ **不在 `custom_nodes/` 下留本包的第二份副本**（会静默覆盖节点定义，机检 **J3**）。
- ⛔ **不让 `relay_core/` import 任何 ComfyUI 模块**（它是纯算法层，要能脱离宿主单测）。
- ⛔ **不写行内注释到 ignore 文件**（`#` 只有行首才算注释；已因此踩过两次）。

---

## 6. 已知薄弱点（诚实列出，别当已覆盖）

| # | 薄弱点 | 现状 |
|---|---|---|
| 1 | **铁律八 无机检** | 「兜底必须出声」这半条没有执行体。结构性的一半（布尔白名单）已落地，但下一个人仍可写 `bool(用户手写值)` 而无人拦 |
| 2 | **铁律三 只查「在位」** | 某条建议是否四块俱全（收益 / 代价 / 退路 / 能力边界）判不了，靠人 |
| 3 | **py3.10 无 CI 覆盖** | `requires-python = ">=3.10"`，CI 只跑 3.11 |
| 4 | **上游依赖不固定版本** | 与本包无关的红会混进来 |
| 5 | **`tools/ui_smoke.cjs` 不进 CI** | 有意的（CI 无浏览器）；代价是**这一档只能靠人记得跑** |
| 6 | **判据 / 工具的「单环境验证」** | 本仓最高频的返工病因（实测 15 次）。已写进 `CONTRIBUTING.md` 坑⑨，**H3x** 只挡住了其中一种写法（`setdefault` 到 `PYTHONPATH`）；**「只在一种环境跑过」这件事本身仍无机检** |
| 7 | **无类型检查**（无 mypy / pyright） | 而 `relay_core/` 是纯算法层、天然适合强类型（140 个 def，57 个有返回注解）。代价要**先量再定**，命令见下 |
| 8 | **`SIM115` 未开**（44 处 `open()` 没走 context manager） | 价值真实，但 20 处在 `tools/review_050.py`（它的期望数被 **H3g 自引用机检**盯着）⇒ 刻意留给单独一轮 |
| 9 | **`H3u`/`H3v` 只覆盖「已声明过的」数字** | 真值判据要求**先有声明**。一处都没声明的数字仍拦不住（这正是 2026-10-08 加它们的原因，也说明这条缺口只是收窄、没有关死） |
| 10 | **`H4a` 是启发式，不是证明** | 参数绑到变量、或包一层，它就看不到。它挡的是「**根本没想过环境**」这种真实写法，挡不住**故意规避**。⚠️ 而且它只验**形式**（写没写 `env=`），**不验那个表达式是否成立** —— 实测 2026-10-08：`env=dict(os.environ, X=本模块没有的常量)` 照样通过 H4a，是 **ruff F821** 抓到的 |
| 11 | **`H4b` 只挡「字面恒真」** | 「语义偏弱」（如 H3h 曾只查值在不在集合里、不查配对）**只能靠人发现** —— 机检的强弱本身没有机检。⇒ 替代办法 = **`CONTRIBUTING.md` 坑⑩：新写/改判据时必须做一次手工变异测试**（**刻意不做自动化 runner**：价值集中在写判据那一刻，而 runner 要维护可执行规格 ⇒ 新增腐化面；本仓已有僵尸工具先例） |
| 12 | **「工具行为 == 工具文档」只覆盖到「名字/参数出现过」** | `H3y`/`H3z` 查的是「登记过没有」，**不查描述对不对**（`--verify-only` 那种语义误解仍要靠人） |

**静态检查现状（2026-10-07 扩）**：`E/F/W` + `B`（bugbear）/ `C4` / `PIE` / `RET` / `RUF100`。
选型判据与「刻意没开的规则连同理由」写在 `pyproject.toml` 的 `[tool.ruff.lint]` 里 —— **改之前先量**：

```bash
ruff check . --exclude dist --statistics                    # 现役规则集
ruff check . --exclude dist --select <候选集> --statistics  # 先量代价再决定开不开
# ⚠️ 别把 RUF001/002/003（中文标点在注释里的误报，约 2 万条）与 UP031（本仓刻意用 %-格式）算进代价
```

---

## 7. 改了本文件之后

本文件的路径引用由 **H3o** 扫（受控 `*.md` 里的仓库内链接与反引号路径必须真实存在）。
新增引用前先确认目标文件真的在仓库里 —— **本地有、仓库里没有的文件（被 gitignore 的本地笔记）
写进来会让 CI 红而本地绿**。
