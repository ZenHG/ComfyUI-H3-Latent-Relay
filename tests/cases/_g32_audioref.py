# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
#
# ruff: noqa: F821  —— 本分片**不是独立模块**。
#   它由 `tests/test_relay_core.py` 用 `exec(compile(src, path, "exec"), globals())`
#   顺序执行：与拆分前的单文件脚本**同一命名空间、同一顺序**。
#   所以 `check` / `CORE` / `NT` / `make_latent`、以及**上游分片定义的夹具**都看得见，
#   但不是本文件定义的 ⇒ F821 必报。
#   真正的兜底是**执行**：这条脚本每次全量跑，任何未定义名会当场 NameError，不会静默。

# ------------------------------------------------- 第 32 组：音频参考窗自动定长（0.6.25）
# 动机（本仓作者）：「参考音频的时长太短会对下一段音色还原度不足」—— 旧口径窗长 = 视频钉住窗
# （实测 22 帧 = 0.925s）⇒ 只装得下最后一个说话人。手填秒数是在「音色不足」与
# 「模型复述上段台词」之间猜 ⇒ 改为**让素材自己决定**：从尾部往前累计够 N 秒有声内容。
print()
print("[32] 音频参考窗自动定长：一槽多人音色（判据全相对量 + 逐项退化可观测）")


def _ar32(voiced_s, tail_s, sr=32000, amp=0.05, seed=32):
    """造一段 latent 音频：`tail_s` 秒静默 + 尾部往前 `voiced_s` 秒**有声**。

    🔴 有声段用「噪声 × 3 Hz 音节调制」而不是方波 —— 方波每 3 ms 才跳变一次，
       **变化点密度**远低于真实语音（真实语音每 1 ms 格都在变）⇒ 用方波当夹具会把
       「累计够 N 秒**有声格**」算成 N×3 秒（本轮 32.3/32.4 就先栽在这上面）。
    """
    n = int((tail_s + voiced_s) * sr)
    g = torch.Generator().manual_seed(seed)
    x = (torch.rand(1, 1, n, generator=g) - 0.5) * amp * 0.02        # 静默底噪
    s0 = int(tail_s * sr)
    m = 1.0 + 0.6 * torch.sin(2 * math.pi * 3.0 * torch.arange(n - s0) / sr)
    v = (torch.rand(1, 1, n - s0, generator=g) - 0.5) * 2.0 * amp
    x[..., s0:] = v * m                                               # 有声：幅度一直在变
    return x


_v32 = 2.0
_a32 = _ar32(_v32, 1.0)
_sec32, _i32 = CORE.auto_audio_ref_seconds(_a32, 32000)
check("32.1 尾部 2 秒有声 + 1 秒静默 ⇒ 自动窗 ≈ 2.0s（静默**不计入**）",
      1.9 <= _sec32 <= 2.1, "窗 %.3fs ｜ %s" % (_sec32, _i32))
check("32.2 判据是**相对量** ⇒ 整体降到 −40 dBFS 窗长不变（同一段素材只改增益）",
      abs(CORE.auto_audio_ref_seconds(_a32 * 0.01, 32000)[0] - _sec32) < 0.15,
      "%.3f vs %.3f" % (CORE.auto_audio_ref_seconds(_a32 * 0.01, 32000)[0], _sec32))
_s32b = CORE.auto_audio_ref_seconds(_ar32(_v32, 8.0), 32000)[0]
check("32.3 🔴 **静默段加长 8 倍，窗长不变**（旧 `P50` 判据会漂到 6.69s）",
      abs(_s32b - _sec32) < 0.15, "1s静默 %.3fs vs 8s静默 %.3fs" % (_sec32, _s32b))
