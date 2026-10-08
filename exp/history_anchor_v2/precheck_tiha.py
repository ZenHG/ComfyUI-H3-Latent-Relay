# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""E1' 提交前预检 —— **零 GPU、只读盘、秒级**。

为什么需要它（2026-09-28 实测事故）：

    `spatial_scale=2` 在 960×544 下非法（latent 34×60 → 降采样得 17×30，17 为奇数，
    触发宿主 `patchify_video(patch_size=(1,2,2))` 的 reshape 崩溃）。
    这个错误 **100% 是静态可判定的**（读配置 + 读 latent 尺寸 + 调 max_feasible_scale），
    却安排在**动态执行路径**里 ⇒ 代价 = 提交 + 模型加载 + 崩溃 + 3 次重试 + 一次 ComfyUI 重启。

    本脚本把这类错误前移到提交前 1 秒。

覆盖的失败面：
  1. `_tiha.json` 不存在 / 字段缺失 / 类型错 / 越界
  2. **维度非法**（提前给出「最大可行 spatial_scale」，而不是等宿主 reshape 崩）
  3. 历史段不齐（缺落盘文件）
  4. 与外观锚撞源
  5. 实跑一遍 `build_history_anchor_refs`，报出会出几块 / 每块尺寸 / token 增量 / 共识分

跑法::

    python exp/history_anchor_v2/precheck_tiha.py --run <run_id> --stage <段号>

退出码（**三个，不是两个** —— 仓库纪律「没报错不是没问题的证据」）::

    0 = 通过（真的审了，且会出块）
    2 = **不要提交**（审了，发现问题）
    3 = **未审**（没有可审的内容：无配置 / depth<=0 / 历史段 <2）
        ⚠️ 未审**不等于**通过 —— 调用方必须显式区分，别把 3 当 0。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys

import torch

# 退出码：0 通过 / 2 不要提交 / 3 未审（本文档 §3.2「闸读不到输入时必须喊，绝不沉默通过」）
EXIT_OK, EXIT_FAIL, EXIT_SKIP = 0, 2, 3

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import history_anchor as HA

try:                                            # 只在没装 safetensors 时才需要
    from safetensors.torch import load_file
except Exception:
    load_file = None


def _default_relay_root() -> str:
    """推导默认的 relay_kit 落盘目录 —— **不写死本机路径**。

    优先级：``RELAY_KIT_ROOT`` > ``COMFYUI_PATH`` > 由本文件位置反推。

    🔴 反推**只在"从部署副本运行"时成立**（副本位于 ``<comfy>/custom_nodes/<kit>/…``）；
    从本仓源码目录运行时会推出一个**错路径**（指向 ``<包名>\\output\\relay_kit``）。
    ⇒ 因此这里**校验推导结果**：ComfyUI 根必须含 ``comfy/`` 子包与 ``output/``，
    否则**硬退出并叫人设 `COMFYUI_PATH`** —— 绝不拿一个明显不对的路径继续往下走
    （《规范》§3.2「闸读不到输入时必须喊，绝不沉默通过」）。
    """
    env = os.environ.get("RELAY_KIT_ROOT")
    if env:
        return env
    comfy = os.environ.get("COMFYUI_PATH")
    if not comfy:
        # <comfy>/custom_nodes/<kit>/exp/history_anchor_v2/ → 上溯 3 层得 <comfy>
        kit = os.path.dirname(os.path.dirname(_HERE))
        comfy = os.path.dirname(os.path.dirname(kit))
        if not (os.path.isdir(os.path.join(comfy, "comfy"))
                and os.path.isdir(os.path.join(comfy, "output"))):
            raise Fail(
                "推导出的 ComfyUI 根看起来不对：%s\n"
                "        （本脚本位于 %s ⇒ 反推只在「从部署副本运行」时成立）\n"
                "        ⇒ 请显式指定：设 COMFYUI_PATH=<你的 ComfyUI 根目录>，"
                "或用 --root <relay_kit 目录>。"
                % (comfy, _HERE))
    return os.path.join(comfy, "output", "relay_kit")

