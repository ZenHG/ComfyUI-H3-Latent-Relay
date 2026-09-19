# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Relay-Kit contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""H3 Relay Kit · 节点层

八个节点，覆盖"用作者的续接方式"所需的全部接线：

  🔗 H3 续接 Latent 存   —— 把本段的 AV latent 落盘，供下一段读
  🔗 H3 续接 Latent 读   —— 读回上一段的 AV latent
  🔗 H3 续接 Latent 桥   —— 把上一段尾段钉进本段 conditioning（latent 直取，零重编码）
  🔗 H3 续接 拷贝桥      —— 上一段尾部**逐位拷贝**进本段初始 latent + 噪声掩码（钉住区不重绘）
  🔗 H3 续接裁重叠        —— 裁掉续接段头部的重叠帧（视频 + 音频同裁）+ 交接 prev_tail
  🔗 H3 续接后处理 Post   —— 画质域后处理（0.5.0 拆出；不动时间轴/帧数/音频）
  🔗 H3 续接音频缝        —— 音频域：上一段环境声补本段头（长度守恒，零 A/V 位移）
  🔗 H3 续接连跑 Chain    —— 同分组框内自动推进「桥 + 落盘」段号并排队连跑

时间轴 / 画质域 / 音频域分工（0.5.0 起）：
  · H3RelayTrimAV    = 时间轴（裁重叠 / 沉降 / 重影），**冻结不再长 widget**；
  · H3RelayPost      = 画质域（色档对齐 / 补高频 / 锐化）；
  · H3RelayAudioSeam = 音频域（跨段环境声补头 / 床环铺）。
  三条纪律同源：**新功能归到对应域，不往 TrimAV 上挂**。

两条续接路线**二选一**，不可同图串联：
  · Latent 桥（conditioning 钉帧）→ 模型重绘上一段（有复现漂移风险，检测兜底）；
  · 拷贝桥（latent + 噪声掩码）→ 钉住区零重绘（0.4.0 起，通用兼容不绑采样器）。

接线（替换像素续接时）：
    CSGlideCastCS[0] ─ conditioning ─┐
    CSGlideCastCS[1] ─ latent ───────┤
    H3 续接 Latent 读 ─ context ─────┤→ 🔗 续接 Latent 桥 [0] → 采样器 positive
    （上一段：采样器 latent → 🔗 续接 Latent 存）

注意：走任一桥时，上游的**像素续接字段（如 H3 Studio 的 cont）必须留空**，
否则两套续接都会往 minimax_keyframes 里塞锚，画面会打架。
"""

from __future__ import annotations

import math
import os

import folder_paths

from . import relay_core as CORE
from . import layout_contract as CONTRACT


CATEGORY = "H3 Relay Kit"

# 落盘根目录：ComfyUI/output/relay_kit/
_RELAY_ROOT = os.path.join(folder_paths.get_output_directory(), "relay_kit")

# Windows 保留设备名：作为目录名会让 makedirs 抛裸 OSError
_WIN_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {"COM%d" % i for i in range(1, 10)}
    | {"LPT%d" % i for i in range(1, 10)}
)


def _stage_path(run_id: str, stage_index: int) -> str:
    """按 run 标识 + 段号推导落盘路径。段号从 0 开始。"""
    # 非法字符替换为 _（而不是删除）—— 否则 "my/film" 与 "myfilm" 会静默撞进同一目录
    rid = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in str(run_id))
    if not rid.strip("_"):
        raise RuntimeError("run_id 不能为空（用来把同一部片子的各段归到一个目录）。")
    if rid.upper() in _WIN_RESERVED:
        rid += "_"
    idx = int(stage_index)
    if idx < 0:
        raise RuntimeError("stage_index 不能为负（段号从 0 开始，第 1 段=0）。")
    return os.path.join(_RELAY_ROOT, rid, "stage_%05d.safetensors" % idx)


def _audio_stage_path(run_id: str, stage_index: int) -> str:
    """音频落盘路径：与 latent 同目录，文件名前缀 audio_（互不覆盖）。"""
    p = _stage_path(run_id, stage_index)
    return os.path.join(os.path.dirname(p), "audio_%05d.safetensors" % int(stage_index))


class H3RelayLatentSave:
    """把本段采样器输出的 AV latent 落盘。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "latent": ("LATENT", {
                    "tooltip": "【接法】从本段采样器（SelfLiftH3Sampler）的 latent 输出口拉线过来。\n"
                               "作用：把本段拍完的 latent 存成文件，它是下一段的「接力棒」。",
                }),
                "run_id": ("STRING", {
                    "default": "relay",
                    "tooltip": "【填什么】这部片子的名字，比如 myfilm、ep01。\n"
                               "⚠ 必须和「续接 Latent 桥」上的 run_id 一字不差，否则桥找不到文件。\n"
                               "换新片子必须换新名字：同一个名字重跑同段号会覆盖旧文件！\n"
                               "文件存到：ComfyUI/output/relay_kit/<run_id>/",
                }),
                "stage_index": ("INT", {
                    "default": 0, "min": 0, "max": 9999, "step": 1,
                    "tooltip": "【填什么】本段是全片的第几段。第 1 段填 0，第 2 段填 1，第 3 段填 2…\n"
                               "⚠ 必须和「续接 Latent 桥」上的 stage_index 一样大。\n"
                               "改段号时两个节点都要改（用 Chain 节点可以自动改）。",
                }),
                "note": ("STRING", {
                    "default": "",
                    "tooltip": "【可留空】随手写个备注（比如「22帧窗 v2」），存进文件里方便事后分辨版本。",
                }),
            },
        }

    RETURN_TYPES = ("LATENT", "STRING")
    RETURN_NAMES = ("latent", "path")
    FUNCTION = "save"
    CATEGORY = CATEGORY
    # 没有下游消费时也必须执行 —— 落盘本身就是它的产物。
    OUTPUT_NODE = True
    DESCRIPTION = "把本段 AV latent 落盘，供下一段做 latent 续接（零重编码）。"

    def save(self, latent, run_id, stage_index, note=""):
        path = _stage_path(run_id, stage_index)
        CORE.save_av_latent(latent, path, note=note)
        size_mb = os.path.getsize(path) / 1024 ** 2
        print(
            "[H3 Relay] 已存 stage %d → %s (%.1f MB)\n            %s"
            % (int(stage_index), path, size_mb, CORE.describe_latent(latent))
        )
        return (latent, path)


