# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
#
# ruff: noqa: F821  —— 本分片**不是独立模块**（与 `tests/cases/` 其余分片同款）：
#   由 `tests/test_relay_core.py` 用 `exec(compile(src, path, "exec"), globals())`
#   顺序执行 ⇒ `check` / `expect_raise` / `CORE` / `NODES` / `os` / `sys` 都看得见，
#   但不是本文件定义的。

# ============================================================================
# 第 37 组：无头连跑 Finite Run（0.6.36）—— 展开图结构 / 链接 / 校验（零 GPU）
#
# 锁四件事：① 图结构（每段 10 节点、链接端点逐一正确 —— 接错槽 = 静默坏片）；
# ② 逐段参数（段号/词块/种子/run_id）逐位一致；③ 建图期校验（烧卡前 raise）；
# ④ 沉降档随段下传（画布能设的裁量，脚本/API 侧也必须能设 —— 铁律一）。
# 真实出片正确性（缝观感/音画同步/内存）不在本组范围 —— 需 GPU + 人眼。
# ============================================================================

print()
print("=" * 78)
print("### 第 37 组：无头连跑 Finite Run（展开图构建 + 校验 + 拼条节点）")
print("=" * 78)

_G = CORE  # 本组大量访问 finite 命名空间下的函数（已由 relay_core 再导出）


def _build(n=3, **kw):
    """构造一张展开图；返回 (graph, outputs, report)。"""
    blocks, _note = _G.resolve_finite_prompts(kw.pop("prompt", "基础词"), kw.pop("prompts", ""), n)
    args = {
        "prefix": "t.", "segments": n, "blocks": blocks, "seed": 7, "run_id": "demo",
        "note": "", "width": 608, "height": 1056, "length": 124, "context_frames": 22,
        "mask_mode": "hard", "pin_audio": True,
        "model": "MODEL", "clip": "CLIP", "vae": "VAE", "audio_vae": "AVAE",
        "image": "IMAGE", "sampler": "SAMPLER", "sigmas": "SIGMAS",
    }
    args.update(kw)
    return _G.build_finite_graph(**args)


def _cls_of(graph, suffix):
    """取某个段角色节点的 class_type（找不到 ⇒ None）。"""
    for nid, node in graph.items():
        if nid.endswith(suffix):
            return node["class_type"]
    return None


def _inputs_of(graph, suffix):
    for nid, node in graph.items():
        if nid.endswith(suffix):
            return node["inputs"]
    return None


# ---------------------------------------------------------------------------
print("[1] 图结构：每段 10 个节点、角色齐全")
# ---------------------------------------------------------------------------
_g3, _o3, _r3 = _build(3)
check("37.1 3 段 = 30 个节点（每段 10 个；上一段由桥自取，手接的 Latent Load 已去掉）",
      len(_g3) == 30 == _G.finite_graph_node_count(3), "得到 %d" % len(_g3))
check("37.2 段 0 九种角色 class_type 全部正确",
      all(_cls_of(_g3, "seg0_" + r) == t for r, t in [
          ("i2v", "MiniMaxH3ImageToVideo"),
          ("bridge", "H3RelayCopyBridge"), ("noise", "RandomNoise"),
          ("guider", "BasicGuider"), ("samp", "SamplerCustomAdvanced"),
          ("save", "H3RelayLatentSave"), ("vdec", "VAEDecode"),
          ("adec", "VAEDecodeAudio"), ("trim", "H3RelayTrimAV"),
          ("join", "H3RelayJoinAV")]))
_g1, _o1, _r1 = _build(1)
check("37.3 1 段 = 10 个节点（无累计链）", len(_g1) == 10, "得到 %d" % len(_g1))

# ---------------------------------------------------------------------------
print("[2] 链接端点：接错槽 = 静默坏片，逐一锁死")
# ---------------------------------------------------------------------------
_b0 = _inputs_of(_g3, "seg0_bridge")
check("37.4 桥的 latent ← ImageToVideo 第 2 路（目标 latent）",
      _b0["latent"] == ["t.seg0_i2v", 1], str(_b0["latent"]))
check("37.5 桥**不接** context_latent（上一段由桥按 run_id + 段号−1 自取）",
      "context_latent" not in _b0, str(_b0.get("context_latent", "已删除")))
check("37.6 桥的 conditioning ← ImageToVideo 第 1 路（复合桥接法）",
      _b0["conditioning"] == ["t.seg0_i2v", 0], str(_b0["conditioning"]))
