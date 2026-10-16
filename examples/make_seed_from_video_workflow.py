# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""make_seed_from_video_workflow.py — 生成「🔗 Seed from Video 视频起段 → 往外接」演示工作流。

这张图演示两件事：**把一段已有视频变成链的第 1 段**，然后**往外接一段**，最后
**把源帧与生成段拼成一条无缝成片**（张量域，Join AV）。

官方节点（LoadVideo / GetVideoComponents / 加载器 / KSampler / VAEDecode / CreateVideo /
SaveVideo）的编码逐字照抄随包示例与宿主 schema；本包节点的槽位一律**现推** ——
以本地 `nodes.py` 的 `INPUT_TYPES()` 为唯一真相源（手写 widgets_values 会错位，
教训见 CHANGES 0.2.1）。

    python examples/make_seed_from_video_workflow.py
    python tools/check_ui_workflow.py examples/seed_from_video_ui.json   # 必须 0 问题
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_finite_run_workflow import WIDGET_TYPES, load_pack_nodes

SRC_VIDEO = "1.mp4"          # ⚠ 示例占位名：换成你自己 input/ 里的视频
RUN_ID = "h3seed_demo"
W, H, LEN, CTX, FPS = 768, 1376, 73, 22, 24.0

PROMPT = (
    "日式赛璐璐 2D 动画，暖琥珀主光，超现实咖啡厨房。\n"
    "[Shot 2] 正面中近景固定机位，白发少女把手中的咖啡袋放到木桌上，指尖捻开袋口，"
    "深褐色的咖啡豆滚落进白瓷碟，她低头看着豆子，笑意变柔。\n"
    "【本段是续接段：开头请直接接住上一段结尾的动作与机位，不要重新起手】\n"
    "【全段只用一个镜头一个机位，中途不切换机位、不改变景别、不俯拍、不重新构图】\n"
    "文字：全片画面无任何文字或符号。\n"
    "overall_soundscape: 纸袋窸窣、咖啡豆落入瓷碟的清脆颗粒声、厨房低频底噪。无音乐。"
)


