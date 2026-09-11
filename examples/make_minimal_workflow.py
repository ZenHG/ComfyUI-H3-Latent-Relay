# -*- coding: utf-8 -*-
"""make_minimal_workflow.py — 生成「最小官方节点 + 本包」的续接演示工作流。

【为什么要用脚本生成，而不是手写一份 JSON 交上去】
UI 格式工作流的 ``widgets_values`` 是**按位置**对应前端 widget 槽位的，而且前端会
**自动注入**一些不出现在 ``INPUT_TYPES`` 里的格子（典型：带
``control_after_generate: True`` 的 ``seed`` 后面会多一格下拉）。手写只要漏掉中间任意一格，
其后所有取值**整体前移一位** —— 文件照样能打开、能提交、**不报任何错**，但参数全是错的。
（实测记录见 CHANGES 0.2.1。随包的 ``tools/check_ui_workflow.py`` 就是为查这个而写的。）

本脚本从**正在运行的 ComfyUI** 的 ``/object_info`` 读真实 schema 来拼，槽位顺序由 schema 推导，
所以天然不会错位；上游升级后重跑一次即可。生成完请用 ``check_ui_workflow.py`` 复核。

【用法】
    # 1) 先启动 ComfyUI（另开一个终端）
    python main.py

    # 2) 生成
    python examples/make_minimal_workflow.py                   # 默认写到 examples/
    python examples/make_minimal_workflow.py --out my.json --api http://127.0.0.1:8188

    # 3) 复核（必须 0 问题）
    python tools/check_ui_workflow.py examples/minimal_relay_official.json

【生成的是什么】
    4 个官方加载器 → 官方条件节点（出词 + AV latent）
                  → 🔗 续接 Latent 桥 → KSampler → 🔗 续接 Latent 存
                  → VAEDecode / VAEDecodeAudio → 🔗 续接裁重叠 → CreateVideo → SaveVideo

    图里有一个 `段号`（PrimitiveInt）同时喂给「桥」和「落盘」的 stage_index，
    所以**跑下一段只需要改这一个数**（这才是"正确连线"最省心的形态）。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import urllib.request

DEFAULT_API = "http://127.0.0.1:8188"

# 与 tools/check_ui_workflow.py 保持一致
WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"}

# 前端自带的虚拟节点：不在 /object_info 里，但前端一定认识。
# （`Note` 是注释框，用它把说明写进图里 —— 比只写在 README 里更容易被看见。）
FRONTEND_ONLY = {"Note"}

# 本机实测可用的模型文件名（都来自 ComfyUI/output 里现成的工作流）。
# 不在候选列表里时会自动退回该节点的第一个候选项。
PREFERRED = {
    "unet_name": "minimax_h3_hybrid_fl2va_ref2va_b25-49-int8.safetensors",
    "clip_name": "qwen3vl_32b_minimax_h3_nvfp4.safetensors",
    "type": "minimax",                      # CLIPLoader 的 type
    "vae_name": [
        "minimax_h3_video_vae_int8_convrot.safetensors",   # 视频 VAE（第 1 个 VAELoader）
        "minimax_h3_audio_vae_fp32.safetensors",           # 音频 VAE（第 2 个 VAELoader）
    ],
}

PROMPT_PLACEHOLDER = (
    "日式赛璐璐 2D 动画，暖琥珀主光，超现实咖啡厨房。\n"
    "[Shot 1] 中近景，Yuki 与 Kara 近身缠斗，一记右直拳擦过耳侧，白发随拳风甩起……\n"
    "【本段是续接段：开头请直接接住上一段结尾的动作与机位，不要重新起手】\n"
    "文字：全片画面无任何文字与符号。\n"
    "overall_soundscape: 拳风破空、拳掌相交的闷响、两人急促的呼吸、厨房低频底噪。无音乐。"
)


# ---------------------------------------------------------------- schema 工具
def _ty(spec):
    if not isinstance(spec, list) or not spec:
        return None
    return "COMBO" if isinstance(spec[0], list) else spec[0]


def spec_of(defn, name):
    for sec in ("required", "optional"):
        if name in (defn.get("input", {}).get(sec) or {}):
            return defn["input"][sec][name]
    return None


def all_inputs(defn):
    """(name, spec) 列表，required 在前 optional 在后 —— 与前端一致。"""
    out = []
    for sec in ("required", "optional"):
        for name, spec in (defn.get("input", {}).get(sec) or {}).items():
            out.append((name, spec))
    return out


def widget_slots(defn):
    """前端实际 widget 槽位顺序（含 control_after_generate 注入）。

    这是本脚本存在的核心：槽位顺序**只**由 schema 推导，绝不靠人记。
    """
    out = []
    for name, spec in all_inputs(defn):
        if _ty(spec) not in WIDGET_TYPES:
            continue
        out.append(name)
        extra = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
        if extra.get("control_after_generate"):
            out.append("control_after_generate")
    return out


def default_value(defn, name):
    spec = spec_of(defn, name)
    if spec is None:
        return None
    extra = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
    t = _ty(spec)
    if t == "COMBO":
        opts = spec[0] if isinstance(spec[0], list) else extra.get("options") or []
        pref = PREFERRED.get(name)
        cands = pref if isinstance(pref, list) else [pref]
        for c in cands:
            if c is not None and c in opts:
                return c
        return opts[0] if opts else ""
    if name == "control_after_generate":
        return "fixed"
    if name == "prompt":
        return PROMPT_PLACEHOLDER
    if name == "run_id":
        return "relay_demo"
    if name == "note" and t == "STRING":
        return ""
    if name == "filename_prefix":
        return "video/H3_relay_demo"
    return extra.get("default", "")


# ---------------------------------------------------------------- 图构造
class Graph:
    def __init__(self, oi):
        self.oi = oi
        self.nodes = []
        self.links = []
        self._next_id = 1
        self._next_link = 1

    def defn(self, node_type):
        if node_type in FRONTEND_ONLY:
            # 前端虚拟节点：只有 widget，没有连线，自己造一份最小 schema
            return {"input": {"required": {"text": ["STRING", {"default": ""}]}},
                    "output": [], "output_name": []}
        d = self.oi.get(node_type)
        if d is None:
            raise SystemExit(
                "[FAIL] 服务端没有节点 %r（本包的 5 个节点要装在 custom_nodes 下并重启）。\n"
                "       当前识别到本包节点：%s"
                % (node_type, sorted(k for k in self.oi if k.startswith("H3Relay")))
            )
        return d

    def add(self, node_type, *, pos, values=None, title=None, links_in=None):
        """links_in: {输入名: (源节点对象, 源输出名)}；values: {widget 名: 覆盖值}。"""
        d = self.defn(node_type)
        node = {"id": self._next_id, "type": node_type, "pos": list(pos),
                "size": [320, 100], "flags": {}, "order": len(self.nodes), "mode": 0,
                "inputs": [], "outputs": [],
                "properties": {"Node name for S&R": node_type}}
        if title:
            node["title"] = title
        self._next_id += 1
        links_in = links_in or {}
        values = values or {}

        outs = list(d.get("output") or [])
        onames = list(d.get("output_name") or [])
        node["outputs"] = [
            {"name": (onames[i] if i < len(onames) else ot), "type": ot, "links": []}
            for i, ot in enumerate(outs)
        ]

        # ⚠ 输入槽位顺序必须与 ComfyUI 前端**保存**出来的格式一致，否则 links 的
        #   目标下标会错位。实测（对比真实工作流文件）约定是：
        #     先列所有「可连线」输入（required 再 optional，按 schema 顺序），
        #     再列所有「widget」输入（同样 required 再 optional）。
        #   V3 的 COMFY_AUTOGROW_V3 / COMFY_DYNAMICCOMBO_V3 属后者，但不带 widget 键。
        linkable, widgety = [], []
        for name, spec in all_inputs(d):
            t = _ty(spec) or "*"
            if t in WIDGET_TYPES:
                widgety.append((name, spec, True))
            elif t.startswith("COMFY_"):
                widgety.append((name, spec, False))
            else:
                linkable.append((name, spec, False))

        for name, spec, is_widget in linkable + widgety:
            entry = {"name": name, "type": _ty(spec) or "*", "link": None}
            if is_widget:
                entry["widget"] = {"name": name}
            link = None
            if name in links_in:
                src, src_out = links_in[name]
                sidx = out_index_of(src, src_out)
                lid = self._next_link
                self._next_link += 1
                self.links.append([lid, src["id"], sidx,
                                   node["id"], len(node["inputs"]), entry["type"]])
                src["outputs"][sidx]["links"].append(lid)
                link = lid
            entry["link"] = link
            node["inputs"].append(entry)

        slots = widget_slots(d)
        node["widgets_values"] = [values.get(s, default_value(d, s)) for s in slots] or None
        self.nodes.append(node)
        return node


def out_index_of(node, out_name):
    if isinstance(out_name, int):
        return out_name
    for i, o in enumerate(node["outputs"]):
        if o["name"] == out_name:
            return i
    raise KeyError("节点 %s 没有输出 %r（有：%s）"
                   % (node["type"], out_name, [o["name"] for o in node["outputs"]]))


def validate(g, oi):
    """结构自检：id 唯一、连线两端存在、槽位下标合法、类型相容、widget 槽位数对得上。

    这些是"文件能打开但跑起来报错"的常见来源；生成后先自己过一遍，别等用户撞。
    """
    bad = []
    ids = [n["id"] for n in g.nodes]
    if len(set(ids)) != len(ids):
        bad.append("节点 id 有重复")
    lids = [l[0] for l in g.links]
    if len(set(lids)) != len(lids):
        bad.append("连线 id 有重复")

    byid = {n["id"]: n for n in g.nodes}
    want_out = {}     # (node_id, slot) -> set(link ids)
    for lid, fn, fs, tn, ts, ty in g.links:
        if fn not in byid or tn not in byid:
            bad.append("连线 #%s 端点了不存在的节点" % lid)
            continue
        src, dst = byid[fn], byid[tn]
        if not (0 <= fs < len(src["outputs"])):
            bad.append("连线 #%s 源槽位 %s 越界（%s 只有 %d 个输出）"
                       % (lid, fs, src["type"], len(src["outputs"])))
            continue
        if not (0 <= ts < len(dst["inputs"])):
            bad.append("连线 #%s 目标槽位 %s 越界（%s 只有 %d 个输入）"
                       % (lid, ts, dst["type"], len(dst["inputs"])))
            continue
        want_out.setdefault((fn, fs), set()).add(lid)
        if dst["inputs"][ts].get("link") != lid:
            bad.append("连线 #%s 自称连到 %s[%s]，但该输入的 link 字段是 %r"
                       % (lid, dst["type"], ts, dst["inputs"][ts].get("link")))
        a = src["outputs"][fs]["type"]
        b = dst["inputs"][ts]["type"]
        if a != "*" and b != "*" and a != b:
            ta, tb = set(a.split(",")), set(b.split(","))
            if not (ta & tb):
                bad.append("连线 #%s 类型不相容：%s.%s(%s) → %s.%s(%s)"
                           % (lid, src["type"], src["outputs"][fs]["name"], a,
                              dst["type"], dst["inputs"][ts]["name"], b))
    for n in g.nodes:
        if n["type"] in FRONTEND_ONLY:
            continue
        if n["type"] not in oi:
            bad.append("节点 %s 在服务端不存在" % n["type"])
            continue
        d = oi[n["type"]]
        slots = widget_slots(d)
        wv = n.get("widgets_values") or []
        if len(wv) != len(slots):
            bad.append("%s 的 widgets_values 有 %d 项，schema 推导出 %d 个槽位 %s"
                       % (n["type"], len(wv), len(slots), slots))
        for i, o in enumerate(n["outputs"]):
            got = set(o.get("links") or [])
            exp = want_out.get((n["id"], i), set())
            if got != exp:
                bad.append("%s 输出[%d]%s 的 links=%s，实际引出 %s"
                           % (n["type"], i, o["name"], sorted(got), sorted(exp)))
    return bad


def build(oi, length=73, width=448, height=768):
    g = Graph(oi)

    unet = g.add("UNETLoader", pos=[40, 40])
    clip = g.add("CLIPLoader", pos=[40, 200])
    vae_v = g.add("VAELoader", pos=[40, 360], values={"vae_name": PREFERRED["vae_name"][0]})
    vae_a = g.add("VAELoader", pos=[40, 520], values={"vae_name": PREFERRED["vae_name"][1]})

    stage = g.add("PrimitiveInt", pos=[40, 680], values={"value": 0}, title="① 段号：第 1 段填 0，第 2 段填 1")

    cond = g.add("MiniMaxH3ImageToVideo", pos=[420, 40],
                 links_in={"clip": (clip, "CLIP"), "vae": (vae_v, "VAE")},
                 values={"prompt": PROMPT_PLACEHOLDER, "width": width, "height": height,
                         "length": length})

    neg = g.add("ConditioningZeroOut", pos=[420, 380],
                links_in={"conditioning": (cond, 0)})

    bridge = g.add("H3RelayMotionContext", pos=[800, 40],
                   links_in={"conditioning": (cond, 0), "latent": (cond, "LATENT"),
                             "stage_index": (stage, "INT")},
                   values={"trim_frames": 22, "audio_frames": 0, "run_id": "relay_demo"},
                   title="🔗 续接 Latent 桥（context_latent 不用接，自动读上一段）")

    sampler = g.add("KSampler", pos=[1180, 40],
                    links_in={"model": (unet, "MODEL"), "positive": (bridge, 0),
                              "negative": (neg, 0), "latent_image": (cond, "LATENT")},
                    values={"seed": 88300001, "steps": 6, "cfg": 1.0,
                            "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0},
                    title="② 采样：seed / steps 在这里")

    save = g.add("H3RelayLatentSave", pos=[1180, 400],
                 links_in={"latent": (sampler, "LATENT"), "stage_index": (stage, "INT")},
                 values={"run_id": "relay_demo", "note": ""},
                 title="🔗 落盘本段 latent（下一段的接力棒）")

    dec_v = g.add("VAEDecode", pos=[1540, 40],
                  links_in={"samples": (sampler, "LATENT"), "vae": (vae_v, "VAE")})
    dec_a = g.add("VAEDecodeAudio", pos=[1540, 260],
                  links_in={"samples": (sampler, "LATENT"), "vae": (vae_a, "VAE")})

    trim = g.add("H3RelayTrimAV", pos=[1900, 40],
                 links_in={"images": (dec_v, "IMAGE"), "audio": (dec_a, "AUDIO"),
                           "trim_frames": (bridge, "trim_frames")},
                 values={"fps": 24.0},
                 title="🔗 裁重叠（自动：钉住区 + 沉降帧）")

    mk = g.add("CreateVideo", pos=[2260, 40],
               links_in={"images": (trim, "images"), "audio": (trim, "audio")},
               values={"fps": 24.0})
    g.add("SaveVideo", pos=[2260, 300], links_in={"video": (mk, "VIDEO")})

    g.add("Note", pos=[420, 700], title="怎么用", values={"text":
        "【最小续接演示 · 官方节点 + ComfyUI-H3-Relay-Kit】\n"
        "\n"
        "第 1 段：\n"
        "  ① 段号 = 0，填好 prompt → 点 Queue。\n"
        "  日志会打印「无 context_latent → 直通（独立段，不续接）」，裁重叠不裁。\n"
        "\n"
        "第 2 段：\n"
        "  只改两处：\n"
        "  ① 段号 = 1（桥与落盘会自动跟着走，不用手改第二处）\n"
        "  ② prompt 换成第 2 段要拍的内容（开头直接接住上一段的动作/机位）\n"
        "  再点 Queue。\n"
        "  日志应出现：\n"
        "    [H3 Relay] latent 桥续接：钉住 22 帧 / 7 步 …\n"
        "    [H3 Relay] 裁首 23 帧 = 钉住 22 + 沉降 1 ｜ 自动检测：…\n"
        "    [H3 Relay] 接缝自检：前 40 帧无突变 … → 起点干净。\n"
        "\n"
        "只看这三件事就算跑通：\n"
        "  · 「钉住 22 帧」= 上一段尾部已接进来\n"
        "  · 「裁首 N 帧 = 钉住 22 + 沉降 Y」= 接缝处理已生效（Y 由本段画面量出来）\n"
        "  · 「起点干净」= 没有跳变，可以拼\n"
        "\n"
        "没接对的两个典型症状：\n"
        "  · 日志写「静默直通」→ 段号填了 ≥1，但 run_id 与上一段不一致 / 文件不在\n"
        "  · 画面从第 1 帧就开始重播上一段 → 裁重叠节点没接上，或 trim_frames 没接桥的第 3 路\n"
        "\n"
        "模型文件：把上面 4 个加载器的下拉改成你本机的文件即可。"}),
    return g


def main():
    ap = argparse.ArgumentParser(description="生成最小官方续接演示工作流")
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "minimal_relay_official.json"))
    ap.add_argument("--length", type=int, default=73, help="段长（像素帧，须落在 5+17k）")
    ap.add_argument("--width", type=int, default=448)
    ap.add_argument("--height", type=int, default=768)
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    api = a.api.rstrip("/")
    print("取 schema：%s/object_info" % api)
    try:
        oi = json.loads(urllib.request.urlopen(api + "/object_info", timeout=180).read().decode("utf-8"))
    except Exception as e:
        raise SystemExit("[FAIL] 取不到 object_info：%r\n       请先启动 ComfyUI（python main.py）。" % (e,))

    missing = [k for k in ("H3RelayMotionContext", "H3RelayLatentSave", "H3RelayTrimAV") if k not in oi]
    if missing:
        raise SystemExit(
            "[FAIL] ComfyUI 里没有这些节点：%s\n"
            "       把 ComfyUI-H3-Relay-Kit 放进 custom_nodes/ 并**重启后端**（只刷浏览器不加载新节点）。"
            % ", ".join(missing))
    print("本包节点已就位：%s" % ", ".join(sorted(k for k in oi if k.startswith("H3Relay"))))

    g = build(oi, length=a.length, width=a.width, height=a.height)
    bad = validate(g, oi)
    if bad:
        print("\n[FAIL] 生成结果结构自检不过：")
        for b in bad:
            print("   ✗ " + b)
        raise SystemExit(1)
    print("结构自检：通过（节点 %d / 连线 %d，widget 槽位逐节点核对）" % (len(g.nodes), len(g.links)))

    wf = {
        "id": "h3-relay-minimal",
        "revision": 0,
        "last_node_id": g._next_id - 1,
        "last_link_id": g._next_link - 1,
        "nodes": g.nodes,
        "links": g.links,
        "groups": [],
        "config": {},
        "extra": {"ds": {"scale": 0.6, "offset": [0, 0]}},
        "version": 0.4,
    }
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    io.open(a.out, "w", encoding="utf-8").write(json.dumps(wf, ensure_ascii=False, indent=2))
    print("已写出：%s（节点 %d / 连线 %d）" % (a.out, len(g.nodes), len(g.links)))
    print("下一步复核：python tools/check_ui_workflow.py %s" % a.out)


if __name__ == "__main__":
    main()
