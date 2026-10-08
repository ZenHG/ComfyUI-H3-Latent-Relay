# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""H3 Latent Relay 离线单测（零 GPU、零模型、秒级）

跑法（在包目录下）：
    python tests/test_relay_core.py

脚本会自动往上找到 ComfyUI 根目录（本包装在 ``<ComfyUI>/custom_nodes/`` 下时）。
装在别处 / 只 clone 了本包时，用环境变量指定：

    COMFYUI_PATH=/path/to/ComfyUI python tests/test_relay_core.py

只需要 ``torch`` 与 ``safetensors``；不加载任何模型、不碰显存。
但需要能 import 到 ComfyUI（comfy.nested_tensor / node_helpers / folder_paths）。

覆盖：
  1. 时序网格自洽（22 帧=7 步 / 39 帧=12 步 / 192 帧=57 步）
  2. 尾段切片逐位正确 + 起始必须落在 5 步周期边界
  3. 硬错误：分辨率不一致 / 非网格帧数 / 窗口大于段长 → 必须 raise
  4. conditioning 注入：keyframes 合并、钉住区旧锚丢弃、音频 ref 追加
  5. AV latent 落盘往返一致
  6. 裁头重叠：画面音频同裁逐位正确、时长对齐、越界 raise
  7. 节点返回值契约：每个分支返回路数 == len(RETURN_TYPES)
  8. 接缝自检 find_head_jump / describe_head_jump
  9. 段号声明与取源矛盾必须 raise（不得静默直通）
 10. streams_from_latent 对非 NestedTensor 的健壮性（不得按 batch 维误拆）
 11. H3RelayChain 的 status 槽位与前端一致
 12. 沉降帧 settle：pin 与 crop 解耦 + 自动检测（观测端决定，用户零配置）
 13. 沉降三路检测：硬跳验身 / 锐度塌陷-恢复 / 双基准与量纲不变
 14. 拷贝桥（0.4.0）：位级拷贝 + 噪声掩码 + raise 防线
 15. 色档收敛信号（0.4.1）：注噪/taper 收敛尾巴的观测端
 16. 0.4.2 回归：导出音频分支 / 中文 note / 服务端校验 / 掩码设备 / 契约降级缓存
 17. 噪声斜坡 ramp（0.4.3）：软证据接缝——连续掩码 = 逐 token sigma 标签
 18. 复现残留（0.5.0 / 调研 §13 D7）：第 4 路检测——现有三路都看不见复现帧；**默认关闭**
 19. 跨段统计匹配（0.5.0 / 调研 §2）：Reinhard 式一阶+二阶矩，段头↔上段末帧；护栏与无重影；
     **统计量取样窗**（0.5.0 修正）：取紧贴缝那帧 vs 旧口径全区聚合——后者会把首帧推过 guide
 20. 拆节点（0.5.0）：`H3RelayPost` 独立 + TrimAV 追加第 4 路输出 `prev_tail`
 21. 重叠区双向融合 blend（0.5.0 / 调研 §4）：窗形权重（smoothstep / hann），两端导数为 0
 22. 音频缝（0.5.0 / 节点内实现）：长度守恒、最静窗、边界 blend、床环铺、采样率/声道兜底、落盘往返、节点守卫
 23. 音画同步守恒（0.6.2）：`joined` 只在给了画面裁量时才能交叉（否则 raise）；默认等长拼接；
     TrimAV 直接给出 `join_align_seconds` 建议值
 24. 床源选窗语音规避**全路径**（0.6.5）：瓦片档（tile>0）也必须过判据、阈值收紧到 0、
     rank 约定一致、无解时不 raise、报告不静默、O(T²) 回归锁、
     持续帧滤波（瞬态不计入）、退化输入不炸、前缀和能量选窗 == 参照
 25. 🛡 patch 台词守卫（0.6.5）：patch 不许吃本段台词（收缩/关闭/零副作用/关=旧行为）
 26. 多段拼接成片（0.6.7）：探测/体检（含「音频绕过裁重叠」判据）/画面流拷贝无损/
     音频代际 2 → 1（PCM 边车直读 ⇒ 逐位一致；AAC 默认 256k）；
     音频逐段去 priming 对齐/退路（重编码）/history 落盘条目筛选/多生产者候选裁决/
     PyAV 版本兼容/CLI 入口（合成 mp4 + 合成边车，零 GPU）；
     **0.6.18 段序与边车裁决**：提交图数据流定序（`graph_downstream_rank` 纯函数 +
     接反了也跟图走 + 无图/有环 ⇒ 拒收全部边车）、并列同源候选取**列表第一个**（不再按
     长度最接近挑）、落选候选写进报告、段号**不压实**（缺哪段就报哪段）、回扫按真段号排
 27. 画质域 AV latent 分块放大（0.6.8）：块数自选的合规边界（每块 ≥ 2·overlap+1）、
     单块=整段放大恒等、分块边界无跳变、帧数不许被改、上游口径常量不漂移（拆包/回包见节点）
 28. 音频出口 dtype 契约（0.6.11 修静音 bug）：`audio_to_fp32` 的零拷贝快路径、
     fp16/bf16/f64 一律收敛 f32 且逐位无损、键与形状不动、None 直通；
     **事故复现锁**（模拟 VHS 的 `-f f32le` 封装路：原始 dtype 进去 = 数字静音，过本包出口 = 逐位还原）；
     🛡 节点出口契约（「Trim AV」「Audio Seam」**全部** AUDIO 返回路都是 f32，
     而**落盘的 PCM 边车保持 fp16** —— 省一半磁盘，组装层读回时自己转）
 29. 声锚 `voice_anchor`（0.6.19 新增；**0.6.20 补内容守卫**）：不接 ⇒ 逐位不变（回归）、
     纯音频张量 / NestedTensor 两种形态都取到尾窗、`audio_ref` 与钉住的音频前缀**同源**、
     锚短于窗 ⇒ 全长取用 + notes 警告、`pin_audio=False` ⇒ 忽略并写明、
     🔴 **内容守卫**（0.6.20）：全零 / 常量 / 静音 / NaN / 单步 一律 **raise**（不静默），
     且真实尺度（实测下限附近）**不被误伤**（反向自证）
 30. 孤立瞬态抑制 declick（0.6.21 新增）：`ratio=0` ⇒ 逐位直通（老图不受影响）、段首孤立峰与
     「静背景里的一处孤立瞬态」⇒ **压到邻域背景电平**（不挖静音洞）、台词区**逐位不动**、
     形状 `[T]`/`[1,T]`/`[1,1,T]` 与 dtype 保真 + 长度守恒、范围闸外的峰不许碰、
     🔴 **裁帧边界**淡入 + 范围 fallback **从边界起算**（含一条反向断言）、
     `limit_n=0` 不许静默失效、全零 ⇒ 无事件、与 `patch` 的**顺序**（先 patch 后 declick）、
     以及**节点层政策**（`declick_gate`：起点探测 / 24fps 裁帧换算 / fallback 补偿）
 31. 音频参考额度（0.6.22）：官方 `ref_audios` **3 槽** vs 本包**恒占 1 个**（声锚 / 上一段尾窗，
     二选一）⇒ 3+1=4 时 report **必须点名超上限**（不许静默超口径）、2+1=3 提示「正好用满」、
     `kind="video"` 的外观锚**不占音频额度**（数音频块而非 ref 块总数）、无官方块 ⇒ 0 且不报警
 32. 音频参考窗自动定长（0.6.25）：不接声锚时 `audio_ref_seconds=0` ⇒ 窗长按素材**自动** 2~6 s
     （只累计**有声**格；接了声锚 ⇒ 自动改用**声锚自身**）；全零 / NaN ⇒ **fail-closed**、
     空音频不炸、显式秒数优先；自动决策与「封顶 / 不足 / 换原料」一律写进 report（铁律 15）
 33. 拆包契约（2026-10-07）：`relay_core` 的**包身份** + `__init__.py` **全量再导出**零遗漏 +
     🔴 **猴子补丁转发**（`relay_core.X = v` 必须写到**所有**持有 X 的子模块 —— 只写属主会**静默失效**）
 34. `run_id` 跨节点同步的 **API 侧**（2026-10-07 新增）：六类节点各存一份 `run_id`，一字不差才算数
     —— 收集 / 三态裁决（一致·全空·冲突**不猜**）/ 广播规划（**空值不扩散**、**连线接管的不写**）/
     端到端改写（**不丢**同节点其它 input 键、**不就地**改调用方的 prompt、已一致时**幂等**）；
     🔴 与前端 `web/relay_kit_sync.js` 是**两份实现** ⇒ 共用 `tests/parity/run_id_cases.json`
     一份 fixture **跨层对账**（Node 侧另有 `tests/test_run_id_parity.mjs`，两侧都要过）
