# Audio seam and multi-segment concatenation (including off-canvas usage)

<!-- EN-SYNC src=docs/10-audio-seam-and-concat.md stamped=2026-10-05 mode=see tools/en_sync.json -->

🌐 English translation of [`docs/10-audio-seam-and-concat.md`](10-audio-seam-and-concat.md). **The Chinese file is the source of
truth** — if the two ever disagree, the Chinese one wins.

> The main README keeps only "how to use it"; this file is **the full detail of the audio domain**: seam conventions and tuning, dialogue protection, multi-segment concatenation, sidecars, and scripted usage without the canvas.
> 📐 **Section numbers keep the original README §7 numbering (7.1 / 7.2 / 7.3 / 7.3.1 / 7.4)** — other docs, code tooltips and session handovers in this repo still reference these numbers, and changing them would break all of them.
> 📐 **Slot notation**: `[N]` = 0-based.

---

The audio seam **must be done inside the node**: patching at the assembly layer would introduce an A/V offset caused by AAC priming,
whereas this node is **length-conserving** — not one audio sample is added or removed ⇒ zero offset.

- Segment 1 must be patched too (it writes segment 1's audio to disk, and it becomes segment 2's bed source); run segments in stage order, don't skip any.
- Don't patch it, or `patch_seconds=0` ⇒ audio passes through bit-exact.
- **Output wiring**: `AudioSeam [0] audio` → the write-to-disk node's `audio` (`CreateVideo.audio`). **Never wire `VAEDecodeAudio` straight into the write node** — that is the untrimmed raw audio and will desync A/V (see the red warning in the main README "how to use it").
- ⚠️ `patch_seconds > 0` replaces **the first `patch_seconds` seconds of this segment's head with the previous segment's ambience**. If this segment's head originally had dialogue, those N seconds of dialogue get replaced ⇒ leaving the segment head blank is a hard discipline (see [`docs/06`](06-continuity-scripting_EN.md)). The node automatically **avoids the voiced regions in the bed source**, and reports `🎙 窗内有声帧占比` in the log.
- 🛡 **patch dialogue guard (on by default)**: patch would eat **this segment's own** dialogue falling in the head ⇒ the node automatically detects the dialogue onset in this segment's head and
  **shrinks patch to 0.40 s before the dialogue**; if the dialogue is too early (usable window < 0.10 s) patch is turned off entirely and the head is kept as-is.
  The report explicitly prints the verdict and the values before/after shrinking. `patch_guard=0` restores the old behavior.
  ⚠️ **The guard only works on material with a silent pad at the segment head** — material whose whole length is filled with speech/music is **ineffective by principle**.
  **Don't stake dialogue safety on it: the main defense is `patch_seconds ≤ 1.2`** (see **§7.3.1**).

The paired prompt-side disciplines (segment-head buffer, dialogue-safe moments, last-frame anchor chain) are in
[`docs/06-continuity-scripting.md`](06-continuity-scripting_EN.md).

## 7.1 Multi-segment concatenation: how to join N mp4 files into one

**Remember one contract first**: `TrimAV` trims **video and audio together**, and `Audio Seam` is **length-conserving**
⇒ **each segment file's audio length = its video length**, with **no overlap** between segments.
So the correct way to join is **a plain head-to-tail splice**.

> ⚠️ **【auto-run ≠ a finished film】** Running the chain gives you N mp4 files. Since 0.6.7 `H3RelayChain` can splice them for you (see below),
> but **you must click it** (enable `auto_concat` or press 🧩) — it won't silently do it for you.

**① Preferred: one-click in the pack (0.6.7)**

On the Chain node: enable `auto_concat` ⇒ auto-splice when the run finishes; or press **🧩 拼成一条** at any time.
The picture is **stream-copied (lossless)**, audio is de-primed and aligned per segment, and right after splicing it self-checks four items
(frame-count conservation / no PTS holes / DTS increasing / A·VΔ ≤ 1 frame); measured, a 4-segment 29 s film comes out in **1.3 seconds**.
If the self-check fails it keeps the film for evidence and states in the status that it **must not be used as a final product**.

**①.5 Audio track tier: default AAC 256k, plus a lossless master (0.6.7)**

