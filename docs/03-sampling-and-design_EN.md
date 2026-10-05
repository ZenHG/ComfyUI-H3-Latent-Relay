# Sampling-chain trade-offs and this pack's design stance

<!-- EN-SYNC src=docs/03-sampling-and-design.md stamped=2026-10-05 mode=see tools/en_sync.json -->

🌐 English translation of [`docs/03-sampling-and-design.md`](03-sampling-and-design.md). **The Chinese file is the source of
truth** — if the two ever disagree, the Chinese one wins.

> This file was split out of the README (0.6.0 restructuring).
> 📐 **Port notation:** throughout this document `[N]` is **0-based** (the *N+1*-th port in the UI = `[N-1]`).
> The "route N" style carried over from older drafts was 1-based and has been converted to `[N]` at the
> places that matter.

---

## ✅ Good enough by default — don't touch the knobs first

Once you have run the three steps above, this is the pack's **default level**: the seam starts clean, no dropped
frames, no ghosting, and the audio seam is backed by the previous segment's ambience.
**For most users this is already enough.** These defaults are **not "a starting point for you to optimize", but
measured recommendations**:

| Knob | Default | Why we advise against changing it |
|---|---|---|
| `trim_frames` | `22` | Pin-window length. Measured: **lengthening (22→39) does not cure the blur, it gets worse instead** (segment-head sharpness 0.864→0.805, seam step 0.0627→0.0732) |
| `settle_frames` | `0` | Do not trim the settle. Measured: **trimming the settle is exactly the source of dropped frames** (trimming 8 frames → jump 0.044, 16 frames → 0.055) |
| `seam_ghost` | `0` | Measured: it only splits one jump into two |
| `mask_mode` | `hard` | Real continuation. The other three are **control arms** |
| Post's quality knobs | all `0` | All off = **bit-exact pass-through**. They "top off the last bit", they are not "must-enable" |
| Audio seam `patch_seconds` | `0` | `0` = pass-through; enable it only when needed (and **segment 1 must connect too** — it is the bed source for later segments) |

