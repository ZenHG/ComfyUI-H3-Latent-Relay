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
"""

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


def arity(cls, kw):
    out = getattr(cls(), cls.FUNCTION)(**kw)
    return len(out) == len(cls.RETURN_TYPES), out


ok, out = arity(NODES.H3RelayMotionContext,
                dict(conditioning=cond, latent=cur, trim_frames=22, context_latent=None))
check("MotionContext 直通分支返回 3 路（曾漏第 3 路 → list index out of range）", ok, "实得 %d" % len(out))
check("直通分支 trim_frames 输出 = 0（首段不裁）", int(out[2]) == 0, "得到 %r" % (out[2],))

ok, out = arity(NODES.H3RelayMotionContext,
                dict(conditioning=cond, latent=cur, trim_frames=22, context_latent=prev))
check("MotionContext 续接分支返回 3 路", ok, "实得 %d" % len(out))
check("续接分支 trim_frames 输出 = 22（供裁剪节点）", int(out[2]) == 22, "得到 %r" % (out[2],))

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

ctx = NODES.H3RelayMotionContext()

# 9.1 stage_index>=1 + run_id 空 + 无 context_latent → 旧行为是静默直通（坏片），必须 raise
try:
    ctx.apply(cond, cur, trim_frames=22, context_latent=None, audio_frames=0,
              run_id="", stage_index=1)
    check("9.1 段号≥1 且无来源 → raise", False, "竟然没报错（会产出无续接的哑剧）")
except RuntimeError as e:
    check("9.1 段号≥1 且无来源 → raise", "静默直通" in str(e), str(e).split("\n")[0])
except Exception as e:  # noqa: BLE001
    check("9.1 段号≥1 且无来源 → raise", False, "抛的是 %s：%s" % (type(e).__name__, e))

# 9.2 run_id 只有空白也算空
try:
    ctx.apply(cond, cur, trim_frames=22, context_latent=None, audio_frames=0,
              run_id="   ", stage_index=3)
    check("9.2 run_id 全空白同样 raise", False, "竟然没报错")
except RuntimeError as e:
    check("9.2 run_id 全空白同样 raise", "静默直通" in str(e), str(e).split("\n")[0])

# 9.3 stage_index=0 仍应正常直通（独立段，合法）
try:
    out0 = ctx.apply(cond, cur, trim_frames=22, context_latent=None, audio_frames=0,
                     run_id="", stage_index=0)
    check("9.3 stage_index=0 仍直通不报错（独立段合法）", int(out0[2]) == 0, "trim=%r" % (out0[2],))
except Exception as e:  # noqa: BLE001
    check("9.3 stage_index=0 仍直通不报错（独立段合法）", False, "抛了 %s" % type(e).__name__)

# 9.4 手动接了 context_latent 时，段号≥1 不应被拦（高级用法）
try:
    out1 = ctx.apply(cond, cur, trim_frames=22, context_latent=prev, audio_frames=0,
                     run_id="", stage_index=1)
    check("9.4 手动接 context_latent → 段号≥1 不拦", int(out1[2]) == 22, "trim=%r" % (out1[2],))
except Exception as e:  # noqa: BLE001
    check("9.4 手动接 context_latent → 段号≥1 不拦", False, "抛了 %s：%s" % (type(e).__name__, e))

# 9.5 run_id 有值但文件不存在 → FileNotFoundError（不是静默直通）
try:
    ctx.apply(cond, cur, trim_frames=22, context_latent=None, audio_frames=0,
              run_id="unittest_no_such_run", stage_index=1)
    check("9.5 run_id 有值但无文件 → FileNotFoundError", False, "竟然没报错")
except FileNotFoundError:
    check("9.5 run_id 有值但无文件 → FileNotFoundError", True)
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
check("11.2 status 排在最后一个（旧工作流少这一格也不会让前面取值错位）",
      list(req) == ["segments"] and list(opt)[-1] == "status",
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
_opt19 = list(NODES.H3RelayTrimAV.INPUT_TYPES()["optional"])
check("19.7 新 widget 追加在 optional 末位（前缀顺序稳定）",
      _opt19[-4:] == ["match_prev", "match_prev_frames", "match_prev_gain_max",
                      "match_prev_offset_max"],
      "尾部=%s" % (_opt19[-4:],))

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

# 19.12 节点层：新 widget 追加在 H3RelayPost 的 optional **末位**（TrimAV 保持冻结）
_opt19b = list(NODES.H3RelayPost.INPUT_TYPES()["optional"])
check("19.12 新 widget 追加在 H3RelayPost optional 末位",
      _opt19b[-1] == "match_prev_stats_frames"
      and NODES.H3RelayPost.INPUT_TYPES()["optional"]["match_prev_stats_frames"][1]["default"] == 1,
      "末位=%s default=%s" % (_opt19b[-1],
                              NODES.H3RelayPost.INPUT_TYPES()["optional"]["match_prev_stats_frames"][1]["default"]))

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

# 20.5 未接 guide + 跨段项打开 → **跳过**（不抛、不崩）
_ok5, _o5 = True, None
try:
    _o5, _r5 = NODES.H3RelayPost().apply(_pin20, None, match_prev=0.5, lowfreq_pull=0.5)
except Exception as _e:                                  # noqa: BLE001
    _ok5, _r5 = False, "raise:%s" % _e
check("20.5 未接 guide + 跨段项开 → 跳过（不抛）",
      _ok5 and torch.equal(_o5, _pin20) and "跳过" in _r5, "report=%s" % _r5)

# 20.6 接了 guide → 跨段项生效（段头被改动，帧数守恒）
_o6, _r6 = NODES.H3RelayPost().apply(_pin20, _g20, match_prev=0.5)
check("20.6 接了 guide → 跨段统计匹配生效（帧数守恒）",
      int(_o6.shape[0]) == 8 and not torch.equal(_o6, _pin20), "report=%s" % _r6)

# 20.7 TrimAV 新增第 4 路输出 prev_tail（**追加在末位**，旧工作流不受影响）
check("20.7 TrimAV 第 4 路输出 prev_tail 追加在末位",
      NODES.H3RelayTrimAV.RETURN_TYPES[:3] == ("IMAGE", "AUDIO", "STRING")
      and NODES.H3RelayTrimAV.RETURN_TYPES[3] == "IMAGE"
      and NODES.H3RelayTrimAV.RETURN_NAMES[3] == "prev_tail",
      "types=%s names=%s" % (NODES.H3RelayTrimAV.RETURN_TYPES,
                             NODES.H3RelayTrimAV.RETURN_NAMES))

# 20.8 prev_tail 真的等于「钉住区最后一帧 = 上段末帧」
_seg20 = torch.rand(50, 16, 16, 3)
_out20 = NODES.H3RelayTrimAV().trim(_seg20, trim_frames=22, fps=24.0, settle_frames=0)
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
