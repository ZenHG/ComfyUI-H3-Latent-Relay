# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Relay-Kit contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
r"""把 `H3RelayPost` 补进 examples/minimal_relay_official.json（**幂等**）。

做四件事：
  1. TrimAV 的 outputs 补第 4 路 `prev_tail`（0.5.0 新增，**追加在末位**）
  2. TrimAV 的 `settle_frames` 由 -1 改为 0（0.5.0 新默认：不裁沉降＝缝处无跳）
  3. 新增一个 `H3RelayPost` 节点，串在 TrimAV 与 CreateVideo 之间
  4. 连线：TrimAV.images → Post.images；TrimAV.prev_tail → Post.guide；Post.images → CreateVideo.images

**幂等**：已经补过就只报告「已补过」并按需修 `settle_frames`，不重复加节点、不重复连线。
节点按**类型**定位（不写死 #13/#14），所以生成器重跑覆盖 JSON 之后可以再跑一次。

⚠ 改完**必须**验槽位对齐（铁律：`widgets_values` 按位置对应，错位不报错但参数全错）：
   - 有服务端时：`python tools/check_ui_workflow.py examples/minimal_relay_official.json`
   - 离线替代：`python tools/review_050.py`（I 节 = 槽位对齐 / J 节 = 存量图扫描）
"""
import json
import os
import sys

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(KIT, "examples", "minimal_relay_official.json")


def _find(d, typ, nth=0):
    hits = [n for n in d["nodes"] if n.get("type") == typ]
    if len(hits) <= nth:
        raise SystemExit("[FAIL] 图里找不到第 %d 个 %s 节点（有：%s）"
                         % (nth + 1, typ, [n.get("type") for n in d["nodes"]]))
    return hits[nth]


