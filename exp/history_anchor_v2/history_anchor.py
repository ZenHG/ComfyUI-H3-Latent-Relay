# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""E1' 时不变历史锚 —— Time-Invariant History Anchor (TIHA)

=============================================================================
为什么重写 E1（一句话）
=============================================================================

旧 E1（``build_history_refs``，2026-09-22 随 ``1c87aff`` 删除）按 **段号** 往回取
历史段（stage-2/-3/-4），每级取该段 **开头** ``stride^i`` 抽稀后的帧块，追加成
``minimax_refs``。单变量 A/B（同 seed / 同 latent / 唯一变量 depth 0→2）证明它
**真生效但反向**：生成区像素差 37/255，漂移却 ``|s5-s3|`` 亮度 ×2.9、锐度 ×9。

根因不在参数，在**三层理论错误**（详见 ``RESEARCH_E1重写-理论与设计.md``）：

1. **归因错误**：把 FramePack 的"防漂移"归给了"多尺度压缩"。而 FramePack 原文里
   ``FramePack`` 结构治的是 **forgetting**（上下文长度），防漂移另有其人
   （early-established endpoints / adjusted sampling orders / discrete history）。
   旧注释直接写"依据 FramePack 距离衰减防漂移" ⇒ 拿错了工具。
2. **度量错误**：FramePack 的帧重要性有三种度量（time proximity /
   **feature similarity** / hybrid）。旧 E1 只用了最差的一种，且退化成**静态段号**。
   按 feature-similarity 排序，段头帧在运动镜头下**恰恰是最该被丢掉的帧**。
