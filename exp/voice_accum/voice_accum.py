# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""VA（Voice Accumulate）· 跨段「按人累积」音色参考 —— **零新依赖、默认关**。

## 它解决什么

跨段续接时，本段的音色参考默认只取**上一段**（0.925 s 或自动 2~6 s）。
用户要的是：「段 3 说话的人，段 1 段 2 都说过 ⇒ 能不能综合参考」。
⇒ 本模块把**同一个说话人**在**历史各段**里的语音片段收集起来，拼成一段当参考。

## 它为什么这么设计（四条，全部来自既有教训）

1. **零新依赖**：只用 `torch` / `numpy`（ComfyUI 自带）。
   🔴 **绝不 import funasr / ASR** —— 那是 944 MB 的本地模型，`secs_score.py` 已因同类依赖
   被移出仓库。ASR 只用于**我们本地**生成更精确的台账，**不进开源功能路径**。
2. **取尾不取头**：从**最近**的历史段往前取，累计够 `max_seconds` 就停。
   （旧 E1 的 `build_history_refs` 死在「取段头帧」上 —— 漂移不减反增，`1c87aff` 删除。）
3. **按内容/按人选，不按段号**：选的是「这个说话人说过的话」，不是「第 k 段」。
4. **门控 + 安全降级**：判不准就**返空**（主干逐位不变），绝不猜。

## 台账（唯一的外部输入）

`relay_kit/<run>/_va.json`，**段号 → 该段的说话人开口顺序**：

```json
{"1": ["<角色A>"], "2": ["<角色B>", "<角色A>"], "3": ["<角色A>", "<角色B>"]}
```

- **本段目标说话人 = 本段顺序里的第一个人**（= 缝上接着说的那个人，与声锚同规则）。
- 台账**没有本段** ⇒ 返空（不猜）。
- 台账**可由提示词直接抽**（纯正则，零依赖）；ASR 只是本地可选的时间精度增强。

## 2026-10-05 的六条问题编号（**本文件内自洽** —— 下面的注释都引它）

| 编号 | 一句话 | 证据等级 |
|---|---|---|
| **R1** | 参考内容本身是错的 —— 台账只给名字时**按等分切** ⇒ 装着**错人的音色 + 半句** | 实测日志（`按字数比例切 3 段（等分）`） |
| **R2** | 与**钉住前缀**的**同源不变式**被打破 —— `ref` 的尾部不再是 `pin` 的来源（**首疑**） | 代码自陈（`relay_core` 写明"两处同源、只改一处会打架"） |
| **R3** | 参考块尾部被**硬截断**（480→405 步）—— 切点落在音节中间 | 实测日志 + 代码 |
| **R4** | 一个**无标签**槽装多个说话人 ⇒ 条件歧义 | 两轮行为实测（多块 0.0497 / 并块 0.2395，都更糟） |
| **R5** | 参考变长 1.84×（5.5 → 10.12 s）—— 常驻行随每一步采样 | 理论（前沿：参考时长有收益拐点）+ 实测数值 |
| **R6** | `timbre_similarity` **近饱和** ⇒ 「按音色接近度补片」**实际等于按段号** | 实测（真实素材极差 0.031；夹具极差 0.001） |

