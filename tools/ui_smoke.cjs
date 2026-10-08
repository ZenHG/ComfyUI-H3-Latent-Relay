/**
 * H3 Relay 前端交互冒烟门（0.6.31 新增）——**浏览器实机点按钮**，零 GPU。
 *
 * 🔴 为什么必须有这道门（规范 §二·A 新增条目）：
 *   「后端 / API 全绿」≠「前端功能可用」。本包 8 个节点的方法全绿、518 条 Python 断言全绿、
 *   三个 JS 单测全绿、`/h3relay/health` 正常 —— 而同期前端有两个功能**根本不可用**：
 *     ① `⏩ 连跑` / `▶ Run` 必抛 `TypeError: CHAINS.filter is not a function`（0.6.12 起）
 *     ② `run_id` 同步钩子**从未挂上**（挂在一个宿主不调用的扩展点上，0.6.19 起）
 *   两者都**不在任何 CI 期望值的覆盖范围内** —— 它们是"绿着但坏的"那一类。
 *
 * 这道门怎么做到**零 GPU**：把图上的 `run_id` **故意弄成冲突**（两个不同的非空值）⇒
 *   所有"会提交 prompt"的按钮（Run / Approve / 连跑 / 续跑）都会在 `runIdPreflight`
 *   被**拦住** ⇒ 不排队、不采样。剩下两个按钮（拼接 / Stop / Reset）本身不提交。
 *   ⇒ 全程断言 `/queue` 始终为空。
 *
 * 用法：
 *   node tools/ui_smoke.cjs                       # 默认 http://127.0.0.1:8188
 *   node tools/ui_smoke.cjs --base-url http://127.0.0.1:8189
 *   H3RELAY_CHROMIUM=/path/to/chrome node tools/ui_smoke.cjs
 *
 * ⚠️ 找不到浏览器 / 连不上宿主 ⇒ **打印醒目 SKIP 并以 0 退出**（不假装通过）。
 *   想让它进 CI，前置条件是「CI 里跑着一个 ComfyUI」+「装了 chromium」——
 *   本仓 CI 目前**两者都没有**，见 LOCAL 规范 §七-22。
 */
'use strict';
const path = require('node:path');
const fs = require('node:fs');

const KIT = path.dirname(__dirname);                 // 仓库根
const args = process.argv.slice(2);
const argv = (k, d) => {
    const i = args.indexOf(k);
    return (i >= 0 && args[i + 1]) ? args[i + 1] : d;
};
const BASE = argv('--base-url', process.env.H3RELAY_BASE_URL || 'http://127.0.0.1:8188').replace(/\/$/, '');
const CHROME = process.env.H3RELAY_CHROMIUM || '';   // 留空 = 让 playwright 自己找

let pass = 0, fail = 0;
const ok = (name, cond, detail = '') => {
    if (cond) { pass += 1; console.log(`  [OK]   ${name}`); }
    else { fail += 1; console.log(`  [FAIL] ${name}${detail ? '  —— ' + detail : ''}`); }
};
const SKIP = (why) => {
    console.log('');
    console.log('======================================================================');
    console.log(`SKIP：${why}`);
    console.log('  ⇒ 本次**没有**验证前端按钮。想跑它：起一个 ComfyUI（' + BASE + '）');
    console.log('    并确保 playwright 能找到浏览器（H3RELAY_CHROMIUM 可指定路径）。');
    console.log('======================================================================');
    process.exit(0);
};

