# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-H3-Latent-Relay contributors
# 第三方出处与许可见 THIRD-PARTY-NOTICES.md
"""H3 Relay Kit · 节点层

八个节点（0.6.8 起）：续接七件套 + 一个画质域潜空间放大适配器，覆盖"用作者的续接方式"所需的全部接线：

  🔗 H3 续接 Latent 存   —— 把本段的 AV latent 落盘，供下一段读
  🔗 H3 续接 Latent 读   —— 读回上一段的 AV latent
  🔗 H3 续接 拷贝桥      —— 上一段尾部**逐位拷贝**进本段初始 latent + 噪声掩码（钉住区不重绘）；
                             0.6.0 起复合桥：接其第 4 路（conditioning）即原「Latent 桥」钉帧路线，二路可并联
  🔗 H3 续接裁重叠        —— 裁掉续接段头部的重叠帧（视频 + 音频同裁）+ 交接 prev_tail
  🔗 H3 续接后处理 Post   —— 画质域后处理（0.5.0 拆出；不动时间轴/帧数/音频）
  🔗 H3 续接音频缝        —— 音频域：上一段环境声补本段头（长度守恒，零 A/V 位移）
  🔗 H3 续接连跑 Chain    —— 同分组框内自动推进「桥 + 落盘」段号并排队连跑

时间轴 / 画质域 / 音频域分工（0.5.0 起）：
  · H3RelayTrimAV    = 时间轴（裁重叠 / 沉降 / 重影），**冻结不再长 widget**；
  · H3RelayPost      = 画质域（色档对齐 / 补高频 / 锐化）；
  · H3RelayAudioSeam = 音频域（跨段环境声补头 / 床环铺）。
  三条纪律同源：**新功能归到对应域，不往 TrimAV 上挂**。

0.6.0 起只有**一个桥**（复合 CopyBridge），钉法按接法由同一节点提供，不可同图串两个桥实例：
  · 接第 4 路（conditioning）→ 上一段尾段钉进本段 conditioning（管取景/构图，原「Latent 桥」路线）；
  · 接第 0 路（latent）→ 上一段尾部逐位拷贝 + 噪声掩码（管运动，钉住区零重绘）；
  · 两路并联（复合桥）→ conditioning 钉帧 + latent 拷贝**同时**生效，是最稳的续接方式。

接线（替换像素续接时）：
    CSGlideCastCS[0] ─ conditioning ─┐
    CSGlideCastCS[1] ─ latent ───────┤
    H3 续接 Latent 读 ─ context ─────┤→ 🔗 续接 拷贝桥 [3]（conditioning）→ 采样器 positive
    （上一段：采样器 latent → 🔗 续接 Latent 存）

注意：走任一桥时，上游的**像素续接字段（如 H3 Studio 的 cont）必须留空**，
否则两套续接都会往 minimax_keyframes 里塞锚，画面会打架。
"""

from __future__ import annotations

import functools
import inspect
import json
import math
import os

import folder_paths

from . import relay_core as CORE
from . import layout_contract as CONTRACT


# ============================================================================
# 节点层异常上下文（#3）
# ============================================================================
# 节点抛错时，用户只看到 relay_core 内部的一句话（如「list index out of range」），
# 不知道是哪个节点、哪一段、什么入参 ⇒ 排查困难。这里包一层，把
# 「节点名 + 段号 + 关键入参实值」拼到消息前，**原始错误全文原样带上**。
#
# 两条硬约束（照做，否则门槛红）：
#   A. **保留原始异常的完整文本**（不许只取第一行）—— 有断言的 needle 落在异常文本第 2 行。
#   B. **节点自己 raise 的中文错误一律原样放行** —— 判据 = 异常的**最深来源帧**是否在本文件。
#      本包节点层的错误文案是精心写的（含操作指引），再包一层只会把好信息变成噪音。
_ERRCTX_VAL_MAX = 60
# 本文件绝对路径（模块加载时算一次）：约束 B 靠它判断异常来源是不是「本节点自己」
_THIS_FILE = os.path.abspath(__file__)


def _errctx_val(v):
    """入参实值 → 短字符串。只放标量；张量/路径一律只印类型，免得把消息灌爆。"""
    if v is None or isinstance(v, (int, float, bool)):
        return str(v)
    if isinstance(v, str):
        return v if len(v) <= _ERRCTX_VAL_MAX else v[:_ERRCTX_VAL_MAX] + "…"
    return "<%s>" % type(v).__name__


def _errctx_origin_is_self(exc) -> bool:
    """异常**最深来源帧**是否在本文件 ⇒ 是本节点自己 raise 的（约束 B 的机器判据）。"""
    tb = exc.__traceback__
    if tb is None:
        return False
    while tb.tb_next is not None:
        tb = tb.tb_next
    return os.path.abspath(tb.tb_frame.f_code.co_filename) == _THIS_FILE


def _node_errors(*keys):
    """节点方法装饰器：异常时补「节点名 + 段号 + 关键入参」，并原样带上原始错误全文。

    签名与返回形状**零变化**：functools.wraps 保留 __wrapped__ ⇒ 宿主按 FUNCTION
    取到的方法、以及 inspect.signature 看到的签名，都跟没装饰时一样。
    """
    def deco(fn):
        # 装饰时把默认值算好（运行期不再反射），免得每段执行都付一次参数表成本
        try:
            _defaults = {k: p.default for k, p in inspect.signature(fn).parameters.items()
                         if k != "self" and p.default is not inspect.Parameter.empty}
        except (TypeError, ValueError):
            _defaults = {}

        @functools.wraps(fn)
        def wrapper(self, *args, **kwargs):
            try:
                return fn(self, *args, **kwargs)
            except Exception as exc:       # noqa: BLE001
                if _errctx_origin_is_self(exc):
                    raise                  # 约束 B：本节点自己的中文错误 → 原样放行
                try:
                    bound = inspect.signature(fn).bind_partial(self, *args, **kwargs).arguments
                except Exception:          # noqa: BLE001
                    bound = {}
                # 段号：能取到就写「第 N 段」，取不到就明说没有 —— **不伪造**（本仓纪律）
                stage = bound.get("stage_index")
                if stage is None:
                    seg = "段号未提供（本节点无 stage_index 入参）"
                else:
                    try:
                        seg = "第 %d 段" % int(stage)
                    except (TypeError, ValueError):
                        seg = "段号=%r" % (stage,)
                vals = []
                for k in keys:
                    if k in bound:
                        vals.append("%s=%s" % (k, _errctx_val(bound[k])))
                    elif k in _defaults:
                        vals.append("%s=%s" % (k, _errctx_val(_defaults[k])))
                # 约束 A：str(exc) **全文**嵌进去（不许 splitlines()[0]）
                raise RuntimeError(
                    "【%s】处理失败：%s；入参 %s。\n"
                    "原因：%s\n"
                    "（原始异常：%s）"
                    % (type(self).__name__, seg, "、".join(vals) or "无",
                       exc, type(exc).__name__)
                ) from exc

        return wrapper

    return deco


CATEGORY = "H3 Relay Kit"

# 落盘根目录：ComfyUI/output/relay_kit/
_RELAY_ROOT = os.path.join(folder_paths.get_output_directory(), "relay_kit")

# Windows 保留设备名：作为目录名会让 makedirs 抛裸 OSError
_WIN_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {"COM%d" % i for i in range(1, 10)}
    | {"LPT%d" % i for i in range(1, 10)}
)


def _stage_path(run_id: str, stage_index: int) -> str:
    """按 run 标识 + 段号推导落盘路径。段号从 0 开始。"""
    # 非法字符替换为 _（而不是删除）—— 否则 "my/film" 与 "myfilm" 会静默撞进同一目录
    rid = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in str(run_id))
    if not rid.strip("_"):
        raise RuntimeError("run_id 不能为空（用来把同一部片子的各段归到一个目录）。")
    if rid.upper() in _WIN_RESERVED:
        rid += "_"
    idx = int(stage_index)
    if idx < 0:
        raise RuntimeError("stage_index 不能为负（段号从 0 开始，第 1 段=0）。")
    return os.path.join(_RELAY_ROOT, rid, "stage_%05d.safetensors" % idx)


def _audio_stage_path(run_id: str, stage_index: int) -> str:
    """音频落盘路径：与 latent 同目录，文件名前缀 audio_（互不覆盖）。"""
    p = _stage_path(run_id, stage_index)
    return os.path.join(os.path.dirname(p), "audio_%05d.safetensors" % int(stage_index))


def _pcm_ui(path: str):
    """把一份 PCM 边车文件变成 **ui 回显条目**（路径相对 output 目录），供拼成片时取回。

    靠**回显**而不是"按命名约定去翻目录"：段文件名是用户定的（SaveVideo 的 filename_prefix），
    本包不该猜；节点自己写的文件由它自己报路径，最不容易错。
    拼接方拿到后用 `os.path.join(output_dir, subfolder, filename)` 还原。
    """
    try:
        if not os.path.isfile(path):
            return None
        rel = os.path.relpath(os.path.dirname(os.path.abspath(path)),
                              folder_paths.get_output_directory())
        # 同时带上绝对路径：拼接方**优先用它**（相对路径在不同平台上分隔符/根目录都可能对不上）。
        return {"filename": os.path.basename(path), "subfolder": rel, "type": "output",
                "abs_path": os.path.abspath(path)}
    except Exception:      # noqa: BLE001
        return None


def _pcm_sidecar(audio, enabled: bool):
    """把本段（裁后）音频落一份 **PCM 边车**，供拼成片时直读（无损、音频只编码一代）。

    为什么落在「裁重叠」上：整条链上只有它手里那份音频**既与画面等长、又还没经过任何有损编码**
    （它是"音视频同裁"的产物）。落到这里，任何用户的图只要接了这个必备节点就自动受益。

    路径用宿主同款计数器命名（`get_save_image_path`）⇒ 同名不撞车；**不依赖 run_id/段号**
    （那个由拼接方从 history 的节点回显里取回，见 `relay_core.pick_pcm_outputs`）。
    失败一律吞掉并写进 report —— 边车是提质手段，**不该因为它写不成就让整段渲染失败**。
    返回 ``(ui 条目 或 None, 追加到 report 的一行)``。
    """
    if not enabled or audio is None:
        return None, ""
    try:
        folder, filename, counter, subfolder, _ = folder_paths.get_save_image_path(
            "relay_kit/pcm/seg", folder_paths.get_output_directory(), 1, 1)
        path = os.path.join(folder, "%s_%05d_.safetensors" % (filename, counter))
        CORE.save_audio(audio, path, note="pcm_sidecar")
        mb = os.path.getsize(path) / 1024 ** 2
        return (_pcm_ui(path),
                "\n           ♪ **PCM 边车**：%s（%.1f MB）—— 拼成片时直读它，"
                "音频不再二次编码（代际 2 → 1）。" % (path, mb))
    except Exception as exc:      # noqa: BLE001
        return None, ("\n           ⚠ PCM 边车写入失败（**不影响本段产物**，拼成片会自动"
                      "退回 mp4 解码）：%r" % (exc,))


# ==================== 画质域 · AV latent 分块放大（0.6.8）====================

_UPSCALER_NODE = "MinimaxH3LatentUpscaler3D"
_UPSCALER_MODELS_URL = "https://huggingface.co/LBH-123-AI/Minimax_h3_latent_upscaler"




def _comfy_registry() -> dict:
    """ComfyUI 根 `nodes` 模块的 `NODE_CLASS_MAPPINGS`。

    ⚠ 不能直接 `import nodes` 就算数：ComfyUI 装载自定义节点时会把**本包目录**插进
    ``sys.path[0]``，而本包自己就有 ``nodes.py`` ⇒ 早期/测试环境下 `import nodes`
    可能绑到**我们自己这份**（影子模块），拿到的注册表里没有上游节点，就会假报「未装上游」。
    这里按「模块文件是否真在 ComfyUI 根」验身，验不上就退到已加载模块里找真正那份
    （**绝不重复 exec 宿主源码** —— 那玩意副作用不可控）。
    """
    import os
    import sys

    import nodes as _n
    root = os.path.normcase(os.path.abspath(str(getattr(folder_paths, "base_path", "") or "")))
    target = os.path.join(root, "nodes.py")

    def _at_root(mod):
        f = os.path.normcase(os.path.abspath(str(getattr(mod, "__file__", "") or "")))
        return f == target and getattr(mod, "NODE_CLASS_MAPPINGS", None) is not None

    if _at_root(_n):
        return _n.NODE_CLASS_MAPPINGS or {}
    for _name, mod in list(sys.modules.items()):
        if _at_root(mod):
            return mod.NODE_CLASS_MAPPINGS or {}
    raise RuntimeError(
        "取不到 ComfyUI 的节点注册表（ComfyUI 根的 nodes 模块没加载？）。\n"
        "    本节点要在 ComfyUI 进程内运行，不能脱离宿主单跑。")


def _upscaler_cls():
    """取上游学习式 3D 放大节点类（MIT，LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler）。

    走 ComfyUI 的节点注册表而不是 import 它的包 —— 那个包内有 `nodes/` 子包，
    与 ComfyUI 根 `nodes` 模块**同名**，按路径 import 迟早撞车。
    """
    cls = _comfy_registry().get(_UPSCALER_NODE)
    if cls is None:
        raise RuntimeError(
            "本节点是「%s」的 AV 打包 latent 适配器，需要先装作者的节点包（MIT）：\n"
            "    git clone https://github.com/LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler.git\n"
            "    放进 ComfyUI/custom_nodes/ 后重启 ComfyUI。\n"
            "权重与上面那个包共用同一目录 models/latent_upscale_models/，从作者处下载：\n"
            "    %s" % (_UPSCALER_NODE, _UPSCALER_MODELS_URL))
    return cls


def _upscaler_module():
    try:
        cls = _upscaler_cls()
    except RuntimeError:
        return None
    import sys as _sys

    return _sys.modules.get(getattr(cls, "__module__", None))


def _upscale_model_names() -> list:
    """放大权重清单 —— **优先直接调原作者的 `scan_models()`**。

    作者的目录就是 ComfyUI 标准的 `models/latent_upscale_models/`，过滤规则是
    `.pth/.safetensors` ⇒ 装了上游节点就**共用同一份权重文件**，本包不另立目录、不复制第二份。
    没装上游（本包照常加载）时按同一条规则自查。
    """
    mod = _upscaler_module()
    scan = getattr(mod, "scan_models", None)
    if callable(scan):
        try:
            names = [str(n) for n in (scan() or [])]
            if names:
                return names
        except Exception as e:
            print("[H3 Relay] 上游 scan_models() 失败，退回本包自查：%r" % e)
    names = [str(n) for n in (folder_paths.get_filename_list("latent_upscale_models") or [])
             if os.path.splitext(str(n))[1].lower() in (".pth", ".safetensors")]
    return names or ["(权重缺失：见 model_name 提示行的下载指引)"]


def _upscale_model_hint() -> str:
    return ("【填什么】H3 潜空间放大权重，目录 = ComfyUI/models/latent_upscale_models/"
            "（与原作者节点同一个目录、同一份文件，本包不另建目录）。\n"
            "📥 权重请从作者处下载：%s\n"
            "    放好后重启 ComfyUI 才会出现在下拉里。\n"
            "⚠ 不是普通 ESRGAN 放大模型 —— 那些与本节点架构不匹配。" % _UPSCALER_MODELS_URL)


