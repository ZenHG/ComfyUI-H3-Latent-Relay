# Prompt-side discipline: head padding · dialogue-safe timing · last-frame anchor chaining · audio-seam pairing

<!-- EN-SYNC src=docs/06-continuity-scripting.md stamped=2026-10-05 mode=see tools/en_sync.json -->

🌐 English translation of [`docs/06-continuity-scripting.md`](06-continuity-scripting.md). **The Chinese file is the source of
truth** — if the two ever disagree, the Chinese one wins.

> This file was split out of the README (0.6.0 restructuring).
> 📐 **Port notation:** throughout this document `[N]` is **0-based** (the Nth port in the UI = `[N-1]`).
> The "route N" style carried over from older drafts was 1-based and has been converted to `[N]` at the
> places that matter.

---

## 🎬 Dialogue avoidance and audio handling at the seam (prompt-side discipline + audio-seam pairing)

### 0. What to do when a long line must cross the seam (finalized 2026-09-21)

Audio is **generated independently** for each segment — the nodes can guarantee "no swallowed words + consistent
timbre (refs) + timeline alignment", but **cannot guarantee prosody across the seam** (pace/intonation is a
coincidence of two separate samplings). So the answer for a long line that spans segments, in priority order:

| Option | Approach | Continuity | Cost |
|---|---|---|---|
| **① Keep the sentence unbroken ⇒ don't cross the seam (preferred)** | Put the seam **after** the sentence: lengthen that segment on its own (e.g. `length=288` = 12s to say the whole line), so the seam lands on the breath at the sentence end | ★★★ same generation within the segment, prosody naturally continuous | longer segment = higher per-segment cost ↑ |
| **② Must cross the seam ⇒ put the break at a breath** | seg1 says the first half (its sentence end lands in the segment-tail safe zone); seg2 leaves the head blank and says the second half; put the break at **punctuation / a breath**, never mid-word | ★★ depends on prompt-side design; timbre is kept consistent by refs | prosody joining is luck; needs an ear check |
| **③ The whole line falls in the seam region (❌ we don't do this)** | the sentence straddles the seam and the seam sits mid-word | ✗ always produces phantom audio / swallowed words | — |

**Budget table for ②** (dialogue-safe window, computed with `trim=22` frames + the 🛡 guard enabled):

| Segment length | Head trim | Head blank (guard margin) | Tail buffer | **Dialogue-safe window** | ≈ characters |
|---|---|---|---|---|---|
| 4s (96 frames) | 0.92s | ≥1.2s | ≥0.5s | **≈1.4s** | 4–6 chars |
| 8s (192 frames) | 0.92s | ≥1.2s | ≥0.5s | **≈5.4s** | 16–20 chars |
| 12s (288 frames) | 0.92s | ≥1.2s | ≥0.5s | **≈9.4s** | 28–34 chars |

**Switch checklist (go through it item by item for a cross-segment dialogue scene):**

| Switch | Should be | Why |
|---|---|---|
| 🛡 `patch_guard` | **on (default)** | automatically avoids dialogue at this segment's head — it was born for exactly this scene (0.6.5) |
| `patch_seconds` | 2.0 is fine (the guard is the backstop); for the absolute-conservative case ⇒ 0 | the guard shrinks/closes automatically, so words are no longer swallowed |
| `settle_frames` | **0** (default) | automatic settle trims at most 36 extra frames ≈1.5s ⇒ it eats dialogue directly |
| assembly-layer `--across` | **0** (default since 0.6.2) | crossfade steals 0.25s per seam ⇒ dialogue misaligned + swallowed |
| `trim_frames` | **22, do not touch** | a hard requirement of the bridge's timing grid (5+17k); turning it off = continuation does not hold |
| ~~E1/E2/E5~~ | **removed** (2026-09-22) | 🔴 the three experimental arms measured **ineffective against the target**, deleted together in `1c87aff` ⇒ these parameters **no longer exist**. ⚠ a follow-up idea for E1 is still open (the repo author 2026-09-25); the re-entry point is that commit's entry in `CHANGES.md` |

**Acceptance check**: ear-check ±1.2s around the seam — words must be complete, breaths natural; ASR word-level
timestamps can serve as a ruler (`faster_whisper word_timestamps=True`), but **the final call is a human ear**.

### 🎬 Dialogue avoidance and audio handling at the seam (prompt-side discipline + audio-seam pairing)

The node chain cleans up the **picture** seam (pin → head trim → settle), but the seam region has two more
classes of pitfall on the **prompt side** and the **audio side**; the three are one coordinated set — miss one
corner and you get phantom audio / broken sentences:

### 1. Segment-head padding: leave "head trim + margin" at the start of a continuation segment

TrimAV eats the head of the continuation segment = **`trim_frames` (the pinned region, default 22 frames) + `settle_frames` (the settle: default `0` since 0.5.0, no trim; with auto `-1` the detection-path ceilings are 12 frames (hard-jump / colour-step path) / 36 frames (sharpness-collapse path) respectively)**.
**0.5.0 trims only 22 by default**; the worst case (auto settle taking the full 36 frames) ≈ **58 frames ≈ 2.42s** (measured typical value: cond bridge ≈ 38 frames ≈ 1.58s). Dialogue placed here gets **cut in half** (a measured lesson: the dialogue onset was trimmed).

⇒ keep **no dialogue for ≥ head trim + 0.2s** at the start of a continuation segment (with 0.5.0's default of
trimming only the pinned region ⇒ ≥1.1s; with auto settle on ⇒ ≥2.6s); the continuation segment's `[Shot 1]` writes only the picture and camera continuation,
and dialogue starts from a later Shot. **To be safe, just schedule by §2's ≥3.6s, satisfying both constraints at once.**

> ⚠️ **`settle_frames` trims "new content", not repeated content — this is the easiest one to trip over.**
> Trim amount = `trim_frames + settle_frames`, but the two bills are completely different in nature:
> - `trim_frames` = a **reproduction** of the previous segment's tail (repeated picture, already shown in the previous segment) → **trimming it loses nothing**;
> - `settle_frames` = **this segment's newly generated content** → **trimming it does lose something**.
>
> So for a **segment head with dialogue / key action / content that must not be lost**, pick one of two:
> 1. Use the **copy bridge's `mask_mode="hard"`** (the pinned region is copied bit-exact, the model does not redraw
>    → **no reproduction blur** → **no settle trim needed at all**, zero content risk); or
> 2. Set **`settle_frames` to `0`** (trim only the pinned region, safest), at the cost of a little blur possibly remaining at the segment head.
>
> If unsure, set `0`, run one segment, and look at the collapse depth reported in the bridge/TrimAV node's `report` before deciding whether to turn auto on.

