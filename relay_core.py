# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Relay-Kit contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""H3 Relay Kit · 核心算法层（纯张量，零 GPU、零模型、可离线单测）

【这是什么】
MiniMax-H3 多段续接的 **latent 桥**：把上一段的 AV latent 切出尾段，
以 `minimax_keyframes`（多块，按 `resolved_frame_index` 排位）
+ `minimax_refs`（音频）注入本段的 conditioning。

【与像素续接的区别（本包存在的理由）】
  像素续接：上一段 mp4 → 解码帧 → VAE 重编码 → 单块 keyframe @frame0
            —— 多一次 VAE 往返，有量化损失，且曝光可能漂移。
  latent 桥：上一段 AV latent → 直接切尾段 → 多块 keyframe 按位置
            —— 零重编码，与采样时的 latent 逐位同源。

二者走的是**同一个 ComfyUI 原生协议**（`minimax_keyframes`），
故可互换；本包提供后者，并保持与前者完全兼容的键结构。

【协议出处（本机实测，非推测）】
  comfy/ldm/minimax/model.py:376
      cond_t = cursor + FRAME_RESCALE * kf["resolved_frame_index"]
  comfy/model_base.py:2186-2196
      keyframes = kwargs.get("minimax_keyframes")   → payload["keyframes"]
      refs      = kwargs.get("minimax_refs")        → payload["refs"]
  → 原生支持任意位置的 keyframe 锚，无需任何 monkey patch。

【帧 / latent 网格】
  H3 VAE 的时序跨度为 (1,4,4,4,4)：每 5 个 latent token 覆盖 17 像素帧。
  因此**只有落在网格上的窗口**才能被整步切出，合法窗口（GUIDE_RUNS）：
      1, 5, 22, 39, 56, 73, 90, 107, 124 ...
  22 帧 = 7 个 latent token；39 帧 = 12 个 token。
  不在网格上 → raise（而不是悄悄挪一格，那会渲染出位移的接缝）。
