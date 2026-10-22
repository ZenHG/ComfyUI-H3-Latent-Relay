# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""relay_core.finite —— ⏩ Finite Run 的展开图构建（纯函数，零宿主依赖）。

``H3RelayFiniteRun`` 节点的算法层：把「N 段连跑」展开成一张无环图（dict in → dict out，
不 import torch / 不 import 任何 ComfyUI 模块 ⇒ 可离线单测）。

展开机制是 ComfyUI 公开能力（非私有 API）：schema 声明 ``enable_expand=True``，
execute 返回 ``{"expand": <图>, "result": <输出>}``，宿主把图里每个节点注册为
**临时节点**（``execution.py`` 的 ``dynprompt.add_ephemeral_node``）并入当次 prompt
执行，外层节点的输出可以是 ``[临时节点 id, 输出位]`` 形式的链接。参照实现
TimelineDirector 的 ``MiniMaxH3FiniteSegmentSampler``（GPL-3.0）**只借鉴机制与编排
思路，不搬代码**（干净室）。

展开图里全部是**已有节点**（别写第二套；算法零重写，本模块只做编排与校验）。
每段 k（0 起算）一条链，共 10 个节点（上一段由桥**自取**，不再手接「Latent Load」）：

    MiniMaxH3ImageToVideo → H3RelayCopyBridge → RandomNoise
    → BasicGuider → SamplerCustomAdvanced → H3RelayLatentSave
    → VAEDecode / VAEDecodeAudio → H3RelayTrimAV → H3RelayJoinAV

其中「Trim AV」的 ``trim_frames`` 由桥的第 3 路给（全自动），``settle_frames`` 由调用方给
（沉降档属**裁量**，不是后处理 ⇒ 必须让脚本/API 侧也能设，否则画布能设、API 设不了）。
``from_segment`` 决定**从第几段起跑**（段号整体平移）：``0`` = 从第 1 段起跑（不接任何已有段）；
``>0`` = 首段的桥按 ``run_id`` + 段号−1 自取盘上那段续接 ⇒ 与画布 Chain 填 ``stage_index``
同义，无头路径从此不必分两次提交、也不必手搓链。

返回的图与 ``GraphBuilder.finalize()`` 同构（链接一律 ``[节点 id, 输出位]``）；
对象型入参（model/clip/vae/…）原样落进对应输入槽，宿主执行时按字面值解析。
所有硬校验都收在 ``build_finite_graph`` 一处 ⇒ 任何调用方都在烧卡前拿到同一组错误。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ._grid import (GUIDE_RUNS, frame_grid_neighbors, frame_grid_floor,
                    is_frame_grid)
from .bridge import MASK_MODES
from .prompt import split_prompt_blocks
from .seam import SETTLE_CAP

#: 一次展开的段数上限（≈640 个临时节点的护栏；要更多分批提交，每批一个 run_id 段区间）。
FINITE_MAX_SEGMENTS = 64

#: 每段节点数（上面链路里数出来的；改链路必须同步这个数）。
_NODES_PER_SEGMENT = 10


class FinitePlanError(ValueError):
    """连跑参数在建图期就不合法（在任何 GPU 工作之前拦住）。"""


# ---------------------------------------------------------------------------
# 参数解析与校验（全部在建图期完成 ⇒ 烧卡之前发现错误）
# ---------------------------------------------------------------------------

