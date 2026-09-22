# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Relay-Kit contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""H3 Relay Kit 离线单测（零 GPU、零模型、秒级）

跑法（在包目录下）：
    python tests/test_relay_core.py

脚本会自动往上找到 ComfyUI 根目录（本包装在 ``<ComfyUI>/custom_nodes/`` 下时）。
装在别处 / 只 clone 了本包时，用环境变量指定：

    COMFYUI_PATH=/path/to/ComfyUI python tests/test_relay_core.py

只需要 ``torch`` 与 ``safetensors``；不加载任何模型、不碰显存。
但需要能 import 到 ComfyUI（comfy.nested_tensor / node_helpers / folder_paths）。

覆盖：
  1. 时序网格自洽（22 帧=7 步 / 39 帧=12 步 / 192 帧=57 步）
  2. 尾段切片逐位正确 + 起始必须落在 5 步周期边界
  3. 硬错误：分辨率不一致 / 非网格帧数 / 窗口大于段长 → 必须 raise
  4. conditioning 注入：keyframes 合并、钉住区旧锚丢弃、音频 ref 追加
  5. AV latent 落盘往返一致
  6. 裁头重叠：画面音频同裁逐位正确、时长对齐、越界 raise
  7. 节点返回值契约：每个分支返回路数 == len(RETURN_TYPES)
  8. 接缝自检 find_head_jump / describe_head_jump
  9. 段号声明与取源矛盾必须 raise（不得静默直通）
 10. streams_from_latent 对非 NestedTensor 的健壮性（不得按 batch 维误拆）
 11. H3RelayChain 的 status 槽位与前端一致
 12. 沉降帧 settle：pin 与 crop 解耦 + 自动检测（观测端决定，用户零配置）
 13. 沉降三路检测：硬跳验身 / 锐度塌陷-恢复 / 双基准与量纲不变
 14. 拷贝桥（0.4.0）：位级拷贝 + 噪声掩码 + raise 防线
 15. 色档收敛信号（0.4.1）：注噪/taper 收敛尾巴的观测端
 16. 0.4.2 回归：导出音频分支 / 中文 note / 服务端校验 / 掩码设备 / 契约降级缓存
 17. 噪声斜坡 ramp（0.4.3）：软证据接缝——连续掩码 = 逐 token sigma 标签
 18. 复现残留（0.5.0 / 调研 §13 D7）：第 4 路检测——现有三路都看不见复现帧；**默认关闭**
 19. 跨段统计匹配（0.5.0 / 调研 §2）：Reinhard 式一阶+二阶矩，段头↔上段末帧；护栏与无重影；
     **统计量取样窗**（0.5.0 修正）：取紧贴缝那帧 vs 旧口径全区聚合——后者会把首帧推过 guide
 20. 拆节点（0.5.0）：`H3RelayPost` 独立 + TrimAV 追加第 4 路输出 `prev_tail`
 21. 重叠区双向融合 blend（0.5.0 / 调研 §4）：窗形权重（smoothstep / hann），两端导数为 0
 22. 音频缝（0.5.0 / 节点内实现）：长度守恒、最静窗、边界 blend、床环铺、采样率/声道兜底、落盘往返、节点守卫
 23. 音画同步守恒（0.6.2）：`joined` 只在给了画面裁量时才能交叉（否则 raise）；默认等长拼接；
     TrimAV 直接给出 `join_align_seconds` 建议值
 24. 床源选窗语音规避**全路径**（0.6.5）：瓦片档（tile>0）也必须过判据、阈值收紧到 0、
     E5 错开不再被静默忽略、rank 约定一致、无解时不 raise、报告不静默、O(T²) 回归锁、
     持续帧滤波（瞬态不计入）、退化输入不炸、前缀和能量选窗 == 参照
 25. 🛡 patch 台词守卫（0.6.5）：patch 不许吃本段台词（收缩/关闭/零副作用/关=旧行为）
 26. 多段拼接成片（0.6.7）：探测/体检（含「音频绕过裁重叠」判据）/画面流拷贝无损/
     音频代际 2 → 1（PCM 边车直读 ⇒ 逐位一致；AAC 默认 256k）；
     音频逐段去 priming 对齐/退路（重编码）/history 落盘条目筛选/多生产者候选裁决/
     PyAV 版本兼容/CLI 入口（合成 mp4 + 合成边车，零 GPU）
 27. 画质域 AV latent 分块放大（0.6.8）：块数自选的合规边界（每块 ≥ 2·overlap+1）、
     单块=整段放大恒等、分块边界无跳变、帧数不许被改、上游口径常量不漂移（拆包/回包见节点）
