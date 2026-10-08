# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
#
# ruff: noqa: F821  —— 本分片**不是独立模块**（与 `tests/cases/` 其余分片同款）：
#   由 `tests/test_relay_core.py` 用 `exec(compile(src, path, "exec"), globals())`
#   顺序执行 ⇒ `check` / `CORE` / `os` / `json` 都看得见，但不是本文件定义的。

# ============================================================================
# 第 34 组：`run_id` 跨节点同步（0.6.31 —— GG 2026-10-07 指正）
#
# 背景：前端有广播（改一处 `run_id` 同步到全组），**API / 脚本用户没有那条路**。
#   ⇒ 本组锁的是后端那一份：`relay_core/plan.py::{collect_run_ids, resolve_run_id,
#   plan_run_id_sync, sync_run_id}`。
#
# 🔴 **跨层孪生**（规范 §二·D）：这四个函数与前端 `web/relay_kit_sync.js` 的
#   `resolveRunId` / `planRunIdSync` 是**同一套语义的两份实现**。"看起来一样"不算对齐 ——
#   本组与 `tests/test_run_id_parity.mjs` 读**同一份** `tests/parity/run_id_cases.json`，
#   两侧都要过 ⇒ 任何一侧单方面改语义都会当场红。
#
#   跨层对账**刻意不含"范围/scope"**：后端没有"画布位置"这回事，API 侧天然是**全图**。
#   前端范围判据（Chain 数量 ⇒ 全图 / 按框隔离）由 `test_run_id_parity.mjs` 的 [3] 组锁。
# ============================================================================

print()
print("=" * 78)
print("### 第 34 组：run_id 跨节点同步（API 侧实现 + 跨层对账）")
print("=" * 78)

import json as _json34  # （就近用；本分片由 runner 顺序执行）
import re as _re34
from pathlib import Path

_HERE34 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(_HERE34, "tests", "parity", "run_id_cases.json"), encoding="utf-8") as _fh34:
    _FX34 = _json34.load(_fh34)


def _same_set34(a, b):
    x = sorted(str(i) for i in (a or []))
    y = sorted(str(i) for i in (b or []))
    return x == y


def _members34(rows):
    """fixture 用 JS 拼写（runId）⇒ 这里映射成后端字段名。**映射本身也在这组里被断言**。"""
    return [{"id": r["id"], "type": "x", "run_id": r["runId"], "linked": bool(r["linked"])}
            for r in rows]


# 34.1 收集：只认六类、其余忽略；连线形态标记 linked；id 一律 str()
for _c in _FX34["collect"]:
    _got = CORE.collect_run_ids(_c["prompt"])
    check("34.1 %s" % _c["name"],
          [{k: m[k] for k in ("id", "type", "run_id", "linked")} for m in _got]
          == [{k: e[k] for k in ("id", "type", "run_id", "linked")} for e in _c["expect"]],
          "实得=%s" % [(m["id"], m["run_id"], m["linked"]) for m in _got])

# 34.2 裁决 + 广播规划：逐条对账 fixture（与前端同一份期望值）
for _c in _FX34["cases"]:
    _mem = _members34(_c["members"])
    _v = CORE.resolve_run_id(_mem)
    _e = _c["expect"]["verdict"]
    # 🔴 `by_value` 也要断言（审核 P0-3）：它是**唯一**用来点名「哪个节点是哪个名字」的数据，
    #   `__init__.py::_describe_run_id` 写提示全靠它 ⇒ 只比 state/value/ids/empty 的话，
    #   把 by_value 删掉两侧照样全绿。
    _bv = True
    if "by_value" in _e:
        _got = {k: list(vv) for k, vv in _v["by_value"].items()}
        _exp = {k: list(vv) for k, vv in _e["by_value"].items()}
        _bv = (sorted(_got.keys()) == sorted(_exp.keys())
               and all(_same_set34(_got[k], _exp[k]) for k in _exp))
    check("34.2 %s · verdict" % _c["name"],
          _v["state"] == _e["state"] and str(_v["value"]) == str(_e["value"])
          and _same_set34(_v["ids"], _e["ids"]) and _same_set34(_v["empty"], _e["empty"]) and _bv
          and ("values" not in _e or _same_set34(_v["values"], _e["values"])),
          "实得 state=%s value=%r ids=%s empty=%s by_value=%s"
          % (_v["state"], _v["value"], _v["ids"], _v["empty"], list(_v["by_value"])))

    _ep = _c["expect"]["plan"]
    if _ep is None:
        # 冲突 / 全空 ⇒ **必须拒绝自动改写**（"不猜"是本仓最硬的纪律之一）
        _r = CORE.sync_run_id({"902": {"class_type": "H3RelayLatentSave",
                                        "inputs": {"run_id": "a"}}},
                              value=None if _c["value"] is None else _c["value"])
        check("34.2 %s · 拒绝自动改写" % _c["name"], _r["changed"] is False,
              "实得 changed=%s" % _r["changed"])
        continue
    _p = CORE.plan_run_id_sync(_mem, _c["origin"],
                               _v["value"] if _c["value"] is None else _c["value"])
    check("34.2 %s · plan" % _c["name"],
          _same_set34(_p["targets"], _ep["targets"])
          and _p["reason"] == _ep["reason"] and _same_set34(_p["skipped"], _ep["skipped"]),
          "实得 targets=%s reason=%s skipped=%s" % (_p["targets"], _p["reason"], _p["skipped"]))

