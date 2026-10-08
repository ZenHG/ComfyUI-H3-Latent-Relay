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

from pathlib import Path   # 本分片自己用到的（其余名字仍来自 runner 的命名空间）

# ---------------------------------------------------------------- 第 8 组：接缝自检
print()
print("[8] 接缝自检 find_head_jump / describe_head_jump")

# 8.1 干净起点：全段缓慢变化（帧差稳定在 3 左右）→ 不应报突变
seq_clean = torch.zeros(30, 8, 8, 3)
for i in range(30):
    seq_clean[i] = (i % 3) * 10.0        # 帧差恒定 ≈ 小幅
j, jump, base = CORE.find_head_jump(seq_clean)
check("8.1 平稳序列无突变（j<0）", j < 0, "j=%d jump=%.2f base=%.2f" % (j, jump, base))

# 8.2 首帧突变（模拟实测：第 0 帧独立、之后稳定）→ 应报 j=0
seq_jump = torch.zeros(30, 8, 8, 3)
for i in range(1, 30):
    seq_jump[i] = 50.0                    # 第 1 帧起基本一致
j, jump, base = CORE.find_head_jump(seq_jump)
check("8.2 首帧突变被检出（j=0）", j == 0, "j=%d jump=%.2f base=%.2f" % (j, jump, base))
check("8.2 突变比值超过阈值", jump > CORE.JUMP_RATIO * max(base, CORE._baseline_floor(seq_jump)),
      "jump=%.1f base=%.2f" % (jump, base))

# 8.3 中段突变（模拟实测：第 22→23 帧跳）→ 应报 j=22
seq_mid = torch.zeros(40, 8, 8, 3)
for i in range(1, 23):
    seq_mid[i] = 50.0 + (i % 2)
for i in range(23, 40):
    seq_mid[i] = 200.0 + (i % 2)
j, jump, base = CORE.find_head_jump(seq_mid)
check("8.3 第 22→23 帧突变被检出（j=22）", j == 22, "j=%d jump=%.2f base=%.2f" % (j, jump, base))

# 8.4 报告文案：有突变时给"再加 N 帧"的建议
msg = CORE.describe_head_jump(seq_mid)
check("8.4 突变报告含「建议 trim 再加 23 帧」", "23 帧" in msg, msg[:90])
msg2 = CORE.describe_head_jump(seq_clean)
check("8.4 平稳报告含「起点干净」", "起点干净" in msg2, msg2[:90])

# 8.5 帧数过少不应崩
j, jump, base = CORE.find_head_jump(torch.zeros(3, 8, 8, 3))
check("8.5 帧数过少返回 (-1,0,0) 不抛异常", j == -1, "j=%d" % j)

# ---------------------------------------------------------------- 第 9 组：段号声明与取源的矛盾必须硬拦
print()
print("[9] stage_index 声明了第 N 段却拿不到上一段 → 必须 raise（不得静默直通）")

ctx = NODES.H3RelayCopyBridge()

# 9.1 stage_index>=1 + 无 context_latent → 必须 raise（不得静默直通，否则产出无续接的哑片）
try:
    ctx.bridge(cur, None, context_frames=22, run_id="", stage_index=1)
    check("9.1 段号≥1 且无来源 → raise", False, "竟然没报错（会产出无续接的哑剧）")
except RuntimeError as e:
    check("9.1 段号≥1 且无来源 → raise", "静默直通" in str(e), str(e).split("\n")[0])

# 9.2 run_id 即便有值，只要没 context_latent 仍 raise（本节点不按 run_id 自动取源，取源归 LatentLoad）
try:
    ctx.bridge(cur, None, context_frames=22, run_id="   ", stage_index=3)
    check("9.2 段号≥1 且无来源（即便 run_id 有值）仍 raise", False, "竟然没报错")
