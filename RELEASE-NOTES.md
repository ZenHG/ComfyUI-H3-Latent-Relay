# 发布说明（Release Notes）

> **这份文件写给用户，不是写给开发者。**
> **两个渠道的用户可见说明都取这里**（唯一真相源）：Comfy Registry 的 changelog 与 GitHub Release 的正文。
> 开发者细节（实现方案、实测证据链、机检编号、翻车过程）留在 [`CHANGES.md`](CHANGES.md)，**不要往这里搬**。
>
> 格式规矩（正文见 [`RELEASING.md`](RELEASING.md) §6）：
>
> - 一版一节，**新的在最上面**，标题固定 `## x.y.z — YYYY-MM-DD`。
> - 每节**必须中英双语**：中文在前，一行 `<!-- EN -->` 分隔，英文在后。
>   英文**独立成文**（不是逐句直译 —— registry 页面上大部分读者只看英文）。
> - 只写**用户能感知的变化**。有行为变化必须用 🔴 点明「要不要动配置」。
> - 节内长度**分段算**：**中文段 ≤700 字符 · 英文段 ≤1700 字符**
>   （`tools/release.py` 的 `NOTES_MAX_ZH` / `NOTES_MAX_EN`，超了直接拒绝发布）。
> - 每节结尾固定两行：中文 `**升级动作**：…` / 英文 `**Action required**: …`。
>
> 为什么要有它：registry 的 changelog **发布后不可改**（版本接口只收 `GET`/`OPTIONS`），
> 所以唯一的防线是**发布前把它写对**；`tools/release.py` 会在 push 之前就按上面几条把你拦住。

---

## 0.6.39 — 2026-10-23

**⏩ Finite Run 现在能接「定妆照」了：全片同一组参考图，一镜到底不再在新段开头跳一下。**

- 新增 3 个参考图槽 `ref_image_1..3`（只接 1 张也行），外加 `ref_image_size` / `refs_mode` / `ref_anchor_mode` / `ref_anchor_frames` 四个开关。
- 🔴 **参考图与首帧图 `image` 不能同时接** —— 官方两个生视频节点各缺对方那一槽，同时接会在开跑前直接报错，不会悄悄挑一个用。要人物与场景稳定就留参考图、断开 `image`。
- `refs_mode` 默认 `lock` = 全片共用同一组图，这是「一镜到底」唯一没有冲突的用法；`first_only` = 只喂本批第 1 段，后续段靠外观参考跟随镜头。
- 槽位顺序就是词里 `<Picture 1>` / `<Picture 2>` / `<Picture 3>` 的编号；中间跳过一个槽，后面的编号会整体前移 —— 不报错，但会接错人。
- 另新增 `from_segment`：Finite Run 能从第 N 段起跑，「视频起段 → 一次提交跑完剩下的段」这条路通了。
- 旧图逐位不变：不接参考图时，行为与之前完全一致。

**升级动作**：图 / 连线 / 参数都不用动；刷新浏览器页面即可。

<!-- EN -->

**Finite Run can now take reference stills: one set for the whole run, so a one-take continuation no longer jumps at the start of a new segment.**

- Three new slots, `ref_image_1..3` (wiring just one is fine), plus `ref_image_size`, `refs_mode`, `ref_anchor_mode` and `ref_anchor_frames`.
- 🔴 **Reference stills and the first-frame image `image` are mutually exclusive.** The two official text-to-video nodes each lack the other's slot, so wiring both raises an error before anything runs — nothing is silently chosen for you. Keep the stills and drop `image` when cast and set must stay stable.
- `refs_mode` defaults to `lock`: every segment sees the same stills, the only conflict-free way to hold a single take. `first_only` feeds this batch's first segment only and lets the appearance anchor carry the rest.
- Slot order is the `<Picture 1>` / `<Picture 2>` / `<Picture 3>` numbering in your prompt. Leaving a middle slot empty shifts every later number down — no error, just the wrong face.
- Also new: `from_segment`, so Finite Run can start at segment N. "Seed from a video, then run the rest in one submission" now works.
- Existing graphs are unchanged: without reference stills the behaviour is exactly as before.

**Action required**: none — no graph, wiring or parameter changes; just refresh the browser page.

---

## 0.6.38 — 2026-10-20

**新增两个节点：把几段拼成一条（「图内拼条」），以及一次提交跑完 N 段（「无头连跑」）。**

- **图内拼条**：把每段裁好的画面与音频接到累计后面，全程在张量里做、**不重新编码**。规格不一致（分辨率 / 采样率 / 声道）会**直接报错**，不会悄悄把小图拉大去凑数。
- **无头连跑**：一次提交把第 1 段到第 N 段整条跑完，不用点按钮、不用逐段改段号；脚本与画布走同一套节点。每段自动读上一段的落盘文件接着写。
- 两个节点都不被已有工作流引用，**旧图完全不受影响**。

**升级动作**：无需改图或连线。

<!-- EN -->

**Two new nodes: join several segments into one picture ("Join AV"), and run N segments in a single submission ("Finite Run").**

- **Join AV**: appends each trimmed segment's picture and audio onto the accumulated result, entirely in tensors with **no re-encoding**. Mismatched specs (resolution / sample rate / channels) **stop with an error** instead of quietly stretching a smaller frame to fit.
- **Finite Run**: runs segment 1 through N in one submission — no button clicking, no editing stage numbers by hand, and scripts use the same nodes as the canvas. Each segment reads the previous segment's saved file and continues from it.
- Neither node is referenced by existing graphs, so **old workflows are unaffected**.

**Action required**: none.

## 0.6.37 — 2026-10-20

**新增「视频起段」节点：你可以拿一段已有的视频当整条链的第 1 段，之后照常往外接。**

- 以前本包只能从「自己生成的上一段」开始接，第 1 段必须是它生成的。现在**实拍、别人的成片、早先跑过的片段**都能当起点。
- 它替你挡三件事：源视频帧数不在 H3 的时间栅格上（按帧尾裁到最近的合法档，裁掉几帧会写在报告里）；画布不是 16 的倍数或与后面各段不一致（**宽高填 0 就跟随源视频**）；源音轨和画面不等长（按画面长度裁尾或补零，没有音轨就给静音种子）。
- **它拿不到源视频的帧率**，一律按 24 fps 处理。源不是 24 fps 的话，画面时长会跟着变——请先把源转成 24 fps 再喂进来；报告里会点名这一行。
- 源很长时请先切段：视频编码是整段做的，显存峰值随帧数上涨。
- 顺手放宽了一根线：「桥」的上一段输入以前**必接**，现在留空也行——它会按片名自己从磁盘读上一段。已经接好的图完全不受影响。

**升级动作**：无需改图或连线。旧图不引用这个节点，逐位不变。

<!-- EN -->

**New node, "Seed from Video": take an existing video as segment 1 of the whole chain, then continue outward as usual.**

- Until now the chain could only start from a segment this pack had generated. Now **live footage, someone else's finished shot, or an earlier run** can all be the starting point.
- It blocks three traps for you: source frame counts off the H3 temporal grid (it trims the tail to the nearest legal step and reports how many frames it dropped); a canvas that is not a multiple of 16 or does not match your later segments (**set width and height to 0 to follow the source video**); and source audio that does not match the picture length (trimmed or zero-padded, or a silent seed when there is no audio track).
- **It cannot read the source frame rate** and always treats the frames as 24 fps. If your source is not 24 fps the picture duration will change — convert the source to 24 fps first; the report names this line.
- One wire relaxed as well: the bridge's previous-segment input used to be **mandatory**; leaving it empty now works — the bridge reads the previous segment from disk by the film name. Graphs that already wire it are unaffected.

