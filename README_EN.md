# ComfyUI-H3-Latent-Relay

<!-- EN-SYNC src=README.md stamped=2026-10-05 mode=see tools/en_sync.json -->

🌐 **English** · [中文](README.md)

A **latent bridge** for MiniMax-H3 multi-segment continuation — a standalone ComfyUI node pack with
**zero third-party node-pack dependencies**. It needs only ComfyUI's own `torch` and `safetensors`, and is
not coupled to any third-party H3 node pack.

| Item | Value |
|---|---|
| Version | **0.6.28** |
| License | **MIT** (third-party attribution in [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)) |
| Host | **ComfyUI ≥ 0.35.0 with MiniMax-H3 support** (the host itself is GPL-3.0, see License) |

> 🔴 **One iron rule**: **every feature has two usage paths — canvas by hand (most users) and graph JSON
> submitted to the API — and both must go through the same node implementation.** A feature that only
> exists in a script is not a feature of this pack.

**What problem it solves.** When generating in segments, "continuation" answers "how does the new segment
know the state the previous one ended in". The mainstream approach is **pixel continuation** (decode the
previous mp4 → VAE re-encode → use as an anchor): it costs an extra VAE round trip (lossy), the anchor is
not the same object as the sampled latent (it drifts), and it can only anchor frame 0. This pack slices the
tail out of the previous segment's AV latent and writes it into `minimax_keyframes` at its true position:
**no re-encoding**, and **no decoded-frame cache on disk** (each segment only adds a ~12 MB latent sidecar
under `output/relay_kit/<run_id>/`). Mechanism and measurements: [`docs/01`](docs/01-mechanism_EN.md).

---

## Core features

**8 nodes in three layers — a minimal workflow only needs the first 3**:

| Layer | Nodes | Notes |
|---|---|---|
| **Required 3** | LatentSave · CopyBridge (composite) · TrimAV | without them it is not continuation |
| **Optional 4** | Post · AudioSeam · Chain · LatentUpscale | independent; **off by default = bit-exact pass-through** |
| **Manual routing only 1** | LatentLoad | the bridge fetches the source from disk itself; connect this only to **override the source** |

| Node (canvas name) | What it does |
|---|---|
| 🔗 **H3 Relay · Latent Save** | after sampling, writes this segment's AV latent to `output/relay_kit/<run_id>/stage_NNNNN.safetensors` |
| 🔗 **H3 Relay · Latent Load** | reads the previous segment (stage − 1); `explicit_path` overrides the source when resuming |
| 🔗 **H3 Relay · Copy Bridge** | **bit-copies** the previous segment's tail AV latent into this segment's initial latent + a noise mask (the pinned region is not re-drawn); with `[16] conditioning` connected it **also pins keyframes in parallel** (framing). `mask_mode` defaults to `hard` |
| 🔗 **H3 Relay · Trim AV** | trims the regenerated head frames (**video and audio together**) — without it the splice point replays or jumps; output `[3]` = `prev_tail` |
| 🔗 **H3 Relay · Post** | **picture domain**: cross-segment statistics matching / low-frequency pull / tone / deconvolution / high-frequency transfer / blur-region sharpening; all off by default |
| 🔗 **H3 Relay · Audio Seam** | **audio domain**: extends the previous segment's ambience into this segment's head, **length-preserving** (zero A/V shift); off by default |
| 🔍 **H3 Relay · Latent Upscale** | **picture domain · latent level**: unpack the AV-packed latent → tile through a learned 3D upscaler → repack. **Zero denoising, not one frame moved in time, audio carried back untouched** (needs an optional upstream dependency — see Install) |
| 🔗 **H3 Relay · Chain** | auto-run controller: advances stage numbers and queues; since 0.6.7 it also **rotates prompts** (`prompts` split on `---`) and **concatenates** (🧩) |

> **Three domains — do not mix them up:** timeline = `H3RelayTrimAV` (frozen) / picture = `H3RelayPost` /
> audio = `H3RelayAudioSeam`.
>
> Node labels are **English-only** since **0.6.18**; the Chinese term ↔ label mapping is in
> [`docs/04`](docs/04-canvas-and-widgets_EN.md) (Chinese).

---

## Requirements

| Dependency | Required? | Notes |
|---|---|---|
| **ComfyUI ≥ 0.35.0 with MiniMax-H3 support** | ✅ | needs `comfy_extras/nodes_minimax_h3.py` and a `comfy/model_base.py` that consumes `minimax_keyframes` / `minimax_refs`. On an older ComfyUI the nodes register but **continuation silently does nothing** |
| `torch` | ✅ | **top-level import** (without it the whole pack fails to register); bundled with ComfyUI |
| `safetensors` | ✅ | lazy import (only the save step fails if missing); bundled with ComfyUI |
| `av >= 17` | for concatenation | needed by "concatenate" and `tools/concat_segments.py` (bundled with the host). A missing module **errors explicitly** with install instructions; if PyAV is too old to provide the stream-copy template it **reports why and falls back to re-encoding** (stated in the report) — never silent |
| community pack `Comfyui_Minimax_h3_latent_Upscaler` + its weights | 🔍 upscale node only | see Install · optional |