def resolve_finite_prompts(prompt: Any, prompts: Any, segments: Any) -> Tuple[List[str], str]:
    """定出每段各用什么词。返回 ``(每段词列表, 说明行)``。

    口径与画布 Chain 一致（铁律一）：``prompts`` 有内容 ⇒ 按「单独一行 ≥3 个短横线」
    分块、第 k 块喂第 k 段，块数不够直接 raise（绝不静默复用）；留空 ⇒ 每段共用
    ``prompt``（= Chain 的「不换词」）；两者都空 ⇒ raise。
    """
    try:
        n = int(segments)
    except (TypeError, ValueError):
        raise FinitePlanError("segments 必须是整数（得到 %r）。" % (segments,)) from None
    _p = "" if prompt is None else str(prompt)
    _ps = "" if prompts is None else str(prompts)
    blocks = split_prompt_blocks(_ps)
    if blocks:
        if len(blocks) < n:
            raise FinitePlanError(
                "`prompts` 只有 %d 块词，跑不到第 %d 段 ⇒ 补齐第 %d 块，"
                "或把 segments 改成 %d 或更小。\n"
                "    （本包不会把最后一块词复用给后面的段 —— 那就是「以为换了词、其实没换」。）"
                % (len(blocks), n, n, len(blocks)))
        note = "每段词：`prompts` 第 1..%d 块（共 %d 块，按 `---` 分块）" % (n, len(blocks))
        if len(blocks) > n:
            note += "；多出的 %d 块未使用" % (len(blocks) - n)
        return blocks[:n], note
    if not _p.strip():
        raise FinitePlanError(
            "`prompt` 与 `prompts` 都是空的 ⇒ 没有词可跑。\n"
            "    两种正确接法（任选其一）：\n"
            "      ① 只跑一种内容：把词填进 `prompt`（每段都用它）；\n"
            "      ② 每段不同词：在 `prompts` 里用单独一行 `---` 分成 %d 块。" % n)
    return [_p] * n, "每段词：全部用同一份 `prompt`（`prompts` 留空 = 不换词）"


