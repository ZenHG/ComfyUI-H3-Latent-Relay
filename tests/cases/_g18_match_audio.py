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

# ---------------------------------------------------------------- 第 18 组：路径 4 复现残留（0.5.0）
# 依据 RESEARCH_seam_frontier §13 D7：现有三路看不见复现残留
# （复现帧清晰 / 色档一致 / 无硬跳）→ 用「到钉住区的最小 MAE」单列第 4 路。
print()
print("### 第 18 组：路径 4 复现残留（D7）")

torch.manual_seed(20260917)
_PIN = 22
_HH, _WW = 32, 32
_base = torch.rand(_PIN, _HH, _WW, 3)
_new = torch.rand(10, _HH, _WW, 3)

# 18.1 纯复现：窗内前 5 帧 = 钉住区内容的复刻 → run 应为 5
_rep = _base[[0, 3, 7, 11, 15]]
_imgs = torch.cat([_base, _rep, _new], 0)
_s, _sig, _ref = CORE.scan_head_repeat(_imgs, _PIN, scan=10)
check("18.1 复现残留 → settle = 连续复现帧数（5）", _s == 5, "settle=%s" % _s)

# 18.2 无复现：窗内全是新内容 → 0
_imgs2 = torch.cat([_base, _new], 0)
_s2, _sig2, _ref2 = CORE.scan_head_repeat(_imgs2, _PIN, scan=10)
check("18.2 无复现 → 0", _s2 == 0, "settle=%s sig=%.4f" % (_s2, _sig2))

# 18.3 只复现 1 帧 → 不足 REPEAT_MIN_RUN → 0（防单帧巧合）
_imgs3 = torch.cat([_base, _base[0:1], _new], 0)
_s3, _, _ = CORE.scan_head_repeat(_imgs3, _PIN, scan=10)
check("18.3 仅 1 帧复现 → 0（防单帧巧合）", _s3 == 0, "settle=%s" % _s3)

# 18.4 测不准的输入 → 0（pin=0 / 窗短于 MIN_RUN），且不得抛
_ok = True
try:
    _a = CORE.scan_head_repeat(_imgs, 0, scan=10)[0]
    _b = CORE.scan_head_repeat(_imgs, _PIN, scan=1)[0]
except Exception as _e:
    _ok = False
    _a = _b = "raise:%s" % _e
check("18.4 pin=0 / 窗过短 → 0 且不抛", _ok and _a == 0 and _b == 0, "a=%s b=%s" % (_a, _b))

# 18.5 detect_settle 契约不变：仍返回三元组
_d = CORE.detect_settle(_imgs, _PIN)
check("18.5 detect_settle 仍返回三元组（契约不变）",
      isinstance(_d, tuple) and len(_d) == 3, "ret=%r" % (_d,))

# 18.5b 显式开启本路 → 复现段被报出（settle > 0）
_old = CORE.SETTLE_REPEAT_PATH
try:
    CORE.SETTLE_REPEAT_PATH = True
    _d_on = CORE.detect_settle(_imgs, _PIN)
finally:
    CORE.SETTLE_REPEAT_PATH = _old
check("18.5b 开启本路 → 复现段被报出（settle > 0）", int(_d_on[0]) > 0,
      "settle_on=%s" % (_d_on[0],))

# 18.6 开关关掉 ⇒ 该路不出候选
try:
    CORE.SETTLE_REPEAT_PATH = False
    _d_off = CORE.detect_settle(_imgs, _PIN)
finally:
    CORE.SETTLE_REPEAT_PATH = _old
check("18.6 SETTLE_REPEAT_PATH=False → 该路不出候选", int(_d_off[0]) == 0,
      "settle_off=%s" % (_d_off[0],))

# 18.7 常量与开关存在（供上层/文档引用）
check("18.7 常量 REPEAT_MAE / REPEAT_MIN_RUN / REPEAT_SCAN 齐备",
      abs(CORE.REPEAT_MAE - 5.0 / 255.0) < 1e-9
      and CORE.REPEAT_MIN_RUN >= 2 and CORE.REPEAT_SCAN >= 8)

# 🔴 18.8 契约钉子：**本路默认关闭**
# 动机（实测发现）：本路只会让裁量变大，而既有契约是「宁可维持旧行为也不赌」。
#   它在**低纹理 / 周期内容**上会误报——合成夹具 seam_seg（3 帧周期）下
#   `pin` 之后的帧必然与钉住区某帧逐位相同 ⇒ 必报；真实静态镜头同理。
#   ⇒ 默认 False；启用前必须用真实渲染验证误报率。
check("18.8 路径 4 **默认关闭**（默认行为与 0.4.x 逐位一致）",
      CORE.SETTLE_REPEAT_PATH is False, "默认=%r" % (CORE.SETTLE_REPEAT_PATH,))

# 18.9 默认关闭时，既有契约不受影响：切换点落在钉住区内 → settle 仍为 0
check("18.9 默认关闭下：切换点落在钉住区内 → settle=0（既有契约保持）",
      int(CORE.detect_settle(_imgs, _PIN)[0]) == 0
      if CORE.SETTLE_REPEAT_PATH is False else True,
      "settle=%s" % (CORE.detect_settle(_imgs, _PIN)[0],))

# ---------------------------------------------------------------- 第 19 组：跨段统计匹配（§2 方向二）
# 依据 RESEARCH_seam_frontier §2：色档漂移是**低阶统计量现象**，一阶+二阶矩理论上充分。
# 与 lowfreq_pull 的区别：后者只做低频加性（一阶）；本组补二阶（对比度）+ 逐通道色度。
print()
print("### 第 19 组：跨段统计匹配 match_prev_stats（§2）")

torch.manual_seed(7)
# ⚠ 数据量级按**真实缝阶跃**取（copy 桥实测 0.0402，远小于 offset_max=0.06），
#   否则会被护栏截断——那是**设计行为**，不是 bug（另见 19.1b）。
_g = torch.rand(1, 16, 16, 3) * 0.40 + 0.30          # 目标（上段末帧）
_z = torch.rand(6, 16, 16, 3) * 0.50 + 0.26          # 源（段头）：均值差 ≈0.04、对比度更高

# 19.1 对齐后：段头首帧的逐通道均值/标准差 ≈ 目标
# ⚠ 容差说明：统计量是**从 guide 与整个作用区聚合**算的（不逐帧），且带线性权重衰减
#   ⇒ 单帧自己的统计量只能**近似**等于目标。这是为时间平滑而做的设计取舍，不是误差。
_o = CORE.match_prev_stats(_z, _g, frames=6, weight=1.0)
_m_ok = torch.allclose(_o[0].mean(dim=(0, 1)), _g[0].mean(dim=(0, 1)), atol=0.02)
_s_ok = torch.allclose(_o[0].std(dim=(0, 1)), _g[0].std(dim=(0, 1)), rtol=0.25)
check("19.1 一阶+二阶矩对齐到目标（均值/标准差）", bool(_m_ok and _s_ok),
      "dμ=%.4f dσ=%.4f" % (float((_o[0].mean(dim=(0, 1)) - _g[0].mean(dim=(0, 1))).abs().max()),
                           float((_o[0].std(dim=(0, 1)) - _g[0].std(dim=(0, 1))).abs().max())))