class H3RelayLatentLoad:
    """读回上一段的 AV latent，供续接。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "run_id": ("STRING", {
                    "default": "relay",
                    "tooltip": "【填什么】与「续接 Latent 存」一致的片子名。\n"
                               "⚠ 不一致 = 找不到上一段的文件，直接报错。",
                }),
                "stage_index": ("INT", {
                    "default": 0, "min": 0, "max": 9999, "step": 1,
                    "tooltip": "【填什么】填「你要读的那一段」的段号 = 本段段号 - 1。\n"
                               "例：现在做第 2 段（本段号 1），这里填 1 → 读第 1 段（stage 0）的文件。\n"
                               "填 0 会报错：第 1 段没有上一段可读。",
                }),
            },
            "optional": {
                "explicit_path": ("STRING", {
                    "default": "",
                    "tooltip": "留空则按 run_id + 段号自动推导；填了就直接读这个文件（用于断点续跑换源）。",
                }),
            },
        }

    RETURN_TYPES = ("LATENT", "STRING")
    RETURN_NAMES = ("context_latent", "info")
    FUNCTION = "load"
    CATEGORY = CATEGORY
    DESCRIPTION = "读回上一段的 AV latent；第 1 段（stage_index=0）没有上一段时会明确报错。"

    def load(self, run_id, stage_index, explicit_path=""):
        idx = int(stage_index) - 1
        explicit = (explicit_path or "").strip()
        # 必须先判段号再进 _stage_path：否则 stage_index=0 会先撞上
        # 「stage_index 不能为负」的误导性报错，下面的引导文案永远到不了。
        if idx < 0 and not explicit:
            raise RuntimeError(
                "stage_index=0 是第 1 段，没有上一段可续。\n"
                "    第 1 段请走独立路径（不接本节点，或把续接 Latent 桥的 context_latent 留空）。"
            )
        path = explicit or _stage_path(run_id, idx)
        latent = CORE.load_av_latent(path)
        info = CORE.describe_latent(latent)
        print("[H3 Relay] 已读 stage %d ← %s\n            %s" % (idx, path, info))
        return (latent, info)


class H3RelayMotionContext:
    """latent 桥：把上一段尾段钉进本段 conditioning。

    与像素续接（mp4 → VAE 重编码）走同一个原生协议（minimax_keyframes），
    区别是本节点**不重编码** —— 直接用上一段采样时的 latent。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "conditioning": ("CONDITIONING", {
                    "tooltip": "【接法】从本段的出词/参考条件节点（如官方 MiniMaxH3ReferenceToVideo 的 positive 口）拉线过来。\n"
                               "作用：告诉模型本段要拍什么。桥会在它里面悄悄塞进「上一段的结尾」。",
                }),
                "latent": ("LATENT", {
                    "tooltip": "【接法】同一个条件节点的 latent 输出口拉线过来。\n"
                               "作用：提供本段的规格（分辨率/帧数）。⚠ 必须和上一段分辨率一样，不一样直接报错。",
                }),
                "trim_frames": ("INT", {
                    "default": 22, "min": 5, "max": 124, "step": 17,
                    "tooltip": "【填什么】让模型看着上一段结尾多少帧来接戏。\n"
                               "  · 22（默认）= 标准，0.9 秒衔接上下文，最稳\n"
                               "  · 5 = 最小，成片新内容多 1 秒，但静态场景衔接变弱\n"
                               "  · 只能填 5/22/39/56/73/90/107/124，别的数直接报错\n"
                               "【不用管的部分】钉住的帧成片里会被自动裁掉（第 3 路输出同步给裁节点）。",
                }),
            },
            "optional": {
                "context_latent": ("LATENT", {
                    "tooltip": "【不用接线，留空！】只要 run_id 填了、stage_index ≥ 1，\n"
                               "桥就会自己去 output/relay_kit/<run_id>/ 读上一段的 latent 文件。\n"
                               "这个口是给高级用法手动连「续接 Latent 读」节点用的。",
                }),
                "audio_frames": ("INT", {"advanced": True, 
                    "default": 0, "min": 0, "max": 362, "step": 1,
                    "tooltip": "【不用动，保持 0】音频跟着视频窗走（钉住同样长的声音，让音乐/环境声接着往下走而不是重新起头）。\n"
                               "想单独加长音频上下文才填别的数（0 = 跟随视频窗）。",
                }),
                "run_id": ("STRING", {
                    "default": "relay",
                    "tooltip": "【填什么】这部片子的名字（和「续接 Latent 存」上一致）。\n"
                               "  · 第 2 段起，桥自动去 ComfyUI/output/relay_kit/<run_id>/ 读上一段的 latent 文件\n"
                               "  · ⚠ 两个节点上名字不一致 = 找不到文件\n"
                               "  · 换新片子必须换名字，重跑会覆盖同段号旧文件",
                }),
                "stage_index": ("INT", {
                    "default": 0, "min": 0, "max": 9999, "step": 1,
                    "tooltip": "【填什么】本段是第几段。\n"
                               "  · 第 1 段 → 填 0（直通：不续接，只把 latent 交给落盘节点存档）\n"
                               "  · 第 2 段 → 填 1（自动读第 1 段的文件来续接）\n"
                               "  · 第 3 段 → 填 2……以此类推\n"
                               "⚠ 和「续接 Latent 存」上的数保持一样大；用 Chain 节点可自动推进，不用手改。",
                }),
                "anchor_latent": ("LATENT", {
                    "tooltip": "【0.5.0 全局外观锚，可选】长期钉进本段条件的第一段/角色基准段 latent。\n"
                               "与 context_latent 的分工：context 管『接戏』（上段尾 22 帧），\n"
                               "anchor 管『身份』（色档/光照/角色长相的长程基准，永不退出）。\n"
                               "refs 协议允许异分辨率参考块——第 0 段与后续段分辨率不同也能当锚。\n"
                               "不接且 anchor_stage ≥ 0 时，自动从 run_id 目录读 anchor_stage 那段。",
                }),
                "anchor_stage": ("INT", {"advanced": True, 
                    "default": -1, "min": -1, "max": 9999, "step": 1,
                    "tooltip": "【0.5.0 自动锚段号】-1 = 关闭外观锚（默认，行为与 0.4.x 逐位一致）。\n"
                               "≥ 0 = 没接 anchor_latent 时自动读 output/relay_kit/<run_id>/stage_<该值> 当锚。\n"
                               "长片漂移明显时填 0（永远以第 1 段为身份基准）。",
                }),
                "anchor_frames": ("INT", {"advanced": True, 
                    "default": 5, "min": 5, "max": 124, "step": 17,
                    "tooltip": "【0.5.0 锚窗帧数】从锚段取尾部多少帧进 refs（5/22/39…同续接网格）。\n"
                               "5 = 一个 token，token 成本最低，身份/色档信息基本够用（默认）。\n"
                               "⚠ 取尾要过 latent 相位校验：锚段长度与锚窗不凑巧时会报错并提示改窗。\n"
                               "锚块每步都随采样骑乘，越大越贵。",
                }),
            },
        }

    RETURN_TYPES = ("CONDITIONING", "STRING", "INT")
    RETURN_NAMES = ("conditioning", "report", "trim_frames")
    FUNCTION = "apply"
    CATEGORY = CATEGORY
    DESCRIPTION = (
        "latent 桥续接（零重编码）：上一段尾段按原样钉进本段，画面声音都接着走。\n"
        "手把手：第 1 段 stage_index=0 跑一遍 → 第 2 段把桥和落盘的 stage_index 都改成 1 → 换 prompt 再跑。"
    )

    def apply(self, conditioning, latent, trim_frames=22, context_latent=None,
              audio_frames=0, run_id="", stage_index=0,
              anchor_latent=None, anchor_stage=-1, anchor_frames=5):
        bad = CORE.self_check()
        if bad:
            raise RuntimeError("H3 Relay 网格自检失败：\n    " + "\n    ".join(bad))
        CONTRACT.enforce()   # 上游 ComfyUI 改了 H3 网格 → 在这里拒绝，而不是产出坏片子

        idx = int(stage_index)
        auto_note = ""
        if context_latent is None and (run_id or "").strip():
            if idx >= 1:
                path = _stage_path(run_id, idx - 1)
                context_latent = CORE.load_av_latent(path)
                auto_note = "自动读上一段 ← %s" % path
                print("[H3 Relay] " + auto_note)
            else:
                auto_note = "stage_index=0（第 1 段）→ 直通不续接。"

        # 0.5.0 外观锚：没接锚 latent 但填了锚段号 → 从 run_id 目录自动读
        anchor_note = ""
        a_idx = int(anchor_stage)
        if anchor_latent is None and a_idx >= 0 and (run_id or "").strip():
            if a_idx == idx:
                raise RuntimeError(
                    "anchor_stage=%d 与本段 stage_index 相同——外观锚必须是**更早**的"
                    "已落盘段（通常是 0，即第 1 段）。" % a_idx)
            try:
                anchor_latent = CORE.load_av_latent(_stage_path(run_id, a_idx))
                anchor_note = "自动读外观锚（第 %d 段）← %s" % (
                    a_idx + 1, _stage_path(run_id, a_idx))
                print("[H3 Relay] " + anchor_note)
            except FileNotFoundError:
                raise RuntimeError(
                    "anchor_stage=%d 表示用第 %d 段做外观锚，但它的落盘文件不存在：\n"
                    "    %s\n先把锚段跑完（并确认「续接 Latent 存」写过它），或把 "
                    "anchor_stage 改回 -1。" % (a_idx, a_idx + 1, _stage_path(run_id, a_idx)))
        elif anchor_latent is None and a_idx >= 0 and not (run_id or "").strip():
            raise RuntimeError(
                "anchor_stage=%d 需要 run_id 才能自动读锚段文件（与续接读段同规则）。"
                "填上 run_id，或把 anchor_stage 改回 -1。" % a_idx)

        # 说清了是第 N 段（N≥2）却拿不到上一段 —— 绝不能悄悄降级成"独立段"：
        # 那样工作流会一路绿灯跑完，产出的却是没有续接的哑剧式接缝。
        # 这是本包唯一一处"用户忘填"会导致静默坏片的路径，故硬拦。
        if context_latent is None and idx >= 1:
            raise RuntimeError(
                "stage_index=%d 表示本段是第 %d 段，但没有可续接的上一段 latent：\n"
                "    · context_latent 没接线，且\n"
                "    · run_id 是空的（或只有空白）\n"
                "再跑下去会「静默直通」—— 产出的是独立段而不是续接段，"
                "但界面与日志都显示成功。\n"
                "    第 2 段起请把 run_id 填成与「续接 Latent 存」完全一致的名字；\n"
                "    若这确实是独立段，把 stage_index 改回 0。"
                % (idx, idx + 1)
            )

        if context_latent is None:
            msg = "[H3 Relay] 无 context_latent → 直通（独立段，不续接）。" + (
                (" " + auto_note) if auto_note else "")
            print(msg)
            return (conditioning, msg, 0)   # 第 3 路 = 裁帧数；直通不裁

        plan = CORE.plan_relay(
            latent,
            context_latent,
            trim_frames=int(trim_frames),
            audio_frames=int(audio_frames) or None,
            anchor_latent=anchor_latent,
            anchor_frames=int(anchor_frames),
        )
        out = CORE.apply_relay(conditioning, plan)

        lines = ["[H3 Relay] latent 桥续接：" + plan.summary()]
        if auto_note:
            lines.append("    " + auto_note)
        if anchor_note:
            lines.append("    " + anchor_note)
        if anchor_latent is not None and context_latent is None:
            lines.append("    ⚠ 外观锚接了但没有上一段可续——直通段忽略锚（锚随续接注入）。")
        lines.append("    本段 " + CORE.describe_latent(latent))
        lines.append("    上段 " + CORE.describe_latent(context_latent))
        for n in plan.notes:
            lines.append("    注记：" + n)
        report = "\n".join(lines)
        print(report)
        return (out, report, int(plan.trim))