def validate_finite_params(*, segments: Any = None, seed: Any = None, run_id: Any = None,
                           context_frames: Any = None, mask_mode: Any = None,
                           length: Any = None, width: Any = None, height: Any = None,
                           settle_frames: Any = None, from_segment: Any = None) -> None:
    """建图前的硬校验（纯值检查，不碰盘、不碰 GPU）。

    任一项为 ``None`` ⇒ **跳过该项**。执行期调用方总会给全；而入队前那次
    （``validate_finite_inputs_early``）看不到链接型入参 —— 宿主 ``get_input_data``
    在没有 execution_list 时对链接走 mark_missing，拿到手就是 ``None``。
    看不到就不猜（猜错会挡住合法图），留给执行期那道门。
    """
    n = None
    if segments is not None:
        try:
            n = int(segments)
        except (TypeError, ValueError):
            raise FinitePlanError("segments 必须是整数（得到 %r）。" % (segments,)) from None
        if n < 1:
            raise FinitePlanError("segments 至少为 1（得到 %d）。" % n)
        if n > FINITE_MAX_SEGMENTS:
            raise FinitePlanError(
                "segments 上限 %d（一次展开的临时节点数护栏；得到 %d）。\n"
                "    要跑更长：分几批提交，每批一个 run_id 段区间。"
                % (FINITE_MAX_SEGMENTS, n))
    if seed is not None:
        try:
            s = int(seed)
        except (TypeError, ValueError):
            raise FinitePlanError("seed 必须是整数（得到 %r）。" % (seed,)) from None
        if s < 0:
            raise FinitePlanError("seed 不能为负（得到 %d）。" % s)
    if n is not None and run_id is not None:
        rid = str(run_id)
        if n > 1 and not rid.strip():
            # 第 2 段起 LatentLoad 要读上一段落盘文件 ⇒ 没 run_id 必然炸。建图期说清，
            # 别让第 1 段的卡烧完了才报。
            raise FinitePlanError(
                "多段连跑必须填 `run_id`（落盘与读上一段都靠它定位文件）。\n"
                "    只跑 1 段可以留空；≥2 段请填一个这部片子独有的名字（换新片子必须换新名字）。")
    if context_frames is not None:
        try:
            cf = int(context_frames)
        except (TypeError, ValueError):
            raise FinitePlanError("context_frames 必须是整数（得到 %r）。" % (context_frames,)) from None
        if cf not in GUIDE_RUNS:
            raise FinitePlanError(
                "context_frames 只认合法窗 %s（得到 %d）。\n"
                "    窗必须落在 H3 的 17k+5 帧栅格上，否则桥切出来的尾段会整体错位。"
                % (" / ".join(str(g) for g in GUIDE_RUNS), cf))
    if mask_mode is not None and mask_mode not in MASK_MODES:
        raise FinitePlanError(
            "mask_mode 只认 %s（得到 %r）。" % (" / ".join(MASK_MODES), mask_mode))
    if length is not None:
        try:
            L = int(length)
        except (TypeError, ValueError):
            raise FinitePlanError("length 必须是整数（得到 %r）。" % (length,)) from None
        if L < 5:
            raise FinitePlanError("length 至少 5 帧（H3 最短片段；得到 %d）。" % L)
        # 🔴 2026-10-20 真机实测补：length 必须落在 **5+17k 栅格**上。
        #   宿主对 INT 只查 min/max、**不查 step**（execution.py 的 INT 分支没有 step
        #   校验；`step=17` 只是前端步进）⇒ `length=96` 会被宿主放行。
        #   上游 `MiniMaxH3ImageToVideo` 拿到 96 会 `align_frame_count` **向上吸附**到 107
        #   ⇒ 用户填 96、实跑 107 帧（latent 32 步），裁首 22 后成片 85 帧而非 74 帧
        #   ⇒ 帧账错 11 帧，`h3_shotcut_check` 的 C1（Σ=成片帧数，严等）假红。
        #   实测：length=96 → 成片 85 帧 / 3.54s（`frlen96`）。
        #   按本仓纪律「非法值直接 raise，不吸附」——段长是续接相位的前提，改了必须让人知道。
        # 🔴 判据走 `_grid.is_frame_grid` 单一真相源（2026-10-22）：这条规则三处都要
        #   （本处执行期硬闸 / check_ui_workflow 跑批前体检 / h3_extend_run 脚本侧），
        #   各处自己写取模表达式 ⇒ 漏改一处就出现「FiniteRun 拦、Chain 不拦」的半边漏洞。
        if not is_frame_grid(L):
            lo, hi = frame_grid_neighbors(L)
            raise FinitePlanError(
                "length=%d 不在 H3 的 5+17k 帧栅格上（合法档：%s …）。\n"
                "    最接近的合法档是 %d（下邻 %d / 上邻 %d）。\n"
                "    ⚠ 宿主只查 min/max 不查步长，上游会把非档值**静默吸附**到最近的档"
                "（96 → 107）⇒ 你填的帧数与实际跑出来的不一样，帧账随之错位。\n"
                "    这里不吸附：段长决定续接相位，改了必须让你知道。" % (
                    L, ", ".join(str(g) for g in sorted(GUIDE_RUNS) if g >= 5), hi, lo, hi))
    for name, val in (("width", width), ("height", height)):
        if val is None:
            continue
        try:
            v = int(val)
        except (TypeError, ValueError):
            raise FinitePlanError("%s 必须是整数（得到 %r）。" % (name, val)) from None
        if v < 32 or v % 16 != 0:
            raise FinitePlanError(
                "%s 必须是 ≥32 且 16 的倍数（H3 画布栅格；得到 %d）。" % (name, v))
    if settle_frames is not None:
        try:
            sf = int(settle_frames)
        except (TypeError, ValueError):
            raise FinitePlanError(
                "settle_frames 必须是整数（得到 %r）。" % (settle_frames,)) from None
        if sf < -1 or sf > SETTLE_CAP:
            raise FinitePlanError(
                "settle_frames 必须在 -1..%d 之间（-1 = 自动量切换点，0 = 只裁钉住区，"
                "N = 固定多裁 N 帧；得到 %d）。" % (SETTLE_CAP, sf))
    if from_segment is not None:
        try:
            int(from_segment)
        except (TypeError, ValueError):
            raise FinitePlanError(
                "from_segment 必须是整数（得到 %r）。" % (from_segment,)) from None
        if int(from_segment) < 0:
            raise FinitePlanError(
                "from_segment 不能为负（0 = 从第 1 段起跑，即不接任何已有段；得到 %d）。"
                % int(from_segment))
        # 上限不在这判：段号只决定**读/写哪个 stage 文件**，与展开图的临时节点数无关
        #   （后者由 segments 管）。填大了只会在桥自取时明确报「文件不在」—— 见
        #   nodes.py 里 H3RelayFiniteRun.run 的起始段预检，那一步更早、也更具体。


