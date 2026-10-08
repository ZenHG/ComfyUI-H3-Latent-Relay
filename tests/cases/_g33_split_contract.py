# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
#
# ruff: noqa: F821  —— 本分片**不是独立模块**。
#   它由 `tests/test_relay_core.py` 用 `exec(compile(src, path, "exec"), globals())`
#   顺序执行：与拆分前的单文件脚本**同一命名空间、同一顺序**。
#   所以 `check` / `CORE` / `os` 都看得见，但不是本文件定义的 ⇒ F821 必报。
#   真正的兜底是**执行**：这条脚本每次全量跑，任何未定义名会当场 NameError，不会静默。

# ============================================================================
# 第 33 组：拆包契约（2026-10-07 拆包引入的语义 —— **锁死不许退化**）
#
# 为什么必须有一条断言守着：
#   2026-10-07 把单文件 `relay_core.py` 拆成 `relay_core/` 包。拆包引入了**两条新语义**，
#   而它们**都不报错**：
#     ① `relay_core` 的**公开身份**靠 `__init__.py` 全量再导出维持 ⇒ 少导一个名字，
#        调用方（`nodes.py` / `tools/` / `exp/`）当场 AttributeError —— 这个还算响；
#     ② 🔴 **猴子补丁转发**：拆包前只有一个 globals，`relay_core.X = v` 对所有读者生效；
#        拆包后 `X` 的模块级全局在**子模块**里 ⇒ 不转发就**静默失效**：
#        断言照样绿、被测分支其实没进去。而且**只转发到"属主"也不够** ——
#        实测 26 个名字被别的子模块 `from .X import name` 拷了副本（`FPS` / `pixel_frames` …）。
#   ⇒ 这两条都属于本仓最怕的「静默失效」，必须有**永不摘除的断言**（规范 §三·3.2）。
#   ⚠️ 改这里之前先读 `relay_core/__init__.py` 末尾的转发层实现。
# ============================================================================

print()
print("=" * 78)
print("### 第 33 组：拆包契约（包身份 / 全量再导出 / 猴子补丁转发）")
print("=" * 78)

import sys as _sys33  # （就近用；本分片由 runner 顺序执行）

# 33.1 它现在是**包**（拆包前是单文件）
check("33.1 `relay_core` 是包（有 `__path__`），入口 = `relay_core/__init__.py`",
      hasattr(CORE, "__path__")
      and os.path.basename(CORE.__file__) == "__init__.py"
      and os.path.basename(os.path.dirname(CORE.__file__)) == "relay_core",
      "file=%s" % getattr(CORE, "__file__", "?"))

# 33.2 `__all__` 与"真的能从包上取到"一一对应（再导出不许漏、不许写幽灵名）
_missing33 = [n for n in CORE.__all__ if not hasattr(CORE, n)]
check("33.2 `__all__` 里每个名字都能从包上取到（再导出零遗漏、无重名）",
      bool(CORE.__all__) and not _missing33
      and len(CORE.__all__) == len(set(CORE.__all__)),
      "缺 %s ｜ __all__ %d 个" % (_missing33[:5], len(CORE.__all__)))

# 33.3 `_HOLDERS` == 「真的持有该名字的子模块」全集（对**全部**导出名逐个核）
_sub33 = list(CORE._SUBMODULES)
if not all(_sys33.modules.get("relay_core." + _s) is not None for _s in _sub33):
    # 兜底：包被以别的名字加载过（如工具侧 `h3latentrelay_pkg`）⇒ 明说跳过，**不静默变弱**
    check("33.3 `_HOLDERS` == 真实持有者集合（跳过：子模块不在 sys.modules 里）", True,
          "sys.modules 缺 relay_core.<子模块> ⇒ 本断言跳过（不是通过）")
else:
    _bad33 = []
    for _n in CORE.__all__:
        _o = CORE._OWNER.get(_n)
        if _o is None or _o not in _sub33:
            _bad33.append("%s: 属主缺失/非法" % _n)
            continue
        _real = sorted(_s for _s in _sub33
                       if _n in vars(_sys33.modules["relay_core." + _s]))
        if sorted(CORE._HOLDERS.get(_n, [])) != _real:
            _bad33.append("%s: _HOLDERS=%s 实际=%s"
                          % (_n, sorted(CORE._HOLDERS.get(_n, [])), _real))
    check("33.3 🔴 `_HOLDERS` == 「真的持有该名字的子模块」全集（对全部 %d 个导出名）"
          % len(CORE.__all__),
          bool(_bad33) is False and len(CORE._HOLDERS) == len(CORE.__all__),
          "%d 处：%s" % (len(_bad33), _bad33[:3]))

# 33.4 🔴 跨子模块副本：`FPS` 有 4 个持有者（_grid/latent/audio/plan）⇒ 必须**全部**改到
#     （这是拆包最隐蔽的一条：只转发属主时，其它三个读到旧值、且不报错）
_fps_old33 = CORE.FPS
_fps_seen33 = _fps_all33 = False
try:
    CORE.FPS = 999.0
    _fps_seen33 = sorted(CORE._HOLDERS.get("FPS", []))
    _fps_all33 = bool(_fps_seen33) and all(
        getattr(_sys33.modules["relay_core." + _s], "FPS", None) == 999.0
        for _s in _fps_seen33)
finally:
    CORE.FPS = _fps_old33
check("33.4 🔴 `relay_core.FPS = v` 转发到**所有持有者**（只写属主会静默失效）",
      _fps_all33 and len(_fps_seen33) >= 2 and CORE.FPS == _fps_old33,
      "持有者 %s ｜ 还原=%s" % (_fps_seen33, CORE.FPS == _fps_old33))

# 33.5 反向：**只在属主模块里用**的名字（无副本）也必须转发到属主 —— `SETTLE_REPEAT_PATH` → `seam`
_sp_old33 = CORE.SETTLE_REPEAT_PATH
_sp_ok33 = False
try:
    CORE.SETTLE_REPEAT_PATH = True
    _sp_ok33 = _sys33.modules["relay_core.seam"].SETTLE_REPEAT_PATH is True
finally:
    CORE.SETTLE_REPEAT_PATH = _sp_old33
check("33.5 🔴 无副本的名字照样转发（`SETTLE_REPEAT_PATH` → `relay_core.seam`）",
      _sp_ok33 and CORE.SETTLE_REPEAT_PATH == _sp_old33,
      "还原=%s" % (CORE.SETTLE_REPEAT_PATH == _sp_old33))

# 33.6 🔴 **真生效**（不只是改了包对象上的字面量）：改 `GUIDE_RUNS` ⇒ 读它的函数行为跟着变
#     `snap_guide_run(35)` 走 `_grid.GUIDE_RUNS`：改成 (5, 1) 后应吸附到 5（原本顶到 22）
_gr_old33 = CORE.GUIDE_RUNS
_gr_ok33 = False
try:
    CORE.GUIDE_RUNS = (5, 1)
    _gr_ok33 = CORE.snap_guide_run(35) == 5
finally:
    CORE.GUIDE_RUNS = _gr_old33
check("33.6 🔴 转发**真生效**：改 `GUIDE_RUNS` ⇒ `snap_guide_run(35)` 由 22 变 5（改完已还原）",
      _gr_ok33 and CORE.snap_guide_run(35) == 22,
      "还原后 snap(35)=%s" % CORE.snap_guide_run(35))
