# -*- coding: utf-8 -*-
# ruff: noqa: UP031, BLE001, UP009
"""voice_bank —— H3 长链声锚（voice anchor）声库：采集 / 查询 / 管理。

背景（2026-10-02 实测，见 I:\\_handover\\E1-TIHA-验证状态与三臂实测）：
    H3 拷贝桥把上一段的音频尾作为 audio_ref ⇒ **上段说话人的嗓音会成为
    本段嗓音的生成条件**。台词逐段换人时音色交叉污染（周砚 F0 114→131、
    谱质心 1028→1308）。给「本段说话人」的声锚后离基准距离缩到 1/5。

设计原则（对开源用户负责）：
    1. **声明谁说话，不猜测谁说话** —— 说话人来自提示词里
       `<Subject N> … 开口说话:<d>…</d>` 的声明（跑批脚本解析后传入），
       本模块不做声纹聚类（重依赖 + 同性别近似声线必错）。
    2. **手动锚永远优先** —— `voices/<角色名>.wav` 存在 ⇒ 自动采集永不覆盖。
    3. **降级可见** —— 采集失败（无语音/太短）⇒ 不写库、返回原因；
       查询未命中 ⇒ 调用方走"无锚"老路径。绝不静默装作有锚。
    4. 声锚质量下限：有声时长 ≥ MIN_VOICED_S（默认 0.6s）、峰值归一 0.9。

台词守卫的两层分工（2026-10-02 GG 指正后理清）：
    · **节点侧守卫**（H3RelayAudioSeam 的 speech-onset 守卫，relay_core.py:838 起，
      纯能量判据、零依赖、随包分发）—— 通用层：任何人开箱即用。
      本模块的 `longest_voiced_span`（能量法）与它同族：不需要 funasr 也能采。
    · **funasr ASR 验证**（本机已缓存模型，本地测试尺）—— 增强层：`--line` 给了
      台词原文且 funasr 可导入 ⇒ 验 CER + 逐字时间戳精确裁锚；funasr 不可用
      ⇒ 打印警告后回退能量法（不阻断）。模型进程内单例（944MB 只加载一次）。
      ⚠ 不作为开源默认依赖 —— 分发面与导网环境不可控。


声库布局（run 无关、跨 run 复用）：
    <bank_dir>/voices.json     {"周砚": {"wav": "...", "src": "2c_m2s1_00005",
                                  "voiced_s": 0.86, "collected": "2026-10-02T14:20"}, ...}
    <bank_dir>/<角色名>.wav    自动采集的锚（手动文件同名时它就是手动锚本身）

CLI：
    python voice_bank.py collect <mp4> --name 周砚 --bank I:/voices
    python voice_bank.py lookup  周砚 --bank I:/voices
    python voice_bank.py list   --bank I:/voices
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import wave

SR = 16000                    # 分析用重采样率（与实测量化脚本同口径）
MIN_VOICED_S = 0.6            # 声锚质量下限：有声时长
TARGET_PEAK = 0.9             # 声锚峰值归一目标（实测 0.9 ⇒ 模型跟随，不削顶）
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
def _load_mono(v: str, ext: str = "raw"):
    """mp4/任意音频 → 16k 单声道临时文件，返回路径。

    ext="raw"（s16le，供本模块 numpy 读）或 ext="wav"（供 funasr —— 其内部
    ffmpeg 靠扩展名推断格式，无扩展名会报 ``Failed to load audio``）。
    临时文件放系统临时目录（源目录可能只读）。
    """
    import tempfile
    out = os.path.join(tempfile.gettempdir(), "_vb_%d.%s" % (os.getpid(), ext))
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", v, "-vn", "-ac", "1", "-ar", str(SR)]
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
    # 小满长句被 0.12s 阈值拆成两截而拒采；0.40s 的句内停顿也要合回 ⇒ 0.5s）
    merged = []
    for t0, t1 in spans:
        if merged and t0 - merged[-1][1] < 0.5:
            merged[-1] = (merged[-1][0], t1)
        else:
            merged.append((t0, t1))
    return [(a, b) for a, b in merged if b - a >= min_s]


def longest_voiced_span(mp4: str):
    """找最长连续有声段，返回 ``(t0, t1, voiced_total_s)``；无语音返回 None。"""
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
    t0, t1 = max(spans, key=lambda ab: ab[1] - ab[0])
    return t0, t1, total


def _read_raw(path: str):
    import numpy as np
    return np.fromfile(path, dtype=np.int16).astype(np.float32) / 32768.0


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


_ASR_SINGLETON = None      # 效能：funasr 模型 944MB，进程内只加载一次（懒加载单例）


def _asr_model():
    global _ASR_SINGLETON
    if _ASR_SINGLETON is None:
        from funasr import AutoModel
        _ASR_SINGLETON = AutoModel(model=ASR_MODEL, device="cpu", disable_update=True)
    return _ASR_SINGLETON


def _asr_verify(mp4: str, line: str):
    """台词守卫：funasr 转写整段 → 与台词原文比对 CER。

    返回 ``(ok, cer, span_seconds 或 None, 转写文本)``。
    span = 台词第一个字起点 → 最后一个字终点（模型给的是 10ms 粒度），
    能拿到 ⇒ 精确按台词裁锚；拿不到 ⇒ None（调用方回退能量法）。
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
    ts = res.get("timestamp")
    span = None
    if ts:
        # timestamp = [[start_ms, end_ms], ...] 逐字 ⇒ 首字起点~末字终点
        span = (ts[0][0] / 1000.0, ts[-1][1] / 1000.0)
    ref = norm_text(line)
    c = cer(ref, norm_text(text)) if (ref and text) else 1.0
    return (c <= ASR_CER_MAX), c, span, text


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


