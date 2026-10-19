# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
#
# ruff: noqa: F821  —— 由 test_relay_core.py exec 进同一命名空间。

# ============ 组37：Trim AV「trim_frames 未接线」硬拦（v0.6.36）============
# 旧默认 0 让「忘接线」与「明确不裁」在服务端完全同形 ⇒ 漏接静默裁 0、接缝重播。
# 守卫放在 Trim AV（而不是桥）是因为 V3 出口拿不到 io.Hidden，桥端探测在 V3 下必失效。

_spec37 = NODES.H3RelayTrimAV.INPUT_TYPES()["required"]["trim_frames"][1]
check("37.1 trim_frames 默认 -1（未接线哨兵）",
      _spec37.get("default") == -1, "得到 %r" % (_spec37.get("default"),))
check("37.2 下限放到 -1（UI 才存得住哨兵）", _spec37.get("min") == -1)

_img37 = torch.zeros(73, 8, 8, 3)
expect_raise("37.3 trim_frames=-1 ⇒ 报错，不再静默裁 0",
             lambda: NODES.H3RelayTrimAV().trim(images=_img37, trim_frames=-1, fps=24.0),
             "未接线")

_ok37, _o37 = arity(NODES.H3RelayTrimAV,
                    {"images": _img37, "trim_frames": 0, "fps": 24.0, "audio": None})
_ok37b, _o37b = arity(NODES.H3RelayTrimAV,
                      {"images": _img37, "trim_frames": 22, "fps": 24.0, "audio": None})
check("37.4 trim_frames=0（首段/独立段）⇒ 原样返回",
      _ok37 and int(_o37[0].shape[0]) == 73,
      "得到 %r" % (int(_o37[0].shape[0]) if _ok37 else None,))
check("37.5 trim_frames=22 ⇒ 真的裁掉 22 帧",
      _ok37b and int(_o37b[0].shape[0]) == 51,
      "得到 %r" % (int(_o37b[0].shape[0]) if _ok37b else None,))

_repo37 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(_repo37, "nodes.py"), encoding="utf-8") as _f37:
    _src37 = _f37.read()
_b37 = _src37.find("def trim(self, images")
_g37 = _src37.find("if pin < 0:", _b37)
_e37 = _src37.find("if pin <= 0:", _b37)
check("37.6 守卫在「pin<=0 早返回」之前（否则等于没拦）", 0 < _b37 < _g37 < _e37,
      "b=%d guard=%d early=%d" % (_b37, _g37, _e37))
check("37.7 桥端不再做 hidden 探测（V3 拿不到 io.Hidden ⇒ 那个守卫必失效）",
      "_trim_slot_consumed" not in _src37 and "require_trim_link" not in _src37)
