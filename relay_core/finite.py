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
每段 k（0 起算）一条链，共 11 个节点：

    MiniMaxH3ImageToVideo → H3RelayLatentLoad → H3RelayCopyBridge → RandomNoise
    → BasicGuider → SamplerCustomAdvanced → H3RelayLatentSave
    → VAEDecode / VAEDecodeAudio → H3RelayTrimAV → H3RelayJoinAV

返回的图与 ``GraphBuilder.finalize()`` 同构（链接一律 ``[节点 id, 输出位]``）；
对象型入参（model/clip/vae/…）原样落进对应输入槽，宿主执行时按字面值解析。
所有硬校验都收在 ``build_finite_graph`` 一处 ⇒ 任何调用方都在烧卡前拿到同一组错误。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ._grid import GUIDE_RUNS
from .bridge import MASK_MODES
from .prompt import split_prompt_blocks

#: 一次展开的段数上限（≈640 个临时节点的护栏；要更多分批提交，每批一个 run_id 段区间）。
FINITE_MAX_SEGMENTS = 64

#: 每段节点数（上面链路里数出来的；改链路必须同步这个数）。
_NODES_PER_SEGMENT = 11


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
                           length: Any = None, width: Any = None, height: Any = None) -> None:
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
    # 校验放这里（唯一的门）：节点与测试都走本函数 ⇒ 烧卡前的错误拦截对所有调用方生效。
    validate_finite_params(segments=segments, seed=seed, run_id=run_id,
                           context_frames=context_frames, mask_mode=mask_mode,
                           length=length, width=width, height=height)
    n = int(segments)
    rid = "" if run_id is None else str(run_id)
    nt = "" if note is None else str(note)
    graph: Dict[str, Dict[str, Any]] = {}
    prev_join: Optional[str] = None

    for k in range(n):
        tag = prefix + "seg%d_" % k
        i2v, load, bridge = tag + "i2v", tag + "load", tag + "bridge"
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

        # ② 读上一段（k=0 交空上下文 ⇒ 桥自动直通，与画布第 1 段同行为）
        graph[load] = {"class_type": "H3RelayLatentLoad",
                       "inputs": {"run_id": rid, "stage_index": k}}

        # ③ 拷贝桥：latent 钉住 + conditioning 钉帧（复合桥，最小示例同款接法）
        bridge_inputs: Dict[str, Any] = {
            "latent": [i2v, 1],
            "context_latent": [load, 0],
            "context_frames": int(context_frames),
            "mask_mode": str(mask_mode),
            "pin_audio": bool(pin_audio),
            "conditioning": [i2v, 0],
            "run_id": rid,
            "stage_index": k,
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
                                  "stage_index": k, "note": nt}}

        # ⑥ 解码 + 裁重叠头（trim_frames 走桥的第 3 路，全自动）
        graph[vdec] = {"class_type": "VAEDecode",
                       "inputs": {"samples": [samp, 0], "vae": vae}}
        graph[adec] = {"class_type": "VAEDecodeAudio",
                       "inputs": {"samples": [samp, 0], "vae": audio_vae}}
        graph[trim] = {"class_type": "H3RelayTrimAV",
                       "inputs": {"images": [vdec, 0], "trim_frames": [bridge, 2],
                                  "fps": 24.0, "audio": [adec, 0]}}

        # ⑦ 张量域拼条（首段无累计输入 ⇒ 透传）
        join_inputs: Dict[str, Any] = {"images": [trim, 0], "audio": [trim, 1]}
        if prev_join is not None:
            join_inputs["images_acc"] = [prev_join, 0]
            join_inputs["audio_acc"] = [prev_join, 1]
        graph[join] = {"class_type": "H3RelayJoinAV", "inputs": join_inputs}
        prev_join = join

    report = (
        "一次提交展开 %d 段（seed=%d，run_id=%s，context_frames=%d，mask_mode=%s）。\n"
        "每段链：ImageToVideo → LatentLoad → CopyBridge → 采样 → LatentSave → 解码 → TrimAV → JoinAV。\n"
        "边车：output/relay_kit/%s/stage_%05d..%05d（含谱系头）。\n"
        "⚠ 合并输出驻留内存：%d 段全画面在一条张量里，长片先小分辨率验证。"
        % (n, int(seed), rid or "（空）", int(context_frames), str(mask_mode),
           rid or "<run_id>", 0, n - 1, n)
    )
    return graph, [[samp, 0], [prev_join, 0], [prev_join, 1], report], report


def finite_graph_node_count(segments: int) -> int:
    """展开图节点数（供测试与报告断言）。"""
    return int(segments) * _NODES_PER_SEGMENT