`minimax_keyframes` / `minimax_refs` / `resolved_frame_index` are all ComfyUI **native protocols**: zero
monkey patching, and **nothing in another pack is patched**.

---

## Install

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/ZenHG/ComfyUI-H3-Latent-Relay.git
```

Or download the ZIP → unpack → rename the folder to `ComfyUI-H3-Latent-Relay` → put it in `custom_nodes/`.

Or **ComfyUI Manager → Custom Nodes Manager → search `h3-latent-relay`**
(node page: <https://registry.comfy.org/nodes/h3-latent-relay>). The pack name differs from the repository
name because the registry forbids "ComfyUI" in a pack name.

**Restart the ComfyUI backend afterwards** (ComfyUI-Manager → *Restart*; otherwise restart the Python
process) — refreshing the browser alone does not load new nodes. Search the node list for `🔗 H3 Relay` (7)
+ `🔍 H3 Relay · Latent Upscale` to see all 8.

**Optional: only needed for 🔍 latent tile upscaling** (not installing it does not affect the other 7
nodes; using it then raises an error naming the repository and the weights):

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler.git   # author LBH-123-AI (MIT)
```
Then download the **weights** from <https://huggingface.co/LBH-123-AI/Minimax_h3_latent_upscaler> into
`ComfyUI/models/latent_upscale_models/` and restart the backend. ⚠️ You need the **H3 latent upscaler**
weights (24-channel), **not** an ESRGAN-style pixel model.

**Node API exit: V3 (default) / V1** (`H3RELAY_NODE_API=v1` is a one-line fallback; restart required).
Both exits have identical node names, input/output order and defaults (machine-checked field by field) ⇒
**switching needs no graph edits**.

