# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""ComfyUI-H3-Latent-Relay

MiniMax-H3 多段续接的 **latent 桥**（零重编码）。

取作者链路的续接方式：上一段的 AV latent 直接切尾段，
以 minimax_keyframes（按位置排位）+ minimax_refs（音频）注入本段 conditioning，
不经过 mp4 解码与 VAE 重编码。

本包不依赖任何第三方 H3 节点包：所用协议（minimax_keyframes / minimax_refs /
resolved_frame_index）由 ComfyUI 原生消费。
唯一例外 = `H3RelayLatentUpscale`（它是 MIT 上游节点 `MinimaxH3LatentUpscaler3D` 的
AV 打包 latent 适配器）：**未装那个包时本包照常加载**，只有该节点在被使用时报可照做的安装指引。

节点：
    🔗 H3 Relay · Latent Save    H3RelayLatentSave     本段 latent 落盘（下一段的接力棒）
    🔗 H3 Relay · Latent Load    H3RelayLatentLoad     手动连线时读上一段（桥自动取源时不用）
    🔗 H3 Relay · Copy Bridge       H3RelayCopyBridge     上一段尾段逐位拷进本段 latent + 噪声掩码（钉住区不重绘）
                                                  **0.6.0 起 = 复合桥**：接上 conditioning 时同时追加钉帧（管取景），
                                                  与 latent 钉住窗（管运动）并联；不接则行为与旧版完全一致
    🔗 H3 Relay · Trim AV        H3RelayTrimAV         裁掉钉住区重播帧（音画同裁+接缝自检），并交出 prev_tail
    🔗 H3 Relay · Post   H3RelayPost           画质域后处理（跨段统计匹配/低频残差/直方图+白平衡/反卷积/高频迁移/糊区锐化/自适应糊区补偿；互斥组 + 逐层可审计）
    🔗 H3 Relay · Audio Seam        H3RelayAudioSeam      音频域：上一段环境声补本段头（床声电平对齐，长度守恒，零 A/V 位移）+ joined 整片拼接（J-cut 时间轴守恒）
    🔗 H3 Relay · Chain    H3RelayChain          UI 自动连跑（段号自动推进 + 自动排队 + **词分发**〔按段号输出第 k 块词〕+ **自动拼接成片**）
    🔍 H3 Relay · Latent Upscale    H3RelayLatentUpscale  画质域：拆 AV 打包 latent → 逐块调学习式 3D 放大器（**零去噪、时间维不动**）→ 回包保留原音频；块数自选、分块==整段

后端路由（不是节点；由 Chain 的按钮触发）：
    POST /h3relay/concat    把已跑完的 N 段拼成一条成片（画面流拷贝无损 + 音频逐段对齐 + 断言）
                            音轨档由 Chain 的 `audio_out` 决定（默认 AAC 256k / 可 192k / `pcm_lossless` 母版）；
                            每段有「Trim AV」落的 **PCM 边车**就直读它 ⇒ 音频代际 2 → 1（无损档零新增）

手把手（UI 三步跑一条链）：
    1. 桥和落盘的 stage_index 填 0，点 Chain 的 ▶ Run —— 第 1 段落盘
    2. Chain 上填 prompts（`---` 分块，第 k 块喂第 k 段）⇒ 点 ⏩ 连跑，词自动换、段号自动走
    3. 开 auto_concat（或点「🧩 拼成一条」）—— 跑完直接得到一条成片
       （音轨默认 AAC 256k；要母版就把 `audio_out` 换成 `pcm_lossless`）
