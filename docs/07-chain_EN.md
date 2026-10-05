# Chain: auto-run · prompt dispatch · concat into a film

<!-- EN-SYNC src=docs/07-chain.md stamped=2026-10-05 mode=see tools/en_sync.json -->

🌐 English translation of [`docs/07-chain.md`](07-chain.md). **The Chinese file is the source of
truth** — if the two ever disagree, the Chinese one wins.

> This file was split out of the README (0.6.0 reorganization); rewritten in 0.6.7 (Chain can now swap prompts, and it can concat films too).
> 📐 **Slot notation**: throughout this document `[N]` is **0-based** (the Nth port in the UI = `[N-1]`).

---

`🔗 H3 Relay · Chain` is a **pure control node that does not participate in wiring** (it has no input/output ports).
It pulls **Chain + bridge + write-to-disk** (plus the "Latent Load" used along the chain) into the **same group box**
(box-select → right-click → Add Group), and the buttons live on the Chain node:

| Button | What it does |
|---|---|
| ▶ Run | Run once at the current stage number (click again if unhappy, overwriting the same stage-number file) |
| ✔ Approve | Stage number +1 (bridge/write-to-disk change in sync), queue the next stage |
| ⏩ 连跑 | Auto-loop according to `segments`: run one stage → stage number +1 → run again (0 = infinite). **While running the button text becomes progress**: `⏳ 连跑中 ██████░░░░ 3/10 · 采样 5/8` |
| ⏭ 续跑 | **Resume from the stage it last ran to** (0.6.15). Reads `output/relay_kit/<run_id>/_progress.json` ⇒ sets the stage number back ⇒ starts the auto-run. After the host hangs / is killed by the watchdog you do not have to start over. ⚠ You must fill in `run_id` first (consistent with bridge / write-to-disk) |
| ⏹ Stop | Stop advancing after the current sampling run finishes |
| 🧩 拼成一条 | Concat the N stages already run into a single film (0.6.7). **The concatenated film path is written into the `concat_result` cell** + one popup |
| ↺ Reset | Stage number back to 0, restart from stage 1 (also clears this round's stage records) |

> ⚠️ **Clicking `▶ Run` / `✔ Approve` mid-auto-run is blocked with a notice** (it will not silently kick the state machine back to idle).

### How to see at a glance "did the click land / is it running / has it stopped / did it error"

State is expressed through **three mutually independent** channels at once:

| Channel | Idle | Queued | Auto-running | Done | Stopped / needs attention | Error |
|---|---|---|---|---|---|---|
| **Title-bar colour band + stage text** | not drawn | 🟦 blue · queued | 🟩 green · auto-running | ✅ cyan · done | 🟧 orange | 🟥 red |
| **`status` cell glyph prefix** | ⚪ | 🟦 | 🟩 | ✅ | ⚠️ / 🟧 | 🟥 |
| **Button text** | unchanged | — | `⏳ 连跑中 ███░░░ 3/5 · 采样 5/8` | — | — | — |

- **At the click instant the button text changes immediately** to "⏳ … · 已点击" (restored after 700 ms; if the state machine has already taken over the text it does not grab it back);
- **Debounce**: repeat clicks on the same button **within 0.4 seconds** only count the first (one auto-run takes tens of seconds; a double-click = burning a whole round of GPU for nothing).
  "⏹ Stop" and "↺ Reset" are **not debounced** — those are the brakes, they must respond immediately at any time.

> The colour band is drawn on the **title bar** (canvas drawing), not by changing the node's `color` field — `color` is **serialized into the workflow**,
> so changing it silently changes the user's graph. When idle nothing is drawn.

### Stage records (they decide which stages "🧩 拼成一条" concatenates)

Every successful queue records the `prompt_id` against the **stage number** (`{id, seq}`, `seq` = which round it belongs to). Three rules:

- **Re-running the same stage ⇒ overwrite, not two stages**;
- **When starting stage k, drop old records with `stage > k`** — re-running an earlier stage on the continuation chain necessarily makes later stages stale.
  The dropped stage numbers are written into `status`; the stages **before** k are still valid ⇒ auto-running from stage 3 need not re-run the first two;
- **Holes (a missing stage between head and tail) are not compacted**: `auto_concat` **refuses to concat** on a hole and says which stage is missing;
  the manual "🧩 拼成一条" is allowed to concat, but the report must list which stage is missing and which stages come from an earlier round.
  ⚠ **Resuming from stage k is not a hole**.

## Parameters (optional cells, always appended after `status` / **default ⇒ old-graph behaviour bit-exact unchanged**)

| Cell | Default | Effect |
|---|---|---|
| `prompts` | `""` | **Each stage's own prompt**: split into blocks by a **standalone `---` line**, block k feeds stage k. Empty = no prompt swap (old behaviour) |
| `prompt_target` | `""` | Which cell the prompt is written into. Empty = auto-detect; `683` = the highest-priority prompt cell on that node; `683.h3_data` = name the field explicitly |
| `auto_concat` | `false` | ⏩ after the auto-run ends, **automatically concat** into a film |
| `concat_name` | `""` | Film filename (without extension). Empty = use the write-to-disk `run_id`; saved under ComfyUI's `output/` |
| `stage_index` | `0` | **Which stage is being run now** (0-based). It decides which **block** of `prompts` the `prompt` output port gives. Clicking ▶/✔/⏩ on the canvas **changes it together with the bridge, write-to-disk, and the previous-stage latent read**; in scripts you change it yourself (see `docs/10` §7.4) |
| `run_id` | `""` | **For resume-from-checkpoint**: fill the same value as the bridge / write-to-disk `run_id`. Once filled, each stage writes its progress into `_progress.json` in the same directory as the stage files; empty = no progress recorded (not a single extra file is written) |
| `concat_result` | `""` | **No need to fill in**: after a successful concat it shows the film path (selectable/copyable). ⚠ It is a separate cell because writing it into `status` would be overwritten by the next status message |

### Wiring: the `prompt` output port (since 0.6.15)

```
H3RelayChain.prompt ──► the prompt node's prompt input
```

🔴 **"Block k feeds stage k" is a node capability, not front-end private work.** Connect the line above and
**canvas manual clicks and script-submitted graph JSON go through the same node** — the block-splitting algorithm is in `relay_core.split_prompt_blocks`
(the single authoritative implementation), and out-of-range (not enough prompt blocks) raises an error directly.

- 🔴 **This line must be connected** (since 0.6.15 the front-end's old route that "writes the prompt cell for you" has been **deleted**):
  with no line connected, clicking auto-run is **blocked on the spot** and it says which line to connect — it **will not silently not swap the prompt**.
  If you only run one stage and do not need a prompt swap, leaving `prompts` empty avoids the block.
- **With the line connected**: the link value takes priority over the widget value, and the prompt-emitting node receives the block Chain picks out by stage number.

## Prompt dispatch: block k feeds stage k

```
(paste into the prompts cell)
Japanese cel-shaded 2D animation… [Shot 1] medium close-up, Yuki and Kara grapple at close quarters…
---
(stage 2's prompt: open by picking up stage 1's motion and camera)
---
(stage 3's prompt)
```

**Where prompts come from**:

- **Typed directly into Chain's `prompts` cell** — the most direct, unaffected by the front-end version (recommended).
- **Linked in from another node** (e.g. `easy positive`). With a link, **the linked prompt wins** —
  what is submitted to the host is the link value. When linking, Chain goes upstream to find a `positive` / `prompt` / `text` text cell
  (it also accepts an upstream that has only one text cell); **if it cannot find one it raises an error**, and will not silently fall back to a stale local value.

⚠ A link only looks **one layer** deep (Chain ← upstream); it does not chase nodes further up.

- **When it is written**: whether you click ▶/✔ manually or ⏩ auto-run, **before starting stage k** block k is written into the target cell.
- **Block count < number of stages to run** ⇒ **do not queue** and raise an error (never silently reuse the previous block — that is exactly the "thought I swapped the prompt, but didn't" trap).
- **Why multi-line text and not a "prompt directory"**: UI users are used to "editing text on the node"; making people create
  `seg1.txt..N.txt` is **forcing an API habit onto the UI**. Multi-line text = one paste, zero filesystem operations.
- **Auto-detection rules** (when `prompt_target` is empty):
  `h3_data` (the JSON string of a third-party prompt-emitting node) > official `MiniMaxH3*.prompt` > other `prompt`;
  **multiple candidates at the same priority raises an error** and lists them (let a human specify, don't guess).
- `h3_data` is a **JSON string** ⇒ the front end **parses → changes only the `prompt` field → re-serializes**,
  keeping all other fields as they are; a parse failure or a non-object ⇒ **refuses to write** and keeps the original value.
- The pure function is in `web/relay_kit_prompt.js` (**canvas copy**); **the single authoritative implementation is `relay_core.split_prompt_blocks`**
  (both the node and the self-check tools call it; cross-language consistency is locked by both sides reading `tests/prompt_blocks_cases.json`),
  unit test: `node tests/test_prompt_dispatch.mjs`.

## Concat into a film: one click in the pack, no external ffmpeg

**The stage list does not guess filenames**: the front end hands the backend each stage's `prompt_id` returned by `queuePrompt`,
and the backend uses ComfyUI's own history to retrieve that stage's write-to-disk echo ⇒ precise down to "the exact stages I just ran".
When no id is given it rescans history for submissions "whose graph contains this Chain node" (taking the most recent N);
if it still cannot find any it **states plainly "what I looked for"** and does not concat half a film.

**What it does**:

1. **Per-stage checkup** (blocked before concat): no video stream / stages disagree on resolution·fps·sample rate·channels /
   **audio longer than video by more than 1 frame** (= that stage's audio most likely **bypassed "Trim AV"**);
2. **Picture**: stream copy (repositioning timestamps packet by packet, pts/dts both shifted by the same offset) ⇒ **zero re-encode, bit-exact lossless**;
3. **Audio**: decode each whole stage ⇒ per stage "remove encoder priming (`AUDIO_ENCODER_PRIME_MS`, same as `AudioSeam.join_prime_ms`)
   → cut to this stage's **valid duration** (= frame count/fps, same as `join_audio_segments`'s `segment_seconds`)" ⇒ re-encode;
4. **Four assertions**: frame count conserved / no PTS holes / monotonic DTS / A·VΔ ≤ 1 frame;
5. Assertion fails ⇒ the film is kept for forensics, and the report states plainly **do not use it as a deliverable** (it is not silently deleted, nor is success faked).

**Measured** (4 stages / 702 frames / 29.25 s, 672×1184):

| Quantity | Value |
|---|---|
| Time | probe 0.12 s + concat 1.26 s + assertions 0.02 s ≈ **1.4 s** |
| Picture | the film's first frame is **bit-exact identical** to the source stage (comparison arm: full re-encode 3.6 s) |
| Audio per-stage landing | **0 samples** off (stage 1 1 ms), NCC 0.999 |
| Film | 702/702 frames conserved, no PTS holes, monotonic DTS, A·VΔ = 0.001 s |

**Fallback**: default `video="auto"` — it first uses picture stream copy, and only if the film's assertions fail does it fall back to **single-pass re-encode** and re-concat.

## Audio profile / video profile (`audio_out` · `video_crf`, added 2026-09-22)

They affect only the **film**, not any stage file. The picture is always **stream copy (lossless)**, so `video_crf` is normally not needed at all.

| `audio_out` | Film audio track | Measured SNR (vs lossless source) | When to use |
|---|---|---|---|
| `aac_256k` (default) | mp4 + AAC 256k | 40.5 ~ 50.0 dB | default; works everywhere in browsers/editors |
| `aac_192k` | mp4 + AAC 192k | 38.4 ~ 44.6 dB | to save ~25% size |
| `pcm_lossless` | mp4 + PCM f32 | **bit-exact** | masters/archival (⚠ no sound in browser preview, ≈4.4 MB/s) |

**The mechanism of audio generation 2 → 1**: the stage file's audio track is the AAC the user's write-to-disk node encoded (generation 1); concat decodes and re-encodes it from the mp4 = generation 2.
So "Trim AV" conveniently writes a **lossless PCM sidecar** (`save_pcm`, on by default, ≈2 MB/stage), and concat **reads the sidecar directly**:
it does not decode AAC and does not touch priming ⇒ only one generation is encoded; choosing `pcm_lossless` means **zero new generations**.

- Sidecar **missing** (old graph / save_pcm off / that run did not go through "Trim AV") ⇒ **that stage** falls back to mp4 decoding, and the report states per stage
  `音频源=PCM / AAC`; when it is filled with silence a warning is given. **It neither raises nor stays silent**.
- Sidecar **write failure** does not affect this stage's render (a log hint + concat falls back automatically).
- Retrieval: the node echoes the sidecar path into ui (`h3relay_pcm`) ⇒ the concat side takes it from history, **it does not guess filenames**.
- **How stage files are found again**: `pick_video_outputs` **scans all keys of history by extension** (the host `SaveVideo` writes
  `images`, the third-party `banzhangVideoCombine` writes `painter_output`), and when an entry carries `abs_path` it prefers that
  (some nodes save outside ComfyUI output). ⇒ third-party write-to-disk nodes can be concatenated directly too.
- **What if the chain has other audio nodes too** (production-line wiring = `TrimAV → AudioSeam → write-to-disk`):
  "Audio Seam" also writes its own sidecar (the very audio it sends to write-to-disk). Concat picks among multiple candidates by
  "**closest to the mp4 audio length**"; a difference > 100 ms is **rejected** and it falls back to decoding
  (100 ms ≈ the container's priming+tail-padding upper bound; a J-cut difference of 0.9 s ⇒ gets cleanly blocked).
  ⚠ A **slight** difference (< 100 ms) is not rejected but padded with silence + a warning — that is the normal handling of "sidecar shorter than video".

**How to use without opening the canvas**: command line `python tools/concat_segments.py s1.mp4 s2.mp4 -o film.mp4`
(**the same core code as the 🧩 button**), or directly `from relay_core import assemble_mp4_segments`,
or `POST /h3relay/concat`. The parameter correspondence between the three and script examples are in `docs/10` §7.4.

**Host compatibility**: `app.queuePrompt` / `api.fetchApi` are front-end internal interfaces; if they are missing it **says so in the status cell**
(not a silent failure); if `add_stream_from_template` (av>=17) is missing it **falls back to re-encode automatically** and states the reason in the report.

`video_crf` (default 16, 0~51) only takes effect when the picture **must be re-encoded**: stages have inconsistent specs, or the stream-copy path's assertions fail (automatic fallback).
⚠ `crf 0` is still **not lossless** (RGB→YUV 4:2:0 chroma subsampling loses first) — for true losslessness you have to start from the **write-to-disk node**.

## Stage-number advance: **table-driven, five places move together**

`stage_index` appears in **five places** in this pack (the nodes in `nodes.py` that declare this widget + Chain itself).
Chain advances them according to an **explicit table** — the table is the single source of truth, and missing any class shows up immediately in `status`'s
"段号同步 #…":

| Node | `stage_index` semantics |
|---|---|
| "Chain" Chain itself | decides which **block** of `prompts` the `prompt` output port gives |
| "Copy Bridge" | which stage of the whole film this stage is |
| "Latent Save" | same as above (must be consistent with the bridge) |
| "Latent Load" | **also this stage's stage number** (the node internally uses `stage_index - 1` to fetch the file) |
| "Audio Seam" | **also this stage's stage number** (decides which stage the bed file `audio_%05d.safetensors` and `joined` concat to) |

All five must stay in sync. History:

- **Before 0.6.12 only "bridge + write-to-disk" were advanced** ⇒ "Latent Load" stayed at `0` and read `stage -1`
  ⇒ from stage 2 on every stage reported `stage_index=0 是第 1 段，没有上一段可续`;
- **0.6.15 added Chain itself** (it decides which block of prompts is output);
- **0.6.18 added "Audio Seam"** ⇒ before that it always considered itself "stage 1" and **overwrote stage 1's bed file**
  `audio_00000.safetensors` **with stage 2's audio** ⇒ at concat stage 1 got stage 2's audio track
  ⇒ **the film's audio/video are on the wrong stages** (the picture itself is correct, only the audio track is wrong).
  The same fix also revived two switches that were already broken: the `patch_seconds > 0` patch branch
  (stage number always 0 ⇒ it always took "stage 1 has no seam to patch") and the `joined` output (only concatenating `0..this stage`).

> Only the places **inside the same group box as Chain** are advanced; when a type has none in the group, it falls back only if
> it is **exactly unique in the whole graph** (so "Latent Save" / "Audio Seam" can be found even outside the group box). When two auto-run groups coexist the fallback degrades to
> "do nothing + a status hint" — in that case pull the corresponding nodes into their own group boxes.

## `run_id` sync: **also table-driven, six places move together** (0.6.19)

`run_id` is "what this film is called", and it decides the directory the stage files land in, `output/relay_kit/<run_id>/`.
It is stored in a copy on **six nodes** (the places in `nodes.py` that declare this widget):

| Node | What `run_id` governs on this node |
|---|---|
| "Latent Save" | which directory the stage files are written to (`<run>/stage_NNNNN.safetensors`) |
| "Copy Bridge" | when `ref_anchor_stage ≥ 0`, which directory the appearance anchor stage is read from |
| "Latent Load" | which directory the previous stage is read from |
| "Trim AV" | only when filled does it write `<run>/appearance_log.jsonl` (the E4 drift curve) |
| "Audio Seam" | which directory the bed file `audio_%05d.safetensors` lands in |
| "Chain" | the progress file `<run>/_progress.json` ("⏭ 续跑" relies on it) |

**Change one place ⇒ the rest of the group's cells follow automatically** (the table is `RUN_ID_TYPES` in `web/relay_kit_sync.js`):

- Scope = **the same group box** (the same `nodesInSameGroup` semantics as the stage-number advance) —
  having two films in one graph is a normal use case, and cross-group syncing would change the other film.
- 🔴 **Name conflicts are not guessed**: two different non-empty names in the same group ⇒ **the "will submit" buttons are blocked** and it lists
  "which node holds which name", for you to decide. Stage files landing in the wrong directory is far harder to trace than an error.
- **Empty values do not spread**: clearing one cell ≠ wanting to clear the whole group.
- **Backstop**: the four buttons `▶ Run` / `✔ Approve` / `⏩ 连跑` / `⏭ 续跑` each run the gate once before queueing,
  and by the way **fill in** the cells that are "the only non-empty one, the rest empty" ⇒ you only need to change one place.
  (Some front-end versions may swallow widget callbacks — then the gate catches it, and it **does not fail silently**.)

> ⚠️ **Pure front-end capability**: it **does not take effect** when a script submits graph JSON (scripts already write this cell as the same variable,
> see [`docs/10`](10-audio-seam-and-concat_EN.md) §7.4).
> ⚠️ **After renaming, the old directory is not moved along**: stage files stay under the old name ("directory name = film name" is this pack's consistent semantics).

## Stage 1: **nothing to do** (no manual bypass)

Stage 1 has no previous stage to copy, but **neither the wiring nor the run needs any extra operation**:

- "Latent Load" at `stage_index = 0` **hands over an "empty context"** (it no longer throws);
- the bridge receives an empty context ⇒ **passes through automatically**: the latent passes as-is, and the trimmed frame count output is `0`;
- at stage number ≥ 1 "Latent Load" reads the previous stage's write-to-disk file ⇒ the bridge really continues.

⇒ Just click "⏩ 连跑" and it runs from stage 1 all the way down.

**Why you cannot "bypass" it** (finalized by measurement in 0.6.12):

- the bridge's `context_latent` is **required** ⇒ with no line connected ⇒ submit validation reports `Required input is missing` directly;
- **bypassing "Latent Load" does not work either** — before submitting, the host "dissolves" bypassed nodes and connects their inputs to downstream,
  and this node has no LATENT input ⇒ the bridge's required input would **disappear from the prompt** and it is rejected the same way.

⇒ Chain therefore **takes over in reverse**: whoever bypassed it is automatically restored to enabled, and it says so in `status`.

⚠ **Do not bypass the bridge**: the bridge has no INT-typed input to pass through, so `trim_frames` would become an empty value.
When the bridge is bypassed, Chain names you in `status` and reminds you to press `Ctrl+B` to restore it.

**Any button that does nothing when clicked** ⇒ look at the `status` cell first. Since 0.6.12 every button callback has a backstop:
an internal exception is also written as `⚠ … 内部出错：…` into `status`, so "no feedback on the canvas at all" no longer happens.

## The status cell (look here when a click does nothing)

`status` writes: the current stage number / queued / ⚠ group not placed right / ⚠ queue failed /
which cell stage k's prompt was written to / not enough blocks / the concat result and the assertion numbers.

## Voice anchor (voice_anchor, 0.6.19): lock timbre when continuing across speakers

### The problem

The copy bridge copies the **previous stage's audio tail** (about 0.9 seconds) bit-exact into this stage, and pins it as `audio_ref` throughout the conditioning.
The design intent is "audio continuity", but the side effect: **the previous stage's speaker timbre becomes a generation condition for this stage's timbre**.
When the dialogue switches speakers stage by stage ⇒ timbre cross-contamination (measured: the same character's F0 114→131 Hz, spectral centroid 1028→1308;
with the voice anchor the distance from the baseline shrinks to 1/5, and the clipping at the generation end also disappears).

### Implementation

`H3RelayCopyBridge` gains a **new optional input `voice_anchor` (LATENT)**:

- Wiring: `LoadAudio → VAEEncodeAudio (audio VAE) → voice_anchor`
- When provided: **two places are replaced from the same source** — ① the pinned audio prefix, ② the `audio_ref` conditioning, both switched to the voice anchor's
  **tail window** (window length = the audio step count converted from `context_frames`, 22 frames ⇒ 37 steps ≈ 0.925 seconds);
- When not connected: the behaviour is **bit-exact identical** to the old version (off by default, zero migration cost);
- Supports three latent forms: `VAEEncodeAudio`'s pure audio tensor (mainstream) / AV joint latent
  (NestedTensor, take stream 2) / an already-split list.

### Limitations

| Limitation | Explanation |
|---|---|
| **The anchor replaces the whole audio tail window** | BGM/ambient bed music inside the tail window is replaced too. Users with bed music should **mix the bed music into the voice anchor** before connecting |
| **Emotion is partly pinned** | The voice anchor's timbre/pace carries over into this stage. Pick a clean clip whose **emotion is close** to the target dialogue |
| **The seam region only anchors the first one, but the reference window may contain multiple people** (0.6.27) | The **pinned** part of the seam region (= the video pinned window `context_frames`, **default** 22 frames ⇒ 0.925 s; adjustable 5/22/39/…/124) always covers only "the person who speaks next at the seam"; while the `audio_ref` **reference window** since 0.6.27 has `0` = **auto-length determined by the voice anchor itself** (**takes the full cap** ≤6 s) ⇒ feed a **multi-person recording** into the voice anchor and the reference can contain the timbre of **multiple speakers**. Speakers switched later within the same stage are still cut by the model itself (the prompt's `<Subject N>`) |
| **Voice-anchor duration** | The tail window needs 0.925 seconds ⇒ voice anchor ≥0.9 seconds (if shorter it uses the full length and reminds in the report); 1–4 seconds recommended |
| **Ignored when `pin_audio=false`** | The report states it (degradation is visible, not silent) |

### Audio reference budget: this pack always occupies 1, the official 3 are **left for you** (0.6.22)

| Fact | Evidence |
|---|---|
| Official `ref_audios` = **Autogrow `min=0 / max=3`** | `comfy_extras/nodes_minimax_h3.py` (**the UI layer's** convention) |
| **The model layer has no cap** | `comfy/ldm/minimax/model.py` is `for blk in refs: cursor += _ref_t_span(blk)` — variable-length accumulation, **no truncation, no count check** |
| This pack **always occupies 1** audio reference when `stage_index ≥ 1` | `relay_core.apply_relay`'s `plan.audio_ref`: with a voice anchor connected = the voice anchor tail window, without = **the previous stage's audio tail**, one of the two |
| The appearance anchor **does not occupy** the audio budget | `plan.anchor_ref` is `kind="video"` ⇒ it goes through the image channel |
| The two channels **coexist, they do not replace each other** | the host writes the user's 3 first, then this pack `append=True` appends its own |

🔴 **Consequence**: if you fill all 3 slots and then connect a voice anchor ⇒ the model side sees **4** audio references.
The model **takes them all** (no error), but it **exceeds the official convention and we have not measured it** ⇒ this is "we let you go out of bounds", and it must be stated proactively:

- **On the canvas**: the bridge node's **title** directly carries a line `⚠ 音频参考 4 个（官方上限 3）…` (not a tooltip, not the console
  — many users do not read logs). The number comes from counting the **connected** slots of `ref_audios.ref_audio_N` in your reference node.
- **In the report**: `relay_core.count_official_audio_refs` gives the authoritative number (script/API users look here).
- ⚠ **This pack's 1 has no "completely off" switch** (without a voice anchor it uses the previous stage's tail window) ⇒
  to really return to the official budget, the only way is to **reduce the reference node's `ref_audio` slots yourself**.
  (This repo's iron rule: no silent degradation — we **hint**, we do not drop the voice anchor for you.)

### Multiple speakers within one stage: how to anchor (0.6.21)

**First pin down the scope**: the voice anchor answers **only one** question — "**who is the person who speaks next at the seam**".
The anchor window is only ~0.9 seconds (= the seam region, converted from `context_frames`), and it replaces the audio context **at the head of this stage**.
⇒ **A later speaker switch within the stage is beyond the anchor, and should be** — that is the job of the prompt's `<Subject N>`.

From this come three rules (all machine-checkable from the prompt):

| Case | Verdict | What to do |
|---|---|---|
| This stage's first speaker == previous stage's last speaker | **same-person continuation** | **No anchor needed** (keeps audio continuity; connecting the same person's anchor is harmless too) |
| This stage's first speaker ≠ previous stage's last speaker | 🔴 **speaker switch at the seam** | **Must connect an anchor, anchor = the first person to speak in this stage** (otherwise the previous person's timbre contaminates this stage) |
| ≥2 speakers within this stage | the anchor **covers only the seam region** | later switches rely on the prompt's `<Subject N>`; **split into stages if you can** (switching within a stage is hard in all three of timbre/lip-sync/timeline) |

🔴 **The two easiest mistakes**:
1. **Picking the anchor by "protagonist" or "the person with the most screen time in this stage"** ⇒ wrong. What you must pick is **the first person to speak**.
2. **Expecting the anchor to govern every person's timbre across the whole stage** ⇒ impossible. The anchor is only at the seam region; later speakers rely on the prompt.

**Criterion tool**:

```bash
python tools/voice_bank.py advise --bank <voice-bank dir> --prompt-file seg2.md --prev-file seg1.md
python tools/voice_bank.py advise --bank <voice-bank dir> --speakers <character A>,<character B> --prev-speaker <character A>
python tools/voice_bank.py selftest     # self-test of the speaker-parsing rules (falsifiable)
```

It computes the speaking order by "each `<d>`'s **nearest preceding** speaker tag" (same source as `check_h3_prompts.py`'s L5),
and outputs "should you connect / whom to connect / how many people in this stage"; missing anchor ⇒ exit code 3 (same as `lookup`).
⚠️ **Minimal format** has no `subject_definitions` section (what follows `<Subject N>` is an action sentence) ⇒ character names are unavailable,
so give them explicitly with `--speakers`.

### Multiple people within one stage: how the two channels cooperate (0.6.23 research conclusion)

> On 2026-10-04 the host's reference channels were **read to the very end of the code** (not guessed); the conclusion differs from the previous version of the notes, so follow the new conclusion.

**① How references enter the model: two channels, same order, same count**

| Channel | Content | Who generates it |
|---|---|---|
| **Text presentation** | one tag per reference: `<Picture i>: ` / `<Video k>: ` / **`<Audio j>: `** | `comfy/text_encoders/minimax.py:169-191` (tokenizer) |
| **DiT conditioning rows** | the `minimax_refs` block list ⇒ arranged **before** the target stream, **pinned and not updated** conditioning rows | `comfy/ldm/minimax/model.py:396-438` (`PackedLayout`) |

🔴 **The "content" of an audio reference goes only through DiT; the text side has only that empty tag** (`# audio never enters Qwen`,
`minimax.py:10`). **The tag's purpose is just to let the prompt reference it** — the official node itself says
*"Use the same tags when prompting"*. Two third-party packs (`minimax-h3-audio-T8`, `csglide_cast.py`)
are the same paradigm: `clip.tokenize(prompt, minimax_ref_items=ref_items)` and
`conditioning_set_values(cond, {"minimax_refs": ref_blocks})` **same order, same count**,
and **a reference is not sent unless the prompt references it**.

**② Therefore this pack does not do "stuff in more blocks" multi-anchoring** (that was the approach envisioned in the notes before 0.6.22)

This pack injects audio references via `conditioning_set_values(..., append=True)` — **appending after the fact** to the DiT side,
whereas tokenize **already happened long ago**. ⇒ the appended block:

1. **has no corresponding tag on the text side** (the `<Audio j>` numbering was fixed at the moment of tokenize) ⇒ **the prompt cannot reference it**;
2. makes the text tag count and the DiT reference block count **no longer equal** — and "same order, same count" is exactly this channel's contract;
3. is the **opposite** of what the two third-party implementations do.

⚠️ More importantly: **the measured effect of this pack's existing 1 anchor does not prove "an untagged block is adopted by the model"** —
the voice anchor also **replaces the pinned audio prefix** (`pin_audio`), both change together ⇒ the effect attribution is confounded.
⇒ Extrapolating "N also work" from "1 works" is **unfounded**. **Therefore this pack does not implement `voice_anchor_2/3`.**

**③ For each of several people in one stage to have their own reference ⇒ use the official `ref_audios` slots (the proper way)**

```
other speakers' audio ──► TrimAudioDuration (trim to ~0.9 s) ──► MiniMaxH3ReferenceToVideo.ref_audio_N
the first person who speaks in this segment ──► VAEEncodeAudio ──► Copy Bridge.voice_anchor   (this pack: stage-level, seam region, automatic window)
```

- Why trimming is mandatory: the reference row **follows every sampling step** (the resident row with `audio_update=False` in `PackedLayout`)
  ⇒ a longer reference is slower, and a long reference drags the timbre of the whole content in with it.
- Reference it in the prompt with `<Audio j>` (e.g. `<Audio 1> is <character B>'s voice`), together with `<Subject N>`.

**④ 🔴 The numbering of `<Audio j>` is **not** the slot number — this is the easiest foot to trip over, and it **raises no error at all**"

Host `comfy_api/latest/_io.py:1198-1211` enumerates `ref_audio_0..max` by **ascending index**,
but `if expected_id in live_inputs` **only puts connected slots into the dict**; the official node then uses
`for audio in (ref_audios or {}).values()` to hand it to the tokenizer, and the tokenizer issues `<Audio 1..N>` by **enumeration order**.

⇒ **The j in `<Audio j>` = the j-th "connected" slot (by ascending slot number)**:

| Slot you connect | What to write in the prompt | Consequence of writing it wrong |
|---|---|---|
| `ref_audio_0` / `ref_audio_1` / `ref_audio_2` (contiguously full) | `<Audio 1>` / `<Audio 2>` / `<Audio 3>` | numbering matches the slot number, no problem |
| **only** `ref_audio_2` | **`<Audio 1>`** | writing `<Audio 3>` ⇒ **points at nothing, and no layer raises an error** |
| connect `ref_audio_0` + `ref_audio_2` | `<Audio 1>` / `<Audio 2>` | writing `<Audio 3>` ⇒ same as above |

🔵 **This pack's backstop**: when slots **skip numbers** (the numbering lies), the bridge node's title directly writes out the actual numbering
(`web/relay_kit_refs.js::ordinalHint`, it only speaks when the numbering lies, so it makes no noise when slots are contiguously full);
the report also gives the authoritative convention.

**⑤ How to state this pack's injected 1 correctly** (already written into the report; do not say the opposite elsewhere)

It is the **(official count + 1)-th audio reference on the DiT side**, **with no text tag and unreachable by the prompt**,
seen by the model only as a conditioning row. For a reference that "can be referenced by the prompt" ⇒ see ③.

**⑥ 🆕 A third channel: multiple people's timbre in one slot (0.6.25)**

What ② said — "no `voice_anchor_2/3`" — still holds (that is **multiple blocks**, tags do not match).
But the **multi-anchor** the repo author wanted is actually a different thing: **occupy only one audio reference slot, yet make stage 2 reproduce the timbre of everyone in stage 1**.
The two can hold at the same time — **the key is not "how many blocks" but "how long the audio is inside that single block"**:

| | Old convention | Since 0.6.25 |
|---|---|---|
| Who decides the window length | the **video pinned window** (22 frames ⇒ **0.925 s**) | `audio_ref_seconds`, `0` = **auto** (measured 2~6 s) |
| What is inside that block | the previous stage's **last sentence** | the previous stage's **tail few seconds** (may contain multiple people) |
| Budget occupied | 1 | **still 1** |

Why it can be lengthened (two measurements reproduced with zero GPU):

1. `plan_relay(audio_frames=…)`'s window length **is inherently independent of the video pinned window** (in the old code the node always passed `None`
   ⇒ wasting this parameter for nothing); measured `a_frames` 39 → 1.625 s, 96 → 4.0 s, 240 → 6.575 s (hits the cap);
2. the window's **raw material is the previous stage's whole audio** — `KEY_EXPORT_TAIL_AUDIO` **does not exist** in the host
   ⇒ `audio_tail_from_latent` takes the `audio_from_latent` branch and can fetch the **whole stage**.

The criteria for auto-length (`relay_core.auto_audio_ref_seconds`):

- **has sound** = each cell's first-order difference of RMS > `P99 × 0.20` (**the difference must be used**: the real path gets
  the **VAE audio latent**, already normalized, so a silent region's RMS is the same order as a speech region ⇒ judging by RMS always gives "voiced cells = 0");
- accumulate **2 seconds of sound** from the tail backwards and stop, **cap 6 seconds**;
- the threshold uses **P99 rather than P50 / max** (four criteria compared side by side in measurement; the reason is in the comment on `AUDIO_REF_VOICE_REL`).

⚠️ **The cost (not confirmed by measurement, do not treat as verified)**: the audio in the window **is the previous stage's dialogue** ⇒
the model **may repeat it** — this is the only risk in theory.

**Measured (2026-10-04, 2 arms 243 frames)**: window **38 steps → 168 steps** (0.93 → 4.18 seconds, 4.4×;
covering the previous stage's speaking segments **4 → 17**) ⇒ both arms **accurately say stage 2's three lines, zero stage-1-specific words** ⇒
**no repetition observed**. The criterion's reliability is guaranteed by a **control experiment**: the same set of ASR criteria on stage 1's sample **hits 3/3
of all 5 proper nouns**. ⚠️ It covers only **one clip, one generation** (the host `er_sde` is non-deterministic ⇒ not a reproducible A/B)
⇒ for dialogue-dense material we still recommend listening yourself.

⇒ **The division of labour between the two channels** (③ and ⑥ are complementary, not conflicting):

| What you want | What to use | How many slots |
|---|---|---|
| stage 2 reproduces the timbre of **everyone** in stage 1 (without specifying who is who) | ⑥ `audio_ref_seconds` (**no voice anchor** ⇒ raw material = the previous stage's tail) | 1 |
| stage 2 reproduces **one specified person** (speaker switch at the seam) | ③ the official `ref_audios` slots + prompt `<Audio j>` | official slots |
| stage 2's timbre **pinned** to a certain person, and governing the seam region | the voice anchor `voice_anchor` (since 0.6.27 **`0` = auto-length by the voice anchor itself** ⇒ feeding a **multi-person recording** achieves "one anchor, many people") | 1 |

### Ensemble scenes: N people speaking / how to connect when character overlap is low (decision table)

> **This section is the summary decision table of §Voice anchor · §Multiple speakers within one stage above** — for the use case "run many stages, many characters, low overlap in every stage".
> ⚠️ This section contains no unlabelled speculation: anything not measured is explicitly written "not measured".

#### 0. First pin down the scope (three items; if unmet everything after is wrong)

| # | Fact | Consequence |
|---|---|---|
| 1 | **The voice anchor answers only one question** — "**who is the person who speaks next at the seam**" (the anchor window is ~0.9 s, converted from `context_frames`) | A **later speaker switch within the stage** is beyond it ⇒ rely on the prompt's `<Subject N>` |
| 2 | **This pack always occupies 1 audio reference of the budget** (`stage_index ≥ 1`; the voice anchor tail window **or** the previous stage's audio tail, one of the two) | **There is no "completely off" switch** ⇒ to free budget you can only reduce the **official** `ref_audio_*` slots |
| 3 | **This pack's 1 has no `<Audio j>` tag** (`append=True` after tokenize) | ⇒ **the prompt cannot reference it**; a "nameable" reference can only go through the **official slots** |

#### 1. First step: count two things (no need to read the prompt by hand)

```bash
python tools/voice_bank.py advise --bank <voice bank> --prompt-file segN.md --prev-file segN-1.md
# the minimal format (no subject_definitions block) yields no character names ⇒ give them explicitly:
python tools/voice_bank.py advise --bank <voice bank> --speakers charA,charB --prev-speaker charA
```

Outputs three things: **whether to connect an anchor / whom to connect / how many people in this stage**. Missing anchor exit code = 3.

#### 2. Decision table: this stage has N people speaking ⇒ how to connect the anchor, how to split the budget

| Case | Voice anchor `voice_anchor` | `audio_ref_seconds` | Official `ref_audios` slots | Note |
|---|---|---|---|---|
| **1 person · same-person continuation** (first speaker == previous stage's last speaker) | **do not connect** (keeps audio continuity; connecting the same person's anchor is harmless too) | `0` = auto 2~6 s | 0 | the auto window takes the previous stage's tail ⇒ timbre continues naturally |
| **1 person · speaker switch at the seam** (first speaker ≠ previous stage's last speaker) | 🔴 **must connect**, anchor = **the first person to speak in this stage** | `0` = auto-length by the **voice anchor** (≥2 s); an explicit number of seconds works as usual (the anchor must be long enough to pull it) | 0 | not connecting ⇒ the previous person's timbre contaminates this stage |
| **≥2 people · first speaker == previous stage's last speaker** | **do not connect** | `0` = auto (previous stage's tail, **may contain multiple people**) ⇒ one anchor, many people | 0; add only if you **want to "name someone"** | see §3 division of labour |
| **≥2 people · speaker switch at the seam** | 🔴 **must connect**, anchor = the first person to speak | `0` = auto-length by the **voice anchor** ⇒ **feed a multi-person recording to achieve "one anchor, many people"** | **naming the rest can only go here** | the anchor + official slots share the budget |
| **ensemble (overlap extremely low, one line each)** | decided by "whether the first speaker switches"; connect only if it switches | prefer `0` (auto) | **split into stages if you can**; if not, pick only the most important person | see §4 degradation path |

🔴 **The two most common mistakes** (both fail silently, no error):
1. **Picking the anchor by "protagonist / person with the most screen time"** ⇒ wrong. Pick the **first person to speak**.
2. **Expecting the voice anchor to govern every person's timbre across the whole stage** ⇒ impossible. The anchor is only at the seam region.

#### 3. The division of labour between the three channels (complementary, not either-or)

| What you want | What to use | How many audio reference slots |
|---|---|---|
| stage N reproduces the timbre of **everyone in the previous stage** (without specifying who is who) | `audio_ref_seconds = 0`, **no voice anchor** (raw material = the previous stage's audio tail) | **1** (this pack's) |
| stage N reproduces the timbre of **the few people you supply yourself** (without specifying who is who) | feed their recordings into the **voice anchor** (`LoadAudio → VAEEncodeAudio → voice_anchor`), `audio_ref_seconds = 0` ⇒ auto-length by the **voice anchor** | **1** (this pack's) |
| stage N reproduces **one specified person** | the official `ref_audios` slots + prompt `<Audio j>` | official slots |
| stage N's timbre **pinned** to a person, and governing the seam region | the voice anchor `voice_anchor` (same as the previous row; the 0.925 s pinned at the seam region is unchanged) | **1** (this pack's) |

🔵 **The voice anchor = the route of "material you specify"**: the reference window's raw material is **the audio you connect** (since 0.6.27 `0` = auto-length by the voice anchor itself).
⇒ if you want "one anchor, many people" without depending on who is in the previous stage, **connect a multi-person recording into the voice anchor**.
⚠️ The anchor path **takes the full cap** (`AUDIO_REF_MAX_S` = 6 s ceiling) — the longer the single-person reference, the better the timbre is preserved; for longer you can only **explicitly** fill `audio_ref_seconds`.
⚠️ The part pinned at the seam region **= the video pinned window** (`context_frames`, default 22 frames ⇒ 0.925 s, adjustable 5/22/39/…/124),
independent of the reference window length ⇒ lengthening the reference window **does not cover more** of the pinned prefix.
⚠️ Conversely: **increasing `context_frames` ⇒ the seam region grows ⇒ the voice anchor must be longer too** (the anchor must be ≥ the seam region's seconds).

#### 4. Budget ledger: when you must free up, and which to free

| Fact | Value |
|---|---|
| Official `ref_audios` slot cap (**UI layer** convention) | **3** |
| Does the model layer check the count | **no check** (`refs` accumulates variable-length, no truncation) |
| This pack always occupies | **1** (`stage_index ≥ 1`; stage 1 occupies none) |
| Does the appearance anchor occupy the audio budget | **no** (`kind="video"`, goes through the image channel) |

⇒ **Ledger** (`stage_index ≥ 1`):

| How many people you want to "name" | Official slots to connect | Model-side total | Out of bounds? |
|---|---|---|---|
| 0 (only this pack's 1, one anchor many people) | 0 | **1** | ✅ |
| 1 | 1 | **2** | ✅ |
| 2 | 2 | **3** | ✅ exactly at the official cap |
| 3 | 3 | **4** | 🔴 **exceeds the official convention, we have not measured it** (the model does not error, but do not treat it as official behaviour) |

**Order of freeing budget**:
1. first drop characters **who do not speak in this stage**;
2. then drop **crowd/background voices** (prompt description is enough, timbre not preserved);
3. **only at the very end touch "the first person to speak in this stage"** — that is the seam region, dropping it causes direct timbre contamination.

⚠️ **This pack's 1 cannot be freed** (no "completely off" switch) ⇒ you can only reduce the official slots. Stage 1 (`stage_index = 0`) is not subject to this limit.

#### 5. Degradation path when character overlap is low (sacrifice order)

The more characters and the lower the overlap per stage, the less timbre can be preserved. **Fixed sacrifice order**:

```
① the first person who speaks at the seam   ← always first priority (protected by the voice anchor, never sacrificed)
② the other "speaking leads" in this segment ← protected by official slots (capped at 3)
③ crowd / background voices / one-liners   ← timbre not protected, described in the prompt
```

**The case where you can only choose one of two directions**:

| You care more about | Approach |
|---|---|
| **preserving the picture** (many people, complex action, afraid of breaking) | no voice anchor + `audio_ref_seconds = 0` (one anchor many people, no naming); leave the official slots empty ⇒ all picture information goes to the prompt |
| **preserving the timbre** (key characters must sound right) | the voice anchor preserves the seam region + official slots (≤2) name the key characters; **accept that the rest lose their timbre** |

**The preferred action is always "split into stages"**: switching speakers within a stage is hard in all three of **timbre / lip-sync / timeline** (see §Multiple speakers within one stage, the three rules above). After splitting into "one person per stage", everyone is "1 person · speaker switch at the seam" = row 2 of the decision table, the most budget-saving and the most stable.

#### 6. `--force` re-collection guide (old voice banks are not re-collected automatically)

`tools/voice_bank.py`'s anchors are **not re-collected automatically**: manual anchors always win, and an automatic anchor that already exists is skipped.

```bash
# ① collect (--force overwrites an existing anchor; --line enables the ASR dialogue guard)
python tools/voice_bank.py collect <stage output.mp4> --name <character name> --bank <voice-bank dir> --force --line "that character's dialogue text"
# ② sanity-check the written anchor: is the duration ≈ expected, is that character's `tail_clean` true in `voices.json`
# ③ lookup / self-test
python tools/voice_bank.py lookup <character name> --bank <voice-bank dir>
python tools/voice_bank.py selftest
```

- To **swap the material by hand**: put a wav directly into the voice bank directory named `<character name>.wav` (**manual anchors win**, no `--force` needed).
- The collection source mp4 can go anywhere; **the anchor file used by ComfyUI** must be placed in `ComfyUI/input/` to be selectable in the dropdown.
- ⚠️ **`tail_clean=false` is not a bug**: the anchor's tail 0.925 s must have sound throughout, and if the continuous speech in the material is too short it is necessarily discounted ⇒ swap in longer, cleaner single-person material to solve it (**raising the sample rate does not help**).

#### 7. Not yet measured

| Item | Status |
|---|---|
| **How to phrase `<Audio j>` and where to place it for the best effect** | 🔴 **no A/B measurement done** (README self-labelled). The two points — numbering and "unreachable" — are **confirmed by reading the code**, so they can be relied on directly; the phrasing is recommended at the corresponding `<Subject N>`'s definition / dialogue description (official *Use the same tags when prompting*) |
| **The actual behaviour of 4 audio references on the model side (beyond the official 3)** | 🔴 **not measured**. The model does not error, but **do not treat it as official behaviour** |
| **Whether the auto reference window (2~6 s) causes repetition** | 🟡 **covers only one clip, one generation**, no repetition observed; for dialogue-dense material listen yourself |

### Backstops

| Backstop | Behaviour |
|---|---|
| not connected / anchor missing | fully falls back to the old path (bit-exact unchanged) |
| anchor shorter than the window | takes the anchor's full length + `report` reminds "≥0.9 seconds recommended" |
| 🔴 **the anchor has no content** (all zeros / constant / silent) · illegal form · contains NaN | **raises directly** (the error is readable, it never silently pretends there is an anchor). The criterion = the tail window's **time-domain standard deviation** < `1e-2`: `MiniMaxH3AudioVAE.encode` returns a **normalized** latent (`(z−mean)/std`) ⇒ "does it have content" is judged by **variance**, not amplitude. ⚠️ **zero ≠ silence** — the origin of the normalized space is the VAE's **mean point** ("average timbre"). **To turn the voice anchor off, unplug the line; do not connect an empty latent** |
| clipping at the concat side | the assembly tool `--peak-cap 0.90` (pure linear gain, recommended value; a cap of 0.95 overshoots back to 1.0 through the assembly's AAC re-encode) |
| fixing loudness drift | use a **loudness-normalized voice anchor** (peak 0.9) — measured, the model follows the anchor's loudness |

> ⚠️ **The easiest foot to trip over**: connecting the **prompt-emitting node's `LATENT` output** to `voice_anchor`.
> That is the **empty AV latent** from `_empty_av_latent()` (all zeros), containing no speech — it is **formally legal** ([B,32,2,T]),
> so before 0.6.19 it **took effect silently** (the report even said "voice anchor in effect"). Since 0.6.20 it **raises on the spot** and points the way.

### Bundled voice bank (tools/voice_bank.py, optional)

Automatically collect voice anchors from already-rendered stages: sound detection (≥0.6s) + peak normalization 0.9 + a voice bank
`voices.json` (the first stable anchor is locked and reused; a manual `<character name>.wav` always wins).
Optional **ASR dialogue guard** (enabled only if funasr is installed): verify the dialogue CER ≤ 0.35 + word-level timestamps
to cut the anchor precisely — mispronounced/nonsense speech never enters the anchor; if not installed ⇒ falls back to the pure energy method, zero extra dependencies.

```
python tools/voice_bank.py collect stage_output.mp4 --name <character name> --bank ./voices --line "dialogue text"
python tools/voice_bank.py lookup  <character name> --bank ./voices
python tools/voice_bank.py advise  --bank ./voices --prompt-file seg2.md --prev-file seg1.md
python tools/voice_bank.py selftest          # self-test of the speaker-parsing rules (falsifiable)
```