# 19.1b 护栏按设计截断：均值差远超 offset_max 时，偏移只走 offset_max
_g_far = _g + 0.40                                   # 均值差 ≈0.40 >> 0.06
_o_far = CORE.match_prev_stats(_z, _g_far, frames=6, weight=1.0)
_dmu = float((_o_far[0].mean(dim=(0, 1)) - _z[0].mean(dim=(0, 1))).abs().max())
check("19.1b 护栏截断：均值差 >> offset_max → 只走 offset_max（不整段换色）",
      _dmu <= 0.06 + 0.02, "实际偏移 %.4f（上限 0.06）" % _dmu)

# 19.2 weight=0 ⇒ 逐位不变（默认关，旧行为）
check("19.2 weight=0 → 逐位不变（默认关）", torch.equal(CORE.match_prev_stats(_z, _g, 6, 0.0), _z))

# 19.3 画布不一致 ⇒ 原样返回（安全兜底，不得抛）
_g_bad = torch.rand(1, 8, 8, 3)
check("19.3 画布不一致 → 原样返回（不抛）",
      torch.equal(CORE.match_prev_stats(_z, _g_bad, 6, 1.0), _z))

# 19.4 帧数守恒 + 尾端不动（权重线性衰减到 0）
check("19.4 帧数守恒", int(_o.shape[0]) == int(_z.shape[0]),
      "%d vs %d" % (int(_o.shape[0]), int(_z.shape[0])))
check("19.4b 作用区外逐位不动（尾端权重=0）", torch.equal(_o[6:], _z[6:]))

# 19.5 护栏：增益被截断（源 σ 极小 → 未截断会爆掉）
_z_flat = torch.full((4, 16, 16, 3), 0.5)
_z_flat[0, 0, 0, 0] = 0.5001                          # 极小 σ
_o_flat = CORE.match_prev_stats(_z_flat, _g, frames=4, weight=1.0)
_dev = float((_o_flat - _z_flat).abs().max())
check("19.5 护栏生效：σ源极小 → 增益被截断，不爆", _dev < 0.35, "最大改动 %.4f" % _dev)

# 19.6 无重影代理判据：修正是**逐通道仿射**⇒ 每个通道与源的空间相关性仍 ≈1
#   （⚠ 必须**逐通道**算：三个通道增益不同，拉平算会混掉，得不到 1.0）
_corrs = []
for _ch in range(3):
    _a = _z[0, :, :, _ch].flatten()
    _b = _o[0, :, :, _ch].flatten()
    _corrs.append(float(torch.corrcoef(torch.stack([_a, _b]))[0, 1]))
check("19.6 只对齐统计量（逐通道仿射）⇒ 空间结构不被复制（相关性≈1）",
      min(_corrs) > 0.999, "逐通道 corr=%s" % ["%.6f" % c for c in _corrs])

# 19.7 节点层：新 widget **追加在 optional 末位**（旧工作流取值不前移）
# 🔴 2026-09-21：`run_id`（E3/E4 观测用）追加在末位 ⇒ 尾部断言跟着延长一位。
# 🔴 2026-09-22：`save_pcm`（音频 PCM 边车）再追加一位。
# 🔴 2026-09-25：`diagnostics`（只读观测总闸，默认关）再追加一位。
#   本断言的作用是「**防止有人把新 widget 插到中间**」⇒ 延长尾部列表即可，不是放宽。
_opt19 = list(NODES.H3RelayTrimAV.INPUT_TYPES()["optional"])
check("19.7 新 widget 追加在 optional 末位（前缀顺序稳定）",
      _opt19[-7:] == ["match_prev", "match_prev_frames", "match_prev_gain_max",
                      "match_prev_offset_max", "run_id", "save_pcm", "diagnostics"],
      "尾部=%s" % (_opt19[-7:],))

# 19.8 只读观测的总闸（2026-09-25 本仓作者 拍板：**对外默认关**，本地产线入口显式开）
#   三路观测（E3 DTW 代价 / 裁量→跳跃曲线 / E4 外观三元组）**都不参与裁量**（纯打印）⇒
#   默认关**不改变任何帧/latent/音频**，只省 CPU（实测三路合计 0.361 s/段 @0.796MP/90 帧）。
_diag_img = torch.rand(50, 16, 16, 3)
_diag_img[22:] += 0.10                                   # 造出「钉住区之后」的差异供观测
_diag_off = NODES.H3RelayTrimAV().trim(_diag_img, trim_frames=22, fps=24.0, settle_frames=0)[2]
_diag_on = NODES.H3RelayTrimAV().trim(_diag_img, trim_frames=22, fps=24.0, settle_frames=0,
                                      diagnostics=True)[2]
check("19.7b 诊断默认关 ⇒ 报告里无 DTW / 跳跃曲线 / 外观三元组（对外省 ~0.4 s/段）",
      ("DTW" not in _diag_off) and ("跳跃曲线" not in _diag_off)
      and ("外观三元组" not in _diag_off), "report=%r" % _diag_off[-80:])
check("19.7c diagnostics=True ⇒ 三路观测全回来（本地产线入口就是这么传的）",
      ("DTW" in _diag_on) and ("跳跃曲线" in _diag_on) and ("外观三元组" in _diag_on),
      "report=%r" % _diag_on[-80:])
_diag_off_img = NODES.H3RelayTrimAV().trim(_diag_img, trim_frames=22, fps=24.0, settle_frames=0)[0]
_diag_on_img = NODES.H3RelayTrimAV().trim(_diag_img, trim_frames=22, fps=24.0, settle_frames=0,
                                          diagnostics=True)[0]
check("19.7d 开关诊断**不改变裁切结果**（帧数一致、逐位相同）",
      int(_diag_off_img.shape[0]) == int(_diag_on_img.shape[0])
      and torch.equal(_diag_off_img, _diag_on_img),
      "%d vs %d" % (int(_diag_off_img.shape[0]), int(_diag_on_img.shape[0])))

# 19.8 节点层：默认全关（不接线时行为与 0.4.x 逐位一致）
_it19 = NODES.H3RelayTrimAV.INPUT_TYPES()["optional"]
check("19.8 默认全关：match_prev=0 / frames=12 / gain=1.15 / off=0.06",
      _it19["match_prev"][1]["default"] == 0.0
      and _it19["match_prev_frames"][1]["default"] == 12
      and abs(_it19["match_prev_gain_max"][1]["default"] - 1.15) < 1e-9
      and abs(_it19["match_prev_offset_max"][1]["default"] - 0.06) < 1e-9)

