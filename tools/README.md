# tools — 自检与合规取证

五个脚本，**零 GPU、秒级**，提 PR 前至少跑前三个。

| 脚本 | 判什么 | 期望 | 怎么跑 |
|---|---|---|---|
| `review_050.py` | **文档—代码一致性**：节点清单 / 参数表 / 断言数 / 版本号 / 示例图槽位 | **80/0** | `python tools/review_050.py` |
| `smoke_nodes.py` | **节点层功能冒烟**：7 个节点真跑一遍（不是只看 INPUT_TYPES） | **15/0** | `python tools/smoke_nodes.py` |
| `check_ui_workflow.py` | **UI 格式工作流 JSON**：槽位下标、连线两端、类型相容、widgets_values 项数、**音画接线**（落盘节点的 `audio` 是否走了「裁重叠/音频缝」的裁后输出） | 问题合计 **0** 条 | `python tools/check_ui_workflow.py examples/minimal_relay_official.json` |
| `scan_expression_overlap.py` | 🔍 **合规取证**：与第三方包逐函数「表达层重合」扫描 | 人工判读（**只报数、不定性**） | `python tools/scan_expression_overlap.py <对方仓库路径>` |
| `verify_rewrite_equivalence.py` | 🔍 **合规取证**：三簇重写前后**逐位等价**差分验证（旧实现从 `git show <rev>` 捞，不手工转录） | **137/0** | `python tools/verify_rewrite_equivalence.py` |
| `sync_deploy_check.py` | **部署副本同步**：比对 `custom_nodes` 下的副本与指定提交的**提交态**（忽略 CRLF 行尾差异） | 全 `OK` | `python tools/sync_deploy_check.py <副本目录>` |

前三个失败时会以非零退出码退出（CI 直接可用），见 `.github/workflows/ci.yml`。

## 两个取证工具为什么留在仓库里

`scan_expression_overlap.py` 与 `verify_rewrite_equivalence.py` 是一次性核查的产物，
但它们是「三簇重写已消除与 `ComfyUI-H3-Motion-Context`（GPL-3.0）的表达层重合」
这一结论的**可复验证据链**。删掉它们 = 结论失去可验证性。
背景与核验方法见 [`THIRD-PARTY-NOTICES.md`](../THIRD-PARTY-NOTICES.md) §一·B。

⚠️ 跑 `scan_expression_overlap.py` 需要对方仓库路径；**不要把对方代码复制进本仓库**，
工具只在进程内读取比对。若对方包已从本机移除，可用任意检出的副本路径（Windows 绝对路径，
例如 `I:/…/ComfyUI-H3-Motion-Context`）。

## 判据纪律

- **只报数、不定性**：重合率高 ≠ 派生。短小数学循环、ComfyUI 强制 API、Python 惯用法都会天然重合。
  定性必须看具体行与上下文，**由人判定**。
- **判据不能与被判对象共用同一个函数**（曾因此出现过恒真的假绿）。
- 改判据后要**反向自证**：故意做坏的实现必须被抓到。
