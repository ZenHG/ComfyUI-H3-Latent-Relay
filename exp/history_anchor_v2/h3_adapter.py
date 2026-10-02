# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""E1'（TIHA）↔ H3 Latent Relay 的**适配层**。

职责边界（刻意做窄）：
  · 读 run 目录下的 ``_tiha.json``；
  · 按「stage-2 / -3 / …」找历史段并加载；
  · 调 ``history_anchor.build_history_anchor_refs()``；
  · 把结果包成宿主认的 ``minimax_refs`` 块。

**不含任何算法** —— 算法全在 ``history_anchor.py``（那份零本包依赖、可独立单测）。

设计要点：**路径推导不在这里重复实现**。
本模块通过调用方注入的 ``stage_path_fn`` 拿到段文件路径 ⇒ 与 ``nodes.py`` 的
``_stage_path()`` **是同一份实现**（避免《规范》§二·D「同类实现不止一处、判定会打架」）。
同理 ``run`` 目录也用 ``dirname(stage_path_fn(run_id, 0))`` 反推，不自己拼。

为什么由配置文件（而非节点入参）驱动：见 ``nodes.py`` 调用点上的注记。
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, List, Optional

from . import history_anchor as TIHA

#: run 目录下的开关文件名。**文件不存在 = 关**（主干逐位不变）
CONFIG_NAME = "_tiha.json"

#: ``_tiha.json`` 里允许出现的键 —— **别名，不在这里重定义**（2026-10-02 合并）。
#: 唯一真相源 = ``history_anchor.KNOWN_CONFIG_KEYS``（理由见那边的注记）：
#: 白名单与 ``precheck_tiha`` 各写一份时，「预检预测的生产行为」会与生产分叉。
KNOWN_KEYS = TIHA.KNOWN_CONFIG_KEYS

#: 历史窗口：从 ``stage_index - 2`` 起往回取（与旧 E1 的取段规则一致）
FIRST_BACK = 2


def config_path(stage_path_fn: Callable[[str, int], str], run_id: str) -> str:
    """按 run 目录推出 ``_tiha.json`` 的路径（不自己拼目录名）。"""
    return os.path.join(os.path.dirname(stage_path_fn(run_id, 0)), CONFIG_NAME)


def load_config(path: str) -> Optional[dict]:
    """读配置。**文件不存在 ⇒ None（= 关）**；存在但坏 ⇒ raise（不静默降级）。"""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception as exc:                                   # noqa: BLE001
        raise RuntimeError("E1'（TIHA）配置读取失败：%s ⇒ %s" % (path, exc)) from exc
    if not isinstance(raw, dict):
        raise RuntimeError(
            "E1'（TIHA）配置顶层必须是对象，得到 %s：%s" % (type(raw).__name__, path))
    return raw


