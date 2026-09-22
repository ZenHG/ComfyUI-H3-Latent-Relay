#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""多段拼接成片（命令行）—— 给 **API / 无头 / 批处理** 用户用。

UI 用户在画布上点「🧩 拼成一条」即可；不想开画布（CI、批处理、自动化、别的语言调的脚本）时用这个。
⚠ 它跟画布按钮走的是**同一份核心代码**（`relay_core.assemble_mp4_segments`）——
本工具**不含第二套拼接实现**，所以两边行为（无损流拷贝 / 音频对齐 / 四项断言 / 退路）永远一致。

用法（用**装了 torch + av 的那个 python**，通常就是 ComfyUI 的解释器）：

    python tools/concat_segments.py s1.mp4 s2.mp4 s3.mp4 -o film.mp4
    python tools/concat_segments.py segs/*.mp4 -o film.mp4 --audio lossless
    python tools/concat_segments.py segs/*.mp4 -o film.mp4 --pcm p1.st p2.st - -   # 第 3、4 段无边车

退出码：**0 = 成片通过四项断言**；1 = 未通过（片可能已写出，留作取证，见 stdout 的 report）。

参数怎么选：
    --audio   aac256（默认，兼容第一） / aac192 / lossless（PCM f32 母版，浏览器不放音）
    --crf     只在画面**必须重编码**时生效（各段规格不一致 / 流拷贝断言不过）；默认 16
    --pcm     逐段 PCM 边车（顺序 = 段序；用 `-` 占位表示该段没有）⇒ 音频只编码一代
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# 包根目录（本文件在 <repo>/tools/ 下）⇒ 直接 import relay_core，不需要装在 site-packages。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# 音轨档 → (编码器, 码率)。与画布上 Chain 的 `audio_out` 三选一**同一套口径**。
AUDIO = {"aac256": ("aac", "256k"), "aac192": ("aac", "192k"), "lossless": ("pcm_f32le", "")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="concat_segments.py",
        description="把 N 个段文件拼成一条成片（画面流拷贝无损 + 音频逐段对齐 + 四项断言）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("segments", nargs="+", help="段文件，**按段号顺序**给（顺序就是成片顺序）")
    ap.add_argument("-o", "--out", required=True, help="成片输出路径（.mp4）")
    ap.add_argument("--audio", choices=sorted(AUDIO), default="aac256",
                    help="成片音轨档（默认 aac256）")
    ap.add_argument("--crf", type=int, default=16, help="画面重编码质量（默认 16；流拷贝路用不到）")
    ap.add_argument("--pcm", nargs="*", default=None,
                    help="逐段 PCM 边车路径（顺序 = 段序；`-` 表示该段没有）")
    ap.add_argument("--json", action="store_true", help="把结果打成机器可读 JSON（脚本用）")
    a = ap.parse_args(argv)

    if a.pcm is not None:
        if len(a.pcm) != len(a.segments):
            ap.error("--pcm 要给 %d 项（用 `-` 占位），实得 %d" % (len(a.segments), len(a.pcm)))
        a.pcm = [None if x == "-" else x for x in a.pcm]

    try:
        from relay_core import assemble_mp4_segments
    except Exception as exc:                      # noqa: BLE001
        print("读不到本包核心（relay_core）：%r\n"
              "    ⇒ 请用**装了 torch 与 av 的 python** 跑本工具，例如 ComfyUI 的解释器：\n"
              "       <ComfyUI>/python_embeded/python.exe tools/concat_segments.py …\n"
              "       （或系统 python 里 pip install torch \"av>=17\" numpy safetensors）" % (exc,),
              file=sys.stderr)
        return 1

    codec, bitrate = AUDIO[a.audio]
    try:
        rep = assemble_mp4_segments([str(p) for p in a.segments], a.out,
                                    audio_codec=codec, audio_bitrate=bitrate,
                                    crf=a.crf, pcm_paths=a.pcm, on_log=print)
    except Exception as exc:                      # noqa: BLE001
        # 交给调用方的必须是**看得懂的一句话**，不是 av/PyAV 的堆栈
        # （最常见 = 段文件路径写错 / 某段还是写一半的文件）。
        print("🔴 拼接过程抛错（%s）：%s\n"
              "    先确认每个段文件都存在、且不是正在写入的半成品；"
              "路径用本机写法（Windows 形如 `<盘符>:\\dir\\s1.mp4`）。" % (type(exc).__name__, exc),
              file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps({"ok": bool(rep.get("ok")), "out": rep.get("out", ""),
                          "mode": rep.get("mode", ""), "pcm_segments": rep.get("pcm_segments", 0),
                          "asserts": rep.get("asserts"), "warnings": rep.get("warnings", []),
                          "seconds": rep.get("seconds")}, ensure_ascii=False))
    else:
        print(rep.get("report", ""))
        if not rep.get("ok"):
            print("\n🔴 未通过四项断言（退出码 1）。片若已写出，留作取证，**别当成品用**。",
                  file=sys.stderr)
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