> **Why it must be one or the other**: the host loader is `if the module has NODE_CLASS_MAPPINGS → return` /
> `elif the module has comfy_entrypoint` ⇒ exporting both means **V3 never takes effect**; in V3 mode this pack
> therefore sets `NODE_CLASS_MAPPINGS = None` explicitly.
> **V3's known difference**: the schema is built **at package load time** (V1 is lazy) ⇒ if one node's parameter
> table fails to build, only **that node is skipped** and logged; everything else still loads (the pack never
> fails as a whole).
> **Lower-bound basis (re-derived 2026-10-02 from git history)**: the floor is **`>=0.35.0`**, set by the
> pack's latest hard dependency — the mask hard-lock needs the host's `mask_row_values` (#15375, v0.34.0+)
> **and** the forward pass scaling masked-row velocities by the mask (#15988, v0.35.0+); every other
> dependency is older (**H3 support itself is v0.30.0**). Verified neither changed between v0.35.0 and HEAD.
> On the metadata side, `web/` mounting (`WEB_DIRECTORY`, since 2023) and `comfy_config` parsing
> `pyproject.toml` (since 2025-06) both exist in v0.35.0 ⇒ **the floor holds for behaviour *and* metadata**.
> `requires-python = ">=3.10"` is deliberately conservative (no 3.10-only syntax is used, so relaxing it
> to 3.9 has no syntax obstacle).

---

## Usage

### Five-minute quickstart

> Fast path: open [`examples/minimal_relay_official.json`](examples/minimal_relay_official.json)
> (notes in [`examples/README.md`](examples/README.md)) and point the 4 loader
> dropdowns at your local model files.

**Segment 1**: ① set `stage_index` to `0` → ② type a prompt into the official prompt node → ③ Queue.
(**Do nothing else**: at stage 0 the bridge passes through. Do not unplug or bypass anything to express
"there is no previous segment" — `context_latent` is a required input.)

**From segment 2 on**: ④ set `stage_index` to `1` (**this one number only**) → ⑤ replace the prompt with
segment 2's content (open by picking up the previous segment's motion / camera; do not restart the action)
→ ⑥ Queue again.
The log should show `钉住 22 帧` / `裁首 N 帧 = 钉住 22 + 沉降 0` / `起点干净`.

| Key parameter | Segment 1 | From segment 2 | Set on |
|---|---|---|---|
| `stage_index` | `0` | `1`, `2`… | **LatentLoad + bridge + LatentSave** (all three must match; Chain syncs them) |
| `run_id` | one film name, e.g. `myfilm` | **character-identical to segment 1** | all **six** nodes that carry it (Chain / bridge / LatentSave / LatentLoad / TrimAV / AudioSeam) — on the canvas, editing one field syncs the rest of its group |
| `context_frames` | `22` | `22` (leave alone) | bridge (pin window; only 5/22/39/56/73/90/107/124) |
| `settle_frames` | — (segment 1 trims nothing) | keep `0` | TrimAV (`0` = no settle trimming, which is the recommendation) |
| `seam_ghost` / `settle_sharpen` | — | keep `0` | TrimAV / Post |

| The three most common ways to break it | Fix |
|---|---|
| Error "segment N cannot find the previous segment" | all **six** `run_id` values must be identical (on the canvas, editing one field auto-syncs the rest of its group; if it does not, see [`docs/05`](docs/05-troubleshooting_EN.md)); confirm segment 1 ran |
| The output replays the previous segment from frame 1 | TrimAV is not connected, or its `trim_frames` is not wired to bridge `[2]` |
| A new film picks up an old film's tail | `run_id` was not changed (same name ⇒ same file names) |

### Two prompt-related things this pack cares about

**This pack does not generate prompts and does not parse them** — the bridge and Trim AV only look at
tensors. Only two things in the prompt affect this pack's behaviour:

**① Voice anchor — the *decision* comes from the prompt**

| Step | What drives it |
|---|---|
| **Executing the anchor** | the **`voice_anchor` input** (`LoadAudio → VAEEncodeAudio` audio latent) — the node only sees tensors and **cannot read the prompt**, so it has no idea who speaks in this segment, or how many people do |
| **Deciding *who* to anchor** | the **prompt**: `<Subject N> … <d>[line]</d>` is the **only** place where "who speaks first in this segment" can be read by machine |
| **Speaker changes inside the segment** | the prompt's `<Subject N>` (the model switches on its own) — **the anchor cannot cover this**; its window is only the seam (~0.9 s) |

One command gives the verdict:

```bash
python tools/voice_bank.py advise --bank <voice bank> --prompt-file segN.md --prev-file segN-1.md
```

Rule: **the anchor is the first person who speaks in this segment**; **same speaker continuing ⇒ you need
not connect it**, **speaker change at the seam ⇒ you must**. If ≥2 people speak in one segment the anchor
only covers the seam — the rest is up to the prompt. Details in [`docs/07`](docs/07-chain_EN.md) §Voice anchor.

**② Dialogue timing at the seam — three things to write by the book**

| Discipline | In one line |
|---|---|
| **Head padding** | A continuation segment must leave room for "trim + margin" — never put dialogue on frame 0 |
| **Dialogue-safe moment** | Keep dialogue away from the seam (that region gets trimmed and pinned); place it later per the formula |
| **Last-frame anchor chain** | Segment N's last-frame state must be **copied verbatim** into segment N+1's prompt — do not just write "continue" |

Mechanism, formulas and measurements: [`docs/06`](docs/06-continuity-scripting_EN.md).

### Minimal wiring

```
prompt node (official / third-party)
  ├─ positive ──────────────────→ bridge [16] conditioning
  └─ LATENT ───────────────────→ bridge [0] latent
                                 bridge [1] context_latent  ← leave empty (auto-loads previous segment)
                                 bridge [2] context_frames  = 22
                                 bridge [17] run_id / [18] stage_index
                                        │
           bridge [0] latent ───────────┼──→ sampler latent_image      ★ MUST pass through the bridge
           bridge [3] conditioning ─────┼──→ sampler positive
           bridge [2] trim_frames ──────┼──→ TrimAV [1] trim_frames    ★ MUST be connected
                                        │
                                  sampler → LATENT ──→ 🔗 H3 Relay · Latent Save [0]
                                                 └──→ VAEDecode / VAEDecodeAudio
                                                           ↓
                               🔗 H3 Relay · Trim AV [0] images ← IMAGE ／ [3] audio ← VAEDecodeAudio
                                     │
                               [1] audio (optionally via AudioSeam)────→ CreateVideo → SaveVideo
                               [0] images ────────────────────────────↗
```

> 🔴 **Only one audio route is correct:** `TrimAV [1] audio` (optionally through `AudioSeam [0] audio`) →
> `CreateVideo.audio`. **Wiring `VAEDecodeAudio` straight into `CreateVideo` desynchronizes A/V** — the
> video drops the overlapping head frames while the audio keeps them, off by ~0.9 s per seam and
> **accumulating segment by segment** (lip-sync visibly off from segment 3 on). Nodes can detect "an input
> is unconnected"; they **cannot** detect "an output is left dangling".
> Any node that **outputs `CONDITIONING` + `LATENT`** wires up the same way — just swap it in for the
> prompt node. The production "first pass → 🔍 upscale → second pass → continuation" layout with all 8
> nodes is in [`docs/03`](docs/03-sampling-and-design_EN.md) §4.1.

### Concatenating segments into one film

Every segment produces its own mp4. On the Chain node enable **`auto_concat`** or click **🧩 拼成一条**
(video is stream-copied losslessly, audio is de-primed and aligned per segment, four assertions run
immediately); without the canvas:
`python tools/concat_segments.py s1.mp4 s2.mp4 -o film.mp4` (exit 0 = four assertions passed).
⚠️ Do not use `ffmpeg -f concat -c copy` or `acrossfade` (measured: inflated duration / 0.25 s stolen per
seam, accumulating). Details and audio-track levels (AAC 256k default / lossless PCM master) in
[`docs/10`](docs/10-audio-seam-and-concat_EN.md) §7.1.

### Cross-speaker voice timbre: voice anchor (0.6.19)

When dialogue alternates speakers per segment, the bridge feeds the previous speaker's voice
(the audio tail used as `audio_ref`) into the next segment's generation ⇒ timbre cross-contamination
(measured: same character F0 drift +15%, spectral centroid +27%). Fix = feed a **voice anchor of the
current segment's speaker** into the optional `voice_anchor` input of `Copy Bridge`
(`LoadAudio → VAEEncodeAudio`): not connected = bit-identical to the old behaviour; connected = timbre
locked and clipping disappears (measured: distance-to-baseline shrunk to 1/5).

🔵 **Both example workflows ship with the voice anchor already connected**
(`LoadAudio → VAEEncodeAudio → the bridge's voice_anchor`). The only thing you have to do is
**point that `LoadAudio` at your own character voice** (≥0.9 s of clean speech, emotion close to the
line; drop the file into `ComfyUI/input/` and pick it from the dropdown). `tools/voice_bank.py` can
collect anchors automatically from already-rendered segments.

**A wrong anchor can never produce a bad film — four fallbacks:**

| Situation | Behaviour |
|---|---|
| **You do not want it / have no clip ready** | **Just unplug the `voice_anchor` wire** = bit-identical to the old behaviour (no graph or parameter change, zero migration cost) |
| A **content-less** anchor (all-zero / constant / silent / contains NaN) | 🔴 **Raises on the spot** (fail-closed since 0.6.20). The message names the reason and tells you "unplug it to turn it off" — it **never fails silently into a bad film** |
| Anchor shorter than the window | Takes the full anchor + a `report` reminder (≥0.9 s recommended) |
| `pin_audio=False` | The anchor is ignored and the `report` says so (degradation is visible) |

**Limitations of the voice anchor:**

| Limitation | Details |
|---|---|
| 🔴 **Consumes 1 "audio reference" slot** | The official reference node `MiniMaxH3ReferenceToVideo` allows at most **3** `ref_audios`; this pack **always occupies 1** whenever `stage_index >= 1` (either the anchor tail window **or** the previous segment's audio tail). Fill all 3 and the model sees **4** audio references — the model **does not complain** (it does not validate the count), but that is **outside the official envelope and untested by us**. ⚠️ **This 1 has no "use none" switch** (with no anchor wired we still use the previous segment's tail) ⇒ to free a slot you must **reduce the `ref_audio` slots on the reference node yourself**. The bridge node's **title** shows the warning on canvas, and the `report` carries the authoritative number. See [`docs/07`](docs/07-chain_EN.md) §Audio reference budget |
| **The anchor replaces the whole audio tail window** | Any BGM / ambience inside that window is replaced too. If you have a music bed, **mix it into the anchor** first |
| **Timbre / pace / emotion are partly pinned** | The anchor is not "timbre only" — emotion and pacing follow it as well. Pick a clean clip whose **emotion matches the target line** |
| **The seam anchors only the first; the reference window can hold several** (0.6.27) | The **pinned** stretch at the seam (= the video pin window `context_frames`, **default** 22 frames ⇒ 0.925 s, adjustable 5/22/39/…/124) always covers only "**whoever speaks next at the seam**"; the `audio_ref` **reference window** since 0.6.27 auto-sizes on the **anchor itself** (`0` ⇒ **takes the full cap**, ≤6 s) ⇒ wire a **multi-speaker recording** as the anchor and the reference holds **several speakers'** timbre. To *name* someone use the official `ref_audios` slots |
| **The automatic reference window could in principle trigger recitation** | Since 0.6.25, with no anchor wired the window is chosen automatically (**2–6 s** — the official guideline is **2–12 s**; we align the lower bound at 2 s and **deliberately cap at 6 s**, because the reference row is sampled at every step, so longer costs more VRAM and time; vs 0.93 s before) ⇒ the window **is** the previous segment's dialogue. **No recitation observed in measurement** (0.93 → 4.18 s, 4.4×, no seg-1 content per ASR), but that is one segment, one generation — listen once on dialogue-dense material; for guaranteed-old behaviour set `audio_ref_seconds` (0.93 = old) |
| 🟣 **Other speakers go through the official slots** | This pack anchors only the one at the seam. To give the **other speakers** a voice reference, wire their audio into the **official** `ref_audios` slots — that is the **only** path that emits a text label `<Audio j>`, which is what the prompt references (the one this pack injects is appended to the DiT side only, has **no label and cannot be referenced from the prompt**; the `report` says so). Run it through the official `TrimAudioDuration` to ~0.9 s first (reference rows ride through every sampling step — long references cost time). ⚠️ **Ordinals count wired slots in order, not slot numbers**: wire only `ref_audio_2` and it is `<Audio 1>`; writing `<Audio 3>` **resolves to nothing and reports nothing** (when slots are non-contiguous the bridge node title states the real ordinals) |
| **Anchor length** | The tail window is 0.925 s ⇒ anchor **>= 0.9 s** (shorter: the full clip is used and the `report` warns); 1–4 s recommended |
| 🔴 **A pause at the anchor's tail costs you timbre fidelity** | `_voice_anchor_tail` on the consumer side takes only the **last 0.925 s** of the anchor (`a_frames/FPS`) ⇒ **silence at the tail is silence the model sees** (that part of the anchor is effectively no anchor at all). `voice_bank.py` prefers spans whose tail window is fully voiced and, when it cannot, **says so in the `report` and in `voices.json`'s `tail_clean`** ("timbre condition will degrade"). Seeing that warning means **the source's continuous speech is too short** — use a longer, cleaner single-speaker clip |
| 🔴 **Raising the anchor's sample rate does not raise the timbre ceiling** | The anchor is finally fed to the H3 audio VAE (native 32 kHz). A higher sample rate **preserves the spectrum that is already there; it does not invent high frequencies that were never captured** — measured, moving the anchor from 16 k to 32 k raised the −40 dB bandwidth from 4.7–5.3 kHz to 9.0–10.5 kHz, but **beyond that the source is the limit**: the audio tracks H3 itself produces only reach ~4.9 kHz of usable bandwidth (H3 *can* output 15 kHz, but those clips are sound effects / ambience, not speech). ⇒ **A better voice means a better source** (a real person / the original recording), not a higher sample rate |
| **Ignored when `pin_audio=False`** | Stated in the `report` (degradation is visible, never silent) |

⚠️ **One common mis-wiring**: **never feed `context_latent` into `voice_anchor`** — that is the
*previous segment's AV latent*, so using it as the anchor just sets the anchor source back to the
default "previous segment's audio tail" ⇒ **it looks enabled but is a no-op** (and it bypasses the
audio-grid reconciliation). The anchor source must be **this segment's speaker**.

🔵 **What if several people speak in one segment?** The anchor answers exactly **one** question:
"**who speaks next at the seam**". ⇒ **the anchor is the first person who speaks in this segment**;
**same speaker continuing ⇒ you need not connect it**, **speaker change at the seam ⇒ you must**.
Later speaker changes inside the segment are handled by the prompt's `<Subject N>` (the anchor cannot
cover them).