# 34.3 表与 nodes.py 对账（与前端同一个 `RUN_ID_TYPES` 概念，少一类 = 那一类永远用别的目录名）
_src34 = Path(os.path.join(_HERE34, "nodes.py")).read_text(encoding="utf-8")
_decl34 = set()
for _m34 in _re34.finditer(r"^class\s+(\w+)", _src34, _re34.M):
    _nxt = _src34.find("\nclass ", _m34.end())
    _body = _src34[_m34.start():_nxt if _nxt != -1 else len(_src34)]
    if _re34.search(r"[\"']run_id[\"']\s*:", _body):
        _decl34.add(_m34.group(1))
_missing34 = sorted(_decl34 - set(CORE.RUN_ID_TYPES))
check("34.3 nodes.py 声明 run_id 的类 ⊆ RUN_ID_TYPES（声明 %d / 表 %d）"
      % (len(_decl34), len(CORE.RUN_ID_TYPES)),
      not _missing34, "表里没有：" + "、".join(_missing34))
check("34.3 RUN_ID_TYPES ⊆ nodes.py 声明的类（不许有幽灵条目）",
      not sorted(set(CORE.RUN_ID_TYPES) - _decl34))

# 34.4 端到端：sync_run_id 真改写 prompt，且**不丢别的 input 键、不就地改调用方的对象**
_p34 = {
    "931": {"class_type": "H3RelayAudioSeam",
            "inputs": {"run_id": "", "stage_index": 3, "patch_seconds": 0.5}},
    "902": {"class_type": "H3RelayLatentSave",
            "inputs": {"run_id": "demo", "stage_index": 0, "note": "keep"}},
    "958": {"class_type": "H3RelayChain", "inputs": {"run_id": ["999", 0]}},
}
_r34 = CORE.sync_run_id(_p34)
check("34.4 空格子被补齐、连线格子被跳过",
      _r34["changed"] is True
      and _r34["prompt"]["931"]["inputs"]["run_id"] == "demo"
      and _r34["prompt"]["958"]["inputs"]["run_id"] == ["999", 0],
      _r34["report"])
check("34.4 改写**不丢**同节点的其它 input 键",
      _r34["prompt"]["931"]["inputs"].get("stage_index") == 3
      and _r34["prompt"]["931"]["inputs"].get("patch_seconds") == 0.5
      and _r34["prompt"]["902"]["inputs"].get("note") == "keep")
check("34.4 改写**不就地**改调用方传进来的 prompt（§二·A：就地改坏输入对象）",
      _p34["931"]["inputs"]["run_id"] == "")
check("34.4 报告里点名被跳过的连线节点（不许静默）",
      "958" in _r34["report"] and "跳过" in _r34["report"], _r34["report"])

# 34.5 全空 + 显式给值 ⇒ 能救回来（API 用户第一次跑批的正常路径）
_r34b = CORE.sync_run_id({"902": {"class_type": "H3RelayLatentSave", "inputs": {"run_id": ""}}},
                         value="myFilm")
