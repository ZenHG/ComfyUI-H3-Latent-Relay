# -*- coding: utf-8 -*-
# ruff: noqa: UP031, BLE001, UP009
"""voice_bank —— H3 长链声锚（voice anchor）声库：采集 / 查询 / 管理。

背景（2026-10-02 实测）：
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

CLI（`<声库目录>` 用你自己的路径，例如 `./voices`）：
    python voice_bank.py collect <mp4> --name 周砚 --bank <声库目录>
    python voice_bank.py lookup  周砚 --bank <声库目录>
    python voice_bank.py list   --bank <声库目录>
    # 「这一段该接谁的锚」—— 一段内多人时的规则（见下方 §该给谁做锚）
    python voice_bank.py advise --bank <声库目录> --prompt-file 段2.md --prev-file 段1.md
    python voice_bank.py advise --bank <声库目录> --speakers 许然,小满 --prev-speaker 许然
    python voice_bank.py selftest      # 说话人解析规则的自检（可证伪）

声锚的语义边界（一段内多人时尤其要记住）：
    声锚回答的问题**只有一个** —— 「**缝上接着说的那个人是谁**」。锚窗只有 ~0.9 秒
    （= 缝区，`context_frames` 换算），段内后续换人由 prompt 的 `<Subject N>` 决定。
    ⇒ **锚 = 本段第一个开口说话的人**；同人续接不必接；换人才必须接。细则见 `advise`。
"""

from __future__ import annotations

import json
import os
import re
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

_D_RE = re.compile(r"<d>")
_SPK_RE = re.compile(r"<Subject (\d+)>|\(S(\d+)\)")


def speakers_in_order(prompt: str) -> list:
    """本段说话人，按**开口顺序**去重（`<Subject N>` 形态）。"""
    out = []
    for m in _D_RE.finditer(prompt or ""):
        marks = list(_SPK_RE.finditer(prompt[:m.start()]))
        if not marks:
            continue
        g = marks[-1]
        tag = "Subject %s" % (g.group(1) or g.group(2))
        if tag not in out:
            out.append(tag)
    return out


def subject_name(prompt: str, tag: str) -> str:
    """把 `<Subject N>` 映射成**角色名**（best-effort）。

    六段式有 `subject_definitions:` 段（`<Subject 1> 许然，中国女性…`）⇒ 取名字。
    **最小格式没有这个段**（`<Subject N>` 后面跟的是动作句）⇒ 拿不到名字，
    原样返回 `Subject N`，由调用方自己映射（或用 `--speakers` 显式给）。
    """
    m = re.search(r"<%s>\s*([^\s，,：:；;。、\n]+)" % re.escape(tag), prompt or "")
    return m.group(1) if m else tag


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
    ("<Subject 1> 许然开口说话:<d>[zh]甲</d>，随后 <Subject 2> 小满开口说话:<d>[zh]乙</d>", ["Subject 1", "Subject 2"]),
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
    n = len(_SELFTEST_CASES) + len(_BW_CASES)
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
        _e1 = _spans_in[-1][1]
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
    p4 = sub.add_parser("advise", help="这一段该不该接声锚 / 该接谁（一段内多人时按「缝上第一个说话人」）")
    p4.add_argument("--bank", required=True, help="声库目录")
    p4.add_argument("--prompt", default="", help="本段提示词原文")
    p4.add_argument("--prompt-file", default="", help="本段提示词文件（与 --prompt 二选一）")
    p4.add_argument("--prev", default="", help="上一段提示词原文（判「缝上是否换人」）")
    p4.add_argument("--prev-file", default="", help="上一段提示词文件")
    p4.add_argument("--speakers", default="", help="显式给说话人（逗号分隔，按开口顺序）——"
                                                 "最小格式没有 subject_definitions 时用它")
    p4.add_argument("--prev-speaker", default="", help="显式给「上一段最后一个说话人」")
    sub.add_parser("selftest", help="说话人解析规则的自检（可证伪）")
    a = ap.parse_args(argv)
    if a.cmd == "selftest":
        print("说话人解析自检（规则：取每个 <d> 最近的前驱说话人标记，按开口顺序去重）")
        return _selftest()
    if a.cmd == "advise":
        def _txt(inline, path):
            if inline:
                return inline
            if path:
                return open(path, encoding="utf-8").read()
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