class H3RelayLatentUpscale:
    """🔍 画质域 · H3 AV latent **分块放大**（学习式 3D 潜空间放大，**零去噪**，块数自选）。

    为什么要有这个节点（2026-09-22，全流程 PREVIEW 真跑撞出来的）：
      上游 `MinimaxH3LatentUpscaler3D` ① **只吃普通 [B,C,T,H,W]**，直接喂 H3 的
      **AV 打包 latent（NestedTensor）会当场炸** `'NestedTensor' object has no attribute 'dim'`
      （实测：一采+二采全跑完，到 SR 那步才死）；② 内部分块**写死 32 帧**，用户没法按显存调。
      本节点 = 「拆包 → 逐块调上游 → 回包」的适配层，**零 patch 第三方包**。

    性质（与上游一致，实测核过源码）：
      · **不产生任何去噪/重采样** —— 只放大空间，**时间维原样不动**（`target_size=(t, H2, W2)`）
        ⇒ 钉住区逐位连续、口型、表演都不被改写；帧数域不变 ⇒ TrimAV 的 22 帧裁量照旧。
      · **音频流完全不碰** ⇒ 回包时原样带过，「音频不走 SR」是结构性成立，不是靠旁路接线。
      · 分块只是**显存/时间**的取舍：块内是模型的全部工作集 ⇒ 峰值显存 = 一块。
        `chunks=1` = 整段一次过（最快、最省计算，最吃显存）。
      · 拼接数学与上游同口径（两侧 replicate 填充 + 线性渐变权重 + 按累计权重归一），
        并有单测锁「分块结果 == 整段结果」（见 tests 第 27 组）。
        🔴 **那锁的是拼接数学，不是画面**：上游内部是 3D 体积注意力，切块 = 换跨块时间上下文
        ⇒ `chunks>1` **会改画面**（实测逐帧 MAE 5.62/255，见 README §9 与 docs/02）。
        默认锁 1；只在显存真放不下时才升，**升完必须重看缝**。

    ⚠ 域纪律：本节点属**画质域**（与 `H3RelayPost` 同域，一个动 latent 一个动像素）；
      时间轴（裁重叠）仍归 `H3RelayTrimAV`，音频仍归 `H3RelayAudioSeam`。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "latent": ("LATENT", {
                    "tooltip": "【接法】接本段采样器（或二采）的输出，原样拉线 —— 打包 AV latent 与普通 latent 都吃。\n"
                               "⚠ 续接契约（落盘给下一段的 latent）应当仍取**放大前**的原生 latent：\n"
                               "    下段桥拷的是原生域数据，若拷放大后的再过一次放大 = 双重放大必漂。",
                }),
                "model_name": (_upscale_model_names(), {"tooltip": _upscale_model_hint()}),
                "mode": (["scale by multiplier", "target dimensions", "megapixels"], {
                    "tooltip": "【怎么选】① ×倍数：给个系数（最常用）；② 目标尺寸：直填宽×高（平台规格，如 720×1280）；"
                               "③ 兆像素：按预算定档（1.0 / 2.0 / 4.0 MP）。\n"
                               "三种都由**下面的对应参数**决定，其余参数忽略。",
                }),
                "scale": ("FLOAT", {
                    "default": 2.0, "min": 1.0, "max": 4.0, "step": 0.05,
                    "tooltip": "【×倍数模式】放大系数，1.0 = 不变。⚠ 本节点只放大不缩小（<1 会由上游报错）。",
                }),
                "width": ("INT", {
                    "default": 1280, "min": 64, "max": 8192, "step": 32,
                    "tooltip": "【目标尺寸模式】目标**像素**宽（自动收边到 align 的倍数）。",
                }),
                "height": ("INT", {
                    "default": 704, "min": 64, "max": 8192, "step": 32,
                    "tooltip": "【目标尺寸模式】目标**像素**高（自动收边到 align 的倍数）。",
                }),
                "megapixels": ("FLOAT", {
                    "default": 1.0, "min": 0.06, "max": 16.0, "step": 0.05,
                    "tooltip": "【兆像素模式】目标总像素（MP）。12GB 卡建议 ≤1.5；显存吃紧就调小。",
                }),
                "chunks": ("INT", {
                    "default": 1, "min": 1, "max": 64, "step": 1,
                    "tooltip": "【显存旋钮】沿时间维分几块跑。**1 = 整段一次过**（最快，最吃显存）。\n"
                               "🔴 **显存不足才加大它——`chunks>1` 会改画面**：上游是 3D 体积注意力，\n"
                               "切块 = 换跨块时间上下文，overlap 渐变只能缓解不能抵消\n"
                               "（实测逐帧 MAE 5.62/255，见 README §9）。单测锁的只是**拼接数学**，\n"
                               "不是「换块数不换画面」——升完必须重看缝。\n"
                               "上限受重叠约束：每块至少 2×overlap+1 帧 ⇒ 最大合法块数会写进输出 report，超了直接报错不偷改。",
                }),
                "overlap": ("INT", {
                    "default": CORE.UPSCALE_OVERLAP_DEFAULT, "min": 0, "max": 16, "step": 1,
                    "tooltip": "【块间重叠】两侧各留几帧做渐变混合。默认 5 = 上游模型的 3D 时间核（temporal_kernel）。\n"
                               "分块（chunks>1）时才起作用；0 = 硬切不混合（只在你自己确认无接缝问题时用）。",
                }),
                "align": ("INT", {
                    "default": 32, "min": 1, "max": 512, "step": 1,
                    "tooltip": "【对齐网格】输出宽高收边到此的倍数。32 是 H3 放大模型的硬要求，别改小。",
                }),
                "precision": (["bf16", "fp16", "fp32"], {
                    "default": "bf16",
                    "tooltip": "【精度】bf16/fp16 省显存；fp32 最稳但慢。与权重自身精度一致最保险。",
                }),
                "device": (["cuda", "rocm", "cpu"], {
                    "default": "cuda",
                    "tooltip": "【算在哪】cpu 档只用于排查，实际慢得多。",
                }),
                "force_unload": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "【跑完卸载】把放大模型退回 CPU 腾显存。⚠ 分多块时开启会反复装卸（更慢），\n"
                               "所以默认关；单块且后面还要接采样器时才开。",
                }),
            },
        }

    RETURN_TYPES = ("LATENT", "STRING")
    RETURN_NAMES = ("latent", "report")
    FUNCTION = "upscale"
    CATEGORY = CATEGORY
    DESCRIPTION = ("H3 AV latent 分块放大：拆包→逐块调上游学习式 3D 放大器（零去噪、只放大空间、时间维不动）"
                   "→回包保留原音频；块数自选（⚠ 它是显存旋钮：chunks>1 会改画面，见 README §9）。")

    @_node_errors("chunks", "overlap", "align")
    def upscale(self, latent, model_name, mode, scale, width, height, megapixels,
                chunks, overlap, align, precision, device, force_unload):
        import time
        import torch

        up_cls = _upscaler_cls()
        src = latent["samples"] if isinstance(latent, dict) else latent
        parts = CORE.streams_from_latent(latent)
        video = parts[0]
        audio = parts[1] if len(parts) > 1 else None
        if video.dim() == 4:
            n_frames = 1
        elif video.dim() == 5:
            n_frames = int(video.shape[2])
        else:
            raise ValueError("期望 latent [B,C,T,H,W]（或单帧 [B,C,H,W]），得到 %s。" % (tuple(video.shape),))

        plan = CORE.upscale_chunk_plan(n_frames, chunks=int(chunks), overlap=int(overlap))
        cfg = {"mode": mode}
        if mode == "scale by multiplier":
            cfg["scale"] = float(scale)
        elif mode == "target dimensions":
            cfg["width"] = int(width)
            cfg["height"] = int(height)
        elif mode == "megapixels":
            cfg["megapixels"] = float(megapixels)
        else:
            raise ValueError("未知 mode=%r（可选：scale by multiplier / target dimensions / megapixels）。" % mode)

        if device == "cuda" and torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()

        t0 = time.time()
        calls = [0]

        def _one(seg: "torch.Tensor") -> "torch.Tensor":
            calls[0] += 1
            r = up_cls.execute(latent={"samples": seg}, model_name=model_name, mode=cfg,
                              align=int(align), enable_temporal_chunking=False,
                              force_unload=bool(force_unload), device=device, precision=precision)
            r = getattr(r, "result", r)
            d = r[0] if isinstance(r, (tuple, list)) else r
            out = d["samples"] if isinstance(d, dict) else d
            if not torch.is_tensor(out):
                raise ValueError("上游放大器没返回张量（得到 %s）—— 检查权重与 models/latent_upscale_models/。" % type(out))
            return out.unsqueeze(2) if out.dim() == 4 else out

        v2 = CORE.temporal_tile_upscale(video, _one, plan)
        dt = time.time() - t0

        if int(v2.shape[-1]) == int(video.shape[-1]) and int(v2.shape[-2]) == int(video.shape[-2]):
            print("[H3 Relay] 潜空间放大：目标尺寸与输入相同 ⇒ 原样直通（T=%d，latent %dx%d 未变）"
                  % (n_frames, int(video.shape[-1]), int(video.shape[-2])))

        samples_out = v2 if audio is None else CORE._nested_pair(v2, audio, template=src)
        packed = {"samples": samples_out}

        # latent→像素 的倍数由上游定义，我们不自已猜一个
        import sys as _sys
        _mod = _sys.modules.get(type(up_cls).__module__)
        vae_ds = int(getattr(_mod, "VAE_DOWNSAMPLE", 0) or 0)
        px = (" | 像素域 %dx%d → %dx%d" % (int(video.shape[-1]) * vae_ds, int(video.shape[-2]) * vae_ds,
                                           int(v2.shape[-1]) * vae_ds, int(v2.shape[-2]) * vae_ds)) if vae_ds else ""
        peak = ""
        if device == "cuda" and torch.cuda.is_available():
            peak = " | 显存峰值 %.1f MB" % (torch.cuda.max_memory_allocated() / 1024 ** 2)
        report = ("放大 %dx%d → %dx%d latent（align=%d）%s | 时间维不动 T=%d | "
                  "块数 请求%d→实际%d（每块 %d 帧，overlap %d）| 音频流原样 %s | 上游调用 %d 次 | "
                  "精度 %s/%s | force_unload=%s | 合法块数上限 %d%s | %.1fs"
                  % (int(video.shape[-1]), int(video.shape[-2]), int(v2.shape[-1]), int(v2.shape[-2]),
                     int(align), px, n_frames, int(chunks), plan["n_chunks"], plan["chunk_frames"],
                     int(overlap),
                     tuple(audio.shape) if audio is not None else "无",
                     calls[0], precision, device, bool(force_unload),
                     CORE.max_upscale_chunks(n_frames, int(overlap)), peak, dt))
        print("[H3 Relay] " + report)
        return (packed, report)


class H3RelayLatentSave:
    """把本段采样器输出的 AV latent 落盘。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "latent": ("LATENT", {
                    "tooltip": "【接法】从本段采样器（SelfLiftH3Sampler）的 latent 输出口拉线过来。\n"
                               "作用：把本段拍完的 latent 存成文件，它是下一段的「接力棒」。",
                }),
                "run_id": ("STRING", {
                    "default": "relay",
                    "tooltip": "【填什么】这部片子的名字，比如 myfilm、ep01。\n"
                               "⚠ 必须和「续接 拷贝桥」上的 run_id 一字不差，否则桥找不到文件。\n"
                               "换新片子必须换新名字：同一个名字重跑同段号会覆盖旧文件！\n"
                               "文件存到：ComfyUI/output/relay_kit/<run_id>/",
                }),
                "stage_index": ("INT", {
                    "default": 0, "min": 0, "max": 9999, "step": 1,
                    "tooltip": "【填什么】本段是全片的第几段。第 1 段填 0，第 2 段填 1，第 3 段填 2…\n"
                               "⚠ 必须和「续接 拷贝桥」上的 stage_index 一样大。\n"
                               "改段号时两个节点都要改（用 Chain 节点可以自动改）。",
                }),
                "note": ("STRING", {
                    "default": "",
                    "tooltip": "【可留空】随手写个备注（比如「22帧窗 v2」），存进文件里方便事后分辨版本。",
                }),
            },
        }

    RETURN_TYPES = ("LATENT", "STRING")
    RETURN_NAMES = ("latent", "path")
    FUNCTION = "save"
    CATEGORY = CATEGORY
    # 没有下游消费时也必须执行 —— 落盘本身就是它的产物。
    OUTPUT_NODE = True
    DESCRIPTION = "把本段 AV latent 落盘，供下一段做 latent 续接（零重编码）。"

    @_node_errors("run_id")
    def save(self, latent, run_id, stage_index, note=""):
        path = _stage_path(run_id, stage_index)
        CORE.save_av_latent(latent, path, note=note)
        size_mb = os.path.getsize(path) / 1024 ** 2
        print(
            "[H3 Relay] 已存 stage %d → %s (%.1f MB)\n            %s"
            % (int(stage_index), path, size_mb, CORE.describe_latent(latent))
        )
        return (latent, path)


