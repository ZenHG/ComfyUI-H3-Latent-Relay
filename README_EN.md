# ComfyUI-H3-Latent-Relay

🌐 **English (this file)** · [中文（默认 / source of truth）](README.md)

A **latent bridge** for MiniMax-H3 multi-segment continuation — a standalone ComfyUI node pack with
**zero third-party node-pack dependencies**. It needs only ComfyUI's own `torch` and `safetensors`,
and is not coupled to any third-party H3 node pack.

| Item | Value |
|---|---|
| Version | **0.6.15** (8 nodes; the composite bridge `H3RelayCopyBridge` is the **only** bridge) |
| License | **MIT** (third-party attribution in [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)) |
| Host | A ComfyUI build **with MiniMax-H3 support** (that host itself is GPL-3.0 — see §12) |

> **What this file is.** A condensed English guide: install / wiring / nodes / knobs / audio seam /
> troubleshooting / FAQ. The Chinese [`README.md`](README.md) is the **source of truth** and carries the
> full 13-section manual (mechanism, measurements, implementation discipline). If the two ever disagree,
> the Chinese README wins.
>
> **Conventions.** Ports are **0-based**: `[N]` in this document = the *N+1*-th port in the UI.
> Node **class names** are English; **canvas display names are Chinese** (given alongside, in brackets),
> because the pack's UI is Chinese — search the node list for `🔗 H3 续接` (7 nodes) and
> `🔍 H3 潜空间分块放大` (1 node).
>
> **On constants.** Defaults in this file are the **measured, recommended** values, not "a starting point
> for you to tune". Assertion counts / test-group numbers are deliberately **not** duplicated here: they
> live in one place each and are machine-checked against the tests — see README.md §8.

---

## 1. What problem does it solve?

When generating in segments, "continuation" answers one question: **how does the new segment know the
state the previous one ended in?**

The mainstream approach is **pixel continuation**: decode the previous mp4 into frames → VAE re-encode
into a latent → inject that as a condition anchor. Its costs:

- one extra VAE round trip (**quantization loss**);
- the re-encoded latent is **not the same object** as the latent the sampler works on ⇒ the anchor drifts;
- it can only anchor one whole block, always pinned at frame 0.

This pack does a **latent bridge** instead: slice the tail AV latent of the previous segment, chunk it
into per-token blocks, and write them into `minimax_keyframes` **at their true positions**. **No
re-encoding** — anchor and sampled latent come from the same source.

A frequently overlooked benefit: **continuation never decodes frames to disk.** The bridge works purely
in latent space, so a segment only adds a **latent sidecar** under `output/relay_kit/<run_id>/`
(`stage_NNNNN.safetensors`, ~12 MB per segment) plus an optional ~2 MB lossless PCM sidecar (§7).
No "decoded frame cache" layer means no hundreds of MB of raw frames per segment — and an entire class of
"lossy re-encode silently corrupts the seam" problems cannot occur.

---

## 2. Install

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/ZenHG/ComfyUI-H3-Latent-Relay.git
```

Or download the ZIP → unpack → rename the folder to `ComfyUI-H3-Latent-Relay` → put it in `custom_nodes/`.

Then **restart the ComfyUI backend** (ComfyUI-Manager → *Restart*; otherwise restart the Python process).
Refreshing the browser alone does **not** load new nodes. Search the node list for `🔗 H3 续接` (7) and
`🔍 H3 潜空间分块放大` to see all 8 nodes.

Dependencies: `torch` (**top-level import** — without it the whole pack fails to register) and
`safetensors` (lazy import — only the save step fails if missing).

**Host requirement:** **ComfyUI ≥ 0.37.0**, declared in `pyproject.toml` via the official
`[tool.comfy] requires-comfyui` field (the host's `comfy_config/config_parser.py` really reads it at
startup). This pack hard-depends on a ComfyUI with MiniMax-H3 support (`comfy_extras/nodes_minimax_h3.py`,
and a `comfy/model_base.py` that consumes `minimax_keyframes` / `minimax_refs`). On an older ComfyUI
without H3 the nodes still register but **continuation silently does nothing** — check your version first.

`minimax_keyframes` / `minimax_refs` / `resolved_frame_index` are all ComfyUI **native** protocols:
zero monkey patching, and **nothing in another pack is patched**.

### 2.1 Optional: only needed for 🔍 latent tile upscaling

`H3RelayLatentUpscale` is an **AV-packed-latent adapter** over the community pack
**`MinimaxH3LatentUpscaler3D`** (author `LBH-123-AI`, **MIT**). This pack does **not** copy its model
structure or code; at runtime it resolves the upstream class from the ComfyUI registry. So you get both
pieces **from the author**:

```bash
# ① the upstream node pack
cd ComfyUI/custom_nodes
git clone https://github.com/LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler.git
```

② **Upscaler weights**: download from <https://huggingface.co/LBH-123-AI/Minimax_h3_latent_upscaler>
into `ComfyUI/models/latent_upscale_models/` (`.safetensors` or `.pth`), then restart the backend.

- The weight directory and listing reuse the author's own `scan_models()` ⇒ same directory, same files,
  no second copy.
- ⚠️ You need the **H3 latent upscaler** weights (24-channel latent), **not** an ESRGAN-style pixel
  upscaler — the architecture does not match and selecting one raises an error.
- **Not installing it does not affect the other 7 nodes**: the pack loads normally and only raises a
  clear error (naming both repositories) when you actually use this node. No silent degradation.

[`examples/`](examples/README.md) ships two directly openable workflows: a **minimal continuation demo**
(19 nodes, official nodes + this pack) and a **full-flow example** (45 nodes: 0.3 MP first pass → 🔍 latent
upscale → 2-step second pass → copy bridge; **all 8 nodes present**, including the wiring discipline for
"which lines must stay in the native domain after upscaling").

### 2.2 Node API version: V3 (default) / V1

The pack ships two node definitions and **enables exactly one at a time** (env var, **restart required**):

| Exit | How to enable | Notes |
|---|---|---|
| **V3** (**default**, since 2026-09-24) | leave the var unset, or `H3RELAY_NODE_API=v3` | modern `io.ComfyNode` + `comfy_entrypoint()` (official schema) |
| **V1** (fallback) | `H3RELAY_NODE_API=v1` | classic `NODE_CLASS_MAPPINGS`; bit-identical to 0.6.x |

Node names, input/output order and values, defaults, combos and display names are all identical
(8 nodes / 116 inputs, machine-checked in `tests/test_v3_schema.py`; in CI one host-only node
`H3RelayLatentUpscale` cannot be imported ⇒ 8 nodes / 103 inputs there, a **designed downgrade**) ⇒
existing workflows, scripts and API graphs **run on either exit, with no graph edits**.

```bash
H3RELAY_NODE_API=v1 python main.py     # one-line fallback if V3 ever misbehaves (both exits stay in the code)
```

⚠️ **Why it must be either/or:** the host loader is `if module has NODE_CLASS_MAPPINGS … return True` /
`elif module has comfy_entrypoint` — the V1 branch returns first, so exporting both means **V3 never
takes effect**. In V3 mode this pack therefore sets `NODE_CLASS_MAPPINGS = None` explicitly.

---

## 3. Five-minute quickstart

> Fast path: open `examples/minimal_relay_official.json`, point the 4 loader dropdowns at your local
> model files, then follow the three steps below. The canvas notes say the same thing.

**Segment 1**

1. Set `stage_index` to `0`;
2. Type a prompt into the official prompt node (`MiniMaxH3ImageToVideo` / `MiniMaxH3ReferenceToVideo`);
3. Queue.

```
[H3 Relay] 读上段 latent：第 1 段无上一段 → 交空上下文（桥会直通、不裁帧）
[H3 Relay] 复合桥：无 context_latent -> 直通（独立段，不续接）。
[H3 Relay] 裁 0 帧 → 不裁（独立段或纯首段）。
```

> 🔴 **Do nothing special for segment 1.** With `stage_index = 0` the loader hands over an *empty
> context* and the bridge passes through automatically. Do **not** unplug or bypass anything to express
> "there is no previous segment": the bridge's `context_latent` is required, so a missing link makes the
> whole graph fail submission validation (`Required input is missing`) — and bypassing the upstream node
> does not help either, because the host *dissolves* bypassed nodes before submitting and the required
> input disappears with it.

**From segment 2 on**

4. Change `stage_index` to `1` — **this one number only** (it drives both the bridge and the save node);
5. Replace the prompt with segment 2's content (open by picking up the previous segment's motion / camera;
   do not restart the action);
6. Queue again.

| Expected in the log | Meaning |
|---|---|
| `钉住 22 帧` | the previous segment's tail **has been pinned in** |
| `裁首 N 帧 = 钉住 22 + 沉降 0` | seam handling **is active** (`settle_frames=0` means "no settle trimming"; the older "settle 1" wording is pre-0.5.0) |
| `起点干净` | no jump detected, safe to concatenate |

**Key parameters**

| Parameter | Segment 1 | From segment 2 | Set on |
|---|---|---|---|
| `stage_index` | `0` | `1`, `2`, `3`… | **Latent load + bridge + latent save** (all three must match; the Chain node syncs them for you) |
| `run_id` | one film name, e.g. `myfilm` | **character-identical to segment 1** | latent load + bridge + latent save |
| `context_frames` | `22` | `22` (leave alone) | bridge (pin window; only `5+17k` accepted: 5/22/39/56/73/90/107/124) |
| `settle_frames` | — (segment 1 trims nothing) | keep `0` | `H3RelayTrimAV` |
| `seam_ghost` | — | keep `0` (off by default) | `H3RelayTrimAV` |
| `settle_sharpen` | — | keep `0` (enable only if needed) | `H3RelayPost` |

**The three most common ways to break it**

| Symptom | Cause | Fix |
|---|---|---|
| Error "segment N cannot find the previous segment" | `stage_index ≥ 1` but `run_id` differs from the previous segment / that segment was never written | make both `run_id` values identical |
| The output replays the previous segment from frame 1 | `trim_frames` on TrimAV is not connected, or not wired to bridge output `[2]` | recheck §4 |
| A new film picks up an old film's tail | `run_id` was not changed (same name ⇒ same file names) | use a new name |

> **After N segments:** you get N mp4 files. Since 0.6.7 the Chain node can also concatenate them
> (fill `prompts` to vary the prompt per segment; enable `auto_concat` or click **🧩 拼成一条**) —
> see [§7 Multi-segment concatenation](#71-multi-segment-concatenation-n-mp4-files-into-one).

---

## 4. Wiring (the one bridge)

Since 0.6.0 there is **one bridge**: `H3RelayCopyBridge` [`🔗 H3 续接 拷贝桥（复合桥）`]. When
`conditioning` is connected it does two things at once — `[0] latent` pins a window that governs
**motion**, while output `[3] conditioning` pins keyframes that govern **framing**. They act on different
objects, so both apply in parallel. Leave `conditioning` unconnected and it is a pure copy bridge.

```
prompt node (official / third-party)
  ├─ positive ──────────────────→ bridge [16] conditioning
  └─ LATENT ───────────────────→ bridge [0] latent
                                 bridge [1] context_latent  ← leave empty (auto-loads previous segment)
                                 bridge [2] context_frames  = 22
                                 bridge [17] run_id / [18] stage_index
                                        │
           bridge [0] latent ───────────┼──→ sampler latent_image       ★ MUST pass through the bridge
           bridge [3] conditioning ─────┼──→ sampler positive
           bridge [2] trim_frames ──────┼──→ TrimAV [1] trim_frames     ★ MUST be connected
                                        │
                                  sampler → LATENT ──→ H3RelayLatentSave [0]
                                                 └──→ VAEDecode / VAEDecodeAudio
                                                           ↓
                               H3RelayTrimAV [0] images ← IMAGE
                                             [3] audio  ← VAEDecodeAudio   ★ input
                                             [1] trim_frames ← bridge [2]
                                       │
         ┌─────────────────────────────┤
         │            [1] audio ───────┴──→ H3RelayAudioSeam [0] audio
         │                                  (optional; connect it for segment 1 too — see §7)
         │                                        │
         │                                    [0] audio
         │                                        │
         └→ [0] images ─────────────┬────────────┴──→ CreateVideo → SaveVideo
                                   │                    ↑
                                   │              ★ audio must come from this route
                                   │                (NOT the raw VAEDecodeAudio one)
                    [3] prev_tail ──→ (optional) H3RelayPost [1] guide
