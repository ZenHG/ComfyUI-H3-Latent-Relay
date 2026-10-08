# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""relay_core.upscale —— 画质域 AV latent 分块放大（拆包 → 逐块 → 回包）。

本模块由 `relay_core.py` 拆分而来（2026-10-07）；代码**逐字搬运**，未改任何算式。
对外仍由 `relay_core/__init__.py` 全量再导出 ⇒ 旧调用点（`relay_core.xxx`）不变。
"""

from __future__ import annotations

import torch

# ---------------------------------------------------------------------------
# 22 · 画质域 · AV latent 分块放大（时间维切块 + 重叠加权拼接）
# ---------------------------------------------------------------------------
#   背景：上游学习式 3D 潜空间放大器（MIT，`MinimaxH3LatentUpscaler3D`）
#   ① 只吃普通 [B,C,T,H,W]，接不住 H3 的 **AV 打包 latent（NestedTensor）**；
#   ② 内部分块**写死 32 帧**，用户无法按显存调块数。
#   故这里只做「拆包 → 逐块放大 → 回包」的适配层，**零 patch 第三方包**，
#   并把块数交还给用户。拼接数学与上游同口径（两侧 replicate 填充 + 线性渐变
#   权重 + 按累计权重归一），以免换实现就换接缝。
# ---------------------------------------------------------------------------

UPSCALE_CHUNK_FRAMES_DEFAULT = 32      # 上游内部分块口径 ⇒ 我们的默认块长
UPSCALE_OVERLAP_DEFAULT = 5            # = 3D 放大模型 temporal_kernel（实测权重形状第 3 维）


def max_upscale_chunks(total_frames: int, overlap: int = UPSCALE_OVERLAP_DEFAULT) -> int:
    """给定总 latent 帧数与重叠，返回**合法的最大块数**（每块至少还能留 1 帧非重叠区）。"""
    t = int(total_frames)
    ov = int(overlap)
    if t < 1:
        raise ValueError("total_frames 必须 ≥ 1，得到 %d。" % t)
    if ov < 0:
        raise ValueError("overlap 不能为负，得到 %d。" % ov)
    return max(1, t // (2 * ov + 1))


def upscale_chunk_plan(total_frames: int, chunks: int = 1,
                       overlap: int = UPSCALE_OVERLAP_DEFAULT) -> dict:
    """规划「沿时间维分块」的窗口（块数自选 + 数值合规校验，非法一律 raise）。

    块长 ``chunk_frames = ceil(T / chunks)``，实际块数 = ``ceil(T / chunk_frames)``
    （向上取整 ⇒ 末块可能更短，**不会**多出空块）。

    合规硬约束（fail-closed，报错都给可照做的建议值）：
      · ``chunks ≥ 1``；``overlap ≥ 0``
      · **每块帧数 ≥ 2·overlap + 1** —— 否则本块的重叠区会互相吞干净，
        拼接后出现「权重累计为 0」的帧（放大结果被静默丢成黑帧）。
    """
    t = int(total_frames)
    n = int(chunks)
    ov = int(overlap)
    if t < 1:
        raise ValueError("total_frames 必须 ≥ 1，得到 %d。" % t)
    if ov < 0:
        raise ValueError("overlap 不能为负，得到 %d。" % ov)
    if n < 1:
        raise ValueError("chunks 必须 ≥ 1（1 = 整段一次过），得到 %d。" % n)
    cf = -(-t // max(1, n))                                  # ceil
    if cf < 2 * ov + 1:
        n_max = max_upscale_chunks(t, ov)
        raise ValueError(
            "chunks=%d ⇒ 每块只有 %d 帧，塞不下两侧各 %d 帧重叠（需 ≥ %d 帧）。\n"
            "    overlap=%d 时最大合法块数 = %d（T=%d）；要更多块请同时调小 overlap。"
            % (n, cf, ov, 2 * ov + 1, ov, n_max, t))
    spans = []
    start = 0
    while start < t:
        seg_start, seg_end = start, min(t, start + cf)
        out_start, out_end = max(0, seg_start - ov), min(t, seg_end + ov)
        spans.append({
            "out_start": out_start, "out_end": out_end,
            "blend_head": seg_start - out_start, "blend_tail": out_end - seg_end,
        })
        start += cf
    return {"total_frames": t, "chunk_frames": cf, "n_chunks": len(spans),
            "requested_chunks": n, "overlap": ov, "spans": spans}


def temporal_tile_upscale(video: torch.Tensor, upscale_fn, plan: dict) -> torch.Tensor:
    """按 ``plan`` 逐块放大 ``video[B,C,T,H,W]`` 并加权拼回（时间维不变、只放大空间）。

    ``upscale_fn(seg)`` 收 ``[B,C,t,H,W]`` 吐 ``[B,C,t,H2,W2]``（**帧数不许变**，
    变了当场 raise —— 否则拼接会静默错位）。块内是模型的全部工作集 ⇒ 显存峰值 = 一块。
    """
    was4 = video.dim() == 4                           # [B,C,H,W] 单帧：与上游同口径，进 4D 出 4D
    if was4:
        video = video.unsqueeze(2)
    if video.dim() != 5:
        raise ValueError("期望视频 latent [B,C,T,H,W]（或单帧 [B,C,H,W]），得到 %s。" % (tuple(video.shape),))
    b, c, t, h, w = (int(x) for x in video.shape)
    if t != int(plan["total_frames"]):
        raise ValueError("plan 的帧数 %d 与 latent 的 %d 不符。" % (plan["total_frames"], t))
    ov = int(plan["overlap"])
    out_full = weight_full = None
    for sp in plan["spans"]:
        o0, o1 = int(sp["out_start"]), int(sp["out_end"])
        seg = _pad_frames(video, o0, o1, ov)      # 越界处复制首/末帧（上游同口径）
        got = upscale_fn(seg)
        if not torch.is_tensor(got):
            raise ValueError("upscale_fn 必须返回张量，得到 %s。" % type(got))
        if int(got.shape[2]) != int(seg.shape[2]):
            raise ValueError(
                "放大块改变了帧数（%d → %d）：本分块拼接只允许**空间**放大。"
                % (int(seg.shape[2]), int(got.shape[2])))
        n_valid = o1 - o0
        valid = got[:, :, ov:ov + n_valid]
        if int(valid.shape[2]) != n_valid:
            raise ValueError("块输出可用帧数不足：需要 %d，只有 %d。" % (n_valid, int(valid.shape[2])))
        wt = torch.ones(n_valid, device=video.device, dtype=video.dtype)
        bh, bt = int(sp["blend_head"]), int(sp["blend_tail"])
        if bh:
            wt[:bh] = torch.arange(1, bh + 1, device=video.device, dtype=video.dtype) / (bh + 1)
        if bt:
            wt[-bt:] = torch.arange(bt, 0, -1, device=video.device, dtype=video.dtype) / (bt + 1)
        view = (1, 1, n_valid, 1, 1)
        if out_full is None:
            out_full = torch.zeros(b, int(got.shape[1]), t, int(got.shape[3]), int(got.shape[4]),
                                   device=got.device, dtype=got.dtype)
            weight_full = torch.zeros(1, 1, t, 1, 1, device=got.device, dtype=got.dtype)
        out_full[:, :, o0:o1] += valid.to(out_full.dtype) * wt.view(view).to(out_full.dtype)
        weight_full[:, :, o0:o1] += wt.view(view).to(weight_full.dtype)
        del seg, got, valid
    if out_full is None:
        raise ValueError("plan 里没有任何块（spans 为空）。")
    zero_w = int((weight_full.squeeze() <= 0).sum())
    if zero_w:
        raise ValueError("拼接后有 %d 帧权重为 0（重叠配置不合规）。" % zero_w)
    out = out_full / weight_full.clamp(min=1e-8).to(out_full.dtype)
    return out.squeeze(2) if was4 else out


def _pad_frames(video: torch.Tensor, o0: int, o1: int, ov: int) -> torch.Tensor:
    """取原帧闭开区 ``[o0-ov, o1+ov)``，越界处**复制首/末帧**（与上游 replicate 同口径）。

    padded 序列 = [复制 ov] + 全序列 + [复制 ov]，其下标 i 对应原帧 ``i-ov``
    ⇒ 原帧区间 ``[o0-ov, o1+ov)`` 在 padded 里正好是 ``[o0, o1+2·ov)``，无需裁剪。
    """
    t = int(video.shape[2])
    if not (0 <= o0 <= o1 <= t):
        raise ValueError("窗口越界：需要 0 ≤ out_start ≤ out_end ≤ T，得到 %d/%d/%d。" % (o0, o1, t))
    if ov <= 0:
        return video[:, :, o0:o1].contiguous()
    pre = video[:, :, :1].expand(-1, -1, ov, -1, -1)
    post = video[:, :, -1:].expand(-1, -1, ov, -1, -1)
    padded = torch.cat([pre, video, post], dim=2)
    return padded[:, :, o0:o1 + 2 * ov].contiguous()