The three `audio_out` tiers on Chain (they only affect **the film's audio track**; the picture is always stream-copied):

| Tier | Container/codec | Measured SNR (vs lossless source, real H3 segments) | Notes |
|---|---|---|---|
| `aac_256k` (default) | mp4 + AAC 256k | **40.5 ~ 50.0 dB** | Best compatibility, plays in browsers |
| `aac_192k` | mp4 + AAC 192k | 38.4 ~ 44.6 dB | Saves about 25% size |
| `pcm_lossless` | mp4 + PCM f32 | **bit-exact (∞)** | Master tier; **no sound in browser preview**, file ≈4.4 MB/s |

**Why the default tier already drops from "two generations" to "one"**: a segment file's audio track is the AAC the user encoded at write time (generation 1).
Splicing from mp4 by decoding and re-encoding = generation 2. Since 0.6.7, "Trim AV" also saves this segment's audio as a **lossless PCM sidecar**
(`save_pcm`, on by default); the film reads the sidecar directly ⇒ **no AAC decode, no priming touched** ⇒ audio is encoded only once;
with `pcm_lossless` **not even one generation is added** (the film's audio track is bit-identical to the sidecar, measured max|Δ| = 0).

> Sidecar missing (old graph, `save_pcm` turned off, or that submission didn't run "Trim AV") ⇒ that segment automatically falls back to mp4 decoding,
> and the splice report **states the audio source per segment** (`PCM` / `AAC`). Cost: that segment is still "generation 2".
>
> **⚠ What if there are other audio nodes after "Trim AV" on the audio chain** (typically = this pack's **Audio Seam**): what actually goes into the mp4 is
> the output of **the most downstream one**. So:
> ① "Audio Seam" also writes its own sidecar (the very audio it sends to the write node; see the node table in the main README "core features");
> ② splicing **reads the submission graph's data flow directly** to rank candidates: whoever is downstream wins — **it follows whatever the wiring is**
>   (no more guessing order by node type; the 2026-09-30 incident was exactly "guessed order" being opposite to the graph's wiring ⇒ wrong sidecar ⇒ audio/video mismatched segments);
> ③ when tied (several are co-sourced), take **the higher-priority one**, and write the losers into the report; when **the submission graph can't be read**,
>   **reject all sidecars and fall back to decoding** (better one extra AAC generation than a mismatched segment);
> ④ if none can be found co-sourced (e.g. a J-cut makes the audio seam output 0.9 s shorter) ⇒ **reject the sidecar, fall back to decoding** and say so in the report.
>
> Sidecar write failure (about 2 MB/segment) **does not affect this segment's render**; it only prints a note in the log.

**Parameter: `video_crf` (default 16)** takes effect only when the picture **must be re-encoded** (segments have inconsistent specs, or the stream-copy path fails an assertion);
the default stream-copy path is lossless, so this cell isn't used. ⚠ `crf 0` **is not lossless** (RGB→YUV 4:2:0 loses first; ceiling 46.5 dB).

**② If you don't want this step to happen inside ComfyUI**: use a single-pass re-encode with the concat filter —
list the N inputs in order (`list.txt` holds absolute paths in stage order, each line `file '/abs/path/s0.mp4'`):

```bash
# self-check: the frame count should equal the sum of the stages' frames, and the A/V duration gap ≤ 1 frame
ffprobe -v error -select_streams v:0 -count_frames -show_entries stream=nb_read_frames -of csv=p=0 out.mp4
ffprobe -v error -select_streams a:0 -show_entries stream=duration -of csv=p=0 out.mp4

# concat (cleanest timeline; list the N inputs in order)
ffmpeg -y -i s0.mp4 -i s1.mp4 -i s2.mp4 -i s3.mp4 \
  -filter_complex "[0:v][0:a][1:v][1:a][2:v][2:a][3:v][3:a]concat=n=4:v=1:a=1[v][a]" \
  -map "[v]" -map "[a]" -c:v libx264 -crf 16 -pix_fmt yuv420p -c:a aac -b:a 192k out.mp4
```

| Don't splice like this | Symptom (measured 2026-09-22) | Why |
|---|---|---|
| `ffmpeg -f concat -c copy` (easiest) | 4 segments: video duration **29.346 s** (should be 29.250, inflated by 96 ms), audio landing +64 / −25 / −33 ms per segment (NCC drops to 0.2) | segment files' audio stream is longer than video (AAC encoder priming, +0.032 s per segment); stream copy can't calibrate at sample level |
| `acrossfade` (looks most professional) | **steals 0.25s per seam** ⇒ from segment 3 on, audio/video misalign, **accumulating per segment** | crossfade is overlap-add, but the video has **no** matching overlap to trim |

> After splicing **you must self-check** (the two ffprobe lines above): frame-count conservation + A/V duration diff ≤ 1 frame. If it fails, switch to ① or change material.

## 7.2 Bed-window speech avoidance: what the criteria can and cannot catch (read this first)

When `patch_seconds > 0` the node **automatically avoids the voiced regions in the bed source**. The criterion is **energy-based**
(a frame counts as "voiced" when its RMS exceeds **3× the whole-source median**; a window is a hit when its voiced-frame ratio exceeds the threshold; the tiled tier uses threshold 0),
**not speech recognition** — what it can and cannot catch, measured, is below; **match it against your own material**:

| Material type | Criterion behavior (measured) |
|---|---|
| Steady noise floor + dialogue (rain/room ambience + dialogue, most common) | ✅ reliably caught (dialogue window ratio 58% ⇒ switched to a 0% window) |
| Very short speech (~0.1s interjection/gasp) | ✅ caught (ratio 12.5%, borderline) |
| Single-frame transient (door close/bang) | ✅ no false positive (ratio 4% < 10% threshold, won't needlessly switch windows) |
| **low-level speech** (only ~6 dB above the ambience: far-field dialogue, breathy voice) | ❌ **missed** (can't reach the 3× threshold) |
| **speech as the noise floor** / BGM-type (podcast, continuous narration, noisy bar) | ❌ **missed** (speech raises the whole-source median ⇒ the threshold self-defeats) |

**Backstops** (from cheapest to most expensive):

1. **Check the log first**: every run with `patch_seconds>0` prints `🎙 取样窗内有声帧占比 X%（判据阈值 Y%）`;
   on a hit it prints an explicit `⚠` and gives the positions before/after switching — **every verdict is auditable, nothing happens silently**.
2. **When unsure, turn it off**: `patch_seconds=0` ⇒ the patch branch isn't entered at all, audio passes through bit-exact.
3. **Drop one complexity tier**: `tile_seconds=0` ⇒ single-window tier (10% threshold + speech avoidance), more conservative than the tiled tier.
4. **Fix it on the material side**: keep dialogue away from the bed source's tail (`bed_select=tail` takes the previous segment's last window by default) —
   segment-head/tail blank discipline is in [`docs/06`](06-continuity-scripting_EN.md).
5. **When the whole source is voiced**: the node **does not error**; it automatically falls back to the **quietest window** (least speech residue) + a log ⚠ —
   it won't kill legitimate material that "uses speech as its noise floor".
6. **The final judgment is the human ear**: the machine only points at the position (the `patch_seconds` seconds after the seam); decide after listening.

> The criterion already has a built-in **persistent-frame filter** (a voiced onset requires ≥2 consecutive frames = 100ms ⇒ a single-frame transient doesn't trigger a window switch;
> the measured ratio for continuous dialogue is unharmed). Still **not implemented**: ASR semantic review (would rescue the BGM-type misses in the table above; registered in `CONTRIBUTING`).

---

## 7.3 Tuning guide: recommended value and rationale for every knob (finalized by measurement in 0.6.5)

> Principle: **the default value is the recommended value**. The table below answers "when exactly should I touch it" — every entry gives
> measured evidence; entries without evidence are explicitly marked "don't touch".

| Knob | Recommended | When to touch it, and why (all measured) |
|---|---|---|
| `patch_seconds` | **≤ 1.2** (aligned with the prompt-side segment-head blank discipline); **0** = off | `patch` only covers the first **10 ms** of AAC priming silence at the segment head (measured on 11 real films: 0–10 ms level −50~−122 dBFS, while the 0–1 s average is 39~80 dB higher) ⇒ **`patch=1.2` is already fully sufficient**. Why 2.0 is no longer recommended: material with dialogue at the head splits into two classes — **class A** (dialogue after 1.4 s) is not touched at all by `patch≤1.2`; **class B** (dialogue from 0.1 s, the whole segment filled with speech) **loses words under any `patch>0.1`, and the guard is ineffective by principle**. ⚠️ **The old note here, "dialogue placed up front is still safe at 2.0", has been overturned**. See **§7.3.1**. |
| `tile_seconds` | **0** when the bed source's tail is clean (single-window direct take); **1.2** when the bed source's tail has dialogue/BGM phrases | Tiling takes material from an earlier clean region of the bed source. The bed-window criterion reliably catches "BGM + dialogue" bed sources (dialogue +14 dB ⇒ window ratio 66.7% ⇒ switch window). In the S2 scenario (BGM always present), measured generation-side BGM is naturally continuous, so patch's value drops and tile mainly protects dialogue. |
| `fade_seconds` | **0.25, don't touch** | 0 = hard cut, an audible click at the seam; a production-line measured value. |
| `bed_select` | **tail, don't touch** | `quiet` (the 0.5.0 old behavior) measurably replaces the patch with quieter content (−42 vs −12.8 dBFS) ⇒ the seam goes from a "dip" to a "silent hole", flattening the attack. For controlled reproduction only. |
| `bed_stage` | **0** (segment 1) | Same-scene ambience is stationary; segment 1 is steadiest; only consider changing when the previous segment is a special sound field (explosion→quiet room). ⚠ must be < this segment's stage number. |
| `patch_guard` | **on (default)** — but **only works on material with a silent pad at the head** | ⚠ **The boundary has been lowered** (measured 2026-09-26): the guard works on **class A** (truly quiet head + dialogue later), shrinking/off automatically; on **class B** (whole segment filled with speech/music ⇒ median raised ⇒ threshold above the speech peak) it **never triggers, ineffective by principle**. **Don't stake dialogue safety on it** — the main defense is `patch_seconds ≤ 1.2` (see §7.3.1). |
| `patch_guard_layers` | **0** (= existing behavior, changes no output) | 🛡 guard criterion tiers (0/1/2/3 cumulative): 1=+energy-step gate, 2=+voiced-frame spectral-centroid gate, 3=+spectral-flatness gate. ⚠️ **insufficient calibration basis** (thresholds drift with analysis-window length, small sample) ⇒ **default 0**; if you want to use it, audition it on your own material first. |
| ~~`exp_bed_jitter`~~ | — | 🔴 **This parameter was removed on 2026-09-22** (`1c87aff`: the experimental tier measurably did nothing to the target, so it was deleted) ⇒ **it is not a settable parameter**; if set, the host **silently ignores it** (extra input keys don't error and don't warn). See [`CHANGES.md`](../CHANGES.md) for the history. |
| `settle_frames` (TrimAV) | **0** | −1 auto fixes blur but **introduces frame drops**. Most of the "blur" is whole-chain detail loss, not the segment-head settle region (decided by a 2026-09-19 sampling-chain A/B). |
| `declick_ratio` | **4.0**; **0 = off (bit-exact pass-through)** | Fixes "an isolated transient out of nowhere in a quiet background" (the repo author ear-checked it as an **artifact**). It only acts when "background < `declick_quiet_dbfs`, and **before the dialogue onset**", and only fixes isolated peaks of `≤ declick_max_len_ms` ⇒ most material's report prints "no event hit ⇒ bit-exact pass-through". The action = **replace** that little stretch (§7.3.2). |
| `declick_quiet_dbfs` | **−50, don't touch** | "The background must be below this level before it acts". Raising it (e.g. −40) starts touching soft consonants; lowering it (e.g. −60) is more conservative. |
| `declick_max_len_ms` | **50** | "How wide counts as one sound" (ms). **The false-positive / miss trade-off knob**: `10` strict (measured event width 8 ms ⇒ still fixed), `50` broad default, `100+` aggressive. The old version hard-coded 2 ms ⇒ **missed the 8 ms isolated transient** (fixed). |
| `cut_head_frames` | **0** ⇒ set to the bridge's `context_frames` | 🔴 leave it unset ⇒ **the "isolated peak cut out by the trim" at the segment head can't be fixed** (this node works on untrimmed audio; the product is trimmed). Usually filled automatically by the batch script. |

**Criterion boundary**: the bed-window criterion and the dialogue guard are both **energy-based** (frame RMS vs whole-source median),
not speech recognition — **material where speech occupies >50% of frames** (podcast/continuous narration) is missed by both criteria,
and patch is meaningless on such material anyway; low-level speech (only ~6 dB above ambience) may also be missed.
Every verdict is printed in the log, auditable; **the final judgment is the human ear** (the four-piece set ① original speed, original film).

---

## 7.3.1 🔴 Dialogue protection: **shortening patch is the main defense; the guard is only a backstop**

> This section is the conclusion of a whole round of zero-GPU measurement on 2026-09-26. **Read it first, then decide what to put in `patch_seconds`.**

### Measured: what patch actually covers

`patch` is designed to cover the segment head's **AAC decoder priming silence + generation transient**.
On 11 real films measured, **every one's first 10 ms at the head is real silence** (−50 ~ −122 dBFS),
while the same head's 0–1.0 s average level is **39 ~ 80 dB** higher.

⇒ **The silence is only in the first 10 ms.** As long as `patch ≥ 0.1 s`, the silence is definitely covered;
shrinking `patch` from 1.0 s to 0.5 s **does not** re-expose the decoder silence —
the only cost is "the segment head's 0.5–1.0 s keeps this segment's own audio".

### Dialogue onset distribution (decides "how short is safe")

On real material with "dialogue at the head", the onsets split into **two classes**:

| Class | Dialogue onset | Head 1.2 s level | Does shortening patch help |
|---|---|---|---|
| **A quiet-head** | **1.4 ~ 1.6 s** | −44 ~ −55 dB | ✅ **fully effective** |
| **B dialogue-at-head** | **0.10 s** | **−3 ~ −4 dB** (near full scale) | ❌ **no solution** |

**Class A**: dialogue after 1.4 s ⇒ **`patch ≤ 1.2 s` doesn't touch the dialogue at all**.
**Class B**: dialogue starts from 0.1 s, and **the whole segment is filled with speech/music** (head level −3~−4 dBFS, near full scale)
⇒ any patch > 0.1 s covers the dialogue, and **no audio criterion can save it** (see below).

### The guard can't save class B — this is by principle

Class B material is characterized by **speech throughout** (speech is itself the noise floor). The guard's threshold is
`2 × whole-source median`, and the whole-source median **is the speech** ⇒ the threshold is above the speech peak ⇒ **zero frames exceed it ⇒ never triggers** (measured and confirmed).

⚠️ **The docs at 0.6.9 and earlier wrote here that "scene dialogue placed up front is still safe at `patch=2.0`" — overturned, don't configure by it.**
That sentence holds for class A, but for class B **it eats words**.

### Criteria exhaustively falsified by measurement (15 classes, don't step on them again)

| Domain | Criterion | Result |
|---|---|---|
| Energy | `rms` mean/percentile/peak, run-length, `step` | ❌ speech and piano/BGM alias at the same magnitude |
| Energy | lowered-baseline percentile (P05/P10 instead of median), lowered `k` | ❌ the BGM valley is **higher** than on speech material ⇒ guaranteed to blow up |
| Energy | in-window median − whole-segment median (relative) | ❌ fully overlapping (speech sample +3.8 dB vs BGM sample +3.9 dB) |
| Spectral shape | `spec_centroid` / `cent_act` (voiced-frame centroid) | ⚠️ only blocks high-frequency impacts (clinking/door), not steady musical tones |
| Harmonic/noise | `hnr` / autocorrelation / spectral flatness `flat_act` | ⚠️ only blocks steady musical tones, and thresholds drift with window length |
| Temporal structure | `zcr` / `gap_ratio` / `n_seg` / intermittency | ❌ overlaps with instruments |
| Modulation | 2–8 Hz syllable rate, spectral flux | ❌ no separation observed |
| External | `webrtcvad` (WebRTC VAD) | ❌ 0.375 acc, essentially a GMM-energy VAD, same origin as the energy domain |
| Cross-domain combo | `step` + `cent_act` (+`flat_act`) | ⚠️ looks effective only at **long-window calibration**; **after shrinking to the production window length TP drops to 0~3/5** ⇒ not credible |

**Root cause**: steady speech and steady music are **fundamentally inseparable on any 1-second-window statistic**.
This isn't the algorithm being bad, it's information being insufficient — separating them requires time-frequency structure (formant movement / voicing alternation),
which amounts to rebuilding a VAD, and a VAD measurably performs **worse**.

### ✅ Conclusion: advice for open-source users (ordered by reliability)

| # | Approach | Reliability | Cost |
|---|---|---|---|
| 1 | **`patch_seconds ≤ 1.2`** (aligned with the prompt-side "segment-head blank" discipline) | ✅ **most reliable** — class A fully immune, class B shouldn't occur | segment head 0.1–1.2 s keeps this segment's own audio (**not** decoder silence; the silence is within 10 ms) |
| 2 | **scene dialogue up front (speaks within 1.5 s of the head) ⇒ `patch_seconds=0`** | ✅ zero-risk switch, the patch branch isn't entered at all, audio passes through bit-exact | the seam transient isn't covered |
| 3 | `patch_guard=on` (default) + `patch_guard_layers=2` | ⚠️ **only effective for class A**; **ineffective** for class B (by principle) | none |
| 4 | **prompt-side blank discipline** (`docs/06`): don't write dialogue within 1.2 s of the segment head | ✅ **root fix** — class B material is itself the product of this discipline failing | one extra sentence when prompting |

> **One sentence**: `patch`'s value (covering 10 ms of silence) and dialogue protection **don't conflict** —
> `patch=1.2` simultaneously "covers the silence" and "doesn't touch dialogue after 1.4 s".
> **The only combination that really eats words is `patch > 1.5` with dialogue in the first 1.5 s**,
> and it is either solved by "shrinking patch" (class A), or **shouldn't occur at all** (class B, prompt discipline).

---

## 7.3.2 Isolated-transient suppression (declick): fixing "a single isolated transient in a quiet background"

> This section corresponds to the same-named entry in `CHANGES` 0.6.21. The three parameters are in the knob table in **§7.3**.

### What that isolated transient is

It's not a sound effect the model generated out of thin air, nor a seam/pin boundary artifact — **every arm has it** (no-pin / A / P5
all measurably have an isolated peak at the same position). Its audibility comes from **contrast**: the event peak is −40 dBFS, while the background around it
is only −60 dBFS ⇒ SNR ~20 dB ⇒ it stands out in a quiet passage as "a sound out of nowhere".

### Criterion: judge "how quiet it is around it", not "how loud it is"

| Version | Criterion | Result |
|---|---|---|
| First version | set an absolute ceiling (floor) on the **event** | 🔴 doesn't hold: soft speech consonants (the aspiration of p/t/k) can be lighter than the "isolated transient", the two overlap in magnitude ⇒ either miss the isolated transient or clip dialogue |
| First version | background uses the **median of a 5 ms window** | 🔴 the window has only five 1 ms samples and the "isolated transient" itself occupies 3 ⇒ **the background is raised by the event itself** (0.0034 vs true noise floor 0.0015) ⇒ ratio only 2.84, **not one caught** |
| **Current** | background = **P20 low percentile of a 50 ms window**; candidate = `peak > ratio × background` **and** `background < effective gate` | ✅ hits the isolated transient where the background is −56 dBFS; doesn't hit consonants where the background ≈ −35 dBFS |
| **Current** (added 2026-10-04) | 🔵 **effective gate = `max(quiet_dbfs, whole-segment background × 4)`** — take the **broader** one | ✅ **decoupled from the material's absolute level**: on material with a −45 dBFS noise floor, an isolated transient 5.6× above it is still fixed (under the old absolute gate it **never hit**, failing silently for the whole segment). See "cross-material compatibility" below |

### What to do: **replace** that little stretch (neither attenuate nor zero it)

| Action | Result |
|---|---|
| Attenuate to **0** | 🔴 **digs a silent hole** in the background ⇒ the hole edge = a new transient (ear-check rejected) |
| **Scale to the neighborhood background** (P50) | 🔴 for a 1~2 ms spike it **doesn't reach zero** (residual ≈10× background) ⇒ still audible |
| **Fill the whole stretch with a linear transition between the left/right neighborhood waveforms** (`_declick_fill`) | ✅ the pulse **doesn't exist** in the waveform; the neighborhood already passed the `quiet_dbfs` gate ⇒ what's filled in has the background's texture |

⚠️ Two hard constraints (each pinned by a regression):
- The fill neighborhood **must avoid other events** (otherwise it moves a neighbor's spike in) — regression `tests/test_relay_core.py` 30.28.
- 🔴 **The neighborhood must be an order of magnitude below the event**: the criterion is `neighborhood peak × 2 > event peak ⇒ skip` (**not** a plain
  `neighborhood > event`). What gets skipped is **real content's edge / tail-off** (speech, sound effects, **volume cliffs**) ⇒
  not a single sample is touched, and it's named in the report.
  · **Why it must be a "multiple" and not an "ordering"**: on material with "sustained noise + volume cliff" (music tail-off,
    door sound, level jumps at scene changes), the two sides of the cliff are **the same magnitude** ⇒ which is bigger depends only on the random samples of those two windows ⇒
    the old form **about 50% of the time** fills the cliff as if it were an isolated transient (measured 0.4997 → 0.0489, and that wasn't an artifact).
  · A real artifact's neighborhood must be an order of magnitude lower (measured real sample 5.7×; the neighborhood ratio at fixed spots 20×+) ⇒ 2× is the conservative line.
  · The consequence of not having this gate (2026-10-04 end-to-end GPU measurement): when `declick_gate` can't get the dialogue onset it falls back to the production discipline's
    upper bound, and that range **contains the pin region** (a copy of the previous segment's speech) ⇒ the event there is a speech tail-off, and fixing it makes it **+5.4 dB louder than the event itself**
    = it **moves** the previous segment's speech to this spot.
  Regression **30.31 / 30.31b / 30.32**.

### "How wide counts as one sound" = `declick_max_len_ms` (user-tunable, default 50 ms)

🔴 The old version **hard-coded 2 ms** ⇒ it judged a measured-**8 ms** isolated transient as "not one sound" and **discarded it outright** (the main cause of misses).
Width is **not** a valid discriminating criterion (on real material candidates get louder as they get wider; trying to separate isolated transients / sound effects by it will fail),
but it also can't be set shorter than "one sound" ⇒ it's the **false-positive / miss trade-off knob**: `10` strict, `50` broad (default),
`100+` aggressive (prone to clipping content). The real gatekeepers are `ratio` and `quiet_dbfs`, two **relative / physical** quantities.

### Two "range/boundary" gates (both have been tripped over)

| Gate | Role | Trip-ups |
|---|---|---|
| **range gate** = **whole segment** (since 2026-10-04; before that "before the dialogue onset, and if the onset can't be found ⇒ trim-frame boundary + 1.2 s") | limits the time span it acts on | ① the old policy's **premise** "the isolated transient is always **before the dialogue**" was **falsified** by end-to-end measurement: of 17 isolated peaks in a 10 s piece of material, **15 fell outside 1.408 s and none were fixed**, while the report only said "range = first 1.408 s" ⇒ everything looked normal (**silent misses**) ⇒ changed to whole-segment, with the gatekeeping handed to the "neighborhood an order of magnitude lower" gate above; ② an even earlier version had degraded to "no range limit + **no** neighborhood gate" ⇒ the dialogue region was clipped by 26% (that was measured **without** the neighborhood gate, no longer applicable) |
| **trim-frame boundary** `cut_head_frames` | this node works on **untrimmed** audio and its product is trimmed ⇒ tell it where the boundary is, then it judges the "isolated peak cut out by the trim" as a **new file head**, and does a 2 ms fade-in at the boundary | leaving it unset ⇒ at the segment head that peak looks like "there's still pin speech in front" to the node ⇒ the background isn't quiet ⇒ **no hit** |

> 🔴 **Shared lesson**: any "range/boundary" gate must first confirm **which coordinate system** it works in.
> In-node coordinate = product coordinate + `context_frames / fps` (measured, at pin=5 the difference is 0.208 s, matching exactly).

### Cross-material compatibility

Two **absolute quantities** in the criterion once failed silently on unfamiliar material; both are now relative:

| Old (absolute) | Failure symptom | Current (relative) |
|---|---|---|
| `background < quiet_dbfs` (−50 dBFS) | when the material's noise floor is −45 it **fails silently for the whole segment** — the report only says "no hit", and the user can never reconcile it against the parameters | `background < max(quiet_dbfs, whole-segment background × 4)` |
| `neighborhood peak > event peak` | on a volume cliff, **about 50% of the time** it mis-fixes | `neighborhood peak × 2 > event peak ⇒ skip` |

The compatibility surface already measured by zero-GPU probes: sample rate **8k–96k** · channels **1 / 2 / 6** · duration **0.05–30 s** ·
noise floor **−80…−30 dBFS** · event width **0.5–200 ms** · all-zero / DC offset / clipping / extremely low level (1e-7)
— all **shape unchanged, no NaN/Inf, no exception**.
**385 real artifacts** batch-run: **zero false positives** (not a single spot was made louder than before).

### Order relative to `patch_seconds`

**declick runs after patch.** patch is "replace the head's N seconds wholesale" ⇒ if declick ran first,
its head treatment would be covered wholesale by patch (while the report looks like it was fixed).

### What it guarantees (machine-checkable)

length conservation · **bit-exact outside the range gate** · the three shapes `[T]`/`[1,T]`/`[1,1,T]` and dtype fidelity ·
`ratio=0` bit-exact pass-through · all-zero ⇒ no event · `limit_n=0` **must not fail silently**
(`tests/test_relay_core.py` group 30, 30.1–30.32). ⚠️ the range gate's **two policies each have assertions**:
head fallback path 30.18–30.21, default whole-segment 30.21b–c — the fallback path **must keep assertions guarding it**,
otherwise if the fallback breaks nobody knows (its whole point is "one-click retreat when things go wrong").

### Known limitations

- 🔴 **Whether and where the "isolated transient" appears is a**generation-side** matter** — this node only fixes "the one that was already generated".
  After changing material/seed it may move or not appear ⇒ **check the report every run** (it prints a line even on a miss).
- ⚠️ `cut_head_frames` needs to be **consistent with the bridge's `context_frames`**; when hand-wiring on the canvas it's hand-filled
  (the batch script usually fills it automatically). Fill it wrong ⇒ the fixed position is off by `Δframes/fps` seconds.

### 🔴 A/B for judging declick: compare PCM, not mp4 (established 2026-10-04)

Within one host process, three arms (baseline twice + declick on) measured: the three arms' `latent` is **bit-identical** ⇒ the product difference comes purely from declick.
But **the same comparison** measured on two carriers gives opposite conclusions:

| Carrier | Conclusion |
|---|---|
| **lossless on-disk PCM** (`relay_kit/<run>/audio_NNNNN.safetensors`) | 601 / 324000 samples changed (0.19%), only the 3 spots in the report; the dialogue region **max\|Δ\| = 0** |
| **mp4-decoded waveform** | differences everywhere (max 0.03, 3761 samples) ⇒ **AAC is a stateful encoder: change the input ⇒ the whole thing re-encodes**, not a false positive |

⇒ **for audio A/B always compare lossless on-disk PCM** (or latent); mp4 is only for **listening** and overall metrics.
Paired discipline: **all arms must run consecutively within the same host process** — across processes (restart/reload) the measured `latent` rel≈0.95~1.4,
and such data is void.

---

## 7.4 API / script users: how to use it without the canvas (⚠ read "which features are UI-only" first)

Half of this pack's new capabilities are **implemented in front-end JS** (canvas buttons). If you submit JSON via `/prompt`, read this table first:

| Capability | UI users | API / script users |
|---|---|---|
| Continuation itself (bridge / TrimAV / post-processing / audio seam) | ✅ wire it up | ✅ **works just the same** (pure nodes) |
| Audio PCM sidecar (`save_pcm`, audio generation 2→1) | ✅ on by default | ✅ **works just the same** (node writes to disk + echoes the path into history) |
| **Word distribution (segment k gets word block k)** | ✅ fill `prompts` + wire `Chain.prompt` to the prompt node | ✅ **works just the same** (since 0.6.15 it's a **node capability**: Chain outputs block k by `stage_index`) — just change `stage_index` in the loop, see below |
| **The chain-run (⏩) loop itself** | ✅ press the button | ⚠ **the script loops itself** (in a single execution the node only knows "which segment this is now" and can't submit the next prompt itself) — template below |
| **Joining N segments into one film** | ✅ 🧩 button / `auto_concat` | ✅ **three non-UI paths** (see below), same core code as the button |

> ⚠️ `status` / `prompt_target` / `auto_concat` / `concat_name` / `audio_out` / `video_crf`
> these cells **only work on the canvas** (`status` is for display, the rest are read by the front-end and sent to the back-end).
> When a script submits JSON they are ignored (no error), **don't rely on them**.
>
> ✅ **`prompts` and `stage_index` are exceptions** (since 0.6.15): they are **real inputs** of `H3RelayChain`,
> and **take effect as usual** when a script submits JSON — the node takes block k from `prompts` by `stage_index`,
> and hands it out through the `prompt` output. **How words are split and what happens out of range is the same implementation as the canvas** (`relay_core`).

**Script-side loop template** (what "chain-run" looks like in a script):

```python
# The only difference from the canvas is who advances the stage number: the canvas uses buttons, a script uses this loop.
# Prompt selection is not your job — the Chain node picks by stage_index itself (the same implementation as the canvas).
import json, time, urllib.request

BASE = "http://127.0.0.1:8188"
wf = json.load(open("my_workflow_api.json", encoding="utf-8"))
CHAIN, BRIDGE, SAVE, LOAD, SEAM = "958", "961", "902", "960", "931"   # ← replace with the node ids from your own graph
SEGMENTS = 3

for k in range(SEGMENTS):
    for nid in (CHAIN, BRIDGE, SAVE, LOAD, SEAM):
        # ⚠ **all five stage numbers must be equal** (Chain / bridge / write-to-disk / previous-stage latent read / **AudioSeam**);
        #   miss AudioSeam ⇒ it overwrites the previous stage's bed file with this stage's audio ⇒ the previous stage's track lands on the wrong stage when concatenating (fixed in 0.6.18).
        #   For old graphs (0.6.14 and earlier) add a stage_index to Chain first.
        wf[nid]["inputs"]["stage_index"] = k
    wf[BRIDGE]["inputs"]["run_id"] = "myfilm"             # the same run_id ⇒ all stages' files land in one directory
    body = json.dumps({"prompt": wf, "client_id": "my-script"}).encode("utf-8")
    req = urllib.request.Request(BASE + "/prompt", body, {"Content-Type": "application/json"})
    pid = json.load(urllib.request.urlopen(req))["prompt_id"]
    while True:                                           # wait for this stage to finish
        h = json.load(urllib.request.urlopen(BASE + "/history/" + pid))
        if pid in h:
            break
        time.sleep(2)
```

**The three non-UI paths for splicing** (all go through the same core code `relay_core.assemble_mp4_segments`, behaving identically):

🔴 **Read this first**: the CLI in ① lives under `tools/`, and `tools/` is **not shipped** with the
installed package (Comfy Registry / Manager installs do not contain it — `.comfyignore` excludes it)
⇒ **only people who `git clone` the repo can use ①**. If you installed from the registry, use ② or ③ —
they live **inside the package** and need no extra files.

① **Command line** (easiest; ⚠️ **only present in a `git clone`**):

```bash
python tools/concat_segments.py s1.mp4 s2.mp4 s3.mp4 -o film.mp4 --audio aac256
# optional: --audio aac192|lossless   --crf 16   --json
#        --pcm p1.safetensors p2.safetensors - -     ← per-stage PCM sidecars, in stage order; `-` = that stage has none
#                                                      (several per stage allowed: earlier = higher priority = further downstream)
```
Exit code **0 = passes the four assertions**, 1 = fails (the film may already be written, kept as evidence).

② **Library call**:

```python
import sys; sys.path.insert(0, "<ComfyUI>/custom_nodes/ComfyUI-H3-Latent-Relay")
from relay_core import assemble_mp4_segments
rep = assemble_mp4_segments(["s1.mp4", "s2.mp4"], "film.mp4",
                            audio_codec="aac", audio_bitrate="256k",
                            # sidecars optional, use None for a missing one; with several per stage, **earlier wins** (further downstream is more likely to be what really went into the mp4)
                            pcm_paths=["p1.safetensors", None])
print(rep["ok"], rep["asserts"], rep["report"])
```

③ **Back-end route** (you're already running ComfyUI and want the **server** to take the segments from history):

```bash
curl -X POST http://127.0.0.1:8188/h3relay/concat -H "Content-Type: application/json" \
  -d '{"stages":[{"stage":0,"prompt_id":"<id1>"},{"stage":1,"prompt_id":"<id2>"}],
       "count":0,"out_name":"film","audio_out":"aac_256k","video_crf":16}'
```
**Segment records use `stages`** (with **real stage numbers**; since 0.6.18 the canvas button uses this path): `stage` is the segment number (0-based),
an empty `prompt_id` means that segment has no record. The stage number is a **position** — report whichever segment is missing, **never shift later segments forward**
(the old field `prompt_ids` is still accepted, but it's flat, position = segment number, so a gap in the middle misaligns).
When `stages` isn't given it rescans history (taking the most recent `count` submissions "whose graph contains this Chain node", sorted by
`stage_index` in the submission graph); `audio_out` has three tiers = `aac_256k` (default) / `aac_192k` / `pcm_lossless`.

**How to get the sidecar (PCM) path**: two ways in a script — pass them yourself by segment order via `--pcm`;
or take them from history (same criterion as the route, fixed key `h3relay_pcm`):

```bash
curl -s http://127.0.0.1:8188/history/<prompt_id> | \
  python -c "import json,sys;d=json.load(sys.stdin);\
  print([o for n in d.values() for o in ((n[\"outputs\"].get(\"18\") or {}).get(\"h3relay_pcm\") or [])])"
```
(replace `18` with the node id of "Trim AV"/"Audio Seam" in your graph; when both exist, prefer the "Audio Seam" one.)

**Dependencies**: splicing needs `av` (bundled with the host ComfyUI; if missing, `pip install "av>=17"`);
the other features depend only on torch + safetensors. **The sidecar is purely additive**: it doesn't change a single byte of the segment files,
and external tools (ffmpeg etc.) can still read the segment files as before.