```

> 🔴 **Only one audio route is correct:** `TrimAV [1] audio` (optionally through `AudioSeam [0] audio`)
> → `CreateVideo.audio`. **Wiring `VAEDecodeAudio` straight into `CreateVideo` desynchronizes A/V** —
> the video drops the overlapping head frames while the audio keeps them, off by ~0.9 s per seam, and it
> **accumulates segment by segment** (from segment 3 on, lip-sync is visibly off). Nodes can detect
> "an input is unconnected"; they **cannot** detect "an output is left dangling". This one is on you.

**Port reference (0-based)**

| Node | Inputs | Outputs |
|---|---|---|
| `H3RelayCopyBridge` (composite bridge) | `[0] latent` `[1] context_latent` `[2] context_frames` `[16] conditioning` `[17] run_id` `[18] stage_index` | `[0] latent` `[1] report` `[2] trim_frames` `[3] conditioning` |
| `H3RelayLatentSave` | `[0] latent` `[1] run_id` `[2] stage_index` `[3] note` | `[0] latent` `[1] path` |
| `H3RelayTrimAV` | `[0] images` `[1] trim_frames` `[2] fps` `[3] audio` `[4] settle_frames` | `[0] images` `[1] audio` `[2] report` `[3] prev_tail` |
| `H3RelayPost` | `[0] images` `[1] guide` + 20 knobs | `[0] images` `[1] report` |
| `H3RelayAudioSeam` | `[0] audio` `[1] run_id` `[2] stage_index` `[3] patch_seconds` `[5] fade_seconds` | `[0] audio` `[1] report` `[2] joined` |
| `H3RelayLatentLoad` (optional) | `[0] run_id` `[1] stage_index` `[2] explicit_path` | `[0] context_latent` `[1] info` |

**Segment 1:** the bridge outputs `0` on `[2]` ⇒ TrimAV passes frames through untouched; the bridge does
not read any context (pass-through).
**From segment 2 on:** fill in `run_id` + `stage_index` and the bridge loads the previous segment itself
from `output/relay_kit/<run_id>/stage_NNNNN.safetensors`. If you want the source drawn on the graph (or
want to resume from a different source), connect `H3RelayLatentLoad` (`[0] context_latent` → bridge `[1]`).

Any node that **outputs `CONDITIONING` + `LATENT`** wires up the same way — just swap it in for the prompt
node.

| Nodes used in the examples | From |
|---|---|
| `MiniMaxH3ReferenceToVideo` / `MiniMaxH3ImageToVideo` / `MiniMaxH3AddGuide` | ComfyUI **built-in** |
| `CSGlideCastCS` (prompt + specs) | `ComfyUI-Banzhang-All` (third party, **not a dependency of this pack**) |
| `SelfLiftH3Sampler` (AV sampler) | `comfyui-SelfLift` (third party, **not a dependency of this pack**) |

> 📂 The two demo workflows under `examples/` additionally need **KJNodes**
> (`CreateFadeMaskAdvanced` / `MiniMaxChunkFeedForward`) — those are dependencies of the **example
> graphs**, not of this pack (the pack itself depends on zero third-party node packs). If you only want
> the minimal wiring, use `minimal_relay_official.json`, which is **official nodes + this pack only**.

Optional nodes (all off by default = bit-exact pass-through; delete them and everything still runs):
**H3RelayPost** (picture domain), **H3RelayAudioSeam** (audio domain), **H3RelayChain** (auto-run, see
[`docs/07-chain.md`](docs/07-chain.md)).

> On the canvas only the main knobs are shown; the rest fold into the `advanced` section. For older graphs
> where the `prev_tail` output is invisible, see
> [`docs/04-canvas-and-widgets.md`](docs/04-canvas-and-widgets.md).

### 4.1 Full-flow layout: **all 8 nodes present** (`examples/fullflow_second_pass_latent_upscale_ui.json`)

The layout above is the **minimum**. The production layout ("first pass → 🔍 upscale → second pass →
continuation") uses all 8 nodes. ★ = delivery branch, ☆ = continuation contract — they are **not the
same line**:

```
① prompt (official MiniMaxH3ReferenceToVideo)      outputs positive + LATENT
② H3RelayLatentLoad   ☆ previous segment on disk (stage_index − 1, automatic)
③ H3RelayCopyBridge   ← ①LATENT + ②context_latent; [3]conditioning → **first-pass guider only**
④ first pass SamplerCustomAdvanced (native resolution, guider ← ③[3])
⑤ H3RelayLatentSave   ☆ ← ④ output             ★ MUST sit BEFORE the upscale
⑥ 🔍 latent tile upscale ← ④ → + SetLatentNoiseMask (pins the first 7 latent frames)
⑦ second pass SamplerCustomAdvanced (at the upscaled resolution)  guider ← **① positive** (🔴 not ③[3], see §10.9)
⑧ VAEDecode ← ⑦ images ／ VAEDecodeAudio ← ④ audio   ★ audio never goes through SR or the second pass
⑨ AudioSeam ← ⑧audio → TrimAV (trims A+V together, writes the PCM sidecar) ← ⑧images
⑩ H3RelayPost ← ⑨ (guide ← TrimAV[3] prev_tail; all 20 knobs default 0 = bit-exact pass-through)
⑪ CreateVideo → SaveVideo (single file with audio track)
⑫ H3RelayChain: `prompts` split on `---` ⇒ per-segment prompts while auto-running
   (since 0.6.15 `Chain.prompt` can be wired to the prompt node's `prompt`, so scripted JSON submission
   gets the same behaviour); click 🧩 when done to get the finished film
