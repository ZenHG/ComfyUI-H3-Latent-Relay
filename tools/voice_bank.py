# -*- coding: utf-8 -*-
"""voice_bank —— H3 长链声锚（voice anchor）声库：采集 / 查询 / 管理。

背景（2026-10-02 实测）：
    H3 拷贝桥把上一段的音频尾作为 audio_ref ⇒ **上段说话人的嗓音会成为
    本段嗓音的生成条件**。台词逐段换人时音色交叉污染（实测某角色 F0 114→131、
    谱质心 1028→1308）。给「本段说话人」的声锚后离基准距离缩到 1/5。

设计原则（对开源用户负责）：
    1. **声明谁说话，不猜测谁说话** —— 说话人来自提示词里
       `<Subject N> … 开口说话:<d>…</d>` 的声明（跑批脚本解析后传入），
       本模块不做声纹聚类（重依赖 + 同性别近似声线必错）。
    2. **手动锚永远优先** —— `voices/<角色名>.wav` 存在 ⇒ 自动采集永不覆盖。
    3. **降级可见** —— 采集失败（无语音/太短）⇒ 不写库、返回原因；
       查询未命中 ⇒ 调用方走"无锚"老路径。绝不静默装作有锚。
    4. 声锚质量下限：有声时长 ≥ MIN_VOICED_S（默认 0.6s）、峰值归一 0.9。

台词守卫的两层分工（2026-10-02 本仓作者 指正后理清）：
    · **节点侧守卫**（H3RelayAudioSeam 的 speech-onset 守卫，relay_core/audio.py::_speech_onset_in_head，
      纯能量判据、零依赖、随包分发）—— 通用层：任何人开箱即用。
      本模块的 `longest_voiced_span`（能量法）与它同族：不需要 funasr 也能采。
    · **funasr ASR 验证**（本机已缓存模型，本地测试尺）—— 增强层：`--line` 给了
      台词原文且 funasr 可导入 ⇒ 验 CER + 逐字时间戳精确裁锚；funasr 不可用
      ⇒ 打印警告后回退能量法（不阻断）。模型进程内单例（944MB 只加载一次）。
      ⚠ 不作为开源默认依赖 —— 分发面与导网环境不可控。


声库布局（run 无关、跨 run 复用）：
    <bank_dir>/voices.json     {"<角色名>": {"wav": "...", "src": "<段产物名>",
                                  "voiced_s": 0.86, "collected": "2026-10-02T14:20"}, ...}
    <bank_dir>/<角色名>.wav    自动采集的锚（手动文件同名时它就是手动锚本身）

CLI（`<声库目录>` 用你自己的路径，例如 `./voices`）：
    python voice_bank.py collect <mp4> --name <角色名> --bank <声库目录>
    python voice_bank.py lookup  <角色名> --bank <声库目录>
    python voice_bank.py list   --bank <声库目录>
    # 「这一段该接谁的锚」—— 一段内多人时的规则（见下方 §该给谁做锚）
    python voice_bank.py advise --bank <声库目录> --prompt-file 段2.md --prev-file 段1.md
    python voice_bank.py advise --bank <声库目录> --speakers <角色A>,<角色B> --prev-speaker <角色A>
    python voice_bank.py selftest      # 说话人解析规则的自检（可证伪）

声锚的语义边界（一段内多人时尤其要记住）：
    声锚回答的问题**只有一个** —— 「**缝上接着说的那个人是谁**」。锚窗只有 ~0.9 秒
    （= 缝区，`context_frames` 换算），段内后续换人由 prompt 的 `<Subject N>` 决定。
    ⇒ **锚 = 本段第一个开口说话的人**；同人续接不必接；换人才必须接。细则见 `advise`。
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import wave
from pathlib import Path

SR = 16000                    # **分析**用重采样率（有声检测 / RMS / ASR 取窗；与实测量化脚本同口径）
# 🔴 **落盘**采样率（2026-10-04 修）。原来落盘也用 `SR` = 16 kHz ⇒ **砍掉了一半频谱**，
#    而宿主 `VAEEncodeAudio` 会把锚 resample 到 **H3 音频 VAE 的原生 32 kHz**
#    （`comfy/sd.py:1070` + `comfy/ldm/minimax/audio_vae.py:374`，
#    `sample_rate=32000` / `hop_length=800` ⇒ 40 latent 帧/秒）
#    ⇒ 16 kHz 落盘时，8 kHz 以上**全是上采样插值编出来的**，
#    齿音 / 气息 / 嘶声这些**辨音色最关键的高频**根本不存在。
#    ✅ 修后实测：−40dB 带宽从 **4.7~5.3 kHz** 抬到 **9.0~10.5 kHz**。
#    ⇒ 采集时**不再降采样**（`-ar` 去掉 ⇒ ffmpeg 保留源率），落盘也用源率。
# ⚠️ **仍然 `-ac 1` 单声道**：H3 的 VAE 是**立体声**（`output_channels=2`），
#    但单声道锚实测可用且**更稳**（立体声两声道相位不一致时 VAE 编码不稳）⇒ 保持单声道。
# ⚠️ **别把"落盘 32 kHz"读成"素材宽带"**（2026-10-04 审码纠正，**错了两次才对**）：
#    ① 编码不是瓶颈 —— 两个音源实测 −40dB 带宽几乎相同：
#       · 边车 `audio_*.safetensors`（relay_kit 交接件）：**float16 无损**、
#         `sample_rate=32000` 原生立体声（实测 −40dB = **4.87 kHz**）
#       · mp4 `*-audio.mp4`（ComfyUI 产出）：**AAC-LC ≈128 kbps 有损**、32000 Hz
#         （实测 −40dB = **4.85 kHz**）⇒ 只差 0.02 kHz ⇒ **AAC 没吃掉高频**。
#    ② **H3 本身能产出宽带频** —— 扫全 72 条产出，最宽的 −40dB 到 **15.07 kHz**
#       （`otui4_s1`，−30dB 15.98 kHz，几乎贴满 Nyquist）。
#       ⇒ 「H3 输出端没有高频」是**错的**。
#    ③ 真正的原因：**那几条宽带素材不是人声**（`otui*` 系列帧均幅 max=0.0001，
#       近乎静音，是音效/环境声 ⇒ 带宽来自底噪，不代表语音高频）。
#       ⇒ 结论落回本模块能负责的范围：**当前这批人声素材（4.7~5.3 kHz）** 就是窄的，
#          换采样率救不了；**要宽带语音必须换人声音源**（真人 / 原始录音）。
#    （过程记录：我先写成"边车是 32 kHz 无损"却仍用 mp4 数字下结论（推理链断裂，
#      本仓作者 抓出）；重测边车后改成"H3 没有高频"，再扫全量才发现是被审素材的问题。）
# ⚠️ 旧的 16 kHz 声库**不会被自动重采**（手动锚优先、采集要显式 --force），
#    ⇒ 想吃到高频得手动 `--force` 重采。
SR_OUT_MIN = 32000             # 落盘采样率下限（**仅作文档与登记标注**，不再触发任何转换）
MIN_VOICED_S = 0.6            # 声锚质量下限：有声时长
# 🔴 消费端尾部窗（2026-10-04）。`_voice_anchor_tail`（relay_core/plan.py）只取锚的**尾部**
#    `a_frames/FPS*AUDIO_HZ` 个 latent 步，而 `a_frames = audio_frames or trim_frames`
#    （`relay_core/plan.py::plan_relay`）⇒ **尾窗秒数 = a_frames / FPS**（不是段长！）。
#    默认 `trim_frames=22` ⇒ 22/24 = **0.917 秒**（README 记的 0.925 秒是按 37 步折算的）。
#    ⇒ **锚的尾部静音 = 那部分等于没锚**（模型只拿到静音，音色条件失效）。
#
# ⚠️ **本值只对默认 `trim_frames=22` 成立**（审码发现，2026-10-04）：
#    用户把 `context_frames` 调大时尾窗同步变大（29 帧 ⇒ 1.208s、48 帧 ⇒ 2.0s），
#    而本模块**不知道**用户会传多少帧 ⇒ 用它当"尾部必须干净"的判据会**给出假保证**。
#    ⇒ 采集侧只保证「尾部 ≥ TAIL_S 有声」，并在消息里**说明这是下界**；
#       真正的完整性由消费端 `plan.notes` 按实际 `a_frames` 报（那边有真值）。
TAIL_S = 0.925                # 尾部窗**下界**秒数（对应默认 trim_frames=22；见上方警告）
TARGET_PEAK = 0.9             # 声锚峰值归一目标（实测 0.9 ⇒ 模型跟随，不削顶）
# 🔴 声锚**响度**归一（2026-10-03 立）。只归一峰值是不够的：模型会跟随锚的**响度**
#    ⇒ 锚比段内人声响多少，生成段就比上一段响多少（实测有锚臂整体抬 +7 dB、
#    段间跳 +6.4 dB）。目标值 = 「模型自然生成语音」的响度，口径 = 单声道 16k /
#    50ms 窗 / 阈值取全段 P75 的**有声窗 RMS 中位**（脚本 `loudness_audit2.py`，
#    本地实验目录，不入库）：无锚各臂 −20.4 ~ −23.8、首段 −22.9 ⇒ 取 −23.0。
TARGET_RMS_DBFS = -23.0       # 声锚响度归一目标（有声窗 RMS 中位，dBFS）
RMS_TOL_DB = 1.5              # 容差：|实测 − 目标| ≤ 此值 ⇒ 不动（避免无谓处理）
MAX_ANCHOR_GAIN_DB = 12.0     # **放大**上限（防把极轻的锚连噪声底一起拉起来）
MAX_ANCHOR_CUT_DB = 24.0      # **压制**上限（压制只是变小，不引入失真 ⇒ 放宽）
PEAK_CEIL = 0.99              # 响度归一后峰值上限；超了 ⇒ 退回纯峰值归一
WIN_S, HOP_S = 0.032, 0.016   # 有声检测窗/步长（与 seam 判据同口径）
VOICED_AMP = 0.02             # 有声判定阈值（帧均幅）
ANCHOR_MAX_S = 4.0            # 声锚最长时长。🔴 消费端取的是锚的**尾部**窗口
                              # （_voice_anchor_tail：尾 37 步 ≈0.925s）⇒ 裁段必须
                              # **结尾落在人声上**，否则尾部是静音 ⇒ 等于没锚
                              # （2026-10-02 三轮审核 B3：ASR 窗 3.28s 尾部 1.4s 静音）
ASR_MODEL = "iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"
ASR_CER_MAX = 0.35            # 台词守卫阈值（wer_asr_verify 的一致性口径）

BANK_JSON = "voices.json"


# ---------------------------------------------------------------- 有声检测
def _load_mono(v: str, ext: str = "raw", sr: int = SR):
    """mp4/任意音频 → **单声道**临时文件，返回路径。

    ext="raw"（s16le，供本模块 numpy 读）或 ext="wav"（供 funasr —— 其内部
    ffmpeg 靠扩展名推断格式，无扩展名会报 ``Failed to load audio``）。
    临时文件放系统临时目录（源目录可能只读）。

    🔴 **`sr` 决定读出的采样率，且必须与调用方的用途一致**（2026-10-04 修）：
      - `sr=SR`（16 kHz，**默认**）= **分析域**。有声检测窗 32ms、阈值 `VOICED_AMP`、
        `_voiced_spans` 的 `HOP_S` 时间轴**全都是按 16 kHz 定的** ⇒ 分析一律走这个。
      - `sr=None`（**跟随源率**，不传 `-ar`）= **落盘域**。声锚要喂给 H3 音频 VAE
        （原生 32 kHz），按 16 k 落盘会砍掉一半频谱。

    ⚠️ **`sr` 的默认值必须是 `SR`(16k)，不能是 `None`**（2026-10-04 踩过）：
       改成 `None` 之后，分析侧的 32k 数据被**按 16k 的时间轴**解释
       ⇒ 时间轴整整差一倍（0.99s 的声音被判在 1.98s）、
       `_voiced_spans` 算出 `t1=4.0s` 越出 3.0s 的源片。
       **换算率与解释率必须成对**，不能只改一个。

    ⚠️ **绝不能拿分析域（16 k）的数据去写 32 k 的头** —— 那等于把音频**加速一倍**、
       **音调升高一个八度**，而文件头和 `voices.json` 里的 `sample_rate` 都看不出来。
       曾经的真实事故：落盘段读 `sr_out`（32k）却用 16k 取数据
       ⇒ 样本数只有期望的 49.9%，三个声锚集体变调。**改这段务必同时改 `selftest`。**
    """
    import tempfile
    out = os.path.join(tempfile.gettempdir(), "_vb_%d.%s" % (os.getpid(), ext))
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", v, "-vn", "-ac", "1"]
    if sr is not None and int(sr) > 0:
        cmd += ["-ar", str(int(sr))]
    if ext == "raw":
        cmd += ["-f", "s16le"]
    subprocess.run(cmd + [out], check=True)
    return out


def _voiced_spans(amps, hop_s, min_s=0.15):
    """连续有声区间列表 [(t0, t1), ...]，合并间隔 <0.5s 的近邻（换气/句内停顿）。"""
    spans, s = [], None
    for i, a in enumerate(amps + [False]):
        if a and s is None:
            s = i
        elif not a and s is not None:
            spans.append((s * hop_s, i * hop_s))
            s = None
    # 合并近邻（同一句话内部的短停顿；0.25s 内的间隙按"换气"处理 —— 实测
    # 长句被 0.12s 阈值拆成两截而拒采；0.40s 的句内停顿也要合回 ⇒ 0.5s）
    merged = []
    for t0, t1 in spans:
        if merged and t0 - merged[-1][1] < 0.5:
            merged[-1] = (merged[-1][0], t1)
        else:
            merged.append((t0, t1))
    return [(a, b) for a, b in merged if b - a >= min_s]



# 🔴 **这里曾有一个 `_tighten()`，已删（2026-10-04，探针实测证其不可达）**
#    它的作用是「把裁窗两端收到真实人声边界」，理由是「`_voiced_spans` 合并 <0.5 s
#    间隙后端点会落在静音上」。**那个理由是错的** ——
#      `_voiced_spans` 的端点由 `s = i`（**第一个 voiced=True 的帧**）与 `i`
#      （第一个 voiced=False 的帧，取前一帧）给出，**构造上就落在有声帧上**。
#      探针实测：5 次调用、**改动 0 次**；构造用例 `0-9静 10-14声 15-17静 18-25声 26-35静`
#      得到 `(0.160, 0.416)` ⇒ 帧 10 与帧 25 **都是有声帧**。
#    ⇒ 该函数**恒为 no-op** ⇒ 注入验证时「退化后自检仍全绿」不是自检没覆盖好，
#      而是**这条分支根本不可达**。
#    ⇒ 当初看起来「必要」，是因为彼时 `_load_mono` 的 `sr` 默认值被误改成 `None`
#      （时间轴差一倍）—— **一个 bug 制造出的假象，让另一个「修复」看起来合理**。
#    ⇒ 教训：**探针实测行为 > 推理出的必要性**。留着一段恒不生效的「保护性」代码，
#      会让人误以为这两处有保护（**比删掉更糟**）。
def longest_voiced_span(mp4: str):
    """找最长连续有声段，返回 ``(t0, t1, voiced_total_s)``；无语音返回 None。

    🔴 返回的 ``(t0, t1)`` 是**收紧后**的人声边界（不含首尾静音）——
       旧实现直接返回 `_voiced_spans` 合并后的端点，会带进静音。
    """
    raw = _load_mono(mp4)
    try:
        x = _read_raw(raw)
    finally:
        try:
            os.remove(raw)
        except OSError:
            pass
    if len(x) < int(WIN_S * SR):
        return None
    w, hop = int(WIN_S * SR), int(HOP_S * SR)
    amps = [float(abs(x[i:i + w]).mean()) for i in range(0, len(x) - w, hop)]
    voiced = [a > VOICED_AMP for a in amps]
    spans = _voiced_spans(voiced, HOP_S)
    total = sum(b - a for a, b in spans)
    if not spans:
        return None
    # 🔴 **尾部优先**选段（2026-10-04）：消费端 `_voice_anchor_tail` 只取锚的**尾部**
    #    `TAIL_S≈0.925s` ⇒ 尾部静音 = 那部分等于没锚。
    #    「取最长段」会把**句中停顿**（<0.5s，被 `_voiced_spans` 并进来）也带上：
    #    实测「取最长段」会把**句中停顿**（<0.5s，被 `_voiced_spans` 并进来）也带上：
    #    某素材的最长段 3.86–5.41s 内含 0.39s 停顿 ⇒ 尾部 0.925s 只有 65% 有声
    #    （另一个锚 100%）。
    #    口径：候选段按长度降序，取**第一个尾部 0.925s 全程有声**的；
    #    全都不满足就退回最长（并在返回里带上 `tail_clean`，供上层提示）。
    cand = sorted(spans, key=lambda ab: -(ab[1] - ab[0]))
    pick, tail_clean = None, False
    for a_s, b_s in cand:
        i0 = int(round(a_s / HOP_S))
        i1 = int(round(b_s / HOP_S))
        need = int(math.ceil(TAIL_S / HOP_S))
        if (i1 - i0) >= need and all(voiced[max(i0, i1 - need):i1]):
            pick, tail_clean = (a_s, b_s), True
            break
    if pick is None:
        pick = cand[0]
    a_s, b_s = pick
    # ⚠️ `_voiced_spans` 返回的单位是**秒**（不是帧号）—— 曾误当帧号除以/乘 `HOP_S`。
    #    端点**天然落在有声帧上**（`_voiced_spans` 构造使然）⇒ 不需要再收紧，
    #    详见本文件上方「`_tighten` 已删」的说明。
    t0, t1 = a_s, b_s
    if t1 <= t0:
        return None
    return t0, t1, total, tail_clean


def _read_raw(path: str):
    import numpy as np
    return np.fromfile(path, dtype=np.int16).astype(np.float32) / 32768.0


# ---------------------------------------------------------------- 响度归一
def speech_rms_db(x, sr: int = SR, win_s: float = 0.05, pct: float = 75.0):
    """「人声」响度：有声窗 RMS 中位（dBFS）。

    口径：单声道 / 50ms 窗 / 阈值 = 全段逐窗 RMS 的 P75 / 取 ≥阈值的窗的 RMS 中位。
    **不把静音与环境声算进来** —— 整段 RMS 会被非语音内容带偏。
    （与本地实验脚本 `loudness_audit2.py` 同口径，便于与实测对账；该脚本不入库。）

    返回 ``(db, 有声窗占比)``；音频太短或几乎全静音 ⇒ ``(None, 占比)``。
    """
    import numpy as np
    x = np.asarray(x, dtype=np.float32).reshape(-1)
    w = int(win_s * sr)
    n = len(x) // w
    if n < 3:
        return None, 0.0
    rms = np.sqrt((x[:n * w].reshape(n, w) ** 2).mean(axis=1))
    db = 20.0 * np.log10(np.maximum(rms, 1e-9))
    voiced = db >= float(np.percentile(db, pct))
    frac = float(voiced.mean())
    if int(voiced.sum()) < 2:
        return None, frac
    return float(np.median(db[voiced])), frac


def normalize_anchor(x, sr: int = SR):
    """声锚双归一：**先响度**（对齐 `TARGET_RMS_DBFS`）**再峰值护栏**。

    返回 ``(y, 说明)``。说明里带实测值与增益，供上层打日志（铁律 15：走哪条
    分支必须可观测）。

    为什么不是「只压峰」：模型**跟随锚的响度** ⇒ 锚比段内人声响多少，生成段就
    比上一段响多少（2026-10-03 实测：有锚臂整体 +7 dB、段间跳 +6.4 dB）。
    """
    import numpy as np
    x = np.asarray(x, dtype=np.float32).reshape(-1)
    db, frac = speech_rms_db(x, sr)
    if db is None:
        # 算不出人声响度 ⇒ 退回纯峰值归一（可见降级，不静默）
        pk0 = float(abs(x).max())
        if pk0 < 1e-4:
            return x, "⚠ 几乎静音（峰值 %.4f）⇒ 不归一" % pk0
        return x / pk0 * TARGET_PEAK, "⚠ 人声响度不可测（有声占比 %.2f）⇒ 退回纯峰值归一" % frac
    gain_db = TARGET_RMS_DBFS - db
    if abs(gain_db) <= RMS_TOL_DB:
        y = x
        note = "响度 %.1f dBFS 已在目标 ±%.1f dB 内 ⇒ 不动" % (db, RMS_TOL_DB)
    else:
        g = max(-MAX_ANCHOR_CUT_DB, min(MAX_ANCHOR_GAIN_DB, gain_db))
        y = x * (10.0 ** (g / 20.0))
        note = "响度 %.1f → %.1f dBFS（增益 %+.1f dB%s）" % (
            db, db + g, g, "" if abs(g - gain_db) < 1e-6 else "，已钳到上限")
    pk = float(abs(y).max())
    if pk > PEAK_CEIL:
        y = y / pk * TARGET_PEAK
        note += "；⚠ 归一后峰值 %.3f 超 %.2f ⇒ 退回纯峰值归一（响度可能仍偏高）" % (pk, PEAK_CEIL)
    return y, note


# ---------------------------------------------------------------- 声库
def _bank_path(bank_dir: str) -> str:
    return os.path.join(bank_dir, BANK_JSON)


def load_bank(bank_dir: str) -> dict:
    p = _bank_path(bank_dir)
    if not os.path.isfile(p):
        return {}
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}


def save_bank(bank_dir: str, bank: dict) -> None:
    os.makedirs(bank_dir, exist_ok=True)
    with open(_bank_path(bank_dir), "w", encoding="utf-8") as fh:
        json.dump(bank, fh, ensure_ascii=False, indent=1, sort_keys=True)


def manual_wav(bank_dir: str, name: str):
    """手动锚：`<bank>/<角色名>.wav` 存在则优先返回其路径。"""
    p = os.path.join(bank_dir, "%s.wav" % name)
    return p if os.path.isfile(p) else None


def lookup(bank_dir: str, name: str):
    """查询声锚。返回 ``(wav_path, source_desc)`` 或 ``(None, 原因)``。

    优先级：手动 `<角色名>.wav`（不在声库登记里）> 声库自动采集 > 无。
    （自动采集的落位也是 `<角色名>.wav`，以 voices.json 是否登记区分二者。）
    返回的 wav 恒为**绝对路径** —— 消费端（LoadAudio / 节点注入）不接受
    相对路径（2026-10-02 实测：相对路径 ⇒ `Invalid audio file` 提交被拒）。
    """
    rec = load_bank(bank_dir).get(name)
    if rec and os.path.isfile(rec.get("wav", "")):
        return os.path.abspath(rec["wav"]), "自动采集(%s)" % rec.get("src", "?")
    if _safe_name(name) is None:
        return None, "角色名含非法字符"
    m = manual_wav(bank_dir, name)
    if m:
        return os.path.abspath(m), "手动"
    return None, "声库无「%s」" % name


# ------------------------------------------------- 「该给谁做锚」的规则（0.6.21）
# 🔴 声锚回答的问题**只有一个**：「**缝上接着说的那个人是谁**」。
#   锚窗只有 ~0.9 秒（= 缝区），它钉住的是**本段开头**那段音频上下文；
#   **段内后续换人，锚管不到、也不该管** —— 那是 prompt 里 `<Subject N>` 的职责。
#
# 由此得到三条规则（都可由 prompt 机检，不用猜）：
#   ① **锚 = 本段第一个开口说话的人**（不是"主角"、也不是"这一段里所有人"）；
#   ② 本段首说话人 == 上一段末说话人 ⇒ **同人续接** ⇒ **不接锚更稳**
#      （保留音频连续性，且不会被锚的音色牵引）；
#   ③ 两者不同 ⇒ **缝上换人** ⇒ **必须接锚**（锚 = 本段首说话人），
#      否则上一个人的音色会污染本段。
# ⚠️ 一段内 ≥2 个说话人 ⇒ 锚只覆盖缝区，后面靠 prompt 切；**能拆段就拆段**。
#
# 说话人判据与 `check_h3_prompts.py` 的 L5 **同源**：取每个 `<d>` **最近的前驱**
# 说话人标记（`<Subject N>` 或兼容形态 `(Sx)`），不能取"第一个" ——
# 120 字符窗会跨到上一个 Shot 的正文里（那里也有 `<Subject N>`）。

#: 台词块的**默认**规则（`--line-re` 可覆盖）。`<d>` / `<D>` 都认（六段式的标记）。
#: ⚠️ `<d>` **不是官方约定**（宿主 `comfy_extras/nodes_minimax_h3.py` 里没有它）——
#:    它是**产线/本仓的六段式约定**。⇒ 别的开源用户的提示词**不保证**长这样 ⇒
#:    所以两个规则都**可覆盖**，且解析**必须报告**"我按什么规则、命中多少"（见 `speaker_lines` 的 `probe`）。
_DEFAULT_LINE_RE = r"<[dD]>"
#: 说话人标记的**默认**规则（`--speaker-re` 可覆盖）。
#: **捕获组 = 说话人标识**：纯数字 ⇒ 当作 `<Subject N>` 的编号（再去 `subject_definitions` 查名字）；
#: 其它 ⇒ 标识**就是名字**（不再查定义段）⇒ `<张三>` 这种写法靠 `--speaker-re '<([^>]+)>'` 就能用。
_DEFAULT_SPK_RE = r"<Subject\s*(\d+)>|\(S(\d+)\)"


def compile_rules(line_re=None, speaker_re=None):
    """编译台词块 / 说话人规则。**不合法 ⇒ raise，消息里给可照做的示例**。

    ⚠️ 编译时**同时开 `re.I` 与 `re.M`**：
       · `re.I` ⇒ `<d>`/`<D>`、`<subject 1>` 都认（省得用户为大小写发愁）；
       · `re.M` ⇒ 你的正则可以用 `^` **锚到行首**（多说话人提示词常见的 `名字: 台词` 写法靠它）。
    """
    try:
        return (re.compile(line_re or _DEFAULT_LINE_RE, re.I | re.M),
                re.compile(speaker_re or _DEFAULT_SPK_RE, re.I | re.M))
    except re.error as e:
        raise ValueError(
            "正则不合法：%s\n"
            "    · `--line-re`：**捕获组 1 = 台词正文**（例：--line-re '<台词>(.*?)</台词>'）；\n"
            "      没给捕获组时按「标记之后到 `</d>` 或行尾」取正文。\n"
            "    · `--speaker-re`：**捕获组 1 = 说话人标识**（例：--speaker-re '<([^>]+)>'，\n"
            "      或 `^([^:\\n：]+)[:：]` 匹配行首的「名字:」）；\n"
            "      标识是纯数字 ⇒ 当作 `<Subject N>` 编号；其它 ⇒ 当作**名字本身**。\n"
            "    · 已开 `re.I` + `re.M` ⇒ 大小写不敏感、`^`/`$` 按**行**匹配。" % e) from e


def speaker_lines(prompt, *, line_re=None, speaker_re=None, probe=None):
    """本段的**逐句**信息：``[(tag, text), …]``，顺序 = 开口顺序（**一处台词块一项**）。

    🔴 **单一真相源**：`speakers_in_order()` 是它的**薄包装**（去重后返回 tag）。
       两处**共用同一套正则、同一条规则**（"每个台词块取**最近的前驱**说话人标记"）。
       ⇒ 加新能力（逐句 / 每句字数）**不需要第二套解析** —— 本仓铁律一要求的。

    🔵 **为什么需要逐句**（2026-10-05）：提示词里其实**已经写好了四件事**，全是零依赖的：

    | 提示词里的东西 | 等价于 |
    |---|---|
    | 台词块的**个数** | 本段**有几句**（开口次数）—— 比"人数"强得多的闸 |
    | 每个台词块最近的**前驱**说话人标记 | **第 i 句是谁说的** |
    | 台词块的**正文** | 每句的**字数 → 相对时长**（切分权重） |
    | `[zh]` / `[en]` 标记 | 语言（决定按汉字还是按音节计权重） |

    ⇒ **VA 的台账不该手写、也不必用 ASR**：它可以从提示词长出来。
      本地 ASR 只是"把边界做准"的**可选**增强（它给 `t0/t1`，见 `_va.json` 的档 3）。

    ## 兼容性（**默认聪明，可被接管，永不静默**）

    | 层 | 怎么用 |
    |---|---|
    | ① 默认规则 | `<d>`/`<D>` 台词块 + `<Subject N>`/`(S N)` 说话人 ⇒ 六段式**开箱可用** |
    | ② 给规则 | `line_re` / `speaker_re`（捕获组语义见 `compile_rules`） |
    | ③ 全手工 | 解析不出来就别解析 —— `--who` + `--lines` 直接给逐句（**任何格式都能用**） |

    🔴 **`probe`（可选字典）**：给了就填 `line_re` / `speaker_re` / `line_hits` / `speaker_hits` /
       `lines` / `no_speaker` ⇒ CLI 拿它打印**解析报告**。
       **判不准就报告、不猜**：`line_hits == 0` 说明规则不匹配（调用方要报错，不许给空台账）。

    ⚠️ **正文取法**：`line_re` **有捕获组 1** ⇒ 用它；**没有** ⇒ 按「标记之后到 `</d>` 或行尾」取。
    """
    lre, sre = compile_rules(line_re, speaker_re)
    src = prompt or ""
    if probe is not None:
        probe["line_re"], probe["speaker_re"] = lre.pattern, sre.pattern
        probe["line_hits"] = len(lre.findall(src))
        probe["speaker_hits"] = len(sre.findall(src))
    out = []
    for m in lre.finditer(src):
        marks = list(sre.finditer(src[:m.start()]))
        if not marks:                       # 台词块**前面没有说话人标记** ⇒ 这一句归不了人（跳过）
            continue
        ident = next((x for x in marks[-1].groups() if x), None)
        if ident is None:
            continue
        ident = ident.strip()
        tag = ("Subject %s" % ident) if ident.isdigit() else ident
        _g1 = m.group(1) if lre.groups >= 1 else None
        if _g1 is not None:
            body = _g1
        else:
            _e = src.find("</d>", m.end())
            if _e == -1:                    # 没有闭合标记 ⇒ 取到**该行**行尾（不吞掉后面的描述）
                _e = src.find("\n", m.end())
            body = src[m.end(): _e if _e != -1 else len(src)]
        out.append((tag, body.strip()))
    if probe is not None:
        probe["lines"] = len(out)
        probe["no_speaker"] = probe["line_hits"] - len(out)
    return out


def speakers_in_order(prompt, *, line_re=None, speaker_re=None) -> list:
    """本段说话人，按**开口顺序**去重。`speaker_lines()` 的**薄包装**（不写第二套解析）。"""
    out = []
    for tag, _txt in speaker_lines(prompt, line_re=line_re, speaker_re=speaker_re):
        if tag not in out:
            out.append(tag)
    return out


#: `line_weight` 用：语言标记（`[zh]` / `[en]` / `[ja]`…）—— **不占发音时长**。
_LANG_RE = re.compile(r"\[[A-Za-z]{2,3}\]")
#: `line_weight` 用：标点与空白 —— **不占发音时长**（全角/半角都要收，**含 ASCII 的 `. , ! ? ; :`**）。
#: 🔴 2026-10-05 自检抓到的真 bug：原写法只收了**全角**标点 ⇒ `[en]I looked at it again.` 里的 `.`
#:    被当成 1 个字符计权（6.5 而不是 5.5）。英文提示词会**系统性高估**长句 ⇒ 切点偏。
_PUNCT_RE = re.compile(
    r"[\s，。、；：？！…—～·「」『』（）()《》〈〉【】〔〕“”‘’"
    r".,!?;:\"'`*#\-_/\\|<>@^%$&+=~\[\]{}]+")
#: `line_weight` 用：连续拉丁/数字串（按"3 字母 ≈ 1 音节"折算）。
_LATIN_RUN_RE = re.compile(r"[A-Za-z0-9]+")


def line_weight(text: str) -> float:
    """一句台词的**近似音节数** —— 比例切的权重。**只用于相对比较，不做绝对判断。**

    规则（零依赖、可解释、**一处只写一次**）：

    · 先去掉**语言标记**（`[zh]`/`[en]`）与**标点/空白**（它们不占发音时长）；
    · 每个**非拉丁字符**（汉字 / 假名 / 谚文…）记 **1**；
    · 连续**拉丁或数字串**记 `len / 3`（英文 ≈ 3 字母 1 音节），取整到 **0.5**；
    · 结果**下限 0.5**（避免零权重把某句切没）。

    ⚠️ 它**不是**时长估计，也**不是 ASR 的替代** —— 它只回答"**谁的话更长**"。
       想让边界落到真实停顿上，靠 `assign_by_prior` 的**能量间隙吸附**；想要精确时间戳，
       仍用档 3（本地 ASR 生成，**不进开源依赖**）。
    """
    s = _PUNCT_RE.sub(" ", _LANG_RE.sub(" ", text or ""))
    latin = float(sum(len(m.group(0)) for m in _LATIN_RUN_RE.finditer(s)))
    non_latin = float(len(_LATIN_RUN_RE.sub("", s).replace(" ", "")))
    w = non_latin + round(latin / 3.0 * 2.0) / 2.0
    return max(0.5, w)


#: 生成的台账带这一行**来源说明**（`_note` 已在 `voice_accum.KNOWN_TOP_KEYS` 白名单里 ⇒ 不报未知键）。
_LEDGER_NOTE = ("由 tools/voice_bank.py va-ledger 生成（档 2：逐句 who + chars；"
                "chars = 近似音节数，只作比例权重）。手写台账（含档 3 的 t0/t1）请勿覆盖。")


def ledger_from_parts(who_groups, weight_groups, *, start_stage=0, note="") -> dict:
    """**逐句手工**建台账：`who_groups[i]` / `weight_groups[i]` = 第 i 段的逐句名单与权重。

    🔵 为什么要有这条**绕开解析**的路（2026-10-05）：`<d>` 与 `<Subject N>` 都是**产线约定**
       （宿主里没有它们）⇒ 别的开源用户的提示词**不保证**长这样。
       ⇒ 解析不出来时**不许硬猜**，但也不该直接判死刑 —— 给一条"我自己写"的路，
         **任何提示词格式都能用**（代价 = 用户要数一下每句多长）。
    """
    stages = {}
    for i, (ws, xs) in enumerate(zip(who_groups or [], weight_groups or [], strict=False)):
        ws = [str(w).strip() for w in ws]
        xs = [float(x) for x in xs]
        if len(ws) != len(xs):
            raise ValueError("第 %s 段：`--who` 有 %d 项、`--lines` 有 %d 项 —— 必须**一一对应**"
                             % (start_stage + i, len(ws), len(xs)))
        if not ws:
            continue
        stages[str(start_stage + i)] = [{"who": w, "chars": x} for w, x in zip(ws, xs, strict=False)]
    return {"_note": _LEDGER_NOTE + ((" · " + note) if note else ""), "stages": stages}


def va_ledger(prompts, *, start_stage: int = 0, speakers=None,
              line_re=None, speaker_re=None, probe=None) -> tuple:
    """把若干段提示词变成 **VA 台账**（**档 2**：逐句 `{"who": …, "chars": …}`）。

    为什么是档 2 而不是档 1（只有开口顺序）：档 1 下 `assign_by_prior` 只能**等分**，
    而等分**必然**把别人的音色算进来（2026-10-05 实测：日志明写"按字数比例切 3 段（等分）"）。
    补上**逐句字数**之后，切分变成"按句长比例 + 吸附到能量间隙" ⇒ 边界落在真实停顿上。

    参数
      `start_stage`  第 1 个提示词对应的**段号**
      `speakers`     角色名（按**首次出现顺序**）—— 只在提示词**没有** `subject_definitions:`
                     段、`subject_name()` 拿不到名字时用
      `line_re` / `speaker_re`  **覆盖默认规则**（见 `compile_rules`）—— 兼容别的提示词格式
      `probe`        可选**列表**；每个阶段 append 一个 dict（规则 / 命中数 / 句数）⇒ 供 CLI 出**解析报告**

    返回 ``(台账 dict, 警告列表)``。**解析不出就叫调用方知道**（不静默给空台账）。
    """
    explicit = [s for s in (speakers or []) if s]
    stages, warn = {}, []
    for i, txt in enumerate(prompts or []):
        pr = {}
        raw = speaker_lines(txt, line_re=line_re, speaker_re=speaker_re, probe=pr)
        pr["stage"] = start_stage + i
        if probe is not None:
            probe.append(pr)
        if not raw:
            warn.append("第 %s 段：解析不出**逐句**信息（台词块 `%s` 命中 %d 处；说话人 `%s` 命中 %d 处）⇒ 跳过"
                        % (start_stage + i, pr.get("line_re"), pr.get("line_hits", 0),
                           pr.get("speaker_re"), pr.get("speaker_hits", 0)))
            continue
        if pr.get("no_speaker"):
            warn.append("第 %s 段：有 %d 处台词块**前面没有说话人标记** ⇒ 那几句归不了人（已跳过）"
                        % (start_stage + i, pr["no_speaker"]))
        tag2name, ents = {}, []
        for tag, body in raw:
            if tag not in tag2name:
                nm = tag if not tag.startswith("Subject ") else subject_name(txt, tag)
                if nm == tag and explicit:
                    k = len(tag2name)
                    nm = explicit[k] if k < len(explicit) else nm
                if nm.startswith("@") or (nm == tag and tag.startswith("Subject ")):
                    # ⚠️ `nm == tag` **只在 tag 是 `Subject N` 时**才算失败 ——
                    #    tag 若是**名字本身**（用户给了 `--speaker-re`），`nm == tag` 是**正常的**。
                    warn.append("段 %s：`%s` 的名字没解析出来（拿到 `%s`）—— "
                                "用 `--speakers` 显式给角色名（逗号分隔，按首次出现顺序）"
                                % (start_stage + i, tag, nm))
                tag2name[tag] = nm
            ents.append({"who": tag2name[tag], "chars": line_weight(body)})
        stages[str(start_stage + i)] = ents
    return {"_note": _LEDGER_NOTE, "stages": stages}, warn


#: `subject_name` 用：`<Subject N>` 行开头的 **@引用**（`@图片1` / `@视频2`…）—— 它排在名字**前面**。
_AT_REF_RE = re.compile(r"^(?:\s*@\S+)+")
#: `subject_name` 用：名字的**终止符**（中英逗号 / 冒号 / 顿号 / 分号）。
_NAME_END_RE = re.compile(r"[,，:：、；;]")


def subject_name(prompt: str, tag: str) -> str:
    """把 `<Subject N>` 映射成**角色名**（best-effort）。

    六段式有 `subject_definitions:` 段（`<Subject 1> @图片1 角色名, 描述…`）⇒ 取**角色名**。
    **最小格式没有这个段**（`<Subject N>` 后面跟的是动作句）⇒ 拿不到名字，
    原样返回 `Subject N`，由调用方自己映射（或用 `--speakers` 显式给）。

    🔴 **2026-10-05 修**（`va-ledger` 一跑真实提示词就暴露）：旧写法取 `<Subject N>` 后的
       **第一个 token**，而真实格式的第一个 token 是 **`@图片N`** ⇒ 返回 `@图片1`。
       后果：`who` 成了 `@图片1` ⇒ 声库里没有这个名字 ⇒ **`advise` 永远查不到锚**（静默退 3）。
       新写法：① 只在**六段式**（有 `subject_definitions:`）里取名字（最小格式**不猜**）；
       ② 先剥掉开头的 **@引用**；③ 名字取到第一个**逗号/冒号/顿号**为止；
       ④ 跳过含 `<d>` 的行（那是**台词行**不是定义行 —— 同一 tag 在两处出现时别取错）。
    """
    src = prompt or ""
    if "subject_definitions" not in src:
        return tag
    _m0 = re.match(r"Subject\s*(\d+)$", tag)
    pat = (r"<Subject\s*%s>" % re.escape(_m0.group(1))) if _m0 else (r"<%s>" % re.escape(tag))
    for m in re.finditer(pat + r"([^\n]*)", src, re.I):
        line = m.group(1)
        if "<d>" in line or "<D>" in line:     # 台词行 ⇒ 跳过
            continue
        rest = _AT_REF_RE.sub("", line).strip()
        name = _NAME_END_RE.split(rest, 1)[0].strip()
        return name or tag
    return tag


def advise_anchor(bank_dir: str, prompt: str = "", prev_prompt: str = "",
                  speakers=None, prev_speaker: str = "") -> tuple:
    """给一段算「该不该接声锚 / 该接谁」。返回 ``(ok, lines, anchor_path)``。

    ``speakers`` 显式给了就优先（最小格式拿不到名字时用）；否则从 prompt 解析。
    ``ok`` 只表示**有没有可用的锚**（缺锚 ⇒ False，退出码 3，与 ``lookup`` 一致）。
    """
    spk = [s for s in (speakers or []) if s] or speakers_in_order(prompt)
    if prompt and not speakers:
        spk = [subject_name(prompt, t) for t in spk]
    prev = prev_speaker or ""
    if not prev and prev_prompt:
        tail = speakers_in_order(prev_prompt)
        prev = subject_name(prev_prompt, tail[-1]) if tail else ""

    lines = []
    lines.append("本段说话人（按开口顺序）：%s" % ("、".join(spk) if spk else "（解析不到 —— 用 --speakers 显式给）"))
    lines.append("上一段最后一个说话人：%s" % (prev or "（未知）"))

    first = spk[0] if spk else ""
    if first and prev and first == prev:
        lines.append("⇒ 缝上**同人续接**（%s）⇒ **不必接声锚**（保留音频连续性；接同一人的锚也无害）" % first)
    elif first and prev:
        lines.append("⇒ 缝上**换人**（%s → %s）⇒ 🔴 **必须接声锚**，锚 = %s（否则 %s 的音色会污染本段）"
                     % (prev, first, first, prev))
    elif first:
        lines.append("⇒ 缝上说话人 = %s（上一段未知）⇒ 换人时接锚更稳" % first)

    if len(spk) >= 2:
        lines.append("⚠ 本段有 %d 个说话人：声锚**只覆盖缝区**（前 ~0.9 秒，= context_frames 换算）；"
                     "后面换人由 prompt 的 `<Subject N>` 决定 —— 锚管不到、也不该管。" % len(spk))
        lines.append("   建议：**能拆段就拆段**（一段一说话人）。同段换人在音色 / 口型 / 时间轴三处都难。")

    path = None
    if first:
        path, why = lookup(bank_dir, first)
        lines.append("声库：%s → %s（%s）" % (first, path or "⟨无⟩", why))
    return (path is not None), lines, path


_SELFTEST_CASES = [
    # (prompt, 期望的说话人顺序)
    ("<Subject 1> A开口说话:<d>[zh]甲</d>，随后 <Subject 2> B开口说话:<d>[zh]乙</d>", ["Subject 1", "Subject 2"]),
    ("<Subject 1> (S1) 看着对方:<d>[zh]甲</d> 又 <Subject 1> (S1) 补一句:<d>[zh]丙</d>", ["Subject 1"]),
    ("[Shot 2] At 00:02.4 <Subject 2> (S2) 开口:<d>[zh]乙</d>", ["Subject 2"]),
    ("没有台词的纯动作段", []),
    # 最近前驱：跨 Shot 时不能取"第一个"标记
    ("[Shot 1] <Subject 1> 走:<d>[zh]甲</d> [Shot 2] <Subject 2> 说:<d>[zh]乙</d>", ["Subject 1", "Subject 2"]),
]

#: 台词守卫的窗口定位用例：``(ref, hyp, 期望是否通过, 说明)``
#: 🔴 关键在**第 3 条** —— 台词念错/不在转写里时必须**仍然拒**（原功能不放松）。
_BW_CASES = [
    ("我拿的只看了一眼", "账本少了三页我拿的只看了一眼都别解释先听我说", True,
     "多人段里定位单句（修前 CER=1.75 被误拒）"),
    ("丙丁", "甲乙丙丁戊己", True, "窗口滑到中间命中"),
    ("我拿的只看了一眼", "账本少了三页都别解释先听我说", False,
     "🔴 台词不在转写里 ⇒ 必须拒（守卫生效）"),
    ("甲乙丙", "甲乙丙", True, "整段就是这一句"),
    ("完全不一样的台词内容", "账本少了三页我拿的只看了一眼", False,
     "🔴 内容不符 ⇒ 必须拒"),
]


def _selftest_loudness() -> int:
    """③ 声锚双归一的自测（合成信号，零外部依赖）。

    为什么必须有：只归一峰值会让锚比段内人声响 7 dB，而**模型跟随锚的响度**
    ⇒ 生成段比上一段响 ⇒ 段间跳变。这条自测把「响度真被拉齐」钉住。
    """
    import numpy as np
    rng = np.random.default_rng(20261003)
    n = SR * 2

    def nz(amp):
        return (rng.standard_normal(n) * amp).astype(np.float32)

    spike = np.zeros(n, np.float32)
    spike[-1] = 0.99                       # 响度极低但峰值满 ⇒ 抬响度必削顶
    cases = [
        ("太响的锚（amp 0.5）", nz(0.5), "rms"),
        ("偏轻的锚（amp 0.03）", nz(0.03), "rms"),
        ("已在目标（amp 0.0708）", nz(0.0708), "keep"),
        ("稀疏尖峰（抬响度必削顶）", spike, "fallback"),
    ]
    bad = 0
    for why, x, want in cases:
        y, note = normalize_anchor(x, SR)
        if want == "rms":
            db, _ = speech_rms_db(y, SR)
            ok = db is not None and abs(db - TARGET_RMS_DBFS) <= 2.0
            got = "%.1f dBFS（目标 %.1f）" % (db, TARGET_RMS_DBFS) if db is not None else "不可测"
        elif want == "keep":
            ok = "不动" in note
            got = note[:46]
        else:
            ok = ("退回" in note) and float(abs(y).max()) <= 0.95
            got = "峰值 %.3f" % float(abs(y).max())
        bad += 0 if ok else 1
        print("  [%s] %-26s %-26s ｜ %s" % ("OK" if ok else "FAIL", why, got, note[:56]))
    return bad


def _selftest_pitch_roundtrip() -> int:
    """🔴 采集链路的**音高/时长不变**自检（2026-10-04 变调事故后加）。

    事故回顾：落盘段读 `sr_out`（32 k）却用 `_load_mono()` 的**默认 16 k 分析域**取数据，
    于是 16 k 的样本被按 32 k 写进 wav 头 ⇒ 播放**加速一倍**、**音调升高一个八度**。
    而 wav 头、`voices.json` 的 `sample_rate` 全都"正常" ⇒ **任何元数据检查都抓不到**。

    🔴 **必须端到端跑 `collect`**（而不是只测 `_load_mono` 本身）：
       事故的错误在**调用点**（该传 `sr=None` 却没传）。第一版自检只测
       `_load_mono(sr=None)` ⇒ 恒过 ⇒ 注入同样的 bug 也不红 ⇒ **假门**。
       这里造 32 kHz 语音状信号当"源片"，走完整 `collect`，
       再对**落盘的锚**测：采样率 / 时长 / 过零间隔 F0。16 k 误用 ⇒ 时长减半、F0 翻倍 ⇒ 必挂。

    ⚠️ **本自检的覆盖边界（据实记录，不假装覆盖）** —— 注入法实测 5 个注入点：
       ① `_load_mono` 的 `sr` 默认值改成 `None`（时间轴错）  ⇒ **红** ✅
       ② 落盘退回 16 k 读回（原始事故）                      ⇒ **红** ✅
       ③ 去掉落盘样本数 fail-closed 校验                      ⇒ **红** ✅
          （补了 `tests/data/voice_two_spans_32k.wav` 之后由绿转红 —— 该夹具
            两段语音状信号只隔 0.30 s，`<0.5 s` 合并阈值会把它们并成一段，
            于是"落盘样本数 ≠ 标称"真能被触发。此前是内联合成的单段信号 ⇒ 无区分力。）
       ④ 去掉尾部择优（`_tighten` 已删，见下）                ⇒ **红** ✅（同上）
       ⑤ 去掉裁窗起点断言 / 越界钳制                          ⇒ 部分红
          —— **越界钳制已删**：`t1` 由帧号 `i` 得出，`i < len(voiced)`
          ⇒ **恒在源长内**，探针实测 5 条素材全部「越界=False」⇒ 不可达。

    🔴 **一次重要的自我纠错**（值得留给下一个人）：`_tighten()` 与越界钳制
       当初都被我判为"必须的保护"，理由是「`_voiced_spans` 合并 <0.5 s 间隙后
       端点会落在静音上」。**探针实测证明那个理由是错的** ——
       `_voiced_spans` 的端点由 `s = i`（第一个 voiced=True）给出，**构造上就有声**；
       `_tighten` 5 次调用**改动 0 次**。
       它们之所以"看起来必要"，是因为彼时 `_load_mono` 的 `sr` 默认值被误改成 `None`
       （时间轴差一倍）—— **一个 bug 制造出的假象，让另一个"修复"看起来合理**。
       ⇒ 已删。教训：**探针实测行为 > 推理出的必要性**；
          恒不生效的"保护性"代码比没有更糟（它让人误以为有保护）。
    """
    import numpy as np
    import shutil
    import tempfile
    bad = 0
    sr = 32000
    f0 = 300.0
    dur = 1.0
    # ⚠️ 源片**前后各留 1.0s 静音**：`_voiced_spans` 会把 <0.5s 的间隙并进来，
    #    留够静音才能让能量法把裁窗**收在真实人声上**（否则裁窗会越界到源片外）。
    lead = 1.0
    tone_n = int(sr * dur)
    t = np.arange(tone_n) / float(sr)
    # 加二次谐波，避免纯正弦的过零判据在削顶后失效
    tone = 0.42 * np.sin(2 * np.pi * f0 * t) + 0.12 * np.sin(2 * np.pi * 2 * f0 * t)
    tone = tone / np.abs(tone).max() * 0.85
    pad = np.zeros(int(sr * lead))
    # 🔴 **尾部加一段更长的纯音**（1.5s），中间只隔 0.3s。
    #    为什么要它：单一纯音下"去掉 `_tighten`"或"去掉裁窗越界钳制"都**观察不到差异**
    #    ⇒ 那两条自检是**假门**（已用注入法实测确认：注入后仍 18/18 绿）。
    #    两段 + <0.5s 间隙 ⇒ `_voiced_spans` 会**并成一段**，`_tighten` 与越界钳制
    #    才有可观测的作用。基频刻意不同（300 / 240 Hz）以便识别是哪一段。
    t2 = np.arange(int(sr * 1.5)) / float(sr)
    tone2 = 0.42 * np.sin(2 * np.pi * (f0 * 0.8) * t2) + 0.10 * np.sin(2 * np.pi * 1.6 * f0 * t2)
    tone2 = tone2 / np.abs(tone2).max() * 0.85
    src = np.concatenate([pad, tone, np.zeros(int(sr * 0.3)), tone2, np.zeros(int(sr * 0.3))])
    src = (src * 32767).astype(np.int16)
    tmpdir = tempfile.mkdtemp(prefix="_vb_selftest_")
    bank = os.path.join(tmpdir, "bank")
    # 🔴 **优先用仓内固定素材**（2026-10-04 补自检缺口）。
    #    为什么必须有：内联合成信号下「尾部择优」与「越界钳制」两条分支
    #    **没有区分力**（注入验证：退化后自检仍全绿 = 假门）。
    #    仓内素材 `tests/data/voice_two_spans_32k.wav` 是**纯合成、CC0、可再分发**：
    #    两段语音状信号（300 Hz / 240 Hz）只隔 0.30 s（< `_voiced_spans` 的 0.5 s
    #    合并阈值）⇒ 尾部择优必须跳过「尾部含停顿」的那段。
    #    注入验证：把尾部择优退化成「取最长」⇒ `tail_clean` 由 True 变 False ⇒ 断言变红。
    #    文件缺失时**静默回退**到内联合成（自检不该因为缺个可选夹具就红）。
    _fixture = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "tests", "data", "voice_two_spans_32k.wav")
    if os.path.isfile(_fixture):
        src_wav = _fixture
    else:
        src_wav = os.path.join(tmpdir, "tone32k.wav")
        with wave.open(src_wav, "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(src.tobytes())
    try:
        # 🔴 **时间轴必须落在源片内**（2026-10-04 差点又漏掉这一维）：
        #    `_load_mono` 的 `sr` 默认值若被改成 `None`，分析侧会拿 32k 数据
        #    按 16k 时间轴解释 ⇒ 声音被判在两倍时间处、`t1` 越出源片。
        #    这类"换算率≠解释率"的错**不会让落盘样本数校验变红** ⇒ 必须独立断言。
        #    这里刻意**不给容差**：越界钳制一旦失效，`_voiced_spans` 并段后的
        #    `t1` 会超出源片末端（实测 1.8s 源给出 t1=2.80s）⇒ 断言变红。
        # ⚠️ 长度必须**从实际用的源片文件**读，不能用内联数组 `len(src)`
        #    （用了仓内夹具时两者不是一回事）。
        with wave.open(src_wav, "rb") as _w:
            _n, _sr_src = int(_w.getnframes()), int(_w.getframerate())
        src_dur = _n / float(_sr_src)
        span = longest_voiced_span(src_wav)
        ok_span = (bool(span) and span[0] > 0.0 and span[1] < src_dur
                   and span[1] - span[0] > 0.5)
        if not ok_span:
            bad += 1
        print("  [%s] %-10s 实测 %-22s 约束 %-22s %s"
              % ("OK" if ok_span else "FAIL", "分析时间轴",
                 ("%.2f-%.2fs" % (span[0], span[1])) if span else "None",
                 "0<t0<t1<%.2fs" % src_dur,
                 "" if ok_span else "🔴 越界或时间轴错（换算率≠解释率？）"))
        # 🔴 **裁窗起点必须落在第一个有声帧上**（`_voiced_spans` 的构造保证）。
        #    这条断言在 `_tighten` 被删之后**依然有效** —— 它验的是
        #    「`t0` 不被静音带偏」这个**真实需求**，而不是某个具体实现。
        #    注入验证：把 `t0, t1 = a_s, b_s` 换成 `t0, t1 = 0.0, span[1]` ⇒ 这项会红。
        t_first = lead
        ok_tight = (bool(span) and abs(span[0] - t_first) < 0.30)
        if not ok_tight:
            bad += 1
        print("  [%s] %-10s 实测 %.2fs      期望 ≈%.2fs（首个有声点）  %s"
              % ("OK" if ok_tight else "FAIL", "裁窗起点",
                 span[0] if span else -1, t_first,
                 "" if ok_tight else "🔴 起点带进静音（_voiced_spans 行为被改）"))
        # 🔴 **尾部 0.925s 必须全程有声**（消费端 `_voice_anchor_tail` 只取尾部）。
        #    `longest_voiced_span` 返回的第 4 元就是这个判据。
        #    源片里两段纯音只隔 0.3s（<0.5s 合并阈值）⇒ 会被并成一段，
        #    尾部优先选段应当**跳过**它或选到尾部干净的那段。
        ok_tail = bool(span) and bool(span[3])
        if not ok_tail:
            bad += 1
        print("  [%s] %-10s 实测 %-22s 期望 %-22s %s"
              % ("OK" if ok_tail else "FAIL", "尾部干净",
                 ("是" if (span and span[3]) else "否"), "是",
                 "" if ok_tail else "🔴 尾部 %.2fs 有停顿 ⇒ 消费端取不到人声" % TAIL_S))
        all_t0 = lead
        all_t1 = src_dur
        # 落盘的锚 = 被选中的那一段；基频断言用「两段基频之一」——
        # 目的是抓「变调/倍频」这类**量级错误**，不是精确识别是哪一段。
        exp_f0s = (f0 * 0.8, f0)
        got_path, msg = collect(src_wav, "Tone", bank, force=True)
        if not got_path or not os.path.isfile(got_path):
            bad += 1
            print("  [FAIL] 采集失败：%s" % (msg or "无锚产出"))
            return bad
        with wave.open(got_path, "rb") as wf:
            sr_a = int(wf.getframerate())
            n_a = int(wf.getnframes())
            a = np.frombuffer(wf.readframes(n_a), dtype=np.int16).astype(np.float64) / 32768.0
        dur_a = n_a / float(sr_a)
        # 🔴 **用「过零间隔中位数」测 F0，不要用过零率**（2026-10-04 两次踩坑）：
        #    过零率 = 翻转数 / 窗长，窗里混进静音就整体拉低；`np.signbit` 在近零
        #    浮点上还反复抖动（F0 曾测出 5 Hz、0 Hz）。间隔中位数对静音免疫
        #    （静音段不产生过零点，压根不进中位数），且不用挑窗。
        seg = a.astype(np.float64)
        seg = seg - seg.mean()                        # 去直流
        # ⚠️ 正弦的过零点**就在**零附近 ⇒ 任何 ±幅度门限找沿都会一个都找不到
        #   （实测 10% 门限 ⇒ 沿数 0）。用经典零交叉（相邻样本异号）即可。
        # ⚠️ 零交叉间隔是**半周期** ⇒ F0 = sr / (2×间隔)。曾两次搞错：
        #   一次用 `signbit` 抖动测出 5 Hz，一次忘了 ×2 测出 603.8 Hz（真值 300）。
        s = np.sign(seg)
        idx = np.flatnonzero(s[:-1] * s[1:] < 0)
        f_meas = 0.0
        if len(idx) >= 3:
            # ⚠️ `half` 是**半周期**样本数 ⇒ 它对应 F0/2，滤波边界要按半周期算：
            #    覆盖 F0 ∈ [60, 400] Hz ⇒ half ∈ [sr/800, sr/120]。
            #    早先误用 `[sr/400, sr/60]`（整整差一倍）⇒ 300 Hz 的 half=53
            #    落在 sr/400=80 之下被全滤掉 ⇒ F0 恒报 0（自检红，误判为落盘失真）。
            half = np.diff(idx).astype(np.float64)
            per = half[(half > sr_a / 800.0) & (half < sr_a / 120.0)]
            if len(per):
                f_meas = float(sr_a) / (2.0 * float(np.median(per)))
        # 时长：裁窗应**不短于**被并入的那段总长，且不超过整条源片
        ok_dur = (dur_a >= 2.0) and (dur_a <= (all_t1 - all_t0) + 0.15)
        checks = (
            ("落盘采样率", sr_a, sr, sr_a == sr),
            ("落盘时长(s)", round(dur_a, 3), ">=2.0", ok_dur),
            ("F0(Hz)", round(f_meas, 1), "240 或 300",
             min(abs(f_meas - v) / v for v in exp_f0s) < 0.03),
        )
        for what, g, w, ok in checks:
            if not ok:
                bad += 1
            print("  [%s] %-10s 实测 %-10s 期望 %-10s %s"
                  % ("OK" if ok else "FAIL", what, g, w,
                     "" if ok else "🔴 变调/失真"))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return bad


def _selftest_low_rate_source() -> int:
    """🔴 **低采样率素材必须原样通过**（2026-10-04 第二个同型事故后加）。

    开源项目的用户素材**绝大多数不是 32 kHz**（16k / 22.05k / 44.1k / 48k 都常见）。
    历史缺陷：音源低于 `SR_OUT_MIN` 时用
        `x = x[:len(x) * SR_OUT_MIN / sr_out]` + 头写 `SR_OUT_MIN`
    —— 切片**不会补长** ⇒ "头 32k / 数据 16k" ⇒ **播放速度 2× ⇒ 音调升高**。
    与落盘读回那次事故**完全同型**（样本数与标称采样率不自洽）。

    正确契约：**保留源率**，不假装提了频（宿主 `VAEEncodeAudio` 自己会 resample 到
    H3 音频 VAE 的 32 kHz）。本自检对 16k / 22.05k / 48k 三种率断言：
    落盘采样率 == 源率、时长不变、F0 不变。
    """
    import numpy as np
    import shutil
    import tempfile
    bad = 0
    f0 = 300.0
    dur = 1.0
    for sr_src in (16000, 22050, 48000):
        lead = 0.5
        n_tone = int(sr_src * dur)
        t = np.arange(n_tone) / float(sr_src)
        tone = (0.42 * np.sin(2 * np.pi * f0 * t) + 0.12 * np.sin(2 * np.pi * 2 * f0 * t))
        tone = tone / np.abs(tone).max() * 0.85
        src = np.concatenate([np.zeros(int(sr_src * lead)), tone])
        src = (src * 32767).astype(np.int16)
        tmpdir = tempfile.mkdtemp(prefix="_vb_selftest_lr_")
        try:
            sw = os.path.join(tmpdir, "src%d.wav" % sr_src)
            with wave.open(sw, "w") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sr_src)
                wf.writeframes(src.tobytes())
            got, msg = collect(sw, "T", os.path.join(tmpdir, "bank"), force=True)
            if not got or not os.path.isfile(got):
                bad += 1
                print("  [FAIL] %5dHz 采集失败：%s" % (sr_src, msg or "无产出"))
                continue
            with wave.open(got, "rb") as wf:
                sr_a = int(wf.getframerate())
                n_a = int(wf.getnframes())
                a = np.frombuffer(wf.readframes(n_a), dtype=np.int16).astype(np.float64) / 32768.0
            dur_a = n_a / float(sr_a)
            seg = a - a.mean()
            s = np.sign(seg)
            idx = np.flatnonzero(s[:-1] * s[1:] < 0)
            f_meas = 0.0
            if len(idx) >= 3:
                half = np.diff(idx).astype(np.float64)
                per = half[(half > sr_a / 800.0) & (half < sr_a / 120.0)]
                if len(per):
                    f_meas = float(sr_a) / (2.0 * float(np.median(per)))
            # 时长允许 ±1 个分析窗（32ms）的裁切误差
            ok = (sr_a == sr_src) and abs(dur_a - dur) < 0.12 and abs(f_meas - f0) / f0 < 0.03
            if not ok:
                bad += 1
            print("  [%s] %5dHz 源 → 落盘 %5dHz / %.2fs / F0 %.1fHz  %s"
                  % ("OK" if ok else "FAIL", sr_src, sr_a, dur_a, f_meas,
                     "" if ok else "🔴 采样率被改写 ⇒ 会变调"))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    return bad


def _selftest_va_ledger():
    """⑥ 逐句解析 / 字数权重 / 名字解析 / 规则覆盖 —— **VA 台账**那条链的断言。

    为什么单独一组（2026-10-05）：`va-ledger` 的全部价值在"从提示词里**读出**逐句信息"，
    而 `<d>` / `<Subject N>` **不是官方约定**（宿主里没有它们）⇒
    "默认能解 / 能被覆盖 / 解不了要报错"这三件事必须**各自有断言**守着。

    返回 ``(失败数, 检查数)`` —— **自己数**（别在外面写死数字，那正是本仓反复踩的漂移坑）。
    """
    res = []

    def chk(ok, title, detail=""):
        res.append(bool(ok))
        print("  [%s] %s%s" % ("OK" if ok else "FAIL", title, ("  " + detail) if detail else ""))

    six = ("subject_definitions:\n"
           "<Subject 1> @图片1 张三, an East Asian man in his forties: short black hair.\n"
           "<Subject 2> @图片2 李四, an East Asian woman.\n"
           "[Shot 1] <Subject 1> 目光不离桌面开口说话:<d>[zh]我还看了第二遍。</d> he says. "
           "<Subject 2> 抬起下巴开口说话:<d>[zh]是我的。</d> she says. "
           "<Subject 1> 又开口:<d>[zh]别走。</d>\n")
    _lines = speaker_lines(six)
    chk([t for t, _b in _lines] == ["Subject 1", "Subject 2", "Subject 1"],
        "逐句：3 处台词 ⇒ 3 项，且**同一人两句出现两次**（不是去重成 2 项）",
        str([t for t, _b in _lines]))
    chk([b for _t, b in _lines] == ["[zh]我还看了第二遍。", "[zh]是我的。", "[zh]别走。"],
        "逐句：正文取到 `</d>` 之前（不吞掉后面的英文描述）", str([b for _t, b in _lines]))
    chk(speakers_in_order(six) == ["Subject 1", "Subject 2"],
        "顺序：仍是**去重**的（与旧口径一致 ⇒ `speakers_in_order` 是薄包装）",
        str(speakers_in_order(six)))
    chk(line_weight("[zh]我还看了第二遍。") == 7.0
        and line_weight("[zh]第三页不是我的字。") == 8.0 and line_weight("[zh]是我的。") == 3.0,
        "权重：汉字各记 1；标点与 `[zh]` **不计**（7 / 8 / 3）",
        "%.1f / %.1f / %.1f" % (line_weight("[zh]我还看了第二遍。"),
                                line_weight("[zh]第三页不是我的字。"), line_weight("[zh]是我的。")))
    chk(abs(line_weight("[en]I looked at it again.") - 5.5) < 1e-9 and line_weight("……") == 0.5,
        "权重：拉丁按 3 字母 ≈ 1 音节（16 字母 ⇒ 5.5）；纯标点 ⇒ **下限 0.5**",
        "%.1f / %.1f" % (line_weight("[en]I looked at it again."), line_weight("……")))
    chk(abs(line_weight("Hello, world! OK?") - 4.0) < 1e-9,
        "权重：**ASCII 标点也剥**（`Hello, world! OK?` ⇒ 12 字母 = 4.0，不是把 3 个标点也算进去）",
        "%.1f" % line_weight("Hello, world! OK?"))
    chk(subject_name(six, "Subject 1") == "张三" and subject_name(six, "Subject 2") == "李四",
        "名字：六段式里剥掉开头的 `@图片N` 取**真名**（旧写法返回 `@图片1` ⇒ 声库永远查不到）",
        "%s / %s" % (subject_name(six, "Subject 1"), subject_name(six, "Subject 2")))
    # 🔴 **2026-10-06 补的防回归**：同日发现产线脚本里写成字符类 `[^，,<d]`（本意是排除
    #    标记 `<d>`，实际变成"排除字母 d"）⇒ 英文名被截断（`David` ⇒ `Davi`）⇒ 声库查不到
    #    ⇒ **静默降级不接锚**。本仓写法本来就对，加一条钉住，防止有人"顺手统一风格"改坏。
    _six_en = ("subject_definitions: <Subject 1> @图片1 David, an East Asian man in his forties.\n"
               "<Subject 2> @图片2 Ding Yi, an East Asian woman.\n")
    chk(subject_name(_six_en, "Subject 1") == "David"
        and subject_name(_six_en, "Subject 2") == "Ding Yi",
        "名字：**英文名含字母 `d` 不许被截断**（`[^<d]` 字符类陷阱：`David` 会变成 `Davi`）",
        "%s / %s" % (subject_name(_six_en, "Subject 1"), subject_name(_six_en, "Subject 2")))
    chk(subject_name("<Subject 1> 抬眼看他们开口说话:<d>你好。</d>", "Subject 1") == "Subject 1",
        "名字：**最小格式**（无 `subject_definitions`）⇒ 原样返回 tag（**不猜**）", "")
    _pr = {}
    _alt = speaker_lines("<台词>你好世界。</台词>\n甲: <台词>在。</台词>",
                         line_re=r"<台词>(.*?)</台词>", speaker_re=r"^([^:\n：]+)[:：]", probe=_pr)
    chk(_alt == [("甲", "在。")] and _pr.get("line_hits") == 2 and _pr.get("no_speaker") == 1,
        "覆盖：换一套格式（`--line-re` + 行首「名字:」）解得出；**前无说话人标记那句被跳过并记账**",
        "%s ｜ probe=%s" % (_alt, {k: _pr[k] for k in ("line_hits", "speaker_hits", "no_speaker")}))
    try:
        compile_rules(line_re="([")
        _bad_re = False
    except ValueError:
        _bad_re = True
    chk(_bad_re, "规则：**不合法正则 ⇒ raise**（fail-closed，不静默退回默认）", "")
    _led, _warn = va_ledger([six], start_stage=7)
    _ents = _led["stages"].get("7") or []
    chk([e["who"] for e in _ents] == ["张三", "李四", "张三"]
        and [e["chars"] for e in _ents] == [7.0, 3.0, 2.0],
        "台账：段号按 `start_stage` 偏移；逐句 `who` = **名字**、`chars` = 音节数",
        str([(e["who"], e["chars"]) for e in _ents]))
    chk(bool(_led.get("_note")) and "档 2" in _led["_note"] and not _warn,
        "台账：带 `_note` 来源说明且**无警告**（`_note` 已在 `KNOWN_TOP_KEYS` 白名单）",
        str(_warn)[:60])
    try:
        ledger_from_parts([["甲", "乙"]], [[5.0]])
        _bad_len = False
    except ValueError:
        _bad_len = True
    chk(_bad_len, "手工档：`--who` 与 `--lines` **长度不等 ⇒ raise**（不猜）", "")
    _led2 = ledger_from_parts([["甲", "乙"]], [[5.0, 3.0]], start_stage=0)
    chk(_led2["stages"]["0"] == [{"who": "甲", "chars": 5.0}, {"who": "乙", "chars": 3.0}],
        "手工档：逐句 `who` + 权重 ⇒ 台账（**任何提示词格式都能用**）", "")
    return sum(1 for x in res if not x), len(res)


def _selftest() -> int:
    bad = 0
    print("① 说话人解析（取每个 <d> 最近的前驱说话人标记，按开口顺序去重）")
    for txt, want in _SELFTEST_CASES:
        got = speakers_in_order(txt)
        ok = got == want
        bad += 0 if ok else 1
        print("  [%s] %-52s → %s" % ("OK" if ok else "FAIL", txt[:52], got))
    print("② 台词守卫的窗口定位（多人段里找单句；**原功能不放松**）")
    for ref, hyp, want_ok, why in _BW_CASES:
        c, i0, i1 = best_window_cer(ref, hyp)
        got_ok = c <= ASR_CER_MAX
        ok = got_ok == want_ok
        bad += 0 if ok else 1
        print("  [%s] %-30s vs %-38s CER=%.2f 命中「%s」 期望%s ⇒ %s"
              % ("OK" if ok else "FAIL", ref[:30], hyp[:38], c, hyp[i0:i1][:20],
                 "过" if want_ok else "拒", why))
    print("③ 声锚双归一（响度对齐 %.1f dBFS + 峰值护栏 %.2f）" % (TARGET_RMS_DBFS, PEAK_CEIL))
    bad += _selftest_loudness()
    print("④ 落盘往返音高不变（防「分析域 16k 数据贴 32k 头」变调事故）")
    bad += _selftest_pitch_roundtrip()
    print("⑤ 低采样率素材原样通过（防「切片补长」式变调；用户素材常是 16k/22.05k）")
    bad += _selftest_low_rate_source()
    print("⑥ 逐句解析 / 字数权重 / 名字解析 / 规则覆盖（VA 台账那条链 —— **兼容性也是断言**）")
    _b6, _n6 = _selftest_va_ledger()
    bad += _b6
    n = len(_SELFTEST_CASES) + len(_BW_CASES) + 4 + 5 + 4 + _n6
    print("  自检：%d/%d 通过" % (n - bad, n))
    return 0 if bad == 0 else 1


_ASR_SINGLETON = None      # 效能：funasr 模型 944MB，进程内只加载一次（懒加载单例）

def _asr_model():
    global _ASR_SINGLETON
    if _ASR_SINGLETON is None:
        from funasr import AutoModel
        _ASR_SINGLETON = AutoModel(model=ASR_MODEL, device="cpu", disable_update=True)
    return _ASR_SINGLETON


def best_window_cer(ref: str, hyp: str):
    """在 ``hyp`` 里找与 ``ref`` 最贴的**一段连续窗口**，返回 ``(cer, i0, i1)``。

    🔴 为什么必须按窗口找（2026-10-03 实测踩到）：**一段里可能有多个人说话**，
    ASR 转写必然把**别人的台词**也转进来 ⇒ 拿"整段 vs 单句"比 CER 会把**合格**语音判成不合格
    （实测：三句连读 ⇒ `CER=1.75 > 0.35` ⇒ 锚被拒，**多说话人段永远采不出锚**）。

    口径：窗口长度固定 = ``len(ref)``（长度差本身就是编辑距离的一部分）。
    在 ``hyp`` 上滑一遍取 CER 最小的那个窗口 ⇒ 该窗口就是这句台词在整段里的位置。

    ✅ **原功能不放松**：台词念错 / 胡言乱语时，**任何**窗口对 ``ref`` 的 CER 都高
    ⇒ 仍然被拒（调用方判 ``cer <= ASR_CER_MAX``）。
    """
    if not ref or not hyp:
        return 1.0, 0, 0
    n = len(ref)
    best, bi = None, 0
    for i in range(max(1, len(hyp) - n + 1)):
        c = cer(ref, hyp[i:i + n])
        if best is None or c < best:
            best, bi = c, i
    return best, bi, min(bi + n, len(hyp))


def _voiced_spans_in(mp4: str, w0: float, w1: float):
    """**只在 [w0, w1] 这段窗口内**找连续有声区间（返回绝对秒）。

    为什么需要它：多说话人段里 ``longest_voiced_span(mp4)`` 取的是**整段**最长有声段
    ⇒ 很可能是**别人**的台词 ⇒ 拿它去修本句的尾巴 = 修到别人身上。
    """
    raw = _load_mono(mp4)
    try:
        x = _read_raw(raw)
    finally:
        try:
            os.remove(raw)
        except OSError:
            pass
    a0, a1 = max(0, int(w0 * SR)), min(len(x), int(w1 * SR))
    seg = x[a0:a1]
    w, hop = int(WIN_S * SR), int(HOP_S * SR)
    if len(seg) < w:
        return []
    amps = [float(abs(seg[i:i + w]).mean()) for i in range(0, len(seg) - w, hop)]
    return [(w0 + s, w0 + t) for s, t in _voiced_spans([a > VOICED_AMP for a in amps], HOP_S)]


def _asr_verify(mp4: str, line: str):
    """台词守卫：funasr 转写整段 → **按窗口定位这一句** → 与台词原文比 CER。

    返回 ``(ok, cer, span_seconds 或 None, 整段转写, 命中窗口文本)``。
    span = 命中窗口首字起点 → 末字终点（模型给的是 10ms 粒度）；
    拿不到（逐字时间戳与转写字符对不上）⇒ None，调用方回退能量法。
    CER > ASR_CER_MAX ⇒ 不通过（模型念了别的东西/胡言乱语 ⇒ 绝不给它当锚）。
    """
    import os
    wav = _load_mono(mp4, ext="wav")
    try:
        m = _asr_model()
        r = m.generate(input=wav, batch_size_s=300)
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass
    res = (r or [{}])[0]
    text = "".join(x.get("text", "") for x in (r or [])).replace(" ", "").strip()
    ts = res.get("timestamp") or []
    hyp, ref = norm_text(text), norm_text(line)
    c, i0, i1 = best_window_cer(ref, hyp)
    span = None
    if ts and len(ts) == len(text):
        # ⚠ 逐字时间戳必须与转写字符**一一对应**才敢用；对不上 ⇒ 不给 span（回退能量法），
        #   绝不用错位的时间戳去裁锚。
        span = (ts[i0][0] / 1000.0, ts[min(i1, len(ts)) - 1][1] / 1000.0)
    return (c <= ASR_CER_MAX), c, span, text, (hyp[i0:i1] if hyp else "")


def norm_text(s: str) -> str:
    """ASR 比对口径：去掉标点/空格（与 wer_asr_verify 的 norm 同思路）。"""
    for ch in ("，", "。", "！", "？", ",", ".", "!", "?", " ", "、", "：", ":"):
        s = s.replace(ch, "")
    return s


def cer(ref: str, hyp: str) -> float:
    r, h = list(ref), list(hyp)
    dp = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, len(h) + 1):
            cur = dp[j]
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return dp[len(h)] / max(len(r), 1)


def _safe_name(name: str):
    """角色名 → 安全文件名基名；含路径分隔符/非法字符 ⇒ None（防路径注入）。"""
    bad = set('\\/:"*?<>|')
    s = name.strip()
    if not s or (set(s) & bad) or s in (".", ".."):
        return None
    return s


# 🔴 ** 曾存在、已删**（2026-10-04）：它只被「裁窗越界钳制」用，
#    而那处钳制经探针实测**不可达**（t1 恒在源长内）⇒ 整条链一起删。
def collect(mp4: str, name: str, bank_dir: str, force: bool = False,
            line: str = ""):
    """从已渲染段提取「角色名」的声锚并入库。

    返回 ``(wav_path 或 None, 消息)``。手动锚存在 ⇒ 直接返回手动锚（不覆盖）。
    """
    _base = _safe_name(name)
    if _base is None:
        return None, "🔴 角色名含路径分隔符/非法字符（%r）⇒ 拒绝（防路径注入）" % name
    name = _base
    # 🔴 防御：调用方可能把 `<d>[zh]` 的**语言标记**一起传进来（它是标签不是台词），
    #    带进去会让 ASR 比对凭空多两个字符 ⇒ 入口统一剥掉（2026-10-03）。
    line = re.sub(r"^\s*\[[^\]]{1,12}\]\s*", "", line or "").strip()
    if manual_wav(bank_dir, name) and not force:
        return manual_wav(bank_dir, name), "已有手动锚，跳过自动采集"
    if not force and name in load_bank(bank_dir):
        # 锁定语义：已有自动锚 ⇒ 首个稳定锚长期复用（锚换来换去反而让音色漂）
        return load_bank(bank_dir)[name]["wav"], "声库已有自动锚，跳过（--force 可覆盖）"

    # —— 台词守卫优先（有台词原文时）：funasr 验「念对没念对」+ 逐字时间戳精确裁锚 ——
    asr_span = None
    if line:
        try:
            ok, c, asr_span, text, hit_txt = _asr_verify(mp4, line)
        except Exception as e:
            # ⚠ 真回退：守卫**不可用**（funasr 缺失等）≠ 守卫**不通过**。
            # 这里必须继续走能量法；只有 ASR 跑通且 CER 超标才拒收。
            print("[voice-bank] ⚠ ASR 守卫不可用（%s: %s）⇒ 回退能量法"
                  % (type(e).__name__, str(e).splitlines()[0][:60]))
            line = ""
        else:
            if not ok:
                return None, ("🔴 台词守卫不通过：CER=%.2f > %.2f"
                              "（最贴的一段窗口「%s」vs 期待「%s」；整段转写「%s」）"
                              "⇒ 语音不合格，绝不入锚"
                              % (c, ASR_CER_MAX, hit_txt[:30], line[:30], text[:40]))

    if asr_span:
        t1 = asr_span[1]
        t0 = max(asr_span[0], t1 - ANCHOR_MAX_S)
        # 🔴 ASR 窗 ∩ 能量证据：实测 funasr 时间戳会带**尾随噪声词**
        #    （S1 末字"止于 4.17s"而能量人声止于 1.90s ⇒ 直接裁 ⇒ 尾部全静音）。
        #    能量证据在人声终点上更可信 ⇒ 取两者交集。
        #    ⚠️ 能量证据必须**限定在本句窗口内**取：多说话人段里整段最长有声段
        #       很可能是**别人**的台词（2026-10-03 修）。
        _spans_in = _voiced_spans_in(mp4, asr_span[0], asr_span[1])
        if not _spans_in:
            return None, "ASR 认为有台词但本句窗口内能量检测全静音 ⇒ 数据矛盾，不采集"
        _e0, _e1 = _spans_in[0][0], _spans_in[-1][1]
        if t1 > _e1 + 0.10:
            t1 = _e1 + 0.05            # 收到能量证据的人声末尾（留 50ms 余量）
        # 🔴 起点同样要收到能量证据上（2026-10-04）：ASR 首字时间戳可能**早于**
        #    人声（起音前的静音/呼吸）⇒ 头部带一截静音，锚的第一声被推后。
        if t0 < _e0 - 0.10:
            t0 = _e0 - 0.05
        t0 = max(t0, t1 - ANCHOR_MAX_S)
        dur = t1 - t0
        if dur < MIN_VOICED_S:
            return None, ("ASR 台词窗仅 %.2fs < 下限 %.2fs ⇒ 不采集" % (dur, MIN_VOICED_S))
        total = dur
        # ASR 路径的 `t1` 就是本句**能量终点** ⇒ 尾部落在人声上（除非句子本身
        # 末尾有 <TAIL_S 的收尾气声，那属于素材固有）。与能量法不同，不做尾部择优。
        tail_clean = True
    else:
        hit = longest_voiced_span(mp4)
        if hit is None:
            return None, "产物里没有检测到语音（模型可能没说话）⇒ 不采集"
        t0, t1, total, tail_clean = hit
        # ⚠️ 这里曾有一段「裁窗越界钳制」（`if t1 > src_dur: t1 = src_dur`）
        #    与一个 `_media_duration()`（ffprobe 取源长），**都已删**（2026-10-04）。
        #    理由（探针实测，**不是推理**）：
        #      · `_tighten`（把两端收到有声边界）5 次调用**改动 0 次** ——
        #        `_voiced_spans` 的端点由 `s = i`（第一个 voiced=True 的帧）给出，
        #        **构造上就落在有声帧上**。
        #      · `t1` 由帧号 `i` 得出且 `i < len(voiced)` ⇒ **恒在源长内**；
        #        实测 5 条素材全部「越界=False」。
        #    当初那个"1.8 s 源给出 t1=2.80 s"的越界样本，是 `_load_mono` 的 `sr`
        #    默认值被误改成 `None`（时间轴差一倍）造出来的**假象** ——
        #    **一个 bug 制造出的假象，让另外两个"修复"看起来合理**。
        #    ⇒ 教训：**探针实测行为 > 推理出的必要性**。恒不生效的"保护性"代码
        #    比没有更糟（它让人误以为这里有保护）。
        dur = t1 - t0
        if dur < MIN_VOICED_S:
            return None, ("裁窗仅 %.2fs < 下限 %.2fs（总有声 %.2fs）⇒ 不采集"
                          % (dur, MIN_VOICED_S, total))
        if total < MIN_VOICED_S:
            return None, ("有声仅 %.2fs < 下限 %.2fs ⇒ 不采集"
                          % (total, MIN_VOICED_S))

    os.makedirs(bank_dir, exist_ok=True)
    wav_out = os.path.abspath(os.path.join(bank_dir, "%s.wav" % name))
    # 先裁段，再在 Python 里做精确峰值归一（比 ffmpeg filter 直观且可校验）
    # 🔴 **不指定 `-ar`**（2026-10-04）：原来固定 `-ar 16000` ⇒ **砍掉一半频谱**。
    #    而宿主 `VAEEncodeAudio` 会把锚 resample 到 **H3 音频 VAE 原生 32 kHz**
    #    （`comfy/sd.py:1070` + `comfy/ldm/minimax/audio_vae.py:374`
    #    `MiniMaxH3AudioVAE`：DAC 编码器 + BigVGAN 解码器，`hop_length=800`）
    #    ⇒ 16 kHz 以上**全是上采样插值编的**，齿音/气息/嘶声这些辨音色最关键的高频不存在。
    #    ✅ 修后实测：落盘 32 kHz 的 −40dB 带宽从 4.7~5.3 kHz 抬到 9.0~10.5 kHz。
    #
    # ⚠️ **别把"提高采样率"当带宽变好的证据**（2026-10-04 审码纠正）：
    #    边车（float16 无损 32 kHz）实测 −40dB = 4.87 kHz vs
    #    mp4（AAC-LC 128k 有损 32 kHz）= 4.85 kHz ⇒ **编码不是瓶颈**；
    #    且 H3 **能**产出宽带频（全 72 条里最宽 −40dB 达 15.07 kHz），
    #    只是那几条宽带的都是**音效/环境声**（帧均幅 ≈0.0001）不是人声。
    #    ⇒ 32 kHz 落盘只是**如实保存**，**不会凭空补出高频**；
    #       **要宽带语音必须换人声音源**（真人 / 原始录音）。
    # ⚠️ 音源本身低于 `SR_OUT_MIN`（用户给 16k / 22.05k mp4 很常见）⇒ 保留它的原率，
    #    **并把实际采样率写进登记信息**（不假装提了频；切片补长会造成变调）。
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", "%.3f" % t0, "-t", "%.3f" % dur,
         "-i", mp4, "-vn", "-ac", "1", wav_out], check=False)
    import numpy as np
    # 读回落盘的真实采样率（以文件为准，不假设源率）
    try:
        with wave.open(wav_out, "rb") as _wf:
            sr_out = int(_wf.getframerate())
            n_fm = int(_wf.getnframes())
    except Exception:
        return None, "裁剪落盘失败（wav 读不回）⇒ 不采集"
    # 🔴🔴 **按 `sr_out` 读回，不是按分析域 16 k**（2026-10-04 修，变调事故）。
    #     `_load_mono()` 默认给 16 k（分析域口径），用它读 32 k 的文件再按 32 k 落盘
    #     ⇒ 样本数少一半 ⇒ 播放加速一倍 ⇒ **音调升高一个八度**，而文件头完全正常。
    #     落盘域必须 `sr=None`（不传 `-ar` ⇒ 跟随文件本身的率）。
    raw = _load_mono(wav_out, sr=None)
    x = _read_raw(raw)
    os.remove(raw)
    # 样本数必须与**标称裁窗**一致（不是与"读回来多少"自洽！）。
    # 🔴 判据选错会漏：拿 `len(x)` 和 ffmpeg 裁出的 `n_fm` 比是**自证**的 ——
    #    两者都来自同一个 16k 误读，比值恒等于 1 ⇒ 永远红不了。
    #    必须锚定**独立来源**的应得样本数 `dur × sr_out`。
    #    （第一版自检就栽在这：纯音被当连续有声，裁出 1.97s 而非 1.0s，
    #      `dur` 也跟着变 ⇒ 校验用真实 dur ⇒ 依然自洽 ⇒ 假门。）
    exp_n = int(round(dur * sr_out))
    tol = max(2, int(0.02 * sr_out))
    if n_fm > 0 and abs(n_fm - exp_n) > tol:
        # ffmpeg 裁出的长度与标称不符（`-ss` 越界 / 源片比标称短）⇒ 拒绝，
        # 否则后面的读写都在一个错的基准上算。
        return None, ("ffmpeg 裁段得 %.3fs（%d 样本）≠ 标称 %.2fs×%dHz=%.0f（差 %.1f%%）"
                      "⇒ 裁窗与标称不符，**拒绝落盘**"
                      % (n_fm / float(sr_out), n_fm, dur, sr_out, exp_n,
                         100.0 * abs(n_fm - exp_n) / max(1.0, exp_n)))
    if exp_n > 0 and abs(len(x) - exp_n) > tol:
        return None, ("裁段样本数 %.0f ≠ 标称 %.2fs×%dHz=%.0f（差 %.1f%%）"
                      "⇒ 读回率与文件头不一致，**拒绝落盘**（否则会变调）"
                      % (len(x), dur, sr_out, exp_n,
                         100.0 * abs(len(x) - exp_n) / max(1.0, exp_n)))

    # ⚠️ 音源本身低于 `SR_OUT_MIN`（用户给 16k / 22.05k mp4 很常见）⇒
    #    **保留它的原率**，不假装提了频；真实率写进登记信息，宿主 VAE 会自己 resample。
    # 🔴 曾经的实现是 `x = x[:len(x) * SR_OUT_MIN / sr_out]` 再把头写成 32k ——
    #    切片**不会补长**（16k 素材只有 16000 样本，切片仍只有 16000），
    #    于是"头 32k / 数据 16k"⇒ **播放速度 2× ⇒ 又一次音调升高**。
    #    与落盘读回那次事故**同型**（样本数与标称率不自洽）。真要提频就用 ffmpeg `-ar`。
    pk = float(abs(x).max())
    if pk < 1e-4:
        return None, "裁出的声锚近乎静音（峰值 %.4f）⇒ 不采集" % pk
    x, _norm = normalize_anchor(x, sr_out)
    if float(abs(x).max()) < 1e-4:
        return None, "归一后近乎静音 ⇒ 不采集"
    with wave.open(wav_out, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr_out)
        wf.writeframes((x * 32767).astype(np.int16).tobytes())
    import datetime
    bank = load_bank(bank_dir)
    bank[name] = {"wav": wav_out, "src": os.path.basename(mp4),
                  "voiced_s": round(total, 2), "span_s": round(dur, 2),
                  "sample_rate": int(sr_out), "norm": _norm,
                  "tail_clean": bool(tail_clean),
                  "collected": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
    save_bank(bank_dir, bank)
    # 铁律 15：走哪条分支必须可观测。`tail_clean=False` 意味着消费端尾部 0.925s
    # 有停顿 ⇒ 明确告诉用户这个锚的音色条件会打折，而不是让他们以为"采到了"。
    _tail = "尾部 %.2fs 全有声 ✅" % TAIL_S if tail_clean else (
        "⚠ 尾部 %.2fs 含停顿（本段有声太短，**音色条件会打折**）" % TAIL_S)
    return wav_out, ("采集 %.2fs（%.2f–%.2fs）@%d Hz；%s；%s"
                     % (dur, t0, t1, sr_out, _norm, _tail))


# ------------------------------------------------- VA 台账（`va-ledger` 子命令）
def _split_groups(s):
    """`"甲,乙;丙"` ⇒ `[["甲", "乙"], ["丙"]]`（`;` 分段、`,` 分句）。"""
    return [[x.strip() for x in g.split(",") if x.strip()] for g in (s or "").split(";")]


def _print_ledger(led):
    for k in sorted(led["stages"], key=lambda x: int(x)):
        ents = led["stages"][k]
        print("  段 %s：%d 句 —— %s"
              % (k, len(ents), " ｜ ".join("%s:%.1f" % (e["who"], e["chars"]) for e in ents)))


def _write_ledger(led, a) -> int:
    """写台账：`--out` / `--run`；`--dry-run` 或没给路径 ⇒ 只打印。**绝不静默覆盖**。"""
    out_path = a.out
    if not out_path and a.run:
        root = a.relay_root or (os.path.join(os.environ["COMFYUI_PATH"], "output", "relay_kit")
                                if os.environ.get("COMFYUI_PATH") else "")
        if not root:
            print("🔴 给 `--run` 时必须给 `--relay-root`（或设 `COMFYUI_PATH` 环境变量）")
            return 2
        out_path = os.path.join(root, a.run, "_va.json")
    if a.dry_run or not out_path:
        print(json.dumps(led, ensure_ascii=False, indent=1))
        if not out_path and not a.dry_run:
            print("  ℹ️ 只打印（没给 `--out` / `--run`）—— 加 `--out <file>` 或 `--run <id>` 才写盘")
        return 0
    if os.path.isfile(out_path) and not a.force:
        print("🔴 已存在，**不覆盖**：%s" % out_path)
        print("   · 它可能是**手写的**（含档 3 的 `t0/t1` = ASR 精确时间），覆盖会丢。")
        print("   · 确认覆盖加 `--force`；想并存请改 `--out`。")
        return 3
    _d = os.path.dirname(out_path)
    if _d:
        os.makedirs(_d, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(led, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print("✅ 已写 %s（%d 段 / %d 句）"
          % (out_path, len(led["stages"]), sum(len(v) for v in led["stages"].values())))
    return 0


def _cmd_va_ledger(a) -> int:
    """`va-ledger` 的**三层**：① 默认规则（六段式开箱）② 用户给规则 ③ **完全手工**（绕开解析）。

    🔵 为什么分三层（2026-10-05，「尽量兼容其他开源用户的提示词」）：
       `<d>` 与 `<Subject N>` 都**不是官方约定**（宿主里没有）⇒ 别人的提示词不保证长这样。
       ⇒ 默认要够聪明（⑥ 段式开箱可用）；不匹配时要**能被接管**（给正则）；
         实在不行要**有出路**（手工给逐句）——**但任何一层失败都必须说出来**（不许静默给空台账）。
    """
    # ③ 全手工 —— 任何提示词格式都能用
    if a.who or a.lines:
        if not (a.who and a.lines):
            print("🔴 `--who` 与 `--lines` 必须**成对**给（逐句名单 ↔ 逐句权重一一对应）")
            return 2
        who_g = _split_groups(a.who)
        num_g = []
        for gi, g in enumerate(_split_groups(a.lines)):
            try:
                num_g.append([float(x) for x in g])
            except ValueError:
                print("🔴 `--lines` 第 %d 组里有非数字：%s" % (gi + 1, g))
                return 2
        if len(who_g) != len(num_g):
            print("🔴 `--who` 有 %d 组、`--lines` 有 %d 组 —— 多段用 `;` 分隔，两边段数必须相等"
                  % (len(who_g), len(num_g)))
            return 2
        try:
            led = ledger_from_parts(who_g, num_g, start_stage=int(a.start_stage),
                                    note="**手工给**的逐句名单与权重（未解析提示词）")
        except ValueError as e:
            print("🔴 " + str(e))
            return 2
        _print_ledger(led)
        return _write_ledger(led, a)

    # ①② 从提示词生成
    import glob as _glob
    prompts, src = list(a.prompt_inline), []
    if a.prompt_inline:
        src.append("--prompt-inline × %d" % len(a.prompt_inline))
    files = list(a.prompt_file)
    if a.prompts_dir:
        files = sorted(_glob.glob(os.path.join(a.prompts_dir, a.pattern)))
        if not files:
            print("🔴 `--prompts-dir %s` 下没有匹配 `%s` 的文件" % (a.prompts_dir, a.pattern))
            return 2
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                prompts.append(fh.read())
            src.append(os.path.basename(f))
        except OSError as e:
            print("🔴 读不了 %s：%s" % (f, e))
            return 2
    if not prompts:
        print("🔴 没给提示词。三种给法：")
        print("   · `--prompt-file <文件>`（可多次，按段号顺序）")
        print("   · `--prompts-dir <目录>`（配合 `--pattern`，当前 = %s）" % a.pattern)
        print("   · `--prompt-inline '<正文>'`（可多次，便于脚本 / 管道）")
        print("   · 或**完全手工**：`--who '甲,乙,甲' --lines '6,8,5'`（任何提示词格式都能用）")
        return 2
    probe = []
    try:
        led, warn = va_ledger(prompts, start_stage=int(a.start_stage),
                              speakers=[s.strip() for s in a.speakers.split(",") if s.strip()],
                              line_re=a.line_re or None, speaker_re=a.speaker_re or None,
                              probe=probe)
    except ValueError as e:
        print("🔴 " + str(e))
        return 2
    # —— **解析报告**（无论成败都打）：用户要能看出"我按什么规则、命中多少" ——
    print("  解析报告（来源：%s）" % ("、".join(src) or "—"))
    for pr in probe:
        print("    段 %s：台词块 `%s` 命中 %d ｜ 说话人 `%s` 命中 %d ｜ 逐句 %d%s"
              % (pr["stage"], pr.get("line_re"), pr.get("line_hits", 0),
                 pr.get("speaker_re"), pr.get("speaker_hits", 0), pr.get("lines", 0),
                 ("（%d 处没有说话人标记 ⇒ 归不了人）" % pr["no_speaker"]) if pr.get("no_speaker") else ""))
    for w in warn:
        print("  ⚠ " + w)
    if not led["stages"]:
        print("🔴 一段都建不出来（**判不准就不硬猜**，所以这里直接失败）。两条路：")
        print("   ① 告诉我你的提示词长什么样：")
        print("      `--line-re '<台词>(.*?)</台词>'`（捕获组 1 = 台词正文）")
        print("      `--speaker-re '<([^>]+)>'`（捕获组 1 = 说话人；纯数字 ⇒ 当作 Subject 编号）")
        print("   ② 或**完全手工**给逐句：`--who '甲,乙,甲' --lines '6,8,5'`")
        return 2
    _print_ledger(led)
    return _write_ledger(led, a)


# ---------------------------------------------------------------- CLI
def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="H3 声锚声库（采集/查询/列表）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("collect", help="从已渲染 mp4 采集声锚")
    p1.add_argument("mp4")
    p1.add_argument("--name", required=True, help="角色名")
    p1.add_argument("--bank", required=True, help="声库目录")
    p1.add_argument("--line", default="", help="台词原文（funasr 守卫：验 CER + 逐字时间戳裁锚）")
    p1.add_argument("--force", action="store_true", help="覆盖已有锚（默认不覆盖）")
    p2 = sub.add_parser("lookup", help="查询某角色的声锚")
    p2.add_argument("name")
    p2.add_argument("--bank", required=True)
    p3 = sub.add_parser("list", help="列出声库")
    p3.add_argument("--bank", required=True)
    p4 = sub.add_parser("advise", help="这一段该不该接声锚 / 该接谁（一段内多人时按「缝上第一个说话人」）")
    p4.add_argument("--bank", required=True, help="声库目录")
    p4.add_argument("--prompt", default="", help="本段提示词原文")
    p4.add_argument("--prompt-file", default="", help="本段提示词文件（与 --prompt 二选一）")
    p4.add_argument("--prev", default="", help="上一段提示词原文（判「缝上是否换人」）")
    p4.add_argument("--prev-file", default="", help="上一段提示词文件")
    p4.add_argument("--speakers", default="", help="显式给说话人（逗号分隔，按开口顺序）——"
                                                 "最小格式没有 subject_definitions 时用它")
    p4.add_argument("--prev-speaker", default="", help="显式给「上一段最后一个说话人」")
    p5 = sub.add_parser("va-ledger",
                        help="生成 VA 台账（档 2：逐句 who + chars）—— **零依赖，不需要 ASR**")
    p5.add_argument("--prompt-file", action="append", default=[],
                    help="提示词文件（**按段号顺序**可给多次）")
    p5.add_argument("--prompt-inline", action="append", default=[],
                    help="提示词**正文**（按段号顺序可给多次）—— 便于脚本 / 管道")
    p5.add_argument("--prompts-dir", default="", help="提示词目录（按 `--pattern` 排序取全部文件）")
    p5.add_argument("--pattern", default="seg*_提交.txt", help="配合 `--prompts-dir` 的通配（默认 seg*_提交.txt）")
    p5.add_argument("--start-stage", type=int, default=0, help="第 1 个提示词对应的**段号**（默认 0）")
    p5.add_argument("--speakers", default="",
                    help="名字解析不出来时显式给角色名（逗号分隔，按首次出现顺序）")
    p5.add_argument("--line-re", default="",
                    help="**覆盖台词块规则**（捕获组 1 = 台词正文；不给捕获组则取「标记后到 </d> 或行尾」）")
    p5.add_argument("--speaker-re", default="",
                    help="**覆盖说话人规则**（捕获组 1 = 标识；标识是纯数字 ⇒ 当作 Subject 编号，其它 ⇒ 当作名字）")
    p5.add_argument("--who", default="",
                    help="**完全手工**（不解析提示词）：逐句名单，逗号分隔；多段用 `;` 分段")
    p5.add_argument("--lines", default="",
                    help="**完全手工**：逐句权重，逗号分隔；多段用 `;` 分段（与 `--who` 一一对应）")
    p5.add_argument("--run", default="", help="写到 `<relay-root>/<run>/_va.json`")
    p5.add_argument("--relay-root", default="",
                    help="relay_kit 根目录；给 `--run` 时必填（或设 `COMFYUI_PATH` 环境变量）")
    p5.add_argument("--out", default="", help="直接指定输出文件（与 `--run` 二选一）")
    p5.add_argument("--dry-run", action="store_true", help="只打印，不写盘")
    p5.add_argument("--force", action="store_true",
                    help="覆盖已存在的台账（**默认拒绝** —— 手写台账可能含档 3 的 ASR 时间）")
    sub.add_parser("selftest", help="说话人解析规则的自检（可证伪）")
    a = ap.parse_args(argv)
    if a.cmd == "va-ledger":
        return _cmd_va_ledger(a)
    if a.cmd == "selftest":
        print("说话人解析自检（规则：取每个 <d> 最近的前驱说话人标记，按开口顺序去重）")
        return _selftest()
    if a.cmd == "advise":
        def _txt(inline, path):
            if inline:
                return inline
            if path:
                # ⚠️ 别在 return 处直接调用 open —— **`SIM115` 不报那个形态**（`review_050` 的 **H4f** 盯着）
                return Path(path).read_text(encoding="utf-8")
            return ""
        ok, lines, path = advise_anchor(
            a.bank, prompt=_txt(a.prompt, a.prompt_file),
            prev_prompt=_txt(a.prev, a.prev_file),
            speakers=[s.strip() for s in a.speakers.split(",") if s.strip()] or None,
            prev_speaker=a.prev_speaker.strip())
        for ln in lines:
            print("  " + ln)
        print("  ⇒ %s" % ("可以接锚" if ok else "🔴 没有可用锚（先 collect，或拔掉 voice_anchor 那根线）"))
        return 0 if ok else 3
    if a.cmd == "collect":
        w, msg = collect(a.mp4, a.name, a.bank, force=a.force, line=a.line)
        print(("[%s] %s" % ("OK" if w else "跳过", msg)))
        return 0 if w else 3
    if a.cmd == "lookup":
        w, why = lookup(a.bank, a.name)
        print("%s  （%s）" % (w or "⟨无⟩", why))
        return 0 if w else 3
    bank = load_bank(a.bank)
    for k in sorted(bank):
        r = bank[k]
        print("  %-12s ← %-28s (%.2fs, %s)" % (k, os.path.basename(r["wav"]),
                                               r.get("span_s", 0), r.get("src", "")))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