### 2. The dialogue-safe moment formula

```text
a continuation segment's prompt moment ≥ actual settle trim + audio-seam patch N (2.0 s recommended) + 0.2 s margin
0.5.0 default (settle=0) ⇒ ≥ 2.2 s; auto settle on (−1, typically ≈24 frames) ⇒ ≥ 3.2 s
in practice always schedule 3.6 s — satisfies both cases at once
```

Origin: the room-tone head patch that 🔗 H3 Relay · Audio Seam adds (see §4) keeps only the part of this segment's audio after N seconds,
so the prompt moment converted to an in-segment moment must land in the kept region. If your prompt check has a pacing rule
like "first cut ≤3s", **decouple the cut point from the dialogue**: schedule the cut at 2.8s and hang the dialogue on a 3.6s Shot.

### 3. Last-frame anchor chain: segment N's last-frame state is copied word-for-word in segment N+1's prompt

The handoff point is chosen **mid camera move / with the action unfinished**; segment N+1's `<Video 1>` (or an
equivalent last-frame reference) copies segment N's last frame word-for-word: who is where, where the hand stops,
how far the camera has pushed in. The cross-segment "one-take" feel is held up by **prompt-side discipline**;
the nodes only guarantee no replay and no jump cut.

### 4. Audio seam: the **node** patches the previous segment's ambience into this segment's head

H3 generates audio independently for each segment; two things stack up at the continuation segment's head:

- **Decode priming**: about 32ms of near-silence at the segment head (measured −72 dB) — TrimAV puts it right on the cut point
  ⇒ in the final film the seam **"twitches" first, then the music starts** (20ms into the seam is 12–13 dB below just before it, heard as "a momentary silence");