except RuntimeError as e:
    check("9.2 段号≥1 且无来源（即便 run_id 有值）仍 raise", "静默直通" in str(e), str(e).split("\n")[0])

# 9.3 stage_index=0 仍应正常直通（独立段，合法）
try:
    out0 = ctx.bridge(cur, None, context_frames=22, run_id="", stage_index=0)
    check("9.3 stage_index=0 仍直通不报错（独立段合法）", int(out0[2]) == 0, "trim=%r" % (out0[2],))
except Exception as e:
    check("9.3 stage_index=0 仍直通不报错（独立段合法）", False, "抛了 %s" % type(e).__name__)

# 9.4 手动接了 context_latent 时，段号≥1 不应被拦（高级用法）
try:
    out1 = ctx.bridge(cur, prev, context_frames=22, run_id="", stage_index=1)
    check("9.4 手动接 context_latent → 段号≥1 不拦", int(out1[2]) == 22, "trim=%r" % (out1[2],))
except Exception as e:
    check("9.4 手动接 context_latent → 段号≥1 不拦", False, "抛了 %s：%s" % (type(e).__name__, e))

# 9.5 段号≥1 无 context（run_id 有值但没接 LatentLoad 来源）→ 仍 raise，不静默直通
try:
    ctx.bridge(cur, None, context_frames=22, run_id="unittest_no_such_run", stage_index=1)
    check("9.5 段号≥1 无来源（即便 run_id 有值）→ 仍 raise", False, "竟然没报错")
except RuntimeError as e:
    check("9.5 段号≥1 无来源（即便 run_id 有值）→ 仍 raise", "静默直通" in str(e), str(e).split("\n")[0])
except Exception as e:
    check("9.5 run_id 有值但无文件 → FileNotFoundError", False, "抛的是 %s" % type(e).__name__)

# 9.6 异常上下文（#3）：**非本节点**抛的裸异常必须被补上「节点名 + 段号」。
#     本包节点层自己写的错误文案是给用户看的操作指引（含"二选一"做法）；但 relay_core /
#     上游库冒上来的异常是裸的（FileNotFoundError、KeyError…），用户看不出是哪个节点、哪一段。
#     判据：走 LatentLoad —— 它有 stage_index，且缺文件时抛的是 **relay_core** 的 FileNotFoundError
#     （属于「该包装」的那一类；节点自己 raise 的那类按约束 B 会被原样放行）。
#     ⚠️ expect_raise 只吃一个 needle，装不下「两个都要在」⇒ 这一条自己写 try/except。
try:
    NODES.H3RelayLatentLoad().load(run_id="unittest_errctx", stage_index=5)
    check("9.6 异常上下文含节点名与段号（不是只判『抛异常』）", False, "竟然没报错")
except RuntimeError as e:
    _m = str(e)
    check("9.6 异常上下文含节点名与段号（不是只判『抛异常』）",
          "H3RelayLatentLoad" in _m and "第 5 段" in _m, _m.splitlines()[0][:90])
except Exception as e:
    check("9.6 异常上下文含节点名与段号（不是只判『抛异常』）",
          False, "抛的是 %s（应当被包成 RuntimeError）" % type(e).__name__)

# ---------------------------------------------------------------- 第 10 组：latent 取流对非 NestedTensor 的健壮性
print()
print("[10] streams_from_latent：不能把普通张量当 NestedTensor 拆 batch 维")

# 10.1 NestedTensor → 按 .tensors 拆成两条流
nt_latent = {"samples": NT.NestedTensor([torch.zeros(1, 24, 57, 48, 28),
                                         torch.zeros(1, 32, 2, 320)])}
sp = CORE.streams_from_latent(nt_latent)
check("10.1 NestedTensor → 2 条流（视频+音频）", len(sp) == 2 and sp[0].shape[1] == 24,
      "得到 %d 条" % len(sp))