(async () => {
    // ---- 0. 依赖与浏览器 -------------------------------------------------
    // ⚠ `require('playwright')` 只按**脚本所在目录向上**找 node_modules。
    //   而这台机器的 playwright 装在 agent 自己的 workspace 里（与本仓不同盘）⇒ 裸 require
    //   必定失败。所以这里**显式列出候选路径**，并在全失败时把「试过哪些」打印出来
    //   —— 否则使用者只会看到一句"没装 playwright"，而真相是"装在别处"。
    function loadPlaywright() {
        const tries = [];
        const homedir = require('node:os').homedir();
        const cands = [
            process.env.H3RELAY_NODE_MODULES && require('node:path').join(process.env.H3RELAY_NODE_MODULES, 'playwright'),
            require('node:path').join(homedir, '.workbuddy', 'binaries', 'node', 'workspace', 'node_modules', 'playwright'),
            'playwright',
        ].filter(Boolean);
        for (const c of cands) {
            try { return { mod: require(c), from: c }; } catch (e) { tries.push(c); }
        }
        return { mod: null, tried: tries };
    }
    const pw = loadPlaywright();
    if (!pw.mod) {
        SKIP('找不到 playwright（试过：' + pw.tried.join(' | ') + '）\n' +
            '  ⇒ 装一个：npm i playwright；或设 H3RELAY_NODE_MODULES 指向装有它的 node_modules');
    }
    const { chromium } = pw.mod;
    console.log('playwright：' + pw.from);

    const launchOpt = { headless: true, args: ['--no-proxy-server', '--disable-gpu'] };
    if (CHROME) launchOpt.executablePath = CHROME;

    /**
     * 扫 playwright 的浏览器缓存，找**任意一个**可用的 chromium。
     * 为什么要扫而不是用 `chromium.executablePath()`：后者返回的是"**这个 playwright 版本**
     * 期望的路径"（本机 playwright 1.63 期望 `chromium-1243`，而缓存里只有 `chromium-1228`）
     * ⇒ 直接用它会得到"找不到浏览器"，而浏览器其实就在硬盘上。
     * 🔴 排序按版本号**从新到旧**，别拿到老版本（前端行为差异会伪装成 bug）。
     */
    function findChromium() {
        const root = process.env.PLAYWRIGHT_BROWSERS_PATH
            || path.join(process.env.LOCALAPPDATA || process.env.HOME || '', 'AppData', 'Local', 'ms-playwright');
        if (process.platform === 'linux') {
            return path.join(root, 'chromium_headless_shell-*/chrome-linux/headless_shell');
        }
        let dir = root;
        try { dir = require('node:os').homedir().replace(/\\/g, '/') + '/AppData/Local/ms-playwright'; } catch (e) { /* 用回退值 */ }
        const candidates = [];
        try {
            for (const name of fs.readdirSync(dir)) {
                const m = /^chromium-(\d+)$/.exec(name);
                if (m) candidates.push({ ver: Number(m[1]), file: path.join(dir, name, 'chrome-win64', 'chrome.exe') });
            }
        } catch (e) { /* 目录不存在 ⇒ 没有缓存 */ }
        candidates.sort((a, b) => b.ver - a.ver);
        return candidates.length ? candidates[0].file : '';
    }

    let browser = null, lastErr = '';
    const found = findChromium();
    const chain = [launchOpt];
    if (found && fs.existsSync(found)) chain.push({ ...launchOpt, executablePath: found });
    for (const opt of chain) {
        try { browser = await chromium.launch(opt); if (opt.executablePath) console.log('浏览器：' + opt.executablePath); break; }
        catch (e) { lastErr = String(e).split('\n')[0].slice(0, 160); }
    }
    if (!browser) SKIP('找不到可用的浏览器（' + lastErr + '）');

    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
    const pageErrors = [];
    const consoleErrs = [];
    page.on('pageerror', (e) => pageErrors.push(String(e).slice(0, 200)));
    page.on('console', (m) => { if (m.type() === 'error') consoleErrs.push(m.text().slice(0, 160)); });

    console.log('== H3 Relay 前端交互冒烟（零 GPU）==');
    console.log('宿主：' + BASE);

    // ---- 1. 宿主活着吗 ---------------------------------------------------
    let health = null;
    for (let i = 0; i < 30; i++) {
        try {
            const r = await page.request.get(BASE + '/h3relay/health', { timeout: 5000 });
            if (r.ok()) { health = await r.json(); break; }
        } catch (e) { /* 还没起来 */ }
        await page.waitForTimeout(2000);
    }
    if (!health) { await browser.close(); SKIP('宿主没起来（' + BASE + '/h3relay/health 无响应）'); }
    ok('宿主在线且本包已加载（v' + health.version + ' / ' + health.nodes_registered + ' 节点）',
        health.nodes_registered === 8);

    // ---- 2. 打开页面 + 载入图 -------------------------------------------
    await page.goto(BASE + '/', { waitUntil: 'domcontentloaded', timeout: 90000 });
    let ready = false;
    for (let i = 0; i < 40; i++) {
        if (await page.evaluate(() => !!(window.app && window.app.canvas))) { ready = true; break; }
        await page.waitForTimeout(1000);
    }
    if (!ready) { await browser.close(); SKIP('前端没就绪（window.app 没出现）'); }

    // ⚠️ 必须用 **fullflow** 那张：`minimal_relay_official.json` 里**没有 Chain 节点**
    //   （它是最小接线图，7 个按钮一个都没有）⇒ 用它跑本门会得到"no-button"的假绿。
    //   fullflow 是仓内**唯一**把 Chain + 六类 run_id 节点都串起来的图。
    const wfPath = path.join(KIT, 'examples', 'fullflow_second_pass_latent_upscale_ui.json');
    if (!fs.existsSync(wfPath)) { await browser.close(); SKIP('找不到 ' + wfPath); }
    const wfText = fs.readFileSync(wfPath, 'utf8');
    await page.evaluate(async (t) => {
        await window.app.loadGraphData(JSON.parse(t), true, false, 'smoke');
    }, wfText);
    await page.waitForTimeout(6000);

    const snap = () => page.evaluate(() => {
        const g = window.app.graph;
        const chain = g._nodes.find((n) => n.type === 'H3RelayChain');
        const runs = g._nodes.filter((n) => String(n.type).startsWith('H3Relay'))
            .map((n) => {
                const w = (n.widgets || []).find((x) => x.name === 'run_id');
                return { id: String(n.id), type: n.type.replace('H3Relay', ''), hook: !!(w && w.__h3RunIdHook), v: w ? String(w.value) : null };
            });
        const wd = (nm) => { const w = (chain.widgets || []).find((x) => x.name === nm); return w ? w.value : null; };
        return {
            buttons: (chain.widgets || []).filter((w) => w.type === 'button').map((w) => w.name),
            status: String(wd('status') || ''),
            stage: wd('stage_index'),
            runs,
            queue: null,
        };
    });

    let s = await snap();
    if (s.buttons.length === 0) {
        await browser.close();
        SKIP('这张图里没有 Chain 节点（按钮无从谈起）—— 换一张图或检查 examples/');
    }
    const queueEmpty = async () => {
        const q = await page.evaluate(async (b) => (await (await fetch(b + '/queue')).json()), BASE);
        return (q.queue_running || []).length === 0 && (q.queue_pending || []).length === 0;
    };

    // ---- 3. 结构：七个按钮 + 钩子 ---------------------------------------
    console.log('\n[1] 结构');
    ok('Chain 节点有 7 个按钮（Run/Approve/连跑/续跑/Stop/拼成一条/Reset）',
        s.buttons.length === 7, '实得 ' + s.buttons.length + ' 个：' + s.buttons.map((b) => b.slice(0, 6)).join('、'));
    const hooked = s.runs.filter((r) => r.hook).length;
    const withWidget = s.runs.filter((r) => r.v !== null).length;
    // ⚠ 报告里只列「**有 run_id 槽位却没挂上**」的：LatentUpscale / Post 这两类
    //   **本来就没有 run_id**（它们的 `w` 是 undefined），混进去会让报告误导人去查一个不存在的 bug。
    ok(`带 run_id 的节点全部挂上同步钩子（${hooked}/${withWidget}）`,
        withWidget === 0 || hooked === withWidget,
        '没挂上的：' + s.runs.filter((r) => r.v !== null && !r.hook).map((r) => r.id + ':' + r.type).join('、')
        + `（另有 ${s.runs.filter((r) => r.v === null).length} 个节点本来就没有 run_id 槽位，不参与）`);

    // ---- 4. run_id 同步 = 全图（0.6.31 的核心） --------------------------
    console.log('\n[2] run_id 同步范围（默认全图，节点摆哪儿都同步得到）');
    const BRIDGE = '961';
    await page.evaluate((id) => {
        const n = window.app.graph._nodes.find((x) => String(x.id) === id);
        const w = n.widgets.find((x) => x.name === 'run_id');
        w.value = 'smokeA';
        if (typeof w.callback === 'function') w.callback('smokeA', n, undefined, undefined, w);
    }, BRIDGE);
    await page.waitForTimeout(1500);
    s = await snap();
    const others = s.runs.filter((r) => r.id !== BRIDGE && r.v !== null);
    const synced = others.filter((r) => r.v === 'smokeA').length;
    ok(`改桥的 run_id ⇒ 其它节点全跟上（${synced}/${others.length}）`,
        others.length === 0 || synced === others.length,
        '没跟上的：' + others.filter((r) => r.v !== 'smokeA').map((r) => r.id + ':' + r.type + '=' + r.v).join('、'));

    // ---- 5. 制造冲突 ⇒ 点会提交的按钮，必须全被拦住 -----------------------
    console.log('\n[3] 七个按钮逐个点（先制造 run_id 冲突 ⇒ 会提交的按钮必须在闸前停住）');
    await page.evaluate((bridgeId) => {
        const g = window.app.graph;
        const chain = g._nodes.find((n) => n.type === 'H3RelayChain');
        const set = (node, v) => { const w = node.widgets.find((x) => x.name === 'run_id'); if (w) w.value = v; };
        set(chain, 'conflictA');
        const bridge = g._nodes.find((n) => String(n.id) === bridgeId);
        set(bridge, 'conflictB');
        for (const n of g._nodes) {
            if (!String(n.type).startsWith('H3Relay') || n === chain || n === bridge) continue;
            set(n, 'conflictA');
        }
    }, BRIDGE);
    await page.waitForTimeout(800);

    // ⚠ 按钮的 callback 是 **async**：里面 `await runIdPreflight(...)`。
    //   原来的 `try { w.callback(...) }` **抓不到 Promise 拒绝** ⇒ 按钮内部炸了也会返回 'clicked'
    //   （今天实测：变异态下这条判据只能靠"有没有排队"间接发现，报不出原因）。
    //   ⇒ 这里 `await` 它并 catch ⇒ 失败原因如实报出来。
    const clickBtn = (frag) => page.evaluate(async (f) => {
        const n = window.app.graph._nodes.find((x) => x.type === 'H3RelayChain');
        const w = n.widgets.find((x) => x.type === 'button' && (x.name || '').includes(f));
        if (!w) return 'no-button';
        try { await w.callback(w.value, n, undefined, undefined, w); return 'clicked'; }
        catch (e) { return 'threw: ' + String(e).slice(0, 140); }
    }, frag);

    for (const [frag, expectSubmit] of [['Run', true], ['连跑', true], ['Approve', true], ['续跑', true]]) {
        pageErrors.length = 0;
        const r = await clickBtn(frag);
        await page.waitForTimeout(2500);
        const q = await queueEmpty();
        const st = await snap();
        // 🔴 按钮被 `guard()` 包着：内部异常会被 **catch 掉并写进 status**（不抛给页面）。
        //   ⇒ 判断"按钮内部炸了"要**读 status 里有没有「内部出错」**，而不是等 pageerror。
        //   （今天实测：变异态下 pageerror 为空、r 仍是 clicked，只有 status 露馅。）
        const internalErr = /内部出错/.test(st.status);
        ok(`「${frag}」点了没内部出错、且没排队（冲突时应被 run_id 闸拦住）`,
            r === 'clicked' && pageErrors.length === 0 && q && !internalErr,
            `r=${r} queue空=${q} status=${JSON.stringify(st.status.slice(0, 90))} pageerr=${JSON.stringify(pageErrors.slice(0, 1))}`);
    }

    // 不提交的三个：Stop / Reset / 拼成一条
    for (const frag of ['Stop', 'Reset']) {
        pageErrors.length = 0;
        const r = await clickBtn(frag);
        await page.waitForTimeout(1500);
        ok(`「${frag}」点了不抛异常`, r === 'clicked' && pageErrors.length === 0,
            `r=${r} ${JSON.stringify(pageErrors.slice(0, 1))}`);
    }
    pageErrors.length = 0;
    const rCat = await clickBtn('拼成一条');
    await page.waitForTimeout(4000);
    const qAfterCat = await queueEmpty();
    const stCat = await snap();
    ok('「拼成一条」点了没内部出错、也不排队（它只发 /h3relay/concat）',
        rCat === 'clicked' && pageErrors.length === 0 && qAfterCat && !/内部出错/.test(stCat.status),
        `r=${rCat} status=${JSON.stringify(stCat.status.slice(0, 80))} ${JSON.stringify(pageErrors.slice(0, 1))}`);
    ok('「拼成一条」给了明确结论（成功或明确拒绝，不许静默）',
        stCat.status.trim().length > 0, JSON.stringify(stCat.status.slice(0, 60)));

    // ---- 6. 冲突解除后，Run 应当恢复"能排队"（证明闸不是永久挡路） -----
    console.log('\n[4] 清空 Chain.run_id 后点 Run ⇒ 走「补齐+广播」那条路并真排队（随后立刻清队列）');
    await page.evaluate((bridgeId) => {
        const g = window.app.graph;
        const set = (n, v) => { const w = n.widgets.find((x) => x.name === 'run_id'); if (w) w.value = v; };
        for (const n of g._nodes) if (String(n.type).startsWith('H3Relay')) set(n, 'smokeA');
        set(g._nodes.find((n) => String(n.id) === bridgeId), 'smokeA');
        // 🔴 关键：**把 Chain 的 run_id 清空**（其余节点一致）。
        //   ⇒ preflight 判定"唯一非空值是 smokeA、Chain 还空着" ⇒ 必须走**补齐 + 广播**那条路
        //     （`runIdPreflight` → `applyRunIdPlan` → `notifyRunId` → `chainStateNear`）。
        //   ⚠️ 这正是 0.6.12 那个 `CHAINS.filter is not a function` 的**唯一触发路径** ——
        //     本门第一版**没覆盖**它，跑变异测试时 14/0 全绿（= 门是空的），
        //     补这一步后才真的红。这是"判据能不能被空壳骗过"的活样本（规范 §三·3.2）。
        set(g._nodes.find((n) => n.type === 'H3RelayChain'), '');
    }, BRIDGE);
    await page.waitForTimeout(800);
    const qBefore = await page.evaluate(async (b) => (await (await fetch(b + '/queue')).json()), BASE);
    if ((qBefore.queue_running || []).length === 0 && (qBefore.queue_pending || []).length === 0) {
        const r = await clickBtn('Run');
        await page.waitForTimeout(3000);
        const q = await page.evaluate(async (b) => (await (await fetch(b + '/queue')).json()), BASE);
        const nRun = (q.queue_running || []).length + (q.queue_pending || []).length;
        const stR = await snap();
        ok('补齐+广播路径能走通（Chain.run_id 为空时应被补齐并排队；status 不含「内部出错」）',
            r === 'clicked' && nRun >= 1 && !/内部出错/.test(stR.status),
            `r=${r} 队列=${nRun} status=${JSON.stringify(stR.status.slice(0, 120))}`);
        // 立刻清掉，绝不留一个占着 GPU 的任务
        await page.evaluate(async (b) => {
            await fetch(b + '/queue', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ clear: true }) });
            await fetch(b + '/interrupt', { method: 'POST' });
        }, BASE);
        await page.waitForTimeout(2000);
    } else {
        ok('解除冲突后 Run 能排队', false, '宿主队列本来就不空，跳过（避免干扰别人的跑批）');
    }

    // ---- 7. 全程没有未捕获异常 ------------------------------------------
    console.log('\n[5] 全程');
    ok('全程无未捕获 JS 异常', pageErrors.length === 0, JSON.stringify(pageErrors.slice(0, 3)));

    await browser.close();
    console.log('\n======================================================================');
    console.log(`结果：通过 ${pass} / 失败 ${fail}`);
    console.log('======================================================================');
    process.exit(fail ? 1 : 0);
})().catch((e) => {
    console.error('SMOKE-ERR', String(e).slice(0, 600));
    process.exit(1);
});