class H3RelayTrimAV:
    """裁掉续接段头部的重叠帧（视频 + 音频同裁，A/V 不失步）。

    为什么必须裁：钉住区的前 ``trim_frames`` 帧是模型对上一段尾部的**重生成**
    （实测与原帧逐帧 MAE ≈ 6/255），属于过渡产物。不裁就拼，接缝处会看到
    约 0.9 秒的重播。

    口径与作者一致：裁 **decode 之后的像素帧**（不是裁 latent）——
    latent 的帧跨度按 token 在序列里的相位（k%5）决定，砍头会让相位错位。

    为什么还要多裁几帧（沉降）：钉住区之后模型还会先**复现**上一段若干帧，
    然后才切到本段 prompt；切换点**逐段不同**，可能落在钉住区之外。
    `settle_frames=0`（0.5.0 起默认）时**一帧沉降都不裁** —— 实测「裁沉降」才是缝处
    跳帧的源头（裁 0 帧跳 0.020「几乎无感」／裁 8 帧 0.044／裁 16 帧 0.055），
    留下的只是**清晰度的渐变**。想自动量出切换点并裁掉：填 `-1`。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE", {
                    "tooltip": "【接法】从本段的画面解码节点（VAEDecode）拉线过来。\n"
                               "作用：收到的是「含重复开头」的完整画面，本节点把重复部分裁掉。",
                }),
                "trim_frames": ("INT", {
                    "default": 0, "min": 0, "max": 362, "step": 1,
                    "tooltip": "【不用填！】从「续接 Latent 桥」的第 3 路输出（trim_frames）拉线过来，全自动：\n"
                               "  · 第 1 段桥直通 → 自动 0（不裁）\n"
                               "  · 第 2 段起 → 自动 = 钉住帧数（如 22）\n"
                               "自己手填反而容易和桥对不上。",
                }),
                "fps": ("FLOAT", {
                    "default": 24.0, "min": 1.0, "max": 120.0, "step": 0.001,
                    "tooltip": "【不用动】H3 固定 24 帧/秒，音频按这个换算着一起裁。",
                }),
            },
            "optional": {
                "audio": ("AUDIO", {
                    "tooltip": "【务必接线】从音频解码节点（VAEDecodeAudio）拉线过来。\n"
                               "画面和声音按同一帧数一起裁，保证音画不串位。不接的话声音会比画面长出一截。",
                }),
                # ⚠ 新 widget 必须追加在**最后一个**：widgets_values 按位置对应，
                #    插在中间会让旧工作流里它后面的取值整体错位（见 CHANGES 0.2.1）。
                "settle_frames": ("INT", {
                    "default": 0, "min": -1, "max": 36, "step": 1,
                    "tooltip": "【保持 0】裁不裁「沉降区」——钉住区之后，模型还会先「复现」上一段几帧\n"
                               "才切到本段画面。这几帧略糊，但**裁它们才是缝处跳帧的源头**。\n"
                               "🔴 实测（同一条段，只改裁量）：\n"
                               "     裁 0 帧 → 跳帧 0.020（**几乎无感**）\n"
                               "     裁 8 帧 → 0.044（能看到跳）\n"
                               "     裁16 帧 → 0.055（明显跳）\n"
                               "   留下的那几帧只是**清晰度的渐变**（先略糊、再恢复），\n"
                               "   人眼对这种渐变的容忍度极高 → **拿渐变换突变不划算**，故默认不裁。\n"
                               "  ·  0（默认）= 不裁沉降：只裁钉住区（上段尾的复现，免费）→ 缝处无跳\n"
                               "  · -1 = 自动：按本段实际画面量出该裁几帧（治糊，但**会引入跳帧**，须目检）\n"
                               "  ·  N = 固定多裁 N 帧（想各段等长时用：全片填同一个数）\n"
                               "日志里「裁首 X 帧 = 钉住 Y + 沉降 Z」就是它的结果。",
                }),
                # ⚠ 继续追加在**最后**（同上铁律）：缝帧重影，2026-09-15 新增。
                #    🔴 默认 0（关）：数值上能压平最大单帧跳，但**观感实测更差**——
                #    总位移守恒（只 −7%）、异常帧数 1→2（一跳变两跳 = 卡两下），
                #    且重影帧本身是「鬼影」（内容不属于任何一段）。详见 relay_core 注释。
                "seam_ghost": ("INT", {"advanced": True, 
                    "default": 0, "min": 0, "max": 6, "step": 1,
                    "tooltip": "【保持 0】缝帧重影（极短交叉溶）：把裁后首帧换成"
                               "「上段末帧 ⊕ 本段首帧」的加权混合。\n"
                               "⚠ **默认关**：实测它只把「一跳」拆成「两跳」，"
                               "总位移没减（−7%）、异常帧数翻倍（1→2）→ 观感是「卡两下」，"
                               "比单帧瞬跳更明显，且混合帧本身是个可见鬼影。\n"
                               "  · 0（默认）= 关。**要消除跳帧，请减少 settle 裁切量**"
                               "（裁切才是跳的源头），或在画质域修复糊区。\n"
                               "  · >0 = 开启（仅在确知本段有收益时用，需目检确认）。",
                }),
                "seam_ghost_alpha": ("FLOAT", {"advanced": True, 
                    "default": 0.5, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "仅重影档用：上段末帧的权重（0.5 = 对半）。默认档下无效。",
                }),
                # ⚠ 继续追加在**最后**（同上铁律）：画质域修复，2026-09-16 新增。
                #    背景：settle_frames 默认改 0（不裁沉降）后，成片段头会保留几帧「重绘糊」。
                #    裁它 → 引入跳帧；不裁 → 留糊。**第三条路 = 画质域修**（不裁、不动时间轴）。
                "settle_sharpen": ("FLOAT", {"advanced": True, 
                    "default": 0.0, "min": 0.0, "max": 1.5, "step": 0.05,
                    "tooltip": "【先保持 0，按需开】糊区锐化（画质域修复）：对裁后**开头若干帧**\n"
                               "做 unsharp mask，强度从缝端最强线性衰减到 0。\n"
                               "  · 0（默认）= 关。\n"
                               "  · 0.4–1.0 = 推荐区间（先试 0.6，再目检有没有 halo/噪点）。\n"
                               "**帧数守恒、不动音频、零采样开销**——不改变时间轴，故不会引入跳帧。\n"
                               "⚠ 诚实边界：锐化只能恢复**对比度**，不能恢复**已丢失的真实细节**。\n"
                               "   对「结构还在、只是软」的重绘糊有效；细节彻底丢了就救不回来。",
                }),
                "settle_sharpen_frames": ("INT", {"advanced": True, 
                    "default": 24, "min": 0, "max": 64, "step": 1,
                    "tooltip": "糊区锐化作用帧数（从裁后首帧起，强度线性衰减到 0）。\n"
                               "默认 24 帧 ≈ 1 秒；实测糊区约 16–20 帧内恢复到基准，故 24 有余量。",
                }),
                # ⚠ 继续追加在**最后**（同上铁律）：低频残差传递，2026-09-16 新增。
                #    借鉴 ComfyUI_MiniMaxH3_Director 的 `match_export_opening_grade`
                #    （`_lowfreq_appearance_pull`）：只吸收上段的低频色档/布光，保留本段细节。
                "lowfreq_pull": ("FLOAT", {"advanced": True, 
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【按需开，默认 0】低频残差传递：把裁后**开头若干帧**的低频\n"
                               "（色档/亮度/布光）拉向**上段末帧**，但**保留本段自己的细节与姿态**。\n"
                               "算法：out = src + w·(blur(guide) − blur(src))，纯加性。\n"
                               "  · 0（默认）= 关\n"
                               "  · 0.7 = 上游同款参数（实测缝点阶跃 0.0399→0.0110）\n"
                               "  · 1.0 = 更强（实测 →0.0011，比后处理 seam_grade 还好 5×）\n"
                               "**关键优势**：只动低频 ⇒ **不复制上段姿态轮廓 ⇒ 无重影**；\n"
                               "   而「全 RGB 混合」（交叉溶/重影）实测会把锐度砍掉 49%（画面花）。\n"
                               "帧数守恒、不动音频、零采样开销。",
                }),
                "lowfreq_frames": ("INT", {"advanced": True, 
                    "default": 12, "min": 0, "max": 48, "step": 1,
                    "tooltip": "低频对齐作用帧数（从裁后首帧起，权重线性衰减到 0）。\n"
                               "上游同款用 12 帧；实测缝区影响就在前 12 帧内。",
                }),
                "lowfreq_blur": ("INT", {"advanced": True, 
                    "default": 64, "min": 8, "max": 128, "step": 8,
                    "tooltip": "低频尺度（盒式模糊核）。越大越只对齐大尺度色档/布光；\n"
                               "上游用 64。实测 32/64 差异很小（阶跃 0.0011 vs 0.0024）。",
                }),
                # ⚠ 继续追加在**最后**（同上铁律）：后处理层补全，2026-09-16。
                #    以下四个都是**画质域修复**：帧数守恒、不动音频、不动时间轴
                #    ⇒ 结构上不可能引入跳帧。默认全 0（关），旧行为逐位不变。
                "deconv_strength": ("FLOAT", {"advanced": True, 
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【按需开，默认 0】段头**反卷积去模糊**（Wiener 逆滤波）。\n"
                               "与「糊区锐化」的区别：锐化只是**放大高频**（噪声一起放大），\n"
                               "反卷积是按假设的 PSF **逆推原始信号**，理论上能真正还原细节。\n"
                               "0=关；0.5~1.0 = 混合比。糊得越重，radius 要越大。",
                }),
                "deconv_radius": ("FLOAT", {"advanced": True, 
                    "default": 1.5, "min": 0.5, "max": 6.0, "step": 0.5,
                    "tooltip": "反卷积假设的模糊半径（像素）。段头糊得越重越大；\n"
                               "太大易出振铃（此时把 deconv_strength 降下来）。",
                }),
                "detail_borrow": ("FLOAT", {"advanced": True, 
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【按需开，默认 0】**段体高频迁移**：段头保留自己的低频（内容/构图），\n"
                               "高频换成段体的高频结构（同一场景/光照下 ⇒ 不会引入异质内容）。\n"
                               "与「低频残差」互补：那个补低频，这个补高频。\n"
                               "⚠ 若段头与段体内容差异大（人物位移大），会带出纹理错位。",
                }),
                "detail_blur": ("INT", {"advanced": True, 
                    "default": 9, "min": 3, "max": 64, "step": 2,
                    "tooltip": "高频迁移的分界尺度（盒式模糊核）：越大则被搬走的高频越粗。",
                }),
                "hist_match": ("FLOAT", {"advanced": True, 
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【按需开，默认 0】**直方图匹配**：把段头的色阶分布对齐到段体。\n"
                               "比「低频残差」更强 —— 那个只对齐**均值**，这个对齐**整条分布**\n"
                               "（亮部/暗部的比例也一致）。0=关；1=完全对齐。",
                }),
                "wb_match": ("FLOAT", {"advanced": True, 
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【按需开，默认 0】**灰世界白平衡校正**：把段头的 R:G:B 比例（色温/色调）\n"
                               "对齐到段体。与「低频残差」正交 —— 那个管亮度总量，这个管色温。\n"
                               "对症「色温滑档」型漂移。增益限幅 ±25% 防偏色。",
                }),
                # 🔴 2026-09-17 新增（RESEARCH_seam_frontier §2 方向二）：**跨段**统计匹配。
                #   与上面三个的区别：hist_match / wb_match / deconv / detail_borrow 都是
                #   「段头 ↔ **本段段体**」（段内）；本组是「段头 ↔ **上段末帧**」（**段间**），
                #   治的正是成片缝上那个亮度阶跃（copy 桥实测 0.0402）。
                #   ⚠ 铁律：新 widget 一律**追加在 optional 末位**（旧工作流取值不前移）。
                "match_prev": ("FLOAT", {"advanced": True, 
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【按需开，默认 0】**跨段统计匹配**：把段头的色档/曝光分布\n"
                               "（逐通道均值 + 标准差）对齐到**上段末帧**——即缝的另一侧。\n"
                               "与「低频残差」的区别：那个只做低频**加性**（一阶）；\n"
                               "这个补上**二阶（对比度）+ 逐通道色度**（Reinhard 式）。\n"
                               "**只对齐统计量，不复制姿态 ⇒ 无重影。**建议从 0.5 起试。",
                }),
                "match_prev_frames": ("INT", {"advanced": True, 
                    "default": 12, "min": 1, "max": 96, "step": 1,
                    "tooltip": "【配合 match_prev 用】作用帧数：从裁后首帧起算，\n"
                               "权重从 match_prev 线性衰减到 0（缝端最强 → 尾端不动）。",
                }),
                "match_prev_gain_max": ("FLOAT", {"advanced": True, 
                    "default": 1.15, "min": 1.0, "max": 2.0, "step": 0.05,
                    "tooltip": "【护栏，一般不用动】逐通道对比度增益上限（σ目标/σ源 的截断）。\n"
                               "调大 = 允许更猛的对比度对齐，但可能把已通过的内容改坏。",
                }),
                "match_prev_offset_max": ("FLOAT", {"advanced": True, 
                    "default": 0.06, "min": 0.0, "max": 0.3, "step": 0.01,
                    "tooltip": "【护栏，一般不用动】逐通道亮度/色度偏移上限（μ目标−μ源 的截断）。\n"
                               "调大 = 允许更大的色档修正，但过大易见「整段换色」。",
                }),
            },
        }

    RETURN_TYPES = ("IMAGE", "AUDIO", "STRING", "IMAGE")
    RETURN_NAMES = ("images", "audio", "report", "prev_tail")
    FUNCTION = "trim"
    CATEGORY = CATEGORY
    DESCRIPTION = (
        "裁掉续接段头部的重叠帧，并自动把钉住区之后那段「复现帧」一并裁掉"
        "（默认 settle_frames=0，只裁钉住区）；视频与音频同裁，避免重播与音画失步。"
    )

    def trim(self, images, trim_frames=0, fps=24.0, audio=None, settle_frames=0,
             seam_ghost=0, seam_ghost_alpha=0.5,
             settle_sharpen=0.0, settle_sharpen_frames=24,
             lowfreq_pull=0.0, lowfreq_frames=12, lowfreq_blur=64,
             deconv_strength=0.0, deconv_radius=1.5,
             detail_borrow=0.0, detail_blur=9,
             hist_match=0.0, wb_match=0.0,
             match_prev=0.0, match_prev_frames=12,
             match_prev_gain_max=1.15, match_prev_offset_max=0.06):
        CONTRACT.enforce()   # 裁帧算术同样依赖上游网格，先过契约
        # 服务端防线：widget 的 min=1.0 只挡 UI，API 提交 fps=0/NaN 会一路除到底
        try:
            fps = float(fps)
        except (TypeError, ValueError):
            fps = float("nan")
        if not math.isfinite(fps) or fps <= 0:
            raise RuntimeError(
                "fps 必须是 (0, +∞) 内的有限正数，得到 %r。\n"
                "    H3 固定 24 帧/秒——把「裁重叠」的 fps 改回 24 即可（音频按它换算着一起裁）。"
                % (fps,)
            )
        pin = int(trim_frames)
        before = int(images.shape[0])
        if pin <= 0:
            msg = "[H3 Relay] 裁 0 帧 → 不裁（独立段或纯首段）。"
            print(msg)
            return (images, audio, msg, images[:1])

        # 沉降帧：钉住区之后模型还会先复现上一段若干帧才切到本段 prompt。
        # 切换点是**逐段不同的量**，所以默认让它自己量（-1），而不是让用户猜。
        want = int(settle_frames)
        bj_note = ""
        if want < 0:
            settle, jump, base = CORE.detect_settle(images, pin)
            why = ("自动检测：切换信号 %.1f / 基准 %.1f（帧差突变/锐度塌陷/色档收敛）" % (jump, base)) if settle \
                else ("自动检测：未检出切换点（帧差 %.1f / 基线 %.1f，无突变亦无锐度塌陷）" % (jump, base))
            # 🔴 2026-09-15 新增：**边界跳帧观测**。沉降公式在「跳变正好落在钉住区边界」
            #   （j == pin-1）时返回 0 —— 语义没错，但**掩盖了「边界本身是断的」**。
            #   故单独量出来报给用户：**裁切治不了跳变，但至少要看得见。**
            bj, d_edge, d_base = CORE.boundary_jump_ratio(images, pin)
            if bj > CORE.BOUNDARY_JUMP_WARN:
                bj_note = ("\n           ⚠ **边界跳帧 %.1f×**（钉住区末帧→首帧新内容：帧差 %.4f / 段内基线 %.4f）"
                           "—— 这是模型接续处的断点；**多裁只是把断点往后挪，治不了它**。"
                           % (bj, d_edge, d_base))
            # 多通道观测剖面：**节点实测的 raw decode 数据**，成片里看不到
            # （H.264 编码会改变锐度/色阶基准 → 离线用成片反推必错）。
            # GG 2026-09-15：「不只是锐度，色阶、明暗等都需要」——沉降检测本就三路，只报一路=只开三分之一窗。
            prof = CORE.observation_profile(images, pin)
            if prof["sharp"]:
                n_sh = 18
                bj_note += (
                    "\n           观测剖面（钉住区后逐帧，节点实测 raw decode）\n"
                    "             锐度÷基准 : %s%s\n"
                    "             亮度      : %s%s\n"
                    "             色阶 R/G/B: %s / %s / %s\n"
                    "             帧差      : %s%s\n"
                    "             基准：锐度 %.6f；体区色档 %.4f / %.4f / %.4f"
                    % (" ".join("%.2f" % v for v in prof["sharp"][:n_sh]),
                       " …" if len(prof["sharp"]) > n_sh else "",
                       " ".join("%.4f" % v for v in prof["luma"][:12]),
                       " …" if len(prof["luma"]) > 12 else "",
                       " ".join("%.4f" % v for v in prof["rgb"][0][:10]),
                       " ".join("%.4f" % v for v in prof["rgb"][1][:10]),
                       " ".join("%.4f" % v for v in prof["rgb"][2][:10]),
                       " ".join("%.4f" % v for v in prof["diff"][:18]),
                       " …" if len(prof["diff"]) > n_sh else "",
                       prof["ref_sharp"], prof["ref_rgb"][0], prof["ref_rgb"][1], prof["ref_rgb"][2]))
            # 裁量→跳跃曲线：成片缝 = raw[pin-1] → raw[pin+settle]（相隔 settle+1 帧），
            # **裁得越多、跳得越大**（GG：裁切=时间跳跃=跳切）。裁之前就把它算出来供权衡。
            curve = CORE.trim_jump_curve(images, pin)
            if curve:
                bj_note += ("\n           裁量→跳跃曲线（settle : 归一跳跃）：%s"
                            % "  ".join("%d:%.1f×" % (s, r) for s, r in curve[:13]))
        else:
            settle, why = want, "手动指定"
        if pin + settle >= before:
            settle = max(0, before - 1 - pin)
            why += "（已夹到本段长度上限）"

        n = pin + settle
        out = CORE.trim_head_frames(images, n)
        # 🔴 2026-09-15 新增：**缝帧重影**（极短交叉溶，GG 认可的解法）。
        #   裁后首帧换成「上段末帧 ⊕ 本段首帧」的混合 → 把缝处一跳**拆成两个半跳跨两格**，
        #   一闪而过 → 体感约等于一镜到底。**帧数守恒、不动音频、零采样开销。**
        #   上段末帧 = `images[pin-1]`（钉住区最后一帧 = 上段尾的复现）——节点手里就有，无需额外输入。
        # 🔴 2026-09-16 新增：后处理层补全（P3/P5/P6/P7）—— 画质域修复，不碰时间轴。
        #   执行顺序：先对齐色调（直方图/白平衡），再补高频（反卷积/段体迁移），最后锐化收口。
        post_note = ""
        if float(hist_match) > 0.0:
            out = CORE.match_hist_head_to_body(out, int(settle_sharpen_frames),
                                               float(hist_match), body_start=40)
            post_note += "\n           ✦ **直方图匹配**：段头色阶分布对齐段体（强度 %.2f）。" % float(hist_match)
        if float(wb_match) > 0.0:
            out = CORE.match_white_balance(out, int(settle_sharpen_frames),
                                           float(wb_match), body_start=40)
            post_note += "\n           ✦ **白平衡校正**：段头 R:G:B 比例对齐段体（强度 %.2f）。" % float(wb_match)
        if float(deconv_strength) > 0.0:
            out = CORE.deconv_head_zone(out, int(settle_sharpen_frames),
                                        float(deconv_strength), float(deconv_radius))
            post_note += ("\n           ✦ **反卷积去模糊**：段头 Wiener 逆滤波（强度 %.2f / 半径 %.1f）。"
                          % (float(deconv_strength), float(deconv_radius)))
        if float(detail_borrow) > 0.0:
            out = CORE.borrow_detail_from_body(out, int(settle_sharpen_frames),
                                               float(detail_borrow), int(detail_blur), body_start=40)
            post_note += ("\n           ✦ **段体高频迁移**：段头高频换用段体结构（强度 %.2f / 尺度 %d）。"
                          % (float(detail_borrow), int(detail_blur)))

        # 🔴 2026-09-16 新增：**低频残差传递**（借鉴 Director 的段间引导低频对齐）。
        #   只吸收上段末帧的低频色档/布光，**保留本段细节与姿态** ⇒ 无重影。
        #   与「全 RGB 混合」有本质区别：后者实测把作用区锐度砍掉 49%（画面花）。
        lowfreq_note = ""
        # 🔴 2026-09-17 新增（RESEARCH_seam_frontier §2 方向二）：**跨段统计匹配**。
        #   作用域 = 段头 ↔ **上段末帧**（缝的另一侧），治成片缝上的亮度阶跃。
        #   与下面 lowfreq_pull 的区别：那个只做低频**加性**（一阶矩）；本组补**二阶矩 + 逐通道色度**。
        #   ⚠ 两者**作用域重叠**（都是段头↔上段末帧）⇒ 建议二选一，同时开需自行确认不过修。
        #   ⚠ 必须放在 lowfreq_pull **之前**：先对齐分布，再修低频残差。
        if float(match_prev) > 0.0 and pin >= 1:
            out = CORE.match_prev_stats(out, images[pin - 1], int(match_prev_frames),
                                        float(match_prev), float(match_prev_gain_max),
                                        float(match_prev_offset_max))
            post_note += ("\n           ✦ **跨段统计匹配**：开头 %d 帧的色档/曝光分布对齐上段末帧"
                          "（强度 %.2f / 增益上限 %.2f / 偏移上限 %.3f）——只对齐统计量，不复制姿态 ⇒ 无重影。"
                          % (int(match_prev_frames), float(match_prev), float(match_prev_gain_max),
                             float(match_prev_offset_max)))

        if float(lowfreq_pull) > 0.0 and pin >= 1:
            out = CORE.lowfreq_pull(out, images[pin - 1], int(lowfreq_frames),
                                    float(lowfreq_pull), int(lowfreq_blur))
            lowfreq_note = (
                "\n           ✦ **低频残差传递**：开头 %d 帧对齐上段末帧的低频"
                "（强度 %.2f / 尺度 %d）——只动低频，不复制姿态 ⇒ 无重影。"
                % (int(lowfreq_frames), float(lowfreq_pull), int(lowfreq_blur)))

        # 🔴 2026-09-16 新增：**画质域修复**——糊区锐化（GG 定方向：先试零 GPU 传统锐化）。
        #   默认 settle_frames=0（不裁沉降）后，成片段头保留几帧「重绘糊」；
        #   裁它 → 跳帧（裁 16 帧跳 0.055）；不裁 → 留糊。
        #   **第三条路 = 画质域修**：不裁、不动时间轴，只提升糊区高频（故不可能引入跳帧）。
        sharpen_note = ""
        if float(settle_sharpen) > 0.0 and int(out.shape[0]) > 0:
            out = CORE.sharpen_head_zone(out, int(settle_sharpen_frames), float(settle_sharpen))
            sharpen_note = (
                "\n           ✦ **糊区锐化**：开头 %d 帧 unsharp（强度 %.2f，缝端最强 → 尾端 0）"
                "——不裁、不动时间轴，故不引入跳帧。"
                % (int(settle_sharpen_frames), float(settle_sharpen)))
        # 🔴 2026-09-16 重做：改用 `seam_crossfade`（两侧都是**连续运动序列**）。
        #   旧实现把「上段末帧」**复制 N 次**去混合 → k>1 时内容冻结（重复帧）、
        #   k=1 时只是"一跳拆两跳"（异常帧数 1→2）——**素材用错了**，故观感更差。
        #   正解：上段**末尾 k 帧** 对 本段**开头 k 帧** 逐帧渐变（t 从 1/(k+1) 递进到 k/(k+1)）。
        #   `seam_crossfade` 自带裁切（cut = pin + settle），故它直接产出裁后序列。
        ghost_note = ""
        if int(seam_ghost) > 0 and pin >= 1:
            out = CORE.seam_crossfade(images, n, pin, int(seam_ghost))
            ghost_note = ("\n           ✦ **极短交叉溶 %d 帧**（上段末尾 %d 帧 ⊕ 本段开头 %d 帧，"
                          "权重按 1/%d 逐帧递进）——两侧都是连续运动序列，帧数守恒、不动音频。"
                          % (int(seam_ghost), int(seam_ghost), int(seam_ghost), int(seam_ghost) + 1))
        audio_out = CORE.trim_audio_head(audio, n, float(fps)) if audio is not None else None
        after = int(out.shape[0])
        line = ("[H3 Relay] 裁首 %d 帧 = 钉住 %d + 沉降 %d ｜ %s\n"
                "           画面 %d → %d 帧（%.3fs → %.3fs）"
                % (n, pin, settle, why, before, after, before / float(fps), after / float(fps))) + bj_note + lowfreq_note + post_note + sharpen_note + ghost_note
        if audio is not None:
            line += "；音频 %d → %d 采样点" % (
                int(audio["waveform"].shape[-1]), int(audio_out["waveform"].shape[-1]))
        else:
            line += "；⚠ 未接 audio，画面裁了但音频没裁 → 可能音画不同步"
        print(line)
        # 接缝自检：裁后起点若仍有突变，说明沉降量不够
        check = CORE.describe_head_jump(out)
        print(check)
        # 🔴 2026-09-17 新增第 4 路输出 `prev_tail`（**追加在末位**，旧工作流不受影响）：
        #   = 钉住区最后一帧 = `images[pin-1]`（节点手里本来就有，无需额外输入）。
        #   ⚠ **口径（2026-09-19 补正）**：它是否等于"上一段的末帧"取决于走哪条桥——
        #     拷贝桥下前 pin 帧是逐位拷贝的上段尾 ⇒ 就是上段末帧；
        #     Latent 桥（cond）下前 pin 帧是本段重画的 ⇒ 只是近似（代理误差 0.006，
        #     比要修的缝阶跃 0.0007 还大 8 倍）。故 cond 路线上别拿它当参照去对齐。
        #   用途：喂给 `H3RelayPost` 的 `guide`，让跨段统计匹配 / 低频残差传递有"缝的另一侧"可对齐。
        tail = images[pin - 1: pin] if pin >= 1 else images[:1]
        return (out, audio_out, line + "\n" + check, tail)


class H3RelayChain:
    """🔗 续接连跑（Chain）—— 纯控制节点，不在执行路径上。

    前端按钮（web/relay_kit_chain.js）会找到同一张图里的
    「续接 Latent 桥 + 续接 Latent 存」，自动推进它们的 stage_index 并排队：

        ▶ Run      按当前段号跑一次（不满意可重跑，覆盖同段号文件）
        ✔ Approve  段号 +1（桥和落盘同步改），排队跑下一段
        ⏩ 连跑     按 segments 自动循环：跑完一段 → 段号+1 → 再跑（0 = 无限）
        ⏹ Stop     当前采样跑完后停止推进
        ↺ Reset    段号归 0，从第 1 段重来

    使用前提：把 Chain、桥、落盘三个节点拉进**同一个分组框**（框选 → 右键 → 添加分组），
    否则按钮找不到要推进的节点。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "segments": ("INT", {
                    "default": 5, "min": 0, "max": 9999, "step": 1,
                    "tooltip": "【连跑几段】点「⏩ 连跑」时生效：5 = 连跑 5 段后自动停；0 = 不停，直到点 ⏹ Stop。\n"
                               "其他按钮不受它影响。",
                }),
            },
            "optional": {
                # 只用于**显示**：前端把状态写进这一格。
                # 必须是**最后一个** widget —— 这样旧工作流里少这一格时只走默认值，
                # 不会让它前面的取值错位（见 CHANGES 0.2.1 的槽位错位说明）。
                "status": ("STRING", {
                    "default": "",
                    "tooltip": "【不用填，自动显示】前端把连跑状态写在这里：\n"
                               "当前段号 / 已排队 / ⚠ 分组没放对 / ⚠ 排队失败…\n"
                               "点了按钮没反应时，先看这一格说了什么。",
                }),
            },
        }

    RETURN_TYPES = ()
    FUNCTION = "noop"
    CATEGORY = CATEGORY
    DESCRIPTION = (
        "自动连跑控制器：配合桥 + 落盘使用。\n"
        "第一步：把 Chain、桥、落盘放进同一个分组框。\n"
        "第二步：桥和落盘 stage_index 填 0，点 ▶ Run 拍第 1 段。\n"
        "第三步：点 ⏩ 连跑（或每段点 ✔ Approve），段号自动推进，不用再手改。\n"
        "状态显示在 status 格子里（点了没反应就看它）。"
    )

    def noop(self, segments=5, **kwargs):
        # **kwargs 吞掉 status 这类只用于显示的输入
        return {}