```

- 🔴 **Do not feed the bridge's `conditioning` to the second pass:** the keyframe anchor
  (`minimax_keyframes`) and this segment's target **must share the same grid**; after the upscale the
  target grid has changed ⇒ connecting it explodes on the spot (mechanism and measurements in §10.9).
  The latent-side pinning still applies as usual.
- 🔴 **The contract must be taken in the native domain:** `LatentSave` goes *before* the upscale. Saving
  the upscaled latent means the next segment runs SR again = double drift.
- Output resolution = native × scale factor (416×736 → 480×864 in this example). **The upscale belongs to
  the delivery branch and is not part of the continuation contract.**

---

## 5. Nodes at a glance

**8 nodes in three layers — a minimal workflow only needs the first 3** (the other 5 are
"delete-and-it-still-runs" optionals; the production full-flow example connects **all 8**):

| Layer | Nodes | Notes |
|---|---|---|
| **Required 3** | LatentSave · CopyBridge (composite) · TrimAV | without them it is not continuation |
| **Optional 4** | H3RelayPost · H3RelayAudioSeam · H3RelayChain · H3RelayLatentUpscale | independent; off by default = bit-exact pass-through |
| **Manual routing only 1** | H3RelayLatentLoad | the bridge fetches the source from disk itself; connect this only to **override the source explicitly** |

| Node (class) | Canvas name | What it does |
|---|---|---|
| `H3RelayLatentSave` | 🔗 H3 续接 Latent 存 | after sampling, writes this segment's AV latent to `output/relay_kit/<run_id>/stage_NNNNN.safetensors` |
| `H3RelayLatentLoad` | 🔗 H3 续接 Latent 读 | reads the previous segment (stage_index − 1); `explicit_path` overrides the source for resuming |
| `H3RelayCopyBridge` | 🔗 H3 续接 拷贝桥（复合桥） | **bit-copies** the previous segment's tail AV latent into this segment's initial latent + a noise mask (pinned region is not re-drawn). With `[16] conditioning` connected it pins keyframes in parallel (framing); without it, copy only. (`mask_mode` levels: [`docs/02`](docs/02-parameters.md)) |
| `H3RelayTrimAV` | 🔗 H3 续接裁重叠 | trims the regenerated head frames (video **and** audio together) — without it, the concatenation point replays or jumps; output `[3]` = `prev_tail`, the previous segment's last frame |
| `H3RelayPost` | 🔗 H3 续接后处理 Post | **picture domain**: cross-segment statistics matching / low-frequency pull / tone / deconvolution / high-frequency transfer / blur-region sharpening; all off by default |
| `H3RelayAudioSeam` | 🔗 H3 续接音频缝 | **audio domain**: extends the previous segment's ambience into this segment's head, **length-preserving** (zero A/V shift); off by default |
| `H3RelayLatentUpscale` | 🔍 H3 潜空间分块放大 | **picture domain · latent level**: unpacks H3's AV-packed latent → tiles through a learned 3D upscaler → repacks. **Zero denoising** (no resampling, no change to performance or lip-sync), **not a single frame moved in time** (frame grid and TrimAV's trim amount are unaffected), **audio stream carried back untouched**. `chunks` is the VRAM knob (1 = whole segment at once), `mode` supports ×factor / target size / megapixels. ⚠️ Optional dependency, see §2.1; ⚠️ `chunks>1` changes the picture, see §9 |
| `H3RelayChain` | 🔗 H3 续接连跑 Chain | auto-run controller: advances "bridge + save" stage numbers inside the same group and queues. **Since 0.6.7 it also rotates prompts and concatenates**: fill `prompts` (`---`-separated; block *k* feeds segment *k*) for per-segment prompts; enable `auto_concat` or click **🧩 拼成一条** to get the finished film right after the run (in-pack implementation, no external ffmpeg). Empty/off = previous behaviour, bit for bit. **Since 0.6.15 it also has a `prompt` output**: wire `Chain.prompt` to the prompt node's `prompt` and "segment *k* gets block *k*" becomes a **node capability** ⇒ **canvas-clicking and scripted JSON submission behave identically** (script in §7.4). Same version added **`run_id`** (resume) and the **⏭ resume** button |

> **Three domains, do not mix them up:** timeline = `H3RelayTrimAV` (frozen) / picture = `H3RelayPost` /
> audio = `H3RelayAudioSeam`.
>
> **🔍 Upscaling should happen exactly once per chain:** this pack's upscale node and the upscale
> **built into** third-party progressive samplers (SelfLift etc.) call the same third-party model, the
> same weights, and do the same thing. Enabling both upscales the same latent twice ⇒ drift accumulates.
> Either inside the sampler, or as a standalone node between the two passes — **pick one** (trade-offs in
> [`docs/03`](docs/03-sampling-and-design.md)).
>
> **♪ PCM audio sidecars (0.6.7):** `H3RelayTrimAV` and `H3RelayAudioSeam` each also write **their own
> audio output** as a lossless PCM sidecar (~2 MB/segment), so "concatenate" can read **lossless audio**
> directly (no second encoding). When both exist on the audio chain, concatenation picks the one whose
> **length is closest to the mp4's audio** ⇒ it takes the copy that actually went into the mp4. To save
> disk, turn off `H3RelayTrimAV.save_pcm` (concatenation then falls back to decoding and says so in the
> report). Node count is not the complexity — **which lines must be wired correctly** is.

---

## 6. Knobs (the main ones)

Defaults are the **measured, recommended** values. The full manual (every `advanced` item, Post's 20 and
AudioSeam's 13) is in [`docs/02-parameters.md`](docs/02-parameters.md).

| Node | Parameter | Default | Notes |
|---|---|---|---|
| Bridge | `context_frames` | `22` | pin-window length; only `5+17k` (5/22/39/…/124), must be less than this segment's frame count |
| Bridge | `mask_mode` | `hard` | 🟢 production `hard` (zero re-draw in the pinned region)｜🟡 controls `ramp`/`window`｜🔴 experiments `taper`/`blend` (`taper` does **not** pin) |
| Bridge | `ref_anchor_stage` | `-1` | when ≥ 0, auto-load that segment as a **global appearance anchor** against long-range drift |
| TrimAV | `settle_frames` / `diagnostics` | `0` / `False` | no settle trimming; `seam_ghost` is `0` too. `diagnostics` is the master switch for three **read-only** observability passes, **off by default** (it changes no frame / latent / audio, it only saves CPU) |
| Post | 19 knobs | `0` | all off = bit-exact pass-through; the two cross-segment items additionally need `cross_seg_ack` |
| AudioSeam | `patch_seconds` | `0` | bit-exact pass-through; `fade_seconds` defaults to `0.25` |

---

## 7. Audio seam

The audio seam **must happen inside the node** (an assembly-layer patch cannot do it): patching at the
assembly layer introduces an A/V shift caused by AAC priming, whereas this node is
**length-preserving** — not one sample added or removed ⇒ zero shift.

- Connect it for segment 1 too (it writes segment 1's audio to disk, and that becomes segment 2's bed
  source); run segments in order, do not skip.
- Leave it out, or `patch_seconds=0` ⇒ audio passes through bit for bit.
- **Output wiring:** `AudioSeam [0] audio` → the save node's `audio` (`CreateVideo.audio`). **Do not wire
  `VAEDecodeAudio` directly into the save node** — that is untrimmed raw audio and will desynchronize A/V
  (see the red warning in §4).
- ⚠️ `patch_seconds > 0` replaces the **first `patch_seconds` seconds of this segment** with the previous
  segment's ambience. If this segment's head contains dialogue, those seconds of dialogue get replaced ⇒
  leaving the head clear is a hard discipline (see [`docs/06`](docs/06-continuity-scripting.md)). The node
  automatically **avoids the voiced regions of the bed source** and logs
  `🎙 窗内有声帧占比` (voiced-frame ratio inside the sampling window).
- 🛡 **Dialogue guard (on by default):** a patch would eat dialogue that this segment itself places in its
  head ⇒ the node detects the dialogue onset in this segment's head and **shrinks the patch to 0.40 s
  before it**; if the dialogue starts too early (usable window < 0.10 s) the patch is disabled entirely
  and the head is kept as-is. The report prints the decision and the before/after values.
  `patch_guard=0` restores the old behaviour.
  ⚠️ **The guard only works on material with a silent pad at the segment head** — it is **structurally
  useless** when the whole segment is wall-to-wall voice or music.
  **Do not rely on it for dialogue safety: the primary defence is `patch_seconds ≤ 1.2`** (see §7.3.1).

### 7.1 Multi-segment concatenation: N mp4 files into one

**One contract first:** `TrimAV` trims **video and audio together**, and `AudioSeam` is
**length-preserving** ⇒ **every segment file's audio length equals its video length**, with **no overlap**
between segments. So the correct concatenation is a **plain end-to-end join**; no "clever muxer" needed.

> ⚠️ **Auto-run ≠ finished film.** You get N mp4 files. Since 0.6.7 `H3RelayChain` can join them (below),
> but **you must trigger it** (`auto_concat` or the 🧩 button) — it will not do it behind your back.

**① Preferred: in-pack one-click (0.6.7)**

On the Chain node: enable `auto_concat` ⇒ concatenate automatically when the run finishes; or click
**🧩 拼成一条** at any time to concatenate the segments produced so far. Video is **stream-copied
(lossless)**, audio is de-primed and aligned per segment, and four assertions run immediately
(frame count conserved / no PTS holes / DTS monotonic / |A−V| ≤ 1 frame). Measured: a 4-segment 29 s film
in **1.3 s**. If an assertion fails the file is kept for forensics and the status explicitly says
**do not use it as a deliverable**.

**①.5 Audio track options: AAC 256k by default, plus a lossless master (0.6.7)**

`audio_out` on Chain has three levels (affects the **finished film's audio track only**; video is always
stream-copied):

| Level | Container / codec | Measured SNR (vs lossless source, real H3 segments) | Notes |
|---|---|---|---|
| `aac_256k` (default) | mp4 + AAC 256k | **40.5 – 50.0 dB** | best compatibility, browsers can play it |
| `aac_192k` | mp4 + AAC 192k | 38.4 – 44.6 dB | ~25 % smaller |
| `pcm_lossless` | mp4 + PCM f32 | **bit-exact (∞)** | master level; **no sound in browser preview**, ~4.4 MB/s |

**Why the default already drops audio from "two generations" to one:** a segment file's audio track is
AAC encoded when you save it (generation 1). Concatenating by decoding the mp4 and re-encoding = generation
2. Since 0.6.7, TrimAV also writes a **lossless PCM sidecar** (`save_pcm`, on by default); concatenation
reads the sidecar directly ⇒ **no AAC decode, and no priming involvement** ⇒ audio is encoded once.
Choosing `pcm_lossless` adds **no generation at all** (finished track is bit-identical to the sidecar,
measured max|Δ| = 0).

> If a sidecar is missing (old graph, `save_pcm` off, or that submission never ran TrimAV), that segment
> silently falls back to mp4 decoding and the concatenation report **names the audio source per segment**
> (`PCM` / `AAC`). It just costs that segment its "generation 2" status.
>
> **⚠ What if other audio nodes sit downstream of TrimAV?** (Typically this pack's `AudioSeam`; the
> production wiring is `TrimAV → AudioSeam → save`.) What really lands in the mp4 is the **last** node's
> output, so: ① `AudioSeam` writes its own sidecar too; ② concatenation picks among candidates by
> **"closest to the mp4's audio length"** (data decides, not wiring assumptions); ③ if no same-source
> candidate exists (e.g. a J-cut makes AudioSeam's output 0.9 s shorter) it **rejects the sidecar,
> falls back to decoding and says so in the report**.
>
> A sidecar write failure (~2 MB/segment) **does not affect this segment's render**; it is only logged.

**Parameter: `video_crf` (default 16)** applies only when the video **must** be re-encoded (segments with
mismatched specs, or a failed stream-copy assertion); the default stream-copy path is lossless, so this
field is unused. ⚠ `crf 0` is **not** lossless (RGB→YUV 4:2:0 loses first; the ceiling is 46.5 dB).

**② If you do not want this to happen inside ComfyUI** (external tools): use a concat filter with a single
re-encode pass — list the N inputs in order (`list.txt` with absolute paths in segment order, one
`file '/abs/path/s0.mp4'` per line):

```bash
# self-check: frame count should equal the sum of the segments, A/V duration delta ≤ 1 frame
ffprobe -v error -select_streams v:0 -count_frames -show_entries stream=nb_read_frames -of csv=p=0 out.mp4
ffprobe -v error -select_streams a:0 -show_entries stream=duration -of csv=p=0 out.mp4

