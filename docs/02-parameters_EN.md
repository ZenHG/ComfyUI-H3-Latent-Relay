# Full parameter manual (bridge / TrimAV / Post / AudioSeam)

🌐 English translation of [`docs/02-parameters.md`](02-parameters.md). **The Chinese file is the source of
truth** — if the two ever disagree, the Chinese one wins.

> This file was split out of the README (0.6.0 restructuring).
> 📐 **Port notation:** throughout this document `[N]` is **0-based** (the *N+1*-th port in the UI = `[N-1]`).
> The "route N" style carried over from older drafts was 1-based and has been converted to `[N]` at the
> places that matter.
> The main README keeps only the conclusions and pointers needed for a quick read.

---

## Parameters

**H3RelayCopyBridge (the composite bridge)** (the **only bridge** since 0.6.0; the copy route has existed
since 0.4.0 and was folded together with the conditioning keyframe route in 0.6.0)

| Parameter | Default | Notes |
|---|---|---|
| `latent` | — | this segment's initial AV latent (connects upstream of the sampler) |
| `context_latent` | — | the previous segment's full AV latent (do not connect this node for segment 1) |
| `context_frames` | 22 | copy-window frame count; legal values 5/22/39/56/73/90/107/124, and it must be less than this segment's frame count |
| `mask_mode` | `hard` | **Mask semantics = `model generation * m + previous tail * (1-m)`; only m=0 pins, m=1 is a re-draw.**<br>`hard` = m=0 across the whole window (the pinned region is re-drawn zero times; **use this for real continuation**);<br>`ramp` (0.4.3) = a noise ramp of "soft evidence": hard pin at the far end (m=0) → **linear** rise to `ramp_top` at the seam end, anchored throughout (every step is anchored back to the copied tail by (1-m)). The native H3 contract reads a continuous m as a per-token sigma label (sigma_row = m·sigma_video), lightly harmonizing the seam side — it cures tone/exposure steps at a hard seam;<br>`blend` (0.5.0) = **two-way fusion** in the overlap region: same semantics as `ramp` but the weight rises on a **window shape** (zero derivative at both ends ⇒ softer transition, see `blend_shape`);<br>`window` (0.6.0) = **symmetric window**: low at both ends, high in the middle — the **essential difference** from ramp/blend is that **the seam end falls back low ⇒ the seam end is re-pinned**. Based on the sinusoidal window of VideoMerge (arXiv:2503.09926) and the WWS of Diff-VF (arXiv:2608.05976, "high weight at the centre, low at the boundary"; its ablation shows that removing it causes a **sudden jump at the window boundary**);<br>`taper` = head m=1.0 (**fully re-drawn**) decaying linearly to `seam_min` at the seam end — ⚠ **the pinned region is not actually pinned**; a control arm for "progressive hand-over" only.<br>⚠️ **One retracted old conclusion:** it used to say "ramp and hard are identical on all three metrics ⇒ no gain" — that was measured with a metric set that cannot see jumps. Re-measured with criteria that include "a single-frame spike after the seam + seam-pair composition correlation": **all four mask modes jump at the seam**, they just fail differently (hard keeps framing but has a large motion spike; ramp/blend flatten the motion but **rewrite the framing**) ⇒ **mask tuning alone cannot fix it**; it must be combined with the composite bridge (below) |
| `blend_top` | 0.50 | `blend` only: cap on the **model's share** at the seam end (the counterpart of ramp's `ramp_top`). 0 = degenerates to hard |
| `blend_tokens` | 0 | `blend` only: how many seam-end tokens take part in the fusion; 0 = spread across the whole window, a small value (2–3) = "fuse only the seam, lock the motion" |
| `blend_shape` | `smoothstep` | `blend` only: window shape. `smoothstep` = x²(3−2x) ／ `hann` = (1−cos πx)/2; **both are monotonic rises with zero derivative at both ends** |
| `taper_tokens` | 4 | taper only: how many tokens before the seam end take part in the linear transition |
| `seam_min` | 0.10 | taper only: the mask floor at the seam end (m value). `0` = the seam end is fully hard-locked; `0.3` = the seam end still keeps 30 % re-draw. **Note it only governs the seam end — the head is always 1.0, fully re-drawn** |
| `pin_audio` | `true` | copies the previous segment's audio tail into this segment's audio head (sampling context). Turn it off for video-only latents |
| `ramp_top` | 0.25 | ramp only: maximum m at the seam end (= the share of that token's sigma that takes part in denoising). `0` = degenerates to hard; `>0.5` noticeably weakens the anchoring and approaches taper — use with care |
| `ramp_tokens` | 0 | ramp only: number of seam-end tokens in the ramp; `0` = spread across the whole copy window; a small value (2–3) = a narrow ramp that "loosens only the seam and locks the motion" |
| **`conditioning`** (0.6.0) | — | **Composite-bridge switch:** connected ⇒ this node **also** appends keyframe pinning to conditioning (governs **framing/composition**), applied **in parallel** with the latent pin window (governs **motion**) — the two paths change different objects (latent vs conditioning) entering the sampler separately, so they do not conflict.<br>**Unconnected = copy bridge only, behaviour bit-identical to 0.5.0** (`[3]` returns `None`).<br>🔴 **Connecting it comes with a hard precondition: this segment's target latent and the anchor must share the same grid.** Inserting a latent upscale in the middle (🔍 0.6.8) raises the second pass's target resolution ⇒ connecting it is guaranteed to explode (see the second hard line in the 🔍 latent tile upscale section / README §10.9) ⇒ **only the native-domain first pass connects it**.<br>Measured (0.3 MP, controlled): brightness step at the seam **0.0009 vs 0.0097 using the conditioning bridge alone (10.8×)**; against the third-party `Motion-Context` under identical conditions it **wins 4 of 5 items** |
| `ref_anchor_latent` / `ref_anchor_stage` / `ref_anchor_frames` (0.6.0) | — / `-1` / `5` | **Global appearance anchor** (via the native `minimax_refs` protocol; near-zero noise, riding every step = an attention sink).<br>Based on **Diff-VF's Skip Residual Guidance** (mixing real detail in at the current noise level, both filling detail and guarding motion) — the refs block is its black-box equivalent ⇒ **prevents long-range drift and may cure post-seam blur flicker**.<br>When `ref_anchor_stage ≥ 0` it auto-loads `output/relay_kit/<run_id>/stage_<that value>` as the anchor; `-1` = off |
| `anchor_latent` / `anchor_blend` | — / `1.0` | **Tone-correction anchor** (0.5.0): the AV latent of the global anchor segment (usually segment 1). Once connected, the copied prefix's per-channel mean/variance is pulled towards the anchor segment by Reinhard moment matching ⇒ what gets pinned back each step is context with corrected tone, and this segment's new content follows back to the global tone. **The correction only affects the pinned prefix that gets trimmed away; it never touches the previous segment's finished frames.** `anchor_blend` = correction strength (1 = full alignment; lower it when the anchor segment is deliberately stylistically different; no effect without an anchor) |

**H3RelayTrimAV**

| Parameter | Default | Notes |
|---|---|---|
| `images` | — | this segment's fully decoded frames (including the head overlap) |
| `trim_frames` | 0 | number of **pinned-region** frames to trim. **Connect the bridge's `[2]` output (trim_frames) and it syncs automatically** — do not fill it in by hand |
| `fps` | 24.0 | frame rate, used to convert frame counts into audio samples |
| `audio` | — | this segment's audio. Only when connected is it trimmed together with the picture; otherwise A/V desynchronizes |
| `settle_frames` | **0** | **0 (default since 0.5.0) = no settle trimming** — measured, "trimming the settle" is the cause of seams jumping (trim 0 frames → jump 0.020, "barely perceptible" / trim 8 → 0.044 / trim 16 → 0.055); what remains is a sharpness gradient the eye tolerates very well. `-1` = measures the switch point and trims it (**cures blur but introduces a jump**); `N` = always trim N extra frames |
| `diagnostics` | **False** | **Master switch for read-only observability** (added 2026-09-25, appended last among optionals). Off by default ⇒ does not run the 🧪 DTW cost (E3) / trim-amount→jump curve / 🧪 appearance triples + drift curve (E4, needs `run_id`). All three **take no part in any trimming decision** (print-only) ⇒ turning it off **changes no frame/latent/audio**, it only saves CPU (measured: the three together **0.361 s/segment** @0.796 MP / 90 frames). ⚠ **The local production line looks at these three lines often** ⇒ the batch entry point passes `True` explicitly (at `l1_api`'s 903 construction site); the public default is off.<br>⚠ With `settle_frames=-1`, `detect_settle` / `boundary_jump_ratio` / `observation_profile` belong to the **trimming path** and are **not** affected by this switch |

**`[3]` output `prev_tail`** (added 0.5.0, appended last): = the last frame of the pinned region =
**`images[pin-1]`**. Feed it to `H3RelayPost`'s `guide` and the cross-segment items finally have "the other
side of the seam" to align against.
⚠️ **Whether it equals "the previous segment's last frame" depends on which bridge route you use:** with
the **composite bridge's `[0]` latent copy**, the first `pin` frames are a **bit-copy** of the previous
tail ⇒ `prev_tail` **is** the previous segment's last frame; with the **composite bridge's conditioning
keyframe route (the original latent bridge)**, the first `pin` frames are **this segment's model re-draw**
⇒ only an **approximation** (measured proxy error up to 0.006, larger than the seam step it is meant to
fix). So before using `guide` as an "exact reference", confirm which pinning method you are on (connecting
only `[3] conditioning` and not `[0] latent` means an approximation).

### H3RelayPost (added 0.5.0)

| Parameter | Default | Notes |
|---|---|---|
| `images` | — | **the only required input.** Connect TrimAV's `images` (post-trim frames) |
| `guide` | — | connect TrimAV's `prev_tail` (= the pinned region's last frame = the other side of the seam). **Only the two cross-segment items need it**; the items are skipped automatically when it is absent. ⚠️ **Whether it is "the previous segment's last frame" depends on the pinning method:** it **is** with the composite bridge's `[0]` latent copy; it is only approximate with the `[3]` conditioning route alone (see the `prev_tail` warning above) |
| `match_prev` | 0 | **Cross-segment statistics matching**: aligns the segment head's per-channel mean + standard deviation to `guide` (Reinhard-style first + second moment). **It only aligns statistics and copies no pose ⇒ no ghosting.** Cures the brightness step at the finished film's seam. ⚠️ **Measured recommendation: keep 0 on the latent (cond) route** — that route's seam is already at 0.0007 (below the perceptible threshold), while `guide` is only an approximate reference (see the `prev_tail` warning), so aligning to it **pushes the first frame away from the true reference** (measured step 0.0007 → 0.0039); its real home is the **copy bridge** (where `prev_tail` is the true reference) |
| `match_prev_frames` / `_gain_max` / `_offset_max` | 12 / 1.15 / 0.06 | affected frame count (weight decays linearly to 0) / contrast-gain guard rail / offset guard rail |
| `match_prev_stats_frames` | 1 | **How many frames the statistics are taken from** — the easiest item to get wrong. `1` (default) = only the frame **hugging the seam** (same convention as the single-frame `guide` ⇒ the correction is exactly "the step at the seam", and the first frame is pulled towards the guide **without overshooting it**); `0` = the old convention (aggregate over the whole affected region) — when the head has an **internal brightness gradient** it pushes the first frame **past** the guide and creates a step out of nowhere at the seam (measured on real renders: step ×12.6, **for comparison only**); `>1` = aggregate the first N frames |
| `lowfreq_pull` / `_frames` / `_blur` | 0 / 12 / 64 | **Low-frequency residual transfer**: aligns only the segment head's **low-frequency** tone to `guide`. Difference from `match_prev`: this item is low-frequency **additive** (first order) only. ⚠ The two scopes overlap; pick one.<br>⚠ **`_blur`'s runtime ∝ radius²**: the default 64 costs **22.1 s** at 2.07 MP / 12 frames (8.6 s at 0.80 MP) ⇒ **lower it to 16–32 at high resolution** (measured: k=32 → 5.6 s, k=16 → 1.6 s, k=9 → 0.6 s); seam step at 32/64 = **0.0011 vs 0.0024**, a tiny difference ⇒ lowering costs almost nothing |
| `head_zone_frames` | 24 | **shared affected-frame count for groups 2+3** (from the first post-trim frame): the four items "histogram / white balance / deconvolution / segment-body high-frequency transfer" below all act over it (each with a linear strength decay to 0). ⚠ **Groups 2/3 only** — the group-4 "blur-region sharpening" uses `settle_sharpen_frames` |
| `hist_match` / `wb_match` | 0 / 0 | **Within-segment** alignment (reference = this segment's body): histogram distribution / grey-world white balance (affected-frame count from `head_zone_frames`).<br>⚠ At high resolution `hist_match` **automatically falls back to equally spaced subsampling** to compute quantiles (when whole-frame pixels exceed `torch.quantile`'s 2²⁴ limit) — **not triggered at 0.3 MP and below** (exact quantiles), triggered from 0.8 MP, costing about **2 s/segment** and **independent of resolution** (2 MP / 4 MP are the same) |
| `deconv_strength` / `_radius` | 0 / 1.5 | deconvolution deblurring (Wiener), lifting the segment head's high frequencies (affected-frame count from `head_zone_frames`) |
| `detail_borrow` / `_blur` | 0 / 9 | segment-body high-frequency transfer (replace the head's high frequencies with the body's structure) (affected-frame count from `head_zone_frames`) |
| `settle_sharpen` / `_frames` | 0 / 24 | blur-region sharpening (unsharp, gradual). **No trimming, no timeline change ⇒ a jump is impossible**. ⚠ `_frames` governs this item only |
| `settle_auto` | 0 | **Adaptive blur-region compensation (the recommended replacement for the fixed sharpening above)**: the node **measures on the spot** each frame's sharpness deficit against the segment-body baseline (deepest at frame 2 after the seam, climbing back over ~15 frames) and sharpens in proportion to the deficit — the deeper the deficit, the more compensation; frames already at target (including the sharp frames 0–1) and the segment body are left **bit-exactly untouched**. Measured on real data: SelfLift's fast-test setting, blur region 61 % → 86 % (strength=1.0) / 99 % (1.5), sharpness ratio 0.945 → 1.007/1.038, climb 15 frames → ~8 frames; the official chain has almost no deficit ⇒ this item **correctly does almost nothing** (no false positives). ⚠ It can only restore contrast; detail that is truly gone cannot be recovered |
| `cross_seg_ack` | **False** | 🔴 **master switch for the two cross-segment items**. Unchecked (default) ⇒ `match_prev` / `lowfreq_pull` **do not act** (the report says why). **Why off by default:** with the `[3]` conditioning route alone, `prev_tail` is only an **approximation** (proxy error 0.006 > the seam step 0.0007 it is meant to fix), and measured alignment actually **amplifies the seam step ×12.6** ⇒ users are not allowed to walk into that trap by default. Check it once you have confirmed `guide` is the true reference (**a composite bridge with `[0]` latent copy connected**) |
| `baseline` | `robust` | **How the segment-body reference for groups 2/3 is taken.** `robust` (default) = take the frames in the **middle 50 % by per-frame brightness** of the segment body for statistics, and report the **dispersion** `(p75−p25)/median`; dispersion > 0.08 ⇒ groups 2/3 **abstain automatically** (rather than changing the picture with an unreliable baseline). `legacy` = the old 0.5.0 convention (whole-segment mean, no filtering, no abstention), **for reproducing the control only** |

**Everything defaults to 0 ⇒ bit-exact pass-through** (unwired behaviour is identical to 0.4.x). Execution
order: cross-segment tone alignment → within-segment tone alignment → high-frequency filling → sharpening
to finish.

Two disciplines (both forced by measurement during 0.5.0):
1. **Mutually exclusive groups**: `hist_match` ↔ `wb_match`, `deconv_strength` ↔ `detail_borrow` —
   **enabling both only applies the primary item** (group 2 keeps the histogram, group 3 keeps the
   deconvolution), the secondary item abstains and says so in the report — stacking makes **the next
   layer's baseline distorted and unattributable**.
2. **Auditable layer by layer**: after each layer the report appends a line such as
   `↳ 直方图匹配 后：段头亮度 0.24961→0.47531（+0.22569）｜高频 0.023201→0.035218（+51.8%）`, turning
   "N stacked black boxes" into "each layer attributable".

> ⚠️ **Legacy aliases with the same names — pick one, do not enable both:** `match_prev*` / `lowfreq_*` /
> `hist_match` / `wb_match` / `deconv_*` / `detail_*` / `settle_sharpen*` **still exist as widgets with
> the same names** on the TrimAV node (0.5.0 froze TrimAV at 22 widgets for backward compatibility with
> old workflows, and not one was removed; TrimAV's copy has no `guide` input, so the two cross-segment
> items can only use `images[pin-1]` internally — which happens to still be the true reference under the
> copy bridge, but is only approximate on the `[3]` conditioning route, and `match_prev_stats_frames`
> cannot be used to correct it there). **Always use the Post copy on new graphs**; enabling both sides
> performs the same operation twice (double sharpening / double tone alignment).

### H3RelayAudioSeam (added 0.5.0)

| Parameter | Default | Notes |
|---|---|---|
| `audio` | — | **the only required input.** Connect TrimAV's `audio` (**for segment 1**, if there is no TrimAV node, connect audio decoding `VAEDecodeAudio` directly) |
| `run_id` / `stage_index` | `relay` / 0 | **character-identical** to the bridge / LatentSave; run segments in order, do not skip |
| `patch_seconds` | 0 | **head patch length (seconds)**. 0 = off (bit-exact pass-through). To enable, try **0.2–1.2** first: the patch region = the previous segment's bed sound + a level-aligned fade-in, just enough to cover the head's priming silence; too long and it eats this segment's own downbeat/content (measured: 1.2 s once flattened a BGM downbeat). 🛡 **A dialogue guard has been on by default since 0.6.5**: this segment's head dialogue onset is detected automatically and the patch shrinks to 0.40 s before it (criterion: 2× the whole-source median energy + persistence filter; `patch_guard=0` disables it). ⚠ This node must be connected **from segment 1** (segment 2's bed source is the audio it writes) |
| `bed_select` | `tail` | bed-window selection: `tail` = **an equally long window at the previous segment's tail** (adjacent to the seam, naturally continuous in level/timbre with what precedes it — measured 29 dB better than "take the globally quietest"); `quiet` = the old 0.5.0 behaviour (take the quietest above a floor), for reproducing the control only. The patch also performs **level alignment** (aligned to the pre-seam level, ±6 dB clamped, with a 0.995 peak guard rail) |
| `patch_guard` | `True` | 🛡 **patch dialogue guard (on by default, 0.6.5)**. On = detect this segment's head dialogue onset automatically and shrink the patch to 0.40 s before it; if dialogue starts too early (< 0.10 s of usable window) the patch is disabled entirely and the head is kept as-is; a purely ambient head ⇒ bit-identical to the guard being off (zero side effects); BGM material does not trigger it (BGM peaks < 2× the median; the first version's P10 baseline was rejected by the S2 stress matrix). Verified on real renders: dialogue at 0.90 s ⇒ after shrinking, not one word lost; dialogue from frame 0 ⇒ zero words swallowed. |
| `tile_seconds` | 0 | bed-loop tile length (seconds). 0 = take the whole window; use 1.2 when the previous segment's clean window is shorter than N |
| `fade_seconds` | 0.25 | cross-fade width at the patch boundary (at second N). 0 = hard cut |
| `bed_stage` | 0 | which **segment**'s audio to use as the bed source. ⚠ must be **< this segment's stage number** (the bed source must be an already-rendered segment) |
| `join_*` (5, all folded) | see tooltips | **the join parameters of the `[2]` output `joined`**: `join_curve` (equal-power `qsin` / linear `tri`), `join_prime_ms` (encoder priming removal), `join_cross_ms` (cross window), `join_segment_seconds` (each segment's valid audio length = frames/fps), `join_align_seconds` (per-seam picture trim = trimmed frames/fps). `align>0` = **J-cut timeline conservation**: the output is strictly as long as the trimmed video (measured alignment error 0), no silence at the end, cross-segment loudness matched automatically (±6 dB clamped); `align=0` = the old shortening semantics (control only) |

**`[2]` output `joined`**: concatenates the chain's segment audios into **one track for the whole film**
(output by this node, ready for muxing/finishing) — the ~33 ms priming silence the encoder puts at the head
of each segment mp4 is removed here, and segments are cross-faded with an equal-power curve (~3 dB higher
than a linear fade for uncorrelated content); it is the node-level fix for "an instant stutter at the seam".
It also writes `audio_raw_*` (raw untrimmed audio = source material for J-cut intros) and
`audio_joined.safetensors` (the whole film as one track).

**Saving**: every segment writes `output/relay_kit/<run_id>/audio_NNNNN.safetensors` (same directory as the
latents; the `audio_` prefix means they never overwrite each other) — **segment 1 must connect this node
too**, because it is segment 2's bed source.

**Guards** (all raise explicitly, nothing silent): bed-source stage ≥ this segment → raise; bed-source file
missing → raise with a hint as to which segment to run first; `patch_seconds` longer than the segment →
**clamped to segment length − 1 with a warning** (rather than breaking the whole chain: an ambience patch is
not worth interrupting a 30-minute render).

**Hard constraint:** `context_latent` must have the **same resolution and channel count** as this segment.
Latents cannot be rescaled, so a mismatch means re-running the previous segment or restarting the chain
from this segment — it raises an explicit error.

### 🔍 H3RelayLatentUpscale (added 0.6.8)

**Picture domain · latent level.** Upstream is the community MIT node `MinimaxH3LatentUpscaler3D` (author
`LBH-123-AI`); this node is its **AV-packed-latent adapter**: unpack → call the upscaler tile by tile →
repack (**the audio stream is carried back untouched**).
Properties (verified against upstream source): **zero denoising / zero resampling**, and
`target_size=(t,H2,W2)` ⇒ **not one frame moves in time** (the frame grid, TrimAV's 22-frame trim amount,
and bit-continuity of lip-sync and the pinned region are all unaffected).

| Parameter | Default | Notes |
|---|---|---|
| `model_name` | dropdown | weights come from `models/latent_upscale_models/` (**the author's directory and the author's `scan_models()` are reused directly**; this pack creates no second directory and copies no second set). ⚠️ You need the **H3 latent upscaler** weights (24-channel), **not** an ESRGAN-style pixel model |
| `mode` | `scale by multiplier` | ① `scale by multiplier` = give a factor; ② **`target dimensions` = type width and height directly (recommended: pairs naturally with `ResolutionSelector` and lands on the 32-step grid)**; ③ `megapixels` = give total pixels |
| `scale` | `2.0` | 【×multiplier mode】 upscale only, never downscale (< 1 raises upstream). ⚠️ A factor that is not a multiple of the `align` step (e.g. ×1.1) **rounds each axis separately ⇒ anisotropic scaling + aspect-ratio drift**, so production should use `target dimensions` instead |
| `width` / `height` | `1280` / `704` | 【target-dimensions mode】 **pixel** width/height, automatically rounded to a multiple of `align` |
| `megapixels` | `1.0` | 【megapixel mode】 on a 12 GB card keep ≤ 1.5; lower it under VRAM pressure |
| `chunks` | `1` | 【VRAM knob】 how many tiles to cut along the time axis. **1 = the whole segment in one pass** (fastest, hungriest). 🔴 **`chunks>1` changes the picture** (measured at 480×864 / 47 frames: per-pixel MAE **5.62/255**, 0 of 192 frames bit-identical) — upstream is `AttnBlock3D` **volumetric attention**, hard-coded to 32 frames per block, so cutting tiles changes the context; it is **not a "mathematically equivalent VRAM saving"**. So: locked at 1 by default, raised only when VRAM genuinely cannot fit it, and **after raising it you must re-check the seam** |
| `overlap` | `5` | how many frames on each side of a tile boundary take part in the linear blend. Default 5 = upstream's 3D temporal kernel (`temporal_kernel`); **must not exceed the model's kernel width**, and is subject to the compliance constraint below |
| `align` | `32` | the multiple that output width/height are rounded to. **32 is a hard requirement of the H3 upscaler model — do not lower it** |
| `precision` | `bf16` | bf16/fp16 save VRAM; fp32 is the most stable but slow. Matching the weights' own precision is safest |
| `device` | `cuda` | the `cpu` setting is for debugging only and is far slower |
| `force_unload` | False | move the upscaler back to CPU after the run to free VRAM. ⚠ with several tiles it load/unloads repeatedly (slower); off by default |

**Compliance constraint (fail-closed):** every tile needs at least `2·overlap+1` frames ⇒
`chunks ≤ T // (2·overlap+1)`. Exceeding it **raises immediately and states the maximum legal tile count**;
parameters are never quietly rewritten (same discipline as the mask and the frame grid).
The stitching math matches upstream: replicate padding on both sides + linear blend weights + normalization
by accumulated weight.

**Outputs**: `[0] latent` (repacked, audio stream untouched), `[1] report` (requested tiles → actual tiles /
T / audio shape / number of upstream calls / peak VRAM / elapsed time — check this to see whether tiling
really went the way you asked).

**Two hard wiring rules:**
1. **The continuation contract is taken before the upscale**: `H3RelayLatentSave` must connect the **first
   pass's output** (native domain), not after this node — otherwise the next segment's bridge copies
   SR-domain data and runs SR again = double drift.
2. **The second pass's guider must not connect the bridge's `conditioning`**: the keyframe anchor
   (`minimax_keyframes`) and this segment's target **must share the same grid**; connecting it after the
   upscale explodes inside the model with `shape mismatch [2392,96] vs [3134,96]` (mechanism in
   [`docs/01-mechanism_EN.md`](01-mechanism_EN.md), "🔍 After upscaling: which line stays in the native
   domain").
   This is the **same family** as the `context_latent` "same resolution" hard constraint above: the
   latent-side one raises explicitly, the conditioning-side one explodes deep inside the model — so simply
   do not wire it.