def validate_finite_inputs_early(**kw: Any) -> Optional[str]:
    """``VALIDATE_INPUTS`` 用：入队前的**尽力**校验。返回错误文案，或 ``None`` = 没意见。

    为什么要有它：执行期才报错的话，上游的加载器节点已经先跑过了（冷启动时那是
    一次完整的模型上卡）。把「看得到字面值」的错误提到 ``/prompt`` 校验阶段，
    宿主直接以 HTTP 400 拒收 —— 一行卡都不烧。

    只许**更松**，不许更紧：任何一项拿到 ``None``（= 链接型入参，校验期不可知）
    就跳过对应检查；交叉项（词块数 vs 段数）必须两项都看得到才判。
    误拦一张合法图，比晚拦一次坏图严重得多。
    """
    try:
        validate_finite_params(
            segments=kw.get("segments"), seed=kw.get("seed"), run_id=kw.get("run_id"),
            context_frames=kw.get("context_frames"), mask_mode=kw.get("mask_mode"),
            length=kw.get("length"), width=kw.get("width"), height=kw.get("height"))
        prompt, prompts, n = kw.get("prompt"), kw.get("prompts"), kw.get("segments")
        if prompt is not None and prompts is not None and n is not None:
            resolve_finite_prompts(prompt, prompts, n)
    except FinitePlanError as exc:
        return str(exc)
    return None


# ---------------------------------------------------------------------------
# 展开图构建
# ---------------------------------------------------------------------------

