# tools — 自检与合规取证

八个脚本：**七个自检/取证**（零 GPU、秒级，提 PR 前至少跑前四个）+ **一个拼接 CLI**（给不开画布的用户）。

| 脚本 | 判什么 | 期望 | 怎么跑 |
|---|---|---|---|
| `review_050.py` | **文档—代码一致性**：节点清单 / 参数表 / 断言数 / 版本号 / 示例图槽位 | **82/0** | `python tools/review_050.py` |
| `smoke_nodes.py` | **节点层功能冒烟**：续接七件真跑一遍（不是只看 INPUT_TYPES；🔍 放大节点要上游权重，不在冒烟内） | **15/0** | `python tools/smoke_nodes.py` |
| `check_ui_workflow.py` | **UI 格式工作流 JSON**：槽位下标、连线两端、类型相容、widgets_values 项数、**音画接线**（落盘节点的 `audio` 是否走了「裁重叠/音频缝」的裁后输出） | 问题合计 **0** 条 | `python tools/check_ui_workflow.py examples/minimal_relay_official.json` |
| `scan_expression_overlap.py` | 🔍 **合规取证**：与第三方包逐函数「表达层重合」扫描 | 人工判读（**只报数、不定性**） | `python tools/scan_expression_overlap.py <对方仓库路径>` |
| `verify_rewrite_equivalence.py` | 🔍 **合规取证**：三簇重写前后**逐位等价**差分验证（旧实现从 `git show <rev>` 捞，不手工转录） | **137/0** | `python tools/verify_rewrite_equivalence.py` |
| `sync_deploy_check.py` | **部署副本同步**：比对 `custom_nodes` 下的副本与指定提交的**提交态**（忽略 CRLF 行尾差异） | 全 `OK` | `python tools/sync_deploy_check.py <副本目录>` |
| `assert_default_exit.py` | **默认出口断言**：不设 `H3RELAY_NODE_API` 时必须走 V3（`NODE_CLASS_MAPPINGS is None` + `comfy_entrypoint` 可调用 + `WEB_DIRECTORY` 在）。为什么需要：2026-09-24 默认从 v1 切到 v3，而**转移前那套测试全都在验 v1 或强制 v3，没有任何一条断言锁住"默认"** | **3/3** | `COMFYUI_PATH=<根> python tools/assert_default_exit.py` |

**拼接 CLI（不是自检工具，给用户用）**

| 脚本 | 干什么 | 怎么跑 |
|---|---|---|
| `concat_segments.py` | 把 N 个段文件拼成一条成片（**与画布上 🧩 按钮同一份核心代码**）：画面流拷贝无损 + 音频逐段对齐 + 四项断言 + 退路 | `python tools/concat_segments.py s1.mp4 s2.mp4 -o film.mp4 [--audio aac256\|aac192\|lossless] [--crf 16] [--pcm p1.st - ...] [--json]` |

退出码 0 = 过四项断言。给 **API / 无头 / 批处理** 用户用，见 README §7.4。

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