def collect(mp4: str, name: str, bank_dir: str, force: bool = False,
            line: str = ""):
    """从已渲染段提取「角色名」的声锚并入库。

    返回 ``(wav_path 或 None, 消息)``。手动锚存在 ⇒ 直接返回手动锚（不覆盖）。
    """
    _base = _safe_name(name)
    if _base is None:
        return None, "🔴 角色名含路径分隔符/非法字符（%r）⇒ 拒绝（防路径注入）" % name
    name = _base
    if manual_wav(bank_dir, name) and not force:
        return manual_wav(bank_dir, name), "已有手动锚，跳过自动采集"
    if not force and name in load_bank(bank_dir):
        # 锁定语义：已有自动锚 ⇒ 首个稳定锚长期复用（锚换来换去反而让音色漂）
        return load_bank(bank_dir)[name]["wav"], "声库已有自动锚，跳过（--force 可覆盖）"

    # —— 台词守卫优先（有台词原文时）：funasr 验「念对没念对」+ 逐字时间戳精确裁锚 ——
    asr_span = None
    if line:
        try:
            ok, c, asr_span, text = _asr_verify(mp4, line)
        except Exception as e:
            # ⚠ 真回退：守卫**不可用**（funasr 缺失等）≠ 守卫**不通过**。
            # 这里必须继续走能量法；只有 ASR 跑通且 CER 超标才拒收。
            print("[voice-bank] ⚠ ASR 守卫不可用（%s: %s）⇒ 回退能量法"
                  % (type(e).__name__, str(e).splitlines()[0][:60]))
            line = ""
        else:
            if not ok:
                return None, ("🔴 台词守卫不通过：CER=%.2f > %.2f（转写「%s」vs 期待「%s」）"
                              "⇒ 语音不合格，绝不入锚" % (c, ASR_CER_MAX, text[:40], line[:40]))

    if asr_span:
        t1 = asr_span[1]
        t0 = max(asr_span[0], t1 - ANCHOR_MAX_S)
        # 🔴 ASR 窗 ∩ 能量证据：实测 funasr 时间戳会带**尾随噪声词**
        #    （S1 末字"止于 4.17s"而能量人声止于 1.90s ⇒ 直接裁 ⇒ 尾部全静音）。
        #    能量证据（同一段的有声检测）在人声终点上更可信 ⇒ 取两者交集。
        _hit = longest_voiced_span(mp4)
        if _hit is None:
            return None, "ASR 认为有台词但能量检测全静音 ⇒ 数据矛盾，不采集"
        _e1 = _hit[1]
        if t1 > _e1 + 0.10:
            t1 = _e1 + 0.05            # 收到能量证据的人声末尾（留 50ms 余量）
            t0 = max(t0, t1 - ANCHOR_MAX_S)
        dur = t1 - t0
        if dur < MIN_VOICED_S:
            return None, ("ASR 台词窗仅 %.2fs < 下限 %.2fs ⇒ 不采集" % (dur, MIN_VOICED_S))
        total = dur
    else:
        hit = longest_voiced_span(mp4)
        if hit is None:
            return None, "产物里没有检测到语音（模型可能没说话）⇒ 不采集"
        t0, t1, total = hit
        if total < MIN_VOICED_S:
            return None, ("有声仅 %.2fs < 下限 %.2fs ⇒ 不采集"
                          % (total, MIN_VOICED_S))
        dur = t1 - t0
        if dur < MIN_VOICED_S:
            # 最长句不够长 ⇒ 用整个有声带（跨停顿拼接会不自然，宁缺毋滥）
            return None, ("最长连续语音 %.2fs < 下限 %.2fs（总有声 %.2fs）⇒ 不采集"
                          % (dur, MIN_VOICED_S, total))

    os.makedirs(bank_dir, exist_ok=True)
    wav_out = os.path.abspath(os.path.join(bank_dir, "%s.wav" % name))
    # 先裁段，再在 Python 里做精确峰值归一（比 ffmpeg filter 直观且可校验）
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", "%.3f" % t0, "-t", "%.3f" % dur,
         "-i", mp4, "-vn", "-ac", "1", "-ar", str(SR), wav_out], check=False)
    import numpy as np
    raw = _load_mono(wav_out)
    x = _read_raw(raw)
    os.remove(raw)
    pk = float(abs(x).max())
    if pk < 1e-4:
        return None, "裁出的声锚近乎静音（峰值 %.4f）⇒ 不采集" % pk
    x = x / pk * TARGET_PEAK
    with wave.open(wav_out, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes((x * 32767).astype(np.int16).tobytes())
    import datetime
    bank = load_bank(bank_dir)
    bank[name] = {"wav": wav_out, "src": os.path.basename(mp4),
                  "voiced_s": round(total, 2), "span_s": round(dur, 2),
                  "collected": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
    save_bank(bank_dir, bank)
    return wav_out, ("采集 %.2fs（%.2f–%.2fs，峰值→%.2f）"
                     % (dur, t0, t1, TARGET_PEAK))


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
    a = ap.parse_args(argv)
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