class H3RelayLatentLoad:
    """读回上一段的 AV latent，供续接。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "run_id": ("STRING", {
                    "default": "relay",
                    "tooltip": "【填什么】与「续接 Latent 存」一致的片子名。\n"
                               "⚠ 不一致 = 找不到上一段的文件，直接报错。",
                }),
                "stage_index": ("INT", {
                    "default": 0, "min": 0, "max": 9999, "step": 1,
                    "tooltip": "【填什么】填「你要读的那一段」的段号 = 本段段号 - 1。\n"
                               "例：现在做第 2 段（本段号 1），这里填 1 → 读第 1 段（stage 0）的文件。\n"
                               "填 0 会报错：第 1 段没有上一段可读。",
                }),
            },
            "optional": {
                "explicit_path": ("STRING", {
                    "default": "",
                    "tooltip": "留空则按 run_id + 段号自动推导；填了就直接读这个文件（用于断点续跑换源）。",
                }),
            },
        }

    RETURN_TYPES = ("LATENT", "STRING")
    RETURN_NAMES = ("context_latent", "info")
    FUNCTION = "load"
    CATEGORY = CATEGORY
    DESCRIPTION = "读回上一段的 AV latent；第 1 段（stage_index=0）没有上一段时会明确报错。"

    @_node_errors("run_id", "explicit_path")
    def load(self, run_id, stage_index, explicit_path=""):
        idx = int(stage_index) - 1
        explicit = (explicit_path or "").strip()
        # 必须先判段号再进 _stage_path：否则 stage_index=0 会先撞上
        # 「stage_index 不能为负」的误导性报错，下面的引导文案永远到不了。
        if idx < 0 and not explicit:
            raise RuntimeError(
                "stage_index=0 是第 1 段，没有上一段可续。\n"
                "    第 1 段请走独立路径（不接本节点，或把续接 拷贝桥的 context_latent 留空）。"
            )
        path = explicit or _stage_path(run_id, idx)
        latent = CORE.load_av_latent(path)
        info = CORE.describe_latent(latent)
        print("[H3 Relay] 已读 stage %d ← %s\n            %s" % (idx, path, info))
        return (latent, info)


class H3RelayTrimAV:
    """裁掉续接段头部的重叠帧（视频 + 音频同裁，A/V 不失步）。

    为什么必须裁：钉住区的前 ``trim_frames`` 帧是模型对上一段尾部的**重生成**
    （实测与原帧逐帧 MAE ≈ 6/255），属于过渡产物。不裁就拼，接缝处会看到
    约 0.9 秒的重播。

    口径与作者一致：裁 **decode 之后的像素帧**（不是裁 latent）——
    latent 的帧跨度按 token 在序列里的相位（k%5）决定，砍头会让相位错位。

    为什么还要多裁几帧（沉降）：钉住区之后模型还会先**复现**上一段若干帧，
    然后才切到本段 prompt；切换点**逐段不同**，可能落在钉住区之外。
    `settle_frames=0`（0.5.0 起默认）时**一帧沉降都不裁** —— 实测「裁沉降」才是缝处
    跳帧的源头（裁 0 帧跳 0.020「几乎无感」／裁 8 帧 0.044／裁 16 帧 0.055），
    留下的只是**清晰度的渐变**。想自动量出切换点并裁掉：填 `-1`。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE", {
                    "tooltip": "【接法】从本段的画面解码节点（VAEDecode）拉线过来。\n"
                               "作用：收到的是「含重复开头」的完整画面，本节点把重复部分裁掉。",
                }),
                "trim_frames": ("INT", {
                    "default": 0, "min": 0, "max": 362, "step": 1,
                    "tooltip": "【不用填！】从「续接 拷贝桥」的第 3 路输出（trim_frames）拉线过来，全自动：\n"
                               "  · 第 1 段桥直通 → 自动 0（不裁）\n"
                               "  · 第 2 段起 → 自动 = 钉住帧数（如 22）\n"
                               "自己手填反而容易和桥对不上。",
                }),
                "fps": ("FLOAT", {
                    "default": 24.0, "min": 1.0, "max": 120.0, "step": 0.001,
                    "tooltip": "【不用动】H3 固定 24 帧/秒，音频按这个换算着一起裁。",
                }),
            },
            "optional": {
                "audio": ("AUDIO", {
                    "tooltip": "【务必接线】从音频解码节点（VAEDecodeAudio）拉线过来。\n"
                               "画面和声音按同一帧数一起裁，保证音画不串位。不接的话声音会比画面长出一截。",
                }),
                # ⚠ 新 widget 必须追加在**最后一个**：widgets_values 按位置对应，
                #    插在中间会让旧工作流里它后面的取值整体错位（见 CHANGES 0.2.1）。
                "settle_frames": ("INT", {
                    "default": 0, "min": -1, "max": 36, "step": 1,
                    "tooltip": "【保持 0】裁不裁「沉降区」——钉住区之后，模型还会先「复现」上一段几帧\n"
                               "才切到本段画面。这几帧略糊，但**裁它们才是缝处跳帧的源头**。\n"
                               "🔴 实测（同一条段，只改裁量）：\n"
                               "     裁 0 帧 → 跳帧 0.020（**几乎无感**）\n"
                               "     裁 8 帧 → 0.044（能看到跳）\n"
                               "     裁16 帧 → 0.055（明显跳）\n"
                               "   留下的那几帧只是**清晰度的渐变**（先略糊、再恢复），\n"
                               "   人眼对这种渐变的容忍度极高 → **拿渐变换突变不划算**，故默认不裁。\n"
                               "  ·  0（默认）= 不裁沉降：只裁钉住区（上段尾的复现，免费）→ 缝处无跳\n"
                               "  · -1 = 自动：按本段实际画面量出该裁几帧（治糊，但**会引入跳帧**，须目检）\n"
                               "  ·  N = 固定多裁 N 帧（想各段等长时用：全片填同一个数）\n"
                               "日志里「裁首 X 帧 = 钉住 Y + 沉降 Z」就是它的结果。",
                }),
                # ⚠ 继续追加在**最后**（同上铁律）：缝帧重影，2026-09-15 新增。
                #    🔴 默认 0（关）：数值上能压平最大单帧跳，但**观感实测更差**——
                #    总位移守恒（只 −7%）、异常帧数 1→2（一跳变两跳 = 卡两下），
                #    且重影帧本身是「鬼影」（内容不属于任何一段）。详见 relay_core 注释。
                "seam_ghost": ("INT", {"advanced": True,
                    "default": 0, "min": 0, "max": 6, "step": 1,
                    "tooltip": "【保持 0】缝帧重影（极短交叉溶）：把裁后首帧换成"
                               "「上段末帧 ⊕ 本段首帧」的加权混合。\n"
                               "⚠ **默认关**：实测它只把「一跳」拆成「两跳」，"
                               "总位移没减（−7%）、异常帧数翻倍（1→2）→ 观感是「卡两下」，"
                               "比单帧瞬跳更明显，且混合帧本身是个可见鬼影。\n"
                               "  · 0（默认）= 关。**要消除跳帧，请减少 settle 裁切量**"
                               "（裁切才是跳的源头），或在画质域修复糊区。\n"
                               "  · >0 = 开启（仅在确知本段有收益时用，需目检确认）。",
                }),
                "seam_ghost_alpha": ("FLOAT", {"advanced": True,
                    "default": 0.5, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "仅重影档用：上段末帧的权重（0.5 = 对半）。默认档下无效。",
                }),
                # ⚠ 继续追加在**最后**（同上铁律）：画质域修复，2026-09-16 新增。
                #    背景：settle_frames 默认改 0（不裁沉降）后，成片段头会保留几帧「重绘糊」。
                #    裁它 → 引入跳帧；不裁 → 留糊。**第三条路 = 画质域修**（不裁、不动时间轴）。
                "settle_sharpen": ("FLOAT", {"advanced": True,
                    "default": 0.0, "min": 0.0, "max": 1.5, "step": 0.05,
                    "tooltip": "【先保持 0，按需开】糊区锐化（画质域修复）：对裁后**开头若干帧**\n"
                               "做 unsharp mask，强度从缝端最强线性衰减到 0。\n"
                               "  · 0（默认）= 关。\n"
                               "  · 0.4–1.0 = 推荐区间（先试 0.6，再目检有没有 halo/噪点）。\n"
                               "**帧数守恒、不动音频、零采样开销**——不改变时间轴，故不会引入跳帧。\n"
                               "⚠ 诚实边界：锐化只能恢复**对比度**，不能恢复**已丢失的真实细节**。\n"
                               "   对「结构还在、只是软」的重绘糊有效；细节彻底丢了就救不回来。",
                }),
                "settle_sharpen_frames": ("INT", {"advanced": True,
                    "default": 24, "min": 0, "max": 64, "step": 1,
                    "tooltip": "糊区锐化作用帧数（从裁后首帧起，强度线性衰减到 0）。\n"
                               "默认 24 帧 ≈ 1 秒；实测糊区约 16–20 帧内恢复到基准，故 24 有余量。",
                }),
                # ⚠ 继续追加在**最后**（同上铁律）：低频残差传递，2026-09-16 新增。
                #    借鉴 ComfyUI_MiniMaxH3_Director 的 `match_export_opening_grade`
                #    （`_lowfreq_appearance_pull`）：只吸收上段的低频色档/布光，保留本段细节。
                "lowfreq_pull": ("FLOAT", {"advanced": True,
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【按需开，默认 0】低频残差传递：把裁后**开头若干帧**的低频\n"
                               "（色档/亮度/布光）拉向**上段末帧**，但**保留本段自己的细节与姿态**。\n"
                               "算法：out = src + w·(blur(guide) − blur(src))，纯加性。\n"
                               "  · 0（默认）= 关\n"
                               "  · 0.7 = 上游同款参数（实测缝点阶跃 0.0399→0.0110）\n"
                               "  · 1.0 = 更强（实测 →0.0011，比后处理 seam_grade 还好 5×）\n"
                               "**关键优势**：只动低频 ⇒ **不复制上段姿态轮廓 ⇒ 无重影**；\n"
                               "   而「全 RGB 混合」（交叉溶/重影）实测会把锐度砍掉 49%（画面花）。\n"
                               "帧数守恒、不动音频、零采样开销。",
                }),
                "lowfreq_frames": ("INT", {"advanced": True,
                    "default": 12, "min": 0, "max": 48, "step": 1,
                    "tooltip": "低频对齐作用帧数（从裁后首帧起，权重线性衰减到 0）。\n"
                               "上游同款用 12 帧；实测缝区影响就在前 12 帧内。",
                }),
                "lowfreq_blur": ("INT", {"advanced": True,
                    "default": 64, "min": 8, "max": 128, "step": 8,
                    "tooltip": "低频尺度（盒式模糊核）。越大越只对齐大尺度色档/布光；\n"
                               "上游用 64。实测 32/64 差异很小（阶跃 0.0011 vs 0.0024）。",
                }),
                # ⚠ 继续追加在**最后**（同上铁律）：后处理层补全，2026-09-16。
                #    以下四个都是**画质域修复**：帧数守恒、不动音频、不动时间轴
                #    ⇒ 结构上不可能引入跳帧。默认全 0（关），旧行为逐位不变。
                "deconv_strength": ("FLOAT", {"advanced": True,
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【按需开，默认 0】段头**反卷积去模糊**（Wiener 逆滤波）。\n"
                               "与「糊区锐化」的区别：锐化只是**放大高频**（噪声一起放大），\n"
                               "反卷积是按假设的 PSF **逆推原始信号**，理论上能真正还原细节。\n"
                               "0=关；0.5~1.0 = 混合比。糊得越重，radius 要越大。",
                }),
                "deconv_radius": ("FLOAT", {"advanced": True,
                    "default": 1.5, "min": 0.5, "max": 6.0, "step": 0.5,
                    "tooltip": "反卷积假设的模糊半径（像素）。段头糊得越重越大；\n"
                               "太大易出振铃（此时把 deconv_strength 降下来）。",
                }),
                "detail_borrow": ("FLOAT", {"advanced": True,
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【按需开，默认 0】**段体高频迁移**：段头保留自己的低频（内容/构图），\n"
                               "高频换成段体的高频结构（同一场景/光照下 ⇒ 不会引入异质内容）。\n"
                               "与「低频残差」互补：那个补低频，这个补高频。\n"
                               "⚠ 若段头与段体内容差异大（人物位移大），会带出纹理错位。",
                }),
                "detail_blur": ("INT", {"advanced": True,
                    "default": 9, "min": 3, "max": 64, "step": 2,
                    "tooltip": "高频迁移的分界尺度（盒式模糊核）：越大则被搬走的高频越粗。",
                }),
                "hist_match": ("FLOAT", {"advanced": True,
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【按需开，默认 0】**直方图匹配**：把段头的色阶分布对齐到段体。\n"
                               "比「低频残差」更强 —— 那个只对齐**均值**，这个对齐**整条分布**\n"
                               "（亮部/暗部的比例也一致）。0=关；1=完全对齐。",
                }),
                "wb_match": ("FLOAT", {"advanced": True,
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【按需开，默认 0】**灰世界白平衡校正**：把段头的 R:G:B 比例（色温/色调）\n"
                               "对齐到段体。与「低频残差」正交 —— 那个管亮度总量，这个管色温。\n"
                               "对症「色温滑档」型漂移。增益限幅 ±25% 防偏色。",
                }),
                # 🔴 2026-09-17 新增（RESEARCH_seam_frontier §2 方向二）：**跨段**统计匹配。
                #   与上面三个的区别：hist_match / wb_match / deconv / detail_borrow 都是
                #   「段头 ↔ **本段段体**」（段内）；本组是「段头 ↔ **上段末帧**」（**段间**），
                #   治的正是成片缝上那个亮度阶跃（copy 桥实测 0.0402）。
                #   ⚠ 铁律：新 widget 一律**追加在 optional 末位**（旧工作流取值不前移）。
                "match_prev": ("FLOAT", {"advanced": True,
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【按需开，默认 0】**跨段统计匹配**：把段头的色档/曝光分布\n"
                               "（逐通道均值 + 标准差）对齐到**上段末帧**——即缝的另一侧。\n"
                               "与「低频残差」的区别：那个只做低频**加性**（一阶）；\n"
                               "这个补上**二阶（对比度）+ 逐通道色度**（Reinhard 式）。\n"
                               "**只对齐统计量，不复制姿态 ⇒ 无重影。**建议从 0.5 起试。",
                }),
                "match_prev_frames": ("INT", {"advanced": True,
                    "default": 12, "min": 1, "max": 96, "step": 1,
                    "tooltip": "【配合 match_prev 用】作用帧数：从裁后首帧起算，\n"
                               "权重从 match_prev 线性衰减到 0（缝端最强 → 尾端不动）。",
                }),
                "match_prev_gain_max": ("FLOAT", {"advanced": True,
                    "default": 1.15, "min": 1.0, "max": 2.0, "step": 0.05,
                    "tooltip": "【护栏，一般不用动】逐通道对比度增益上限（σ目标/σ源 的截断）。\n"
                               "调大 = 允许更猛的对比度对齐，但可能把已通过的内容改坏。",
                }),
                "match_prev_offset_max": ("FLOAT", {"advanced": True,
                    "default": 0.06, "min": 0.0, "max": 0.3, "step": 0.01,
                    "tooltip": "【护栏，一般不用动】逐通道亮度/色度偏移上限（μ目标−μ源 的截断）。\n"
                               "调大 = 允许更大的色档修正，但过大易见「整段换色」。",
                }),
                # 🔴 2026-09-21 新增（E3/E4 可达性改造）：观测层的 run 标识。
                #   E3（DTW）/ E4（漂移）都是**只读观测**，不该依赖裁量开关（settle<0）才能执行。
                #   填了 ⇒ E4 把本段外观三元组追加进 <run>/appearance_log.jsonl 并读历史算漂移曲线；
                #   留空（默认）⇒ 只算本段三元组、不写盘，行为与加此 widget 之前一致。
                "run_id": ("STRING", {"default": "",
                    "tooltip": "【观测用，可留空】续接 run 标识（与「续接 Latent 存」的 run_id 一致）。\n"
                               "填了才会把每段外观统计写入 <run>/appearance_log.jsonl 并算漂移曲线（E4）。",
                }),
                # 🔴 2026-09-22 新增（音频代际 2 → 1）：本段音频的 **PCM 边车**。
                #    默认**开**：它只多写一个文件，节点输出与成片**逐位不变**（不是"改行为"），
                #    所以不受"新功能默认关"的约束 —— 关掉它只是让拼接退回逐段解 mp4 音频。
                "save_pcm": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "【建议保持开】把本段（裁后）音频额外存一份 **PCM 边车**。\n"
                               "为什么需要它：段文件的音轨是 AAC 有损的（用户在落盘节点编的），\n"
                               "拼成片时若从 mp4 解码再编，就是**第二次有损**（代际 2）。\n"
                               "存了边车 ⇒ 拼接直读无损 PCM ⇒ **音频只编码一代**；\n"
                               "再配 Chain 的「无损母版」音轨档，则成片音轨**零新增代际**。\n"
                               "  · 开（默认）= 多写一个 ~2 MB/段 的文件到 output/relay_kit/pcm/\n"
                               "  · 关 = 不写（老行为），拼接时该段退回 mp4 解码并如实报出\n"
                               "⚠ 写失败只会在日志里提示，**不影响本段渲染**（拼接自动退路）。",
                }),
                # 🔴 2026-09-25 新增（GG 拍板）：只读观测的总闸。**默认关**。
                #    本地跑批要常看这几行 ⇒ 入口显式传 True（l1_api / chain_auto.sh）。
                "diagnostics": ("BOOLEAN", {
                    "default": False,
                    "label_on": "🧪 诊断开（+3 路观测）", "label_off": "诊断关（默认）",
                    "tooltip": "【默认关，本地产线才开】三路**只读观测**——都**不参与任何裁量**，\n"
                               "关掉不改变任何帧 / latent / 音频（纯打印）：\n"
                               "  · 🧪 DTW 对齐代价（E3）：整段对齐下来平均每步多贵；\n"
                               "  · 裁量→跳跃曲线：各 settle 档位对应的缝处跳跃倍数（裁之前就能权衡）；\n"
                               "  · 🧪 外观三元组 + 漂移曲线（E4，需同时填 `run_id`）：跨段亮度/对比度/锐度漂移。\n"
                               "开 = 多约 **0.4 s/段 CPU**（0.8MP；2MP 约 0.5–1.5 s），**零 GPU**。\n"
                               "⚠ 本地产线要常看这几行 ⇒ 跑批入口（l1_api / chain_auto.sh）显式传 True；\n"
                               "   对外默认关：多数用户不看，纯开销。",
                }),
            },
        }

    RETURN_TYPES = ("IMAGE", "AUDIO", "STRING", "IMAGE")
    RETURN_NAMES = ("images", "audio", "report", "prev_tail")
    FUNCTION = "trim"
    CATEGORY = CATEGORY
    DESCRIPTION = (
        "裁掉续接段头部的重叠帧，并自动把钉住区之后那段「复现帧」一并裁掉"
        "（默认 settle_frames=0，只裁钉住区）；视频与音频同裁，避免重播与音画失步。"
    )

    @_node_errors("trim_frames", "fps", "settle_frames", "save_pcm")
    def trim(self, images, trim_frames=0, fps=24.0, audio=None, settle_frames=0,
             seam_ghost=0, seam_ghost_alpha=0.5,
             settle_sharpen=0.0, settle_sharpen_frames=24,
             lowfreq_pull=0.0, lowfreq_frames=12, lowfreq_blur=64,
             deconv_strength=0.0, deconv_radius=1.5,
             detail_borrow=0.0, detail_blur=9,
             hist_match=0.0, wb_match=0.0,
             match_prev=0.0, match_prev_frames=12,
             match_prev_gain_max=1.15, match_prev_offset_max=0.06,
             run_id="", save_pcm=True, diagnostics=False):
        CONTRACT.enforce()   # 裁帧算术同样依赖上游网格，先过契约
        # 服务端防线：widget 的 min=1.0 只挡 UI，API 提交 fps=0/NaN 会一路除到底
        try:
            fps = float(fps)
        except (TypeError, ValueError):
            fps = float("nan")
        if not math.isfinite(fps) or fps <= 0:
            raise RuntimeError(
                "fps 必须是 (0, +∞) 内的有限正数，得到 %r。\n"
                "    H3 固定 24 帧/秒——把「裁重叠」的 fps 改回 24 即可（音频按它换算着一起裁）。"
                % (fps,)
            )
        pin = int(trim_frames)
        before = int(images.shape[0])
        if pin <= 0:
            msg = "[H3 Relay] 裁 0 帧 → 不裁（独立段或纯首段）。"
            print(msg)
            # 第 1 段（也是本段音频最完整的一段）同样要落边车 —— 否则成片第一段白丢一代。
            _u, _n = _pcm_sidecar(audio, save_pcm)
            if _n:
                print(msg + _n)
            _r = (images, audio, msg, images[:1])
            return {"ui": {CORE.PCM_UI_KEY: [_u]}, "result": _r} if _u else _r

        # 沉降帧：钉住区之后模型还会先复现上一段若干帧才切到本段 prompt。
        # 切换点是**逐段不同的量**，所以默认让它自己量（-1），而不是让用户猜。
        want = int(settle_frames)
        bj_note = ""
        if want < 0:
            settle, jump, base = CORE.detect_settle(images, pin)
            why = ("自动检测：切换信号 %.1f / 基准 %.1f（帧差突变/锐度塌陷/色档收敛）" % (jump, base)) if settle \
                else ("自动检测：未检出切换点（帧差 %.1f / 基线 %.1f，无突变亦无锐度塌陷）" % (jump, base))
            # 🔴 2026-09-15 新增：**边界跳帧观测**。沉降公式在「跳变正好落在钉住区边界」
            #   （j == pin-1）时返回 0 —— 语义没错，但**掩盖了「边界本身是断的」**。
            #   故单独量出来报给用户：**裁切治不了跳变，但至少要看得见。**
            bj, d_edge, d_base = CORE.boundary_jump_ratio(images, pin)
            if bj > CORE.BOUNDARY_JUMP_WARN:
                bj_note = ("\n           ⚠ **边界跳帧 %.1f×**（钉住区末帧→首帧新内容：帧差 %.4f / 段内基线 %.4f）"
                           "—— 这是模型接续处的断点；**多裁只是把断点往后挪，治不了它**。"
                           % (bj, d_edge, d_base))
            # 多通道观测剖面：**节点实测的 raw decode 数据**，成片里看不到
            # （H.264 编码会改变锐度/色阶基准 → 离线用成片反推必错）。
            # GG 2026-09-15：「不只是锐度，色阶、明暗等都需要」——沉降检测本就三路，只报一路=只开三分之一窗。
            prof = CORE.observation_profile(images, pin)
            if prof["sharp"]:
                n_sh = 18
                bj_note += (
                    "\n           观测剖面（钉住区后逐帧，节点实测 raw decode）\n"
                    "             锐度÷基准 : %s%s\n"
                    "             亮度      : %s%s\n"
                    "             色阶 R/G/B: %s / %s / %s\n"
                    "             帧差      : %s%s\n"
                    "             基准：锐度 %.6f；体区色档 %.4f / %.4f / %.4f"
                    % (" ".join("%.2f" % v for v in prof["sharp"][:n_sh]),
                       " …" if len(prof["sharp"]) > n_sh else "",
                       " ".join("%.4f" % v for v in prof["luma"][:12]),
                       " …" if len(prof["luma"]) > 12 else "",
                       " ".join("%.4f" % v for v in prof["rgb"][0][:10]),
                       " ".join("%.4f" % v for v in prof["rgb"][1][:10]),
                       " ".join("%.4f" % v for v in prof["rgb"][2][:10]),
                       " ".join("%.4f" % v for v in prof["diff"][:18]),
                       " …" if len(prof["diff"]) > n_sh else "",
                       prof["ref_sharp"], prof["ref_rgb"][0], prof["ref_rgb"][1], prof["ref_rgb"][2]))
        else:
            settle, why = want, "手动指定"

        # 🧪 E3 DTW 残留量（调研 §11-A）：只读观测，**不进裁量契约**。
        #   🔴 2026-09-21 移出 `want<0` 分支 —— 原位置使产线 settle=0（默认）时**永不执行**
        #      ⇒ 功能等于不存在（《开发验收纪律》：产线路径可达 + 有消费者才叫完成）。
        #      观测层不该依赖裁量开关（观测 ≠ 裁量）。
        #   与 0.5.0 第 4 路（scan_head_repeat 的最小 MAE 单点）互补：
        #   那个答「从 pin 起连续复现几帧」，这个答「整段对齐下来
        #   平均每步多贵」⇒ 能看出残留强度与不连续形态。
        #   ⚠ 故意不喂给 detect_settle：DTW 路把「运动中的相似姿态」
        #     也给低代价，单独当裁量会多吃内容（2026-09-20 定）。
        # 🔴 2026-09-25 GG 拍板：三路只读观测**默认关**（`diagnostics=False`）。
        #   实测三路合计 **0.361 s/段**（0.796MP/90 帧）—— 对外是纯开销；本地要常看 ⇒ 入口传 True。
        #   注意它们**不参与任何裁量**（纯打印）⇒ 关掉不改变任何帧/latent/音频。
        #   （`want<0` 分支里的 detect_settle / boundary_jump_ratio / observation_profile
        #    属**裁量链路**，不受本开关影响。）
        _diag = bool(diagnostics)
        _dtw = CORE.head_repeat_dtw(images, pin) if _diag else None
        if _dtw:
            bj_note += (
                "\n           🧪 DTW 对齐代价（只读观测，不改裁量）："
                "窗→钉住区 %.5f｜比较 %d vs %d 帧"
                % (_dtw["align_cost"],
                   _dtw["frames"][0], _dtw["frames"][1]))
        # 裁量→跳跃曲线：成片缝 = raw[pin-1] → raw[pin+settle]（相隔 settle+1 帧），
        # **裁得越多、跳得越大**（GG：裁切=时间跳跃=跳切）。裁之前就把它算出来供权衡。
        curve = CORE.trim_jump_curve(images, pin) if _diag else None
        if curve:
            bj_note += ("\n           裁量→跳跃曲线（settle : 归一跳跃）：%s"
                        % "  ".join("%d:%.1f×" % (s, r) for s, r in curve[:13]))
        # 🧪 E4 漂移曲线（调研 §11-B）：逐段外观三元组 + 跨段斜率，**只读观测**。
        #   🔴 2026-09-21 接进产线路径（此前 `nodes.py` 零引用 = 死代码）。
        #   run_id 空（默认）⇒ 只算本段三元组、不写盘；填了 ⇒ 追加 <run>/appearance_log.jsonl
        #   并读全部历史算 drift_curve。**同一把尺子逐段量**（segment_appearance_stats 统一口径）。
        if _diag:
            try:
                _st = CORE.segment_appearance_stats(images)
                bj_note += ("\n           🧪 外观三元组（E4 只读）：mean %.5f ｜ std %.5f ｜ 锐度 %.5f"
                            % (float(_st[0]), float(_st[1]), float(_st[2])))
                if run_id:
                    _rd = os.path.join(_RELAY_ROOT, str(run_id))
                    os.makedirs(_rd, exist_ok=True)
                    _lg = os.path.join(_rd, "appearance_log.jsonl")
                    _hist = []
                    if os.path.isfile(_lg):
                        with open(_lg, "r", encoding="utf-8") as _fh:
                            for _ln in _fh:
                                if _ln.strip():
                                    _hist.append(tuple(json.loads(_ln)["stats"]))
                    _hist.append(tuple(float(v) for v in _st))
                    with open(_lg, "a", encoding="utf-8") as _fh:
                        _fh.write(json.dumps({"stats": [float(v) for v in _st]},
                                             ensure_ascii=False) + "\n")
                    if len(_hist) >= 2:
                        _dc = CORE.drift_curve(_hist)
                        bj_note += ("\n           🧪 漂移曲线（E4，累计 %d 段）：亮度斜率 %+.5f ｜ "
                                    "对比度 %+.5f ｜ 锐度 %+.5f（后两项无量纲、可跨片比）"
                                    % (len(_hist), _dc["slope"], _dc["std_slope"], _dc["sharp_slope"]))
            except Exception as _e:      # 观测失败**绝不阻断渲染**，但必须可见（不静默）
                bj_note += "\n           🧪 E4 观测失败（不影响裁切）：%r" % (_e,)
        if pin + settle >= before:
            settle = max(0, before - 1 - pin)
            why += "（已夹到本段长度上限）"

        n = pin + settle
        out = CORE.trim_head_frames(images, n)
        # 🔴 2026-09-15 新增：**缝帧重影**（极短交叉溶，GG 认可的解法）。
        #   裁后首帧换成「上段末帧 ⊕ 本段首帧」的混合 → 把缝处一跳**拆成两个半跳跨两格**，
        #   一闪而过 → 体感约等于一镜到底。**帧数守恒、不动音频、零采样开销。**
        #   上段末帧 = `images[pin-1]`（钉住区最后一帧 = 上段尾的复现）——节点手里就有，无需额外输入。
        # 🔴 2026-09-16 新增：后处理层补全（P3/P5/P6/P7）—— 画质域修复，不碰时间轴。
        #   执行顺序：先对齐色调（直方图/白平衡），再补高频（反卷积/段体迁移），最后锐化收口。
        post_note = ""
        if float(hist_match) > 0.0:
            out = CORE.match_hist_head_to_body(out, int(settle_sharpen_frames),
                                               float(hist_match), body_start=40)
            post_note += "\n           ✦ **直方图匹配**：段头色阶分布对齐段体（强度 %.2f）。" % float(hist_match)
        if float(wb_match) > 0.0:
            out = CORE.match_white_balance(out, int(settle_sharpen_frames),
                                           float(wb_match), body_start=40)
            post_note += "\n           ✦ **白平衡校正**：段头 R:G:B 比例对齐段体（强度 %.2f）。" % float(wb_match)
        if float(deconv_strength) > 0.0:
            out = CORE.deconv_head_zone(out, int(settle_sharpen_frames),
                                        float(deconv_strength), float(deconv_radius))
            post_note += ("\n           ✦ **反卷积去模糊**：段头 Wiener 逆滤波（强度 %.2f / 半径 %.1f）。"
                          % (float(deconv_strength), float(deconv_radius)))
        if float(detail_borrow) > 0.0:
            out = CORE.borrow_detail_from_body(out, int(settle_sharpen_frames),
                                               float(detail_borrow), int(detail_blur), body_start=40)
            post_note += ("\n           ✦ **段体高频迁移**：段头高频换用段体结构（强度 %.2f / 尺度 %d）。"
                          % (float(detail_borrow), int(detail_blur)))

        # 🔴 2026-09-16 新增：**低频残差传递**（借鉴 Director 的段间引导低频对齐）。
        #   只吸收上段末帧的低频色档/布光，**保留本段细节与姿态** ⇒ 无重影。
        #   与「全 RGB 混合」有本质区别：后者实测把作用区锐度砍掉 49%（画面花）。
        lowfreq_note = ""
        # 🔴 2026-09-17 新增（RESEARCH_seam_frontier §2 方向二）：**跨段统计匹配**。
        #   作用域 = 段头 ↔ **上段末帧**（缝的另一侧），治成片缝上的亮度阶跃。
        #   与下面 lowfreq_pull 的区别：那个只做低频**加性**（一阶矩）；本组补**二阶矩 + 逐通道色度**。
        #   ⚠ 两者**作用域重叠**（都是段头↔上段末帧）⇒ 建议二选一，同时开需自行确认不过修。
        #   ⚠ 必须放在 lowfreq_pull **之前**：先对齐分布，再修低频残差。
        if float(match_prev) > 0.0 and pin >= 1:
            out = CORE.match_prev_stats(out, images[pin - 1], int(match_prev_frames),
                                        float(match_prev), float(match_prev_gain_max),
                                        float(match_prev_offset_max))
            post_note += ("\n           ✦ **跨段统计匹配**：开头 %d 帧的色档/曝光分布对齐上段末帧"
                          "（强度 %.2f / 增益上限 %.2f / 偏移上限 %.3f）——只对齐统计量，不复制姿态 ⇒ 无重影。"
                          % (int(match_prev_frames), float(match_prev), float(match_prev_gain_max),
                             float(match_prev_offset_max)))

        if float(lowfreq_pull) > 0.0 and pin >= 1:
            out = CORE.lowfreq_pull(out, images[pin - 1], int(lowfreq_frames),
                                    float(lowfreq_pull), int(lowfreq_blur))
            lowfreq_note = (
                "\n           ✦ **低频残差传递**：开头 %d 帧对齐上段末帧的低频"
                "（强度 %.2f / 尺度 %d）——只动低频，不复制姿态 ⇒ 无重影。"
                % (int(lowfreq_frames), float(lowfreq_pull), int(lowfreq_blur)))

        # 🔴 2026-09-16 新增：**画质域修复**——糊区锐化（GG 定方向：先试零 GPU 传统锐化）。
        #   默认 settle_frames=0（不裁沉降）后，成片段头保留几帧「重绘糊」；
        #   裁它 → 跳帧（裁 16 帧跳 0.055）；不裁 → 留糊。
        #   **第三条路 = 画质域修**：不裁、不动时间轴，只提升糊区高频（故不可能引入跳帧）。
        sharpen_note = ""
        if float(settle_sharpen) > 0.0 and int(out.shape[0]) > 0:
            out = CORE.sharpen_head_zone(out, int(settle_sharpen_frames), float(settle_sharpen))
            sharpen_note = (
                "\n           ✦ **糊区锐化**：开头 %d 帧 unsharp（强度 %.2f，缝端最强 → 尾端 0）"
                "——不裁、不动时间轴，故不引入跳帧。"
                % (int(settle_sharpen_frames), float(settle_sharpen)))
        # 🔴 2026-09-16 重做：改用 `seam_crossfade`（两侧都是**连续运动序列**）。
        #   旧实现把「上段末帧」**复制 N 次**去混合 → k>1 时内容冻结（重复帧）、
        #   k=1 时只是"一跳拆两跳"（异常帧数 1→2）——**素材用错了**，故观感更差。
        #   正解：上段**末尾 k 帧** 对 本段**开头 k 帧** 逐帧渐变（t 从 1/(k+1) 递进到 k/(k+1)）。
        #   `seam_crossfade` 自带裁切（cut = pin + settle），故它直接产出裁后序列。
        ghost_note = ""
        if int(seam_ghost) > 0 and pin >= 1:
            out = CORE.seam_crossfade(images, n, pin, int(seam_ghost))
            ghost_note = ("\n           ✦ **极短交叉溶 %d 帧**（上段末尾 %d 帧 ⊕ 本段开头 %d 帧，"
                          "权重按 1/%d 逐帧递进）——两侧都是连续运动序列，帧数守恒、不动音频。"
                          % (int(seam_ghost), int(seam_ghost), int(seam_ghost), int(seam_ghost) + 1))
        audio_out = CORE.trim_audio_head(audio, n, float(fps)) if audio is not None else None
        after = int(out.shape[0])
        line = ("[H3 Relay] 裁首 %d 帧 = 钉住 %d + 沉降 %d ｜ %s\n"
                "           画面 %d → %d 帧（%.3fs → %.3fs）"
                % (n, pin, settle, why, before, after, before / float(fps), after / float(fps))) + bj_note + lowfreq_note + post_note + sharpen_note + ghost_note
        if audio is not None:
            line += "；音频 %d → %d 采样点" % (
                int(audio["waveform"].shape[-1]), int(audio_out["waveform"].shape[-1]))
        else:
            line += "；⚠ 未接 audio，画面裁了但音频没裁 → 可能音画不同步"
        # 🔴 2026-09-21 铁律·音画同步：把「音频拼接该填多少」**直接算给用户**。
        #   以前要用户自己从「裁首 N 帧」换算 fps，于是没人填对 ⇒ 默认落在「旧缩短语义」上
        #   ⇒ 每缝后段音频提前 cross 秒。少一次心算 = 少一类错位片。
        if n > 0:
            line += ("\n           ▶ 音频拼接（AudioSeam 的 joined）：J-cut 守恒路的 "
                     "`join_align_seconds` 填 **%.4f**（= 裁首 %d 帧 ÷ %g fps）；"
                     "若只想等长拼接不做交叉 ⇒ `join_cross_ms` 填 0（默认）。"
                     % (n / float(fps), n, float(fps)))
        print(line)
        # 接缝自检：裁后起点若仍有突变，说明沉降量不够
        check = CORE.describe_head_jump(out)
        print(check)
        # 🔴 2026-09-17 新增第 4 路输出 `prev_tail`（**追加在末位**，旧工作流不受影响）：
        #   = 钉住区最后一帧 = `images[pin-1]`（节点手里本来就有，无需额外输入）。
        #   ⚠ **口径（2026-09-19 补正）**：它是否等于"上一段的末帧"取决于走哪条桥——
        #     拷贝桥下前 pin 帧是逐位拷贝的上段尾 ⇒ 就是上段末帧；
        #     复合桥的 conditioning 钉帧路线（原 Latent 桥 cond 路线）下前 pin 帧是本段重画的 ⇒ 只是近似（代理误差 0.006，
        #     比要修的缝阶跃 0.0007 还大 8 倍）。故这条路线（仅接第 4 路不接第 0 路）上别拿它当参照去对齐。
        #   用途：喂给 `H3RelayPost` 的 `guide`，让跨段统计匹配 / 低频残差传递有"缝的另一侧"可对齐。
        tail = images[pin - 1: pin] if pin >= 1 else images[:1]
        _u, _n = _pcm_sidecar(audio_out, save_pcm)
        if _n:
            print(_n.strip("\n"))
        _r = (out, audio_out, line + "\n" + check + _n, tail)
        return {"ui": {CORE.PCM_UI_KEY: [_u]}, "result": _r} if _u else _r