**Action required**: none. Existing graphs do not reference this node and are bit-identical.

## 0.6.36 — 2026-10-19

**续接段忘了接「裁重叠」的帧数时，以前会悄悄一帧都不裁：接缝把上一段开头重播一遍，而所有检查都显示正常。这一版改成当场报错，并告诉你怎么接。**

- 「Trim AV」的 `trim_frames` 默认从 `0` 改为 **`-1`（未接线）**。它正常由「Copy Bridge」第 3 路供给；已经接好线的图**完全不受影响**。
- 忘了这根线：运行会停下并提示——续接段接「Copy Bridge」第 3 路；首段 / 独立段填 `0`。
- 首段和独立段本来就不需要裁：把 `trim_frames` 填 `0` 即可。
- 「Post」的低频残差 `lowfreq_blur` 默认从 `64` 降到 `16`：同一段约 22 秒 → 约 1.6 秒，缝上的差别只有 0.0013（看不出来）。老图仍按原来的值跑。
- 「潜空间分块放大」的空间分块提示补上小显存建议（8G / 2MP 先试 2 块，3~4 块通常够），两个细调参数收进「高级」。
- 「连跑控制」不再要求先画分组框：没有分组时自动在整张图里找；只有一张图放多套链路时才需要分组。
- 「跨段统计匹配」的提示写明：目前只有负向证据（近似参照路线会变差），拷贝桥下还没有正向实测。
- 术语统一：标签改口语（「钉住音频」→「复制音频」、「床声」→「环境声」），提示里的「段头 / 声锚 / 外观锚」改成「本段开头 / 声音参考 / 外观参考」。

**升级动作**：无需改图或连线。只有「首段放了 Trim AV、却没接线」的图需要把该格填成 `0`。

<!-- EN -->

**If a continuation segment's trim count was never wired, nothing used to be trimmed: the seam replayed the previous segment's opening while every check still passed. It now stops right away and tells you how to wire it.**

- Trim AV's `trim_frames` now defaults to `-1` (not wired) instead of `0`. It is normally fed by Copy Bridge's 3rd output; graphs that already wire it are unaffected.
- If that link is missing, the run stops with a message: wire Copy Bridge's 3rd output for continuation segments, or set `0` for a first or standalone segment.
- First and standalone segments need no trimming: set `trim_frames` to `0`.
- Post's low-frequency residual `lowfreq_blur` now defaults to `16` instead of `64`: about 22 s → about 1.6 s on the same shot, with a seam difference of only 0.0013 (invisible). Existing graphs keep their saved value.
- The spatial-tiling hint now gives low-VRAM advice (on 8 GB at 2 MP try 2 tiles; 3–4 is usually enough), and two fine-tuning knobs moved under Advanced.
- Chain no longer asks you to draw a group box first: without one it searches the whole graph; groups are only needed when one graph holds several chains.
- The cross-segment match hint now states it has negative evidence only (the approximate-guide path gets worse); there is no positive measurement on the copy-bridge path yet.
- Wording cleanup: plainer labels (Pin audio → Copy audio, Bed source → Ambience source) and tooltips without the old jargon.

**Action required**: none — no graph or wiring changes. Only a graph that puts Trim AV on a first segment without wiring it needs that field set to `0`.

## 0.6.35 — 2026-10-16

**英文界面下 Chain 的 7 个按钮此前整块是中文，桥节点标题挂着一行长中文警告。这一版都改对了，并把「切英文后画布上真的是英文」做成自动断言。**

- **Chain 的 7 个按钮跟着语言走**：切英文显示 `▶ Run (run the current segment once)` 这类；切回中文原样。点起来的反应不变。
- **桥节点标题的额度角标改短**：一长段中文（`⚠ 音频参考 4 个（官方上限 3）：你的 3 + 本包 1。模型不拦…`）改成 `⚠ refs 4/3`。三种形态（用满 / 越界 / 读不到）含义不变，长解释挪进提示（悬停 `音频参考窗` 那格）。
- **英文提示开头改成 `[Wiring] Take …`**（原用全角 `【…】`，英文里是排版异物）；另修 3 处**英文还写着旧口径**的提示（`TrimAV` / `Post` 跨段匹配、CopyBridge 实验开关）。
- **示例图 `fullflow` 的 5 个节点标题改了写法**：旧名切英文也不变；现在是「默认名 + （说明）」，名字那半跟着语言走。接线、参数、槽位**一个字节没动**。
**升级动作**：不用改任何东西；装完刷新浏览器页面（F5）。切语言开关即可看到英文按钮。

<!-- EN -->

**The Chain node's 7 buttons used to stay entirely in Chinese on the English UI, and the bridge title carried a long Chinese warning. Both are fixed, and "after switching to English, the canvas really is English" is now an automated assertion.**

- **The Chain node's 7 buttons follow the language.** In English: `▶ Run (run the current segment once)`; back in Chinese they are as before. Clicking behaves the same.
- **The bridge title's audio-reference badge is much shorter.** The old badge was a full sentence of Chinese spelling out the audio-reference count and the official limit; it is now the compact `⚠ refs 4/3` — three forms (full / over / unreadable), same meaning. The long explanation moved into the tooltip (hover `Audio reference window`).
- **English tooltips now open with `[Wiring] Take …`** instead of full-width `【…】`. Three tooltips whose English still described the **old** behaviour are corrected (`TrimAV` / `Post` cross-segment matching, CopyBridge experiment switch).
- **The `fullflow` example's 5 node titles were rewritten** from old names that never changed with the language into `default name + (explanation)`, so the name part now follows the language. Wiring, parameters and widget slots are **untouched**.

**Action required**: nothing; refresh the browser page (F5). Use the language switch to see the English buttons.

---

## 0.6.34 — 2026-10-13

**默认值修了矛盾、声锚不用接线、提示重写成人话、实验功能有了开关、落盘文件带上来龙去脉。图、连线、图 JSON 都不用改；但请刷新浏览器页面。**

- **音频缝的「头部补丁」默认打开（1.2 秒）**：以前默认关 ⇒ 每段环境声各起各的；现在段首 1.2 秒用上一段的环境声，台词守卫兜底。⚠ 段号 ≥1 而上一段音频不在盘上（片名填错 / 没跑过）会**报错**并说明怎么办。老工作流不受影响。
- **声锚可以「只填段号」**：以前要搭两个节点再拉一根线；现在填「声锚段号」就自动读那段音频。接线仍然优先。
- **全部悬浮提示重写**：只讲它做什么、打开会怎样、什么时候该调；同一条提示不再有两套中文。标签也按同一张术语表统一（48 条）并加机检。
- **两个「好功能默认关」打开了**：段头糊区补偿（`settle_auto` 0.5，不糊的帧不动）与连跑完自动拼成片（`auto_concat`）。
- **实验功能有了看得见的开关**：原先只能靠 run 目录的配置文件。默认 `auto` = 与旧版逐位一致 ⇒ 老配置不受影响；`off` = 彻底关；选对应档 = 强制开。
- **落盘文件带上了来龙去脉**：每段 latent 头里记下片名 / 段号 / 上一段（`format 2`）。**纯附加**：旧版读新文件、新版读旧文件都照读。
- **跨段统计匹配更保守**：缝的差异小于噪声时**不再去修**，修时也**不放大差异**。噪声大的片段头可能不再被修，那是设计。

**升级动作**：不用改任何东西；装完刷新浏览器页面（F5）。

