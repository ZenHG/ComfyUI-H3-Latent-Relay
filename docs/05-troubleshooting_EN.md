# Troubleshooting · Workflow self-check · API submission

<!-- EN-SYNC src=docs/05-troubleshooting.md stamped=2026-10-05 mode=see tools/en_sync.json -->

🌐 English translation of [`docs/05-troubleshooting.md`](05-troubleshooting.md). **The Chinese file is the source of
truth** — if the two ever disagree, the Chinese one wins.

> This file was split out of the README (0.6.0 reorganization).
> 📐 **Slot notation**: throughout this document `[N]` = **0-based** (the Nth port in the UI = `[N-1]`).
> The "route N" carried over from older drafts is 1-based legacy writing; it has been changed to `[N]` at the key points.
> The main README keeps only the conclusions and pointers needed for a quick read.

---

## Troubleshooting

| Symptom | Cause | Action |
|---|---|---|
| `不是 H3 的整步续接窗口` | The frame count is not on the 5+17k grid | Change it to a legal value |
| `context_latent 是 WxH，本段是 WxH` | The two segments differ in resolution | Unify the resolution and re-run |
| `起始于周期位置 N（非 0）` | The segment length makes the tail segment's start misaligned | Adjust the segment length so that `(total_steps - tail_steps) % 5 == 0` |
| `钉住 N 帧、本段只有 M 帧` | The window ≥ the segment length | Shorten the window or lengthen the segment |
| `要裁 N 帧，但本段只有 M 帧` | `trim_frames` was mistakenly filled with the whole segment length | Connect the bridge's `[2]` output; do not fill it in by hand |
| The log shows `⚠ 未接 audio` | The Trim AV node has no audio connected | Connect `VAEDecodeAudio`'s output to its `audio` |
| ~0.9 s replays at the seam | The Trim AV node is not connected | Following the wiring diagram above, put `🔗 H3 Relay · Trim AV` before VideoCombine |
| A 1-frame hard jump at the splice / the previous segment's subtitle text bleeds into this segment's head | The "reproduced frames" after the pinned region were not trimmed (the switch point differs per segment) | **Handled automatically by default since 0.3.0**, no parameter change needed; the log writes `裁首 N 帧 = 钉住 X + 沉降 Y` |
| Several segments run in a row but every segment's dialogue is identical | `prompts` is not filled in | Fill `prompts` on the Chain (`---`-separated blocks, the k-th block feeds the k-th segment), then click ⏩; `tools/check_ui_workflow.py` checks whether there are enough blocks |
| Clicking ⏩ to run a chain, nothing queues after the first segment, and status says "segment k has no matching prompt" | The number of `prompts` blocks < `segments` | Add the missing k-th block, or set `segments` to ≤ the block count (**the previous block is deliberately not reused silently**, so you do not think the prompt changed) |
| The prompt is not written in, and status says "automatically detected N candidates" | The graph has several writable prompt slots (several `prompt` / several `h3_data`) | Fill `prompt_target` with the node id (open the "node ID badge" in the settings to see ids), e.g. `683` or `683.h3_data` |
| Clicking 🧩 to concatenate into one, it reports "not a single segment video file found" | There is no usable segment record this round, and `segments` is not a positive number | Run one round with ▶/⏩ first and then concatenate; or set `segments` to a positive number; or confirm the graph has a save-to-disk node such as `SaveVideo` |
| Concatenation reports "segment k's audio is 0.X s longer than its video ⇒ most likely the audio line bypassed TrimAV" | The save node's `audio` is connected to `VAEDecodeAudio` (untrimmed raw audio) | Re-wire to "TrimAV `[1] audio`" (or through "audio seam `[0] audio`"), re-run that segment, then concatenate |
| After concatenating, each seam has ~0.87 s of still picture / the sound shifts forward overall | You concatenated with `ffmpeg -f concat` or `acrossfade` | Use the pack's 🧩; if you must use an external tool, use the concat filter with re-encoding and **must** self-check frame-count conservation + A/V difference ≤ 1 frame |
| **The finished picture is all correct but the audio track is the wrong segment** (segment 1 plays segment 2's sound) | 0.6.17 and earlier: the chain's stage advance **missed "Audio Seam"** ⇒ it overwrote the previous segment's bed file `audio_00000.safetensors` with this segment's audio ⇒ at concatenation the previous segment picked that up | Upgrade to **0.6.18** and **refresh the browser page** (the front-end fix is in `web/`); for an already-broken film, check against the "segment k source PCM" line in the concatenation report and use `tools/concat_segments.py --pcm` to explicitly name each segment's sidecar and re-concatenate |
| **The concatenated clip order is scrambled / segments missing** | 0.6.17 and earlier: segment records lingered across runs, and before concatenation the sparse records were **compacted** (the stage-number→position mapping was lost) ⇒ later segments were treated as earlier ones | Upgrade to **0.6.18** + refresh the page. New behaviour: automatic concatenation **refuses outright** on a hole and states which segment is missing; manual concatenation is allowed but the report lists which segment is missing and which came from an earlier run |
| Finished segment lengths differ by one or two frames | The automatic mode measures a different settle amount per segment | For equal lengths, fill `settle_frames` with the same positive number (e.g. 9) across the film |
| The log writes `未检出显著切换点` | This segment's switch point is inside the pinned region, or the picture is too flat/blurry | Normal; treat it as trimming the pinned region only; if it still jumps, fill `settle_frames` by hand |
| The log writes `但位置超出自动沉降上限（12 帧）…不动刀` | This is this segment's own cut, not the seam | Ignore it; if you really want to trim, fill `settle_frames` by hand |
| Two continuation schemes fight in the picture | Upstream pixel continuation is not turned off | Clear the upstream cont / reference-video fields |
| The dialogue onset is trimmed / the seam has "unexplained pseudo-sound" | The dialogue was placed in the continuation segment's head; or the audio transient was not patched | Move the dialogue later per the "dialogue-safe moment formula"; connect 🔗 H3 Relay · Audio Seam and set `patch_seconds` to 2.0 (patched **inside the node** since 0.5.0) |
| **`stage_index=N 表示本段是第 N+1 段，但没有可续接的上一段`** | When editing the stage number by hand, only the bridge or only the save was changed; or you switched films without filling `run_id` | The two `stage_index` values must be equal and `run_id` identical on both sides; if it really is an independent segment, set the stage number back to 0 |
| **Not a single `🔗 H3 Relay` in the node list** | The environment's `torch` is missing/broken (this pack imports torch at the top level) | Look for this pack's IMPORT FAILED in the ComfyUI startup log; repair the Python environment's torch |
| Nodes register but continuation has no effect at all, and the log shows "upstream node file not found" | The host ComfyUI is too old and has no MiniMax-H3 support | Update ComfyUI to a version with MiniMax-H3 support (including `comfy_extras/nodes_minimax_h3.py`) |
| **After upgrading, a node's signature is unchanged** (e.g. `H3RelayTrimAV` in `/object_info` still has only 3 outputs and no `prev_tail`), yet the on-disk source is clearly the new version | 🔴 **There is a second copy of this pack in `custom_nodes/`** (typically a `ComfyUI-H3-Latent-Relay.bak-<date>` left by syncing) — both carry `__init__.py` ⇒ **ComfyUI loads them all**, and the node definition is **overwritten** by whichever loads last. ⚠️ Officially only directories **ending in `.disabled`** are ignored (`nodes.py`'s `if module_path.endswith(".disabled"): continue`); **`.bak` is not recognised** | Move the non-active copy **out of `custom_nodes/`** (or rename it to end in `.disabled`) and restart the backend. Self-check: `python tools/review_050.py`'s **J3** section scans out same-pack copies |
| The log shows `上游时序网格已变` | After a ComfyUI update, the H3 grid does not match this version | File an Issue in this repository per the prompt; do not use continuation until a fixed version is released |
| **In an example graph this pack's nodes show a row of empty input dots** (official nodes are fine), yet the feature is not actually broken | The workflow file's `inputs` lack the `{"widget": {"name": …}}` marker. The front end's `nonWidgetedInputs()` **renders inputs without that marker as ordinary slots**; `widgetInputs.ts`'s `onGraphConfigured` **only removes, never adds**, and will not fill it in for you | Regenerate with the current version: `python examples/make_minimal_workflow.py`. Self-check: `python tools/check_ui_workflow.py <file>` (this criterion was added 2026-09-19; see `CHANGES.md` 0.5.0 "example-template fix") |
| **A/V drifts further out of sync the longer it runs** (lip-sync clearly off from segment 3 on) | The save node's `audio` is connected to **`VAEDecodeAudio` (untrimmed raw audio)**, while the picture goes through "Trim AV"'s trimmed frames ⇒ ~0.9 s off per seam, accumulating segment by segment | Re-wire `audio` to "TrimAV `[1] audio`" (if an audio seam is used, connect its `[0] audio`). Self-check: `python tools/check_ui_workflow.py <file>` — **this criterion was added 2026-09-21** (a node can only detect "an input is unconnected", not "an output is left dangling", so a tool must check it) |
| **Clicking `▶ Run` / `⏩ 连跑` is blocked, and status says "the same group's `run_id` is inconsistent"** | Since 0.6.19 it **no longer picks one automatically**: if the six cells hold ≥2 different names (the mistaken edit would drop segment files into the wrong directory, which is far harder to trace than an error) | Fix them per the "which node holds which name" list in status; in fact **editing any one of them makes the rest follow automatically**, so the fastest way is to clear the extra value |
| **You changed `run_id` and found some node did not follow** (that node still uses the old directory ⇒ it reports "cannot find the previous segment's file") | It is **not in the same group box** as the one you changed (sync takes effect only within the group, so as not to wrongly change another film on the same graph); or the front end was not refreshed (still running the old JS) | **Refresh the browser page**; put this group's six nodes into the **same group box**; when there is no group box the sync scope is the **whole graph** (status states this with ⚠). If unsure, fill all six by hand to be equal — when they disagree, clicking those four buttons is blocked and lists them |

