# tools — 自检与合规取证

十三个脚本：**九个自检/取证**（零 GPU、秒级，提 PR 前至少跑前四个）+ **一个拼接 CLI**（给不开画布的用户）+ **一个打包器**（产出最小分发集）+ **一个发布器**（两个渠道成对发布 + 交叉验证）+ **一个声库采集器**（多说话人长片的声锚）。

| 脚本 | 判什么 | 期望 | 怎么跑 |
|---|---|---|---|
| `review_050.py` | **文档—代码一致性**：节点清单 / 参数表 / 断言数 / 版本号 / 示例图槽位 / **L13 英文文档同步闸** / **H3j smoke 期望值** / **H3k registry 元数据** / **H3l 发布规范在位** / **H3m 无凭据字面量** | **91/0** | `python tools/review_050.py` |
| `smoke_nodes.py` | **节点层功能冒烟**：续接七件真跑一遍（不是只看 INPUT_TYPES；🔍 放大节点要上游权重，不在冒烟内） | **16/0** | `python tools/smoke_nodes.py` |
| `check_ui_workflow.py` | **UI 格式工作流 JSON**：槽位下标、连线两端、类型相容、widgets_values 项数、**音画接线**（落盘节点的 `audio` 是否走了「裁重叠/音频缝」的裁后输出） | 问题合计 **0** 条 | `python tools/check_ui_workflow.py examples/minimal_relay_official.json` |
| `scan_expression_overlap.py` | 🔍 **合规取证**：与第三方包逐函数「表达层重合」扫描 | 人工判读（**只报数、不定性**） | `python tools/scan_expression_overlap.py <对方仓库路径>` |
| `verify_rewrite_equivalence.py` | 🔍 **合规取证**：三簇重写前后**逐位等价**差分验证（旧实现从 `git show <rev>` 捞，不手工转录） | **137/0** | `python tools/verify_rewrite_equivalence.py` |
| `sync_deploy_check.py` | **部署副本同步**：比对 `custom_nodes` 下的副本与指定提交的**提交态**（忽略 CRLF 行尾差异） | 全 `OK` | `python tools/sync_deploy_check.py <副本目录>` |
| `assert_default_exit.py` | **默认出口断言**：不设 `H3RELAY_NODE_API` 时必须走 V3（`NODE_CLASS_MAPPINGS is None` + `comfy_entrypoint` 可调用 + `WEB_DIRECTORY` 在）。为什么需要：2026-09-24 默认从 v1 切到 v3，而**转移前那套测试全都在验 v1 或强制 v3，没有任何一条断言锁住"默认"** | **4/4** | `COMFYUI_PATH=<根> python tools/assert_default_exit.py` |
| `ci_env_repro.py` | **在本机复现 CI 环境**跑任意校验脚本（CI 里宿主 `nodes` 导不进来 ⇒ 少一个 Upscale 节点，两套环境的期望数**不同**）。为什么需要：只跑本机**证明不了「CI 的数也在声明里」** —— 2026-09-29 就这么放过一次 CI 红 | 被跑脚本的退出码直传 | `python tools/ci_env_repro.py tests/test_v3_schema.py` |
| `en_sync.py` | **英文文档同步闸**：中文 = 源、`*_EN.md` = 派生物 ⇒ 节级 hash 比对。**源里新增的节默认必须译**（未登记即红；跳译要 `--omit` 写理由）。`--apply` 只重写**机械面**（版本号 / 文件头标记 / 索引缺行）、`--brief` 出增量翻译任务书交给译者或 LLM、`--ack` 暂缓**带期限**。**刻意不机翻**：机翻会静默改掉数字与标识符，而机检只扫中文源 ⇒ 英文版成监管盲区 | 退出码 `0`（不一致 `1`、**闸失效 `2`**） | `python tools/en_sync.py`（另有 `--status` / `--brief 7.3` / `--apply` / `--stamp`） |

**拼接 CLI（不是自检工具，给用户用）**

| 脚本 | 干什么 | 怎么跑 |
|---|---|---|
| `concat_segments.py` | 把 N 个段文件拼成一条成片（**与画布上 🧩 按钮同一份核心代码**）：画面流拷贝无损 + 音频逐段对齐 + 四项断言 + 退路 | `python tools/concat_segments.py s1.mp4 s2.mp4 -o film.mp4 [--audio aac256\|aac192\|lossless] [--crf 16] [--pcm p1.st - ...] [--json]` |

退出码 0 = 过四项断言。给 **API / 无头 / 批处理** 用户用，见 `docs/10` §7.4。

**打包器（不是自检工具，给分发用）**

| 脚本 | 干什么 | 怎么跑 |
|---|---|---|
| `make_minimal_bundle.py` | **产出最小可运行分发集**（**24 文件 / 796 KB，zip 263 KB**；⓪ 先跑英文文档同步闸——不过就不出包 ① 清单与**静态 import 推导**交叉校验 ② 写出 `dist/` ③ **自验**把产出当独立包加载：8 节点 / 默认 V3 / 前端在 / 异常上下文 / **零模块来自原仓**）：只带运行期 12 文件 + 前端 2 + 示例 3 + 元数据/法律/必读 7，**不带** `docs/` `tests/` `tools/` `.github/` | `python tools/make_minimal_bundle.py --zip` |

**发布器（不是自检工具，给发版用）**

| 脚本 | 干什么 | 怎么跑 |
|---|---|---|
| `release.py` | **两个渠道成对发布 + 交叉验证**：前置（干净工作树 / 版本四处 / `review_050` / `en_sync`）→ push → **等本轮 CI 真绿** → `comfy node validate` + 打包 + registry publish → 回头查两边是不是这一版 → 同步部署副本。规范正文 = [`RELEASING.md`](../RELEASING.md) | `COMFYUI_PATH=<根> python tools/release.py [--go\|--verify-only\|--set-token]` |

**声库采集器（不是自检工具，给多说话人长片用）**

| 脚本 | 干什么 | 怎么跑 |
|---|---|---|
| `voice_bank.py` | **从已渲染段采集「某角色的声锚」**，喂给 Copy Bridge 的可选输入 `voice_anchor`（跨说话人续接锁定音色，见 [`docs/07`](../docs/07-chain.md) §声锚）。有声检测（≥0.6 s）+ 峰值归一 0.9 + 声库 `voices.json`（**首个稳定锚锁定复用**，手动 `<角色名>.wav` 永远优先）。可选 **ASR 台词守卫**：装了 `funasr` 才启用（验台词 CER ≤ 0.35 + 逐字时间戳精确裁锚）；没装 ⇒ 打印警告后回退纯能量法，**零额外依赖**。另有 **`advise`**（一段内多人时算「该不该接锚 / 该接谁」——规则 = 锚给**缝上第一个开口说话的人**；同人续接不必接、换人才必须接）与 **`selftest`**（说话人解析规则的自检，可证伪） | `python tools/voice_bank.py collect <段产物.mp4> --name <角色名> --bank <声库目录> [--line "台词原文"]`（另有 `lookup` / `list` / `advise` / `selftest`） |

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
