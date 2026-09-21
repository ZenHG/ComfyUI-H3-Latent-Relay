---
name: Bug 报告
about: 跑不通、报错、出坏片
title: "[BUG] "
labels: bug
---

## 环境

- 本包版本（`__init__.py` 里的 `__version__`）：
- ComfyUI 版本 / 提交日期：
- Python / torch 版本：
- OS：

## 现象

（报错信息、日志最后 20 行、或者"片子哪里不对"）

```
把日志贴在这里
```

## 复现

1. 用的哪张图（如果是示例图，说明改了哪些参数）
2. 段号 / `run_id` / `mask_mode` / `settle_frames`
3. 第几段开始出问题

## 已排除

- [ ] 已重启 ComfyUI 后端（不是只刷新浏览器）
- [ ] 已跑 `python tests/test_relay_core.py`（期望 300/0）

> ⚠️ 请不要粘贴本机绝对路径、模型路径或任何凭证（见 SECURITY.md）。