> ⚠️ In the table above, the row **`stage_index=N 表示本段是第 N+1 段，但没有可续接的上一段`** is the **hard block added in 0.2.1**.
> Older versions **silently passed it through as an independent segment** in that situation — the UI showed all green, yet the output was a seam with no continuation, discoverable only by visual inspection. Now it errors directly.

## 🔧 Self-check for a corrupted workflow file (bundled tool)

A workflow's `widgets_values` map to the front-end widget slots **by position**. When generating a UI
workflow with a script, if you miss the slot the ComfyUI front end **injects automatically** (typical:
a `seed` with `control_after_generate: True` gets an extra dropdown after it), every value after it
shifts one position — the file opens and submits fine, but the sampling parameters are all wrong and
**nothing errors** (measured: `SelfLiftH3Sampler`'s `w_max` receives a file name and `upscaler_model`
receives `false`).

The pack ships a health-check script that validates values position by position against the real schema:

```bash
python tools/check_ui_workflow.py --all
python tools/check_ui_workflow.py /path/to/ComfyUI/user/default/workflows/xxx.json
```

**The schema comes from two places** (since 2026-09-19): **this pack's 8 nodes are now read from the
in-pack `nodes.py`**, and only the other nodes go through the running ComfyUI's `/object_info`; it also
reports "the definition the server loaded disagrees with the code". **A server that cannot be reached is
not fatal**: this pack's nodes are still validated, only the non-pack nodes are marked "not checked".
When ComfyUI is not in the default location use `--comfyui /path/to/ComfyUI` or the environment variable
`COMFYUI_PATH`.
(Criterion: looking only at the server, a **backend that has not been restarted** would make it judge a
new file with an old schema ⇒ a false report or a false green.)