<!-- EN -->
**Self-contradictory defaults fixed, the voice anchor needs no wiring, every tooltip rewritten in plain language, the experiments got a switch, and saved files carry their lineage. No graph or JSON changes — but refresh the browser page.**

- **The audio seam's head patch is on by default (1.2 s)**: it used to be off, so each segment restarted its own ambience. The head 1.2 s now takes the previous segment's ambience, dialogue guard as a backstop. ⚠ From segment 2 on, a missing previous audio errors out instead of quietly producing discontinuous audio. Existing graphs are unaffected.
- **The voice anchor takes a stage number alone**: it used to need two nodes and a wire. A wired input wins.
- **Every tooltip rewritten**: what it does, what turning it on changes, when to adjust it. No parameter has two Chinese wordings any more; labels follow one 48-entry glossary, machine-checked.
- **Two features that shipped off are now on**: head soft-zone compensation (`settle_auto` 0.5, clear frames untouched) and auto-joining at the end of a chain run (`auto_concat`).
- **The experiments have a visible switch**: previously only a config file in the run directory. Default `auto` is bit-identical to the old behaviour; `off` disables them; naming one forces it on.
- **Saved latents carry their lineage**: each stage file's header records film name, stage, and parent stage (`format 2`). Purely additive: both directions still read.
- **Cross-segment matching is more conservative**: differences below the noise floor are no longer corrected, and a correction never amplifies the step. A noisy head may go uncorrected — by design.

**Action required**: nothing; refresh the browser page (F5).


## 0.6.33 — 2026-10-12

🔴 **图、连线、参数都不用改，但要刷新浏览器页面。**

**低显存多了个「省得更干净」的旋钮：`spatial_tiles`**（沿画面**长边**切块放大）—— 每块的**时间轴完整** ⇒ 时间上下文一点不丢，代价换成空间边界变窄（用 `spatial_overlap` + `seam_feather` 把接缝压进过渡带）。默认 `1` = **不切**，与旧版**逐位一致**；与 `chunks` 可叠加（时间分块在外、空间分块在内）。🔴 **切了就会改画面** ⇒ **切完必须重看缝**。另：`prompts` 只有 1 块词而本段 `stage>0` 时，报错会**直接给出两种正确接法**。

**顺带修掉「🧩 拼成一条」的一批问题**：① 不再误报"一段视频文件都没找到"（兜底回扫只认**真落了段视频**的提交）；② 失败时状态格显示**真错因**；③ 两段同规格却报"规格不一致"的伪差修掉（fps 改**相对容差** 0.5%）；④ 连跑/拼接按钮**整行都能点**；⑤ 官方「停止/取消」与队列清空后状态会**回落**；⑥ `prompts` 留空不再被拦。

**升级动作**：不用改任何东西；装完刷新浏览器页面（F5）。想省显存就把「空间分块数」从 1 往上调。

<!-- EN -->
🔴 **No graph, wiring or parameter changes — but refresh the browser page.**

**A "cleaner" VRAM knob: `spatial_tiles`** — it upscales in tiles along the **long edge**, so every tile keeps its **whole time axis** and no temporal context is lost; the cost moves to the spatial boundary, handled by `spatial_overlap` + `seam_feather`. Default `1` = **no split**, **bit-identical** to the old path; it combines with `chunks` (time chunking outside, spatial tiling inside). 🔴 **Splitting still changes the picture** ⇒ **re-inspect the seam afterwards**. Also: when `prompts` holds a single block and the segment is `stage>0`, the error now **names both correct wirings**.

**Also fixes a batch of "🧩 Join into one" issues**: ① it no longer falsely reports that no segment video was found (the fallback scan only accepts submissions that actually wrote a segment video); ② on failure the status cell shows the **real cause**; ③ a false "spec mismatch" between identically sized segments is fixed (fps uses a **0.5% relative tolerance**); ④ chain and join buttons are **clickable across the whole row**; ⑤ the official Stop/Cancel and clearing the queue now make the status **fall back**; ⑥ an empty `prompts` is no longer blocked.

**Action required**: nothing. Refresh the browser page (F5) after installing. Raise "spatial tiles" above 1 to save VRAM.

## 0.6.32 — 2026-10-08

🔴 **「跑到第几段」这一格改了含义。图、连线、参数都不用改，但要刷新浏览器页面。**

**① `segments` 现在 = 「跑到第 N 段」**（以前读作「再跑 N 段」）。从第 1 段起跑时两者相同，
老图行为不变；只有**从中间接着跑**才不同：段号已在第 3 段、填 3 ⇒ 只跑这 1 段。
段号已越过这一格 ⇒ 不排队，并告诉你该填几。

**② 「⏭ 续跑」不再把已跑好的段号退回去。** 起点取「进度段号」与「画布段号」里更靠后的那个：
正常跑完后画布段号已经更新，续跑就直接接着跑；只有宿主采样中途被杀才重跑那一段。

**③ 按钮"点了没反应"现在看得见**（弹一次提示）。

**④ 拼接不再把「视频轨有起始偏移」的段判成坏段**（有些落盘节点会这么写）⇒ 现在按「视频结束时刻」比。

**⑤ 响度均衡默认开（不接声锚也生效）。** 以前不接锚时每段电平由模型自己决定，实测同片三段差
**7.97 dB**；现在统一对齐（−23.0 dBFS），实测 **8.08 → 2.43 dB**。关掉：取消勾选 `loudness_normalize`。

**升级动作**：把 `web/` 换成新版并**刷新浏览器页面**（F5）。图、连线、参数都不用改。

<!-- EN -->

🔴 **The "run up to which stage" cell changed meaning. No graph, wiring or parameter change — but refresh the browser page.**

**① `segments` now means "run up to stage N"** (it used to read as "N more stages"). Starting from
stage 1 the two are identical, so existing graphs behave as before; they differ only when you **start
from the middle** — canvas stage 3 with `segments=3` runs that one stage. If the stage number is
already past it, nothing is queued and it says what to enter.

**② «⏭ Resume» no longer pushes your finished stage number backwards.** The start point is the later
of the progress file's stage and the canvas stage; after a normal finish the canvas number is already
ahead, so resume just continues. Only a host killed mid-sampling re-runs that stage.

**③ A blocked button is now visible** (a popup, not silence).

**④ Concatenation no longer rejects segments whose video track has a non-zero start** (some save nodes
write it that way) — it now compares the video's **end time**.

**⑤ Loudness levelling is now on by default (it works without a voice anchor too).** Without an anchor
each segment's level was up to the model, measured at a **7.97 dB** spread across three segments; all
segments are now aligned to one target (−23.0 dBFS), measured **8.08 → 2.43 dB**. Untick
`loudness_normalize` on the audio-seam node to turn it off.

**Action required**: replace `web/` with the new version and **refresh the browser page** (F5).
No graph, wiring or parameter changes.

## 0.6.31 — 2026-10-07

🔴 **修两个一直坏着的按钮；`run_id` 改成"任意位置都同步"，并且 API 侧也有了。**

**① 连跑与 Run 两个按钮从 0.6.12 起就是坏的** —— 点下去只报「内部出错」。
另外五个按钮（Approve / 续跑 / Stop / 拼成一条 / Reset）一直正常。
**② `run_id` 的"改一处、其它自动跟着变"从来没生效过**（0.6.19 引入），
而且**失败无声** ⇒ 音频缝与桥的名字不一致时，床源会去**别的目录**找音频。