def build_finite_graph(
    *,
    prefix: str,
    segments: int,
    blocks: List[str],
    seed: int,
    run_id: str,
    note: str,
    width: int,
    height: int,
    length: int,
    context_frames: int,
    mask_mode: str,
    settle_frames: int = 0,
    from_segment: int = 0,
    pin_audio: bool,
    model: Any,
    clip: Any,
    vae: Any,
    audio_vae: Any,
    image: Any = None,
    sampler: Any = None,
    sigmas: Any = None,
    voice_anchor: Any = None,
) -> Tuple[Dict[str, Dict[str, Any]], List[Any], str]:
    """构建 N 段展开图。返回 ``(graph, outputs, 报告)``。

    ``prefix`` 由宿主在 execute 时经 ``GraphBuilder.alloc_prefix()`` 分配（以本节点
    unique_id 为根 ⇒ 同一张图里放多个连跑节点也不会撞 id）。
    ``blocks`` 长度必须 == segments（由 ``resolve_finite_prompts`` 产出）。
    ``outputs`` 顺序 = 节点 RETURN_TYPES：末段 latent 链接、拼成一条的画面/音频链接、
    报告字符串（字面值）。
    """
    if len(blocks) != int(segments):
        raise FinitePlanError(
            "内部错误：词块数 %d 与段数 %d 不一致（应由 resolve_finite_prompts 保证）。"
            % (len(blocks), int(segments)))
    # None ⇒ 取默认（与下面 rid / nt 同一惯例）：脚本侧显式传 None 不该炸成 TypeError。
    if settle_frames is None:
        settle_frames = 0
    if from_segment is None:
        from_segment = 0
    # 校验放这里（唯一的门）：节点与测试都走本函数 ⇒ 烧卡前的错误拦截对所有调用方生效。
    validate_finite_params(segments=segments, seed=seed, run_id=run_id,
                           context_frames=context_frames, mask_mode=mask_mode,
                           length=length, width=width, height=height,
                           settle_frames=settle_frames, from_segment=from_segment)
    n = int(segments)
    fs = int(from_segment)
    rid = "" if run_id is None else str(run_id)
    nt = "" if note is None else str(note)
    graph: Dict[str, Dict[str, Any]] = {}
    prev_join: Optional[str] = None

    for k in range(n):
        tag = prefix + "seg%d_" % k
        stage = fs + k                     # 本次实际跑第几段（from_segment 平移）
        i2v, bridge = tag + "i2v", tag + "bridge"
        noise, guider, samp = tag + "noise", tag + "guider", tag + "samp"
        save, vdec, adec = tag + "save", tag + "vdec", tag + "adec"
        trim, join = tag + "trim", tag + "join"

        # ① 词 → conditioning + 空 AV latent（官方节点；first_frame 可选 = t2v 模式）
        i2v_inputs: Dict[str, Any] = {
            "clip": clip, "vae": vae, "prompt": blocks[k],
            "width": int(width), "height": int(height), "length": int(length),
        }
        if image is not None:
            i2v_inputs["first_frame"] = image
        graph[i2v] = {"class_type": "MiniMaxH3ImageToVideo", "inputs": i2v_inputs}

        # ② 上一段 latent **不接了** —— 桥自己按 run_id + 段号−1 从磁盘取（0.6.37 起免接线）。
        #    stage ≥ 1 时取的是上一段；stage = 0（from_segment=0 且 k=0）没有上一段
        #    ⇒ 桥自动直通，与画布第 1 段同行为。

        # ③ 拷贝桥：latent 钉住 + conditioning 钉帧（复合桥，最小示例同款接法）
        bridge_inputs: Dict[str, Any] = {
            "latent": [i2v, 1],
            "context_frames": int(context_frames),
            "mask_mode": str(mask_mode),
            "pin_audio": bool(pin_audio),
            "conditioning": [i2v, 0],
            "run_id": rid,
            "stage_index": stage,
        }
        if voice_anchor is not None:
            bridge_inputs["voice_anchor"] = voice_anchor
        graph[bridge] = {"class_type": "H3RelayCopyBridge", "inputs": bridge_inputs}

        # ④ 采样三件套（核心节点；sigmas/sampler 由外层用户自配）
        graph[noise] = {"class_type": "RandomNoise", "inputs": {"noise_seed": int(seed)}}
        graph[guider] = {"class_type": "BasicGuider",
                         "inputs": {"model": model, "conditioning": [bridge, 3]}}
        graph[samp] = {"class_type": "SamplerCustomAdvanced",
                       "inputs": {"noise": [noise, 0], "guider": [guider, 0],
                                  "sampler": sampler, "sigmas": sigmas,
                                  "latent_image": [bridge, 0]}}

        # ⑤ 边车落盘（OUTPUT_NODE；B1 谱系头由节点内部按 run_id/段号自动写）
        graph[save] = {"class_type": "H3RelayLatentSave",
                       "inputs": {"latent": [samp, 0], "run_id": rid,
                                  "stage_index": stage, "note": nt}}

        # ⑥ 解码 + 裁重叠头（trim_frames 走桥的第 3 路，全自动；settle_frames 由调用方给）
        graph[vdec] = {"class_type": "VAEDecode",
                       "inputs": {"samples": [samp, 0], "vae": vae}}
        graph[adec] = {"class_type": "VAEDecodeAudio",
                       "inputs": {"samples": [samp, 0], "vae": audio_vae}}
        graph[trim] = {"class_type": "H3RelayTrimAV",
                       "inputs": {"images": [vdec, 0], "trim_frames": [bridge, 2],
                                  "fps": 24.0, "audio": [adec, 0],
                                  "settle_frames": int(settle_frames)}}

        # ⑦ 张量域拼条（首段无累计输入 ⇒ 透传）
        join_inputs: Dict[str, Any] = {"images": [trim, 0], "audio": [trim, 1]}
        if prev_join is not None:
            join_inputs["images_acc"] = [prev_join, 0]
            join_inputs["audio_acc"] = [prev_join, 1]
        graph[join] = {"class_type": "H3RelayJoinAV", "inputs": join_inputs}
        prev_join = join

    # —— 合并输出的显存账（2026-10-22 补：原来只有一句"长片先小分辨率验证"，没数字）——
    # JoinAV 用 `torch.cat` 逐段累加 ⇒ **驻留 = Σ各段帧**（不是最大值，是总和），
    # 且每接一段都要把已累计的全部拷贝一遍 ⇒ 拷贝量 = O(N²)。
    # 实测口径（RTX 5070 12GB / 合伙人项目 192 帧·1216×672）：
    #    2 段 0.9 GiB ｜ 6 段 2.6 GiB ｜ 10 段 4.4 GiB ｜ **37 段 16.2 GiB ⇒ 必爆**
    # 12GB 卡的实务上限约 10 段；更长必须分批提交，或走 🔗 Chain 的**文件路**拼接
    # （那段不把全片驻留内存，而是逐段落盘后拼）。
    _mib_frame = float(width) * float(height) * 3.0 / (1024.0 * 1024.0)
    _acc_gib = n * float(length) * _mib_frame / 1024.0
    _copy_gib = sum((i + 1) for i in range(n)) * float(length) * _mib_frame / 1024.0
    if _acc_gib > 10.0:
        _vram_note = (
            "\n🔴 合并输出需 **%.1f GiB** 显存/内存（%d 段 × %d 帧 × %d×%d），"
            "累计拷贝 %.0f GiB。**12GB 卡装不下**（实测 10 段 4.4 GiB、37 段 16.2 GiB）。"
            "\n   两条路：① 分批提交，每批 ≤10 段（各自一个 run_id 段区间）；"
            "② 走 🔗 Chain 的「🧩 拼成一条」——那段逐落盘后拼，不把全片驻留内存。" % (
                _acc_gib, n, int(length), int(width), int(height), _copy_gib))
    elif _acc_gib > 6.0:
        _vram_note = (
            "\n⚠ 合并输出需 **%.1f GiB**（累计拷贝 %.0f GiB）—— 已接近 12GB 卡的实务上限"
            "（约 10 段 / 4.4 GiB）。再长请分批提交或走 🔗 Chain 文件路拼接。" % (
                _acc_gib, _copy_gib))
    else:
        _vram_note = (
            "\n合并输出驻留内存约 **%.2f GiB**（累计拷贝 %.1f GiB）—— 在 12GB 卡实务范围内。"
            % (_acc_gib, _copy_gib))

    report = (
        "一次提交展开 %d 段（seed=%d，run_id=%s，context_frames=%d，mask_mode=%s，"
        "settle_frames=%d，from_segment=%d）。\n"
        "每段链：ImageToVideo → CopyBridge → 采样 → LatentSave → 解码 → TrimAV → JoinAV"
        "（上一段由桥自取，不再手接 Latent Load；trim_frames 由桥给，settle_frames 见上）。\n"
        "边车：output/relay_kit/%s/stage_%05d..%05d（含谱系头）%s。%s"
        % (n, int(seed), rid or "（空）", int(context_frames), str(mask_mode),
           int(settle_frames), fs, rid or "<run_id>", fs, fs + n - 1,
           ("；首段读 stage_%05d 续接" % (fs - 1)) if fs > 0 else "；首段不接上一段（直通）",
           _vram_note)
    )
    return graph, [[samp, 0], [prev_join, 0], [prev_join, 1], report], report


def finite_graph_node_count(segments: int) -> int:
    """展开图节点数（供测试与报告断言）。"""
    return int(segments) * _NODES_PER_SEGMENT
