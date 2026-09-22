# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Relay-Kit contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""ComfyUI-H3-Relay-Kit

MiniMax-H3 多段续接的 **latent 桥**（零重编码）。

取作者链路的续接方式：上一段的 AV latent 直接切尾段，
以 minimax_keyframes（按位置排位）+ minimax_refs（音频）注入本段 conditioning，
不经过 mp4 解码与 VAE 重编码。

本包不依赖任何第三方 H3 节点包：所用协议（minimax_keyframes / minimax_refs /
resolved_frame_index）由 ComfyUI 原生消费。

节点：
    🔗 H3 续接 Latent 存    H3RelayLatentSave     本段 latent 落盘（下一段的接力棒）
    🔗 H3 续接 Latent 读    H3RelayLatentLoad     手动连线时读上一段（桥自动取源时不用）
    🔗 H3 续接 拷贝桥       H3RelayCopyBridge     上一段尾段逐位拷进本段 latent + 噪声掩码（钉住区不重绘）
                                                  **0.6.0 起 = 复合桥**：接上 conditioning 时同时追加钉帧（管取景），
                                                  与 latent 钉住窗（管运动）并联；不接则行为与旧版完全一致
    🔗 H3 续接裁重叠        H3RelayTrimAV         裁掉钉住区重播帧（音画同裁+接缝自检），并交出 prev_tail
    🔗 H3 续接后处理 Post   H3RelayPost           画质域后处理（跨段统计匹配/低频残差/直方图+白平衡/反卷积/高频迁移/糊区锐化/自适应糊区补偿；互斥组 + 逐层可审计）
    🔗 H3 续接音频缝        H3RelayAudioSeam      音频域：上一段环境声补本段头（床声电平对齐，长度守恒，零 A/V 位移）+ joined 整片拼接（J-cut 时间轴守恒）
    🔗 H3 续接连跑 Chain    H3RelayChain          UI 自动连跑（段号自动推进 + 自动排队 + **词分发** + **自动拼接成片**）

后端路由（不是节点；由 Chain 的按钮触发）：
    POST /h3relay/concat    把已跑完的 N 段拼成一条成片（画面流拷贝无损 + 音频逐段对齐 + 断言）
                            音轨档由 Chain 的 `audio_out` 决定（默认 AAC 256k / 可 192k / `pcm_lossless` 母版）；
                            每段有「裁重叠」落的 **PCM 边车**就直读它 ⇒ 音频代际 2 → 1（无损档零新增）

手把手（UI 三步跑一条链）：
    1. 桥和落盘的 stage_index 填 0，点 Chain 的 ▶ Run —— 第 1 段落盘
    2. Chain 上填 prompts（`---` 分块，第 k 块喂第 k 段）⇒ 点 ⏩ 连跑，词自动换、段号自动走
    3. 开 auto_concat（或点「🧩 拼成一条」）—— 跑完直接得到一条成片
       （音轨默认 AAC 256k；要母版就把 `audio_out` 换成 `pcm_lossless`）