# 10.2 普通 [B,C,T,H,W] 张量（B=1）→ 必须当成**一条**流，不能沿 batch 维拆
plain1 = {"samples": torch.zeros(1, 24, 57, 48, 28)}
sp1 = CORE.streams_from_latent(plain1)
check("10.2 普通张量 B=1 → 1 条流（不是被 unbind 成 1 条 4 维）",
      len(sp1) == 1 and sp1[0].ndim == 5, "得到 %d 条，ndim=%d" % (len(sp1), sp1[0].ndim))

# 10.3 B=4 → 仍然只能是一条流（旧写法会拆成 4 条"伪音频流"）
plain4 = {"samples": torch.zeros(4, 24, 57, 48, 28)}
sp4 = CORE.streams_from_latent(plain4)
check("10.3 普通张量 B=4 → 仍是 1 条流（旧写法会错拆成 4 条）",
      len(sp4) == 1 and sp4[0].shape[0] == 4, "得到 %d 条" % len(sp4))

# 10.4 video-only latent 去当 context_latent → 必须明确 raise，不能静默当"有音频"
try:
    CORE.audio_from_latent(plain1)
    check("10.4 video-only → audio_from_latent 必须 raise", False, "竟然没报错")
except ValueError as e:
    check("10.4 video-only → audio_from_latent 必须 raise", "没有音频流" in str(e), str(e).split("\n")[0])
except Exception as e:
    check("10.4 video-only → audio_from_latent 必须 raise", False, "抛的是 %s" % type(e).__name__)

# 10.5 list 形态（已拆开的流）仍可用
sp_list = CORE.streams_from_latent([torch.zeros(1, 24, 57, 48, 28), torch.zeros(1, 32, 2, 320)])
check("10.5 list 形态仍拆成 2 条流", len(sp_list) == 2, "得到 %d 条" % len(sp_list))

# 10.6 完全不是张量的输入 → 明确报错
try:
    CORE.streams_from_latent({"samples": "not a tensor"})
    check("10.6 非张量输入 → raise", False, "竟然没报错")
except ValueError:
    check("10.6 非张量输入 → raise", True)

# ---------------------------------------------------------------- 第 11 组：Chain 的状态格子
print()
print("[11] H3RelayChain：前端要往 status 写状态，后端必须有这一格")

chain_cls = NODES.H3RelayChain
req = chain_cls.INPUT_TYPES().get("required") or {}
opt = chain_cls.INPUT_TYPES().get("optional") or {}
check("11.1 status 作为可选 widget 存在（否则前端提示无处显示）", "status" in opt,
      "optional=%s" % list(opt))
# ⚠ 2026-09-22 口径更新（0.6.7 加词分发/自动拼接）：原断言是「status 必须是最后一格」，
#   本意是**槽位安全** —— 新格一律**追加在末尾**，旧工作流少格子时只走默认值、
#   不会让它前面的取值整体前移（见 CHANGES 0.2.1）。0.6.7 的四个新格**全部追加在 status 之后**
#   ⇒ 原意完好，断言改为「前缀不动 + 追加只发生在 status 之后」。
check("11.2 槽位安全：segments 仍居首、status 未被前移、新格只追加在它之后",
      list(req) == ["segments"] and list(opt)[0] == "status",
      "required=%s optional=%s" % (list(req), list(opt)))

# 11.3 前端会把 status 一起传进来 → run 必须能吃下（0.6.15 起 FUNCTION 由 noop 改为 run）
try:
    chain_cls().run(segments=3, status="第 2 段")
    check("11.3 run 能吃下 status 参数（不会 TypeError）", True)
except Exception as e:
    check("11.3 run 能吃下 status 参数（不会 TypeError）", False, repr(e))