class H3RelayChain:
    """🔗 续接连跑（Chain）—— 纯控制节点，不在执行路径上。

    前端按钮（web/relay_kit_chain.js）会找到同一张图里的
    「续接 拷贝桥 + 续接 Latent 存」，自动推进它们的 stage_index 并排队：

        ▶ Run      按当前段号跑一次（不满意可重跑，覆盖同段号文件）
        ✔ Approve  段号 +1（桥和落盘同步改），排队跑下一段
        ⏩ 连跑     按 segments 自动循环：跑完一段 → 段号+1 → 再跑（0 = 无限）
        ⏹ Stop     当前采样跑完后停止推进
        ↺ Reset    段号归 0，从第 1 段重来
        🧩 拼成一条 把已经跑完的 N 段拼成一条成片（0.6.7）

    0.6.7 加的两件事（**都默认关 = 老图逐位不变**）：
      · **词分发**（`prompts` + `prompt_target`）：填了 `prompts` 时，跑第 k 段前自动把第 k 块词
        写进出词节点 —— 连跑不再"反复提交同一份词"。
      · **自动拼接**（`auto_concat` + `concat_name`）：连跑结束后把 N 段拼成一条成片，
        画面**流拷贝（无损）**、音频按段去 priming 对齐（秒级）。

    使用前提：把 Chain、桥、落盘三个节点拉进**同一个分组框**（框选 → 右键 → 添加分组），
    否则按钮找不到要推进的节点。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "segments": ("INT", {
                    "default": 5, "min": 0, "max": 9999, "step": 1,
                    "tooltip": "【连跑几段】点「⏩ 连跑」时生效：5 = 连跑 5 段后自动停；0 = 不停，直到点 ⏹ Stop。\n"
                               "其他按钮不受它影响。填了 prompts 时，段数还应 ≤ 词块数。",
                }),
            },
            "optional": {
                # 只用于**显示**：前端把状态写进这一格。
                # 必须是**最后一个** widget —— 这样旧工作流里少这一格时只走默认值，
                # 不会让它前面的取值错位（见 CHANGES 0.2.1 的槽位错位说明）。
                "status": ("STRING", {
                    "default": "",
                    "tooltip": "【不用填，自动显示】前端把连跑状态写在这里：\n"
                               "当前段号 / 已排队 / ⚠ 分组没放对 / ⚠ 排队失败 / 词分发结果 / 拼接结果…\n"
                               "点了按钮没反应时，先看这一格说了什么。",
                }),
                # —— 0.6.7：词分发 + 自动拼接。**一律追加在 status 之后**，
                #    旧工作流少这几格只走默认值，位置不错位。
                "prompts": ("STRING", {
                    "default": "", "multiline": True,
                    "tooltip": "【可选｜多段各自的词】用**单独一行 `---`** 分隔每段的词：\n"
                               "第 1 块喂第 1 段、第 2 块喂第 2 段……留空 = 老行为（不换词）。\n"
                               "⚠ 块数不够要跑的段数时**不排队**并报错 —— 免得你以为换了词、其实没有。\n"
                               "UI 用：把 N 段词一次性粘进来，跑之前不用再手动改画布上的词。",
                }),
                "prompt_target": ("STRING", {
                    "default": "",
                    "tooltip": "【可选】词写进哪个格子。两种写法：\n"
                               "  · `683` —— 节点 id，自动挑它的词格；\n"
                               "  · `683.h3_data` —— 点名到字段（`h3_data` 是 JSON 字符串时会只改里面的 prompt）。\n"
                               "留空 = 自动探测：优先 `CSGlideCastCS` 的 `h3_data`，其次官方出词节点的 `prompt`；\n"
                               "**探测到多个就报错**，这时把节点 id 填进来。",
                }),
                "auto_concat": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "【可选】⏩ 连跑跑完就**自动把 N 段拼成一条**成片（画面流拷贝无损，秒级）。\n"
                               "关（默认）= 老行为，跑完只得到 N 个 mp4；想拼时点「🧩 拼成一条」。",
                }),
                "concat_name": ("STRING", {
                    "default": "",
                    "tooltip": "【可选】成片文件名（不用写扩展名）。留空 = 用落盘的 run_id。\n"
                               "成片存到 ComfyUI 的 output/ 目录，拼完路径会写进 status 那一格。",
                }),
                # —— 2026-09-22 追加（成片音轨档 / 画质档）：**继续追加在末位**，槽位安全同上。
                "audio_out": (["aac_256k", "aac_192k", "pcm_lossless"], {
                    "default": "aac_256k",
                    "tooltip": "【成片音轨档】拼成片时音轨怎么编：\n"
                               "  · `aac_256k`（默认）— mp4 + AAC 256k。**兼容第一**，浏览器能放。\n"
                               "      （实测 SNR ≈ 48 dB，已经比画面那一代的 44.6 dB 还高 ⇒ 听感透明）\n"
                               "  · `aac_192k` — 同上，省 ~25% 体积，SNR ≈ 40 dB。\n"
                               "  · `pcm_lossless` — **无损母版**：音轨不再做任何有损编码。\n"
                               "      ⚠ 代价：文件约 4.4 MB/秒（44.1k 立体声 f32），且**浏览器预览没声音**\n"
                               "      （PCM 音轨浏览器不放）⇒ 这是给剪辑/归档的档，不是预览档。\n"
                               "前提：拼接时能读到各段的 **PCM 边车**（「裁重叠」的 save_pcm 开着）。\n"
                               "  读不到就退回 mp4 解码 —— 此时 `pcm_lossless` 仍是「零新增有损」（1 代）。",
                }),
                "video_crf": ("INT", {
                    "default": 16, "min": 0, "max": 51, "step": 1,
                    "tooltip": "【画面重编码质量】crf 越小越清晰、文件越大。默认 16。\n"
                               "⚠ **默认走画面流拷贝（无损），这一格根本用不到** —— 它只在两种时候生效：\n"
                               "  · 各段**规格不一致**（分辨率/帧率/像素格式对不上）⇒ 拷贝不可行，只能重编码；\n"
                               "  · 流拷贝路的成片断言不过 ⇒ 自动退回重编码。\n"
                               "参考（本仓实测，416×736 段）：crf 16 ≈ 720 kbps；crf 0 仍**不是**无损\n"
                               "（RGB→YUV 4:2:0 先丢，天花板 46.5 dB）⇒ 想要真无损请从**落盘节点**下手，\n"
                               "不是把这里调到 0。",
                }),
            },
        }

    RETURN_TYPES = ()
    FUNCTION = "noop"
    CATEGORY = CATEGORY
    DESCRIPTION = (
        "自动连跑控制器：配合桥 + 落盘使用。\n"
        "第一步：把 Chain、桥、落盘放进同一个分组框。\n"
        "第二步：桥和落盘 stage_index 填 0，点 ▶ Run 拍第 1 段。\n"
        "第三步：填 prompts（`---` 分块，第 k 块喂第 k 段）⇒ 点 ⏩ 连跑，词自动换、段号自动推进。\n"
        "第四步（可选）：开 auto_concat 让跑完自动成片，或点「🧩 拼成一条」当场拼。\n"
        "         成片音轨默认 AAC 256k（`audio_out` 可换 192k 或无损母版）；画面一律流拷贝（无损）。\n"
        "状态显示在 status 格子里（点了没反应就看它）。"
    )

    @_node_errors("segments")
    def noop(self, segments=5, **kwargs):
        # **kwargs 吞掉 status / prompts / prompt_target / auto_concat / concat_name
        # 这类只给前端读的输入（它们不参与执行）。
        return {}


class H3RelayCopyBridge:
    """0.4.0 拷贝桥：上一段尾部 AV latent **逐位拷贝**进本段初始 latent + 噪声掩码。

    0.6.0 起本节点是**唯一桥**：conditioning 钉帧路径已折叠进第 4 路（复合桥，见下），不再另设节点。
      · Latent 桥（钉帧）：模型重绘上一段尾段 → 有复现漂移/发糊风险（0.3.x 实测），
        观测端沉降检测兜底；
      · 拷贝桥（本节点）：``mask_mode="hard"`` 时钉住区不重绘（掩码 0 区每步被钉回
        拷贝 latent），复现伪影这一类从机制上消失；掩码消费走 ComfyUI 原生 H3 契约与
        SelfLift 的 noise_mask 支持——**不绑定任何特定采样器**。
        ⚠️ ``mask_mode="taper"`` **不钉住**（每帧留 seam_min~100% 重绘自由度），只作对照实验档。
        ``mask_mode="ramp"``（0.4.3）是"软证据"档：连续掩码按原生 H3 契约就是逐 token 的
        sigma 标签（sigma_row = m·sigma_video），远端硬钉、缝端以 ramp_top 强度 harmonize，
        每一步都被 (1−m) 锚回拷贝尾——治硬接缝的色档/曝光台阶，全程有锚（与 taper 相反）。
    输出 INT = 应裁帧数（=拷贝跨度），接 H3RelayTrimAV 的 trim_frames；
    TrimAV 的 settle_frames 保持 0（0.5.0 起默认不裁沉降），观测端继续守接管帧。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "latent": ("LATENT", {
                    "tooltip": "本段初始 AV latent（CSGlideCastCS / EmptyH3LatentAV 的 latent 输出）。\n"
                               "上一段尾部会逐位写进它的开头，并附噪声掩码。",
                }),
                "context_latent": ("LATENT", {
                    "tooltip": "上一段的完整 AV latent（🔗 H3 续接 Latent 读）。\n"
                               "第 1 段（stage 0）不要接本节点——没有上一段可拷。",
                }),
                "context_frames": ("INT", {
                    "default": 22, "min": 5, "max": 124, "step": 17,
                    "tooltip": "拷贝窗口帧数，只认 5+17k 网格（5/22/39/56/73/90/107/124）。\n"
                               "须小于本段帧数（前缀必须给新内容留位置）。",
                }),
            },
            "optional": {
                "mask_mode": (["hard", "taper", "ramp", "blend", "window"], {
                    "default": "hard",
                    "tooltip": "【分型】🟢 生产档：hard（真续接唯一推荐，保持默认）｜🟡 对照档：ramp｜🔴 实验档：taper / blend\n"
                               "掩码语义：每步输出 = 模型生成 * m + 上段尾 * (1-m)。**m=0 才钉住，m=1 是重绘。**\n"
                               "🟢 hard = 全窗 m=0（钉住区零重绘）——真续接请保持它，下面的都不用看；\n"
                               "🟡 ramp = 0.4.3 噪声斜坡（软证据）：远端 m=0 硬钉 → 缝端线性升到 ramp_top；\n"
                               "        原生契约把连续 m 当逐 token sigma 标签（sigma_row = m * sigma_video），\n"
                               "        缝侧轻度 harmonize、每步仍被 (1-m) 锚回拷贝尾。\n"
                               "🔴 taper = 头部 m=1.0（**完全重绘**）线性降到缝端 seam_min ——\n"
                               "        ⚠ **钉住区实际没有被钉住**，只是软提示；仅「渐进接管」对照实验。\n"
                               "🔴 blend = 重叠区双向窗形融合（ramp 的窗形版，FlowLong 式 Hamming 混合）——\n"
                               "        与 ramp 同语义不同曲线；同样仅对照实验。\n"
                               "🔴🔴 **2026-09-19 修正一条过时结论**：旧 tooltip 写「ramp 与 hard 三项完全等同 ⇒ 无增益」——\n"
                               "     那是用**看不见跳帧的指标集**（亮度阶跃/锐度/运动余弦）测的，已作废。\n"
                               "     用含「缝后单帧尖峰 + 缝对构图相关」的判据重测：**四个掩码档在缝处全都跳**，\n"
                               "     只是失败模式不同——hard 保取景但运动尖峰大；ramp/blend 运动平了但**取景被改写**。\n"
                               "     ⇒ 「钉住」与「释放」在单机制内对立，**单靠掩码调参治不好**。\n"
                               "     详见 CHANGES.md 0.6.0「掩码档实测」一节（判据与口径同步记在那里）。",
                }),
                "taper_tokens": ("INT", {"advanced": True,
                    "default": 4, "min": 1, "max": 12, "step": 1,
                    "tooltip": "【🔴 实验档 taper 专属】缝端前多少个 token 参与线性过渡。",
                }),
                "seam_min": ("FLOAT", {"advanced": True,
                    "default": 0.10, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【🔴 实验档 taper 专属】缝端掩码下限（m 值）。\n"
                               "0 = 缝端完全硬锁；>0 表示缝端仍留同等比例的重绘自由度\n"
                               "（0.3 即缝端 30% 重绘）。注意它只管缝端——头部恒为 1.0 全重绘。",
                }),
                "pin_audio": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "把上一段音频尾也拷进本段音频 latent 开头（采样上下文用）。\n"
                               "掩码只做视频流；可见的声画拼接仍归「裁重叠」与组装层。",
                }),
                "ramp_top": ("FLOAT", {"advanced": True,
                    "default": 0.25, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【🟡 对照档 ramp 专属】缝端最大 m（= 该 token 参与去噪的 sigma 比例）。\n"
                               "0 = 退化为 hard；0.25 默认 = 缝端 25% 强度 harmonize。\n"
                               "🔴 **1.0 是有理论依据的最优档，别怕它**：\n"
                               "  · FIFO-Diffusion(NeurIPS 2024) Theorem 3.3：对角去噪的误差\n"
                               "    **以噪声等级差为上界** O(|σ_τf − σ_τ1|)；\n"
                               "  · 本档的掩码只在**前 N 个 token** 上铺斜坡，第 N+1 个 token 直接是 1.0。\n"
                               "    若 ramp_top<1，边界处就有 (1−ramp_top) 的**噪声断崖**，直接进误差上界；\n"
                               "    **ramp_top=1.0 让斜坡恰好抵达满噪声 ⇒ 断崖归零**。\n"
                               "  · ⚠ 与 taper 不是一回事：ramp 只有**最后一个** token 到 ramp_top，\n"
                               "    前面的 token 仍被 (1−m) 锚回拷贝尾；taper 是**头部 m=1（完全不钉）**。\n"
                               "  · 实测单调规律（7 token 窗）：窗内最大台阶 = ramp_top/6，\n"
                               "    台阶越大缝处阶跃越大（hard 0.0076 < 0.50 档 0.0281 < 0.95 档 0.1040）\n"
                               "    ⇒ 想在 ramp_top 大时仍不恶化，**必须同时加长窗口**（RELAY_FRAMES）。",
                }),
                "ramp_tokens": ("INT", {"advanced": True,
                    "default": 0, "min": 0, "max": 12, "step": 1,
                    "tooltip": "【🟡 对照档 ramp 专属】参与斜坡的缝端 token 数；0 = 整个拷贝窗铺开。\n"
                               "小值（如 2~3）= 「只松缝、锁运动」的窄斜坡。",
                }),
                "anchor_latent": ("LATENT", {
                    "tooltip": "【0.5.0 统计纠偏基准，可选】全局锚段（通常第 1 段）的 AV latent。\n"
                               "接上后：拷贝前缀的逐通道均值/方差被拉向锚段（Reinhard 矩匹配）。\n"
                               "纠偏只作用于被裁掉的钉住前缀——不碰上一段成片；但采样时每步钉回的\n"
                               "就是这份已复位色档的上下文 → 本段新内容跟着回到全局色档。\n"
                               "治的是「色档/曝光随接力次数漂移」。用「续接 Latent 读」+ 指定\n"
                               "explicit_path 读第 1 段的 stage 文件即可。",
                }),
                "anchor_blend": ("FLOAT", {
                    "default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【0.5.0 纠偏强度】0~1：拷贝前缀被拉向锚段统计量的比例。\n"
                               "1 = 全量对齐（默认，锚段就是审美基准时用）；0.5 = 一半。\n"
                               "锚段与本段允许有意的风格差异时调低。不接 anchor_latent 时无效。",
                }),
                # 🔴 2026-09-17 新增（RESEARCH_seam_frontier §4 方向四）：**重叠区双向融合**。
                #   与 ramp 同语义、不同曲线：ramp 线性升，blend 用**窗形**升（两端导数 0）。
                #   依据 FlowLong / Unified Long Video Inpainting 的滑窗 Hamming 混合
                #   （arXiv:2511.03272）：重叠区由两个独立估计加权平均，权重取窗函数。
                #   ⚠ 本节点**无存量 UI 工作流**引用（已核），故新 widget 可紧邻同族项放。
                "blend_top": ("FLOAT", {"advanced": True,
                    "default": 0.50, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【🔴 实验档 blend 专属】缝端**模型占比**上限（对应 ramp 的 ramp_top）。\n"
                               "0 = 退化成 hard；越大越信任本段自己的预测。建议 0.5 起试。",
                }),
                "blend_tokens": ("INT", {"advanced": True,
                    "default": 0, "min": 0, "max": 12, "step": 1,
                    "tooltip": "【🔴 实验档 blend 专属】参与融合的缝端 token 数；0 = 整个拷贝窗铺开。\n"
                               "小值（2~3）= 「只融缝、锁运动」。",
                }),
                "blend_shape": (list(CORE.BLEND_SHAPES), {"advanced": True,
                    "default": CORE.BLEND_SHAPE_DEFAULT,
                    "tooltip": "【仅 blend 模式】窗形：\n"
                               " · smoothstep = x²(3−2x)，多项式 S 曲线（默认）\n"
                               " · hann = (1−cos πx)/2，余弦 S 曲线\n"
                               "两者都是**两端导数为 0**的单调升 ⇒ 与窗外衔接无折角、过渡更柔。",
                }),
                # ⚠ 追加在**最后**（保护既有 widgets_values 的按位对槽）
                "window_top": ("FLOAT", {"advanced": True,
                    "default": CORE.SEAM_WINDOW_TOP, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【🟡 对照档 window 专属】**窗函数峰值**（中心处允许的最大重绘自由度）。\n"
                               "0 = 退化为 hard（整窗硬钉）。\n"
                               "🔴 与 ramp 的关键差别：ramp 是**单调升**（缝端最自由）；\n"
                               "   本档是**对称窗**——缝端那一头**回落到低位 ⇒ 缝端重新钉牢**。\n"
                               "依据（2026-09-19 联网核实）：VideoMerge(arXiv:2503.09926) 用 sine weighting\n"
                               "   替代线性加权以消除「abrupt change in semantics」；Diff-VF(arXiv:2608.05976)\n"
                               "   的 WWS「中心权重高、边界权重低」，消融证明去掉它 → **窗口边界突然跳跃**。",
                }),
                "window_shape": (list(CORE.WINDOW_SHAPES), {"advanced": True,
                    "default": CORE.WINDOW_SHAPE_DEFAULT,
                    "tooltip": "【仅 window 模式】窗形：\n"
                               " · sine = sin(π(i+0.5)/n)，端点低但不为 0（默认，留一点自由度防死钉）\n"
                               " · hann = (1−cos2πx)/2，端点**严格 0**（两端完全硬钉）",
                }),
                # ⚠ 以下为 0.6.0「复合桥」折叠槽位——**一律追加在末尾**（守 widgets_values 按位对槽）
                "conditioning": ("CONDITIONING", {
                    "tooltip": "【可选·复合桥】接上后，本节点**同时**在 conditioning 上追加钉帧\n"
                               "（管取景/构图），与 latent 钉住窗（管运动）**并联生效**。\n"
                               "两条路径改的是不同对象 ⇒ 互不冲突。\n"
                               "不接 = 只做拷贝桥（行为与旧版完全一致）。\n"
                               "🔴 实测（0.3MP 受控）：接上后缝处亮度阶跃 0.0009 vs 单 cond 桥 0.0097（10.8× 更好）。",
                }),
                "run_id": ("STRING", {
                    "default": "relay",
                    "tooltip": "【可选·复合桥】片子名。用来自动读外观锚段（ref_anchor_stage ≥ 0 时）。",
                }),
                "stage_index": ("INT", {
                    "advanced": True, "default": 0, "min": 0, "max": 9999, "step": 1,
                    "tooltip": "【可选·复合桥】本段段号；与 ref_anchor_stage 一起用于自动读锚。",
                }),
                "ref_anchor_latent": ("LATENT", {
                    "tooltip": "【可选·复合桥】全局外观锚（通常第 1 段）的 AV latent。\n"
                               "走 minimax_refs 原生协议，近零噪声全程骑乘每一步 = attention sink，防长程漂移。",
                }),
                "ref_anchor_stage": ("INT", {
                    "advanced": True, "default": -1, "min": -1, "max": 9999, "step": 1,
                    "tooltip": "≥ 0 = 没接 ref_anchor_latent 时自动读 output/relay_kit/<run_id>/stage_<该值> 当锚。",
                }),
                "ref_anchor_frames": ("INT", {
                    "advanced": True, "default": 5, "min": 1, "max": 64, "step": 1,
                    "tooltip": "外观锚取该段**开头**多少帧（取头不取尾）。",
                }),
            },
        }

    RETURN_TYPES = ("LATENT", "STRING", "INT", "CONDITIONING")
    RETURN_NAMES = ("latent", "report", "trim_frames", "conditioning")
    FUNCTION = "bridge"
    CATEGORY = CATEGORY
    DESCRIPTION = (
        "把上一段尾部 AV latent 逐位拷进本段开头并附噪声掩码（钉住区不重绘），\n"
        "消除「复现发糊/漂移」这一类接缝伪影。输出 trim_frames 接「裁重叠」。\n"
        "⚠️ 与「Latent 桥」（conditioning 钉帧经第 4 路折叠进复合桥，不另设节点）。"
    )

    @_node_errors("context_frames", "mask_mode", "run_id")
    def bridge(self, latent, context_latent, context_frames,
               mask_mode="hard", taper_tokens=4, seam_min=0.10, pin_audio=True,
               ramp_top=0.25, ramp_tokens=0,
               blend_top=CORE.SEAM_BLEND_TOP, blend_tokens=0,
               blend_shape=CORE.BLEND_SHAPE_DEFAULT,
               window_top=CORE.SEAM_WINDOW_TOP,
               window_shape=CORE.WINDOW_SHAPE_DEFAULT,
               anchor_latent=None, anchor_blend=1.0,
               conditioning=None, run_id="relay", stage_index=0,
               ref_anchor_latent=None, ref_anchor_stage=-1, ref_anchor_frames=5):
        CONTRACT.enforce()
        # 0.6.0：Latent 桥（H3RelayMotionContext）已删除，本节点成为**唯一桥**。
        # 原 Latent 桥「段号>=1 却无来源 -> 必须 raise（不得静默直通）」是反坏片关键守卫，
        # 路由收敛后必须保留在此：
        _idx = int(stage_index)
        if context_latent is None and _idx >= 1:
            raise RuntimeError(
                "stage_index=%d 表示本段是第 %d 段，但没有可续接的上一段 latent：\n"
                "    · context_latent 没接线，且\n"
                "    · run_id 是空的（或只有空白）\n"
                "再跑下去会「静默直通」—— 产出的是独立段而不是续接段，\n"
                "但界面与日志都显示成功。\n"
                "    第 2 段起请把 run_id 填成与「续接 Latent 存」完全一致的名字；\n"
                "    若这确实是独立段，把 stage_index 改回 0。"
                % (_idx, _idx + 1))
        if context_latent is None:
            # 直通（独立段，不续接）：latent 原样返回，conditioning 不钉帧原样返回。
            _msg = "[H3 Relay] 复合桥：无 context_latent -> 直通（独立段，不续接）。"
            print(_msg, flush=True)
            return (latent, _msg, 0, conditioning)
        out, covered, report = CORE.build_continue_latent(
            latent, context_latent, int(context_frames),
            mask_mode=mask_mode, taper=int(taper_tokens),
            seam_min=float(seam_min), pin_audio=bool(pin_audio),
            ramp_top=float(ramp_top), ramp_tokens=int(ramp_tokens),
            window_top=float(window_top), window_shape=window_shape,
            blend_top=float(blend_top), blend_tokens=int(blend_tokens),
            blend_shape=str(blend_shape),
            anchor_latent=anchor_latent, anchor_blend=float(anchor_blend),
        )

        # —— 0.6.0 复合桥：接上 conditioning 时，**同时**在 conditioning 上追加钉帧 ——
        # 两条路径改的是不同对象（latent vs conditioning）⇒ 并联不冲突。
        # 不接 conditioning = 行为与旧版完全一致（第 4 路返回 None）。
        cond_out = None
        if conditioning is not None:
            a_idx = int(ref_anchor_stage)
            if ref_anchor_latent is None and a_idx >= 0 and (run_id or "").strip():
                if a_idx == int(stage_index):
                    raise RuntimeError(
                        "ref_anchor_stage=%d 与本段 stage_index 相同——外观锚必须是**更早**的"
                        "已落盘段（通常是 0，即第 1 段）。" % a_idx)
                try:
                    ref_anchor_latent = CORE.load_av_latent(_stage_path(run_id, a_idx))
                    print("[H3 Relay] 复合桥：自动读外观锚（第 %d 段）" % (a_idx + 1),
                          flush=True)
                except FileNotFoundError:
                    raise RuntimeError(
                        "ref_anchor_stage=%d 的落盘文件不存在：%s\n"
                        "先把锚段跑完，或把 ref_anchor_stage 改回 -1。"
                        % (a_idx, _stage_path(run_id, a_idx)))
            plan = CORE.plan_relay(
                latent, context_latent,
                trim_frames=int(context_frames),
                audio_frames=None,
                anchor_latent=ref_anchor_latent,
                anchor_frames=int(ref_anchor_frames),
            )
            cond_out = CORE.apply_relay(conditioning, plan)
            extra = ["[H3 Relay] 复合桥·钉帧路径：" + plan.summary()]
            for n in plan.notes:
                extra.append("    注记：" + n)
            extra.append("    ⚠ 第 4 路 conditioning 必须接到采样器的 positive；"
                         "不接 = 只做拷贝桥。")
            report = report + "\n" + "\n".join(extra)

        print(report, flush=True)
        return (out, report, covered, cond_out)


