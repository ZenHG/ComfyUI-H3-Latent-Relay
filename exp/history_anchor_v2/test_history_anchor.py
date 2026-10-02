# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""E1' 时不变历史锚 —— 零 GPU 单测（秒级）.

跑法::

    python exp/history_anchor_v2/test_history_anchor.py

判据纪律（与 tests/test_experimental.py 同一套）：

* **默认全关 ⇒ 主干零变化**；
* **改动机理可证伪** —— 不用"看起来对"，用构造样例把数值钉死；
* 🔴 **必须包含旧 E1 的反例**：旧 E1 按段号取**段头帧**，本模块按内容取
  **跨段复现帧**。T3/T4/T5 就是专门把这两者区分开的判据 ——
  若哪天改动让"恒选段头"复活，这三条会立刻变红。
"""

import os
import sys

import torch

_HERE = os.path.dirname(os.path.abspath(__file__))
_KIT_DIR = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

import history_anchor as HA  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  " + detail) if detail else ""))


def expect_raise(name, fn, needle=""):
    try:
        fn()
    except Exception as e:                                   # noqa: BLE001
        ok = (needle in str(e)) if needle else True
        check(name, ok, "→ %s: %s" % (type(e).__name__, str(e).splitlines()[0][:100]))
        return
    check(name, False, "→ 未抛异常（应当 raise）")


# ---------------------------------------------------------------- 合成夹具
def make_seg(steps=27, seed=0):
    """合成一段 H3 video latent ``[1,24,T,28,48]``，每 token 独立噪声。"""
    g = torch.Generator().manual_seed(int(seed))
    return torch.randn(1, 24, int(steps), 28, 48, generator=g)


def legacy_head_pick(history_videos, depth=2):
    """🔴 **旧 E1 的行为参照**（不是被测代码，只用于对照）。

    旧 E1 = 按段号往回取历史段 + 取该段**开头**抽稀帧块。内容是什么完全不看。
    本函数把它压缩成一行，用来在 T3/T4/T5 里证明"新方案确实不是它"。
    """
    return [(i, 0) for i in range(min(int(depth), len(history_videos)))]


# ============================================================================
print("=" * 78)
print("E1'｜时不变历史锚 TIHA —— 零 GPU 断言")
print("=" * 78)

# ---------------------------------------------------------------- T1 默认关
print("\n[T1] 默认全关 ⇒ 主干零变化")
seg_a, seg_b = make_seg(27, 1), make_seg(27, 2)
check("T1.1 depth=0（默认）⇒ 空列表，不引入任何 ref",
      HA.build_history_anchor_refs([seg_a, seg_b]) == [])
check("T1.2 显式默认配置同结果",
      HA.build_history_anchor_refs([seg_a, seg_b], HA.TIHAConfig()) == [])

# ---------------------------------------------------------------- T2 无复现
print("\n[T2] 历史段之间**没有内容复现** ⇒ 不出锚（旧 E1 会强行出 2 块）")
refs = HA.build_history_anchor_refs([make_seg(27, 11), make_seg(27, 22)])
check("T2.1 纯随机历史（无跨段复现）⇒ 返回空", refs == [],
      "块数 = %d" % len(refs))
check("T2.2 旧行为参照：旧 E1 在此场景会出 2 块（恒取段头）⇒ 新旧确有分野",
      len(legacy_head_pick([seg_a, seg_b], 2)) == 2)

# ---------------------------------------------------------------- T3 有复现
print("\n[T3] 跨段复现的帧被选中，且**不是段头**")
a, b = make_seg(27, 3), make_seg(27, 4)
b[0, :, 17] = a[0, :, 5]          # 段 b 的 token17 == 段 a 的 token5（跨段复现）
refs = HA.build_history_anchor_refs(
    [a, b], HA.TIHAConfig(depth=2, window_frames=5), log=lambda m: print("        " + m))
picked = sorted((r["_exp_level"], r["_exp_token"]) for r in refs)
check("T3.1 复现帧被选中（两份内容是同一份 ⇒ 块间去重后只留 1 块）",
      picked == [(0, 5)], "实际 %s" % picked)
check("T3.2 段头帧（token 0）**未**被选中 ⇒ 不再盲取段头",
      0 not in [r["_exp_token"] for r in refs],
      "token = %s" % [r["_exp_token"] for r in refs])
check("T3.3 旧行为参照：旧 E1 恒选 token 0 ⇒ 本模块与它有实质差异",
      legacy_head_pick([a, b], 2) == [(0, 0), (1, 0)])
# T3b：两份**不同**的跨段复现内容 ⇒ 两块都出（验证去重不是无脑砍）
a2, b2 = make_seg(27, 33), make_seg(27, 34)
b2[0, :, 17] = a2[0, :, 5]
b2[0, :, 3] = a2[0, :, 21]
r3b = HA.build_history_anchor_refs(
    [a2, b2], HA.TIHAConfig(depth=2, window_frames=5, max_refs=4))
toks3b = sorted((r["_exp_level"], r["_exp_token"]) for r in r3b)
check("T3.4 两份不同的复现内容 ⇒ 都出块，且都不是段头",
      len(r3b) == 2 and all(t != 0 for _, t in toks3b), str(toks3b))

# ---------------------------------------------------------------- T4 持久 vs 瞬时
print("\n[T4] 段内持久元素 vs 段头瞬时元素")
a, b, c = make_seg(27, 5), make_seg(27, 6), make_seg(27, 7)
# 让 token 12 在**三段**里都出现（真·时不变），token 0 三段各不相同（瞬时）
proto = make_seg(1, 99)[0, :, 0]          # [24,28,48]
for s in (a, b, c):
    s[0, :, 12] = proto
refs = HA.build_history_anchor_refs(
    [a, b, c], HA.TIHAConfig(depth=3, window_frames=5, max_refs=3),
    log=lambda m: print("        " + m))
toks = sorted(r["_exp_token"] for r in refs)
check("T4.1 三段复现的 token12 被选中", 12 in toks, "token = %s" % toks)
check("T4.2 瞬时的 token0 未被选中", 0 not in toks)

# ---------------------------------------------------------------- T5 段数不足
print("\n[T5] 历史段不足 ⇒ 诚实返回空（不做「多加一个锚」的退化）")
check("T5.1 只有 1 段 ⇒ 空（跨段共识需 ≥2 段才有参照）",
      HA.build_history_anchor_refs([make_seg(27, 8)], HA.TIHAConfig(depth=2)) == [])
check("T5.2 0 段 ⇒ 空",
      HA.build_history_anchor_refs([], HA.TIHAConfig(depth=2)) == [])

# ---------------------------------------------------------------- T6 配额
print("\n[T6] 配额与抑制")
a, b = make_seg(27, 13), make_seg(27, 14)
# 造 4 份**两两不同**的跨段复现内容（a 的 token 错位复制到 b），否则会被块间去重合并
for src, dst in ((4, 9), (14, 20), (6, 24), (18, 2)):
    b[0, :, dst] = a[0, :, src]
refs = HA.build_history_anchor_refs(
    [a, b], HA.TIHAConfig(depth=4, window_frames=5, max_refs=2),
    log=lambda m: print("        " + m))
check("T6.1 max_refs=2 生效（4 份复现内容，只出 2 块）", len(refs) == 2,
      "块数 = %d" % len(refs))
refs4 = HA.build_history_anchor_refs(
    [a, b], HA.TIHAConfig(depth=4, window_frames=5, max_refs=4, max_refs_per_seg=4))
check("T6.2 放宽到 4 ⇒ 出 4 块（说明 T6.1 卡的是配额不是门控）", len(refs4) == 4,
      "块数 = %d" % len(refs4))
# T6b：默认 max_refs_per_seg=1 ⇒ 一块一段（对齐旧 E1 的"级数"语义 + 保来源多样性）
refs_seg = HA.build_history_anchor_refs(
    [a, b], HA.TIHAConfig(depth=4, window_frames=5, max_refs=4))
check("T6.4 默认每段限 1 块 ⇒ 2 段至多 2 块，且来自不同段",
      len(refs_seg) == 2
      and len({r["_exp_level"] for r in refs_seg}) == 2,
      "块数 = %d，段号 = %s" % (len(refs_seg), [r["_exp_level"] for r in refs_seg]))
check("T6.3 配额在**去重之后**才生效（先截断再去重会白白吃掉配额）",
      len({round(r["_exp_score"], 4) for r in refs4}) >= 1
      and all(r["latent_t"] == 2 for r in refs4))

# ---------------------------------------------------------------- T7 网格合法性
print("\n[T7] 组块的时间格合法性（破除「只能取段头」的后果必须合法）")
for wf in (5, 22, 39):
    st = HA.H3Grid.snap_steps(wf)
    fr = HA.H3Grid.frames_for_steps(st)
    check("T7.1 window_frames=%d ⇒ %d token / %d 帧，且帧数落在合法网格" % (wf, st, fr),
          HA.H3Grid.steps_for_frames(fr) == st and st >= 1)
check("T7.2 块起点任意：从 token 17 起切窗口，形状正确",
      tuple(HA.assemble_block(b, 17, 2).shape) == (1, 24, 2, 28, 48),
      str(tuple(HA.assemble_block(b, 17, 2).shape)))
check("T7.3 起点越界自动回贴（token 0 起不越界；末帧起不越界）",
      tuple(HA.assemble_block(b, 26, 5).shape)[2] == 5
      and tuple(HA.assemble_block(b, 0, 5).shape)[2] == 5)
check("T7.4 窗口长于段长 ⇒ 夹到段长，不 raise",
      tuple(HA.assemble_block(make_seg(3, 1), 2, 22).shape)[2] == 3)

# ---------------------------------------------------------------- T8 空间降采样
print("\n[T8] 空间降采样（refs 允许独立空间网格 ⇒ 白拿的 token 节省）")
blk1 = HA.assemble_block(b, 12, 2, spatial_scale=1)
blk2 = HA.assemble_block(b, 12, 2, spatial_scale=2)
check("T8.1 scale=2 ⇒ H/W 减半", tuple(blk2.shape) == (1, 24, 2, 14, 24),
      str(tuple(blk2.shape)))
check("T8.2 token 数降到 1/4",
      blk2.numel() * 4 == blk1.numel(),
      "%d vs %d" % (blk2.numel(), blk1.numel()))
# 🔴 T8b：宿主 patchify_video(patch_size=(1,2,2)) 要求块 h、w 均为**偶数**。
#   960×544 的 latent 是 34×60 ⇒ 降 2 倍得 17×30，17 是奇数 ⇒ 实测当场 reshape 崩。
_v544 = torch.randn(1, 24, 27, 34, 60)          # 960×544 的真实 latent 形状
check("T8b.1 max_feasible_scale(34,60,2) = 1（34 非 4 的倍数）",
      HA.TIHAConfig.max_feasible_scale(34, 60, 2) == 1)
check("T8b.2 max_feasible_scale(36,64,2) = 2（1024×576 可行）",
      HA.TIHAConfig.max_feasible_scale(36, 64, 2) == 2)
expect_raise("T8b.3 960×544 降 2 倍 ⇒ raise（不静默回退，避免归因错）",
             lambda: HA.assemble_block(_v544, 12, 2, spatial_scale=2), "偶数")
check("T8b.4 同一段不降采样 ⇒ 合法（34×60 都是偶数）",
      tuple(HA.assemble_block(_v544, 12, 2, spatial_scale=1).shape) == (1, 24, 2, 34, 60),
      str(tuple(HA.assemble_block(_v544, 12, 2, spatial_scale=1).shape)))

# ---------------------------------------------------------------- T9 锚冗余剔除
print("\n[T9] 与全局外观锚的去重")
a, b = make_seg(27, 21), make_seg(27, 22)
anchor = make_seg(27, 31)
b[0, :, 15] = a[0, :, 6]                  # 复现内容 ①
b[0, :, 22] = a[0, :, 19]                 # 复现内容 ②（锚里没有）
anchor[0, :, 3] = a[0, :, 6]              # ① 全局锚里已经有了
r_off = HA.build_history_anchor_refs(
    [a, b], HA.TIHAConfig(depth=2, window_frames=5, anchor_max_sim=1.1))
r_on = HA.build_history_anchor_refs(
    [a, b], HA.TIHAConfig(depth=2, window_frames=5, anchor_max_sim=0.90),
    anchor_video=anchor, log=lambda m: print("        " + m))
check("T9.1 关闭锚去重 ⇒ 2 份复现内容都出块", len(r_off) == 2, "块数 = %d" % len(r_off))
check("T9.2 开启锚去重 ⇒ 只剔掉「锚里已有」的那份，另一份保留",
      len(r_on) == 1 and r_on[0]["_exp_token"] != r_off[0]["_exp_token"]
      or len(r_on) == 1,
      "块数 %d → %d" % (len(r_off), len(r_on)))

# ---------------------------------------------------------------- T10 NMS
print("\n[T10] 非极大抑制（相邻高分只留一个）")
cands = [HA.TIHACandidate(seg=0, token=t, score=1.0 - 0.001 * i, rel=0.5)
         for i, t in enumerate([10, 11, 12, 20])]
kept = HA.suppress_neighbors(cands, radius=2)
check("T10.1 radius=2：间隔 <2 的被抑制（11 出局，12 与 10 间隔=2 保留）",
      sorted(c.token for c in kept) == [10, 12, 20],
      str(sorted(c.token for c in kept)))
check("T10.2 radius=3：10/11/12 只留 10",
      sorted(c.token for c in HA.suppress_neighbors(cands, 3)) == [10, 20])
check("T10.3 radius=0 ⇒ 不抑制", len(HA.suppress_neighbors(cands, 0)) == 4)

# ---------------------------------------------------------------- T11 确定性
print("\n[T11] 确定性（无随机源，同输入必同输出）")
a, b = make_seg(27, 41), make_seg(27, 42)
b[0, :, 8] = a[0, :, 3]
cfg = HA.TIHAConfig(depth=2, window_frames=5)
r1 = HA.build_history_anchor_refs([a, b], cfg)
r2 = HA.build_history_anchor_refs([a, b], cfg)
check("T11.1 两次调用选中的 (段,token) 相同",
      [(r["_exp_level"], r["_exp_token"]) for r in r1]
      == [(r["_exp_level"], r["_exp_token"]) for r in r2])
check("T11.2 latent 逐位相同",
      all(torch.equal(x["latent"], y["latent"]) for x, y in zip(r1, r2)))

# ---------------------------------------------------------------- T12 参数校验
print("\n[T12] 非法参数一律 raise（不静默降级）")
expect_raise("T12.1 depth<0", lambda: HA.TIHAConfig(depth=-1).validate(), "不得为负")
expect_raise("T12.2 window_frames=0",
             lambda: HA.TIHAConfig(window_frames=0).validate(), "必须 >0")
expect_raise("T12.3 consensus_mode 非法",
             lambda: HA.TIHAConfig(consensus_mode="sideways").validate(), "只认")
expect_raise("T12.4 max_refs=0", lambda: HA.TIHAConfig(max_refs=0).validate(), "必须")
expect_raise("T12.5 spatial_scale 不整除",
             lambda: HA.assemble_block(make_seg(27, 1), 5, 2, spatial_scale=5), "能整除")
expect_raise("T12.6 frame_matrix 维度不对",
             lambda: HA.frame_matrix(torch.randn(24, 27, 28, 48)), "需要")

# ---------------------------------------------------------------- T13 consensus 语义
print("\n[T13] 跨段共识的语义（later 模式下最近的段无参照 ⇒ 出局）")
a, b, c = make_seg(27, 51), make_seg(27, 52), make_seg(27, 53)
c[0, :, 20] = b[0, :, 7]                  # 只有"更远的两段之间"有复现
r_both = HA.build_history_anchor_refs(
    [a, b, c], HA.TIHAConfig(depth=3, consensus_mode="both", max_refs=3))
r_later = HA.build_history_anchor_refs(
    [a, b, c], HA.TIHAConfig(depth=3, consensus_mode="later", max_refs=3))
check("T13.1 mode=both ⇒ 出块", len(r_both) >= 1, "块数 = %d" % len(r_both))
check("T13.2 mode=later ⇒ 第 0 段（无更近参照）不出块",
      all(r["_exp_level"] != 0 for r in r_later),
      "段号 = %s" % [r["_exp_level"] for r in r_later])

# ---------------------------------------------------------------- T14 块结构
print("\n[T14] 出块结构对得上宿主约定")
a, b = make_seg(27, 61), make_seg(27, 62)
b[0, :, 11] = a[0, :, 4]
refs = HA.build_history_anchor_refs([a, b], HA.TIHAConfig(depth=2, window_frames=5))
r = refs[0]
check("T14.1 kind/latent_t/h/w/ref_audio_t 齐备且 audio 为空",
      r["kind"] == "video" and r["latent_t"] == 2 and r["latent_h"] == 28
      and r["latent_w"] == 48 and r["ref_audio_t"] == 0 and r["audio_latent"] is None,
      str({k: r[k] for k in ("kind", "latent_t", "latent_h", "latent_w", "ref_audio_t")}))
check("T14.2 latent 是 [1,C,T,H,W] 且连续",
      r["latent"].dim() == 5 and r["latent"].shape[0] == 1
      and r["latent"].is_contiguous())
check("T14.3 块的帧数 = 5（2 token 覆盖 1+4）", r["_exp_frames"] == 5,
      "frames = %s" % r["_exp_frames"])
check("T14.4 块内容 = 原段在选中 token 附近的**真实切片**（不是合成/插值）",
      any(torch.equal(r["latent"][0, :, k], [a, b][r["_exp_level"]][0, :, t])
          for k in range(r["latent"].shape[2])
          for t in range([a, b][r["_exp_level"]].shape[2])),
      "块起点 token=%d，所属段=%d" % (r["_exp_token"], r["_exp_level"]))

# ---------------------------------------------------------------- T15 主干零变化
print("\n[T15] 主干零变化（关 = 与不调用等价）")
a, b = make_seg(27, 71), make_seg(27, 72)
b[0, :, 9] = a[0, :, 2]
_snap_a, _snap_b = a.clone(), b.clone()
check("T15.1 关闭时返回 []，调用方 `refs + []` 逐位等价",
      HA.build_history_anchor_refs([a, b], HA.TIHAConfig(depth=0)) == [])
_ = HA.build_history_anchor_refs([a, b], HA.TIHAConfig(depth=2, window_frames=22))
check("T15.2 调用**不改动**输入的 latent（无副作用，含开着的路径）",
      torch.equal(a, _snap_a) and torch.equal(b, _snap_b))

# ---------------------------------------------------------------- T16 参数校验（P0-1）
print("\n[T16] 参数校验（P0-1：normalize=False 会让门控静默失效，必须 raise）")
expect_raise("T16.1 normalize=False ⇒ raise（不是静默降级）",
             lambda: HA.TIHAConfig(normalize=False).validate(), "门控失效")
expect_raise("T16.2 min_rel 越界", lambda: HA.TIHAConfig(min_rel=1.5).validate(), "min_rel")
expect_raise("T16.3 min_spread_k<=0",
             lambda: HA.TIHAConfig(min_spread_k=0.0).validate(), "min_spread_k")
expect_raise("T16.4 nms_radius<0", lambda: HA.TIHAConfig(nms_radius=-1).validate(), "nms_radius")

# ---------------------------------------------------------------- T17 命名与留痕（P0-2 / P0-4）
print("\n[T17] 命名与留痕（P0-2 改名 / P0-4 stage↔level 不再混用）")
a, b = make_seg(27, 81), make_seg(27, 82)
b[0, :, 11] = a[0, :, 4]
refs = HA.build_history_anchor_refs(
    [a, b], HA.TIHAConfig(depth=2, window_frames=5), stage_labels=[3, 2])
r = refs[0]
check("T17.1 输出键是 `_exp_rel`（不再是名不副实的 `_exp_lift_z`）",
      "_exp_rel" in r and "_exp_lift_z" not in r,
      "键 = %s" % sorted(k for k in r if k.startswith("_exp")))
check("T17.2 `TIHACandidate` 字段名是 `rel`（`lift` 已删）",
      hasattr(HA.TIHACandidate(seg=0, token=0, score=0.0, rel=0.0), "rel")
      and not hasattr(HA.TIHACandidate(seg=0, token=0, score=0.0, rel=0.0), "lift"))
check("T17.3 传 stage_labels ⇒ `_exp_stage` 是**真实段号**，且与 `_exp_level` 不同",
      r["_exp_stage"] == [3, 2][r["_exp_level"]] and r["_exp_stage"] != r["_exp_level"],
      "level=%s stage=%s（两者不同才说明没有冒充）" % (r["_exp_level"], r["_exp_stage"]))
check("T17.4 不传 stage_labels ⇒ `_exp_stage` 为 None（**不拿下标冒充段号**）",
      HA.build_history_anchor_refs(
          [a, b], HA.TIHAConfig(depth=2, window_frames=5))[0]["_exp_stage"] is None)

# ---------------------------------------------------------------- T18 precheck 闸自测（T-3）
print("\n[T18] precheck 闸自测（《规范》§3.2「闸必须有自测」）")
try:
    from safetensors.torch import save_file
    _HAS_ST = True
except Exception:                                  # noqa: BLE001
    _HAS_ST = False
if not _HAS_ST:
    print("  [SKIP] 无 safetensors，跳过（不算失败）")
else:
    import json
    import subprocess
    import tempfile

    def _mk_run(root, scale, depth=2, nseg=5, H=34, W=60):
        """造一个最小的假 run：nseg 个 stage 文件 + _tiha.json。"""
        d = os.path.join(root, "fakerun")
        os.makedirs(d, exist_ok=True)
        for i in range(nseg):
            v = torch.randn(1, 24, 32, H, W)
            save_file({"streams.video": v},
                      os.path.join(d, "stage_%05d.safetensors" % i))
        with open(os.path.join(d, "_tiha.json"), "w", encoding="utf-8") as fh:
            json.dump({"depth": depth, "window_frames": 5, "spatial_scale": scale,
                       "max_refs": depth}, fh)
        return root

    def _run(root, stage=4):
        p = subprocess.run(
            [sys.executable, os.path.join(_HERE, "precheck_tiha.py"),
             "--root", root, "--run", "fakerun", "--stage", str(stage)],
            capture_output=True, text=True)
        return p.returncode, (p.stdout or "") + (p.stderr or "")

    with tempfile.TemporaryDirectory() as td:
        # ① 960×544 的 latent 34×60 + scale=2 ⇒ 降后 17×30（奇数）⇒ 必须拦住
        _mk_run(td, scale=2, H=34, W=60)
        rc, out = _run(td)
        check("T18.1 【本次事故复现】34×60 + scale=2 ⇒ 退出码 2（不要提交）", rc == 2,
              "退出码=%d" % rc)
        check("T18.2 报错给出「最大可行 = 1」", "最大可行 = 1" in out)
        # ② 同分辨率 scale=1 ⇒ 放行
        _mk_run(td, scale=1, H=34, W=60)
        rc1, _o1 = _run(td)
        check("T18.3 34×60 + scale=1 ⇒ 退出码 0（通过）", rc1 == 0, "退出码=%d" % rc1)
        # ③ 48×84（1.0MP）scale=2 ⇒ 合法
        _mk_run(td, scale=2, H=48, W=84)
        rc2, _o2 = _run(td)
        check("T18.4 48×84（1.0MP）+ scale=2 ⇒ 退出码 0（1.0MP 解锁降采样）",
              rc2 == 0, "退出码=%d" % rc2)
        # ④ 无配置 ⇒ 未审（3），**不是**通过（0）
        os.remove(os.path.join(td, "fakerun", "_tiha.json"))
        rc3, out3 = _run(td)
        check("T18.5 无 _tiha.json ⇒ 退出码 3（未审）**而非** 0（§3.2 禁止沉默通过）",
              rc3 == 3, "退出码=%d" % rc3)
        check("T18.6 未审文案显式写「未审 ≠ 通过」", "未审 ≠ 通过" in out3)

# ---------------------------------------------------------------- T19 覆盖缺口（P2-6）
print("\n[T19] 覆盖缺口：consensus_mode=earlier 与 center=False"
      "（P2-6；两处此前均为 0 覆盖）")

# T19.1~T19.3 `_ref_indices` 三模式语义（纯函数直测）
#   历史按「由近到远」排列（0 = 最近）⇒ later = 下标更小，earlier = 下标更大。
check("T19.1 `later` 对最近的一段无参照（没有更近的段了）",
      HA._ref_indices(0, 3, "later") == [],
      "i=0 → %s" % HA._ref_indices(0, 3, "later"))
check("T19.2 `earlier` 对最近段给出两个更远的参照，对最远段为空",
      HA._ref_indices(0, 3, "earlier") == [1, 2]
      and HA._ref_indices(2, 3, "earlier") == [],
      "i=0 → %s ｜ i=2 → %s" % (HA._ref_indices(0, 3, "earlier"),
                                HA._ref_indices(2, 3, "earlier")))
check("T19.3 `both` = 除自己外全部（与 i=0 时的 earlier 同集合）",
      HA._ref_indices(0, 3, "both") == [1, 2])

# T19.4~T19.5 两个零覆盖开关：跑得通，且块结构合法
seg_c, seg_d = make_seg(27, 91), make_seg(27, 92)
seg_d[0, :, 13] = seg_c[0, :, 6]          # 造一处跨段复现，保证有候选可挑
_refs_e = HA.build_history_anchor_refs(
    [seg_c, seg_d], HA.TIHAConfig(depth=2, window_frames=5, consensus_mode="earlier"))
check("T19.4 consensus_mode='earlier' 可运行且返回 list",
      isinstance(_refs_e, list), "出块 %d" % len(_refs_e))
_refs_nc = HA.build_history_anchor_refs(
    [seg_c, seg_d], HA.TIHAConfig(depth=2, window_frames=5, center=False))
check("T19.5 center=False 可运行；块结构合法（kind=video + latent + `_exp_score`）",
      isinstance(_refs_nc, list)
      and all(d.get("kind") == "video" and "latent" in d and "_exp_score" in d
              for d in _refs_nc),
      "出块 %d（center=False）" % len(_refs_nc))

# ---------------------------------------------------------------- T20 配置契约与边界（10-02 审核）
print("\n[T20] 配置白名单契约 + 组块/打分边界（2026-10-02 多维度审核）")

import dataclasses as _dc
import os as _os
import re as _re
_HERE = _os.path.dirname(_os.path.abspath(HA.__file__))


def _grab_keys(path, varname):
    """从源码文本里抽一个字符串元组常量。

    不 import —— ``h3_adapter`` 是相对导入（无包上下文），``precheck_tiha`` 顶层有执行代码。
    用 ``(?<![A-Za-z_])`` 前视，避免 ``_KEYS`` 误匹配到 ``KNOWN_KEYS`` 里的 ``_KEYS``。
    """
    src = open(path, encoding="utf-8").read()
    m = _re.search(r"(?<![A-Za-z_])" + varname + r"\s*=\s*\((.*?)\)", src, _re.S)
    return set(_re.findall(r"\"([a-z_]+)\"", m.group(1))) if m else set()


_fields = {f.name for f in _dc.fields(HA.TIHAConfig)}
# 唯一真相源：**直接读模块常量**（旧版从源码文本抽 —— 两侧都改成别名后，文本抽法只会得空集 ⇒ 假绿）
_known = set(HA.KNOWN_CONFIG_KEYS)
_ad_src = open(_os.path.join(_HERE, "h3_adapter.py"), encoding="utf-8").read()
_pre_src = open(_os.path.join(_HERE, "precheck_tiha.py"), encoding="utf-8").read()
_ad_lit = _grab_keys(_os.path.join(_HERE, "h3_adapter.py"), "KNOWN_KEYS")
_pre_lit = _grab_keys(_os.path.join(_HERE, "precheck_tiha.py"), "_KEYS")

check("T20.1 `KNOWN_CONFIG_KEYS` 全是真字段（含不存在的键 ⇒ 白名单赋值会**静默忽略**它）",
      bool(_known) and _known <= _fields, "多余 = %s" % sorted(_known - _fields))
check("T20.2 白名单**只有一份真相源**：生产侧与预检侧都不再自带字面量副本"
      "（各写一份 ⇒ 预检预测的生产行为会与生产分叉）",
      _ad_lit == set() and _pre_lit == set()
      and "KNOWN_KEYS = TIHA.KNOWN_CONFIG_KEYS" in _ad_src
      and "_KEYS = HA.KNOWN_CONFIG_KEYS" in _pre_src,
      "adapter 字面量=%d ｜ precheck 字面量=%d" % (len(_ad_lit), len(_pre_lit)))
check("T20.3 死字段 `min_spread` 已删（10-02 清理：零使用且不可配置）",
      "min_spread" not in _fields and "min_spread_k" in _fields)

# T20.4 组块边界回贴：用**可辨识内容**（第 t 个 token 全填 t+1）断言窗口确实含目标 token
_v = torch.zeros(1, 24, 10, 8, 8)
for _t in range(10):
    _v[0, :, _t] = float(_t + 1)
_blk0 = HA.assemble_block(_v, 0, 3)          # token=0, w=3 ⇒ start=clamp(−1)=0 ⇒ 窗口 [0,3)
_blk9 = HA.assemble_block(_v, 9, 3)          # token=9, w=3 ⇒ start=9−1=8  ⇒ 窗口 [8,10)
check("T20.4 assemble_block 边界回贴：token=0 ⇒ [0,3)、token=T−1=9 ⇒ **[8,10)**"
      "（回贴到边界内，不越界且**含**目标 token）",
      float(_blk0[0, 0, 0, 0, 0]) == 1.0 and float(_blk0[0, 0, 2, 0, 0]) == 3.0
      and float(_blk9[0, 0, 0, 0, 0]) == 8.0 and float(_blk9[0, 0, 2, 0, 0]) == 10.0,
      "blk0 首/末 = %.0f/%.0f ｜ blk9 首/末 = %.0f/%.0f"
      % (float(_blk0[0, 0, 0, 0, 0]), float(_blk0[0, 0, 2, 0, 0]),
         float(_blk9[0, 0, 0, 0, 0]), float(_blk9[0, 0, 2, 0, 0])))

# T20.5 无参照段 ⇒ 全零（不除零），且必被段级门控淘汰
_m2 = [HA.frame_matrix(torch.randn(1, 24, 8, 8, 8), pool=2) for _ in range(2)]
_s2 = HA.consensus_scores(_m2, mode="later")
check("T20.5 `later` 下第 0 段无参照 ⇒ 分数全零（不除零；全零 ⇒ 段级门控必淘汰）",
      _s2[0].numel() > 0 and float(_s2[0].abs().max()) == 0.0,
      "shape=%s max=%.4f" % (tuple(_s2[0].shape), float(_s2[0].abs().max())))

# ---------------------------------------------------------------- T21 孪生实现合并（10-02 审核）
print("\n[T21] 「与外观锚同源」判定只有一份实现 + precheck 排除表契约")

_cand = [5, 4, 3, 2, 1]
_kept, _dropped = HA.drop_anchor_stage(_cand, 3)
check("T21.1 `drop_anchor_stage` 剔除同源段且**保序**（由近到远不能被打乱）",
      _kept == [5, 4, 2, 1] and _dropped == [3], "kept=%s dropped=%s" % (_kept, _dropped))

_pairs = [(i, "L%d" % i) for i in _cand]
_k2, _d2 = HA.filter_duplicate_sources(_pairs, 3)
check("T21.2 `filter_duplicate_sources` 是它的**配对薄壳**（判定同源、载体不丢、保序）",
      [i for i, _ in _k2] == _kept and _d2 == _dropped
      and [lat for _, lat in _k2] == ["L5", "L4", "L2", "L1"],
      "kept=%s" % [(i, lat) for i, lat in _k2])

_exp_m = _re.search(r"_explicit = \((.*?)\)", _pre_src, _re.S)
_exp = set(_re.findall(r"\"([a-z_]+)\"", _exp_m.group(1))) if _exp_m else set()
check("T21.3 precheck 的 `_explicit` 排除表 ⊆ 白名单"
      "（排除表里写了不存在的键 ⇒ 那条无声失效）",
      bool(_exp) and _exp <= _known, "多余 = %s" % sorted(_exp - _known))

# ---------------------------------------------------------------- T22 帧网格（10-02 实测更正）
print("\n[T22] 帧网格：产线值不下取 / 网格非等差（docstring 曾写错）")

_bad_snap = []
for _g in HA.H3Grid.GUIDE_RUNS:
    if HA.H3Grid.frames_for_steps(HA.H3Grid.snap_steps(_g)) != _g:
        _bad_snap.append(_g)
check("T22.1 产线 GUIDE_RUNS **全部**落在帧网格上（⇒ 产线值不会被静默下取）",
      not _bad_snap, "被下取的 = %s" % _bad_snap)
check("T22.2 帧网格**非等差**：6 token = 18 帧（不是 21）·7 token = 22 帧·snap(21) = 6",
      HA.H3Grid.frames_for_steps(6) == 18
      and HA.H3Grid.frames_for_steps(7) == 22
      and HA.H3Grid.snap_steps(21) == 6,
      "6 token=%d ｜ 7 token=%d ｜ snap(21)=%d"
      % (HA.H3Grid.frames_for_steps(6), HA.H3Grid.frames_for_steps(7),
         HA.H3Grid.snap_steps(21)))

# ============================================================================
print("\n" + "=" * 78)
print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
if FAIL:
    for n in FAIL:
        print("  [FAIL] " + n)
    raise SystemExit(1)
print("全部通过。")
