# Offline self-test: assertion-group detail and tool inventory

<!-- EN-SYNC src=docs/08-testing.md stamped=2026-10-05 mode=see tools/en_sync.json -->

🌐 English translation of [`docs/08-testing.md`](08-testing.md). **The Chinese file is the source of
truth** — if the two ever disagree, the Chinese one wins.

> This file was split out of the README (0.6.0 reorganization). The main README keeps only the commands and conclusions.
> 📐 **Slot notation**: `[N]` = 0-based.

## Unit tests (zero GPU, seconds)

```bash
python tests/test_relay_core.py
```

The script locates the ComfyUI root by walking up automatically; if it is installed elsewhere use
`COMFYUI_PATH=/path/to/ComfyUI python tests/test_relay_core.py`. `pytest tests/` is also supported.

⚠️ **`pytest tests/` vs running the scripts one by one** (measured 2026-10-07): the three ways now give
**identical results**. They used to differ — `pytest` runs these three script-style tests in **the same
process**, and both `test_relay_core` and `test_experimental` insert **this pack's own directory** into
`sys.path` (they load `nodes.py` offline) ⇒ a later `test_v3_schema` resolved its `import nodes` to
**our own copy** (a shadow module) ⇒ it once landed in **63/0 · 7 nodes**. **The root cause is fixed**
(`nodes.py::_comfy_registry` no longer lets an ImportError escape — see the "V3 field-by-field parity"
section), so the shadow module **no longer affects whether a node is present** and `pytest tests/` now
directly reports `72/0 · 8 nodes`.

**The assertion body is split by group** into `tests/cases/` (seven `_g*.py` files).
`tests/test_relay_core.py` keeps only the file header / root discovery / `check()` / **case execution** /
summary. The cases are run with `exec(compile(src, path, "exec"), globals())` **in order into the same
namespace** — the same globals and the same order as the single-file flat script before the split, so
fixtures shared across groups remain visible. ⚠️ Case file names start with `_`, so `pytest tests/`
does **not** collect them separately (otherwise every assertion would run twice).

**518 assertions, zero GPU, no model loading**, covering thirty-three aspects:

### Counting and declaration discipline (the two notes from former README §8, moved in with the slimming)

- **Write a concrete count only where a machine check watches it**: `test_relay_core` (H3f) ·
  `review_050` (H3g self-reference) · `test_v3_schema`'s node count · input count · item count (H3h) ·
  `assert_default_exit` (H3i). **Everywhere else write "0 failures"** — a hard-coded number **inevitably
  drifts** (measured 2026-09-25: the same number declared in four places, only one machine-checked, and
  the other two drifted for a long time unnoticed).
- ⚠️ **When writing a correction note, do not restate the old wrong number** — that pollutes the regexes
  of the machine checks above.
- This file's line "518 assertions / thirty-three aspects" + the `| 21 |` row of the table below are
  machine-checked by **H3c** (since 2026-09-30; after the README slimming the criterion moved from the
  README to this file).

