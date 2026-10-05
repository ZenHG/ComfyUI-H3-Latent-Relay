# 09 · Experimental-layer observables: reading conventions and reference ranges

<!-- EN-SYNC src=docs/09-metrics.md stamped=2026-10-05 mode=see tools/en_sync.json -->

🌐 English translation of [`docs/09-metrics.md`](09-metrics.md). **The Chinese file is the source of
truth** — if the two ever disagree, the Chinese one wins.

> Applies to: **trunk** (`main`). This file is about the **read-only observables** E3 (DTW alignment cost) and E4 (appearance drift
> curve) — both **promoted to permanent read-only observations** in `1c87aff` on 2026-09-22, controlled by `H3RelayTrimAV`'s
> `diagnostics` switch (off by default; turning it off changes no frame / latent / audio, it only saves CPU).
> ⚠️ In the experimental layer, **E1 (multi-scale history) / E2 (cond reference noise) / E5 (bed-window jitter) were removed in that commit**,
> and the `exp/seam-frontier` branch was deleted too (its content all merged into `main`).
> **The numbers in this file are "reference ranges", not "decision thresholds"** — the reason is in §4. The experimental layer is all off by default; trunk behaviour is unchanged.

---

## 1. The conclusion first: which quantities have numbers and which don't

| Quantity | Reference value available? | Source |
|---|---|---|
| Frame jump (normalized) | ✅ **threshold 0.030** | `docs/01-mechanism.md` §Seam-frame ghosting |
| Brightness step at the seam | ✅ **0.0007 = below the perceptible threshold** (measured on the cond route) | `docs/02-parameters.md` §Cross-segment statistics matching |
| Reproduction residual (`scan_head_repeat`, 0.5.0's 4th route) | ✅ **5/255, separation from a foreign source >2×** | `CHANGES.md` §0.3.1 |
| **E3 DTW alignment cost** (this experimental layer) | ⚠️ **first given in this file** (§2) | this measurement |
| **E4 drift-curve slope** (this experimental layer) | ⚠️ **first given in this file** (§3) | this measurement |

E3 / E4 previously had **no numbers at all** in `docs/`: E4 was not yet wired into any node, and E3 only printed a line when `settle_frames<0`.

---

## 2. E3 · How to read the DTW alignment cost

**Convention** (`relay_core.head_repeat_dtw`):
- Comparison targets = the "post-seam window" `[pin, pin+36)` **vs** the "pinned region" `[0, pin)`;
- Features = **per-frame pixels** after `_canonicalize` to a fixed `short_side=256` ⇒ **the cost is independent of input resolution**, but **varies with picture content/contrast** ⇒ **do not compare absolute values across material**;
- the output `align_cost` is already normalized by path length, so it is independent of window length.

**Must be converted to a ratio**: `ratio = cost(this segment's window vs the true pinned region) / cost(this segment's window vs a foreign segment's tail)`.
The foreign baseline is taken as **another segment's last frame one segment away in the same run** (here `seg_i` against `seg_{i-2}`). **Absolute values are meaningless; only the ratio is comparable.**

**Measured (a real 4-segment chain, patch=0, 672×1184, the official sampling chain, N=1 chain / 3 seams)**:

| Seam | vs the true pinned region | vs a foreign source | **Ratio** | Reading |
|---|---|---|---|---|
| seg1→seg2 | 0.03518 | 0.28565 | **0.123** | strong reproduction (the new segment's head is almost the previous segment's tail) |
| seg2→seg3 | 0.28068 | 0.23752 | **1.182** | no reproduction (even more expensive than a foreign source) |
| seg3→seg4 | 0.20164 | 0.28160 | **0.716** | borderline / weak residual |

**Reference ranges (prior, to be calibrated)**:

| Ratio | Reading | Suggested action |
|---|---|---|
| **< 0.30** | clear reproduction residual | look at the visual inspection; use `settle_frames` (it introduces frame jumps — careful) or change the words on the generation side |
| 0.30 – 0.80 | borderline | record only, no action |
| **≥ 1.00** | no reproduction | normal |

⚠️ Two known noise sources: ① which segment you pick as the foreign baseline changes the ratio (here seg2's foreign baseline is on the high side); ② if new content is caught inside the `scan=36`-frame window, the cost is pulled up (this is exactly the value of DTW over the single-point method: **it can see "the reproduction region is discontinuous"**).

---

## 3. E4 · How to read the drift-curve slope

**Convention** (`relay_core.segment_appearance_stats` / `drift_curve`):
- for each segment, take the **body region** (from frame 40 by default) triple: `mean` (brightness), `std` (contrast), `sharpness` (high frequency);
- relative to the first segment, produce `mean_shift` / `std_ratio` / `sharp_ratio`, then least-squares against the segment number ⇒ three slopes.
- 🔴 **`slope` (brightness) is an absolute quantity**, affected by how bright/dark the material is, **not directly comparable across films**; what is comparable across films are the **dimensionless** `std_slope` and `sharp_slope`.

**Measured (the same real 4-segment chain)**:

| Segment | mean | std | sharp | mean_shift | std_ratio | sharp_ratio |
|---|---|---|---|---|---|---|
| seg1 | 0.47449 | 0.25593 | 0.00932 | 0 | 1.000 | 1.000 |
| seg2 | 0.30550 | 0.25335 | 0.00557 | −0.16899 | 0.990 | **0.598** |
| seg3 | 0.25169 | 0.23708 | 0.00626 | −0.22280 | 0.926 | 0.672 |
| seg4 | 0.22733 | 0.23465 | 0.00720 | −0.24716 | 0.917 | 0.773 |

`slope = −0.0795`｜`std_slope = −0.0313`｜`sharp_slope = −0.0607`

**Reading**: the mean **darkens monotonically** (dropping 0.247 over 4 segments) and **accumulates**; sharpness drops to its lowest at seg2 (0.598) and then **recovers** (0.672 → 0.773) ⇒ **"drift monotonically worsens with segment count" does not hold on this chain**.

**Reference ranges (prior, to be calibrated)**:

| Dimensionless slope | Reading | Suggested action |
|---|---|---|
| `|std_slope| ≤ 0.05` and `|sharp_slope| ≤ 0.10` | normal | no action (this run's −0.031 / −0.061 fall in this band) |
| either > 0.10 / segment | perceptible drift accumulating per segment | consider enabling the global appearance anchor `ref_anchor_stage=0` or raising the anchor-frame tier |
| either > 0.20 / segment | clear drift | re-anchor, and re-check the prompt's scene / colour-temperature description |

---

## 4. Why these can only be "reference ranges" and not "thresholds"

1. **Insufficient sample size**: this run is N=1 chain / 3 seams / 4 segments, and the only material is the E1 set (high-speed-train carriage dialogue, no BGM). Different subjects, resolutions and sampling chains are all uncovered.
2. **No visual-inspection labels**: this production line's discipline is "whether a cut / a pass is judged only by human visual inspection; the machine check only reports numbers" (the single source of truth for this discipline is the project's own collaboration rules). To turn this into a threshold you need **~20–30 human visual-inspection verdicts** to calibrate.
3. **Absolute quantities are not portable**: E3's absolute cost varies with content, E4's `mean` varies with how bright/dark the material is ⇒ only the **ratio / dimensionless slope** can cross films.

## 5. Self-calibration in three steps (recommended: run it on each machine yourself)

1. Run **your own** 3–4 segment chain once and collect §2's ratios and §3's dimensionless slopes (≥3 seams each).
2. Gather 20–30 visual-inspection verdicts (one "reproduced / not reproduced" and "drift perceptible / not perceptible" tag per seam).
3. Use the tags to set the boundary for the two ratios (logistic regression is enough) — **until then, use this file's ranges only as a "should I take a look" hint, not as an automatic criterion**.