class H3RelayCopyBridge:
    """0.4.0 拷贝桥：上一段尾部 AV latent **逐位拷贝**进本段初始 latent + 噪声掩码。

    与 H3RelayMotionContext（conditioning 钉帧）二选一，不可同图串联：
      · Latent 桥（钉帧）：模型重绘上一段尾段 → 有复现漂移/发糊风险（0.3.x 实测），
        观测端沉降检测兜底；
      · 拷贝桥（本节点）：``mask_mode="hard"`` 时钉住区不重绘（掩码 0 区每步被钉回
        拷贝 latent），复现伪影这一类从机制上消失；掩码消费走 ComfyUI 原生 H3 契约与
        SelfLift 的 noise_mask 支持——**不绑定任何特定采样器**。
        ⚠️ ``mask_mode="taper"`` **不钉住**（每帧留 seam_min~100% 重绘自由度），只作对照实验档。
        ``mask_mode="ramp"``（0.4.3）是"软证据"档：连续掩码按原生 H3 契约就是逐 token 的
        sigma 标签（sigma_row = m·sigma_video），远端硬钉、缝端以 ramp_top 强度 harmonize，
        每一步都被 (1−m) 锚回拷贝尾——治硬接缝的色档/曝光台阶，全程有锚（与 taper 相反）。
    输出 INT = 应裁帧数（=拷贝跨度），接 H3RelayTrimAV 的 trim_frames；
    TrimAV 的 settle_frames 保持 0（0.5.0 起默认不裁沉降），观测端继续守接管帧。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "latent": ("LATENT", {
                    "tooltip": "本段初始 AV latent（CSGlideCastCS / EmptyH3LatentAV 的 latent 输出）。\n"
                               "上一段尾部会逐位写进它的开头，并附噪声掩码。",
                }),
                "context_latent": ("LATENT", {
                    "tooltip": "上一段的完整 AV latent（🔗 H3 续接 Latent 读）。\n"
                               "第 1 段（stage 0）不要接本节点——没有上一段可拷。",
                }),
                "context_frames": ("INT", {
                    "default": 22, "min": 5, "max": 124, "step": 17,
                    "tooltip": "拷贝窗口帧数，只认 5+17k 网格（5/22/39/56/73/90/107/124）。\n"
                               "须小于本段帧数（前缀必须给新内容留位置）。",
                }),
            },
            "optional": {
                "mask_mode": (["hard", "taper", "ramp", "blend"], {
                    "default": "hard",
                    "tooltip": "掩码语义：每步输出 = 模型生成 * m + 上段尾 * (1-m)。**m=0 才钉住，m=1 是重绘。**\n"
                               "hard = 全窗 m=0（钉住区零重绘，默认，真续接用这个）；\n"
                               "taper = 头部 m=1.0（**完全重绘**）线性降到缝端 seam_min\n"
                               "        —— ⚠ **钉住区实际上没有被钉住**，只是给模型一个软提示；\n"
                               "        seam_min=0.3 意味着连缝端都留 30% 重绘。\n"
                               "        仅用于「渐进接管」对照实验；期望真续接请保持 hard。\n"
                               "ramp = 0.4.3 噪声斜坡（软证据）：远端 m=0 硬钉 → 缝端线性升到\n"
                               "        ramp_top；原生契约把连续 m 当逐 token sigma 标签\n"
                               "        （sigma_row = m * sigma_video），缝侧轻度 harmonize、\n"
                               "        每一步仍被 (1-m) 锚回拷贝尾——治硬接缝的色档/曝光台阶。\n"
                               "        与 taper 相反：ramp 全程有锚，taper 头部无锚。",
                }),
                "taper_tokens": ("INT", {"advanced": True, 
                    "default": 4, "min": 1, "max": 12, "step": 1,
                    "tooltip": "仅 taper 模式：缝端前多少个 token 参与线性过渡。",
                }),
                "seam_min": ("FLOAT", {"advanced": True, 
                    "default": 0.10, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "仅 taper 模式：缝端掩码下限（m 值）。\n"
                               "0 = 缝端完全硬锁；>0 表示缝端仍留同等比例的重绘自由度\n"
                               "（0.3 即缝端 30% 重绘）。注意它只管缝端——头部恒为 1.0 全重绘。",
                }),
                "pin_audio": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "把上一段音频尾也拷进本段音频 latent 开头（采样上下文用）。\n"
                               "掩码只做视频流；可见的声画拼接仍归「裁重叠」与组装层。",
                }),
                "ramp_top": ("FLOAT", {"advanced": True, 
                    "default": 0.25, "min": 0.0, "max": 0.95, "step": 0.05,
                    "tooltip": "仅 ramp 模式：缝端最大 m（= 该 token 参与去噪的 sigma 比例）。\n"
                               "0 = 退化为 hard；0.25 默认 = 缝端 25% 强度 harmonize；\n"
                               ">0.5 起锚定明显变弱，接近 taper 的行为，慎用。",
                }),
                "ramp_tokens": ("INT", {"advanced": True, 
                    "default": 0, "min": 0, "max": 12, "step": 1,
                    "tooltip": "仅 ramp 模式：参与斜坡的缝端 token 数；0 = 整个拷贝窗铺开。\n"
                               "小值（如 2~3）= 「只松缝、锁运动」的窄斜坡。",
                }),
                "anchor_latent": ("LATENT", {
                    "tooltip": "【0.5.0 统计纠偏基准，可选】全局锚段（通常第 1 段）的 AV latent。\n"
                               "接上后：拷贝前缀的逐通道均值/方差被拉向锚段（Reinhard 矩匹配）。\n"
                               "纠偏只作用于被裁掉的钉住前缀——不碰上一段成片；但采样时每步钉回的\n"
                               "就是这份已复位色档的上下文 → 本段新内容跟着回到全局色档。\n"
                               "治的是「色档/曝光随接力次数漂移」。用「续接 Latent 读」+ 指定\n"
                               "explicit_path 读第 1 段的 stage 文件即可。",
                }),
                "anchor_blend": ("FLOAT", {
                    "default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【0.5.0 纠偏强度】0~1：拷贝前缀被拉向锚段统计量的比例。\n"
                               "1 = 全量对齐（默认，锚段就是审美基准时用）；0.5 = 一半。\n"
                               "锚段与本段允许有意的风格差异时调低。不接 anchor_latent 时无效。",
                }),
                # 🔴 2026-09-17 新增（RESEARCH_seam_frontier §4 方向四）：**重叠区双向融合**。
                #   与 ramp 同语义、不同曲线：ramp 线性升，blend 用**窗形**升（两端导数 0）。
                #   依据 FlowLong / Unified Long Video Inpainting 的滑窗 Hamming 混合
                #   （arXiv:2511.03272）：重叠区由两个独立估计加权平均，权重取窗函数。
                #   ⚠ 本节点**无存量 UI 工作流**引用（已核），故新 widget 可紧邻同族项放。
                "blend_top": ("FLOAT", {"advanced": True, 
                    "default": 0.50, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【仅 blend 模式】缝端**模型占比**上限（对应 ramp 的 ramp_top）。\n"
                               "0 = 退化成 hard；越大越信任本段自己的预测。建议 0.5 起试。",
                }),
                "blend_tokens": ("INT", {"advanced": True, 
                    "default": 0, "min": 0, "max": 12, "step": 1,
                    "tooltip": "【仅 blend 模式】参与融合的缝端 token 数；0 = 整个拷贝窗铺开。\n"
                               "小值（2~3）= 「只融缝、锁运动」。",
                }),
                "blend_shape": (list(CORE.BLEND_SHAPES), {"advanced": True, 
                    "default": CORE.BLEND_SHAPE_DEFAULT,
                    "tooltip": "【仅 blend 模式】窗形：\n"
                               " · smoothstep = x²(3−2x)，多项式 S 曲线（默认）\n"
                               " · hann = (1−cos πx)/2，余弦 S 曲线\n"
                               "两者都是**两端导数为 0**的单调升 ⇒ 与窗外衔接无折角、过渡更柔。",
                }),
            },
        }

    RETURN_TYPES = ("LATENT", "STRING", "INT")
    RETURN_NAMES = ("latent", "report", "trim_frames")
    FUNCTION = "bridge"
    CATEGORY = CATEGORY
    DESCRIPTION = (
        "把上一段尾部 AV latent 逐位拷进本段开头并附噪声掩码（钉住区不重绘），\n"
        "消除「复现发糊/漂移」这一类接缝伪影。输出 trim_frames 接「裁重叠」。\n"
        "⚠️ 与「Latent 桥」（conditioning 钉帧）二选一，不可同图串联。"
    )

    def bridge(self, latent, context_latent, context_frames,
               mask_mode="hard", taper_tokens=4, seam_min=0.10, pin_audio=True,
               ramp_top=0.25, ramp_tokens=0,
               blend_top=CORE.SEAM_BLEND_TOP, blend_tokens=0,
               blend_shape=CORE.BLEND_SHAPE_DEFAULT,
               anchor_latent=None, anchor_blend=1.0):
        CONTRACT.enforce()
        out, covered, report = CORE.build_continue_latent(
            latent, context_latent, int(context_frames),
            mask_mode=mask_mode, taper=int(taper_tokens),
            seam_min=float(seam_min), pin_audio=bool(pin_audio),
            ramp_top=float(ramp_top), ramp_tokens=int(ramp_tokens),
            blend_top=float(blend_top), blend_tokens=int(blend_tokens),
            blend_shape=str(blend_shape),
            anchor_latent=anchor_latent, anchor_blend=float(anchor_blend),
        )
        print(report, flush=True)
        return (out, report, covered)


class H3RelayPost:
    """🔗 H3 续接后处理（Post）—— 画质域修复，**不碰时间轴**。

    为什么单独一个节点（2026-09-17 拆出）：
      原来这 15 个旋钮全塞在 `H3RelayTrimAV` 里，而 ComfyUI 的 UI 工作流把
      ``widgets_values`` **按位置**存 ⇒ 「新 widget 只追加末位」成了硬约束 ⇒
      TrimAV 被顶到 22 个 widget、UI 不可读、且**每加一个后处理件都要往末尾塞**。
      拆出来之后：**TrimAV 冻结不再长**，本节点从零开始、widget 可逻辑分组，
      **以后的画质域功能一律加在这里**。

    分工：
      · ``H3RelayTrimAV`` = 时间轴（裁重叠 / 沉降 / 重影）+ 交接 ``prev_tail``
      · ``H3RelayPost``  = 画质域（色档对齐 / 高频补 / 锐化）——**不动帧数、不动音频**

    ``guide`` 接 ``H3RelayTrimAV`` 的第 4 路输出 ``prev_tail``（**是上段末帧还是它的近似，
    取决于走哪条桥**，见下面 ``guide`` 槽位的说明）：
    只有 ``match_prev`` 与 ``lowfreq_pull`` 需要它；不接时这两项自动失效（其余照常）。

    🔴 2026-09-19 口径补正：``prev_tail = images[pin-1]`` 是「本段自己解码序列的钉住区末帧」。
      · **拷贝桥**：前 pin 帧是**逐位拷贝**的上段尾 ⇒ 它**就是**上段末帧；
      · **Latent 桥（cond）**：前 pin 帧是**本段模型重画**的 ⇒ 只是**近似**（实测代理误差
        0.006，比要修的缝阶跃 0.0007 还大 8 倍）⇒ **对错参照，越对齐越糟**。
      故 cond 路线上 ``match_prev`` 保持 0；本节点的用武之地是拷贝桥。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE", {
                    "tooltip": "【接法】接 `🔗 H3 续接裁重叠` 的 images 输出（**裁后**的画面）。\n"
                               "本节点只改画质、**不改帧数、不动音频**。",
                }),
            },
            "optional": {
                "guide": ("IMAGE", {
                    "tooltip": "【接法】接 `🔗 H3 续接裁重叠` 的第 4 路输出 `prev_tail`\n"
                               "（= 钉住区最后一帧，即缝的另一侧）。\n"
                               "只有「跨段统计匹配」与「低频残差传递」需要它；不接时这两项自动跳过。\n"
                               "🔴 **它究竟是不是「上一段的末帧」，取决于走哪条桥**：\n"
                               "  · **拷贝桥** = 前 pin 帧是逐位拷贝的上段尾 ⇒ `prev_tail` **就是**上段末帧；\n"
                               "  · **Latent 桥（cond）** = 前 pin 帧是**本段重画**的 ⇒ 只是**近似**\n"
                               "    （实测代理误差 0.006 > 要修的缝阶跃 0.0007）⇒ 对错参照，越对齐越糟。",
                }),
                # —— 组 1：跨段色档/曝光对齐（缝的另一侧 = guide）——
                "match_prev": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【组 1 · 跨段】**跨段统计匹配**：把段头的色档/曝光分布\n"
                               "（逐通道均值 + 标准差）对齐到 guide。\n"
                               "Reinhard 式一阶+二阶矩；**只对齐统计量，不复制姿态 ⇒ 无重影**。\n"
                               "目标：压「成片缝上的亮度阶跃」。需要 guide。\n"
                               "🔴 **2026-09-17 真渲染实测：在 Latent 桥（cond）路线上请保持 0**——\n"
                               "该路线缝本身已到 0.0007（可感阈值以下），而 guide（= 裁重叠的\n"
                               "`prev_tail`）在 cond 下只是**近似**参照（本段自己重画的第 pin-1 帧，\n"
                               "实测代理误差 0.006 > 要修的阶跃 0.0007）⇒ 对齐它反而把首帧推离真参照\n"
                               "（阶跃 0.0007 → 0.0039，旧口径更差 → 0.0088）。真正的用武之地是\n"
                               "**拷贝桥**（那里 prev_tail 才是真·上段末帧）。详见 CHANGES 0.5.0。",
                }),
                "match_prev_frames": ("INT", {"advanced": True, 
                    "default": 12, "min": 1, "max": 96, "step": 1,
                    "tooltip": "【配合 match_prev】作用帧数：从首帧起算，权重线性衰减到 0。",
                }),
                "match_prev_gain_max": ("FLOAT", {"advanced": True, 
                    "default": 1.15, "min": 1.0, "max": 2.0, "step": 0.05,
                    "tooltip": "【护栏】逐通道对比度增益上限（防把已通过的内容改坏）。",
                }),
                "match_prev_offset_max": ("FLOAT", {"advanced": True, 
                    "default": 0.06, "min": 0.0, "max": 0.3, "step": 0.01,
                    "tooltip": "【护栏】逐通道亮度/色度偏移上限（防「整段换色」）。",
                }),
                "lowfreq_pull": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【组 1 · 跨段】**低频残差传递**：只把段头**低频**色档对齐 guide，\n"
                               "不动细节与姿态 ⇒ 无重影。与 match_prev 的区别：\n"
                               "本项只做低频**加性**（一阶）；match_prev 补二阶+逐通道色度。\n"
                               "⚠ 两者**作用域重叠**，建议二选一。需要 guide。\n"
                               "⚠ guide 在 Latent 桥（cond）下只是**近似**的缝另一侧（见 guide 槽位说明），\n"
                               "本项只动低频、比 match_prev 温和，但同样别拿它去对错参照。",
                }),
                "lowfreq_frames": ("INT", {"advanced": True, "default": 12, "min": 1, "max": 96, "step": 1,
                                           "tooltip": "【配合 lowfreq_pull】作用帧数（权重线性衰减到 0）。"}),
                "lowfreq_blur": ("INT", {"advanced": True, "default": 64, "min": 4, "max": 256, "step": 4,
                                         "tooltip": "【配合 lowfreq_pull】低频尺度（盒式模糊核，上游用 64）。"}),
                # —— 组 2/3 共用：段头作用帧数 ——（2026-09-19 参数收口）
                #   问题：组 2（色档对齐）与组 3（高频补）的**四个**强度旋钮，
                #   作用区长度一直借的是组 4 的 `settle_sharpen_frames`
                #   —— 名字是「糊区锐化帧数」，用户在 UI 上根本看不出这四个
                #   「作用多少帧」受哪个旋钮管。
                #   本节点 0.5.0 期内**无任何存量图**（已全扫 ComfyUI user 目录为零命中），
                #   故这一处**就地插入**而不是追加末位；默认值与 `settle_sharpen_frames`
                #   相同（24）⇒ 没显式设过值的图行为逐位不变。
                "head_zone_frames": ("INT", {
                    "default": 24, "min": 1, "max": 96, "step": 1,
                    "tooltip": "【组 2+3 共用】段头**作用区长度**（帧）：从裁后首帧起算，\n"
                               "「直方图匹配 / 白平衡 / 反卷积 / 段体高频迁移」四项\n"
                               "都按这个帧数作用（强度各自带线性衰减，尾端归 0）。\n"
                               "⚠ 组 4 的「糊区锐化」**不看这个**，它用下面的 `settle_sharpen_frames`。",
                }),
                # —— 组 2：段内色档对齐（基准 = 本段段体）——
                "hist_match": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【组 2 · 段内】**直方图匹配**：把段头的色阶**分布**对齐到本段段体。\n"
                               "治「段头↔段体」的色阶漂移（段**内**，不需要 guide）。\n"
                               "作用帧数 =「组 2+3 共用」的 `head_zone_frames`（默认 24）。",
                }),
                "wb_match": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【组 2 · 段内】**灰世界白平衡**：把段头 R:G:B 比例对齐段体。\n"
                               "与直方图匹配正交 —— 那个管亮度总量，这个管色温。\n"
                               "作用帧数 =「组 2+3 共用」的 `head_zone_frames`（默认 24）。",
                }),
                # —— 组 3：高频补（治糊）——
                "deconv_strength": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【组 3 · 补高频】**反卷积去模糊**（Wiener）：提升段头高频。\n"
                               "太大易出振铃（此时把强度降下来）。\n"
                               "作用帧数 =「组 2+3 共用」的 `head_zone_frames`（默认 24）。",
                }),
                "deconv_radius": ("FLOAT", {"advanced": True, "default": 1.5, "min": 0.5, "max": 4.0, "step": 0.1,
                                            "tooltip": "【配合 deconv_strength】模糊核半径。"}),
                "detail_borrow": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【组 3 · 补高频】**段体高频迁移**：把段头的高频换成段体的结构。\n"
                               "比反卷积更「像真的」，但可能与段头内容不符。\n"
                               "作用帧数 =「组 2+3 共用」的 `head_zone_frames`（默认 24）。",
                }),
                "detail_blur": ("INT", {"advanced": True, "default": 9, "min": 3, "max": 64, "step": 2,
                                        "tooltip": "【配合 detail_borrow】高频分离尺度。"}),
                # —— 组 4：收口 ——
                "settle_sharpen": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 1.5, "step": 0.05,
                    "tooltip": "【组 4 · 收口】**糊区锐化**（unsharp）：对开头 N 帧做**渐变**锐化。\n"
                               "不裁、不动时间轴 ⇒ 从原理上不可能引入跳帧。建议 0.4–1.0。",
                }),
                "settle_sharpen_frames": ("INT", {"advanced": True, "default": 24, "min": 1, "max": 96, "step": 1,
                                                  "tooltip": "【配合 settle_sharpen】作用帧数（渐变衰减到 0）。\n"
                                                             "⚠ **只管「糊区锐化」这一项**——组 2/组 3 的作用帧数\n"
                                                             "是上面「组 2+3 共用」的 `head_zone_frames`（0.5.0 期内曾共用本项，现已分开）。"}),
                # ⚠ 继续追加在**最后**（同上铁律）。
                "match_prev_stats_frames": ("INT", {"advanced": True, 
                    "default": 1, "min": 0, "max": 24, "step": 1,
                    "tooltip": "【配合 match_prev】**统计量取几帧**——本条最容易写错：\n"
                               "  · 1（默认）= 只取**紧贴缝的那一帧** ⇔ 与 guide（单帧）同口径 ⇒\n"
                               "    修正量恰是「缝上的阶跃」，首帧被拉向 guide 而**不会越过它**；\n"
                               "  · 0 = 旧口径（对整个作用区聚合）⇒ 段头**内部有亮度梯度**时，\n"
                               "    聚合均值被后续帧拉低，修正量变成「段头平均 vs guide」的差，\n"
                               "    首帧被**推过 guide**、缝上凭空多出一个阶跃（实测 ×12.6）。仅对照用。\n"
                               "  · >1 = 用前 N 帧聚合（介于两者之间）。",
                }),
            },
        }

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("images", "report")
    FUNCTION = "apply"
    CATEGORY = CATEGORY
    DESCRIPTION = (
        "续接段的画质域后处理（不碰时间轴、不动帧数、不动音频）："
        "跨段统计匹配 / 低频残差 / 段内直方图与白平衡 / 反卷积与段体高频迁移 / 糊区锐化。"
        "全部默认关闭；guide 接裁重叠节点的 prev_tail 才有「缝的另一侧」可对齐。"
    )

    def apply(self, images, guide=None,
              match_prev=0.0, match_prev_frames=12,
              match_prev_gain_max=1.15, match_prev_offset_max=0.06,
              lowfreq_pull=0.0, lowfreq_frames=12, lowfreq_blur=64,
              head_zone_frames=24,
              hist_match=0.0, wb_match=0.0,
              deconv_strength=0.0, deconv_radius=1.5,
              detail_borrow=0.0, detail_blur=9,
              settle_sharpen=0.0, settle_sharpen_frames=24,
              match_prev_stats_frames=CORE.MATCH_PREV_STATS_FRAMES):
        n0 = int(images.shape[0])
        out = images
        notes = []

        # 执行顺序：先对齐色调（跨段 → 段内），再补高频，最后锐化收口。
        if float(match_prev) > 0.0:
            if guide is None:
                notes.append("跨段统计匹配：**跳过**（未接 guide）")
            else:
                out = CORE.match_prev_stats(out, guide, int(match_prev_frames),
                                            float(match_prev), float(match_prev_gain_max),
                                            float(match_prev_offset_max),
                                            int(match_prev_stats_frames))
                notes.append("跨段统计匹配 %.2f（%d 帧，对齐上段末帧；统计量取 %s）"
                             % (float(match_prev), int(match_prev_frames),
                                "紧贴缝那帧" if int(match_prev_stats_frames) == 1
                                else "整个作用区聚合（旧口径，仅对照）"
                                if int(match_prev_stats_frames) <= 0
                                else "前 %d 帧" % int(match_prev_stats_frames)))
        if float(lowfreq_pull) > 0.0:
            if guide is None:
                notes.append("低频残差传递：**跳过**（未接 guide）")
            else:
                out = CORE.lowfreq_pull(out, guide, int(lowfreq_frames),
                                        float(lowfreq_pull), int(lowfreq_blur))
                notes.append("低频残差传递 %.2f（%d 帧 / 尺度 %d）"
                             % (float(lowfreq_pull), int(lowfreq_frames), int(lowfreq_blur)))
        if float(hist_match) > 0.0:
            out = CORE.match_hist_head_to_body(out, int(head_zone_frames),
                                               float(hist_match), body_start=40)
            notes.append("直方图匹配 %.2f（段头↔段体，前 %d 帧）"
                         % (float(hist_match), int(head_zone_frames)))
        if float(wb_match) > 0.0:
            out = CORE.match_white_balance(out, int(head_zone_frames),
                                           float(wb_match), body_start=40)
            notes.append("白平衡校正 %.2f（段头↔段体，前 %d 帧）"
                         % (float(wb_match), int(head_zone_frames)))
        if float(deconv_strength) > 0.0:
            out = CORE.deconv_head_zone(out, int(head_zone_frames),
                                        float(deconv_strength), float(deconv_radius))
            notes.append("反卷积去模糊 %.2f / 半径 %.1f（前 %d 帧）"
                         % (float(deconv_strength), float(deconv_radius), int(head_zone_frames)))
        if float(detail_borrow) > 0.0:
            out = CORE.borrow_detail_from_body(out, int(head_zone_frames),
                                               float(detail_borrow), int(detail_blur), body_start=40)
            notes.append("段体高频迁移 %.2f / 尺度 %d（前 %d 帧）"
                         % (float(detail_borrow), int(detail_blur), int(head_zone_frames)))
        if float(settle_sharpen) > 0.0:
            out = CORE.sharpen_head_zone(out, int(settle_sharpen_frames), float(settle_sharpen))
            notes.append("糊区锐化 %.2f（%d 帧）" % (float(settle_sharpen), int(settle_sharpen_frames)))

        assert int(out.shape[0]) == n0, "后处理必须帧数守恒"
        line = "[H3 Relay] 后处理：%s" % ("；".join(notes) if notes else "全部关闭（直通）")
        print(line)
        return (out, line)