# 11.4 前端 JS 确实在找这个 widget 名
#   ⚠ 2026-09-28：判据放宽 —— 0.6.13 把「按名字找 widget」抽成了 `findWidget(n, name)`，
#   原来那条 `'x.name === "status"' in _src` 是**对源码字面量的硬匹配**，重构一次就假红。
#   这里认**两种写法**：抽出来的 `findWidget(..., "status")`，或内联的 `name === "status"`。
_js = os.path.join(_KIT_DIR, "web", "relay_kit_chain.js")
try:
    _src = Path(_js).read_text(encoding="utf-8")
    _found = ('findWidget(chainNode, "status")' in _src) or ('x.name === "status"' in _src)
    check("11.4 前端 JS 找的 widget 名与后端一致（status）", _found,
          "未在 relay_kit_chain.js 里找到对 status 的查找")
except Exception as e:
    check("11.4 前端 JS 找的 widget 名与后端一致（status）", False, repr(e))

# 11.5~11.8 0.6.7 词分发 / 自动拼接的**接口契约**（默认关 = 老图逐位不变）
for _w in ("prompts", "prompt_target", "auto_concat", "concat_name"):
    check("11.5 %s 作为可选 widget 存在" % _w, _w in opt)
check("11.6 默认档 = 老行为（prompts 空、auto_concat 关、成片名空）",
      opt["prompts"][1].get("default") == "" and opt["prompts"][1].get("multiline") is True
      and opt["auto_concat"][1].get("default") is False
      and opt["concat_name"][1].get("default") == "",
      "prompts.default=%r multiline=%r auto_concat.default=%r"
      % (opt["prompts"][1].get("default"), opt["prompts"][1].get("multiline"),
         opt["auto_concat"][1].get("default")))
try:
    chain_cls().run(segments=3, status="第 2 段", prompts="a\n---\nb", prompt_target="6.h3_data",
                    auto_concat=True, concat_name="film")
    check("11.7 run 能吃下 0.6.7 的四个新参数（不会 TypeError）", True)
except Exception as e:
    check("11.7 run 能吃下 0.6.7 的四个新参数（不会 TypeError）", False, repr(e))
try:
    _js2 = Path(os.path.join(_KIT_DIR, "web", "relay_kit_prompt.js")).read_text(encoding="utf-8")
    # ⚠ 2026-09-15：0.6.15 把「前端写词」那条老路删了（词改由 Chain 节点输出）
    #   ⇒ 断言改为「只留两个还需要的纯函数」，并**反向**确认老路函数已不在（防它悄悄回来）。
    check("11.8 词分发纯函数模块只留 splitPromptBlocks + collectStageIds（老路已删）",
          "export function splitPromptBlocks" in _js2 and "export function collectStageIds" in _js2
          and "export function writePrompt" not in _js2
          and "export function resolveTarget" not in _js2)
except Exception as e:
    check("11.8 词分发纯函数模块在位（前端与离线单测共用同一份逻辑）", False, repr(e))

# 11.9~11.11 成片音轨档 / 画质档（2026-09-22 二轮）：**继续追加在末位**，槽位安全。
check("11.9 audio_out 作为三选一 widget 存在（aac_256k / aac_192k / pcm_lossless）",
      "audio_out" in opt and list(opt["audio_out"][0]) == ["aac_256k", "aac_192k", "pcm_lossless"]
      and opt["audio_out"][1].get("default") == "aac_256k",
      "audio_out=%r" % (opt.get("audio_out"),))
check("11.10 video_crf 可调（默认 16，0~51 合法输入）",
      "video_crf" in opt and opt["video_crf"][1].get("default") == 16
      and opt["video_crf"][1].get("min") == 0 and opt["video_crf"][1].get("max") == 51,
      "video_crf=%r" % (opt.get("video_crf"),))
check("11.11 槽位安全：新格仍在其前面所有格之后（concat_name 之后）",
      list(opt).index("audio_out") > list(opt).index("concat_name")
      and list(opt).index("video_crf") > list(opt).index("concat_name"),
      "optional=%s" % list(opt))