# ---- 19.9~19.12：🔴 2026-09-17 真渲染抓到的口径 bug 与修正（统计量取几帧）----
# 事故：`stats_frames` 旧口径 = 对整个作用区聚合，而 guide 是**单帧** ⇒ 段头**内部有亮度梯度**时
#   （实测：首帧已到 guide 水平、第 2 帧起掉 ~0.008），聚合均值被后续帧拉低 ⇒ offs 变成
#   「段头平均 vs guide」的差 ⇒ **首帧被推过 guide**，缝上凭空多出一个阶跃。
#   真渲染两臂（唯一变量 = match_prev 0↔0.7）实测：纯末→首阶跃 0.0007 → 0.0088（×12.6）。
#   目标函数是「**首帧 ≈ guide**」，不是「段头均值 ≈ guide」。
torch.manual_seed(11)
_gi = torch.full((1, 16, 16, 3), 0.50)                 # 上段末帧 = guide
_h0 = torch.full((1, 16, 16, 3), 0.495)                # 段头首帧：已贴住 guide（差 0.005）
_hrest = torch.full((5, 16, 16, 3), 0.470)             # 段头第 2 帧起更暗 ⇒ 内部梯度 0.025
_grad = torch.cat([_h0, _hrest], dim=0)
_gap_before = float((_grad[0].mean() - _gi[0].mean()).abs())

_o1 = CORE.match_prev_stats(_grad, _gi, frames=6, weight=1.0)              # 默认：取紧贴缝那帧
_gap1 = float((_o1[0].mean() - _gi[0].mean()).abs())
check("19.9 统计量取紧贴缝那帧 ⇒ 首帧落到 guide（不越过）",
      _gap1 < _gap_before * 0.05, "阶跃 %.4f → %.4f" % (_gap_before, _gap1))

_o0 = CORE.match_prev_stats(_grad, _gi, frames=6, weight=1.0, stats_frames=0)   # 旧口径
_gap0 = float((_o0[0].mean() - _gi[0].mean()).abs())
check("19.10 旧口径（作用区聚合）会把首帧推过 guide ⇒ 阶跃反而变大（复现真渲染事故）",
      _gap0 > _gap_before * 2.0, "阶跃 %.4f → %.4f（×%.1f）" % (_gap_before, _gap0,
                                                              _gap0 / max(_gap_before, 1e-9)))

# 19.11 段头**无内部梯度**时两种口径必须等价（修正不能顺带改变正常情形）
_hom = torch.full((6, 16, 16, 3), 0.495)
_a = CORE.match_prev_stats(_hom, _gi, frames=6, weight=1.0)
_b = CORE.match_prev_stats(_hom, _gi, frames=6, weight=1.0, stats_frames=0)
check("19.11 无梯度段头：两种口径结果一致（修正只针对梯度情形）",
      torch.allclose(_a, _b, atol=1e-6), "max|Δ|=%.3e" % float((_a - _b).abs().max()))

# 19.12 节点层：新 widget 一律**追加在 H3RelayPost 的 optional 末位**（TrimAV 保持冻结）
#   2026-09-19 末两位换成 baseline / cross_seg_ack（追加 ⇒ 旧图 widgets_values 取值位置不变）
_opt19b = list(NODES.H3RelayPost.INPUT_TYPES()["optional"])
_it19b = NODES.H3RelayPost.INPUT_TYPES()["optional"]
check("19.12 新 widget 追加在 H3RelayPost optional 末位（baseline / cross_seg_ack）",
      _opt19b[-2:] == ["baseline", "cross_seg_ack"]
      and _it19b["baseline"][0] == ["robust", "legacy"]
      and _it19b["baseline"][1]["default"] == "robust"
      and _it19b["cross_seg_ack"][1]["default"] is False
      and _it19b["match_prev_stats_frames"][1]["default"] == 1,
      "末两位=%s baseline=%s ack=%s" % (_opt19b[-2:], _it19b["baseline"][1]["default"],
                                        _it19b["cross_seg_ack"][1]["default"]))

# ---------------------------------------------------------------- 第 20 组：拆节点（H3RelayPost）
# 动机（2026-09-17）：后处理 15 个旋钮原塞在 TrimAV 里，而 UI 工作流的 widgets_values 是
# **按位置**存的 ⇒ 「新 widget 只追加末位」把 TrimAV 顶到 22 个 widget。
# 拆出 `H3RelayPost` 后：TrimAV **冻结不再长**，后处理从零开始、以后新功能只加在新节点上。
print()
print("### 第 20 组：拆节点 H3RelayPost（后处理独立）")

# 20.1 已注册
check("20.1 H3RelayPost 已注册", hasattr(NODES, "H3RelayPost")
      and "H3RelayPost" in getattr(NODES, "NODE_CLASS_MAPPINGS", {}),
      "mappings=%s" % (list(getattr(NODES, "NODE_CLASS_MAPPINGS", {}).keys()),))

_it20 = NODES.H3RelayPost.INPUT_TYPES()
_req20, _opt20 = list(_it20["required"]), list(_it20["optional"])

# 20.2 只有 images 是必填；其余全 optional（老工作流不受影响）
check("20.2 必填只有 images（其余全 optional）", _req20 == ["images"], "required=%s" % (_req20,))

# 20.3 默认全关（不接线时逐位直通）
_defaults_off = all(
    _it20["optional"][k][1].get("default") == 0.0
    for k in ("match_prev", "lowfreq_pull", "hist_match", "wb_match",
              "deconv_strength", "detail_borrow", "settle_sharpen"))
check("20.3 默认全关（不接线时行为不变）", _defaults_off)

# 20.4 默认全关 → 逐位直通 + 帧数守恒
_pin20 = torch.rand(8, 16, 16, 3)
_g20 = torch.rand(1, 16, 16, 3)
_o20, _r20 = NODES.H3RelayPost().apply(_pin20, _g20)
check("20.4 默认全关 → 逐位直通且帧数守恒",
      torch.equal(_o20, _pin20) and int(_o20.shape[0]) == 8, "report=%s" % _r20)

# 20.5 🔴 2026-09-19 R4 落地：**跨段项默认弃权**（guide 未确认是真参照时不作用）
#   依据：cond 桥下 prev_tail 只是近似（代理误差 0.006 > 要修的缝阶跃 0.0007），
#        实测对齐它把缝阶跃放大 ×12.6 ⇒ 默认关，要显式打勾才作用。
_ok5, _o5 = True, None
try:
    _o5, _r5 = NODES.H3RelayPost().apply(_pin20, None, match_prev=0.5, lowfreq_pull=0.5)
except Exception as _e:
    _ok5, _r5 = False, "raise:%s" % _e
check("20.5 跨段项未确认参照 → 自动弃权（不抛、逐位直通）",
      _ok5 and torch.equal(_o5, _pin20) and "自动弃权" in _r5, "report=%s" % _r5)