class H3RelayPost:
    """🔗 H3 续接后处理（Post）—— 画质域修复，**不碰时间轴**。

    为什么单独一个节点（2026-09-17 拆出）：
      原来这 15 个旋钮全塞在 `H3RelayTrimAV` 里，而 ComfyUI 的 UI 工作流把
      ``widgets_values`` **按位置**存 ⇒ 「新 widget 只追加末位」成了硬约束 ⇒
      TrimAV 被顶到 22 个 widget、UI 不可读、且**每加一个后处理件都要往末尾塞**。
      拆出来之后：**TrimAV 冻结不再长**，本节点从零开始、widget 可逻辑分组，
      **以后的画质域功能一律加在这里**。

    分工：
      · ``H3RelayTrimAV`` = 时间轴（裁重叠 / 沉降 / 重影）+ 交接 ``prev_tail``
      · ``H3RelayPost``  = 画质域（色档对齐 / 高频补 / 锐化）——**不动帧数、不动音频**

    ``guide`` 接 ``H3RelayTrimAV`` 的第 4 路输出 ``prev_tail``（**是上段末帧还是它的近似，
    取决于走哪条桥**，见下面 ``guide`` 槽位的说明）：
    只有 ``match_prev`` 与 ``lowfreq_pull`` 需要它；不接时这两项自动失效（其余照常）。

    🔴 2026-09-19 口径补正：``prev_tail = images[pin-1]`` 是「本段自己解码序列的钉住区末帧」。
      · **拷贝桥**：前 pin 帧是**逐位拷贝**的上段尾 ⇒ 它**就是**上段末帧；
      · **Latent 桥（cond）**：前 pin 帧是**本段模型重画**的 ⇒ 只是**近似**（实测代理误差
        0.006，比要修的缝阶跃 0.0007 还大 8 倍）⇒ **对错参照，越对齐越糟**。
      故 cond 路线上 ``match_prev`` 保持 0；本节点的用武之地是拷贝桥。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE", {
                    "tooltip": "【接法】接 `🔗 H3 续接裁重叠` 的 images 输出（**裁后**的画面）。\n"
                               "本节点只改画质、**不改帧数、不动音频**。",
                }),
            },
            "optional": {
                "guide": ("IMAGE", {
                    "tooltip": "【接法】接 `🔗 H3 续接裁重叠` 的第 4 路输出 `prev_tail`\n"
                               "（= 钉住区最后一帧，即缝的另一侧）。\n"
                               "只有「跨段统计匹配」与「低频残差传递」需要它；不接时这两项自动跳过。\n"
                               "🔴 **它究竟是不是「上一段的末帧」，取决于走哪条桥**：\n"
                               "  · **拷贝桥** = 前 pin 帧是逐位拷贝的上段尾 ⇒ `prev_tail` **就是**上段末帧；\n"
                               "  · **Latent 桥（cond）** = 前 pin 帧是**本段重画**的 ⇒ 只是**近似**\n"
                               "    （实测代理误差 0.006 > 要修的缝阶跃 0.0007）⇒ 对错参照，越对齐越糟。",
                }),
                # —— 组 1：跨段色档/曝光对齐（缝的另一侧 = guide）——
                "match_prev": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【组 1 · 跨段】**跨段统计匹配**：把段头的色档/曝光分布\n"
                               "（逐通道均值 + 标准差）对齐到 guide。\n"
                               "Reinhard 式一阶+二阶矩；**只对齐统计量，不复制姿态 ⇒ 无重影**。\n"
                               "目标：压「成片缝上的亮度阶跃」。需要 guide。\n"
                               "🔴 **2026-09-17 真渲染实测：在 Latent 桥（cond）路线上请保持 0**——\n"
                               "该路线缝本身已到 0.0007（可感阈值以下），而 guide（= 裁重叠的\n"
                               "`prev_tail`）在 cond 下只是**近似**参照（本段自己重画的第 pin-1 帧，\n"
                               "实测代理误差 0.006 > 要修的阶跃 0.0007）⇒ 对齐它反而把首帧推离真参照\n"
                               "（阶跃 0.0007 → 0.0039，旧口径更差 → 0.0088）。真正的用武之地是\n"
                               "**拷贝桥**（那里 prev_tail 才是真·上段末帧）。详见 CHANGES 0.5.0。",
                }),
                "match_prev_frames": ("INT", {"advanced": True,
                    "default": 12, "min": 1, "max": 96, "step": 1,
                    "tooltip": "【配合 match_prev】作用帧数：从首帧起算，权重线性衰减到 0。",
                }),
                "match_prev_gain_max": ("FLOAT", {"advanced": True,
                    "default": 1.15, "min": 1.0, "max": 2.0, "step": 0.05,
                    "tooltip": "【护栏】逐通道对比度增益上限（防把已通过的内容改坏）。",
                }),
                "match_prev_offset_max": ("FLOAT", {"advanced": True,
                    "default": 0.06, "min": 0.0, "max": 0.3, "step": 0.01,
                    "tooltip": "【护栏】逐通道亮度/色度偏移上限（防「整段换色」）。",
                }),
                "lowfreq_pull": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【组 1 · 跨段】**低频残差传递**：只把段头**低频**色档对齐 guide，\n"
                               "不动细节与姿态 ⇒ 无重影。与 match_prev 的区别：\n"
                               "本项只做低频**加性**（一阶）；match_prev 补二阶+逐通道色度。\n"
                               "⚠ 两者**作用域重叠**，建议二选一。需要 guide。\n"
                               "⚠ guide 在 Latent 桥（cond）下只是**近似**的缝另一侧（见 guide 槽位说明），\n"
                               "本项只动低频、比 match_prev 温和，但同样别拿它去对错参照。",
                }),
                "lowfreq_frames": ("INT", {"advanced": True, "default": 12, "min": 1, "max": 96, "step": 1,
                                           "tooltip": "【配合 lowfreq_pull】作用帧数（权重线性衰减到 0）。"}),
                "lowfreq_blur": ("INT", {"advanced": True, "default": 64, "min": 4, "max": 256, "step": 4,
                                         "tooltip": "【配合 lowfreq_pull】低频尺度（盒式模糊核，上游用 64）。"}),
                # —— 组 2/3 共用：段头作用帧数 ——（2026-09-19 参数收口）
                #   问题：组 2（色档对齐）与组 3（高频补）的**四个**强度旋钮，
                #   作用区长度一直借的是组 4 的 `settle_sharpen_frames`
                #   —— 名字是「糊区锐化帧数」，用户在 UI 上根本看不出这四个
                #   「作用多少帧」受哪个旋钮管。
                #   本节点 0.5.0 期内**无任何存量图**（已全扫 ComfyUI user 目录为零命中），
                #   故这一处**就地插入**而不是追加末位；默认值与 `settle_sharpen_frames`
                #   相同（24）⇒ 没显式设过值的图行为逐位不变。
                "head_zone_frames": ("INT", {
                    "default": 24, "min": 1, "max": 96, "step": 1,
                    "tooltip": "【组 2+3 共用】段头**作用区长度**（帧）：从裁后首帧起算，\n"
                               "「直方图匹配 / 白平衡 / 反卷积 / 段体高频迁移」四项\n"
                               "都按这个帧数作用（强度各自带线性衰减，尾端归 0）。\n"
                               "⚠ 组 4 的「糊区锐化」**不看这个**，它用下面的 `settle_sharpen_frames`。",
                }),
                # —— 组 2：段内色档对齐（基准 = 本段段体）——
                "hist_match": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【组 2 · 段内】**直方图匹配**：把段头的色阶**分布**对齐到本段段体。\n"
                               "治「段头↔段体」的色阶漂移（段**内**，不需要 guide）。\n"
                               "作用帧数 =「组 2+3 共用」的 `head_zone_frames`（默认 24）。\n"
                               "⚠ 高分辨率下自动**等间隔子采样**求分位（超 2^24 点时）——\n"
                               "   0.3MP 及以下不触发（走精确分位）；0.8MP 起触发，\n"
                               "   耗时约 2 s/段且**与分辨率无关**（2MP/4MP 同级）。",
                }),
                "wb_match": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【组 2 · 段内】**灰世界白平衡**：把段头 R:G:B 比例对齐段体。\n"
                               "与直方图匹配正交 —— 那个管亮度总量，这个管色温。\n"
                               "作用帧数 =「组 2+3 共用」的 `head_zone_frames`（默认 24）。",
                }),
                # —— 组 3：高频补（治糊）——
                "deconv_strength": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【组 3 · 补高频】**反卷积去模糊**（Wiener）：提升段头高频。\n"
                               "太大易出振铃（此时把强度降下来）。\n"
                               "作用帧数 =「组 2+3 共用」的 `head_zone_frames`（默认 24）。",
                }),
                "deconv_radius": ("FLOAT", {"advanced": True, "default": 1.5, "min": 0.5, "max": 4.0, "step": 0.1,
                                            "tooltip": "【配合 deconv_strength】模糊核半径。"}),
                "detail_borrow": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【组 3 · 补高频】**段体高频迁移**：把段头的高频换成段体的结构。\n"
                               "比反卷积更「像真的」，但可能与段头内容不符。\n"
                               "作用帧数 =「组 2+3 共用」的 `head_zone_frames`（默认 24）。",
                }),
                "detail_blur": ("INT", {"advanced": True, "default": 9, "min": 3, "max": 64, "step": 2,
                                        "tooltip": "【配合 detail_borrow】高频分离尺度。"}),
                # —— 组 4：收口 ——
                "settle_sharpen": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 1.5, "step": 0.05,
                    "tooltip": "【组 4 · 收口】**糊区锐化**（unsharp）：对开头 N 帧做**渐变**锐化。\n"
                               "不裁、不动时间轴 ⇒ 从原理上不可能引入跳帧。建议 0.4–1.0。",
                }),
                "settle_auto": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "【推荐替代上面的 settle_sharpen】**自适应糊区补偿**：节点**当场量**每帧\n"
                               "清晰度对段体基线的亏空，按亏空比例锐化——亏空越深补得越多；\n"
                               "已达标的帧（含清晰的帧 0-1）与段体一律不动。\n"
                               "固定 settle_sharpen 的「线性衰减」与真实亏空曲线（帧 0-1 清晰、\n"
                               "第 2 帧最深、~15 帧爬回）不重合；本项跟着量出来的曲线走。\n"
                               "  · 0（默认）= 关。0.5–1.0 = 推荐起点。\n"
                               "  · 与 settle_sharpen 同时开 ⇒ 只作用本项（固定版弃权并点名）。\n"
                               "报告打印量测结果：最低帧/比值、回基线帧数、补偿帧数。\n"
                               "⚠ 同 settle_sharpen：只能恢复对比度，救不回彻底丢失的细节。",
                }),
                "settle_sharpen_frames": ("INT", {"advanced": True, "default": 24, "min": 1, "max": 96, "step": 1,
                                                  "tooltip": "【配合 settle_sharpen】作用帧数（渐变衰减到 0）。\n"
                                                             "⚠ **只管「糊区锐化」这一项**——组 2/组 3 的作用帧数\n"
                                                             "是上面「组 2+3 共用」的 `head_zone_frames`（0.5.0 期内曾共用本项，现已分开）。"}),
                # ⚠ 继续追加在**最后**（同上铁律）。
                "match_prev_stats_frames": ("INT", {"advanced": True,
                    "default": 1, "min": 0, "max": 24, "step": 1,
                    "tooltip": "【配合 match_prev】**统计量取几帧**——本条最容易写错：\n"
                               "  · 1（默认）= 只取**紧贴缝的那一帧** ⇔ 与 guide（单帧）同口径 ⇒\n"
                               "    修正量恰是「缝上的阶跃」，首帧被拉向 guide 而**不会越过它**；\n"
                               "  · 0 = 旧口径（对整个作用区聚合）⇒ 段头**内部有亮度梯度**时，\n"
                               "    聚合均值被后续帧拉低，修正量变成「段头平均 vs guide」的差，\n"
                               "    首帧被**推过 guide**、缝上凭空多出一个阶跃（实测 ×12.6）。仅对照用。\n"
                               "  · >1 = 用前 N 帧聚合（介于两者之间）。",
                }),
                # ⚠ 继续追加在**最后**（同上铁律）。2026-09-19 新增两项：
                "baseline": (["robust", "legacy"], {
                    "advanced": True,
                    "default": "robust",
                    "tooltip": "【组 2+3 的分母】**段体参考怎么取**：\n"
                               "  · robust（默认）= 逐帧亮度取**中央 50%** 的帧再算统计，\n"
                               "    并报出**离散度** `(p75−p25)/中位`；离散度 > 0.08 ⇒ 判「基准不可信」，\n"
                               "    组 2/组 3 **自动弃权**（宁可不动，也不用不可靠的基准改画面）。\n"
                               "  · legacy = 0.5.0 旧口径（**整段均值**，不筛不弃权），**仅作对照复现**。\n"
                               "段只有 90 帧时，`body_start=40` 之后只剩 50 帧——旧口径下基准很容易被\n"
                               "推镜/闪白带偏，这就是本档存在的原因。",
                }),
                "cross_seg_ack": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "【跨段两项的总闸】**确认 guide 是真参照**才打勾。\n"
                               "· 不打勾（默认）⇒ `match_prev` 与 `lowfreq_pull` **自动弃权**（不作用、报告里说明）。\n"
                               "· 为什么默认关：cond 桥（latent 桥）下 `prev_tail` 只是**近似**\n"
                               "  （实测代理误差 0.006 > 要修的缝阶跃 0.0007）⇒ 对齐它反而**把首帧推离真参照**\n"
                               "  （实测 `match_prev=0.7` 把阶跃放大 ×12.6）。**真参照只在拷贝桥下成立。**\n"
                               "· 依据：接缝归因边界规范 §三 R4「代理量打标、近似参照自动弃权」。",
                }),
            },
        }

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("images", "report")
    FUNCTION = "apply"
    CATEGORY = CATEGORY
    DESCRIPTION = (
        "续接段的画质域后处理（不碰时间轴、不动帧数、不动音频）："
        "跨段统计匹配 / 低频残差 / 段内直方图与白平衡 / 反卷积与段体高频迁移 / 糊区锐化。"
        "全部默认关闭；guide 接裁重叠节点的 prev_tail 才有「缝的另一侧」可对齐。"
    )

    @_node_errors("match_prev", "hist_match", "wb_match")
    def apply(self, images, guide=None,
              match_prev=0.0, match_prev_frames=12,
              match_prev_gain_max=1.15, match_prev_offset_max=0.06,
              lowfreq_pull=0.0, lowfreq_frames=12, lowfreq_blur=64,
              head_zone_frames=24,
              hist_match=0.0, wb_match=0.0,
              deconv_strength=0.0, deconv_radius=1.5,
              detail_borrow=0.0, detail_blur=9,
              settle_sharpen=0.0, settle_auto=0.0, settle_sharpen_frames=24,
              match_prev_stats_frames=CORE.MATCH_PREV_STATS_FRAMES,
              baseline="robust", cross_seg_ack=False):
        n0 = int(images.shape[0])
        out = images
        notes = []
        robust = (baseline != "legacy")
        hz = int(head_zone_frames)

        # 段体基准的**离散度**（一次算完，组 2/组 3 共用；只读统计，零帧数代价）
        disp = 0.0
        if n0 > 40:
            _, disp = CORE.robust_body(images[40:].float(), robust)
        abstain = bool(robust and disp > CORE.POST_BODY_DISP_MAX)

        def _snap():
            return CORE.head_metrics(out, hz)

        def _audit(tag, before):
            lum, hf = CORE.head_metrics(out, hz)
            notes.append("　↳ %s 后：段头亮度 %.5f→%.5f（%+.5f）｜高频 %.6f→%.6f（%+.1f%%）"
                         % (tag, before[0], lum, lum - before[0], before[1], hf,
                            (hf / before[1] - 1.0) * 100.0 if before[1] > 0 else 0.0))

        # 执行顺序：先对齐色调（跨段 → 段内），再补高频，最后锐化收口。
        # —— 跨段两项：**默认弃权**（R4 代理量打标，见 cross_seg_ack 的 tooltip）——
        if float(match_prev) > 0.0 or float(lowfreq_pull) > 0.0:
            if not cross_seg_ack:
                notes.append("跨段两项（match_prev=%.2f / lowfreq_pull=%.2f）：**自动弃权** —— "
                             "guide 未确认是真参照（cond 桥下只是近似，实测对齐它把缝阶跃放大 ×12.6）；"
                             "确认真参照（拷贝桥）请把 `cross_seg_ack` 打勾"
                             % (float(match_prev), float(lowfreq_pull)))
            else:
                if float(match_prev) > 0.0:
                    if guide is None:
                        notes.append("跨段统计匹配：**跳过**（未接 guide）")
                    else:
                        out = CORE.match_prev_stats(out, guide, int(match_prev_frames),
                                                    float(match_prev), float(match_prev_gain_max),
                                                    float(match_prev_offset_max),
                                                    int(match_prev_stats_frames))
                        notes.append("跨段统计匹配 %.2f（%d 帧，对齐上段末帧；统计量取 %s）"
                                     % (float(match_prev), int(match_prev_frames),
                                        "紧贴缝那帧" if int(match_prev_stats_frames) == 1
                                        else "整个作用区聚合（旧口径，仅对照）"
                                        if int(match_prev_stats_frames) <= 0
                                        else "前 %d 帧" % int(match_prev_stats_frames)))
                if float(lowfreq_pull) > 0.0:
                    if guide is None:
                        notes.append("低频残差传递：**跳过**（未接 guide）")
                    else:
                        out = CORE.lowfreq_pull(out, guide, int(lowfreq_frames),
                                                float(lowfreq_pull), int(lowfreq_blur))
                        notes.append("低频残差传递 %.2f（%d 帧 / 尺度 %d）"
                                     % (float(lowfreq_pull), int(lowfreq_frames), int(lowfreq_blur)))

        # —— 组 2 / 组 3：段内（基准 = 段体）——
        hm, wb = float(hist_match), float(wb_match)
        dc, db_ = float(deconv_strength), float(detail_borrow)
        if abstain:
            if hm > 0.0 or wb > 0.0 or dc > 0.0 or db_ > 0.0:
                notes.append("组 2/组 3 **全部弃权**：段体基准离散度 %.3f > %.2f"
                             "（基准不可信 ⇒ 宁可不动；要强制旧口径请把 baseline 设 legacy）"
                             % (disp, CORE.POST_BODY_DISP_MAX))
        else:
            if (hm > 0.0 or wb > 0.0 or db_ > 0.0) and n0 <= 40:
                notes.append("组 2/组 3（段内对齐类）**跳过**：段长 %d ≤ body_start=40，段体为空、无可对齐的基准"
                             % n0)
                hm = wb = db_ = 0.0
            if hm > 0.0 and wb > 0.0:
                notes.append("⚠ 组 2 互斥：直方图匹配与白平衡**同时开了** ⇒ 只作用**直方图匹配**"
                             "，白平衡弃权（两者作用域重叠：一个管总量、一个管比例）")
                wb = 0.0
            if dc > 0.0 and db_ > 0.0:
                notes.append("⚠ 组 3 互斥：反卷积与段体高频迁移**同时开了** ⇒ 只作用**反卷积**"
                             "，高频迁移弃权（两者都是「把高频换成段体的」，叠加会让后一层"
                             "的基准失真、不可归因）")
                db_ = 0.0
            if hm > 0.0:
                b = _snap()
                out = CORE.match_hist_head_to_body(out, hz, hm, body_start=40, robust=robust)
                notes.append("直方图匹配 %.2f（段头↔段体，前 %d 帧，baseline=%s）"
                             % (hm, hz, baseline))
                _audit("直方图匹配", b)
            if wb > 0.0:
                b = _snap()
                out = CORE.match_white_balance(out, hz, wb, body_start=40, robust=robust)
                notes.append("白平衡校正 %.2f（段头↔段体，前 %d 帧，baseline=%s）" % (wb, hz, baseline))
                _audit("白平衡校正", b)
            if dc > 0.0:
                b = _snap()
                out = CORE.deconv_head_zone(out, hz, dc, float(deconv_radius))
                notes.append("反卷积去模糊 %.2f / 半径 %.1f（前 %d 帧）"
                             % (dc, float(deconv_radius), hz))
                _audit("反卷积去模糊", b)
            if db_ > 0.0:
                b = _snap()
                out = CORE.borrow_detail_from_body(out, hz, db_, int(detail_blur),
                                                   body_start=40, robust=robust)
                notes.append("段体高频迁移 %.2f / 尺度 %d（前 %d 帧，baseline=%s）"
                             % (db_, int(detail_blur), hz, baseline))
                _audit("段体高频迁移", b)
        if float(settle_auto) > 0.0:
            b = _snap()
            if float(settle_sharpen) > 0.0:
                notes.append("⚠ 互斥：settle_auto 与 settle_sharpen 同时开 ⇒ 只作用自适应版，固定锐化弃权")
            out, _srep = CORE.settle_compensate(out, body_start=40, strength=float(settle_auto))
            notes.append(_srep or "自适应糊区补偿 %.2f（段长不足，未触发）" % float(settle_auto))
            _audit("自适应糊区补偿", b)
        elif float(settle_sharpen) > 0.0:
            b = _snap()
            out = CORE.sharpen_head_zone(out, int(settle_sharpen_frames), float(settle_sharpen))
            notes.append("糊区锐化 %.2f（%d 帧）" % (float(settle_sharpen), int(settle_sharpen_frames)))
            _audit("糊区锐化", b)

        assert int(out.shape[0]) == n0, "后处理必须帧数守恒"
        line = "[H3 Relay] 后处理：%s" % ("；".join(notes) if notes else "全部关闭（直通）")
        print(line)
        return (out, line)


class H3RelayAudioSeam:
    """音频缝：把上一段的环境声补进本段头部（**长度守恒**，零 A/V 位移）。

    治的现象：段首音频带 ~32ms 解码 priming 近静默 + 生成瞬态，裁重叠把这段放到
    裁剪点上 ⇒ 成片缝处先"抽一下"再起乐（实测缝起 20ms 掉 12–13 dB，邻域最静 −72 dB）。

    做法：把本段头部 ``patch_seconds`` 秒整段换成**上一段的最静窗环境声**
    （stationary 噪声，拼接不可闻），到 ``patch_seconds`` 处交叉淡变回本段自身音频，
    之后**逐位不动**。帧数/音频长度都不变 ⇒ 不消耗时间轴。

    ⚠️ 真 crossfade（三角窗叠化）做不到"单段内长度守恒"——它要裁掉**上一段**的尾部，
    而本节点只能改本段。所以 crossfade 仍归组装层；等长路线用本节点。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "audio": ("AUDIO", {
                    "tooltip": "【接法】从「续接裁重叠」的 audio 输出口拉线过来。\n"
                               "（第 1 段没有裁重叠节点时，直接接音频解码 VAEDecodeAudio。）",
                }),
                "run_id": ("STRING", {
                    "default": "relay",
                    "tooltip": "【填什么】这部片子的名字。\n"
                               "⚠ 必须和「续接 Latent 存/桥」上的 run_id 一字不差——\n"
                               "本节点要靠它找到上一段落盘的音频当床源。",
                }),
                "stage_index": ("INT", {
                    "default": 0, "min": 0, "max": 9999, "step": 1,
                    "tooltip": "【填什么】本段是全片的第几段。第 1 段填 0，第 2 段填 1…\n"
                               "第 1 段无缝可补，会直接直通（但仍会把音频落盘，供第 2 段当床源）。",
                }),
            },
            "optional": {
                "patch_seconds": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 4.0, "step": 0.05,
                    "tooltip": "【组 1 · 音频缝】**头部补丁长度（秒）**。0 = 关（默认，逐位直通）。\n"
                               "建议 2.0：段首 2 秒的生成瞬态整段换成上一段的环境声。\n"
                               "⚠ 前提是段首本来就不该有台词（前 1.2s 无词是产线纪律）——\n"
                               "补丁会把这 N 秒的内容换成环境声。",
                }),
                "tile_seconds": ("FLOAT", {"advanced": True,
                    "default": 0.0, "min": 0.0, "max": 4.0, "step": 0.05,
                    "tooltip": "【组 2 · 床环铺】床源改取这么长的瓦片，自叠化环铺满补丁长度。\n"
                               "0 = 整窗直取最静 N 秒（默认）。\n"
                               "上一段最长干净环境窗短于补丁长度时用它（建议 1.2）。",
                }),
                "fade_seconds": ("FLOAT", {
                    "default": 0.25, "min": 0.0, "max": 0.5, "step": 0.01,
                    "tooltip": "【组 3】补丁边界（第 N 秒处）的交叉淡变宽度。\n"
                               "0 = 硬切（会有可闻的接点）；0.25 是产线实测值。",
                }),
                "bed_stage": ("INT", {"advanced": True,
                    "default": 0, "min": 0, "max": 9999, "step": 1,
                    "tooltip": "【填什么】用第几段的音频当床源（默认 0 = 第 1 段）。\n"
                               "同场景环境声是 stationary 的，取第 1 段最稳；\n"
                               "⚠ 必须小于本段段号（床源得是已经渲染完的段）。",
                }),
                "note": ("STRING", {"advanced": True,
                    "default": "",
                    "tooltip": "【可留空】备注，存进落盘文件的元数据里方便事后分辨版本。",
                }),
                # ⚠ 铁律：新 widget 一律**追加在 optional 末位**（旧工作流取值不前移）。
                #   🔴 2026-09-19 真渲染阴性结果驱动（见 CHANGES「音频缝床声选择」）：
                #   旧行为「取床源全局最静窗」会把补丁换成一段**更静**的东西
                #   （实测床声 −42.0 dBFS vs 缝前 −12.8 dBFS）⇒ 缝上从「凹陷」变「静音洞」、
                #   还把本该有的起拍压平。默认档改为**取尾部窗 + 电平对齐**（带峰值护栏）。
                "bed_select": (["tail", "quiet"], {
                    "default": CORE.AUDIO_SEAM_BED_SELECT,
                    "tooltip": "【默认 tail，别改】床声从床源哪里取：\n"
                               "  · tail（默认）= 取**床源尾部**与补丁等长的一段——紧邻缝，\n"
                               "    电平与音色与缝前**天然连续**；再整体对齐到缝前电平（限幅 ±6 dB）。\n"
                               "  · quiet = 0.5.0 旧行为：取全局**最静**窗。\n"
                               "    🔴 实测它会把补丁换成一段更静的内容（−42 vs −12.8 dBFS）⇒\n"
                               "    缝上从「凹陷」变成「静音洞」、起拍被压平。**仅作对照复现用。**",
                }),
                # —— 第 3 路输出 joined（拼接）的三个细分参数：都折叠，别占画布 ——
                "join_curve": (["qsin", "tri"], {
                    "advanced": True,
                    "default": "qsin",
                    "tooltip": "【joined 用】缝处交叉曲线：\n"
                               "  · qsin（默认，**等功率**）——两段内容不相关时不掉电平；\n"
                               "  · tri（线性，ffmpeg acrossfade 的默认）——实测中缝 **−4.7 dB**，\n"
                               "    听感「音量先小再恢复」，**仅作对照复现**。",
                }),
                "join_prime_ms": ("FLOAT", {
                    "advanced": True,
                    "default": CORE.AUDIO_ENCODER_PRIME_MS, "min": 0.0, "max": 200.0, "step": 1.0,
                    "tooltip": "【joined 用】每段头要丢掉的**编码器 priming**（毫秒）。\n"
                               "默认 33 ms（AAC 实测值 @32k = 1056 样本）。**它不是内容**，\n"
                               "丢掉是**对齐修正**；0 = 不丢（对照用）。",
                }),
                "join_cross_ms": ("FLOAT", {
                    "advanced": True,
                    "default": 0.0, "min": 0.0, "max": 2000.0, "step": 5.0,
                    "tooltip": "【joined 用】拼接缝的交叉淡变长度（毫秒）。**默认 0 = 不做交叉**。\n"
                               "🔴 交叉淡变是 overlap-add ⇒ **必然缩短时间轴** ⇒ 每缝后段音频相对\n"
                               "画面**提前** cross 毫秒、且逐段累积（第 3 段起口型明显对不上）。\n"
                               "所以它只在你**同时给了** `join_align_seconds`（画面裁量）时才有意义\n"
                               "（那时走 J-cut 守恒路，输出与裁后视频严格等长）。\n"
                               "只填 cross 不填 align ⇒ 本节点直接报错，不会静默拼出一条错位音轨。",
                }),
                "join_segment_seconds": ("FLOAT", {
                    "advanced": True,
                    "default": 0.0, "min": 0.0, "max": 60.0, "step": 0.001,
                    "tooltip": "【joined 用】每段音频的**有效时长**（秒）= 该段视频帧数/fps。\n"
                               "节点 PCM 通常比 mp4 长（实测 7.5s vs 3.75s），不截断则拼接超长。\n"
                               "0 = 不截（旧行为）。产线 = 90帧/24fps = 3.75。",
                }),
                "join_align_seconds": ("FLOAT", {
                    "advanced": True,
                    "default": 0.0, "min": 0.0, "max": 10.0, "step": 0.001,
                    "tooltip": "【joined 用】每缝的**画面裁量**（秒）= 裁掉的视频帧数/fps。\n"
                               ">0 = **J-cut 时间轴守恒**：后段音频从其裁量处进入（与裁后画面第 0 帧对齐），\n"
                               "其前 cross 秒素材电平对齐后渐入前段尾 —— 输出与裁后视频严格等长、\n"
                               "缝上无电平凹陷（根治 acrossfade「缩短时间轴 ⇒ 音频提前 + 卡顿」）。\n"
                               "0 = 旧缩短语义（仅兼容/对照）。要求 ≥ join_cross_ms。",
                }),
                "patch_guard": ("BOOLEAN", {"advanced": True, "default": True,
                    "label_on": "🛡 台词守卫开", "label_off": "守卫关（旧行为）",
                    "tooltip": "🛡 patch 台词守卫（默认开，0.6.5）：patch_seconds>0 会整段替换**本段**头部——\n"
                               "本段自己的台词落在里面就被吞（2026-09-21 耳检+词级时间戳实锤：\n"
                               "「这家店」0.60–1.70s 被 2.0s patch 吃掉）。\n"
                               "开 ⇒ 自动探测本段头部台词起点（2×全源中位能量判据 + 持续帧），\n"
                               "patch 收缩到台词前 0.40s（margin 盖住探测滞后）；台词太靠前（<0.10s 可用）则 patch 整个关闭。\n"
                               "头部本来就是环境声 ⇒ 行为与关闭时**逐位一致**（零副作用）。\n"
                               "关 ⇒ 旧行为（可能吞字）。",
                }),
            },
        }

    RETURN_TYPES = ("AUDIO", "STRING", "AUDIO")
    RETURN_NAMES = ("audio", "report", "joined")
    FUNCTION = "seam"
    CATEGORY = CATEGORY
    # 落盘本身就是产物：没有下游消费也要执行（否则第 1 段的床源文件永远不生成）
    OUTPUT_NODE = True
    DESCRIPTION = ("把上一段的环境声补进本段头部，去掉裁切点上的解码静默与生成瞬态；"
                   "长度守恒、零 A/V 位移。**并顺手把 0..本段 的音频接成一条连续音轨"
                   "（去编码器 priming + 等功率交叉），从第 3 路 `joined` 输出——"
                   "拼接这件事本身也在节点内完成，用户不需要外部 ffmpeg。**")

    def _joined(self, run_id, idx, curve="qsin",
                prime_ms=CORE.AUDIO_ENCODER_PRIME_MS, cross_ms=50.0,
                segment_seconds=0.0, align_seconds=0.0):
        """把 0..本段 的落盘音频接成**一条**（复合进本节点，不新增轮子）。

        为什么拼接必须由本包做（两个病都不在**单段**可达范围内）：
          · **编码器 priming**：每段 mp4 的 AAC 流头 ~33 ms 近静音（实测 @32k = 1056 样本；
            同位置本节点落盘的音频有内容 −22.9 dBFS、mp4 解码 −66.8 dBFS，差 44 dB）——
            它在**编码之后**才产生，单段节点看不见、删不掉。
          · **交叉曲线**：两段内容不相关时，ffmpeg `acrossfade` 默认 **tri（线性）中缝掉 −4.7 dB**
            —— 就是听感「音量先小再恢复」；等功率 `qsin` 掉 −1.85 dB，配去 priming 只掉 **−0.36 dB**。

        返回 ``(audio|None, note)``；段文件不全或读失败 ⇒ 返回 None 并说明（不抛，避免断链）。
        """
        _raw_dir = os.path.dirname(_audio_stage_path(run_id, 0))
        paths = [os.path.join(_raw_dir, "audio_raw_%05d.safetensors" % i)
                 for i in range(0, int(idx) + 1)]
        _fallback = [_audio_stage_path(run_id, i) for i in range(0, int(idx) + 1)]
        paths = [p if os.path.isfile(p) else f        # raw 缺失回退裁后（前奏不可得，退化为无前奏）
                 for p, f in zip(paths, _fallback)]
        miss = [i for i, p in enumerate(paths) if not os.path.isfile(p)]
        if miss:
            return None, "（joined 跳过：缺第 %s 段落盘音频）" % miss
        try:
            segs = [CORE.load_audio(p) for p in paths]
            sr = int(segs[0]["sample_rate"])
            j, rep = CORE.join_audio_segments(
                segs,
                prime_samples=int(round(float(prime_ms) / 1000.0 * sr)),
                cross_samples=int(round(float(cross_ms) / 1000.0 * sr)),
                curve=str(curve or "qsin"),
                segment_seconds=float(segment_seconds or 0.0),
                align_seconds=float(align_seconds or 0.0))
            # J-cut 守恒结果落盘（组装层 --audio-pcm 直接取用；覆盖写，最后一次执行为准）
            if float(align_seconds or 0.0) > 0:
                try:
                    jp = os.path.join(os.path.dirname(paths[-1]), "audio_joined.safetensors")
                    CORE.save_audio(j, jp, note="joined align=%.3f" % float(align_seconds))
                    rep += "\n           已落盘：%s" % jp
                except Exception as _e:                                   # 落盘失败不断链
                    rep += "\n           ⚠ joined 落盘失败：%r" % (_e,)
        except Exception as e:                                   # noqa: BLE001
            return None, "（joined 失败：%r）" % (e,)
        return j, rep

    @_node_errors("patch_seconds", "tile_seconds", "bed_stage")
    def seam(self, audio, run_id, stage_index, patch_seconds=0.0, tile_seconds=0.0,
             fade_seconds=0.25, bed_stage=0, note="",
             bed_select=CORE.AUDIO_SEAM_BED_SELECT,
             join_curve="qsin", join_prime_ms=CORE.AUDIO_ENCODER_PRIME_MS,
             join_cross_ms=0.0, join_segment_seconds=0.0, join_align_seconds=0.0,
             patch_guard=True):
        me = _audio_stage_path(run_id, int(stage_index))
        idx = int(stage_index)
        patch = float(patch_seconds or 0.0)
        # 🔴 2026-09-19 J-cut：原始（未裁）音频另落盘，供 _joined 取「被裁掉的前奏」；
        #   工作视图切到 [align:]（= 裁后音频）—— patch/落盘 audio_0000i/第 1 路输出
        #   的语义全部保持现状（都基于裁后视图）。
        _al_s = float(join_align_seconds or 0.0)
        # 🔴 2026-09-21 铁律·音画同步：`joined` 只有在**给了画面裁量**时才能做交叉淡变。
        #   交叉淡变是 overlap-add ⇒ **必然缩短时间轴** ⇒ 每缝后段音频相对画面提前 cross 秒
        #   （第 3 段起口型明显对不上，且逐段累积）。这类「静默产出一条错位音轨」按包内纪律
        #   **必须 raise**，不给"忽略参数"的中间态（R5：无断言即未验收；硬错误不静默降级）。
        _cross_ms = float(join_cross_ms or 0.0)
        if _cross_ms > 0.0 and _al_s <= 0.0:
            raise ValueError(
                "音频缝：给了 join_cross_ms=%.0f ms，但 join_align_seconds=0。\n"
                "    交叉淡变是 overlap-add ⇒ **必然缩短时间轴** ⇒ 每缝后段音频相对画面"
                "提前 %.0f ms，逐段累积到口型对不上。\n"
                "    二选一（两条路都不会错位）：\n"
                "      ① J-cut 守恒路（缝上更平滑）：join_align_seconds 填「续接裁重叠」"
                "报告里那行建议值（= 裁首帧数 ÷ fps），且 join_cross_ms ≤ 它；\n"
                "      ② 等长拼接（不做交叉，与段文件首尾相接等价）：join_cross_ms 填 0（默认）。"
                % (_cross_ms, _cross_ms))
        _raw_wf, _raw_sr, _raw_lead, _raw_dt = CORE._audio_parts(audio)
        _raw_path = os.path.join(os.path.dirname(me), "audio_raw_%05d.safetensors" % idx)
        try:
            CORE.save_audio(audio, _raw_path, note=note)
        except Exception:
            pass                                                  # 原始落盘失败不断链
        if _al_s > 0:
            _an = int(round(_al_s * _raw_sr))
            if 0 < _an < int(_raw_wf.shape[-1]):
                _shaped = _raw_wf[..., _an:].reshape(*_raw_lead, _raw_wf.shape[0],
                                                     _raw_wf.shape[-1] - _an) if _raw_lead else \
                    _raw_wf[..., _an:]
                audio = {"waveform": _shaped.to(_raw_dt), "sample_rate": _raw_sr}
        if idx <= 0 or patch <= 0.0:
            CORE.save_audio(audio, me, note=note)
            why = "第 1 段无缝可补" if idx <= 0 else "补丁关（patch_seconds=0）"
            line = ("[H3 Relay] 音频缝：%s → 直通｜本段音频已落盘（供后段当床源）：%s"
                    % (why, me))
            _j, _jn = self._joined(run_id, idx, join_curve, join_prime_ms, join_cross_ms,
                                  join_segment_seconds, join_align_seconds)
            if _jn.startswith("（joined"):
                line += "｜ " + _jn
            else:
                line += "｜ " + _jn.splitlines()[0]
            print(line)
            # ♪ 边车回显：本节点刚落的那份 `me` **就是送进落盘节点的同一份音频**
            #   （直通档也是它）⇒ 拼接直读它，既无损又不会绕过任何音频缝处理。
            _u = _pcm_ui(me)
            _r = (audio, line, _j if _j is not None else audio)
            return {"ui": {CORE.PCM_UI_KEY: [_u]}, "result": _r} if _u else _r

        b_idx = int(bed_stage)
        if b_idx >= idx:
            raise RuntimeError(
                "床源段号（%d）必须小于本段段号（%d）——床源得是**已经渲染完**的那一段。\n"
                "    想用上一段当床源就填 0（或用默认值）。" % (b_idx, idx)
            )
        bed_path = _audio_stage_path(run_id, b_idx)
        if not os.path.isfile(bed_path):
            raise FileNotFoundError(
                "床源音频不存在：%s\n"
                "    第 %d 段还没跑过（本节点会顺手把每段音频落盘）。\n"
                "    先按段号顺序跑一次第 %d 段，再来跑本段。" % (bed_path, b_idx, b_idx)
            )
        # 电平目标 = **缝前**那段音频的尾部（= 上一段，真值）；拿不到才退回床源自身尾部。
        #   为什么单独取上一段：床源默认可选第 1 段（stationary 环境声更稳），
        #   但「缝前电平」只由**上一段**决定——两者不是一回事（0.5.0 把水平目标错当床源了）。
        target = None
        prev_path = _audio_stage_path(run_id, idx - 1)
        if os.path.isfile(prev_path):
            try:
                target = CORE.load_audio(prev_path)
            except Exception as _e:                       # noqa: BLE001
                print("[H3 Relay] 音频缝：读上一段音频失败（%r）→ 电平目标退回床源尾部" % (_e,))
        out, rep = CORE.audio_seam_patch(audio, CORE.load_audio(bed_path),
                                         patch, float(tile_seconds or 0.0),
                                         float(fade_seconds),
                                         select=str(bed_select or CORE.AUDIO_SEAM_BED_SELECT),
                                         target_audio=target,
                                         stage_index=idx, patch_guard=bool(patch_guard))
        CORE.save_audio(out, me, note=note)
        line = rep + ("｜已落盘（供后段当床源）：%s" % me)
        if target is None:
            line += "｜ ⚠ 上一段音频文件缺失 ⇒ 电平目标退回床源尾部（不够准）"
        _j, _jn = self._joined(run_id, idx, join_curve, join_prime_ms, join_cross_ms,
                                  join_segment_seconds, join_align_seconds)
        if _jn.startswith("（joined"):
            line += "｜ " + _jn
        else:
            line += "｜ " + _jn.splitlines()[0]
        print(line)
        # ♪ 边车回显（同上）：`me` = 本节点输出 `out` = 落盘节点拿到的音频。
        _u = _pcm_ui(me)
        _r = (out, line, _j if _j is not None else out)
        return {"ui": {CORE.PCM_UI_KEY: [_u]}, "result": _r} if _u else _r

NODE_CLASS_MAPPINGS = {
    "H3RelayLatentSave": H3RelayLatentSave,
    "H3RelayLatentLoad": H3RelayLatentLoad,
    "H3RelayCopyBridge": H3RelayCopyBridge,
    "H3RelayTrimAV": H3RelayTrimAV,
    "H3RelayPost": H3RelayPost,
    "H3RelayAudioSeam": H3RelayAudioSeam,
    "H3RelayChain": H3RelayChain,
    "H3RelayLatentUpscale": H3RelayLatentUpscale,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "H3RelayLatentSave": "🔗 H3 续接 Latent 存",
    "H3RelayLatentLoad": "🔗 H3 续接 Latent 读",
    "H3RelayCopyBridge": "🔗 H3 续接 拷贝桥",
    "H3RelayTrimAV": "🔗 H3 续接裁重叠",
    "H3RelayPost": "🔗 H3 续接后处理 Post",
    "H3RelayAudioSeam": "🔗 H3 续接音频缝",
    "H3RelayChain": "🔗 H3 续接连跑 Chain",
    "H3RelayLatentUpscale": "🔍 H3 潜空间分块放大",
}