| Group | Coverage |
|---|---|
| 1 | Timing-grid self-consistency (22 frames = 7 steps / 39 frames = 12 steps / 192 frames = 57 steps) |
| 2 | Tail-segment slicing is bit-exact + the start must fall on a 5-step period boundary |
| 3 | Hard errors: inconsistent resolution / off-grid frame count / window ≥ segment length → must raise |
| 4 | conditioning injection: keyframes merged, the pinned region's old anchor dropped, the audio ref appended |
| 5 | AV latent write-to-disk round trip is bit-exact |
| 6 | Head-overlap trimming: picture and audio trimmed together bit-exactly, duration aligned, trim=0 pass-through, out-of-range raises |
| 7 | Node return-value contract: each branch's return count == `len(RETURN_TYPES)` |
| 8 | Seam self-check `find_head_jump` / `describe_head_jump` |
| 9 | A contradiction between the stage-number declaration and the source must raise (no silent pass-through) |
| 10 | `streams_from_latent` must not split an ordinary tensor along the batch dim into a pseudo audio stream |
| 11 | The Chain's `status` slot matches the front-end JS (an older workflow with one slot fewer does not shift) |
| 12 | Settle frames: pin decoupled from crop, automatic detection of four scenarios, the trim node's three branches |
| 13 | Blur-type settle + the sharpness route's three quantities decoupled: a collapse wider than the old window but **with recovery** → should trim; no recovery stays 0 |
| 14 | Copy bridge: bit-level copy / mask structure / audio tail / cross-resolution / full prefix / phase mismatch / node contract |
| 15 | Tone-step convergence signal: detection at the observation end of noise injection / the taper's converging tail |
| 16 | 0.4.2 regression: the exported-audio branch / Chinese note / server-side validation / mask device / contract degradation cache |
| 17 | Noise ramp: soft-evidence seam — a continuous mask = a per-token sigma label |
| 18 | Reproduced-residue: a 4th detection method (not a slot; the existing three quantities cannot see the reproduced frames, **off by default**) |
| 19 | Cross-segment statistics matching: Reinhard-style first + second moment, segment head ↔ the previous segment's last frame; guard rails and no ghosting |
| 20 | Node split: `H3RelayPost` standalone + TrimAV gains the `[3]` output `prev_tail` |
| 21 | Overlap-region two-way fusion blend: window-shape weights (smoothstep / hann), zero derivative at both ends |
| 22 | Audio seam: length conservation, two bed-window selection modes, level alignment + peak guard rail, boundary blend, bed-loop tiling without steps, write-to-disk round trip, the node's two guards, `joined` concatenation composite |
| 23 | **A/V sync conservation (0.6.2)**: `joined` can only cross-fade when a picture trim amount is given (otherwise raise); equal-length by default + **zero timeline shift**; the J-cut conservation path's length formula; the `join_align_seconds` recommended value for TrimAV's direct output |
| 24 | **Bed-source window selection avoiding speech · all paths (0.6.4)**: the tiled mode (tile>0) must also pass the criterion with the threshold tightened to 0, E5 staggering is no longer silently ignored, the rank convention is consistent (2D/3D same point), when all sources collide it does not raise and falls back to the quietest window, off by default is bit-identical, the O(T²) regression lock, the report is not silent, persistent-frame filtering (transients excluded), degraded input does not explode, prefix-sum energy window selection == the reference |
| 25 | **🛡 patch dialogue guard (0.6.5)**: this segment's head dialogue onset ⇒ the patch shrinks automatically (onset−0.25s)/turns off; the dialogue region is bit-lossless; clean material has zero side effects; guard off = old behaviour |
| 26 | **Multi-segment concatenation into a film (0.6.7)**: `probe_mp4` container facts, the four checks (including the "audio bypassed TrimAV" criterion), stream copy is **bit-lossless**, per-segment audio de-priming alignment (the dominant-frequency criterion), fallback re-encoding, no output on empty input, history-entry filtering, the fast assertion path == the deep path, **the backend route's segment-discovery logic** (run offline with a stub server); **audio generations 2 → 1**: default AAC **256k** (192k available) / the PCM sidecar is **read directly ⇒ bit-exact with the film** / mixed tracks label each segment's source / a mismatched sample rate is discarded / a short sidecar is silence-padded with a warning / the producer echoes the ui key + pure-function retrieval + `save_pcm=0` degrades to the old return; **multiple audio-chain producers** (candidates ordered by the **submitted graph's data flow** — the more downstream, the higher the priority, following any wiring change; a tie among same-source candidates takes the first in the list; an unreadable graph ⇒ reject all sidecars; losing candidates are written into the report); **PyAV version compatibility** (missing template stream ⇒ an explicit error + automatic fallback to re-encoding); **the CLI entry point really runs** (`tools/concat_segments.py`: exit code 0 for a good film / exit code 1 for bad input); **0.6.18 segment order** (stage numbers not compacted, missing segments reported by which one, rescan ordered by true stage number) (synthetic mp4 + synthetic sidecars, zero GPU) |
| 27 | **Picture-domain AV latent tile upscaling (0.6.8)**: tile-count→tile-length conversion (ceil does not add an empty tile), seamless span coverage (first tile starts at 0 / last tile ends at T / adjacent tiles must overlap or tile exactly), `overlap=0` tiling, **the fail-closed rule of ≥ 2·overlap+1 per tile** (over the limit it errors immediately and gives the **maximum legal tile count**, never quietly rewriting parameters), **tiling == whole segment** (max\|Δ\| = 5.96e-08), `chunks=1` bit-identical to whole-segment upscaling, **not one frame may change along the time axis**, a wrong rank / wrong ruler always raises, upstream's convention constants (`overlap=5` / default tile length 32) must not drift; **5 adapter-layer items**: AV-packed latent unpack→per-tile→repack, the audio stream carried back untouched, NestedTensor input must not explode, report fields (requested tiles→actual tiles / T / peak VRAM / elapsed time), an actionable install guide when upstream is missing (no silent degradation). The fake upscaler = nearest×2 (per-frame independent ⇒ an analytic solution), zero GPU |
| 28 | **Audio-exit dtype contract (0.6.11)**: `audio_to_fp32`'s zero-copy fast path (already f32 ⇒ returns the original object) / fp16·bf16·f64 all converge to f32 bit-losslessly / keys and shapes untouched / `None` passes through; **the incident-reproduction lock** (verbatim simulation of `VHS_VideoCombine`'s `-f f32le` muxing path: the raw dtype going in must drop to <1e-6 = digital silence after encoding, while passing through this pack's exit restores it bit-exactly); the node-exit contract ("Trim AV"'s two return routes + "Audio Seam"'s two return routes both have f32 AUDIO, while **the on-disk PCM sidecar stays fp16**) |
| 29 | **Voice anchor `voice_anchor` (added 0.6.19 / content guard added 0.6.20)**: not connected ⇒ bit-identical (regression); both forms — a pure audio tensor (`VAEEncodeAudio`) and an AV composite (NestedTensor, taking the 2nd stream) — take the tail window; `audio_ref` and the "pinned audio prefix" are **the same source** (changing only one causes a conflict); an anchor shorter than the window ⇒ the full length is used + a `report` reminder; `pin_audio=False` ⇒ ignored and stated; 🔴 **content guard (0.6.20)**: if the tail window contains `NaN/Inf`, or its **temporal standard deviation < `1e-2`** (a constant field = silence/empty anchor) ⇒ **raises immediately** (the message names "all zero" / "constant"), and **real scales are not caught by mistake** (reverse self-proof: it still passes near the measured lower bound). The criterion is based on: in the normalized latent space, "is there content" is judged by **variance**, not amplitude; **zero ≠ silence** (zero is the VAE's **mean point**) |
| 30 | **Isolated-transient suppression declick (added 0.6.21)**: `ratio=0` ⇒ bit-exact pass-through (old graphs unaffected); a segment-head isolated peak and "a single isolated transient in a quiet background" ⇒ **that short stretch is replaced** (filled by a linear transition from the left/right neighbourhood, not pulled to 0 ⇒ no silent hole is dug; 🔴 **the neighbourhood must be an order of magnitude lower than the event** ("neighbourhood peak × 2 > event peak" ⇒ skip, do not touch) — that would be a real content edge / tail-off, including a **volume cliff** (music ending, a door, a scene cut; the old writing used the **order relation** "neighbourhood > event" ⇒ both sides of a cliff are the same magnitude ⇒ **about a 50% chance of mistreatment**, regression 30.31 / **30.31b** / 30.32); the dialogue region is **bit-untouched**; three shapes `[T]`/`[1,T]`/`[1,1,T]` are length-conserving with the shape restored; dtype fidelity (fp16 in, fp16 out); 🔴 **the scope gate = the whole segment** (since 2026-10-04; the old policy's premise "an isolated transient is always **before the dialogue**" was **disproved** by end-to-end measurement — of 17 isolated peaks in a 10 s clip, **15 fell outside the old gate and were all missed**) ⇒ **each policy has its own assertions** (the head fallback path 30.18–30.21 / the whole-segment default 30.21b–c; the fallback path must keep its assertions); 🔴 **the trim-frame boundary** (this node works on untrimmed audio while the output is trimmed) ⇒ the boundary must fade in, and peaks after the boundary **are still within scope** and get hit (the scope fallback counts from the **boundary** — the old convention counted from the head ⇒ at pin=22 frames it never reached the real culprit; locked by a reverse assertion); `limit_n=0` must not fail silently; all-zero ⇒ no event; 🔴 **node-layer policy** (`declick_gate`: dialogue onset / the 24fps trim-frame boundary / fallback compensation, moved in from a `nodes.py` closure on 2026-10-04 ⇒ from **zero coverage** to 6 assertions, the criterion reconciled verbatim against the host measurement); 🔴 **order with patch** (patch first then declick ⇒ content brought in by the bed source is also treated, whereas the reverse is both masked by patch and never seen by declick ⇒ locked by three assertions) |

| 31 | **Audio-reference budget (0.6.22)**: the official `ref_audios` 3 slots vs this pack **always occupying 1** (voice anchor / the previous segment's tail window, either-or) ⇒ at 3+1=4 the **report must name the over-limit** (it must never be silent about it); 2+1=3 hints "exactly full"; a `kind="video"` appearance anchor **does not consume the audio budget**; no official block ⇒ 0 and no warning. **(0.6.23 adds 31.5–31.8)**: the block this pack appends must state "**the text side has no `<Audio j>` tag ⇒ the prompt cannot reference it**" + its DiT-side **ordinal = official count + 1** (off by one means **pointing at the wrong place**) + state "the proper route for several people in one segment" (official slots / numbering by **wired order** / `TrimAudioDuration` window trimming); 🔴 **not injected for segment 1 ⇒ none of these lines may appear** (nothing done, nothing reported) |

| 33 | **Split contract (2026-10-07)**: `relay_core`'s **package identity** (it has `__path__`, its entry point is `__init__.py`) + the **full re-export** in `__init__.py` (every name in `__all__` is reachable from the package, no duplicates) + 🔴 **monkey-patch forwarding** (`relay_core.X = v` must be written to **every** submodule holding X): `_HOLDERS` is reconciled name by name against the real holder set for **all** exported names; one case for a **cross-module copy** (`FPS` has 4 holders) and one for a **single owner** (`SETTLE_REPEAT_PATH`); plus a **really-takes-effect** assertion (changing `GUIDE_RUNS` makes `snap_guide_run(35)` go from 22 to 5). ⚠️ With owner-only forwarding this group goes red (verified by a **mutation test**, so it is not a false green) |
| 34 | **`run_id` cross-node sync (2026-10-07, 0.6.31)**: the API / script-side implementation `collect_run_ids` / `resolve_run_id` / `plan_run_id_sync` / `sync_run_id`; 🔴 **cross-layer parity** (it is the same semantics as the front-end `web/relay_kit_sync.js`, i.e. two implementations ⇒ they share the single fixture `tests/parity/run_id_cases.json` and both sides must pass); rewriting does **not** drop the node's other input keys and does **not** mutate the caller's prompt in place; an explicit `value` rescues the all-empty case; already-consistent input is **idempotent** (no dict copy); all four names are reachable on the `relay_core` top level and listed in `__all__` |
| 32 | **Automatic audio-reference window sizing (0.6.25)**: `audio_ref_seconds=0` ⇒ **automatic** (accumulate 2 seconds of **voiced** content walking back from the previous segment's tail, cap 6 seconds) ⇒ **several speakers' timbre in one reference slot** (budget unchanged); 🔴 **the criterion must use the first difference** (the real path is a **VAE latent**, already normalized ⇒ judging by RMS always yields "voiced cells = 0"); 🔴 **the threshold takes P99, not P50 / max** (four-way comparison: `P50×2` is pulled down by the silent difference tail ⇒ the window drifts from the true 2.00 to **6.69 s**; `max×0.05` is pinned by **a single outlier pulse** ⇒ voiced cells **215 → 2**; Otsu grips too tight ⇒ only `P99×0.20` holds across all scenarios); the criteria are **all relative** (lowering everything by −40 dBFS leaves the window length unchanged); **lengthening a silent segment 8× leaves the window unchanged**, **injecting an outlier pulse leaves the window unchanged** (two 🔴 regressions 32.3 / 32.4); every degradation is observable: silence / peak<1e-6 / **NaN·Inf fail-closed** / **empty audio does not throw** (`reshape(-1,0)` would break the chain) / a cap point named; priority: `audio_ref_seconds` > `audio_frames` > automatic (32.12–32.15); with a voice anchor wired ⇒ automatic **does not intervene** and the report names it |

## Unit tests for front-end pure functions (run by node, zero dependencies)

```bash
node tests/test_prompt_dispatch.mjs      # expected 43/0
node tests/test_audio_ref_budget.mjs     # expected 45/0
node tests/test_i18n.mjs                 # expected 33/0
```

Covers the pure functions in `web/` that **run the same copy as the browser**, plus two sets of **static
scans** (`relay_kit_chain.js` depends on the browser and cannot be unit-tested ⇒ scanning catches silent
failure):

- `relay_kit_prompt.js` — `---` block boundaries (two dashes do not count as a separator / CRLF / empty
  blocks dropped), collecting segment records by stage number ("re-running the same segment must not
  count as two segments in the film", and **holes are never compacted**);
- `relay_kit_refs.js` (added 0.6.22) — **the official 3 audio-reference slots vs this pack always
  occupying 1**: slot-name recognition (🔴 must recognise Autogrow's prefixed form `ref_audios.ref_audio_N`
  — not recognising it is **a silent failure with the count stuck at 0 and the warning never appearing**),
  a three-state verdict (over-limit / safe / **unknown**, where unknown must not degrade to 0), an
  **idempotent** title suffix, plus a static scan for "a pure module must not import `app` / the shell
  must not rewrite the criterion itself";
  **(0.6.23 addition)** the audio slots' **"wired order" numbering** (`<Audio j>`): the j in `<Audio j>`
  **is not the slot number** but **the j-th wired slot** (the host's `_io.py:1198` sorts by ascending
  index and takes only slots with a value; `execution.py:169` passes the user-submitted `inputs`) ⇒
  wiring only `ref_audio_2` makes it `<Audio 1>`, and writing `<Audio 3>` **resolves to nothing without
  erroring**. Assertions cover: the ordinal mapping (`inputs` shuffled must still sort by ascending slot
  number), **speaking only when it would deceive** (contiguous full wiring ⇒ empty string, no noise
  produced), not forcing a hint in the unknown state, and **idempotent stripping** under four suffix
  combinations;
- `relay_kit_sync.js` (added 0.6.19) — `run_id` sync's **three-state verdict / broadcast planning /
  conflict wording**, and **scans `nodes.py` to check whether that node table is complete**. A
  hand-written list misses a category, and the missed category is a **silent failure** — exactly the
  disease of 2026-09-30 "the stage advance missed the audio seam ⇒ the film's A/V was the wrong segment";
- `relay_kit_i18n.js` (added 0.6.28) — the word table and rewrite logic of the canvas **Chinese/English
  toggle**, guarding three **silent failures**: (1) changing `name` instead of `label` (= modifying the
  graph: dirty workflow, misplaced links); (2) replacing the whole `title` ⇒ wiping the budget hint that
  `relay_kit_refs_ui.js` appends (**only the prefix is swapped, the suffix is kept**); (3) guessing a
  translation for a **value outside the enum whitelist** (`model_name` is a file name, a stage number is
  a digit ⇒ translating them means the file can no longer be found). Also: custom titles must not be
  overwritten · an empty title gets the translation · idempotence · **bilingual tooltips (`H3_TIPS`,
  keyed by `class_type.parameter`, keeping only the core sentence + the recommended value)** ·
  word-table shape self-check (empty entries / zh==en / all eight nodes present).
  🔴 **Word-table completeness is not this layer's job** (that is Python's): `review_050.py`'s
  H3n/H3p items check the keys of `nodes.py`'s `INPUT_TYPES` against the tables ⇒ **a new parameter
  without a label or a tooltip fails at the machine-check layer**. The tooltip is written to
  `widget.tooltip` (the cell the front end reads first, and one that serialization never writes), so
  sharing a workflow carries no translation and other people's graphs are unaffected;
- static scan: the phase table ↔ the `say()` phase string, **whether every submitting button passed the
  `run_id` gate**;
- **assertion-count self-reference**: the "expected N/0" written in `ci.yml` and in this document must
  equal the real run count — a number written in three places will inevitably drift (this repository saw
  it on 2026-10-02: a threshold table drifted for 7 days unnoticed).
  ⚠️ **Since 0.6.23 the criterion is "three-way equality"**: the declared value == the constant in the
  file == **the pass count this file actually runs right now**.
  Comparing only "declared == constant" would miss the false green of **changing an assertion but
  forgetting the constant** — after hardening, one was caught on the spot (an assertion added, the
  constant not changed).

> Since 0.6.15 the old "the front end writes the prompt for you" route has been **deleted wholesale**
> (prompt dispatch became an output-port capability of the Chain node)
> ⇒ that batch of functions (`detectPromptTargets` / `resolveTarget` / `writePrompt` / `describeTarget`)
> and their assertions were removed together — so this page no longer has the two items "prompt-slot
> detection / the three `prompt_target` spellings".

## V3 shell and default exit (zero GPU, seconds)

```bash
COMFYUI_PATH=<root> python tests/test_v3_schema.py      # expected 72/0 (8 nodes / 122 inputs; same tier with or without the host registry)
COMFYUI_PATH=<root> python tools/assert_default_exit.py  # expected 5/5
```

`test_v3_schema.py` verifies **V3 and V1 are field-by-field identical**: node id sequence and order /
values / `optional` / the **total order** of Combo `options` / output count and display names /
`is_output_node` / display names / category.
Why a machine check is mandatory: an old workflow's `widgets_values` are stored **by position**, so one
wrong slot in the parameter table means **the user's parameters are silently shifted**.

> 🔴 **Why there is now only one number** (first measured 2026-09-25 / mechanism corrected 2026-09-30 /
> **converged 2026-10-07**): the one differing node is `H3RelayLatentUpscale`, whose `INPUT_TYPES()` asks
> `_comfy_registry()` for the **host** node registry so it can look up the upstream pack (the author's
> MinimaxH3LatentUpscaler3D):
> · Registry **obtainable** ⇒ upstream is not in it ⇒ `_upscaler_cls()` raises **RuntimeError**
>   ⇒ `_upscaler_module()` **catches it** (by design) ⇒ falls back to `get_filename_list`
>   (the host registers `latent_upscale_models` itself) ⇒ does not raise.
> · Registry **unobtainable** (host `nodes` cannot be imported / is shadowed by this pack's own directory)
>   ⇒ it likewise converges to **RuntimeError** ⇒ likewise swallowed ⇒ **the node is still present**.
> ⇒ Both environments are **8 nodes / 122 input / 72 items**, hence **one number**.
>
> 🔴 It was not like this before 2026-10-07: if the `import nodes` inside `_comfy_registry()` raised
>   **ImportError** it would **propagate** (`_upscaler_module()` only catches `RuntimeError`) ⇒
>   INPUT_TYPES raised ⇒ the V3 shell's per-node fault tolerance **silently skipped that node** ⇒
>   **7 nodes / 63 items**. That "third state" only occurred under the **artificial hard block**
>   `sys.modules["nodes"] = None` ⇒ what `tools/ci_env_repro.py` reproduced was **never the state CI
>   actually enters** (CI raises RuntimeError, which is swallowed ⇒ 8 nodes).
>   **The old docs wrote an artificial state up as "CI's environment", which was wrong** — fixed in
>   `nodes.py::_comfy_registry` (**scan `sys.modules` first**, then `import nodes` with every exception
>   caught ⇒ all failures converge to RuntimeError, the one this function declares).
>
> ⇒ The criterion is still answered by **the node's own `INPUT_TYPES()`** (the test's `2.1`), **not**
>   inferred from "can the host nodes be imported" — the latter reaches the **opposite** conclusion
>   (measured 2026-09-30: the probe said "expect 7", the actual was 8 ⇒ a false red).
> ⚠️ **Do not simulate "no registry" by "just removing the upstream node from the registry"** — that
>   reproduces "host installed, upstream pack not installed", which **is still 8 nodes**. To reproduce
>   "no host registry": set `sys.modules["nodes"] = None` then run the test (tool version
>   `tools/ci_env_repro.py`) ⇒ **also 72/0 · 8 nodes**.
> This set of numbers is machine-checked by `tools/review_050.py`'s **H3h** (semantics = "the current
> environment's true value must appear in the declared set").

`assert_default_exit.py` locks **"the default exit = V3" itself** (5 items: **2 contract items** =
`NODE_API_DEFAULT == "v3"` (the *fact* of the source default) + **the node set registered by V3's
`get_node_list()` == the keys of `nodes.py`** (the only tolerated miss = `H3RelayLatentUpscale` skipped
because its own `INPUT_TYPES()` raised); **3 behaviour items** = `NODE_CLASS_MAPPINGS is None` /
`comfy_entrypoint` callable / `WEB_DIRECTORY` present). It must **run alone in one process** and **with no
environment variables set** — `test_v3_schema.py` hard-codes `H3RELAY_NODE_API` to `v3`, so in the same
process "default" cannot be verified.

The reason for adding it: when the default was switched from v1 to v3 on 2026-09-24, **every test before
the switch either verified v1 or forced v3, and not one assertion locked "default"**. And it **caught a
real problem on its very first run** (in CI the V3 exit actually could not load).

> ⚠️ The V3 exit needs `import comfy_api`, whose transitive closure = **the host's whole Python
> dependency set** (on 2026-09-24 three were missing within one day: `packaging` → `comfy-aimdo` →
> `tqdm`). This pack already added the backstop "**V3 load failure ⇒ fall back to V1 + loud warning +
> record the reason**" (an optional exit must not bring the whole pack down), and put `packaging` /
> `comfy-aimdo` into `requirements.txt`; the CI side was changed to **install the host's own
> `requirements.txt` directly** (excluding only torch and the front-end bits), so it no longer patches
> gaps one by one.

## Other self-checks under tools/ (full list in [`tools/README.md`](../tools/README.md))

| Tool | What it judges | Expected |
|---|---|---|
| `tools/review_050.py` | doc–code consistency (node list / parameter table / assertion count / version / **all** example-graph slots (I1-I4: slots / node counts / widget markers) / **the three iron rules** / **the L13 English-doc sync gate** / **the H3j smoke expected values** / **H3k registry metadata** / **H3l release policy present** / **H3m no credential literals** / **H3n i18n word-table completeness** / **H3p bilingual tooltip completeness** / **H3q JS comma expression** / **H3r i18n anti-overwrite guard** / **H3s link endpoint field compat** / **H3t official locale entry** / **H3o dangling doc references** / **H3u registry package file count (true value)** / **H3v tools script count (true value)** / **H3w untracked files** /  **H3x `setdefault` to `PYTHONPATH`** / **H3y tools script registry** / **H3z release.py flags complete** / **H4a explicit env for Python children** / **H4b criterion-table self-check** / **H4c ledger consistency** / **H4d JS expected values (true value)** / **H4e dist whitelist (true value)**) | **116/0** |
| `tools/smoke_nodes.py` | node-layer functional smoke test (the seven continuation items really run once; 🔍 the upscale node needs upstream weights and is not in the smoke test) | **16/0** |
| `tools/assert_default_exit.py` | default exit = V3 (contract 2 + behaviour 3; includes **the node set V3 actually registers**) | **5/5** |
| `tools/sync_deploy_check.py` | the deployed copy matches the commit state of the given commit | all `OK` |
| `tools/ci_env_repro.py` | **fabricate the "no host registry" path** locally to run any validation script (`sys.modules["nodes"] = None`). ⚠️ since 2026-10-07 that path yields the **same** counts as a direct run — the "7 nodes" figure was an **artificial state this very tool created** (root-fixed); **same counts ≠ same path**, so it is still worth running | passes through the run script's exit code |
| `tools/en_sync.py` | **English-doc sync gate** (Chinese = source, `*_EN.md` = derived artifact, section-level hash) | exit code `0` |
| `tools/release.py` | **publish to both channels as a pair + cross-validate** (GitHub push → wait for CI green → registry publish → check back that both are this version). Policy text = [`RELEASING.md`](../RELEASING.md) | exit code `0` (`--verify-only` is read-only) |
| `tools/user_smoke.py` | **external open-source user's view smoke test** (added 2026-10-07): copies a **clean clone** per `git ls-files` (exactly what someone gets from `git clone`) ⇒ asserts ① the default V3 exit and `H3RELAY_NODE_API=v1` **register the same set of nodes** (the machine form of iron rule 1) ② every node's `INPUT_TYPES()` evaluates ③ `WEB_DIRECTORY` lives inside the clone and every front-end JS is present ④ **no module comes from outside the clone**; then it **really runs one node method** from `dist/` (not just an import). ⚠️ Why it is required: every other self-check runs **inside the source repo** ⇒ none of them can prove that a clone is self-sufficient | **10/0** |
| `tools/voice_bank.py` | **voice-bank collector** (not a self-check): collect a character's voice anchor from already-rendered segments, feed it to Copy Bridge's `voice_anchor`. Optional ASR dialogue guard (enabled only when funasr is installed, otherwise falls back to the energy method) | manual reading (`OK` / `跳过`) |

CI (`.github/workflows/ci.yml`) runs in order: **ruff static check → the regression five (`test_relay_core` ·
`review_050` · `smoke_nodes` · `test_experimental` · `check_ui_workflow`) → the default-exit assertion →
V3 field-by-field parity → JS prompt dispatch → build the distribution set
(`make_minimal_bundle.py --zip`, including the en_sync pre-gate) → **the external-user-view smoke test (`user_smoke.py`: clean clone + a real run from `dist/`)**, and any failing step turns it red.
`en-sync` is not a separate CI step — it is covered by `review_050`'s **L13**.
⚠️ **Known coverage gaps**: only Python **3.11** is run (while `requires-python = ">=3.10"`); host
dependencies are not pinned ⇒ upstream drift creates noise-red. See the gap list at the head of `ci.yml`.

### English-doc sync gate (`tools/en_sync.py`, added 2026-09-30)

**Why it is not "automatic translation"**: machine translation would silently change measured numbers
(`0.0007`) and identifiers (`settle_frames`), while this repository's machine checks **scan only the
Chinese source** ⇒ the English version would become a regulatory blind spot. So the gate does only three
things: **watch for sync / auto-rewrite the mechanical surface / generate translation briefs**.

| Command | Effect |
|---|---|
| `python tools/en_sync.py` | gate (= `--check`): **0** in sync / **1** some section not synced / **2** gate broken |
| `--status` | per-section report (fresh / source changed / unregistered / acked / deliberately untranslated) |
| `--brief <section>` | generate an incremental translation brief (that section's Chinese text + glossary + hard constraints) for a translator or an LLM |
| `--apply` | **only** rewrite the mechanical surface: version / the file-head `<!-- EN-SYNC -->` marker / missing index rows (never touches prose) |
| `--stamp` | register after translating (source hash + English hash) |
| `--omit "section" --why …` | register "deliberately untranslated" (on `--stamp` a **new section defaults to todo ⇒ red**, forcing a decision) |
| `--ack "section" --why … --until date` | register an ack (**with a deadline**; it turns red again automatically when it expires) |

State file `tools/en_sync.json` (section mapping + both-side hashes + ack ledger), glossary
`tools/en_glossary.json`. **Adding a doc pair = adding one line to `en_sync.py`'s `PAIRS`**. The gate
also runs before packaging (an out-of-date English version blocks the package).

Rules (three; read them before changing the gate): ① **single source** (the English must not contain an
assertion number the Chinese source does not); ② **must translate by default** (an unregistered source
section = red); ③ **fail-closed** (a missing/broken manifest ⇒ exit code 2, never returns 0).

## Minimal runnable distribution set (for users/distribution, not a development artifact)

```bash
python tools/make_minimal_bundle.py --zip     # → dist/ComfyUI-H3-Latent-Relay(.zip)
```

**42 files / 1260 KB** (zip 446 KB), carrying only:

| Group | Files |
|---|---|
| Runtime-required (25) | `__init__.py` · `relay_core/` (11 files) · `nodes.py` · `layout_contract.py` · `v3/` (4 files) · `exp/__init__.py` · `exp/history_anchor_v2/` (3 files) · `exp/voice_accum/` (3 files) |
| Front end (7) | The seven JS files under `web/` (chain / prompt dispatch / voice anchor / i18n ×2 / sync) |
| Examples (3) | `examples/`'s two workflows + their README |
| Metadata/legal/must-read (7) | `requirements.txt` · `pyproject.toml` · `LICENSE` · `licenses/Apache-2.0.txt` · `THIRD-PARTY-NOTICES.md` · `README.md` · `README_EN.md` |

**Not included**: `docs/` · `tests/` · `tools/` · `.github/` · `CHANGES.md` · `CONTRIBUTING.md` ·
`CODE_OF_CONDUCT.md` · `SECURITY.md` · `.gitattributes` · `.gitignore` (the per-item "why" is maintained
by the `EXCLUDED_NOTE` in the script). ⚠️ **The numbers above are governed by
[`tools/README.md`](../tools/README.md)** (one convention is written concretely in one place only —
measured 2026-09-30, two places once contradicted each other: 626 KB vs 646 KB).

**This script does three things, all required**:
1. **Cross-validate the manifest against static import derivation** — recursively find from `__init__.py`
   the pack files that will really be imported (including implicit dependencies like each level's parent
   package's `__init__.py`), and they **must equal the manifest one by one**
   ⇒ if someone later adds a module but forgets the manifest, it **errors on the spot** instead of letting
   a user hit `ImportError` first;
2. write out `dist/` (idempotent; only `--force` overwrites, and it deletes only directories it produced);
3. **self-verify**: load the produced directory as a **standalone pack**, assert all 8 nodes are present,
   the default exit is `v3` (no fallback), `WEB_DIRECTORY` has front-end files, the node-layer exception
   context is still there,
   and assert **not one pack module comes from the original repository** (proving this copy is
   self-sufficient).
   `--verify-only <dir>` runs only the self-verification.

> Measured: unzip to **outside the repository** (another disk) and load it — the 8 nodes / slots /
> default exit / front end / exception context all pass, and the count of modules from the original
> repository = **0**.