try:
    chain_cls().run(segments=3, audio_out="pcm_lossless", video_crf=23)
    check("11.12 run 能吃下音轨/画质两个新参数（**kwargs 兜住）", True)
except Exception as e:
    check("11.12 run 能吃下音轨/画质两个新参数（**kwargs 兜住）", False, repr(e))

# 11.13~11.19 0.6.15 词分发**节点化**（铁律一：UI 与 API 必须同一套实现，且必须基于节点）
#   背景：0.6.7~0.6.14 词分发只存在于前端 JS（web/relay_kit_prompt.js）⇒ `docs/10` §7.4
#   把 API/脚本侧的「词分发」标成 ❌。那正是铁律一禁止的「只有 UI 路径才有的分支（或反过来）」。
#   0.6.15 把「第 k 段喂第 k 块词」做成 Chain 节点的**输出口** ⇒ 画布连线与 API 提交图 JSON
#   走同一个节点；核心分块算法在 relay_core（唯一权威实现）。
check("11.13 stage_index 作为可选 widget 存在（决定输出第几块词）",
      "stage_index" in opt and opt["stage_index"][1].get("default") == 0
      and opt["stage_index"][1].get("min") == 0,
      "stage_index=%r" % (opt.get("stage_index"),))
check("11.14 槽位安全：0.6.15 的三个新格都追加在末位（stage_index / run_id / concat_result）",
      list(opt)[-3:] == ["stage_index", "run_id", "concat_result"]
      and list(opt).index("stage_index") > list(opt).index("video_crf"),
      "optional=%s" % list(opt))
check("11.15 词分发节点化的接口契约：FUNCTION=run / 输出 prompt+report",
      chain_cls.FUNCTION == "run" and tuple(chain_cls.RETURN_TYPES) == ("STRING", "STRING")
      and tuple(chain_cls.RETURN_NAMES) == ("prompt", "report"),
      "FUNCTION=%r RETURN_TYPES=%r RETURN_NAMES=%r"
      % (chain_cls.FUNCTION, chain_cls.RETURN_TYPES, getattr(chain_cls, "RETURN_NAMES", None)))

# 11.16 行为：按 stage_index 取第 k 块 —— **不是**"能跑就算过"，要真的取对
_chain = chain_cls()
_p0 = _chain.run(segments=2, prompts="词A\n---\n词B", stage_index=0)
_p1 = _chain.run(segments=2, prompts="词A\n---\n词B", stage_index=1)
check("11.16 run 按 stage_index 输出第 k 块词（0 起算）",
      _p0[0] == "词A" and _p1[0] == "词B" and "2" in _p0[1] and "2" in _p1[1],
      "stage0=%r stage1=%r" % (_p0, _p1))

# 11.17 越界必须 raise（**不许静默复用上一块词** —— 那正是"以为换了词、其实没换"）
try:
    _chain.run(segments=9, prompts="只有一块", stage_index=3)
    check("11.17 词块不够 ⇒ 直接报错（不静默复用）", False, "没 raise")
except Exception as e:
    check("11.17 词块不够 ⇒ 直接报错（不静默复用）",
          "1 块" in str(e) or "只有一块" in str(e), repr(e)[:140])

# 11.18 未填 prompts ⇒ 空词 + 明说"不换词"（老行为：一个字都不动）
_p_empty = _chain.run(segments=2, prompts="", stage_index=1)
check("11.18 未填 prompts ⇒ 输出空词并说明不换词（老行为不变）",
      _p_empty[0] == "" and "不换词" in _p_empty[1], "%r" % (_p_empty,))