**改动**：① 同步范围改成「图上只有一条连跑节点 ⇒ **全图同步**」，节点摆在哪都行；
只有一张图里画了**多条**连跑节点（＝一部图放两部片子）才按分组框隔离。
② 后端补上同一套语义：新函数 `relay_core.sync_run_id` 与新接口 `POST /h3relay/runid`，
**跑批脚本不用再手改六处**。③ 新增一条浏览器冒烟门，专盯"后端全绿但按钮不能用"这类静默失效。

**另**：本版**一并包含未单独发布的 0.6.30**（纯内部拆包）。你唯一可能注意到的是
**下载件从 24 个文件变成 42 个** —— 体积与用法都不变。

**升级动作**：**刷新一次浏览器**即可（前端脚本即时加载）。跑批脚本可选用
`POST /h3relay/runid` 先自检一遍 `run_id` 是否一致。

<!-- EN -->

🔴 **Two buttons that have been broken since 0.6.12 are fixed; `run_id` now syncs from anywhere, and the API gets it too.**

**1. The auto-run and single-run buttons have been broken since 0.6.12** — clicking them only reported an
internal error. The other five buttons (approve, resume, stop, assemble-into-one, reset) always worked.
**2. The "edit once, the rest follow" `run_id` sync never actually worked** (added in 0.6.19) and it failed
**silently**, so when the audio seam's `run_id` disagreed with the bridge, the bed sound was looked up in the
wrong folder.

**What changed**: (1) the sync scope is now "a single chain node on the graph means sync across the whole
graph", so node positions no longer matter; grouping only applies when one graph deliberately holds several
films. (2) The back end gained the same semantics: a new `relay_core.sync_run_id` helper plus a new
`POST /h3relay/runid` route, so **batch scripts no longer edit six fields by hand**. (3) Added a
browser-level smoke gate that targets exactly the failure mode where "the back end is green but the buttons
do not work".

**Note**: this release **also includes 0.6.30**, which was never published on its own (a purely internal
refactor). The only thing you may notice is that the download now contains 42 files instead of 24 —
same total size, same usage.

**Action required**: **just reload your browser** (front-end scripts load on refresh). Batch scripts may
optionally call `POST /h3relay/runid` to check that every `run_id` agrees before submitting.


## 0.6.30 — 2026-10-07

**纯内部重构：核心代码从单文件拆成一个包 —— 行为逐位不变，你什么都不用改。**

`relay_core` 原本是一个 5482 行、23 万字符的单文件；现在拆成 `relay_core/` 包（11 个文件：
时序网格 / latent 存取 / 续接计划 / 音频缝 / 接缝检测 / 拷贝桥 / 只读观测 / 拼接成片 / 分块放大 /
词分发）。**对使用者的接口一个都没变**：`relay_core` 仍是同一个导入名，所有函数与常量照旧可用
（包里的 `__init__.py` **全量再导出**）；节点、连线、参数、图 JSON 一律不动。

**唯一看得到的变化在下载件上**：最小分发集从 24 个文件变成 42 个（多出来的都是包内的子模块，
单个文件都变小了，合计体积基本不变）。

离线断言全部照旧通过，并且已**逐个符号**比对确认代码**逐字搬运、未改一行算式**。

**升级动作**：**无需任何动作**。图 / 连线 / 参数都不用改；脚本里的 `from relay_core import …` 照旧。

<!-- EN -->

**A purely internal refactor: the core is now a package instead of one file — behaviour is unchanged,
bit for bit.**

`relay_core` used to be a single 5,482-line file. It is now the `relay_core/` package (11 files: timing
grid, latent I/O, relay planning, audio seam, seam detection, copy bridge, read-only observations,
concatenation, chunked upscaling, prompt dispatch). **Nothing about the interface changes**: `relay_core`
is still the same import name and every function and constant remains available (the package's
`__init__.py` re-exports all of them), so nodes, links, parameters and workflow JSON are untouched.

The only visible difference is in the download: the minimal distribution set grows from 24 files to 42
(the extra entries are the submodules, each smaller than the old file, and total size is roughly
unchanged).

The offline assertions all still pass, and every symbol was compared one by one to confirm the code was
moved verbatim with no expression changed.

**Action required**: **none**. Workflows, links and parameters are unaffected; any
`from relay_core import …` in your scripts keeps working.

---

## 0.6.29 — 2026-10-06

**节点上的中/EN 按钮改成跟随官方语言设置；顺手修掉一个把「改了没生效」拖了整整一轮的坑。**

🔴 **默认语言改为跟随官方设置**（设置里的 `Language`，即 `Comfy.Locale`），不再是"看界面语言"。
官方改语言 ⇒ 本包节点跟着变；点节点上的按钮才覆盖，**覆盖后官方再切语言不冲掉你的选择**。
**单向**：我们的按钮**只改本包节点**，**不写官方设置** —— 写它会触发 ComfyUI 重载当前工作流。
🔴 **修的坑**：此前读的 `<html lang>` 实测**与官方设置不一致**（官方设了中文，它仍是 `en`）
⇒ 会出现"官方界面中文、本包节点英文"。

**升级动作**：无需改图或连线，但**必须刷新浏览器页面**（F5）—— 重启 ComfyUI 进程不算。
想让节点回到跟随官方语言：清一次浏览器本地存储里的 `h3relay_lang`。

<!-- EN -->

**The per-node Chinese/EN button now follows the official language setting — plus a fix for a trap that
wasted a whole round of "my change did nothing".**

🔴 **The default language now follows the official setting** (Settings → `Language`, i.e. `Comfy.Locale`)
instead of "whatever the interface language is". Changing the official language makes this pack's nodes
follow; the node button overrides that, and **the official setting no longer clobbers your choice**.
**One-way**: our button changes **only this pack's nodes** and **never writes the official setting** —
writing it makes ComfyUI reload the current workflow. 🔴 **The trap it fixes**: the previously read
`<html lang>` was **measured to disagree with the official setting** (official set to Chinese while it
still said `en`) ⇒ producing "Chinese official UI, English pack nodes".

**Action required**: no graph or wiring changes, but you **must reload the browser page** (F5) —
restarting the ComfyUI process is not enough. To put the nodes back on the official language, clear the
`h3relay_lang` key in browser local storage.

---


## 0.6.28 — 2026-10-06

**节点上加一个中/EN 按钮；顺手修掉分发集漏发前端文件的毛病。**

🆕 **画布节点中英切换**（**UI-only**）：本包 8 个节点各多一个中/EN 按钮，点一下就把**全包**的
节点标题、参数标签、端口名切成中文 / 英文（下拉框显示中文、**存档值不变**）。
**默认跟随界面语言**，点按钮才覆盖（记在浏览器本地）。🔴 **只改显示**：`name`、下拉框的值、
工作流 JSON 一个字节都不动；你自己改过的节点标题原样保留；「音频参考额度」提示不会被抹掉。

🔴 **修一个分发集缺陷**：最小分发集里**漏发了 3 个前端文件**（`relay_kit_sync.js` /
`relay_kit_refs.js` / `relay_kit_refs_ui.js`）。`relay_kit_chain.js` 用 ES `import` 依赖它们
⇒ 装最小集的用户**整个 chain.js 都不执行**（连跑 / 🧩 拼接 / 额度提示全废），
而打包器自验还打印 [OK]（只验「清单里的在不在」，从不验「有没有漏」）。
已补齐，并给打包器加了**反向门**：`web/` 下受控的 `.js` 必须与清单**一一对应**。

另：深度文档补上 **VA（跨段音色累积）** 与声锚叠加的说明（中英双语）。