def pack_node(mod, cls_name, node_id, pos, links, wv, out_links, order):
    """按 INPUT_TYPES 推导本包节点的 inputs / widgets_values（槽位规则见 finite run 生成器）。"""
    it = getattr(mod, cls_name).INPUT_TYPES()
    inputs, vals = [], []
    wv_iter = iter(wv)
    for group in ("required", "optional"):
        for name, spec in (it.get(group) or {}).items():
            t = spec[0] if isinstance(spec, tuple) else spec
            is_combo = isinstance(t, list)
            link = links.get(name)
            if link is not None and not (is_combo or t in WIDGET_TYPES):
                inputs.append({"name": name, "type": "COMBO" if is_combo else t, "link": link})
            elif is_combo or t in WIDGET_TYPES:
                # ⚠ widget 型**即使被接成输入**，也要带 `widget` 标记**并**占一位 widgets_values
                #   （前端保留原值、只是改画成输入口）—— 缺一样都会让后面的旋钮整体错位一格。
                inputs.append({"name": name, "type": "COMBO" if is_combo else t,
                               "link": link, "widget": {"name": name}})
                vals.append(next(wv_iter))
            else:
                inputs.append({"name": name, "type": t, "link": None})
    assert len(vals) == len(wv), "widget_values 给了 %d 个，schema 要 %d 个" % (len(wv), len(vals))
    names = getattr(mod, cls_name).RETURN_NAMES
    types = getattr(mod, cls_name).RETURN_TYPES
    return {
        "id": node_id, "type": cls_name, "pos": pos, "size": [340, 150], "flags": {},
        "order": order, "mode": 0, "inputs": inputs,
        "outputs": [{"name": n, "type": t, "links": out_links.get(i)}
                    for i, (n, t) in enumerate(zip(names, types, strict=False))],
        "properties": {"Node name for S&R": cls_name},
        "widgets_values": vals,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 Seed from Video 视频起段演示工作流")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "seed_from_video_ui.json"))
    a = ap.parse_args()
    mod = load_pack_nodes()

    nodes = [
        {"id": 1, "type": "UNETLoader", "pos": [0, 0], "size": [320, 60], "flags": {}, "order": 0, "mode": 0,
         "inputs": [{"name": "unet_name", "type": "COMBO", "link": None, "widget": {"name": "unet_name"}},
                    {"name": "weight_dtype", "type": "COMBO", "link": None, "widget": {"name": "weight_dtype"}}],
         "outputs": [{"name": "MODEL", "type": "MODEL", "links": [1]}],
         "properties": {"Node name for S&R": "UNETLoader"},
         "widgets_values": ["minimax_h3_hybrid_fl2va_ref2va_b25-49-int8.safetensors", "default"]},
        {"id": 2, "type": "CLIPLoader", "pos": [0, 100], "size": [320, 80], "flags": {}, "order": 1, "mode": 0,
         "inputs": [{"name": "clip_name", "type": "COMBO", "link": None, "widget": {"name": "clip_name"}},
                    {"name": "type", "type": "COMBO", "link": None, "widget": {"name": "type"}},
                    {"name": "device", "type": "COMBO", "link": None, "widget": {"name": "device"}}],
         "outputs": [{"name": "CLIP", "type": "CLIP", "links": [2]}],
         "properties": {"Node name for S&R": "CLIPLoader"},
         "widgets_values": ["qwen3vl_32b_minimax_h3_nvfp4.safetensors", "minimax", "default"]},
        {"id": 3, "type": "VAELoader", "pos": [0, 200], "size": [320, 60], "flags": {}, "order": 2, "mode": 0,
         "inputs": [{"name": "vae_name", "type": "COMBO", "link": None, "widget": {"name": "vae_name"}}],
         "outputs": [{"name": "VAE", "type": "VAE", "links": [3, 4]}],
         "properties": {"Node name for S&R": "VAELoader"},
         "widgets_values": ["minimax_h3_video_vae_int8_convrot.safetensors"]},
        {"id": 4, "type": "VAELoader", "pos": [0, 280], "size": [320, 60], "flags": {}, "order": 3, "mode": 0,
         "inputs": [{"name": "vae_name", "type": "COMBO", "link": None, "widget": {"name": "vae_name"}}],
         "outputs": [{"name": "VAE", "type": "VAE", "links": [5, 6]}],
         "properties": {"Node name for S&R": "VAELoader"},
         "widgets_values": ["minimax_h3_audio_vae_fp32.safetensors"]},
        # ⑤ 源视频 → 画面/音频（⚠ `upload` 是前端注入槽，必须命名，同 LoadImage 的处理）
        {"id": 5, "type": "LoadVideo", "pos": [0, 360], "size": [320, 100], "flags": {}, "order": 4, "mode": 0,
         "inputs": [{"name": "file", "type": "COMBO", "link": None, "widget": {"name": "file"}},
                    {"name": "upload", "type": "VIDEOUPLOAD", "link": None, "widget": {"name": "upload"}}],
         "outputs": [{"name": "VIDEO", "type": "VIDEO", "links": [7]}],
         "properties": {"Node name for S&R": "LoadVideo"},
         "widgets_values": [SRC_VIDEO, "video"],
         "widgets_values_named": {"file": SRC_VIDEO, "upload": "video"}},
        {"id": 6, "type": "GetVideoComponents", "pos": [0, 480], "size": [320, 120], "flags": {}, "order": 5, "mode": 0,
         "inputs": [{"name": "video", "type": "VIDEO", "link": 7}],
         "outputs": [{"name": "images", "type": "IMAGE", "links": [8, 9]},
                     {"name": "audio", "type": "AUDIO", "links": [10, 11]},
                     {"name": "frame_rate", "type": "FLOAT", "links": None},
                     {"name": "bit_depth", "type": "INT", "links": None},
                     {"name": "color_space", "type": "STRING", "links": None}],
         "properties": {"Node name for S&R": "GetVideoComponents"},
         "widgets_values": []},
        {"id": 7, "type": "PrimitiveStringMultiline", "pos": [0, 620], "size": [460, 240], "flags": {},
         "order": 6, "mode": 0,
         "inputs": [{"name": "value", "type": "STRING", "link": None, "widget": {"name": "value"}}],
         "outputs": [{"name": "STRING", "type": "STRING", "links": [12]}],
         "properties": {"Node name for S&R": "PrimitiveStringMultiline"},
         "widgets_values": [PROMPT]},
        {"id": 8, "type": "MiniMaxH3ImageToVideo", "pos": [380, 0], "size": [340, 180], "flags": {},
         "order": 7, "mode": 0,
         "inputs": [{"name": "clip", "type": "CLIP", "link": 2},
                    {"name": "vae", "type": "VAE", "link": 3},
                    {"name": "prompt", "type": "STRING", "link": 12, "widget": {"name": "prompt"}},
                    {"name": "width", "type": "INT", "link": None, "widget": {"name": "width"}},
                    {"name": "height", "type": "INT", "link": None, "widget": {"name": "height"}},
                    {"name": "length", "type": "INT", "link": None, "widget": {"name": "length"}}],
         "outputs": [{"name": "CONDITIONING", "type": "CONDITIONING", "links": [13, 14]},
                     {"name": "LATENT", "type": "LATENT", "links": [15]}],
         "properties": {"Node name for S&R": "MiniMaxH3ImageToVideo"},
         "widgets_values": [PROMPT, W, H, LEN]},
        # ⑨ 本包：源视频 → 第 0 段（种子段）边车
        pack_node(mod, "H3RelaySeedFromVideo", 9, [380, 200],
                  {"video": [6, 0], "video_vae": [3, 0], "audio_vae": [4, 0], "audio": [6, 1]},
                  [W, H, RUN_ID, 0, "trim", ""], {0: [16], 1: None}, 8),
        # ⑩ 桥：上一段 = 种子段（**直接接 SeedFromVideo 的输出**，保证它先执行；
        #    也可以拔掉这根线 —— 桥会按 run_id + 段号−1 自己从磁盘取，0.6.37 起）
        pack_node(mod, "H3RelayCopyBridge", 10, [760, 0],
                  {"latent": [8, 1], "context_latent": [9, 0], "conditioning": [8, 0]},
                  [CTX, "hard", 4, 0.10, True, 0.25, 0, 1.0, 0.5, 0, "smoothstep", 0.5, "sine",
                   RUN_ID, 1, -1, 5, 0.0, -1, "auto"],
                  {0: [17], 1: [18], 2: [19], 3: [20]}, 9),
        {"id": 11, "type": "ConditioningZeroOut", "pos": [760, 200], "size": [300, 60], "flags": {},
         "order": 10, "mode": 0,
         "inputs": [{"name": "conditioning", "type": "CONDITIONING", "link": 14}],
         "outputs": [{"name": "CONDITIONING", "type": "CONDITIONING", "links": [21]}],
         "properties": {"Node name for S&R": "ConditioningZeroOut"},
         "widgets_values": []},
        {"id": 12, "type": "KSampler", "pos": [1140, 0], "size": [320, 260], "flags": {}, "order": 11, "mode": 0,
         "inputs": [{"name": "model", "type": "MODEL", "link": 1},
                    {"name": "positive", "type": "CONDITIONING", "link": 20},
                    {"name": "negative", "type": "CONDITIONING", "link": 21},
                    {"name": "latent_image", "type": "LATENT", "link": 17},
                    {"name": "seed", "type": "INT", "link": None, "widget": {"name": "seed"}},
                    {"name": "steps", "type": "INT", "link": None, "widget": {"name": "steps"}},
                    {"name": "cfg", "type": "FLOAT", "link": None, "widget": {"name": "cfg"}},
                    {"name": "sampler_name", "type": "COMBO", "link": None, "widget": {"name": "sampler_name"}},
                    {"name": "scheduler", "type": "COMBO", "link": None, "widget": {"name": "scheduler"}},
                    {"name": "denoise", "type": "FLOAT", "link": None, "widget": {"name": "denoise"}}],
         "outputs": [{"name": "LATENT", "type": "LATENT", "links": [22, 23, 24]}],
         "properties": {"Node name for S&R": "KSampler"},
         "widgets_values": [88300001, "fixed", 8, 1.0, "euler", "simple", 1.0]},
        pack_node(mod, "H3RelayLatentSave", 13, [1140, 280], {"latent": [12, 0]},
                  [RUN_ID, 1, ""], {0: [25], 1: None}, 12),
        {"id": 14, "type": "VAEDecode", "pos": [1500, 0], "size": [300, 60], "flags": {}, "order": 13, "mode": 0,
         "inputs": [{"name": "samples", "type": "LATENT", "link": 22},
                    {"name": "vae", "type": "VAE", "link": 4}],
         "outputs": [{"name": "IMAGE", "type": "IMAGE", "links": [26]}],
         "properties": {"Node name for S&R": "VAEDecode"}, "widgets_values": []},
        {"id": 15, "type": "VAEDecodeAudio", "pos": [1500, 100], "size": [300, 60], "flags": {}, "order": 14,
         "mode": 0,
         "inputs": [{"name": "samples", "type": "LATENT", "link": 23},
                    {"name": "vae", "type": "VAE", "link": 6}],
         "outputs": [{"name": "AUDIO", "type": "AUDIO", "links": [27]}],
         "properties": {"Node name for S&R": "VAEDecodeAudio"}, "widgets_values": []},
        # ⑯ 裁掉续接段头部的重叠帧（trim_frames 走桥第 3 路）
        pack_node(mod, "H3RelayTrimAV", 16, [1500, 200],
                  {"images": [14, 0], "trim_frames": [10, 2], "audio": [15, 0]},
                  [0, FPS, 0, 0, 0.5, 0.0, 24, 0.0, 12, 64, 0.0, 1.5, 0.0, 9, 0.0, 0.0, 0.0,
                   12, 1.15, 0.06, RUN_ID, True, False],
                  {0: [28], 1: [29], 2: None, 3: None}, 15),
        # ⑰ 拼条：源帧 + 本段裁后 = 一条成片
        pack_node(mod, "H3RelayJoinAV", 17, [1860, 200],
                  {"images": [16, 0], "audio": [16, 1], "images_acc": [6, 0], "audio_acc": [6, 1]},
                  [], {0: [30], 1: [31], 2: None}, 16),
        {"id": 18, "type": "CreateVideo", "pos": [2220, 200], "size": [300, 100], "flags": {}, "order": 17,
         "mode": 0,
         "inputs": [{"name": "images", "type": "IMAGE", "link": 30},
                    {"name": "audio", "type": "AUDIO", "link": 31},
                    {"name": "fps", "type": "FLOAT", "link": None, "widget": {"name": "fps"}},
                    {"name": "bit_depth", "type": "COMBO", "link": None, "widget": {"name": "bit_depth"}},
                    {"name": "color_space", "type": "COMBO", "link": None, "widget": {"name": "color_space"}},
                    {"name": "codec", "type": "COMBO", "link": None, "widget": {"name": "codec"}}],
         "outputs": [{"name": "VIDEO", "type": "VIDEO", "links": [32]}],
         "properties": {"Node name for S&R": "CreateVideo"},
         "widgets_values": [FPS, "auto", "sRGB", "none"]},
        {"id": 19, "type": "SaveVideo", "pos": [2220, 340], "size": [320, 100], "flags": {}, "order": 18, "mode": 0,
         "inputs": [{"name": "video", "type": "VIDEO", "link": 32},
                    {"name": "filename_prefix", "type": "STRING", "link": None, "widget": {"name": "filename_prefix"}},
                    {"name": "format", "type": "COMFY_DYNAMICCOMBO_V3", "link": None, "widget": {"name": "format"}},
                    {"name": "format.codec", "type": "COMFY_DYNAMICCOMBO_V3", "link": None,
                     "widget": {"name": "format.codec"}},
                    {"name": "codec", "type": "COMFY_DYNAMICCOMBO_V3", "link": None, "widget": {"name": "codec"}}],
         "outputs": [{"name": "video", "type": "VIDEO", "links": []}],
         "properties": {"Node name for S&R": "SaveVideo"},
         "widgets_values": ["video/H3_seed_from_video", "auto", "auto", "auto"]},
        {"id": 20, "type": "Note", "pos": [1860, 380], "size": [440, 320], "flags": {}, "order": 19, "mode": 0,
         "inputs": [], "outputs": [], "properties": {"text": ""},
         "widgets_values": [
             "【🔗 Seed from Video · 从一段已有视频起段，再往外接】\n"
             "1. LoadVideo 选一段**已有视频**（本示例 input/1.mp4）⇒ Seed from Video 把它"
             "编码成第 0 段（种子段）的边车。\n"
             "2. 它替你挡三处坑：时序栅格（5+17k 帧，多了按帧尾裁）、画布栅格（16 的倍数，"
             "不符就居中裁切重采样）、音画等长（裁尾/补零/静音种子）。\n"
             "3. width/height 必须与「MiniMaxH3 ImageToVideo」**填同一个数**（桥拒跨分辨率）。\n"
             "4. 词按续接写：开头直接接住上一段结尾的动作与机位，别重新起手；"
             "**一段只放一个机位** —— 要换机位就放在段边界，别让模型在段中段硬切。\n"
             "5. 桥的 context_latent 这里接的是 Seed from Video 的输出（保证它先执行）；"
             "**这根线也可以拔掉** —— 桥会按 run_id + 段号−1 自己从磁盘取（0.6.37 起）。\n"
             "6. 跑完得到一条成片：源帧（39）＋ 本段裁后（73−22）＝ 90 帧 @24fps = 3.75 s。\n"
             "⚠ run_id 换片子必须换名字；同 run_id 会在 output/relay_kit/<run_id>/ 里覆盖。"
         ]},
    ]

    links = [
        [1, 1, 0, 12, 0, "MODEL"], [2, 2, 0, 8, 0, "CLIP"], [3, 3, 0, 8, 1, "VAE"],
        [4, 3, 0, 14, 1, "VAE"], [5, 4, 0, 9, 2, "VAE"], [6, 4, 0, 15, 1, "VAE"],
        [7, 5, 0, 6, 0, "VIDEO"],
        [8, 6, 0, 17, 2, "IMAGE"], [9, 6, 0, 9, 0, "IMAGE"],
        [10, 6, 1, 17, 3, "AUDIO"], [11, 6, 1, 9, 3, "AUDIO"],
        [12, 7, 0, 8, 2, "STRING"],
        [13, 8, 0, 11, 0, "CONDITIONING"], [14, 8, 0, 10, 3, "CONDITIONING"],
        [15, 8, 1, 10, 0, "LATENT"],
        [16, 9, 0, 10, 1, "LATENT"],
        [17, 10, 0, 12, 3, "LATENT"], [18, 10, 1, 13, 0, "LATENT"],
        [19, 10, 2, 16, 1, "INT"], [20, 10, 3, 12, 1, "CONDITIONING"],
        [21, 11, 0, 12, 2, "CONDITIONING"],
        [22, 12, 0, 14, 0, "LATENT"], [23, 12, 0, 15, 0, "LATENT"], [24, 12, 0, 13, 0, "LATENT"],
        [25, 13, 0, 18, 0, "VIDEO"],
        [26, 14, 0, 16, 0, "IMAGE"], [27, 15, 0, 16, 2, "AUDIO"],
        [28, 16, 0, 17, 0, "IMAGE"], [29, 16, 1, 17, 1, "AUDIO"],
        [30, 17, 0, 18, 0, "IMAGE"], [31, 17, 1, 18, 1, "AUDIO"],
        [32, 18, 0, 19, 0, "VIDEO"],
    ]

    wf = {
        "id": "h3-relay-seed-from-video", "revision": 0, "last_node_id": 20,
        "last_link_id": 32, "nodes": nodes, "links": links, "groups": [],
        "config": {}, "extra": {"ds": {"scale": 0.6, "offset": [0, 0]}}, "version": 0.4,
    }
    Path(a.out).write_text(json.dumps(wf, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已写出 %s（%d 节点 / %d 链接）" % (a.out, len(nodes), len(links)))
    print("复核：python tools/check_ui_workflow.py %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