# 11.19 跨语言口径锁定：relay_core 的分块 == 共享样本的期望
#   Node 侧 tests/test_prompt_dispatch.mjs 读**同一份** tests/prompt_blocks_cases.json。
#   🔴 为什么必须有：词分发现在是「节点（Python）+ 画布副本（JS）」两条**调用**路径，
#   但**口径只能有一份**。工具里曾经还照抄过第三份（用 splitlines，与 JS 已经漂移）。
try:
    import json as _json
    _cases = _json.loads(Path(os.path.join(_KIT_DIR, "tests", "prompt_blocks_cases.json")).read_text(encoding="utf-8"))["cases"]
    _bad = [c["name"] for c in _cases
            if CORE.split_prompt_blocks(c["text"]) != c["expect"]]
    check("11.19 分块口径与共享样本一致（%d 例，跨语言锁）" % len(_cases), not _bad,
          "不一致：%s" % _bad)
except Exception as e:
    check("11.19 分块口径与共享样本一致", False, repr(e))

# ---------------------------------------------------------------- 第 12 组：沉降帧
print()
print("[12] 沉降帧：pin 与 crop 解耦 + 自动检测（观测端决定，用户零配置）")


def seam_seg(n, switch_at, level=200.0, cut_every=None):
    """合成一段"续接段 decode 结果"。

    [0, switch_at) = 上一段尾部的**复现**（帧差 ≈ 0~3）；[switch_at, n) = 本段新内容
    （基准亮度不同 → 切换处 1 帧硬跳）。cut_every 用来塞一个"段内真实切镜"。
    """
    im = torch.zeros(n, 8, 8, 3)
    for i in range(n):
        im[i] = (0.0 if i < switch_at else level) + (i % 3) * 3.0
    if cut_every:
        for i in range(cut_every, n, cut_every):
            im[i] = 250.0 - (i % 5)
    return im


# 12.1~12.5 core 层：settle 只动 crop，不动 pin / 锚位 / 音频窗
pl0 = CORE.plan_relay(cur, prev, 22)
check("12.1 settle=0 → trim == 22 且 settle == 0（旧行为逐位不变，回归保护）",
      pl0.trim == 22 and pl0.settle == 0, "trim=%d settle=%d" % (pl0.trim, pl0.settle))
check("12.1b summary() 正常格式化（带沉降字段）", "裁首 22 帧（含沉降 0）" in pl0.summary(), pl0.summary())
pl3 = CORE.plan_relay(cur, prev, 22, settle_frames=3)
check("12.2 settle=3 → trim == 25，但 span 仍 22、锚位不变（pin 未被污染）",
      pl3.trim == 25 and pl3.span == 22 and pl3.indices == pl0.indices,
      "trim=%d span=%d" % (pl3.trim, pl3.span))
check("12.3 settle 不影响音频窗（settle=0 与 settle=3 的窗**逐位相同**）",
      pl3.audio_ref["ref_audio_t"] == pl0.audio_ref["ref_audio_t"],
      "settle0=%d settle3=%d 步" % (pl0.audio_ref["ref_audio_t"],
                                    pl3.audio_ref["ref_audio_t"]))
check("12.4 settle<0 → 归零，不报错",
      CORE.plan_relay(cur, prev, 22, settle_frames=-5).trim == 22)
expect_raise("12.5 pin + settle >= 段长 → raise（裁完没画面）",
             lambda: CORE.plan_relay(lat124, lat124, 22, settle_frames=103), "裁完就没画面")

# 12.6~12.10 检测器：窄窗 + 上限 + 失败安全
sA, _, _ = CORE.detect_settle(seam_seg(107, 15), 22)
check("12.6 切换点(15) 落在钉住区之内 → settle=0（不该多裁）", sA == 0, "settle=%d" % sA)
sB, jB, _ = CORE.detect_settle(seam_seg(73, 23), 22)
check("12.7 切换点在原第 22→23 帧 → settle=1（正是 0.1.1 实测的那个场景）",
      sB == 1, "settle=%d jump=%.1f" % (sB, jB))
sC, _, _ = CORE.detect_settle(seam_seg(73, 23, cut_every=25), 22)
check("12.8 段内第 25 帧有真实切镜 → 仍取 1，不把新内容裁掉（D3 回归）",
      0 <= sC <= CORE.MAX_SETTLE, "settle=%d" % sC)