- **Generation-restart transient**: 0~1.4s (measured spectrum: 0-1s mid/high frequencies reach 10× the ambience, 20-60Hz reaches 6×;
  the low-frequency tail decays back to ambience level only by ~1.4s).

🔗 **Audio Seam** cures both with one equal-length replacement: it swaps the head `patch_seconds` seconds of this segment for
**the previous segment's quietest-window ambience** (stationary noise, inaudible when spliced), crossfades back to this segment's
own audio at second N, and **does not touch a single sample point after that**.

| Parameter | What it does | When to use |
|---|---|---|
| `patch_seconds` (default 0) | replace the whole N seconds at the later segment's head with the **previous segment's quietest window** (the window is chosen automatically to avoid dialogue tails) | 2.0 recommended (covers the 1.4s transient tail) |
| `tile_seconds` (default 0) | take a W-second tile of the bed source and **loop it with self-crossfades** to fill N | when the previous segment's clean ambience window is shorter than N (dense dialogue), use 1.2 |
| `fade_seconds` (default 0.25) | crossfade width at the patch boundary | 0 = hard cut (an audible join) |

**Why it must be done in the node, not the assembly layer** (this is the 0.5.0 route correction):

1. **The effect must land in the segment file.** The assembly layer's patch is a "second step after rendering" — what you play in the UI, inspect segment by segment,
   and see in the intermediate deliverable all still have the un-patched audio; the node version comes out of the segment file already carrying the ambience.
2. **The assembly layer's crossfade shifts A/V.** ffmpeg `acrossfade`'s semantics are to **trim X seconds off the later segment's head**
   (the crossfade region consumes the later segment's content) ⇒ each seam makes the **audio after the seam lead the picture by X seconds**.
   Measured (2 segments, X=0.25): the music peak moves from 8.75s to 8.50s while the total duration stays the same (the tail is padded flat) —
   a 4-segment chain is a cumulative 0.75s of A/V relative shift. **The node's patch is an equal-length replacement, zero shift.**

> ⚠️ A true crossfade (dissolving two segments) **cannot achieve "length conservation within a single segment"**: it has to trim the **previous**
> segment's tail, whereas a single-segment node can only change this segment. So a true crossfade still belongs to the assembly layer (when needed); the equal-length route uses this node.

Band-profile criterion (A/B measured by the same algorithm in the assembly layer, same convention): before the patch the seam region's 60-250Hz reaches ambience
**4×** (audible "phantom audio with no reason"); after the patch ≈**1.0×** (level with the whole film's ambience median).
Broadband convention: **after the patch the quietest point near the seam no longer lands on the seam** (old recipe Δ = −26.4 dB → after the patch Δ = −1.1 dB).

**Segment-head discipline is still a precondition**: the patch replaces these N seconds with ambience, so the continuation segment's
**first 1.2s should have no dialogue** (see the "head padding" formula above).

### 5. Long films: no mechanical cap on segment count

The stage chain (write / read back) self-continues by stage number (this segment's number − 1 = the number to read); the node cap is 9999 segments;
at 192 frames (8s) per segment you can extend the film arbitrarily.
**Drift accumulates** — colour step / character / sharpness decay with the number of handoffs (measured on 2 segments: seam brightness step 0.0004, visually "almost perfect"; ≥3 segments not systematically measured) — since 0.5.0 there is an **appearance anchor** to manage it:
fill the composite bridge (0.6.0's only bridge) with `ref_anchor_stage=0` (or wire `ref_anchor_latent`) and every segment takes segment 1 as its identity baseline,
the anchor not dropping out as the chain continues (see the two tables under "Parameters"). For ≥3 segments we still recommend a machine check per seam:

- video-seam brightness step ≤0.008 (0-1 convention; >0.03 is hard-cut level — for the seamless convention only;
  a normal cut in a shot-based drama is already in this band);
- audio-seam region 60-250Hz ≤ 1.5× the whole film's ambience median (>2.5 is FAIL; the transient-bump case measured 2.7~4×);
- the assembly's four assertions: frame-count conservation / no PTS holes / monotonic DTS / A·VΔ.

> The production-line one-click chain and the per-seam automatic acceptance scripts (`chain_auto.sh` / `h3_chain_accept.py`)
> live in the author's production-line repo, not in this pack; this pack is only responsible for making **each segment's seam clean on the generation side**.
