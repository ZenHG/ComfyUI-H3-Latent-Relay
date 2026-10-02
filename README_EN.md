# ComfyUI-H3-Latent-Relay

<!-- EN-SYNC src=README.md stamped=2026-09-30 mode=see tools/en_sync.json -->

🌐 **English (this file)** · [中文（默认 / source of truth）](README.md)

A **latent bridge** for MiniMax-H3 multi-segment continuation — a standalone ComfyUI node pack with
**zero third-party node-pack dependencies**. It needs only ComfyUI's own `torch` and `safetensors`, and is
not coupled to any third-party H3 node pack.

| Item | Value |
|---|---|
| Version | **0.6.20** (8 nodes; the composite bridge `H3RelayCopyBridge` is the **only** bridge) |
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

**8 nodes in three layers — a minimal workflow only needs the first 3** (the other 5 are
"delete-and-it-still-runs" optionals):

| Layer | Nodes | Notes |
|---|---|---|
| **Required 3** | LatentSave · CopyBridge (composite) · TrimAV | without them it is not continuation |
| **Optional 4** | Post · AudioSeam · Chain · LatentUpscale | independent; **off by default = bit-exact pass-through** |
| **Manual routing only 1** | LatentLoad | the bridge fetches the source from disk itself; connect this only to **override the source** |

| Node (canvas name) | What it does |
|---|---|
| 🔗 **H3 Relay · Latent Save** | after sampling, writes this segment's AV latent to `output/relay_kit/<run_id>/stage_NNNNN.safetensors` |
| 🔗 **H3 Relay · Latent Load** | reads the previous segment (stage − 1); `explicit_path` overrides the source when resuming |
| 🔗 **H3 Relay · Copy Bridge（复合桥）** | **bit-copies** the previous segment's tail AV latent into this segment's initial latent + a noise mask (the pinned region is not re-drawn); with `[16] conditioning` connected it **also pins keyframes in parallel** (framing). `mask_mode` defaults to `hard` |
| 🔗 **H3 Relay · Trim AV** | trims the regenerated head frames (**video and audio together**) — without it the splice point replays or jumps; output `[3]` = `prev_tail` |
| 🔗 **H3 Relay · Post** | **picture domain**: cross-segment statistics matching / low-frequency pull / tone / deconvolution / high-frequency transfer / blur-region sharpening; all off by default |
| 🔗 **H3 Relay · Audio Seam** | **audio domain**: extends the previous segment's ambience into this segment's head, **length-preserving** (zero A/V shift); off by default |
| 🔍 **H3 Relay · Latent Upscale** | **picture domain · latent level**: unpack the AV-packed latent → tile through a learned 3D upscaler → repack. **Zero denoising, not one frame moved in time, audio carried back untouched** (needs an optional upstream dependency — see Install) |
| 🔗 **H3 Relay · Chain** | auto-run controller: advances stage numbers and queues; since 0.6.7 it also **rotates prompts** (`prompts` split on `---`) and **concatenates** (🧩) |

> **Three domains — do not mix them up:** timeline = `H3RelayTrimAV` (frozen) / picture = `H3RelayPost` /
> audio = `H3RelayAudioSeam`.
>
> Node labels are **English-only** since **0.6.18**; the Chinese term ↔ label mapping is in
> [`docs/04`](docs/04-canvas-and-widgets.md) (Chinese).

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
| Error "segment N cannot find the previous segment" | all **six** `run_id` values must be identical (on the canvas, editing one field auto-syncs the rest of its group; if it does not, see [`docs/05`](docs/05-troubleshooting.md)); confirm segment 1 ran |
| The output replays the previous segment from frame 1 | TrimAV is not connected, or its `trim_frames` is not wired to bridge `[2]` |
| A new film picks up an old film's tail | `run_id` was not changed (same name ⇒ same file names) |

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
                               [1] audio (optionally via 🔗 音频缝)────→ CreateVideo → SaveVideo
                               [0] images ────────────────────────────↗
