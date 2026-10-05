# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""VA（Voice Accumulate）↔ H3 Latent Relay 的**适配层**。

职责边界（刻意做窄，与 `exp/history_anchor_v2/h3_adapter.py` 同款）：
  · 读 run 目录下的 `_va.json`（**段号 → 说话人开口顺序**）；
  · 按「本段之前的历史段」找段并加载**音频 latent**；
  · 调 `voice_accum.accumulate_for()`；
  · 把结果包成宿主认的 `minimax_refs` 音频块。

**不含任何算法** —— 算法全在 `voice_accum.py`（那份零本包依赖、可独立单测）。

🔴 **原料用 `stage_%05d.safetensors`（AV latent），不用 `audio_%05d.safetensors`（波形）**：
   后者是 **32 kHz 解码波形**，要用它就得过一遍音频 VAE（`_encode_ref_audio`）⇒ 节点要多一个
   `audio_vae` 入参（**schema 变更**）。而 `stage_*` 里的音频流本来就是 **40 Hz latent**
   （`CORE.audio_from_latent` 直接取）⇒ **零 VAE、零 schema 变更、零新依赖**。
   代价：读盘量大一些（stage 文件含视频流）。

路径推导**不在这里重复实现** —— 通过调用方注入的 `stage_path_fn`（与 `nodes.py` 的
`_stage_path()` 是同一份实现）。
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional, Tuple

import torch

from . import voice_accum as VA

#: run 目录下的开关文件名。**文件不存在 = 关**（主干逐位不变）
CONFIG_NAME = "_va.json"


def config_path(stage_path_fn: Callable[[str, int], str], run_id: str) -> str:
    """按 run 目录推出 `_va.json` 的路径（不自己拼目录名）。"""
    return os.path.join(os.path.dirname(stage_path_fn(run_id, 0)), CONFIG_NAME)


