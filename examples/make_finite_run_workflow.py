# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""make_finite_run_workflow.py — 生成「⏩ Finite Run 无头连跑」演示工作流。

【为什么单独一个生成器（而不是并进 make_minimal_workflow.py）】
那个生成器从**正在运行的 ComfyUI** 的 `/object_info` 读官方节点 schema。本图的官方节点
（加载器 / KSamplerSelect / BasicScheduler / CreateVideo / SaveVideo）编码**逐字照抄
随包发布的两份示例**（它们本身就是服务端生成、且过过 `check_ui_workflow` 的）⇒ 不需要
起服务就能重建；本生成器只动态推导**本包节点**（⏩ Finite Run）的槽位 —— 它的参数会随
版本增长，槽位顺序必须以本地 `nodes.py` 的 `INPUT_TYPES()` 为唯一真相源
（手写 widgets_values 错位的教训见 CHANGES 0.2.1）。

【生成的是什么】
    UNETLoader / CLIPLoader / VAELoader×2 → LoadImage（首帧，可拔）
    → KSamplerSelect + BasicScheduler（采样两件套，各段共用）
    → ⏩ H3 Relay · Finite Run（内部展开 N 段：ImageToVideo → LatentLoad → CopyBridge
       → 采样 → LatentSave → 解码 → TrimAV → JoinAV）
    → CreateVideo → SaveVideo（一条成片）

【用法】
    python examples/make_finite_run_workflow.py                # 写到 examples/finite_run_ui.json
    python tools/check_ui_workflow.py examples/finite_run_ui.json   # 复核（必须 0 问题）
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import types
from pathlib import Path

WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"}

# 官方节点编码：逐字照抄 examples/minimal_relay_official.json 与
# examples/fullflow_second_pass_latent_upscale_ui.json（两份都是服务端生成、
# 过过 check_ui_workflow 的随包示例）。link 字段在下面按本图的连线重写。
PREFERRED = {
    "unet_name": "minimax_h3_hybrid_fl2va_ref2va_b25-49-int8.safetensors",
    "clip_name": "qwen3vl_32b_minimax_h3_nvfp4.safetensors",
    "clip_type": "minimax",
    "video_vae": "minimax_h3_video_vae_int8_convrot.safetensors",
    "audio_vae": "minimax_h3_audio_vae_fp32.safetensors",
    "first_frame": "DS_A1_00002_.png",     # ⚠ 示例占位名：换成你自己 input/ 里的图
}

PROMPT_BLOCK_1 = (
    "日式赛璐璐 2D 动画，暖琥珀主光，超现实咖啡厨房。\n"
    "[Shot 1] 中近景，Yuki 与 Kara 近身缠斗，一记右直拳擦过耳侧，白发随拳风甩起……\n"
    "文字：全片画面无任何文字与符号。\n"
    "overall_soundscape: 拳风破空、拳掌相交的闷响、两人急促的呼吸、厨房低频底噪。无音乐。"
)
PROMPT_BLOCK_2 = (
    "承接上一段结尾：Yuki 收拳侧步，Kara 借势后撤撞上料理台，杯碟摇晃。\n"
    "[Shot 1] 同机位同光位，动作相位严格接住上一帧，不重新起手、不切镜。\n"
    "文字：全片画面无任何文字与符号。\n"
    "overall_soundscape: 杯碟碰撞、布料摩擦、两人呼吸渐稳、厨房低频底噪。无音乐。"
)