🔴 **Dynamic COMBOs (such as `SaveVideo`'s `format` / `codec`) must be checked too**: after such an
input is selected it **derives a sub-widget** (e.g. `format=auto` → `format.codec`), so the front end's
actual slots are `filename_prefix` / `format` / `format.codec` / `codec` — 4 in total. This pack's
generator and checker both expand the sub-parameters via the schema's `options[key].inputs`, and **do
not** drop a dynamic COMBO as an ordinary wire — this was the worst self-proving false-green spot fixed
on 2026-09-19 (one whitelist entry missed, `want`/`got` missed together ⇒ even a wrong graph reports 0).
Regenerate the example graph `examples/minimal_relay_official.json` with the current version to align
it with the front-end archive position by position.

## 🤖 API submission (script / programmatic)

This pack's 8 nodes **can all be submitted through the `/prompt` API** (the same set of nodes as the UI
graph, with no UI-only logic). Three routes:

1. **Front-end export**: ComfyUI menu "Workflow → Export (API)" gives the API format (each node is
   `{"<id>": {"class_type": ..., "inputs": {...}}}`, with inputs keyed by **named parameters** rather than
   `widgets_values` order).
2. **Programmatic construction**: every input's name/type/default can be queried from `GET /object_info`
   (read key by key, e.g. `oi["H3RelayPost"]["input"]["optional"]["settle_auto"]`) — just fill in by name,
   and write a wire value as `[source node id string, output index]`. Example:

   ```json
   "904": {"class_type": "H3RelayPost",
            "inputs": {"images": ["903", 0], "settle_auto": 1.0, "baseline": "robust"}}
   ```