⚖️ **R2/R5 未做过单变量实测**（本轮是零 GPU 改造），改造后需要按三臂法复验 —— 别把它们当已验证。
"""

from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import torch

#: 音频 latent 的采样率（步/秒）—— 与 `relay_core.AUDIO_HZ` 同值。
AUDIO_HZ: float = 40.0

#: 默认参数。全部可在 `_va.json` 的 `config` 节覆盖。
DEFAULT_MAX_S: float = 6.0        # **每个说话人**的参考音频时长上限（与 AUDIO_REF_MAX_S 同口径）
#: **全部说话人合计**的上限 —— 显存安全阀（本仓作者 2026-10-04：「太长了会显存压力很大」）。
#: 参考行是 `PackedLayout` 里 `audio_update=False` 的**常驻行**，随**每一步采样** ⇒ 总长越长越吃显存。
#: 🔵 **12 → 8 s（2026-10-05，前沿理论）**：零样本克隆的参考时长存在**收益拐点** ——
#:    XTTS-v2 系统评测（1/3/6/10/20/40 s）给出「1→10 s 提升明显、**20 s 之后递减**」，
#:    业界实践区间 = **6~10 s**。再叠加另一条：视频 DiT 的**注意力汇**（Wen et al. 2025）
#:    会让后层少数 token 吸走几乎全部注意力 ⇒ 参考行变长**不等于**影响力变大，可能只是被吸收。
#:    ⇒ 合计上限收到 8 s；想更长请**显式**填 `max_total_seconds`（并在 notes 里会看到本行）。
DEFAULT_MAX_TOTAL_S: float = 8.0
#: **一个无标签槽只承载一个说话人**（默认 1）。
#:
#: 🔵 理由（2026-10-04/05 两轮实测 + 前沿理论）：本包注入的音频块走
#:    `conditioning_set_values(..., append=True)`，**tokenize 早已发生** ⇒ 块**没有 `<Audio j>` 标签**，
#:    prompt 引不到它 ⇒ **模型无法知道哪一块是谁**。
#:    · 追加 N 块（无标签）实测 开/关两臂音频余弦 **0.0497**；
#:    · 并入 1 块但装 3 个人（R4）实测 **0.2395**，**两轮都更糟**。
#:    · 前沿的做法是给每个说话人**各自的控制通道**：Playmate2 的 Mask-CFG（arXiv 2510.12089）、
#:      AlignDiT 的多模态 CFG（arXiv 2504.20629）、Magpie 的分层分离 —— H3 没有这类旋钮。
#:    ⇒ 默认只给「缝上接着说的那个人」一块；**其余人请走官方 `ref_audios` 的 3 个带标签槽**
#:      （`<Audio j>` 按已接线顺序编号，见 `docs/07-chain.md` §一段内多人）。
#: ⚠️ 想放回「每人各一块」请显式 `config.max_speakers = N`，notes 会标明这是**未验证**用法。
DEFAULT_MAX_SPEAKERS: int = 1
#: 判不准时**是否**退回「按字数比例切」（含无字数时的**等分**）。**默认关**。
#:
#: 🔵 为什么默认关（2026-10-05，R1）：把一段三人对话**等分**成三段，切点只吸附到
#:    ±`snap_s` 内最近的能量间隙；真实素材上能量间隙比值只有约 2.2 ⇒ 吸附不可靠
#:    ⇒ 参考块里**必然**装着**错误说话人的音色**与**被切断的半句**（实测日志明写「等分」）。
#:    而前沿共识是**参考必须是单一说话人的完整话语**（CosyVoice 先静音分割再取关键片段；
#:    XTTS 把 6~10 s 定为最优）。
#:    ⇒ 「兜底」的正确含义是**保证功能可用**，不是**保证每人都分到一块**：
#:      判不准时改为只给缝上那个人**本段第一个有声片段**（同一说话人，绝不切比例）。
#: ⚠️ `True` = 保留旧的「等分/比例切」行为（老用户可显式打开；notes 会标明是估计）。
DEFAULT_PRIOR_SPLIT: bool = False
#: 音色相似度的**噪声下限**：候选之间的差距小于它就认为代理**无区分力**。
#:
#: 🔵 为什么需要（2026-10-05，R6，本轮新发现 + 实测）：`timbre_similarity` 是 latent
#:    逐通道均值/标准差的余弦，**尺度不变**（响度被消掉）⇒ 在真实素材上**近饱和**：
#:    · 本轮真实素材：三个不同角色对同一基准段给出 0.976 / 0.984 / 0.953 ⇒ 极差 **0.031**；
#:    · 零 GPU 实测（夹具，刻意把"相对对比度"从 (0.02,0.06) 改成 (0.01,0.07)）：极差仅 **0.001041**。
#:    ⇒ 此时「按音色接近度补片」**等价于**「按段号从近到远」——那一步的"智能"是假的。
#:
#: 🔴 **默认 0.0 = 不切换**（2026-10-05 决定）：本项的**判据归属 本仓作者**（「以第一次说话那一段为
#:    基准，再按音色接近度补后续段」是 本仓作者 2026-10-04 的口径）。我不擅自改判据，改成
#:    **每次跑都把"代理无区分力"这件事说出来**（见 `SIM_REPORT_NOISE`），并给一个开关。
#:    填 >0（建议 0.05）才真的切成「最近段优先」——那是有理论依据的备选规则（参考 = 即将续接的
#:    语境 ⇒ 近的比远的可信），但**必须实测**（验证矩阵 T7）后再定默认。
DEFAULT_SIM_NOISE: float = 0.0
#: **只报告、不切换**的阈值：候选极差小于它 ⇒ notes 里点明"这次排序实际是按段号"。
#: 取 0.05 = 真实素材实测极差 0.031 的量级上界；夹具 0.001 也远在阈值下 ⇒ 两种情形都会出声。
SIM_REPORT_NOISE: float = 0.05
#: **声锚已接时，是否让 VA 与声锚叠加**。**默认 False = 跳过 VA**（保持 0.6.27 的行为）。
#:
#: 🔵 背景（2026-10-06 读码确证，非推断）：声锚与 VA 在 `relay_core` 里是**两处独立**的干预 ——
#:    · 声锚**同时换** ref（`plan_relay:798`）与 pin（`build_continue_latent:4127`）
#:      ⇒ 等于**伪造"上段尾"**：让模型以为上段最后一句是这个人说的 ⇒ 音色被**硬**对齐；
#:    · VA **只换** ref（`plan.audio_ref`），pin 留**真实**上段尾 ⇒ 只给**软**引导。
#:    ⇒ 两者不是"抢一个槽"，而是**同一问题的两种素材来源**：
#:      声锚 = 用户给的干净锚文件（精度高、需准备）；VA = run 自己的历史 latent（自动、零准备）。
#:
#: ⇒ 两条素材可以**叠加**：`[历史素材…][声锚尾窗]`。末尾 = 声锚尾 = pin 的内容
#:    ⇒ 同源不变式仍成立。实现上**不需要新代码路径** —— `h3_adapter` 的 `tail_latent`
#:    取的就是 `plan.audio_ref` 的 latent，而声锚路径下它**已经是声锚尾窗**。
#:
#: 🔴 **为什么默认 False**（2026-10-06）：叠加组合**零次实测**；而「声锚优先」是**已验证**的
#:    （声锚 0.6.1 实测离基准 1/5；VA 单开经盲听胜出）。**未实测的组合不许进默认路径。**
#:    要试叠加请显式 `config.with_anchor = true`，notes 会写明「已叠加」。
#: ⚠️ 打开后 ref 变长（历史素材 + 声锚尾）—— 参考行是**常驻行、随每步采样** ⇒ 吃显存、拖速度。
DEFAULT_WITH_ANCHOR: bool = False
DEFAULT_MAX_STAGES: int = 8       # 最多往回看几段
DEFAULT_MIN_SPAN_S: float = 0.25  # 单段有声片段的时长下限（更短的不算一句）
DEFAULT_GAP_S: float = 0.18       # 间隙小于它就并成同一句
#: 「有声」判据 = 每格 RMS 的**一阶差分** > P99 × 本值。
#: 🔴 必须是**差分**而不是 RMS 绝对值：真实路径拿到的是 VAE 音频 latent（已归一化），
#:    静音段的 RMS 与语音段同量级 ⇒ 用绝对值判会得到「有声格 = 0」。
#: 🔴 基准必须取 **P99**（不是 max / P50）—— 理由与实测横评见
#:    `relay_core.AUDIO_REF_VOICE_REL` 的注记（同一套判据，同一套理由）。
VOICE_REL: float = 0.20

#: 兜底档的切点吸附半径（秒）—— 切点只在 ±本值内吸附到真实能量间隙。
DEFAULT_SNAP_S: float = 0.40
#: `strict=True` 时，自动切分判不准就**返空**（fail-closed）；默认 `False` = **兜底优先**
#: （本仓作者 2026-10-04：「开源项目必须考虑其他用户千奇百怪的素材，需要智能兼容，
#:   保证功能的完整性和兜底措施」⇒ 默认给结果 + 在 notes 里标明是估计）。
DEFAULT_STRICT: bool = False

#: `_va.json` 顶层允许的键。**未知键 ⇒ 警告**（不 raise：白名单会随版本增长）。
#: 🆕 `_note`（2026-10-05）：`tools/voice_bank.py va-ledger` 生成的台账会带一行**来源说明**
#: （"由提示词生成 / chars 是近似音节数"）。它是**元数据、不参与判定**，加进白名单是为了
#: 让生成的台账**不触发未知键警告**（否则每次跑都刷一行噪音）。
KNOWN_TOP_KEYS = ("config", "stages", "_note")

#: `_bool` 认得的**字符串**真假写法（大小写与首尾空白无关）。
#:
#: 🔵 为什么需要（2026-10-06，**智能兼容**）：`_va.json` 是**用户手写的**，而 Python 里
#:    `bool("false") == True` ⇒ 用户写字符串 `"false"`（意图是**关**）会被当成**开**。
#:    这是最坏的一类失败：**不报错、行为相反** —— 比崩还难查。
#:    同理 `"0"` / `"no"` / `""` 都应判为假。中英都收（本仓用户以中文为主）。
_BOOL_TRUE = ("1", "true", "t", "yes", "y", "on", "是")
_BOOL_FALSE = ("0", "false", "f", "no", "n", "off", "否", "")


# --------------------------------------------------------------------- 配置
def load_config(path: str) -> Optional[dict]:
    """读 `_va.json`。**文件不存在 ⇒ None（= 关）**；存在但**结构坏** ⇒ raise（不静默降级）。

    🔵 **`utf-8-sig` 而不是 `utf-8`**（2026-10-06，智能兼容）：Windows 记事本保存
       "UTF-8" 时**默认加 BOM**（`EF BB BF`）⇒ 用 `utf-8` 会读到 `\\ufeff{...}`
       ⇒ `json` 直接抛 `JSONDecodeError`，报的还是"第 1 行第 1 列"，**看不出是 BOM**。
       `utf-8-sig` 会**吃掉** BOM；没有 BOM 时行为与 `utf-8` 完全一致 ⇒ **零副作用**。
    """
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8-sig") as fh:
            raw = json.load(fh)
    except Exception as exc:                                   # noqa: BLE001
        raise RuntimeError("VA 配置读取失败：%s ⇒ %s" % (path, exc)) from exc
    if not isinstance(raw, dict):
        raise RuntimeError("VA 配置顶层必须是对象，得到 %s：%s" % (type(raw).__name__, path))
    return raw


def anchor_gate(has_anchor: bool, cfg: dict) -> Tuple[bool, str]:
    """声锚已接时是否让 VA 让路 —— **纯函数**（零依赖、可独立单测）。

    ============  ====================  =========================================
    `has_anchor`  `cfg["with_anchor"]`  返回
    ============  ====================  =========================================
    False         任意                  ``(False, "")`` —— 不涉及，照常走
    True          False（默认）          ``(True, 原因)`` —— **跳过 VA**
    True          True                  ``(False, 告警)`` —— **叠加**（未实测）
    ============  ====================  =========================================

    🔴 **`has_anchor` 必须是真 bool，否则 raise**（2026-10-06 第 2 轮审核加固）：
       `None` / `0` 这类值会被 `not has_anchor` 当成"**没声锚**"⇒ **静默放行 VA**。
       而"声锚是否已接"是个**确定的判断**（`voice_anchor is not None`）⇒ 含糊值只可能是调用方写错，
       按仓库纪律（硬错误一律 raise，不测静默降级）应当场报错，而不是猜。

    🔵 `cfg` 不是 dict（含 `None`）时**按默认值判**，不崩 —— 本函数是对外可单独调用的纯函数，
       不该要求调用方先保证类型。
    🔵 为什么默认跳过关掉的原因写在 `DEFAULT_WITH_ANCHOR` 的注释里（未实测的组合不进默认路径）。
       本函数单独抽出来的理由：`h3_adapter` 的职责边界是"**不含任何算法**"
       （见该文件头注释）⇒ 判据放这里，适配层只取结果。
    """
    if not isinstance(has_anchor, bool):
        raise TypeError(
            "anchor_gate：`has_anchor` 必须是 bool（拿到 %s）—— 它是「声锚是否已接」的判断结果，"
            "含糊值会被当成「没声锚」而**静默放行**。" % type(has_anchor).__name__)
    if not has_anchor:
        return False, ""
    _w = cfg.get("with_anchor", DEFAULT_WITH_ANCHOR) if isinstance(cfg, dict) \
        else DEFAULT_WITH_ANCHOR
    if not bool(_w):
        return True, (
            "声锚已接 ⇒ **跳过 VA**（默认；未实测的组合不进默认路径）。"
            "想让 VA 与声锚**叠加**（参考 = `[历史素材…][声锚尾窗]`，末尾 = 声锚尾 = pin 的内容，"
            "同源不变式仍成立）请填 `config.with_anchor = true`。")
    return False, (
        "⚗️ **声锚 + VA 叠加**（`config.with_anchor=true`）—— 参考 = `[历史素材…][声锚尾窗]`。"
        "🔴 本组合**未实测**，与「声锚单独」没有可比结论；出问题请先关掉这个开关再复现。")


def parse_config(raw: dict) -> Tuple[dict, Dict[str, List[str]], Dict[str, list],
                                     Dict[str, list], List[str]]:
    """→ ``(cfg, stage_orders, stage_lines, stage_chars, notes)`` —— **五个**。

    `stages` 的每个值支持**两档**：

    ```json
    "1": ["<角色A>", "<角色B>", "<角色C>"]                     // 档 1：只有开口顺序（零依赖）
    "1": [{"who":"<角色A>","t0":1.20,"t1":3.80},              // 档 2：带**逐句时间**（推荐）
          {"who":"<角色B>","t0":4.30,"t1":5.80}, …]
    ```

    🔴 **为什么需要档 2**（2026-10-04 实测）：档 1 只能靠**能量/音色**自己切分说话人边界，
       而在这批真实素材上**两条判据都不成立** ——
       · 能量间隙：段0 最大间隙 0.47 s、中位 0.21 s（比值仅 2.2，且最大间隙旁边还有个
         0.30 s 的**句内**停顿）⇒ 切错；
       · 音色变化最大处：峰 1.50 / 7.75，真值 ≈ 2.86 / 5.76 ⇒ **完全不重合**（曲线极平）。
       ⇒ **判不准就不猜**（档 1 下 `assign_spans` 会返空、VA 不生效）。
       档 2 的 t0/t1 由**本地离线**生成（可用 ASR 逐字时间戳），**不进开源依赖**。
    """
    notes: List[str] = []
    for k in raw:
        if k not in KNOWN_TOP_KEYS:
            notes.append("⚠ 未知配置键 `%s` 已忽略（本次跑的是默认值）" % k)
    c = raw.get("config") or {}
    if not isinstance(c, dict):
        raise RuntimeError("VA 配置的 `config` 节必须是对象，得到 %s" % type(c).__name__)

    def _num(key, default, lo, hi):
        """智能解析数字：**认不出 ⇒ 用默认值 + 出声**（不崩）。

        🔵 为什么改成兜底（2026-10-06，智能兼容）：`_va.json` 是用户手写的，写错一个数字
           就让**整条续接链失败**是不划算的。判据 = **配置值**（用户意图不明 ⇒ 兜底 + 出声）；
           **结构/契约**（`config` 不是对象、`stages` 不是列表）才 raise。
           ⚠️ `True` 会被 `float()` 当成 1.0 —— 病态但有界（`lo`/`hi` 钳着），不另判。
        """
        v = c.get(key, default)
        try:
            f = float(v)
        except (TypeError, ValueError):
            notes.append("⚠ 配置 `%s` 的值 `%r` 不是数字 ⇒ 按默认值 %s 处理"
                         % (key, v, default))
            f = float(default)
        return max(lo, min(hi, f))

    def _bool(key, default):
        """智能解析布尔（见 `_BOOL_TRUE` / `_BOOL_FALSE`）。**认不出 ⇒ 默认值 + 出声**。"""
        if key not in c:
            return default
        v = c[key]
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return bool(v)
        if isinstance(v, str):
            s = v.strip().lower()
            if s in _BOOL_TRUE:
                return True
            if s in _BOOL_FALSE:
                return False
        notes.append("⚠ 配置 `%s` 的值 `%r` 认不出 ⇒ 按默认值 %s 处理"
                     "（布尔请写 JSON 的 `true` / `false`）" % (key, v, default))
        return default

    cfg = {
        "max_seconds": _num("max_seconds", DEFAULT_MAX_S, 0.05, 60.0),
        "max_total_seconds": _num("max_total_seconds", DEFAULT_MAX_TOTAL_S, 0.05, 120.0),
        "max_stages": int(_num("max_stages", DEFAULT_MAX_STAGES, 1, 64)),
        "min_span_s": _num("min_span_s", DEFAULT_MIN_SPAN_S, 0.0, 10.0),
        "gap_s": _num("gap_s", DEFAULT_GAP_S, 0.0, 10.0),
        "snap_s": _num("snap_s", DEFAULT_SNAP_S, 0.0, 5.0),
        "strict": _bool("strict", DEFAULT_STRICT),
        # 🆕 2026-10-05（前沿理论赋能），三条的动机见各自默认值处的注释
        "max_speakers": int(_num("max_speakers", DEFAULT_MAX_SPEAKERS, 1, 8)),
        "prior_split": _bool("prior_split", DEFAULT_PRIOR_SPLIT),
        "sim_noise": _num("sim_noise", DEFAULT_SIM_NOISE, 0.0, 1.0),
        # 🆕 2026-10-06：声锚已接时是否叠加 VA（动机与代价见 DEFAULT_WITH_ANCHOR 的注释）
        #    `_bool` 而非 `bool` ⇒ 用户写字符串 `"false"` 也能正确判为**关**
        "with_anchor": _bool("with_anchor", DEFAULT_WITH_ANCHOR),
    }
    stages_raw = raw.get("stages") or {}
    if not isinstance(stages_raw, dict):
        raise RuntimeError("VA 配置的 `stages` 节必须是对象（段号 → 说话人顺序/逐句时间）")
    stage_orders: Dict[str, List[str]] = {}
    stage_lines: Dict[str, list] = {}
    stage_chars: Dict[str, list] = {}
    for k, v in stages_raw.items():
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, (list, tuple)) or not v:
            raise RuntimeError(
                "VA 配置 `stages.%s` 必须是「角色名」或「逐句对象列表」，得到 %r" % (k, v))
        order, lines, chs = [], [], []
        for e in v:
            if isinstance(e, str):
                if not e.strip():
                    raise RuntimeError("VA 配置 `stages.%s` 里有空角色名" % k)
                order.append(e.strip())
                lines.append(None)
                chs.append(None)
            elif isinstance(e, dict):
                who = str(e.get("who") or "").strip()
                if not who:
                    raise RuntimeError("VA 配置 `stages.%s` 的条目缺 `who`：%r" % (k, e))
                t0, t1 = e.get("t0"), e.get("t1")
                order.append(who)
                lines.append(None if t0 is None or t1 is None
                             else (float(t0), float(t1)))
                chs.append(float(e["chars"]) if e.get("chars") is not None else None)
            else:
                raise RuntimeError(
                    "VA 配置 `stages.%s` 的条目只能是字符串或对象，得到 %r" % (k, e))
        stage_orders[str(k)] = order
        # 只有**逐句时间齐全**才启用档 2（缺一句就整段退回档 1，不半用）
        stage_lines[str(k)] = (lines if all(x is not None for x in lines) else [])
        stage_chars[str(k)] = (chs if all(x is not None for x in chs) else [])
    if not stage_orders:
        notes.append("⚠ `stages` 为空 ⇒ 无可锚内容，返空（主干逐位不变）")
    return cfg, stage_orders, stage_lines, stage_chars, notes


# ------------------------------------------------------------------ 有声切分
def voiced_spans(x: Any, sr: float = AUDIO_HZ,
                 min_s: float = DEFAULT_MIN_SPAN_S,
                 gap_s: float = DEFAULT_GAP_S) -> List[Tuple[float, float]]:
    """把一段音频（波形或 latent，``[..., T]``）切成**有声片段** `[(t0, t1), …]`（秒）。

    判据 = 每格 RMS 的**一阶差分** > ``P99 × VOICE_REL``（相对量 ⇒ 与素材绝对电平无关）。
    相邻有声格之间的空隙 < ``gap_s`` 就并成同一句；短于 ``min_s`` 的片段丢弃。

    ⚠️ **本函数不猜"谁在说"** —— 它只回答"哪里有人在说"。归属由 `assign_spans()` 按台账做。
    """
    a = x.detach().to(torch.float32) if torch.is_tensor(x) else torch.as_tensor(x)
    if a.ndim < 1:
        return []
    t = int(a.shape[-1])
    if t <= 1:
        return []
    c = 1
    for d in tuple(int(v) for v in a.shape[:-1]):
        c *= d
    w = a.reshape(max(1, c), t)
    if not bool(torch.isfinite(w).all()):
        return []                                    # 含 NaN/Inf ⇒ 拒判（不静默当"安静"）
    rms = (w ** 2).mean(dim=0).sqrt()
    r = rms.to("cpu").numpy()
    if float(np.max(r)) < 1e-6:
        return []                                    # 绝对静音（物理事实，不是相对判断）
    d = np.abs(np.diff(r, prepend=r[:1]))
    thr = float(np.quantile(d, 0.99)) * VOICE_REL
    voiced = d > thr
    if not bool(voiced.any()):
        return []
    idx = np.flatnonzero(voiced)
    gap = max(1, int(round(float(gap_s) * float(sr))))
    min_g = max(1, int(round(float(min_s) * float(sr))))
    spans: List[Tuple[int, int]] = []
    s0, prev = int(idx[0]), int(idx[0])
    for i in idx[1:]:
        i = int(i)
        if i - prev > gap:
            spans.append((s0, prev))
            s0 = i
        prev = i
    spans.append((s0, prev))
    out = [(a0 / sr, (b0 + 1) / sr) for a0, b0 in spans if (b0 + 1 - a0) >= min_g]
    return out


def assign_by_prior(spans: Sequence[Tuple[float, float]],
                    order: Sequence[str],
                    chars: Optional[Sequence[float]] = None,
                    snap_s: float = DEFAULT_SNAP_S) -> Tuple[Dict[str, List[Tuple[float, float]]], str]:
    """**结构档（主路径）**：把有声区按**每句字数比例**切成 `len(order)` 段，边界**吸附到最近的能量间隙**。

    🔵 **为什么这是主路径而不是"兜底"**（本仓作者 2026-10-04：「参考原有声锚怎么处理的」）：
       原声锚**从不做时间检测** —— 锚窗由 `context_frames` 换算（结构位置），"该给谁"由
       提示词正则决定（`voice_bank.speakers_in_order`），并明确「段内 ≥2 人 ⇒ 只覆盖缝区，
       后面靠 prompt」。⇒ **用结构位置代替时间检测**是这个机制一贯的范式，不是退让。
       本档照抄该范式：**顺序来自提示词（零依赖）**，位置用**比例 + 能量吸附**（零依赖）。
       ASR 逐句时间只是**可选增强**，不是必需。

    🔴 为什么需要它（本仓作者 2026-10-04：「开源项目必须考虑其他用户千奇百怪的素材，需要智能兼容，
       保证功能的完整性和兜底措施」）：能量间隙 / 音色变化两条判据在真实素材上**都可能分不开**
       （实测：段0 最大间隙 0.47 s / 中位 0.21 s，比值仅 2.2）⇒ 只靠它们会**返空 = 功能缺失**。
       本档**一定给出结果**（每句字数可从提示词零依赖抽出），并在 notes 里**标明是估计**。

    做法：① 有声区 = `[首片起点, 末片终点]`；② 按字数比例定 n−1 个切点；
          ③ 每个切点**吸附到 ±`snap_s` 内最近的能量间隙中点**（让边界落在真实停顿里）。
    🔴 **逐句语义（2026-10-05）**：`order` 的一个元素 = 提示词里的**一个 `<d>` 句**。
       同一个人说两句 ⇒ 他在 `order` 里出现两次 ⇒ **拿两段**。
       旧写法按 `set(order)` 去重 ⇒ 两句并成一段，等于把"他两句之间的停顿 + 夹在中间别人的话"
       都算成他的时长（那正是 R1 的错法）。
    """
    if not spans:
        return {}, "段内无有声片段"
    if len(set(order)) == 1:
        return {order[0]: [(spans[0][0], spans[-1][1])]}, "单说话人段：整段归他"
    n = len(order)
    v0, v1 = spans[0][0], spans[-1][1]
    total = v1 - v0
    if total <= 1e-6 or n < 2:
        return {}, "有声区太短，无法按比例切"
    _by_line = bool(chars) and len(chars) == n
    w = [float(x) for x in chars] if _by_line else [1.0] * n
    sw = float(sum(w)) or 1.0
    mids = [(spans[i][1] + spans[i + 1][0]) / 2.0 for i in range(len(spans) - 1)]
    cuts, acc = [], 0.0
    for i in range(n - 1):
        acc += w[i] / sw
        c = v0 + total * acc
        near = [m for m in mids if abs(m - c) <= float(snap_s)]
        cuts.append(min(near, key=lambda m: abs(m - c)) if near else c)
    bounds = [v0] + sorted(cuts) + [v1]
    table: Dict[str, List[Tuple[float, float]]] = {}
    for i, sp in enumerate(order):
        table.setdefault(sp, []).append((bounds[i], bounds[i + 1]))
    return table, ("按**逐句**字数比例切 %d 句（%s），边界吸附到能量间隙"
                   % (n, "带逐句字数" if _by_line else "等分"))


def assign_first_line(spans: Sequence[Tuple[float, float]],
                      order: Sequence[str],
                      target: str) -> Tuple[Dict[str, List[Tuple[float, float]]], str]:
    """**默认兜底**：判不准时，只把**本段第一个有声片段**给**缝上第一个人**。

    🔵 为什么是"第一片"而不是"等分切"（2026-10-05，R1，有实测日志）：

    · 顺序来自提示词（`voice_bank.speakers_in_order()`）⇒ 本段**第一个开口的人** = `order[0]`
      = **缝上接着说的那个人**（与声锚同规则）。
    · 一段对话里，**第一个有声片段**极大概率就是 `order[0]` 的第一句 ⇒ 内容**属于同一个
      说话人**，最坏也只是被句内停顿截短（素材量少一点）。
    · 对比「按比例等分」：等分**必然**把别人的音色算进来，而且模型**不报错** ——
      症状只出现在成片里（2026-10-05 实测日志：`按字数比例切 3 段（等分）`）。
      ⇒ 实测代价：开/关两臂音频余弦 0.2395（相差越大越说明条件被污染）。

    🔴 **必须 `len(spans) >= 2` 才允许走这条路**（本轮审核抓到的真 bug）：
       能量判据**把整段判成一个片段**时（真实素材上很常见 —— 段内停顿太短、比值只有约 2.2），
       `spans[0]` 就是**整段** ⇒ 里面**混着所有说话人** ⇒ 正是 R1 要治的病。
       "第一片是 order[0] 的一句"这个前提**只在片段真的被分开了**时成立。
       ⇒ 单片段时返回空表（交由更上层：有字数先验就按比例切，否则**不给**）。

    ⇒ 判据 = 「**宁可少给，不可给错**」：**同一个人被截短** >> **别人的音色混进来**。

    返回 `{target: [spans[0]]}`；不满足条件时返回空表（**不猜**）。
    """
    if not spans or not order:
        return {}, "段内无有声片段"
    if len(spans) < 2:
        return {}, ("整段只切出 **1 个**有声片段 ⇒ 「第一片 = order[0] 的一句」这个前提不成立"
                    "（它可能是整段、混着所有人）⇒ 默认不给片")
    if str(target) != str(order[0]):
        return {}, ("`%s` 不是本段第一个开口的人（%s）⇒ 默认不给片（它的位置无从判断）"
                    % (target, order[0]))
    return ({str(target): [spans[0]]},
            "兜底 · 本段**第一个有声片段**（单一说话人，绝不切比例）"
            "—— 若它在句中被切短，只影响素材量、不影响说话人")


def assign_spans(spans: Sequence[Tuple[float, float]],
                 order: Sequence[str]) -> Tuple[Dict[str, List[Tuple[float, float]]], str]:
    """把有声片段按**台账给的开口顺序**归给说话人。

    返回 ``(归属表, 说明)``。**判不准 ⇒ 空表 + 说明**（门控，不猜）：

    | 情形 | 处理 |
    |---|---|
    | 该段只有 1 个说话人（含"同一人连说两句" ⇒ 台账里名字出现两次） | ✅ 整段音频都归他 |
    | 片段数 == 说话人数 | ✅ 按顺序 1:1（**同一人的多个片段会合并，不互相覆盖**） |
    | 片段数 < 说话人数（两句连读被并成一段） | 🔴 返空 —— 顺序会错位，错位就是**把别人的音色当他的** |
    | 片段数 > 说话人数 | 🔴 返空 —— 同一句被切成多段，无法判断哪几段属于谁 |

    🔴 **`order` 里同一角色可能出现多次**（`speakers_in_order()` 按 `<d>` 逐个记，同一人连说
       两句就出现两次）⇒ 归属表必须**按名字累加**，不能 `{sp: [spans[i]]}` 那样直接覆盖
       （2026-10-04 审核抓到：直接覆盖会让**第二句把第一句挤掉**）。
    """
    if not spans:
        return {}, "该段无有声片段"
    if len(set(order)) == 1:
        return {order[0]: list(spans)}, "单说话人段：整段归他"
    if len(spans) == len(order):
        table: Dict[str, List[Tuple[float, float]]] = {}
        for i, sp in enumerate(order):
            table.setdefault(sp, []).append(spans[i])
        return table, "片段数 == 说话人数：按顺序 1:1"
    return {}, ("片段数 %d ≠ 说话人数 %d ⇒ 不猜（避免把别人的音色算给他）"
                % (len(spans), len(order)))


# ------------------------------------------------------------------ 音色摘要
def timbre_signature(x: Any) -> np.ndarray:
    """零依赖「音色摘要」= 音频 latent 的 **逐通道均值 + 逐通道标准差**（2C 维，L2 归一化）。

    🔴 **这是近似，且未验证**：我们**不知道**它是否真的对应人耳说的"音色接近"。
       - 为什么不给节点装权威尺子：**SECS 依赖 modelscope/funasr**（已因此被移出仓库）
         ⇒ 违反「开源零依赖」。SECS 尺子只在我们本地离线用（不进本包）。
       - 为什么用 latent 通道统计而不是时域频谱：音频 latent 只有 **40 Hz** 栅格，
         时域频谱只到 20 Hz（那是韵律、不是音色）；而 VAE 的**通道统计**是它编码音色的那部分。
       - ⇒ 想要**权威**判断只能用 SECS **离线**（本地，不进节点）。
         ⚠️ **目前没有把离线结果接回节点的通路**（未实现）—— 节点侧只有这个近似。
    """
    a = x.detach().to(torch.float32) if torch.is_tensor(x) else torch.as_tensor(x)
    if a.ndim < 2:
        return np.zeros(2, dtype=np.float64)
    t = int(a.shape[-1])
    if t < 2:
        return np.zeros(2 * max(1, int(a.numel())), dtype=np.float64)
    w = a.reshape(-1, t).to("cpu").numpy().astype(np.float64)
    v = np.concatenate([w.mean(axis=1), w.std(axis=1)])
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v


def timbre_similarity(x: Any, y: Any) -> float:
    """两个音频块的「音色相似度」= 摘要的余弦（越大越像）。**近似，未验证**。"""
    a, b = timbre_signature(x), timbre_signature(y)
    if a.shape != b.shape or a.size == 0:
        return -1.0
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na < 1e-12 or nb < 1e-12:
        return -1.0
    return float(np.dot(a, b) / (na * nb))


#: `_slice_audio` 的「不限量」哨兵（别写 `10**9` 这种魔法数）
_NO_LIMIT = 1 << 30


def _slice_audio(a: torch.Tensor, t0: float, t1: float, room: int) -> Optional[torch.Tensor]:
    """从 `a` 切 `[t0, t1)` 秒，并按 `room` 步**取尾**（近的比远的可信）。"""
    t_total = int(a.shape[-1])
    i0 = max(0, min(t_total, int(math.floor(t0 * AUDIO_HZ))))
    i1 = max(0, min(t_total, int(math.ceil(t1 * AUDIO_HZ))))
    if i1 - i0 < 1 or room < 1:
        return None
    if i1 - i0 > room:
        i0 = i1 - room
    return a[..., i0:i1].clone()


# ------------------------------------------------------------------ 按人累积
def accumulate_for(target: str,
                   stage_orders: Dict[int, Sequence[str]],
                   stage_audio: Dict[int, Any],
                   *,
                   current_stage: int,
                   first_stage: Optional[int] = None,
                   stage_lines: Optional[Dict[int, list]] = None,
                   stage_chars: Optional[Dict[int, list]] = None,
                   strict: bool = DEFAULT_STRICT,
                   snap_s: float = DEFAULT_SNAP_S,
                   max_seconds: float = DEFAULT_MAX_S,
                   max_stages: int = DEFAULT_MAX_STAGES,
                   min_span_s: float = DEFAULT_MIN_SPAN_S,
                   gap_s: float = DEFAULT_GAP_S,
                   prior_split: bool = DEFAULT_PRIOR_SPLIT,
                   sim_noise: float = DEFAULT_SIM_NOISE) -> Tuple[Optional[torch.Tensor], List[str]]:
    """按人累积：以**该角色首次开口的那一段为基准**，再按音色接近度补后续段。

    本仓作者 2026-10-04 的规则（抗漂移）：

    1. **基准 = 该角色第一次说话的段**（在段 1 说 ⇒ 段 1 是基准；首次在段 3 ⇒ 段 3 是基准）。
       基准**永远入选**，不因相似度被挤掉。
    2. 其余候选段按**与基准的音色相似度降序**补进来，直到 `max_seconds`。
    3. 只回看 `current_stage - 1` 往回、最多 `max_stages` 段；**一片都没收到 ⇒ `None`**。

    ⚠️ 相似度用 `timbre_similarity()`（**零依赖近似，未验证**）；台账的 `pick` 可覆盖。
    """
    picked: List[torch.Tensor] = []
    notes: List[str] = []
    want = float(max_seconds)
    lo = max(0, int(current_stage) - int(max_stages))

    # ① 逐段切出该角色的片段（每段一个块，段内取尾）
    per_stage: Dict[int, List[torch.Tensor]] = {}
    for k in sorted(stage_orders):
        if not (lo <= int(k) < int(current_stage)):
            continue
        order = stage_orders.get(k) or []
        audio = stage_audio.get(k)
        if target not in order or audio is None:
            continue
        a = audio.detach().to(torch.float32) if torch.is_tensor(audio) else torch.as_tensor(audio)
        if a.ndim == 3:
            a = a.unsqueeze(0)
        if a.ndim != 4:
            notes.append("段 %d：音频形态 %s 不是 [B,C,2,T] ⇒ 跳过" % (k, tuple(a.shape)))
            continue
        spans = voiced_spans(a, min_s=min_span_s, gap_s=gap_s)
        lines = (stage_lines or {}).get(k) or []
        chars = (stage_chars or {}).get(k) or []
        if lines:
            # 档 3：台账给了**逐句时间** ⇒ 直接用（不猜）。**最精确**。
            mine = [(float(t0), float(t1)) for (who, t0, t1) in lines if who == target]
            why = "逐句时间（台账档 3，精确）"
        else:
            table, why = assign_spans(spans, order)
            mine = table.get(target) or []
            if not mine and not strict:
                # 兜底链，**顺序有讲究**（2026-10-05）：
                #   ① 台账给了**每句字数** ⇒ 这不是盲猜 ⇒ 按比例切（有信息就先用信息）；
                #   ② 只给**缝上那个人**本段第一个有声片段（单一说话人，绝不切比例）；
                #   ③ `prior_split=True` 才做的**显式**盲切（等分）。
                # 🔴 底线：**任何一步都不许"反正先给一个"** —— 判不准就不给片（主干逐位不变）。
                # ⚠️ 失败理由要**全部留下**（旧写法每步覆盖 `why` ⇒ 用户看不到"为什么没给"）。
                _reasons: List[str] = []
                if chars and len(chars) == len(order) and len(set(order)) > 1:
                    # 台账**给了每句字数** ⇒ 这不是"盲猜"（有先验），才允许按比例切。
                    # 🔴 门按 **`len(order)`（= 句数）** 判，**不按** `len(set(order))`（= 人数）——
                    #    逐句的 `chars` 与 `order` 一一对应（同一人说两句就有两个权重）。
                    table, why = assign_by_prior(spans, order, chars, snap_s=snap_s)
                    mine = table.get(target) or []
                    _reasons.append(("结构档（带逐句字数）· " + why) if mine else why)
                if not mine:
                    table, why = assign_first_line(spans, order, target)
                    mine = table.get(target) or []
                    _reasons.append(("结构档 · " + why) if mine else why)
                if not mine and prior_split:
                    table, why = assign_by_prior(spans, order, chars, snap_s=snap_s)
                    mine = table.get(target) or []
                    _reasons.append(("opt-in 结构档（无字数先验 ⇒ 等分）· " + why) if mine else why)
                if mine:
                    why = _reasons[-1]
                else:
                    _reasons.append("判不准 ⇒ **不给片**（等分切会把别人的音色算给他 —— 2026-10-05 R1）；"
                                    "确认素材允许盲切请填 `config.prior_split=true`，"
                                    "或给台账补每句 `chars` / 逐句时间（档 2/3）")
                    why = " ｜ ".join(_reasons)
        if not mine:
            notes.append("段 %d：归属失败（%s）⇒ 跳过" % (k, why))
            continue
        notes.append("段 %d：归属方式 = %s" % (k, why))
        got = [_slice_audio(a, t0, t1, _NO_LIMIT) for t0, t1 in mine]
        got = [g for g in got if g is not None]
        if got:
            per_stage[k] = got

    if not per_stage:
        return None, notes + ["🔴 历史段里收不到 `%s` 的素材 ⇒ 返空（主干逐位不变）" % target]

    # ② 基准段优先（本仓作者 规则：以第一次说话那一段为基准）
    base_k = None
    if first_stage is not None and int(first_stage) in per_stage:
        base_k = int(first_stage)
    else:
        base_k = min(per_stage)                      # 已知窗口里最早的那段（降级，出声）
        if first_stage is not None:
            notes.append("⚠ 基准段 %d 拿不到（未加载 / 读取失败 / 不在回看窗口 [%d, %d) 内）"
                         "⇒ 降级用窗口内最早的段 %d 当基准"
                         % (int(first_stage), lo, int(current_stage), base_k))
    base_sig = torch.cat(per_stage[base_k], dim=-1)

    # ③ 其余按「与基准的音色相似度」降序
    rest = []
    for k, chunks in per_stage.items():
        if k == base_k:
            continue
        sig = torch.cat(chunks, dim=-1)
        rest.append((timbre_similarity(sig, base_sig), k))
    rest.sort(key=lambda x: (-x[0], -x[1]))
    # 🔵 代理可区分性**观测**（2026-10-05 R6）：极差太小 ⇒ 这一步的排序**实际等于按段号**。
    #    默认**只报告、不改判据**（判据归属 本仓作者：以首次开口段为基准、按音色接近度补）；
    #    填 `sim_noise>0` 才真的切成「最近段优先」（近的比远的可信 —— 参考 = 即将续接的语境）。
    if len(rest) >= 2:
        _vals = [s for s, _k in rest]
        _spread = float(max(_vals) - min(_vals))
        if _spread < float(sim_noise):
            rest.sort(key=lambda x: -x[1])
            notes.append("⚠ `%s`：候选段相似度极差仅 %.4f（< `sim_noise` %.4f）⇒ 代理无区分力，"
                         "改按**最近段优先**补片" % (target, _spread, float(sim_noise)))
        elif _spread < SIM_REPORT_NOISE:
            notes.append("⚠ `%s`：候选段相似度极差仅 %.4f（< 报告阈值 %.2f）⇒ 本步排序**实际等于"
                         "按段号**（代理近饱和，见本文件模块 docstring 里的 R6）；"
                         "想改用「最近段优先」请填 `config.sim_noise=0.05`"
                         % (target, _spread, SIM_REPORT_NOISE))

    # ④ 装：基准在前，再按相似度降序补，直到预算用尽
    plan = [(base_k, "基准（首次开口段）")] + [(k, "相似度 %.3f" % s) for s, k in rest]
    picked: List[torch.Tensor] = []
    room = int(round(want * AUDIO_HZ))
    for k, why in plan:
        if room < 1:
            break
        take: List[torch.Tensor] = []
        _cut = False
        for chunk in reversed(per_stage[k]):          # 段内**从最后一句往前**挑（近的优先）
            _n = int(chunk.shape[-1])
            if _n <= room:
                # ✅ **整片**：两端都落在**真实间隙**上（不制造音节中间的切点）——
                #    这是 2026-10-05 的改动（旧写法无条件 `_slice_audio(..., room)` ⇒ 每片都砍头）。
                take.append(chunk)
                room -= _n
            elif room >= 1:
                # 预算不足 ⇒ 只能切。**取尾**（近的比远的可信），切点写进 notes（不静默）。
                take.append(chunk[..., _n - room:].clone())
                _cut = True
                room = 0
            else:
                break
            if room < 1:
                break
        if take:
            picked.extend(reversed(take))             # 段内回到**时间正序**
            notes.append("段 %d（%s）：取 %d 片%s"
                         % (k, why, len(take),
                            "（⚠ 末片**取尾**、切点落在音节中 —— 预算不足；"
                            "想避免请加大 `max_seconds` 或减少看几段）" if _cut else ""))
    if not picked:
        return None, notes + ["🔴 预算不足 ⇒ 返空"]
    merged = torch.cat(picked, dim=-1)                # 时间正序：基准段在前
    notes.append("✅ `%s` 累积完成：基准段 %d ｜ %d 片 / %d 步（%.2f s）"
                 % (target, base_k, len(picked), int(merged.shape[-1]),
                    int(merged.shape[-1]) / AUDIO_HZ))
    return merged, notes


def concat_blocks(blocks: Sequence[Tuple[str, torch.Tensor]]) -> torch.Tensor:
    """把「每人一段」按给定顺序拼成**一段**（用于并入桥那 1 个音频参考块）。

    🔴 为什么拼成一段而不是追加多块（2026-10-04 三臂实测）：追加的多块**没有 `<Audio j>` 标签**
       ⇒ 模型无法对应谁是谁 ⇒ 开/关两臂音频余弦只有 **0.0497**（打乱生成）。
    """
    if not blocks:
        raise ValueError("concat_blocks 收到空列表")
    return torch.cat([t for _w, t in blocks], dim=-1)


def compose_ref(pieces: Sequence[torch.Tensor],
                tail: Optional[torch.Tensor],
                *,
                max_steps: Optional[int] = None,
                pin_steps: int = 0,
                history_budget: Optional[int] = None) -> Tuple[torch.Tensor, Dict[str, Any]]:
    """把**历史素材**与**上一段音频尾窗**合成**一个**参考块 —— 修的是 **R2**（同源不变式）。

    ## 为什么必须这样做（读码确证，不是推断）

    `relay_core.py:4008-4012` 自己写着：

    > 0.6.1 声锚：钉住的音频前缀同样改用声锚尾窗（与 plan_relay 的 audio_ref **同源** ——
    > 两处读的都是"上段尾"，**只改一处会打架**）

    ⇒ 旧口径下 `audio_ref` 的**尾部**与**钉住前缀**是同一段音频。VA 只替换 `audio_ref`
      ⇒ 不变式被打破：硬约束（pin，钉进输出流的头）说"前面是 A 的尾音"，
      软条件（ref）说"这是 B/C/D 的音色" ⇒ **软条件的信息进不去，只剩扰动**
      （实测：开/关两臂音频余弦 0.2395，是"打乱"而不是"变差一点"）。

    ⇒ 正解 = **让参考以 pin 的来源结尾**：`[历史素材 …][上一段音频尾]`。
      顺序不能反（前沿的位置偏置研究：causal mask 偏向靠前、相对位置编码带来距离衰减、
      两头被读到而中间容易被跳过）⇒ **头部放身份素材、尾部放连续性来源，中间放次要素材**。

    ## 参数（每个都有出处）

      pieces           历史素材片，**按优先级降序**（基准段在前）—— 顺序即"丢了谁先丢最后一个"。
                       🔴 接受三种形态：`None` / **单个张量** / **张量序列**（显式分流，
                       不依赖真值性 —— 张量的 `bool()` 在 numel > 1 时**抛异常**）。
                       粒度 = 这一层的"片"：给**多片**才谈得上"丢整片不切半片"。
      tail             `plan.audio_ref["audio_latent"]`（桥自己那一段）—— **必须传入**，
                       否则参考与 pin 不同源 = 旧的坏口径（会写进 info.note 里报警）
      max_steps        本段**音频栅格**步数（硬上限）—— 传了它 ⇒ 下游**永不需要再截断**
      pin_steps        钉住前缀的音频步数（= `ceil(trim_frames / FPS * AUDIO_HZ)`）——
                       这是 tail 部分**必须保住的下限**（保住它 = 不变式成立）
      history_budget   想给历史素材的步数上限（= `max_total_seconds × AUDIO_HZ`）

    ## 返回

    ``(张量, info)``。info 的键：`kept` / `dropped` / `tail_steps` / `hist_steps` /
    `head_truncated` / `note`。**不返回 None** —— 只要 tail 或 pieces 有一边非空就一定有结果。

    ## 纪律（对应 audit 的假门清单）

    · **绝不切半片**：装不下就**整片丢**（从优先级最低的往后丢），切点永不落在音节中间。
      唯一例外 = tail 自己就超过 `max_steps` ⇒ 只能从头裁（**保住 tail 的终点** ⇒ 不变式仍成立）。
    · **tail 部分只保到下界就不再多给**：`tail_keep = clamp(max_steps − history_budget, pin_steps, len(tail))`
      ⇒ 历史素材有预算时 tail 会**让位**（音色的活由历史素材干），但永不低于 `pin_steps`。
      **没给 `history_budget` ⇒ 尾保持全长**（不擅自缩小；历史只用剩余空间）。
    · 🔵 **想让尾优先占满**（不让位）⇒ 调用方把 `pin_steps` 抬到 `len(tail)` 即可 ——
      `clamp` 里的 `max` 会把 `t_keep` 顶到 `len(tail)`。**声锚路径就是这么做的**
      （见 `h3_adapter.build_audio` 的 `_pin_eff`）：声锚是**用户给的**素材，不该被历史挤短。
      ⚠️ 若 `len(tail) > max_steps`，最终只能**从头裁**（保尾的终点），历史素材拿 0 额度。
    """
    info: Dict[str, Any] = {"kept": 0, "dropped": 0, "tail_steps": 0, "hist_steps": 0,
                            "head_truncated": 0, "tail_keep": 0, "note": ""}
    # 🔴 **`pieces` 允许三种形态**（2026-10-05 审核抓到的真 bug）：`None` / **单个张量** /
    #    **张量序列**。原写法 `(pieces or [])` 在传**张量**时会抛
    #    `RuntimeError: Boolean value of Tensor with more than one value is ambiguous`
    #    —— 而适配层最早就是直接传 `concat_blocks()` 的张量 ⇒ **真跑会崩**（夹具测不到，
    #    因为夹具全都传列表 ⇒ 典型的"假门"）。现在显式分流，不再依赖真值性。
    if pieces is None:
        seq: List[torch.Tensor] = []
    elif torch.is_tensor(pieces):
        seq = [pieces]
    else:
        seq = list(pieces)
    ok = [p for p in seq
          if torch.is_tensor(p) and int(p.numel()) > 0 and int(p.shape[-1]) > 0]
    t_len = 0
    if torch.is_tensor(tail) and int(tail.numel()) > 0:
        t_len = int(tail.shape[-1])
    if not ok and t_len == 0:
        raise ValueError("compose_ref：历史素材与上一段音频尾**都**为空 —— 没有东西可注入。")

    hard = 0 if max_steps is None else max(0, int(max_steps))
    _pin = max(0, int(pin_steps))
    _bud = None if history_budget is None else max(0, int(history_budget))

    # ① tail 部分留多长
    #    · **给了历史预算** ⇒ 尾**让位**：`clamp(max_steps − 预算, pin_steps, len(tail))`
    #      （音色的活交给历史素材干，但尾**永不低于 `pin_steps`** —— 那是不变式的下限）
    #    · **没给预算** ⇒ **不擅自缩小尾**（保持桥自己那一段全长，历史只用剩余空间）
    if hard > 0 and _bud is not None:
        t_keep = min(max(_pin, hard - _bud), t_len)
    else:
        t_keep = t_len
    t_keep = max(0, int(t_keep))
    info["tail_keep"] = int(t_keep)
    tail_part = tail[..., t_len - t_keep:].clone() if t_keep > 0 else None

    # ② 历史部分的可用额度（**不为负** —— 尾比上限还长时历史拿 0）
    room = max(0, hard - t_keep) if hard > 0 else _NO_LIMIT
    if _bud is not None:
        room = min(room, _bud)

    # ③ 装历史：**整片装**，装不下就停（后面的优先级更低 ⇒ 一并放弃）
    keep: List[torch.Tensor] = []
    for i, p in enumerate(ok):
        n = int(p.shape[-1])
        if n <= room:
            keep.append(p)
            room -= n
        else:
            info["dropped"] = len(ok) - i
            break

    parts = list(keep) + ([tail_part] if tail_part is not None else [])
    if not parts:
        # 只可能发生在 hard 太小（< 1 步）时 —— 不静默返回空张量
        raise ValueError(
            "compose_ref：预算 %s 步装不下任何东西（tail 要 %d 步、历史 %d 片）—— 请加大 `max_steps`。"
            % (hard, t_len, len(ok)))
    out = torch.cat(parts, dim=-1)
    total = int(out.shape[-1])
    _t_eff = int(t_keep)
    if hard > 0 and total > hard:
        # 只可能是 tail 自己就超上限（历史片已按预算装过）⇒ 从头裁，**保住 tail 的终点**。
        info["head_truncated"] = total - hard
        out = out[..., total - hard:]
        total = hard
        # 裁的是**头部** ⇒ 剩下的全在尾段里 ⇒ tail 的**有效**长度随之变小。
        # 🔴 必须这样算：否则 `hist_steps = total - t_keep` 会出**负数**（审核实测 -17）。
        _t_eff = min(_t_eff, total)
    info["tail_steps"] = int(_t_eff if tail_part is not None else 0)
    info["hist_steps"] = int(max(0, total - info["tail_steps"]))
    info["kept"] = len(keep)

    if tail_part is None:
        info["note"] = ("🔴 没给「上一段音频尾」⇒ 参考只含历史素材，"
                        "**与钉住前缀不再同源**（旧的坏口径，见 R2）")
    elif not keep:
        info["note"] = "参考 = 只有上一段音频尾（历史素材一片都没装下）"
    elif info["head_truncated"]:
        info["note"] = ("⚠ 上限 %d 步小于「上一段音频尾」本身（%d 步）⇒ 从头裁 %d 步（切点在音节中）"
                        % (hard, t_len, info["head_truncated"]))
    else:
        info["note"] = ("参考 = 历史 %d 片（%d 步）+ 上一段音频尾 %d 步（**尾部即 pin 的来源**）"
                        % (len(keep), info["hist_steps"], info["tail_steps"]))
    return out, info


def make_ref_block(audio_latent: torch.Tensor) -> dict:
    """包成宿主认的 `minimax_refs` 音频块（与官方节点 `ref_audios` 分支同形状）。"""
    return {"kind": "audio", "ref_audio_t": int(audio_latent.shape[-1]),
            "audio_latent": audio_latent}


def accumulate_all(order: Sequence[str],
                   stage_orders: Dict[int, Sequence[str]],
                   stage_audio: Dict[int, Any],
                   *,
                   current_stage: int,
                   first_stages: Optional[Dict[str, int]] = None,
                   stage_lines: Optional[Dict[int, list]] = None,
                   stage_chars: Optional[Dict[int, list]] = None,
                   strict: bool = DEFAULT_STRICT,
                   snap_s: float = DEFAULT_SNAP_S,
                   max_seconds: float = DEFAULT_MAX_S,
                   max_total_seconds: float = DEFAULT_MAX_TOTAL_S,
                   max_stages: int = DEFAULT_MAX_STAGES,
                   min_span_s: float = DEFAULT_MIN_SPAN_S,
                   gap_s: float = DEFAULT_GAP_S,
                   max_speakers: int = DEFAULT_MAX_SPEAKERS,
                   prior_split: bool = DEFAULT_PRIOR_SPLIT,
                   sim_noise: float = DEFAULT_SIM_NOISE) -> Tuple[List[Tuple[str, torch.Tensor]], List[str]]:
    """**本段每个说话人各累积一块**（默认只做前 `max_speakers` 个，现为 **1**）。

    🔵 **2026-10-05 默认值改了（12→8 s / 每人 → 只给 1 人 / 等分 → 不猜）**，三条都有实测依据：

    | 旧口径 | 新默认 | 依据 |
    |---|---|---|
    | 本段**每个人**各一块 | **只给缝上那个人**（`max_speakers=1`） | 本包的块**没有 `<Audio j>` 标签** ⇒ 一槽多身份 = 条件歧义。实测：追加多块余弦 **0.0497**、并入一块 **0.2395**，两轮都更糟。前沿给每个说话人各自的控制通道（Playmate2 Mask-CFG / AlignDiT 多模态 CFG），H3 没有这类旋钮 |
    | 判不准 ⇒ **按比例/等分**切 | 判不准 ⇒ **只给第一个有声片段** | 等分切**必然**把别人的音色算进来，且模型不报错。前沿：参考必须是**单一说话人的完整话语** |
    | 合计 **12 s** | 合计 **8 s** | 参考时长有收益拐点（XTTS：1→10 s 提升明显、20 s 后递减）；视频 DiT 的注意力汇会把长行吸收 ⇒ 变长 ≠ 影响力变大 |

    ⚠️ **本仓作者 2026-10-04 的「每人一块」是显式口径**，本次改动**逆了它**，理由 = 上述两轮实测。
    想放回旧行为：`config.max_speakers = N`（+ 需要盲切时再 `config.prior_split = true`）。

    顺序 = 本段**开口顺序**（缝上那个人排第一，他的位置最要紧）。
    预算：**每人** `max_seconds`，**合计** `max_total_seconds`（显存安全阀 —— 参考行随每一步采样）。

    ⚠️ **注入的块没有 `<Audio j>` 标签**（本包走 `append=True`，tokenize 已发生）⇒
       prompt **引不到**它们；模型只能靠音频内容 + prompt 的 `<Subject N>` 自己对应。**未验证**。
    """
    out: List[Tuple[str, torch.Tensor]] = []
    notes: List[str] = []
    total_s = 0.0
    seen: List[str] = []
    # 🔴 **只给前 `max_speakers` 个人**（默认 1 = 缝上接着说的那个人）。理由见
    #    `DEFAULT_MAX_SPEAKERS` 的注释：本包注入的块**没有 `<Audio j>` 标签** ⇒ 一槽多身份 = 条件歧义。
    _all = list(dict.fromkeys(order))
    _keep = max(1, min(int(max_speakers), len(_all)))
    _uniq = _all[:_keep]
    if len(_all) > len(_uniq):
        notes.append("⚠ 本段 %d 人，而 `max_speakers=%d` ⇒ 只给**缝上接着说的那个人**（`%s`）"
                     "累积参考；其余人（%s）**不给块** —— 本包注入的块没有 `<Audio j>` 标签，"
                     "一个槽装多个身份 = 条件歧义（两轮实测：追加多块余弦 0.0497 / 并入一块 0.2395，"
                     "**都更糟**）。其余人请走**官方 `ref_audios` 的 3 个带标签槽**（`<Audio j>`）；"
                     "确认要旧行为请显式填 `config.max_speakers=%d`（**未验证**）"
                     % (len(_all), _keep, _uniq[0], "→".join(_all[_keep:]), len(_all)))
    # 🔴 合计预算**按人数均分**（2026-10-04 实测踩到：3 人时前两人吃满预算 ⇒ 最后一人被饿死）。
    #    "每人一块、做事情做到底" 要求**人人有份** ⇒ 先算人均额度，再按开口顺序发。
    _per = min(float(max_seconds), float(max_total_seconds) / max(1, len(_uniq)))
    for sp in _uniq:
        if sp in seen:
            continue
        seen.append(sp)
        room = min(_per, float(max_total_seconds) - total_s)
        if room <= 1e-6:
            notes.append("⚠ 合计预算 %.1f s 已用尽 ⇒ `%s` 不再累积（要更多请调 `max_total_seconds`）"
                         % (float(max_total_seconds), sp))
            break
        got, sub = accumulate_for(
            sp, stage_orders, stage_audio, current_stage=current_stage,
            first_stage=(first_stages or {}).get(sp),
            stage_lines=stage_lines,
            stage_chars=stage_chars,
            strict=strict,
            snap_s=snap_s,
            max_seconds=room,
            max_stages=max_stages, min_span_s=min_span_s, gap_s=gap_s,
            prior_split=prior_split, sim_noise=sim_noise)
        notes.extend("`%s`：" % sp + n for n in sub)
        if got is None:
            notes.append("⚠ `%s` 在历史段里收不到素材 ⇒ **不给块**（不硬凑）" % sp)
            continue
        out.append((sp, got))
        total_s += int(got.shape[-1]) / AUDIO_HZ
    if out:
        notes.append("✅ 共 %d 人 / %d 块 / 合计 %.2f s（人均额度 %.2f s = min(%.1f, %.1f/%d)）"
                     % (len(out), len(out), total_s, _per, float(max_seconds),
                        float(max_total_seconds), len(_uniq)))
    return out, notes


# --------------------------------------------------------------------- 自检
def _selftest() -> int:
    """零 GPU 自检：切分判据 / 归属门控 / 累积顺序 / 边界。"""
    fails: List[str] = []
    ok = 0

    def chk(name, cond, detail=""):
        nonlocal ok
        if cond:
            ok += 1
            print("  [OK] %s %s" % (name, detail))
        else:
            fails.append("%s %s" % (name, detail))
            print("  [!!] %s %s" % (name, detail))

    def mk(spans_spec, total_s=10.0, amp=0.05, pat=(0.02, 0.06)):
        """造一段音频 latent：`spans_spec` = [(起, 止), …] 有声，其余**恰好为零**。

        🔴 有声段用 **幅度逐格交替**（默认 0.02 / 0.06）而不是"噪声 × 缓慢斜坡" ——
           本判据是**逐格 RMS 的一阶差分**：斜坡的增量太小、噪声的逐格起伏又随机，
           两者都会让某些有声格落在阈值下 ⇒ **把一句切碎**（首版夹具实测踩过：
           1 段被切成 3 段，起点还漂了 0.32 s）。交替幅度让每个有声格的差分
           都是同一个非零值 ⇒ 切分结果是确定的。

        `pat` 换一组值可以造出**不同的"音色"**（相对对比度不同）——
           ⚠️ 只改 `amp` 是**无效**的：音色摘要做了 L2 归一化 ⇒ 整体幅度被消掉，
           两个只差音量的块相似度仍是 1.0（这正是"响度不算音色"的体现）。
        """
        n = int(total_s * AUDIO_HZ)
        x = torch.zeros(1, 1, 2, n)
        p = torch.tensor(list(pat)) * (amp / 0.05)
        for t0, t1 in spans_spec:
            i0, i1 = int(t0 * AUDIO_HZ), int(t1 * AUDIO_HZ)
            m = int(i1 - i0)
            if m <= 0:
                continue
            x[..., i0:i1] = p.repeat(m // 2 + 1)[:m].view(1, 1, 1, m)
        return x

    sp = voiced_spans(mk([(1.0, 2.0), (5.0, 6.5)]))
    chk("切分：两段有声 ⇒ 两个片段", len(sp) == 2, "%s" % [(round(a, 2), round(b, 2)) for a, b in sp])
    chk("切分：时长近似（1.0 / 1.5 s）",
        len(sp) == 2 and abs((sp[0][1] - sp[0][0]) - 1.0) < 0.15
        and abs((sp[1][1] - sp[1][0]) - 1.5) < 0.15, "")
    chk("切分：全静音 ⇒ 无片段", voiced_spans(torch.zeros(1, 1, 2, 400)) == [], "")
    chk("切分：含 NaN ⇒ 无片段（不静默当安静）",
        voiced_spans(torch.tensor([[[[float("nan")] * 4]]])) == [], "")
    # 🔴 判据必须是**差分**而不是 RMS 绝对值：真实路径拿到的是**归一化**的 VAE 音频 latent
    #    ⇒ 静默段的 RMS 与语音段**同量级**。这里给静默段加**非零底噪**复现该形态：
    #    绝对值判据会把整段判成"有声"（1 个 0~4 s 的大片段），差分判据仍只认出中间那句。
    _nz = mk([(1.0, 2.0)], total_s=4.0)
    _nz[..., :40] = 0.05
    _nz[..., 120:] = 0.05
    _spnz = voiced_spans(_nz)
    chk("切分：静默段有**非零底噪**（归一化 latent 的真实形态）⇒ 仍只认出中间那句",
        len(_spnz) == 1 and 0.9 <= _spnz[0][0] <= 1.1 and 1.9 <= _spnz[0][1] <= 2.1,
        "%s" % [(round(a, 2), round(b, 2)) for a, b in _spnz])

    t1, why1 = assign_spans([(0.0, 1.0)], ["A"])
    chk("归属：单说话人段 ⇒ 整段归他", list(t1) == ["A"] and len(t1["A"]) == 1, why1)
    t2, why2 = assign_spans([(0.0, 1.0), (2.0, 3.0)], ["A", "B"])
    chk("归属：2 片 2 人 ⇒ 按顺序 1:1", t2 == {"A": [(0.0, 1.0)], "B": [(2.0, 3.0)]}, why2)
    t3, why3 = assign_spans([(0.0, 1.0)], ["A", "B"])
    chk("归属：1 片 2 人 ⇒ **返空不猜**", t3 == {} and "不猜" in why3, why3)
    t4, why4 = assign_spans([(0.0, 1.0), (2.0, 3.0), (4.0, 5.0)], ["A", "B"])
    chk("归属：3 片 2 人 ⇒ **返空不猜**", t4 == {}, why4)
    # 🔴 2026-10-04 审核抓到的真 bug：台账里**同一人连说两句** ⇒ 名字出现两次。
    t5, why5 = assign_spans([(0.0, 1.0), (2.0, 3.0)], ["A", "A"])
    chk("归属：**同一人连说两句** ⇒ 两句都归他（旧写法第二句把第一句覆盖掉）",
        t5 == {"A": [(0.0, 1.0), (2.0, 3.0)]}, why5)
    t6, why6 = assign_spans([(0.0, 1.0)], ["A", "A"])
    chk("归属：同一人两句被并成一片 ⇒ 仍归他（不返空）", t6 == {"A": [(0.0, 1.0)]}, why6)
    t7, why7 = assign_spans([(0.0, 1.0), (2.0, 3.0), (4.0, 5.0)], ["A", "B", "A"])
    chk("归属：A→B→A ⇒ A 两句 / B 一句（按名字累加）",
        t7 == {"A": [(0.0, 1.0), (4.0, 5.0)], "B": [(2.0, 3.0)]}, why7)

    audio = {0: mk([(0.0, 1.0)], total_s=5.0), 1: mk([(0.0, 1.0)], total_s=5.0)}
    got, notes = accumulate_for("A", {0: ["A"], 1: ["A"]}, audio, current_stage=2, max_seconds=6.0)
    chk("累积：同人跨 2 段 ⇒ 拼出音频", got is not None and int(got.shape[-1]) > 60,
        "步数 %s" % (None if got is None else int(got.shape[-1])))
    got2, _ = accumulate_for("A", {1: ["A"]}, audio, current_stage=2, max_seconds=0.5)
    chk("累积：上限生效（≤ 0.5 s = 20 步）",
        got2 is not None and int(got2.shape[-1]) <= 20, "步数 %s" % (None if got2 is None else int(got2.shape[-1])))
    got3, notes3 = accumulate_for("Z", {0: ["A"], 1: ["A"]}, audio, current_stage=2, max_seconds=6.0)
    chk("累积：目标从未开口 ⇒ **返空**", got3 is None and any("返空" in n for n in notes3), "")
    got4, notes4 = accumulate_for("A", {}, audio, current_stage=2, max_seconds=6.0)
    chk("累积：无台账 ⇒ **返空**", got4 is None, "")
    got5, _ = accumulate_for("A", {0: ["A"], 1: ["A"]}, audio, current_stage=2,
                             max_seconds=6.0, max_stages=1)
    chk("累积：max_stages=1 ⇒ 只看最近 1 段", got5 is not None and int(got5.shape[-1]) <= 60,
        "步数 %s" % (None if got5 is None else int(got5.shape[-1])))
    blk = make_ref_block(torch.zeros(1, 1, 2, 37))
    chk("块形状与官方一致", blk["kind"] == "audio" and blk["ref_audio_t"] == 37, "")

    # —— 每人一块（本仓作者 2026-10-04：「做事情做到底，为什么只取 1 个？」）——
    # 🔴 **2026-10-05：这条口径**逆了 本仓作者 的指令**，理由 = 两轮实测（追加多块余弦 0.0497、
    #    并入一块 0.2395，都更糟）+ 前沿理论（一个无标签槽只承载一个身份）。
    #    ⇒ 现在**默认 `max_speakers=1`**；下面这组改成**显式传 `max_speakers=N` 才走旧路**，
    #      并单加一条「默认只给缝上那个人」的断言（正向）与一条「旧行为可显式打开」的断言（反向）。
    _orders = {0: ["A", "B"], 1: ["B", "A"]}
    _aud = {0: mk([(0.0, 1.0), (2.0, 3.0)], total_s=5.0),
            1: mk([(0.0, 1.0), (2.0, 3.0)], total_s=5.0)}
    _blk_d, _an_d = accumulate_all(["A", "B"], _orders, _aud, current_stage=2, max_seconds=6.0)
    chk("默认（max_speakers=1）：本段 2 人 ⇒ **只给缝上那个人 1 块** + notes 点明其余人不给",
        len(_blk_d) == 1 and _blk_d[0][0] == "A"
        and any("只给**缝上接着说的那个人**" in n for n in _an_d),
        "%d 块 %s" % (len(_blk_d), [b[0] for b in _blk_d]))
    _blocks, _ = accumulate_all(["A", "B"], _orders, _aud, current_stage=2, max_seconds=6.0,
                                max_speakers=2)          # ← 显式 opt-in（旧口径）
    chk("opt-in（max_speakers=2）：本段 2 人 ⇒ **2 块**（旧口径可显式打开）",
        len(_blocks) == 2 and [b[0] for b in _blocks] == ["A", "B"], "")
    chk("每人一块：块里是**那个人自己**的片段（每段 1 句 ≈ 40 步 × 2 段）",
        79 <= int(_blocks[0][1].shape[-1]) <= 82, "步数 %d" % int(_blocks[0][1].shape[-1]))
    _blocks2, _an2 = accumulate_all(["A", "B"], _orders, _aud, current_stage=2,
                                    max_seconds=6.0, max_total_seconds=1.0, max_speakers=2)
    chk("每人一块：**合计预算按人数均分**（1.0 s / 2 人 ⇒ 每人 0.5 s = 20 步，**都有份**）",
        len(_blocks2) == 2 and all(int(b[1].shape[-1]) <= 21 for b in _blocks2)
        and any("人均额度" in n for n in _an2),
        "%s" % [int(b[1].shape[-1]) for b in _blocks2])
    _blocks3, _an3 = accumulate_all(["C"], _orders, _aud, current_stage=2, max_seconds=6.0)
    chk("每人一块：某人历史里没说过 ⇒ **不给块**（不硬凑）",
        _blocks3 == [] and any("收不到素材" in n for n in _an3), "")

    # —— 基准 = 该角色**首次开口**的段（本仓作者 2026-10-04：抗漂移）——
    # 段 0 的 A 与段 1 的 A 用**不同幅度**（⇒ 音色摘要不同）；预算只够 1 句
    # ⇒ 必须选**基准段 0**，而不是"最近"的段 1。
    _o = {0: ["A"], 1: ["A"]}
    _a = {0: mk([(0.0, 1.0)], total_s=5.0, amp=0.05),
          1: mk([(0.0, 1.0)], total_s=5.0, amp=0.02)}
    _b, _n = accumulate_all(["A"], _o, _a, current_stage=2, first_stages={"A": 0},
                            max_seconds=1.0)   # 只够 1 片 ⇒ 判别"取了谁"
    # ⚠️ 必须**比内容**，不能比 notes 文案 —— 只比文案时"基准改最近段"这个注入**测不出**（假绿）。
    #    夹具里基准段幅度 0.06、非基准段 0.024 ⇒ 峰值可直接分辨。
    _pk = float(_b[0][1].abs().max()) if _b else -1.0
    chk("基准：预算只够 1 句 ⇒ 取的是**基准段的内容**（峰值 0.06 而非 0.024）",
        _b is not None and abs(_pk - 0.06) < 0.005 and any("基准（首次开口段）" in n for n in _n),
        "峰值 %.3f" % _pk)
    _b2, _n2 = accumulate_all(["A"], _o, _a, current_stage=2, first_stages={"A": 0},
                              max_seconds=6.0)
    chk("基准：预算够 ⇒ 基准 + 后续段都进来（2 片）",
        _b2 and sum(1 for n in _n2 if "：取" in n) == 2,
        "%d 片" % sum(1 for n in _n2 if "：取" in n))
    # ④ 「取尾不取头」：片段比预算长 ⇒ 必须留**尾部**（旧 E1 就是死在"取段头"上）
    _x4 = torch.zeros(1, 1, 2, 200)
    _p4 = torch.tensor([0.02, 0.06]).repeat(40)[:80] * (0.4 + torch.arange(80) / 80.0)
    _x4[..., 0:80] = _p4.view(1, 1, 1, 80)      # 幅度**递增** ⇒ 头尾不同（否则周期 2 的图案分不出取头/取尾）
    _o4, _a4 = {0: ["A"]}, {0: _x4}
    _b4, _n4 = accumulate_all(["A"], _o4, _a4, current_stage=1,
                              first_stages={"A": 0}, max_seconds=1.0)   # 预算 40 步
    _t0_4, _t1_4 = voiced_spans(_a4[0])[0]
    _end4 = int(math.ceil(_t1_4 * AUDIO_HZ))
    chk("取尾：预算 1 s < 片段 2 s ⇒ 留**尾部**（丢掉开头；取头会红）",
        _b4 is not None and int(_b4[0][1].shape[-1]) == 40
        and torch.allclose(_b4[0][1], _a4[0][..., _end4 - 40:_end4]),
        "步数 %s ｜ 期望尾片 %s" % (None if _b4 is None else int(_b4[0][1].shape[-1]),
                                    tuple(_a4[0][..., _end4 - 40:_end4].shape)))
    _b3, _n3 = accumulate_all(["A"], _o, _a, current_stage=2, first_stages={"A": 9},
                              max_seconds=6.0)
    chk("基准：基准段拿不到 ⇒ 降级用窗口内最早段 + **出声说明**",
        _b3 is not None and any("拿不到" in n for n in _n3), "")

    # —— 音色相似度：本身要可判别 + **真的参与排序** ——
    # ⚠️ 只改幅度是**无效**的（摘要 L2 归一化 ⇒ 响度被消掉，相似度恒 1.0）。
    #    要造"不同音色"必须改**相对对比度**：pat 换成 (0.01, 0.07)。
    _tA = mk([(0.0, 1.0)], total_s=2.0, pat=(0.02, 0.06))
    _tB = mk([(0.0, 1.0)], total_s=2.0, pat=(0.01, 0.07))
    _same = timbre_similarity(_tA, _tA)
    _diff = timbre_similarity(_tA, _tB)
    chk("相似度：同一块 = 1.0；**对比度不同**的块 < 1.0",
        abs(_same - 1.0) < 1e-9 and _diff < _same - 1e-6, "同 %.4f ｜ 异 %.4f" % (_same, _diff))
    chk("相似度：**只改音量** ⇒ 恒 1.0（响度不算音色 —— 这是有意为之，不是 bug）",
        abs(timbre_similarity(_tA, _tA * 3.0) - 1.0) < 1e-9, "")
    # 段 0 = 基准；段 1 与基准**同对比度**（⇒ 更像）；段 2 对比度不同（⇒ 不像）
    # 预算只够「基准 + 1 段」⇒ 必须选**更像**的段 1。
    _o5 = {0: ["A"], 1: ["A"], 2: ["A"]}
    _a5 = {0: mk([(0.0, 1.0)], total_s=5.0, pat=(0.02, 0.06)),
           1: mk([(0.0, 1.0)], total_s=5.0, pat=(0.02, 0.06)),
           2: mk([(0.0, 1.0)], total_s=5.0, pat=(0.01, 0.07))}
    _b5, _n5 = accumulate_all(["A"], _o5, _a5, current_stage=3,
                              first_stages={"A": 0}, max_seconds=2.0)
    _j5 = "\n".join(_n5)
    chk("相似度：预算只够再加 1 段 ⇒ 选**更像基准**的段 1（不是段 2）",
        "段 1（相似度" in _j5 and "段 2（相似度" not in _j5, _j5.replace("\n", " ｜ ")[:110])

    cfg, stages, _lines, _chars, cnotes = parse_config({"stages": {"1": "甲", "2": ["乙", "甲"]},
                                                "bogus": 1})
    chk("配置：字符串自动包成列表", stages == {"1": ["甲"], "2": ["乙", "甲"]}, "")
    chk("配置：未知键 ⇒ 出声警告（不 raise）", any("bogus" in n for n in cnotes), "")
    chk("配置：默认值填充 + 钳制", cfg["max_seconds"] == DEFAULT_MAX_S
        and parse_config({"config": {"max_seconds": 999}, "stages": {"1": ["A"]}})[0]["max_seconds"] == 60.0, "")

    # —— 档 2：台账给**逐句时间** ⇒ 直接用，不做任何猜测 ——
    _cfg2, _ord2, _lin2, _ch2, _n2 = parse_config({"stages": {"1": [
        {"who": "A", "t0": 1.0, "t1": 2.0}, {"who": "B", "t0": 3.0, "t1": 4.0}]}})
    chk("档2：顺序与逐句时间都解出来",
        _ord2 == {"1": ["A", "B"]} and _lin2 == {"1": [(1.0, 2.0), (3.0, 4.0)]}, "")
    _cfg3, _ord3, _lin3, _ch3, _n3 = parse_config({"stages": {"1": [
        {"who": "A", "t0": 1.0, "t1": 2.0}, {"who": "B"}]}})
    chk("档2：**缺一句时间 ⇒ 整段退回档 1**（不半用）", _lin3 == {"1": []} and _ord3 == {"1": ["A", "B"]}, "")
    # 端到端：档 2 下即使能量切分完全失败，也能取到正确的片段
    _o6, _a6 = {0: ["A", "B"]}, {0: mk([(0.0, 1.0), (2.0, 3.0)], total_s=5.0)}
    _b6, _n6 = accumulate_all(["A", "B"], _o6, _a6, current_stage=1,
                              first_stages={"A": 0, "B": 0},
                              stage_lines={0: [("A", 0.0, 1.0), ("B", 2.0, 3.0)]},
                              max_seconds=6.0, max_speakers=2)
    chk("档2：逐句时间 ⇒ 每人各拿自己那句（A≈40 步 / B≈40 步）",
        len(_b6) == 2 and 38 <= int(_b6[0][1].shape[-1]) <= 42
        and 38 <= int(_b6[1][1].shape[-1]) <= 42,
        "%s" % [int(x[1].shape[-1]) for x in _b6])

    # 🔴 档 3 必须**真的被走**：给一段"能量切不开"的音频（1 片）⇒ 只有逐句时间能救。
    #    关掉档 3 时会落到兜底（等分）⇒ 结果不同 ⇒ 这条能判别。
    _o9, _a9 = {0: ["A", "B"]}, {0: mk([(0.5, 9.5)], total_s=10.0)}
    _b9, _n9 = accumulate_all(["A", "B"], _o9, _a9, current_stage=1,
                              first_stages={"A": 0, "B": 0},
                              stage_lines={0: [("A", 0.5, 2.0), ("B", 6.0, 9.5)]},
                              max_seconds=6.0, max_speakers=2)
    chk("档3：能量切不开时**按逐句时间**取（A = 1.5 s = 60 步，不是兜底等分）",
        len(_b9) == 2 and 55 <= int(_b9[0][1].shape[-1]) <= 62,
        "%s" % [int(x[1].shape[-1]) for x in _b9])
    _cfg4, _o4b, _l4, _ch4, _n4b = parse_config({"stages": {"1": [
        {"who": "A", "chars": 7}, {"who": "B", "chars": 10}]}})
    chk("配置：档2 的 `chars` 被解析出来（`stage_chars`）", _ch4 == {"1": [7.0, 10.0]},
        "%s" % _ch4)
    # 🔴 端到端：`stage_chars` 必须**从台账透传**到兜底档（不然字数先验形同虚设）
    _o7b, _a7b = {0: ["A", "B", "C"]}, {0: mk([(0.5, 9.5)], total_s=10.0)}   # 9 s 有声
    _b10, _n10 = accumulate_all(["A", "B", "C"], _o7b, _a7b, current_stage=1,
                                first_stages={"A": 0, "B": 0, "C": 0},
                                stage_chars={0: [7.0, 10.0, 10.0]}, max_seconds=6.0,
                                max_speakers=3)
    chk("端到端：`stage_chars` 透传到兜底档（A ≈ 7/27 × 9 s ≈ 2.33 s = 93 步）",
        len(_b10) == 3 and 88 <= int(_b10[0][1].shape[-1]) <= 98,
        "%s" % [int(x[1].shape[-1]) for x in _b10])
    # 🔴 **逐句 `chars` 必须真的被采纳**（端到端 —— 门是 `len(order)` 而不是 `len(set(order))`）：
    #    `order` 有 **3 句**但只有 **2 个人** ⇒ 旧门（按人数 2 判）会**不采纳** ⇒ A 一片都拿不到。
    #    A 的额度 6 s = 240 步 ⇒ **两段合起来**才是 240（只拿一段会是 120）⇒ 能判别"两段都进来"。
    _o8, _a8 = {0: ["A", "B", "A"]}, {0: mk([(0.5, 9.5)], total_s=10.0)}
    _b8, _n8 = accumulate_all(["A"], _o8, _a8, current_stage=1, first_stages={"A": 0},
                              stage_chars={0: [3.0, 3.0, 3.0]}, max_seconds=6.0)
    chk("端到端：**逐句 `chars` 真的被采纳**（3 句 2 人 ⇒ A 拿两段共 240 步；按人数判则 0 块）",
        len(_b8) == 1 and 238 <= int(_b8[0][1].shape[-1]) <= 242
        and any("逐句字数" in n for n in _n8),
        "%s 步" % (None if not _b8 else int(_b8[0][1].shape[-1])))

    # —— 兜底档：判不准也**必须给结果**（本仓作者：开源素材千奇百怪，要智能兼容 + 兜底）——
    _big = [(0.5, 9.5)]                       # 一整块有声（能量判据**切不开**）
    _t, _w = assign_spans(_big, ["A", "B", "C"])
    chk("兜底前的门：1 片 3 人 ⇒ assign_spans 返空（不猜）", _t == {}, _w)
    _t2, _w2 = assign_by_prior(_big, ["A", "B", "C"])
    chk("兜底档：1 片 3 人 ⇒ **仍给出 3 段**（功能完整性优先）+ 标明是估计",
        sorted(_t2) == ["A", "B", "C"] and "比例" in _w2,
        " ".join("%s=%.2f-%.2f" % (k, v[0][0], v[0][1]) for k, v in sorted(_t2.items())))
    _t3, _w3 = assign_by_prior(_big, ["A", "B", "C"], chars=[7.0, 10.0, 10.0])
    _mid = _t3["A"][0][1]
    chk("兜底档：**字数先验**生效（A 只占 7/27 ⇒ 分界 < 1/3 处）",
        abs(_mid - (0.5 + 9.0 * 7.0 / 27.0)) < 0.2, "A 的右界 %.2f" % _mid)
    # 等分切点 = (0.5+4.3)/2 = 2.40；最近间隙中点 = (1.4+3.0)/2 = 2.20（差 0.2 < snap 0.4）
    # ⇒ 吸附后应为 **2.20**，而不是 2.40（不吸附就会是 2.40 ⇒ 这条能判别）。
    _t4, _w4 = assign_by_prior([(0.5, 1.0), (1.2, 1.4), (3.0, 4.3)], ["A", "B"])
    chk("兜底档：切点**吸附**到能量间隙（2.40 → 2.20）",
        abs(_t4["A"][0][1] - 2.20) < 0.01, "A 的右界 %.2f（不吸附会是 2.40）" % _t4["A"][0][1])
    chk("兜底档：单说话人段 ⇒ 整段归他", assign_by_prior(_big, ["A"])[0] == {"A": [(0.5, 9.5)]}, "")
    # 🔴 **逐句语义**（2026-10-05）：`order` 的一个元素 = 提示词里的**一个 `<d>` 句**。
    #    同一人说两句 ⇒ 他出现两次 ⇒ 拿**两段**（旧写法按 `set(order)` 去重 ⇒ 并成一段，
    #    等于把他两句之间的停顿、以及夹在中间别人的话都算成他的时长）。
    _tp, _wp = assign_by_prior([(0.0, 9.0)], ["A", "B", "A"], chars=[3.0, 3.0, 3.0])
    chk("兜底档：**逐句** —— 同一人说两句 ⇒ 拿**两段**（不是合并成一段）",
        len(_tp.get("A") or []) == 2 and len(_tp.get("B") or []) == 1
        and _tp["A"][0] != _tp["A"][1], str(_tp))
    chk("兜底档：两段的**位置与顺序**正确（A₁ 止 ≤ B 起 ≤ B 止 ≤ A₂ 起）",
        _tp["A"][0][1] <= _tp["B"][0][0] + 1e-9 and _tp["B"][0][1] <= _tp["A"][1][0] + 1e-9,
        str(_tp))
    _tq, _wq = assign_by_prior([(0.0, 9.0)], ["A", "B", "A"], chars=[6.0, 3.0])   # 长度 ≠ 句数
    chk("兜底档：`chars` 长度 ≠ **句数** ⇒ 退回等分并在说明里讲明（不半用）",
        "等分" in _wq and abs(_tq["A"][0][1] - 3.0) < 1e-9, "%s ｜ %s" % (_wq, _tq))
    # 端到端：没有逐句时间也**不能返空**
    _o7, _a7 = {0: ["A", "B", "C"]}, {0: mk([(0.5, 9.5)], total_s=10.0)}
    # ✅ **默认口径（2026-10-05 改）**：判不准时**绝不按等分切**。两条子情形各有断言。
    #  ① **整段只切出 1 个有声片段**（能量判据分不开）⇒ 那一片**可能混着所有人** ⇒ **一片都不给**。
    #     （🔴 这是本轮审核抓到的真 bug 的回归锁：首版 `assign_first_line` 会把整段当"第一片"。）
    _b7, _n7 = accumulate_all(["A", "B", "C"], _o7, _a7, current_stage=1,
                              first_stages={"A": 0, "B": 0, "C": 0},
                              max_seconds=6.0, max_speakers=3)
    _j7 = "\n".join(_n7)
    chk("默认兜底：**1 片 3 人**（分不开）⇒ **一片都不给** + 出声说明为什么",
        _b7 == [] and "整段只切出 **1 个**有声片段" in _j7
        and "等分切会把别人的音色算给他" in _j7,
        "%d 块" % len(_b7))
    #  ② **切出了 ≥2 片**（说明真的分开了）⇒ 只有**缝上那个人**拿第一片（同一说话人）。
    _o7c, _a7c = {0: ["A", "B", "C"]}, {0: mk([(0.5, 2.0), (3.0, 4.5)], total_s=10.0)}
    _b7c, _n7c = accumulate_all(["A", "B", "C"], _o7c, _a7c, current_stage=1,
                                first_stages={"A": 0, "B": 0, "C": 0},
                                max_seconds=6.0, max_speakers=3)
    _j7c = "\n".join(_n7c)
    chk("默认兜底：**2 片 3 人** ⇒ **只有缝上那个人**拿第一片（A / ≈60 步），其余人返空",
        len(_b7c) == 1 and _b7c[0][0] == "A" and 55 <= int(_b7c[0][1].shape[-1]) <= 65
        and "第一个有声片段" in _j7c,
        "%d 块 %s" % (len(_b7c), [x[0] for x in _b7c]))
    # 🔴 **反向断言**：旧口径（等分/比例切）**可显式打开** ⇒ 断言它没被删掉，只是不再是默认。
    _b7b, _n7b = accumulate_all(["A", "B", "C"], _o7, _a7, current_stage=1,
                                first_stages={"A": 0, "B": 0, "C": 0}, max_seconds=6.0,
                                max_speakers=3, prior_split=True)
    chk("opt-in 兜底：`prior_split=True` ⇒ 回到旧口径（1 片 3 人也能切出 3 块）",
        len(_b7b) == 3 and all(int(x[1].shape[-1]) > 0 for x in _b7b),
        "%d 块" % len(_b7b))
    # 判据不能靠文案：默认与 opt-in 的**块数**必须真的不同（注入"删掉 opt-in 分支"会红）
    chk("兜底：默认 0 块 vs opt-in 3 块 —— 两条路**结果真的不同**（不是文案差异）",
        len(_b7) == 0 and len(_b7b) == 3, "默认 %d / opt-in %d" % (len(_b7), len(_b7b)))
    # strict=True 时保持 fail-closed（可选）
    _b8, _n8 = accumulate_all(["A", "B", "C"], _o7, _a7, current_stage=1,
                              first_stages={"A": 0, "B": 0, "C": 0},
                              max_seconds=6.0, strict=True)
    chk("strict=True ⇒ 判不准就**返空**（fail-closed 仍是可选项）", _b8 == [], "")
    # 🔴 拼成一段（本仓作者 选 A：并入桥那 1 个块，**不追加新块**）
    _m = concat_blocks([("A", torch.zeros(1, 1, 2, 40)), ("B", torch.ones(1, 1, 2, 60))])
    chk("concat：按顺序拼成一段（40 + 60 = 100 步）", int(_m.shape[-1]) == 100, "")
    chk("concat：**顺序即给定顺序**（前 40 步是 A 的零、后 60 步是 B 的一）",
        float(_m[..., :40].abs().max()) == 0.0 and float(_m[..., 40:].min()) == 1.0, "")
    try:
        concat_blocks([])
        chk("concat：空列表 ⇒ raise", False, "")
    except ValueError:
        chk("concat：空列表 ⇒ raise", True, "")

    # ========================================================================
    # 2026-10-05 · 前沿理论赋能（每条的动机就写在它自己的注释里，不引外部文档）
    # ========================================================================

    # —— ① 默认兜底 = 「只给缝上那个人的第一片」（R1 的修法）——
    _fl, _flw = assign_first_line([(0.5, 2.0), (5.0, 6.0)], ["A", "B"], "A")
    chk("兜底：缝上那个人（order[0]）⇒ 拿**第一个有声片段**",
        _fl == {"A": [(0.5, 2.0)]} and "第一个有声片段" in _flw, _flw)
    _fl2, _fl2w = assign_first_line([(0.5, 2.0), (5.0, 6.0)], ["A", "B"], "B")
    chk("兜底：**不是** order[0] 的人 ⇒ 返空（位置无从判断，不猜）",
        _fl2 == {} and "不是本段第一个开口的人" in _fl2w, _fl2w)
    chk("兜底：空片段 ⇒ 返空", assign_first_line([], ["A"], "A")[0] == {}, "")

    # —— ② **同源不变式**：参考必须以「上一段音频尾」结尾（R2 的正解）——
    _hist = torch.ones(1, 1, 2, 120)
    _tail = torch.ones(1, 1, 2, 200) * 7.0
    _tail[..., :150] = 1.0            # 头 150 ≠ 尾 50 ⇒ 能判别"取了哪一段"
    _ref, _ci = compose_ref([_hist], _tail, max_steps=200, pin_steps=40, history_budget=120)
    _tks = int(_ci["tail_steps"])
    chk("同源：参考的**尾部逐位 == 音频尾的尾部**（不变式成立）",
        _tks > 0 and torch.allclose(_ref[..., -_tks:], _tail[..., -_tks:]),
        "参考 %d 步 / 尾 %d 步" % (int(_ref.shape[-1]), _tks))
    chk("同源：尾部长**不低于 pin 步数**（pin 的来源必须保住）",
        _tks >= 40, "尾 %d 步（pin 要 40）" % _tks)
    chk("同源：总长 ≤ `max_steps` ⇒ 下游**永不**需要再截断（= R3 的修法）",
        int(_ref.shape[-1]) <= 200 and _ci["head_truncated"] == 0,
        "参考 %d 步" % int(_ref.shape[-1]))
    chk("同源：装得下 ⇒ 一片不丢 + note 点明尾部是 pin 的来源",
        _ci["kept"] == 1 and _ci["dropped"] == 0 and "pin 的来源" in _ci["note"], _ci["note"])
    _ref2, _c2 = compose_ref([torch.ones(1, 1, 2, 100), torch.zeros(1, 1, 2, 100)],
                             _tail[..., :80], max_steps=180, pin_steps=40)
    chk("同源：装不下 ⇒ **整片丢弃**（第 1 片整片进来、第 2 片整片丢；没有任何半片）",
        _c2["kept"] == 1 and _c2["dropped"] == 1 and int(_ref2.shape[-1]) == 180
        and torch.allclose(_ref2[..., :100], torch.ones(1, 1, 2, 100)),
        "kept=%d dropped=%d ｜ %d 步" % (_c2["kept"], _c2["dropped"], int(_ref2.shape[-1])))
    _ref3, _c3b = compose_ref([], _tail, max_steps=90, pin_steps=40)
    chk("同源：音频尾自己超上限 ⇒ 从头裁，**但终点保住**（尾部逐位 == 原尾的尾部）",
        int(_ref3.shape[-1]) == 90 and _c3b["head_truncated"] == 110
        and torch.allclose(_ref3[..., -50:], _tail[..., -50:]), str(_c3b))
    chk("同源：`tail_steps` / `hist_steps` **自洽且非负**（两者之和 == 成品长度）",
        _c3b["tail_steps"] >= 0 and _c3b["hist_steps"] >= 0
        and _c3b["tail_steps"] + _c3b["hist_steps"] == int(_ref3.shape[-1])
        and _c3b["tail_steps"] <= _c3b["tail_keep"],
        "tail %d（请求 %d）/ hist %d / 成品 %d"
        % (_c3b["tail_steps"], _c3b["tail_keep"], _c3b["hist_steps"], int(_ref3.shape[-1])))
    _ref4, _c4 = compose_ref([_hist], None, max_steps=200)
    chk("同源：调用方没给音频尾 ⇒ 仍出结果 + note **报红**（与 pin 不再同源）",
        int(_ref4.shape[-1]) == 120 and "不再同源" in _c4["note"], _c4["note"])
    _ref5, _c5 = compose_ref([], _tail, max_steps=50, pin_steps=40)
    chk("同源：没有历史素材 ⇒ 参考就是音频尾本身（不变式平凡成立）",
        int(_ref5.shape[-1]) == 50 and _c5["kept"] == 0, _c5["note"])
    try:
        compose_ref([], None, max_steps=100)
        chk("同源：两边都空 ⇒ raise（不返回空张量）", False, "")
    except ValueError:
        chk("同源：两边都空 ⇒ raise（不返回空张量）", True, "")
    # 🔴 **本轮实测数字锁**（2026-10-05 的 va5 口径）：目标栅格 405 步 / pin 37 步 /
    #    历史预算 8 s = 320 步 ⇒ 尾让位到 85 步、历史拿满 320 步、**总长恰好 405**。
    _rr, _ci2 = compose_ref([torch.ones(1, 1, 2, 160), torch.ones(1, 1, 2, 160)],
                            torch.ones(1, 1, 2, 405), max_steps=405, pin_steps=37,
                            history_budget=320)
    chk("数字锁（本轮实测）：栅格 405 / pin 37 / 历史预算 320 ⇒ 尾 85 + 历史 320 = 405",
        int(_rr.shape[-1]) == 405 and _ci2["tail_steps"] == 85 and _ci2["hist_steps"] == 320
        and _ci2["kept"] == 2 and _ci2["dropped"] == 0,
        str({k: _ci2[k] for k in ("tail_steps", "hist_steps", "kept", "dropped")}))

    # —— ③ 代理可区分性：默认**只报告**；给 `sim_noise` 才切换（R6，本轮新发现）——
    _b7c, _n7c = accumulate_all(["A"], _o5, _a5, current_stage=3,
                                first_stages={"A": 0}, max_seconds=2.0)
    _j7c = "\n".join(_n7c)
    chk("代理观测（R6）：极差 < 报告阈值 ⇒ **出声**「本步排序实际等于按段号」，且判据不变",
        "实际等于" in _j7c and "段 1（相似度" in _j7c, _j7c.replace("\n", " ｜ ")[:120])
    _b7d, _n7d = accumulate_all(["A"], _o5, _a5, current_stage=3,
                                first_stages={"A": 0}, max_seconds=2.0, sim_noise=0.05)
    _j7d = "\n".join(_n7d)
    chk("代理开关（R6）：`sim_noise=0.05` ⇒ 切成**最近段优先**（取段 2，不再是更像的段 1）",
        "改按**最近段优先**" in _j7d and "段 2（相似度" in _j7d and "段 1（相似度" not in _j7d,
        _j7d.replace("\n", " ｜ ")[:120])

    # —— ④ 三条新配置键 ——
    _cfg5 = parse_config({"config": {"max_speakers": 3, "prior_split": True, "sim_noise": 0.2},
                          "stages": {"1": ["A"]}})[0]
    _cfg6 = parse_config({"stages": {"1": ["A"]}})[0]
    chk("配置：三条新键（max_speakers / prior_split / sim_noise）解析 + 默认值/钳制",
        _cfg5["max_speakers"] == 3 and _cfg5["prior_split"] is True
        and abs(_cfg5["sim_noise"] - 0.2) < 1e-9
        and _cfg6["max_speakers"] == DEFAULT_MAX_SPEAKERS
        and _cfg6["prior_split"] is DEFAULT_PRIOR_SPLIT
        and _cfg6["sim_noise"] == DEFAULT_SIM_NOISE
        and parse_config({"config": {"max_speakers": 99}, "stages": {"1": ["A"]}})[0]["max_speakers"] == 8,
        "%s" % {k: _cfg5[k] for k in ("max_speakers", "prior_split", "sim_noise")})

    # —— ⑤ 声锚门（`with_anchor`，2026-10-06）——
    _cfg7 = parse_config({"stages": {"1": ["A"]}})[0]
    _cfg8 = parse_config({"config": {"with_anchor": True}, "stages": {"1": ["A"]}})[0]
    chk("配置：`with_anchor` 默认 **False**（= 声锚优先，0.6.27 的已验证行为）",
        _cfg7["with_anchor"] is DEFAULT_WITH_ANCHOR and _cfg7["with_anchor"] is False,
        "%r" % _cfg7["with_anchor"])
    chk("配置：`with_anchor=true` 能读到（未实测的组合只能 opt-in）",
        _cfg8["with_anchor"] is True, "%r" % _cfg8["with_anchor"])
    _s_no, _g_no = anchor_gate(False, _cfg7)
    _s_def, _g_def = anchor_gate(True, _cfg7)
    _s_on, _g_on = anchor_gate(True, _cfg8)
    chk("声锚门：无声锚 ⇒ **不涉及**（不拦也不放行，notes 不许加噪音）",
        _s_no is False and _g_no == "", "%r" % _g_no)
    chk("声锚门：有声锚 + 默认 ⇒ **跳过 VA**，且原因里写出开关名（可照做）",
        _s_def is True and "跳过" in _g_def and "with_anchor" in _g_def, "")
    chk("声锚门：有声锚 + `with_anchor=true` ⇒ **放行**，且告警写明「未实测」",
        _s_on is False and "叠加" in _g_on and "未实测" in _g_on, "")
    # 🔴 反向断言：默认与 opt-in 的**判定结果真的不同**（注入"删掉 with_anchor 分支"会红）
    chk("声锚门：默认 skip=True vs opt-in skip=False —— **结果真的不同**（不是文案差异）",
        _s_def is True and _s_on is False, "默认 %s / opt-in %s" % (_s_def, _s_on))
    # —— ⑤b 边界/病态：不许崩、不许猜 ——
    chk("边界：`anchor_gate` 对 `cfg=None` / 缺键 ⇒ 走默认（不崩、不猜）",
        anchor_gate(True, None)[0] is True and anchor_gate(True, {})[0] is True
        and anchor_gate(False, None) == (False, ""), "")
    chk("边界：`with_anchor` 非布尔（1 / 0）⇒ 按真值判，不崩",
        parse_config({"config": {"with_anchor": 1}, "stages": {"1": ["A"]}})[0]["with_anchor"] is True
        and parse_config({"config": {"with_anchor": 0},
                          "stages": {"1": ["A"]}})[0]["with_anchor"] is False, "")
    # —— ⑤c 类型契约（2026-10-06 第 2 轮审核加固）——
    _raised = False
    try:
        anchor_gate(None, _cfg7)
    except TypeError:
        _raised = True
    chk("声锚门：`has_anchor` 非 bool（None）⇒ **raise TypeError**（不许静默放行 VA）",
        _raised, "")
    chk("声锚门：`cfg` 非 dict（列表）⇒ 按默认值判，**不崩**",
        anchor_gate(True, ["not", "a", "dict"])[0] is True
        and anchor_gate(True, ["not", "a", "dict"])[1] != "", "")

    # —— ⑤d 智能兼容（2026-10-06）：**用户手写的配置值**一律兜底 + 出声，不崩 ——
    _gb = parse_config({"config": {"with_anchor": "false", "prior_split": "no"},
                        "stages": {"1": ["A"]}})
    chk('智能兼容：字符串 "false" / "no" ⇒ **判为关**'
        '（`bool("false")` 是 True ⇒ 旧写法会**静默反向**）',
        _gb[0]["with_anchor"] is False and _gb[0]["prior_split"] is False,
        "with_anchor=%r prior_split=%r" % (_gb[0]["with_anchor"], _gb[0]["prior_split"]))
    chk('智能兼容："true" / "TRUE" / " 1 " / "yes" / "是" ⇒ 判为开',
        all(parse_config({"config": {"with_anchor": s},
                          "stages": {"1": ["A"]}})[0]["with_anchor"] is True
            for s in ("true", "TRUE", " 1 ", "yes", "是")), "")
    _gn = parse_config({"config": {"max_seconds": "六"}, "stages": {"1": ["A"]}})
    chk('智能兼容：数字认不出（"六"）⇒ **用默认值 + 出声**（不崩整条链）',
        abs(_gn[0]["max_seconds"] - DEFAULT_MAX_S) < 1e-9
        and any("不是数字" in n for n in _gn[4]), "%s" % _gn[4])
    _gu = parse_config({"config": {"with_anchor": "maybe"}, "stages": {"1": ["A"]}})
    chk('智能兼容：布尔认不出（"maybe"）⇒ 默认值 + 出声（且写明该写什么）',
        _gu[0]["with_anchor"] is False and any("认不出" in n for n in _gu[4]),
        "%s" % _gu[4])
    _st = False
    try:
        parse_config({"config": {"max_seconds": 6.0}, "stages": {"1": 42}})
    except RuntimeError:
        _st = True
    chk("智能兼容的**分界**：结构错（`stages.1` 是数字）⇒ **仍 raise**（不兜底）", _st, "")
    # BOM：Windows 记事本存 "UTF-8" **默认加 BOM** ⇒ `utf-8` 读会崩
    import shutil as _sh
    import tempfile as _tfp
    _bd = _tfp.mkdtemp(prefix="va_bom_")
    try:
        _bp = os.path.join(_bd, "_va.json")
        with open(_bp, "wb") as _bh:
            _bh.write(b"\xef\xbb\xbf" + json.dumps({"stages": {"1": ["A"]}}).encode("utf-8"))
        chk("智能兼容：`_va.json` 带 **UTF-8 BOM** ⇒ 仍能读（记事本用户的默认存法）",
            (load_config(_bp) or {}).get("stages") == {"1": ["A"]}, "")
    finally:
        _sh.rmtree(_bd, ignore_errors=True)

    print("\n自检：%d 通过 / %d 失败" % (ok, len(fails)))
    if fails:
        print("失败项：")
        for f in fails:
            print("   -", f)
    return 1 if fails else 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