# concatenate (cleanest timeline; list the N inputs in order)
ffmpeg -y -i s0.mp4 -i s1.mp4 -i s2.mp4 -i s3.mp4 \
  -filter_complex "[0:v][0:a][1:v][1:a][2:v][2:a][3:v][3:a]concat=n=4:v=1:a=1[v][a]" \
  -map "[v]" -map "[a]" -c:v libx264 -crf 16 -pix_fmt yuv420p -c:a aac -b:a 192k out.mp4
```

| Do NOT do this | Symptom (measured 2026-09-22) | Why |
|---|---|---|
| `ffmpeg -f concat -c copy` (the lazy option) | 4 segments: video duration **29.346 s** (should be 29.250, +96 ms) and audio landing points +64 / −25 / −33 ms (NCC falls to 0.2) | each segment's audio stream is longer than its video (AAC encoder priming, +0.032 s per segment); stream copy cannot calibrate at sample level |
| `acrossfade` (looks professional) | **steals 0.25 s per seam** ⇒ A/V misalignment from segment 3 on, **accumulating per segment** | crossfade is overlap-add, and the video has **no** corresponding overlap to trim |

> Always run the self-check above: frame count conserved + A/V duration delta ≤ 1 frame. If it fails, use
> route ① or change your material.

### 7.2 Bed-window voice avoidance: what the criterion catches and what it misses (read first)

With `patch_seconds > 0` the node **automatically avoids voiced regions in the bed source**. The criterion
is **energy-based** (a frame whose RMS exceeds **3× the whole-source median** counts as "voiced";
a window above a ratio threshold is a collision; tile mode threshold is 0) — **it is not speech
recognition**. Measured behaviour:

| Material | Criterion (measured) |
|---|---|
| Steady noise floor + dialogue (rain / room tone + lines — the common case) | ✅ reliably caught (58 % voiced ratio ⇒ switched to a 0 % window) |
| Very short vocalisations (~0.1 s sighs / breaths) | ✅ caught (12.5 %, borderline) |
| Single-frame transients (door slam / bang) | ✅ no false positive (4 % < 10 % threshold, no pointless window switch) |
| **Low-level voice** (only ~6 dB above ambience: distant dialogue, breathy voice) | ❌ **missed** (does not reach the 3× threshold) |
| **Voice as the bed / continuous BGM-plus-voice** (podcast, continuous narration, noisy bar) | ❌ **missed** (voice raises the whole-source median ⇒ the threshold neutralises itself) |

**Fallbacks, cheapest first:**

1. **Read the log**: every run with `patch_seconds>0` prints
   `🎙 取样窗内有声帧占比 X%（判据阈值 Y%）`; collisions are flagged with `⚠` plus before/after window
   positions — **every decision is auditable, nothing is silent**.
2. **When in doubt, turn it off**: `patch_seconds=0` ⇒ the patch branch never runs, audio passes through
   bit for bit (a zero-risk switch).
3. **Drop one complexity level**: `tile_seconds=0` ⇒ single-window mode (10 % threshold + voice
   avoidance), more conservative than tile mode.
4. **Fix it at the source**: keep dialogue away from the bed source's tail (`bed_select=tail` takes the
   previous segment's last window) — head/tail padding discipline in
   [`docs/06`](docs/06-continuity-scripting.md).
5. **When everything is voiced**: the node does **not** error; it falls back to the **quietest window**
   (least voice residue) and logs `⚠` — legitimate "voice-as-bed" material is not rejected.
6. **The final judge is your ears**: the machine only points at a location (the `patch_seconds` after the
   seam). Listen before deciding.

> The criterion includes **persistence filtering** (≥ 2 consecutive frames = 100 ms before a vocal onset
> counts ⇒ single-frame transients do not switch windows; continuous dialogue ratios are measured as
> lossless). Still **not implemented**: ASR semantic re-checking (it would rescue the BGM-type misses in
> the table above; registered in `CONTRIBUTING`).

### 7.3.1 🔴 Dialogue protection: **shortening the patch is the primary defence — the guard is only a backstop**

> This section is the conclusion of a full zero-GPU measurement round (2026-09-26). **Read it before
> choosing `patch_seconds`.**

**What the patch actually covers.** The patch targets the AAC decoder priming silence and the generation
transient at the segment head. Across 11 real finished films, **the first 10 ms of every head is genuine
silence** (−50 to −122 dBFS), while the mean level over the head's 0–1.0 s is **39–80 dB higher**.

⇒ **The silence lives only in the first 10 ms.** Any `patch ≥ 0.1 s` covers it; shrinking from 1.0 s to
0.5 s does **not** re-expose the decoder silence — it only costs you "the head's 0.5–1.0 s keeps this
segment's own audio".

**Where dialogue starts (this decides how short is safe):** measured ground-truth material with dialogue
in the head splits into two classes:

| Class | Dialogue onset | Head level over 1.2 s | Does shortening help? |
|---|---|---|---|
| **A — quiet head** | **1.4 – 1.6 s** | −44 to −55 dB | ✅ **fully effective** |
| **B — dialogue glued to the head** | **0.10 s** | **−3 to −4 dB** (near full scale) | ❌ **no solution** |

**Class A:** dialogue starts after 1.4 s ⇒ **`patch ≤ 1.2 s` never touches it**.
**Class B:** dialogue starts at 0.1 s and the **whole segment is wall-to-wall voice/music** (head level
−3 to −4 dBFS) ⇒ any `patch > 0.1 s` covers dialogue, and **no audio criterion can save it**.

**Why the guard cannot rescue class B — it is structural.** Class B material has **voice throughout**
(voice *is* the noise floor). The guard's threshold is `2 × whole-source median`, but that median **is**
the voice ⇒ the threshold sits above the voice peak ⇒ **zero frames exceed it ⇒ it never triggers**
(measured and confirmed). ⚠️ Versions 0.6.9 and earlier documented the opposite ("`patch=2.0` stays safe
with dialogue up front") — **that claim is retracted; do not configure from it.**
It holds for class A; for class B it **swallows words**.

**Criteria exhaustively falsified (15 families — do not re-walk this path):** energy (RMS mean /
percentiles / peak, run-length, step), lowered baselines (P05/P10 instead of median, lowered *k*),
relative measures (window median − whole-segment median), spectral shape (`spec_centroid` / `cent_act`),
harmonic-to-noise measures (`hnr` / autocorrelation / spectral flatness), temporal structure
(`zcr` / `gap_ratio` / `n_seg`), modulation (2–8 Hz syllable rate, spectral flux), external
(`webrtcvad`: 0.375 accuracy — fundamentally a GMM-energy VAD, same family), and cross-domain
combinations (`step` + `cent_act` + `flat_act`: only looks good on long windows; at production window
lengths TP collapses to 0–3 / 5). ⚠️ This is **not** "the model has never seen this before": steady voice
and steady music are **inseparable on any statistic of a 1-second window**. Separating them requires
time-frequency structure (formant transitions, voiced/unvoiced alternation) — i.e. rebuilding a VAD, and
VADs measure **worse**.

**✅ Recommendation, ordered by reliability:**

| # | Practice | Reliability | Cost |
|---|---|---|---|
| 1 | **`patch_seconds ≤ 1.2`** (aligned with the head-padding discipline on the prompt side) | ✅ **most reliable** — class A is immune, class B should not occur | the head's 0.1–1.2 s keeps its own audio (the decoder silence is < 10 ms, so it is still covered) |
| 2 | **Dialogue up front (speaks within 1.5 s of the head) ⇒ `patch_seconds=0`** | ✅ zero-risk switch; the patch branch never runs, audio is bit-exact | the seam transient is not covered |
| 3 | `patch_guard=on` (default) + `patch_guard_layers=2` | ⚠️ **class A only**; structurally useless for class B | none |
| 4 | **Head-padding discipline on the prompt side** (`docs/06`): never write dialogue into the head's first 1.2 s | ✅ **fixes the root cause** — class B material is the product of this discipline failing | one extra line in your prompt |

> **In one sentence:** the patch's value (covering 10 ms of silence) does **not** conflict with dialogue
> protection — `patch=1.2` both covers the silence and leaves dialogue after 1.4 s alone. The only
> combination that really swallows words is `patch > 1.5` **with** dialogue inside the first 1.5 s, and
> that is either solved by shortening the patch (class A) or **must not be produced in the first place**
> (class B — prompt discipline).

### 7.4 API / script users: using it without the canvas (⚠ read "what is UI-only")

Half of this pack's newer capabilities are **implemented in front-end JS** (canvas buttons). If you submit
graphs as JSON to `/prompt`, read this table first:

| Capability | Canvas users | API / script users |
|---|---|---|
| The continuation core (bridge / TrimAV / Post / AudioSeam) | ✅ wiring | ✅ **same** (pure nodes) |
| PCM audio sidecar (`save_pcm`, audio generations 2→1) | ✅ on by default | ✅ **same** (node writes the file and echoes the path into history) |
| **Prompt distribution (block k to segment k)** | ✅ fill `prompts` + wire `Chain.prompt` to the prompt node | ✅ **same** (a **node capability** since 0.6.15: Chain emits block *k* based on `stage_index`) — just change `stage_index` in your loop |
| **The auto-run loop itself** | ✅ button | ⚠ **your script must loop** (within a single execution the node only knows "which segment am I"; it cannot submit the next prompt itself) — template below |
| **Concatenating N segments** | ✅ 🧩 button / `auto_concat` | ✅ **three non-UI paths** (below), sharing the same core code as the button |

> ⚠️ `status` / `prompt_target` / `auto_concat` / `concat_name` / `audio_out` / `video_crf` are
> **canvas-only** (`status` is display; the rest are read by the front-end and sent to the backend).
> A script submitting JSON has them ignored (no error) — **do not rely on them**.
>
> ✅ **`prompts` and `stage_index` are the exceptions** (since 0.6.15): they are **real inputs** of
> `H3RelayChain`, so scripted JSON submission honours them — the node takes block *k* out of `prompts` by
> `stage_index` and hands it out on the `prompt` output. **How prompts are split and what happens out of
> range is the same implementation as the canvas** (`relay_core`).

**Script-side loop template** (what "auto-run" looks like in a script):

```python
# The only difference from the canvas is who advances the stage number: a button there, this loop here.
# You do not pick the prompt —— H3RelayChain takes block k from `prompts` by `stage_index`.
import json, time, urllib.request