_g0 = _inputs_of(_g3, "seg0_guider")
check("37.7 引导器的 conditioning ← 桥第 4 路（钉帧后的 conditioning）",
      _g0["conditioning"] == ["t.seg0_bridge", 3], str(_g0["conditioning"]))
_s0 = _inputs_of(_g3, "seg0_samp")
check("37.8 采样器 latent_image ← 桥第 1 路（桥后 latent）",
      _s0["latent_image"] == ["t.seg0_bridge", 0], str(_s0["latent_image"]))
check("37.9 采样器 noise/guider/sampler/sigmas 四槽齐全",
      _s0["noise"] == ["t.seg0_noise", 0] and _s0["guider"] == ["t.seg0_guider", 0]
      and _s0["sampler"] == "SAMPLER" and _s0["sigmas"] == "SIGMAS")
_sv0 = _inputs_of(_g3, "seg0_save")
check("37.10 落盘的 latent ← 采样输出第 1 路",
      _sv0["latent"] == ["t.seg0_samp", 0], str(_sv0["latent"]))
_t0 = _inputs_of(_g3, "seg0_trim")
check("37.11 TrimAV 的 trim_frames ← 桥第 3 路（全自动，不手填）",
      _t0["trim_frames"] == ["t.seg0_bridge", 2], str(_t0["trim_frames"]))
check("37.12 TrimAV 的 images/audio ← 两路解码",
      _t0["images"] == ["t.seg0_vdec", 0] and _t0["audio"] == ["t.seg0_adec", 0])
_j1 = _inputs_of(_g3, "seg1_join")
check("37.13 第 2 段拼条接上一段 JoinAV 的两路输出",
      _j1["images_acc"] == ["t.seg0_join", 0] and _j1["audio_acc"] == ["t.seg0_join", 1])
_j0 = _inputs_of(_g3, "seg0_join")
check("37.14 首段拼条**不接**累计（两个 acc 键都不在场）",
      "images_acc" not in _j0 and "audio_acc" not in _j0)
check("37.15 外层输出 = 末段采样 latent + 末段 JoinAV 两路 + 报告字面量",
      _o3[0] == ["t.seg2_samp", 0] and _o3[1] == ["t.seg2_join", 0]
      and _o3[2] == ["t.seg2_join", 1] and isinstance(_o3[3], str) and len(_o3) == 4)

# ---------------------------------------------------------------------------
print("[3] 逐段参数：段号 / 词块 / 种子 / run_id 在展开图里逐位正确")
# ---------------------------------------------------------------------------
_gb, _ob, _rb = _build(3, prompt="共用词", prompts="词一\n---\n词二\n---\n词三")
_i2v = [n["inputs"]["prompt"] for nid, n in sorted(_gb.items()) if nid.endswith("_i2v")]
check("37.16 每段各喂各的词块（第 k 块喂第 k 段）", _i2v == ["词一", "词二", "词三"], str(_i2v))
_st = [n["inputs"]["stage_index"] for nid, n in sorted(_gb.items())
       if nid.endswith("_bridge")]
check("37.17 桥/落盘的段号逐段 +1（0 起算）", _st == [0, 1, 2], str(_st))
_sv = [n["inputs"]["stage_index"] for nid, n in sorted(_gb.items())
       if nid.endswith("_save")]
check("37.18 落盘段号与桥一致（接力棒对得上）", _sv == [0, 1, 2], str(_sv))
_ns = {n["inputs"]["noise_seed"] for nid, n in _gb.items() if nid.endswith("_noise")}
check("37.19 所有段同一个种子（与画布连跑同语义）", _ns == {7}, str(_ns))
_rid = {n["inputs"]["run_id"] for nid, n in _gb.items()
        if nid.endswith(("_save", "_bridge"))}
check("37.20 run_id 在 读/桥/落盘 三处一字不差", _rid == {"demo"}, str(_rid))
_cf = {n["inputs"]["context_frames"] for nid, n in _gb.items() if nid.endswith("_bridge")}
check("37.21 所有段同一个钉住窗", _cf == {22}, str(_cf))
_gs, _os2, _rs2 = _build(2, prompt="只有一份词")
_i2v2 = [n["inputs"]["prompt"] for nid, n in sorted(_gs.items()) if nid.endswith("_i2v")]
check("37.22 prompts 留空 ⇒ 每段都用同一份 prompt（不换词老行为）",
      _i2v2 == ["只有一份词", "只有一份词"], str(_i2v2))