**升级动作**：无需改图或连线。**装了最小分发集（`dist/`）的请重装一次**；
从 Comfy Registry / Manager 装的**不受影响**（那个包按 `.comfyignore` 打包，`web/` 是全的）。

<!-- EN -->

**A Chinese/EN button on every node — plus a fix for the minimal distribution set.**

🆕 **Canvas Chinese/EN toggle** (**UI-only**): each of this pack's 8 nodes now carries a small Chinese/EN button;
one click switches the titles, parameter labels and port names of **the whole pack** (dropdowns show Chinese
while their **stored value stays untouched**). It **follows the UI language by default**; the button overrides
that and the choice is remembered in the browser. 🔴 **Display only**: `name`, dropdown values and the workflow
JSON do not change by a single byte; a title you renamed yourself is kept; the audio-reference-budget hint
appended to a title survives.

🔴 **A distribution-set defect, fixed**: the minimal bundle **shipped without 3 front-end files**
(`relay_kit_sync.js` / `relay_kit_refs.js` / `relay_kit_refs_ui.js`). `relay_kit_chain.js` imports them as ES
modules ⇒ anyone installing the minimal bundle got **`chain.js` not executing at all** (auto-run, the 🧩
concat button and the budget hint all dead) — while the bundler's self-check still printed [OK] (it only checks
that the files **in** the list exist, never that any are **missing**). They are shipped now, and the bundler
gained the **reverse gate**: every version-controlled `.js` under `web/` must match the manifest one-to-one.

Also: the deep docs now cover **VA (cross-segment timbre accumulation)** and how it combines with the voice
anchor (bilingual).

**Action required**: no graph or wiring changes. **If you installed the minimal bundle (`dist/`), reinstall
it**; installs from the Comfy Registry / Manager are unaffected (that package is built from `.comfyignore` and
ships the whole `web/`).

---

## 0.6.27 — 2026-10-06

**接了声锚，音频参考窗也按素材自己定长 —— 原料就是声锚自身。**

旧行为：一旦接声锚，窗长退回 0.925 秒 ⇒ 接一段**多人录音**当锚时，只有最后一个人的音色进得来。
现在 `audio_ref_seconds = 0`（默认）按**声锚自身**往前取满上限（≤6 秒）⇒ 想「一段里多人各有音色」，
接一段多人录音当锚即可。

🔴 **有行为变化，无需动图**：锚不比 0.925 秒长 ⇒ **逐位同 0.6.26**（比的是步数不是秒）；锚更长 ⇒ 窗自动变长；想固定就显式填 `audio_ref_seconds`；缝区**钉住**的那 0.925 秒不受影响。

🆕 **上限为什么是 6 秒**：官方建议 2~12 秒。本包下限对齐 2 秒，**上限故意取 6 秒** —— 参考行随每步采样，更长更吃显存也更慢。

🆕 **VA（跨段音色累积）**：实验层、**默认关**。素材改从**本 run 已生成的段**按人累积；开关 = run 目录下的 `_va.json`（没有 = 关），台账可由 `voice_bank.py va-ledger` **从提示词生成**（零新依赖）。⚠️ 声锚与 VA 走同一条通道：接了声锚时 VA 默认跳过，要叠加填 `config.with_anchor = true`。

报告现在会点名参考窗的**原料**（声锚 / 上一段音频尾），复述风险按原料分开陈述。

**升级动作**：无需改图或连线。想让声锚参考窗更长：接一段多人录音当锚、`audio_ref_seconds` 留 `0`。

<!-- EN -->

**The voice-anchor reference window now auto-sizes from the anchor itself.**

Old behaviour: wiring an anchor made the window **fall back to 0.925 s** ⇒ with a **multi-speaker recording** as
the anchor, **only the last speaker's timbre** got in. Now `audio_ref_seconds = 0` (default) walks back through
the **anchor itself**, up to 6 s ⇒ wire a multi-speaker recording to keep several voices. No number to type.

🔴 **Behaviour change, no graph edits needed**: an anchor **no longer than 0.925 s** ⇒ **bit-identical to
0.6.26** (we compare steps, not seconds); a **longer** anchor ⇒ the window grows automatically; set
`audio_ref_seconds` explicitly to pin it; the **pinned** 0.925 s at the seam is **unaffected**.

🆕 **Why 6 s**: the official guideline is **2–12 s**. We align the lower bound at 2 s and **cap at 6 s** — the
reference row rides through every sampling step, so longer costs more VRAM and time.

🆕 **VA (cross-segment timbre accumulation)** — **experimental, off by default**: timbre accumulates per speaker
from segments **this run already produced**, instead of material you wire. Switch = `_va.json` in the run
directory (absent ⇒ off); the ledger can be generated **from your prompt** by `voice_bank.py va-ledger` (no new
dependency). ⚠️ **Anchor and VA share one channel**: with an anchor wired VA is skipped; to combine them set
`config.with_anchor = true`.

The report now names the window's **source** (anchor / previous tail) and states the recitation risk per source.

**Action required**: none — no graph or wiring changes. For a longer anchor reference window, wire a
multi-speaker recording as the anchor and leave `audio_ref_seconds` at `0`.

---

## 0.6.26 — 2026-10-04

**修一个你可能已经听出来的问题：`voice_bank.py` 采出的声锚变调。**

在 10-04 之后重采过声库的话，那批锚**音调偏高** —— 原因是**落盘数据的采样率与文件头
对不上**（16 kHz 的样本写进了 32 kHz 的头，播放快一倍）。
🔴 **文件头和登记信息看起来完全正常**，所以它一路混到了耳朵这里。
已修，并加两道自动检查（落盘前样本数自洽 + 落盘往返音高不变）。
顺带修了两个同类老问题：裁窗**可能越出素材末尾**、裁窗两端**可能落在停顿上**。

🔴 **旧声库是 16 kHz 的**：本来就偏低频，且**不会自动重采**。想换新口径请手动
`python tools/voice_bank.py collect <mp4> --name <角色> --bank <声库目录> --force`。

🔴 **一条物理限制**：锚从 16 k 提到 32 k，`−40dB` 带宽 4.7~5.3 → 9.0~10.5 kHz，
**再往上受音源限制**（H3 自己吐的音轨有效带宽约 4.9 kHz）。
**提高采样率不会凭空补出高频** —— 要更好音色得**换音源**（真人 / 原始录音）。
新采的锚会在 `report` 与 `voices.json` 的 `tail_clean` 里点名「尾部有停顿 → 音色打折」。

**升级动作**：**无需动作** —— 图、连线、节点参数全不变，本次只改开发者工具与文档。
旧声库要吃新口径就按上面那条命令 `--force` 重采。

<!-- EN -->

**Fixes a problem you may have already heard: anchors from `voice_bank.py` came out pitch-shifted.**

If you re-collected your voice bank after 10-04, those anchors were **an octave up** — the written
data's sample rate did not match the file header (16 kHz samples under a 32 kHz header, playing twice
as fast). 🔴 **The header and the registry looked perfectly normal**, which is why it survived to your
ears. Fixed, plus two automatic guards (sample-count self-consistency before writing, and a
write→read pitch round-trip). Two older bugs of the same family are fixed too: the crop window could
**run past the end of the source**, and its edges could land **in a pause** rather than on speech.

🔴 **If your existing anchors are 16 kHz**: they were low in bandwidth to begin with, and they are
**not re-collected automatically**. To move to the corrected behaviour, re-collect manually with
`--force` (command above).

