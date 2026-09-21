# 贡献指南

感谢你考虑为本项目贡献代码。

## 开发环境
- Python ≥ 3.10，需要 `torch` 与 `safetensors`。
- 把本目录放进 `ComfyUI/custom_nodes/` 即可被加载；纯算法层改动也可脱离 ComfyUI 单测。

## 离线自测（提 PR 前必须跑通）
```bash
python tests/test_relay_core.py
```
- 零 GPU、不加载模型、秒级完成。会自动上溯定位 ComfyUI 根目录；装在别处时用
  `COMFYUI_PATH=/path/to/ComfyUI python tests/test_relay_core.py` 指定。
  注意：脚本本身需要**能 import 到 ComfyUI**（`comfy.nested_tensor` / `node_helpers` /
  `folder_paths`）——`relay_core` 模块可独立复用，但这份打包测试**不能**脱离 ComfyUI 跑。
- 覆盖二十三个方面（**实测执行 286 项断言**），是本包正确性的主要保障。
  报告里请贴实际执行数，不要按源码行数统计（互斥分支不会同时执行）。
- **改动后必须同步这几处计数与清单**（v0.4.2 起列为纪律，`tools/review_050.py` 会逐项核）：
  `__init__.py` 头注释节点清单 · 本文件的「方面数/断言数」· `tests/test_relay_core.py`
  头注释覆盖清单 · 版本号三处（`__init__.py` / `pyproject.toml` / `CHANGES.md` 顶部）·
  `README.md` 节点表与参数表 · `requirements.txt`。

## 代码纪律

- 🔴 **铁律一：UI 与 API 必须同一套实现，且必须基于节点**（2026-09-21 定）。

  本包的用户里**多数是用 ComfyUI 画布手动跑的**（改段号 → 点 Queue → 改 prompt → 再点 Queue），
  只有少数走 API 提交图 JSON。所以"只在脚本里生效"的改动，等于对多数用户**不存在**。

  | 允许 | 不允许 |
  |---|---|
  | 做成**节点的输入 / 输出**（widget 或连线） | 把功能藏在**外部脚本**里（`ffmpeg` 后处理、Python 换音轨、旁路 remux） |
  | 画布手点与 API 提交**走同一段 `relay_core` 代码** | 只有 API 路径上才有的分支（"脚本里有、节点里没有"，或反过来） |
  | 效果在节点 `report` / 画布报错里**可见可自证** | 只有写进日志、用户永远看不到的"隐式行为" |

  **PR 自查三条**（缺一即不算完成）：
  1. 这个功能能不能**只靠画布连线 + widget** 用上？
  2. 手动点 Queue 与 API 提交，是否**落到同一段实现**（而不是两条路各写一遍）？
  3. 效果是否在**节点 report / 画布报错**里可见（用户不读源码也能自证）？

  ⚠️ 真实反例：`--across 0.25` 这类"组装层 crossfade"只在私有脚本里改 ⇒
  开源用户一点都拿不到；而节点里**同一病因**的路径（`joined` 的旧缩短语义）默认开着却没人管。
  `tools/review_050.py` L10 会机检本条目是否还在。

- 🔴 **铁律二：音画同步不靠组装层兜**。段的音频长度 = 视频长度是**节点侧契约**
  （`裁重叠` 音视频同裁 + `音频缝` 长度守恒）；任何会**缩短时间轴**的操作
  （重叠交叉淡变 = overlap-add）都不得出现在默认档上，且不得静默生效 —— 必须 `raise`。
  对应断言：`tests/test_relay_core.py` 第 23 组。

- **硬错误必须 raise，绝不静默降级**（如帧数不在网格上、分辨率不匹配、段号与取源矛盾）。
- `relay_core.py` 是纯算法层，**不得 import 任何 ComfyUI 模块**，以保证可独立单测与复用。
- 新增 / 修改节点时，请同步更新 `README.md`（节点表 / 参数表 / 排障表）与 `CHANGES.md`。

## 许可（提 PR 前必读）

- 提交 PR 即表示：你对你提交的代码拥有处分权，并同意以 **MIT** 许可随本仓库一同发布。
- 🔴 **不得引入 GPL / AGPL / LGPL 或其它 copyleft 项目的代码、注释结构或逐字表达。**
  本仓库主张 MIT，而"机制不受版权保护、表达受保护"——照抄一个函数体哪怕改了函数名也算。
  本仓库曾因此**整体重写过一次**，见 [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md) §一·B。
- 参考第三方实现时：只取**机制**，自己写**表达**；并在 NOTICES 的 §一 表里补一行出处。
- ⚠️ 运行时宿主 **ComfyUI 是 GPL-3.0**（我们 import 它、但不复制它），见 NOTICES §一·C。
  新增 `import comfy.*` 之前先想清楚这一步的耦合代价。

## 提交信息
- 建议清晰说明「改了什么 / 为什么」，并关联相关 Issue。中英文均可。
- 提交请使用**非个人敏感**的 git 身份（例如 GitHub 提供的 no-reply 邮箱），避免把私人邮箱写进公开历史。