"""

import asyncio
import os as _os

# 🔴 2026-09-25：拼接**串行锁**。路由把 `assemble_mp4_segments` 丢线程池后（见 `/h3relay/concat`），
#   两个并发请求会**同时**进去 ⇒ ① 都走 `while os.path.exists(out_path)` 可能**撞同一个输出名**
#   同时写同一文件；② PyAV/ffmpeg 并发编码。改之前它们在事件循环里被同步调用 ⇒ **天然串行**，
#   丢线程池后这个隐含保证没了 ⇒ 显式补回来。
#   ⚠ 必须是 `asyncio.Lock`：`threading.Lock` 的 `with` 是**同步**的，等锁时会**再次堵住事件循环**。
#   （Python 3.10+ 的 `asyncio.Lock()` 不再绑定 loop，可安全地建在模块级。）
_CONCAT_LOCK = asyncio.Lock()

# ============================================================================
# 节点 API 出口开关：**v3（默认，2026-09-24 起）**｜v1（一行回退）
# ============================================================================
# ⚠️ 两条出口**必须互斥**：宿主加载器是
#     `if hasattr(module,"NODE_CLASS_MAPPINGS") and ... is not None: ... return True`
#     `elif hasattr(module,"comfy_entrypoint"): ...`
#   （ComfyUI/nodes.py:2295-2337）—— V1 分支命中即 return ⇒ 同时导出两者时 **V3 永不生效**。
#   所以 V3 模式下把 NODE_CLASS_MAPPINGS **显式设为 None**（宿主的判据含 "is not None"）。
# 默认 **v3**（2026-09-24 切换）：V3 出口已过 70 项逐字段机检（8 节点 / 116 个 input
# 的顺序·取值·组合项全序与 V1 一致）＋ 2 段真实链验证（362/362 帧守恒 · 流拷贝无损 ·
# PCM 边车被拼接路由取到）。
# ⚠️ 回退到 V1：设 H3RELAY_NODE_API=v1 后重启 —— 两条出口的代码都还在，只是默认换了。
#
# `NODE_API_DEFAULT` 单独留一个常量，给 tools/assert_default_exit.py 断言用：
# `NODE_API` 在 V3 加载失败时会被**回改成 "v1"**（兜底），所以单看 NODE_API 分不清
# 「默认本来就是 v1」与「默认是 v3 但本环境加载不了 V3」—— 这两件事的处置完全不同。
NODE_API_DEFAULT = "v3"
NODE_API = _os.environ.get("H3RELAY_NODE_API", NODE_API_DEFAULT).strip().lower()
# 兜底原因留档（None = 没回退）。给 tools/assert_default_exit.py 打印诊断用。
NODE_API_FALLBACK_REASON = None

if NODE_API == "v3":
    try:
        from .v3.entrypoint import comfy_entrypoint          # noqa: F401
    except Exception as _v3_err:                             # noqa: BLE001
        # V3 出口依赖宿主的 `comfy_api`，而它的传递闭包要宿主自己的整套 Python 依赖
        # （实测缺过 packaging / comfy-aimdo / tqdm，2026-09-24 GitHub Actions 三次实测）。
        # 原则：**一个可选出口不许把整包搞挂** —— 退回 V1 并**大声说明**（不静默降级）。
        NODE_API_FALLBACK_REASON = "%s: %s" % (type(_v3_err).__name__, _v3_err)
        print("[H3 Relay] ⚠️ V3 出口加载失败，已回退到 V1：%s" % NODE_API_FALLBACK_REASON)
        print("[H3 Relay]    修 V3：把宿主 ComfyUI 的 requirements.txt 装上"
              "（V3 要 import comfy_api，其依赖闭包含 packaging / comfy-aimdo / tqdm 等）；"
              "要显式用 V1：设 H3RELAY_NODE_API=v1 后重启")
        NODE_API = "v1"

if NODE_API == "v3":
    NODE_CLASS_MAPPINGS = None
    NODE_DISPLAY_NAME_MAPPINGS = None
    __all__ = ["comfy_entrypoint", "WEB_DIRECTORY"]
else:
    from .nodes import (                                     # noqa: F401
        NODE_CLASS_MAPPINGS,
        NODE_DISPLAY_NAME_MAPPINGS,
    )

    __all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

WEB_DIRECTORY = "./web"

__version__ = "0.6.18"


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

    def _abs_of(p):
        # 优先用节点自报的绝对路径：第三方落盘节点可能存到 ComfyUI output **之外**
        #   （实测：某第三方落盘节点的「自定义保存路径」指到 ComfyUI output 之外，
        #   而它的 subfolder 是空的）⇒ 用 type+subfolder 拼会拼出一个不存在的路径。
        if p.get("abs_path"):
            return str(p["abs_path"])
        base = folder_paths.get_directory_by_type(p["type"]) or folder_paths.get_output_directory()
        return os.path.join(base, p["subfolder"], p["filename"])

    def _mp4_paths(entry):
        """history 条目 → 该次提交落盘的**视频**绝对路径（type 可能是 output/temp/input）。"""
        return [_abs_of(p) for p in CORE.pick_video_outputs((entry or {}).get("outputs") or {})]

    def _pcm_candidates(entry):
        """本段的 PCM 边车候选 → ``(paths, note)``；``paths`` **已按优先级排好**。

        优先级 = **提交图里的数据流深度**（`CORE.graph_downstream_rank`）：谁在下游谁靠前，
        因为它才是真进 mp4 的那份。接线怎么变都跟着变 —— 这是本函数存在的全部理由。

        🔴 为什么不再用 `class_type` 白名单猜顺序（2026-09-30 真实事故）：
          白名单写的是"音频缝在裁重叠之后"，可图里接的是**音频缝 → 裁重叠**（反的）
          ⇒ 更靠前、未裁的那份被优先 ⇒ 段 1 的成片音轨是段 2 的 ⇒ **音画错段**。
          白名单是猜的，图不是。

        **多候选但图读不出来**（提交图缺失/有环）⇒ 返回空 = **拒收全部边车**：
        宁可退到 mp4 解码（音轨多一代 AAC），也绝不配错段。单候选没有歧义 ⇒ 照常给。
        """
        got = CORE.pick_pcm_outputs((entry or {}).get("outputs") or {})
        if not got:
            return [], ""
        if len(got) == 1:                       # 无歧义：图序与它无关
            return [_abs_of(got[0])], ""
        ct = (((entry or {}).get("prompt") or [None, None, {}])[2]) or {}
        rank = CORE.graph_downstream_rank(ct, [p["node"] for p in got])
        if rank is None:
            return [], ("♪ PCM 边车 ×%d 但**读不出提交图的数据流** ⇒ 全部拒收"
                        "（退回 mp4 解码；宁可多一代 AAC，也不配错段）" % len(got))
        return ([_abs_of(p) for p in sorted(got, key=lambda p: rank.get(str(p["node"]), 1))],
                "")

    def _chain_node(entry, chain_id):
        """提交图里本 Chain 的节点 dict（取不到返回 ``None``）。

        用于"没给段记录、只能回扫 history"那条兜底路 —— 光按时间排序会把**重跑过的段**
        算成两段；有了提交图才能读出真段号，也才谈得上"缺了第几段"。
        """
        ct = (((entry or {}).get("prompt") or [None, None, {}])[2]) or {}
        for k, v in ct.items():
            if (isinstance(v, dict) and v.get("class_type") == "H3RelayChain"
                    and (not chain_id or str(k) == str(chain_id))):
                return v
        return None

    def _chain_stage(node):
        """本 Chain 节点的 ``stage_index``；读不出来返回 ``None``（= 段号未知，不瞎猜）。"""
        try:
            return int((node.get("inputs") or {}).get("stage_index"))
        except (TypeError, ValueError):
            return None

    def _normalize_stages(data):
        """请求里的 ``stages`` → ``[(stage, prompt_id, seq)]``（按段号升序）。

        ``stages`` = 前端带**真段号**上报的段记录（0.6.18 起）；老的平铺 ``prompt_ids``
        由调用方兜底。段号是**位置**，不是"第几个非空项" —— 压实正是 2026-09-30
        那次段序错乱（第 2 段的 id 顶到第 1 段槽）的成因。

        同一段号出现多次 ⇒ **后一次覆盖前一次**（与前端 `stageIds[k] = …` 同语义）；
        畸形项（非 dict / 段号不是整数 / 段号为负）直接丢掉 —— 丢掉的项不会占位，
        所以**不会**把后面的段往前挪。
        """
        out = {}
        # ⚠ 先切上限再遍历：请求体是外部输入，10⁶ 条 `stages` 会在切片**之前**被完整走一遍。
        for it in (data.get("stages") or [])[:_MAX_SEG]:
            if not isinstance(it, dict):
                continue
            try:
                stage = int(it.get("stage"))
            except (TypeError, ValueError):
                continue
            if stage < 0:
                continue
            try:
                seq = int(it.get("seq"))
            except (TypeError, ValueError):
                seq = None
            out[stage] = (str(it.get("prompt_id") or "").strip() or None, seq)
        return [(s, p, q) for s, (p, q) in sorted(out.items())]

    def _stage_gaps(stages):
        """段号列表里**区间内缺掉**的段号（1 起算，升序）；无从判 ⇒ 空。

        ⚠ **跨度上限**：段号来自请求，畸形值（如 ``10**9``）会让 `range` 建出天文数字的列表。
        跨度 ≥ `_MAX_SEG`（一次拼接的段数上限）时直接判"无从判" —— 那本来就不可能是同一次拼接的段号。
        """
        have = sorted(s for s in stages if s is not None)
        if not have or len(have) != len(stages) or have[-1] - have[0] >= _MAX_SEG:
            return []
        return [s + 1 for s in range(have[0], have[-1] + 1) if s not in set(have)]

    def _pick_segments(queue, want, chain_id, count):
        """段清单 → ``(pairs, note, missing)``。

        ``want`` = ``[(stage, prompt_id)]``（按段号升序；``prompt_id`` 为空 = 该段没有记录）。
        ``pairs`` = ``[(stage_1based, prompt_id, entry)]``；``missing`` = 缺掉的段号（1 起算）。

        🔴 **不压实**：段号与位置一一对应。旧代码把稀疏的 id 列表压紧（`[p for p in ids if …]`）
        ⇒ 第 2 段的 id 顶到第 1 段槽 ⇒ **拼出来的片段序是错的**（2026-09-30 日志实证）。
        缺哪段就明说哪段，绝不悄悄往前挪。
        """
        hist = queue.get_history(max_items=_HIST_SCAN) or {}
        if want:
            pairs, unresolved = [], []
            for stage, pid in want:
                ent = hist.get(pid) if pid else None
                if ent is None:
                    unresolved.append(stage + 1)
                else:
                    pairs.append((stage + 1, pid, ent))
            missing = sorted(set(_stage_gaps([s for s, _ in want]) + unresolved))
            note = "按段号取：%d/%d 段命中" % (len(pairs), len(want))
            if missing:
                note += "；第 %s 段没有可用的记录（重启过后端？）" % "、".join(map(str, missing))
            return pairs, note, missing
        if count <= 0:
            return [], ("没给段记录，且 count ≤ 0 ⇒ 不知道该拼哪几段。"
                        "把 Chain 的 segments 填成正数，或用 ▶/⏩ 跑过一轮。"), []
        found = []
        for pid, e in hist.items():
            node = _chain_node(e, chain_id)
            if node is not None:
                found.append((_chain_stage(node), pid, e))
        if not found:
            return [], "回扫 history：**没有**找到「图里带本 Chain 节点」的提交（后端重启过？）", []
        # 全都读得出段号才按段排；只要有一个读不出（老提交没有 inputs）就退回 history 顺序
        # （= 执行顺序，对"没记录只能回扫"这条兜底路是最可靠的代理）—— 不混着排。
        if all(s is not None for s, _, _ in found):
            found.sort(key=lambda t: t[0])
        found = found[-count:]
        return ([(s + 1 if s is not None else None, pid, e) for s, pid, e in found],
                "回扫 history：图里带本 Chain 的提交共 %d 次，取最近 %d 次（按段号排）"
                % (len(found), len(found)),
                _stage_gaps([s for s, _, _ in found]))

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

        # 段记录：**优先 `stages`（带真段号，0.6.18 起前端上报）**，退回老的平铺 `prompt_ids`。
        #   `stages` = `[{"stage": k, "prompt_id": "…", "seq": n}]`（`prompt_id` 可为空 = 该段没记录）。
        stages_in = _normalize_stages(data)
        want = [(s, p) for s, p, _ in stages_in][:_MAX_SEG]
        if not want:
            want = [(i, str(x)) for i, x in enumerate(data.get("prompt_ids") or [])][:_MAX_SEG]
        pairs, note, missing = _pick_segments(
            PromptServer.instance.prompt_queue, want,
            str(data.get("chain_node_id") or ""), count)
        lines = ["[H3 Relay] 拼接请求：%s" % note]
        if missing:
            lines.append("  ⚠ **缺第 %s 段**（没有记录 / history 里查不到）⇒ 成片里会是缺段的。"
                         "要补齐请把那几段重跑一遍再拼。" % "、".join(map(str, missing)))
        # 同一批里混了**不同轮**的记录（前端每条记录带 `seq`）⇒ 段与段之间可能不接续，
        # 必须写进报告（交接要求「哪几段来自更早一轮」可见）。
        _seqs = [q for _, _, q in stages_in if q is not None]
        if _seqs:
            _newest = max(_seqs)
            _older = [s + 1 for s, _, q in stages_in if q != _newest]
            if _older:
                lines.append("  ⚠ 第 %s 段的记录来自**更早一轮**（不是最新那轮的产物）⇒ "
                             "段间可能不接续，拼完请目检缝。" % "、".join(map(str, _older)))
        segs, pcms = [], []
        try:
            for stage, pid, entry in pairs:
                paths, (cands, pcm_note) = _mp4_paths(entry), _pcm_candidates(entry)
                lines.append("  · 段 %s（prompt %s）→ %d 个视频文件%s%s%s"
                             % (stage if stage is not None else "?", str(pid)[:8], len(paths),
                                ("（%s）" % paths[0]) if paths else "",
                                ("｜♪ PCM 边车 ×%d" % len(cands)) if cands else "",
                                ("｜" + pcm_note) if pcm_note else ""))
                if paths:
                    segs.append(paths[0])
                    pcms.append(cands)
        except Exception as exc:            # noqa: BLE001
            # 「从 history 里挑段」这层同样**不许把异常甩成 500** —— 画布上什么都看不到，
            # 用户只会以为"点了没反应"（同本文件对拼接编码路的要求）。
            lines.append("  🔴 从 history 解析段文件时出错（%s）：%s"
                         % (type(exc).__name__, exc))
            return web.json_response({"ok": False, "report": "\n".join(lines), "out": "",
                                      "searched": note})
        a_codec, a_bitrate = AUDIO_OUT.get(str(data.get("audio_out") or ""),
                                          AUDIO_OUT[_DEFAULT_AUDIO_OUT])
        try:
            crf = int(data.get("video_crf"))
        except Exception:
            crf = 16
        crf = max(0, min(51, crf))
        lines.append("  · 音轨档 %s%s ｜ 画面重编码 crf=%d（流拷贝路用不到）"
                     % (a_codec, "" if not a_bitrate else " @" + a_bitrate, crf))
        async with _CONCAT_LOCK:
            out_dir = folder_paths.get_output_directory()
            out_path, n = os.path.join(out_dir, name + ".mp4"), 2
            while os.path.exists(out_path):             # 不覆盖已有成片，换个序号
                out_path = os.path.join(out_dir, "%s-%d.mp4" % (name, n))
                n += 1
            if not segs:
                lines.append("  🔴 一段视频文件都没找到 ⇒ 没有拼。"
                             "（跑过一轮再来，或把 Chain 的 segments 填成正数。）")
                return web.json_response({"ok": False, "report": "\n".join(lines), "out": "",
                                          "attempted_out": out_path, "searched": note})
            # 🔴 拼之前先核对**文件真的在盘上**：缺了立刻报清楚，不进编码阶段
            #   （路径发现本身是内存里扫 history + isfile，微秒级；这一步只为"别白等"）。
            _miss = [p for p in segs if not os.path.isfile(p)]
            if _miss:
                lines.append("  🔴 %d 个段文件在盘上不存在（路径来自节点回显，已被删/被移？）⇒ 没有拼："
                             % len(_miss))
                lines += ["     · " + p for p in _miss]
                return web.json_response({"ok": False, "report": "\n".join(lines), "out": "",
                                          "attempted_out": out_path, "missing": _miss,
                                          "searched": note})
            # 🔴 2026-09-25：**必须丢线程池**。本路由是 `async def`（跑在 aiohttp 事件循环里），
            #   而 `assemble_mp4_segments` 是同步的 ffmpeg 编码（几十秒级）——直接调会把事件循环
            #   整个堵住，期间 UI 的**所有**请求（队列轮询 / `/queue` / `/history`）全部无响应
            #   ⇒ 用户看到的就是"点了没反应 / 卡死了"。
            #   实测对照（1.5 s 重活）：同步调用下事件循环被占满 **1.502 s**；
            #   `run_in_executor` 下 0.155 s 照常返回。
            #   ⚠ **不要用 `asyncio.wait_for` 包** —— 它超时能抛，但**挡不住阻塞**（同步调用仍占着循环）。
            try:
                rep = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: CORE.assemble_mp4_segments(
                        segs, out_path, on_log=print, audio_codec=a_codec,
                        audio_bitrate=a_bitrate, crf=crf, pcm_paths=pcms))
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

    # ========================================================================
    # 环境自检（7.4②）：`GET /h3relay/health`
    # ========================================================================
    # 为什么值得有：.github/ISSUE_TEMPLATE/bug_report.md 要用户填「本包版本 / ComfyUI 版本 /
    #   Python+torch / OS」—— 全靠手动翻；本包特有的三项（节点注册数 / 时序契约 / 上游可选依赖）
    #   更是没人会主动查。这里一次给全 ⇒ **把 3–5 轮问答压成一行 curl**。
    # ⚠ 它**查不到"包压根没加载"**（目录嵌套放错 / 没重启）—— 那时本路由也不存在。
    #   那种情况看启动日志的 `[H3 Relay] v… 已加载` 那行：**没有 = 没加载**。
    @PromptServer.instance.routes.get("/h3relay/progress")
    async def h3relay_progress(request):  # pragma: no cover - 需要运行中的宿主
        """连跑进度（断点续跑用）：`?run_id=xxx` ⇒ `{ok, stage_index, segments, updated}`。

        进度由 `H3RelayChain.run()` **每段写一次**（`output/relay_kit/<run_id>/_progress.json`，
        `run_id` 留空则不写 —— 老图零副作用）。
        🔴 记的是「第 `stage_index` 段**已开始**」，**不是已完成** ⇒ 宿主卡死/重启后
        从这一段**重跑**即可，**绝不跳段**（跳段会拼出中间缺一段的成片）。
        """
        import json as _json
        import os as _os
        from .nodes import _progress_path            # 延迟 import：V3 / V1 两种出口下都在

        run_id = (request.query.get("run_id") or "").strip()
        if not run_id:
            return web.json_response({"ok": False, "error": "缺少 run_id 参数"}, status=400)
        try:
            path = _progress_path(run_id)
        except Exception as err:                     # noqa: BLE001
            return web.json_response({"ok": False, "error": "run_id 不合法：%s" % err})
        if not _os.path.isfile(path):
            return web.json_response({
                "ok": False, "run_id": run_id,
                "error": "还没有进度记录（这一段还没开始跑过，或者 Chain 上的 run_id 没填）"})
        try:
            with open(path, encoding="utf-8") as fh:
                data = _json.load(fh)
        except Exception as err:                     # noqa: BLE001
            return web.json_response({"ok": False, "error": "进度文件读不了：%s" % err})
        data["ok"] = True
        data["path"] = path
        return web.json_response(data)

    @PromptServer.instance.routes.get("/h3relay/health")
    async def h3relay_health(request):  # pragma: no cover - 需要运行中的宿主
        import platform
        import sys as _sys

        # ① 节点注册（⚠ 分出口取：V3 模式下 NODE_CLASS_MAPPINGS 恒为 None，拿它数会永远得 0）
        names = _node_names() or ["<枚举失败>"]

        # ② 时序契约（不一致 ⇒ 本包尾段切片算术对上游失效 ⇒ 会产出错位坏片）
        try:
            from . import layout_contract as _lc
            _c_ok, _c_msgs = _lc.check_layout(force=True)
        except Exception as _e:                      # noqa: BLE001
            _c_ok, _c_msgs = None, ["检查本身失败：%r" % (_e,)]

        # ③ 上游可选依赖（第 8 节点用；没装不影响其它 7 个）
        try:
            from . import nodes as _n
            _has = _n._comfy_registry().get(_n._UPSCALER_NODE) is not None
            _up = "已装" if _has else "未装（可选；只影响 🔍 H3 Relay · Latent Upscale）"
        except Exception as _e:                      # noqa: BLE001
            _up = "未知（%s）" % type(_e).__name__

        # ④ 宿主带不带 MiniMax-H3 —— 缺了的话本包**节点照样注册、图照样搭，但续接静默无效**
        #    （`requires-comfyui` 表达不了「必须带 H3」这个约束 ⇒ 只能运行时查）。
        #    判据见 layout_contract.host_h3_status：文件在不在；None = 不在宿主进程里，不下断言。
        try:
            from . import layout_contract as _lc2
            _h3, _h3_why = _lc2.host_h3_status()
        except Exception as _e:                      # noqa: BLE001
            _h3, _h3_why = None, "查不出来（%s: %s）" % (type(_e).__name__, _e)

        try:
            import torch as _t
            _torch = _t.__version__
        except Exception:                            # noqa: BLE001
            _torch = "（未装）"

        _fp = folder_paths
        return web.json_response({
            "package": "ComfyUI-H3-Latent-Relay",
            "version": __version__,
            "node_api": NODE_API,
            "node_api_default": NODE_API_DEFAULT,
            "node_api_fallback": NODE_API_FALLBACK_REASON,
            "nodes_registered": len(names),
            "nodes": names,
            "contract_ok": _c_ok,
            "contract": _c_msgs,
            "upstream_upscaler": _up,
            "host_h3": _h3,                  # true / false；null = 查不出来（不在宿主进程里）
            "host_h3_detail": _h3_why,
            "python": _sys.version.split()[0],
            "torch": _torch,
            "os": "%s %s" % (platform.system(), platform.release()),
            "comfyui_base": str(getattr(_fp, "base_path", "") or ""),
            "output_dir": _fp.get_output_directory(),
        })


# ============================================================================
# 加载摘要（7.4①）：一行说清"包到底加载成什么样了"
# ============================================================================
# 为什么值得打这一行：**最高频的"装了没生效"是包压根没加载**（`custom_nodes/` 下嵌套了两层、
#   或装完没重启）—— 那种情况连 `/h3relay/health` 都不存在，任何基于路由的自检都够不着。
#   这一行是**零门槛**判据：**它没出现在启动日志里 ⇒ 包没加载**；出现了就能直接贴进 issue
#   （issue 模板要的「本包版本」也就有了）。
# ⚠ 上游可选依赖**不在这里查**：包加载时宿主的 `nodes` 模块可能还没就绪，查了会误报"未装"。
# ⚠ 整段包在 try 里：**摘要本身绝不许把包加载搞挂**。
def _node_names():
    """当前出口会注册的节点名清单。

    ⚠ V3 模式下 `NODE_CLASS_MAPPINGS` **恒为 None**（见文件头「两条出口必须互斥」），
      所以必须走 `v3.nodes_v3.NODES`；某些加载环境（离线工具）拿不到它时，**退回 V1 的映射** ——
      两套清单本来就一一对应（`tools/assert_default_exit.py` + 70 项逐字段机检都在锁这件事）。

    🔴 2026-09-25 修：原先写的是 `from .v3 import nodes`，而**文件名是 `v3/nodes_v3.py`**
      ⇒ 每次都抛 `ModuleNotFoundError`，又被下面的 `except: pass` 吞掉 ⇒ **这个分支从来没生效过**，
      一直静默退回 V1 映射。当时没暴露，是因为两套清单 1:1、退回去算出来**还是 8 个、名字也对**
      —— **结果正确 ≠ 代码正确**。真风险：一旦 V1/V3 分叉，banner 与 `/h3relay/health` 会
      **静默报 V1 的清单，而宿主跑的是 V3**（正是本包最忌讳的静默失效）。
      ⇒ 两处收口：① 走对模块名；② `except` 分支**必须出声**（不静默降级）。
      机检 = `tests/test_v3_schema.py` 的 6.1（判据 = **取到的是 NODES 序而非字典序**，
      这正是当初能一眼看穿"走了哪条分支"的那个差异）。
    """
    try:
        if NODE_API == "v3":
            # ⚠ 模块名是 `nodes_v3`（**不是** `nodes`）。这里**保证能成功**：NODE_API 仍是 "v3"
            #   意味着 `from .v3.entrypoint import comfy_entrypoint` 已成功，而它开头就
            #   `from .nodes_v3 import NODES` ⇒ 该模块早已在 sys.modules 里。
            from .v3.nodes_v3 import NODES as _v3_NODES
            ns = [getattr(c, "__name__", "?") for c in (_v3_NODES or ())]
            if ns:
                return ns
            print("[H3 Relay] ⚠️ V3 节点清单为空（`v3.nodes_v3.NODES`）⇒ 退回 V1 映射计数。"
                  "这行说明 V3 出口有问题，别只看 banner 的节点数。")
        elif NODE_CLASS_MAPPINGS:
            return sorted(NODE_CLASS_MAPPINGS)
    except Exception as _e:                 # noqa: BLE001
        # 🔴 不许静默：退回 V1 计数会让 banner/health 报出**另一套出口**的清单。
        print("[H3 Relay] ⚠️ 节点清单取用失败（%s: %s）⇒ 退回 V1 映射计数，"
              "banner 的节点数可能不代表当前出口。" % (type(_e).__name__, _e))
    try:
        from .nodes import NODE_CLASS_MAPPINGS as _v1
        return sorted(_v1 or {})
    except Exception:                       # noqa: BLE001
        return []


def _host_h3_warning() -> str:
    """宿主缺 MiniMax-H3 时的一行 ❌（放行，但**大声说**）。

    🔴 为什么单独一行、而不是并进「时序契约」那句：两者**处置完全不同** ——
      契约不一致是「上游改了网格」（升级宿主后重跑复核），
      缺 H3 是「宿主根本不支持」（得换宿主）⇒ 混在一句里，用户会照着错的说明书修。
    只在**确知缺失**（False）时出声；None（查不出来）保持安静，不制造噪音。
    """
    try:
        from . import layout_contract as _lc
        ok, detail = _lc.host_h3_status()
    except Exception:                          # noqa: BLE001
        return ""
    if ok is False:
        return ("[H3 Relay] ❌ 宿主没有 MiniMax-H3 支持（%s）"
                "⇒ 本包节点会正常注册，但**续接不会有任何效果**。"
                "请把 ComfyUI 更新到带 H3 的版本（≥ 0.37.0）后重启。" % detail)
    return ""


def _load_banner() -> str:
    _names = _node_names()
    n_nodes = len(_names) if _names else -1
    api = NODE_API + ("（V3 失败已回退）" if NODE_API_FALLBACK_REASON else "")
    try:
        from . import layout_contract as _lc
        _ok, _ = _lc.check_layout()
        contract = "✓" if _ok else "**不一致（会产出错位坏片）**"
    except Exception as _e:                 # noqa: BLE001
        contract = "未查（%s）" % type(_e).__name__
    line = ("[H3 Relay] v%s 已加载｜节点 %s 个（出口 %s）｜时序契约 %s"
            % (__version__, n_nodes if n_nodes >= 0 else "?", api, contract))
    warn = _host_h3_warning()
    return line + ("\n" + warn if warn else "")


try:
    print(_load_banner())
except Exception as _e:                     # noqa: BLE001
    print("[H3 Relay] ⚠ 加载摘要生成失败（不影响包加载）：%r" % (_e,))