📋 **Ensemble scenes (many segments, many characters, low overlap between segments)** — the full
decision table + audio-reference budget ledger + degradation order are in
[`docs/07`](docs/07-chain_EN.md) §Ensemble scenes: one table answers "**N people speak in this segment ⇒ how to wire
the anchor and split the budget**".

### Other speakers' voice references: official slots + `<Audio j>` in the prompt (format rules)

This pack's voice anchor covers **only the one at the seam**, and it has **no text label** (it is appended
to the DiT side after tokenisation) ⇒ **the prompt cannot reference it**. To give the **other speakers**
their own voice references you must go through the **official** reference node — three steps:

1. Run that speaker's audio through `TrimAudioDuration` to **~0.9 s** (reference rows ride through **every
   sampling step**, so long references cost time).
2. Wire it into the **official** `MiniMaxH3ReferenceToVideo` `ref_audios.ref_audio_N` slot (**not** this
   pack's bridge node).
3. Reference it in the prompt with **`<Audio j>`** — the label is emitted by the official node at
   tokenisation time, in **wired order**, **1-based**.

🔴 **Three format rules** (getting them wrong **does not raise an error** — it just silently does nothing
or resolves to nothing):

| # | Rule | Note |
|---|---|---|
| 1 | **The ordinal is the j-th _wired_ slot** (ascending slot number), **not the slot number** | See the table below. When slots are non-contiguous the bridge node **title** states the real ordinals (it stays silent otherwise) |
| 2 | **The one this pack appends (anchor / previous segment's audio tail) has no label** | ⇒ **the prompt cannot reference it**; the `report` states its DiT-side ordinal = official count + 1 |
| 3 | Placement: the **same tag family** as `<Picture i>` / `<Video k>` (official: *Use the same tags when prompting*) | Putting it next to the matching `<Subject N>` definition / line is the most direct |

Ordinal mapping (the **easiest and completely silent** trap):

| Slots you actually wire | What the prompt should say | Cost of getting it wrong |
|---|---|---|
| `ref_audio_0` + `ref_audio_1` + `ref_audio_2` | `<Audio 1>` / `<Audio 2>` / `<Audio 3>` | Ordinals match slot numbers — fine |
| **only** `ref_audio_2` | **`<Audio 1>`** | writing `<Audio 3>` ⇒ **resolves to nothing, nothing reports anything** |
| `ref_audio_0` + `ref_audio_2` | `<Audio 1>` / `<Audio 2>` | same as above |

> ⚠️ Rules 1 and 2 are **confirmed by reading the source** (the tokenizer emits labels in enumeration order;
> this pack appends via `conditioning_set_values`). For rule 3, **which wording works best has not been A/B
> tested yet** — rely on rules 1 and 2. Mechanism and source references:
> [`docs/07-chain.md`](docs/07-chain_EN.md) §Several speakers in one segment.

### Want segment 2 to reproduce **everyone's** voice? One slot is enough (0.6.25; anchors work too since 0.6.27)

The route above (official slots + `<Audio j>`) is for "**naming who is who**". If what you want is
"**bring the whole timbre over without naming anyone**" (**multi-anchor**, **one single audio-reference
slot**) — use `audio_ref_seconds`:

| | Old behaviour | Since 0.6.25 |
|---|---|---|
| How much audio goes in that one slot | = the video pin window (22 frames ⇒ **0.93 s** — room for the last speaker only) | **2–6 s automatically** (walks back until 2 s of voiced content; the official guideline is 2–12 s — our cap is deliberately 6 s to save VRAM and time) |
| Slot budget | 1 | **still 1** (unchanged) |

- **No number to type**: `0` = automatic, the material decides (`min_voiced_s` = 2 s of voiced, cap 6 s).
- **No anchor wired** ⇒ the source is the **previous segment's audio tail** (the "everyone in segment 1" route).
- 🔵 **An anchor works too (0.6.27)** ⇒ the source is the **anchor itself**; `0` = automatic window sized on the
  **anchor's** voiced tail (**takes the full cap**, ≤6 s — a longer single-speaker reference holds the timbre better) ⇒ wire **your own multi-speaker recording** as the anchor
  to "bring everyone" **without depending on who was in the previous segment**. The pinned 0.925 s at the seam
  is **unaffected**.
- ⚠️ **Cost**: with no anchor the window **is** the previous segment's dialogue ⇒ the model **could in principle
  recite it**. **Not observed in measurement** (0.93 → 4.18 s window, 4.4×, no seg-1 content per ASR; the same
  criterion hits seg-1 keywords 3/3 on control samples) — but that covers one segment, one generation.
  (With an anchor the window is **that speaker's own speech** ⇒ far lower recitation risk.)
  To have a prompt *address* a specific person you still need the official slots above.
- The two routes are **complementary, not conflicting**: "bring everyone" ⇒ this parameter; "name someone"
  ⇒ official slots; "pin the timbre and own the seam" ⇒ the voice anchor.

Mechanism, the division of labour between the three channels, and the measured numbers:
[`docs/07-chain.md`](docs/07-chain_EN.md) §Several speakers in one segment **⑥**.

Implementation details and more (**audio-reference budget**, BGM, emotion, multi-speaker segments) in
[`docs/07-chain.md`](docs/07-chain_EN.md) §Voice anchor; bundled auto-collector `tools/voice_bank.py`
(optional ASR dialogue guard — works without funasr too).

### Node Chinese/EN toggle (0.6.28, **canvas button**)

Every node of this pack now carries a small 「**中 / EN**」 button: one click switches the titles, parameter
labels and port names of **all 8 nodes** between Chinese and English (dropdowns show Chinese while their
**stored value stays untouched**).

- It **follows the UI language by default** ⇒ an English UI does not get Chinese node
  labels; clicking the button overrides that, and the choice is remembered in the browser.
- 🔴 **Display only, never the graph**: only `label` / `title` are touched, **never `name` and never a
  dropdown's values** ⇒ not one byte of the workflow JSON changes. A title you renamed yourself is **kept**.
- It **coexists with the audio-reference-budget hint**: only the name part of the title is swapped, so a
  budget hint appended after it survives.
- It does **not** fight interface translation plugins (Global Translation & co.): those translate UI text,
  this one only handles this pack's own nodes.
- 🔴 **UI-only**: a script submitting JSON (`/prompt`) never touches the canvas ⇒ no button there
  (and nothing is lost).

Details and the word table: [`docs/04`](docs/04-canvas-and-widgets_EN.md) §Node Chinese/EN toggle.

---

## Repository layout

```
ComfyUI-H3-Latent-Relay/
├── __init__.py            # entry point: /h3relay/concat route, V1/V3 exit selection, startup banner
├── relay_core.py          # business core (single source of truth): timing grid / copy bridge / TrimAV / AudioSeam / concat
├── nodes.py               # node layer (the 8 nodes' inputs, outputs and human-readable reports)
├── layout_contract.py     # layout contract: pass through + leave a trace when upstream is missing, raise only on a real mismatch
├── v3/                    # V3 shell (io.ComfyNode + comfy_entrypoint); V1 goes through NODE_CLASS_MAPPINGS
├── exp/history_anchor_v2/ # E1' time-invariant history anchor (top-level import ⇒ required; inert without _tiha.json)
├── web/                   # front-end JS: the 🧩 concat button, Chain panel, `run_id` one-field-syncs-the-group, **the node Chinese/EN toggle** (canvas only)
├── examples/              # two openable workflows: minimal continuation (21 nodes) and full flow (47 nodes)
├── docs/                  # deep docs 01–10 (mechanism / parameters / sampling / canvas / troubleshooting / scripting / chain / tests / metrics / audio)
├── tests/                 # offline self-test (zero GPU): 512 assertions + V3 parity + prompt-dispatch pure functions
├── tools/                 # self-checks / forensic tools / concat CLI / bundler (incl. the en_sync docs gate)
├── licenses/              # third-party license texts shipped with the pack
├── dist/                  # bundler output (minimal distribution set + zip; not tracked)
├── pyproject.toml         # metadata (the host really reads requires-comfyui / deps; Comfy Registry reads name / Icon / PublisherId)
├── .comfyignore           # what `comfy node publish` ships (without it docs/tests/tools go to users too)
├── icon.png / icon.svg    # Registry / Manager card icon (400×400; the `.svg` is the source, `.png` is shipped)
├── banner.png / banner.svg # Registry node-page banner (21:9 = 1680×720; same source/render split)
├── requirements.txt       # dependencies for ComfyUI-Manager
└── (top level also has README_EN.md · CHANGES.md · CONTRIBUTING.md · SECURITY.md ·
     CODE_OF_CONDUCT.md · THIRD-PARTY-NOTICES.md · LICENSE · .github/)
```

At runtime only `__init__.py` · `relay_core.py` · `nodes.py` · `layout_contract.py` · `v3/` (4 files) ·
`exp/history_anchor_v2/` (3 files, imported at the top of `nodes.py`, hence required) · `web/` (3 JS files)
are actually loaded — the minimal distribution set in `dist/` is exactly those plus examples and metadata;
`docs/`, `tests/` and `tools/` are development-only.

---

## Configuration & hard rules

| # | Hard rule |
|---|---|
| 1 | **`run_id` must match character-identically across all six places** (LatentSave / bridge / LatentLoad / TrimAV / AudioSeam / Chain). **On the canvas, editing one field auto-syncs the rest of the group** (a name conflict blocks the run and lists which node holds which name — it never guesses); a script submitting JSON should write that field from a single variable (see [`docs/10`](docs/10-audio-seam-and-concat_EN.md) §7.4). Run segments in order, **never skip** |
| 2 | **Segment 1 needs nothing special**: the bridge passes through; `context_latent` is required — **do not unplug or bypass it** |
| 3 | 🔴 **Audio must come from TrimAV (or AudioSeam) output**; never wire `VAEDecodeAudio` directly into the save node (~0.9 s per seam, accumulating) |
| 4 | **`context_frames` only accepts `5+17k`** (5/22/39/56/73/90/107/124) and must be smaller than the segment length; out-of-range values **raise instead of snapping** |
| 5 | **Production `mask_mode` = `hard`** (zero re-draw in the pinned region); `taper` **does not pin** and is a control arm only |
| 6 | **Keep `settle_frames = 0`**: measured, "trimming the settle" is what causes jumps at the seam. To cure blur use Post's `settle_sharpen` / `settle_auto` (picture-domain fix, no timeline change ⇒ cannot introduce a jump) |
| 7 | **`patch_seconds ≤ 1.2`** (AudioSeam): enough to cover the 10 ms decoder silence at the head; use `0` when dialogue sits at the very head. **Shortening the patch is the primary defence for dialogue; the guard is only a backstop** |
| 8 | **🔍 Upscale once per chain**: this pack's upscale node and the one **built into** third-party progressive samplers (SelfLift etc.) are the same operation — enabling both upscales the same latent twice |
| 9 | **`chunks=1` is the only path consistent with upstream whole-segment inference**: `chunks>1` **changes the picture** (3D volumetric attention is cut); raise it only under OOM and **re-check the seam** |
| 10 | 🔴 **The continuation contract is taken in the native domain**: LatentSave goes **before** the upscale; the **second pass's guider must not connect the bridge's `conditioning`** (different grid ⇒ it explodes) |
| 11 | `diagnostics` is off by default (three print-only passes, **no effect on trimming**); enable it for DTW / trim-amount→jump curves / appearance drift. ⚠️ The "settle 1" seen in older posts is pre-0.5.0 — **trust this page and the node reports** |
| 12 | 🔴 **The canvas path and the script path must be the same node implementation** (see the iron rule at the top); purely front-end capabilities (the 🧩 button, Chain panel fields, **the `run_id` one-field-syncs-the-group behaviour**, **the node Chinese/EN toggle**) **are ignored** when a script submits JSON — script users take the three non-UI paths in [`docs/10`](docs/10-audio-seam-and-concat_EN.md) §7.4 |

---

## License and attribution

- **This pack = MIT** ([`LICENSE`](LICENSE)): commercial use, modification and redistribution allowed;
  keep the copyright notice.
- Third-party sources and credits: [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md) (including the fact
  that **the runtime host ComfyUI is GPL-3.0**, and our position on it).
- ⚠️ **About `H3RelayMotionContext` (GPL-3.0)**: its anchor-synthesis part **overlapped in expression** with
  that pack in the **public history of v0.2.1–v0.5.0**. It was **rewritten wholesale** on 2026-09-19 and the
  current version contains none of its derived expression; the historical commits were **not rewritten** and
  can be checked out for review (NOTICES §一·B) ⇒ **for strict compliance use ≥ 0.6.0**.
- **Model weights are not included**: bring your own MiniMax-H3 weights; their licence and commercial
  terms are set by their provider.
- Compliance: this is a general-purpose video generation tool. Users must obey local law and the licences
  of the models/materials they use; do not use it to forge likenesses, spread disinformation, or infringe
  the rights of others. MIT provides no warranty of fitness.
- **This pack is not an official MiniMax product** and is neither affiliated with nor endorsed by MiniMax
  or ComfyUI.
- When redistributing, keep [`LICENSE`](LICENSE) · [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md) ·
  `licenses/` alongside the pack.

---

## Documentation index

The Chinese documents are the source of truth; `README_EN.md` and the English `docs/01`–`docs/10` are
**derived artifacts**, machine-checked section by section by [`tools/en_sync.py`](tools/en_sync.py)
(edit the Chinese source and the gate stays red until the English follows).

| Document | Content |
|---|---|
| [`docs/01-mechanism.md`](docs/01-mechanism.md) · 🇬🇧 [English](docs/01-mechanism_EN.md) | history and trade-offs of both continuation routes · protocol sources · which line stays in the native domain after upscaling · timing grid · why the head must be trimmed · runtime contract |
| [`docs/02-parameters.md`](docs/02-parameters.md) · 🇬🇧 [English](docs/02-parameters_EN.md) | **full parameter manual** (bridge / TrimAV / Post / AudioSeam / 🔍 upscale, every `advanced` item and default) |
| [`docs/03-sampling-and-design.md`](docs/03-sampling-and-design.md) · 🇬🇧 [English](docs/03-sampling-and-design_EN.md) | sampling-chain trade-offs · **§4.1 full-flow layout: all 8 nodes present** |
| [`docs/04-canvas-and-widgets.md`](docs/04-canvas-and-widgets.md) · 🇬🇧 [English](docs/04-canvas-and-widgets_EN.md) | canvas appearance · `advanced` folding · missing `prev_tail` on older graphs |
| [`docs/05-troubleshooting.md`](docs/05-troubleshooting.md) · 🇬🇧 [English](docs/05-troubleshooting_EN.md) | **complete troubleshooting table** · **FAQ** · workflow-file self-check · API submission |
| [`docs/06-continuity-scripting.md`](docs/06-continuity-scripting.md) · 🇬🇧 [English](docs/06-continuity-scripting_EN.md) | prompt-side discipline: head padding · dialogue-safe timing · last-frame anchor chaining |
| [`docs/07-chain.md`](docs/07-chain.md) · 🇬🇧 [English](docs/07-chain_EN.md) | the Chain auto-run controller (prompts / concat / resume) |
| [`docs/08-testing.md`](docs/08-testing.md) · 🇬🇧 [English](docs/08-testing_EN.md) | offline self-test: 31 assertion groups (512 assertions) · tool inventory |
| [`docs/09-metrics.md`](docs/09-metrics.md) · 🇬🇧 [English](docs/09-metrics_EN.md) | reference ranges for observables (DTW residual / appearance drift) · self-calibration |
| 🇨🇳 [`docs/10-audio-seam-and-concat.md`](docs/10-audio-seam-and-concat.md) | **audio seam & multi-segment concatenation** — Chinese only for now: seam conventions and tuning guide · **dialogue protection** · criterion limits · track levels · sidecars · **script usage without the canvas** |
| [`RELEASE-NOTES.md`](RELEASE-NOTES.md) | **user-facing release notes** (bilingual, one section per version) · **read this before upgrading** |
| [`CHANGES.md`](CHANGES.md) | version history with the measurement evidence for each change (the **developer** record) |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | dev environment · testing discipline · **English-doc sync discipline** · licence terms |
| [`RELEASING.md`](RELEASING.md) | **release policy (single source of truth)**: GitHub + Comfy Registry must be updated as a pair · one command `tools/release.py` · failure handling · key discipline |
| [`SECURITY.md`](SECURITY.md) | secrets / dependencies / network behaviour disclosure |
| [`tools/README.md`](tools/README.md) | the thirteen scripts and their expected values (nine self-check/forensic + concat CLI + bundler + **releaser** + **voice-bank collector**) |
| [`examples/README.md`](examples/README.md) | the two openable workflows (minimal continuation 21 nodes / full flow 47 nodes) · generators |
| [`README.md`](README.md) | **Chinese source of truth** (install / wiring / nodes / parameters / troubleshooting / FAQ) — in-depth derivations always link out to that file and `docs/` |