# 20.5b 打了勾但未接 guide → 走「跳过」分支（不抛）
_o5b, _r5b = NODES.H3RelayPost().apply(_pin20, None, match_prev=0.5, cross_seg_ack=True)
check("20.5b 已打勾 + 未接 guide → 跳过（不抛）",
      torch.equal(_o5b, _pin20) and "跳过" in _r5b, "report=%s" % _r5b)

# 20.6 打勾 + 接了 guide → 跨段项生效（段头被改动，帧数守恒）
_o6, _r6 = NODES.H3RelayPost().apply(_pin20, _g20, match_prev=0.5, cross_seg_ack=True)
check("20.6 打勾 + 接了 guide → 跨段统计匹配生效（帧数守恒）",
      int(_o6.shape[0]) == 8 and not torch.equal(_o6, _pin20), "report=%s" % _r6)

# 20.6b 关键负向：**不打勾时即使接了 guide 也必须逐位直通**（防重演 ×12.6 事故）
_o6b, _r6b = NODES.H3RelayPost().apply(_pin20, _g20, match_prev=0.5)
check("20.6b 未打勾（默认）⇒ 接了 guide 也逐位直通（×12.6 事故回归钉）",
      torch.equal(_o6b, _pin20) and "自动弃权" in _r6b, "report=%s" % _r6b)

# —— 20.6c~20.6g：2026-09-19 视频侧三项改造（稳健基准 / 互斥组 / 逐层审计）——
# 用 **90 帧** 段（>body_start=40，段体非空）；段体离散度低（不触发弃权）。
torch.manual_seed(23)
_pin90 = torch.cat([torch.rand(24, 8, 8, 3) * 0.10 + 0.20,
                    torch.rand(66, 8, 8, 3) * 0.20 + 0.60], 0)
_o6c, _r6c = NODES.H3RelayPost().apply(_pin90, None, hist_match=1.0, wb_match=1.0,
                                       deconv_strength=1.0, detail_borrow=1.0)
check("20.6c 互斥组：组 2 只作用直方图、组 3 只作用反卷积（报告点名）",
      "组 2 互斥" in _r6c and "组 3 互斥" in _r6c
      and "白平衡校正" not in _r6c and "尺度" not in _r6c
      and int(_o6c.shape[0]) == 90,
      "report=%s" % _r6c[:130])
check("20.6d 逐层审计：报告含「↳ 直方图匹配 后：段头亮度 … 高频 …」",
      "↳ 直方图匹配 后：段头亮度" in _r6c and "高频" in _r6c, "report=%s" % _r6c[:130])

# 20.6e 段体基准离散度超阈 ⇒ 组 2/3 弃权（稳健基准的弃权合同）
# 段体（第 40 帧之后）= 17 帧亮 + 33 帧暗 ⇒ p25=0.2 / p75=0.8 / 中位 0.2 ⇒ 离散度 3.0 ≫ 0.08
_disp90 = torch.cat([torch.full((24, 4, 4, 3), 0.30),
                     torch.full((33, 4, 4, 3), 0.80),
                     torch.full((33, 4, 4, 3), 0.20)], 0)
_od, _rd = NODES.H3RelayPost().apply(_disp90, None, hist_match=1.0)
check("20.6e 段体离散度超阈（baseline=robust 默认）⇒ 弃权 + 报告说明",
      torch.equal(_od, _disp90) and "弃权" in _rd, "report=%s" % _rd[:130])
_ol, _rl = NODES.H3RelayPost().apply(_disp90, None, hist_match=1.0, baseline="legacy")
check("20.6f baseline=legacy ⇒ 不弃权（旧口径可复现对照）",
      not torch.equal(_ol, _disp90) and "弃权" not in _rl, "report=%s" % _rl[:130])

# 20.6g 段长 ≤ body_start ⇒ 段内对齐类跳过（段体为空，不许硬改）
_o6g, _r6g = NODES.H3RelayPost().apply(_pin20, None, hist_match=1.0)
check("20.6g 段长 ≤ 40 ⇒ 段内对齐类跳过（逐位直通）",
      torch.equal(_o6g, _pin20) and "跳过" in _r6g, "report=%s" % _r6g)

# 20.6h 正常段（离散度低）⇒ robust 与 legacy **都生效、都不弃权**（稳健化不误伤好段）
_ohr, _rhr = NODES.H3RelayPost().apply(_pin90, None, hist_match=1.0)
_ohl, _rhl = NODES.H3RelayPost().apply(_pin90, None, hist_match=1.0, baseline="legacy")
check("20.6h 正常段：robust 与 legacy 都生效且都不弃权（稳健化不误伤）",
      (not torch.equal(_ohr, _pin90)) and (not torch.equal(_ohl, _pin90))
      and "弃权" not in _rhr and "弃权" not in _rhl, "robust报告=%s" % _rhr[:90])

# 20.7 TrimAV 新增第 4 路输出 prev_tail（**追加在末位**，旧工作流不受影响）
check("20.7 TrimAV 第 4 路输出 prev_tail 追加在末位",
      NODES.H3RelayTrimAV.RETURN_TYPES[:3] == ("IMAGE", "AUDIO", "STRING")
      and NODES.H3RelayTrimAV.RETURN_TYPES[3] == "IMAGE"
      and NODES.H3RelayTrimAV.RETURN_NAMES[3] == "prev_tail",
      "types=%s names=%s" % (NODES.H3RelayTrimAV.RETURN_TYPES,
                             NODES.H3RelayTrimAV.RETURN_NAMES))

# 20.8 prev_tail 真的等于「钉住区最后一帧 = 上段末帧」
_seg20 = torch.rand(50, 16, 16, 3)
_out20 = unwrap(NODES.H3RelayTrimAV().trim(_seg20, trim_frames=22, fps=24.0, settle_frames=0))
check("20.8 prev_tail == 上段末帧（= images[pin-1]）",
      len(_out20) == 4 and int(_out20[3].shape[0]) == 1
      and torch.equal(_out20[3][0], _seg20[21]),
      "len=%d" % len(_out20))

# 20.9 分工钉子：TrimAV 不再新增后处理件（本组新件全在新节点上）
check("20.9 后处理件都在 H3RelayPost 上（TrimAV 冻结）",
      all(k in _opt20 for k in ("match_prev", "lowfreq_pull", "hist_match",
                                "wb_match", "deconv_strength", "detail_borrow",
                                "settle_sharpen")),
      "post optional=%d 个" % len(_opt20))

# ---------------------------------------------------------------- 第 21 组：blend 重叠区双向融合（§4）
# 依据 RESEARCH_seam_frontier §4：拷贝桥是「单向写入」；理论更优是重叠区由**两个独立估计**
# 加权平均，权重取**窗函数**（FlowLong / Unified Long Video Inpainting 的滑窗 Hamming 混合）。
# 与 ramp 的关系：同一套掩码语义、**不同的权重曲线**（线性 vs 窗形）。
print()
print("### 第 21 组：blend 重叠区双向融合（§4）")