_bl, _note = _G.resolve_finite_prompts("p", "a\n---\nb\n---\nc\n---\nd", 2)
check("37.23 词块多于段数 ⇒ 只取前 N 块并说明", _bl == ["a", "b"] and "多出" in _note,
      "%s / %s" % (_bl, _note))

# ---------------------------------------------------------------------------
print("[4] 可选槽：不接就不写进图（不塞空值）")
# ---------------------------------------------------------------------------
_gv, _ov, _rv = _build(2, image=None, voice_anchor=None)
check("37.24 不接首帧图 ⇒ ImageToVideo 无 first_frame 键（纯文生成模式）",
      all("first_frame" not in n["inputs"] for nid, n in _gv.items() if nid.endswith("_i2v")))
check("37.25 不接声锚 ⇒ 桥无 voice_anchor 键",
      all("voice_anchor" not in n["inputs"] for nid, n in _gv.items() if nid.endswith("_bridge")))
_gw, _ow, _rw = _build(2, voice_anchor="ANCHOR")
check("37.26 接声锚 ⇒ 每段桥都带同一个声锚",
      all(n["inputs"].get("voice_anchor") == "ANCHOR"
          for nid, n in _gw.items() if nid.endswith("_bridge")))

# ---------------------------------------------------------------------------
print("[5] 建图期校验：能在烧卡前说清的错误绝不留给采样器")
# ---------------------------------------------------------------------------
expect_raise("37.27 segments=0 ⇒ raise", lambda: _build(0), "至少为 1")
expect_raise("37.28 segments=65 ⇒ raise（一次展开的节点数护栏）", lambda: _build(65), "上限")
expect_raise("37.29 词块不够 ⇒ raise（不静默复用）", lambda: _build(3, prompts="只有一块"), "补齐")
expect_raise("37.30 多段但 run_id 空 ⇒ raise（第 2 段起要读盘）",
             lambda: _build(2, run_id="  "), "run_id")
expect_raise("37.31 词与 prompts 全空 ⇒ raise", lambda: _build(1, prompt="  ", prompts=""), "没有词")
expect_raise("37.32 钉住窗不在 5+17k 栅格 ⇒ raise", lambda: _build(1, context_frames=20), "合法窗")
expect_raise("37.33 mask_mode 非法 ⇒ raise", lambda: _build(1, mask_mode="taper2"), "只认")
expect_raise("37.34 length<5 ⇒ raise", lambda: _build(1, length=3), "至少 5")
expect_raise("37.35 width 非 16 倍数 ⇒ raise", lambda: _build(1, width=600), "16 的倍数")
expect_raise("37.36 seed 为负 ⇒ raise", lambda: _build(1, seed=-1), "不能为负")
check("37.37 单段 + 空 run_id **不** raise（首段无需读上一段）",
      _build(1, run_id="")[1][0] == ["t.seg0_samp", 0])

# ---------------------------------------------------------------------------
print("[6] 节点层契约：V3_EXPAND / 输入槽 / 拼条节点行为")
# ---------------------------------------------------------------------------
_FR = NODES.H3RelayFiniteRun
check("37.38 FiniteRun 标了 V3_EXPAND（V3 外壳据此开 enable_expand）",
      getattr(_FR, "V3_EXPAND", False) is True)
_it = _FR.INPUT_TYPES()
check("37.39 输入槽 22 个（16 必填 + 6 可选）",
      len(_it["required"]) == 16 and len(_it["optional"]) == 6,
      "必填 %d / 可选 %d" % (len(_it["required"]), len(_it["optional"])))
check("37.40 四路输出（末段 latent / 画面 / 音频 / 报告）",
      _FR.RETURN_TYPES == ("LATENT", "IMAGE", "AUDIO", "STRING")
      and _FR.RETURN_NAMES == ("last_latent", "images", "audio", "report"))
check("37.41 链接型输入全在 optional（必填链接会堵掉纯文生成/不换词/不用声锚）",
      list(_it["optional"]) == ["image", "prompts", "voice_anchor", "note",
                                "settle_frames", "from_segment"]
      and all(t in ("IMAGE", "LATENT") for t in
              (_it["optional"]["image"][0], _it["optional"]["voice_anchor"][0])))