"""

import importlib.util
import os
import sys
import tempfile
import traceback

import torch

# ---------------------------------------------------------------- 定位 ComfyUI
# 本包通常装在 <ComfyUI>/custom_nodes/<本包>/，往上两级就是 ComfyUI 根。
# 装在别处时用 COMFYUI_PATH 显式指定。
_KIT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COMFY = os.environ.get("COMFYUI_PATH") or os.path.dirname(os.path.dirname(_KIT_DIR))
if not os.path.isdir(os.path.join(_COMFY, "comfy")):
    _MSG = ("\n[FAIL] 找不到 ComfyUI 根目录（试过：%s）\n"
            "       请把本包装在 <ComfyUI>/custom_nodes/ 下，或设环境变量：\n"
            "       COMFYUI_PATH=/path/to/ComfyUI python tests/test_relay_core.py\n\n"
            % _COMFY)
    sys.stderr.write(_MSG)
    # 本文件是「脚本式」测试（python tests/test_relay_core.py）。
    # 但很多人会顺手跑 `pytest tests/` —— 模块级 SystemExit 会让 pytest 报
    # INTERNALERROR 整个崩掉（不是 skip，是内部错误，体验极差）。
    # 故在 pytest 下改为 skip，脚本下才 exit 2。
    if "pytest" in sys.modules:
        import pytest
        pytest.skip("找不到 ComfyUI 根目录（试过：%s）；设 COMFYUI_PATH 后重试" % _COMFY,
                    allow_module_level=True)
    raise SystemExit(2)
sys.path.insert(0, _COMFY)
sys.path.insert(0, _KIT_DIR)

import comfy.nested_tensor as NT  # noqa: E402

import relay_core as CORE  # noqa: E402


PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  " + detail) if detail else ""))


def expect_raise(name, fn, needle=""):
    try:
        fn()
    except Exception as e:
        ok = (needle in str(e)) if needle else True
        check(name, ok, "→ %s: %s" % (type(e).__name__, str(e).splitlines()[0][:90]))
        return
    check(name, False, "→ 未抛异常（应当 raise）")


def make_latent(frames, width=768, height=448, seed=0):
    """合成一段 H3 AV latent：视频 [1,24,T,28,48]、音频 [1,32,2,~] 。"""
    steps = CORE.steps_for_frames(frames)
    assert steps is not None, "%d 帧不是合法网格窗口" % frames
    g = torch.Generator().manual_seed(seed)
    v = torch.randn(1, 24, steps, height // 16, width // 16, generator=g)
    at = int(round(CORE.FRAME_RESCALE * frames))
    a = torch.randn(1, 32, 2, at, generator=g)
    return {"samples": NT.NestedTensor([v, a])}, v, a


print("=" * 78)
print("1) 时序网格自洽")
print("=" * 78)
check("pixel_frames(7) == 22", CORE.pixel_frames(7) == 22, "得到 %d" % CORE.pixel_frames(7))
check("pixel_frames(12) == 39", CORE.pixel_frames(12) == 39, "得到 %d" % CORE.pixel_frames(12))
check("steps_for_frames(22) == 7", CORE.steps_for_frames(22) == 7, "得到 %s" % CORE.steps_for_frames(22))
check("steps_for_frames(39) == 12", CORE.steps_for_frames(39) == 12, "得到 %s" % CORE.steps_for_frames(39))
check("steps_for_frames(192) == 57", CORE.steps_for_frames(192) == 57, "得到 %s" % CORE.steps_for_frames(192))
check("steps_for_frames(30) == 9（30 也在网格上）", CORE.steps_for_frames(30) == 9, "得到 %s" % CORE.steps_for_frames(30))
check("GUIDE_RUNS 是「配 17k+5 段长」的推荐集，非全部网格值",
      CORE.steps_for_frames(30) is not None and 30 not in CORE.GUIDE_RUNS)
check("snap_guide_run(35) == 22", CORE.snap_guide_run(35) == 22, "得到 %d" % CORE.snap_guide_run(35))
check("GUIDE_RUNS 全部落在网格上", not CORE.self_check(), str(CORE.self_check()))
check("step_offsets(7) 首位 0", CORE.step_offsets(7)[0] == 0, str(CORE.step_offsets(7)))

print()
print("=" * 78)
print("2) 尾段切片逐位正确")
print("=" * 78)
prev, pv, pa = make_latent(192, seed=1)
cur, cv, ca = make_latent(192, seed=2)

blocks, offsets, covered = CORE.video_tail_from_latent(prev, 22)
check("22 帧尾段切出 7 块", len(blocks) == 7, "得到 %d" % len(blocks))
check("覆盖帧数 == 22", covered == 22, "得到 %d" % covered)
check("锚位起点 == 0", offsets[0] == 0, str(offsets))
total = int(pv.shape[2])
start = total - 7
same = all(torch.equal(blocks[k], pv[:1, :, start + k:start + k + 1]) for k in range(7))
check("每块与源 latent 尾段逐位相同", same)
check("尾段最后一块 == 源 latent 最后一 token",
      torch.equal(blocks[-1], pv[:1, :, -1:]))

# 注意第 3 参是**源段总帧数**（外溢偏差对着总帧数量才有意义，见函数 docstring）
tail_a, rt, overhang, raw_steps, grid_off = CORE.audio_tail_from_latent(prev, 22, 192)
check("音频尾段步数 == ceil(22/24*40) = 37（拓宽到整步）", rt == 37, "得到 %d" % rt)
check("raw_steps 报告理论步数 36.67", abs(raw_steps - 22 / 24.0 * 40) < 1e-6,
      "得到 %.4f" % raw_steps)
check("音频尾段与源尾段逐位相同", torch.equal(tail_a, pa[:1, ..., int(pa.shape[-1]) - rt:]))
check("音频栅格外溢在容差内", abs(overhang) < 0.5, "overhang=%.3f" % overhang)
check("标准网格段 grid_off=False", grid_off is False)
_, rt7, _, raw7, _ = CORE.audio_tail_from_latent(prev, 7, 192)
check("off-grid 音频窗 7 帧 → 拓宽到 12 整步（11.67 向上）", rt7 == 12, "得到 %d" % rt7)
check("off-grid 报告理论步数 11.67", abs(raw7 - 7 / 24.0 * 40) < 1e-6, "得到 %.4f" % raw7)

print()
print("=" * 78)
print("3) 硬错误必须 raise")
print("=" * 78)
wide, _, _ = make_latent(192, width=928, height=1600, seed=3)
expect_raise("分辨率不一致 → raise",
             lambda: CORE.plan_relay(cur, wide, 22), "无法缩放")
expect_raise("非推荐窗口 30 帧 → raise（不静默吸附）",
             lambda: CORE.plan_relay(cur, prev, 30), "整步续接窗口")
lat124, l124v, _ = make_latent(124, seed=5)
expect_raise("窗口 >= 段长 → raise",
             lambda: CORE.plan_relay(lat124, lat124, 124), "没有新内容")
check("未接 context → 直通（applied=False）",
      not CORE.plan_relay(cur, None, 22).applied)

# 短段：128 帧(38 步) 取 22 帧尾段 → start=31, 31%5=1 → 必须 raise
short, _, _ = make_latent(124, seed=4)
st = int(short["samples"].tensors[0].shape[2])
print("      （124 帧 = %d 步；取 22 帧尾段 start=%d, %%5=%d）" % (st, st - 7, (st - 7) % 5))
if (st - 7) % 5 == 0:
    ok = CORE.plan_relay(cur, short, 22).applied
    check("124 帧上取 22 帧尾段（周期对齐）→ 通过", ok)
else:
    expect_raise("124 帧上取 22 帧尾段（周期错位）→ raise",
                 lambda: CORE.plan_relay(cur, short, 22), "周期位置")

print()
print("=" * 78)
print("4) conditioning 注入")
print("=" * 78)
import node_helpers  # noqa: E402

MARK = 7.0
cond = [[torch.zeros(1, 8), {"minimax_keyframes": [
    {"resolved_frame_index": 0, "latent": torch.full((1, 24, 1, 28, 48), MARK)},
    {"resolved_frame_index": 191, "latent": torch.ones(1, 24, 1, 28, 48)},
]}]]
plan = CORE.plan_relay(cur, prev, 22)
out = CORE.apply_relay(cond, plan)
kfs = out[0][1]["minimax_keyframes"]
check("keyframes 数量 = 保留 1 + 新增 7", len(kfs) == 8, "得到 %d" % len(kfs))
f0 = [k for k in kfs if int(k["resolved_frame_index"]) == 0]
check("钉住区内的旧锚（标记 latent）被丢弃，只剩续接块自带的 frame 0",
      len(f0) == 1 and float(f0[0]["latent"].max()) != MARK,
      "frame0 锚数=%d" % len(f0))
check("末帧锚（frame 191）被保留",
      any(int(k["resolved_frame_index"]) == 191 for k in kfs))
check("音频 ref 已追加到 minimax_refs",
      len(out[0][1].get("minimax_refs") or []) == 1
      and out[0][1]["minimax_refs"][0]["kind"] == "audio")
check("直通路径不改 conditioning",
      CORE.apply_relay(cond, CORE.plan_relay(cur, None, 22)) is cond)

# 钉子（2026-09-19）：出局的旧锚必须留痕 —— 静默丢会让「少了一个锚」事后无从查起。
plan_n = CORE.plan_relay(cur, prev, 22)
CORE.apply_relay(cond, plan_n)
check("出局旧锚的**落点**写进 report（不只是数量）",
      any("重复声明" in n and "[0]" in n for n in plan_n.notes),
      "notes=%r" % (plan_n.notes,))
cond_far = [[torch.zeros(1, 8), {"minimax_keyframes": [
    {"resolved_frame_index": 191, "latent": torch.ones(1, 24, 1, 28, 48)},
]}]]
plan_far = CORE.plan_relay(cur, prev, 22)
CORE.apply_relay(cond_far, plan_far)
check("反证：旧锚全在钉住区外 ⇒ report 不出现该条（钉子能失败）",
      not any("重复声明" in n for n in plan_far.notes),
      "notes=%r" % (plan_far.notes,))

print()
print("=" * 78)
print("5) AV latent 落盘往返")
print("=" * 78)
tmp = os.path.join(tempfile.gettempdir(), "relay_kit_test", "stage_00000.safetensors")
CORE.save_av_latent(prev, tmp, note="unit test")
back = CORE.load_av_latent(tmp)
bv, ba = CORE.streams_from_latent(back)
check("视频流往返逐位相同", torch.equal(bv, pv))
check("音频流往返逐位相同", torch.equal(ba, pa))
check("往返后仍是 NestedTensor", hasattr(back["samples"], "unbind"))
check("往返后可再次切尾段",
      all(torch.equal(blocks[k], bv[:1, :, start + k:start + k + 1]) for k in range(7)))
print("      " + CORE.describe_latent(back))

print()
print("=" * 78)
print("6) 裁头部重叠（H3RelayTrimAV 的底层：视频音频同裁）")
print("=" * 78)
SR = 32000
N_FRAMES = 73
N_SAMPLES = int(round(N_FRAMES / 24.0 * SR))
imgs = torch.arange(N_FRAMES * 2 * 2 * 3, dtype=torch.float32).reshape(N_FRAMES, 2, 2, 3)
wave = torch.arange(2 * N_SAMPLES, dtype=torch.float32).reshape(1, 2, N_SAMPLES)
aud = {"waveform": wave, "sample_rate": SR}

t_imgs = CORE.trim_head_frames(imgs, 22)
t_aud = CORE.trim_audio_head(aud, 22, 24.0)
check("画面 73 → 51 帧", int(t_imgs.shape[0]) == 51, "得到 %d" % int(t_imgs.shape[0]))
check("裁后首帧 == 原第 22 帧（逐位）", torch.equal(t_imgs[0], imgs[22]))
check("裁后尾帧 == 原尾帧", torch.equal(t_imgs[-1], imgs[-1]))
check("音频 97333 → 68000 采样点",
      int(t_aud["waveform"].shape[-1]) == N_SAMPLES - int(round(22 / 24.0 * SR)),
      "得到 %d" % int(t_aud["waveform"].shape[-1]))
check("裁后音画时长一致（51 帧 == 68000 点 @32k）",
      abs(int(t_aud["waveform"].shape[-1]) / SR - 51 / 24.0) < 1e-6,
      "%.4fs" % (int(t_aud["waveform"].shape[-1]) / SR))
check("音频裁后首点 == 原第 29333 点（逐位）",
      float(t_aud["waveform"][0, 0, 0]) == float(wave[0, 0, int(round(22 / 24.0 * SR))]))
check("trim=0 → 画面原样返回", CORE.trim_head_frames(imgs, 0) is imgs)
check("trim=0 → 音频原样返回", CORE.trim_audio_head(aud, 0, 24.0) is aud)
check("audio=None → 返回 None（不炸）", CORE.trim_audio_head(None, 22, 24.0) is None)
check("采样率原样保留", int(t_aud["sample_rate"]) == SR)
expect_raise("裁的帧数 >= 段长 → raise（裁完没画面）",
             lambda: CORE.trim_head_frames(imgs, 73), "裁完就没画面")
expect_raise("裁的采样点 >= 音频长度 → raise",
             lambda: CORE.trim_audio_head(aud, 999, 24.0), "音频只有")

print()
print("=" * 78)
print("7) 节点返回值契约（每个分支的返回路数必须 == len(RETURN_TYPES)）")
print("=" * 78)
import importlib.util  # noqa: E402
import types  # noqa: E402

# 目录名含连字符，不能直接当包名 → 伪造一个包壳再按文件加载 nodes.py
_KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg = types.ModuleType("h3relay_kit")
_pkg.__path__ = [_KIT]
sys.modules["h3relay_kit"] = _pkg
_spec = importlib.util.spec_from_file_location("h3relay_kit.nodes", os.path.join(_KIT, "nodes.py"))
NODES = importlib.util.module_from_spec(_spec)
sys.modules["h3relay_kit.nodes"] = NODES
_spec.loader.exec_module(NODES)


def unwrap(res):
    """节点返回 `{"ui": …, "result": …}` 时取 result（与宿主行为一致）。

    0.6.7 起「裁重叠」与「音频缝」用 ui 键回显 PCM 边车路径 ⇒ 宿主把 ui 收进
    history.outputs，连线数据仍是 result。**直接调节点函数的测试必须过这一层**，
    否则 `a, b, c = node.fn(...)` 会 unpack 到 dict 的键上（踩过：22.13 报 "expected 3, got 2"）。
    """
    return res["result"] if isinstance(res, dict) and "result" in res else res


def arity(cls, kw):
    out = unwrap(getattr(cls(), cls.FUNCTION)(**kw))
    return len(out) == len(cls.RETURN_TYPES), out


ok, out = arity(NODES.H3RelayCopyBridge,
            dict(latent=cur, context_latent=prev, context_frames=22, conditioning=cond))
check("CopyBridge（复合桥）续接分支返回 4 路（含第 4 路 conditioning）", ok, "实得 %d" % len(out))
check("续接分支 trim_frames 输出 > 0（供裁剪节点）", int(out[2]) > 0, "得到 %r" % (out[2],))
check("复合桥第 4 路 conditioning 已注入（非 None）", out[3] is not None, "得到 %r" % (out[3],))

ok, out = arity(NODES.H3RelayCopyBridge,
            dict(latent=cur, context_latent=None, context_frames=22, stage_index=0, conditioning=cond))
check("CopyBridge 直通分支（stage 0 无来源）返回 4 路", ok, "实得 %d" % len(out))
check("直通分支 trim_frames 输出 = 0（首段不裁）", int(out[2]) == 0, "得到 %r" % (out[2],))

img73 = torch.zeros(73, 2, 2, 3)
ok, out = arity(NODES.H3RelayTrimAV, dict(images=img73, trim_frames=0, fps=24.0, audio=None))
check("TrimAV trim=0 分支返回 3 路", ok, "实得 %d" % len(out))
ok, out = arity(NODES.H3RelayTrimAV, dict(images=img73, trim_frames=22, fps=24.0, audio=aud))
check("TrimAV 裁剪分支返回 3 路", ok, "实得 %d" % len(out))
check("裁剪分支画面 73 → 51 帧", int(out[0].shape[0]) == 51, "得到 %d" % int(out[0].shape[0]))
check("裁剪分支音频与画面同裁（68000 点）",
      int(out[1]["waveform"].shape[-1]) == 68000, "得到 %d" % int(out[1]["waveform"].shape[-1]))

ok, out = arity(NODES.H3RelayLatentSave,
                dict(latent=prev, run_id="unittest_arity", stage_index=0, note="arity test"))
check("LatentSave 返回 2 路", ok, "实得 %d" % len(out))
ok, out = arity(NODES.H3RelayLatentLoad,
                dict(run_id="unittest_arity", stage_index=1, explicit_path=""))
check("LatentLoad 返回 2 路（stage_index=本段号，读的是上一段）", ok, "实得 %d" % len(out))
check("LatentLoad 读回上一段 latent（192 帧 @768x448）",
      CORE.pixel_frames(int(out[0]["samples"].tensors[0].shape[2])) == 192)

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
except Exception as e:  # noqa: BLE001
    check("9.3 stage_index=0 仍直通不报错（独立段合法）", False, "抛了 %s" % type(e).__name__)

# 9.4 手动接了 context_latent 时，段号≥1 不应被拦（高级用法）
try:
    out1 = ctx.bridge(cur, prev, context_frames=22, run_id="", stage_index=1)
    check("9.4 手动接 context_latent → 段号≥1 不拦", int(out1[2]) == 22, "trim=%r" % (out1[2],))
except Exception as e:  # noqa: BLE001
    check("9.4 手动接 context_latent → 段号≥1 不拦", False, "抛了 %s：%s" % (type(e).__name__, e))

# 9.5 段号≥1 无 context（run_id 有值但没接 LatentLoad 来源）→ 仍 raise，不静默直通
try:
    ctx.bridge(cur, None, context_frames=22, run_id="unittest_no_such_run", stage_index=1)
    check("9.5 段号≥1 无来源（即便 run_id 有值）→ 仍 raise", False, "竟然没报错")
except RuntimeError as e:
    check("9.5 段号≥1 无来源（即便 run_id 有值）→ 仍 raise", "静默直通" in str(e), str(e).split("\n")[0])
except Exception as e:  # noqa: BLE001
    check("9.5 run_id 有值但无文件 → FileNotFoundError", False, "抛的是 %s" % type(e).__name__)

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
except Exception as e:  # noqa: BLE001
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

# 11.3 前端会把 status 一起传进来 → noop 必须能吃下
try:
    chain_cls().noop(segments=3, status="第 2 段")
    check("11.3 noop 能吃下 status 参数（不会 TypeError）", True)
except Exception as e:  # noqa: BLE001
    check("11.3 noop 能吃下 status 参数（不会 TypeError）", False, repr(e))

# 11.4 前端 JS 确实在找这个 widget 名
_js = os.path.join(_KIT_DIR, "web", "relay_kit_chain.js")
try:
    _src = open(_js, encoding="utf-8").read()
    check("11.4 前端 JS 找的 widget 名与后端一致（status）",
          'x.name === "status"' in _src, "未在 relay_kit_chain.js 里找到该查找")
except Exception as e:  # noqa: BLE001
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
    chain_cls().noop(segments=3, status="第 2 段", prompts="a\n---\nb", prompt_target="6.h3_data",
                     auto_concat=True, concat_name="film")
    check("11.7 noop 能吃下 0.6.7 的四个新参数（不会 TypeError）", True)
except Exception as e:  # noqa: BLE001
    check("11.7 noop 能吃下 0.6.7 的四个新参数（不会 TypeError）", False, repr(e))
try:
    _js2 = open(os.path.join(_KIT_DIR, "web", "relay_kit_prompt.js"), encoding="utf-8").read()
    check("11.8 词分发纯函数模块在位（前端与离线单测共用同一份逻辑）",
          "export function splitPromptBlocks" in _js2 and "export function writePrompt" in _js2)
except Exception as e:  # noqa: BLE001
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
    chain_cls().noop(segments=3, audio_out="pcm_lossless", video_crf=23)
    check("11.12 noop 能吃下音轨/画质两个新参数（**kwargs 兜住）", True)
except Exception as e:  # noqa: BLE001
    check("11.12 noop 能吃下音轨/画质两个新参数（**kwargs 兜住）", False, repr(e))

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
check("12.3 settle 不影响音频窗（仍 37 步）",
      pl3.audio_ref["ref_audio_t"] == pl0.audio_ref["ref_audio_t"] == 37)
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
ok, out = arity(NODES.H3RelayTrimAV, dict(images=seg73, trim_frames=22, fps=24.0,
                                          audio=aud73, settle_frames=-1, seam_ghost=0))
check("12.14 settle_frames=-1（显式走自动）→ 自动裁 23 帧（73 → 50）",
      int(out[0].shape[0]) == 50, "得到 %d 帧" % int(out[0].shape[0]))
# 🔴 2026-09-16 新默认：**不裁沉降**。依据：裁沉降才是缝处跳帧的源头
#   （裁 0 帧跳 0.020 几乎无感 / 裁 8 帧 0.044 / 裁 16 帧 0.055）——见 relay_core 注释。
ok, out_d0 = arity(NODES.H3RelayTrimAV, dict(images=seg73, trim_frames=22, fps=24.0,
                                            audio=aud73, seam_ghost=0))
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
ok, out0 = arity(NODES.H3RelayTrimAV, dict(images=seg73, trim_frames=0, fps=24.0, audio=None))
check("12.19 trim_frames=0（首段/独立段）→ 原样返回，绝不触发自动检测",
      out0[0] is seg73 and int(out0[0].shape[0]) == 73)
ok, outx = arity(NODES.H3RelayTrimAV, dict(images=seg73, trim_frames=22, fps=24.0,
                                          audio=None, settle_frames=0))
check("12.20 settle_frames=0 → 回到 0.2.x 旧行为（73 → 51 帧）",
      int(outx[0].shape[0]) == 51, "得到 %d" % int(outx[0].shape[0]))
ok, outm = arity(NODES.H3RelayTrimAV, dict(images=seg73, trim_frames=22, fps=24.0,
                                          audio=None, settle_frames=9))
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

# —— 缝帧重影（极短交叉溶，2026-09-15 GG 认可的解法）——
ok, outg = arity(NODES.H3RelayTrimAV, dict(images=seg73, trim_frames=22, fps=24.0,
                                           audio=aud73, settle_frames=-1, seam_ghost=1, seam_ghost_alpha=0.5))
check("12.23 缝帧重影：**帧数守恒**（与关重影同长 → 音频不必动、无 A/V 漂移）",
      int(outg[0].shape[0]) == int(out[0].shape[0]) == 50, "得到 %d" % int(outg[0].shape[0]))
check("12.24 缝帧重影：首帧 = 上段末帧 ⊕ 裁后首帧（对半），其余帧逐位不动",
      torch.allclose(outg[0][0], 0.5 * seg73[21] + 0.5 * seg73[23], atol=1e-6)
      and torch.equal(outg[0][1], seg73[24]),
      "首帧最大差 %.2e" % float((outg[0][0] - (0.5 * seg73[21] + 0.5 * seg73[23])).abs().max()))

# —— 12.25 默认值钉子（2026-09-15 深夜定案）——
# 动机：`seam_ghost` 曾默认开 1 帧。实测它只把「一跳」拆成「两跳」——
#   总位移守恒（超基线总量 12.61 → 11.68，仅 −7%）、**异常帧数 1 → 2**，
#   观感从「跳一下」变成「卡两下」，且混合帧本身是可见鬼影（GG 目检定案）。
#   ⇒ 默认值是**实测结论**而非偏好，钉住防回归。
_tg = NODES.H3RelayTrimAV.INPUT_TYPES()["optional"]["seam_ghost"][1]
check("12.25 缝帧重影默认 **关**（实测一跳拆两跳更差；钉住防回归）",
      int(_tg.get("default", -1)) == 0, "default=%r" % _tg.get("default"))
check("12.26 缝帧重影关闭时逐位不动（默认档 = 0.4.3 行为）",
      torch.equal(out[0][0], seg73[23]), "首帧最大差 %.2e"
      % float((out[0][0] - seg73[23]).abs().max()))

# ============ 组13：模糊型沉降（v0.3.1）——帧差法盲区的高频能量补判 ============

# —— 极短交叉溶（2026-09-16 重做：两侧都是**连续运动序列**）——
# 旧实现把「上段末帧」复制 k 次去混合 → k>1 时内容冻结（重复帧）；本组专测"不冻结"。
_cf = torch.zeros(40, 4, 4, 3)
for _i in range(40):
    _cf[_i] = _i / 40.0          # 每帧不同 → 序列本身有"运动"
ok, _c0 = arity(NODES.H3RelayTrimAV, dict(images=_cf, trim_frames=22, fps=24.0, audio=None,
                                          settle_frames=0, seam_ghost=0))
ok, _c3 = arity(NODES.H3RelayTrimAV, dict(images=_cf, trim_frames=22, fps=24.0, audio=None,
                                          settle_frames=0, seam_ghost=3))
check("12.30 交叉溶 k=3：帧数守恒（40→18）+ 权重按 1/4 递进（首帧 = 3/4·A₃ + 1/4·B₁）",
      int(_c3[0].shape[0]) == 18
      and torch.allclose(_c3[0][0], 0.75 * _cf[21] + 0.25 * _cf[22], atol=1e-6)
      and torch.allclose(_c3[0][2], 0.25 * _cf[19] + 0.75 * _cf[24], atol=1e-6),
      "首帧 %.4f 期望 %.4f" % (float(_c3[0][0][0, 0, 0]), float((0.75 * _cf[21] + 0.25 * _cf[22])[0, 0, 0])))
check("12.31 交叉溶 k=3：**不冻结**（前三帧互不相同 —— 旧实现会把上段末帧复制 3 次）",
      not torch.allclose(_c3[0][0], _c3[0][1]) and not torch.allclose(_c3[0][1], _c3[0][2]))
check("12.32 交叉溶 k=3：作用区外（第 4 帧起）逐位不动",
      torch.equal(_c3[0][3:], _cf[25:]))
check("12.33 交叉溶 k=1：退化为对半单帧（与旧 seam_ghost 行为一致，向后兼容）",
      torch.allclose(_c3[0][0], _c3[0][0]) and torch.equal(_c0[0], _cf[22:]))

# —— 后处理层补全（P3/P5/P6/P7，2026-09-16）——
# 合成素材：段头「糊 + 偏暖 + 偏暗」，段体「锐 + 中性 + 较亮」。
_pg = torch.Generator().manual_seed(23)
_ph, _pw = 48, 48
_pbase = (0.40 + 0.18 * torch.sin(6.283 * torch.arange(_pw).float().view(1, -1) / _pw)).expand(_ph, _pw)
_pbody = torch.stack([_pbase * 1.05, _pbase * 1.00, _pbase * 0.95], -1) + 0.06 * torch.randn(_ph, _pw, 3, generator=_pg)
_phead = torch.stack([_pbase * 1.15, _pbase * 0.95, _pbase * 0.80], -1)
_ppost = torch.cat([_phead.unsqueeze(0).expand(12, -1, -1, -1), _pbody.unsqueeze(0).expand(52, -1, -1, -1)], 0)


def _post_common(fn, name, kw):
    _o0 = fn(_ppost, 12, 0.0, **kw)
    _o1 = fn(_ppost, 12, 1.0, **kw)
    check("12.4x %s：帧数守恒 + 参数 0 逐位不动" % name,
          int(_o1.shape[0]) == 64 and torch.equal(_o0, _ppost))
    check("12.4x %s：作用区外（段体）逐位不动" % name,
          torch.equal(_o1[40:], _ppost[40:]))
    return _o0, _o1


# 12.40 频域高频增强：**必须先有高频可增强** ⇒ 用例改为「对锐图先做高斯模糊」再造段头。
#   （原用例的段头是纯低频正弦，无高频可增强 → 测不出效果，属用例缺陷而非实现缺陷）
_dblur = CORE._box_blur_hwc(_pbody.unsqueeze(0), 9)[0]        # 锐图 → 糊图
_pblur = torch.cat([_dblur.unsqueeze(0).expand(12, -1, -1, -1), _pbody.unsqueeze(0).expand(52, -1, -1, -1)], 0)
_d0 = CORE.deconv_head_zone(_pblur, 12, 0.0)
_d1 = CORE.deconv_head_zone(_pblur, 12, 1.0)
check("12.40 频域高频增强：帧数守恒 + 参数 0 逐位不动",
      int(_d1.shape[0]) == 64 and torch.equal(_d0, _pblur))
check("12.40 频域高频增强：作用区外逐位不动", torch.equal(_d1[40:], _pblur[40:]))
check("12.40 频域高频增强：糊段头的锐度被抬起来（>1.2× 原值）",
      float(CORE._sharpness(_d1[:12]).median()) > 1.2 * float(CORE._sharpness(_pblur[:12]).median()),
      "%.6f → %.6f" % (float(CORE._sharpness(_pblur[:12]).median()), float(CORE._sharpness(_d1[:12]).median())))

# 12.41 段体高频迁移：段头锐度上升
_b0, _b1 = _post_common(CORE.borrow_detail_from_body, "段体高频迁移", dict(blur=9, body_start=40))
check("12.41 段体高频迁移：段头锐度上升",
      float(CORE._sharpness(_b1[:12]).median()) > 1.5 * float(CORE._sharpness(_ppost[:12]).median()),
      "%.6f → %.6f" % (float(CORE._sharpness(_ppost[:12]).median()), float(CORE._sharpness(_b1[:12]).median())))

# 12.42 直方图匹配：段头均值向段体靠拢
_h0, _h1 = _post_common(CORE.match_hist_head_to_body, "直方图匹配", dict(body_start=40))
_gap_before = abs(float(_ppost[:12].mean()) - float(_ppost[40:].mean()))
_gap_after = abs(float(_h1[:12].mean()) - float(_ppost[40:].mean()))
check("12.42 直方图匹配：段头均值向段体靠拢",
      _gap_after < _gap_before, "gap %.4f → %.4f" % (_gap_before, _gap_after))

# 12.43 白平衡校正：段头通道比例向段体靠拢
_w0, _w1 = _post_common(CORE.match_white_balance, "白平衡校正", dict(body_start=40))


def _ratio(t):
    m = t.mean(dim=(0, 1, 2))
    return m / m.mean()


_r_body = _ratio(_ppost[40:])
_e_before = float((_ratio(_ppost[:12]) - _r_body).abs().max())
_e_after = float((_ratio(_w1[:12]) - _r_body).abs().max())
check("12.43 白平衡校正：段头 R:G:B 比例向段体靠拢",
      _e_after < _e_before, "偏差 %.4f → %.4f" % (_e_before, _e_after))

# —— 低频残差传递（2026-09-16，借鉴 Director 的段间引导低频对齐）——
# 合成"两段不同亮度"的序列：上段暗、下段亮（或反之），且**各带高频噪声**。
# 要钉住的核心性质：**缝点色档被拉近，但高频（锐度）不掉** ——
# 这正是它与「全 RGB 混合」的分水岭（后者实测把作用区锐度砍掉 49%）。
_g = torch.Generator().manual_seed(11)
_lf_pre = 0.80 + 0.04 * torch.rand(22, 8, 8, 3, generator=_g)
_lf_post = 0.20 + 0.04 * torch.rand(40, 8, 8, 3, generator=_g)
_lf = torch.cat([_lf_pre, _lf_post], dim=0)
ok, _l0 = arity(NODES.H3RelayTrimAV, dict(images=_lf, trim_frames=22, fps=24.0, audio=None,
                                          settle_frames=0, lowfreq_pull=0.0))
ok, _l1 = arity(NODES.H3RelayTrimAV, dict(images=_lf, trim_frames=22, fps=24.0, audio=None,
                                          settle_frames=0, lowfreq_pull=1.0,
                                          lowfreq_frames=12, lowfreq_blur=9))
_gap0 = abs(float(_l0[0][0].mean().item()) - float(_lf[21].mean().item()))
_gap1 = abs(float(_l1[0][0].mean().item()) - float(_lf[21].mean().item()))
check("12.34 低频残差：帧数守恒（62→40）+ w=0 时逐位不动（默认关 = 旧行为）",
      int(_l1[0].shape[0]) == 40 and torch.equal(_l0[0], _lf[22:]))
check("12.35 低频残差：缝点色档被拉向上段末帧（|Δ| 显著下降）",
      _gap1 < _gap0 * 0.5, "gap %.4f → %.4f" % (_gap0, _gap1))
check("12.36 低频残差：**高频（锐度）基本不掉** —— 区别于全 RGB 混合（后者砍 49%）",
      float(CORE._sharpness(_l1[0][:12]).median().item())
      > 0.85 * float(CORE._sharpness(_l0[0][:12]).median().item()),
      "锐度 %.6f → %.6f" % (float(CORE._sharpness(_l0[0][:12]).median().item()),
                            float(CORE._sharpness(_l1[0][:12]).median().item())))

# —— 糊区锐化（画质域修复，2026-09-16 GG 定方向）——
# 动机：settle_frames 默认改 0（不裁沉降）后，成片段头保留几帧「重绘糊」；
#   裁它 → 跳帧（裁 16 帧跳 0.055）；不裁 → 留糊。
#   **第三条路 = 画质域修**：不裁、不动时间轴，只提升糊区高频 → 从原理上不可能引入跳帧。
# ⚠ 必须用 trim_frames=22 触发裁切分支：trim_frames=0 是「首段/独立段」，走早返回、不裁也不锐化。
_gimg = torch.rand(60, 8, 8, 3, generator=torch.Generator().manual_seed(3))
ok, _o0 = arity(NODES.H3RelayTrimAV, dict(images=_gimg, trim_frames=22, fps=24.0, audio=None,
                                          settle_frames=0, settle_sharpen=0.0))
ok, _o8 = arity(NODES.H3RelayTrimAV, dict(images=_gimg, trim_frames=22, fps=24.0, audio=None,
                                          settle_frames=0, settle_sharpen=0.8,
                                          settle_sharpen_frames=12))
check("12.27 糊区锐化：帧数守恒（60→38，只裁钉住区）+ amount=0 时逐位不动",
      int(_o0[0].shape[0]) == 38 and int(_o8[0].shape[0]) == 38
      and torch.equal(_o0[0], _gimg[22:]))
check("12.28 糊区锐化：开头帧高频上升、作用范围外（输出第 20 帧）逐位不动",
      float(CORE._sharpness(_o8[0][:1]).item()) > float(CORE._sharpness(_gimg[22:23]).item())
      and torch.allclose(_o8[0][20], _gimg[42], atol=1e-6),
      "锐度 %.6f → %.6f" % (float(CORE._sharpness(_gimg[22:23]).item()),
                            float(CORE._sharpness(_o8[0][:1]).item())))
check("12.29 糊区锐化：衰减正确（输出第 11 帧 = 作用区末帧，权重 0 → 与原帧重合）",
      torch.allclose(_o8[0][11], _gimg[33], atol=1e-6))

def blur_seg(n=40, pin=22, blur=(22, 28), amp=20.0, dev=15.0, seed=7):
    """模糊型沉降合成。钉住区 = 纹理A（复现，帧差中等）；[blur0,blur1) = 重绘发虚
    （常数帧：锐度≈0、帧差≈0）；其后 = 纹理B（新内容，均值同、振幅略小）。
    边界帧差刻意 < 4×基线 → 帧差法必然盲，只有锐度法能救。"""
    g = torch.Generator().manual_seed(seed)
    texA = 100.0 + torch.rand(8, 8, 3, generator=g) * amp
    texB = 100.0 + amp / 2 + (torch.rand(8, 8, 3, generator=g) - 0.5) * 2 * dev
    base_mean = float(texA.mean())
    im = torch.zeros(n, 8, 8, 3)
    for i in range(n):
        if i < pin:
            im[i] = texA + (i % 3) * 3.0
        elif blur[0] <= i < blur[1]:
            im[i] = base_mean
        else:
            im[i] = texB + (i % 3) * 3.0
    return im


sE, jE, bE = CORE.detect_settle(blur_seg(), 22)
check("13.1 模糊沉降（塌陷 f24-29、恢复 f30）→ 锐度法量出 settle≈8-9",
      6 <= sE <= CORE.MAX_SETTLE, "settle=%d dip=%.1f ref=%.1f" % (sE, jE, bE))
check("13.2 模糊路径的返回值语义：val=塌陷谷底（<0.35×基准）、base=复现区锐度基准",
      jE < CORE.BLUR_COLLAPSE_RATIO * bE and bE > 0.0,
      "dip=%.1f ref=%.1f" % (jE, bE))
sF, _, _ = CORE.detect_settle(blur_seg(blur=(22, 40)), 22)   # 糊过窗：22+18 > 22+12
check("13.3 塌陷越过检测窗且窗内无恢复 → 0（宁少勿多）", sF == 0, "settle=%d" % sF)
sG, _, _ = CORE.detect_settle(blur_seg(blur=(22, 22)), 22)   # 无模糊区
check("13.4 全程带纹理、无塌陷 → 0（不误伤）", sG == 0, "settle=%d" % sG)
sH, _, _ = CORE.detect_settle(blur_seg(blur=(22, 34)), 22)   # 糊 22..33（12 帧贴满窗）、34 起新内容
check("13.5 长塌陷(12帧)窗内可见恢复 → settle=12（贴满上限）",
      sH == 12, "settle=%d" % sH)
sI, _, _ = CORE.detect_settle(blur_seg(seed=11), 22)         # 换 seed 回归
check("13.6 换随机种子结论稳定（6 ≤ settle ≤ 12）",
      6 <= sI <= CORE.MAX_SETTLE, "settle=%d" % sI)

# —— v0.3.2 双基准：复现区自身发软时，窗外新内容区才是真基准 ——

def blur_seg_mild(n=40, pin=22, blur=(22, 26), seed=9):
    """HD-mild 动机案例：复现区本身半软（振幅 20），塌陷区 0.3-0.5×源
    （对软复现基准不可见），新内容全锐（振幅 60）。
    0.3.1 单基准（复现区）→ dip>0.35×ref 返回 0；0.3.2 双基准 → 量出 settle。"""
    g = torch.Generator().manual_seed(seed)
    texA = 100.0 + torch.rand(8, 8, 3, generator=g) * 20.0
    texB = 110.0 + (torch.rand(8, 8, 3, generator=g) - 0.5) * 60.0
    meanA = float(texA.mean())
    im = torch.zeros(n, 8, 8, 3)
    for i in range(n):
        if i < pin:
            im[i] = texA + (i % 2) * 6.0
        elif blur[0] <= i < blur[1]:
            im[i] = 0.5 * texA + 0.5 * meanA + (i % 3) * 1.5
        else:
            im[i] = texB + (i % 3) * 3.0
    return im


sM, jM, bM = CORE.detect_settle(blur_seg_mild(), 22)
check("13.7 HD-mild：对软复现基准不可见 → 双基准量出 settle=4",
      sM == 4, "settle=%d dip=%.1f ref=%.1f" % (sM, jM, bM))
_imM = blur_seg_mild()
_shM = CORE._sharpness(_imM[:36])
_faM = CORE._sharpness(_imM[36:76])
_expM = max(float(_shM[:22].median()), float(_faM.median()))
check("13.8 双基准语义：ref = max(复现区锐度, 窗外新内容区锐度)",
      abs(bM - _expM) < 1e-4, "ref=%.2f expect=%.2f" % (bM, _expM))
# —— v0.3.2 量纲不变性：0-1 产线数据与 0-255 测试数据必须同一结论 ——

check("13.9 量纲不变（帧差路）：0-255 的 settle=1 在 0-1 数据上同为 1（BASELINE_FLOOR 量纲 bug 回归）",
      CORE.detect_settle(seam_seg(73, 23) / 255.0, 22)[0] == 1,
      "settle=%d" % CORE.detect_settle(seam_seg(73, 23) / 255.0, 22)[0])
check("13.10 量纲不变（锐度路）：0-1 的 HD-mild 同样量出 settle=4",
      CORE.detect_settle(blur_seg_mild() / 255.0, 22)[0] == 4,
      "settle=%d" % CORE.detect_settle(blur_seg_mild() / 255.0, 22)[0])

# —— v0.3.3 段体自参考 + 假跳否决 ——

def soft_uniform_seg(n=40, pin=22, seed=5):
    """全段一致偏软（4 步低清风格）：头与体都是弱纹理，整体量级低但无塌陷差。"""
    g = torch.Generator().manual_seed(seed)
    im = torch.zeros(n, 8, 8, 3)
    for i in range(n):
        base = 100.0 + torch.rand(8, 8, 3, generator=g) * (6.0 if i < pin else 9.0)
        im[i] = base + (i % 3) * 1.0
    return im


sS, _, _ = CORE.detect_settle(soft_uniform_seg(), 22)
check("13.11 全段一致偏软（整体量级低、头体无差）→ 0（量纲无关的决策）",
      sS == 0, "settle=%d" % sS)

def ramp_blur_seg(n=40, pin=22, seed=3):
    """渐进模糊：f22-25 纹理振幅按 t=0.4/0.55/0.7/0.85 递减到深塌陷，f26 起新内容。"""
    g = torch.Generator().manual_seed(seed)
    texA = 100.0 + torch.rand(8, 8, 3, generator=g) * 20.0
    texB = 110.0 + (torch.rand(8, 8, 3, generator=g) - 0.5) * 30.0
    meanA = float(texA.mean())
    im = torch.zeros(n, 8, 8, 3)
    ts = [0.4, 0.55, 0.7, 0.85]
    for i in range(n):
        if i < pin:
            im[i] = texA + (i % 3) * 3.0
        elif i < pin + 4:
            tt = ts[i - pin]
            im[i] = (1 - tt) * texA + tt * meanA
        else:
            im[i] = texB + (i % 3) * 3.0
    return im


sR, vR, _ = CORE.detect_settle(ramp_blur_seg(), 22)
check("13.12 渐进模糊（缓坡到底才过深线）→ settle=4（覆盖整个塌陷前缀）",
      sR == 4, "settle=%d" % sR)

def fake_jump_seg(n=40, pin=22, seed=4):
    """假跳：f22 既是深塌陷又是一次大跳（亮常帧），f23 起新内容——
    跳点必须被验身否决，由锐度路定界（返回值 = 塌陷谷底而非跳变帧差）。"""
    g = torch.Generator().manual_seed(seed)
    texA = 100.0 + torch.rand(8, 8, 3, generator=g) * 20.0
    texB = 110.0 + (torch.rand(8, 8, 3, generator=g) - 0.5) * 30.0
    im = torch.zeros(n, 8, 8, 3)
    for i in range(n):
        if i < pin:
            im[i] = texA + (i % 3) * 3.0
        elif i == pin:
            im[i] = 250.0
        else:
            im[i] = texB + (i % 3) * 3.0
    return im


sF, vF, bF = CORE.detect_settle(fake_jump_seg(), 22)
check("13.13 假跳被验身否决 → 锐度路定界 settle=1，信号=塌陷谷底（非跳变帧差）",
      sF == 1 and vF < 10.0 and bF > 100.0,
      "settle=%d val=%.1f ref=%.1f" % (sF, vF, bF))

# —— 锐度路三量解耦（2026-09-15）：塌陷宽于旧窗但**有恢复** → 必须裁 ——
# 动机：cond 桥「重绘尾段」的复现发糊实测 11–17 帧宽 > 旧 MAX_SETTLE=12。旧实现让
# MAX_SETTLE 同时兼任扫描窗宽，糊区填满窗口时 after 为空 →「窗内必须见恢复」永假 →
# settle 恒 0（实测 onerA_cond4 的 s2/s3 settle=0，段头/段体锐度比 0.71 / 0.41，糊原样留在成片）。
# 解耦后扫描窗独立（SETTLE_SCAN）、参考偏移固定（SETTLE_REF_OFF）→ 恢复得以进入窗内 → 应裁。
sW, _, _ = CORE.detect_settle(blur_seg(n=60, blur=(22, 40)), 22)   # 糊 22..39（18 帧）+ 40 起恢复
check("13.14 塌陷宽于旧窗但有恢复 → 裁，且可超过旧 MAX_SETTLE（解耦生效）",
      sW > CORE.MAX_SETTLE, "settle=%d MAX_SETTLE=%d" % (sW, CORE.MAX_SETTLE))
sX, _, _ = CORE.detect_settle(blur_seg(n=40, blur=(22, 40)), 22)   # 同宽糊，但跑到段尾、无恢复
check("13.15 同一塌陷宽度但段尾截断（无恢复）→ 仍 0（13.3「宁少勿多」契约不破）",
      sX == 0, "settle=%d" % sX)

# ============ 组14：拷贝桥（0.4.0）——latent 硬拷贝 + 噪声掩码 ============

def av_latent(t_steps, a_ticks=48, seed=0, w=8, h=8):
    g = torch.Generator().manual_seed(seed)
    video = torch.rand(1, 4, t_steps, h, w, generator=g)
    audio = torch.rand(1, 2, 2, a_ticks, generator=g)
    return {"samples": CORE._nested_pair(video, audio)}


tgt = av_latent(12, seed=1)
prv = av_latent(12, seed=2)
outC, trimC, repC = CORE.build_continue_latent(tgt, prv, 22)
sv = CORE.video_from_latent(outC)
pv = CORE.video_from_latent(prv)
tv = CORE.video_from_latent(tgt)
check("14.1 拷贝位级一致：前缀 7 步 == 上段尾部 7 步（start=5 对齐）",
      torch.equal(sv[:, :, :7], pv[:, :, 5:12]))
check("14.2 前缀之后保留本段原 latent", torch.equal(sv[:, :, 7:], tv[:, :, 7:]))
check("14.3 输入 target 未被变异", torch.equal(tv, CORE.video_from_latent(tgt)))
m = outC["noise_mask"]
check("14.4 hard 掩码：形状 [1,1,12,8,8]，前 7 步=0 其余=1，有限",
      tuple(m.shape) == (1, 1, 12, 8, 8) and bool((m[:, :, :7] == 0).all())
      and bool((m[:, :, 7:] == 1).all()) and bool(torch.isfinite(m).all()))
check("14.5 trim 输出 = covered = 22（接 TrimAV）", trimC == 22 and "22" in repC)
oa = CORE.audio_from_latent(outC)
pa = CORE.audio_from_latent(prv)
ta = CORE.audio_from_latent(tgt)
rt = int(oa.shape[-1] - round(22 / 24.0 * 40.0) + 0)  # 期望 37
check("14.6 音频尾拷贝：前 37 tick == 上段尾部，其后保留本段",
      torch.equal(oa[..., :37], pa[..., 11:48]) and torch.equal(oa[..., 37:], ta[..., 37:]))
outT, _, trimT = CORE.build_continue_latent(av_latent(12, seed=3), av_latent(12, seed=4), 22,
                                            mask_mode="taper")
wexp = CORE.prefix_taper_weights(7, 4, 0.10)
mT = outT["noise_mask"]
check("14.7 taper 掩码：头 1.0 线性降到缝端 0.10",
      torch.allclose(mT[:, :, :7, 0, 0], torch.tensor(wexp, dtype=torch.float32), atol=1e-6)
      and abs(float(mT[:, :, 6, 0, 0]) - 0.10) < 1e-6, "weights=%s" % (wexp,))
expect_raise("14.8 跨分辨率 → raise（拷贝桥禁止）",
             lambda: CORE.build_continue_latent(tgt, av_latent(12, seed=5, w=8, h=16), 22), "跨分辨率")
big_prev = av_latent(22, seed=6)
expect_raise("14.9 前缀占满整段（73f→22 步 ≥ 目标 12 步）→ raise",
             lambda: CORE.build_continue_latent(tgt, big_prev, 73), "没有新内容")
mis_prev = av_latent(13, seed=7)
expect_raise("14.10 尾段起点非 5 对齐 → raise（接缝位移防线复用）",
             lambda: CORE.build_continue_latent(tgt, mis_prev, 22), "起始于周期位置")
expect_raise("14.11 mask_mode 非法 → raise",
             lambda: CORE.build_continue_latent(tgt, prv, 22, mask_mode="soft"), "mask_mode")
okC, outN = arity(NODES.H3RelayCopyBridge, dict(latent=av_latent(12, seed=8),
                                                context_latent=av_latent(12, seed=9),
                                                context_frames=22))
check("14.12 节点返回 3 路（latent/report/trim）", okC, "实得 %d" % len(outN))
check("14.13 节点 trim 输出 = 22", int(outN[2]) == 22)
_it = NODES.H3RelayCopyBridge.INPUT_TYPES()
check("14.14 节点注册 + required 键序 + optional 末位（追加铁律）",
      "H3RelayCopyBridge" in NODES.NODE_CLASS_MAPPINGS
      and list(_it["required"]) == ["latent", "context_latent", "context_frames"]
      and list(_it["optional"])[:4] == ["mask_mode", "taper_tokens", "seam_min", "pin_audio"]
      and list(_it["optional"])[4:6] == ["ramp_top", "ramp_tokens"],
      "required=%s optional=%s" % (list(_it["required"]), list(_it["optional"])))

# —— 14.15/14.16 掩码语义钉子（2026-09-15）——
# 动机：taper 曾被下游误当「软一点的硬锁」当默认跑了 23 次，段首被重画导致「续不上」。
# 这里把语义本身断言下来：hard 全 0 = 真钉住；taper 每一帧 m>0 = **没有被钉住**。
# 谁要改 taper 的方向，先过这两条，再回头改 nodes/README/CHANGES 的措辞。
check("14.15 hard 掩码 = 钉住区全 0（真钉住：d*0 + anchor*1）",
      bool((outC["noise_mask"][:, :, :7] == 0).all()),
      "min=%s max=%s" % (float(m[:, :, :7].min()), float(m[:, :, :7].max())))
check("14.16 taper 掩码 = 钉住区**无一处为 0**（头 1.0 全重绘，缝端仍留 seam_min）",
      bool((mT[:, :, :7] > 0).all()) and float(mT[:, :, 0, 0, 0]) == 1.0
      and float(mT[:, :, 6, 0, 0]) > 0.0,
      "头=%.2f 缝端=%.2f 最小=%.4f（taper 不钉住）"
      % (float(mT[:, :, 0, 0, 0]), float(mT[:, :, 6, 0, 0]), float(mT[:, :, :7].min())))

# ============ 组15：色档收敛信号（v0.4.1）——注噪/taper 收敛尾巴的观测端 ============

def grade_seg(n=40, pin=22, dark=(22, 25), factor=0.88, seed=13):
    """色档收敛合成：f22-24 整体压暗 12%（收敛中），f25 起回归体档。纹理均匀无模糊。"""
    g = torch.Generator().manual_seed(seed)
    texA = 100.0 + torch.rand(8, 8, 3, generator=g) * 20.0
    im = torch.zeros(n, 8, 8, 3)
    for i in range(n):
        f = factor if dark[0] <= i < dark[1] else 1.0
        im[i] = texA * f + (i % 3) * 3.0
    return im


sG, vG, bG = CORE.detect_settle(grade_seg(), 22)
check("15.1 色档收敛（暗 3 帧后回归体档）→ 锐度路不触发、色档路 settle=3",
      sG == 3, "settle=%d val=%.1f thr=%.1f" % (sG, vG, bG))

def wobble_seg(n=40, pin=22, seed=17):
    """自然亮度波动：体区各帧亮度随机 ±5%，头体同分布 → 不误裁。"""
    g = torch.Generator().manual_seed(seed)
    texA = 100.0 + torch.rand(8, 8, 3, generator=g) * 20.0
    im = torch.zeros(n, 8, 8, 3)
    for i in range(n):
        w = 1.0 + (torch.rand(1, generator=g).item() - 0.5) * 0.10
        im[i] = texA * w + (i % 3) * 3.0
    return im


sW, _, _ = CORE.detect_settle(wobble_seg(), 22)
check("15.2 自然亮度波动（头体同分布）→ 0（阈值随体 MAD 自适应）",
      sW == 0, "settle=%d" % sW)

sM2, _, _ = CORE.detect_settle(blur_seg_mild() * 0.85, 22)  # 模糊+整体压暗：锐度路与色档路同响
check("15.3 三信号取最大：mild 模糊(4) 与压暗色档(≥4) → settle ≥ 4",
      sM2 >= 4, "settle=%d" % sM2)
check("15.4 量纲不变：0-1 输入同结论",
      CORE.detect_settle(grade_seg() / 255.0, 22)[0] == 3,
      "settle=%d" % CORE.detect_settle(grade_seg() / 255.0, 22)[0])

# ============ 组16：0.4.2 回归（导出音频分支 / 中文 note / 服务端校验） ============

# 16.1 导出音频尾段分支（0.4.1 前返回 3 元组 → 解包必崩，此前从未被测）
exp_prev = dict(prev)
_pa2 = CORE.audio_from_latent(prev)     # 别用组 2 的 pa——组 14 已把同名变量覆盖成小 latent
exp_tail_v, _eo, _ec = CORE.video_tail_from_latent(prev, 22)
exp_prev[CORE.KEY_EXPORT_TAIL_VIDEO] = torch.cat(exp_tail_v, dim=2)
exp_prev[CORE.KEY_EXPORT_FRAMES] = 22
exp_prev[CORE.KEY_EXPORT_TAIL_AUDIO] = _pa2[0, :, :, -37:]   # 3 维，顺带测 unsqueeze
try:
    t_x, rt_x, oh_x, raw_x, go_x = CORE.audio_tail_from_latent(exp_prev, 22, 192)
    check("16.1 导出音频分支返回 5 元组且步数=尾段长", rt_x == 37 and go_x is False,
          "rt=%d" % rt_x)
except (ValueError, TypeError) as e:
    check("16.1 导出音频分支返回 5 元组且步数=尾段长", False, "→ %s" % e)
plan_x = CORE.plan_relay(cur, exp_prev, 22)
check("16.2 plan_relay 走导出尾段快路径不崩且续接成功",
      plan_x.applied and plan_x.audio_ref["ref_audio_t"] == 37)
cb_t, cb_trim, cb_rep = CORE.build_continue_latent(cur, exp_prev, 22)
check("16.3 拷贝桥走导出音频分支不崩（trim=22）", cb_trim == 22)

# 16.4 中文 note 落盘往返（0.4.1 前 ord(c)>255 直接崩）
tmp_c = os.path.join(tempfile.gettempdir(), "relay_kit_test", "stage_cjk.safetensors")
try:
    CORE.save_av_latent(prev, tmp_c, note="22帧窗 v2 备注——中文、emoji🎬")
    back_c = CORE.load_av_latent(tmp_c)
    # 比较基准现取：组 14 已把组 2 的局部名 pv/pa 覆盖成拷贝桥的小 latent
    check("16.4 中文 note 落盘往返不崩且流仍逐位相同",
          torch.equal(CORE.streams_from_latent(back_c)[0],
                      CORE.video_from_latent(prev)))
except Exception as e:
    check("16.4 中文 note 落盘往返不崩且流仍逐位相同", False, "→ %s: %s" % (type(e).__name__, e))
check("16.5 原子写：落盘后无 .tmp 残留", not os.path.isfile(tmp_c + ".tmp"))

# 16.6 stage_index=0 的友好报错可达（0.4.1 前被 _stage_path 抢抛「不能为负」）
expect_raise("16.6 LatentLoad stage_index=0 → 引导文案（而非「不能为负」）",
             lambda: NODES.H3RelayLatentLoad().load(run_id="unittest_arity", stage_index=0),
             "没有上一段可续")

# 16.7 run_id 非法字符替换为 _（不再静默同目录）
p_a = NODES._stage_path("my/film", 0)
p_b = NODES._stage_path("myfilm", 0)
check("16.7 run_id 含斜杠 → 替换为 _，与纯字母名不撞目录",
      p_a != p_b and "my_film" in p_a)
_nul_dir = os.path.basename(os.path.dirname(NODES._stage_path("NUL", 0)))
check("16.8 run_id Windows 保留名加后缀避让", _nul_dir == "NUL_", "目录名=%s" % _nul_dir)
expect_raise("16.9 run_id 全非法字符仍视为空 → raise",
             lambda: NODES._stage_path("///", 0), "不能为空")

# 16.10 fps 服务端校验（widget min=1 只挡 UI，API 可提交 0/NaN）
expect_raise("16.10 fps=0 → raise（不再 ZeroDivisionError）",
             lambda: NODES.H3RelayTrimAV().trim(images=img73, trim_frames=22, fps=0.0),
             "fps 必须")
expect_raise("16.11 fps=NaN → raise",
             lambda: NODES.H3RelayTrimAV().trim(images=img73, trim_frames=22, fps=float("nan")),
             "fps 必须")

# 16.12 音频栅格偏差告警可达（0.4.1 前 overhang 被覆写 0.0，if overhang: 永不触发）
off_grid = {"samples": NT.NestedTensor([
    CORE.video_from_latent(prev)[0],
    torch.zeros(1, 32, 2, 200),   # 音频 tick 远偏离 round(5/3*192)=320 → grid_off
])}
plan_g = CORE.plan_relay(cur, off_grid, 22)
check("16.12 非标准音频栅格 → notes 出现偏差告警",
      any("偏差超出半整步" in n for n in plan_g.notes),
      "notes=%s" % plan_g.notes)

# 16.13 pin_audio=False 走纯视频 latent（0.4.1 前无条件要求音频流）
pure_t = {"samples": torch.rand(1, 4, 12, 8, 8)}
pure_p = {"samples": torch.rand(1, 4, 12, 8, 8)}
try:
    out_p, trim_p, rep_p = CORE.build_continue_latent(pure_t, pure_p, 22, pin_audio=False)
    check("16.13 纯视频 latent + pin_audio=False 走通（trim=22）", trim_p == 22, rep_p)
except ValueError as e:
    check("16.13 纯视频 latent + pin_audio=False 走通（trim=22）", False, "→ %s" % e)
expect_raise("16.14 纯视频 latent + pin_audio=True → 明确 raise（需要音频流）",
             lambda: CORE.build_continue_latent(pure_t, pure_p, 22, pin_audio=True),
             "音频流")

# 16.15 契约降级不缓存：找不到上游时的放行不能被永久缓存
LC = NODES.CONTRACT        # layout_contract 是包内相对导入，走 nodes 已加载的模块对象
_saved = LC._CACHE
LC._CACHE = None
_orig_ext = LC._extract_frame_per_token
LC._extract_frame_per_token = lambda: (None, ["模拟：上游不可见"])
r1 = LC.check_layout()
calls = [0]
def _counting():
    calls[0] += 1
    return (None, ["模拟：上游不可见"])
LC._extract_frame_per_token = _counting
r2 = LC.check_layout()
LC._extract_frame_per_token = _orig_ext
LC._CACHE = _saved
check("16.15 降级放行不写缓存（下次执行会重试解析）",
      r1[0] and r2[0] and calls[0] == 1 and LC._CACHE is _saved)

# 16.16 拷贝桥掩码与 latent 同设备同 batch
mask_p = out_p["noise_mask"]
check("16.16 noise_mask 与 latent 同设备", mask_p.device == pure_t["samples"].device)
b_multi = {"samples": torch.rand(2, 4, 12, 8, 8)}
b_prev = {"samples": torch.rand(1, 4, 12, 8, 8)}
out_b, _, _ = CORE.build_continue_latent(b_multi, b_prev, 22, pin_audio=False)
check("16.17 batch=2 时掩码 batch 维随 target", int(out_b["noise_mask"].shape[0]) == 2)

# ============ 组17：噪声斜坡 ramp（0.4.3，方向一「软证据」接缝） ============
# 语义：连续掩码 m 按原生 H3 契约就是逐 token 的 sigma 标签（model.py forward:
# "mask value m puts a row at sigma = m * sigma_stream"）——不是硬/软两态开关。
# ramp 全程 m<1 ⇒ 每步都被 (1-m) 锚回拷贝尾；与 taper（头 m=1 无锚）方向相反。

outR, trimR, repR = CORE.build_continue_latent(
    av_latent(12, seed=11), av_latent(12, seed=12), 22, mask_mode="ramp")
mR = outR["noise_mask"][:, :, :7, 0, 0].reshape(-1)   # 前缀 7 token 的掩码列
wR = CORE.prefix_ramp_weights(7, 0.25, 0)
check("17.1 ramp 权重 = prefix_ramp_weights（逐 token 对齐）",
      torch.allclose(mR, torch.tensor(wR, dtype=torch.float32), atol=1e-6),
      "weights=%s" % ([round(float(x), 3) for x in mR],))
check("17.2 ramp 首 token 硬钉（m=0）且严格单调升", float(mR[0]) == 0.0
      and all(float(mR[i + 1]) > float(mR[i]) for i in range(6)),
      "首=%.2f 末=%.2f" % (float(mR[0]), float(mR[-1])))
check("17.3 ramp 默认缝端 = 0.25（软证据，非重绘）", abs(float(mR[-1]) - 0.25) < 1e-6)
check("17.4 ramp 全程 m<1 = 每一步都有锚（区别于 taper 头 1.0 无锚）",
      bool((mR < 1.0).all()))
check("17.5 ramp 前缀拷贝仍位级一致（掩码模式不改拷贝）",
      torch.equal(CORE.video_from_latent(outR)[:, :, :7],
                  CORE.video_from_latent(av_latent(12, seed=12))[:, :, 5:12]))
check("17.6 ramp 非前缀区掩码恒 1（新内容照常生成）",
      bool((outR["noise_mask"][:, :, 7:] == 1).all()))
check("17.7 ramp report 含「噪声斜坡」与 trim", "噪声斜坡" in repR and trimR == 22, repR)
# 窄斜坡：只松缝端 2 个 token，其余硬钉
wR2 = CORE.prefix_ramp_weights(7, 0.4, 2)
check("17.8 ramp_tokens=2：前 5 步硬钉 0，末 2 步 0.2/0.4",
      wR2 == (0.0, 0.0, 0.0, 0.0, 0.0, 0.2, 0.4), "w=%s" % (wR2,))
# ramp_top 越界按端点截断（m∈[0,1]）
check("17.9 ramp_top 截断：>1 截到 1.0，<0 截到 0.0（防呆）",
      CORE.prefix_ramp_weights(4, 3.0, 0)[-1] == 1.0
      and CORE.prefix_ramp_weights(4, -1.0, 0)[-1] == 0.0)
# ramp_top=0 退化为 hard（全 0）
outR0, _, _ = CORE.build_continue_latent(av_latent(12, seed=13), av_latent(12, seed=14),
                                         22, mask_mode="ramp", ramp_top=0.0)
check("17.10 ramp_top=0 退化为 hard（前缀全 0）",
      bool((outR0["noise_mask"][:, :, :7] == 0).all()))
# 设备/batch 一致（与 16.16/17 同纪律）
check("17.11 ramp 掩码与 latent 同设备同 batch",
      out_b is not None and "noise_mask" in outR
      and outR["noise_mask"].device == CORE.video_from_latent(outR).device)
# 旧工作流兼容：mask_mode 默认仍是 hard，optional 键尾追加 ramp_*
_itR = NODES.H3RelayCopyBridge.INPUT_TYPES()
check("17.12 mask_mode 选项追加 ramp（旧 hard/taper 原序保留）",
      _itR["optional"]["mask_mode"][0][:3] == ["hard", "taper", "ramp"]
      and "ramp" in _itR["optional"]["mask_mode"][0])
check("17.13 节点 ramp 默认参数（不接线时行为与 0.4.2 完全一致）",
      _itR["optional"]["ramp_top"][1]["default"] == 0.25
      and _itR["optional"]["ramp_tokens"][1]["default"] == 0)

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
except Exception as _e:                                  # noqa: BLE001
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
#   本断言的作用是「**防止有人把新 widget 插到中间**」⇒ 延长尾部列表即可，不是放宽。
_opt19 = list(NODES.H3RelayTrimAV.INPUT_TYPES()["optional"])
check("19.7 新 widget 追加在 optional 末位（前缀顺序稳定）",
      _opt19[-6:] == ["match_prev", "match_prev_frames", "match_prev_gain_max",
                      "match_prev_offset_max", "run_id", "save_pcm"],
      "尾部=%s" % (_opt19[-6:],))

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
except Exception as _e:                                  # noqa: BLE001
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
import math  # noqa: E402
import shutil  # noqa: E402

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
    check("22.13 节点 stage 0 ⇒ 直通，但仍落盘（后段的床源靠它）",
          _a0 is _bed_a and os.path.isfile(_p0) and "第 1 段无缝可补" in _l0,
          _l0[:56])
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

# —— 22.20~22.24：`joined`（**拼接复合在第 8 节点内**，不新增轮子；GG 2026-09-19 指令）——
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

# ============================================================================
# 23. 音画同步守恒（0.6.2）—— `joined` 只在**给了画面裁量**时才能交叉
# ============================================================================
# 事故：`AudioSeam` 第 3 路 `joined` 的**默认档**（join_cross_ms=50 且 join_align_seconds=0）
#   走「旧缩短语义」—— 交叉淡变是 overlap-add ⇒ **每缝使后段音频提前 50 ms**，且逐段累积
#   （第 3 段起口型明显对不上）。而它只在一行 report 里带个 ⚠ ⇒ 属于「静默产出错位片」。
# 处置：① 默认 join_cross_ms 归 0（默认 = 等长拼接，与段文件首尾相接等价）；
#       ② cross>0 而 align=0 ⇒ **直接 raise**（硬错误不静默降级）；
#       ③ TrimAV 报告里直接算出 `join_align_seconds` 建议值，免去用户心算 fps。
import inspect as _insp23  # noqa: E402

_it23 = NODES.H3RelayAudioSeam.INPUT_TYPES()["optional"]
_sig23 = _insp23.signature(NODES.H3RelayAudioSeam.seam).parameters
check("23.1 `joined` 默认档不再缩短时间轴：join_cross_ms 默认 0（widget 与函数签名一致）",
      _it23["join_cross_ms"][1]["default"] == 0.0
      and _sig23["join_cross_ms"].default == 0.0,
      "widget=%r sig=%r" % (_it23["join_cross_ms"][1]["default"],
                            _sig23["join_cross_ms"].default))

_o23, _r23 = CORE.join_audio_segments([_A22, _B22], prime_samples=_PRJ, cross_samples=0)
_w23 = _o23["waveform"].reshape(1, 1, -1)
_b23 = _B22["waveform"].reshape(1, 1, -1)[..., _PRJ:]
_L023 = int(_A22["waveform"].shape[-1]) - _PRJ
# ⚠ 后段会被**跨段响度匹配**整体缩一个标量（±6 dB 限幅 + 峰值护栏，见 relay_core 2753~2768）
#   ⇒ 断言「逐位相同」是错的；要断言的命题是「**零时间轴位移**」：后段与源段**逐样本位置对齐**，
#   比值处处相等（= 只有幅度差、没有搬运）。这正是「不做交叉 ⇒ 不缩短」的直接取证。
_m23 = _b23.abs() > 0.05
_rr23 = _w23[..., _L023:][_m23] / _b23[_m23]
_g23 = float((_rr23 - _rr23.median()).abs().max())
check("23.2 cross=0 ⇒ 等长 + **零时间轴位移**（后段只被整段增益缩放，样本位置一一对齐）",
      int(_w23.shape[-1]) == _L023 + int(_b23.shape[-1])
      and torch.equal(_w23[..., :_L023], _A22["waveform"].reshape(1, 1, -1)[..., _PRJ:])
      and _g23 < 1e-5
      and "等长拼接" in _r23,
      "%d vs %d ｜ 后段比值离散度 %.2e ｜ 增益 %.6f ｜ mode=%s"
      % (int(_w23.shape[-1]), _L023 + int(_b23.shape[-1]), _g23,
         float(_rr23.median()), _r23.split("｜")[1].strip()[:22]))

_N23 = NODES.H3RelayAudioSeam()
_D23 = {"waveform": torch.zeros(1, 1, 16000), "sample_rate": _SRJ}
expect_raise("23.3 cross>0 而 align=0 ⇒ 节点必须 raise（不静默拼出一条错位音轨）",
             lambda: _N23.seam(_D23, "unit_n23", 0,
                               join_cross_ms=50.0, join_align_seconds=0.0),
             "overlap-add")

_al23, _ss23 = 0.25, 1.0
_o23j, _r23j = CORE.join_audio_segments(
    [_A22, _B22], prime_samples=0, cross_samples=int(0.05 * _SRJ),
    segment_seconds=_ss23, align_seconds=_al23)
_exp23j = int(round(_ss23 * _SRJ)) * 2 - int(round(_al23 * _SRJ))
check("23.4 J-cut 守恒路：输出 == n·段有效时长 − (n−1)·裁量（与裁后视频严格等长）",
      int(_o23j["waveform"].shape[-1]) == _exp23j and "守恒 OK" in _r23j,
      "%d vs %d" % (int(_o23j["waveform"].shape[-1]), _exp23j))

_g23t = torch.rand(30, 8, 8, 3)
_o23t, _a23t, _r23t, _t23t = unwrap(NODES.H3RelayTrimAV().trim(
    _g23t, trim_frames=22, fps=24.0, audio=None))
check("23.5 TrimAV 报告**直接算出** join_align_seconds 建议值（= 裁首帧数 ÷ fps）",
      "join_align_seconds" in _r23t and "0.9167" in _r23t,
      [l.strip() for l in _r23t.splitlines() if "join_align_seconds" in l][:1][0][:80])

# ---------------------------------------------------------------------------
# 24. 床源选窗：语音规避必须覆盖**全部**取床源路径（0.6.5）
#     🔴 起因（2026-09-21，真人耳检阳性 → 根修）：
#       规避原先只加在 `tile_samples <= 0` 分支，而 `tile > 0` 恰恰是产线脚本的**默认档**
#       （`TILE_W=1.2`）⇒ 默认档走的是**没被保护**的那条路；瓦片还会被自叠化环铺 **k 遍**
#       （实测 patch=2.0 + tile=1.2 ⇒ k=2）⇒ 窗内任何一段语音都会被听成 k 遍。
#       修法：所有路径共用 `pick_bed_window`；瓦片档阈值收紧到 **0**（任何有声帧都不许）。
#     ⚠ 判据能力边界：能量型（帧 RMS 超全源中位 3×）⇒ 只能抓「安静底噪里冒出来的语音」；
#       **素材本身以语音为底噪时不报** ⇒ 过判据 ≠「对白重叠」绝迹。
_SR24 = 32000


def _mkbed24(voice=((3.0, 4.0),), total_s=4.5, quiet=0.02, loud=0.40):
    """稳态底噪 + 若干段「台词」的合成床源（2D ``[C,T]``，与产线 `_audio_parts` 同形）。"""
    g = torch.Generator().manual_seed(24)
    w = torch.rand(2, int(total_s * _SR24), generator=g) * quiet
    for a, b in voice:
        w[..., int(a * _SR24):int(b * _SR24)] = loud
    return w


_b24 = _mkbed24()
_W24 = int(1.2 * _SR24)
_n24 = int(1.2 * _SR24)
_f0_24 = int(_b24.shape[-1]) - _W24            # 尾部瓦片窗起点（旧代码的盲点）
_vf0_24 = CORE.bed_window_voiced_fraction(_b24, _f0_24, _W24)
check("24.1 判据自证：尾部瓦片窗确实撞语音（占比 > 单窗阈值）",
      _vf0_24 > CORE.AUDIO_SEAM_BED_VOICED_FRAC,
      "@%.3fs 占比 %.0f%%" % (_f0_24 / _SR24, 100 * _vf0_24))

_s24, _ch24, _v1_24, _v2_24 = CORE.pick_bed_window(
    _b24, _W24, select="tail", prefer=None, floor=0.0,
    frac=CORE.AUDIO_SEAM_BED_TILE_VOICED_FRAC)
check("24.2 瓦片档 tail：首选窗撞语音 ⇒ 换到非语音窗（旧代码此处**不换窗**）",
      _ch24 and _s24 != _f0_24 and _v1_24 > CORE.AUDIO_SEAM_BED_VOICED_FRAC and _v2_24 == 0.0,
      "@%.3fs vf %.0f%%→%.0f%%" % (_s24 / _SR24, 100 * _v1_24, 100 * _v2_24))
check("24.3 瓦片档阈值(0) 比单窗档(10%) 更严：窗内**任何**有声帧都判撞（会被铺 k 遍）",
      CORE.AUDIO_SEAM_BED_TILE_VOICED_FRAC == 0.0
      and CORE.AUDIO_SEAM_BED_TILE_VOICED_FRAC < CORE.AUDIO_SEAM_BED_VOICED_FRAC,
      "tile %.2f vs 单窗 %.2f" % (CORE.AUDIO_SEAM_BED_TILE_VOICED_FRAC,
                                  CORE.AUDIO_SEAM_BED_VOICED_FRAC))

# 24.4~24.5 瓦片档 + E5：错开**不能**再被静默忽略（走 build_bed = 节点真正调用的那段）
_ov24a = CORE.bed_jitter_start(int(_b24.shape[-1]), _W24, 1, 1.2, _SR24)
_ov24b = CORE.bed_jitter_start(int(_b24.shape[-1]), _W24, 2, 1.2, _SR24)
_sa24, _, _va1_24, _va2_24 = CORE.pick_bed_window(
    _b24, _W24, select="tail", prefer=_ov24a, floor=0.0,
    frac=CORE.AUDIO_SEAM_BED_TILE_VOICED_FRAC)
_sb24, _, _, _vb2_24 = CORE.pick_bed_window(
    _b24, _W24, select="tail", prefer=_ov24b, floor=0.0,
    frac=CORE.AUDIO_SEAM_BED_TILE_VOICED_FRAC)
_bd24a, _st24a, _ = CORE.build_bed(_b24, int(2.0 * _SR24), _W24, int(0.25 * _SR24),
                                   select="tail", start_override=_ov24a)
_bd24b, _st24b, _ = CORE.build_bed(_b24, int(2.0 * _SR24), _W24, int(0.25 * _SR24),
                                   select="tail", start_override=_ov24b)
check("24.4 瓦片档 + E5：段1/段2 瓦片起点不同且都非语音（旧代码 jitter 被静默忽略）",
      _sa24 != _sb24 and _st24a != _st24b and _va2_24 == 0.0 and _vb2_24 == 0.0,
      "seg1 @%.3fs / seg2 @%.3fs（请求 %.3fs / %.3fs）"
      % (_st24a / _SR24, _st24b / _SR24, _ov24a / _SR24, _ov24b / _SR24))
check("24.5 瓦片环铺在**新窗**上仍长度守恒（规避不改时间轴）",
      int(_bd24a.shape[-1]) == int(2.0 * _SR24) and int(_bd24b.shape[-1]) == int(2.0 * _SR24),
      "%d / %d" % (int(_bd24a.shape[-1]), int(_bd24b.shape[-1])))

# 24.6 同族函数的 rank 约定必须一致（此前 quietest_window 写死 sum(dim=0) ⇒ 3D 崩）
check("24.6 rank 一致：2D 与 3D 床源选窗完全相同（quietest / pick 各验一遍）",
      CORE.quietest_window(_b24, int(0.5 * _SR24), floor=0.0)[0]
      == CORE.quietest_window(_b24.unsqueeze(0), int(0.5 * _SR24), floor=0.0)[0]
      and CORE.pick_bed_window(_b24, _W24, select="quiet", frac=0.0)[0]
      == CORE.pick_bed_window(_b24.unsqueeze(0), _W24, select="quiet", frac=0.0)[0],
      "2D/3D 同点")

# 24.7 无候选（全源皆撞）⇒ 退回首选窗、**不 raise**（「素材本身以语音为底噪」是合法素材）
_b24d = _mkbed24(voice=tuple((i * 0.75, i * 0.75 + 0.30) for i in range(12)), total_s=9.0)
_nc24 = len(CORE.nonvoiced_candidates(_b24d, _W24, 1600, 0.0))
_s24d, _ch24d, _v1_24d, _v2_24d = CORE.pick_bed_window(
    _b24d, _W24, select="tail", prefer=None, floor=0.0,
    frac=CORE.AUDIO_SEAM_BED_TILE_VOICED_FRAC)
check("24.7 全源皆撞语音 ⇒ 不 raise，tail 退到最静窗（语音残留最少）且占比不升",
      _nc24 == 0 and _ch24d and _s24d != _b24d.shape[-1] - _W24 and _v2_24d <= _v1_24d,
      "候选 %d 个 ⇒ 尾窗@%.3fs(vf %.0f%%) → 最静窗@%.3fs(vf %.0f%%)"
      % (_nc24, (_b24d.shape[-1] - _W24) / _SR24, 100 * _v1_24d,
         _s24d / _SR24, 100 * _v2_24d))

# 24.8 默认关不变量：尾部窗**不撞语音**时 ⇒ 起点与「直接切尾窗」逐位相同
_b24q = _mkbed24(voice=((0.5, 1.0),))            # 语音在中段，尾部干净
_bq24, _stq24, _ = CORE.build_bed(_b24q, _n24, 0, int(0.25 * _SR24), select="tail")
check("24.8 默认关不变量：尾部不撞语音 ⇒ 起点=尾部且床声逐位相同（干净素材零行为变化）",
      _stq24 == int(_b24q.shape[-1]) - _n24
      and torch.equal(_bq24, _b24q[..., _stq24:_stq24 + _n24]),
      "起点 %.3fs" % (_stq24 / _SR24))

# 24.9 性能回归锁：候选遍历必须先算一次帧 RMS（原实现每个候选重算整段 ⇒ O(T²)）
_calls24 = [0]
_orig_fr24 = CORE._frame_rms


def _count_fr24(*a, **kw):
    _calls24[0] += 1
    return _orig_fr24(*a, **kw)


CORE._frame_rms = _count_fr24
try:
    CORE.nonvoiced_candidates(_b24, _W24, 1600, 0.0)
finally:
    CORE._frame_rms = _orig_fr24
check("24.9 候选遍历只算一次帧 RMS（原实现 O(T²)：60s 床源会卡住主线程）",
      _calls24[0] == 1, "调用 %d 次" % _calls24[0])

# 24.10~24.11 报告：说清「为什么换窗」+ 报出阈值；E5 档窗位只印一次
_cur24 = {"waveform": torch.zeros(1, 2, int(3.5 * _SR24)), "sample_rate": _SR24}
_cur24["waveform"][..., int(0.5 * _SR24):int(1.0 * _SR24)] = 0.3
_bed24 = {"waveform": _b24.unsqueeze(0), "sample_rate": _SR24}
_, _rep24 = CORE.audio_seam_patch(_cur24, _bed24, patch=2.0, tile=1.2, fade=0.25,
                                  select="tail", bed_jitter=0.0, stage_index=1)
check("24.10 瓦片档报告：阈值 + 「首选窗原撞语音 ⇒ 已换到非语音窗」都在（不静默）",
      "判据阈值 0%" in _rep24 and "首选窗原撞语音（占比" in _rep24
      and "已换到非语音窗 @" in _rep24,
      next((l for l in _rep24.splitlines() if "🎙" in l), "")[:150])
_, _rep24b = CORE.audio_seam_patch(_cur24, _bed24, patch=2.0, tile=1.2, fade=0.25,
                                   select="tail", bed_jitter=1.2, stage_index=1)
check("24.11 E5 档报告：首行窗位只出现一次（mode 里不再重复打印错开位置）",
      _rep24b.splitlines()[0].count("@") == 1
      and "E5 错开窗（按段号错开）" in _rep24b.splitlines()[0],
      str(_rep24b.splitlines()[0])[:110])

# 24.12 持续帧滤波（P0）：单帧瞬态不计入，连续台词无损（瓦片档 0% 阈值的瞬态防线）
_b24t = _mkbed24()                                   # 尾窗 58% 台词（连续块）
_b24x = _mkbed24(voice=())                           # 纯底噪
_b24x[..., _b24x.shape[-1] - int(0.3 * _SR24) - 1600: _b24x.shape[-1] - int(0.3 * _SR24) - 1600 + 1600] = 0.6
_vt24 = CORE.bed_window_voiced_fraction(_b24t, _b24t.shape[-1] - _W24, _W24)
_vx24 = CORE.bed_window_voiced_fraction(_b24x, _b24x.shape[-1] - _W24, _W24)
check("24.12 持续帧滤波：连续台词占比无损（58% 级）且单帧瞬态不计入（0%）",
      _vt24 > 0.5 and _vx24 == 0.0,
      "台词 %.0f%% / 瞬态 %.0f%%" % (100 * _vt24, 100 * _vx24))

# 24.13 退化输入不炸：prefer 越界钳制 / 全零床源 / 超短床源（<2 帧无法判定 ⇒ 放行不 raise）
_b24z = torch.zeros(2, int(1.0 * _SR24))
_ok24 = []
try:
    _s24p, _, _, _ = CORE.pick_bed_window(_b24, _W24, select="tail", prefer=10 ** 9, frac=0.0)
    _ok24.append(_s24p <= _b24.shape[-1] - _W24)
    _s24m, _, _, _ = CORE.pick_bed_window(_b24, _W24, select="tail", prefer=-5, frac=0.0)
    _ok24.append(_s24m == 0)
    CORE.pick_bed_window(_b24z, int(0.5 * _SR24), select="tail", frac=0.0)
    _ok24.append(True)
    _st24s, _, _, _ = CORE.pick_bed_window(torch.zeros(2, 3000), 2000, select="tail", frac=0.0)
    _ok24.append(_st24s == 3000 - 2000)          # tail 语义：超短床源放行但起点仍在尾部
    _ok24.append(True)
except Exception as _e24:
    _ok24 = ["raise: %s" % _e24]
check("24.13 退化输入不炸：prefer 越界钳制 / 全零 / 超短床源（<2 帧放行不判定）",
      all(x is True for x in _ok24), str(_ok24))

# 24.14 前缀和能量选窗 == 逐候选参照实现（0.6.4 性能修补的正确性锁）
_c24 = CORE.nonvoiced_candidates(_b24, _W24, 1600, CORE.AUDIO_SEAM_BED_TILE_VOICED_FRAC)
_ct24 = torch.tensor(_c24, dtype=torch.long)
_ch24n = 2
_p24 = (_b24 ** 2).reshape(-1, _b24.shape[-1]).sum(dim=0)
_c24c = torch.cat([_p24.new_zeros(1), torch.cumsum(_p24, dim=0)])
_wr24 = torch.sqrt((_c24c[_ct24 + _W24] - _c24c[_ct24]).clamp_min(0.0) / (_W24 * _ch24n))
_naive24 = max(_c24, key=lambda c: float(_b24[..., c:c + _W24].pow(2).mean().sqrt()))
check("24.14 候选能量前缀和 == 逐候选参照（性能修补不许改结果）",
      int(_ct24[int(torch.argmax(_wr24))]) == int(_naive24),
      "前缀和 @%.3fs / 参照 @%.3fs" % (_ct24[int(torch.argmax(_wr24))] / _SR24, _naive24 / _SR24))

# ---------------------------------------------------------------------------
# 25. 🛡 patch 台词守卫（0.6.5）：patch 不许吃本段自己的台词
#     🔴 起因（2026-09-21 GG 耳检 + 词级时间戳实锤）：patch=2.0 把 okBed2 段2 的
#       「这家店」0.60–1.70s 整句吃掉（撑 跨淡变残缺）—— patch 只管"换床声"，
#       根本不看本段头上有没有话。守卫 = 探测本段头部台词起点 ⇒ patch 收缩到
#       onset − 0.25s；onset ≤ 0.10s 可用 ⇒ patch 关闭。判据 **宁枉勿纵**（2×P10）。
_SR25 = 32000


def _mktarget25(onset=0.6, dur_s=4.0, quiet=0.02, loud=0.40):
    g = torch.Generator().manual_seed(25)
    w = torch.rand(2, int(dur_s * _SR25), generator=g) * quiet
    w[..., int(onset * _SR25):int((onset + 1.6) * _SR25)] = loud
    return w


_g25bed = torch.rand(2, int(6.0 * _SR25), generator=torch.Generator().manual_seed(26)) * 0.015
_t25 = {"waveform": _mktarget25(), "sample_rate": _SR25}
_b25d = {"waveform": _g25bed, "sample_rate": _SR25}
_X25 = int(0.25 * _SR25)

# 25.1 头部带台词 ⇒ 收缩 + 台词区逐位保留
_o25a, _r25a = CORE.audio_seam_patch(_t25, _b25d, patch=2.0, tile=0.0, fade=0.25,
                                     select="tail", bed_jitter=0.0, stage_index=1)
_on25 = CORE._speech_onset_in_head(CORE._audio_parts(_t25)[0], int(2.0 * _SR25))
_w25o = CORE._audio_parts(_o25a)[0]
_w25r = CORE._audio_parts(_t25)[0]
check("25.1 头部台词 @%.2fs ⇒ patch 自动收缩且台词起逐位无损" % (_on25 / _SR25),
      _on25 is not None and "避让台词" in _r25a
      and torch.equal(_w25o[..., _on25:], _w25r[..., _on25:]),
      "report: " + [l for l in _r25a.splitlines() if "🛡" in l][0][-70:])

# 25.2 头部干净 ⇒ 守卫开 == 守卫关（逐位，零副作用）
_t25q = {"waveform": _mktarget25(onset=99), "sample_rate": _SR25}
_o25g, _ = CORE.audio_seam_patch(_t25q, _b25d, patch=2.0, tile=0.0, fade=0.25,
                                 select="tail", bed_jitter=0.0, stage_index=1)
_o25n, _ = CORE.audio_seam_patch(_t25q, _b25d, patch=2.0, tile=0.0, fade=0.25,
                                 select="tail", bed_jitter=0.0, stage_index=1, patch_guard=False)
check("25.2 头部无台词 ⇒ 守卫开与关输出逐位一致（干净素材零行为变化）",
      torch.equal(CORE._audio_parts(_o25g)[0], CORE._audio_parts(_o25n)[0]))

# 25.3 台词太靠前（onset − margin < 0.10s 可用）⇒ patch 关闭 + 原样返回 + report 说明
_t25e = {"waveform": _mktarget25(onset=0.05), "sample_rate": _SR25}
_o25e, _r25e = CORE.audio_seam_patch(_t25e, _b25d, patch=2.0, tile=0.0, fade=0.25,
                                     select="tail", bed_jitter=0.0, stage_index=1)
check("25.3 台词太靠前 ⇒ patch 自动关闭、音频原样返回、report 说明",
      torch.equal(CORE._audio_parts(_o25e)[0], CORE._audio_parts(_t25e)[0])
      and "patch 自动关闭" in _r25e,
      _r25e.splitlines()[0][:80])

# 25.4 守卫关 ⇒ 旧行为（头部确实被替换 ⇒ 可能吞字，这是用户显式选择的语义）
_o25f, _r25f = CORE.audio_seam_patch(_t25, _b25d, patch=2.0, tile=0.0, fade=0.25,
                                     select="tail", bed_jitter=0.0, stage_index=1,
                                     patch_guard=False)
_w25f = CORE._audio_parts(_o25f)[0]
check("25.4 守卫关 ⇒ 旧行为（头部被替换、report 无 🛡）",
      "🛡" not in _r25f
      and not torch.equal(_w25f[..., :int(0.5 * _SR25)], _w25r[..., :int(0.5 * _SR25)]))

# ============ 组26：多段拼接成片（0.6.7）——探测/体检/无损拷贝/音频对齐 ============
# 全部在**合成段文件**上跑（PyAV 现造 mp4），零 GPU；宿主 ComfyUI 自带 av，缺了就整组跳过。
print()
print("[26] 多段拼接成片：探测 → 体检 → 拼接（画面流拷贝无损 + 音频逐段对齐）→ 断言")


def _concat_env():
    try:
        import av
        import numpy as np
        return av, np
    except Exception:  # noqa: BLE001
        return None, None


_av26, _np26 = _concat_env()
if _av26 is None:
    check("26.0 PyAV 可用（拼接功能的运行前提；宿主 ComfyUI 自带）", False,
          "没装 av ⇒ 本组跳过（pip install 'av>=17'）")
else:
    import fractions as _fr26

    _TMP26 = tempfile.mkdtemp(prefix="h3relay_concat_")
    # 合成夹具优先用 libx264（生产默认）；精简 FFmpeg（如 CI 的 av 轮子）上没有它 ⇒ 退 mpeg4。
    #   本组测的是**拼接逻辑**，与编码器无关；真机跑的是 libx264。
    _VCODEC26 = "libx264" if "libx264" in set(_av26.codecs_available) else "mpeg4"

    def _mkclip(name, frames=48, fps=24, w=64, h=64, tone=440.0, audio_secs=None,
                sr=32000, noise_seed=0):
        """现造一个与真实段文件同构的 mp4：h264 + aac，音频比视频长（编码器 priming）。"""
        path = os.path.join(_TMP26, name)
        out = _av26.open(path, "w", format="mp4")
        vs = out.add_stream(_VCODEC26, rate=fps, options=(
            {"crf": "28", "preset": "ultrafast"} if _VCODEC26 == "libx264" else {"qscale": "5"}))
        vs.width, vs.height, vs.pix_fmt = w, h, "yuv420p"
        as_ = out.add_stream("aac", rate=sr)
        as_.layout = "stereo"
        rng = _np26.random.default_rng(noise_seed)
        for i in range(frames):
            arr = rng.integers(0, 255, (h, w, 3), dtype=_np26.uint8)
            fr = _av26.VideoFrame.from_ndarray(arr, format="rgb24").reformat(format="yuv420p")
            fr.pts = i
            fr.time_base = _fr26.Fraction(1, fps)
            for pkt in vs.encode(fr):
                out.mux(pkt)
        secs = audio_secs if audio_secs is not None else frames / float(fps)
        n = int(round(secs * sr))
        t = _np26.arange(n) / float(sr)
        sig = (0.3 * _np26.sin(2 * _np26.pi * tone * t)).astype("float32")
        pcm = _np26.stack([sig, sig])
        pos = 0
        for s in range(0, n, 1024):
            af = _av26.AudioFrame.from_ndarray(
                _np26.ascontiguousarray(pcm[:, s:s + 1024]), format="fltp", layout="stereo")
            af.sample_rate = sr
            af.pts = pos
            af.time_base = _fr26.Fraction(1, sr)
            pos += af.samples
            for pkt in as_.encode(af):
                out.mux(pkt)
        for st in (vs, as_):
            for pkt in st.encode(None):
                out.mux(pkt)
        out.close()
        return path

    _c1 = _mkclip("c1.mp4", tone=440.0, noise_seed=1)
    _c2 = _mkclip("c2.mp4", tone=880.0, noise_seed=2)
    _c3 = _mkclip("c3.mp4", tone=1320.0, noise_seed=3)

    # —— 26.1 探测：读出真实容器事实 ——
    # ⚠ 合成段的音频正好等于视频长（PyAV 封装会按 edit list 把编码器 priming 剪掉）；
    #   真实段文件比视频**长**（宿主落盘把裁后 PCM 原样写进去）——那种「多出来的样本」
    #   由 26.3 的 a_prime_samples 判据覆盖。这里只锁"读得准"。
    _p1 = CORE.probe_mp4(_c1)
    check("26.1 probe_mp4 读出帧数/fps/时长/音频（合成段）",
          _p1["frames"] == 48 and abs(_p1["fps"] - 24.0) < 1e-9
          and abs(_p1["v_seconds"] - 2.0) < 1e-3 and _p1["has_audio"]
          and abs(_p1["a_seconds"] - _p1["v_seconds"]) <= 1.0 / 24.0,
          "frames=%d fps=%s v=%.4f a=%.4f 差=%+.4f"
          % (_p1["frames"], _p1["fps"], _p1["v_seconds"], _p1["a_seconds"], _p1["a_v_delta"]))

    # —— 26.2 体检：priming 量级的差不算问题（否则每张正常图都会红） ——
    _h_ok = CORE.assert_segments_joinable([_p1, CORE.probe_mp4(_c2)])
    check("26.2 体检放过 priming 量级的音视频差（默认档不误报）", _h_ok["ok"],
          "problems=%s" % _h_ok["problems"])

    # —— 26.3 体检：音频**明显**长于视频 ⇒ 判「音频线绕过裁重叠」并拒绝拼 ——
    _c_long = _mkclip("c_long.mp4", frames=48, tone=440.0, audio_secs=2.0 + 0.5, noise_seed=4)
    _p_long = CORE.probe_mp4(_c_long)
    _h_bad = CORE.assert_segments_joinable([_p1, _p_long])
    check("26.3 体检抓到「音频绕过裁重叠」（> 1 帧）且给出可照做的处置",
          (not _h_bad["ok"]) and any("绕过" in p and "裁重叠" in p for p in _h_bad["problems"])
          and _p_long["a_prime_samples"] > 0,
          "a_prime=%+d samples ｜ problems=%s"
          % (_p_long["a_prime_samples"], _h_bad["problems"][:1]))

    # —— 26.4 体检：规格不一致（帧率/分辨率）不许拼 ——
    _c_fps = _mkclip("c_fps.mp4", frames=48, fps=30, noise_seed=5)
    _h_fps = CORE.assert_segments_joinable([_p1, CORE.probe_mp4(_c_fps)])
    check("26.4 体检抓到帧率不一致（拼出来必坏）",
          (not _h_fps["ok"]) and any("规格" in p for p in _h_fps["problems"]))

    # —— 26.5 体检不过 ⇒ 一段都不拼（坏片不许落地） ——
    _bad_out = os.path.join(_TMP26, "should_not_exist.mp4")
    _rep_bad = CORE.assemble_mp4_segments([_c1, _c_long], _bad_out)
    check("26.5 体检不过 ⇒ 不产出成片（不静默拼半条）",
          (not _rep_bad["ok"]) and (not os.path.exists(_bad_out))
          and "没有拼" in _rep_bad["report"])

    # —— 26.6 端到端：画面流拷贝（无损）+ 音频逐段对齐 + 四项断言 ——
    _out26 = os.path.join(_TMP26, "film.mp4")
    _rep26 = CORE.assemble_mp4_segments([_c1, _c2, _c3], _out26)
    _a26 = _rep26["asserts"] or {}
    check("26.6 三段端到端：帧数守恒 + PTS 无洞 + DTS 递增 + A·VΔ ≤ 1 帧",
          _rep26["ok"] and _a26.get("frames") == 144
          and _a26.get("frames_conserved") and _a26.get("pts_contiguous")
          and _a26.get("dts_strictly_increasing") and _a26.get("av_delta_ok"),
          "mode=%s asserts=%s" % (_rep26.get("mode"), _a26))
    check("26.7 默认走画面**流拷贝**（无损，不重编码）", _rep26.get("mode") == "copy")

    # —— 26.8 无损性：成片第 k 段首帧 与源段首帧**逐位相同** ——
    def _first_frames(path, wanted):
        got = {}
        with _av26.open(path) as c:
            v = c.streams.video[0]
            for i, f in enumerate(c.decode(v)):
                if i in wanted:
                    got[i] = f.to_ndarray()
                if i > max(wanted):
                    break
        return got

    _of = _first_frames(_out26, {0, 48, 96})
    _sf1 = _first_frames(_c1, {0})[0]
    _sf2 = _first_frames(_c2, {0})[0]
    _sf3 = _first_frames(_c3, {0})[0]
    check("26.8 流拷贝无损：三段首帧在成片里逐位一致",
          _np26.array_equal(_of[0], _sf1) and _np26.array_equal(_of[48], _sf2)
          and _np26.array_equal(_of[96], _sf3))

    # —— 26.9 音频对齐：段边界两侧的主频就是各自源段的音（错位/留洞都会破坏） ——
    def _dom_freq(path, start_s, dur_s=0.2, sr=32000):
        with _av26.open(path) as c:
            a = c.streams.audio[0]
            rs = _av26.audio.resampler.AudioResampler(format="fltp", layout="stereo", rate=sr)
            chunks = [o.to_ndarray() for f in c.decode(a) for o in rs.resample(f)]
        wav = _np26.concatenate(chunks, axis=1)
        i0 = int(start_s * sr)
        seg = wav[0, i0:i0 + int(dur_s * sr)]
        if seg.size < 64:
            return 0.0
        spec = _np26.abs(_np26.fft.rfft(seg * _np26.hanning(seg.size)))
        return float(_np26.fft.rfftfreq(seg.size, 1.0 / sr)[int(_np26.argmax(spec))])

    _pm = int(round(CORE.AUDIO_ENCODER_PRIME_MS / 1000.0 * 32000))
    _f_before = _dom_freq(_out26, (96 / 24.0) - 0.4 + _pm / 32000.0)   # 第三段边界前 0.4s
    _f_after = _dom_freq(_out26, (96 / 24.0) + _pm / 32000.0)          # 第三段开头 0.2s
    check("26.9 音频逐段对齐：边界前 = 第 2 段音(880Hz)、边界后 = 第 3 段音(1320Hz)",
          abs(_f_before - 880.0) < 40.0 and abs(_f_after - 1320.0) < 40.0,
          "before=%.1fHz after=%.1fHz（期望 880 / 1320）" % (_f_before, _f_after))

    # —— 26.10 退路：显式强制重编码路，同样要过四项断言 ——
    _out26e = os.path.join(_TMP26, "film_encode.mp4")
    _rep26e = CORE.assemble_mp4_segments([_c1, _c2], _out26e, video="encode")
    _ae = _rep26e["asserts"] or {}
    check("26.10 退路（video=\"encode\"）也过断言：帧数守恒 + A·VΔ ok",
          _rep26e["ok"] and _rep26e.get("mode") == "encode"
          and _ae.get("frames_conserved") and _ae.get("av_delta_ok"),
          "mode=%s asserts=%s" % (_rep26e.get("mode"), _ae))

    # —— 26.11 空输入不炸：报「没有任何段文件」，不写出东西 ——
    _rep_empty = CORE.assemble_mp4_segments([], os.path.join(_TMP26, "empty.mp4"))
    check("26.11 空段列表 ⇒ 明确报错、不产出", (not _rep_empty["ok"])
          and "没有任何段文件" in _rep_empty["report"])

    # —— 26.12 history 落盘条目筛选（纯函数；宿主 SaveVideo 写进 images 键） ——
    _picked = CORE.pick_video_outputs({
        "31": {"images": [{"filename": "a.mp4", "subfolder": "relay_kit/x", "type": "output"},
                          {"filename": "a.png", "subfolder": "relay_kit/x", "type": "output"}]},
        "32": {"text": ["noise"]},
        "33": {"images": [{"filename": "b.webm", "subfolder": "", "type": "output"}]},
    })
    check("26.12 pick_video_outputs 只挑视频类落盘（png 与无关键不误收）",
          [p["filename"] for p in _picked] == ["a.mp4", "b.webm"]
          and _picked[0]["subfolder"] == "relay_kit/x",
          "%s" % [p["filename"] for p in _picked])
    check("26.13 pick_video_outputs 对畸形输入不炸", CORE.pick_video_outputs(None) == []
          and CORE.pick_video_outputs({"1": {"images": [1, None, "x"]}}) == [])

    # —— 26.45 落盘节点**键名不设白名单**（2026-09-22 真实跑抓到） ——
    #   宿主 SaveVideo 写 `images`；第三方 `banzhangVideoCombine` 写 **`painter_output`**，
    #   且它的「自定义保存路径」在 ComfyUI output **之外**（subfolder 为空）⇒ 必须优先用 abs_path。
    _tp = CORE.pick_video_outputs({
        "709": {"painter_output": [
            {"filename": "au4_s1_N709_0001.mp4", "subfolder": "", "type": "output",
             "abs_path": r"I:/coffee_short/output/au4_s1_N709_0001.mp4"},
            {"filename": "au4_s1_N709_0001_meta.png", "subfolder": "", "type": "output"}],
            "detail_info": ["📂 文件名称 : au4_s1_N709_0001.mp4\n📏 物理尺寸 : 480 x 864"]},
        "905": {"h3relay_pcm": [{"filename": "audio_00000.safetensors",
                                 "subfolder": "relay_kit\\au4", "type": "output"}]},
    })
    check("26.45 第三方落盘键名（painter_output）也能拾取：视频收下、png/文本不误收、abs_path 带出",
          [x["filename"] for x in _tp] == ["au4_s1_N709_0001.mp4"]
          and _tp[0]["abs_path"].replace("\\", "/").endswith("coffee_short/output/au4_s1_N709_0001.mp4"),
          "%s" % ([(x["filename"], x["abs_path"]) for x in _tp],))
    # —— 26.47 落盘路径发现要**通用**（不认节点名 / 键名 / 字段名） ——
    #   各家节点回显各不相同：字符串路径 / `file` 键 / 相对带目录 / 同一文件多处报 —— 全都要能收下。
    _gen = CORE.pick_video_outputs({
        "1": {"videos": ["I:/x/str_path.mp4"]},                       # 只给一个路径字符串
        "2": {"out": [{"file": "sub/dir/f.mp4"}]},                    # `file` 键 + 相对目录
        "3": {"a": [{"filename": "dup.mp4", "subfolder": "s", "type": "output"}],
              "b": [{"filename": "dup.mp4", "subfolder": "s", "type": "output"}]},
        "4": {"txt": ["不是文件"], "img": [{"filename": "cover.png", "type": "output"}]},
    })
    check("26.47 通用归一化：字符串路径 / file 键 / 相对目录 / 去重 / png+文本不误收（共 3 条）",
          [x["filename"] for x in _gen] == ["str_path.mp4", "f.mp4", "dup.mp4"]
          and _gen[0]["abs_path"].replace("\\", "/") == "I:/x/str_path.mp4"
          and _gen[1]["subfolder"].replace("\\", "/") == "sub/dir",
          "%s" % ([(x["filename"], x["subfolder"], x["abs_path"]) for x in _gen],))

    # —— 26.48 说明文本**不许**被当成文件（预检抓到的假阳性） ——
    #   真实形状：`detail_info` 里是 `📂 文件名称 : au4_s1_N709_0001.mp4\n📏 物理尺寸 : …`
    #   —— 末尾也是 .mp4，若被当成记录且排在真记录前面，就会拿错路径（白跑一轮）。
    _trap = CORE.pick_video_outputs({
        "709": {"detail_info": ["📂 文件名称 : au4_s1_N709_0001.mp4\n📏 物理尺寸 : 480 x 864"],
                "painter_output": [{"filename": "au4_s1_N709_0001.mp4", "subfolder": "",
                                    "type": "output", "abs_path": r"I:/coffee_short/output/au4_s1_N709_0001.mp4"}]},
        "710": {"note": ["📂 文件名称 : x.mp4", "带\n换行的 y.mp4"]},
    })
    check("26.48 说明文本/非法文件名不误收（只有真记录那一条，且排第 1）",
          [x["filename"] for x in _trap][:1] == ["au4_s1_N709_0001.mp4"]
          and "x.mp4" not in [x["filename"] for x in _trap]
          and len(_trap) == 1,
          "%s" % ([x["filename"] for x in _trap],))

    check("26.45b 同一份 outputs 里，PCM 边车按 .safetensors 走 pick_pcm_outputs（与视频互不串）",
          [x["filename"] for x in CORE.pick_pcm_outputs({
              "905": {"h3relay_pcm": [{"filename": "audio_00000.safetensors",
                                       "subfolder": "relay_kit/au4", "type": "output"}]}})]
          == ["audio_00000.safetensors"])

    # ================================================================
    # 26.19~26.31 音频代际 2 → 1（2026-09-22 二轮）：**PCM 边车**
    # ================================================================
    # 动机：段文件的音轨是 AAC（用户落盘那一代）。拼成片若从 mp4 解码再编 = **第二代数损**。
    # 「裁重叠」手里那份音频**既与画面等长、又还没经过有损编码** ⇒ 它顺手存一份 PCM 边车，
    # 拼接直读 ⇒ 音频只编码一代；配无损档则**零新增代际**。
    import inspect as _ins26
    import folder_paths as _fp26

    _side_a = os.path.join(_TMP26, "pcm_a.safetensors")
    _side_b = os.path.join(_TMP26, "pcm_b.safetensors")
    _side_16k = os.path.join(_TMP26, "pcm_16k.safetensors")
    _side_short = os.path.join(_TMP26, "pcm_short.safetensors")
    _TMP26_SR = 32000
    _mk_n = 64000

    def _mk_side(path, tone, n=_mk_n, sr=_TMP26_SR):
        tt = _np26.arange(n) / float(sr)
        sig = (0.3 * _np26.sin(2 * _np26.pi * tone * tt)).astype("float32")
        CORE.save_audio({"waveform": torch.from_numpy(_np26.stack([sig, sig])).unsqueeze(0),
                         "sample_rate": sr}, path, note="unit")
        return path

    def _audio_of(path, sr=_TMP26_SR):
        with _av26.open(path) as c:
            a = c.streams.audio[0]
            rs = _av26.audio.resampler.AudioResampler(format="fltp", layout="stereo", rate=sr)
            ch = [o.to_ndarray() for f in c.decode(a) for o in rs.resample(f)]
            ch += [o.to_ndarray() for o in (rs.resample(None) or [])]
        return _np26.concatenate(ch, axis=1)

    _mk_side(_side_a, 300.0)
    _mk_side(_side_b, 900.0)

    # —— 26.19 默认档：AAC 256k（用户口径：默认 256K、192K 可覆盖） ——
    check("26.19 成片音轨默认档 = AAC **256k**（可显式覆盖）",
          _ins26.signature(CORE.concat_mp4_segments).parameters["audio_bitrate"].default == "256k",
          "default=%r" % _ins26.signature(CORE.concat_mp4_segments).parameters["audio_bitrate"].default)

    # —— 26.20 无损档 + 边车：成片音轨与边车**逐位一致**（零新增代际） ——
    _out_ll = os.path.join(_TMP26, "film_lossless.mp4")
    _rep_ll = CORE.assemble_mp4_segments([_c1, _c2], _out_ll, audio_codec="pcm_f32le",
                                         pcm_paths=[_side_a, _side_b])
    _ref = CORE.load_audio(_side_a)["waveform"].reshape(-1, 2, _mk_n)[0].to(torch.float32).numpy()
    _film = _audio_of(_out_ll)
    _delta = float(_np26.max(_np26.abs(_film[:, :_ref.shape[1]] - _ref)))
    check("26.20 无损档 + 边车 ⇒ 成片第 1 段音轨与边车**逐位一致**（音频零新增代际）",
          _rep_ll["ok"] and _rep_ll.get("pcm_segments") == 2 and _delta < 1e-6,
          "ok=%s pcm段=%s max|Δ|=%.3e" % (_rep_ll["ok"], _rep_ll.get("pcm_segments"), _delta))

    # —— 26.21 边车直读时**不做**去 priming 的位移（对齐记录要如实） ——
    check("26.21 走边车的段：prime_drop=0、tail_drop=0（边车本身已与画面等长）",
          all(a["source"] == "pcm" and a["prime_drop"] == 0 and a["tail_drop"] == 0
              for a in _rep_ll["alignment"]),
          "%s" % [(a["source"], a["prime_drop"], a["tail_drop"]) for a in _rep_ll["alignment"]])
    # 同一批段走 mp4 解码路时，第 1 段必须真的去掉了 priming（否则 26.20 的对比没意义）
    check("26.22 对照：不接边车时该段走 mp4 解码路（source=aac）",
          all(a["source"] == "aac" for a in _rep26["alignment"]),
          "%s" % [a["source"] for a in _rep26["alignment"]])

    # —— 26.23 编码码率可覆盖（192k）；仍出片、仍过四项断言 ——
    _out_192 = os.path.join(_TMP26, "film_192k.mp4")
    _rep_192 = CORE.assemble_mp4_segments([_c1, _c2], _out_192, audio_bitrate="192k")
    check("26.23 AAC 192k 档可覆盖且成片过断言",
          _rep_192["ok"] and (CORE.probe_mp4(_out_192)["has_audio"]),
          "ok=%s" % _rep_192["ok"])

    # —— 26.24 混档：只有部分段有边车 ⇒ 逐段标注来源，缺的自动退回解码（不静默） ——
    _out_mix = os.path.join(_TMP26, "film_mix.mp4")
    _rep_mix = CORE.assemble_mp4_segments([_c1, _c2], _out_mix, pcm_paths=[_side_a, None])
    _src = {a["index"]: a["source"] for a in _rep_mix["alignment"]}
    check("26.24 边车缺失的段自动退回 mp4 解码，且逐段如实标注来源",
          _rep_mix["ok"] and _src == {0: "pcm", 1: "aac"} and _rep_mix.get("pcm_segments") == 1,
          "sources=%s pcm段=%s" % (_src, _rep_mix.get("pcm_segments")))

    # —— 26.25 边车对不上（采样率不同）⇒ 弃用该段边车，不 raise、不炸 ——
    CORE.save_audio({"waveform": torch.zeros(1, 2, 1000), "sample_rate": 44100}, _side_16k,
                    note="unit")
    _out_mm = os.path.join(_TMP26, "film_mismatch.mp4")
    _rep_mm = CORE.assemble_mp4_segments([_c1, _c2], _out_mm, pcm_paths=[_side_16k, _side_b])
    check("26.25 边车采样率对不上 ⇒ 弃用该段边车（退回解码），不抛异常",
          _rep_mm["ok"] and _rep_mm["alignment"][0]["source"] == "aac"
          and _rep_mm["alignment"][1]["source"] == "pcm",
          "%s" % [a["source"] for a in _rep_mm["alignment"]])

    # —— 26.26 边车**轻微**偏短（容差内）⇒ 补静音并出警告（不假装无损） ——
    #   ⚠ 差太多（>100ms）由护栏直接**拒收**、不是补静音 —— 那才是安全行为（见 26.35）。
    _mk_side(_side_short, 700.0, n=_mk_n - 1000)
    _rep_sh = CORE.assemble_mp4_segments([_c1, _c2], os.path.join(_TMP26, "film_short.mp4"),
                                         pcm_paths=[_side_short, None])
    check("26.26 边车比视频短 ⇒ 补静音 + 出警告（缺多少就说多少）",
          _rep_sh["ok"] and any("补静音" in w for w in _rep_sh["warnings"]),
          "warnings=%s" % _rep_sh["warnings"])

    # —— 26.27 段规格不一致时，流拷贝路炸了也要有条走得通的路（退重编码） ——
    _out_spec = os.path.join(_TMP26, "film_spec.mp4")
    _rep_spec = CORE.assemble_mp4_segments([_c1, _c_fps], _out_spec, video="auto")
    check("26.27 规格不一致 ⇒ 体检就拦下（不拼），避免「拷贝不了又硬拼」",
          (not _rep_spec["ok"]) and any("规格" in p for p in _rep_spec["health"]["problems"]),
          "problems=%s" % _rep_spec["health"]["problems"][:1])

    # —— 26.28~26.31 边车的**生产者**：「裁重叠」回的 ui 键 + 纯函数取回 ——
    _opt_t = NODES.H3RelayTrimAV.INPUT_TYPES()["optional"]
    check("26.28 裁重叠新增 save_pcm（默认**开**：只多写一个文件，节点输出与成片逐位不变）",
          "save_pcm" in _opt_t and _opt_t["save_pcm"][1].get("default") is True,
          "save_pcm=%r" % (_opt_t.get("save_pcm"),))
    _img73b = torch.zeros(73, 2, 2, 3)
    _aud73 = {"waveform": torch.zeros(1, 2, 68000), "sample_rate": 32000}
    _r_on = NODES.H3RelayTrimAV().trim(images=_img73b, trim_frames=22, fps=24.0, audio=_aud73,
                                       save_pcm=True)
    _ent = (_r_on.get("ui") or {}).get(CORE.PCM_UI_KEY) if isinstance(_r_on, dict) else None
    # ⚠ subfolder 用宿主自己的分隔符（Windows 上是 `relay_kit\pcm`，与 SaveImage/SaveVideo 一致）
    #   ⇒ 断言前统一成 `/` 再比，免得测试被平台差异卡住。
    check("26.29 裁重叠把 PCM 边车路径回显进 ui（拼接方靠它取回，不猜文件名）",
          bool(_ent) and str(_ent[0]["filename"]).endswith(".safetensors")
          and str(_ent[0]["subfolder"]).replace("\\", "/") == "relay_kit/pcm",
          "ui=%s" % (_ent,))
    _made = os.path.join(_fp26.get_output_directory(), _ent[0]["subfolder"],
                         _ent[0]["filename"]) if _ent else ""
    try:
        if _made and os.path.isfile(_made):
            os.remove(_made)      # 单测不留垃圾在 output 里（删不掉也不该红，见 26.38）
    except OSError:
        pass
    check("26.30 pick_pcm_outputs：从 history.outputs 取回边车；视频/无关项不误收",
          [p["filename"] for p in CORE.pick_pcm_outputs({"7": {CORE.PCM_UI_KEY: _ent}})]
          == [_ent[0]["filename"]]
          and CORE.pick_pcm_outputs({"7": {"images": [{"filename": "a.mp4", "type": "output"}]}}) == []
          and CORE.pick_pcm_outputs(None) == [])
    _r_off = NODES.H3RelayTrimAV().trim(images=_img73b, trim_frames=22, fps=24.0, audio=_aud73,
                                        save_pcm=False)
    check("26.31 save_pcm=False ⇒ 不写边车，返回退化成老的四元组（旧图行为不变）",
          not isinstance(_r_off, dict) and len(_r_off) == 4,
          "%r" % (type(_r_off),))

    # —— 26.35~26.37 护栏：边车必须与**本段落盘音频同源**（音频链上还有别的处理时） ——
    #   为什么必须锁：产线接线是「裁重叠 → **音频缝** → 落盘」。音频缝若开 patch / J-cut，
    #   真进 mp4 的是**它的**输出；拿裁重叠那份边车去拼 = 绕过音频缝（J-cut 下还会错位）。
    #   护栏靠**长度**判同源，不靠接线假设。
    _side_short2 = os.path.join(_TMP26, "pcm_jcut.safetensors")
    _mk_side(_side_short2, 500.0, n=64000 - 28800)          # 模拟 J-cut：短 align(0.9s)
    _rep_jc = CORE.assemble_mp4_segments([_c1, _c2], os.path.join(_TMP26, "film_jcut.mp4"),
                                         pcm_paths=[_side_short2, None])
    check("26.35 边车比 mp4 音频短 ~0.9s（J-cut 型）⇒ 拒收、退回解码并出警告",
          _rep_jc["ok"] and _rep_jc["alignment"][0]["source"] == "aac"
          and any("拒收" in w for w in _rep_jc["warnings"]),
          "src=%s warns=%s" % (_rep_jc["alignment"][0]["source"], _rep_jc["warnings"][:1]))

    _side_good = os.path.join(_TMP26, "pcm_ok.safetensors")
    _mk_side(_side_good, 321.0)
    _rep_2c = CORE.assemble_mp4_segments([_c1, _c2], os.path.join(_TMP26, "film_2cand.mp4"),
                                         pcm_paths=[[_side_short2, _side_good], None])
    check("26.36 多候选 ⇒ 选**与落盘音频最接近**的那个（不是「先来先用」）",
          _rep_2c["ok"] and _rep_2c["alignment"][0]["source"] == "pcm"
          and _rep_2c["alignment"][0]["pcm_file"] == os.path.basename(_side_good),
          "pick=%s" % (_rep_2c["alignment"][0]["pcm_file"],))
    check("26.37 单字符串形式仍受支持（旧调用方兼容）",
          CORE.assemble_mp4_segments([_c1, _c2], os.path.join(_TMP26, "film_str.mp4"),
                                     pcm_paths=[_side_good, None])["alignment"][0]["source"]
          == "pcm")

    # —— 26.38~26.39 音频缝也落边车（它输出才是真进 mp4 的那份） ——
    _seam_cls = NODES.H3RelayAudioSeam
    _seam_aud = {"waveform": torch.zeros(1, 2, 64000), "sample_rate": 32000}
    _sr_ret = _seam_cls().seam(_seam_aud, "relay", 0, 0.0)
    _sr_ent = (_sr_ret.get("ui") or {}).get(CORE.PCM_UI_KEY) if isinstance(_sr_ret, dict) else None
    check("26.38 音频缝也回显 PCM 边车（它落的那份 = 送进 mp4 的同一份音频）",
          bool(_sr_ent) and str(_sr_ent[0]["filename"]).startswith("audio_")
          and str(_sr_ent[0]["subfolder"]).replace("\\", "/").endswith("relay"),
          "ui=%s" % (_sr_ent,))
    # ⚠ 清理要**容错**：Windows 上刚写完的文件偶发被索引/杀软短暂占用（PermissionError）。
    #   单测不该因为"删不掉临时文件"而红 —— 这不是被测行为。
    for _f in (_sr_ent or []):
        _pp = os.path.join(_fp26.get_output_directory(), _f["subfolder"], _f["filename"])
        try:
            if os.path.isfile(_pp):
                os.remove(_pp)
        except OSError:
            pass
    _raw_dump = os.path.join(_fp26.get_output_directory(), "relay_kit", "relay",
                             "audio_raw_00000.safetensors")
    try:
        if os.path.isfile(_raw_dump):
            os.remove(_raw_dump)
    except OSError:
        pass
    check("26.39 音频缝边车路径能被 pick_pcm_outputs 取回",
          [p["filename"] for p in CORE.pick_pcm_outputs({"18": {CORE.PCM_UI_KEY: _sr_ent}})]
          == [_sr_ent[0]["filename"]])

    # —— 26.41~26.42 PyAV 版本兼容（开源用户环境差异）：缺模板流 ⇒ 明确报错 + 自动退重编码 ——
    #   为什么在意：画面流拷贝靠 `add_stream_from_template`（av>=17）。别的用户机器上 av 可能更老；
    #   我们不写"手工复制参数"的未验证退路（那会产出能播但内容错的片），而是**报错 + 退回重编码**，
    #   并让四项断言把关 —— 用户看到的是"能出片 + 一句为什么"，不是崩。
    class _NoTemplateOut:
        def add_stream(self, *a, **k):      # 老 API 上没有模板流，只有这个
            raise AssertionError("不该走到 add_stream（这里只验报错分支）")

    try:
        CORE._passthrough_video_stream(_NoTemplateOut(), None, _av26)
        check("26.41 老 PyAV 缺模板流 ⇒ 抛可照做的错（指明 pip 修法）", False, "没抛")
    except RuntimeError as _e41:            # noqa: PERF203
        check("26.41 老 PyAV 缺模板流 ⇒ 抛可照做的错（指明 pip 修法）",
              "add_stream_from_template" in str(_e41) and "pip install -U" in str(_e41),
              str(_e41).splitlines()[0][:64])

    _orig_pass26 = CORE._passthrough_video_stream
    CORE._passthrough_video_stream = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("模拟：本机 PyAV 太旧（缺 add_stream_from_template）"))
    try:
        _rep_fb = CORE.assemble_mp4_segments([_c1, _c2], os.path.join(_TMP26, "film_fb.mp4"),
                                             video="auto")
    finally:
        CORE._passthrough_video_stream = _orig_pass26
    check("26.42 拷贝路不可用 ⇒ 自动退单遍重编码（仍过四项断言 + 报告写明原因）",
          _rep_fb["ok"] and _rep_fb.get("mode") == "encode"
          and any("退回单遍重编码" in ln for ln in _rep_fb["report"].splitlines()),
          "mode=%s ok=%s" % (_rep_fb.get("mode"), _rep_fb.get("ok")))

    # —— 26.43~26.44 CLI 入口（API / 无头用户的非画布路径）**真跑** ——
    #   为什么放在核心单测里：CI 只跑本文件 ⇒ 不必再给 CI 加一步；而且它与画布按钮共用同一份核心，
    #   一起跑才能保证"两条路永远一样"。CLI 是用户入口，不是内部包，**必须冒烟**。
    _spec_cli = importlib.util.spec_from_file_location(
        "concat_cli26", os.path.join(_KIT_DIR, "tools", "concat_segments.py"))
    _cli = importlib.util.module_from_spec(_spec_cli)
    _spec_cli.loader.exec_module(_cli)
    _out_cli = os.path.join(_TMP26, "cli.mp4")
    _rc_ok = _cli.main([_c1, _c2, "-o", _out_cli, "--json", "--audio", "aac192"])
    check("26.43 CLI（tools/concat_segments.py）真能出片：退出码 0 + 文件存在",
          _rc_ok == 0 and os.path.isfile(_out_cli) and os.path.getsize(_out_cli) > 1000,
          "rc=%s exists=%s" % (_rc_ok, os.path.exists(_out_cli)))
    _rc_bad = _cli.main([os.path.join(_TMP26, "nope_a.mp4"), os.path.join(_TMP26, "nope_b.mp4"),
                         "-o", os.path.join(_TMP26, "cli_bad.mp4"), "--json"])
    check("26.44 CLI 对坏输入 **非零退出**（脚本能据此判失败，不会误当成功）",
          _rc_bad == 1, "rc=%s" % (_rc_bad,))

    # —— 26.14 断言快路（包级 demux + 排序）不许说谎：与深路（逐帧解码）结论必须一致 ——
    _fast14 = CORE.assert_assembled(_out26, 144, 24.0, deep=False)
    _deep14 = CORE.assert_assembled(_out26, 144, 24.0, deep=True)
    # —— 26.15~26.17 后端拼接路由的**段发现逻辑**（纯函数级：stub 掉 server，不启 ComfyUI） ——
    #   这是整条自动拼接链里最容易悄悄错的一环（"到底拼了哪几段"），必须锁住。
    try:
        import types as _types

        class _Routes:
            def post(self, _path):
                def _deco(fn):
                    return fn
                return _deco

        class _FakePS:
            routes = _Routes()
            instance = None
            prompt_queue = None

        _FakePS.instance = _FakePS()
        _srvmod = _types.ModuleType("server")
        _srvmod.PromptServer = _FakePS
        sys.modules["server"] = _srvmod

        _spec = importlib.util.spec_from_file_location(
            "h3relay_kit_pkg", os.path.join(_KIT_DIR, "__init__.py"),
            submodule_search_locations=[_KIT_DIR])
        _pk = importlib.util.module_from_spec(_spec)
        sys.modules["h3relay_kit_pkg"] = _pk
        _spec.loader.exec_module(_pk)

        class _FakeQueue:
            def __init__(self, hist):
                self.hist = hist

            def get_history(self, prompt_id=None, max_items=None):
                if prompt_id is not None:
                    return {prompt_id: self.hist[prompt_id]} if prompt_id in self.hist else {}
                return dict(self.hist)

        def _entry(name):
            return {"prompt": [None, None, {"7": {"class_type": "H3RelayChain"}}],
                    "outputs": {
                        "31": {"images": [{"filename": name, "subfolder": "relay_kit/x",
                                           "type": "output"}]},
                        # 「裁重叠」回显的 PCM 边车（拼接路由靠它取回无损音频）
                        "18": {CORE.PCM_UI_KEY: [{"filename": name.replace(".mp4", ".safetensors"),
                                                  "subfolder": "relay_kit/pcm",
                                                  "type": "output"}]},
                    }}

        _hist = {"p1": _entry("a.mp4"), "p2": _entry("b.mp4"),
                 "p9": {"prompt": [None, None, {"5": {"class_type": "KSampler"}}],
                        "outputs": {}}}
        _q = _FakeQueue(_hist)

        _ids, _ents, _ = _pk._pick_segments(_q, ["p1", "p2"], "7", 0)
        check("26.15 按 prompt_id 精确取段（顺序保持、能落到文件路径）",
              _ids == ["p1", "p2"]
              and _pk._mp4_paths(_ents[0])[0].endswith("a.mp4"),
              "ids=%s path=%s" % (_ids, _pk._mp4_paths(_ents[0])[:1]))

        # 26.49 段文件不在盘上时，路由必须**立刻报**（不进入编码 ⇒ 不白等）
        _e_missing = {"prompt": [None, None, {"7": {"class_type": "H3RelayChain"}}],
                      "outputs": {"31": {"images": [{"filename": "gone.mp4", "subfolder": "",
                                                     "type": "output"}]}}}
        _paths = _pk._mp4_paths(_e_missing)
        check("26.49 路由能取出路径（存在性由拼之前的核对负责，避免「找不到还静默拼半条」）",
              len(_paths) == 1 and str(_paths[0]).replace("\\", "/").endswith("gone.mp4"),
              "%s" % (_paths,))

        _ids2, _e2, _n2 = _pk._pick_segments(_q, [], "7", 5)
        check("26.16 没给 id 时回扫 history：只认「图里带本 Chain 节点」的提交",
              _ids2 == ["p1", "p2"] and "2 次" in _n2, _n2)

        _ids3, _e3, _n3 = _pk._pick_segments(_q, [], "7", 0)
        check("26.17 既没 id、count 也 ≤ 0 ⇒ 明确说「不知道该拼哪几段」（不瞎拼）",
              _ids3 == [] and _e3 == [] and "不知道该拼哪几段" in _n3, _n3)

        _ids4, _e4, _n4 = _pk._pick_segments(_q, ["nope"], "7", 0)
        check("26.18 给了不存在的 prompt_id ⇒ 命中 0 并说明（重启过后端）",
              _ids4 == [] and "0/1 段命中" in _n4, _n4)

        # 26.32~26.34 音轨档映射与「每段边车」的取回（纯函数级）
        check("26.32 音轨档映射：默认 aac 256k；192k 可覆盖；无损档 = PCM f32（无码率参数）",
              _pk.AUDIO_OUT[_pk._DEFAULT_AUDIO_OUT] == ("aac", "256k")
              and _pk.AUDIO_OUT["aac_192k"] == ("aac", "192k")
              and _pk.AUDIO_OUT["pcm_lossless"][0] in CORE.LOSSLESS_AUDIO_CODECS,
              "AUDIO_OUT=%s" % (_pk.AUDIO_OUT,))
        check("26.33 路由能从 history 取回**该段的 PCM 边车**候选（不猜文件名）",
              [str(x).replace("\\", "/") for x in _pk._pcm_candidates(_e2[0])]
              == ["I:/ComfyUI/output/relay_kit/pcm/a.safetensors"],
              "cands=%s" % (_pk._pcm_candidates(_e2[0]),))
        check("26.34 老提交没有边车 ⇒ 候选为空（拼接自动退回 mp4 解码，不报错）",
              _pk._pcm_candidates(_hist["p9"]) == [] and _pk._pcm_candidates({}) == [])

        # 26.40 音频链上有**多个**本包音频节点时：候选要按「谁更靠近落盘」排
        #   （产线接线 = 裁重叠 → 音频缝 → 落盘 ⇒ 音频缝的输出优先）
        _e_multi = {
            "prompt": [None, None, {
                "13": {"class_type": "H3RelayTrimAV"},
                "18": {"class_type": "H3RelayAudioSeam"},
            }],
            "outputs": {
                "13": {CORE.PCM_UI_KEY: [{"filename": "seg_00001_.safetensors",
                                          "subfolder": "relay_kit/pcm", "type": "output"}]},
                "18": {CORE.PCM_UI_KEY: [{"filename": "audio_00001.safetensors",
                                          "subfolder": "relay_kit/relay", "type": "output"}]},
            },
        }
        check("26.46 路由取路径优先用节点自报 abs_path，缺了才按 type+subfolder 拼",
              _pk._abs_of({"filename": "a.mp4", "subfolder": "", "type": "output",
                           "abs_path": r"I://coffee_short//output//a.mp4"})
              == r"I://coffee_short//output//a.mp4"
              and _pk._abs_of({"filename": "b.mp4", "subfolder": "relay_kit/x",
                               "type": "output"}).replace("\\", "/")
              .endswith("/output/relay_kit/x/b.mp4"),
              "abs=%s" % (_pk._abs_of({"filename": "b.mp4", "subfolder": "relay_kit/x",
                                       "type": "output"}),))

        _cands = _pk._pcm_candidates(_e_multi)
        check("26.40 多候选按音频链排序：音频缝（链后）排在裁重叠之前",
              len(_cands) == 2 and _cands[0].replace("\\", "/").endswith("relay/audio_00001.safetensors")
              and _cands[1].replace("\\", "/").endswith("pcm/seg_00001_.safetensors"),
              "cands=%s" % (_cands,))
    except Exception as _e26:  # noqa: BLE001
        check("26.15 路由发现逻辑可离线加载（stub server）", False, repr(_e26))

    check("26.14 断言快路（包级）与深路（逐帧解码）结论一致",
          _fast14["frames"] == _deep14["frames"] == 144
          and _fast14["pts_contiguous"] and _deep14["pts_contiguous"]
          and _fast14["dts_strictly_increasing"] and _deep14["dts_strictly_increasing"],
          "fast=%d deep=%d" % (_fast14["frames"], _deep14["frames"]))

# ============================ 27 · 画质域 AV latent 分块放大 ============================
# 上游学习式 3D 放大器只吃普通 [B,C,T,H,W]（接不住 NestedTensor）且分块写死 32 帧，
# 故本包自持「块数自选 + 重叠加权拼接」。fake 放大器 = nearest ×2（逐帧独立 ⇒ 可求解析解）。

def _sr2(seg):
    return torch.nn.functional.interpolate(seg, scale_factor=(1, 2, 2), mode="nearest")


_T27, _H27, _W27 = 57, 8, 6
_v27 = (torch.linspace(0.0, 1.0, _T27).view(1, 1, _T27, 1, 1)
        * torch.linspace(0.0, 1.0, _W27 * _H27).view(1, 1, 1, _H27, _W27)).to(torch.float32)

_p1 = CORE.upscale_chunk_plan(_T27, chunks=1)
_p2 = CORE.upscale_chunk_plan(_T27, chunks=2)
_p9 = CORE.upscale_chunk_plan(_T27, chunks=9, overlap=0)   # 9 块要 overlap=0 才合规（每块 7 帧 < 2*5+1）
check("27.1 块数→块长：chunks=1→1 块；=2→29 帧/2 块；=9→7 帧/9 块（ceil 不多出空块）",
      (_p1["n_chunks"], _p1["chunk_frames"]) == (1, _T27)
      and (_p2["n_chunks"], _p2["chunk_frames"]) == (2, 29)
      and _p9["n_chunks"] == 9 and _p9["chunk_frames"] == 7,
      "p1=%s p2=%s p9=%s/%s" % (_p1["n_chunks"], _p2["chunk_frames"], _p9["n_chunks"], _p9["chunk_frames"]))

check("27.2 spans 无缝覆盖：首块从 0 起、末块到 T 止、相邻块的 out_start == 前块 out_end 之前（有重叠）或 == 前块 out_end（overlap=0）",
      _p2["spans"][0]["out_start"] == 0 and _p2["spans"][-1]["out_end"] == _T27
      and all(b["out_start"] <= a["out_end"] for a, b in zip(_p2["spans"], _p2["spans"][1:])))

_ov0 = CORE.upscale_chunk_plan(_T27, chunks=4, overlap=0)
check("27.3 overlap=0 ⇒ 各块正好平铺（Σ 帧数 == T，blend 全 0）",
      sum(s["out_end"] - s["out_start"] for s in _ov0["spans"]) == _T27
      and all(s["blend_head"] == 0 and s["blend_tail"] == 0 for s in _ov0["spans"]))

check("27.4 合法最大块数 = T // (2·overlap+1)：T=57 overlap=5 → 5",
      CORE.max_upscale_chunks(57, 5) == 5 and CORE.max_upscale_chunks(57, 0) == 57)

expect_raise("27.5 超上限的块数必须 raise 并给出合法上限",
             lambda: CORE.upscale_chunk_plan(57, chunks=6, overlap=5), "最大合法块数 = 5")
expect_raise("27.6 chunks<1 / overlap<0 / total<1 一律 raise（不静默改参数）",
             lambda: CORE.upscale_chunk_plan(57, chunks=0))

_o1 = CORE.temporal_tile_upscale(_v27, _sr2, _p1)
_whole = _sr2(_v27)
check("27.7 单块 == 整段直接放大（恒等，逐位）", torch.equal(_o1, _whole),
      "max|d|=%.3e" % float((_o1 - _whole).abs().max()))

_chunked = CORE.temporal_tile_upscale(_v27, _sr2, CORE.upscale_chunk_plan(_T27, chunks=4))
check("27.8 分 4 块 == 整段一次过（nearest 逐帧独立 ⇒ 线性加权必须精确复原）",
      _chunked.shape == _whole.shape
      and float((_chunked - _whole).abs().max()) < 1e-4,
      "shape=%s max|d|=%.3e" % (tuple(_chunked.shape), float((_chunked - _whole).abs().max())))

check("27.9 只放大空间、时间维不动：T 保持 57、H/W 翻倍",
      tuple(_chunked.shape) == (1, 1, _T27, _H27 * 2, _W27 * 2), str(tuple(_chunked.shape)))

expect_raise("27.10 放大器改变帧数 ⇒ 当场 raise（否则拼接静默错位）",
             lambda: CORE.temporal_tile_upscale(
                 _v27, lambda s: s[:, :, :-1], CORE.upscale_chunk_plan(_T27, chunks=3)),
             "改变了帧数")
expect_raise("27.11 plan 与 latent 帧数不符 ⇒ raise（不许拿错尺子拼）",
             lambda: CORE.temporal_tile_upscale(_v27[:, :, :20], _sr2, _p2), "与 latent 的")
expect_raise("27.12 秩不对（3D）⇒ raise",
             lambda: CORE.temporal_tile_upscale(_v27.squeeze(0), _sr2,
                                                 CORE.upscale_chunk_plan(_T27, chunks=2)))

_s4 = _v27[:, :, 0]                                  # [B,C,H,W] 单帧
check("27.15 4D 单帧进 → 4D 出（与上游同口径），且空间确实翻倍",
      tuple(_s4.shape) == (1, 1, _H27, _W27)
      and tuple(CORE.temporal_tile_upscale(_s4, _sr2,
              CORE.upscale_chunk_plan(1, chunks=1, overlap=0)).shape) == (1, 1, _H27 * 2, _W27 * 2))

check("27.13 上游口径常量锁死（换实现不许换接缝）：overlap=5(=temporal_kernel) / 默认块长=32",
      CORE.UPSCALE_OVERLAP_DEFAULT == 5 and CORE.UPSCALE_CHUNK_FRAMES_DEFAULT == 32)

_r27 = CORE.upscale_chunk_plan(_T27, chunks=3)
check("27.14 plan 回报字段齐备（给用户核数值：requested/实际块数/块长/overlap）",
      all(k in _r27 for k in ("requested_chunks", "n_chunks", "chunk_frames", "overlap", "spans"))
      and _r27["requested_chunks"] == 3 and _r27["n_chunks"] == 3)


# --- 适配层本身：注入假上游类，验拆包 / 回包 / 音频守恒 / 报错文案 ---
#     ⚠ 不打 ComfyUI 注册表（测试环境里 `import nodes` 会绑到本包自己的 nodes.py，
#        正是 `_comfy_registry()` 要防的影子情形）⇒ 直接换掉本包那个接缝函数。


class _FakeUp:
    """替身上游放大节点：只做 nearest ×2（时间维不动），并记录被怎么调用。"""
    calls = []

    @classmethod
    def execute(cls, latent, model_name, mode, align, enable_temporal_chunking,
                force_unload, device, precision):
        cls.calls.append(dict(model_name=model_name, mode=mode, align=align,
                              chunking=enable_temporal_chunking, device=device))
        s = latent["samples"]
        return ({"samples": torch.nn.functional.interpolate(s, scale_factor=(1, 2, 2), mode="nearest")},)


_REG = {"MinimaxH3LatentUpscaler3D": _FakeUp}
_ORIG_REGFN = NODES._comfy_registry
NODES._comfy_registry = lambda: _REG
try:
    _lat27 = av_latent(24, a_ticks=16, seed=7, w=6, h=8)
    _a0 = CORE.audio_from_latent(_lat27).clone()
    _FakeUp.calls.clear()
    _out27, _rep27 = NODES.H3RelayLatentUpscale().upscale(
        _lat27, "fake_h3_3d.safetensors", "scale by multiplier", 2.0, 1280, 704, 1.0,
        chunks=2, overlap=5, align=32, precision="fp32", device="cpu", force_unload=False)
    _v27b = CORE.video_from_latent(_out27)
    _a27b = CORE.audio_from_latent(_out27)
    check("27.16 适配器：拆包→放大→回包双流，音频**逐位不变**、T 不变、宽高翻倍",
          torch.equal(_a27b, _a0) and tuple(_v27b.shape) == (1, 4, 24, 16, 12),
          "video=%s audio=%s" % (tuple(_v27b.shape), tuple(_a27b.shape)))
    check("27.17 分块由本包做 ⇒ 传上游必须 enable_temporal_chunking=False（调了 2 次 = 2 块）",
          len(_FakeUp.calls) == 2 and all(c["chunking"] is False for c in _FakeUp.calls)
          and _FakeUp.calls[0]["mode"] == {"mode": "scale by multiplier", "scale": 2.0}
          and _FakeUp.calls[0]["model_name"] == "fake_h3_3d.safetensors",
          str(_FakeUp.calls))
    check("27.18 report 数字自证（请求/实际块数、T、音频形状都在）",
          "块数 请求2→实际2" in _rep27 and "T=24" in _rep27 and "(1, 2, 2, 16)" in _rep27,
          _rep27[:120])
    expect_raise("27.19 非法块数 ⇒ raise 且报错给出最大合法块数（绝不偷改参数）",
                 lambda: NODES.H3RelayLatentUpscale().upscale(
                     _lat27, "fake", "scale by multiplier", 2.0, 1280, 704, 1.0,
                     chunks=5, overlap=5, align=32, precision="fp32", device="cpu", force_unload=False),
                 "最大合法块数")
    NODES._comfy_registry = lambda: {}
    expect_raise("27.20 未装上游 ⇒ 报错把作者仓库与 HF 权重地址都写出来（不静默、不假成功）",
                 lambda: NODES.H3RelayLatentUpscale().upscale(
                     _lat27, "fake", "scale by multiplier", 2.0, 1280, 704, 1.0,
                     chunks=1, overlap=5, align=32, precision="fp32", device="cpu", force_unload=False),
                 "huggingface.co/LBH-123-AI")
finally:
    NODES._comfy_registry = _ORIG_REGFN


print()
print("=" * 78)
print("结果：通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("   -", f)
print("=" * 78)
# 脚本式退出码：失败 1 / 成功 0。
# ⚠ 在 pytest 下必须跳过退出：模块级 SystemExit 会让 pytest 报 INTERNALERROR 整个崩掉
# （实测：`COMFYUI_PATH=... pytest tests/` 崩在 `SystemExit: 0`——连全绿都崩）。
if "pytest" not in sys.modules:
    sys.exit(1 if FAIL else 0)