check("34.5 全空时给显式 value ⇒ 改写成功",
      _r34b["changed"] is True
      and _r34b["prompt"]["902"]["inputs"]["run_id"] == "myFilm", _r34b["report"])
check("34.5 全空且不给 value ⇒ 拒绝并说清用法",
      CORE.sync_run_id({"902": {"class_type": "H3RelayLatentSave", "inputs": {}}})["changed"] is False)

# 34.6 一致时是**幂等**的（不改 ⇒ 不复制对象，省一次大 dict 拷贝）
_same34 = {"902": {"class_type": "H3RelayLatentSave", "inputs": {"run_id": "demo"}}}
_r34c = CORE.sync_run_id(_same34)
check("34.6 已一致 ⇒ changed=False 且原样返回（幂等）",
      _r34c["changed"] is False and _r34c["prompt"] is _same34)

# 34.7 公开身份：`relay_core.sync_run_id` 等四个名字必须能直接 import 到（脚本用户的入口）
check("34.7 四个名字在 `relay_core` 顶层可取（API 用户 `from relay_core import sync_run_id`）",
      all(hasattr(CORE, _n) for _n in ("collect_run_ids", "resolve_run_id",
                                       "plan_run_id_sync", "sync_run_id")))
check("34.7 且都在 `__all__` 里（不许只能靠私有路径绕）",
      all(_n in CORE.__all__ for _n in ("collect_run_ids", "resolve_run_id",
                                        "plan_run_id_sync", "sync_run_id")))