# 21.1 已注册为一种 mask_mode
check("21.1 blend 已进 MASK_MODES", "blend" in CORE.MASK_MODES, "%s" % (CORE.MASK_MODES,))

# 21.2 端点：远端严格 0、缝端严格 blend_top
_w21 = CORE.prefix_blend_weights(7, 0.5, 0, "smoothstep")
check("21.2 端点：远端 0 / 缝端 = blend_top",
      abs(_w21[0]) < 1e-9 and abs(_w21[-1] - 0.5) < 1e-9,
      "w=%s" % [round(x, 4) for x in _w21])

# 21.3 窗形两端**导数为 0**（与 ramp 的线性曲线本质区别）
_lin = CORE.prefix_ramp_weights(9, 0.5, 0)
_win = CORE.prefix_blend_weights(9, 0.5, 0, "smoothstep")
_d0 = abs((_win[1] - _win[0]) - (_lin[1] - _lin[0]))          # 首段斜率差（窗形应更小）
_d1 = abs((_win[-1] - _win[-2]) - (_lin[-1] - _lin[-2]))      # 末段斜率差
check("21.3 窗形两端斜率 < 线性（S 曲线特征）",
      (_win[1] - _win[0]) < (_lin[1] - _lin[0])
      and (_win[-1] - _win[-2]) < (_lin[-1] - _lin[-2]),
      "首段 窗=%.4f 线=%.4f ｜ 末段 窗=%.4f 线=%.4f"
      % (_win[1] - _win[0], _lin[1] - _lin[0], _win[-1] - _win[-2], _lin[-1] - _lin[-2]))

# 21.4 两种窗形都单调升、都落在 [0, top]
for _sh in CORE.BLEND_SHAPES:
    _w = CORE.prefix_blend_weights(12, 0.6, 0, _sh)
    _mono = all(_w[i] <= _w[i + 1] + 1e-9 for i in range(len(_w) - 1))
    _rng = all(-1e-9 <= x <= 0.6 + 1e-9 for x in _w)
    check("21.4 窗形 %s：单调升且在 [0, top]" % _sh, _mono and _rng,
          "%s" % [round(x, 3) for x in _w])

# 21.5 blend_tokens>0 → 只融缝端 k 个，更早恒 0（「只融缝、锁运动」）
_w5 = CORE.prefix_blend_weights(7, 0.5, 2, "smoothstep")
check("21.5 blend_tokens=2 → 前 5 个恒 0、末 2 个升",
      all(abs(x) < 1e-9 for x in _w5[:5]) and _w5[5] < _w5[6] and abs(_w5[6] - 0.5) < 1e-9,
      "w=%s" % [round(x, 4) for x in _w5])

# 21.6 blend_top=0 → 退化成 hard（前缀全 0）
check("21.6 blend_top=0 → 退化为 hard（全 0）",
      all(abs(x) < 1e-9 for x in CORE.prefix_blend_weights(7, 0.0, 0, "smoothstep")))

# 21.7 与 ramp 曲线不同（同 top 下中点相同、四分点不同 —— 证明不是同一个函数）
_w7 = CORE.prefix_blend_weights(9, 1.0, 0, "smoothstep")
_l7 = CORE.prefix_ramp_weights(9, 1.0, 0)
check("21.7 blend 与 ramp 是不同曲线（四分点不同）",
      abs(_w7[2] - _l7[2]) > 0.05, "blend[2]=%.4f ramp[2]=%.4f" % (_w7[2], _l7[2]))

# 21.8 节点 widget：mask_mode 含 blend（旧值原序保留）
_it21 = NODES.H3RelayCopyBridge.INPUT_TYPES()
_mm = _it21["optional"]["mask_mode"][0]
check("21.8 节点 mask_mode 追加 blend（旧 hard/taper/ramp 原序保留）",
      _mm[:3] == ["hard", "taper", "ramp"] and "blend" in _mm, "%s" % (_mm,))

# 21.9 节点默认仍是 hard（不接线时行为与 0.4.x 一致）
check("21.9 节点 mask_mode 默认仍是 hard",
      _it21["optional"]["mask_mode"][1]["default"] == "hard",
      "%s" % (_it21["optional"]["mask_mode"][1]["default"],))

# ---------------------------------------------------------------- 第 22 组：音频缝（0.5.0 · 节点内实现）
# 依据：音频缝必须在**节点里**做，不能靠组装层 ffmpeg（效果要落进段文件，组装只剩拼接）。
# 本组锁三件事：长度守恒（零 A/V 位移）/ 默认逐位直通 / 边界是 blend 而不是硬切。
import math
import shutil

print()
print("### 第 22 组：音频缝（节点内实现，长度守恒）")

_SR = 32000
_it22 = NODES.H3RelayAudioSeam.INPUT_TYPES()
_opt22 = _it22["optional"]
_REQ22 = _it22["required"]

check("22.1 默认全关（patch/tile=0，fade=0.25，bed_stage=0，OUTPUT_NODE）",
      _opt22["patch_seconds"][1]["default"] == 0.0
      and _opt22["tile_seconds"][1]["default"] == 0.0
      and abs(_opt22["fade_seconds"][1]["default"] - 0.25) < 1e-9
      and _opt22["bed_stage"][1]["default"] == 0
      and _opt22["bed_select"][1]["default"] == "tail"
      and list(_opt22["bed_select"][0]) == ["tail", "quiet"]
      and _REQ22["audio"][0] == "AUDIO"
      and NODES.H3RelayAudioSeam.OUTPUT_NODE is True,
      "RETURN=%s" % (NODES.H3RelayAudioSeam.RETURN_NAMES,))

torch.manual_seed(7)
# 本段：头部 32ms 解码 priming 近静默 + 段体 0.30 幅度噪声（复刻实测症状）
_cur_wf = torch.rand(1, 1, _SR * 3) * 0.30
_cur_wf[..., :int(0.032 * _SR)] = 0.0
_cur_a = {"waveform": _cur_wf, "sample_rate": _SR}
# 床源：前 2s 吵（0.5）、后 2s 静（0.05）——最静窗必须落在后半
_bed_a = {"waveform": torch.cat([torch.rand(1, 1, _SR * 2) * 0.50,
                                 torch.rand(1, 1, _SR * 2) * 0.05], -1),
          "sample_rate": _SR}

_out0, _rep0 = CORE.audio_seam_patch(_cur_a, _bed_a, patch=0.0)
check("22.2 patch=0 ⇒ 逐位直通（返回原对象，不做拷贝）",
      _out0 is _cur_a, "rep=%r" % (_rep0[:20],))

_out1, _rep1 = CORE.audio_seam_patch(_cur_a, _bed_a, patch=2.0, fade=0.25)
_w1 = _out1["waveform"]
check("22.3 补丁后长度守恒（零 A/V 位移）",
      int(_w1.shape[-1]) == int(_cur_wf.shape[-1]) and int(_out1["sample_rate"]) == _SR,
      "%d → %d 点" % (int(_cur_wf.shape[-1]), int(_w1.shape[-1])))