3. **通道错误**：把"场景状态"（机位 / 构图 / 瞬时动作）塞进了 **refs 通道**，而
   refs 通道是 **attention sink**（宿主 ``img_update=zeros``，近零噪声全程骑乘）。
   同时本段的场景状态已由 22 帧 context **硬钉**提供且更新 ⇒ 旧 E1 是用过时的
   场景状态去对抗新的场景状态。LongLive(ICLR'26) 自己承认的已知缺陷
   "sink 固定在首帧块会保留过时 style/identity" 即是此机制。

=============================================================================
本模块改了什么（四条硬改动）
=============================================================================

A. **破除"只能取段头"** —— 源码级证据：宿主 ``comfy/ldm/minimax/model.py``
   ``_video_t_spans(n) = [FRAME_RESCALE * FRAME_PER_TOKEN[k % 5] for k in range(n)]``
   跨度由 **块内** index ``k % 5`` 决定，与块在原段的绝对位置 **完全无关**；
   ``_video_grid`` 以块自己的 ``cursor`` 为原点。⇒ 从历史段 **任意位置** 切连续
   窗口作独立 refs 块，时间格**天然合法**。旧实现的"取头不取尾"是自建约束。

B. **按内容选帧，不按位置选帧** —— 用 **跨段共识度** 给每帧打"时不变分"：
   一帧的内容若在**其它历史段**里复现，它就是时不变的（身份 / 服装 / 道具 /
   材质 / 色档）；若是瞬时动作或一次性机位，则无处复现。只锚前者。

C. **门控 + 安全降级** —— 段内分数极差过小（无可区分的时不变帧）⇒ 整段出局，
   返回空。旧 E1 是"要几级给几级"，无内容感知 ⇒ 无效参考也硬塞，副作用全吃、
   收益为零。

D. **token 经济** —— 每块默认 5 帧（2 token，旧 E1 第 0 级 22 帧 = 7 token），
   且块数受门控（常 0–2 块）；另开 ``spatial_scale`` 空间降采样（refs 允许独立
   空间网格，宿主 ``_frame_grid`` 按块自身 h/w 建网格）⇒ token 再降 1/4。
   旧 E1 base=22 时 +28% token、base=90 时 +112%；本模块典型 +4%~8%。

=============================================================================
纪律（与仓库一致）
=============================================================================

* **默认全关**（``depth=0``）⇒ 主干行为逐位不变。
* **改动机理可证伪** —— 所有判据零 GPU、秒级可断言（见 ``test_history_anchor.py``），
  且**必须包含旧 E1 的反例**（T3/T4：旧 E1 会错选段头帧，本模块不会）。
* **硬错误一律 raise**，不静默降级；**无内容可锚则诚实返回空**，不硬塞。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import torch
import torch.nn.functional as F

__all__ = [
    "TIHAConfig",
    "TIHACandidate",
    "frame_matrix",
    "noise_scale",
    "center_matrices",
    "consensus_scores",
    "rank_candidates",
    "suppress_neighbors",
    "assemble_block",
    "build_history_anchor_refs",
    "filter_duplicate_sources",
    "drop_anchor_stage",
    "KNOWN_CONFIG_KEYS",
    "H3Grid",
]

#: ``_tiha.json`` 允许出现的键 —— **唯一真相源**（2026-10-02 下沉到这里）。
#:
#: 为什么必须下沉：白名单原先在 ``h3_adapter.KNOWN_KEYS`` 与 ``precheck_tiha._KEYS``
#: 各写一份（**外加 precheck 里内联的第三份**）。三份内容一致时无害，但一旦改一处，
#: 「预检预测的生产行为」就与「生产行为」分叉 —— 而预检的**全部**意义就是预测生产。
#: 放这里是因为 ``history_anchor`` 被生产侧（``h3_adapter``）与预检侧
#: （``precheck_tiha``）**都能 import**；后两者之间做不到（``h3_adapter`` 用包内相对导入）。
#:
#: 与 ``TIHAConfig`` 字段集的关系由单测机检：``KNOWN_CONFIG_KEYS`` 必须**全是真字段**
#: （否则白名单赋值会静默忽略它），且两侧引用必须**相等**。
KNOWN_CONFIG_KEYS: Tuple[str, ...] = (
    "depth", "window_frames", "spatial_scale", "max_refs", "max_refs_per_seg",
    "pool", "top_q", "min_rel", "min_spread_k", "consensus_mode", "center",
    "normalize", "dedup_sim", "nms_radius", "anchor_max_sim",
)

# ============================================================================
# H3 时间网格（与 relay_core 同源；独立一份以便零依赖单测）
# ============================================================================


class H3Grid:
    """H3 latent 的像素帧 ↔ token 换算。

    ``FRAME_PER_TOKEN = (1, 4, 4, 4, 4)`` 循环 ⇒ 第 k 个 token 覆盖的像素帧数
    只由 ``k % 5`` 决定。**关键推论**：任意连续窗口 ``[t0, t0+w)`` 切成独立块后，
    宿主按块内 index 重建时间格 ⇒ **窗口起点 t0 不受任何相位约束**。
    旧 E1 的"取头不取尾"即源于误以为存在这个约束。
    """

    FRAME_PER_TOKEN: Tuple[int, ...] = (1, 4, 4, 4, 4)
    GUIDE_RUNS: Tuple[int, ...] = (124, 107, 90, 73, 56, 39, 22, 5, 1)

    @classmethod
    def frames_for_steps(cls, steps: int) -> int:
        """token 数 → 覆盖的像素帧数（块内 index 从 0 起）。"""
        n = int(steps)
        return sum(cls.FRAME_PER_TOKEN[k % 5] for k in range(n))

    @classmethod
    def steps_for_frames(cls, frames: int) -> Optional[int]:
        """像素帧数 → 整步 token 数；不在网格上返回 None。"""
        n = int(frames)
        acc = 0
        for k in range(max(n, 0)):
            acc += cls.FRAME_PER_TOKEN[k % 5]
            if acc >= n:
                return k + 1 if acc == n else None
        return 0 if n == 0 else None

    @classmethod
    def snap_steps(cls, frames: int) -> int:
        """向下吸附到能整除的 token 数（窗口用，允许非 GUIDE_RUNS 值）。

        ``FRAME_PER_TOKEN`` 的前缀和是 **1 / 5 / 9 / 13 / 17 / 18 / 22 / 26 / 30 …**
        —— 🔴 **不是等差**：第 6 个 token 取到 ``FRAME_PER_TOKEN[5 % 5] = 1``，只覆盖 1 帧。
        （2026-10-02 实测更正：本文档此前写「…/ 17 / 21 / 25 …」是**错的**。）

        ✅ 实测：产线 ``GUIDE_RUNS``（1/5/22/39/56/73/90/107/124）**全部**落在该网格上
        ⇒ **产线值不会被下取**；只有非网格值才会（如实测 21 → 18 帧）。
        ⚠️ 下取本身是**静默**的 ⇒ 调用方 ``build_history_anchor_refs`` 会在下取时日志出声。
        """
        n = int(frames)
        s = cls.steps_for_frames(n)
        if s is not None:
            return max(s, 1)
        acc, k = 0, 0
        while acc + cls.FRAME_PER_TOKEN[k % 5] <= n:
            acc += cls.FRAME_PER_TOKEN[k % 5]
            k += 1
        return max(k, 1)


# ============================================================================
# 配置
# ============================================================================


@dataclass
class TIHAConfig:
    """E1' 的全部旋钮。**默认 = 关**（``depth=0``）。"""

    # —— 开关 ——
    depth: int = 0
    """请求的级数上限。**硬上限而非目标值**：门控后可能少于此值，甚至为 0。
    与旧 E1 的语义差别就在这里 —— 旧 E1 的 depth 是"必须凑够几级，不够就 raise"。"""

    # —— 组块 ——
    window_frames: int = 5
    """每个锚块的窗口长度（像素帧）。5 帧 = 2 token，最省；22 帧 = 7 token。
    ⚠️ 窗口内必须时间连续（保运动语义），**不做抽稀** —— 见下方 design note。"""

    spatial_scale: int = 1
    """锚块空间降采样倍率（1 = 原分辨率，2 = 1/2 ⇒ token 1/4）。
    refs 允许独立空间网格（宿主 ``_frame_grid`` 按块自身 h/w 建网格），
    而锚只需要身份/色档/材质，不需要细节 ⇒ 降采样是白拿的 token 节省。

    🔴 **硬约束（2026-09-27 实测踩到）**：宿主 ``patchify_video`` 用
    ``patch_size=(1,2,2)``，做 ``reshape(b,c,t,pt,h,ph,w,pw)``
    ⇒ **块的空间尺寸 h、w 必须都是偶数**。降采样后不满足即 **raise**
    （不静默回退 —— 静默回退会让"我开了降采样"与"实际没降"不一致）。
    即要求 ``lat_h % (2 * spatial_scale) == 0`` 且 ``lat_w % (2 * spatial_scale) == 0``。

    ⚠️ 以下示例按 **W×H** 写；而代码里 ``blob.shape[3]/shape[4]`` 是 **H×W**
    （两处约定不同，读的时候别混）：
    实测：1024×576 的 latent 64×36（W×H），scale=2 ⇒ 32×18 ✓；
          960×544 的 latent 60×34（W×H），scale=2 ⇒ 30×**17** ✗（17 为奇数，reshape 崩）。
    不确定就用 :meth:`max_feasible_scale` 先算。"""

    # —— 打分 ——
    pool: int = 6
    """帧签名的空间池化边长。``[C,H,W] → [C,pool,pool]`` 后展平。
    池化同时降噪：latent 的高频通道噪声对相似度无贡献，池化后更稳。"""

    top_q: int = 1
    """跨段共识取每个参照段里最相似的 top-q 帧求平均（q>1 抗偶然匹配）。"""

    consensus_mode: str = "both"
    """参照段集合的取法：
      ``both``    —— 所有其它历史段（默认；任一段里复现即算时不变）
      ``later``   —— 只比"离当前更近"的段（更严格：必须在之后还出现）
      ``earlier`` —— 只比更远的段
    """

    normalize: bool = True
    """帧签名做 L2 归一（余弦相似度）。关掉则用点积（受帧能量影响，一般别关）。"""

    center: bool = True
    """🔴 **跨段全局中心化**（先减去所有历史帧签名的均值，再归一）。

    为什么必须有：**实测（2026-09-27，run=e1t 真实 latent）** 不中心化时，
    所有帧的两两余弦挤在 **0.94–0.99** —— H3 latent 的**公共偏置**（同一底模、
    同一条链的整体色调/能量）把相似度整体抬满，动态范围只剩 0.05，
    "内容是否复现"这个信号被完全淹没。中心化后余弦衡量的是
    **这一帧相对"全链平均帧"的偏离方向**，偏置被消掉，动态范围回到可用区间。

    代价：接近均值的帧中心化后模长趋零，归一会被噪声主导 ⇒ 分母带 clamp，
    且这类帧本来就是"最没有特征"的，落选是正确行为。"""

    # —— 门控（本模块相对旧 E1 的核心增量）——
    min_spread_k: float = 3.0
    """🔴 **段级门控**：段内共识分的**极差**必须 ≥ ``k × 噪声尺度``。
    低于此值 = 该段**没有可区分的时不变帧**（整段都在动 / 或整段都一个样）⇒ **整段出局**。
    旧 E1 没有这一条 ⇒ 无效参考也硬塞，副作用全吃、收益为零。

    🔴 **2026-10-02 清理**：旧的绝对阈值字段 ``min_spread: float = 0.02`` 已删 ——
    它**全仓零使用**（只活在定义处），且**不在** ``h3_adapter.KNOWN_KEYS`` 白名单里
    ⇒ 既不可配置也不生效。它的职能已由本字段的「``k × 噪声尺度``」**分辨率无关版**取代
    （绝对阈值随 pool / 通道数 / 底模漂 ⇒ 换分辨率会静默失效）。

    ``噪声尺度 = 2.5 / sqrt(d)``，``d = C·pool²`` 是签名维度 —— 它是**纯随机
    帧**之间余弦极差在「签名是 d 维单位向量」这一**理想化假设**下的**参照尺度**
    （max−min ≈ 5σ，σ = 1/√d）。即**"这段里根本没有任何可区分的时不变帧"大概长什么样**。

    🔴 **它是参照，不是严格上界**（2026-10-02 按 P2-5 更正）：``pool`` 边长不整除
    输入尺寸时，``adaptive_avg_pool2d`` 走**变窗口**（相邻窗有重叠）⇒ 签名的
    **有效自由度 ≠ d** ⇒ ``2.5/√d`` 只是**近似**。本常数由**实测标定**（见下方
    e1t 实测），**不要当成理论保证**；换底模 / 换 ``pool`` 时应重新标定。

    为什么用这个而不是拍一个绝对数：绝对数随 ``pool`` / 通道数 / 底模漂，
    换个分辨率就静默失效。用噪声尺度做单位 ⇒ **分辨率无关、底模无关**。
    实测（run=e1t 真实 latent，pool=6，d=864 ⇒ 噪声尺度 0.085）：
      纯随机合成段 spread ≈ 0.055（< 3×0.085=0.255 ⇒ **拒**）；
      stage3 spread ≈ 0.765（> 0.255 ⇒ **收**），信号是噪声的 9 倍。"""

    min_rel: float = 0.85
    """**帧级门控**：候选在段内的**相对位置** ``rel = (s-min)/(max-min) ≥ min_rel``。

    段级门控已保证"极差显著大于噪声"，此时段内的相对位置才是可信的：
    rel 高 = 这一帧比本段其它帧都更复现。用相对量（而非绝对 lift）是因为
    绝对量会被段间的整体水平差异干扰，而我们要的是**段内排序**。

    ⚠️ 曾用"稳健 z（MAD 归一）≥ 4"做帧级门控，**在真实数据上过严**（真实
    视频段内差异本来就大 ⇒ MAD 大 ⇒ z 天然小），实测全部落选。相对位置
    判据在合成与真实两端都稳定。"""

    anchor_max_sim: float = 1.1
    """与**全局外观锚**的最大相似度（>1 即关闭此门）。高于此值的候选视为
    "锚里已经有"，剔除 —— 避免和历史锚重复占用有限的参考配额。"""

    # —— 去重 / 配额 ——
    nms_radius: int = 2
    """非极大抑制半径（token）：相邻高分帧只留分最高的一个。"""

    dedup_sim: float = 0.98
    """块间互去重阈值：两个候选块签名余弦 > 此值 ⇒ 保留分高的。
    旧 E1 只剔了"与外观锚同段"，**没有候选间去重**。"""

    max_refs: int = 2
    """最终块数硬上限。宿主多模态参考有配额上限（产线口径 9 张），
    必须给全局外观锚和音频让位 ⇒ 历史锚默认只给 2。"""

    max_refs_per_seg: int = 1
    """**每段**最多出几块。默认 1 —— 对齐旧 E1 的"级数"语义（一级 = 一段），
    同时保证锚的**来源多样性**：同一个最高分段里再挑第二块，信息增量远小于
    换一个段（同一段的两块内容高度相关）。"""

    @staticmethod
    def max_feasible_scale(lat_h: int, lat_w: int, want: int = 2) -> int:
        """在给定 latent 空间尺寸下，≤ ``want`` 的**最大合法**降采样倍率。

        合法性 = 降采样后 h、w 仍为**偶数**（宿主 ``patch_size=(1,2,2)`` 的硬要求，
        见 ``spatial_scale`` 注记）。``want=2`` 时要求 ``lat_h % 4 == 0`` 且
        ``lat_w % 4 == 0``，否则退回 1（不降采样）。

        实测：1024×576（latent 64×36）⇒ 2；960×544（latent 60×34）⇒ 1。
        """
        s = max(int(want), 1)
        while s > 1:
            if int(lat_h) % (2 * s) == 0 and int(lat_w) % (2 * s) == 0:
                return s
            s -= 1
        return 1

    def validate(self) -> None:
        if int(self.depth) < 0:
            raise ValueError("depth 不得为负，得到 %d。" % int(self.depth))
        # 🔴 P0-1（2026-09-28 审核）：normalize=False 会让 noise_scale 门控**静默失效**。
        #    实测：模长 1.0 → 23.58，共识分极差 0.718 → 99.07，而阈值仍是按单位向量定的
        #    0.2552 ⇒ **段级门控恒真**。全仓 grep 确认该组合零使用 ⇒ 直接禁掉。
        if not bool(self.normalize):
            raise ValueError(
                "normalize=False 会让 noise_scale 门控失效（点积量纲随帧能量缩放，"
                "而阈值 2.5/√d 只对单位向量成立）⇒ 段级门控恒真、min_rel 一并失效。"
                "本模块不支持该组合。")
        if not (0.0 <= float(self.min_rel) <= 1.0):
            raise ValueError("min_rel 必须在 [0,1]，得到 %.3f。" % float(self.min_rel))
        if float(self.min_spread_k) <= 0.0:
            raise ValueError("min_spread_k 必须 >0，得到 %.3f。" % float(self.min_spread_k))
        if int(self.nms_radius) < 0:
            raise ValueError("nms_radius 不得为负，得到 %d。" % int(self.nms_radius))
        if int(self.window_frames) <= 0:
            raise ValueError("window_frames 必须 >0，得到 %d。" % int(self.window_frames))
        if int(self.spatial_scale) < 1:
            raise ValueError("spatial_scale 必须 ≥1，得到 %d。" % int(self.spatial_scale))
        if int(self.pool) < 1:
            raise ValueError("pool 必须 ≥1，得到 %d。" % int(self.pool))
        if int(self.top_q) < 1:
            raise ValueError("top_q 必须 ≥1，得到 %d。" % int(self.top_q))
        if str(self.consensus_mode) not in ("both", "later", "earlier"):
            raise ValueError(
                "consensus_mode 只认 both/later/earlier，得到 %r。" % self.consensus_mode)
        if int(self.max_refs) < 1:
            raise ValueError("max_refs 必须 ≥1，得到 %d。" % int(self.max_refs))
        if float(self.dedup_sim) < 0.0 or float(self.dedup_sim) > 1.0:
            raise ValueError("dedup_sim 必须在 [0,1]，得到 %.3f。" % float(self.dedup_sim))


@dataclass
class TIHACandidate:
    """一个通过门控的候选帧。"""

    seg: int          # **历史列表下标**（level，0 = 最近的可用历史），不是段号
    token: int        # 段内 token 下标
    score: float      # 共识分（越高 = 越时不变）
    rel: float        # 段内**相对位置** rel ∈ [0,1]（越高 = 本段内越突出）
    frames: int = 0   # 组块后实际覆盖的像素帧数（填在 assemble 阶段）


# ============================================================================
# 1) 帧签名
# ============================================================================


def frame_matrix(video: torch.Tensor, pool: int = 6,
                 normalize: bool = True) -> torch.Tensor:
    """``[B,C,T,H,W]`` → ``[T, C*pool*pool]`` 帧签名矩阵（每行一帧）。

    池化只吃 **H/W 两个空间维**（latent 通道互不相关，跨通道池化无意义），
    得到 ``[T, C, pool, pool]`` 再展平。池化同时是降噪：latent 的高频通道噪声
    对"内容是否复现"没有贡献，池化后相似度更稳。

    ``normalize=True`` 时每行 L2 归一 ⇒ 后续点积即余弦相似度。
    """
    if video.dim() != 5:
        raise ValueError("frame_matrix 需要 [B,C,T,H,W]，得到 %s" % (tuple(video.shape),))
    v = video.detach().float()
    b, c, t, h, w = v.shape
    if int(t) <= 0:
        raise ValueError("历史段 latent 的时间维为 0，无法打分。")
    # [T,C,H,W] → [T*C,1,H,W] → 池化 → [T,C,pool,pool] → [T,C*pool*pool]
    x = v[0].permute(1, 0, 2, 3).contiguous().reshape(t * c, 1, h, w)
    x = F.adaptive_avg_pool2d(x, (int(pool), int(pool)))
    m = x.reshape(t, c * int(pool) * int(pool)).contiguous()
    if normalize:
        m = _l2(m)
    return m


def noise_scale(dim: int) -> float:
    """**纯随机帧**之间余弦极差的**参照尺度**，用作段级门控的标尺。

    ⚠️ 是"参照"不是"严格上界"（2026-10-02 P2-5 更正）：池化变窗口会让签名的
    有效自由度偏离 ``d`` ⇒ 本值是**近似**，常数由实测标定。
    完整说明见 ``TIHAConfig.min_spread_k`` 的注记。

    d 维单位随机向量两两余弦 ≈ ``N(0, 1/d)`` ⇒ σ = ``1/√d``；T 次取样的
    max−min 约 5σ（T 的对数依赖很弱，忽略）⇒ ``噪声尺度 = 2.5/√d``。

    这就是为什么段级门控要用它做单位：换个 ``pool`` / 通道数 / 底模，
    绝对阈值会静默失效，而噪声尺度跟着维度走。
    """
    return 2.5 / math.sqrt(max(int(dim), 1))


def _l2(m: torch.Tensor) -> torch.Tensor:
    """行 L2 归一，带 clamp（中心化后近均值帧的模长趋零，防止除零放大噪声）。"""
    return m / m.pow(2).sum(dim=1, keepdim=True).clamp(min=1e-12).sqrt()


def center_matrices(mats: Sequence[torch.Tensor], normalize: bool = True,
                    mean: Optional[torch.Tensor] = None
                    ) -> Tuple[List[torch.Tensor], torch.Tensor]:
    """跨段全局中心化：减去**所有历史帧签名的均值**，可选重新归一。

    目的与本分见 ``TIHAConfig.center`` 的实测注记 —— 不中心化时真实 H3 latent
    的帧间余弦会挤在 0.94–0.99，区分度被公共偏置吃掉。

    返回 ``(新张量列表, 用到的均值)``，不改动输入。传 ``mean`` 可对**外观锚**
    施加**同一个**均值（否则锚与历史不在同一坐标系，去重阈值无意义）。
    """
    if not mats:
        return [], (mean if mean is not None else torch.zeros(0))
    dim = int(mats[0].shape[1])
    if mean is None:
        total = sum(int(m.shape[0]) for m in mats)
        acc = torch.zeros(dim, dtype=mats[0].dtype, device=mats[0].device)
        for m in mats:
            acc += m.sum(dim=0)
        mean = acc / float(max(total, 1))
    out = [m - mean for m in mats]
    if normalize:
        out = [_l2(m) for m in out]
    return out, mean


# ============================================================================
# 2) 跨段共识打分 —— "这一帧的内容在别的段里还出现过吗"
# ============================================================================


def _ref_indices(i: int, n: int, mode: str) -> List[int]:
    """段 i 的参照段下标集合。历史按 **由近到远** 排列（0 = 最近）。"""
    if mode == "both":
        return [j for j in range(n) if j != i]
    if mode == "later":      # 更靠近当前 = 下标更小
        return [j for j in range(0, i)]
    if mode == "earlier":    # 更远 = 下标更大
        return [j for j in range(i + 1, n)]
    raise ValueError("未知 consensus_mode: %r" % mode)


def consensus_scores(mats: Sequence[torch.Tensor], top_q: int = 1,
                     mode: str = "both") -> List[torch.Tensor]:
    """给每段每帧算**跨段共识分**。

    ``score_i[t] = mean over 参照段 j of ( top-q max over g of cos(f_it, f_jg) )``

    语义：一帧的内容若在别的段里复现 ⇒ 它是**时不变**的（身份 / 服装 / 道具 /
    材质 / 色档）；若只在某一瞬出现（一个动作、一次机位）⇒ 无处复现，分数低。

    返回与输入同长的列表，元素为 ``[T_i]`` 分数张量。
    参照段为空的段（如 ``mode="later"`` 下的第 0 段）返回**全零** ⇒ 必被段级门控淘汰。
    """
    n = len(mats)
    out: List[torch.Tensor] = []
    for i in range(n):
        ti = int(mats[i].shape[0])
        refs = _ref_indices(i, n, mode)
        if not refs:
            out.append(torch.zeros(ti, dtype=mats[i].dtype, device=mats[i].device))
            continue
        acc = torch.zeros(ti, dtype=mats[i].dtype, device=mats[i].device)
        for j in refs:
            s = mats[i] @ mats[j].t()                    # [T_i, T_j] 余弦
            q = int(min(int(top_q), int(s.shape[1])))
            acc += s.topk(q, dim=1).values.mean(dim=1)
        out.append(acc / float(len(refs)))
    return out


# ============================================================================
# 3) 门控 + 排序 + 抑制
# ============================================================================


def rank_candidates(mats: Sequence[torch.Tensor],
                    scores: Sequence[torch.Tensor],
                    cfg: TIHAConfig) -> List[TIHACandidate]:
    """段级门控 → 帧级门控 → 全局排序 → 非极大抑制。

    ⚠️ **本函数不做配额**（``max_refs`` 由调用方在**内容去重之后**截断 —— 顺序不可换，
    见函数内注记）。第一版 docstring 写了「→ 配额」，与实现不符，2026-09-28 更正。

    返回**已按分数降序**的候选列表（可能为空）。
    """
    cands: List[TIHACandidate] = []
    for i, s in enumerate(scores):
        if s.numel() == 0:
            continue
        lo, hi = float(s.min()), float(s.max())
        spread = hi - lo
        # 🔴 段级门控：极差必须显著大于"纯随机帧"的噪声极差
        #    （理论依据见 TIHAConfig.min_spread_k；单位随签名维度自适应）
        if spread < float(cfg.min_spread_k) * noise_scale(int(mats[i].shape[1])):
            continue
        # 帧级门控：段内**相对位置**（段级已保证 spread 可信 ⇒ rel 可信）
        rel = (s - lo) / max(spread, 1e-12)
        keep = (rel >= float(cfg.min_rel)).nonzero(as_tuple=False).flatten()
        for t in keep.tolist():
            cands.append(TIHACandidate(
                seg=i, token=int(t),
                score=float(s[int(t)]), rel=float(rel[int(t)])))
    if not cands:
        return []
    cands.sort(key=lambda c: (-c.score, c.seg, c.token))
    # ⚠️ 这里**不做** max_refs 截断：配额必须在**内容去重之后**才生效。
    # 先截断再去重会出静默错误 —— 例如前 2 名恰好是"同一份内容在两段里各出现一次"
    # （跨段复现的定义就是如此），去重后只剩 1 块，配额被白白吃掉。
    return suppress_neighbors(cands, int(cfg.nms_radius))


def suppress_neighbors(cands: List[TIHACandidate], radius: int) -> List[TIHACandidate]:
    """同段内相邻 token 的非极大抑制（NMS）。

    时不变内容在时间维上是**成片**的（连续好几帧都在拍那面墙）⇒ 不打散会一次
    占掉全部配额、且几块内容几乎相同。保留每段内分数最高的、彼此间隔 ≥ radius 的。
    """
    if radius <= 0:
        return list(cands)
    kept: List[TIHACandidate] = []
    for c in cands:                       # cands 已按分数降序
        if all(not (k.seg == c.seg and abs(k.token - c.token) < radius) for k in kept):
            kept.append(c)
    return kept


def drop_anchor_stage(indices: Sequence[int],
                      anchor_stage: int) -> Tuple[List[int], List[int]]:
    """剔除与**全局外观锚同段**的段号 —— 「与锚同源」判定的**唯一实现**（2026-10-02 合并）。

    为什么单独抽出来：生产路径原先在 ``h3_adapter`` 里**自己写了一遍**同一个判断
    （目的是"读盘前就筛掉"，见 P1-2），而 ``filter_duplicate_sources`` 里还留着一份
    ⇒ 同一规则两份实现（《规范》§二·D「孪生体」）。两份**当前恰好等价**，但改一处就打架，
    而打架的后果是"预检说会出 N 块、生产实际出 M 块" —— 预检的价值直接归零。

    ``indices`` 按**由近到远**给；返回 ``(kept, dropped)``，两者都保持输入顺序。
    """
    a = int(anchor_stage)
    kept: List[int] = []
    dropped: List[int] = []
    for i in indices:
        (dropped if int(i) == a else kept).append(int(i))
    return kept, dropped


def filter_duplicate_sources(pairs: Sequence[Tuple[int, Any]],
                             anchor_stage: int) -> Tuple[List[Tuple[int, Any]], List[int]]:
    """剔除与**全局外观锚同段**的历史级，保留 ``(段号, latent)`` 配对。

    ``pairs`` = ``[(stage_index, latent), ...]``（由近到远）。返回 ``(kept, skipped)``。

    ⚠️ 本函数是 :func:`drop_anchor_stage` 的**配对保留薄壳** —— 判定不在这里重复实现。
    保留 latent 是为了让预检 / 离线验证脚本能继续按段号索引载体；
    **生产路径不该用它**：生产要在**读盘前**就筛掉（P1-2 的优化），那时手上还没有 latent。
    """
    _, dropped = drop_anchor_stage([int(i) for i, _ in pairs], anchor_stage)
    drop_set = set(dropped)
    return [(int(i), lat) for i, lat in pairs if int(i) not in drop_set], dropped


# ============================================================================
# 4) 组块 —— 破除"只能取段头"
# ============================================================================


def assemble_block(video: torch.Tensor, token: int, window_tokens: int,
                   spatial_scale: int = 1) -> torch.Tensor:
    """以 ``token`` 为中心切一个**连续**窗口，可空间降采样。

    🔴 这是本模块破除旧 E1 根因的关键一步：窗口**起点任意**。

    依据（宿主源码，2026-09-27 逐行核实）：
    ``comfy/ldm/minimax/model.py``
      ``_video_t_spans(n) = [FRAME_RESCALE * FRAME_PER_TOKEN[k % 5] for k in range(n)]``
      ``_video_grid(vt, frame, cursor)`` 以块自身的 ``cursor`` 为原点铺时间格
    ⇒ 块内时间格由**块内 index** 决定，与原段中的绝对位置无关。
    旧实现"取头不取尾"是把自建约束当成了宿主约束。

    窗口内保持时间连续（不做 stride 抽稀）：
    抽稀会把非相邻的帧拼成"假连续"序列，宿主仍按 k%5 给它铺时间格 ⇒
    等于向模型声明一段**不存在的运动**。旧 E1 的"多尺度"正是这么做的。
    本模块要的是**外观**，宁可窗口短而真，不要窗口长而假。
    """
    if video.dim() != 5:
        raise ValueError("assemble_block 需要 [B,C,T,H,W]，得到 %s" % (tuple(video.shape),))
    total = int(video.shape[2])
    w = int(max(window_tokens, 1))
    if w > total:
        w = total
    # 以 token 为中心，尽量居中；越界则回贴到边界内
    start = int(token) - w // 2
    start = max(0, min(start, total - w))
    blk = video[:1, :, start:start + w].contiguous()
    s = int(spatial_scale)
    _, _, _, h, ww = blk.shape
    if s > 1:
        if h % s or ww % s:
            raise ValueError(
                "空间降采样 %d 倍要求 latent H/W 能整除，得到 %dx%d。" % (s, h, ww))
        blk = F.avg_pool3d(blk, kernel_size=(1, s, s), stride=(1, s, s)).contiguous()
    # 🔴 宿主硬约束：patchify_video 用 patch_size=(1,2,2) 做
    #    reshape(b,c,t,pt,h,ph,w,pw) ⇒ 块的 h、w **必须都是偶数**，否则当场
    #    RuntimeError（实测：960×544 的 latent 34×60 降 2 倍得 17×30，17 为奇数即崩）。
    #    这里硬校验而不是自动回退 —— 静默回退会让"我开了降采样"与"实际没降"不一致，
    #    出实验结论时会归因错。
    h2, w2 = int(blk.shape[3]), int(blk.shape[4])
    if h2 % 2 or w2 % 2:
        raise ValueError(
            "锚块空间尺寸 %dx%d 含奇数边 —— 宿主 patchify 要求 h、w 均为偶数。"
            "请改用可行的 spatial_scale（用 TIHAConfig.max_feasible_scale(%d, %d, %d) 求解，"
            "当前请求 %d ⇒ 最大可行 %d）。"
            % (h2, w2, h, ww, s, s, TIHAConfig.max_feasible_scale(h, ww, s)))
    return blk


# ============================================================================
# 5) 主入口
# ============================================================================


def build_history_anchor_refs(
    history_videos: Sequence[torch.Tensor],
    cfg: Optional[TIHAConfig] = None,
    anchor_video: Optional[torch.Tensor] = None,
    video_from_latent: Optional[Any] = None,
    log: Optional[Any] = None,
    stage_labels: Optional[Sequence[Any]] = None,
) -> List[Dict[str, Any]]:
    """E1' 主入口：把历史段里**时不变的帧**挑出来，组装成 ``minimax_refs`` 块。

    ``history_videos``：历史段的 video latent 列表（``[B,C,T,H,W]``），
    **由近到远**（``history_videos[0]`` = 最近的一段）。
    也可以直接传 AV latent，此时用 ``video_from_latent`` 抽出 video 流。

    ``cfg.depth=0``（默认）⇒ 返回 ``[]``，主干逐位不变。

    ``stage_labels``：与 ``history_videos`` **一一对应**的真实段号（如 ``[3, 2, 1]``）。
    🔴 强烈建议传 —— 不传时日志与留痕只能标成 ``level%d``（历史列表下标），
    而产线语境里的「段」专指 ``stage_index``，两者混用**已经造成过一次错报**（见 ``_label``）。

    与旧 E1 的行为差异（可直接对照单测）：

    ========================  ====================  ========================
    场景                      旧 E1                  E1'（本模块）
    ========================  ====================  ========================
    depth=0                   ``[]``                ``[]``（一致）
    历史段间无内容复现         强行返回 2 级           **返回 ``[]``**
    段头是瞬时、中段是持久     选段头（错）            **选中段（对）**
    可锚帧不足 depth           ``raise``             **返回少于 depth 的块**
    块起点                    恒为 0（段头）          **门控选出的 token**
    块内时序                  stride 抽稀（假连续）    **连续（真）**
    ========================  ====================  ========================
    """
    cfg = cfg or TIHAConfig()
    cfg.validate()

    def _label(level: int) -> str:
        """历史列表下标 → **可读标签**。

        🔴 P0-4（2026-09-28 审核）：本函数存在的原因是 —— ``seg``/``_exp_level`` 是
        ``history_videos`` 的**下标**，而产线语境里的「段」专指 ``stage_index``。
        调用方（``h3_adapter``）手上就有真实段号，传 ``stage_labels`` 进来即可；
        **没传就明确标成 ``level``，绝不冒充段号**（历史上正是"冒充"导致了一次错报）。
        """
        if stage_labels is not None and 0 <= level < len(stage_labels):
            return "stage%s" % (stage_labels[level],)
        return "level%d" % level

    out: List[Dict[str, Any]] = []
    if int(cfg.depth) <= 0:
        return out
    if video_from_latent is not None:
        history_videos = [video_from_latent(h) for h in history_videos]
        if anchor_video is not None:
            anchor_video = video_from_latent(anchor_video)
    hist = [h for h in history_videos if h is not None]
    if len(hist) < 2:
        # 🔴 跨段共识需要 ≥2 段才有参照。1 段 ⇒ 无从判断"是否复现" ⇒ 诚实返回空，
        #    而不是像旧 E1 那样退化成"单个额外锚"（那不是多尺度，只是多加一个锚）。
        if log is not None:
            log("E1'：历史段只有 %d 段（跨段共识需 ≥2）⇒ 不出锚块。" % len(hist))
        return out

    window_tokens = H3Grid.snap_steps(int(cfg.window_frames))
    # 🔴 下取必须出声（2026-10-02）。`snap_steps` 是**向下吸附**：非网格值会被悄悄改小
    #    （实测 21 → 18 帧），而 `window_frames` 是**实验参数** —— 静默改小等于
    #    "我以为是 21 帧、实际跑了 18 帧"，与 §二·A「不报错，只是行为不对」同形态。
    #    ✅ 实测产线 GUIDE_RUNS（1/5/22/39/56/73/90/107/124）**全部**在网格上 ⇒ 正常路径不触发。
    _wf_actual = H3Grid.frames_for_steps(window_tokens)
    if log is not None and _wf_actual != int(cfg.window_frames):
        log("E1'：⚠ window_frames=%d **不在帧网格上** ⇒ 下取到 %d 帧（%d token）。"
            "产线 GUIDE_RUNS（1/5/22/39/56/…）都在网格上，不受影响。"
            % (int(cfg.window_frames), _wf_actual, window_tokens))

    # 1) 帧签名 + 跨段全局中心化（实测必需，见 TIHAConfig.center）
    mats = [frame_matrix(v, pool=int(cfg.pool), normalize=bool(cfg.normalize))
            for v in hist]
    gmean = None
    if bool(cfg.center) and len(mats) >= 2:
        mats, gmean = center_matrices(mats, normalize=bool(cfg.normalize))

    # 2) 跨段共识
    scores = consensus_scores(mats, top_q=int(cfg.top_q), mode=str(cfg.consensus_mode))

    # 3) 门控 + 排序 + 抑制 + 配额
    cands = rank_candidates(mats, scores, cfg)

    # 4) 与全局外观锚去重（可选）
    if anchor_video is not None and float(cfg.anchor_max_sim) <= 1.0:
        am = frame_matrix(anchor_video, pool=int(cfg.pool), normalize=bool(cfg.normalize))
        if gmean is not None:                     # 与历史用**同一个**均值，否则不同坐标系
            am = _l2(am - gmean) if bool(cfg.normalize) else am - gmean
        kept = []
        for c in cands:
            sim = float((mats[c.seg][c.token] @ am.t()).max())
            if sim > float(cfg.anchor_max_sim):
                if log is not None:
                    log("E1'：候选 (段%d, token%d) 与全局锚重复 (cos=%.3f) ⇒ 剔除。"
                        % (c.seg, c.token, sim))
                continue
            kept.append(c)
        cands = kept

    # 5) 块间互去重（旧 E1 缺失）—— 再按配额截断（顺序不可换，见 rank_candidates 注）
    picked: List[TIHACandidate] = []
    picked_mat: List[torch.Tensor] = []
    per_seg: Dict[int, int] = {}
    for c in cands:
        if len(picked) >= int(cfg.max_refs):
            break
        if per_seg.get(c.seg, 0) >= int(cfg.max_refs_per_seg):
            continue
        m = mats[c.seg][c.token]
        if any(float(m @ p) > float(cfg.dedup_sim) for p in picked_mat):
            continue
        picked.append(c)
        picked_mat.append(m)
        per_seg[c.seg] = per_seg.get(c.seg, 0) + 1

    # 6) 组块
    for c in picked:
        blk = assemble_block(hist[c.seg], c.token, window_tokens,
                             spatial_scale=int(cfg.spatial_scale))
        c.frames = H3Grid.frames_for_steps(int(blk.shape[2]))
        out.append({
            "kind": "video",
            "latent": blk,
            "latent_t": int(blk.shape[2]),
            "latent_h": int(blk.shape[3]),
            "latent_w": int(blk.shape[4]),
            "ref_audio_t": 0,
            "audio_latent": None,
            # 留痕（宿主忽略这些键，供排障与事后审计）
            # 🔴 P0-4（2026-09-28 审核）：``_exp_level`` 是**历史列表下标**，不是段号。
            #    两者在 ``stage_labels`` 提供时**必须都记**，否则"这两个块锚了哪个历史时刻"
            #    要靠人脑反查下标 → 段号（本项目已因此错报过一次）。
            "_exp_level": c.seg,
            # 真实段号（调用方提供时为原值；没提供则 None，**不拿下标冒充**）
            "_exp_stage": (stage_labels[c.seg]
                           if stage_labels is not None and 0 <= c.seg < len(stage_labels)
                           else None),
            "_exp_token": c.token,
            "_exp_score": round(c.score, 6),
            "_exp_rel": round(c.rel, 4),
            "_exp_frames": c.frames,
        })

    if log is not None:
        if out:
            log("E1'：出 %d 个时不变历史锚（%s）｜ 候选池 %d → 门控后 %d"
                % (len(out), ", ".join("%s@tok%d s=%.3f" % (_label(c.seg), c.token, c.score)
                                       for c in picked), len(cands), len(picked)))
        else:
            log("E1'：无帧通过时不变门控 ⇒ 不出锚块（本段沿用全局锚，行为与关闭一致）。")
    return out