_JV = NODES.H3RelayJoinAV
_imgs = torch.zeros(2, 4, 4, 3)
_imgs_acc = torch.ones(3, 4, 4, 3)
_aud = {"waveform": torch.zeros(1, 1, 5), "sample_rate": 32000}
_aud_acc = {"waveform": torch.ones(1, 1, 7), "sample_rate": 32000}
_ji, _ja, _jr = _JV().join(_imgs, _aud, _imgs_acc, _aud_acc)
check("37.42 JoinAV 画面沿 batch 维拼（3+2=5 帧）", tuple(_ji.shape) == (5, 4, 4, 3),
      str(tuple(_ji.shape)))
check("37.43 JoinAV 音频沿时间拼（7+5=12 样本）",
      tuple(_ja["waveform"].shape) == (1, 1, 12) and _ja["sample_rate"] == 32000)
check("37.44 JoinAV 报告说明拼了几帧", "5 帧" in _jr and "12 样本" in _jr, _jr[:60])
_ji0, _ja0, _jr0 = _JV().join(_imgs, _aud)
check("37.45 JoinAV 首段（无累计）原样透传", _ji0 is _imgs and _ja0 is _aud)
expect_raise("37.46 画面规格不符 ⇒ raise（不学核心节点静默拉伸）",
             lambda: _JV().join(torch.zeros(2, 8, 4, 3), _aud, _imgs_acc, _aud_acc), "拒绝拼接")
expect_raise("37.47 音频采样率不符 ⇒ raise",
             lambda: _JV().join(_imgs, {"waveform": torch.zeros(1, 1, 5), "sample_rate": 16000},
                                _imgs_acc, _aud_acc), "采样率")

# 37.48 防回归（2026-10-16 审核抓出的真 bug）：可选链接槽（image/voice_anchor）不接线时
#   宿主整个键都不传 ⇒ 签名必须给默认值，否则直接 TypeError（t2v 模式被堵死）。
_raw48 = _FR().run(model="M", clip="C", vae="V", audio_vae="AV", sampler="S", sigmas="G",
                   prompt="词", prompts="", segments=1, seed=0, run_id="r", width=608,
                   height=1056, length=124, context_frames=22, mask_mode="hard",
                   pin_audio=True)
check("37.48 不接 image/voice_anchor 直接调 run() 不炸（可选链接槽有默认值）",
      isinstance(_raw48, dict) and "expand" in _raw48, type(_raw48).__name__)
check("37.49 报告里带「每段词」来源行（哪来的词写进 report，不猜）",
      "每段词" in _raw48["result"][3], _raw48["result"][3].splitlines()[-1][:60])

# —— 37.50~ 入队前校验（2026-10-16 GPU 验收发现「校验太晚」后补）——
#   宿主契约（execution.py:1095-1112）：VALIDATE_INPUTS 在**入队前**调用；链接型入参
#   此时一律是 None（get_input_data 无 execution_list 时走 mark_missing）⇒ 只能判
#   「看得到字面值」的项，看不到的**不许猜** —— 猜错会挡住合法图，比晚拦一次坏图严重。
_ve = _G.validate_finite_inputs_early
check("37.50 全部字面值可见且合法 ⇒ 没意见（None）",
      _ve(prompt="词", prompts="", segments=2, run_id="r") is None)
check("37.51 词块数不够 ⇒ 入队前就报（文案与执行期同一句）",
      "块词" in (_ve(prompt="", prompts="只有一块", segments=2, run_id="r") or ""),
      str(_ve(prompt="", prompts="只有一块", segments=2, run_id="r"))[:50])
check("37.52 多段没 run_id ⇒ 入队前就报",
      "run_id" in (_ve(prompt="词", prompts="", segments=2, run_id="") or ""))
check("37.53 segments 超上限 ⇒ 入队前就报",
      "上限" in (_ve(prompt="词", prompts="", segments=99, run_id="r") or ""))
# 关键护栏：看不到的项（链接型入参 = None）一律放行，绝不误拦
check("37.54 prompts 是链接（None）⇒ 不判交叉项，放行",
      _ve(prompt="词", prompts=None, segments=2, run_id="r") is None)
check("37.55 segments 是链接（None）⇒ 段数相关项全放行",
      _ve(prompt="", prompts="", segments=None, run_id="") is None)
check("37.56 两项都是字面空串（确实没词）⇒ 报「没有词可跑」",
      "没有词可跑" in (_ve(prompt="", prompts="", segments=2, run_id="r") or ""))