def build_refs(
    run_id: str,
    stage_index: int,
    ref_anchor_stage: int,
    *,
    stage_path_fn: Callable[[str, int], str],
    load_fn: Callable[[str], Any],
    video_fn: Callable[[Any], Any],
    anchor_latent: Any = None,
    depth: Optional[int] = None,
    log: Optional[Callable[[str], None]] = None,
) -> List[dict]:
    """E1' 主入口（被 ``H3RelayCopyBridge`` 调用）。

    返回 ``minimax_refs`` 块列表；**默认关**（无配置文件 / ``depth<=0`` / 历史段 <2）⇒ 返回 ``[]``。

    ``anchor_latent``：调用方**已经加载过**的外观锚 latent。
    传进来就不重复读盘（《规范》§二·A 同源：同类动作重复一次就是白读 6 MB）。
    """
    _log = log or (lambda _m: None)
    if not (run_id or "").strip():
        return []
    raw = load_config(config_path(stage_path_fn, run_id))
    if raw is None:
        return []                                   # 关（主干逐位不变）

    # 🔴 未知键必须出声（2026-10-02 审核新增）。
    #    原实现用 `if k in raw and hasattr(cfg, k)` 白名单赋值 ⇒ **多写的键被静默忽略**，
    #    拼错一个字母（如 `window_frame` 少个 s）就退回默认值，而日志里没有任何迹象 ——
    #    与《规范》§二·A「宿主忽略未声明的输入键」是同一形态。
    #    这里**只警告不 raise**：白名单会随版本增长，硬拒会让老配置在新版本上直接报错。
    unknown = sorted(k for k in raw if k not in KNOWN_KEYS)
    if unknown:
        _log("E1'：⚠ _tiha.json 含**未知键** %s ⇒ 已忽略（本次跑的是这些键的**默认值**）。"
             "合法键见 h3_adapter.KNOWN_KEYS。" % unknown)

    want_depth = int(raw.get("depth", 0)) if depth is None else int(depth)
    if want_depth <= 0:
        return []

    # —— 找历史段（先按段号筛掉「与外观锚同源」的，**再**读盘：避免白读 6 MB）——
    a_idx = int(ref_anchor_stage)
    # 🔴 「与锚同源」的判定**不在本文件实现**（2026-10-02 合并）：原先这里自己写了一遍
    #    `idx == a_idx`，而 `history_anchor.filter_duplicate_sources` 里还有一份
    #    ⇒ 同一规则两份实现（《规范》§二·D）。现在统一走唯一的 `drop_anchor_stage`。
    cand = [int(stage_index) - k for k in range(FIRST_BACK, FIRST_BACK + want_depth)]
    cand, dropped = TIHA.drop_anchor_stage([i for i in cand if i >= 0], a_idx)
    if dropped:
        _log("E1'：与外观锚同源 ⇒ 不读不用：%s" % dropped)
    picks, missing = [], []
    for idx in cand:                                # 先筛后读 ⇒ 省掉白读的 6 MB（P1-2）
        p = stage_path_fn(run_id, idx)
        if os.path.isfile(p):
            picks.append(idx)
        else:
            missing.append(idx)
    if missing:
        _log("E1'：缺段落 %s（未落盘）⇒ 该级不参与打分。" % missing)

    if len(picks) < 2:
        # 跨段共识需要 ≥2 段参照；1 段只能退化成「多加一个锚」，那不是多尺度。
        _log("E1'：可用历史段 %d < 2（跨段共识无参照）⇒ 不出锚块。" % len(picks))
        return []

    # 🔴 读盘失败必须带**文件路径**再抛（2026-10-02）。`os.path.isfile` 只能证明"文件在"，
    #    证不了"写完了 / 没坏"；半写文件会让 safetensors 抛一句与"哪一段"无关的报错，
    #    而这条异常会直接冒泡成"整段渲染失败"（《规范》§二·E：报错节点 ≠ 出错位置）。
    #    这里只**补上下文**，不吞异常 —— 吞掉就变成"以为开着 TIHA、其实没开"。
    hist = []
    for i in picks:
        p = stage_path_fn(run_id, i)
        try:
            hist.append(video_fn(load_fn(p)))
        except Exception as exc:                    # noqa: BLE001
            raise RuntimeError(
                "E1'（TIHA）读历史段 stage%d 失败：%s ⇒ %s（文件可能半写/损坏；"
                "删掉该段重跑，或把 depth 调小以避开这一段）"
                % (i, p, exc)) from exc

    anchor_v = video_fn(anchor_latent) if anchor_latent is not None else None

    cfg = TIHA.TIHAConfig(
        depth=want_depth,
        window_frames=int(raw.get("window_frames", TIHA.TIHAConfig.window_frames)),
        spatial_scale=int(raw.get("spatial_scale", TIHA.TIHAConfig.spatial_scale)),
        max_refs=int(raw.get("max_refs", want_depth)),
    )
    for k in KNOWN_KEYS:
        if k in raw and hasattr(cfg, k):
            setattr(cfg, k, raw[k])

    refs = TIHA.build_history_anchor_refs(
        hist, cfg,
        anchor_video=anchor_v,
        # 🔴 把真实段号交给核心：日志与留痕要用 stage 号，不能用历史列表下标
        #    （2026-09-28 置信度审核抓到的 P0-4：下标被当成段号报出去过）
        stage_labels=picks,
        log=_log,
    )
    if refs:
        _log("E1'：已生成 %d 个时不变历史锚块（来源 stage %s）"
             % (len(refs), [r.get("_exp_stage") for r in refs]))
    return refs