"""

from .nodes import (
    NODE_CLASS_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS,
)

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

__version__ = "0.6.7"


# ============================================================================
# 后端路由：拼接成片（0.6.7）
# ============================================================================
# 为什么拼接是「包内 route + 画布按钮」而不是一个节点（CONTRIBUTING 铁律一·例外）：
#   拼接的输入是**别的节点写出来的 mp4 文件**，不是张量 —— 节点的连线口拿不到
#   "别人刚才往哪个文件写了什么"，只有发起排队的前端知道（每段 `queuePrompt` 回来的 prompt_id）。
#   于是：前端把 prompt_id 交上来 ⇒ 本路由用 ComfyUI 自己的 history 取回每段的落盘路径
#   ⇒ 调 relay_core 的拼接函数（与节点共用同一套核心代码）。
#   ⚠ 路由只是**入口**，不是"把功能藏在脚本里"：它在包内、由画布按钮触发、结果写回节点 status。
try:                                   # 宿主提供；离线单测环境里没有 server 也能 import 本包
    from server import PromptServer
except Exception:                      # pragma: no cover - 取决于运行环境
    PromptServer = None

if PromptServer is not None:           # pragma: no branch
    import os

    import folder_paths
    from aiohttp import web

    from . import relay_core as CORE

    _MAX_SEG = 200                     # 一次拼接的段数上限（防手抖传个天文数字）
    _HIST_SCAN = 400                   # 回扫 history 的条数上限（别把整个队列复制一遍）
    # 成片音轨档（前端 audio_out → 编码器 + 码率）。**默认 aac 256k**（兼容第一）；
    #   pcm_lossless = 无损母版：音轨不再经过有损编码（代价：**浏览器预览没声音**，
    #   且文件是 4.4 MB/秒级别的 PCM ⇒ 这是给剪辑/归档的档，不是预览档）。
    AUDIO_OUT = {"aac_256k": ("aac", "256k"), "aac_192k": ("aac", "192k"),
                 "pcm_lossless": ("pcm_f32le", "")}
    _DEFAULT_AUDIO_OUT = "aac_256k"
    # PCM 边车候选的「离落盘有多近」排序（越大越近）。链上越靠后的音频节点，
    #   它的输出越可能就是真进 mp4 的那份 ⇒ 优先用它。
    _PCM_RANK = {"H3RelayAudioSeam": 2, "H3RelayTrimAV": 1}

    def _abs_of(p):
        base = folder_paths.get_directory_by_type(p["type"]) or folder_paths.get_output_directory()
        return os.path.join(base, p["subfolder"], p["filename"])

    def _mp4_paths(entry):
        """history 条目 → 该次提交落盘的**视频**绝对路径（type 可能是 output/temp/input）。"""
        return [_abs_of(p) for p in CORE.pick_video_outputs((entry or {}).get("outputs") or {})]

    def _pcm_candidates(entry):
        """本段的 PCM 边车**候选**（可能多个），按「谁更靠近落盘」排序。

        音频链上可能有**多个**本包音频节点各落一份边车（典型 = 「裁重叠」在缝之前、
        「音频缝」在缝之后）。真进 mp4 的是**最后那个** ⇒ 这里把候选按 class_type 排序，
        由 `relay_core` 再用「与 mp4 音频长度最接近」做最终裁决（数据说话，不靠接线假设）。
        """
        got = CORE.pick_pcm_outputs((entry or {}).get("outputs") or {})
        if not got:
            return []
        ct = (((entry or {}).get("prompt") or [None, None, {}])[2]) or {}

        def rank(p):
            try:
                return _PCM_RANK.get(str((ct.get(str(p.get("node"))) or {}).get("class_type")), 0)
            except Exception:                      # noqa: BLE001
                return 0
        return [_abs_of(p) for p in sorted(got, key=rank, reverse=True)]

    def _pick_segments(queue, prompt_ids, chain_id, count):
        """段清单 → ``(ids, entries, note)``。**不猜文件名**：
        给了 prompt_id 就精确取（前端每段排队回来都记着）；没给就回扫 history，
        只认「图里带本 Chain 节点」的提交，取最近 count 次。``note`` 是给用户看的"我找了什么"。
        """
        hist = queue.get_history(max_items=_HIST_SCAN) or {}
        if prompt_ids:
            ids = [p for p in prompt_ids if p in hist]
            note = "按 prompt_id 取：%d/%d 段命中" % (len(ids), len(prompt_ids))
            if len(ids) < len(prompt_ids):
                note += "；%d 段在 history 里查不到（重启过后端？）" % (len(prompt_ids) - len(ids))
            return ids, [hist[i] for i in ids], note
        if count <= 0:
            return [], [], ("没给 prompt_id，且 count ≤ 0 ⇒ 不知道该拼哪几段。"
                            "把 Chain 的 segments 填成正数，或用 ▶/⏩ 跑过一轮。")
        hit = [pid for pid, e in hist.items()
               if any(isinstance(v, dict) and (
                   v.get("class_type") == "H3RelayChain"
                   and (not chain_id or str(k) == str(chain_id)))
                   for k, v in ((e or {}).get("prompt") or [None, None, {}])[2].items())]
        ids = hit[-count:]
        return (ids, [hist[i] for i in ids],
                "回扫 history：图里带本 Chain 的提交共 %d 次，取最近 %d 次" % (len(hit), len(ids))
                if hit else "回扫 history：**没有**找到「图里带本 Chain 节点」的提交（后端重启过？）")

    @PromptServer.instance.routes.post("/h3relay/concat")
    async def h3relay_concat(request):  # pragma: no cover - 需要运行中的宿主
        try:
            data = await request.json()
        except Exception:
            data = {}
        data = data if isinstance(data, dict) else {}
        try:
            count = max(0, min(int(data.get("count") or 0), _MAX_SEG))
        except Exception:
            count = 0
        run_id = str(data.get("run_id") or "").strip()
        name = str(data.get("out_name") or "").strip() or run_id or "h3relay_final"
        name = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in name) or "h3relay_final"

        ids, entries, note = _pick_segments(
            PromptServer.instance.prompt_queue,
            [str(x) for x in (data.get("prompt_ids") or [])][:_MAX_SEG],
            str(data.get("chain_node_id") or ""), count)
        lines = ["[H3 Relay] 拼接请求：%s" % note]
        segs, pcms = [], []
        for pid, entry in zip(ids, entries):
            paths, pcm = _mp4_paths(entry), _pcm_candidates(entry)
            lines.append("  · prompt %s → %d 个视频文件%s%s"
                         % (str(pid)[:8], len(paths),
                            ("（%s）" % paths[0]) if paths else "",
                            ("｜♪ PCM 边车 ×%d" % len(pcm)) if pcm else ""))
            if paths:
                segs.append(paths[0])
                pcms.append(pcm)
        a_codec, a_bitrate = AUDIO_OUT.get(str(data.get("audio_out") or ""),
                                          AUDIO_OUT[_DEFAULT_AUDIO_OUT])
        try:
            crf = int(data.get("video_crf"))
        except Exception:
            crf = 16
        crf = max(0, min(51, crf))
        lines.append("  · 音轨档 %s%s ｜ 画面重编码 crf=%d（流拷贝路用不到）"
                     % (a_codec, "" if not a_bitrate else " @" + a_bitrate, crf))
        out_dir = folder_paths.get_output_directory()
        out_path, n = os.path.join(out_dir, name + ".mp4"), 2
        while os.path.exists(out_path):                 # 不覆盖已有成片，换个序号
            out_path = os.path.join(out_dir, "%s-%d.mp4" % (name, n))
            n += 1
        if not segs:
            lines.append("  🔴 一段视频文件都没找到 ⇒ 没有拼。"
                         "（跑过一轮再来，或把 Chain 的 segments 填成正数。）")
            return web.json_response({"ok": False, "report": "\n".join(lines), "out": "",
                                      "attempted_out": out_path, "searched": note})
        try:
            rep = CORE.assemble_mp4_segments(segs, out_path, on_log=print,
                                             audio_codec=a_codec, audio_bitrate=a_bitrate,
                                             crf=crf, pcm_paths=pcms)
        except Exception as exc:            # noqa: BLE001
            # 拼接内部抛错（缺库 / 文件被占用 / 编码器缺失…）也必须变成画布上看得懂的提示，
            # 不然前端只看到 500、status 格子里什么都没有。
            lines.append("  🔴 拼接过程抛错（%s）：%s" % (type(exc).__name__, exc))
            lines.append("     %s 可能是不完整的半成品，别当成品用。" % out_path)
            return web.json_response({"ok": False, "report": "\n".join(lines), "out": "",
                                      "attempted_out": out_path, "searched": note})
        return web.json_response({"ok": bool(rep.get("ok")),
                                  "report": "\n".join(lines) + "\n" + rep.get("report", ""),
                                  "out": out_path if rep.get("ok") else "",
                                  "attempted_out": out_path, "asserts": rep.get("asserts"),
                                  "searched": note})
