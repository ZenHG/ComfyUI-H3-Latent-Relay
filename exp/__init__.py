# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""E1' 实验子包 —— 时不变历史锚（TIHA, Time-Invariant History Anchor）。

本子包**默认关**：只有 run 目录下存在 ``_tiha.json`` 且 ``depth > 0`` 时才生效；
否则 ``h3_adapter.build_refs()`` 返回空列表，主干逐位不变。

命名沿革（**勿混**）：
  · 对外名统一用 **TIHA**；
  · ``E1'`` 只在历史沿革里指"实验层 E1 的重写版"——旧 E1 随提交 ``1c87aff`` 删除；
  · 它与 ``tests/test_experimental.py`` 的历史编号 ``E1.1``~``E1.14`` **不是同一套编号**
    （那套是实验层的测试编号，已随 E1 归档）。

模块边界：
  · ``history_anchor.py``  —— 算法核心，**零本包依赖**（只 import torch），可独立单测；
  · ``h3_adapter.py``      —— 适配层，负责读 run 配置 / 找历史段 / 调核心；
  · ``precheck_tiha.py``   —— 提交前预检（离线，零 GPU）；
  · ``test_history_anchor.py`` —— 零 GPU 单测；
  · ``gen_e1long_prompts.py``  —— 长链测试出词生成器。
"""