3. **Validation**: before submitting, run `python tools/check_ui_workflow.py your_graph.json` — this pack's
   nodes are validated position by position against the **local definition** (works even if the backend has
   not been restarted), so shifted values / missing slots surface here.

⚠ Two disciplines: ① folded `advanced: True` parameters **still take part in API submission** (folding is
display only); ② for a version upgrade that changed node parameter names/order, an old API script must
first reconcile against `object_info` (all breaking changes in this pack are recorded in `CHANGES.md`).

---

## Troubleshooting table (addendum: former README §9)

> The following two sections were moved in from the README (0.6.15 slimming); the content is verbatim identical to before the move.

| Symptom | Cause | Action |
|---|---|---|
| Stage number ≥1 but it errors that it cannot get the previous segment | `run_id` inconsistent / the previous segment was not written to disk | **All six** `run_id` values character-identical (on the canvas, editing one auto-syncs the rest of its group); confirm segment 1 has run |
| Not a single node in the node list | The ComfyUI you installed is not the H3-enabled one, or the backend was not restarted | Update ComfyUI + restart the Python process. **Two-step diagnosis**: ① check whether the startup log has `[H3 Relay] v… 已加载｜节点 N 个（出口 …）｜时序契约 …` — **no such line = the pack did not load at all** (most often because it is nested two levels deep under `custom_nodes/`); ② if the line is there, run `curl -s 127.0.0.1:8188/h3relay/health` to get in one shot the version / exit mode / registered node count / timing contract / **whether the host has H3** (`host_h3`) / upstream deps / Python+torch / OS |
| Continuation "looks like it has no effect" | The ComfyUI version has no H3 support | Confirm `comfy_extras/nodes_minimax_h3.py` exists. The startup log has **a separate line** with an ❌ explanation (`[H3 Relay] ❌ 宿主没有 MiniMax-H3 支持…`), and `/h3relay/health`'s `host_h3` will also be `false` |
| The finished film replays the previous segment at the seam | TrimAV's `trim_frames` is not wired to bridge `[2]` | Add the wire per "4. Wiring" |
| **A/V drifts further out of sync the longer it runs** (lip-sync clearly off from segment 3 on) | **The audio line does not go through "Trim AV"**: `VAEDecodeAudio` is wired straight to the save node, so the picture is trimmed but the audio is not ⇒ ~0.9 s off per seam, accumulating | Re-wire the save node's `audio` to `TrimAV [1] audio` (or through `AudioSeam [0] audio`) |
| **Several segments run in a row but every segment's dialogue is identical** | `prompts` is not filled in (a chain's default behaviour is **to submit the same graph over and over**, changing only the stage number ⇒ the prompt does not change) | Fill `prompts` on the Chain (`---`-separated blocks, the k-th block feeds the k-th segment) then click ⏩ to run the chain; for a single segment, editing the prompt by hand also works. ⚠ **If you submit JSON from a script**: you must also connect `Chain.prompt` to the prompt node's `prompt` input (a script cannot reach that old canvas route, see `docs/10` §7.4) |
| After concatenating into one film each seam has ~0.87 s of still picture / the sound shifts forward overall | You concatenated with `ffmpeg -f concat` or `acrossfade` | See the concatenation method + self-check in `docs/10` §7.1 |
| Concatenation reports "segment k's audio is 0.X s longer than its video ⇒ most likely the audio line bypassed TrimAV" | The save node's `audio` is connected to `VAEDecodeAudio` (untrimmed raw audio) | Re-wire to "TrimAV `[1] audio`" (or through "audio seam `[0] audio`"), re-run that segment, then concatenate |
| The concatenation report says a segment's "audio source = AAC" | That segment has no PCM sidecar (old graph / `save_pcm` off / this submission did not run "Trim AV") | For full audio quality: confirm the graph's "Trim AV" has `save_pcm` on and that the audio comes out of it, re-run that segment and concatenate |
| 🔍 The upscale node reports "you must install the author's node pack (MIT) first" | `Comfyui_Minimax_h3_latent_Upscaler` is not installed (this pack is only its adapter layer and does not copy its code) | Install the pack per §2.1 + download the weights from the author's HF repository, restart the backend |
| 🔍 No weights in the `model_name` dropdown | The weights are not in `ComfyUI/models/latent_upscale_models/`, or were placed there without a restart | Download from <https://huggingface.co/LBH-123-AI/Minimax_h3_latent_upscaler> then **restart the backend** (the list is scanned once at load) |
| 🔍 After raising `chunks` **the picture changed** (not just lower VRAM) | The upscale model has **3D volumetric attention**: tiling cuts the cross-tile temporal context, and the `overlap` gradient can only ease it, not cancel it. Measured with the same seed and parameters, `chunks=1` vs `4`: per-frame MAE **5.62/255**, 0 of 192 frames identical (peak 1483→1120 MB) | **`chunks=1` is the only path consistent with upstream "whole-segment inference"**; raise it only under OOM, and treat it as "quality traded for VRAM" and re-inspect visually |
| After turning on `lowfreq_pull`, **every segment waits 20-odd seconds** | Box-blur time **∝ radius²**, while `lowfreq_blur` defaults to **64**: measured 2.07MP/12 frames **22.1 s** (8.6 s at 0.80MP) — **strongly resolution-dependent**, and paid only when `lowfreq_pull` is on (off by default = 0 s) | Lower `lowfreq_blur` to **16–32**: same conditions measured **k=32 → 5.6 s · k=16 → 1.6 s · k=9 → 0.6 s**. The quality cost is tiny — measured seam step 32/64 = **0.0011 vs 0.0024** (`docs/02`) ⇒ **at high resolution the default should be lowered** |
| After choosing `pcm_lossless`, **no sound plays in the browser** | The finished film's audio track is PCM f32, and browsers do not play PCM | This is the expected behaviour of the **master track**: for editing/archiving. For previewability use the default `aac_256k` |
| The log has "⚠ PCM 边车写入失败" | Disk full / directory not writable / safetensors missing | **Does not affect this segment's output**; concatenation falls back to decoding automatically. Free up space or `pip install safetensors` |
| The log **no longer has** 🧪 DTW / trim-amount→jump curve / appearance triples | "Trim AV"'s `diagnostics` is **off by default** (the three passes are print-only and take no part in trimming) | Turn `diagnostics` on if you want them |

