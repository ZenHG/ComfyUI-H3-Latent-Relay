# Mechanism: continuation routes · protocol sources · timing grid · head trimming and how much

<!-- EN-SYNC src=docs/01-mechanism.md stamped=2026-10-05 mode=see tools/en_sync.json -->

🌐 English translation of [`docs/01-mechanism.md`](01-mechanism.md). **The Chinese file is the source of
truth** — if the two ever disagree, the Chinese one wins.

> 📐 **Port notation:** throughout this document `[N]` is **0-based** (the *N+1*-th port in the UI = `[N-1]`).
> The "route N" style carried over from older drafts was 1-based and has been converted to `[N]` at the
> places that matter.

---

## 🧭 The two continuation routes: how to choose (measured comparison)

Since 0.6.0 this pack has **a single composite bridge**, `H3RelayCopyBridge`, which applies **both pinning
methods in parallel** (`[0]` latent bit-copy + `[3]` conditioning keyframe pinning). Before 0.5.x the two
**pinning methods** in the table below lived in two separate bridges and were mutually exclusive; from
0.6.0 they are merged into one composite bridge, applied in parallel on the same graph, each contributing
its strength — **neither blurry** nor **hard brightness jumps**:

| | **Composite bridge · `[0]` latent copy (governs motion)** | **Composite bridge · `[3]` conditioning keyframe pin (governs framing)** |
|---|---|---|
| Mechanism | the previous segment's tail latent is **bit-copied**; the pinned region is **re-drawn zero times** | the previous segment's tail is pinned into conditioning; the model **re-draws it** |
| Blur (reproduction settling) | **none** | **yes** (backstopped by settle detection on the observation side) |
| Tone / brightness step | **large** (measured 0.040 ≈ a hard cut) | **small** (measured 0.004) |
| Must new content be trimmed away? | **no** | **yes** (`settle_frames` will eat new content) |
| **Content / story risk** | **zero** | **yes** (when the head contains new content) |
| Suits | **content first**: dialogue or a key action in the head, not a single frame to lose | **smoothness first**: a little blur at the head is acceptable, and a dialogue-free buffer can be budgeted |

> **Want a third route:** `mask_mode="ramp"` (added in 0.4.3) — "soft evidence at the seam": every step
> still anchors back to `(1−m)`, with `m` ramping from 0 up to `ramp_top` (default 0.25). In design it
> might mitigate both blur and hard jumps.
> **Measured (2026-09-16, copy bridge):** `ramp_top=0.25` is **exactly identical** to `hard` on all three
> of step / sharpness / motion cosine (0.0407 vs 0.0402) — no intermediate state appeared ⇒ **treat it as
> a control arm for now, not a default**.
>
> The fact that `settle_frames` trims **new content** is in the warning box of item 1 under "Dialogue
> avoidance and audio handling at the seam".

## Protocol sources (native ComfyUI, no monkey patching needed)

```
comfy/ldm/minimax/model.py:376
    cond_t = cursor + FRAME_RESCALE * kf["resolved_frame_index"]
comfy/model_base.py:2186-2196
    keyframes = kwargs.get("minimax_keyframes") → payload["keyframes"]
    refs      = kwargs.get("minimax_refs")      → payload["refs"]
```

## 🔍 After upscaling: which line stays in the native domain (measured on the 0.6.8 full flow)

The production route is **first pass (native) → latent upscale → second pass (high resolution) → next
segment's continuation**. It touches two **different** protocol channels whose attitudes towards
"resolution" are exactly opposite:

| Channel | Who uses it | May it differ in resolution from this segment's target? | What a violation looks like |
|---|---|---|---|
| `minimax_keyframes` (**keyframe pin**) | composite bridge's `[3] conditioning` output | 🔴 **not allowed** | explodes deep inside the model: `shape mismatch [2392,96] vs [3134,96]` |
| `minimax_refs` (**appearance anchor**) | `ref_anchor_latent` / reference images, reference videos | ✅ allowed | — |