```

> 🔴 **Only one audio route is correct:** `TrimAV [1] audio` (optionally through `AudioSeam [0] audio`) →
> `CreateVideo.audio`. **Wiring `VAEDecodeAudio` straight into `CreateVideo` desynchronizes A/V** — the
> video drops the overlapping head frames while the audio keeps them, off by ~0.9 s per seam and
> **accumulating segment by segment** (lip-sync visibly off from segment 3 on). Nodes can detect "an input
> is unconnected"; they **cannot** detect "an output is left dangling" — this one is on you.
> Any node that **outputs `CONDITIONING` + `LATENT`** wires up the same way — just swap it in for the
> prompt node. The production "first pass → 🔍 upscale → second pass → continuation" layout with all 8
> nodes is in [`docs/03`](docs/03-sampling-and-design.md) §4.1.

### Concatenating segments into one film

Every segment produces its own mp4. On the Chain node enable **`auto_concat`** or click **🧩 拼成一条**
(video is stream-copied losslessly, audio is de-primed and aligned per segment, four assertions run
immediately); without the canvas:
`python tools/concat_segments.py s1.mp4 s2.mp4 -o film.mp4` (exit 0 = four assertions passed).
⚠️ Do not use `ffmpeg -f concat -c copy` or `acrossfade` (measured: inflated duration / 0.25 s stolen per
seam, accumulating). Details and audio-track levels (AAC 256k default / lossless PCM master) in
[`docs/10`](docs/10-audio-seam-and-concat.md) §7.1.

### Cross-speaker voice timbre: voice anchor (0.6.19)

When dialogue alternates speakers per segment, the bridge feeds the previous speaker's voice
(the audio tail used as `audio_ref`) into the next segment's generation ⇒ timbre cross-contamination
(measured: same character F0 drift +15%, spectral centroid +27%). Fix = feed a **voice anchor of the
current segment's speaker** into the optional `voice_anchor` input of `Copy Bridge`
(`LoadAudio → VAEEncodeAudio`): not connected = bit-identical to the old behaviour; connected = timbre
locked and clipping disappears (measured: distance-to-baseline shrunk to 1/5).
⚠️ **A content-less anchor raises on the spot** (all-zero / constant / silent / NaN) — it never fails
silently. **To turn the anchor off, unplug the wire** — do not feed it an empty latent (in particular
not the prompt node's `LATENT`, which is an empty AV latent). Implementation /
limitations (BGM, emotion, multi-speaker segments) / fallbacks in
[`docs/07-chain.md`](docs/07-chain.md) §声锚; bundled auto-collector `tools/voice_bank.py`
(optional ASR dialogue guard — works without funasr too).

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
├── web/                   # front-end JS: the 🧩 concat button, Chain panel, `run_id` one-field-syncs-the-group (canvas only)
├── examples/              # two openable workflows: minimal continuation (19 nodes) and full flow (45 nodes)
├── docs/                  # deep docs 01–10 (mechanism / parameters / sampling / canvas / troubleshooting / scripting / chain / tests / metrics / audio)
├── tests/                 # offline self-test (zero GPU): 447 assertions + V3 parity + prompt-dispatch pure functions
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
| 1 | **`run_id` must match character-identically across all six places** (LatentSave / bridge / LatentLoad / TrimAV / AudioSeam / Chain). **On the canvas, editing one field auto-syncs the rest of the group** (a name conflict blocks the run and lists which node holds which name — it never guesses); a script submitting JSON should write that field from a single variable (see [`docs/10`](docs/10-audio-seam-and-concat.md) §7.4). Run segments in order, **never skip** |
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
| 12 | 🔴 **The canvas path and the script path must be the same node implementation** (see the iron rule at the top); purely front-end capabilities (the 🧩 button, Chain panel fields, **the `run_id` one-field-syncs-the-group behaviour**) **are ignored** when a script submits JSON — script users take the three non-UI paths in [`docs/10`](docs/10-audio-seam-and-concat.md) §7.4 |

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

The Chinese documents are the source of truth; `README_EN.md` and the English `docs/01`/`docs/02` are
**derived artifacts**, machine-checked section by section by [`tools/en_sync.py`](tools/en_sync.py)
(edit the Chinese source and the gate stays red until the English follows).

| Document | Content |
|---|---|
| [`docs/01-mechanism.md`](docs/01-mechanism.md) · 🇬🇧 [English](docs/01-mechanism_EN.md) | history and trade-offs of both continuation routes · protocol sources · which line stays in the native domain after upscaling · timing grid · why the head must be trimmed · runtime contract |
| [`docs/02-parameters.md`](docs/02-parameters.md) · 🇬🇧 [English](docs/02-parameters_EN.md) | **full parameter manual** (bridge / TrimAV / Post / AudioSeam / 🔍 upscale, every `advanced` item and default) |
| [`docs/03-sampling-and-design.md`](docs/03-sampling-and-design.md) | sampling-chain trade-offs · **§4.1 full-flow layout: all 8 nodes present** |
| [`docs/04-canvas-and-widgets.md`](docs/04-canvas-and-widgets.md) | canvas appearance · `advanced` folding · missing `prev_tail` on older graphs |
| [`docs/05-troubleshooting.md`](docs/05-troubleshooting.md) | **complete troubleshooting table** · **FAQ** · workflow-file self-check · API submission |
| [`docs/06-continuity-scripting.md`](docs/06-continuity-scripting.md) | prompt-side discipline: head padding · dialogue-safe timing · last-frame anchor chaining |
| [`docs/07-chain.md`](docs/07-chain.md) | the Chain auto-run controller (prompts / concat / resume) |
| [`docs/08-testing.md`](docs/08-testing.md) | offline self-test: 29 assertion groups (447 assertions) · tool inventory |
| [`docs/09-metrics.md`](docs/09-metrics.md) | reference ranges for observables (DTW residual / appearance drift) · self-calibration |
| 🇨🇳 [`docs/10-audio-seam-and-concat.md`](docs/10-audio-seam-and-concat.md) | **audio seam & multi-segment concatenation** — Chinese only for now: seam conventions and tuning guide · **dialogue protection** · criterion limits · track levels · sidecars · **script usage without the canvas** |
| [`CHANGES.md`](CHANGES.md) | version history with the measurement evidence for each change |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | dev environment · testing discipline · **English-doc sync discipline** · licence terms |
| [`RELEASING.md`](RELEASING.md) | **release policy (single source of truth)**: GitHub + Comfy Registry must be updated as a pair · one command `tools/release.py` · failure handling · key discipline |
| [`SECURITY.md`](SECURITY.md) | secrets / dependencies / network behaviour disclosure |
| [`tools/README.md`](tools/README.md) | the thirteen scripts and their expected values (nine self-check/forensic + concat CLI + bundler + **releaser** + **voice-bank collector**) |
| [`examples/README.md`](examples/README.md) | the two openable workflows (minimal continuation 19 nodes / full flow 45 nodes) · generators |