BASE = "http://127.0.0.1:8188"
wf = json.load(open("my_workflow_api.json", encoding="utf-8"))
CHAIN, BRIDGE, SAVE, LOAD = "958", "961", "902", "960"   # ← your own node ids
SEGMENTS = 3

for k in range(SEGMENTS):
    for nid in (CHAIN, BRIDGE, SAVE, LOAD):
        # ⚠ all four stage numbers must match; on older graphs (0.6.14 and earlier)
        #   add a stage_index input to the Chain node first
        wf[nid]["inputs"]["stage_index"] = k
    wf[BRIDGE]["inputs"]["run_id"] = "myfilm"             # same run_id ⇒ all segments in one directory
    body = json.dumps({"prompt": wf, "client_id": "my-script"}).encode("utf-8")
    req = urllib.request.Request(BASE + "/prompt", body, {"Content-Type": "application/json"})
    pid = json.load(urllib.request.urlopen(req))["prompt_id"]
    while True:                                           # wait for this segment
        h = json.load(urllib.request.urlopen(BASE + "/history/" + pid))
        if pid in h:
            break
        time.sleep(2)
```

**The three non-UI concatenation paths** (all share `relay_core.assemble_mp4_segments`, identical
behaviour):

① **Command line** (easiest; use a python with torch+av — usually your ComfyUI interpreter):

```bash
python tools/concat_segments.py s1.mp4 s2.mp4 s3.mp4 -o film.mp4 --audio aac256
# optional: --audio aac192|lossless   --crf 16   --json
#           --pcm p1.safetensors p2.safetensors - -   ← PCM sidecars per segment, in segment order, `-` = none
```
Exit code **0 = all four assertions passed**, 1 = not (the file may still have been written, kept for
forensics).

② **Library call** (embed in your own pipeline):

```python
import sys; sys.path.insert(0, "<ComfyUI>/custom_nodes/ComfyUI-H3-Latent-Relay")
from relay_core import assemble_mp4_segments
rep = assemble_mp4_segments(["s1.mp4", "s2.mp4"], "film.mp4",
                            audio_codec="aac", audio_bitrate="256k",
                            pcm_paths=["p1.safetensors", None])   # sidecars optional, use None to skip