_G.validate_finite_params(segments=2, run_id="r")      # 只给两项也不该炸
check("37.57 validate_finite_params 容忍未知项（None 跳过）", True)
expect_raise("37.58 但给了非法值照样 raise（跳过 ≠ 放水）",
             lambda: _G.validate_finite_params(segments=0), "至少为 1")
expect_raise("37.59 单给 context_frames 也照样判（不因为别项缺席而放水）",
             lambda: _G.validate_finite_params(context_frames=23), "合法窗")
# 37.60~ 报错文案：没有 stage_index 入参的节点**不该**带上「段号」话术
#   （否则错误开头读起来像第二个问题）。Trim AV / Post / Upscale / Finite Run 都属此类。
try:
    _FR().run(model="M", clip="C", vae="V", audio_vae="AV", sampler="S", sigmas="G",
              prompt="", prompts="只有一块", segments=2, seed=0, run_id="r", width=608,
              height=1056, length=124, context_frames=22, mask_mode="hard", pin_audio=True)
    _e60 = ""
except RuntimeError as _exc60:
    _e60 = str(_exc60)
check("37.60 无 stage_index 的节点报错不带「段号」话术（读起来不像第二个问题）",
      bool(_e60) and "段号" not in _e60.splitlines()[0], _e60.splitlines()[0][:70])
check("37.61 报错首行点出节点名与关键入参",
      "H3RelayFiniteRun" in _e60.splitlines()[0] and "run_id=r" in _e60.splitlines()[0],
      _e60.splitlines()[0][:70])
check("37.62 报错全文里带上人话原因（不许 splitlines 截断）",
      "块词" in _e60, _e60.splitlines()[1][:60])

# ---------------------------------------------------------------------------
print("[7] 沉降档 settle_frames 随段下传（画布能设、脚本/API 也必须能设 —— 铁律一）")
# ---------------------------------------------------------------------------
#   背景：0.6.38 GPU 验收发现「换镜续接」唯一的旋钮 `settle_frames` 只在画布链上能设，
#   Finite Run 的展开图里压根没写它 ⇒ 脚本侧拿不到（正是铁律一禁止的"两条路不一样"）。
#   它是**裁量**（Trim AV 自己的入参）不是后处理 ⇒ 必须随段下传。
_sd = {n["inputs"].get("settle_frames") for nid, n in _g3.items() if nid.endswith("_trim")}
check("37.63 不传 settle_frames ⇒ 每段 TrimAV 都是 0（默认 = 只裁钉住区，老图逐位不变）",
      _sd == {0}, str(_sd))
_gm1, _om1, _rm1 = _build(2, settle_frames=-1)
_sdm1 = {n["inputs"].get("settle_frames") for nid, n in _gm1.items() if nid.endswith("_trim")}
check("37.64 settle_frames=-1（换镜自动）⇒ 每段 TrimAV 都带 -1", _sdm1 == {-1}, str(_sdm1))
_gn7, _on7, _rn7 = _build(2, settle_frames=7)
_sdn7 = {n["inputs"].get("settle_frames") for nid, n in _gn7.items() if nid.endswith("_trim")}
check("37.65 settle_frames=N（固定多裁）⇒ 每段 TrimAV 都带同一个 N", _sdn7 == {7}, str(_sdn7))
check("37.66 沉降档写进 report（不读源码也能自证生效）",
      "settle_frames=-1" in _rm1 and "settle_frames=0" in _r3,
      next((l for l in _rm1.splitlines() if "settle" in l), "（report 里没有）"))
expect_raise("37.67 settle_frames=-2 ⇒ raise（小于 -1 没有语义）",
             lambda: _build(1, settle_frames=-2), "settle_frames")
expect_raise("37.68 settle_frames 超过 SETTLE_CAP ⇒ raise（对齐 TrimAV 的 widget 上界）",
             lambda: _build(1, settle_frames=CORE.SETTLE_CAP + 1), "settle_frames")
expect_raise("37.69 settle_frames 非整数 ⇒ raise",
             lambda: _build(1, settle_frames="自动"), "settle_frames")