# 离群脉冲：幅度取**素材自身峰值 × 30**（绝对值无所谓 —— 判据要证明的是
# 「脉冲再大也不改窗长」）。不依赖任何外部素材 ⇒ 单测自包含。
_oo32 = _ar32(_v32, 1.0)
_oo32[0, 0, _oo32.shape[-1] // 3] = float(_oo32.abs().max()) * 30.0   # 注入离群脉冲
_s32c = CORE.auto_audio_ref_seconds(_oo32, 32000)[0]
check("32.4 🔴 **离群脉冲不改窗长**（旧 `max` 判据会被脉冲顶死；有声格 215→2）",
      abs(_s32c - _sec32) < 0.6, "无脉冲 %.3fs vs 有脉冲 %.3fs" % (_sec32, _s32c))
_long32 = CORE.auto_audio_ref_seconds(_ar32(9.0, 0.5), 32000)
check("32.5 长有声（9s）⇒ 窗**封顶 6.0s** 且报告点名（不许静默截断）",
      _long32[0] <= CORE.AUDIO_REF_MAX_S + 1e-6
      and (_long32[1]["clipped"] is (abs(_long32[0] - CORE.AUDIO_REF_MAX_S) < 1e-6)),
      "窗 %.3fs clipped=%s" % (_long32[0], _long32[1]["clipped"]))
_zero32, _zi32 = CORE.auto_audio_ref_seconds(torch.zeros(1, 1, 32000), 32000)
check("32.6 全零 ⇒ 窗 0 + **说清原因**（不给参考，而不是给一段噪声）",
      _zero32 == 0.0 and bool(_zi32["reason"]), "%s" % _zi32["reason"])
_nan32, _ni32 = CORE.auto_audio_ref_seconds(
    torch.cat([torch.zeros(1, 1, 100), torch.tensor([[[float("nan")]]]),
               torch.zeros(1, 1, 100)], -1), 32000)
check("32.7 含 NaN ⇒ **fail-closed**（窗 0 + 点名 NaN；不许静默退回「很安静」）",
      _nan32 == 0.0 and "NaN" in _ni32["reason"], "%s" % _ni32["reason"])
_e32, _ei32 = CORE.auto_audio_ref_seconds(torch.zeros(1, 1, 0), 32000)
check("32.8 **空音频**不抛（旧写法 `reshape(-1, 0)` 会 RuntimeError 炸链）",
      _e32 == 0.0 and bool(_ei32["reason"]), "%s" % _ei32["reason"])
_tiny32 = CORE.auto_audio_ref_seconds(_ar32(0.05, 0.1), 32000)
check("32.9 有声不足 `min_voiced_s` ⇒ 给出**全部**有声（不足也 > 0，不返回 0）",
      0.0 < _tiny32[0] < _v32, "窗 %.3fs" % _tiny32[0])
# 32.10–32.15 · 端到端：plan_relay 的优先级与 notes 可观测性
_p32 = lambda **kw: CORE.plan_relay(cur, prev, 22, **kw)
_n32 = "\n".join(_p32().notes)
check("32.10 默认（不传秒数）⇒ **自动**窗长（notes 出现「自动」）",
      "自动" in _n32, "t=%d ｜ %s" % (_p32().audio_ref["ref_audio_t"], _n32[:70]))
check("32.11 自动时 notes 必须**写明按什么定的**（铁律 15：自动决策也要可观测）",
      "有声" in _n32 and "可能复述" in _n32, _n32[:90])
_p32s = _p32(audio_ref_seconds=4.0)
_ns32 = "\n".join(_p32s.notes)
check("32.12 显式秒数**覆盖**自动（4.0s ⇒ 160 步 @24fps）",
      _p32s.audio_ref["ref_audio_t"] == 160, "t=%d" % _p32s.audio_ref["ref_audio_t"])
check("32.13 显式秒数时 notes 写明**一个槽可含多人** + 未实测的复述风险",
      "多个说话人" in _ns32 and "可能复述" in _ns32, _ns32[:90])
_anc32 = _p32(voice_anchor={"samples": torch.randn(1, 2, 2, 37)})   # 37 步 ≈ 0.925s（短锚）
check("32.14 接了声锚 ⇒ 自动改用**声锚自身**；短锚（0.925s ≈ 钉住窗）⇒ 窗长**逐位同旧口径**",
      _anc32.audio_ref["ref_audio_t"] == 37 and "声锚" in "\n".join(_anc32.notes),
      "t=%d ｜ %s" % (_anc32.audio_ref["ref_audio_t"], _anc32.notes[0][:80]))
check("32.15 `audio_frames` 显式给了 ⇒ 自动**不抢**（显式优先；96 帧 = 4.0s = 160 步，"
      "正好与 32.12 撞上 ⇒ 改用 39 帧 = 1.625s = 65 步 才能分辨）",
      _p32(audio_frames=39).audio_ref["ref_audio_t"] == 65,
      "t=%d（自动会给 %d）" % (_p32(audio_frames=39).audio_ref["ref_audio_t"],
                                _p32().audio_ref["ref_audio_t"]))
# 32.16–32.17 · 0.6.27：接了声锚也自动，原料 = **声锚自身**。
# 动机：用户把一段**多人录音**接进声锚 ⇒ 旧口径（恒 = 视频钉住窗 0.925s）只有最后一个人
# 的音色进得来。修法 = 自动算法喂声锚（它只认"给它的那段音频的有声尾巴"，与原料是谁无关）。
_g40 = torch.Generator().manual_seed(32)
# ⚠️ 有声段的**幅度必须一直在变**（`0.04/0.06` 交替）—— 恒定幅度 ⇒ 逐格 RMS 的一阶差分
#    全为 0 ⇒ 自动算法只认得出 1 个有声格、窗长直接跑到整段（实测踩过）。
_anc_long_src = torch.cat([torch.zeros(1, 1, 20),                                   # 0.5s 静默
                           torch.tensor([0.02, 0.06]).repeat(100).view(1, 1, 200)],  # 5s 有声
                          -1).unsqueeze(2).repeat(1, 1, 2, 1)                   # [1,1,2,220] @40Hz
_anc32l = _p32(voice_anchor={"samples": _anc_long_src})
check("32.16 🔴 长锚（5s 有声）⇒ 窗**取满上限**（本仓作者：单人参考越长音色越好）—— "
      "旧策略停在 2s（80 步），现在把 5s 全吃进来（200 步）",
      _anc32l.audio_ref["ref_audio_t"] == 200 and "取满上限" in "\n".join(_anc32l.notes),
      "t=%d" % _anc32l.audio_ref["ref_audio_t"])
check("32.17 长锚时 notes 点明原料 = **声锚**，且复述风险按原料**分开陈述**（不许张冠李戴）",
      "声锚" in "\n".join(_anc32l.notes) and "自己的语音" in "\n".join(_anc32l.notes),
      _anc32l.notes[0][:110] if _anc32l.notes else "(空)")