> **🚫 Control-arm list (don't treat as defaults)**: `mask_mode="taper"` (fake copy, the pinned region is **not actually pinned**),
> `mask_mode="ramp"` (on the copy bridge, measured **exactly equal** to `hard`: 0.0407 vs 0.0402),
> `mask_mode="blend"`, `match_prev=0.7` (measured: **amplifies the seam step 12.6×**).
> These are kept for our own control experiments; **never enable them in production defaults**.

**Only two cases need you to read on**:

1. **Picture detail/sharpness** is unsatisfying → first look at the "sampling chain" cut below, then at "parameters → Post";
2. **A very slight transition remains at the seam** and you want to squeeze out the last 1% → look at "the two continuation routes: how to choose" and "parameters".

### 🎛 Sampling chain: the trade-off between two routes

> This pack only handles latent / AV tensors, it **contains no sampler**.

**First check one thing about yourself**: open your sampler node and see whether it is **progressive resolution**
(low resolution early, raised to full resolution later, e.g. SelfLift's `transition_step` / `lowres_scale`),
then look at the scheduler's **total step count** `N`.
If it is, then **the number of steps actually run at full resolution ≈ `N − transition_step`** — this number is the
source of the detail ceiling.
The official docs' example (6 of 8 steps) and your likely default (4 of 6 steps) **both leave only 2 full-resolution steps** —
this is not a problem of some individual config.

🔴 **The relay chain has one more bill to pay (unique to this pack's main scenario, absent from standalone generation)**:
a progressive chain pins the seam region via the **noise mask** in `latent`, and the **low-resolution prefix uses a
downsampled mask + a downsampled anchor**, the true source is used only in the full-resolution phase.
⇒ **`transition_step` is simultaneously a "detail knob" and a "seam-quality knob"**: raising it = more steps pinning
frames on the blurring mask boundary.
This coupling only appears once you wire a progressive chain into this pack's bridge — don't treat it as a pure quality knob.

**Why it "blurs" (mechanism, not a tuning mistake)**: the low-resolution prefix's latent **physically has no high
frequency**; a learned upsampler can only "hallucinate" high frequency; the remaining 2 full-resolution steps are far
from enough to recover it.
Compared with an **all-full-resolution** official chain (`SamplerCustomAdvanced` + euler), the difference is **structural**.

**Measured (same seed, same prompt, same reference image, same parameters; 672×1184 / 90 frames / 2 segments; method and reproduction in `CHANGES.md`)**:

> ⚠️ **Scope** (don't read this as causation): the two arms ran **each node's currently-used config** — the official arm =
> `SamplerCustomAdvanced` + euler (8 steps + Refiner), the progressive arm = the UI's current values (`lowres_scale=0.5`, 6 steps + Refiner 2).
> So what it proves is **which of these two in-service configs has more detail**, **not** that "the progressive-resolution
> mechanism itself has a lower ceiling".
> The dose (`lowres_scale` / `transition_step`) is monotonic, and **the limit of that mechanism = the low-resolution
> prefix running all the way to full resolution = the official chain**.
> To get the number for "the progressive chain's ceiling", you must run a dose sweep yourself — this pack **did not**
> (see the honesty note at the end of this section).

| | **Progressive-resolution chain (SelfLift-type)** | **All full resolution (official `SamplerCustomAdvanced` + euler)** |
|---|---|---|
| Continuation **detail** (global high-frequency median) | 0.005750 | **0.011345 (1.97×)** |
| Continuation 1:1 local detail | 0.00604 | **0.01682 (2.78×)** |
| Brightness step at the seam | 0.0123 | **0.0094** |
| Seam-region low-frequency continuity ratio (→1 = continuous) | 0.76 | **0.98** |
| Audio seam `min_local/median` (after de-priming) | **0.909** | 0.332 |
| NFE per segment (denoising evaluations) | equal (progressive resolution **does not add** NFE) | equal |
| CFG | adjustable (e.g. 1.1) | **always 1** (`BasicGuider` has no negative slot) |

**When to keep a progressive-resolution chain**: VRAM or time constrained; you need its exclusive abilities
(dual high/low-resolution models / temporal-stability patch);
the material is such that **the audio seam matters more than picture detail** (the table above measured that chain's
audio seam to be better).
⚠️ Don't count "tiling to save VRAM" as one of its exclusive abilities: progressive samplers generally implement the
high-resolution phase with "tiled attention", and **tiling is mutually exclusive with the noise mask** (the mask must
be pinned down step by step, tiling cuts the cross-tile temporal context) ⇒
**whenever this pack's bridge is present (= there is a mask), the tiling route is closed**, and the only VRAM knob left
is `lowres_scale`.
**When to switch to all full resolution**: **picture detail first**, VRAM can hold it — this is the most direct step
toward "an imperceptible seam".

**There are actually more than two routes — this pack itself provides a third** (this file's §4.1 "full-flow tier"
wires it up, all 8 nodes present):

| | ① All full resolution (single pass) | ② Progressive resolution (single pass) | ③ Native-domain first pass → 🔍 upscale → high-resolution second pass (two passes) |
|---|---|---|---|
| Detail ceiling | **highest** (every step on the target grid) | limited by `N − transition_step` | **high** (every step of the second pass on the target grid) |
| VRAM / time | most expensive | **cheapest** | medium (first pass in the native domain, second pass at high resolution) |
| Keyframe pin (`conditioning` side) | ✅ throughout | ⚠️ anchor swapped at the transition point | ✅ **throughout the first pass** (the second-pass side **must be released**, or it blows up on the spot: `docs/05` §10.9) |
| Seam-region pinning (`latent` mask side) | ✅ | ⚠️ mask boundary downsampled with the low-resolution phase (the bill at the top of this section) | ✅ (first-pass side) |
| Out-of-chain dependency | — | needs a third-party progressive sampler | needs this pack's `🔍 H3 Relay · Latent Upscale` (+ the author's weights, see the main README §install·optional) |
| Fits | **picture detail first** | VRAM/time constrained, audio seam matters more than picture | VRAM only enough for the native domain, yet unwilling to accept the progressive chain's detail ceiling |

**Key discipline for the two-pass method** (this file's §4.1 has the full 12 steps): upscaling is a **delivery branch,
it does not enter the continuation contract** —
`Latent Save` must be wired **before** the upscale, otherwise the next segment gets an already-upscaled latent, and
upscaling it again = double drift.

🔴 **② and ③ are mutually exclusive, do not chain them**: the progressive sampler's **built-in** upsampling (called
`latent_upsample` in SelfLift) and this pack's `🔍 H3 Relay · Latent Upscale` **call the same third-party model, the same
weights, and do the same thing** (both take only the 24-channel H3 latent weights, both live in `models/latent_upscale_models/`).
Enabling both = the same latent upscaled twice ⇒ drift stacks.
Criterion: **upscaling should happen exactly once per chain** — either inside the sampler (②) or as a standalone node
between the two passes (③).

**If you keep progressive resolution, these parameters decide how detail and cost are distributed**:

| Parameter | Role | Cost |
|---|---|---|
| `transition_step` | the **step count** of the low-resolution prefix (= the cheap steps); full-resolution steps = `total steps − it`. **Lowering it ⇒ more full-resolution share at the same NFE**. Legal range `1 … total steps−2`, out of range raises directly (no snapping) | full-resolution steps cost more (time ↑). 🔴 **on the relay chain it is also a seam-quality knob** (see the bill at the top of this section) |
| `lowres_scale` | the **spatial ratio** of the low-resolution prefix (larger = finer), legal range `0.25–1.0`. **`1.0` equals all-full-resolution = the official chain** — the speedup gain goes to zero, leaving only the extra overhead of scheduling two phases | compute ↑ |
| `rho` | `>0` enables **anchor correction** (pull high-risk positions toward the pixel VAE anchor, curing upsampling artifacts). ⚠️ **it is not "correction strength" but "the fraction of positions corrected"**: in implementation it takes a high-quantile threshold of the error map and corrects only the most-inconsistent `rho` fraction (`0.6` = correct the worst 60%), the strength is given by the two parameters below. The official README's suggested starting point for H3 is `rho=0.6`, `w_min=w_max=1`. **This project previously had it at 0, i.e. the capability was never enabled** | one extra VAE decode→upsample→re-encode round trip. 🔴 **`rho=1.0` cannot be paired with the noise mask** (the pure-anchor route gets an empty tensor while the mask is present ⇒ `TypeError: unsupported operand type(s) for -: 'Tensor' and 'NoneType'`) |
| `w_min` / `w_max` | lower / upper bound of the correction **strength** (normalized by error within the selected positions) | — |
| tiling (`highres_tiling` and the like) | compute the high-resolution phase in tiles, saving VRAM | 🔴 **mutually exclusive with the noise mask** ⇒ unusable on the relay chain (see above) |
| built-in upsampler (`latent_upsample` and the like) | replace bilinear interpolation with a learned upsampler | same origin as this pack's `🔍`, **choose one** (see above) |

> ⚠️ **Honesty note (two layers, don't conflate them)**:
> - ✅ **Verified (source-level + pure-CPU reproduction, zero GPU)**: the semantic correction in the table above (`rho` = position fraction not strength),
>   `transition_step`'s seam coupling, the crash of `rho=1.0` + mask, tiling vs mask mutual exclusion, the built-in upsampler sharing its origin with this pack's `🔍`.
> - ❌ **Not measured (quality)**: the actual effect of `rho` / `w_min` / `w_max` / `transition_step` on **final picture quality and the audio seam** —
>   this pack **has not run a dose sweep**. The measured table above only answers "which of the two in-service configs is higher",
>   it does **not** answer "where the progressive mechanism's ceiling is". When measured it will go into `CHANGES.md`, no hype, no hiding.

### 🧭 This pack's design stance: **what can be measured is not given to the user to configure; an imperceptible seam is the goal**

- **What can be measured intelligently gets no knob**: how many frames to trim is **measured on the spot** by the node
  (`trim_frames` is the "you are not allowed to configure it" kind), settle detection, audio-bed window position, level
  alignment are all measured at runtime from your picture/audio —
  because these are **objectively measurable** (see "how many frames to trim: you don't configure it, the node measures it on the spot").
- **Only the "preference / hardware" kind is handed to you**: which chain to mirror, whether to enable quality-domain repair, the mask mode —
  these depend on your VRAM, material and taste, **there is no objective optimum**, so this pack puts them on the canvas with recommended defaults.
- **The default tier is the "imperceptible" tier**: run "5-minute quick start" and the seam should be **invisible**;
  when it is still visible, troubleshoot step by step in the order of the previous section (chain first, then quality-domain repair, finally mask/trim amount).

---

## 4.1 Full-flow tier: **all 8 nodes present** (`examples/fullflow_second_pass_latent_upscale_ui.json`) (formerly README §4.1)

> Moved in from the README (0.6.15 slimming); identical word for word to before the move.

The one above is the **minimum required** wiring. The production "first pass → 🔍 upscale → second pass → continuation"
uses all 8 nodes; the order and the two hard disciplines are as follows (★ = delivery branch, ☆ = continuation
contract, the two **are not the same line**):

```
① prompt node (official MiniMaxH3ReferenceToVideo)   both positive + LATENT outputs are used
② 🔗 H3 Relay · Latent Load   ☆ previous stage's on-disk latent (stage −1, automatic)
③ 🔗 H3 Relay · Copy Bridge      ← ① LATENT + ② context_latent; [3] conditioning → **feeds only the first pass's guider**
④ first pass SamplerCustomAdvanced (native resolution, guider ← ③[3])
⑤ 🔗 H3 Relay · Latent Save   ☆ ← ④ output            ★ MUST be connected **before** the upscale
⑥ 🔍 H3 Relay · Latent Upscale   ← ④ → + SetLatentNoiseMask (pins the first 7 latent frames)
⑦ second pass SamplerCustomAdvanced (at the higher resolution) guider ← **① positive** (🔴 NOT ③[3], see `docs/05` §10.9)
⑧ VAEDecode ← ⑦ picture ／ VAEDecodeAudio ← ④ audio   ★ audio never goes through SR or the second pass
⑨  continuation AudioSeam ← ⑧ audio → 🔗 H3 Relay · Trim AV (video and audio trimmed together + PCM sidecar written) ← ⑧ picture
⑩ 🔗 H3 Relay · Post ← ⑨ (guide ← TrimAV[3] prev_tail; all 20 knobs default to 0 = bit-exact pass-through)
⑪ CreateVideo → SaveVideo (single file with audio)
⑫ 🔗 H3 Relay · Chain: split `prompts` with `---` ⇒ the auto-run swaps prompts stage by stage (**since 0.6.15, wire `Chain.prompt` to the prompt node's `prompt`; script-submitted JSON gets the same feature**); when the run finishes press 🧩 拼成一条 to get the film directly
```

- 🔴 **The second pass does not connect the bridge's `conditioning`**: the keyframe-pin anchor (`minimax_keyframes`)
  and this segment's target **must be on the same grid**; after the first pass the picture is already upscaled ⇒
  connecting it blows up on the spot (mechanism and measured numbers in `docs/05` §10.9). Pinning on the latent side still applies.
- 🔴 **The contract takes the native domain**: `Latent Save` goes before the upscale; save the upscaled one ⇒ the next
  segment goes through SR again = double drift.
- Output-segment resolution = native × upscale factor (this example 416×736 → 480×864); **upscaling is a delivery branch,
  it does not enter the continuation contract**.

**Port quick reference** (0-based)
| Node | Inputs | Outputs |
|---|---|---|
| `H3RelayCopyBridge` (composite bridge) | `[0] latent` `[1] context_latent` `[2] context_frames` `[16] conditioning` `[17] run_id` `[18] stage_index` | `[0] latent` `[1] report` `[2] trim_frames` `[3] conditioning` |
| `H3RelayLatentSave` | `[0] latent` `[1] run_id` `[2] stage_index` `[3] note` | `[0] latent` `[1] path` |
| `H3RelayTrimAV` | `[0] images` `[1] trim_frames` `[2] fps` `[3] audio` `[4] settle_frames` | `[0] images` `[1] audio` `[2] report` `[3] prev_tail` |
| `H3RelayPost` | `[0] images` `[1] guide` + 20 knobs | `[0] images` `[1] report` |
| `H3RelayAudioSeam` | `[0] audio` `[1] run_id` `[2] stage_index` `[3] patch_seconds` `[5] fade_seconds` | `[0] audio` `[1] report` `[2] joined` |
| `H3RelayLatentLoad` (optional) | `[0] run_id` `[1] stage_index` `[2] explicit_path` | `[0] context_latent` `[1] info` |

**Segment 1**: the bridge's `[2]` outputs `0` → TrimAV passes through unchanged; the bridge does not read context (pass-through).
**From segment 2 on**: fill in `run_id` + `stage_index` and the bridge reads the previous segment itself from
`output/relay_kit/<run_id>/stage_NNNNN.safetensors`.
To draw the source on the graph (or to swap the source when resuming from a breakpoint), wire `🔗 H3 Relay · Latent Load` (`[0] context_latent` → bridge `[1]`).

As long as a node **outputs `CONDITIONING` + `LATENT`**, the wiring is the same — just replace the prompt node in the graph with it.

| Node used | Comes from |
|---|---|
| `MiniMaxH3ReferenceToVideo` / `MiniMaxH3ImageToVideo` / `MiniMaxH3AddGuide` | ComfyUI **built-in** |
| `CSGlideCastCS` (prompt + spec) | `ComfyUI-Banzhang-All` (third-party, **not a dependency of this pack**) |
| `SelfLiftH3Sampler` (AV sampler) | `comfyui-SelfLift` (third-party, **not a dependency of this pack**) |

> 📂 **The two demo workflows in `examples/`** additionally need **KJNodes** (they use `CreateFadeMaskAdvanced` /
> `MiniMaxChunkFeedForward`) — those are the **example graphs'** dependencies, not this pack's (this pack itself has zero
> third-party node-pack dependencies).
> If you only want the "minimal wiring", use `minimal_relay_official.json`, which is **all official nodes + this pack**.

Optional nodes (all off by default = bit-exact pass-through; deleting them still runs): **Post** (quality domain),
**audio seam** (audio domain), **Chain** (auto-run, see [`docs/07-chain.md`](07-chain_EN.md)).

> On the canvas the nodes draw only the main knobs by default, the rest fold into the `advanced` area; for handling graphs
> that cannot see the `prev_tail` output, see
> [`docs/04-canvas-and-widgets.md`](04-canvas-and-widgets_EN.md).

---