# 37.63b None ⇒ 取默认 0（与同函数里 run_id/note 的 None 惯例一致）。
#   为什么单锁：脚本侧显式传 None 时，旧写法会在 int(None) 上抛**裸 TypeError**
#   （不是 FinitePlanError，也没有「该填什么」的说明）⇒ 调用方看到一句看不懂的崩。
_gn1, _on1, _rn1 = _build(2, settle_frames=None)
check("37.63b settle_frames=None ⇒ 取默认 0（不抛裸 TypeError）",
      {n["inputs"].get("settle_frames") for nid, n in _gn1.items()
       if nid.endswith("_trim")} == {0} and "settle_frames=0" in _rn1)
#   入队前那道门**不**接 settle_frames：它由宿主 schema 的 min/max 把关（-1..SETTLE_CAP），
#   这里多判一次只会造出第二套边界 ⇒ 只要它不因为不认识这个参数而崩就行。
check("37.70 入队前校验不认识 settle_frames 也不崩（边界归宿主 schema 管）",
      _G.validate_finite_inputs_early(prompt="词", prompts="", segments=2, run_id="r") is None)
_raw71 = _FR().run(model="M", clip="C", vae="V", audio_vae="AV", sampler="S", sigmas="G",
                   prompt="词", prompts="", segments=2, seed=0, run_id="r", width=608,
                   height=1056, length=124, context_frames=22, mask_mode="hard",
                   pin_audio=True, settle_frames=-1)
check("37.71 节点层 run() 把 settle_frames 传进展开图（每段 TrimAV 都是 -1）",
      {n["inputs"].get("settle_frames") for nid, n in _raw71["expand"].items()
       if nid.endswith("_trim")} == {-1})
check("37.72 TrimAV 的 widget 上界就是 relay_core.SETTLE_CAP（两处同一个数，不许各写一份）",
      NODES.H3RelayTrimAV.INPUT_TYPES()["optional"]["settle_frames"][1]["max"] == CORE.SETTLE_CAP,
      str(NODES.H3RelayTrimAV.INPUT_TYPES()["optional"]["settle_frames"][1]["max"]))

# ---------------------------------------------------------------------------
print("[8] 桥自取 context（0.6.37 免接线）：接了就照接、没接就自取、取不到就 raise")
# ---------------------------------------------------------------------------
import shutil as _sh2
import tempfile as _tf2

_BRG = NODES.H3RelayCopyBridge
_OLD_SP = NODES._stage_path
_TMP2 = _tf2.mkdtemp(prefix="h3auto_")
NODES._stage_path = lambda rid, idx: os.path.join(_TMP2, str(rid),
                                                  "stage_%05d.safetensors" % int(idx))


def _lat(t=12, a=48, seed=0):
    g = torch.Generator().manual_seed(seed)
    return {"samples": CORE._nested_pair(torch.rand(1, 4, t, 8, 8, generator=g),
                                         torch.rand(1, 2, 2, a, generator=g))}


_lt = _lat(seed=11)
_o1 = _BRG().bridge(_lt, None, 22, run_id="auto", stage_index=0)
check("37.70 不接 context_latent + 段号 0 ⇒ 直通（trim=0、原样返回）",
      _o1[0] is _lt and int(_o1[2]) == 0 and "直通" in _o1[1], "trim=%s" % _o1[2])

try:
    _BRG().bridge(_lt, None, 22, run_id="auto", stage_index=1)
    check("37.71 不接 + 段号 1 + 落盘文件不存在 ⇒ raise", False, "竟然没抛")
except RuntimeError as _e:
    check("37.71 不接 + 段号 1 + 落盘文件不存在 ⇒ raise（不许静默直通）",
          "静默直通" in str(_e) and "自取" in str(_e), str(_e).splitlines()[0][:60])

_sv2 = NODES.H3RelayLatentSave()
getattr(_sv2, NODES.H3RelayLatentSave.FUNCTION)(_lat(seed=12), "auto", 0, "auto seed")
_o2 = _BRG().bridge(_lat(seed=13), None, 22, run_id="auto", stage_index=1)
check("37.72 不接 + 段 0 已落盘 ⇒ 桥自取成功（trim=22，与接线同结果）",
      int(_o2[2]) == 22, "trim=%s" % _o2[2])

_o3 = _BRG().bridge(_lat(seed=13), _lat(seed=12), 22, run_id="auto", stage_index=1)
check("37.73 接了 context_latent ⇒ 以接线为准（自取不介入，存量图逐位不变）",
      int(_o3[2]) == 22, "trim=%s" % _o3[2])

NODES._stage_path = _OLD_SP
_sh2.rmtree(_TMP2, ignore_errors=True)

