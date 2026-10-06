# Canvas appearance · `advanced` folding · notes on migrating old graphs

<!-- EN-SYNC src=docs/04-canvas-and-widgets.md stamped=2026-10-05 mode=see tools/en_sync.json -->

🌐 English translation of [`docs/04-canvas-and-widgets.md`](04-canvas-and-widgets.md). **The Chinese file is the source of
truth** — if the two ever disagree, the Chinese one wins.

> This file was split out of the README (0.6.0 restructuring).
> 📐 **Port notation:** throughout this document `[N]` is **0-based** (the Nth port in the UI = `[N-1]`).
> The "route N" style carried over from older drafts was 1-based and has been converted to `[N]` at the
> places that matter.

---

### 🔧 Why the nodes look "small/large" on the canvas — the `advanced` collapse (0.5.0)

The nodes in this pack **draw only the few commonly used knobs on the canvas by default**; the rest are marked
`"advanced": True` and folded into the expand area at the bottom of the node. This is not hiding features — it is
**so the nodes don't get so big**:

- Node resize in the ComfyUI frontend is **hard-clamped**: the width has a **hard floor of 225px**
  (`GraphView-*.js`'s `useNodeResize` → `Math.max(size.width, 225)`),
  and the height cannot go below the **content height** (number of widget rows) ⇒ **only by rendering fewer
  rows can the node shrink**.
- `advanced` is an official mechanism (the same one as `comfy_extras/nodes_model_advanced.py`);
  it **does not change** widget order, the positional `widgets_values` reads, or the defaults, and it does not
  affect API submission.

To see every knob, pick one of three:
1. Click the **advanced inputs** expand bar at the bottom of the node;
2. Right sidebar → the **Advanced Inputs** group (it lists the advanced items of every node);
3. Turn on **`Comfy.Node.AlwaysShowAdvancedWidgets`** in settings (off by default ⇒ once on, everything on the canvas expands).

| Node | Kept on canvas (main knobs) | Folded into advanced |
|---|---|---|
| Trim AV | `trim_frames` / `fps` / `settle_frames` | 15 quality-domain knobs + `seam_ghost` / `seam_ghost_alpha` |
| Post | 9 main knobs (8 **main strengths** including `head_zone_frames` + `cross_seg_ack`) | 10 fine-grained items and guard rails (`*_frames` / `*_gain_max` / `*_offset_max` / `*_blur` / `radius` / `stats_frames` / `baseline`) |
| Copy Bridge (composite bridge) | `context_frames` / `mask_mode` / `pin_audio` / `anchor_blend` / `ref_anchor_stage` | 7 mode-specific parameters (the taper / ramp / blend families) + composite collapsed slots (conditioning / run_id / stage_index / ref_anchor_*) |
| Audio Seam | `patch_seconds` / `fade_seconds` | `tile_seconds` / `bed_stage` / `note` |

> Connection ports (`images` / `audio` / `guide` / `latent` / `conditioning` …) are unaffected and always drawn on the node.

### Term ↔ node-label mapping (since 0.6.18 node labels are **English only**)

The names shown on the canvas/in the menu are English (`NODE_DISPLAY_NAME_MAPPINGS`), whereas this repo's
Chinese docs call them by **function** — the table below maps the two sides:

| Chinese term (as used in this doc) | Node label (what you see on the canvas) | Type name (`class_type`) |
|---|---|---|
| TrimAV (the node) | `🔗 H3 Relay · Trim AV` | `H3RelayTrimAV` |
| Copy bridge / composite bridge | `🔗 H3 Relay · Copy Bridge` | `H3RelayCopyBridge` |
| write to disk / Latent save | `🔗 H3 Relay · Latent Save` | `H3RelayLatentSave` |
| load previous-segment latent / Latent load | `🔗 H3 Relay · Latent Load` | `H3RelayLatentLoad` |
| post-processing / Post | `🔗 H3 Relay · Post` | `H3RelayPost` |
| audio seam | `🔗 H3 Relay · Audio Seam` | `H3RelayAudioSeam` |
| chain run / chain control | `🔗 H3 Relay · Chain` | `H3RelayChain` |
| latent tiled upscale | `🔍 H3 Relay · Latent Upscale` | `H3RelayLatentUpscale` |

> ⚠️ **Changing labels does not touch old graphs**: a workflow stores the **type name** (third column above) and
> the user's own **title**, so changing a display name **does not affect saved graphs** (links, slots and
> execution all stay as they were) — only newly dragged-out nodes get the new name.
> The Chinese doc body keeps the Chinese words; **when naming a node explicitly it uses a short name like `"Trim AV"`**
> — a substring of the label, so it matches at a glance.