**Why keyframes are not allowed:** when the packer `PackedLayout` computes the row count for the keyframe
block it uses the **target grid** (`n = vt × frame_rows(target H,W)`; the source comment says
"sharing the target spatial grid"), while `_cond_video_rows()` patchifies **the anchor's own latent**.
The two counts are equal only when the grids match.

The numbers measured on 2026-09-22 split exactly this apart: native 416×736 (latent 46×26) =
**299 rows/frame**, SR to 480×864 (latent 54×30) = **405 rows/frame** ⇒ the packer reserves
`7×405 + 299(reference image) = 3134` rows while the anchor only has `7×299 + 299 = 2392` ⇒
`all_video_rows[~img_update] = cond_video_rows` fails to broadcast.

**Conclusion (written into the wiring discipline):** **only the native-domain first pass connects the
bridge's `[3] conditioning`**; the second pass's guider connects the prompt node's `positive`. That does
not mean the second pass loses pinning — **latent-side pinning (copied prefix + noise mask) is
grid-independent and still applies**; only the conditioning half ("framing pin") is given up.
The same hard rule applies elsewhere: **`H3RelayLatentSave` must take the first pass's final state before
the upscale** (the contract lives in the native domain), otherwise the next segment's bridge copies SR-domain
data and runs SR once more = double drift.

> This is the same family of problems as the "same resolution" hard constraint on `context_latent`:
> **the latent-side one raises explicitly, the conditioning-side one explodes inside the model**. So both
> must be avoided at wiring time; do not count on an error to catch it.

## Relation to pixel continuation

Both write the **same conditioning protocol**, so they are interchangeable:

| | Pixel continuation | This pack (latent bridge) |
|---|---|---|
| Data source | previous segment's mp4 → decoded frames | previous segment's AV latent |
| VAE round trip | yes (one encode) | **no** |
| Keyframe blocks | 1 (whole segment @frame 0) | **one block per token, placed by position** |
| Audio | hung on the same keyframe | goes through `minimax_refs`, a window independent of video |
| Bit-reproducible | no | **yes** |

> When using this pack, the **pixel-continuation fields of upstream nodes** (e.g. an H3 director node) must
> be left empty, otherwise both sets of anchors are written into `minimax_keyframes` and the picture fights
> itself.

## Timing grid (why only specific frame counts are usable)

The H3 VAE's temporal span is `(1,4,4,4,4)`: every 5 latent tokens cover 17 pixel frames.
Only windows that can be cut on **whole steps** are meaningful:

```
valid windows: 5, 22, 39, 56, 73, 90, 107, 124 ...   (= 5 + 17k)
22 frames =  7 latent tokens
39 frames = 12 latent tokens
```

A frame count off the grid renders a **globally displaced seam**, so this pack raises on out-of-range
values and **never silently snaps to a neighbour**.

One further hard constraint: the cut tail's start must land on a 5-token period boundary, otherwise each
token's frame span no longer matches its write position — again a displacement, again an error.

## ⚠️ Why the head must be trimmed

The first `trim_frames` frames of the pinned region are not new content — they are the model's
**re-generation of the previous segment's tail** (measured per-frame MAE ≈ 6/255; the same picture, sampled
twice).

Measured evidence: segment B's first frame is most similar to segment A's 22nd-from-last frame (MAE 3.09),
while the runner-up candidate's MAE is 17.5 — an isolated spike, so the match is real, not coincidence.

Without trimming: **~0.92 seconds of the picture replays** at the splice point (22 frames @24fps).

What gets trimmed is the **decoded pixel frames, not the latent** — a latent's frame span is decided by
token phase in the sequence (`k % 5`), and chopping the head desynchronizes that phase. Audio must be
trimmed **the same way**, or A/V desynchronizes.

## 🔍 How many frames to trim: not configurable, measured on the spot by the node