print(rep["ok"], rep["asserts"], rep["report"])
```

③ **Backend route** (you are already running ComfyUI and want the **server** to pull segments from
history):

```bash
curl -X POST http://127.0.0.1:8188/h3relay/concat -H "Content-Type: application/json" \
  -d '{"prompt_ids":["<id1>","<id2>"],"count":0,"out_name":"film","audio_out":"aac_256k","video_crf":16}'
```
Without `prompt_ids` it rescans history (the most recent `count` submissions whose graph contains this
Chain node); `audio_out` has three levels = `aac_256k` (default) / `aac_192k` / `pcm_lossless`.

**How to obtain sidecar (PCM) paths:** either pass them in segment order via `--pcm`, or read them from
history (same criterion as the route, fixed key `h3relay_pcm`):

```bash
curl -s http://127.0.0.1:8188/history/<prompt_id> | \
  python -c "import json,sys;d=json.load(sys.stdin);\
  print([o for n in d.values() for o in ((n[\"outputs\"].get(\"18\") or {}).get(\"h3relay_pcm\") or [])])"
```
(Replace `18` with your TrimAV / AudioSeam node id; when both exist the AudioSeam copy is preferred.)

**Dependencies:** concatenation needs `av` (bundled with the ComfyUI host; otherwise
`pip install "av>=17"`); everything else needs only torch + safetensors. **A sidecar is purely additive**:
it does not change a single byte of the segment file, so external tools (ffmpeg etc.) keep reading the mp4
as before.

---

## 8. Offline self-test

```bash
python tests/test_relay_core.py         # expect 0 failures
node   tests/test_prompt_dispatch.mjs   # expect 0 failures (Chain prompt distribution, pure functions)
python tools/review_050.py              # expect 0 failures (docs ↔ code consistency)
python tools/smoke_nodes.py             # expect 0 failures (node-layer smoke test)
```

**Hundreds of assertions, zero GPU, no models loaded.** Exact counts are intentionally kept in one place
per assertion set and machine-checked (README.md §8 carries them; per-group detail in
[`docs/08-testing.md`](docs/08-testing.md)).

The command-line concatenation entry point (§7.4) is smoke-tested in the same suite:

```bash
python tools/concat_segments.py s1.mp4 s2.mp4 -o film.mp4 --json   # exit 0 = four assertions passed
```

The scripts locate the ComfyUI root automatically; if it lives elsewhere use
`COMFYUI_PATH=/path/to/ComfyUI python tests/test_relay_core.py`.

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `stage_index ≥ 1` yet "cannot load the previous segment" | `run_id` mismatch / previous segment was never saved | make both `run_id` values identical and confirm segment 1 ran |
| No nodes at all in the node list | Not a ComfyUI with H3, or the backend was not restarted | update ComfyUI + restart the Python process. **Two-step diagnosis:** ① look for the startup line `[H3 Relay] v… 已加载｜节点 N 个（出口 …）｜时序契约 …` — **if it is absent the pack never loaded** (most often `custom_nodes/` is nested one level too deep); ② if present, `curl -s 127.0.0.1:8188/h3relay/health` returns version / API exit / registered node count / timing contract / upstream deps / Python+torch / OS in one shot |
| Continuation "seems to do nothing" | ComfyUI build without H3 support | check that `comfy_extras/nodes_minimax_h3.py` exists |
| The output replays the previous segment at the seam | TrimAV's `trim_frames` is not wired to bridge `[2]` | connect it per §4 |
| **A/V drifts more and more** (lip-sync clearly off from segment 3) | **audio bypasses TrimAV**: `VAEDecodeAudio` wired straight into the save node, so video was trimmed and audio was not ⇒ ~0.9 s per seam, accumulating | rewire the save node's `audio` to `TrimAV [1] audio` (optionally via `AudioSeam [0] audio`) — see §4 |
| **Every segment has the same dialogue** | `prompts` was not filled (auto-run's default is to resubmit the *same* graph with only the stage number changed) | fill `prompts` (`---`-separated, block *k* for segment *k*) and start the chain; for a single segment, editing the prompt by hand works too. ⚠ **If you submit JSON from a script**, you must also wire `Chain.prompt` to the prompt node's `prompt` (the canvas-only path is unavailable to scripts — §7.4) |
| After concatenation every seam has ~0.87 s of frozen video / audio shifted earlier | concatenated with `ffmpeg -f concat` or `acrossfade` | use the method in §7.1 plus the self-check |
| Concatenation reports "segment k audio is 0.X s longer than video ⇒ the audio line probably bypasses TrimAV" | the save node's `audio` comes from `VAEDecodeAudio` (untrimmed) | rewire to `TrimAV [1] audio` (or via `AudioSeam [0] audio`), re-run that segment, concatenate again |
| The report says a segment's "audio source = AAC" | no PCM sidecar for it (old graph / `save_pcm` off / that submission never ran TrimAV) | for full audio quality confirm TrimAV's `save_pcm` is on and that audio leaves from it, then re-run that segment |
| `🔍 H3 潜空间分块放大` errors "install the author's pack (MIT) first" | `Comfyui_Minimax_h3_latent_Upscaler` is not installed (this pack is only an adapter and does not copy its code) | install per §2.1 and download weights from the author's HF repo, then restart |
| `model_name` dropdown is empty | weights are not in `ComfyUI/models/latent_upscale_models/`, or you put them there without restarting | download from <https://huggingface.co/LBH-123-AI/Minimax_h3_latent_upscaler> and **restart the backend** (the listing is scanned once at load) |
| After raising `chunks` the **picture changed** (not just VRAM) | the upscaler contains **3D volumetric attention**: tiling cuts cross-tile temporal context, and the `overlap` blend only mitigates it. Measured, same seed and params, `chunks=1` vs `4`: per-frame MAE **5.62/255**, 0 of 192 frames identical (peak 1483 → 1120 MB) | **`chunks=1` is the only path consistent with upstream whole-segment inference**; raise it only under OOM, and re-inspect as a "quality for VRAM" trade |
| Every segment takes 20+ s with `lowfreq_pull` on | box blur cost **∝ radius²**, and `lowfreq_blur` defaults to **64**: measured 2.07 MP / 12 frames = **22.1 s** (8.6 s at 0.80 MP) — strongly resolution dependent, and only paid when `lowfreq_pull` is on (off by default = 0 s) | lower `lowfreq_blur` to **16–32**: measured k=32 → 5.6 s, k=16 → 1.6 s, k=9 → 0.6 s. Quality cost is tiny — measured seam step 32/64 = **0.0011 vs 0.0024** ([`docs/02`](docs/02-parameters.md)) ⇒ **at high resolutions you should lower it by default** |
| No sound in the browser after choosing `pcm_lossless` | the finished track is PCM f32 and browsers do not play PCM | expected for the **master level**: it is for editing/archiving. Use the default `aac_256k` for preview |
| Log says "⚠ PCM 边车写入失败" | disk full / directory not writable / safetensors missing | **does not affect this segment's output**; concatenation falls back to decoding. Free space or `pip install safetensors` |
| The log no longer shows 🧪 DTW / trim→jump curves / appearance triples | TrimAV's `diagnostics` is **off by default** (all three are print-only and do not affect trimming) | enable `diagnostics` if you want them |

The complete troubleshooting table, the workflow-file self-check tool and API submission details are in
[`docs/05-troubleshooting.md`](docs/05-troubleshooting.md).

---

## 10. FAQ

> All of these were actually asked; answers carry **measured numbers**.

### 10.1 Does auto-running eat VRAM as it goes? Will later segments hit a hardware wall?
**VRAM does not accumulate.** Each submission is an **independent execution** (auto-run is just "queue
again"), and models are staged per segment — measured across 4 segments, the staged amounts are
**identical** (`MiniMaxH3 21099MB / TEModel 17034MB / VideoVAE 2677MB / AudioVAE 576MB`).
**Disk does accumulate:** `output/relay_kit/<run_id>/` ≈ 12 MB/segment (latent 8.9 + audio 4), plus the
PCM sidecar ≈ 2 MB/segment (can be disabled), plus the segment mp4s ⇒ **~25 MB/segment, 100 segments ≈
2.5 GB**. Archive by `run_id`.

### 10.2 Does picture quality degrade as the film gets longer?
**Not "because you are further into the film"**: the segment-to-segment relay is a **bit-exact latent
tensor hand-off** — no pixels, no re-encoding, no decode→encode generation stacking; each segment is
VAE-decoded once.
But **cross-segment appearance drift is real** (a generation-side effect, not pipeline loss): measured
appearance logs over a 4-segment chain, brightness mean `0.3697 → 0.3107` from segment 2 to 4 (≈ −16 %),
std −0.008, sharpness +0.0007 (the latter two non-monotonic). Countermeasures: `H3RelayPost`'s
`match_prev` (cross-segment statistics matching) / `lowfreq_pull` (⚠ at high resolution lower
`lowfreq_blur` to 32 first — the default 64 costs 22 s/segment at 2 MP, see §9).

### 10.3 Is A/V sync still valid through the PCM sidecar?
**Yes, and more robustly than the old path.** The sidecar is TrimAV's **post-trim** audio — the same cut
as the video it outputs, hence naturally equal-length and same-source. At concatenation time each segment
is precisely truncated/padded to `frames ÷ fps × sample rate` (truncate the tail if long, pad silence with
a warning if short) ⇒ every segment's audio length == its video length, **zero accumulated drift**.
Measured on 4 segments / 702 frames: all four landing points **0 samples**, |A−V| **0.0011 s**; with
`pcm_lossless` the finished track is **bit-identical** to the sidecar.
⚠️ Boundary: this aligns **segment-valid durations**, not subtitle-level sync — it guarantees "the seam
does not drift".

### 10.4 If I never auto-concatenate (manual node wiring only), do sidecars affect anything else?
**Purely additive; not a single byte of the segment file changes.** A sidecar is just **one extra file**
(≈2 MB/segment); unless you click 🧩 or enable `auto_concat`, the backend concatenation route is never
called. **External tools (ffmpeg, your own assembly script) read the mp4 and ignore sidecars.** To avoid
them entirely, turn off `H3RelayTrimAV.save_pcm` (concatenation then falls back to decoding and says so).

### 10.5 Can I use MKV / a lossless audio track?
**MKV can be written** (the host's `SaveVideo` offers `mp4/mkv/webm`) **but it buys nothing**: its audio
codec is hard-coded (`libopus if WEBM else aac`; `CreateVideo` hard-codes aac) ⇒ **the host cannot give you
a lossless audio track**. Our concatenation goes through PyAV and controls the container, and measured
**PCM (`pcm_s16le` / `pcm_f32le`) writes straight into mp4** ⇒ default is mp4, the lossless level is also
mp4, **no MKV dependency introduced**.
(Measured: `flac` fails in mp4/mov/mkv alike and only works in a native `.flac`, which is why the lossless
level is PCM and not FLAC.)

### 10.6 How long does concatenation take? Can it hang while "looking for segment files"?
**Neither takes time.** Measured on a 4-segment 29.25 s film: probing 0.12 s + concatenation **1.26 s**
(stream copy for video + audio re-encode) + four assertions **0.019 s**.
**Path lookup never scans directories and never guesses file names**: it does a dictionary scan of the
`/history` ComfyUI already has, plus `os.path.isfile` (microseconds, zero GPU, zero directory walking);
missing files are reported **before encoding starts** (no "wait ages and then find out").
**Paths are never hard-coded to the official `output/`** — that would create the reverse problem ("only
works on my machine"): third-party save nodes use custom save paths, users start ComfyUI with
`--output-directory`, containers mount drives — hard-coding simply fails. This pack's rule: **trust only
the path the node itself reports** (`abs_path` first, fall back to `type`+`subfolder`; no hard-coded key
whitelist).

### 10.7 The scripts are configured on the node, but the button does nothing?
`app.queuePrompt` / `api.fetchApi` are **front-end internal interfaces** and may have different shapes in
different ComfyUI versions. When they are missing this pack **says so** in the Chain node's `status`
field (no silent failure) and points at the paths that still work — see §7.4.

### 10.8 Is a full-width, constant-amplitude left-right sway between segments a pipeline problem?
**Usually not** — it is **self-contradictory motion instruction in the prompt**: the same directional
displacement is split across non-equivalent degrees of freedom, e.g. asking both for "the camera pans
horizontally and continuously" **and** "a feature in frame enlarges" (the latter is a **dolly-in**). The
model flip-flops between the two motion readings frame by frame ⇒ a constant-amplitude, non-decaying sway.
**Fix:** give each segment **one** active motion; give a **speed reference** ("at the same pace as the
footsteps") or an **open end** ("until it never reaches a certain position"); always write background as a
**result** ("slides out of frame behind him"), not a zoom target; when subjects move in different
directions, do not write "moves along with X".

### 10.9 In a first-pass → 🔍 upscale → second-pass chain, why can't the second pass use the bridge's `conditioning`?
**Because keyframe pinning goes through `minimax_keyframes`, and the packer assumes the keyframe shares
this segment's grid.** After upscaling the target grid has changed (e.g. 26×46 → 30×54 latent) while the
bridge's anchor is still on the native grid ⇒ they cannot be matched when packing, and the failure is
immediate on the second pass's first step:

```
RuntimeError: shape mismatch: value tensor of shape [2392, 96]
              cannot be broadcast to indexing result of shape [3134, 96]