🔴 **One physical limit**: moving an anchor from 16 k to 32 k raised its −40 dB bandwidth from
4.7–5.3 kHz to 9.0–10.5 kHz, but **beyond that the source is the ceiling** (the audio tracks H3 itself
reach only ~4.9 kHz). **A higher sample rate does not invent high frequencies that were never
captured** — a better voice needs a **better source** (a real person or the original recording).
Newly collected anchors now **say so** in the `report` and in `voices.json`'s `tail_clean`.

**Action required**: **none** — graphs, wiring and node parameters are all unchanged; this release
only touches developer tooling and documentation. Re-collect with `--force` to benefit.

---

## 0.6.25 — 2026-10-04

**多锚落地：一个音频参考槽装上一段多个人的音色。** 窗长改成**自动**（`0`），素材自己说话。

**修一段多人音色还原不足。** 旧口径下音频参考窗 = 视频钉住窗（22 帧 ⇒ **仅 0.93 秒**），
只装得下最后一个说话人；现在从上一段尾部往前**累计够 2 秒有声**就停（上限 6 秒，实测给 2~6 秒）。
**额度占用不变 —— 仍然只占官方 3 槽里的 1 个。**

- 新增 `audio_ref_seconds`（advanced）：`0` = **自动**（推荐，不用手填）；填值 = 强制指定秒数。
- **接了声锚就不生效**（窗的原料变成声锚，长度不由上一段决定）—— report 会点名。
- report 写明**按什么定的**（判据、有声格数、比旧口径长几倍）；静默素材 / NaN / 空音频都点名原因。

⚠️ **代价**：窗里**就是上一段的台词**，模型**理论上可能复述**（实测未观察到，但只覆盖一段素材一次生成）。
要旧行为就把 `audio_ref_seconds` 填 **0.93**。

**升级动作**：**不接声锚的图行为会变**（参考窗 0.93 s → 自动 2~6 s）。想保持旧行为就把
`audio_ref_seconds` 显式填 **0.93**；接了声锚的图**不受影响**。旧图不用重连。

<!-- EN -->

**Multi-anchor: one audio-reference slot now carries several speakers from the previous segment.**
The window length is now **automatic** (`0`) — the material decides.

**Fixes thin timbre reproduction in multi-speaker segments.** The audio-reference window used to equal the
video pin window (22 frames ⇒ **only 0.93 s**), which fits the last speaker only. It now walks back from the
previous segment's tail until it has accumulated **2 s of voiced content** (cap 6 s; measured 2–6 s in
practice). **The slot budget is unchanged — still one of the official three slots.**

- New `audio_ref_seconds` (advanced): `0` = **automatic** (recommended, nothing to type); a value forces that
  many seconds.
- **Wiring a voice anchor disables it** (the window's source becomes the anchor, so the previous segment no
  longer determines its length) — the `report` says so.
- The `report` states **how it was decided** (criterion, voiced-grid count, growth vs the old window); silent
  material / NaN / empty audio all name their reason.

⚠️ **Cost**: the window **is** the previous segment's dialogue, so the model **could in principle recite it**
(not observed in measurement, but that covers one segment and one generation). Set `audio_ref_seconds` to
**0.93** for the old behaviour.

**Action required**: graphs **without** a voice anchor change behaviour (reference window 0.93 s → automatic
2–6 s). Set `audio_ref_seconds` to **0.93** to keep the old window; graphs **with** a voice anchor are
unaffected. No rewiring needed.

## 0.6.24 — 2026-10-04

**孤立瞬态抑制：修好漏治与误治，默认行为变好。**

- **范围**：以前只在**段首 1.4 秒**内动手。实测一段 10 秒素材里 **15 / 17 处孤立峰落在范围外、全没治**，
  而报告只写「范围 = 前 1.408 s」—— 看不出漏了。现在**全段**动手。
- **素材底噪**：背景闸以前写死 −50 dBFS ⇒ 底噪更高的素材**整段静默失效**（只说「未命中」）。
  现在按**素材自身的背景**相对判断，报告里显示**实际生效**的闸值。
- **音量断崖**（音乐收尾、门声、场景切换）：以前会被当成孤立瞬态**误治**（约一半概率）。
  现在要求「邻域比事件**低一个量级**」才算伪影。
- 术语统一：面向用户与开发的文案里，「滴」一律写作**孤立瞬态**。
- 边界修复：空音频不再抛异常；含 NaN/Inf 的输入改为**明确报错**（以前会把 NaN 传遍整轨）。

**升级动作**：无。`declick_quiet_dbfs` 现在是**下限**（实际闸会按素材背景放宽），老图参数照旧可用。

<!-- EN -->

**Isolated-transient suppression: fixes both misses and false positives. Defaults get better.**

- **Scope**: it used to act only within the **first ~1.4 s**; measured on a 10 s segment,
  **15 of 17 isolated peaks fell outside that window and were never treated**
  (the report only said "range = first 1.408 s" — the miss was invisible). It now acts on the
  **whole segment**.
- **Material noise floor**: the background gate was a hard **−50 dBFS** ⇒ material with a higher
  floor failed **silently across the whole segment** (only ever reported as "no hit"). It is now
  judged **relative to the material's own background**, and the report shows the threshold actually used.
- **Volume cliffs** (music tails, door slams, scene-change level jumps): these used to be
  **misfired on** as isolated transients (roughly half the time). A candidate now has to sit an
  **order of magnitude above** its neighbourhood.
- Terminology: the colloquial word for it is unified as **isolated transient** throughout the copy.
- Boundary fixes: empty audio no longer raises; NaN/Inf input now **fails loudly** instead of
  smearing NaN across the whole track.