def _widget_names(cls, link_types):
    """按 INPUT_TYPES 顺序给出「占 widget 槽」的名字（连线口不占）。"""
    it = cls.INPUT_TYPES()
    out = []
    for sec in ("required", "optional"):
        for name, spec in (it.get(sec) or {}).items():
            if not isinstance(spec, tuple):
                continue
            t = spec[0]
            if isinstance(t, str) and t in link_types:
                continue
            out.append(name)
            if isinstance(spec[1], dict) and spec[1].get("control_after_generate"):
                out.append("__control_after_generate__")
    return out


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    d = json.load(open(P, encoding="utf-8"))
    changed = False

    trim = _find(d, "H3RelayTrimAV")
    mk = _find(d, "CreateVideo")
    existing = [n for n in d["nodes"] if n.get("type") == "H3RelayPost"]

    # ---- 1) TrimAV 补第 4 路输出 prev_tail ----
    if not any(o.get("name") == "prev_tail" for o in trim["outputs"]):
        trim["outputs"].append({"name": "prev_tail", "type": "IMAGE", "links": []})
        changed = True
        print("[1] TrimAV 追加第 4 路输出 prev_tail")
    else:
        print("[1] TrimAV 已有 prev_tail（跳过）")

    # ---- 2) settle_frames 修正为 0（0.5.0 新默认）----
    # widget 槽序：trim_frames(链接) / fps / settle_frames / …（见 INPUT_TYPES）
    wv = trim.get("widgets_values") or []
    if len(wv) >= 3 and wv[2] == -1:
        wv[2] = 0
        trim["widgets_values"] = wv
        changed = True
        print("[2] TrimAV settle_frames: -1 → 0（0.5.0 新默认）")
    elif len(wv) == 0:
        trim["widgets_values"] = [24.0, 0]
        changed = True
        print("[2] TrimAV 无 widgets_values → 补 [fps=24.0, settle_frames=0]")
    else:
        print("[2] TrimAV settle_frames 已是 %r（跳过）" % (wv[2] if len(wv) >= 3 else None))

    if existing:
        print("[3][4] H3RelayPost 已在图里（#%s）——不重复加节点/连线" % existing[0]["id"])
        if changed:
            json.dump(d, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("[OK] %s（节点 %d 个，连线 %d 条）" % (P, len(d["nodes"]), len(d["links"])))
        return

    # ---- 3) 新增 H3RelayPost ----
    new_id = int(d.get("last_node_id") or max(n["id"] for n in d["nodes"])) + 1
    l1 = int(d.get("last_link_id") or max(l[0] for l in d["links"])) + 1
    l2, l3 = l1 + 1, l1 + 2

    # 槽位顺序按真实 INPUT_TYPES 推导，不手抄（手抄就是错位的来源）
    # 不写死本机路径：本包通常装在 <ComfyUI>/custom_nodes/<本包>/ ⇒ 往上两级即 ComfyUI 根
    sys.path.insert(0, os.environ.get("COMFYUI_PATH")
                    or os.path.dirname(os.path.dirname(KIT)))
    sys.path.insert(0, KIT)
    import importlib.util
    import types
    _pkg = types.ModuleType("h3relay_kit")
    _pkg.__path__ = [KIT]
    sys.modules["h3relay_kit"] = _pkg
    _sp = importlib.util.spec_from_file_location("h3relay_kit.nodes",
                                                 os.path.join(KIT, "nodes.py"))
    _n = importlib.util.module_from_spec(_sp)
    sys.modules["h3relay_kit.nodes"] = _n
    _sp.loader.exec_module(_n)

    LINK_T = ("IMAGE", "AUDIO", "LATENT", "MODEL", "CONDITIONING", "VAE", "CLIP",
              "VIDEO", "SAMPLER", "SIGMAS", "GUIDER")
    slots = _widget_names(_n.H3RelayPost, LINK_T)
    itp = _n.H3RelayPost.INPUT_TYPES()["optional"]
    defaults = [itp[s][1]["default"] for s in slots]

    post = {
        "id": new_id, "type": "H3RelayPost",
        "pos": [trim["pos"][0] + 340, trim["pos"][1]],
        "size": [330, 320], "flags": {}, "order": 0, "mode": 0,
        "inputs": ([{"name": "images", "type": "IMAGE", "link": l1},
                    {"name": "guide", "type": "IMAGE", "link": l2}]
                   + [{"name": s, "type": ("FLOAT" if isinstance(v, float) else
                                           "INT" if isinstance(v, int) else "STRING"),
                       "link": None, "widget": {"name": s}}
                      for s, v in zip(slots, defaults)]),
        "outputs": [{"name": "images", "type": "IMAGE", "links": [l3]},
                    {"name": "report", "type": "STRING", "links": []}],
        "widgets_values": defaults,
        "title": "🔗 H3 续接后处理 Post（全部默认关）",
    }
    d["nodes"].append(post)
    print("[3] 新增节点 #%d H3RelayPost（%d 个 widget 全默认关）" % (new_id, len(slots)))

    # ---- 4) 连线 ----
    d["links"] = [l for l in d["links"] if not (l[1] == trim["id"] and l[2] == 0)]
    trim["outputs"][0]["links"] = [l1]
    trim["outputs"][3]["links"] = [l2]
    mk["inputs"][0]["link"] = l3
    d["links"].extend([
        [l1, trim["id"], 0, new_id, 0, "IMAGE"],
        [l2, trim["id"], 3, new_id, 1, "IMAGE"],
        [l3, new_id, 0, mk["id"], 0, "IMAGE"],
    ])
    print("[4] 连线：TrimAV.images→Post.images / TrimAV.prev_tail→Post.guide / Post.images→CreateVideo")

    d["last_node_id"] = new_id
    d["last_link_id"] = l3
    json.dump(d, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("[OK] 已写回 %s（节点 %d 个，连线 %d 条）" % (P, len(d["nodes"]), len(d["links"])))


if __name__ == "__main__":
    main()