拆分说明（2026-10-07）：**断言主体**按组分片到 `tests/cases/`，本文件只留
文件头 / 定位 / `check()` / 分片执行 / 汇总。执行语义与拆分前逐位等价（见 runner 里的注释）。
"""

# 🔴 这几个 import 现在**只被分片用**（分片与本文件共享同一命名空间）⇒ 本文件里"看着没用"，
#    但删掉会让分片当场 NameError。F401 在这里是**故意的**。
import importlib.util  # noqa: F401  （分片用：spec_from_file_location 加载 CLI / 包壳）
import os
import sys
import tempfile  # noqa: F401  （分片用：临时目录）

import torch

# ---------------------------------------------------------------- 定位 ComfyUI
# 本包通常装在 <ComfyUI>/custom_nodes/<本包>/，往上两级就是 ComfyUI 根。
# 装在别处时用 COMFYUI_PATH 显式指定。
_KIT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COMFY = os.environ.get("COMFYUI_PATH") or os.path.dirname(os.path.dirname(_KIT_DIR))
if not os.path.isdir(os.path.join(_COMFY, "comfy")):
    _MSG = ("\n[FAIL] 找不到 ComfyUI 根目录（试过：%s）\n"
            "       请把本包装在 <ComfyUI>/custom_nodes/ 下，或设环境变量：\n"
            "       COMFYUI_PATH=/path/to/ComfyUI python tests/test_relay_core.py\n\n"
            % _COMFY)
    sys.stderr.write(_MSG)
    # 本文件是「脚本式」测试（python tests/test_relay_core.py）。
    # 但很多人会顺手跑 `pytest tests/` —— 模块级 SystemExit 会让 pytest 报
    # INTERNALERROR 整个崩掉（不是 skip，是内部错误，体验极差）。
    # 故在 pytest 下改为 skip，脚本下才 exit 2。
    if "pytest" in sys.modules:
        import pytest
        pytest.skip("找不到 ComfyUI 根目录（试过：%s）；设 COMFYUI_PATH 后重试" % _COMFY,
                    allow_module_level=True)
    raise SystemExit(2)
sys.path.insert(0, _COMFY)
sys.path.insert(0, _KIT_DIR)

import comfy.nested_tensor as NT

import relay_core as CORE


PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  " + detail) if detail else ""))


def expect_raise(name, fn, needle=""):
    try:
        fn()
    except Exception as e:
        ok = (needle in str(e)) if needle else True
        check(name, ok, "→ %s: %s" % (type(e).__name__, str(e).splitlines()[0][:90]))
        return
    check(name, False, "→ 未抛异常（应当 raise）")


def make_latent(frames, width=768, height=448, seed=0):
    """合成一段 H3 AV latent：视频 [1,24,T,28,48]、音频 [1,32,2,~] 。"""
    steps = CORE.steps_for_frames(frames)
    assert steps is not None, "%d 帧不是合法网格窗口" % frames
    g = torch.Generator().manual_seed(seed)
    v = torch.randn(1, 24, steps, height // 16, width // 16, generator=g)
    at = int(round(CORE.FRAME_RESCALE * frames))
    a = torch.randn(1, 32, 2, at, generator=g)
    return {"samples": NT.NestedTensor([v, a])}, v, a


# ============================================================================
# 分片执行（2026-10-07 拆分）
# ============================================================================
# 🔴 为什么用 `exec(..., globals())` 而不是把每片做成函数：
#   原文是**扁平脚本** —— 分组之间共享上百个夹具变量（`_cur_a` / `_b24` / `_anc32l` …），
#   且 `check()` 直接写在模块层。改成函数要重写全部夹具的作用域，那是**改语义**；
#   共享命名空间顺序执行则与原文逐位等价（同一 globals、同一顺序）。
# ⚠️ 分片文件名以 `_` 开头 ⇒ pytest 不会单独收集它们（否则会**跑两遍**）。
_CASES = [
    "_g01_grid_contract.py",
    "_g08_seam_settle.py",
    "_g13_post_bridge.py",
    "_g18_match_audio.py",
    "_g23_bed_concat.py",
    "_g27_upscale_anchor.py",
    "_g32_audioref.py",
    # ⚠️ 这一片**不是**从原单文件里切出来的：它是拆包当天**新写**的（拆包契约的回归锁）。
    #    所以 `tmp/do_split_tests.py` 的"拼回 == 原文"自证**不含**它 —— 见该脚本的 PARTS。
    "_g33_split_contract.py",
    # ⚠ 同上：**新写的**（0.6.31 的 API 侧 run_id 同步），不在 `tmp/do_split_tests.py` 的拼回自证里。
    "_g34_run_id_sync.py",
]
for _case in _CASES:
    _case_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cases", _case)
    with open(_case_path, encoding="utf-8") as _fh:
        exec(compile(_fh.read(), _case_path, "exec"), globals())

print()
print("=" * 78)
print("结果：通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("   -", f)
print("=" * 78)
# 脚本式退出码：失败 1 / 成功 0。
# ⚠ 在 pytest 下必须跳过退出：模块级 SystemExit 会让 pytest 报 INTERNALERROR 整个崩掉
# （实测：`COMFYUI_PATH=... pytest tests/` 崩在 `SystemExit: 0`——连全绿都崩）。
if "pytest" not in sys.modules:
    sys.exit(1 if FAIL else 0)