# 配置文件里允许出现的键 —— **不在这里重定义**（2026-10-02 合并）。
# 唯一真相源 = `history_anchor.KNOWN_CONFIG_KEYS`：预检的职责就是"预测生产行为"，
# 白名单一旦与生产侧各写一份，预测就会失真（《规范》§二·D「孪生体」）。本脚本已 import HA。
_KEYS = HA.KNOWN_CONFIG_KEYS


class Fail(Exception):
    """预检失败（收集所有问题后一次性抛出，避免"修一个报一个"）。"""


def load_video(path: str) -> torch.Tensor:
    """读 relay_kit 落盘的 AV latent，取 video 流 ``[1,C,T,H,W]``。

    直读 ``streams.video``（**不需要 comfy / relay_core**，保证预检脚本零重依赖）。
    """
    if load_file is None:
        raise Fail("未安装 safetensors，无法预检落盘 latent。")
    if not os.path.isfile(path):
        raise Fail("落盘文件不存在：%s" % path)
    d = load_file(path)
    for k in ("streams.video", "video"):
        if k in d and d[k].dim() == 5:
            return d[k]
    raise Fail("%s 里找不到 5 维 video 流（键：%s）" % (path, sorted(d.keys())))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=None,
                    help="relay_kit 落盘目录。缺省按 RELAY_KIT_ROOT → COMFYUI_PATH → "
                         "由脚本位置反推（不写死本机路径）。")
    ap.add_argument("--run", required=True)
    ap.add_argument("--stage", type=int, required=True, help="本段段号（0 起）")
    # ⚠️ 默认必须与母版 `#961 CopyBridge.ref_anchor_stage` 的默认一致（= **-1**）。
    #    2026-10-02 实测踩到：默认 0 会把 stage0 当成「与外观锚同源」剔掉 ⇒ stage3 只剩 1 段历史
    #    ⇒ 误报「可用历史段 < 2 ⇒ E1' 不出块，行为等同关闭」；而真跑其实出 **2 块**
    #    （实跑证据：`m1wf5` / `m1wf22` 两臂各出 2 块，+8.5% / +29.8% token）。
    #    ⇒ 预检的默认值与生产默认值不一致 = 预检自己制造假警报（§二·D 孪生体同源问题）。
    ap.add_argument("--anchor-stage", type=int, default=-1)
    args = ap.parse_args()
    if not args.root:
        try:
            args.root = _default_relay_root()
        except Fail as exc:
            print("[FAIL] 无法确定 relay_kit 目录（见下）—— **不要提交**，先按提示指路。")
            print("       %s" % exc)
            return EXIT_FAIL

    problems, notes = [], []
    print("=" * 76)
    print("E1' 提交前预检 |  run=%s  stage=%d  anchor=stage%d" % (args.run, args.stage, args.anchor_stage))
    print("=" * 76)

    # ---------- 1) 配置 ----------
    cfg_path = os.path.join(args.root, args.run, "_tiha.json")
    if not os.path.isfile(cfg_path):
        # 不是错误：没配置文件 = E1' 关，主干逐位不变
        print("[未审] 无 %s ⇒ E1' 关闭（主干逐位不变），本期无内容可审。" % cfg_path)
        print("       ⚠️ 未审 ≠ 通过。退出码 3，别当 0 用。")
        return EXIT_SKIP
    print("[1] 配置：%s" % cfg_path)
    try:
        raw = json.load(io.open(cfg_path, encoding="utf-8"))
    except Exception as exc:
        print("[FAIL] 配置文件不是合法 JSON：%s" % exc)
        return EXIT_FAIL
    if not isinstance(raw, dict):
        print("[FAIL] 配置顶层必须是对象，得到 %s" % type(raw).__name__)
        return EXIT_FAIL
    for k, v in raw.items():
        print("      %-18s = %r%s" % (k, v, "" if k in _KEYS else "   ← 未知键（被忽略）"))

    depth = raw.get("depth", 0)
    if not isinstance(depth, int):
        problems.append("depth 必须是整数，得到 %r" % (depth,))
    elif depth <= 0:
        print("\n[未审] depth=%d ⇒ E1' 关闭，本期无内容可审。退出码 3。" % depth)
        return EXIT_SKIP
    if args.stage < 2:
        problems.append("stage=%d：历史窗口是 stage-2/-3…，stage < 2 时无历史可读" % args.stage)

    # ---------- 2) 历史段是否齐备（先判段号，再读盘）----------
    print("\n[2] 历史段（由近到远，stage-2/-3/…）")
    pairs, missing = [], []
    for k in range(2, 3 + int(depth)):
        idx = args.stage - k
        if idx < 0:
            break
        p = os.path.join(args.root, args.run, "stage_%05d.safetensors" % idx)
        if os.path.isfile(p):
            pairs.append((idx, p))
            print("      stage%-3d ✓  %s" % (idx, os.path.basename(p)))
        else:
            missing.append(idx)
            print("      stage%-3d ✗  缺落盘文件" % idx)
    if missing:
        notes.append("缺段落 %s：TIHA 只会在**已落盘**的段上算共识（缺失即少一段参照，不 raise）" % missing)

    keep, dupe = HA.filter_duplicate_sources([(i, None) for i, _ in pairs], int(args.anchor_stage))
    if dupe:
        print("      与外观锚同源 ⇒ 会剔除：%s" % dupe)
    keep_idx = [i for i, _ in keep]
    if len(keep_idx) < 2:
        print("\n[未审] 可用历史段 %d < 2（跨段共识需 ≥2 段参照）⇒ E1' 不出块，"
              "行为等同关闭。" % len(keep_idx))
        print("       ⚠️ 不会崩，但也**没审到任何东西**。退出码 3，别当 0 用。")
        return EXIT_SKIP
    print("      ⇒ 用于打分的段：%s" % keep_idx)

    # ---------- 3) 维度合法性（本次事故的那一类）----------
    print("\n[3] 维度合法性（宿主 patchify_video patch_size=(1,2,2) 要求块 h、w 均为偶数）")
    videos = []
    for idx in keep_idx:
        try:
            videos.append(load_video(os.path.join(args.root, args.run,
                                                  "stage_%05d.safetensors" % idx)))
        except Fail as exc:
            problems.append(str(exc))
    if not videos:
        print("[FAIL] 一个历史段都读不出来")
        for p in problems:
            print("       · " + p)
        return EXIT_FAIL
    lat_h, lat_w = int(videos[0].shape[3]), int(videos[0].shape[4])
    want_s = int(raw.get("spatial_scale", 1))
    max_s = HA.TIHAConfig.max_feasible_scale(lat_h, lat_w, want_s)
    print("      latent 空间尺寸 = %d(H) × %d(W)" % (lat_h, lat_w))
    print("      请求 spatial_scale = %d ⇒ 最大可行 = %d" % (want_s, max_s))
    if want_s != max_s:
        problems.append(
            "spatial_scale=%d 不可行：降采样后得 %d×%d（%s 为奇数），宿主 patchify 会 raise。"
            "最大可行 = %d。"
            % (want_s, lat_h // want_s, lat_w // want_s,
               "H" if (lat_h // want_s) % 2 else "W", max_s))
    else:
        print("      ✓ 合法")
    # 同一 run 各段尺寸应一致
    dims = {(int(v.shape[3]), int(v.shape[4])) for v in videos}
    if len(dims) > 1:
        problems.append("历史段之间 latent 空间尺寸不一致：%s（跨分辨率段不能混着当参照）" % sorted(dims))

    # ---------- 4) 实跑（真 latent，零 GPU）----------
    print("\n[4] 实跑 build_history_anchor_refs（真 latent、零 GPU）")
    # 这 4 个键在上面已**特殊处理**（depth 有默认回退 / spatial_scale 先按可行值试算）
    # ⇒ 从白名单里排除掉再**全量**灌入。原先这里是手写的 11 键清单 ——
    # 漏一个键就会"预检用默认值试算、生产用真值" ⇒ 预测失真（2026-10-02 换掉）。
    _explicit = ("depth", "window_frames", "spatial_scale", "max_refs")
    cfg = HA.TIHAConfig(
        depth=int(depth),
        window_frames=int(raw.get("window_frames", HA.TIHAConfig.window_frames)),
        spatial_scale=want_s if want_s == max_s else max_s,     # 先按可行值试算，好给出"改完长什么样"
        max_refs=int(raw.get("max_refs", depth)),
    )
    for k in HA.KNOWN_CONFIG_KEYS:
        if k in _explicit:
            continue
        if k in raw:
            setattr(cfg, k, raw[k])
    try:
        refs = HA.build_history_anchor_refs(
            videos, cfg, log=lambda m: print("      " + m),
            stage_labels=keep_idx)          # 传真实段号 ⇒ 日志与留痕直接用 stage
    except Exception as exc:
        problems.append("build_history_anchor_refs 抛异常：%s: %s"
                        % (type(exc).__name__, str(exc).splitlines()[0]))
        refs = None

    # ---------- 5) token 账 ----------
    if refs is not None:
        cur = args.stage
        base_tokens = None
        p = os.path.join(args.root, args.run, "stage_%05d.safetensors" % cur)
        if os.path.isfile(p):
            v = load_video(p)
            base_tokens = int(v.shape[2]) * int(v.shape[3]) * int(v.shape[4])
            print("\n[5] token 账（本段 video latent = %d token，%dx%d）"
                  % (base_tokens, int(v.shape[3]), int(v.shape[4])))
        else:
            print("\n[5] token 账")
            notes.append("本段 stage_%05d 尚未落盘 ⇒ token 占比无法预核（提交后自然生成）" % cur)
        add = sum(int(r["latent_t"]) * int(r["latent_h"]) * int(r["latent_w"]) for r in refs)
        print("      出块 %d 个" % len(refs))
        for r in refs:
            # 🔴 `_exp_level` 是**历史列表下标（level）**，`_exp_stage` 才是真实段号。
            #    （2026-09-28 置信度审核抓到：这里原先直接打 `stage%d % _exp_level` 冒充段号，
            #     会把 stage2 标成 "stage1"，因为 keep_idx=[3,2,1] ⇒ level1 → stage2。）
            lv = int(r.get("_exp_level", -1))
            real_stage = r.get("_exp_stage", keep_idx[lv] if 0 <= lv < len(keep_idx) else -1)
            print("        stage%-3s (level %d) @ token %d ｜ 共识分 %.4f ｜ rel %.2f ｜ "
                  "%d token / %d 帧 / %dx%d"
                  % (real_stage, lv, r.get("_exp_token", -1),
                     r.get("_exp_score", 0.0), r.get("_exp_rel", 0.0),
                     r["latent_t"], r["_exp_frames"], r["latent_h"], r["latent_w"]))
        print("      新增 token = %d%s"
              % (add, ("  = +%.1f%%" % (100.0 * add / base_tokens)) if base_tokens else ""))

    # ---------- 结论 ----------
    print("\n" + "=" * 76)
    for n in notes:
        print("[NOTE] " + n)
    if problems:
        print("[FAIL] 不要提交 —— 共 %d 项：" % len(problems))
        for p in problems:
            print("   · " + p)
        print("=" * 76)
        return EXIT_FAIL
    print("[PASS] 预检通过（已实跑验证会出块），可以提交。")
    print("=" * 76)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