---

---

## FAQ (addendum: former README §10)

> All are questions actually asked, with answers carrying **measured numbers**.

### 10.1 Does an automatic chain use more and more VRAM, and will the hardware run out later on?

**VRAM does not accumulate.** Each submission is **an independent execution** (a chain merely "queues
again"), and the model is re-staged every segment — measured across 4 segments, the staged amount per
segment is **exactly identical** (`MiniMaxH3 21099MB / TEModel 17034MB / VideoVAE 2677MB / AudioVAE 576MB`).
**What does accumulate is disk**: `output/relay_kit/<run_id>/` ≈ 12 MB/segment (latent 8.9 + audio 4) +
PCM sidecar ≈2 MB/segment (can be turned off) + the segment mp4 itself ⇒ **about 25 MB/segment, 100
segments ≈ 2.5 GB**; archiving by `run_id` is recommended.

### 10.2 Does picture quality get worse the longer it runs?

**It does not degrade automatically just because it "runs long"**: the inter-segment relay is a
**bit-exact latent tensor hand-off**, not going through pixels and not re-encoding, so there is no
"decode→re-encode" generational accumulation; each segment is VAE-decoded only once.
But **cross-segment appearance drift does exist**: measured on a 4-segment chain's appearance log,
segment 2→segment 4 brightness mean `0.3697 → 0.3107` (≈ −16%), std −0.008, sharpness +0.0007 (the last
two are non-monotonic).
To address it, turn on `H3RelayPost`'s `match_prev` / `lowfreq_pull`
(⚠ at high resolution first lower `lowfreq_blur` to 32 — the default 64 costs 22 s/segment at 2MP, see
the "Troubleshooting table" in this file).

### 10.3 With the PCM sidecar, does A/V sync still hold?

**It holds, and it is more stable than the old route.** The sidecar is "Trim AV"'s **post-trim** audio —
**the same cut** as the picture it outputs, naturally equal-length and from the same source.
At concatenation time it **exactly trims/pads** per `want = this segment's frame count ÷ fps × sample
rate` (longer ⇒ trim the tail, shorter ⇒ pad silence and warn)
⇒ each segment's audio length == its video length, with **zero accumulated drift** between segments.
Measured on 4 segments / 702 frames: four segment landing points at **0 samples**, A·VΔ **0.0011 s**;
the `pcm_lossless` track is **bit-exact** with the sidecar.
⚠️ Boundary: the sidecar aligns the **clip's effective duration**, not subtitle-level sync — what it
guarantees is "the seam does not drift".

### 10.4 When not using automatic concatenation (wiring nodes by hand only), does the sidecar affect other flows?

**Purely additive; not one byte of the segment files changes.** The sidecar merely **writes one more
file** (≈2 MB/segment); if you do not click "🧩 拼成一条" and do not turn on `auto_concat`, the backend
concatenation route is never called.
**External tools (ffmpeg, your own assembly script) read the mp4 and ignore the sidecar.**
To not keep the sidecar: turn off `H3RelayTrimAV.save_pcm` (concatenation falls back to decoding
automatically and states it in the report).

### 10.5 Can I use MKV / a lossless audio track?

**You can store MKV** (the host `SaveVideo`'s `format` offers `mp4/mkv/webm`), **but there is no gain**:
its audio encoding is hard-coded (`libopus if WEBM else aac`; `CreateVideo` also hard-codes aac) ⇒ **you
cannot get a lossless audio track from the host**.
Our concatenation goes through PyAV and the container is ours to decide, so measured **PCM
(`pcm_s16le` / `pcm_f32le`) writes straight into mp4** ⇒ the default is mp4 and the lossless track is
also mp4, **introducing no MKV dependency**.
(Measured: `flac` fails to write into mp4/mov/mkv every time and only goes into a native `.flac`, so the
lossless track is PCM, not FLAC.)

### 10.6 How long does concatenation take? Does it get stuck "finding segment files"?

**Neither takes time.** Measured on a 4-segment 29.25 s film: probing 0.12 s + concatenation **1.26 s**
(picture stream copy + audio re-encode) + the four assertions **0.019 s**.
**Finding paths does not scan directories or guess file names**: it only does a dictionary scan in the
`/history` ComfyUI already provides + `os.path.isfile` (microseconds, zero GPU, zero directory walking);
a missing file is reported **before encoding starts**.
**It will not hard-code the path to the official `output/`** — that would create the reverse "only runs
locally": a third-party save node using a "custom save path", a user starting with `--output-directory`,
a container mount — hard-coding would **fail outright**. This pack's convention = **trust only the path
the node itself reports** (`abs_path` first, and only when it is missing fall back to `type`+`subfolder`;
neither key names nor field names are hard-coded into a whitelist).

### 10.7 I configured a script in the node table, but clicking the button does nothing?

`app.queuePrompt` / `api.fetchApi` are **front-end internal interfaces**, and different ComfyUI versions
may have a different shape.
When they are missing this pack **says so explicitly** in the Chain's `status` cell (not a silent
failure) and points to a path that still works — see `docs/10` §7.4.

### 10.8 In the output, a "whole-frame equal-amplitude left-right sway" between segments — is that a pipeline problem?

**Usually not**; it is the **prompt layer's motion instructions contradicting themselves**: splitting the
same direction of displacement between non-equivalent degrees of freedom — for example requiring both
"the camera pans horizontally continuously" **and** "some scenery in the frame zooms in" (the latter is a
**push-in**).
The model picks back and forth between the two motion readings frame by frame ⇒ a whole-frame
equal-amplitude jitter (constant amplitude, no decay).
**The fix**: give a segment only **one** active motion; give a **speed reference** ("same speed as the
footsteps") or an **open end** ("until it never reaches a certain position"); always write the background
as a **result** ("slides out of frame behind him"), not as a zoom target; when the subject is not moving
in the same direction, do not write "moves together with so-and-so".

### 10.9 In the first pass → 🔍 upscale → second pass chain, why can the second pass's guider not connect the bridge's `conditioning`?

**Because frame pinning goes through `minimax_keyframes`, and the packer assumes the keyframe shares
this segment's target grid.**
After upscaling the target grid changes (e.g. 26×46 → 30×54 latent), while the bridge's anchor is still
on the native grid ⇒ they do not match at packing time, and the second pass explodes on its very first
step once the first pass finishes:

```
RuntimeError: shape mismatch: value tensor of shape [2392, 96]
              cannot be broadcast to indexing result of shape [3134, 96]
（comfy/ldm/minimax/model.py: all_video_rows[~img_update] = cond_video_rows）
```

**Wiring**: only the **first pass** (native domain) connects the bridge's 4th route to its `BasicGuider`;
the **second pass** connects the prompt node's `positive`.
The second pass still does not repaint the seam region — **the latent-side pinning (prefix copy + noise
mask) is grid-independent and still in effect**; what is given up is only the conditioning-side half,
the "framing keyframe pin".
For comparison: the appearance anchor goes through a different channel, `minimax_refs`, which **allows**
a different resolution from the target (`ref_anchor_latent` is exactly how it anchors across
resolutions) — do not treat the two channels as the same thing. Full wiring is in the whole-flow example
under [`examples/`](../examples/README.md).

### 10.10 Not enough VRAM but you want to keep detail — which sampling chain should you choose?

**First, what not to do**: do not **plug a progressive sampler (SelfLift etc.) straight into this
pack's bridge**. On a **continuation chain** that route costs two extra things — the low-resolution
prefix uses a **downsampled mask and anchor** (the true source is used only in the full-resolution
pass), so `transition_step` doubles as a seam-quality knob; and its "tiling to save VRAM" is **mutually
exclusive** with the noise mask ⇒ as long as the bridge is present (= there is a mask), the only VRAM
knob left is `lowres_scale`.
Mechanism and source-level reconciliation in
[`docs/03`](03-sampling-and-design_EN.md).

**This pack itself provides a third route** (`docs/03` §4.1's whole-flow layout is fully wired, all 8
nodes present):

| Your constraint | Which to choose |
|---|---|
| Enough VRAM, picture detail first | Full-resolution throughout: `SamplerCustomAdvanced` + euler, `guider` ← the bridge's 4th route |
| VRAM only enough for the native domain, but you do not want to accept the progressive chain's detail ceiling | **Two-pass**: first pass pins frames in the native domain → 🔍 upscale → second pass recovers detail at high resolution (`guider` ← the prompt node's `positive`, see §10.9) |
| Both VRAM and time are tight, or **the audio seam** matters more than picture detail | A third-party progressive sampler (this round's measurement found that chain's audio seam better), accepting the two extra costs above |

> Upscaling is a **delivery branch and does not enter the continuation contract**: `LatentSave` is connected **before** the upscale.

---

### 10.11 The finished film has no sound / only `<name>-audio.mp4` has sound?

It is first a **file name** issue, then a **version** issue:

1. **A different compositing node produces a different output file name**: the core `CreateVideo → SaveVideo`
   is a **single file with an audio track**; whereas `VHS_VideoCombine`'s **main file has no audio track** —
   the one with audio is the `<name>-audio.mp4` it saves separately
   — so hearing nothing when you open the main file is normal, not a bug.
2. **0.6.10 and earlier combined with `VHS_VideoCombine` mutes the whole track**: this pack's audio is fp16,
   while VHS hard-codes `-f f32le` when compositing audio and **does not convert dtype** ⇒ every sample is
   zero after encoding. **Upgrading to 0.6.11 fixes it** (this pack's AUDIO exit converts to f32
   uniformly). Symptom signature: the picture, report and log are **all normal**.
3. If you cannot upgrade for now: swap the compositing node for the core `CreateVideo → SaveVideo`, or
   `banzhangVideoCombine` (they convert to f32 themselves), to work around it.

---