# ---------------------------------------------------------------------------
print("[9] from_segment：从第 N 段起跑（段号整体平移，0.6.39）")
# ---------------------------------------------------------------------------
#   为什么要有它：无头路径此前**只能**从第 1 段起跑 ⇒ 「视频起段 → 一次提交跑完剩下的段」
#   这条最常用的路走不通，只能分两次提交或手搓链（手搓链 = 第二套实现，铁律一禁止）。
#   画布 Chain 早就能填 stage_index，这里只是把同一能力补到无头路径。
_gf, _of, _rf = _build(3, from_segment=1)
_stf = [n["inputs"]["stage_index"] for nid, n in sorted(_gf.items())
        if nid.endswith(("_bridge", "_save"))]
check("37.74 from_segment=1 ⇒ 桥与落盘的段号整体平移成 1,2,3", _stf == [1, 1, 2, 2, 3, 3], str(_stf))
check("37.75 from_segment=0 ⇒ 与旧行为逐位一致（仍是 0,1,2）",
      [n["inputs"]["stage_index"] for nid, n in sorted(_g3.items())
       if nid.endswith(("_bridge", "_save"))] == [0, 0, 1, 1, 2, 2])
check("37.76 报告写明实际段号区间与「首段读哪一份续接」",
      "stage_00001..00003" in _rf and "首段读 stage_00000 续接" in _rf,
      next((l for l in _rf.splitlines() if "边车" in l), "")[:70])
check("37.77 from_segment=0 时报告说「首段不接上一段（直通）」",
      "首段不接上一段（直通）" in _build(2)[2])
expect_raise("37.78 from_segment 为负 ⇒ raise", lambda: _build(1, from_segment=-1), "不能为负")
# ⚠ 只挡**int() 解不出**的值：`"1"` 这种能解析的串与 segments/seed 同口径（容忍，不自作严格）
expect_raise("37.79 from_segment 是非数字串 ⇒ raise",
             lambda: _build(1, from_segment="第一段"), "整数")
_gnf, _onf, _rnf = _build(2, from_segment=None)
check("37.80 from_segment=None ⇒ 取默认 0（不抛裸 TypeError）",
      {n["inputs"]["stage_index"] for nid, n in _gnf.items() if nid.endswith("_save")} == {0, 1})
# 节点层：起始段文件不在 ⇒ **开跑前**就报（不等模型上卡）；在 ⇒ 直通建图
import shutil as _sh3
import tempfile as _tf3
_OLD_SP3 = NODES._stage_path
_TMP3 = _tf3.mkdtemp(prefix="h3fs_")
NODES._stage_path = lambda rid, idx: os.path.join(_TMP3, str(rid),
                                                  "stage_%05d.safetensors" % int(idx))
try:
    _FR().run(model="M", clip="C", vae="V", audio_vae="AV", sampler="S", sigmas="G",
              prompt="词", prompts="", segments=2, seed=0, run_id="fsx", width=608,
              height=1056, length=124, context_frames=22, mask_mode="hard",
              pin_audio=True, from_segment=1)
    check("37.81 起始段边车不在 ⇒ run() 开跑前就 raise", False, "竟然没抛")
except RuntimeError as _e81:
    check("37.81 起始段边车不在 ⇒ run() 开跑前就 raise（不等模型上卡）",
          "stage_00000" in str(_e81) and "from_segment=1" in str(_e81),
          str(_e81).splitlines()[0][:70])
_sv3 = NODES.H3RelayLatentSave()
getattr(_sv3, NODES.H3RelayLatentSave.FUNCTION)(_lat(seed=21), "fsx", 0, "seed for fs")
_raw82 = _FR().run(model="M", clip="C", vae="V", audio_vae="AV", sampler="S", sigmas="G",
                   prompt="词", prompts="", segments=2, seed=0, run_id="fsx", width=608,
                   height=1056, length=124, context_frames=22, mask_mode="hard",
                   pin_audio=True, from_segment=1)
check("37.82 起始段边车在 ⇒ 正常建图，段号 1,2 且不读第 0 段以外的东西",
      {n["inputs"]["stage_index"] for nid, n in _raw82["expand"].items()
       if nid.endswith("_save")} == {1, 2})
check("37.83 from_segment=1 时 report 里点名要读的那一份（不读源码也能自证）",
      "首段读 stage_00000 续接" in _raw82["result"][3])
NODES._stage_path = _OLD_SP3
_sh3.rmtree(_TMP3, ignore_errors=True)