**Action required**: none. `declick_quiet_dbfs` is now a **lower bound** (the effective gate is
widened by the material's background), so existing graph values keep working.

## 0.6.23 — 2026-10-04

**一段里多人说话时，参考该怎么给 —— 顺手修掉一个「写错了也不报错」的坑。**

- 本包的声锚只覆盖**接缝**那**一个**说话人（这是它的设计边界）。**其余人的声音参考**请把音频接
  **官方参考节点**的 `ref_audio` 槽 —— 只有那条路会给每个参考生成一个文本标签 `<Audio j>`，
  提示词才引用得到它，也才能和 `<Subject N>` 对上。
- 本包注入的那一个参考是**事后追加**到模型侧条件里的 ⇒ **没有这个标签、提示词引不到它**
  （它只作为条件行被模型看到）。报告里现在会写明这一点，不再让人误以为
  「接了声锚就能在提示词里引用它」。
- 🔴 **编号按「已接线顺序」数，不是槽号**：只接了第 3 个槽 `ref_audio_2` 时，它在提示词里是
  **`<Audio 1>`**；照槽号写 `<Audio 3>` 会**指到空气，而且任何地方都不报错**。
  槽位跳号时，**桥节点标题会直接写出实际编号**（连续接满时不打扰你）。
- 接线前先用官方的音频裁剪节点裁到 **~0.9 秒**：参考行会跟着每一步采样，参考越长越慢。

**升级动作**：图 / 连线 / 参数都不用动；只有「其余人参考」这一种用法需要照上面走。

<!-- EN -->

**How to give voice references when several people speak in one segment — plus a fix for a trap that
never reported anything.**

- This pack's voice anchor covers exactly **one** speaker: the one at the seam. That is its design
  boundary. For the **other speakers**, wire their audio into the **official reference node's**
  `ref_audio` slots — that is the only path that emits a text label `<Audio j>` per reference, which is
  what the prompt uses to refer to it (and to pair it with `<Subject N>`).
- The one reference this pack injects is appended to the model side **after** tokenisation, so it has
  **no such label and the prompt cannot reference it** (the model only sees it as a conditioning row).
  The report now says so, instead of letting you assume "anchor wired ⇒ I can reference it in the prompt".
- 🔴 **Ordinals count wired slots in order, not slot numbers**: wire only the third slot (`ref_audio_2`)
  and it is **`<Audio 1>`** in the prompt; writing `<Audio 3>` **points at nothing and reports nothing
  anywhere**. When slots are non-contiguous, the **bridge node's title states the real ordinals**
  (and stays quiet when they match).
- Trim to **~0.9 s** with the official audio-trim node before wiring: reference rows ride through every
  sampling step, so longer references are slower.

**Action required**: nothing — no graph, wiring or parameter changes. Only the "other speakers" recipe
above is new.

---

## 0.6.22 — 2026-10-04

**新功能：治「安静背景里无缘无故的孤立瞬态」（declick），默认开。**

- 「音频缝」新增 `declick_ratio`（默认 4.0，**填 0 = 关闭、逐位直通**）。它只在**台词开始之前**动手，
  且要求那一小段比周围安静背景突出 4 倍以上 —— 判据判的是「**它周围有多静**」，不是「它本身多响」
  （语音的轻辅音也很短，只看响度会掐到台词）。
- 动手方式是把那一小段**用旁边的背景换掉**，而不是把音量拧小。拧小永远会剩一点，你听到的就是那一点。
- 实测那处孤立瞬态宽约 **8 毫秒**，而旧版把宽度上限写死 2 毫秒 ⇒ 它被判成「不是一声」直接丢弃，
  **这才是「孤立瞬态」一直还在的原因**。宽度上限现为 `declick_max_len_ms`（默认 50）。
- 新增 `cut_head_frames`：填上「本段会被裁掉多少帧」，才能治到「裁帧切断波形」造成的接缝孤立瞬态。
- ⚠️ 绝大多数素材会报告「未命中事件 ⇒ 逐位直通」—— 那是正常的，不是没生效。
- 🆕 官方音频参考只有 **3** 个槽位，本包在续接段会固定占用 **1** 个 ⇒ 3 槽填满时模型侧会看到 4 个。
  **桥节点标题上会直接挂警告**（不再只写日志 —— 大量用户不看日志）。

**升级动作**：图 / 连线 / 参数都不用动。想完全回到 0.6.21 的行为，把 `declick_ratio` 填 `0`。

<!-- EN -->

**New: kills the stray "tick" in a quiet background (declick), on by default.**

- `Audio Seam` gains `declick_ratio` (default 4.0; **set 0 to disable, bit-exact pass-through**). It only acts
  **before the dialogue onset**, and only where the sound stands ≥4× above the quiet background around it.
  The criterion is **how quiet its surroundings are**, not how loud it is — soft speech plosives are short
  bursts too, so judging by loudness alone eats dialogue.
- The action **replaces** that sliver with neighbouring background; it does not just turn the volume down.
  Turning it down always leaves a residue — and that residue is what you hear.
- The tick measures about **8 ms**, while the old build hard-coded a 2 ms width cap, so it was classified as
  "not a tick" and dropped — **that was why it kept coming back**. The cap is now `declick_max_len_ms` (50).
- New `cut_head_frames`: set it to how many frames this segment gets trimmed by, otherwise the seam tick
  caused by cutting through a waveform cannot be treated.
- ⚠️ Most material will report "no event matched ⇒ bit-exact pass-through". That is normal, not a failure.
- 🆕 The official audio-reference budget is only **3** slots, and this pack always occupies **1** on
  continuation stages — so with all 3 filled the model sees 4. **The bridge node title now shows a warning**
  instead of only logging it.

**Action required**: nothing — no graph, wiring or parameter changes. To get exactly the 0.6.21 behaviour,
set `declick_ratio` to `0`.

---

## 0.6.21 — 2026-10-03

**声锚现在会做「响度归一」—— 修「有锚的段整体比别的段响」。**

- 此前只把锚的**峰值**压到 0.9，但**模型会跟随锚的响度**：锚比段内人声响多少，生成段就比上一段响多少。
  实测段间跳变 **+6.4 dB**。
- 现按**有声部分的中位响度**归一到 −23 dBFS（放大与压制分别设上限；到不了就退回纯峰值归一，并在报告里说明）。
- 🔴 **有行为变化**：依赖「锚只压峰」的旧链路，段间响度会变 —— 这正是本次要修的那件事。
  想恢复旧行为用 `--no-voice-loudness`。**图 / 连线 / 参数都不用动。**
- 新增一条命令回答「这段的声锚该给谁」：`voice_bank.py advise`。
  规则 = **锚给「缝上第一个开口说话的人」**；同一个人续接不必接，缝上换人才必须接。

**升级动作**：不用动配置；介意段间响度变化的人看一眼 `--no-voice-loudness`。

<!-- EN -->

**Voice anchors are now loudness-normalised — fixes "every anchored segment is louder than the rest".**

- Only the anchor *peak* used to be capped at 0.9, but the **model follows the anchor's loudness**: the louder
  the anchor, the louder the generated segment. Measured step between segments: **+6.4 dB**.
- Anchors are now normalised to −23 dBFS by the **median loudness of their voiced parts** (gain and
  attenuation have separate caps; if the target cannot be reached it falls back to peak-only normalisation
  and says so in the report).
- 🔴 **Behaviour change**: pipelines that relied on peak-only anchoring will hear different inter-segment
  loudness — that is the fix. Use `--no-voice-loudness` to restore the old behaviour.
  **No graph, wiring or parameter changes.**
- New `voice_bank.py advise` answers "who should this segment's anchor be?".
  Rule = **anchor the first person who speaks at the seam**; the same speaker continuing does not need one,
  a speaker change at the seam requires it.

**Action required**: nothing to change; see `--no-voice-loudness` if you care about the loudness shift.

---

## 0.6.20 — 2026-10-02

**锚没有内容 ⇒ 当场报错（此前是静默的）。**

- 尾窗含 NaN/Inf，或几乎是常量（= 静音 / 空锚）⇒ 直接报错，并说明是「全为零」还是「常量」。
- 此前把**出词节点的 `LATENT` 输出**接到声锚输入上，会**静默生效**、报告里还写着「声锚生效」
  ⇒ 既丢了音频连续性，又什么都没换来。
- ⚠️ **想关掉声锚请拔线**（不接 = 逐位同旧版），**不要接一个空 latent**。图 / 连线 / 参数都不用动。

**升级动作**：不用动；若曾用空 latent 关声锚，请改为拔线。

<!-- EN -->

**An anchor with no content now fails loudly (it used to fail silently).**

- If the tail window contains NaN/Inf, or is nearly constant (= silence / an empty anchor), the node raises
  and says whether it was all-zero or constant.
- Feeding the text-node's `LATENT` output into the anchor input used to take effect **silently**, with the
  report still claiming the anchor was active — losing audio continuity and gaining nothing.
- ⚠️ **To turn the anchor off, unplug it** (unplugged = bit-exact same as before). **Never plug in an empty
  latent.** No graph, wiring or parameter changes.

**Action required**: nothing — but if you used to disable the anchor by feeding an empty latent, unplug it instead.

---

## 0.6.19 — 2026-10-02

**两件事：`run_id` 改一处全组跟随；`Copy Bridge` 新增可选「声锚」。**

- `run_id` 是「这部片子叫什么」，决定段文件落在哪个目录 —— 它原先在**六个节点**上各存一份、必须一字不差，
  改一处要手动改五处，漏改的那处会让桥去**另一个目录**找段文件。
- 现在改任一格，**同组其余格自动跟随**；同组出现**两个不同的名字时不猜**，而是拦住会提交的按钮
  并列出哪个节点是哪个名字。范围只限**同一个分组框**（一张图里放两部片子是正常用法）。
- 清空一格 **≠** 想清空全组（否则误删一个字符就会把整部片子的目录名清掉）。
- 🔴 **纯前端能力 ⇒ 必须刷新浏览器页面**（`Ctrl+F5`）。用 `/prompt` 提交 JSON 的脚本**不受影响也无需改**
  —— 脚本本来就把这一格写成同一个变量。
- `Copy Bridge` 新增**可选**输入 `voice_anchor`（LATENT）：跨说话人续接时锁定本段说话人的音色。
  **不接 = 逐位同旧版。** 多说话人长片建议接；单人 / 无对白片不用。

**升级动作**：不用动图，但**必须刷新浏览器页面**。

<!-- EN -->

**`run_id` syncs group-wide from one edit; `Copy Bridge` gains an optional voice anchor.**

- `run_id` names the film and decides which directory segment files land in. It used to be stored on **six
  nodes** and had to match character-for-character, so one edit meant five manual edits — and the missed one
  sent the bridge looking in the **wrong directory**.
- Editing any one of them now updates the rest of its group. If the group holds **two different names it
  refuses to guess** — it blocks the submitting buttons and lists which node carries which name. Scope is
  limited to the **same group box** (two films in one graph is normal usage).
- Clearing one field does **not** mean "clear the whole group" (otherwise deleting one character would rename
  the whole film's directory).
- 🔴 **This is front-end only ⇒ reload the browser page** (`Ctrl+F5`). Scripts submitting JSON via `/prompt`
  are unaffected and need no changes — they already use a single variable.
- `Copy Bridge` gains an optional `voice_anchor` (LATENT) input that pins the current speaker's timbre across
  a speaker change. **Unplugged = bit-exact same as before.** Recommended for multi-speaker long-form;
  unnecessary for single-speaker or silent films.

**Action required**: no graph changes, but you **must reload the browser page**.

---

## 0.6.18 — 2026-09-30

**修三处静默的段号 / 段序错误（成片会音画错段或段序错乱，画布一个字都不报）。**

- 「段号推进」漏了「音频缝」节点 ⇒ 跑第 2 段时音频缝仍以「第 1 段」自居，把第 1 段的音频文件
  **覆盖成了第 2 段的音频** ⇒ 拼接时第 1 段拿到第 2 段的音轨。现改为表驱动，并在状态栏点名同步了哪几类。
- 拼接挑「哪份 PCM 边车真的进了 mp4」原先按节点类型**猜**顺序；现改为读**提交图的数据流**（谁在下游谁优先），
  落选的写进报告。读不出提交图时**拒收全部边车、退回 mp4 解码** —— 宁可音轨多一代有损编码，也绝不配错段。
- 段记录跨轮残留 + 空洞被压实 ⇒ **后面的段被当成前面的段拼进成片**。现记录带真段号，
  重跑前段会丢掉其后记录，自动拼接遇到空洞**直接拒拼**并说明缺第几段。
- 画布菜单里的**节点标签改成纯英文**（`🔗 H3 Relay · Trim AV` 这样）。**老图不受影响**
  （工作流存的是类型名与你自设的标题）；中文文档正文保留中文词，另加「术语 ↔ 节点标签对照表」。
- 另：连跑按钮现在有**状态色带 + 阶段文字 + 按钮文字**三条互相独立的通道表明「点上没有」，
  并对重复点击做 0.4 秒防抖（双击 = 白烧一轮）。

**升级动作**：不用动图，但**必须刷新浏览器页面**；若你的图里有多个音频节点各落一份 PCM 边车，看一眼拼接报告确认挑对了。

<!-- EN -->

**Fixes three silent failures that mis-numbered or mis-ordered segments.**

- The stage counter missed the **Audio Seam** node, so while running stage 2 the seam still thought it was
  stage 1 and **overwrote stage 1's audio with stage 2's**. It is now table-driven, and the status line names
  what was synced.
- Picking which PCM sidecar actually made it into the mp4 used to **guess** from node types. It now reads the
  **submitted graph's data flow** (downstream wins) and lists the losers in the report. Unreadable graph ⇒
  all sidecars rejected, falling back to decoding the mp4 (one extra generation of lossy audio beats
  mis-assigned audio).
- Stale cross-run segment records meant **later segments were spliced in as earlier ones**. Records now carry
  real stage numbers, re-running an early stage drops the stale later ones, and automatic concat **refuses**
  to run with a hole, naming the missing stages.
- Canvas node labels are now **pure English** (`🔗 H3 Relay · Trim AV`). **Existing graphs are unaffected**
  (workflows store type names and your own titles).
- The run button now reports through **three independent channels** (title colour band + status text + button
  text), with a 0.4 s debounce (a double click used to burn a whole GPU round).

**Action required**: no graph changes, but you **must reload the browser page**; if your graph drops several
PCM sidecars, glance at the concat report to confirm the right one was picked.

---

## 0.6.17 — 2026-09-30

**把两个发布渠道（GitHub / Comfy Registry）规范成一条命令，并回头交叉验证。节点行为零变化。**

- 新增发布规范 `RELEASING.md` 与执行体 `tools/release.py`：`--go` = push → 等 CI **真绿** → 发 registry
  → 回头核对「两边是不是这一版」。
- 它**不认命令的退出码就算成功** —— 会回头查远端 main 是不是本版、registry 的版本列表里有没有本版。
- 修了一条静默失效的忽略规则（`node.zip` 那行带行尾注释 ⇒ 整行被当成模式 ⇒ 实际没生效）。

**升级动作**：不用动你的配置。

<!-- EN -->

**Both release channels are now driven by one command that cross-verifies them.**

- New `RELEASING.md` (the single source of truth) and `tools/release.py`: `--go` = push → wait for CI to
  **actually pass** → publish to the registry → verify that both sides really carry this version.
- It does **not** treat an exit code as proof of success — it re-checks the remote commit and the registry
  version list.
- Fixed a silently ineffective ignore rule (a trailing comment on the `node.zip` line turned the whole line
  into a pattern).

**Action required**: nothing to change on your side.

---

## 0.6.16 — 2026-09-30

**为「上架」做准备的一版：Comfy Registry 元数据就绪 + 产品名统一。节点行为零变化。**

- 画布节点菜单里的**分组名**由「H3 Relay Kit」改为「H3 Latent Relay」
  （**图、节点名、连线全未变**，只是菜单里的归类名）。
- registry 元数据（PublisherId / DisplayName / Icon / license 形态）就绪，
  安装 id 为 `h3-latent-relay`。
- ⚠️ 版本号在 `0.6.9` ~ `0.6.15` 之间**从未单独发布过 registry 版本**，本版是首次上线。

**升级动作**：不用动你的配置。

<!-- EN -->

**Groundwork for shipping: Comfy Registry metadata and product naming.**

- The node-menu **category** changed from "H3 Relay Kit" to "H3 Latent Relay"
  (**graphs, node names and wiring are all unchanged** — only the grouping label in the menu).
- Registry metadata (PublisherId / DisplayName / Icon / license form) is in place; the install id is
  `h3-latent-relay`.
- ⚠️ No registry release was published for `0.6.9` ~ `0.6.15`; this is the first published version.

**Action required**: nothing to change on your side.