"""

from __future__ import annotations

import itertools
import json
import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import torch


# ---------------------------------------------------------------- 网格常量
# 与 H3 VAE 的时序跨度一致。改这里等于改 H3 的 VAE，必须同步。
FRAME_PER_TOKEN: Tuple[int, ...] = (1, 4, 4, 4, 4)
FPS: float = 24.0
FRAME_RESCALE: float = 5.0 / 3.0   # 像素帧 → 音频 latent 步的换算
AUDIO_HZ: float = 40.0             # 音频 latent 的采样率（步/秒）

# 合法窗口，降序。必须与 FRAME_PER_TOKEN 自洽（见 self_check）。
GUIDE_RUNS: Tuple[int, ...] = (124, 107, 90, 73, 56, 39, 22, 5, 1)

# Apt_Preset 在 latent 里留下的导出尾段键（有则优先复用，避免二次切片）
KEY_EXPORT_TAIL_VIDEO = "apt_h3_export_tail_latent"
KEY_EXPORT_TAIL_AUDIO = "apt_h3_export_tail_audio_latent"
KEY_EXPORT_FRAMES = "apt_h3_export_context_frames"


# ---------------------------------------------------------------- 网格工具
def pixel_frames(latent_t: int) -> int:
    """latent token 数 → 覆盖的像素帧数。"""
    return sum(FRAME_PER_TOKEN[k % 5] for k in range(int(latent_t)))


def step_offsets(latent_t: int) -> List[int]:
    """每个 latent token 的**起始像素帧位置**（首位恒为 0）。

    与 ``pixel_frames`` 是同一张表的两种读法：``pixel_frames(n)`` 给总量，
    本函数给每个 token 的入口。写成**排他前缀和** —— 第 i 个位置 = 它前面
    所有 token 的跨度之和。``cycle`` 让周期由 ``FRAME_PER_TOKEN`` 自己决定。
    """
    n = max(int(latent_t), 0)
    spans = itertools.islice(itertools.cycle(FRAME_PER_TOKEN), n)
    return [0, *itertools.accumulate(spans)][:n]


def steps_for_frames(n: int) -> Optional[int]:
    """像素帧数 → 整步的 latent token 数；不在网格上返回 None。

    跨度按 ``1,4,4,4,4`` 循环，所以**可达的帧数就是这些跨度的前缀和**：
    5 → 2 步、22 → 7 步、39 → 12 步、56 → 17 步。落在两格之间的值
    （4、6、7…）无解。0 帧视作 0 步。

    ⚠️ 这里**不硬编码周期**（虽然 5 步和为 17，可写成闭式解、再快一个数量级）：
    闭式解在上游改 ``FRAME_PER_TOKEN`` 时会**静默算错**，而 ``layout_contract``
    只对照常量、查不出公式里的硬编码。``cycle`` 跟着常量走，网格变了自动跟着变。
    """
    n = int(n)
    for steps, total in enumerate(
            itertools.accumulate(itertools.islice(
                itertools.cycle(FRAME_PER_TOKEN), max(n, 0))),
            start=1):
        if total >= n:
            return steps if total == n else None
    return 0 if n == 0 else None


def snap_guide_run(n: int) -> int:
    """向下吸附到最近的合法窗口（供 UI 侧提示用；核心路径仍会硬校验）。"""
    n = int(n)
    for g in GUIDE_RUNS:
        if g <= n:
            return g
    return 0


def self_check() -> List[str]:
    """自洽性检查：GUIDE_RUNS 必须全部落在网格上。返回问题列表（空=通过）。"""
    bad = []
    for g in GUIDE_RUNS:
        if steps_for_frames(g) is None:
            bad.append(f"GUIDE_RUNS 含非网格值 {g}（pixel_frames 无法整步覆盖）")
    return bad


# ---------------------------------------------------------------- latent 取流
def streams_from_latent(latent: Any) -> List[torch.Tensor]:
    """从 LATENT 取 [video, audio, ...]。

    支持三种形态：
      - ``NestedTensor``（H3 AV 联合 latent，本产线常态）→ 按 ``.tensors`` 拆流
      - list/tuple（已拆开的流）
      - 单个 ``torch.Tensor``（video-only latent）→ 当作**一条**流

    ⚠️ 不要用 ``hasattr(samples, "unbind")`` 来判定"是不是 NestedTensor" ——
    **任何 torch.Tensor 都有 unbind**，普通 ``[B,C,T,H,W]`` 会被沿 batch 维拆开，
    B=1 时碰巧看不出错、B>1 时静默产出垃圾流。故这里显式按类型分支。
    """
    if latent is None:
        raise ValueError("latent 为空：续接需要一段真实的 H3 AV latent。")
    samples = latent["samples"] if isinstance(latent, dict) else latent
    nested = getattr(samples, "tensors", None)
    if nested is not None:
        parts = list(nested)
    elif isinstance(samples, (tuple, list)):
        parts = list(samples)
    elif torch.is_tensor(samples):
        parts = [samples]
    else:
        raise ValueError(
            "期望 H3 的 AV latent（nested 视频/音频对），得到 %r。\n"
            "    接续接错的常见原因：把 video-only latent 接到了 context_latent。" % type(samples)
        )
    parts = [t for t in parts if torch.is_tensor(t)]
    if not parts:
        raise ValueError("AV latent 里没有可用的张量流。")
    return parts


def video_from_latent(latent: Any) -> torch.Tensor:
    """取视频流，规范成 [B,C,T,H,W]。"""
    v = streams_from_latent(latent)[0]
    if v.ndim == 4:
        v = v.unsqueeze(0)
    if v.ndim != 5:
        raise ValueError(
            "期望视频 latent 形状 [B,C,T,H,W]，得到 %s。" % (tuple(v.shape),)
        )
    return v


def audio_from_latent(latent: Any) -> torch.Tensor:
    """取音频流，规范成 [B,C,2,T]。"""
    parts = streams_from_latent(latent)
    if len(parts) < 2:
        raise ValueError(
            "context_latent 没有音频流。\n"
            "    续接需要采样器的 AV 输出（视频+音频同源），不是纯视频 latent。"
        )
    a = parts[1]
    if a.ndim == 3:
        a = a.unsqueeze(0)
    if a.ndim != 4:
        raise ValueError("期望音频 latent 形状 [B,C,2,T]，得到 %s。" % (tuple(a.shape),))
    return a


# ---------------------------------------------------------------- 尾段切片
def video_tail_from_latent(
    latent: Any, frames: int
) -> Tuple[List[torch.Tensor], List[int], int]:
    """从 AV latent 切出 ``frames`` 帧的**视频尾段**，切成逐 token 的块。

    返回 ``(blocks, offsets, covered)``：
      - ``blocks[k]`` 是第 k 个 latent token（[1,C,1,H,W]）
      - ``offsets[k]`` 是该 token 的起始像素帧位置
      - ``covered`` 是实际覆盖帧数（等于 frames，否则 raise）

    三条硬约束（违反即 raise，绝不静默降级）：
      1. ``frames`` 必须落在网格上；
      2. 尾段不得长于 latent 本身；
      3. 尾段起始必须落在 5-token 周期边界，否则各 token 的帧跨度
         会和写入的位置对不上 → 渲染出**整体位移的接缝**。
    """
    frames = int(frames)

    # 优先复用外层（Apt 链）已经写好的导出尾段，省一次切片
    exported = latent.get(KEY_EXPORT_TAIL_VIDEO) if isinstance(latent, dict) else None
    exported_frames = int(
        latent.get(KEY_EXPORT_FRAMES, 0) if isinstance(latent, dict) else 0
    )
    if exported is not None and exported_frames and frames == exported_frames:
        if exported.ndim == 4:
            exported = exported.unsqueeze(0)
        steps = steps_for_frames(frames)
        if exported.ndim != 5 or int(exported.shape[2]) != steps:
            raise ValueError(
                "latent 内附的导出尾段与 %d 帧的 H3 网格不符（形状 %s，期望 %d 步）。"
                % (frames, tuple(exported.shape), steps)
            )
        blocks = [exported[:1, :, k:k + 1].clone() for k in range(steps)]
        return blocks, step_offsets(steps), frames

    video = video_from_latent(latent)
    total = int(video.shape[2])
    steps = steps_for_frames(frames)
    if steps is None:
        raise ValueError(
            "%d 帧不是 H3 latent 的整步窗口，无法从 latent 切片。\n"
            "    合法窗口：%s。\n"
            "    若确实要用这个帧数，请改用像素路径（context_frames）而不是 context_latent。"
            % (frames, ", ".join(str(g) for g in GUIDE_RUNS if g > 1))
        )
    if steps > total:
        raise ValueError(
            "需要 %d 个 latent 步（%d 帧），但 context_latent 只有 %d 步。\n"
            "    上一段太短，或分辨率/时长与这一段不匹配。"
            % (steps, frames, total)
        )
    start = total - steps
    if start % 5 != 0:
        raise RuntimeError(
            "%d 步的尾段在 %d 步的 latent 里起始于周期位置 %d（非 0），"
            "各 token 的帧跨度会与写入位置错位 → 接缝整体位移。\n"
            "    通常说明段长不匹配；调整段长使 (总步数 - %d) 是 5 的倍数。"
            % (steps, total, start % 5, steps)
        )
    covered = pixel_frames(steps)
    if covered != frames:
        raise RuntimeError(
            "%d 步覆盖 %d 帧，期望 %d 帧（网格自洽性被破坏）。" % (steps, covered, frames)
        )
    blocks = [video[:1, :, start + k:start + k + 1].clone() for k in range(steps)]
    return blocks, step_offsets(steps), covered


def audio_tail_from_latent(
    latent: Any, a_frames: int, src_total_frames: int
) -> Tuple[torch.Tensor, int, float, float, bool]:
    """从 AV latent 切出 ``a_frames`` 帧对应的**音频尾段**。

    ``src_total_frames`` 是**源段（latent）的总像素帧数**——H3 的音频 tick 数按
    总帧数四舍五入生成，外溢偏差只有对着总帧数量才有意义（对着尾窗量恒为巨值）。

    返回 ``(tail, take, grid_slack, raw_steps, grid_off)``：
      · ``take`` —— 实际取用的音频步数；非 40Hz 网格整步的 ``a_frames``
        会**向上拓宽**到最近整步，要的比源段现有还多则全给；
      · ``grid_slack`` —— 音频栅格相对视频栅格的外溢量（正常在 ±⅓ 步内）；
      · ``raw_steps`` —— 换算出的理论步数，供上层留注记；
      · ``grid_off`` —— 外溢量超出半整步（输入段可能非标准网格），
        此时 ``grid_slack`` 按 0 报出，告警文案由上层写进 notes。
    """
    a_frames = int(a_frames)

    exported = latent.get(KEY_EXPORT_TAIL_AUDIO) if isinstance(latent, dict) else None
    if exported is not None:
        if exported.ndim == 3:
            exported = exported.unsqueeze(0)
        if exported.ndim != 4:
            raise ValueError("latent 内附的导出音频尾段形状非法。")
        n_t = int(exported.shape[-1])
        return exported[:1].clone(), n_t, 0.0, float(n_t), False

    audio = audio_from_latent(latent)
    total_t = int(audio.shape[-1])
    # 音频栅格相对视频栅格的**外溢量**。H3 按源段总帧数四舍五入生成音频 tick，
    # 正常只落在 ±⅓ 步内；超出这条带 ⇒ 输入本身不是标准网格。
    grid_slack = total_t - FRAME_RESCALE * int(src_total_frames)
    grid_off = abs(grid_slack) >= 0.5
    if grid_off:
        # 只置标志、不改事实：外溢值按 0 报出，告警文案留给上层写进 notes。
        grid_slack = 0.0

    raw_steps = a_frames / float(FPS) * AUDIO_HZ
    # 非 40Hz 网格整步的值一律**向上拓宽**到最近整步：
    # 音频窗的作用是给模型"已经播过的声音"当上下文，多带半步是安全的，
    # 截短半步则可能丢掉节拍点。换算误差只往"多带"方向偏。
    want = int(math.ceil(raw_steps - 1e-9))
    take = min(want, total_t)          # 要的比现有的多 ⇒ 全给，不报错
    if take < 1:
        raise ValueError("音频窗口为空（%d 帧换算后不足一步）。" % a_frames)
    tail = audio[:1].narrow(-1, total_t - take, take).clone()
    return tail, take, float(grid_slack), raw_steps, grid_off


# ---------------------------------------------------------------- 续接计划
@dataclass
class RelayPlan:
    """一次续接的完整计划（可打印、可序列化，便于留痕排障）。"""

    applied: bool = False
    span: int = 0                      # 被钉住的像素帧数
    steps: int = 0                     # 被钉住的 latent token 数
    trim: int = 0                      # 下游应裁掉的首部**像素帧**数 = span + settle
    settle: int = 0                    # 其中属于「沉降区」的帧数（不受 5+17k 网格约束）
    indices: List[int] = field(default_factory=list)
    keyframes: List[Dict[str, Any]] = field(default_factory=list)
    audio_ref: Optional[Dict[str, Any]] = None
    anchor_ref: Optional[Dict[str, Any]] = None   # 0.5.0 全局外观锚（refs 路线）
    clipped_channels: Optional[int] = None
    notes: List[str] = field(default_factory=list)

    def summary(self) -> str:
        if not self.applied:
            return "未应用（context_latent 未接）"
        line = (
            "钉住 %d 帧 / %d 步，锚位 %d..%d，裁首 %d 帧（含沉降 %d），音频 %s%s"
            % (
                self.span, self.steps,
                self.indices[0] if self.indices else -1,
                self.indices[-1] if self.indices else -1,
                self.trim,
                self.settle,
                ("%d 步" % self.audio_ref["ref_audio_t"]) if self.audio_ref else "关",
                ("，外观锚 %d token" % self.anchor_ref["latent_t"]) if self.anchor_ref else "",
            )
        )
        return line


def plan_relay(
    latent: Any,
    context_latent: Any,
    trim_frames: int = 22,
    audio_frames: Optional[int] = None,
    settle_frames: int = 0,
    anchor_latent: Optional[Any] = None,
    anchor_frames: int = 5,
) -> RelayPlan:
    """生成续接计划。

    参数
      latent          本段的目标 latent（提供分辨率 / 步数 / 帧数）
      context_latent  上一段的 AV latent（提供被钉住的尾段）
      trim_frames     钉住的像素帧数，必须在 GUIDE_RUNS 上
      audio_frames    音频钉住窗口（像素帧口径）；默认与视频同窗
      settle_frames   沉降帧数（默认 0 = 只裁钉住区）。钉住区之后模型还会先
                      **复现**上一段若干帧才切到本段 prompt，那几帧一并裁掉
                      才能保证拼接处不跳变、上段文字不串入。不受 5+17k 网格
                      约束 —— 裁的是 decode 之后的像素帧，不动 latent 相位。
                      （UI 上的自动检测由 H3RelayTrimAV 做，见 detect_settle）

    分辨率必须一致 —— latent 无法缩放，不一致只能重跑上一段或从本段重启链。
    """
    plan = RelayPlan()
    if context_latent is None:
        plan.notes.append("未接 context_latent：本段不续接（按独立段处理）。")
        return plan

    trim_frames = int(trim_frames)
    settle_frames = max(0, int(settle_frames))
    dst = video_from_latent(latent)
    src = video_from_latent(context_latent)
    w, h = int(dst.shape[4]) * 16, int(dst.shape[3]) * 16
    sw, sh = int(src.shape[4]) * 16, int(src.shape[3]) * 16
    if (sw, sh) != (w, h):
        raise ValueError(
            "context_latent 是 %dx%d，本段是 %dx%d。latent 无法缩放，"
            "续接要求两段同分辨率。\n"
            "    解决：让上一段用同分辨率重跑，或把链从这里重启。"
            % (sw, sh, w, h)
        )
    if int(src.shape[1]) != int(dst.shape[1]):
        raise ValueError(
            "context_latent 有 %d 个通道，本段有 %d 个 —— 不是同一底模产出的 H3 视频 latent。"
            % (int(src.shape[1]), int(dst.shape[1]))
        )

    frame_count = pixel_frames(int(dst.shape[2]))
    if trim_frames not in GUIDE_RUNS:
        # 不静默吸附：改了帧数就是改了续接窗口，必须让你知道。
        near = snap_guide_run(trim_frames)
        hint = ("最接近的合法窗口是 %d。" % near) if near else "没有比它更小的合法窗口。"
        raise ValueError(
            "%d 帧不是 H3 的整步续接窗口。\n"
            "    合法值：%s（= 5 + 17k，配 17k+5 帧段长时尾段起点正好落在 5 步周期边界）。\n"
            "    %s"
            % (trim_frames, ", ".join(str(g) for g in sorted(GUIDE_RUNS) if g >= 5), hint)
        )
    if trim_frames >= frame_count:
        raise ValueError(
            "钉住 %d 帧、本段只有 %d 帧 —— 没有新内容可生成。\n"
            "    缩短窗口或加长本段。" % (trim_frames, frame_count)
        )
    if trim_frames + settle_frames >= frame_count:
        raise ValueError(
            "钉住 %d 帧 + 沉降 %d 帧 = %d 帧 ≥ 本段 %d 帧 —— 裁完就没画面了。\n"
            "    调小沉降帧数，或加长本段。"
            % (trim_frames, settle_frames, trim_frames + settle_frames, frame_count)
        )

    blocks, offsets, covered = video_tail_from_latent(context_latent, trim_frames)
    plan.span = covered
    plan.steps = len(blocks)
    plan.indices = list(offsets)
    plan.settle = settle_frames
    # pin 受 5+17k 网格约束（上面已硬校验）；settle 裁的是像素帧，不影响 latent 相位。
    plan.trim = covered + settle_frames
    if settle_frames:
        plan.notes.append(
            "沉降 %d 帧：钉住区之后模型还会先复现若干帧才切到本段 prompt，"
            "这几帧一并裁掉，避免拼接处跳变与上段文字串入。" % settle_frames
        )

    plan.keyframes = [
        {"resolved_frame_index": int(p), "latent": blk}
        for p, blk in zip(plan.indices, blocks)
    ]

    a_frames = int(audio_frames) if audio_frames else trim_frames
    src_total_frames = pixel_frames(int(src.shape[2]))
    tail, rt, overhang, raw_steps, grid_off = audio_tail_from_latent(
        context_latent, a_frames, src_total_frames)
    if grid_off:
        plan.notes.append(
            "⚠ 音频栅格与视频帧数偏差超出半整步（上段 %d 帧，音频栅格换算后与之对不上），"
            "已按无外溢处理——输入段可能不是标准 H3 网格，请检查上一段来源。"
            % src_total_frames
        )
    if rt > raw_steps + 1e-6:
        plan.notes.append(
            "音频窗 %d 帧换算 %.2f 步，已拓宽到 %d 整步（宁多带、不截短）。"
            % (a_frames, raw_steps, rt)
        )
    dst_audio_t = None
    try:
        dst_audio_t = int(audio_from_latent(latent).shape[-1])
    except ValueError:
        dst_audio_t = None
    if dst_audio_t is not None and rt > dst_audio_t:
        plan.notes.append(
            "音频窗口 %d 步超过本段音频栅格 %d 步，已截断。" % (rt, dst_audio_t)
        )
        tail = tail[..., :dst_audio_t].clone()
        rt = dst_audio_t
    plan.audio_ref = {"kind": "audio", "ref_audio_t": int(rt), "audio_latent": tail}
    if overhang:
        plan.notes.append("音频栅格外溢 %.3f 步（已在放置时对齐）。" % overhang)

    if anchor_latent is not None:
        # 0.5.0 全局外观锚：refs 路线（异分辨率合法，见 build_anchor_ref 注释）。
        # 锚失败绝不静默降级——refs 丢了 = 长程一致性悄悄失效，必须让用户看见。
        try:
            plan.anchor_ref = build_anchor_ref(anchor_latent, int(anchor_frames))
        except RuntimeError as e:
            raise ValueError(
                "外观锚构建失败（anchor_frames=%d）：%s" % (int(anchor_frames), e)
            )
        aw, ah = int(plan.anchor_ref["latent_w"]) * 16, int(plan.anchor_ref["latent_h"]) * 16
        plan.notes.append(
            "全局外观锚：%d 帧 refs 块（%dx%d%s）——全程骑乘每步，长程身份/色档基准。"
            % (int(anchor_frames), aw, ah,
               "，异分辨率" if (aw, ah) != (w, h) else ""))

    plan.applied = True
    return plan


def _anchor_position(anchor):
    """一个锚在目标时间轴上的落点（ComfyUI 原生字段；缺失按 0 处理）。"""
    return int(anchor.get("resolved_frame_index", 0))


def _partition_anchors(anchors, zone_end):
    """按落点把锚切成 (钉住区内, 钉住区外) 两组，两组都是副本。

    钉住区 = 本段开头那 ``zone_end`` 帧。它由上一段的 latent 逐位决定，
    详见 ``build_continue_latent`` 的掩码语义。
    """
    inside, outside = [], []
    for anchor in anchors:
        bucket = inside if _anchor_position(anchor) < zone_end else outside
        bucket.append(dict(anchor))
    return inside, outside


def apply_relay(conditioning, plan: RelayPlan):
    """把续接计划写进 conditioning —— 本包与 ComfyUI 原生协议之间的唯一出口。

    出口按顺序只做三件事：

    1. **锚位合成**：conditioning 上可能已经挂着别人放的锚（官方 AddGuide、
       上游末帧锚）。本段不替换它们，而是把旧锚与本次新增的锚合成一张新表，
       旧锚保持原有相对次序排在前面。
    2. **钉住区去重**：本段开头 ``plan.span`` 帧由上一段的 latent 逐位决定，
       任何人再往这段区间里放锚，都是对同一批帧的重复声明。重复声明在此出局，
       落点记进 report —— 静默丢会让「少了一个锚」事后无从查起。
    3. **ref 追加**：音频与外观锚走 ``minimax_refs``，追加写回，
       上游已有的 ref 不受影响。

    ``plan.applied`` 为假时原样返回（首段没有前序可接）。
    """
    import node_helpers

    if not plan.applied:
        return conditioning

    zone_end = int(plan.span)
    out, redundant = [], []

    for emb, extra in conditioning:
        merged = dict(extra)
        inside, outside = _partition_anchors(
            merged.get("minimax_keyframes") or [], zone_end)
        redundant.extend(_anchor_position(a) for a in inside)
        merged["minimax_keyframes"] = outside + [dict(a) for a in plan.keyframes]
        out.append([emb, merged])

    if redundant:
        plan.notes.append(
            "钉住区（0..%d）内的既有锚 %s 是重复声明，已出局（共 %d 个）。"
            % (zone_end - 1, sorted(set(redundant)), len(set(redundant)))
        )

    if plan.audio_ref is not None or plan.anchor_ref is not None:
        refs = [r for r in (plan.audio_ref, plan.anchor_ref) if r is not None]
        out = node_helpers.conditioning_set_values(
            out, {"minimax_refs": refs}, append=True
        )
    return out


# ---------------------------------------------------------- 0.5.0 漂移对策
def match_stats(
    src: torch.Tensor,
    ref: torch.Tensor,
    blend: float = 1.0,
    gain_min: float = 0.5,
    gain_max: float = 2.0,
) -> torch.Tensor:
    """逐通道一阶+二阶矩对齐（Reinhard 2001 统计迁移的 latent 域版）。

    把 ``src`` 的每通道均值/标准差拉向 ``ref``：
    ``out = (src - μ_src) * clamp(σ_ref/σ_src, 0.5, 2.0) + μ_ref``，
    再按 blend 与原文混（blend=1 全量校正，0 = 原样）。

    用途是**纠偏被裁掉的钉住前缀**（消耗品条件，不伤上段成片）：模型看到
    的全局统计被拉回锚段色档 → 本段生成内容随Conditioning harmonize 复位漂移。
    增益限幅防呆：σ 比超出 [0.5, 2] 时截断，宁欠矫不炸图（对比：不限幅时
    一段黑场会把 σ_src≈0 的通道增益推到 ∞）。
    """
    if int(src.shape[1]) != int(ref.shape[1]):
        raise ValueError(
            "矩对齐要求通道数一致：src %d ≠ ref %d（不是同一底模的 latent？）"
            % (int(src.shape[1]), int(ref.shape[1]))
        )
    blend = max(0.0, min(1.0, float(blend)))
    if blend == 0.0:
        return src
    dims = tuple(range(2, src.ndim))
    m_s = src.mean(dim=dims, keepdim=True)
    s_s = src.std(dim=dims, keepdim=True)
    m_r = ref.mean(dim=dims, keepdim=True).to(m_s.dtype)
    s_r = ref.std(dim=dims, keepdim=True).to(s_s.dtype)
    gain = (s_r / s_s.clamp(min=1e-6)).clamp(gain_min, gain_max)
    matched = (src - m_s) * gain + m_r
    return src + blend * (matched - src)


def build_anchor_ref(anchor_latent: Any, frames: int) -> Dict[str, Any]:
    """0.5.0 全局外观锚：把锚段（通常第 0 段）**开头** ``frames`` 帧构建为原生
    ``minimax_refs`` 的 ``kind="video"`` 参考块。

    取头不取尾：refs 块在宿主里按"自身第 0 token 起"的相位约定重建时间格
    （model.py ``_video_t_spans`` 从 k%5=0 开始），切片只有从 0 起才天然满足
    该约定；取尾则受锚段总步数相位约束（``video_tail_from_latent`` 的
    start%5==0 检查），换个段长就报"相位错位"。开头切片恒合法，且语义
    更正——第 1 段开场 = 全片的视觉圣经（身份/色档/布光基准）。

    依据宿主源码（2026-09-15 逐行核实）：refs 块打包进 payload 后，其 cond 行
    以近零噪声（VISUAL_COND_TIMESTEP）全程骑乘每一个采样步
    （comfy/model_base.py extra_conds → comfy/ldm/minimax/model.py
    ``_cond_video_rows``/``img_update=zeros``），正对应 LLM 流式生成的
    attention sink——永不退出的外观锚（StreamingT2V/LongLive 的长程一致性
    在黑盒协议层的等价物）。refs 自带独立空间网格（``_frame_grid``），
    **允许与目标段不同分辨率**——这是与 keyframes 路线的关键区别，也是
    「拿第 0 段当锚」跨段可用的前提。
    """
    video = video_from_latent(anchor_latent)
    steps = steps_for_frames(int(frames))
    if steps is None:
        raise ValueError(
            "%d 帧不是 H3 的整步窗口，无法从锚段 latent 切 refs 块。合法值：%s。"
            % (int(frames), ", ".join(str(g) for g in sorted(GUIDE_RUNS) if g >= 5))
        )
    total = int(video.shape[2])
    if steps > total:
        raise ValueError(
            "锚窗 %d 帧（%d 步）超过锚段 latent 的 %d 步——锚段太短，换更小的锚窗。"
            % (int(frames), steps, total)
        )
    z = video[:1, :, 0:steps].contiguous()
    return {
        "kind": "video",
        "latent": z,
        "latent_t": int(z.shape[2]),
        "latent_h": int(z.shape[3]),
        "latent_w": int(z.shape[4]),
        "ref_audio_t": 0,
        "audio_latent": None,
    }


# ---------------------------------------------------------------- AV latent 存取
# 磁盘格式：一个 safetensors，内含 streams.video / streams.audio + metadata。
# NestedTensor 无法直接 safetensors 序列化，故按流拆开存。
_LATENT_META_KEY = "relay_kit_meta"


def save_av_latent(latent: Any, path: str, note: str = "") -> str:
    """把 H3 的 AV latent 落盘，供下一段当 context_latent 读回。"""
    from safetensors.torch import save_file

    parts = streams_from_latent(latent)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tensors: Dict[str, torch.Tensor] = {}
    names = []
    for i, t in enumerate(parts):
        name = "video" if i == 0 else ("audio" if i == 1 else "stream_%d" % i)
        names.append(name)
        # safetensors 要求连续内存。detach() 已脱离 autograd 共享（与旧版 clone 的
        # 安全性等价），跨设备时 .cpu() 本来就物化新张量——不再额外 clone，
        # 落盘瞬间的 CPU 峰值从 2-3 倍降到 1 倍。
        tc = t.detach()
        if tc.device.type != "cpu":
            tc = tc.cpu()
        tensors["streams." + name] = tc.contiguous()
    meta = {
        "format": 1,
        "streams": names,
        "shapes": [list(t.shape) for t in parts],
        "note": str(note),
    }
    # UTF-8 字节流：note 含中文时 ord(c) 会超 uint8 直接崩，必须先 encode
    meta_bytes = json.dumps(meta, ensure_ascii=False).encode("utf-8")
    tensors[_LATENT_META_KEY] = torch.frombuffer(bytearray(meta_bytes), dtype=torch.uint8)
    # 原子写：先写同目录 .tmp 再替换，中途崩溃不会留下截断的 safetensors
    tmp = path + ".tmp"
    try:
        save_file(tensors, tmp)
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise
    return path


def load_av_latent(path: str):
    """读回落盘的 AV latent，重建 NestedTensor。"""
    from safetensors.torch import load_file

    try:
        import comfy.nested_tensor as _nt
    except Exception:  # pragma: no cover - ComfyUI 运行时一定可用
        _nt = None

    if not os.path.isfile(path):
        raise FileNotFoundError("读不到续接 latent：%s" % path)
    raw = load_file(path)
    meta_t = raw.pop(_LATENT_META_KEY, None)
    if meta_t is None:
        raise ValueError("%s 不是本工具写的 AV latent（缺少元数据）。" % path)
    meta = json.loads(bytes(meta_t.tolist()).decode("utf-8"))
    parts = [raw["streams." + n] for n in meta["streams"]]
    if _nt is not None:
        samples = _nt.NestedTensor(parts)
    else:  # pragma: no cover
        samples = parts
    return {"samples": samples}


def trim_head_frames(images: torch.Tensor, frames: int) -> torch.Tensor:
    """裁掉 IMAGE 的前 ``frames`` 帧（沿第 0 维）。

    续接段的前 ``trim_frames`` 帧是对上一段尾部的**重生成**（实测逐帧 MAE ≈ 6/255），
    它们是钉住区的产物，不属于新内容 —— 不裁就会在拼接处看到约 0.9s 重播。
    """
    frames = int(frames)
    if frames <= 0:
        return images
    n = int(images.shape[0])
    if frames >= n:
        raise ValueError(
            "要裁 %d 帧，但本段只有 %d 帧 —— 裁完就没画面了。\n"
            "    检查 trim_frames 是否误填成整段长度。" % (frames, n)
        )
    return images[frames:]


def trim_audio_head(audio: Any, frames: int, fps: float = FPS) -> Any:
    """把 AUDIO 的前 ``frames`` 帧对应的采样点裁掉（与视频同裁，保住 A/V 同步）。"""
    frames = int(frames)
    if frames <= 0 or audio is None:
        return audio
    wf = audio["waveform"]
    sr = int(audio["sample_rate"])
    n = int(round(frames / float(fps) * sr))
    total = int(wf.shape[-1])
    if n >= total:
        raise ValueError(
            "要裁 %d 帧（%d 个采样点），但音频只有 %d 点。"
            % (frames, n, total)
        )
    return {"waveform": wf[..., n:], "sample_rate": sr}


# ---------------------------------------------------------------- 音频缝（节点内实现）
# 为什么必须在节点里做：组装层（ffmpeg）那三件事——crossfade / 头部补丁 / 床环铺——
# 都是「渲染之后的第二步」。效果要由节点在**渲染时**就落进段文件，组装才只剩拼接。
#
# 本组只实现**长度守恒**的两类（本段自己能改完的）：
#   · patch：把本段头部 N 秒（含解码 priming 的近静默 + 生成瞬态）换成**上一段的同场景环境声**
#   · tile ：补丁床用 W 秒瓦片自叠化环铺（干净窗短于 N 时的正解）
#
# ⚠️ 真 crossfade（三角窗叠化）**无法在单段内长度守恒地实现**：ffmpeg acrossfade 的语义是
#    「上一段尾部裁掉 X 秒」——那要改**上一段的文件**，而单段节点只能改本段。
#    所以 crossfade 只能留在组装层，或者改用 patch（等长、零 A/V 位移）。
#    附带收益：patch 不消耗时间轴 ⇒ 组装层 acrossfade「每缝吃 0.25s、
#    使后段音频相对画面整体提前」那个副作用在 patch 路线上不存在。
AUDIO_SEAM_PATCH: float = 0.0    # 头部补丁秒数（0 = 关，逐位直通）
AUDIO_SEAM_TILE: float = 0.0     # 补丁床瓦片秒数（0 = 整窗直取 N 秒）
AUDIO_SEAM_FADE: float = 0.25    # 补丁边界交叉淡变宽度（秒）
# 🔴 2026-09-19 真渲染阴性结果后的修法（见 CHANGES「音频缝床声选择」）：
#   旧行为「取床源全局最静窗」在「环境声 + 音乐」素材上会取到**近乎无内容**的窗
#   （实测床声 −42 dBFS vs 缝前 −12.8 dBFS）⇒ 补丁本身就是一段更静的东西，
#   缝上从「凹陷」变成「静音洞」，还把本该有的起拍压平。
AUDIO_SEAM_BED_SELECT: str = "tail"      # tail（默认，新：取床源尾部同长窗，与缝天然连续）
                                         # quiet（0.5.0 旧行为，仅作对照复现）
AUDIO_SEAM_BED_FLOOR_DB: float = 12.0    # quiet 档的选窗下限：只取「≥ 目标 − 该值 dB」的窗（避免取到静默）
AUDIO_SEAM_BED_GAIN_MAX_DB: float = 6.0  # 床声电平对齐上限（±dB）；超出则夹住并报 ⚠
_AUDIO_META_KEY = "relay_kit_audio_meta"


def _audio_parts(audio: Any) -> Tuple[torch.Tensor, int, Tuple[int, ...], torch.dtype]:
    """把 AUDIO 拆成 ([C,T] float32 视图, sample_rate, 原前导维, 原 dtype)。

    ComfyUI 的 AUDIO 约定 ``{"waveform": [B, C, T], "sample_rate": int}``；
    但 [C,T] / [T] 也要能跑（手搓或第三方节点给的张量形状不一）。
    """
    if not isinstance(audio, dict) or "waveform" not in audio:
        raise TypeError("AUDIO 必须是 {'waveform': 张量, 'sample_rate': int}，得到 %r" % (type(audio),))
    wf = audio["waveform"]
    if not isinstance(wf, torch.Tensor):
        raise TypeError("AUDIO.waveform 必须是张量，得到 %r" % (type(wf),))
    if wf.dim() == 0:
        raise ValueError("AUDIO.waveform 不能是 0 维张量。")
    lead = tuple(int(v) for v in wf.shape[:-2])
    flat = wf.reshape(-1, int(wf.shape[-2]) if wf.dim() >= 2 else 1, int(wf.shape[-1]))
    flat = flat[0] if flat.shape[0] else flat.reshape(1, -1)      # [C, T]
    # 采样率取整；缺失时按 H3 的 32kHz 兜底（不静默按 1 处理，那会把秒数算成样本数）
    sr = int(audio.get("sample_rate") or 32000)
    if sr <= 0:
        raise ValueError("AUDIO.sample_rate 必须是正数，得到 %r。" % (audio.get("sample_rate"),))
    return flat.float(), sr, lead, wf.dtype


def _match_channels(wf: torch.Tensor, channels: int) -> torch.Tensor:
    """把床源声道数对齐到本段（单声道广播 / 多声道降混 / 取前 N 路）。"""
    c = int(wf.shape[0])
    if c == channels:
        return wf
    if c == 1:
        return wf.expand(channels, -1)
    if channels == 1:
        return wf.mean(dim=0, keepdim=True)
    return wf[:channels]


def _resample_to(wf: torch.Tensor, src_sr: int, dst_sr: int) -> torch.Tensor:
    """线性重采样（床源与本段采样率不一致时才走）。"""
    if src_sr == dst_sr or src_sr <= 0 or dst_sr <= 0:
        return wf
    import torch.nn.functional as F

    n = max(1, int(round(int(wf.shape[-1]) * float(dst_sr) / float(src_sr))))
    return F.interpolate(wf.unsqueeze(0), size=n, mode="linear",
                         align_corners=False).squeeze(0)


def quietest_window(wf: torch.Tensor, n_samples: int,
                    floor: float = 0.0) -> Tuple[int, float]:
    """在 ``[C,T]`` 波形里找**能量最低**的连续 ``n_samples`` 窗。

    返回 ``(起点样本, 窗内 RMS)``。用前缀和一次算完全部窗（O(T)，不是 O(T·n)）。

    ``floor``（可选）：只接受 RMS ≥ 该值的窗；**全部不达标才**退回全局最静窗。
    动机（2026-09-19 实测）：全局最静窗在「环境声 + 音乐」素材上是**近乎静默**的一段
    （实测 −42 dBFS，比缝前低 29 dB）⇒ 拿它当床声等于把缝上的洞换个位置。
    """
    total = int(wf.shape[-1])
    n = int(n_samples)
    if n <= 0 or n > total:
        return 0, float("nan")
    p = (wf ** 2).sum(dim=0)                    # [T] 逐样本跨声道能量
    c = torch.cat([p.new_zeros(1), torch.cumsum(p, dim=0)])
    win = c[n:] - c[:-n]                        # [T-n+1] 每窗总能量
    rms = torch.sqrt(win.clamp_min(0.0) / (n * max(1, int(wf.shape[0]))))
    if float(floor) > 0.0:
        ok = rms >= float(floor)
        if bool(ok.any()):
            idx = torch.nonzero(ok, as_tuple=False).flatten()
            i = int(idx[int(torch.argmin(rms[idx]))])
            return i, float(rms[i])
    i = int(torch.argmin(rms))
    return i, float(rms[i])


def _rms_db(x: torch.Tensor) -> float:
    return 20.0 * math.log10(max(float(x.pow(2).mean().sqrt()), 1e-9))


def target_level(wf: torch.Tensor, probe_samples: int = 6400,
                 hop_samples: int = 640) -> float:
    """补丁要对齐的**目标电平**：尾部 ``probe_samples`` 窗内「20ms 子窗 RMS 的**中位数**」。

    为什么不是单窗 RMS（2026-09-19，BGM 适配性反思）：
      · 环境声（雨声/room tone）是 stationary ⇒ 单窗 RMS 稳定；
      · **BGM/音乐是非 stationary**：一个 200ms 窗可能正好落在**弱拍/换气/衰减**上，
        单窗 RMS 会比真实伴奏电平低 6–10 dB ⇒ 拿它当目标会把补丁整体压低，制造人为凹陷。
      取「20ms 子窗 RMS 的中位数」对乐句内的强弱起伏稳健，两种素材都适用。

    取尾部的理由：补丁区紧跟在「上一段末尾」之后，要延续的是**它**的电平。
    节点优先把上一段音频传进来（``target_audio``），拿不到才退回床源自身尾部。
    """
    total = int(wf.shape[-1])
    k = max(1, min(int(probe_samples), total))
    seg = wf[..., total - k:]
    hop = max(1, min(int(hop_samples), k))
    n_sub = max(1, k // hop)
    sub = seg[..., :n_sub * hop].reshape(*seg.shape[:-1], n_sub, hop)
    rms = sub.pow(2).mean(dim=-1).clamp_min(0.0).sqrt()      # [..., n_sub]
    return float(rms.reshape(-1).median())


def build_bed(wf: torch.Tensor, n_samples: int, tile_samples: int,
              fade_samples: int, select: str = AUDIO_SEAM_BED_SELECT,
              floor: float = 0.0) -> Tuple[torch.Tensor, int, float]:
    """从床源波形取 ``n_samples`` 长的床声；``tile_samples>0`` 时按瓦片自叠化环铺。

    ``select``：
      · ``tail``（**默认**）取**尾部同长窗**——紧邻缝，电平与音色与缝前天然连续；
      · ``quiet`` 取（可带下限的）最静窗——0.5.0 旧行为，**仅作对照**。

    返回 ``(床波形 [C,n], 起点样本, 原始床声 RMS)``。**不做电平处理**——
    对齐与峰值护栏都在 ``audio_seam_patch`` 里一步做完（少一层处理 = 少一层累计误差）。
    """
    total = int(wf.shape[-1])
    n = int(n_samples)
    if n <= 0:
        raise ValueError("床声长度必须为正，得到 %d。" % n)
    if tile_samples <= 0:
        if select == "tail":
            start = max(0, total - n)
        else:
            start, _ = quietest_window(wf, min(n, total), floor=floor)
        bed = wf[..., start:start + n]
    else:
        W, X = int(tile_samples), int(fade_samples)
        if X <= 0 or X >= W:
            raise ValueError(
                "瓦片环铺要求 0 < 边界淡变 %.3fs < 瓦片长 %.3fs。\n"
                "    瓦片太短会退化成「同一段噪声原样重复」，反而听得出来。" % (X / 32000.0, W / 32000.0)
            )
        if select == "tail":
            start = max(0, total - W)
        else:
            start, _ = quietest_window(wf, min(W, total), floor=floor)
        tile = wf[..., start:start + W]
        k = max(2, int(math.ceil((n - X) / float(W - X) - 1e-9)))
        w = torch.linspace(0.0, 1.0, X, device=wf.device, dtype=torch.float32)
        acc = tile
        for _ in range(k - 1):
            blend = acc[..., -X:] * (1.0 - w) + tile[..., :X] * w
            acc = torch.cat([acc[..., :-X], blend, tile[..., X:]], dim=-1)
        bed = acc[..., :n]
    if int(bed.shape[-1]) < n:                  # 床源比 N 还短：循环凑够（防御，不炸）
        reps = int(math.ceil(n / max(1, int(bed.shape[-1]))))
        bed = bed.repeat(1, reps)[..., :n]
    return bed, start, float(bed.pow(2).mean().sqrt())


def audio_seam_patch(audio: Any, bed_audio: Any, patch: float = AUDIO_SEAM_PATCH,
                     tile: float = AUDIO_SEAM_TILE,
                     fade: float = AUDIO_SEAM_FADE,
                     select: str = AUDIO_SEAM_BED_SELECT,
                     target_audio: Any = None,
                     gain_max_db: float = AUDIO_SEAM_BED_GAIN_MAX_DB) -> Tuple[Any, str]:
    """把本段头部 ``patch`` 秒换成 ``bed_audio``（上一段）里的床声窗；**长度守恒**。

    替换区 ``[0, N-X)`` 纯床声，``[N-X, N)`` 是床声 → 本段自身音频的交叉淡变
    （X = ``fade``），``N`` 之后**逐位不动**。返回 ``(audio, report)``。

    🔴 2026-09-19 修正（真渲染阴性结果驱动）：床声窗**默认取尾部窗**（``select="tail"``），
    并把床声**电平对齐**到「缝前电平」（``target_audio`` 的尾部；没给就用床源自身尾部），
    增益限幅 ``±gain_max_db``。旧行为 ``select="quiet"``（全局最静窗）会取到近乎静默的一段
    ⇒ 实测把缝上的凹陷换成静音洞，仅保留作对照。
    """
    if patch is None or float(patch) <= 0:
        return audio, ""
    if bed_audio is None:
        return audio, ""
    wf, sr, lead, dtype = _audio_parts(audio)
    total = int(wf.shape[-1])
    ch = int(wf.shape[0])
    n = int(round(float(patch) * sr))
    X = max(0, int(round(float(fade) * sr)))
    warn = ""
    if n >= total:                              # 补丁比整段还长：夹住并显著告警（不炸整条链）
        n = max(1, total - 1)
        X = min(X, n)
        warn = "｜ ⚠ 补丁超出段长，已夹到 %.3fs" % (n / float(sr))
    if X >= n:                                  # 边界淡变不能吃掉整个替换区
        X = max(0, n - 1)
    bed_wf, bed_sr, _, _ = _audio_parts(bed_audio)
    bed_wf = _match_channels(_resample_to(bed_wf, bed_sr, sr), ch)
    tgt_wf = bed_wf
    if target_audio is not None:
        _t, _tsr, _, _ = _audio_parts(target_audio)
        tgt_wf = _match_channels(_resample_to(_t, _tsr, sr), ch)
    tgt = target_level(tgt_wf, int(round(0.2 * sr)))
    floor = tgt * (10.0 ** (-AUDIO_SEAM_BED_FLOOR_DB / 20.0))
    bed, start, bed_rms = build_bed(bed_wf, n, int(round(float(tile) * sr)), X,
                                    select=select, floor=floor)
    bed_db = _rms_db(bed)
    # 电平对齐 + 峰值护栏，**一步做完**（不额外加处理层：少一层 = 少一层累计误差）
    g_db, clamped = 0.0, False
    if bed_rms > 0.0 and tgt > 0.0:
        _g = 20.0 * math.log10(tgt / bed_rms)
        _used = max(-float(gain_max_db), min(float(gain_max_db), _g))
        _peak = float(bed.abs().max()) if int(bed.numel()) else 0.0
        if _peak > 0.0:                       # 尾部窗本来就响，+dB 会推过 1.0 ⇒ 削波
            _head = 20.0 * math.log10(0.995 / _peak)
            clamped = clamped or _head < _used
            _used = min(_used, _head)
        clamped = clamped or abs(_g) > float(gain_max_db)
        if _used != 0.0:
            bed = bed * (10.0 ** (_used / 20.0))
        g_db = _used

    out = wf.clone()
    keep = n - X                                # [0, keep) 纯床声
    if keep > 0:
        out[..., :keep] = bed[..., :keep]
    if n > keep:                                # [keep, n) 床声 → 本段自身
        w = torch.linspace(0.0, 1.0, n - keep, device=wf.device, dtype=torch.float32)
        out[..., keep:n] = bed[..., keep:n] * (1.0 - w) + wf[..., keep:n] * w

    tile_note = "，%.2fs 瓦片自叠化环铺" % float(tile) if float(tile) > 0 else ""
    mode_note = "尾部窗（与缝天然连续）" if str(select) == "tail" else "最静窗（旧口径·仅对照）"
    rep = ("[H3 Relay] 音频缝：头部补丁 %.2fs ← 床源%s @%.2fs%s，边界交叉淡变 %.2fs\n"
           "           床声电平 %.1f dBFS → 对齐目标 %.1f dBFS（%s %+.1f dB%s）\n"
           "           音频 %d → %d 采样点（长度守恒，零 A/V 位移）%s"
           % (float(patch), mode_note, start / float(sr), tile_note, X / float(sr),
              bed_db, 20.0 * math.log10(max(tgt, 1e-9)),
              "增益" + ("已夹住" if clamped else ""), g_db,
              "⚠ 超出限幅，建议换床源段" if clamped else "",
              total, int(out.shape[-1]), warn))
    shaped = out.reshape(*lead, ch, total) if lead else out
    return {"waveform": shaped.to(dtype), "sample_rate": sr}, rep


def save_audio(audio: Any, path: str, note: str = "") -> str:
    """把 AUDIO 落盘（safetensors，原子写），供后段当床源读回。

    存**原始波形张量**（任意前导维，通常 [B,C,T]）而不是展平后的 [C,T]——
    这样 load 回来与存之前逐位、逐形状都一致（单测 22.12 锁这个）。
    """
    from safetensors.torch import save_file

    _, sr, _, _ = _audio_parts(audio)
    wf = audio["waveform"]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    meta = {"format": 1, "sample_rate": sr, "shape": [int(v) for v in wf.shape],
            "note": str(note)}
    meta_bytes = json.dumps(meta, ensure_ascii=False).encode("utf-8")
    tensors = {"waveform": wf.detach().cpu().contiguous(),
               _AUDIO_META_KEY: torch.frombuffer(bytearray(meta_bytes), dtype=torch.uint8)}
    tmp = path + ".tmp"
    try:
        save_file(tensors, tmp)
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise
    return path


def load_audio(path: str) -> Dict[str, Any]:
    """读回落盘的 AUDIO。"""
    from safetensors.torch import load_file

    if not os.path.isfile(path):
        raise FileNotFoundError("读不到落盘音频：%s" % path)
    raw = load_file(path)
    meta_t = raw.pop(_AUDIO_META_KEY, None)
    if meta_t is None:
        raise ValueError("%s 不是本工具写的 AUDIO（缺少元数据）。" % path)
    meta = json.loads(bytes(meta_t.tolist()).decode("utf-8"))
    wf = raw["waveform"]
    shape = meta.get("shape")
    if shape and list(wf.shape) != list(shape):
        wf = wf.reshape(*[int(v) for v in shape])
    return {"waveform": wf, "sample_rate": int(meta["sample_rate"])}


# ---------------------------------------------------------------- 接缝自检
# 实测（2026-09-11）：续接段的「钉住区 → 新内容」切换**不总落在 trim 值上**。
# 73 帧段实测切换点在原第 22→23 帧之间（MAE 6.5 → 102.3），
# 而 trim=22 恰好把切换点前的那一帧留在裁剪后的第 0 帧 → 首帧突变。
# 107 帧段同一 trim 值则无此现象（切换点被完整裁掉）。
# ⇒ trim 值必须按**本段的实际切换点**定，不能写死。
#
# 落点：切换点是**逐段不同的随机变量**，所以它不该是用户填的配置，而是
# **观测出来的量**。裁节点手里就是完整 decode 序列（含钉住区），真实切换点
# 此刻就在手上 —— `detect_settle` 就在那里量，量不出就退回只裁钉住区。
JUMP_RATIO: float = 4.0       # 首帧差 / 段内基线 的报警阈值
# 基线过小时的保护下限——**随数据量纲缩放**（v0.3.2）：ComfyUI decode 张量是 0-1，
# 旧写死 1.0（0-255 量纲）会把 0-1 数据的帧差路径整个抬死（基线 0.0076 被抬到 1.0，
# 阈值 4.0 永不可达 → 帧差法在产线上从未生效过）。0.004 ≈ 1/255 满量程，
# 对 0-255 的旧测试行为不变（0.004×250 ≈ 1.0）。
BASELINE_FLOOR_REL: float = 0.004
MAX_SETTLE: int = 12          # 沉降帧上限（≈0.5 s）。自动检测不会超过它
# 裁后仍见突变时，只有位置在这么靠前才值得动刀：
# 裁后下标 j 表示"还差 j+1 帧沉降"，所以上限是 MAX_SETTLE-1。
ADVISE_WITHIN: int = MAX_SETTLE - 1
# —— 模糊型沉降（v0.3.1）：切换不是硬跳而是「重绘发虚」——帧差法看不见，改看高频能量。——
# 度量用 **Laplacian 响应的均方**（≈方差）：模糊先杀高对比边缘，平方放大这一损失。
# mean-abs 版实测钝 3 倍（HD mild 塌陷 0.29-0.45× 在它眼里是 0.79-0.83×，过不了 0.35 阈）。
BLUR_ZONE_RATIO: float = 0.55      # 锐度 < 基准×此比例 → 记为塌陷帧
BLUR_COLLAPSE_RATIO: float = 0.35  # 塌陷最深要低于基准×此比例才算真模糊（防误伤天生偏软的段）
BLUR_RECOVER_RATIO: float = 0.5    # 窗内必须看到恢复到基准×此比例，才敢裁（看不到恢复宁少勿多）
# —— 锐度路三量解耦（2026-09-15，GG 目检「每处接缝都看到模糊沉降」）——
# ⚠ 版本号待定：本改动与在飞的 v0.4.3+ 未提交改动同处一树，由 GG 决定并入哪个版本。
# 实测（onerA_cond4_4seg，cond 桥）：糊区 11–17 帧宽、最深 0.29–0.31×基准。原实现让
# MAX_SETTLE=12 同时充当扫描窗宽 → 糊区把窗口填满时 after 为空 →「窗内必须见恢复」
# 永假 → settle 恒 0（s2/s3 实测 settle=0；段头/段体锐度比 0.71 / 0.41，糊区原样留在成片）。
# 又：原实现把三件事压在同一个 MAX_SETTLE 上——扫描窗宽、裁剪上限、体区参考偏移
# （far_lo = pin + max_settle + 2）——单纯加窗会连带漂移参考区，破坏 12.9/13.8 两条契约。
# 故锐度路这三者拆开独立；**硬跳路与色档路仍用 MAX_SETTLE，安全阀不动**。
SETTLE_SCAN: int = 36          # 锐度路扫描窗宽（≈1.5 s）
SETTLE_CAP: int = 36           # 锐度路裁剪上限（= 扫描窗，糊区才裁得干净）
# 🔴 2026-09-15 实测上调 24→36（依据：节点 report 剖面 + 裁量→跳跃曲线）：
#   cond 桥的暖机「塌陷 + 爬升」总长**实测 >25 帧**（剖面里到第 17 帧锐度 0.77 仍在涨）
#   → 24 帧的窗**看不到恢复点** → 只能退回「最后一个塌陷帧」→ 留下几帧 0.53–0.55× 残余
#   （GG 目检「接缝处从模糊变清晰」）。
#   而 `trim_jump_curve` 显示：裁 8 帧→跳跃 8.4×、裁 12 帧→~9.0×（**多裁 4 帧只多 0.6×**）
#   ⇒ **把窗放到 36 让恢复点进窗 = 切净糊、跳跃几乎不变**，是净赚。
#   ⚠ 配套：出词纪律的「段首无台词」要按新裁头上限放宽（≥ 裁头 + 0.2 s）。
SETTLE_REF_OFF: int = 14       # 体区参考起点相对 pin 的偏移（固定；pin=22 时 = 原 pin+12+2）
BLUR_COLLAPSE_RATIO_SHARP: float = 0.45
SETTLE_RECOVER_TARGET: float = 0.8

# —— 路径 4：复现残留（RESEARCH_seam_frontier §13 D7）——
# 动机：现有三路（硬跳 / 锐度 / 色档）**没有一路能看见复现残留** ——
#   复现帧是**清晰**的（锐度路看不见）、**色档一致**的（色档路看不见）、
#   也**没有硬跳**（硬跳路看不见）。
# 判据：窗内第 i 帧与**钉住区（前 pin 帧）**的**最小** MAE。
# 实测分离度（0–255 灰度）：重复 = 2.81；非重复 = 11.3 / 45.2 → 分离比 > 2×。
# 本实现内部用 0–1 归一化，故阈值 = 5/255。
REPEAT_MAE: float = 5.0 / 255.0        # 判「这一帧是钉住区内容的复现」的 MAE 门槛
REPEAT_MIN_RUN: int = 2                # 连续重复至少几帧才采信（防单帧巧合）
REPEAT_SCAN: int = 36                  # 扫描窗宽（与 SETTLE_SCAN 同量级）
# 锐度路的「恢复点」目标：裁到锐度回到此比例的首帧，而不是停在「最后一个塌陷帧」。
# 动机（2026-09-15 GG 目检）：只裁到塌陷边界（<0.55×）会留下几帧 0.53–0.55× 的**恢复尾巴**，
# 观感 =「接缝处从模糊变清晰」——糊本身修好了，但「从糊变清」这个过渡仍是可见缺陷。
#
# 🔴🔴 2026-09-15 深夜**重大修正**：这条「裁到恢复点」把 settle 从 8 推到 16，
#   **代价被严重低估**——实测跳帧随裁量单调上升（同一条 s2，只改裁量）：
#
#     settle  0 → 最大单帧跳 5.08（归一 0.020）  ← GG：「几乎无感」
#     settle  8 →                7.2×（归一 0.044）  ← GG：「跳了」
#     settle 16 →               14.08（归一 0.055）  ← GG：「跳」
#
#   **裁切 = 时间跳跃**：裁掉的帧越多，缝处要跨过的时间就越长，跳得越大。
#   而「糊」是**清晰度的渐变**，人眼对渐变容忍度极高（GG 对 settle 0 的评语就是「几乎无感」）。
#   ⇒ **拿「可容忍的渐变」去换「不可容忍的突变」，是拿错的筹码换对的东西。**
#
#   正确方向（2026-09-15 定案）：**时间轴上什么都不做**（不裁沉降、不重影），
#   把「治糊」放到**画质域**去解 —— 对糊区做锐化/去模糊修复（不改变帧数、不改变时间轴）。
#   糊区是「上段尾的复现」，上段尾的**真值帧是清晰的**，天然可作修复的参考引导。
#   ⇒ 本常量保留（诊断价值：它量出的塌陷深度是真的），但**默认不据此裁切**。
#
# 锐度路单列的深线（硬跳路验身仍用 BLUR_COLLAPSE_RATIO=0.35）：cond 路钉住区本身被重绘
# 发软 → 自参考基准被污染、塌陷比值被抬浅，实测 S1 深塌陷只到 0.36×，卡在 0.35 门外漏检。
# —— v0.4.1 色档收敛信号：注噪/taper 路的「收敛尾巴」是低频现象（重影+色档漂移），
# 锐度法不可见（L1 taper 实测：可见头部 3 帧亮度 0.270→0.282 爬升 + 首帧重影——GG 目检确认）。
# 旧像素注噪路裁 26=22+4 裁的就是它——本信号是它的观测端版本。
GRADE_DEV_Z: float = 4.0        # 体区 MAD 的 z 门槛
GRADE_DEV_FLOOR: float = 0.003  # 绝对下限（0-1 量纲；255 量纲测试由 z 项主导）
# —— v0.3.3 稳健统计层：参考分布来自段体（窗外体区），z 分数定位，深度比值做证据闸 ——
# 动机（GG 2026-09-13）：分辨率/步数/LoRA/场景内容都会整体移动锐度与帧差的绝对量级，
# 任何"绝对常数"都会在某个参数组合下失效。因此：
#   · 参考分布 = 段体自身（窗外 ≥8 帧，median + MAD）——4 步软渲染、运动模糊、平坦场景
#     自动进基准，零配置、零量纲；
#   · z 分数（|x-med|/(1.4826·MAD)）负责"是不是异常"，比值门槛（0.35/0.55/0.5，本来就
#     无量纲）负责"是不是深到值得动刀"——两道闸缺一不可：只有 z 会把"段体天生比头锐"
#     的正常渐变误裁，只有比值会在分布漂移时定位失准；
#   · 硬跳必须**验身**：跳后 2-5 帧锐度回到体分布才采信，否则视为假跳/闪烁交给锐度路。
Z_JUMP: float = 8.0                # 硬跳的稳健 z 门槛（对段体帧差 MAD 标准化；实测真跳 z≈90）
_LAP_KERNELS: dict = {}            # 按 device 字符串惰性缓存的 3×3 Laplacian 卷积核


def _robust_stats(x: torch.Tensor) -> Tuple[float, float]:
    """稳健 (median, 1.4826×MAD)。空输入返回 (0, 0)。"""
    if x.numel() == 0:
        return 0.0, 0.0
    med = float(x.median())
    mad = float((x - med).abs().median()) * 1.4826
    return med, mad


def _abs_max(t: torch.Tensor) -> float:
    """max(|t|)，不物化 abs() 的全量拷贝（float 张量上 max(|min|, max) 与之等价）。"""
    return max(-float(t.min()), float(t.max()))


def _baseline_floor(images: torch.Tensor) -> float:
    """帧差基线的保护下限，随数据实际量纲缩放（0-1 产线 / 0-255 测试同一套阈值）。"""
    try:
        scale = _abs_max(images.detach())
    except Exception:
        return 0.0
    return BASELINE_FLOOR_REL * scale if scale > 0 else 0.0


# —— L1 域归一 / L4 跨尺度仲裁（2026-09-15 实测新增）——
# 实测（`scale_invariance_probe.py`，同一段素材四尺度）：绝对锐度基准跨 **37.4×**
# （116px 1.16e-2 → 928px 3.11e-4）⇒ **绝对阈值跨分辨率必然失效**；
# 且**高分辨率下塌陷变浅**（928×1600 只到 0.45×，而 232×400 是 0.34×）——
# 因为 3×3 Laplacian 是**固定像素核**，高分辨率下它测的是最细的噪声级细节，**对中频的糊迟钝**。
# 对策：L1 先归一分析域（缩到固定短边），L4 要求两个尺度都见深塌陷才动刀（拒绝尺度伪影）。
ANALYSIS_SHORT_SIDE: int = 256        # L1：梯度类指标的默认分析短边（**只缩不放**）
ANALYSIS_SHORT_SIDE_ALT: int = 384    # L4：仲裁用的第二个尺度
SETTLE_CROSS_SCALE: bool = True       # L4 开关
# 路径 4（复现残留）开关。
# 🔴 默认 **False**：本路**只会让裁量变大**，而既有契约是「宁可维持旧行为也不赌」。
#   实测发现它会在**低纹理 / 周期内容**上误报——合成夹具 `seam_seg`（3 帧周期）下
#   `pin` 之后的帧必然与钉住区某帧逐位相同 ⇒ 必报。真实静态镜头同理。
#   ⇒ 本路已实现且单测覆盖，但**默认不启用**；启用前必须用真实渲染验证误报率。
#   置 True 会改变 settle 行为（可能多裁）——**不是逐位兼容的改动**。
SETTLE_REPEAT_PATH: bool = False


def _canonicalize(images: torch.Tensor, short_side: int = None) -> torch.Tensor:
    """L1 域归一：把帧缩到**固定短边**再算梯度类指标（**只缩不放**）。

    帧比目标还小时**原样返回** —— 合成夹具（8×8）与低分辨率素材不受影响，
    故该层对既有行为是"只在高分辨率下生效"的纯增益。
    """
    ss = ANALYSIS_SHORT_SIDE if short_side is None else int(short_side)
    if ss <= 0 or images.dim() != 4:
        return images
    h, w = int(images.shape[1]), int(images.shape[2])
    if min(h, w) <= ss:
        return images
    k = ss / float(min(h, w))
    nh, nw = max(1, int(round(h * k))), max(1, int(round(w * k)))
    f = images.to(torch.float32)
    ch_last = f.shape[-1] in (1, 3, 4)                 # [N,H,W,C]（ComfyUI IMAGE）
    if ch_last:
        f = f.permute(0, 3, 1, 2)                      # → [N,C,H,W]
    f = torch.nn.functional.interpolate(f, size=(nh, nw), mode="area")
    return f.permute(0, 2, 3, 1) if ch_last else f


def _sharpness(images: torch.Tensor, short_side: int = None) -> torch.Tensor:
    """逐帧高频能量代理：3×3 **Laplacian 响应的均方**（≈锐度方差）。纯 torch，不依赖 cv2/PIL。

    带纹理的画面显著大于 0；重绘发虚（模糊）先杀高对比边缘，平方度量把它放大
    （实测：HD mild 塌陷帧在方差度量下 0.29-0.45×基准，mean-abs 度量下只见 0.79-0.83×）。
    """
    f = _canonicalize(images, short_side)              # L1 域归一（只缩不放）
    if f.dim() != 4:
        return torch.zeros(0)
    f = f.to(torch.float32)
    g = f.mean(dim=-1).unsqueeze(1)                    # [N,1,H,W]
    # 按 device 分键缓存：多 GPU / 多设备交替时不会来回重建，也无全局竞态
    key = str(g.device)
    kern = _LAP_KERNELS.get(key)
    if kern is None or kern.device != g.device:
        kern = g.new_tensor([[0.0, 1.0, 0.0],
                             [1.0, -4.0, 1.0],
                             [0.0, 1.0, 0.0]]).view(1, 1, 3, 3)
        _LAP_KERNELS[key] = kern
    # replicate pad（不能用 conv2d 自带的 zero pad：常数帧边界会吃出假响应，
    # 小分辨率合成测试里边界占比过半，整个度量直接反转）
    gp = torch.nn.functional.pad(g, (1, 1, 1, 1), mode="replicate")
    resp = torch.nn.functional.conv2d(gp, kern)
    return resp.pow(2).mean(dim=(1, 2, 3))             # [N]


def _frame_diffs(images: torch.Tensor) -> torch.Tensor:
    """相邻帧差的逐帧标量（纯 CPU、纯张量，不依赖 cv2/PIL）。"""
    f = images.to(torch.float32)
    if f.dim() == 4 and f.shape[-1] in (1, 3, 4):   # [N,H,W,C] → 按通道均值
        return (f[1:] - f[:-1]).abs().mean(dim=(1, 2, 3))
    return (f[1:] - f[:-1]).abs().flatten(1).mean(dim=1)


def scan_head_jump(images: torch.Tensor, scan: int = 40) -> Tuple[int, float, float]:
    """段首扫描的**原始观测**：返回 ``(argmax 下标, 该处帧差, 段内基线)``。

    不做显著性判断、不加范围限制 —— 由上层按各自口径解释：
    ``find_head_jump`` 只判显著性，``describe_head_jump`` 再叠加"可执行范围"。
    """
    n = int(images.shape[0])
    if n < 4:
        return -1, 0.0, 0.0
    # ★ 先切窗再做差：整段物化两遍全量帧差，在 120+ 帧 / 768×448 上是 ~1GB 的瞬时峰值
    hi = min(scan, n - 1)
    if hi <= 2:
        return -1, 0.0, 0.0
    diff = _frame_diffs(images[:hi + 1])
    baseline = float(diff[2:hi].median())
    seg = diff[:hi]
    j = int(torch.argmax(seg).item())
    return j, float(seg[j].item()), baseline


def find_head_jump(images: torch.Tensor, scan: int = 40) -> Tuple[int, float, float]:
    """在前 ``scan`` 帧内找「本段起点」处的突变。

    返回 ``(jump_index, jump_mae, baseline_mae)``：
      - ``baseline_mae``：段内相邻帧差的中位数（跳过前 2 帧）
      - ``jump_mae``：``images[jump_index]`` 与后一帧的差
      - ``jump_index``：突变发生在前一帧的下标（即"应当再往前裁 1 帧"的位置）
    找不到突变时返回 ``(-1, 0.0, baseline)``。

    纯 CPU、纯张量：不依赖 cv2/PIL，可在节点里直接调。
    """
    j, jump, baseline = scan_head_jump(images, scan)
    if j < 0:
        return -1, jump, baseline
    if jump > JUMP_RATIO * max(baseline, _baseline_floor(images)):
        return j, jump, baseline
    return -1, jump, baseline


# —— 边界跳帧观测（2026-09-15 实测新增）——
# 动机：copy 桥在「钉住区最后一帧 → 本段第一帧新内容」处的帧差，实测达**段内基线的 29.6×**
# （视觉上就是「跳帧」）。而沉降公式 `settle = j + 1 - pin` 在 **j == pin-1**
# （跳变正好落在钉住区边界）时返回 **0** —— 把边界跳变**静默判成「无沉降」**。
# 语义上没错（边界就是切换点，没有"多余复现"可裁），但**它掩盖了「边界本身是断的」这个事实**。
# 故把该量单独暴露，供 report 直接显示 —— **裁切治不了跳变，但至少要看得见。**
BOUNDARY_JUMP_WARN: float = 6.0   # 报警阈值：边界帧差 ÷ 段内基线


# —— 缝帧重影（极短交叉溶）——
# 🔴 2026-09-15 深夜实测定案：**默认关（0）**。保留能力，但不要默认开。
#
# 原设想：把缝处一跳拆成两个半跳跨两格 → 眼不及辨。数值上确实如此
# （最大单帧跳 14.08 → 7.51，归一 0.055 → 0.029）。
#
# **但观感实测更差**（GG 目检原话：「比之前的实现还差，有明显跳帧、不流畅」）。根因：
#   ① **总位移几乎没减**：超基线总量 12.61 → 11.68（仅 −7%）—— 一跳变两跳，位移守恒；
#   ② **异常帧数 1 → 2**：单帧瞬跳可能被当成眨眼/运动模糊，**连续两帧异常 = 卡了两下**；
#   ③ 重影帧是**鬼影**：内容既不属于上段尾也不属于本段首，本身就是一个可见异物。
#   ⇒ **「把突变摊平成两个小突变」不等于消除突变**——人眼对「持续异常」比对「瞬时异常」更敏感。
#
# 更重要的对照（同一条 s2，只改裁量）：
#   settle 0  → 跳帧 0.020（GG：**几乎无感**）
#   settle 16 → 跳帧 0.055（GG：跳）
#   ⇒ **跳的源头是「裁切」本身**（裁切 = 时间跳跃）。重影治不了它，只会改变它的形状。
#   ⇒ 正确方向见 `SETTLE_RECOVER_TARGET` 处的注释：**时间轴不动，画质域修复**。
SEAM_GHOST_FRAMES: int = 0        # 重影帧数（**默认 0 = 关**；>0 仅在确知收益时手动开）
SEAM_GHOST_ALPHA: float = 0.5     # 上段末帧的权重（0.5 = 对半）


def seam_ghost_blend(head: torch.Tensor, prev_last: torch.Tensor,
                     frames: int = SEAM_GHOST_FRAMES,
                     alpha: float = SEAM_GHOST_ALPHA) -> torch.Tensor:
    """把 ``head`` 的**前 frames 帧**替换为「上段末帧 ⊕ 本段首帧」的加权混合。

    帧数守恒（替换而非插入）⇒ 音频不需要跟着动 ⇒ **无 A/V 漂移**。
    纯张量、无依赖，便于离线回测。
    """
    if frames <= 0 or int(head.shape[0]) == 0:
        return head
    k = min(int(frames), int(head.shape[0]))
    a = float(min(max(alpha, 0.0), 1.0))
    ref = prev_last.to(head.dtype)
    if ref.dim() == head.dim() - 1:          # [H,W,C] → [1,H,W,C]
        ref = ref.unsqueeze(0)
    return torch.cat([a * ref + (1.0 - a) * head[:k], head[k:]], dim=0)


# —— 画质域修复：糊区锐化（2026-09-16 GG 定方向：先试零 GPU 传统锐化）——
# 动机：默认 settle_frames=0（不裁沉降）后，成片段头保留几帧「模型重绘导致的糊」
#   （实测锐度 0.33–0.8× 基准）；而「裁掉它」会引入跳帧（裁 16 帧跳 0.055）。
#   ⇒ 时间轴两条路都不走，改走**画质域**：不裁、不动时间轴，只提升糊区高频。
#
# 方法：unsharp mask  `out = img + amount·(img − blur3x3(img))`，
#   强度按「越靠缝越强」线性衰减（糊本身是渐变的：0.33 → 1.0）。
#
# 诚实边界（**别当万能药**）：
#   **锐化只能恢复「对比度」，不能恢复「已丢失的真实细节」。**
#   对「结构还在、只是软」的重绘糊有效；对「细节完全丢失」无效。
#   副作用：放大噪声、强边缘可能出 halo（白边）→ 故 amount 上限设 1.5。
SETTLE_SHARPEN: float = 0.0          # 锐化强度（0 = 关；建议 0.4–1.0）
SETTLE_SHARPEN_FRAMES: int = 24      # 作用帧数（从裁后首帧起，线性衰减到 0）
SETTLE_SHARPEN_MAX: float = 1.5      # 强度上限（防呆）


def seam_crossfade(images: torch.Tensor, cut: int, pin: int, frames: int = 1) -> torch.Tensor:
    """极短交叉溶：返回**裁后**帧序列，但把开头 ``frames`` 帧替换为
    「上段末尾 frames 帧 ⊕ 本段开头 frames 帧」的渐变混合。

    参数
    ----
    ``cut``  裁切点（= ``pin + settle``）—— 本段新内容从这里开始
    ``pin``  钉住区长度 —— 「上段末尾 k 帧」从 ``images[pin-k : pin]`` 取
    ``frames`` 溶的帧数（1 = 对半单帧；2–3 = 极短交叉溶；上限 6）

    与 ``seam_ghost_blend`` 的关键区别：**两侧都是连续运动的序列**
    （而非把上段末帧复制 N 次）→ k>1 时不会"内容冻结"，过渡是逐帧摊开的。
    ``t_i = (i+1)/(k+1)``：k=1 → 0.5；k=3 → 0.25/0.5/0.75；每帧只走总跳幅的 1/(k+1)。
    **帧数守恒**、不动音频、零采样开销。
    """
    n = int(images.shape[0])
    cut, pin, k = int(cut), int(pin), int(frames)
    if k <= 0 or pin < 1 or n <= cut:
        return images[cut:] if 0 < cut <= n else images
    k = min(k, pin, n - cut)          # 上段末尾要有 k 帧、裁后要有 k 帧
    if k <= 0:
        return images[cut:]

    a = images[pin - k: pin]          # 上段末尾 k 帧：A_1 … A_k（A_k = 上段末帧）
    b = images[cut: cut + k]          # 本段开头 k 帧：B_1 … B_k
    a_rev = torch.flip(a, dims=[0])   # A_k … A_1 —— 让输出第 0 帧最接近上段末帧

    t = torch.linspace(1.0 / (k + 1.0), k / (k + 1.0), k,
                       device=images.device, dtype=images.dtype).view(k, 1, 1, 1)
    mixed = (1.0 - t) * a_rev + t * b
    return torch.cat([mixed, images[cut + k:]], dim=0)

# —— 低频残差传递（2026-09-16，借鉴 Director 的段间引导低频对齐）——
# 来源与依据见 `lowfreq_pull` 文档串；核心一行：
#     out = clamp(src + w·(blur(guide) − blur(src)), 0, 1)
# **只吸收 guide 的低频色档/布光，保留 src 自己的细节与姿态** —— 故不会产生第二个轮廓。
LOWFREQ_PULL_WEIGHT: float = 0.0      # 默认关（0 = 不动）；Director 实测用 0.70
LOWFREQ_PULL_FRAMES: int = 12         # 作用帧数（从裁后首帧起，权重线性衰减到 0）
LOWFREQ_PULL_BLUR: int = 64           # 低频尺度（盒式模糊核，Director 用 64）


def _box_blur_hwc(x: torch.Tensor, kernel: int) -> torch.Tensor:
    """盒式模糊（NHWC 进出，reflect pad）。kernel 强制奇数且 >=3。

    用 `avg_pool2d` 实现（与 Director 的 GPU 批量路径同算子）：
    该运算逐样本独立、不跨帧耦合，故可一次批量算完。
    """
    k = int(kernel)
    if k < 3 or x.dim() != 4:
        return x.float()
    if k % 2 == 0:
        k += 1
    n, h, w, c = x.shape
    pad = k // 2
    t = x.float().permute(0, 3, 1, 2)                       # NHWC → NCHW
    t = torch.nn.functional.pad(t, (pad, pad, pad, pad), mode="reflect")
    t = torch.nn.functional.avg_pool2d(t, kernel_size=k, stride=1, count_include_pad=False)
    return t.permute(0, 2, 3, 1).contiguous()


def lowfreq_pull(images: torch.Tensor, guide: torch.Tensor,
                 frames: int = LOWFREQ_PULL_FRAMES,
                 weight: float = LOWFREQ_PULL_WEIGHT,
                 blur: int = LOWFREQ_PULL_BLUR) -> torch.Tensor:
    """把 ``images`` **开头 frames 帧**的低频拉向 ``guide``（通常 = 上段末帧）。

    逐帧：``out[i] = clamp(images[i] + w_i·(blur(guide) − blur(images[i])), 0, 1)``，
    权重 ``w_i`` 从 ``weight`` 线性衰减到 0（缝端最强 → 尾端不动）。

    **关键性质**：只动低频 ⇒ **不复制 guide 的姿态轮廓** ⇒ 无重影。
    这与「全 RGB 混合」（交叉溶/重影）有本质区别 —— 后者会复制姿态边缘。

    **帧数守恒、不动音频、零采样开销。**
    """
    if weight <= 0.0 or images.dim() != 4 or int(images.shape[0]) == 0:
        return images
    if guide is None:
        return images
    n = int(images.shape[0])
    k = min(int(frames), n)
    if k <= 0:
        return images

    g = guide
    if g.dim() == 4:
        g = g[0]                                  # [H,W,C]
    if tuple(g.shape[:2]) != tuple(images.shape[1:3]):
        return images                             # 画布不一致 → 不动刀（安全兜底）
    g = g.unsqueeze(0).to(images.dtype)           # [1,H,W,C]

    zone = images[:k]
    b_guide = _box_blur_hwc(g, blur)              # guide 的低频只算一次
    b_zone = _box_blur_hwc(zone, blur)            # k 帧批量一次算完
    w = torch.linspace(float(weight), 0.0, k, device=images.device,
                       dtype=torch.float32).view(k, 1, 1, 1)
    pulled = (zone.float() + w * (b_guide - b_zone)).clamp(0.0, 1.0).to(images.dtype)
    return torch.cat([pulled, images[k:]], dim=0)

# —— P2 跨段统计匹配（Reinhard 式一阶+二阶矩）—— 2026-09-17 / RESEARCH_seam_frontier §2
# 动机：色档/曝光漂移是**低阶统计量现象**，一阶（均值）二阶（标准差）矩理论上是充分统计。
#   现有 `lowfreq_pull` 只做**低频加性**修正（一阶）；本函数补上**二阶（对比度）+ 逐通道色度**。
# 文献：Reinhard《Color transfer between images》IEEE CG&A 21(5):34-41, 2001（lab 空间逐通道
#   均值/方差匹配）· WCT《Universal Style Transfer via Feature Transforms》NeurIPS 2017,
#   arXiv:1705.08086 · TTC(Pathwise Test-Time Correction) arXiv:2602.05871。
# ⚠ 与「全 RGB 混合 / 交叉溶」有本质区别：后者复制**姿态轮廓**（→ 重影）；
#   本函数只对齐**统计量**（均值/标准差）⇒ 不复制结构 ⇒ 无重影。
MATCH_PREV_WEIGHT: float = 0.0     # 默认关（0 = 不动）；建议从 0.5 起试
MATCH_PREV_FRAMES: int = 12        # 作用帧数（从裁后首帧起，权重线性衰减到 0）
MATCH_PREV_GAIN_MAX: float = 1.15  # 逐通道增益上限（σt/σs 截断，防把已通过的内容改坏）
MATCH_PREV_OFF_MAX: float = 0.06   # 逐通道偏移上限（μt−μs 截断）
# 🔴 2026-09-17 真渲染实测定案：统计量**只取紧贴缝的那一帧**（= 1），不再对整个作用区聚合。
#   旧口径（聚合整个作用区）与「单帧 guide」口径**不匹配**，会给出错方向的修正：
#   当段头**内部有亮度梯度**时（实测：首帧已到 guide 水平、第 2 帧起掉 ~0.008），
#   聚合均值被后续帧拉低 ⇒ offs 变成「段头平均 vs guide」的差 ⇒ 首帧被**推过 guide**，
#   缝上凭空多出一个阶跃（实测纯末→首阶跃 0.0007 → 0.0088，×12.6）。
#   我们要的目标函数是「**首帧 ≈ guide**」，不是「段头均值 ≈ guide」。
#   设 0 可回退旧口径（仅作对照，勿用于产线）。
MATCH_PREV_STATS_FRAMES: int = 1


def match_prev_stats(images: torch.Tensor, guide: torch.Tensor,
                     frames: int = MATCH_PREV_FRAMES,
                     weight: float = MATCH_PREV_WEIGHT,
                     gain_max: float = MATCH_PREV_GAIN_MAX,
                     offset_max: float = MATCH_PREV_OFF_MAX,
                     stats_frames: int = MATCH_PREV_STATS_FRAMES) -> torch.Tensor:
    """跨段统计匹配：把 ``images`` 开头 ``frames`` 帧的色档/曝光对齐到 ``guide``（上段末帧）。

    逐通道 ``out = (x − μs)/σs · σt + μt``，再按**缝端最强 → 尾端 0**的线性权重与
    原帧混合。修正量**一次性算出**（不对每帧重算统计）⇒ 时间上平滑。

    🔴 ``stats_frames``（统计量取几帧）——**这是本条最容易写错的地方**：
      · ``1``（默认）= 统计量取**紧贴缝的那一帧**，与 ``guide``（单帧）口径一致
        ⇒ ``offs`` 恰是「缝上的阶跃」，首帧按权重被拉向 guide，**不会越过它**；
      · ``0`` = 旧口径：对**整个作用区**聚合。与单帧 guide 口径不匹配 ⇒
        段头内部有亮度梯度时，聚合均值被后续帧拉低，修正量变成「段头平均 vs guide」的差，
        于是**首帧被推过 guide**、缝上凭空多出一个阶跃（2026-09-17 真渲染实测 ×12.6）。
        **仅作对照档，勿用于产线。**
      · ``>1`` = 用前 N 帧聚合（介于两者之间；用于「段头前几帧整体就是一个色档」的情形）。

    **护栏**（防把已通过的内容改坏）：
      · 逐通道增益 ``σt/σs`` 截断到 ``[1/gain_max, gain_max]``
      · 逐通道偏移 ``μt−μs`` 截断到 ``±offset_max``
      · 画布不一致 / 权重 ≤0 / 空输入 ⇒ **原样返回**（安全兜底）

    **帧数守恒、不动音频、零采样开销。**
    """
    if weight <= 0.0 or images.dim() != 4 or int(images.shape[0]) == 0:
        return images
    if guide is None:
        return images
    n = int(images.shape[0])
    k = min(int(frames), n)
    if k <= 0:
        return images

    g = guide
    if g.dim() == 4:
        g = g[0]                                   # [H,W,C]
    if tuple(g.shape[:2]) != tuple(images.shape[1:3]):
        return images                              # 画布不一致 → 不动刀

    src = images[:k].float()                       # [k,H,W,C]
    tgt = g.to(images.dtype).float()               # [H,W,C]
    c = src.shape[-1]
    if tgt.shape[-1] != c:
        return images

    # 统计量的**取样窗**：默认只取紧贴缝的一帧（与单帧 guide 同口径）。
    _sf = int(stats_frames)
    ref = src if _sf <= 0 else src[:max(1, min(_sf, k))]

    mu_s = ref.mean(dim=(0, 1, 2))                 # [C]
    sd_s = ref.std(dim=(0, 1, 2)).clamp_min(1e-6)
    mu_t = tgt.mean(dim=(0, 1))                    # [C]
    sd_t = tgt.std(dim=(0, 1)).clamp_min(1e-6)

    gain = (sd_t / sd_s).clamp(1.0 / float(gain_max), float(gain_max))
    offs = (mu_t - mu_s).clamp(-float(offset_max), float(offset_max))

    corr = (src - mu_s) * gain + mu_s + offs
    w = torch.linspace(float(weight), 0.0, k, device=images.device,
                       dtype=torch.float32).view(k, 1, 1, 1)
    out = (src + w * (corr - src)).clamp(0.0, 1.0).to(images.dtype)
    return torch.cat([out, images[k:]], dim=0)


# —— P3 反卷积去模糊（Wiener）—— 2026-09-16 补齐后处理层方案
def deconv_head_zone(images: torch.Tensor, frames: int = 12,
                     strength: float = 0.0, radius: float = 1.5,
                     noise: float = 1e-4) -> torch.Tensor:
    """段头**频域高频增强**（原「反卷积」路线，2026-09-16 修正）。

    ⚠️ 为什么不用 Wiener 逆滤波：逆滤波要求**已知 PSF**，而我们的「糊」是
       **模型生成时的低频化**，PSF 未知 —— 硬套一个高斯 PSF 只会适得其反。
       实测：逆滤波的增益在中低频段趋于 **0**（等于又加一次低通），
       段头锐度反而从 0.000026 掉到 0.000005。

    ⇒ 改为**频域高频增强**：按频段增益，高频段放大、低频段保持，避免空域
      unsharp 的 halo。`radius` 越大 ⇒ 截止频率越低 ⇒ 被增强的高频越多（控制
      从哪个尺度开始算高频，等效截止频率）。

    strength = 与原帧混合比（0=不动）；帧数守恒、只作用开头 frames 帧、权重渐减。
    """
    if strength <= 0.0 or images.dim() != 4 or int(images.shape[0]) == 0:
        return images
    n = int(images.shape[0])
    k = min(int(frames), n)
    if k <= 0:
        return images
    x = images[:k].float()
    h, w = int(x.shape[1]), int(x.shape[2])
    Y = torch.fft.rfft2(x.permute(0, 3, 1, 2))            # [k,C,H,Wr]
    # 径向频率（0..1），用来做软截止
    fy = torch.fft.fftfreq(h, device=x.device).view(-1, 1)
    fx = torch.fft.rfftfreq(w, device=x.device).view(1, -1)
    fr = torch.sqrt(fy ** 2 + fx ** 2)
    fr = fr / fr.max().clamp_min(1e-6)
    cutoff = min(max(1.0 / max(float(radius), 0.5), 0.05), 1.0)   # 半径越大 → 截止越低（增强的高频越多）
    gain = 1.0 + float(strength) * (fr / cutoff).clamp(0.0, 1.0) ** 2   # 高频段最多 ×2
    out = torch.fft.irfft2(Y * gain.unsqueeze(0).unsqueeze(0), s=(h, w)).permute(0, 2, 3, 1)
    out = out.clamp(0.0, 1.0).to(images.dtype)
    wgt = torch.linspace(1.0, 0.0, k, device=images.device, dtype=torch.float32).view(k, 1, 1, 1)
    blended = x.to(images.dtype) * (1.0 - wgt * float(strength)) + out * (wgt * float(strength))
    return torch.cat([blended.clamp(0.0, 1.0).to(images.dtype), images[k:]], dim=0)


# —— 后处理层的「段体参考」稳健化（2026-09-19）——
# 治的病：段内三层（直方图 / 白平衡 / 高频迁移）的基准一律是「第 40 帧之后整段均值」。
#   段只有 90–192 帧 ⇒ 90 帧时段体只剩 50 帧；段体若有运动/推镜/曝光漂移，
#   **基准本身就不是「这段的常态外观」却照样拿去改段头** ——
#   与音频缝「取到近静默窗当床声」是同一类错（选窗准则反了）。
POST_BODY_DISP_MAX: float = 0.08   # 段体逐帧亮度 (p75−p25)/中位 超此值 ⇒ 基准不可信 ⇒ 弃权


def robust_body(body: torch.Tensor, enabled: bool = True):
    """段体参考帧的**稳健选取** + **离散度报数**。返回 ``(subset, dispersion)``。

    - ``dispersion`` = 段体逐帧 mean luma 的 ``(p75−p25)/中位数``（无量纲）；
    - ``subset`` = 亮度落在 ``[p25, p75]`` 的帧（中央 50%）—— 段体有运动/曝光漂移时，
      整段均值会被两端拉偏，**中央段的统计才代表这段的常态外观**；
    - ``enabled=False`` ⇒ 不筛（原样返回），供 ``baseline=legacy`` 复现旧口径做对照。

    ⚠️ 这是**零帧数代价**的统计（只读），不产生额外 pass。
    """
    n = int(body.shape[0])
    if n <= 0:
        return body, 0.0
    lum = body.float().mean(dim=(1, 2, 3))
    med = float(lum.median())
    if not enabled or n < 4 or med <= 1e-6:
        return body, 0.0
    q = torch.quantile(lum, torch.tensor([0.25, 0.75], device=lum.device, dtype=lum.dtype))
    disp = float((q[1] - q[0]) / med)
    sel = body[(lum >= q[0]) & (lum <= q[1])]
    if int(sel.shape[0]) < 2:
        return body, disp
    return sel, disp


def head_metrics(images: torch.Tensor, frames: int):
    """段头的两个**可审计标量**：``(平均亮度, 高频能量)``。

    用途：把「黑箱叠 N 层」变成「**每层可归因**」——每层作用后各报一次，谁把段头改了多少一目了然。
    高频 = 与 3×3 盒式模糊之差的绝对值均值（尺度无关，够用）。
    """
    k = max(1, min(int(frames), int(images.shape[0])))
    h = images[:k].float()
    return float(h.mean()), float((h - _box_blur_hwc(h, 3)).abs().mean())


def borrow_detail_from_body(images: torch.Tensor, frames: int = 12,
                            strength: float = 0.0, blur: int = 9,
                            body_start: int = 40, robust: bool = True) -> torch.Tensor:
    """把**段体**的高频结构迁移到段头：段头留自己的低频，高频换成段体的。

    做法：``head + s·(highpass(body_ref) 的能量匹配到 head 的高频)``。
    ``body_ref`` 取段体若干帧的中位（避免单帧噪声）。

    用途：段头"糊"= 高频缺失，而段体高频是**同一个场景/光照**下的 ⇒ 迁移不会引入异质内容
    （这点比 P2 低频残差更进一层：P2 只补低频，本函数补的是高频）。

    ⚠ 风险：若段头与段体内容差异大（如人物位移大），迁移会带出"纹理错位"。
    """
    if strength <= 0.0 or images.dim() != 4 or int(images.shape[0]) <= int(body_start):
        return images
    n = int(images.shape[0])
    k = min(int(frames), n)
    if k <= 0:
        return images
    x = images.float()
    head = x[:k]
    body, _ = robust_body(x[int(body_start):], robust)       # 稳健参考（见 robust_body）
    ref = body.median(dim=0).values.unsqueeze(0)             # [1,H,W,C] 段体代表帧
    lo_head = _box_blur_hwc(head, blur)
    lo_ref = _box_blur_hwc(ref, blur)
    hi_head = head - lo_head                                  # 段头高频
    hi_ref = ref - lo_ref                                     # 段体高频
    # 能量匹配：让迁移的高频与段头高频同量级（避免强度失控）
    e_head = hi_head.abs().mean().clamp_min(1e-6)
    e_ref = hi_ref.abs().mean().clamp_min(1e-6)
    hi_ref = hi_ref * (e_head / e_ref)
    wgt = torch.linspace(1.0, 0.0, k, device=images.device, dtype=torch.float32).view(k, 1, 1, 1)
    add = hi_ref.expand(k, -1, -1, -1) * (wgt * float(strength))
    out = (head + add).clamp(0.0, 1.0).to(images.dtype)
    return torch.cat([out, images[k:]], dim=0)


# —— P6 直方图匹配（段头 → 段体）—— 2026-09-16 补齐后处理层方案
def match_hist_head_to_body(images: torch.Tensor, frames: int = 12,
                            strength: float = 0.0, body_start: int = 40,
                            bins: int = 256, robust: bool = True) -> torch.Tensor:
    """把段头的**色阶分布**（直方图）对齐到段体 —— 比 P2 低频残差更强。

    P2（``lowfreq_pull``）只对齐**低频均值**；本函数对齐**整条分布曲线**
    （即不只"亮度一致"，而是"亮的更亮、暗的更暗"的比例也一致）。

    逐通道做 CDF 映射；``strength`` = 与原帧的混合比。
    只作用开头 ``frames`` 帧，权重线性衰减。
    """
    if strength <= 0.0 or images.dim() != 4 or int(images.shape[0]) <= int(body_start):
        return images
    n = int(images.shape[0])
    k = min(int(frames), n)
    if k <= 0:
        return images
    x = images.float()
    body, _ = robust_body(x[int(body_start):], robust)       # 稳健参考（见 robust_body）
    out = x[:k].clone()
    for c in range(int(x.shape[3])):
        src = x[:k, :, :, c].flatten()
        ref = body[:, :, :, c].flatten()
        # 段体的分位点
        qs = torch.linspace(0.0, 1.0, int(bins), device=x.device)
        ref_q = torch.quantile(ref, qs)
        # 把段头像素按其在段头分布中的分位，映射到段体同分位的值
        src_q = torch.quantile(src, qs)
        idx = torch.searchsorted(src_q, src.clamp(src_q[0], src_q[-1]))
        idx = idx.clamp(1, int(bins) - 1)
        lo, hi = src_q[idx - 1], src_q[idx]
        t = ((src - lo) / (hi - lo).clamp_min(1e-8)).clamp(0.0, 1.0)
        mapped = ref_q[idx - 1] * (1.0 - t) + ref_q[idx] * t
        out[:, :, :, c] = mapped.reshape(k, int(x.shape[1]), int(x.shape[2]))
    wgt = torch.linspace(1.0, 0.0, k, device=images.device, dtype=torch.float32).view(k, 1, 1, 1)
    blended = x[:k] * (1.0 - wgt * float(strength)) + out * (wgt * float(strength))
    return torch.cat([blended.clamp(0.0, 1.0).to(images.dtype), images[k:]], dim=0)


# —— P7 灰世界白平衡校正（段头 → 段体）—— 2026-09-16 补齐后处理层方案
def match_white_balance(images: torch.Tensor, frames: int = 12,
                        strength: float = 0.0, body_start: int = 40,
                        robust: bool = True) -> torch.Tensor:
    """把段头的**通道比例（色温）**对齐到段体 —— 对症"色温滑档"。

    与 P2 正交：P2 管**亮度/低频总量**，本函数管**R:G:B 的相对比例**（色温/色调）。
    做法：灰世界假设下，令段头各通道均值比例 = 段体的比例；逐通道乘增益。

    ``strength`` = 增益与原值的混合比（0 = 不动；1 = 完全对齐）。
    """
    if strength <= 0.0 or images.dim() != 4 or int(images.shape[0]) <= int(body_start):
        return images
    n = int(images.shape[0])
    k = min(int(frames), n)
    if k <= 0:
        return images
    x = images.float()
    head = x[:k]
    body, _ = robust_body(x[int(body_start):], robust)       # 稳健参考（见 robust_body）
    hm = head.mean(dim=(0, 1, 2)).clamp_min(1e-6)       # [C]
    bm = body.mean(dim=(0, 1, 2)).clamp_min(1e-6)
    # 只取通道比例，不改变整体亮度
    h_ratio = hm / hm.mean()
    b_ratio = bm / bm.mean()
    gain = (b_ratio / h_ratio).clamp(0.8, 1.25)          # 限幅防偏色
    g = 1.0 + (gain - 1.0) * float(strength)
    wgt = torch.linspace(1.0, 0.0, k, device=images.device, dtype=torch.float32).view(k, 1, 1, 1)
    adj = 1.0 + (g.view(1, 1, 1, -1) - 1.0) * wgt
    out = (head * adj).clamp(0.0, 1.0).to(images.dtype)
    return torch.cat([out, images[k:]], dim=0)

def unsharp_frames(images: torch.Tensor, amount: float) -> torch.Tensor:
    """3×3 高斯 unsharp mask。``amount<=0`` 原样返回（零开销短路）。

    纯 torch、无 cv2/PIL 依赖；通道分组卷积，NHWC 进出（与节点 IMAGE 约定一致）。
    """
    if amount <= 0.0 or images.dim() != 4 or int(images.shape[0]) == 0:
        return images
    x = images.to(torch.float32).permute(0, 3, 1, 2)        # NHWC → NCHW
    c = int(x.shape[1])
    k = x.new_tensor([1.0, 2.0, 1.0])
    k = (k[:, None] * k[None, :]).div(16.0).view(1, 1, 3, 3)
    k = k.expand(c, 1, 3, 3).contiguous()
    # replicate pad：zero pad 会在画面边界吃出假响应（与 _sharpness 同一坑）
    xp = torch.nn.functional.pad(x, (1, 1, 1, 1), mode="replicate")
    blur = torch.nn.functional.conv2d(xp, k, groups=c)
    out = (x + float(amount) * (x - blur)).clamp(0.0, 1.0)
    return out.permute(0, 2, 3, 1).to(images.dtype)


def settle_compensate(images: torch.Tensor, body_start: int = 40,
                      strength: float = 1.0, smooth: int = 3) -> Tuple[torch.Tensor, str]:
    """**自适应糊区补偿**——方向二「观测—校正闭环」的落地：settle 检测器升级成纠偏器。

    症状（2026-09-19 逐帧剖面的定案）：缝后第 2 帧起清晰度断崖（实测段体基线的 42%）
    再单调爬升 ~15 帧——**生成层 settle 行为在像素域的残留**。固定 ``settle_sharpen``
    按「缝端最强→尾端 0」线性衰减，与真实亏空曲线（帧 0-1 清晰、第 2 帧最深）**不重合**；
    本函数**当场量**每帧高频能量（与 3×3 盒式模糊之差，拉普拉斯能量比的廉价代理），
    以段体稳健中位为基准，**按亏空比例**做 unsharp：

      · 亏空越深补得越多（帧 2 最深 → 补最强）；
      · 已达基线的帧（含清晰的帧 0-1）与**段体一律不动**（deficit=0）；
      · 权重上限 1.0（防振铃/噪声放大）、时间平滑（默认 3 帧）防逐帧跳变；
      · 段长 ≤ body_start 或 strength≤0 ⇒ 直通。

    纯 torch、帧数守恒、零采样开销。返回 ``(out, report)``。
    """
    if strength <= 0.0 or images.dim() != 4:
        return images, ""
    x = images.float()
    n = int(x.shape[0])
    bs = int(body_start)
    if n <= bs:
        return images, ""
    hp = x - _box_blur_hwc(x, 3)                            # 高通 [N,H,W,C]
    hf = hp.abs().mean(dim=(1, 2, 3))                       # 每帧高频能量 [N]
    body, _ = robust_body(x[bs:])                           # 段体稳健筛选（复用）
    base = float((body - _box_blur_hwc(body, 3)).abs().mean())
    if base <= 1e-7:
        return images, ""
    ratio = hf / base                                       # 每帧 / 段体基准
    deficit = ((base - hf) / base).clamp(0.0, 1.0)          # 亏空比例 [0,1]
    deficit[bs:] = 0.0                                      # 段体一律不动
    w = (deficit * float(strength)).clamp(0.0, 1.0)
    sm = max(1, int(smooth))
    if sm > 1:                                              # 时间平滑（边缘复制 pad）
        wp = torch.nn.functional.pad(w.view(1, 1, -1), (sm // 2, sm // 2), mode="replicate")
        w = wp.view(-1)[sm // 2: sm // 2 + n].clamp(0.0, 1.0)
    w = w.masked_fill(deficit <= 0.0, 0.0)              # 原本无亏空的帧（含帧 0-1）严格不动：
                                                        # 平滑会把邻帧的亏空渗进来，必须屏蔽
    out = (x + w.view(-1, 1, 1, 1) * hp).clamp(0.0, 1.0).to(images.dtype)

    lo = int(torch.argmin(ratio[:bs]))                      # 量测报告（可审计）
    climb = next((i for i in range(lo, n) if float(ratio[i]) >= 0.95), -1)
    acted = int((w > 0.02).sum())
    rep = ("自适应糊区补偿 %.2f：段头最低 %.0f%%（第 %d 帧）｜回基线第 %d 帧｜"
           "补 %d 帧（最大增益 %.2f，段体与已达标帧不动）"
           % (float(strength), float(ratio[:bs].min()) * 100, lo, climb,
              acted, float(w.max())))
    return out, rep


def sharpen_head_zone(images: torch.Tensor, frames: int = SETTLE_SHARPEN_FRAMES,
                      amount: float = SETTLE_SHARPEN) -> torch.Tensor:
    """对**开头 frames 帧**做渐变锐化（缝端最强 → 尾端 0）。

    只做**一次**卷积（整段），再按线性权重与原帧混合——逐帧不同强度不必逐帧卷积。
    **帧数守恒、不动音频、零采样开销。**
    """
    if amount <= 0.0 or images.dim() != 4:
        return images
    n = int(images.shape[0])
    k = min(int(frames), n)
    if k <= 0:
        return images
    zone = images[:k]
    sharp = unsharp_frames(zone, amount)
    w = torch.linspace(1.0, 0.0, k, device=images.device, dtype=sharp.dtype)
    w = w.view(k, 1, 1, 1)
    return torch.cat([w * sharp + (1.0 - w) * zone, images[k:]], dim=0)

def _blur_collapse(images: torch.Tensor, pin: int, short_side: int = None):
    """在**指定分析尺度**上判「是否有深塌陷」。返回 ``(has_collapse, dip, ref)``。

    供 L4 跨尺度仲裁复用：同一现象在两个尺度上都出现 = 真现象；
    只在单一尺度出现 = 尺度伪影（编码噪声 / 原生分辨率细节噪声）→ 不动刀。
    """
    n = int(images.shape[0])
    pin = int(pin)
    if pin <= 0 or n < pin + 2:
        return False, 0.0, 0.0
    sh = _sharpness(images[: min(n, pin + SETTLE_SCAN + 2)], short_side=short_side)
    if sh.numel() < pin + 2:
        return False, 0.0, 0.0
    ref_lo = pin + SETTLE_REF_OFF
    far = _sharpness(images[ref_lo: min(n, ref_lo + 40)], short_side=short_side)
    ref = max(float(sh[:pin].median()), float(far.median()) if far.numel() else 0.0)
    if ref <= 0.0:
        return False, 0.0, 0.0
    zone = sh[pin: pin + SETTLE_SCAN + 1]
    dip = float(zone.min())
    has = bool((zone < BLUR_ZONE_RATIO * ref).any()) and dip < BLUR_COLLAPSE_RATIO_SHARP * ref
    return has, dip, ref


def boundary_jump_ratio(images: torch.Tensor, pin: int,
                        max_settle: int = MAX_SETTLE) -> Tuple[float, float, float]:
    """量「钉住区边界」处的帧差倍数：``D(pin-1 → pin) ÷ 段内基线``。

    返回 ``(ratio, d_edge, d_base)``；数据不足返回 ``(0.0, 0.0, 0.0)``。
    纯比值、无量纲 —— 分辨率/步数/LoRA 整体移动量级时结论不变。
    """
    pin = int(pin)
    n = int(images.shape[0])
    if pin <= 1 or n < pin + 2:
        return 0.0, 0.0, 0.0
    diff = _frame_diffs(images[: min(n, pin + max_settle + 2)])
    if diff.numel() <= pin:
        return 0.0, 0.0, 0.0
    d_edge = float(diff[pin - 1].item())          # 钉住区末帧 → 首帧新内容
    inner = diff[2:max(3, pin - 1)]
    d_base = float(inner.median()) if inner.numel() else float(diff.median())
    d_base = max(d_base, _baseline_floor(images))
    return (d_edge / d_base if d_base > 0 else 0.0), d_edge, d_base


def trim_jump_curve(images: torch.Tensor, pin: int,
                    max_settle: int = SETTLE_CAP) -> list:
    """**裁量 vs 跳跃** 曲线：预测「裁掉 settle 帧后，成片缝处会有多大的跳」。

    关键几何（2026-09-15 想通）：成片的缝是
        ``raw[pin-1]``（= 上段末帧的复现，裁后它成为缝前末帧）
      → ``raw[pin+settle]``（= 裁后首帧）
    —— **两者相隔 settle+1 帧**。所以那个"跳"不是相邻帧差，而是**跨过被裁区的「时间跳跃量」**。
    ⇒ **裁得越多，跳得越大**（GG：「裁切 = 时间跳跃 = 跳切」）。节点可在裁之前就把它算出来。

    返回 ``[(settle, 归一跳跃), ...]``；跳跃 = ``MAE(raw[pin+s], raw[pin-1]) ÷ 段内基线``。
    纯比值、无量纲；``settle=0`` 那一项即"只裁钉住区"时的天然跳跃（下界）。
    """
    pin = int(pin)
    n = int(images.shape[0])
    if pin <= 1 or n < pin + 2:
        return []
    f = images.to(torch.float32)
    inner = _frame_diffs(images[: min(n, pin + MAX_SETTLE + 2)])
    base = float(inner[2:max(3, pin - 1)].median()) if inner.numel() > pin else float(inner.median())
    base = max(base, _baseline_floor(images))
    if base <= 0:
        return []
    ref = f[pin - 1]
    out = []
    for s in range(0, max(0, int(max_settle)) + 1):
        i = pin + s
        if i >= n:
            break
        out.append((s, float((f[i] - ref).abs().mean()) / base))
    return out


def observation_profile(images: torch.Tensor, pin: int,
                        scan: int = SETTLE_SCAN) -> dict:
    """节点实测的**多通道观测剖面**：锐度 / 亮度 / 色阶(RGB) / 帧差，覆盖「钉住区之后 scan+1 帧」。

    动机（2026-09-15 GG 要求「不只是锐度，色阶、明暗等都需要」）：
    沉降检测本来就是**三路**（帧差 / 锐度 / 色档），report 只报一路 = **只开了三分之一的窗**。
    把节点实际看到的**各通道原始数**全报出来，人眼与机检才能在同一组数上对话。

    返回 dict：
      ref_sharp   锐度基准（= `detect_settle` 锐度路用的那个）
      ref_rgb     [R,G,B] 体区色档基准（= 色档路用的那个）
      sharp[i]    第 pin+i 帧 锐度 ÷ ref_sharp
      luma[i]     第 pin+i 帧 灰度均值（0-1）
      rgb[c][i]   第 pin+i 帧 R/G/B 均值（0-1）
      diff[i]     第 pin+i-1 → pin+i 的帧差（MAE，0-1 量纲）
    """
    pin = int(pin)
    n = int(images.shape[0])
    out = {"ref_sharp": 0.0, "ref_rgb": [0.0, 0.0, 0.0],
           "sharp": [], "luma": [], "rgb": [[], [], []], "diff": []}
    if pin <= 0 or n < pin + 2:
        return out
    hi = min(n, pin + scan + 2)
    win = images[:hi]
    sh = _sharpness(win)
    ref_lo = pin + SETTLE_REF_OFF
    far = _sharpness(images[ref_lo: min(n, ref_lo + 40)])
    ref = max(float(sh[:pin].median()), float(far.median()) if far.numel() else 0.0)
    out["ref_sharp"] = ref

    f = win.to(torch.float32)
    if f.dim() == 4 and f.shape[-1] in (1, 3, 4):
        rgb = f.mean(dim=(1, 2))                      # [N,C]（ComfyUI IMAGE = [N,H,W,C]）
    else:
        rgb = f.reshape(f.shape[0], -1).mean(dim=1, keepdim=True)
    # 色档基准 = 窗外体区 RGB 均值中位（与色档路同口径）
    body_lo = pin + MAX_SETTLE + 2
    body = images[body_lo: min(n, body_lo + 40)].to(torch.float32)
    if body.shape[0] >= 4:
        brgb = (body.mean(dim=(1, 2)) if (body.dim() == 4 and body.shape[-1] in (1, 3, 4))
                else body.reshape(body.shape[0], -1).mean(dim=1, keepdim=True))
        out["ref_rgb"] = [float(brgb[:, c].median()) if brgb.shape[1] > c else 0.0
                          for c in range(3)]

    diffs = _frame_diffs(win)
    nd = int(diffs.shape[0])
    idx = list(range(pin, min(hi, pin + scan + 1)))
    out["sharp"] = [(float(sh[i]) / ref) if ref > 0 else 0.0 for i in idx]
    out["luma"] = [float(rgb[i].mean()) for i in idx]
    for c in range(3):
        out["rgb"][c] = [float(rgb[i, c]) if rgb.shape[1] > c else 0.0 for i in idx]
    out["diff"] = [float(diffs[i - 1]) if 0 < i <= nd else 0.0 for i in idx]
    return out


def scan_head_repeat(images: torch.Tensor, pin: int,
                     scan: int = REPEAT_SCAN,
                     thresh: float = REPEAT_MAE) -> Tuple[int, float, float]:
    """路径 4：**复现残留**检测——窗内帧与钉住区的最小 MAE。

    动机见模块常量 ``REPEAT_MAE`` 上方注释（RESEARCH_seam_frontier §13 D7）：
    现有三路都看不见复现残留 —— 复现帧**清晰**（锐度路盲）、**色档一致**（色档路盲）、
    **无硬跳**（硬跳路盲）。本路是唯一能看见它的一路。

    返回 ``(settle, signal, reference)``：
      ``settle``    = 从 ``pin`` 起**连续复现**的帧数（超出钉住区、应裁掉的部分）
      ``signal``    = 最后一个复现帧的最小 MAE（越小越像）
      ``reference`` = 阈值本身（便于上层按同一量纲报数）

    **纯自参考**：不与任何全局常数比，只问「这一帧像不像钉住区自己」。
    测不准（窗太短 / ``pin`` 为 0 / 画布退化）⇒ 0 ⇒ 与旧行为逐位一致。

    ⚠ 本路**只会让裁量变大**（上层取 max）⇒ 若误判会多吃内容。
      故 ``REPEAT_MIN_RUN`` 要求连续 ≥2 帧，且阈值收到 5/255（近位级相同）。
    """
    n = int(images.shape[0])
    if pin < 1 or n <= pin:
        return 0, 0.0, float(thresh)
    k = min(int(scan), n - pin)
    if k < REPEAT_MIN_RUN:
        return 0, 0.0, float(thresh)

    small = _canonicalize(images[: pin + k], ANALYSIS_SHORT_SIDE)
    zone = small[pin:]                      # [k,H,W,C]
    pinreg = small[:pin]                    # [pin,H,W,C]

    # 逐帧「到钉住区的最小 MAE」——对 pin 循环，避免 pin×k 整块物化
    best = torch.full((k,), float("inf"), dtype=torch.float32)
    for p in range(pin):
        best = torch.minimum(best, (zone - pinreg[p]).abs().mean(dim=(1, 2, 3)))

    hit = best < float(thresh)
    if not bool(hit.any()):
        return 0, float(best.min().item()), float(thresh)

    # 只认**从 pin 起连续**的那一段：复现残留的形态就是"接着钉住区继续重复"
    run = 0
    for i in range(k):
        if bool(hit[i]):
            run = i + 1
        else:
            break
    if run < REPEAT_MIN_RUN:
        return 0, float(best.min().item()), float(thresh)
    return run, float(best[run - 1].item()), float(thresh)


def detect_settle(
    images: torch.Tensor,
    pin: int,
    max_settle: int = MAX_SETTLE,
    ratio: float = JUMP_RATIO,
) -> Tuple[int, float, float]:
    """在**未裁剪**的段首附近量出真实切换点，返回建议的沉降帧数。

    ``images`` 是完整 decode 结果（长度 N，前 ``pin`` 帧是钉住区的复现）。
    返回 ``(settle, signal, reference)``——signal/reference 的量纲随判定路径不同：
    硬跳路 = 帧差，锐度路 = Laplacian 均方，色档路 = RGB 均值偏离；全**纯比值判定**。
    三路独立出候选，**取 settle 最大者**（不同伪影类型互补，谁检测到得深听谁的）。

    v0.4.1 四层判定（GG 要求的多维度/自参考方案）：

      0. **结构性护栏**（不变）：窄窗贴 ``pin`` 不做全局 argmax；``settle ≤ max_settle``；
         测不准 ⇒ 0 ⇒ 与"只裁钉住区"的旧行为逐位一致，最坏不会更差。
      1. **硬跳路（帧差 · 双门槛 + 验身）**：窗内最大帧差须同时过
         「体 z 分数 > Z_JUMP」（对段体帧差的 median+MAD 标准化——运动剧烈的段
         体差大，同样的跳不值钱）和「比值 > JUMP_RATIO×体中位」两道门；
         再**验身**：跳后 2-5 帧锐度须回到体分布（无一帧深塌陷）——跳完还是糊的
         = 假跳/闪烁，否决。
      2. **锐度路（塌陷-恢复 · 深度证据闸）**：基准 = max(复现区中位, 窗外体区中位)
         （v0.3.2 双基准：复现区自身可能已发软）；窗内须**同时**出现
         「塌陷帧（<0.55×基准）」和「深塌陷证据（最深处 <0.35×基准）」，
         且窗内可见恢复（>0.5×基准，宁少勿多）——沉降 = 最后一个塌陷帧 − pin + 1。
      3. **色档收敛路（v0.4.1，新增）**：注噪/taper 续接的「收敛尾巴」——可见头部
         若干帧的亮度/色档仍在对齐去噪轨迹（重影+低频漂移），锐度法不可见。
         基准 = 窗外体区 RGB 均值的**逐通道中位数**；阈值 = max(GRADE_DEV_Z×体MAD,
         绝对下限)。**必须在窗内观察到收敛**（头部偏离 → 其后全部回归基准）才动刀：
         没有收敛 = 头部本来就是另一档内容的正常延续（如窗外远处切镜），照裁会
         把合法内容裁掉（12.9 类反例）。沉降 = 首个回归帧的下标。
      4. **参考分布全部来自段体**：分辨率/步数/LoRA/内容整体移动量级时，基准同步
         移动——4 步低清、928p 高清、CombatV2、平坦场景共用同一套比值。
    """
    pin, max_settle = int(pin), max(0, int(max_settle))
    if pin <= 0 or max_settle <= 0:
        return 0, 0.0, 0.0
    n = int(images.shape[0])
    if n < pin + 2:
        return 0, 0.0, 0.0
    # ★ 只算用得到的那一小段，别对整段做差（120 帧 448x768 实测 39 ms vs 187 ms）
    win = images[: min(n, pin + max_settle + 2)]
    far_lo = pin + max_settle + 2
    body = images[far_lo: min(n, far_lo + 40)]
    if body.shape[0] < 8:
        body = win                      # 短段：体参考退化到窗内（仍优于任何全局常数）
    diff = _frame_diffs(win)
    inner = diff[2:max(3, pin - 1)]
    baseline = float(inner.median()) if inner.numel() else float(diff.median())
    lo = max(2, pin - 1)
    hi = min(int(diff.shape[0]), pin + max_settle)
    if hi <= lo:
        return 0, 0.0, baseline
    seg = diff[lo:hi]
    j = lo + int(torch.argmax(seg).item())
    val = float(diff[j].item())

    best = (0, 0.0, baseline)           # (settle, signal, reference)——取最大

    # —— 路径 1：硬跳（体 z 分数 + 比值双门槛，过了再验身）——
    b_med, b_mad = _robust_stats(_frame_diffs(body))
    z_denom = max(b_mad, _baseline_floor(win))
    z_jump = (val - b_med) / z_denom if z_denom > 0 else 0.0
    if z_jump > Z_JUMP and val > ratio * max(b_med, _baseline_floor(win)):
        sharp_all = _sharpness(images[: min(n, pin + max_settle + 8)])
        ref_all = max(float(sharp_all[:pin].median()),
                      _robust_stats(_sharpness(body))[0])
        accept = ref_all <= 0.0         # 锐度基准退化 → 无法验身 → 采信跳点
        if not accept:
            after_jump = sharp_all[j + 1: min(j + 5, int(sharp_all.shape[0]))]
            accept = after_jump.numel() == 0 or not bool(
                (after_jump < BLUR_COLLAPSE_RATIO * ref_all).any())
        if accept:
            s = max(0, min(max_settle, j + 1 - pin))
            if s > best[0]:
                best = (s, val, baseline)

    # —— 路径 2：锐度塌陷-恢复（扫描窗 / 参考偏移 / 裁剪上限 三者独立，见 SETTLE_SCAN 注释）——
    # ⚠ 不能用 win：win 的宽度绑在 max_settle 上；本路按 SETTLE_SCAN 独立取窗，
    #   参考区按 SETTLE_REF_OFF 固定偏移（不再随窗漂移）。
    sh_scan = _sharpness(images[: min(int(images.shape[0]), pin + SETTLE_SCAN + 2)])
    if sh_scan.numel() >= pin + 2:
        ref_lo = pin + SETTLE_REF_OFF
        far_sh = _sharpness(images[ref_lo: min(int(images.shape[0]), ref_lo + 40)])
        ref_sh = max(float(sh_scan[:pin].median()),
                     float(far_sh.median()) if far_sh.numel() else 0.0)
        if ref_sh > 0.0:
            zone = sh_scan[pin: pin + SETTLE_SCAN + 1]
            below = zone < BLUR_ZONE_RATIO * ref_sh
            dip = float(zone.min())
            # —— L4 跨尺度仲裁（见 `_blur_collapse`）：真塌陷在**两个分析尺度上都该出现**；
            #    只在单一尺度出现 = 尺度伪影（编码噪声 / 原生分辨率细节噪声）→ 本路不出候选。——
            _ok = True
            if SETTLE_CROSS_SCALE:
                _ok, _d2, _r2 = _blur_collapse(images, pin, ANALYSIS_SHORT_SIDE_ALT)
            if _ok and bool(below.any()) and dip < BLUR_COLLAPSE_RATIO_SHARP * ref_sh:
                last = int(below.nonzero()[-1].item())
                after = zone[last + 1:]
                if after.numel() > 0 and float(after.max()) >= BLUR_RECOVER_RATIO * ref_sh:
                    # 🔴 2026-09-15 修：裁到**恢复点**，不是「最后一个塌陷帧」。
                    #   实测（onerA_cond4）：只裁到塌陷边界（<0.55×）会留下 4 帧
                    #   0.53–0.55× 的「恢复尾巴」——GG 目检 = 「接缝处从模糊变清晰」。
                    #   故继续往后找锐度回到 SETTLE_RECOVER_TARGET×ref 的首帧，从那里起算 settle。
                    # ⚠ 必须**从最后一个塌陷帧之后**开始找恢复点！
                    #   2026-09-15 实测踩到：真实数据的形态是「首帧还算锐(0.79×) → 塌陷 → 恢复」，
                    #   若从窗首开始找，"首个恢复帧"就是第 0 帧 → s=0 → 一帧不裁（settle 从 8 退化成 0）。
                    rec = zone[last + 1:] >= SETTLE_RECOVER_TARGET * ref_sh
                    idx = rec.nonzero()
                    s = (min(SETTLE_CAP, last + 1 + int(idx[0].item())) if idx.numel()
                         else min(SETTLE_CAP, last + 1))
                    if s > best[0]:
                        best = (s, dip, ref_sh)

    # —— 路径 3：色档收敛（v0.4.1）——头部偏离体区色档、且窗内可见回归才动刀——
    rgb_head = win.float().mean(dim=(1, 2))                       # [Nw,3]（量纲随输入）
    rgb_body = body.float().mean(dim=(1, 2))                      # [Nb,3]
    if rgb_head.shape[0] >= pin + 1 and rgb_body.shape[0] >= 4:
        ref_rgb = rgb_body.median(dim=0).values
        devs_head = (rgb_head[pin: pin + max_settle + 1] - ref_rgb).abs().mean(-1)
        devs_body = (rgb_body - ref_rgb).abs().mean(-1)
        _, b_mad_g = _robust_stats(devs_body)
        # 量纲下限用已切出的小窗 + 体区估计，不对整段物化 abs()
        scale = max(_abs_max(win), _abs_max(body)) or 1.0
        thr = max(GRADE_DEV_Z * b_mad_g, GRADE_DEV_FLOOR * scale)
        ok = devs_head <= thr
        if bool(ok.any()):
            c = int(ok.nonzero()[0].item())            # 首个回归基准的帧（窗内偏移）
            if bool(ok[c:].all()):                     # 其后全部回归 = 观察到收敛
                s = min(max_settle, c)
                if s > best[0]:
                    best = (s, float(devs_head[c - 1]) if c > 0 else 0.0, float(thr))

    # —— 路径 4：复现残留（v0.5.0 / RESEARCH_seam_frontier §13 D7）——
    #   现有三路对「模型把钉住区内容又演了一遍」完全盲：那种帧**清晰**、**色档一致**、
    #   **无硬跳** ⇒ 三路都不出候选。本路用「到钉住区的最小 MAE」把它抓出来。
    #   仍取 max ⇒ 契约不变（只会让裁量变大，不会变小）。
    if SETTLE_REPEAT_PATH:
        s4, sig4, ref4 = scan_head_repeat(images, pin, REPEAT_SCAN, REPEAT_MAE)
        s4 = min(max_settle, s4)
        if s4 > best[0]:
            best = (s4, sig4, ref4)

    return best


def describe_head_jump(images: torch.Tensor, scan: int = 40,
                       within: Optional[int] = None) -> str:
    """给日志用的一行接缝自检结论。

    ``within`` 之内才给"再裁 N 帧"这种**可执行**的建议（默认 ``ADVISE_WITHIN``）。
    超出的突变只报位置与数值 —— 那多半是本段自己的真实切镜而不是接缝，
    照它动刀会把新内容裁掉（v0.3.0 之前就是这个行为）。
    """
    if within is None:
        within = ADVISE_WITHIN
    j, jump, baseline = scan_head_jump(images, scan)
    floor = max(baseline, _baseline_floor(images))
    ratio = jump / floor if floor > 0 else 0.0
    if j < 0 or jump <= JUMP_RATIO * floor:
        return ("[H3 Relay] 接缝自检：前 %d 帧无突变（最大帧差 %.2f，段内基线 %.2f）→ 起点干净。"
                % (scan, jump, baseline))
    if j > int(within):
        return ("[H3 Relay] ⚠ 接缝自检：第 %d→%d 帧有突变（%.2f vs 基线 %.2f，比值 %.1f×），"
                "但位置超出自动沉降上限（%d 帧）→ 大概率是本段自己的切镜，不是接缝，不动刀。"
                % (j, j + 1, jump, baseline, ratio, MAX_SETTLE))
    return ("[H3 Relay] ⚠ 接缝自检：第 %d→%d 帧有突变（%.2f vs 基线 %.2f，比值 %.1f×）\n"
            "            → 把「续接裁重叠」的 settle_frames 从 -1（自动）改成 %d 再跑"
            "（多裁掉突变前那帧）。\n"
            "            别改 trim_frames —— 那一格已被连线接管，前端会藏起来，改不了。"
            % (j, j + 1, jump, baseline, ratio, j + 1))


def describe_latent(latent: Any) -> str:
    """一行摘要，用于日志与节点输出。"""
    try:
        parts = streams_from_latent(latent)
    except Exception as e:
        return "无法解析 latent：%s" % e
    bits = []
    for i, t in enumerate(parts):
        name = "视频" if i == 0 else ("音频" if i == 1 else "#%d" % i)
        bits.append("%s%s" % (name, tuple(t.shape)))
    v = parts[0]
    if v.ndim >= 5:
        bits.append("%d 帧 @ %dx%d" % (pixel_frames(int(v.shape[2])), int(v.shape[4]) * 16, int(v.shape[3]) * 16))
    return " | ".join(bits)


# ---------------------------------------------------------------- 0.4.0 拷贝桥
# 机制出处（拆解吸收，均开放许可）：
#   · AIMixer/ComfyUI_MiniMaxH3_Director（Apache-2.0）——latent 硬拷贝 + 噪声掩码 +
#     音频尾拷贝 + NestedTensor 双流打包；
#   · comfyui-minimax-h3-audio-T8（native_masked_context）——掩码走 **ComfyUI 原生
#     H3 契约**（mask=0 保留 / 1 生成，MiniMaxH3.scale_latent_inpaint /
#     mask_row_values，0.30+），与具体采样器无关 → 通用兼容；
#     且 T8 用全 0 硬锁（钉住区零重绘）——与我们 0.3.x 实测「复现=漂移源」同向，
#     设为默认；Director 的 1.0→seam_min 锥形作为实验档保留。
#   · 消费端实测：SelfLiftH3Sampler 原生读 latent["noise_mask"]（[B,1,T,H,W]，
#     post-CFG hook 把 0 区每步钉回 clean anchor）——无需改任何采样器。
# 音频：尾随拷贝只作采样上下文（掩码只做视频流）；可见的声画拼接仍归 TrimAV/组装层。
#
# 0.4.3 新增 "ramp"（噪声斜坡，方向一：历史 = 带噪声等级的软证据）：
#   原生契约本来就支持**连续**掩码值——宿主源码两处实证（2026-09-15 读码）：
#     comfy/ldm/minimax/model.py  forward()：
#       "masked rows run at their own strength: mask value m puts a row at
#        sigma = m * sigma_stream" → 逐 token 的 timestep 标签 = 1 − m·σ；
#     comfy/model_base.py  MiniMaxH3.scale_latent_inpaint()：
#       x_blend_weight = (token_grid_mask − denoise_mask)/(1 − denoise_mask)
#       ——m∈(0,1) 走连续混合，不是四舍五入到两态。
#   外加外层每步输出 blend out = out·m + anchor·(1−m)（RePaint 式逐步回锚）。
#   因此 ramp 档的 m∈(0,1) 语义 = Diffusion Forcing（arXiv:2407.01392）的
#   逐 token 噪声等级 + SDEdit（arXiv:2108.01073）的按强度重绘：缝侧 token
#   以 ramp_top 强度被模型轻度 harmonize（重上色/微调曝光），远端仍硬钉。
#   与 taper 的区别：taper 头部 m=1.0 = 无锚全重绘；ramp 全程 m≤ramp_top<1，
#   每一步都被 (1−m) 权重锚回拷贝尾——是"软证据"，不是"没证据"。
SEAM_TAPER_TOKENS: int = 4
SEAM_MIN_MASK: float = 0.10
SEAM_MIN_FLOOR: float = 0.0
SEAM_RAMP_TOP: float = 0.25
SEAM_RAMP_TOP_MIN: float = 0.0     # 0 = 显式退化为 hard
SEAM_RAMP_TOP_MAX: float = 1.0     # 1.0 是**理论最优**（FIFO-Diffusion Thm 3.3：误差以噪声等级差为上界；
                                   # ramp_top=1.0 让斜坡恰好抵达满噪声 ⇒ 窗边界断崖归零）。
                                   # ⚠ 旧注释写「m=1 即无锚，归 taper 管」是**误判**：ramp 只有最后
                                   # 一个 token 到 top，前面的 token 仍被 (1−m) 锚回拷贝尾；taper 是头部 m=1。
                                   # ⚠ 想在大 ramp_top 下不恶化，必须**同时加长窗口**（窗内台阶 = ramp_top/(n−1)）。
AUDIO_RELEASE_TICKS: int = 6

# —— 0.5.0 `blend`：重叠区双向融合（RESEARCH_seam_frontier §4 方向四）——
# 与 ramp 的关系：**同一套掩码语义、不同的权重曲线**。
#   · ramp  = 线性升（0 → top）
#   · blend = **窗形升**（smoothstep / Hann 式 S 曲线，两端导数为 0 ⇒ 过渡更柔）
# 理论出处：FlowLong / Unified Long Video Inpainting 的滑窗 **Hamming 加权混合**
#   （arXiv:2511.03272 —— 与本仓库最贴的一篇）；SEINE(arXiv:2310.20700) 把过渡段当 inpainting；
#   SynCoS(arXiv:2503.08605) 多段同步耦合共享首尾锚帧；GLC-Diffusion(arXiv:2501.05484) 时序耦合去噪。
# 共同做法：重叠区由**两个独立估计**加权平均，权重取**窗函数**（端点导数小 ⇒ 过渡柔）。
# ⚠ 诚实标注：在本仓库的掩码表述下，blend 与 ramp 是**同一族的不同曲线**，
#   不是新机制；能否胜过 `ramp@0.5`（跳帧 14.9×）是**实测问题**。
SEAM_BLEND_TOP: float = 0.50       # 缝端模型占比（= ramp_top 的对应物）
SEAM_BLEND_TOP_MAX: float = 1.0
BLEND_SHAPES: Tuple[str, ...] = ("smoothstep", "hann")
BLEND_SHAPE_DEFAULT: str = "smoothstep"

# —— 0.6.0 `window`：**对称窗**（两端低、中心高）——
# 与 ramp/blend 的本质区别：那两档**单调上升**（缝端最自由），本档缝端**回落**（缝端重新钉牢）。
# 依据：VideoMerge(arXiv:2503.09926) 正弦窗；Diff-VF(arXiv:2608.05976) WWS
#   「中心权重高、边界权重低」+ 消融「移除 WWS → 窗口边界突然跳跃」（= 本项目症状）。
SEAM_WINDOW_TOP: float = 0.50      # 窗函数峰值（中心处允许的最大重绘自由度）
WINDOW_SHAPES: Tuple[str, ...] = ("sine", "hann")
WINDOW_SHAPE_DEFAULT: str = "sine"

MASK_MODES: Tuple[str, ...] = ("hard", "taper", "ramp", "blend", "window")


def prefix_taper_weights(
    steps: int,
    taper: int = SEAM_TAPER_TOKENS,
    seam_min: float = SEAM_MIN_MASK,
) -> Tuple[float, ...]:
    """拷贝前缀的掩码权重：头部 1.0（**完全重绘**，反正被裁），线性降到缝端 seam_min。

    ⚠️ **这不是「软一点的硬锁」——taper 档下钉住区没有被钉住。**
    掩码语义是 ``denoised = 模型生成 * m + 上段尾 * (1-m)``：m=0 才钉住，m=1 是重绘。
    所以 taper 档每一帧都留 ``seam_min``~100% 的重绘自由度（seam_min=0.3 即缝端仍 30% 重绘），
    段首会被模型改写 ⇒ 观感「续不上」，而 TrimAV 仍按窗口帧数照裁 ⇒ 顺带裁掉真实剧情。
    本档**只用于「渐进接管」对照实验**；真续接请用 ``mask_mode="hard"``。
    （2026-09-15：产线曾误把 taper 当默认跑了 23 次，见 CHANGES 0.4.2 文档节。）
    """
    n = int(steps)
    if n < 1:
        return ()
    taper = max(1, min(int(taper), n))
    head = n - taper
    floor = max(SEAM_MIN_FLOOR, min(1.0, float(seam_min)))
    weights = [1.0] * head
    weights.extend(1.0 + (floor - 1.0) * (float(i + 1) / float(taper)) for i in range(taper))
    return tuple(weights)


def prefix_ramp_weights(
    steps: int,
    ramp_top: float = SEAM_RAMP_TOP,
    ramp_tokens: int = 0,
) -> Tuple[float, ...]:
    """0.4.3 噪声斜坡权重：窗远端 0.0（硬钉）线性升到缝端 ramp_top。

    与 taper 的本质区别：taper 是"重绘自由度"刻度（头 1.0 = 无锚全重绘）；
    ramp 是"重噪强度"刻度——每帧都留 (1−m) 的锚权重，每一步都被采样器按
    (1−m) 拉回拷贝尾，m 只决定该 token 以多大 sigma 参与去噪。
    ramp_top 必须落在 [0, 1]（越界即按端点截断）：m=0 退化成 hard——远端硬钉
    且缝端也无重绘自由度；m→1 则锚权重 (1−m)→0，等于没有锚，那是 taper 干的事，
    widget 侧把上限收到 0.95 防呆。

    ramp_tokens>0 时只让最后这么多 token 参与斜坡，更早的钉住 token 恒 0（硬钉）——
    用于"只松缝、锁运动"的窄斜坡；默认 0 = 整个窗口铺开。

    端点约定：整窗铺开时 token i 取 top·i/(n−1)——远端严格 0（硬钉）、缝端严格 top
    （n=1 时唯一 token 即缝端取 top）；窄斜坡时最后 k 个 token 取 top·(j+1)/k（渐进软化）。
    """
    n = int(steps)
    if n < 1:
        return ()
    top = min(SEAM_RAMP_TOP_MAX, max(SEAM_RAMP_TOP_MIN, float(ramp_top)))
    if int(ramp_tokens) <= 0:
        if n == 1:
            return (float(top),)
        return tuple(top * float(i) / float(n - 1) for i in range(n))
    span = max(1, min(int(ramp_tokens), n))
    weights = [0.0] * (n - span)
    weights.extend(top * float(j + 1) / float(span) for j in range(span))
    return tuple(weights)


def _window_shape(x: float, shape: str) -> float:
    """把 ``x ∈ [0,1]`` 映射成**端点导数为 0**的 S 曲线（单调、0→1）。"""
    x = min(1.0, max(0.0, float(x)))
    if shape == "hann":
        import math
        return 0.5 - 0.5 * math.cos(math.pi * x)      # (1−cos πx)/2
    return x * x * (3.0 - 2.0 * x)                     # smoothstep


def prefix_window_weights(
    steps: int,
    top: float = SEAM_WINDOW_TOP,
    shape: str = WINDOW_SHAPE_DEFAULT,
) -> Tuple[float, ...]:
    """0.6.0 `window` 档：**对称窗**——两端低、中心高（非单调）。

    与 ramp / blend 的**本质区别**：那两档都是**单调上升**（远端钉住、缝端最自由）；
    本档是**窗函数**，缝端那一头重新回到低位（= 缝端重新钉牢）。

    依据（2026-09-19 联网核实原文）：
      · **VideoMerge**(arXiv:2503.09926)：「用 **sine weighting** 替代线性加权……
        minimizes artifacts due to **abrupt change in semantics**」；
      · **Diff-VF**(arXiv:2608.05976) 的 WWS：「窗口**中心**的帧获得**更高**权重，
        靠近**边界**的帧获得**较低**权重」，且消融证明
        「**移除 WWS 导致窗口边界处出现突然的「跳跃」**」——正是本项目的症状。

    ⚠️ 诚实标注：我们的钉住窗**整窗都会被 TrimAV 裁掉**（不进成片），
    所以本档的作用是**塑造模型状态**，而不是"让可见区变柔"。
    缝端那一头重新钉牢（窗函数在 i=n−1 处回落到低位）才是它相对 ramp 的关键差别。
    """
    n = int(steps)
    if n < 1:
        return ()
    t = min(1.0, max(0.0, float(top)))
    if n == 1:
        return (float(t),)
    import math
    if shape == "hann":
        # Hann：端点严格 0（完全硬钉），中心 1
        return tuple(t * (0.5 - 0.5 * math.cos(2.0 * math.pi * (i + 0.5) / n))
                     for i in range(n))
    # sine：端点低但不为 0（留一点自由度，避免整窗死钉）
    return tuple(t * math.sin(math.pi * (i + 0.5) / n) for i in range(n))


def prefix_blend_weights(
    steps: int,
    blend_top: float = SEAM_BLEND_TOP,
    blend_tokens: int = 0,
    shape: str = BLEND_SHAPE_DEFAULT,
) -> Tuple[float, ...]:
    """0.5.0 重叠区**双向融合**权重（RESEARCH_seam_frontier §4 方向四）。

    与 `prefix_ramp_weights` 的关系：**同一套掩码语义（``输出 = 生成·m + 上段尾·(1−m)``）、
    不同的权重曲线**——
      · ramp  = **线性**升（``top·i/(n−1)``）
      · blend = **窗形**升（smoothstep / Hann，两端导数为 0）

    理论依据：重叠区由**两个独立估计**加权平均时，权重取**窗函数**（Hamming 类）——
    端点处权重变化率为 0 ⇒ 与"窗外"的衔接没有折角 ⇒ 过渡更柔
    （FlowLong / Unified Long Video Inpainting 的滑窗 Hamming 混合，arXiv:2511.03272）。

    ``blend_tokens>0`` 时只让最后这么多 token 参与融合，更早的恒 0（硬钉）——
    用于"只融缝、锁运动"。

    端点约定：整窗铺开时 token i 取 ``top·S(i/(n−1))``——远端严格 0、缝端严格 top；
    窄融合时最后 k 个 token 取 ``top·S((j+1)/k)``。
    """
    n = int(steps)
    if n < 1:
        return ()
    top = min(SEAM_BLEND_TOP_MAX, max(0.0, float(blend_top)))
    sh = shape if shape in BLEND_SHAPES else BLEND_SHAPE_DEFAULT
    if int(blend_tokens) <= 0:
        if n == 1:
            return (float(top),)
        return tuple(top * _window_shape(float(i) / float(n - 1), sh) for i in range(n))
    span = max(1, min(int(blend_tokens), n))
    weights = [0.0] * (n - span)
    weights.extend(top * _window_shape(float(j + 1) / float(span), sh) for j in range(span))
    return tuple(weights)


def _nested_pair(video: torch.Tensor, audio: torch.Tensor, template: Any = None) -> Any:
    """打包 AV 双流为 NestedTensor（ComfyUI 运行时），离线环境退化为 list。"""
    try:
        from comfy.nested_tensor import NestedTensor

        return NestedTensor((video, audio))
    except Exception:
        cls = type(template) if template is not None else None
        if cls is not None and cls is not torch.Tensor:
            try:
                return cls((video, audio))
            except Exception:
                pass
    return [video, audio]


def build_continue_latent(
    target: Any,
    prev: Any,
    frames: int,
    mask_mode: str = "hard",
    taper: int = SEAM_TAPER_TOKENS,
    seam_min: float = SEAM_MIN_MASK,
    pin_audio: bool = True,
    ramp_top: float = SEAM_RAMP_TOP,
    ramp_tokens: int = 0,
    blend_top: float = SEAM_BLEND_TOP,
    blend_tokens: int = 0,
    blend_shape: str = BLEND_SHAPE_DEFAULT,
    window_top: float = SEAM_WINDOW_TOP,
    window_shape: str = WINDOW_SHAPE_DEFAULT,
    anchor_latent: Optional[Any] = None,
    anchor_blend: float = 1.0,
) -> Tuple[Dict[str, Any], int, str]:
    """0.4.0 拷贝桥：把上一段 AV 尾部**逐位拷贝**进本段初始 latent + 噪声掩码。

    返回 ``(latent, covered, report)``；``covered`` = 应裁帧数（接 TrimAV 的 trim_frames）。

    与 conditioning 钉帧（H3RelayMotionContext）的本质区别：钉住区**不重绘**——
    mask=0 区每步被采样器钉回拷贝进来的上段尾部 latent，0.3.x 实测的
    「复现发糊/漂移」这一类伪影从机制上消失。三条硬约束（违反即 raise）：
      1. ``frames`` 落在 5+17k 网格且尾段起点 5-token 对齐（复用 video_tail_from_latent）；
      2. 上段与本段分辨率一致；
      3. 拷贝前缀必须给新内容留至少 1 个 token。

    ``mask_mode="ramp"``（0.4.3）把钉住区从"硬事实"改成"软证据"：掩码连续值按
    原生契约就是逐 token 的 sigma 标签（详见上方机制注释），缝侧轻度 harmonize、
    远端硬钉，全程有锚——治硬接缝处色档/曝光"台阶化"。语义与 taper 相反，见
    ``prefix_ramp_weights``。
    """
    if mask_mode not in MASK_MODES:
        raise ValueError(
            "mask_mode 只认 %s，得到 %r" % (" / ".join(MASK_MODES), mask_mode)
        )
    tv = video_from_latent(target)
    pv = video_from_latent(prev)
    if (int(tv.shape[3]), int(tv.shape[4])) != (int(pv.shape[3]), int(pv.shape[4])):
        raise ValueError(
            "拷贝桥禁止跨分辨率：上段 %dx%d ≠ 本段 %dx%d"
            % (int(pv.shape[4]) * 16, int(pv.shape[3]) * 16,
               int(tv.shape[4]) * 16, int(tv.shape[3]) * 16)
        )
    blocks, _offsets, covered = video_tail_from_latent(prev, frames)
    steps = len(blocks)
    total_t = int(tv.shape[2])
    if steps >= total_t:
        raise ValueError(
            "拷贝前缀 %d 步占满本段 %d 步 → 没有新内容可生成；缩短 context_frames 或加长本段"
            % (steps, total_t)
        )

    # tv.clone() 是必须的：下面要原位写前缀，不能污染调用方的 latent。
    # 峰值 ≈ 1×target 视频流（latent 在 GPU 时即 1×显存，尾段 blocks 本身已占 steps/total）。
    video = tv.clone()
    tail_v = torch.cat(blocks, dim=2).to(device=video.device, dtype=video.dtype)

    # 0.5.0 统计纠偏（方向二）：把拷贝前缀的逐通道矩拉向全局锚段。
    # 纠偏只作用于被裁掉的钉住前缀——它是消耗品条件，绝不碰上一段成片；
    # 而采样时每步钉回的就是这份"已复位色档"的上下文 → 本段新内容随之 harmonize。
    stats_desc = ""
    if anchor_latent is not None:
        av = video_from_latent(anchor_latent)
        if (int(av.shape[1]) != int(video.shape[1])):
            raise ValueError(
                "anchor_latent 有 %d 通道，本段有 %d —— 不是同一底模产出的 H3 视频 latent。"
                % (int(av.shape[1]), int(video.shape[1]))
            )
        tail_v = match_stats(tail_v, av, blend=anchor_blend).to(tail_v.dtype)
        stats_desc = "；统计纠偏 blend=%.2f（锚 %s）" % (
            max(0.0, min(1.0, float(anchor_blend))), describe_latent(anchor_latent))
    video[:, :, :steps] = tail_v

    audio = None
    rt = 0
    if pin_audio:
        # 只有真的要钉音频时才要求 target 带音频流——pin_audio=False
        # 必须允许纯视频 latent 走通（参数语义）。
        audio = audio_from_latent(target).clone()
        a_tail, rt, _overhang, _raw, _grid_off = audio_tail_from_latent(
            prev, int(frames), pixel_frames(int(pv.shape[2])))
        rt = max(0, min(int(rt), int(audio.shape[-1]) - 1))
        if rt > 0:
            audio[..., :rt] = a_tail[..., :rt].to(device=audio.device, dtype=audio.dtype)
    else:
        try:
            audio = audio_from_latent(target)     # 有则原样保留（不拷贝、不改动）
        except ValueError:
            audio = None                          # 纯视频桥：只出视频流

    # 掩码与 latent 同设备同 batch：GPU latent + CPU 掩码会在采样器里炸或静默错位
    vmask = torch.ones((int(tv.shape[0]), 1, total_t, int(tv.shape[3]), int(tv.shape[4])),
                       dtype=torch.float32, device=tv.device)
    if mask_mode == "hard":
        vmask[:, :, :steps] = 0.0
        mask_desc = "硬锁（全 0，钉住区零重绘）"
    elif mask_mode == "ramp":
        w = prefix_ramp_weights(steps, ramp_top, ramp_tokens)
        # 权重直接建在掩码设备上，省一次跨设备拷贝（latent 在 GPU 时 vmask 也在 GPU）
        vmask[:, :, :steps] = torch.tensor(w, dtype=torch.float32,
                                           device=vmask.device).view(1, 1, steps, 1, 1)
        mask_desc = "噪声斜坡 0.00→%.2f（每步锚回 (1−m)，软证据档）" % w[-1]
    elif mask_mode == "blend":
        w = prefix_blend_weights(steps, blend_top, blend_tokens, blend_shape)
        vmask[:, :, :steps] = torch.tensor(w, dtype=torch.float32,
                                           device=vmask.device).view(1, 1, steps, 1, 1)
        mask_desc = "重叠区双向融合 0.00→%.2f（%s 窗，两端导数 0 ⇒ 过渡柔）" % (w[-1], blend_shape)
    elif mask_mode == "window":
        w = prefix_window_weights(steps, window_top, window_shape)
        vmask[:, :, :steps] = torch.tensor(w, dtype=torch.float32,
                                           device=vmask.device).view(1, 1, steps, 1, 1)
        mask_desc = ("对称窗 %s（峰值 %.2f）：两端低、中心高 ⇒ **缝端重新钉牢**，"
                     "中心留自由度" % (window_shape, max(w) if w else 0.0))
    else:
        w = prefix_taper_weights(steps, taper, seam_min)
        # 权重直接建在掩码设备上，省一次跨设备拷贝（latent 在 GPU 时 vmask 也在 GPU）
        vmask[:, :, :steps] = torch.tensor(w, dtype=torch.float32,
                                           device=vmask.device).view(1, 1, steps, 1, 1)
        mask_desc = "锥形 %.2f→%.2f（taper=%d）" % (w[0], w[-1], taper)

    out = dict(target) if isinstance(target, dict) else {}
    if audio is None:
        out["samples"] = video          # 纯视频路径：不打包 NestedTensor
    else:
        out["samples"] = _nested_pair(video, audio, tv)
    out["noise_mask"] = vmask
    report = (
        "[H3 Relay] 拷贝桥：写入 %d 步（%d 帧）视频尾 + %d 音频 tick（上下文用%s）；"
        "掩码 %s%s；trim=%d"
        % (steps, covered, rt,
           "" if audio is not None else "／本段无音频流", mask_desc, stats_desc,
           covered)
    )
    return out, int(covered), report


# —— 音频拼接（0.5.0 第 9 节点用；2026-09-19 立）——
# 为什么放进**节点层**（GG 2026-09-19 指令「功能完整地在节点层面实现」）：
#   缝上的两个音频病**都不在单段节点的可达范围内** ——
#     · 编码器 priming：每段 mp4 的 AAC 流头 ~33 ms 近静音（实测 @32k = 1056 样本；
#       同位置节点落盘有内容 −22.9 dBFS、mp4 解码 −66.8 dBFS，差 44 dB）。它在**编码之后**产生。
#     · 交叉曲线：两段内容不相关时，线性淡变（ffmpeg acrossfade 默认 tri）中缝掉 −4.7 dB。
#   两件事都只能由「拼接方」做 ⇒ 那就让**包自己当拼接方**，用户不必碰任何 ffmpeg 命令。
AUDIO_ENCODER_PRIME_MS: float = 33.0     # AAC 编码器 priming 实测值（@32k ≈ 1056 样本）


def join_audio_segments(audios, prime_samples: int = 0, cross_samples: int = 0,
                        curve: str = "qsin", segment_seconds: float = 0.0,
                        align_seconds: float = 0.0):
    """把多段音频按序接成**一条**，返回 ``(audio, report)``。两种语义：

    **J-cut 时间轴守恒（``align_seconds > 0``，推荐）**——``align`` = 每缝的**画面裁量**
    （秒，= 裁帧数/fps）；``segment_seconds`` = 每段音频有效时长（秒，= 该段视频帧数/fps；
    节点 PCM 通常比 mp4 长，必须截）。第 i≥1 段从其 ``align`` 处进入成片时间轴（与裁后
    画面第 0 帧对齐），其前 ``cross`` 秒素材作**渐入前奏**：先**电平对齐**到前段尾
    （±6 dB 限幅 + 0.995 峰值护栏），再按 ``curve`` 权重**叠加**到前段尾部（前段原位保留）
    —— 电影业 J-cut：声音先走、时间轴不缩短、缝上无电平凹陷：
    输出总长 = n·segment_seconds − (n−1)·align，**与裁后视频严格等长**。

    **旧缩短语义（``align_seconds = 0`` 且 ``segment_seconds = 0``，仅兼容/对照）**：
    等功率交叉且 cross 计入时间轴（输出 = Σ(段长−prime) − (n−1)·cross）——
    ⚠ 每缝使后段音频相对画面**提前 cross 秒**、片尾需补 cross 静音。

    ``prime_samples``：编码器 priming（节点 PCM 源 = 0；mp4 源每段头 ~33 ms）。
    采样率与声道以**第一段**为准。
    """
    if not audios:
        raise ValueError("join_audio_segments：至少需要一段音频。")
    al_s = float(align_seconds)
    if al_s < 0:
        raise ValueError("align_seconds 不能为负（%r）。" % al_s)
    ws = []
    sr0 = ch0 = None
    dt0 = None
    for i, a in enumerate(audios):
        wf, sr, _lead, dt = _audio_parts(a)
        if sr0 is None:
            sr0, ch0, dt0 = sr, int(wf.shape[0]), dt
        wf = _match_channels(_resample_to(wf, sr, sr0), ch0).float()
        k = int(prime_samples)
        if k > 0:
            if k >= int(wf.shape[-1]):
                raise ValueError("去 priming %d 样本 ≥ 第 %d 段长度 %d 样本，段太短。"
                                 % (k, i + 1, int(wf.shape[-1])))
            wf = wf[..., k:]
        if segment_seconds > 0:
            seg_n = int(round(float(segment_seconds) * sr0))
            if seg_n <= 0 or seg_n > int(wf.shape[-1]):
                raise ValueError("segment_seconds=%.3fs（%d 样本）超出第 %d 段实际 %d 样本。"
                                 % (segment_seconds, seg_n, i + 1, int(wf.shape[-1])))
            wf = wf[..., :seg_n]
        ws.append(wf)
    n = int(cross_samples)
    align_n = int(round(al_s * sr0)) if al_s > 0 else 0
    out = ws[0]
    notes_extra = ""
    # 🔴 跨段响度匹配（2026-09-19 第三轮迭代）：各段 BGM 独立生成，段间整体电平差可达
    #    ~8 dB（实测 A 中位 −14 vs B −22）—— 拼接点上表现为「瞬时卡顿」的真正来源之一。
    #    参考电平 = 第 1 段常态 RMS；第 i≥1 段**整段**缩放（限幅 ±6 dB，峰值护栏 0.995）。
    _ref_rms = float(out.pow(2).mean().sqrt())
    for _wi in range(1, len(ws)):
        _w = ws[_wi]
        _cur = float(_w.pow(2).mean().sqrt())
        if _cur > 0.0 and _ref_rms > 0.0:
            _g_db = 20.0 * math.log10(_ref_rms / _cur)
            _g = 10.0 ** (max(-6.0, min(6.0, _g_db)) / 20.0)
            if abs(_g_db) > 6.0:
                notes_extra += "｜ 第 %d 段响度匹配限幅 %+.1f dB" % (_wi + 1, _g_db)
            _pk = float(_w.abs().max())
            if _pk > 0.0:
                _g = min(_g, 0.995 / _pk)          # 峰值护栏
            ws[_wi] = _w * _g
    for w in ws[1:]:
        if align_n > 0:
            # —— J-cut 守恒：前奏（电平对齐后渐入叠加）+ 本段从 align 起全量 ——
            ent = min(align_n, int(w.shape[-1]) - 1)
            m = min(n, ent, int(out.shape[-1]))
            if m > 0:
                pre = w[..., ent - m:ent]                       # 前奏素材（进入点之前）
                t = torch.linspace(0.0, 1.0, m, device=out.device, dtype=torch.float32)
                f2 = torch.sin(t * math.pi / 2.0) if curve == "qsin" else t
                if curve not in ("qsin", "tri"):
                    raise ValueError("未知交叉曲线 %r（只认 qsin / tri）。" % (curve,))
                tail = out[..., -m:]
                # 🔴 对齐目标 = max(前段尾, 本段常态中位)：只对齐到「已衰落的 A 尾」会让
                #    缝上仍是两个低电平相加（jA 首轮实测缝上 −8.2 dB 更深）⇒ 取两者较大者，
                #    保证交叉窗电平不低于任一侧的常态。
                _w_med = float(w.pow(2).mean().sqrt())           # 本段（进入段）整体 RMS = 常态电平
                tgt = max(float(tail.pow(2).mean().sqrt()), _w_med)
                cur = float(pre.pow(2).mean().sqrt())
                if cur > 0.0 and tgt > 0.0:
                    g_db = 20.0 * math.log10(tgt / cur)
                    g = 10.0 ** (max(-6.0, min(6.0, g_db)) / 20.0)
                    if abs(g_db) > 6.0:
                        notes_extra += "｜ 前奏电平对齐限幅 %+.1f dB" % g_db
                    pre = pre * g
                tail = tail + pre * f2                           # 前段原位保留 + 前奏渐入
                pk = float(tail.abs().max())
                if pk > 0.995:
                    tail = tail * (0.995 / pk)
                    notes_extra += "｜ 交叉窗峰值 %.3f 已按护栏回退" % pk
                out = torch.cat([out[..., :-m], tail], dim=-1)
            out = torch.cat([out, w[..., ent:]], dim=-1)
        else:
            # —— 旧缩短语义（兼容/对照；⚠ 每缝使后段音频提前 cross 秒）——
            if n > 0:
                m = min(n, int(out.shape[-1]), int(w.shape[-1]))
                t = torch.linspace(0.0, 1.0, m, device=out.device, dtype=torch.float32)
                if curve == "qsin":                       # 等功率（四分之一正弦）
                    f1, f2 = torch.sin(t * math.pi / 2.0), torch.cos(t * math.pi / 2.0)
                elif curve == "tri":                      # 线性（旧口径，仅对照；不相关内容掉 −4.7 dB）
                    f1, f2 = 1.0 - t, t
                else:
                    raise ValueError("未知交叉曲线 %r（只认 qsin / tri）。" % (curve,))
                out = torch.cat([out[..., :-m],
                                 out[..., -m:] * f1 + w[..., :m] * f2,
                                 w[..., m:]], dim=-1)
            else:
                out = torch.cat([out, w], dim=-1)
    lens = [int(w.shape[-1]) for w in ws]
    total = int(out.shape[-1])
    shaped = out.reshape(1, ch0, total).to(dt0) if dt0 is not None else out
    if align_n > 0:
        exp_total = int(round(float(segment_seconds) * sr0)) * len(ws) - (len(ws) - 1) * align_n
        mode = "J-cut 守恒（align=%.3fs/缝，与裁后视频等长）" % al_s
    else:
        exp_total = sum(lens) - (len(ws) - 1) * n
        mode = "旧缩短语义（⚠ 缝后音画错位 cross 秒，仅兼容/对照）"
    rep = ("[H3 Relay] 音频拼接：%d 段 → %d 样本（%.3fs）｜ %s\n"
           "           去 priming %.1f ms/段 ｜ 段有效时长 %s ｜ %s 交叉 %.1f ms/缝 ｜ "
           "段长(样本) %s ｜ 采样率 %d ｜ 声道 %d ｜ 长度%s%s"
           % (len(ws), total, total / float(sr0), mode,
              int(prime_samples) / float(sr0) * 1000.0,
              ("%.3fs" % segment_seconds) if segment_seconds > 0 else "全量",
              curve, n / float(sr0) * 1000.0, lens, sr0, ch0,
              ("守恒 OK" if total == exp_total else "⚠ 不守恒（期望 %d）" % exp_total),
              notes_extra))
    return {"waveform": shaped, "sample_rate": sr0}, rep