（comfy/ldm/minimax/model.py: all_video_rows[~img_update] = cond_video_rows）
```

**Wiring:** only the **first pass** (native domain) `BasicGuider` takes the bridge's 4th output; the
**second pass** takes the prompt node's `positive`. The second pass still does not redraw the seam region —
**latent-side pinning (copied prefix + noise mask) is grid-independent and still applies**; only the
conditioning-side "framing pin" is dropped.
For contrast: the appearance anchor travels a different channel, `minimax_refs`, which **does** allow a
different resolution than the target (that is how `ref_anchor_latent` anchors across resolutions) — do not
confuse the two channels. Full wiring in the [`examples/`](examples/README.md) full-flow graph.

### 10.10 Not enough VRAM but I want to keep detail — which sampling chain?
**First, what not to do:** do not stuff a progressive sampler (SelfLift etc.) **directly into this pack's
bridge**. On a **continuation chain** that path charges two extra costs — the low-resolution prefix uses a
**downsampled mask and anchor** (the true source only appears at full resolution), so `transition_step`
doubles as a seam-quality knob; and its "tiled VRAM saving" is **mutually exclusive** with the noise mask ⇒
as long as the bridge is present (= a mask exists), `lowres_scale` is the only VRAM knob left. Mechanism
and source-level verification in [`docs/03`](docs/03-sampling-and-design.md).

**This pack provides a third path** (already wired in the §4.1 full-flow graph, all 8 nodes present):

| Your constraint | Choose |
|---|---|
| VRAM is fine, detail first | full resolution throughout: `SamplerCustomAdvanced` + euler, `guider` ← the bridge's 4th output |
| VRAM only fits the native domain, but you do not want the progressive chain's detail ceiling | **two-pass**: first pass pins keyframes in the native domain → 🔍 upscale → second pass recovers detail at high resolution (`guider` ← the prompt node's `positive`, see §10.9) |
| VRAM **and** time are tight, or the **audio seam** matters more than detail | a third-party progressive sampler (measured better at the audio seam in our round), accepting the two costs above |

> Upscaling belongs to the **delivery branch, not the continuation contract**: `LatentSave` goes **before**
> the upscale.

### 10.11 The finished film has no sound / only `<name>-audio.mp4` has sound?
First a **file-name** issue, then a **version** issue:

1. **Different combine nodes produce different file names:** the core `CreateVideo → SaveVideo` produces a
   **single file with an audio track**; `VHS_VideoCombine`'s **main file has no audio track**, and the
   audio-bearing file is the extra `<name>-audio.mp4` it writes — hearing nothing in the main file is
   normal, not a bug.
2. **0.6.10 and earlier combined with `VHS_VideoCombine` produces a fully silent track:** this pack's audio
   is fp16, while VHS hard-codes `-f f32le` when muxing audio and **does not convert dtype** ⇒ every
   sample encodes to zero. **Upgrading to 0.6.11 fixes it** (this pack converts all AUDIO outputs to f32).
   Hallmark of the symptom: picture, reports and logs **all look normal**.
3. If you cannot upgrade right now: switch the combine node to the core `CreateVideo → SaveVideo`, or to
   `banzhangVideoCombine` (they convert to f32 themselves).

---

## 11. Mechanism

You can make films without reading this. Three key points:

- **Why it does not go blurry:** in `hard` mode the copy bridge makes the pinned region **re-draw zero
  times** (every step, the mask-0 region is pinned back to the copied latent), so the whole class of
  "reproduction artifacts" disappears structurally;
- **Why only specific frame counts are accepted:** H3's latent frame span is determined by token phase
  (`k%5`), so windows only accept `5+17k`; start and end must land on period boundaries, and out-of-range
  values always raise instead of silently snapping;
- **Why the head must be trimmed:** the pinned prefix is a **replay** in the finished film, so without
  trimming it replays or jumps at the splice point; how much to trim is measured on the spot by the node.

Full derivation (history and trade-offs of both routes, protocol sources, timing grid, how trim amounts
are measured, runtime contract guards, audio window lengths) in
[`docs/01-mechanism.md`](docs/01-mechanism.md).

---

## 12. License and attribution

- **This pack = MIT** (see [`LICENSE`](LICENSE)). Commercial use, modification and redistribution allowed;
  keep the copyright notice.
- **Third-party sources and credits → [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md):** mechanism and
  contract level references to `ComfyUI_MiniMaxH3_Director` (**Apache-2.0**, verified first-hand) and
  `comfyui-minimax-h3-audio-T8` (⚠️ **GPL-3.0-or-later**, used only as evidence for the native mask
  contract, **no code copied**); algorithm sources in the paper table.
- ⚠️ **The runtime host ComfyUI is GPL-3.0** (this pack imports its modules in-process but **does not copy
  its code**; ComfyUI's LICENSE contains no custom-node/plugin exception clause). Facts, our position and
  open items in [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md) **§一·C**.
- ⚠️ **About `ComfyUI-H3-Motion-Context` (GPL-3.0):** the anchor-synthesis part of `H3RelayMotionContext`
  **overlapped in expression** with that pack in the **public history of v0.2.1–v0.5.0**. It was
  **rewritten wholesale** on 2026-09-19 and the current version contains none of its derived expression.
  Historical commits were **not rewritten** and can be checked out for review (NOTICES §一·B).
  ⇒ **For strict compliance use ≥ 0.6.0.**
- ⚠️ **Optional runtime dependency `Comfyui_Minimax_h3_latent_Upscaler` (MIT, author `LBH-123-AI`):**
  `H3RelayLatentUpscale` calls its node classes at runtime and **reuses its weight directory**
  (`models/latent_upscale_models/`). No code is copied, but the **math of tile stitching** matches
  upstream ⇒ attribution in [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md) **§一·D**. Download
  weights from the author (§2.1).
- **Model weights are not included:** bring your own MiniMax-H3 weights; their licence and commercial terms
  are set by their provider.
- **Compliance:** this is a general-purpose video generation tool. Users must obey local law and the
  licences of the models/materials they use; do not use it to forge likenesses, spread disinformation, or
  infringe the rights of others. MIT provides no warranty of fitness.
- **This pack is not an official MiniMax product** and is neither affiliated with nor endorsed by MiniMax
  or ComfyUI.
- When redistributing, keep `LICENSE`, `THIRD-PARTY-NOTICES.md` and `licenses/`.

---

## 13. Documentation index

The detailed documents are **in Chinese** — they are the source of truth for anything not covered here.

| File | Content |
|---|---|
| [`docs/01-mechanism.md`](docs/01-mechanism.md) | history and trade-offs of both continuation routes · protocol sources · **which lines must stay in the native domain after upscaling (keyframes and refs take opposite attitudes to resolution)** · timing grid · why the head must be trimmed · how the trim amount is measured · runtime contract · audio window lengths |
| [`docs/02-parameters.md`](docs/02-parameters.md) | full parameter manual (bridge / TrimAV / Post / AudioSeam / **🔍 latent tile upscale**) |
| [`docs/03-sampling-and-design.md`](docs/03-sampling-and-design.md) | sampling-chain trade-offs · this pack's design stance (what should be measured is not left to user configuration) |
| [`docs/04-canvas-and-widgets.md`](docs/04-canvas-and-widgets.md) | canvas appearance · `advanced` folding · missing `prev_tail` on older graphs |
| [`docs/05-troubleshooting.md`](docs/05-troubleshooting.md) | complete troubleshooting table · workflow-file self-check · API submission |
| [`docs/06-continuity-scripting.md`](docs/06-continuity-scripting.md) | prompt-side discipline: head padding · dialogue-safe timing · last-frame anchor chaining · audio-seam companion rules |
| [`docs/07-chain.md`](docs/07-chain.md) | the Chain auto-run controller |
| [`docs/08-testing.md`](docs/08-testing.md) | offline self-test: per-group assertion detail · `tools/` inventory |
| [`docs/09-metrics.md`](docs/09-metrics.md) | reference ranges for observables (DTW residual / appearance drift) · how to self-calibrate |
| [`CHANGES.md`](CHANGES.md) | version history with the measurement evidence for each change |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | dev environment · testing discipline · licence terms |
| [`SECURITY.md`](SECURITY.md) | secrets / dependencies / network behaviour disclosure |
| [`tools/README.md`](tools/README.md) | the seven scripts and their expected values (six self-check/forensic + one concat CLI) |
| [`examples/README.md`](examples/README.md) | the two openable workflows: **minimal continuation demo** (19 nodes) and **full-flow example** (45 nodes, all 8 nodes present) · generators |