> 🔴 **0.5.0 migration note: the "Trim AV" in old graphs cannot see `prev_tail`.**
> The ComfyUI frontend's `LGraphNode.configure()` **takes the serialized `outputs` array as-is**
> (`litegraph/src/LGraphNode.ts`: `this.outputs = cloneObject(info.outputs)`),
> so in **workflows saved before 0.5.0** that node's `outputs` has only 3 entries ⇒ after opening you **cannot see the 4th one, `[3]`
> `prev_tail`**, and thus cannot wire `guide` by hand. **Execution is unaffected** (nobody links the new output);
> you just cannot wire post-processing. Two fixes, either one:
> 1. **Re-add the node**: delete the old "Trim AV" and drag in a new one, then reconnect the wires (recommended; it also refreshes all widgets);
> 2. **Patch the JSON**: append `{"name": "prev_tail", "type": "IMAGE", "links": []}` to the end of that node's `outputs`
>    (back up first; section J of `python tools/review_050.py` scans out graphs with this "stale output array").
>
> The scripted pipeline (the author's production-line repo's `l1_api.py`, **not in this pack**) is unaffected — the graph is built fresh each time and always takes the current node definition.

### Changing `run_id` in one place updates the whole group (0.6.19, **canvas-only**)

`run_id` (the name of this film; it decides where segment files land, `output/relay_kit/<run_id>/`) is stored once on each of
**six nodes** (LatentSave / bridge / LatentLoad / TrimAV / AudioSeam / Chain) and must match character-identically.
Since 0.6.19, **editing any one field ⇒ the rest of the group follows automatically**:

- Scope = **the same group box**. With no group box drawn it degrades to **the whole graph**; in that case `status` ⚠
  **states outright** that the scope is the whole graph (putting two films on one graph is a normal usage — for that, draw a group box).
- Editing one **non-empty** name ⇒ the other fields follow, and `status` reports “`run_id` unified to “…” (N nodes synced)”.
- **Clearing one field does not spread** (clearing ≠ wanting to clear the whole group), but it warns “there are names elsewhere — an inconsistency will make the segment files unfindable”.
- 🔴 **Two different non-empty names ⇒ no automatic pick**: clicking `▶ Run` / `✔ Approve` / `⏩ 连跑` / `⏭ 续跑` is blocked,
  and `status` lists “which node holds which name”; fix them all to match, then run.
- By the way: **exactly one non-empty and the rest still empty** ⇒ clicking those four buttons **fills them in automatically** — so you only need to edit one field.

> ⚠️ This is **pure frontend** behaviour: it **does not take effect** when a script submits the graph JSON (a script
> just writes that field from a single variable; see [`docs/10`](10-audio-seam-and-concat_EN.md) §7.4).
> ⚠️ **Renaming does not move the old directory**: segment files stay under the old name ("directory name = film name" is the long-standing semantics).

### Node Chinese/EN toggle (0.6.28, **canvas-only**)

Each of this pack's 8 nodes now carries a small 「**中 / EN**」 button. Clicking any one of them switches the
titles / parameter labels / port names of **the whole pack** (not just that node).

| | Behaviour |
|---|---|
| Default language | **follows the UI language** (reads `<html lang>`) ⇒ an English UI keeps English node labels; the button overrides that |
| Remembered | the override lives in the browser's `localStorage` (key `h3relay_lang`) ⇒ it survives closing the page |
| What changes | node `title`, widget `label`, port `label`, the **display text** of dropdowns |
| What never changes | 🔴 **`name`, dropdown `values`, the workflow JSON** — not one byte |

**Three design constraints (all of them guard silent failures)**:

1. 🔴 **Display only, never the graph**. `name` is what the backend reads parameters by and what gets
   serialized — changing it means changing the graph. Dropdown Chinese text uses the official
   `options.getOptionLabel` hook (a function never enters the JSON ⇒ sharing the graph does not pollute it).
2. 🔴 **Coexists with the audio-reference-budget hint**. That hint is **appended to the end of the title**
   (`relay_kit_refs_ui.js`) and both modules write `title` ⇒ this one **swaps only the name part and keeps the
   suffix** ⇒ correct in either order.
3. 🔴 **Never guesses a translation it is unsure about.** Only **whitelisted** enum values get Chinese display
   text (`hard` → 硬边); `model_name` (a model file name), stage numbers, paths and your own names are
   **never translated** — translating those means the file can no longer be found.

**Relation to interface translation plugins**: plugins like Global Translation translate **DOM text**, this
feature changes `label`/`title` **data** and only for **this pack's own nodes** ⇒ the two do not fight. Keys
missing from the table are **skipped silently** (no crash, no change); forgetting to translate a new parameter
is caught by `tools/review_050.py`'s **H3n** check.

> ⚠️ **Pure frontend**: it **does not take effect** when a script submits the graph JSON (nor is it needed —
> labels are only there for humans).
> ⚠️ The button is mounted via `addDOMWidget(serialize:false)` (never enters the workflow JSON); if a frontend
> version lacks that API the button degrades to a 「中/EN」 **corner tag** on the node (state still visible, not
> clickable) and **nothing about execution changes**.