_n22 = int(2.0 * _SR)
_X22 = int(0.25 * _SR)
_keep22 = _n22 - _X22
# 🔴 2026-09-19 行为变更（真渲染阴性结果驱动）：默认档 = **尾部窗 + 电平对齐**
#   旧「全局最静窗」降为 `select="quiet"`，仅作对照。
_bsrc = _bed_a["waveform"].reshape(-1, _bed_a["waveform"].shape[-1])   # [C,T]
_bed_tail = _bsrc[..., int(_bsrc.shape[-1]) - _n22:]
_tgt22 = CORE.target_level(_bsrc)
_g22 = _tgt22 / max(float(_bed_tail.pow(2).mean().sqrt()), 1e-12)
check("22.4 默认档：补丁区 = 床源**尾部窗** × 单一增益（形状未变形、非最静窗）",
      bool((_w1[..., :_keep22] - _bed_tail[..., :_keep22] * _g22).abs().max() < 1e-6)
      and not torch.equal(_w1[..., :_keep22], _bed_tail[..., :_keep22]),
      "增益 %+.2f dB" % (20 * math.log10(max(_g22, 1e-12))))
_patch_rms = float(_w1[..., :_keep22].pow(2).mean().sqrt())
check("22.5 电平对齐：补丁电平 = 缝前目标 ±1.5 dB（本轮「把凹陷换成静音洞」的回归钉子）",
      abs(20 * math.log10(max(_patch_rms, 1e-9) / max(_tgt22, 1e-9))) < 1.5,
      "补丁 %.1f vs 目标 %.1f dBFS" % (20 * math.log10(max(_patch_rms, 1e-9)),
                                      20 * math.log10(max(_tgt22, 1e-9))))
check("22.5b 电平对齐不削波（增益后峰值 ≤ 1.0）",
      float(_w1[..., :_keep22].abs().max()) <= 1.0 + 1e-6,
      "峰值 %.4f" % float(_w1[..., :_keep22].abs().max()))

_wgt = torch.linspace(0.0, 1.0, _X22)
_exp_blend = ((_bed_tail * _g22)[..., _keep22:_n22] * (1.0 - _wgt)
              + _cur_wf[..., _keep22:_n22] * _wgt)
check("22.6 边界窗 [N−X,N) 是 blend（非硬切、非纯床、非纯本段）",
      torch.allclose(_w1[..., _keep22:_n22], _exp_blend, atol=1e-6)
      and not torch.equal(_w1[..., _keep22:_n22], (_bed_tail * _g22)[..., _keep22:_n22])
      and not torch.equal(_w1[..., _keep22:_n22], _cur_wf[..., _keep22:_n22]))

check("22.7 N 之后逐位不动（段体一个样本都不许改）",
      torch.equal(_w1[..., _n22:], _cur_wf[..., _n22:]))

# 选窗档位：tail（默认）取尾部；quiet（旧行为）取最静 —— 两档必须能取到且互不相同
# （用「吵–静–吵」三段 fixture：尾部吵、最静窗在中间，两档才会分出差别）
_bmix = torch.cat([torch.rand(1, _SR) * 0.50,
                   torch.rand(1, _SR) * 0.02,
                   torch.rand(1, _SR) * 0.50], -1)
_nq = int(0.5 * _SR)
_bt, _st, _rt = CORE.build_bed(_bmix, _nq, 0, int(0.1 * _SR))
_bq, _sq, _rq = CORE.build_bed(_bmix, _nq, 0, int(0.1 * _SR), select="quiet")
check("22.7b 选窗档位：tail=床源尾部 / quiet=最静窗（位置不同、quiet 明显更静）",
      _st == int(_bmix.shape[-1]) - _nq and _sq != _st and _rq < _rt * 0.2,
      "tail @%.2fs RMS %.4f ｜ quiet @%.2fs RMS %.4f"
      % (_st / _SR, _rt, _sq / _SR, _rq))

# 瓦片环铺：用**平滑**床源，接缝若有台阶会立刻现形（噪声源看不出跳变）
_ph = torch.arange(_SR * 3, dtype=torch.float32) / _SR
_sine = (torch.sin(2 * math.pi * 5.0 * _ph) * 0.4).unsqueeze(0).unsqueeze(0)
_bed_t, _stp, _rtp = CORE.build_bed(_sine[0], _n22, int(1.2 * _SR), _X22, select="quiet")
_d_step = float((_bed_t[..., 1:] - _bed_t[..., :-1]).abs().max())
_t_step = float((_sine[0][..., 1:] - _sine[0][..., :-1]).abs().max())
check("22.8 瓦片自叠化环铺：长度=N 且接缝无台阶",
      int(_bed_t.shape[-1]) == _n22 and _d_step < 5.0 * _t_step,
      "最长相邻差 %.5f vs 瓦片内 %.5f" % (_d_step, _t_step))

# 目标电平要**稳健**：尾部有 80ms 弱段（占 200ms 窗的 40%）时，中位数目标不应被它拖低
_beat = _bsrc.clone()
_beat[..., -int(0.08 * _SR):] *= 0.1
_r_med = CORE.target_level(_beat)                                  # 20ms 子窗 RMS 的中位数
_r_win = float(_beat[..., -int(0.2 * _SR):].pow(2).mean().sqrt())  # 单窗 RMS（旧口径）
check("22.8b 目标电平取「20ms 子窗 RMS 中位数」⇒ 不被尾部弱段拖低（BGM 适配的产物）",
      _r_med > _r_win * 1.2,
      "中位数 %.4f vs 单窗 %.4f（比值 %.2f）" % (_r_med, _r_win, _r_med / max(_r_win, 1e-12)))