class H3RelayAudioSeam:
    """音频缝：把上一段的环境声补进本段头部（**长度守恒**，零 A/V 位移）。

    治的现象：段首音频带 ~32ms 解码 priming 近静默 + 生成瞬态，裁重叠把这段放到
    裁剪点上 ⇒ 成片缝处先"抽一下"再起乐（实测缝起 20ms 掉 12–13 dB，邻域最静 −72 dB）。

    做法：把本段头部 ``patch_seconds`` 秒整段换成**上一段的最静窗环境声**
    （stationary 噪声，拼接不可闻），到 ``patch_seconds`` 处交叉淡变回本段自身音频，
    之后**逐位不动**。帧数/音频长度都不变 ⇒ 不消耗时间轴。

    ⚠️ 真 crossfade（三角窗叠化）做不到"单段内长度守恒"——它要裁掉**上一段**的尾部，
    而本节点只能改本段。所以 crossfade 仍归组装层；等长路线用本节点。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "audio": ("AUDIO", {
                    "tooltip": "【接法】从「续接裁重叠」的 audio 输出口拉线过来。\n"
                               "（第 1 段没有裁重叠节点时，直接接音频解码 VAEDecodeAudio。）",
                }),
                "run_id": ("STRING", {
                    "default": "relay",
                    "tooltip": "【填什么】这部片子的名字。\n"
                               "⚠ 必须和「续接 Latent 存/桥」上的 run_id 一字不差——\n"
                               "本节点要靠它找到上一段落盘的音频当床源。",
                }),
                "stage_index": ("INT", {
                    "default": 0, "min": 0, "max": 9999, "step": 1,
                    "tooltip": "【填什么】本段是全片的第几段。第 1 段填 0，第 2 段填 1…\n"
                               "第 1 段无缝可补，会直接直通（但仍会把音频落盘，供第 2 段当床源）。",
                }),
            },
            "optional": {
                "patch_seconds": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 4.0, "step": 0.05,
                    "tooltip": "【组 1 · 音频缝】**头部补丁长度（秒）**。0 = 关（默认，逐位直通）。\n"
                               "建议 2.0：段首 2 秒的生成瞬态整段换成上一段的环境声。\n"
                               "⚠ 前提是段首本来就不该有台词（前 1.2s 无词是产线纪律）——\n"
                               "补丁会把这 N 秒的内容换成环境声。",
                }),
                "tile_seconds": ("FLOAT", {"advanced": True, 
                    "default": 0.0, "min": 0.0, "max": 4.0, "step": 0.05,
                    "tooltip": "【组 2 · 床环铺】床源改取这么长的瓦片，自叠化环铺满补丁长度。\n"
                               "0 = 整窗直取最静 N 秒（默认）。\n"
                               "上一段最长干净环境窗短于补丁长度时用它（建议 1.2）。",
                }),
                "fade_seconds": ("FLOAT", {
                    "default": 0.25, "min": 0.0, "max": 0.5, "step": 0.01,
                    "tooltip": "【组 3】补丁边界（第 N 秒处）的交叉淡变宽度。\n"
                               "0 = 硬切（会有可闻的接点）；0.25 是产线实测值。",
                }),
                "bed_stage": ("INT", {"advanced": True, 
                    "default": 0, "min": 0, "max": 9999, "step": 1,
                    "tooltip": "【填什么】用第几段的音频当床源（默认 0 = 第 1 段）。\n"
                               "同场景环境声是 stationary 的，取第 1 段最稳；\n"
                               "⚠ 必须小于本段段号（床源得是已经渲染完的段）。",
                }),
                "note": ("STRING", {"advanced": True, 
                    "default": "",
                    "tooltip": "【可留空】备注，存进落盘文件的元数据里方便事后分辨版本。",
                }),
            },
        }

    RETURN_TYPES = ("AUDIO", "STRING")
    RETURN_NAMES = ("audio", "report")
    FUNCTION = "seam"
    CATEGORY = CATEGORY
    # 落盘本身就是产物：没有下游消费也要执行（否则第 1 段的床源文件永远不生成）
    OUTPUT_NODE = True
    DESCRIPTION = ("把上一段的环境声补进本段头部，去掉裁切点上的解码静默与生成瞬态。"
                   "长度守恒、零 A/V 位移；默认关（0 = 逐位直通）。")

    def seam(self, audio, run_id, stage_index, patch_seconds=0.0, tile_seconds=0.0,
             fade_seconds=0.25, bed_stage=0, note=""):
        me = _audio_stage_path(run_id, int(stage_index))
        idx = int(stage_index)
        patch = float(patch_seconds or 0.0)
        if idx <= 0 or patch <= 0.0:
            CORE.save_audio(audio, me, note=note)
            why = "第 1 段无缝可补" if idx <= 0 else "补丁关（patch_seconds=0）"
            line = ("[H3 Relay] 音频缝：%s → 直通｜本段音频已落盘（供后段当床源）：%s"
                    % (why, me))
            print(line)
            return (audio, line)

        b_idx = int(bed_stage)
        if b_idx >= idx:
            raise RuntimeError(
                "床源段号（%d）必须小于本段段号（%d）——床源得是**已经渲染完**的那一段。\n"
                "    想用上一段当床源就填 0（或用默认值）。" % (b_idx, idx)
            )
        bed_path = _audio_stage_path(run_id, b_idx)
        if not os.path.isfile(bed_path):
            raise FileNotFoundError(
                "床源音频不存在：%s\n"
                "    第 %d 段还没跑过（本节点会顺手把每段音频落盘）。\n"
                "    先按段号顺序跑一次第 %d 段，再来跑本段。" % (bed_path, b_idx, b_idx)
            )
        out, rep = CORE.audio_seam_patch(audio, CORE.load_audio(bed_path),
                                         patch, float(tile_seconds or 0.0),
                                         float(fade_seconds))
        CORE.save_audio(out, me, note=note)
        line = rep + "｜已落盘（供后段当床源）：%s" % me
        print(line)
        return (out, line)


NODE_CLASS_MAPPINGS = {
    "H3RelayLatentSave": H3RelayLatentSave,
    "H3RelayLatentLoad": H3RelayLatentLoad,
    "H3RelayMotionContext": H3RelayMotionContext,
    "H3RelayCopyBridge": H3RelayCopyBridge,
    "H3RelayTrimAV": H3RelayTrimAV,
    "H3RelayPost": H3RelayPost,
    "H3RelayAudioSeam": H3RelayAudioSeam,
    "H3RelayChain": H3RelayChain,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "H3RelayLatentSave": "🔗 H3 续接 Latent 存",
    "H3RelayLatentLoad": "🔗 H3 续接 Latent 读",
    "H3RelayMotionContext": "🔗 H3 续接 Latent 桥",
    "H3RelayCopyBridge": "🔗 H3 续接 拷贝桥",
    "H3RelayTrimAV": "🔗 H3 续接裁重叠",
    "H3RelayPost": "🔗 H3 续接后处理 Post",
    "H3RelayAudioSeam": "🔗 H3 续接音频缝",
    "H3RelayChain": "🔗 H3 续接连跑 Chain",
}