def load_pack_nodes():
    """按文件加载本包 nodes.py（目录名含连字符，不能直接当包名）。"""
    kit = Path(__file__).resolve().parent.parent
    pkg = types.ModuleType("h3latentrelay")
    pkg.__path__ = [str(kit)]
    sys.modules["h3latentrelay"] = pkg
    spec = importlib.util.spec_from_file_location(
        "h3latentrelay.nodes", os.path.join(str(kit), "nodes.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["h3latentrelay.nodes"] = mod
    spec.loader.exec_module(mod)
    return mod


def finite_run_node(mod, node_id, links, widget_values):
    """按 INPUT_TYPES 推导 ⏩ Finite Run 的 inputs 数组与 widgets_values。

    槽位规则（与前端一致）：
      · inputs 数组 = 链接型输入在前（按声明序），widget 型在后（按声明序）；
      · widgets_values 只装 widget 型的值，顺序 = required widgets → optional widgets。
    """
    it = mod.H3RelayFiniteRun.INPUT_TYPES()
    inputs, wv = [], []
    wv_iter = iter(widget_values)
    for group in ("required", "optional"):
        for name, spec in (it.get(group) or {}).items():
            t = spec[0] if isinstance(spec, tuple) else spec
            is_combo = isinstance(t, list)          # COMBO 的 spec[0] 是候选列表
            if is_combo or t in WIDGET_TYPES:
                inputs.append({"name": name, "type": "COMBO" if is_combo else t,
                               "link": None, "widget": {"name": name}})
                wv.append(next(wv_iter))
            else:
                link = links.get(name)
                inputs.append({"name": name, "type": t, "link": link})
    assert len(wv) == len(widget_values), (
        "widget_values 给了 %d 个， schema 要 %d 个" % (len(widget_values), len(wv)))
    return {
        "id": node_id, "type": "H3RelayFiniteRun",
        "pos": [980, 40], "size": [340, 160], "flags": {}, "order": 8, "mode": 0,
        "inputs": inputs,
        "outputs": [
            {"name": "last_latent", "type": "LATENT", "links": []},
            {"name": "images", "type": "IMAGE", "links": [40]},
            {"name": "audio", "type": "AUDIO", "links": [41]},
            {"name": "report", "type": "STRING", "links": None},
        ],
        "properties": {"Node name for S&R": "H3RelayFiniteRun"},
        "widgets_values": wv,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 Finite Run 无头连跑演示工作流")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "finite_run_ui.json"))
    a = ap.parse_args()

    mod = load_pack_nodes()

    nodes = [
        {"id": 1, "type": "UNETLoader", "pos": [0, 0], "size": [320, 60], "flags": {},
         "order": 0, "mode": 0,
         "inputs": [{"name": "unet_name", "type": "COMBO", "link": None,
                     "widget": {"name": "unet_name"}},
                    {"name": "weight_dtype", "type": "COMBO", "link": None,
                     "widget": {"name": "weight_dtype"}}],
         "outputs": [{"name": "MODEL", "type": "MODEL", "links": [1]}],
         "properties": {"Node name for S&R": "UNETLoader"},
         "widgets_values": [PREFERRED["unet_name"], "default"]},
        {"id": 2, "type": "CLIPLoader", "pos": [0, 100], "size": [320, 80], "flags": {},
         "order": 1, "mode": 0,
         "inputs": [{"name": "clip_name", "type": "COMBO", "link": None,
                     "widget": {"name": "clip_name"}},
                    {"name": "type", "type": "COMBO", "link": None,
                     "widget": {"name": "type"}},
                    {"name": "device", "type": "COMBO", "link": None,
                     "widget": {"name": "device"}}],
         "outputs": [{"name": "CLIP", "type": "CLIP", "links": [2]}],
         "properties": {"Node name for S&R": "CLIPLoader"},
         "widgets_values": [PREFERRED["clip_name"], PREFERRED["clip_type"], "default"]},
        {"id": 3, "type": "VAELoader", "pos": [0, 200], "size": [320, 60], "flags": {},
         "order": 2, "mode": 0,
         "inputs": [{"name": "vae_name", "type": "COMBO", "link": None,
                     "widget": {"name": "vae_name"}}],
         "outputs": [{"name": "VAE", "type": "VAE", "links": [3]}],
         "properties": {"Node name for S&R": "VAELoader"},
         "widgets_values": [PREFERRED["video_vae"]]},
        {"id": 4, "type": "VAELoader", "pos": [0, 280], "size": [320, 60], "flags": {},
         "order": 3, "mode": 0,
         "inputs": [{"name": "vae_name", "type": "COMBO", "link": None,
                     "widget": {"name": "vae_name"}}],
         "outputs": [{"name": "VAE", "type": "VAE", "links": [4]}],
         "properties": {"Node name for S&R": "VAELoader"},
         "widgets_values": [PREFERRED["audio_vae"]]},
        {"id": 5, "type": "LoadImage", "pos": [0, 360], "size": [320, 120], "flags": {},
         "order": 4, "mode": 0,
         "inputs": [{"name": "image", "type": "COMBO", "link": None,
                     "widget": {"name": "image"}},
                    {"name": "upload", "type": "IMAGEUPLOAD", "link": None,
                     "widget": {"name": "upload"}}],
         "outputs": [{"name": "IMAGE", "type": "IMAGE", "links": [5]},
                     {"name": "MASK", "type": "MASK", "links": None}],
         "properties": {"Node name for S&R": "LoadImage"},
         "widgets_values": [PREFERRED["first_frame"], "image"],
         # `upload` 是前端注入的槽（后端 schema 无此输入）⇒ 必须给它命名，
         # 否则 check_ui_workflow 的「尾部多一项」判据判红（与随包 fullflow 示例同款处理）。
         "widgets_values_named": {"image": PREFERRED["first_frame"], "upload": "image"}},
        {"id": 6, "type": "KSamplerSelect", "pos": [380, 0], "size": [300, 60],
         "flags": {}, "order": 5, "mode": 0,
         "inputs": [{"name": "sampler_name", "type": "COMBO", "link": None,
                     "widget": {"name": "sampler_name"}}],
         "outputs": [{"name": "SAMPLER", "type": "SAMPLER", "links": [6]}],
         "properties": {"Node name for S&R": "KSamplerSelect"},
         "widgets_values": ["euler"]},
        {"id": 7, "type": "BasicScheduler", "pos": [380, 100], "size": [300, 80],
         "flags": {}, "order": 6, "mode": 0,
         "inputs": [{"name": "model", "type": "MODEL", "link": 1},
                    {"name": "scheduler", "type": "COMBO", "link": None,
                     "widget": {"name": "scheduler"}},
                    {"name": "steps", "type": "INT", "link": None,
                     "widget": {"name": "steps"}},
                    {"name": "denoise", "type": "FLOAT", "link": None,
                     "widget": {"name": "denoise"}}],
         "outputs": [{"name": "SIGMAS", "type": "SIGMAS", "links": [7]}],
         "properties": {"Node name for S&R": "BasicScheduler"},
         "widgets_values": ["simple", 6, 1]},
    ]

    nodes.append(finite_run_node(
        mod, 8,
        links={"model": 1, "clip": 2, "vae": 3, "audio_vae": 4,
               "sampler": 6, "sigmas": 7, "image": 5},
        widget_values=[
            "（示例见 prompts 格：两段词用单独一行 --- 分开）",  # prompt（本示例以 prompts 为准）
            2,                # segments
            88300001,         # seed
            "relay_demo",     # run_id
            448, 768, 73,     # width / height / length
            22,               # context_frames
            "hard",           # mask_mode
            True,             # pin_audio
            PROMPT_BLOCK_1 + "\n---\n" + PROMPT_BLOCK_2,   # prompts（可选）
            "",               # note（可选）
        ]))

    nodes.extend([
        {"id": 9, "type": "CreateVideo", "pos": [1380, 40], "size": [320, 80],
         "flags": {}, "order": 9, "mode": 0,
         "inputs": [{"name": "images", "type": "IMAGE", "link": 40},
                    {"name": "audio", "type": "AUDIO", "link": 41},
                    {"name": "fps", "type": "FLOAT", "link": None,
                     "widget": {"name": "fps"}},
                    {"name": "bit_depth", "type": "COMBO", "link": None,
                     "widget": {"name": "bit_depth"}},
                    {"name": "color_space", "type": "COMBO", "link": None,
                     "widget": {"name": "color_space"}},
                    {"name": "codec", "type": "COMBO", "link": None,
                     "widget": {"name": "codec"}}],
         "outputs": [{"name": "VIDEO", "type": "VIDEO", "links": [42]}],
         "properties": {"Node name for S&R": "CreateVideo"},
         "widgets_values": [24.0, "auto", "sRGB", "none"]},
        {"id": 10, "type": "SaveVideo", "pos": [1740, 40], "size": [320, 100],
         "flags": {}, "order": 10, "mode": 0,
         "inputs": [{"name": "video", "type": "VIDEO", "link": 42},
                    {"name": "filename_prefix", "type": "STRING", "link": None,
                     "widget": {"name": "filename_prefix"}},
                    {"name": "format", "type": "COMFY_DYNAMICCOMBO_V3", "link": None,
                     "widget": {"name": "format"}},
                    {"name": "format.codec", "type": "COMFY_DYNAMICCOMBO_V3", "link": None,
                     "widget": {"name": "format.codec"}},
                    {"name": "codec", "type": "COMFY_DYNAMICCOMBO_V3", "link": None,
                     "widget": {"name": "codec"}}],
         "outputs": [{"name": "video", "type": "VIDEO", "links": []}],
         "properties": {"Node name for S&R": "SaveVideo"},
         "widgets_values": ["video/H3_finite_run", "auto", "auto", "auto"]},
        {"id": 11, "type": "Note", "pos": [980, 260], "size": [420, 320], "flags": {},
         "order": 11, "mode": 0, "inputs": [], "outputs": [],
         "properties": {"text": ""},
         "widgets_values": [
             "【⏩ Finite Run · 一次提交跑完 N 段】\n"
             "1. 填 prompts：两段词用单独一行 --- 分开（第 1 块喂第 1 段）；\n"
             "   只跑一种内容就留空 prompts、把词填进 prompt 格。\n"
             "2. segments = 要跑的段数（本示例 2）；run_id 必须填（各段中间文件靠它定位）。\n"
             "3. 首帧图可拔（不接 = 纯文生成视频）；各段共用同一张图，与画布连跑同语义。\n"
             "4. 采样两件套（KSamplerSelect + BasicScheduler）各段共用同一个。\n"
             "5. 跑完得到：末段 latent（可接下游）+ 拼成一条的画面/音频 + report。\n"
             "   各段中间文件在 output/relay_kit/<run_id>/stage_NNNNN.safetensors。\n"
             "⚠ 合并输出驻留内存：段数多 / 分辨率高时先小分辨率试两段。\n"
             "⚠ 不含音频缝补丁与画质域后处理（要那些请用画布连跑逐段接）。"
         ]},
    ])

    links = [
        [1, 1, 0, 8, 0, "MODEL"],
        [2, 2, 0, 8, 1, "CLIP"],
        [3, 3, 0, 8, 2, "VAE"],
        [4, 4, 0, 8, 3, "VAE"],
        [5, 5, 0, 8, 6, "IMAGE"],
        [6, 6, 0, 8, 4, "SAMPLER"],
        [7, 1, 0, 7, 0, "MODEL"],
        [8, 7, 0, 8, 5, "SIGMAS"],
        [40, 8, 1, 9, 0, "IMAGE"],
        [41, 8, 2, 9, 1, "AUDIO"],
        [42, 9, 0, 10, 0, "VIDEO"],
    ]

    wf = {
        "id": "h3-relay-finite-run", "revision": 0, "last_node_id": 11,
        "last_link_id": 42, "nodes": nodes, "links": links, "groups": [],
        "config": {}, "extra": {"ds": {"scale": 0.6, "offset": [0, 0]}}, "version": 0.4,
    }
    Path(a.out).write_text(json.dumps(wf, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已写出 %s（%d 节点 / %d 链接）" % (a.out, len(nodes), len(links)))
    print("复核：python tools/check_ui_workflow.py %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