# ---------------------------------------------------------------------------
# 34.8 / 34.9 —— 对外接口本身（路由 + 报告函数），**离线**跑（审核 P1-7 / P1-8）
#
# 为什么必须测：它们是**装包用户唯一会用到的入口**（脚本 / API），
# 而节点方法那一层全绿并不能证明路由能跑 —— 今天的教训正是"绿着但坏的"。
# 手法与第 23 组的拼接路由同款（stub `server.PromptServer` + `spec_from_file_location`）。
# ---------------------------------------------------------------------------
try:
    import importlib.util as _ilu34
    import asyncio as _aio34
    import types as _t34

    _KIT34 = _HERE34          # _HERE34 已经是仓库根（上面读 fixture 用的就是它）

    class _Routes34:
        """装饰器透传 stub（加任何 HTTP 方法都不用回来改这里）。"""
        def __getattr__(self, _name):
            def _reg(_path):
                def _deco(fn):
                    return fn
                return _deco
            return _reg

    class _Q34:
        def __init__(self):
            self.history = {}

    class _FakePS34:
        routes = _Routes34()
        prompt_queue = _Q34()
        instance = None

    _FakePS34.instance = _FakePS34()
    _srv34 = _t34.ModuleType("server")
    _srv34.PromptServer = _FakePS34
    _sys34 = sys.modules.get("server")
    sys.modules["server"] = _srv34
    _spec34 = _ilu34.spec_from_file_location(
        "h3latentrelay_pkg_runid", os.path.join(_KIT34, "__init__.py"),
        submodule_search_locations=[_KIT34])
    _pk34 = _ilu34.module_from_spec(_spec34)
    sys.modules["h3latentrelay_pkg_runid"] = _pk34
    _spec34.loader.exec_module(_pk34)

    class _Req34:
        def __init__(self, payload):
            self._p = payload
        async def json(self):
            if self._p is None:
                raise ValueError("empty body")
            return self._p

    def _call34(payload):
        """调路由并把 aiohttp 的 `Response` 拆成 `(status, body)`。

        ⚠ 不能自己造个假 Response：这里只 stub 了 `server` 模块，**`web` 是真的 aiohttp**
        ⇒ 路由返回的是**真** `Response`（`.body` 是 bytes）。曾按"假 Response"写过一次 ⇒ AttributeError。
        """
        async def _go():
            r = await _pk34.h3relay_runid(_Req34(payload))
            raw = r.body if isinstance(r.body, bytes) else str(r.body).encode("utf-8")
            try:
                body = _json34.loads(raw.decode("utf-8") or "{}")
            except Exception:
                body = {"_raw": raw[:200].decode("utf-8", "replace")}
            return int(r.status), body
        return _aio34.run(_go())

    _prompt34 = {
        "902": {"class_type": "H3RelayLatentSave", "inputs": {"run_id": "demo", "stage_index": 0}},
        "961": {"class_type": "H3RelayCopyBridge", "inputs": {"run_id": "demo"}},
        "931": {"class_type": "H3RelayAudioSeam", "inputs": {"run_id": ""}},
        "958": {"class_type": "H3RelayChain", "inputs": {"run_id": "demo"}},
    }

    # 34.8 自检（不带 apply）：回三态裁决 + **点名**冲突
    _r34 = _call34({"prompt": _prompt34})[1]
    check("34.8 `POST /h3relay/runid` 自检：ok + scope=graph + 六类成员",
          _r34.get("ok") is True and _r34.get("scope") == "graph"
          and sorted(m["id"] for m in _r34.get("members", [])) == ["902", "931", "958", "961"],
          repr(_r34)[:200])
    check("34.8 空着的那格在报告里被点名（否则用户不知道要补哪一格）",
          "931" in _r34.get("report", ""), _r34.get("report", ""))

    _rc34 = _call34({"prompt": {"902": {"class_type": "H3RelayLatentSave", "inputs": {"run_id": "a"}},
                                "961": {"class_type": "H3RelayCopyBridge", "inputs": {"run_id": "b"}}}})[1]
    check("34.8 冲突时：state=conflict 且**点名每个值属于谁**",
          _rc34["verdict"]["state"] == "conflict" and "902" in _rc34["report"] and "961" in _rc34["report"],
          _rc34.get("report", ""))

    # 34.9 apply：回一份已统一的 prompt，且**原件不动**
    _orig34 = _json34.loads(_json34.dumps(_prompt34))
    _ra34 = _call34({"prompt": _prompt34, "apply": True, "value": "apiDemo"})[1]
    check("34.9 apply：回一份已统一的 prompt（可直接提交）",
          _ra34.get("changed") is True
          and _ra34["prompt"]["931"]["inputs"]["run_id"] == "apiDemo"
          and _ra34["prompt"]["902"]["inputs"]["run_id"] == "apiDemo",
          repr(_ra34.get("report", ""))[:200])
    check("34.9 apply **不就地**改调用方传进去的那份（§二·A「就地改坏输入对象」）",
          _prompt34 == _orig34)

    # 34.10 三条错误分支：入参缺 / prompt_id 不存在 / prompt_id 取不出 prompt
    _e1_s, _e1 = _call34({})
    check("34.10 既没 prompt 也没 prompt_id ⇒ 400 且说清要什么",
          _e1_s == 400 and "prompt" in _json34.dumps(_e1, ensure_ascii=False), repr(_e1)[:120])
    _e2_s, _e2 = _call34({"prompt_id": "nope"})
    check("34.10 prompt_id 查不到 ⇒ 404 且说清（重启会清空 /history）",
          _e2_s == 404, repr(_e2)[:120])
    _FakePS34.prompt_queue.history = {"pid1": {"prompt": [0, "pid1", {"902": {"class_type": "H3RelayLatentSave",
                                                            "inputs": {"run_id": "z"}}}, {}]}}
    _e3 = _call34({"prompt_id": "pid1"})[1]
    check("34.10 从 history 取 prompt（注意 `entry['prompt'][2]` 的下标 —— 取错没人会发现）",
          _e3.get("ok") is True and _e3["members"][0]["run_id"] == "z", repr(_e3)[:160])
    _FakePS34.prompt_queue.history = {}

    # 34.11 报告函数三态（它是"冲突点名"这条纪律的**唯一**实现，审核 P1-8）
    for _st, _want in (("ok", "一致"), ("empty", "空"), ("conflict", "不一致")):
        _v34 = CORE.resolve_run_id([
            {"id": "902", "run_id": "demo" if _st != "conflict" else "a"},
            {"id": "961", "run_id": "demo" if _st != "conflict" else "b"}] if _st != "empty" else [])
        check("34.11 报告函数三态之一：%s" % _st,
              _v34["state"] == _st and _want in _pk34._describe_run_id(_v34),
              _pk34._describe_run_id(_v34)[:120])
finally:
    if _sys34 is not None:
        sys.modules["server"] = _sys34