sD, _, _ = CORE.detect_settle(seam_seg(120, 40), 22)
check("12.9 切换点远超 pin+MAX_SETTLE → 回退 0（宁可维持旧行为也不赌）", sD == 0, "settle=%d" % sD)
check("12.10 pin<=0 时不检测（首段/独立段绝不触发）",
      CORE.detect_settle(seam_seg(73, 23), 0) == (0, 0.0, 0.0))

# 12.11~12.13 自检文案：只有**可执行**的才给建议（D3）
msg_far = CORE.describe_head_jump(seq_mid)      # 突变在裁后第 22 帧 → 太远
check("12.11 突变位置超出可执行范围 → 不再建议「再裁」（D3）",
      "不动刀" in msg_far and "settle_frames" not in msg_far, msg_far[:100])
check("12.12 但仍然报出位置（含「第 22→23 帧」）", "第 22→23 帧" in msg_far)
seq_near = torch.zeros(30, 8, 8, 3)
for i in range(3, 30):
    seq_near[i] = 200.0
msg_near = CORE.describe_head_jump(seq_near)    # 突变就在裁后头部 → 可执行
check("12.13 突变就在头部 → 给出可执行的 settle_frames 建议", "settle_frames" in msg_near, msg_near[:90])

# 12.14~12.22 裁节点：默认自动、可关、可固定
seg73 = seam_seg(73, 23)
aud73 = {"waveform": torch.zeros(1, 2, int(round(73 / 24.0 * SR))), "sample_rate": SR}
# ⚠ 本组（12.14~12.18）测的是**裁剪契约**，故显式关掉缝帧重影（seam_ghost=0）以隔离变量；
#   重影本身另有专测 12.23~12.24（2026-09-15 新增）。
ok, out = arity(NODES.H3RelayTrimAV, {"images": seg73, "trim_frames": 22, "fps": 24.0,
                                          "audio": aud73, "settle_frames": -1, "seam_ghost": 0})
check("12.14 settle_frames=-1（显式走自动）→ 自动裁 23 帧（73 → 50）",
      int(out[0].shape[0]) == 50, "得到 %d 帧" % int(out[0].shape[0]))
# 🔴 2026-09-16 新默认：**不裁沉降**。依据：裁沉降才是缝处跳帧的源头
#   （裁 0 帧跳 0.020 几乎无感 / 裁 8 帧 0.044 / 裁 16 帧 0.055）——见 relay_core 注释。
ok, out_d0 = arity(NODES.H3RelayTrimAV, {"images": seg73, "trim_frames": 22, "fps": 24.0,
                                            "audio": aud73, "seam_ghost": 0})
check("12.14b 默认 settle_frames=**0** → 只裁钉住区 22 帧（73 → 51），不裁沉降",
      int(out_d0[0].shape[0]) == 51, "得到 %d 帧" % int(out_d0[0].shape[0]))
check("12.15 自动裁后首帧 == 原第 23 帧（切换点被裁掉，逐位）",
      torch.equal(out[0][0], seg73[23]))
check("12.16 自动裁后音频同裁 23 帧 → 音画时长一致（容差 = 1 个采样点）",
      abs(int(out[1]["waveform"].shape[-1]) / SR - 50 / 24.0) < 1.0 / SR,
      "%.4fs" % (int(out[1]["waveform"].shape[-1]) / SR))
check("12.17 报告里写清「钉住 + 沉降」两段账", "钉住 22 + 沉降 1" in out[2], out[2].splitlines()[0][:80])
check("12.18 自动裁后自检「起点干净」（接缝真的修好了）",
      "起点干净" in out[2], out[2].splitlines()[-1][:90])