def build_audio(
    run_id: str,
    stage_index: int,
    *,
    stage_path_fn: Callable[[str, int], str],
    load_fn: Callable[[str], Any],
    audio_fn: Callable[[Any], Any],
    tail_latent: Optional[Any] = None,
    pin_steps: int = 0,
    max_steps: Optional[int] = None,
    has_anchor: bool = False,
    log: Optional[Callable[[str], None]] = None,
) -> Tuple[Optional[Any], List[str]]:
    """VA 主入口（被 `H3RelayCopyBridge` 调用）。

    返回 ``(参考音频张量 或 None, notes)``。**默认关**（无 `_va.json` / 台账缺本段 / 收不到素材）
    ⇒ 返回 ``(None, notes)`` ⇒ 主干逐位不变。

    🔴 **`has_anchor`（声锚已接）时默认仍然跳过**（`config.with_anchor=false`）：
       声锚**同时换 ref 与 pin**（等于伪造"上段尾"，音色被**硬**对齐），VA 只换 ref。
       两者是**同一问题的两种素材来源**，可以叠加成 `[历史素材…][声锚尾窗]`，
       但叠加组合**零次实测** ⇒ 不许进默认路径。要试请填 `config.with_anchor = true`。
       判断收在本函数（不放在 `nodes.py`）⇒ **配置键与开关只在一处读**，调用方不必重复实现。
       ⚠️ 代价：声锚已接但无 `_va.json` 时，本函数会多读一次 `_va.json`（= 1 次 isfile）。

    🔴 **必须给 `tail_latent`（= 桥自己那一段 `plan.audio_ref["audio_latent"]`）**：
       旧口径下参考与**钉住前缀**同源（都取上段尾），`relay_core.py:4008-4012` 自己写着
       「两处读的都是"上段尾"，**只改一处会打架**」。VA 只替换 `audio_ref` ⇒ 不变式被打破
       ⇒ 软条件的信息进不去、只剩扰动（实测两臂余弦 0.2395）。本函数因此**以它结尾**
       （见 `voice_accum.compose_ref`）。缺它 ⇒ 会在 notes 里报红并退回旧口径。
       ✅ 声锚路径下它**已经是声锚尾窗** ⇒ 叠加时同源不变式**自动**保持，无需额外代码。

    🔴 **为什么返回「一段音频」而不是「N 个块」**（2026-10-04 实测，本仓作者 选 A）：
       先前版本每人追加 1 个 `minimax_refs` 音频块（共 1 + 3 = 4 块）。三臂 A/B 实测：
       **开/关 VA 两臂的音频余弦只有 0.0497**（93% 样本不同），而同配置重跑**逐位相同**
       ⇒ 差异可归因 VA，且是**打乱生成**而非改善音色。
       根因：这些块**没有 `<Audio j>` 标签**（`append=True` 事后追加）⇒ 模型拿到 4 块却
       **无法对应谁是谁** ⇒ 条件冲突。
       ⇒ 改为**并入桥那 1 个块**：把各角色的累积片段拼成**一段**，替换 `plan.audio_ref` 的内容。
         额度仍是 **1**，与已实测有效的 `audio_ref_seconds`（一个槽装更长音频）同路。
    """
    _log = log or (lambda _m: None)
    notes: List[str] = []
    path = config_path(stage_path_fn, run_id)
    raw = VA.load_config(path)
    if raw is None:
        return None, notes                            # 默认关：唯一的额外动作 = 1 次 isfile

    cfg, stages, stage_lines_cfg, stage_chars_cfg, cnotes = VA.parse_config(raw)
    notes.extend(cnotes)

    # 声锚已接时的取舍 —— 判据本身是 `voice_accum.anchor_gate`（纯函数、可独立单测），
    # 适配层只做"取结果 + 说话"。
    _skip, _gnote = VA.anchor_gate(has_anchor, cfg)
    if _gnote:
        notes.append(_gnote)
    if _skip:
        _log("VA（按人累积音色）：已接「声锚」且未开 `with_anchor` ⇒ **跳过**（默认）")
        return None, notes

    cur = str(int(stage_index))
    order = stages.get(cur)
    if not order:
        notes.append("台账 `%s` 里没有本段（段号 %s）⇒ 返空（不猜）" % (CONFIG_NAME, cur))
        return None, notes
    notes.append("VA：本段开口顺序 = %s（共 %d 人；默认只给**缝上那个人**一块，"
                 "上限由 `config.max_speakers` 定，现为 %d）"
                 % ("→".join(order), len(set(order)), int(cfg["max_speakers"])))

    # ① **基准段** = 该角色**第一次说话**的段 —— 从**全台账**算（不限于回看窗口）。
    #    本仓作者 2026-10-04：以第一次说话那一段为基准（抗漂移）；后续段按音色接近度补。
    first_stages: Dict[str, int] = {}
    for sp in dict.fromkeys(order):
        ks = [int(k) for k, v in stages.items() if sp in v and int(k) < int(stage_index)]
        if ks:
            first_stages[sp] = min(ks)

    # ② 要加载的段 = 回看窗口 ∪ 各角色的基准段（基准段可能在窗口之外）。
    back = int(cfg["max_stages"])
    lo = max(0, int(stage_index) - back)
    need = set(range(lo, int(stage_index))) | set(first_stages.values())
    stage_orders: Dict[int, List[str]] = {}
    stage_audio: Dict[int, Any] = {}
    for k in sorted(need):
        ok = stages.get(str(k))
        if not ok:
            continue
        p = stage_path_fn(run_id, k)
        if not os.path.isfile(p):
            notes.append("段 %d：段文件不存在 ⇒ 跳过（%s）" % (k, os.path.basename(p)))
            continue
        try:
            lat = load_fn(p)
            stage_audio[k] = audio_fn(lat)
        except Exception as exc:                      # noqa: BLE001
            notes.append("段 %d：音频读取失败（%s: %s）⇒ 跳过" % (k, type(exc).__name__, exc))
            continue
        stage_orders[k] = list(ok)

    blocks, anotes = VA.accumulate_all(
        order, stage_orders, stage_audio,
        current_stage=int(stage_index),
        first_stages=first_stages,
        # 档 2（逐句时间）优先；档 1 退能量切分（真实素材上不可靠，会返空）
        stage_lines={int(k): v for k, v in stage_lines_cfg.items() if v},
        stage_chars={int(k): v for k, v in stage_chars_cfg.items() if v},
        strict=bool(cfg["strict"]),
        snap_s=float(cfg["snap_s"]),
        max_seconds=float(cfg["max_seconds"]),
        max_total_seconds=float(cfg["max_total_seconds"]),
        max_stages=back,
        min_span_s=float(cfg["min_span_s"]),
        gap_s=float(cfg["gap_s"]),
        max_speakers=int(cfg["max_speakers"]),
        prior_split=bool(cfg["prior_split"]),
        sim_noise=float(cfg["sim_noise"]),
    )
    notes.extend(anotes)
    if not blocks:
        return None, notes

    # 历史素材按**本段开口顺序**（顺序 = 缝上那个人在前，与他出场次序一致）
    # 🔴 **传「片」的列表，不传拼好的张量**：`compose_ref` 的"预算不够就**整片丢**"是按
    #    **片**做的 —— 先拼成一段再传，就等于只剩一片，丢的时候只能全丢（粒度丢掉了）。
    pieces = [blk for _who, blk in blocks]
    notes.append("VA：历史素材 **%d 片**（合计 %d 步 ≈ %.2f s）"
                 % (len(pieces), sum(int(p.shape[-1]) for p in pieces),
                    sum(int(p.shape[-1]) for p in pieces) / VA.AUDIO_HZ))

    # 🔴 **同源不变式**：参考必须以「上一段音频尾窗」**结尾**（= pin 的来源）——
    #    否则硬约束（pin）与软条件（ref）打架，见 `voice_accum.compose_ref` 的注释。
    # 🔵 声锚路径下让**声锚尾窗完整保留**（2026-10-06 零 GPU 探测发现的真问题）：
    #    `compose_ref` 的默认预算口径是「tail 让位给历史」⇒ 叠加时会把声锚尾窗
    #    从 ~5 s 压到 ~2 s（实测：200 步 → 85 步）。那等于**用更短更杂的历史素材
    #    换掉更长更纯的声锚** ⇒ 叠加就没有意义了。
    #    ⇒ 声锚是**用户显式给的**素材（最可信）⇒ 让它优先占满：tail 全留，
    #      历史素材吃剩余额度（总长仍受 `max_steps` 封顶）。
    #    ⚠️ 只作用于声锚路径（`has_anchor`）；**无声锚时 `pin_steps` 原样**
    #      ⇒ 已实测的 VA 单开路径（臂B）**逐位不变**。
    # 🔵 用 `torch.is_tensor` 显式判类型，**不用宽泛的 try/except**（2026-10-06 第 2 轮审核）：
    #    非张量（`None` / 列表 / 自定义对象）直接不进分支 ⇒ 零异常、语义一眼可见。
    _pin_eff = int(pin_steps)
    if has_anchor and torch.is_tensor(tail_latent):
        _pin_eff = max(_pin_eff, int(tail_latent.shape[-1]))
    merged, cinfo = VA.compose_ref(
        pieces, tail_latent,
        max_steps=max_steps,
        pin_steps=_pin_eff,
        history_budget=int(round(float(cfg["max_total_seconds"]) * VA.AUDIO_HZ)))
    notes.append("VA：%s" % cinfo["note"])
    if cinfo["dropped"]:
        notes.append("VA：⚠ **%d 片历史素材整片丢弃**（预算装不下）—— "
                     "**绝不切半片**（切点会落在音节中间）"
                     % cinfo["dropped"])
    if tail_latent is None:
        notes.append("VA：🔴 调用方没给 `tail_latent` ⇒ **无法保证与钉住前缀同源**。"
                     "这条是 2026-10-05 认定的首疑根因（R2）⇒ 请检查节点侧的传参。")
    _log("VA（按人累积音色）：历史 %d 步 + 音频尾 %d 步 ⇒ 参考 %d 步"
         % (cinfo["hist_steps"], cinfo["tail_steps"], int(merged.shape[-1])))
    return merged, notes