_bed16 = {"waveform": torch.rand(1, 1, _SR * 2) * 0.05, "sample_rate": _SR // 2}
_o9, _ = CORE.audio_seam_patch(_cur_a, _bed16, patch=1.0)
check("22.9 床源采样率不一致 ⇒ 自动重采样（长度守恒、不炸）",
      int(_o9["waveform"].shape[-1]) == int(_cur_wf.shape[-1])
      and int(_o9["sample_rate"]) == _SR)

_bed_st = {"waveform": torch.rand(1, 2, _SR * 2) * 0.05, "sample_rate": _SR}
_o10, _ = CORE.audio_seam_patch(_cur_a, _bed_st, patch=1.0)
check("22.10 床源多声道 ⇒ 降混对齐（形状与本段一致）",
      tuple(_o10["waveform"].shape) == tuple(_cur_wf.shape))

_o11, _r11 = CORE.audio_seam_patch(_cur_a, _bed_a, patch=10.0)
check("22.11 补丁超长 ⇒ 夹紧 + 报告告警（不炸整条链）",
      int(_o11["waveform"].shape[-1]) == int(_cur_wf.shape[-1]) and "⚠" in _r11,
      _r11.splitlines()[-1][:60])

_tmp22 = tempfile.mkdtemp(prefix="h3relay_audio_")
_p22 = os.path.join(_tmp22, "a.safetensors")
CORE.save_audio(_out1, _p22, note="单测")
_back = CORE.load_audio(_p22)
check("22.12 音频落盘/读回逐位一致（含采样率元数据）",
      torch.equal(_back["waveform"], _out1["waveform"])
      and int(_back["sample_rate"]) == _SR)

# —— 走节点（不只是 core）：落盘 + 床源读回 + 两道守卫
_RID22 = "_unit_audio22"
_p0 = NODES._audio_stage_path(_RID22, 0)
_dir22 = os.path.dirname(_p0)
try:
    _n22obj = NODES.H3RelayAudioSeam()
    _a0, _l0, _j0 = unwrap(_n22obj.seam(_bed_a, _RID22, 0))   # 第 3 路 joined（复合，2026-09-19）
    # 🔴 `_a0 is _bed_a`（对象同一性）**保留**，但它现在依赖一条**判据修复**（2026-10-04）：
    #    本夹具是「前 2s 响（0.5）+ 后 2s 静（0.05）」⇒ 分界处 @1.983s 是一个**音量断崖**。
    #    全段政策下它进了候选；而当时「邻域静」的判据是「邻域 max **>** 事件峰」——
    #    断崖两侧同一量级（0.4997 vs 左邻同量级噪声的 max）⇒ 谁大**只看那两窗的随机样本**
    #    ⇒ 约 50% 概率把断崖**填成背景**（实测 0.4997 → 0.0489，而那不是孤立瞬态）。
    #    判据改成「邻域 max **×`AUDIO_DECLICK_EDGE_REL` >** 事件峰」后断崖**被跳过**
    #    ⇒ 本夹具回到「未命中 ⇒ 返回原对象」。
    #    ⚠️ 因此这条断言现在**同时验两件事**：stage 0 的直通语义 + 断崖判据不被削。
    #    ⚠️ 夹具的随机源是**有种子**的（上方 `torch.manual_seed(7)`）⇒ 结果确定，不是 flaky。
    check("22.13 节点 stage 0 ⇒ 直通（原对象返回、长度守恒）+ 仍落盘（后段的床源靠它）",
          (_a0 is _bed_a and os.path.isfile(_p0) and "第 1 段无缝可补" in _l0
           and int(_a0["waveform"].shape[-1]) == int(_bed_a["waveform"].shape[-1])),
          "同一对象=%s isfile=%s 长度 %d→%d"
          % (_a0 is _bed_a, os.path.isfile(_p0), int(_bed_a["waveform"].shape[-1]),
             int(_a0["waveform"].shape[-1])))
    _a1, _l1, _j1 = unwrap(_n22obj.seam(_cur_a, _RID22, 1, patch_seconds=2.0))
    _got = _a1["waveform"][..., :_keep22]
    _want = CORE.load_audio(_p0)["waveform"]
    _w2 = _want.reshape(-1, _want.shape[-1])
    _tail1 = _w2[..., int(_w2.shape[-1]) - _n22:]
    _g1 = CORE.target_level(_w2) / max(float(_tail1.pow(2).mean().sqrt()), 1e-12)
    check("22.14 节点 stage 1 ⇒ 头部换成上一段音频的**尾部窗**×增益（电平对齐）+ 报长度守恒",
          torch.allclose(_got, _tail1[..., :_keep22] * _g1, atol=1e-6)
          and "尾部窗" in _l1 and "增益" in _l1 and "长度守恒" in _l1,
          _l1.splitlines()[0][:56])
    expect_raise("22.15 节点：床源段号 ≥ 本段段号 ⇒ 明确 raise（不静默取自己）",
                 lambda: _n22obj.seam(_cur_a, _RID22, 1, patch_seconds=1.0, bed_stage=1),
                 "必须小于本段段号")
    expect_raise("22.16 节点：床源文件不存在 ⇒ raise 并指出要先跑第几段",
                 lambda: _n22obj.seam(_cur_a, _RID22, 3, patch_seconds=1.0, bed_stage=2),
                 "床源音频不存在")
finally:
    shutil.rmtree(_dir22, ignore_errors=True)
    shutil.rmtree(_tmp22, ignore_errors=True)

check("22.17 节点已注册且显示名以 🔗 开头",
      NODES.NODE_CLASS_MAPPINGS.get("H3RelayAudioSeam") is NODES.H3RelayAudioSeam
      and NODES.NODE_DISPLAY_NAME_MAPPINGS["H3RelayAudioSeam"].startswith("🔗"),
      "%s" % (NODES.NODE_DISPLAY_NAME_MAPPINGS.get("H3RelayAudioSeam"),))

check("22.18 音频域归属写进节点描述（时间轴/画质域/音频域不混挂）",
      "长度守恒" in (NODES.H3RelayAudioSeam.DESCRIPTION or ""),
      (NODES.H3RelayAudioSeam.DESCRIPTION or "")[:44])

# 🔴 峰值护栏（BGM 适配反思的产物）：旧的「最静窗」永不需要增益；改成**尾部窗**后床声本来就响，
#   再乘几 dB 会推过 1.0 ⇒ 下游编码削波。这里用「床声尾部很小但峰值很高 + 目标很大」逼出夹住路径。
_bed_pk = {"waveform": torch.cat([torch.rand(1, 1, _SR) * 0.02,
                                  torch.rand(1, 1, _SR) * 0.02], -1), "sample_rate": _SR}
_bed_pk["waveform"][..., int(_SR * 1.5)] = 0.90          # 一个高尖峰（峰值护栏的触发点）
_tgt_pk = {"waveform": torch.full((1, 1, _SR), 0.60), "sample_rate": _SR}
_o19, _r19 = CORE.audio_seam_patch(_cur_a, _bed_pk, patch=0.5, target_audio=_tgt_pk,
                                   gain_max_db=24.0)
check("22.19 增益后峰值护栏：床声不会被推过 1.0（削波防护）+ 报告标注已夹住",
      float(_o19["waveform"].abs().max()) <= 1.0 + 1e-6 and "夹住" in _r19,
      "峰值 %.4f ｜ %s" % (float(_o19["waveform"].abs().max()),
                          [ln for ln in _r19.splitlines() if "床声电平" in ln][:1]))

# —— 22.20~22.24：`joined`（**拼接复合在第 8 节点内**，不新增轮子；本仓作者 2026-09-19 指令）——
_it22j = NODES.H3RelayAudioSeam.INPUT_TYPES()
check("22.20 AudioSeam 第 3 路输出 joined 追加末位 + 五个拼接旋钮全折叠（J-cut 两参 2026-09-19）",
      NODES.H3RelayAudioSeam.RETURN_TYPES == ("AUDIO", "STRING", "AUDIO")
      and NODES.H3RelayAudioSeam.RETURN_NAMES == ("audio", "report", "joined")
      # ⚠️ 2026-09-20 判据修正：原本要求这五个**占据 optional 末位**，
      #    但那与「新 widget 只能追加末位（守 widgets_values 按位对槽）」直接冲突——
      #    实验档一追加就把这条判成失败。真正要守的是「五个都在 + 全折叠」，
      #    不是「它们后面不能再有别人」。
      and all(k in _it22j["optional"]
              for k in ("join_curve", "join_prime_ms", "join_cross_ms",
                        "join_segment_seconds", "join_align_seconds"))
      and all(_it22j["optional"][k][1].get("advanced")
              for k in ("join_curve", "join_prime_ms", "join_cross_ms",
                        "join_segment_seconds", "join_align_seconds")),
      "输出=%s" % (NODES.H3RelayAudioSeam.RETURN_NAMES,))

# 两段**不相关**正弦（220 / 330 Hz）+ 头部 1056 采样静音（模拟 AAC 编码器 priming）
_SRJ = 32000
_PRJ = 1056


def _seg22(f, dur_s=1.0):
    tt = torch.arange(int(dur_s * _SRJ), dtype=torch.float32) / _SRJ
    x = (0.30 * torch.sin(2 * math.pi * f * tt)).unsqueeze(0)
    x[..., :_PRJ] = 0.0                      # 编码器 priming
    return {"waveform": x.unsqueeze(0), "sample_rate": _SRJ}


_A22, _B22 = _seg22(220.0), _seg22(330.0)
_X22J = int(0.25 * _SRJ)
_keep22J = _SRJ - _PRJ


def _seam_stats22(curve, prime):
    """交叉窗（= out 末尾 X 个样本的落点）里逐 10ms 的**最小 RMS** —— 直接对「中缝凹陷」取证。"""
    _o, _ = CORE.join_audio_segments([_A22, _B22], prime_samples=prime,
                                     cross_samples=_X22J, curve=curve)
    _w = _o["waveform"].reshape(-1)
    _end = (_SRJ - prime) if prime else _SRJ      # 交叉窗结束 = 第二段开始重叠处
    _seg = _w[_end - _X22J: _end]
    _h = 320                                      # 10 ms
    _n = int(_seg.shape[-1]) // _h
    _mins = _seg[:_n * _h].reshape(_n, _h).pow(2).mean(dim=1).sqrt().min()
    return float(_mins), _w


_r_q, _w_q = _seam_stats22("qsin", _PRJ)
_r_t, _w_t = _seam_stats22("tri", _PRJ)
_d_db = 20 * math.log10(_r_q / max(_r_t, 1e-12))
check("22.21 等功率(qsin) 交叉窗最静点比线性(tri) 高（不相关内容；这是「音量先小再恢复」的直接取证）",
      _d_db >= 1.5, "qsin 最静 %.5f vs tri 最静 %.5f → %+.2f dB" % (_r_q, _r_t, _d_db))
_r_np, _w_np = _seam_stats22("qsin", 0)
_d2 = 20 * math.log10(_r_q / max(_r_np, 1e-12))
check("22.22 去 priming 使交叉窗最静点抬升（B 头不再是 33ms 静音）",
      _d2 >= 1.0, "去priming %.5f vs 不去 %.5f → %+.2f dB" % (_r_q, _r_np, _d2))
check("22.23 长度守恒 = Σ(段长−prime) − (n−1)·cross",
      int(_w_q.shape[-1]) == 2 * _keep22J - _X22J,
      "%d vs %d" % (int(_w_q.shape[-1]), 2 * _keep22J - _X22J))
_o24, _r24 = CORE.join_audio_segments([_A22], prime_samples=0, cross_samples=0)
check("22.24 单段 → 直通（长度不变、无交叉）",
      int(_o24["waveform"].shape[-1]) == _SRJ,
      "%d" % int(_o24["waveform"].shape[-1]))
check("22.25 负向对照：prime_ms=0 开关真的不删（**每段都删** ⇒ 总长差 = n×P）",
      int(_w_np.shape[-1]) - int(_w_q.shape[-1]) == _PRJ * 2
      and float(_B22["waveform"].reshape(-1)[:_PRJ].abs().max()) < 1e-6,
      "不删 %d vs 删 %d（差 %d）｜ 输入 B 头峰值 %.1e"
      % (int(_w_np.shape[-1]), int(_w_q.shape[-1]),
         int(_w_np.shape[-1]) - int(_w_q.shape[-1]),
         float(_B22["waveform"].reshape(-1)[:_PRJ].abs().max())))

# 22.26~22.28 —— 2026-09-19 深夜：自适应糊区补偿（settle_auto / 方向二「观测—校正闭环」落地）
torch.manual_seed(9)
_xsa = torch.rand(68, 24, 24, 3) * 0.2 + 0.4
_xsa[:] = _xsa[64:65]                                    # 整段 = 同一清晰帧（body 基线零波动）
_xsa[2:18] = CORE._box_blur_hwc(_xsa, 9)[2:18]          # 只有帧 2-17 软糊（真实爬升区形态）
_osa, _rsa = CORE.settle_compensate(_xsa, body_start=40, strength=1.0)
_hf = lambda t: (t.float() - CORE._box_blur_hwc(t.float(), 3)).abs().mean(dim=(1, 2, 3))
_bsa = float(_hf(_xsa[40:]).median())
check("22.26 自适应糊区补偿：亏空帧清晰度提升、帧 0-1 与段体逐位不动、报告含量测",
      float(_hf(_osa)[2]) > float(_hf(_xsa)[2]) * 1.2
      and torch.equal(_osa[40:], _xsa[40:])
      and _hf(_osa)[0].sub(_hf(_xsa)[0]).abs().item() < 1e-6
      and "回基线" in _rsa and "最低" in _rsa,
      _rsa[:66])
_xnb = torch.rand(24, 24, 3).unsqueeze(0).repeat(68, 1, 1, 1)
                                                         # 整段同一帧 ⇒ 每帧 hf 精确相同 ⇒ deficit 精确 0
_onb, _rnb = CORE.settle_compensate(_xnb, body_start=40, strength=1.0)
check("22.27 无亏空 ⇒ 逐位直通（不误伤：亏空=0 的帧一个样本都不动）",
      torch.equal(_onb, _xnb),
      "输出差 %.2e" % float((_onb.float() - _xnb.float()).abs().max()))
_oa1, _ra1 = NODES.H3RelayPost().apply(_xsa, None, settle_auto=1.0, settle_sharpen=0.6)
_itp = NODES.H3RelayPost.INPUT_TYPES()["optional"]
check("22.28 节点互斥（auto 优先、固定版弃权点名）+ settle_auto 默认 0、紧邻 settle_sharpen",
      "固定锐化弃权" in _ra1
      and _itp["settle_auto"][1]["default"] == 0.0
      and list(_itp).index("settle_auto") == list(_itp).index("settle_sharpen") + 1
      and "亏空" in _itp["settle_auto"][1]["tooltip"],
      _ra1.split("；")[-1][:56])