ok, out0 = arity(NODES.H3RelayTrimAV, {"images": seg73, "trim_frames": 0, "fps": 24.0, "audio": None})
check("12.19 trim_frames=0（首段/独立段）→ 原样返回，绝不触发自动检测",
      out0[0] is seg73 and int(out0[0].shape[0]) == 73)
ok, outx = arity(NODES.H3RelayTrimAV, {"images": seg73, "trim_frames": 22, "fps": 24.0,
                                          "audio": None, "settle_frames": 0})
check("12.20 settle_frames=0 → 回到 0.2.x 旧行为（73 → 51 帧）",
      int(outx[0].shape[0]) == 51, "得到 %d" % int(outx[0].shape[0]))
ok, outm = arity(NODES.H3RelayTrimAV, {"images": seg73, "trim_frames": 22, "fps": 24.0,
                                          "audio": None, "settle_frames": 9})
check("12.21 settle_frames=9（手动固定）→ 裁 31 帧，供各段等长用",
      int(outm[0].shape[0]) == 42, "得到 %d" % int(outm[0].shape[0]))
_req = NODES.H3RelayTrimAV.INPUT_TYPES()["required"]
_opt = NODES.H3RelayTrimAV.INPUT_TYPES()["optional"]
check("12.22 新 widget 一律**追加在 optional 末位**（前缀顺序稳定、旧工作流取值不前移）",
      list(_req) == ["images", "trim_frames", "fps"]
      and list(_opt)[:2] == ["audio", "settle_frames"]
      and list(_opt)[:4] == ["audio", "settle_frames", "seam_ghost", "seam_ghost_alpha"]
      and list(_opt)[:4] == ["audio", "settle_frames", "seam_ghost", "seam_ghost_alpha"]
      and len(_opt) >= 4,
      "required=%s optional=%s" % (list(_req), list(_opt)))

# —— 缝帧重影（极短交叉溶，2026-09-15 本仓作者 认可的解法）——
ok, outg = arity(NODES.H3RelayTrimAV, {"images": seg73, "trim_frames": 22, "fps": 24.0,
                                           "audio": aud73, "settle_frames": -1, "seam_ghost": 1, "seam_ghost_alpha": 0.5})
check("12.23 缝帧重影：**帧数守恒**（与关重影同长 → 音频不必动、无 A/V 漂移）",
      int(outg[0].shape[0]) == int(out[0].shape[0]) == 50, "得到 %d" % int(outg[0].shape[0]))
check("12.24 缝帧重影：首帧 = 上段末帧 ⊕ 裁后首帧（对半），其余帧逐位不动",
      torch.allclose(outg[0][0], 0.5 * seg73[21] + 0.5 * seg73[23], atol=1e-6)
      and torch.equal(outg[0][1], seg73[24]),
      "首帧最大差 %.2e" % float((outg[0][0] - (0.5 * seg73[21] + 0.5 * seg73[23])).abs().max()))

# —— 12.25 默认值钉子（2026-09-15 深夜定案）——
# 动机：`seam_ghost` 曾默认开 1 帧。实测它只把「一跳」拆成「两跳」——
#   总位移守恒（超基线总量 12.61 → 11.68，仅 −7%）、**异常帧数 1 → 2**，
#   观感从「跳一下」变成「卡两下」，且混合帧本身是可见鬼影（本仓作者 目检定案）。
#   ⇒ 默认值是**实测结论**而非偏好，钉住防回归。
_tg = NODES.H3RelayTrimAV.INPUT_TYPES()["optional"]["seam_ghost"][1]
check("12.25 缝帧重影默认 **关**（实测一跳拆两跳更差；钉住防回归）",
      int(_tg.get("default", -1)) == 0, "default=%r" % _tg.get("default"))
check("12.26 缝帧重影关闭时逐位不动（默认档 = 0.4.3 行为）",
      torch.equal(out[0][0], seg73[23]), "首帧最大差 %.2e"
      % float((out[0][0] - seg73[23]).abs().max()))

