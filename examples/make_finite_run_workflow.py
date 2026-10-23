# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""make_finite_run_workflow.py — 生成「⏩ Finite Run 无头连跑」演示工作流。

与 make_minimal_workflow.py 分开：那个要**运行中的 ComfyUI** 提供官方节点 schema；
本图的官方节点编码逐字照抄随包两份示例（服务端生成、过过 check_ui_workflow）⇒ 不起
服务就能重建。本生成器只动态推导**本包节点**的槽位 —— 参数会随版本增长，槽位顺序以
本地 `nodes.py` 的 `INPUT_TYPES()` 为唯一真相源（手写 widgets_values 会错位，
教训见 CHANGES 0.2.1）。

生成：加载器×4 → LoadImage（可拔）→ KSamplerSelect + BasicScheduler
      → ⏩ Finite Run（内部展开 N 段）→ CreateVideo → SaveVideo。

    python examples/make_finite_run_workflow.py
    python tools/check_ui_workflow.py examples/finite_run_ui.json   # 必须 0 问题
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
    "ref_1": "ref_person.png",             # ⚠ 示例占位名：换成你自己 input/ 里的定妆照
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
    given = 0
    for group in ("required", "optional"):
        for name, spec in (it.get(group) or {}).items():
            t = spec[0] if isinstance(spec, tuple) else spec
            is_combo = isinstance(t, list)          # COMBO 的 spec[0] 是候选列表
            if is_combo or t in WIDGET_TYPES:
                inputs.append({"name": name, "type": "COMBO" if is_combo else t,
                               "link": None, "widget": {"name": name}})
                try:
                    wv.append(next(wv_iter))
                    given += 1
                except StopIteration:
                    # 节点加了新 widget 而示例没给值 ⇒ 用 schema 默认并**出声**。
                    # 不兜底的话，加参数的人只改 nodes.py 就会让本生成器抛
                    # StopIteration（报错还指不到是哪个 widget）。
                    dv = spec[1].get("default") if isinstance(spec, tuple) and len(spec) > 1 else None
                    wv.append(dv)
                    print("  ⚠ widget `%s` 示例没给值 ⇒ 用 schema 默认 %r" % (name, dv))
            else:
                link = links.get(name)
                inputs.append({"name": name, "type": t, "link": link})
    assert given == len(widget_values), (
        "示例给了 %d 个 widget 值，schema 只吃下 %d 个（多出来的会错位）"
        % (len(widget_values), given))
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
         "widgets_values": [PREFERRED["ref_1"], "image"],
         # `upload` 是前端注入的槽（后端 schema 无此输入）⇒ 必须给它命名，
         # 否则 check_ui_workflow 的「尾部多一项」判据判红（与随包 fullflow 示例同款处理）。
         "widgets_values_named": {"image": PREFERRED["ref_1"], "upload": "image"}},
        {"id": 6, "type": "KSamplerSelect", "pos": [380, 0], "size": [300, 60],
         "flags": {}, "order": 5, "mode": 0,
         "inputs": [{"name": "sampler_name", "type": "COMBO", "link": None,
                     "widget": {"name": "sampler_name"}}],
         "outputs": [{"name": "SAMPLER", "type": "SAMPLER", "links": [6]}],
         "properties": {"Node name for S&R": "KSamplerSelect"},
         "widgets_values": ["euler"]},
        {"id": 7, "type": "BasicScheduler", "pos": [380, 100], "size": [300, 80],
         "flags": {}, "order": 6, "mode": 0,
         # ⚠ model 用 **link 7**（不是 1）：UNETLoader 的 MODEL 同时喂本节点与
         #   Finite Run，两个下游各需独立 link_id。写成 1 会和 FiniteRun 的 model
         #   抢同一条边 ⇒ links 数组里的 link 7 成孤儿（存量 bug，2026-10-23 修）。
         "inputs": [{"name": "model", "type": "MODEL", "link": 7},
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
        # ⚠ link 号必须与下面 `links` 数组里的 link_id **逐字一致**（母版习惯：
        #   link_id 全局唯一，且 links 数组 / inputs.link / outputs.links 三处同号）。
        #   2026-10-22 实测教训：这里原来写 "sigmas": 7，而 links 数组里 sigmas 用的是
        #   link_id 8（7 已被 #1→#7 的 MODEL 线占用）⇒ UI 能开、check_ui_workflow 也过，
        #   但 UI→API 转换时 sigmas 取不到线、slot 映射错位 ⇒ 提交报
        #   `sigmas, received_type(MODEL) mismatch input_type(SIGMAS)`。
        links={"model": 1, "clip": 2, "vae": 3, "audio_vae": 4,
               "sampler": 6, "sigmas": 8, "ref_image_1": 5},
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
            0,                # settle_frames（可选）：0 = 同机位默认；换镜填 -1
            0,                # from_segment（可选）：0 = 从第 1 段起跑；接已有段填那一段的段号
            # 以下 4 项只在接了 ref_image_* 时生效；不接线 = 存量行为。
            "match",          # ref_image_size（可选）
            "lock",           # refs_mode（可选）：全片共用一组定妆照
            "off",            # ref_anchor_mode（可选）：不额外挂外观参考
            5,                # ref_anchor_frames（可选）
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
             "6. settle_frames：同机位续接保持 0（默认）；换机位/换镜填 -1（自动把硬切退回段边界）。\n"
             "7. from_segment：0 = 从第 1 段起跑。想从一段已有视频往外接，先用「🔗 Seed from Video」\n"
             "   把视频写成第 0 段，再把这里填 1（一次提交跑完剩下的段；段号整体平移）。\n"
             "⚠ 合并输出驻留内存：段数多 / 分辨率高时先小分辨率试两段。\n"
             "⚠ 不含音频缝补丁与画质域后处理（要那些请用画布连跑逐段接）。"
         ]},
    ])

    links = [
        [1, 1, 0, 8, 0, "MODEL"],
        [2, 2, 0, 8, 1, "CLIP"],
        [3, 3, 0, 8, 2, "VAE"],
        [4, 4, 0, 8, 3, "VAE"],
        # ⚠ to_slot 按**连线型** inputs 的下标数（widget 型不占槽）。`ref_image_1` 是第 8 个
        #   连线型输入 ⇒ 下标 7；写成 6 会接到 `image`（首帧图）上，而两者**互斥** ⇒ 白接。
        [5, 5, 0, 8, 7, "IMAGE"],
        [6, 6, 0, 8, 4, "SAMPLER"],
        [7, 1, 0, 7, 0, "MODEL"],
        [8, 7, 0, 8, 5, "SIGMAS"],
        [40, 8, 1, 9, 0, "IMAGE"],
        [41, 8, 2, 9, 1, "AUDIO"],
        [42, 9, 0, 10, 0, "VIDEO"],
    ]

    # —— 写盘前自检：link 编号三处一致（母版习惯，2026-10-22 翻车点）——
    # links 数组的 link_id ｜ 节点 inputs.link ｜ 节点 outputs.links 必须指向同一批号。
    # 不一致时 UI 仍能打开、check_ui_workflow 也不报，但 UI→API 转换会错位 ⇒ 提交被拒。
    declared = {l[0] for l in links}
    used_in = set()
    used_out = set()
    for nd in nodes:
        for i in nd.get("inputs") or []:
            if i.get("link") is not None:
                used_in.add(i["link"])
        for o in nd.get("outputs") or []:
            for lid in (o.get("links") or []):
                if lid is not None:
                    used_out.add(lid)
    bad = (used_in | used_out) - declared
    if bad:
        raise SystemExit(
            "生成器自检失败：节点引用了 links 数组里不存在的 link_id %s\n"
            "    已声明：%s\n"
            "    inputs 引用：%s\n"
            "    outputs 引用：%s" % (sorted(bad), sorted(declared),
                                  sorted(used_in), sorted(used_out)))
    if len(declared) != len(links):
        raise SystemExit("生成器自检失败：link_id 有重复（必须全局唯一）")

    # 🔴 links 数组是**唯一边表**：节点的 `inputs.link` / `outputs.links` 由它派生。
    #    为什么不能三处各手写一份：存量 bug —— MODEL 要同时喂 `Finite Run`(link 1) 与
    #    `BasicScheduler`(link 7)，而 node1 的 outputs 只列 `[1]`、node7 的 model 写 `link: 1`
    #    ⇒ link 7 成孤儿。前端重建连线时读 `inputs[].link` 所以当时没炸，但任何按槽位解
    #    links 数组的工具都会解错，而且这种不一致**没有任何机检看得见**。
    _by_id = {nd["id"]: nd for nd in nodes}

    def _link_inputs(nd):
        """节点的**连线型** inputs（widget 型不占连线槽位）。"""
        return [i for i in (nd.get("inputs") or []) if i.get("widget") is None]

    # ① 先按「as-built」的 inputs 校验 links 数组里的 from/to 槽位（**必须在回填前**，
    #    否则拿回填结果校验自己 = 同义反复，变异测试打不掉）。
    for lid, fn, fslot, tn, tslot, _ty in links:
        _fo = _by_id[fn].get("outputs") or []
        if not (0 <= fslot < len(_fo)):
            raise SystemExit("link %d：from 槽 %d 超出节点 %s 的输出数 %d"
                             % (lid, fslot, fn, len(_fo)))
        _ti = _link_inputs(_by_id[tn])
        if not (0 <= tslot < len(_ti)):
            raise SystemExit("link %d：to 槽 %d 超出节点 %s 的连线型输入数 %d"
                             % (lid, tslot, tn, len(_ti)))
        if _ti[tslot].get("link") != lid:
            raise SystemExit(
                "link %d：links 数组说它接节点 %s 的第 %d 个连线型输入（%s），"
                "但那个输入的 link 是 %r\n    ⇒ to_slot 写错了（写成 %d 会接到 %s 上）。"
                % (lid, tn, tslot, _ti[tslot].get("name"), _ti[tslot].get("link"),
                   tslot, _ti[tslot].get("name")))

    # ② 回填：清空后按 links 数组重建（一个输出槽可喂多个下游 ⇒ 追加不是覆盖）。
    for nd in nodes:
        for i in _link_inputs(nd):
            i["link"] = None
        for o in nd.get("outputs") or []:
            o["links"] = None
    for lid, fn, fslot, tn, tslot, _ty in links:
        _fo = _by_id[fn]["outputs"][fslot]
        if _fo.get("links") is None:
            _fo["links"] = []
        if lid not in _fo["links"]:
            _fo["links"].append(lid)
        _link_inputs(_by_id[tn])[tslot]["link"] = lid

    # ③ 孤儿线复核：links 数组里每条都必须正好被一处 output + 一处 input 引用。
    _ref_out, _ref_in = {}, {}
    for nd in nodes:
        for o in nd.get("outputs") or []:
            for x in (o.get("links") or []):
                _ref_out.setdefault(x, 0)
                _ref_out[x] += 1
        for i in _link_inputs(nd):
            if i.get("link") is not None:
                _ref_in.setdefault(i["link"], 0)
                _ref_in[i["link"]] += 1
    _orphan = [l[0] for l in links if l[0] not in _ref_out or l[0] not in _ref_in]
    if _orphan:
        raise SystemExit(
            "生成器自检失败：links 数组里这些 link 在 nodes 里没有对应接线（孤儿线）%s\n"
            "    links 数组声明的每条边，都必须能在某个 output.links 与某个 inputs.link 里找到。"
            % sorted(_orphan))

    wf = {
        "id": "h3-relay-finite-run", "revision": 0, "last_node_id": 11,
        "last_link_id": 42, "nodes": nodes, "links": links, "groups": [],
        "config": {}, "extra": {"ds": {"scale": 0.6, "offset": [0, 0]}}, "version": 0.4,
    }
    Path(a.out).write_text(json.dumps(wf, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已写出 %s（%d 节点 / %d 链接）" % (a.out, len(nodes), len(links)))
    print("自检：link 编号三处一致 ✓（declared %d / inputs 引用 %d / outputs 引用 %d）"
          % (len(declared), len(used_in), len(used_out)))
    print("复核：python tools/check_ui_workflow.py %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