**A fixed trim count is not always right.** Measured (2026-09-11, low spec 448×768):

| Segment length | First frame after trimming only the pinned region (22 frames) | Notes |
|---|---|---|
| **73 frames** | ❌ has a discontinuity | the switch point is **between original frames 22 and 23** (MAE 6.52 → **102.27**); 22 leaves exactly the frame *before* the jump at trimmed frame 0 |
| **107 frames** | ✅ clean | the switch point is earlier and is trimmed away entirely |

Reason: at the start of a continuation segment the model first reproduces the previous segment's tail (the
pinned region) and **only then** switches to generating from the prompt. Which frame that switch lands on
depends on segment length and prompt — it is a **per-segment** quantity.

⇒ So it must not be a value you fill in, but **a value measured on the spot**: `H3RelayTrimAV` receives the
fully decoded frames, so the true switch point is in hand at that moment. With the default
`settle_frames = 0` (**no settle trimming**, since 0.5.0) **not a single settling frame is trimmed** —
measured, "trimming the settle" is what actually causes jumps at the seam (numbers in the red box below).
Want the old automatic trimming: use `-1` (measures the switch point and trims it, but **introduces a
jump**); use `N` = always trim N extra frames.

```
[H3 Relay] 裁首 23 帧 = 钉住 22 + 沉降 1 ｜ 自动检测：切换信号 203.0 / 基准 3.0（帧差突变/锐度塌陷/色档收敛）
           画面 73 → 50 帧（3.042s → 2.083s）；音频 97333 → 66666 采样点
[H3 Relay] 接缝自检：前 40 帧无突变（最大帧差 6.00，段内基线 3.00）→ 起点干净。
```

**By default you do nothing.** Three cases:

| What you want | `settle_frames` |
|---|---|
| A seam **without a jump** (default) | keep `0` — trims only the pinned region (the previous segment's tail reproduction; trimming it loses nothing) |
| A sharper segment head (cure blur) | `-1` (measures how many frames to trim) ⚠ **cost: introduces a jump** |
| Segments of **equal length** | the same positive number for the whole film, e.g. `9` (cost: each segment is 9 frames shorter) |

> 🔴 **Why no settle trimming by default?** Measured on the same segment, varying only the trim amount:
> trim 0 frames → jump **0.020** at the seam (barely perceptible) / trim 8 → 0.044 / trim 16 → 0.055
> (obviously jumping). **"Trimming the settle" is the cause of the jump**; the remaining frames are only a
> **sharpness gradient** (slightly soft first, then recovering), and the eye tolerates gradients extremely
> well. ⇒ Trading an "unacceptable discontinuity" for an "acceptable gradient" is a bad deal.

### What if the segment head is blurry: `settle_sharpen` (blur-region sharpening, fixed in the picture domain)

**Background:** with settle trimming off by default, a continuation segment keeps a few frames of
"blur caused by model re-drawing" at its start. Neither timing-axis option works — **trim it → jump**;
**do not trim → keep the blur**.
⇒ A third path: **fix it in the picture domain** — do not trim, do not touch the timeline, just lift the
high frequencies in the blurry region.

`settle_sharpen` applies an unsharp mask to the **first few frames after trimming**, with strength decaying
**linearly to 0** from the seam end (the blur itself is a gradient, so the strength should be one too);
`settle_sharpen_frames` controls how many frames are affected (default 24 ≈ 1 second).

| `settle_sharpen` | Effect |
|---|---|
| `0` (default) | off |
| `0.4`–`1.0` | recommended range (try `0.6` first, then inspect for halo / noise) |

**Measured on real films** (blur-region first-frame sharpness ÷ segment-body baseline):

| amount | Relative to baseline | Max pixel change |
|---|---|---|
| 0.00 (original) | 0.873 | 0 |
| 0.60 | 0.953 | 0.091 |
| **1.00** | **1.008** | 0.151 |
| 1.50 | 1.080 | 0.227 |

> ⚠️ **Honest boundary:** **sharpening can only restore "contrast", not "real detail that is already
> gone".** It works on re-draw blur where the structure survives but is soft; details that are truly lost
> cannot be recovered. Side effects are amplified noise and possible white edges (halo) on strong
> edges → the `amount` cap is 1.5.

### Seam-frame ghosting `seam_ghost` (**off by default** — measured: it only splits one jump into two)

Trimming the settle region is necessary (otherwise you keep a "blur first, sharp later" tail), but **the
trim itself is a temporal jump** — the more you trim, the bigger the jump at the seam. At 16 trimmed
frames the measured jump reaches **0.055** (a visible "hop").

**Seam-frame ghosting** replaces the **first frame after trimming** with a weighted blend of "previous
segment's last frame ⊕ this segment's first frame" (50/50 by default), so the **single jump at the seam is
split into two half-jumps across two frames**, flashing past → perceptually close to a continuous take.

| `seam_ghost` | Effect |
|---|---|
| `0` (**default**) | off, back to a hard join (a hop is visible at the seam) — measured **the best-looking setting** |
| `1` | 1 ghost frame. Numerically the jump goes **0.055 → 0.029** (below the 0.030 threshold = "no jump"), but it **looks worse**: total displacement only drops 7 %, the anomaly count goes 1→2 (one hop becomes two = two stutters), and the blended frame itself is a visible ghost |
| `2`–`6` | more ghost frames (a longer, softer cross-dissolve; generally unnecessary) |

- **Frame count is conserved** (replacement, not insertion) ⇒ **audio needs no change and there is no A/V drift**;
- **Zero sampling cost** (pure tensor post-processing; the sampler is untouched, nothing is rendered again);
- Cost: the ghost frame itself is a bit soft (measured sharpness 0.41×, **1 frame only**).

`seam_ghost_alpha` controls the previous segment's last-frame weight (default `0.5` = even split). Smaller
values favour this segment's first frame and make the ghost fainter.

> **A real A/B** (same latent, same seed, same trim amount; the only variable is the ghost switch):
> off: seam frame differences `1.26 1.11 `**`14.08`**` 1.51` → normalized 0.055 ⛔
> on:  seam frame differences `1.27 1.11 `**`7.22 7.51`**` 1.48` → normalized 0.029 ✅

Decision criterion: the window hugs the pinned region (`pin-1 .. pin+12`), the baseline is the median of
adjacent-frame differences taken from **inside the pinned region**, and `frame diff > 4 × max(baseline, 1.0)`
is treated as a switch point; any detection beyond the cap is left alone (hard-jump / tone route: 12 frames;
sharpness-collapse route: 36 frames — see the `settle_frames` note under "head buffer" above).
Pure CPU, computes only the short stretch it needs (~40 ms per segment), zero extra dependencies.

> **A shot cut inside the segment is not misidentified.** The old implementation scanned "the global max
> over the first 40 frames", so a real cut at frame 25 of the segment was treated as the seam and the new
> content was recommended for trimming; the narrow window cannot see it at all. The post-trim self-check
> likewise only suggests "trim N more frames" when the position is close enough to the front; otherwise it
> just reports the position and does not cut.

---

## 🛡 Runtime contract (timing-grid guard rail)

Before every execution, the bridge / trim nodes compare this pack's timing-grid constant `FRAME_PER_TOKEN`
against the source of the live ComfyUI's `comfy_extras/nodes_minimax_h3.py`: identical ⇒ proceed (cached
once per process); if an upstream update changed the grid, they **refuse to run** and report both values —
better to not run than to produce a bad film.

## 🔊 Audio window alignment convention

When converting `audio_frames` to the 40 Hz audio grid, the window is **widened upwards** to the nearest
whole step (previously it was rounded): the audio window is the model's context for "sound already played",
so carrying half a step extra is safe while truncating half a step may lose a beat. The widening is noted
in the report.
