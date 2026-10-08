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
import types

# 目录名含连字符，不能直接当包名 → 伪造一个包壳再按文件加载 nodes.py
_KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg = types.ModuleType("h3latentrelay")
_pkg.__path__ = [_KIT]
sys.modules["h3latentrelay"] = _pkg
_spec = importlib.util.spec_from_file_location("h3latentrelay.nodes", os.path.join(_KIT, "nodes.py"))
NODES = importlib.util.module_from_spec(_spec)
sys.modules["h3latentrelay.nodes"] = NODES
_spec.loader.exec_module(NODES)


def unwrap(res):
    """节点返回 `{"ui": …, "result": …}` 时取 result（与宿主行为一致）。

    0.6.7 起「Trim AV」与「Audio Seam」用 ui 键回显 PCM 边车路径 ⇒ 宿主把 ui 收进
    history.outputs，连线数据仍是 result。**直接调节点函数的测试必须过这一层**，
    否则 `a, b, c = node.fn(...)` 会 unpack 到 dict 的键上（踩过：22.13 报 "expected 3, got 2"）。
    """
    return res["result"] if isinstance(res, dict) and "result" in res else res


def arity(cls, kw):
    out = unwrap(getattr(cls(), cls.FUNCTION)(**kw))
    return len(out) == len(cls.RETURN_TYPES), out


ok, out = arity(NODES.H3RelayCopyBridge,
            {"latent": cur, "context_latent": prev, "context_frames": 22, "conditioning": cond})
check("CopyBridge（复合桥）续接分支返回 4 路（含第 4 路 conditioning）", ok, "实得 %d" % len(out))
check("续接分支 trim_frames 输出 > 0（供裁剪节点）", int(out[2]) > 0, "得到 %r" % (out[2],))
check("复合桥第 4 路 conditioning 已注入（非 None）", out[3] is not None, "得到 %r" % (out[3],))

ok, out = arity(NODES.H3RelayCopyBridge,
            {"latent": cur, "context_latent": None, "context_frames": 22, "stage_index": 0, "conditioning": cond})
check("CopyBridge 直通分支（stage 0 无来源）返回 4 路", ok, "实得 %d" % len(out))
check("直通分支 trim_frames 输出 = 0（首段不裁）", int(out[2]) == 0, "得到 %r" % (out[2],))

img73 = torch.zeros(73, 2, 2, 3)
ok, out = arity(NODES.H3RelayTrimAV, {"images": img73, "trim_frames": 0, "fps": 24.0, "audio": None})
check("TrimAV trim=0 分支返回 3 路", ok, "实得 %d" % len(out))
ok, out = arity(NODES.H3RelayTrimAV, {"images": img73, "trim_frames": 22, "fps": 24.0, "audio": aud})
check("TrimAV 裁剪分支返回 3 路", ok, "实得 %d" % len(out))
check("裁剪分支画面 73 → 51 帧", int(out[0].shape[0]) == 51, "得到 %d" % int(out[0].shape[0]))
check("裁剪分支音频与画面同裁（68000 点）",
      int(out[1]["waveform"].shape[-1]) == 68000, "得到 %d" % int(out[1]["waveform"].shape[-1]))

ok, out = arity(NODES.H3RelayLatentSave,
                {"latent": prev, "run_id": "unittest_arity", "stage_index": 0, "note": "arity test"})
check("LatentSave 返回 2 路", ok, "实得 %d" % len(out))
ok, out = arity(NODES.H3RelayLatentLoad,
                {"run_id": "unittest_arity", "stage_index": 1, "explicit_path": ""})
check("LatentLoad 返回 2 路（stage_index=本段号，读的是上一段）", ok, "实得 %d" % len(out))
check("LatentLoad 读回上一段 latent（192 帧 @768x448）",
      CORE.pixel_frames(int(out[0]["samples"].tensors[0].shape[2])) == 192)

