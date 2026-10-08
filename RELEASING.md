# 发布规范（RELEASING）

> 本文件是**发布流程的唯一真相源**。执行体 = [`tools/release.py`](tools/release.py)（两边必须一致；
> 改了流程先改这里）。CI 的期望数、门槛清单见 [`docs/08-testing.md`](docs/08-testing.md)。

## 0. 一句话

**一次发布 = 两个渠道同时更新，且回头验证过。**

```bash
python tools/release.py            # 预演（本地前置 + 改动面，不推不发）
python tools/release.py --go       # 真发布：push → 等 CI 绿 → GitHub Release → registry publish → 交叉验证
python tools/release.py --verify-only   # 只回答「两边现在同步吗」（纯只读，**不跑 ① 前置**）
python tools/release.py --release-only  # 只补建 GitHub Release（幂等；不 push、不发 registry）
python tools/release.py --go --skip-ci-wait   # 🔴 跳过「等 CI 绿」那一步（**默认不跳**）
python tools/release.py --set-token     # 轮换 registry PAT（从 stdin 读，不发布；见 §5）
```

> 🔴 **`--verify-only` 与「预演」不是一回事**：前者**只跑 ⑥ 交叉验证**（纯只读），
> **不跑 ① 前置**（干净工作树 / 版本四处 / `review_050` / `en_sync`）。
> 想看前置预演，**不带任何参数**跑 `python tools/release.py`。
>
> 🔴 **`--skip-ci-wait` 是逃生门，不是常规路径**：跳过它 = **在没验证过的提交上发 registry**，
> 而 registry 的 changelog 发出去就改不回来（见 §6）。只在 CI 本身故障且你**另行确认过门槛全绿**时才用。
>
> `--release-only` 为什么存在：Release **可补建**（正文可改、可删可重建），registry 的 changelog **不可**。
> ⇒ 补 Release 时**绝不能**顺带再发一次 registry（同版本会被拒，白把流程弄红）。

🔴 **顺序是故意的**：唯一"发出去就改不回来"的动作（registry 的 changelog，见 §6）**排在最后**，
所以中间任何一步失败，都停在"什么都没弄脏"的状态上。

## 1. 两个渠道各是什么

| 渠道 | 发布物 | "完成"的判据 |
|---|---|---|
| **GitHub**（`ZenHG/ComfyUI-H3-Latent-Relay`） | ① 提交历史 + 源码 ② **Release**（`vX.Y.Z` tag + 正文） | 远端 `main` 含本地 HEAD，**那一轮的 CI 是绿的**，且 Releases 页面上**有本版**（正文 = `RELEASE-NOTES.md`，见 §6） |
| **Comfy Registry**（`zenhg/h3-latent-relay`） | `.comfyignore` 筛出的 zip（运行期文件，**不含 docs/tests/tools**） | `api.comfy.org/nodes/h3-latent-relay/versions` 里**能查到本版版本号** |

🔴 **为什么必须成对**：任何一个单独成功都是**半成品**，而且两边都不报错 ——
- 只 push 不 publish ⇒ GitHub 是新版、Manager 里还是旧版（用户装到的和文档写的不是同一个东西）
- 只 publish 不 push ⇒ registry 那版指向的代码在远端不存在（无法复核、issue 无从查起）
- push 了但 CI 红 ⇒ 已公开的提交没过自检

## 2. 发布前置（缺一项就别开始）

1. **工作树干净** —— 发出去的必须是**提交态**（`tools/release.py` 会拦）。
2. **版本号四处一致**（`review_050` 的 H1 盯着）：
   `__init__.py` 的 `__version__` · `pyproject.toml` 的 `version` · `CHANGES.md` 顶部 `## x.y.z` ·
   `README.md` 首部 `| 版本 | **x.y.z** |`。
3. **两份记录都写了本版**：
   - `CHANGES.md` 顶部那一节 = 本版（面向开发者；版本不符会被 H1 报出来）。
   - `RELEASE-NOTES.md` 有本版一节、**中英双语、≤2000 字符**（面向用户；**registry 的 changelog 与
     GitHub Release 正文都取它**）。格式与机器判据见 **§6**，`tools/release.py` 会在 **push 之前**拦住。
4. **本地门槛全绿**：跑 `h3relay_check_all.py`（本机工具，在**仓库之外的** tools 目录里；
   12 项，与 CI 逐步对应）。`tools/release.py` 自己只跑最会翻车的两项（`review_050` / `en_sync`），
   全量请你手跑。
5. **Registry 元数据合规**（`review_050` 的 H3k 盯着）：`name` 不带 `ComfyUI`、`license` 为
   `{ file = … }`/`{ text = … }`、`PublisherId` 非占位、`Icon` 是能取到的 https 直链、
   `.comfyignore` 排开发件而**不排运行期目录**。

## 3. 执行体做的六步（`tools/release.py --go`）

| 步 | 动作 | 失败时的状态 |
|---|---|---|
| ① 前置 | 干净工作树 · 版本四处 · `review_050` · `en_sync` · **`RELEASE-NOTES.md` 本版一节（§6）** | 什么都没发生 |
| ② GitHub push | `git push origin HEAD:refs/heads/main`（失败自动换 HTTP/1.1 / schannel / **去代理直连**重试） | 什么都没发生 |
| ③ 等 CI | 找 `headSha == 本地 HEAD` 的那一轮，`gh run watch --exit-status` | **已 push、未 publish** ⇒ 修完重跑，安全 |
| ④ GitHub Release | `gh release create vX.Y.Z --target <HEAD> --notes-file <RELEASE-NOTES 生成>`。**幂等**：已存在就不动（正文可改，但改不改是人的决定）。正文还会自动补上「上一次 Release 之后压着的版本」（§6.5） | 已 push、未 Release ⇒ 重跑；registry 完全没被碰 |
| ⑤ Registry | `comfy node validate` → `node pack` 预览（开发件混入即拒）→ `publish --token --changelog`（changelog = `RELEASE-NOTES.md` 本版一节，见 §6） | 已 push、未 publish ⇒ 重跑 |
| ⑥ 交叉验证 | 远端 `main` 含 HEAD ？registry 版本列表含本版 ？Releases 有本版 ？（给了 `H3RELAY_DEPLOY` 则再比部署副本） | 会**点名**哪一项没过 |

> ⚠️ **③ 的失败是本流程唯一的"半成品"状态**（GitHub 有了、registry 没有）。这是**有意如此**：
> 宁可在 registry 上少一版，也不要在 CI 红的提交上发版。修完再跑一遍即可，registry 那边不会被弄脏。
> ⑦ 之后还有一步**可选的**「同步本机部署副本」（仅当给了 `H3RELAY_DEPLOY`）；它不是公开渠道，只提示不判红。

## 4. 失败处置速查

| 症状 | 含义 | 怎么办 |
|---|---|---|
| `git: 'remote-https' is not a git command` | 用的是便携版 git（缺助手） | 设 `H3RELAY_GIT` 指向完整版 git 再跑 |
| `CONNECT tunnel failed, response 502` | 本机代理对 git 的 CONNECT/POST 打回 | 脚本会自动**去代理直连**重试一次；仍失败就手动 `env -u http_proxy … git push …` |
| `comfy node publish` 报 publisher 不匹配 | registry 上 `PublisherId` 与 `pyproject.toml` 不一致 | 改成网站上 `@` 后面那串（**两边都不可改，别猜**） |
| `comfy node validate` 报警 | 见 [`docs/08-testing.md`](docs/08-testing.md) 的 S 规则说明 | 行级 `# noqa` + 在代码里写清理由，**别为消警告换拼法** |
| registry 说版本已存在 | 同版本不能重发 | bump 一版（**已发布的版本与 changelog 都不可改**） |
| `gh` 找不到 / 建 Release 失败 | 环境里没有 gh 或没登录 | 装/登录 gh。⚠️ 这一步**在 registry 之前**，所以停在这里 = **什么都没弄脏** |
| `Release vX.Y.Z 已存在` | tag 上已有 Release | 脚本**幂等跳过**（不擅自覆盖正文）；要改正文去网页上改 |

## 5. 密钥（Registry PAT）

- 落点：**仓库根 `.comfy_registry_token`**（一行纯文本）。可用 `COMFY_REGISTRY_PAT` 或
  `COMFY_REGISTRY_PAT_FILE` 覆盖。
- 三重保护：`.gitignore`（git 不收）· `.comfyignore`（万一 `git add -f` 强塞也剔出发布包）·
  `tools/release.py` **发布前真查**（`git ls-files` + `git check-ignore`，任一不符**直接拒发**）。
- 「建 Publisher + 生成 API Key」**只能在 <https://registry.comfy.org> 网页上做**（GitHub OAuth，
  没有任何 API/CLI 路径；`comfy-cli` 只吃 PAT）。PublisherId **发布后不可改**。
- **发布者资料（description / logo / website / source_code_repo / support）也只能在网页上改** ——
  实测（2026-09-30）：`PUT /publishers/{id}` 存在且接受 PUT，但**发布用 PAT 不被接受**：
  放 `Authorization: Bearer <PAT>` 得 `401 user not found`，放请求体得 `401 missing auth token`。
  它要的是**用户会话**令牌，而那只有浏览器登录后才有。⇒ 别在这上面浪费时间，也别把它写进自动化。
- 密钥进过任何共享记录（聊天/工单/截图）⇒ **立刻作废重发**，换掉文件内容即可。
- **轮换**（别用 `echo … > 文件`，那会把明文留在 shell 历史里）：

  ```bash
  # ① 在 registry 网站生成新 Key  ＞ ② 装进本机（从 stdin 读、不回显、不进 shell 历史）
  python tools/release.py --set-token
  # ③ 再去网站把**旧 Key 作废** —— 本地删文件 ≠ 远端失效
  ```

  `--set-token` 写盘后会**立刻**验一遍忽略规则，且**不做任何网络动作**（不会顺带发布）。
- 🔴 **别往 ignore 文件里写行内注释**（`模式  # 注释` 会被当成模式的一部分 ⇒ **静默不忽略**）。
  加完行**必须** `git check-ignore -v <路径>` 验一遍。

## 6. 版本与发布说明（`RELEASE-NOTES.md`）

### 6.1 版本号

- `version` 用语义化三位：`X.Y.Z`，且必须**四处一致**（§2 第 2 条）。
- 🔴 **`name` 与「已发布的 version / changelog」都不可改** —— registry 的版本接口只收 `GET`/`OPTIONS`
  （`PATCH` 实测 **405**）。**发出去就改不回来**，这是本文件所有「发布前把关」的根因。

### 6.2 两份记录、两个读者（**别混**）

| 文件 | 读者 | 写什么 |
|---|---|---|
| `CHANGES.md` | 开发者 / 未来维护者 | 实现取舍 · 实测证据链 · 机检编号 · 翻车过程。允许本机细节（H3e 对它豁免） |
| `RELEASE-NOTES.md` | **用户** | **用户能感知的变化**。两个渠道的用户可见文案**都取它**：registry 的 changelog + GitHub Release 正文 |

🔴 **不要把 `CHANGES.md` 的整节搬去 registry**。2026-10-05 之前就是这么做的，结果 registry 的版本页
是一大坨内部细节、**而且没有英文** —— 而它已经改不回来了。（0.6.16~0.6.22 就停在那副样子。）

### 6.3 每节的格式（`tools/release.py` **硬校验**，不符即拒绝发布）

```markdown
## X.Y.Z — YYYY-MM-DD

**<中文：一句话说清本版最该知道的事>**

- 要点，每条一行；🔴 **有行为变化就点明「要不要动配置」**。

**升级动作**：图 / 连线 / 参数都不用动。（或写明要做什么）

<!-- EN -->

**<English: one line>**

- Bullets, one per line; 🔴 flag any behaviour change.

**Action required**: nothing — no graph, wiring or parameter changes.
```

机器判据（`release.py::changelog_text`，在 **push 之前**跑）：

1. 存在 `## <pyproject.version>` 一节 —— **没有就直接拒绝发布**（**不回退**到 `CHANGES.md`：静默回退
   正是这次要修的病）。
2. `<!-- EN -->` **恰好一次**；中文在前、英文在后，两段都非空。
3. 中文段 ≥ 40 汉字；英文段 ≥ 40 英文词**且不含汉字**（英文要**独立成文**，不是逐句直译）。
4. 两段各自的结尾行（`**升级动作**` / `**Action required**`）必须在。
5. 长度：中文段 ≤ **700 字符** · 英文段 ≤ **1700 字符**（registry 的版本页上要一屏看完）。
   —— 判据是**各语种自己的预算**，不是「整节总长」：中文一个字承载的信息比英文一个词多，
   同一段内容英文天然长 2~3 倍 ⇒ 拿总长当判据等于**只卡英文**，还会诱使人靠"中英不均衡地删"凑数。

⚠️ **英文不是"顺便翻一下"**：registry 的读者大半只看英文，英文段是**主要**文案。
⚠️ 两段的**首句会被拿去当 GitHub Release 的标题**（§6.5）⇒ 首句要**自成一句话**、控制在一句以内
（不要在首句里塞第二个论点，它会被标题截在半路）。

### 6.4 事实类的数字

断言数、文件数、体积这类**会被机检复述的数字，不要写进发布说明** —— 它是冻结的，写进去就等于
把「当时对、以后错」的陈述永久挂在公开页面上。

### 6.5 GitHub Release 正文（同一份文案的第二个落点）

- **标题（双语，别手写 —— 脚本从 `RELEASE-NOTES.md` 取）**：
  `vX.Y.Z — <中文段首句> · <English first line>`。
  🔴 标题**必须**带英文：Releases 页与 registry 版本页是同一批读者（大半只看英文），而标题是页面上
  **唯一**一定被看见的那行 —— 只写中文等于把英文读者挡在门外。取首句时会去掉 `*` / 反引号与句末标点
  （GitHub 的 h1 **不做**行内 Markdown 渲染 ⇒ 留在那里就是字面的星号）。
- **正文**：本版一节 ＋ **「上一次 GitHub Release 之后压着的版本」全部补上**（折叠块）。
  🔴 为什么必须补：v0.6.15 之后有 **7 个版本从未建过 Release**（0.6.16~0.6.22）——
  只给最新一版建 Release，从 0.6.15 直升的用户**永远看不到中间发生了什么**。
  判据**不写死版本号**（写死了下次必漂），而是问 gh「上一个 Release 是哪版」，中间的全补。
- Release 的正文**事后可改**（与 registry 的 changelog 相反）⇒ §6.2 说的「梳理历史」在这里做得到：
  先把 `RELEASE-NOTES.md` 的对应节改好，再去网页上把正文贴过去。

## 7. 明确不做的事

- ❌ **不挂**官方那条「改 `pyproject.toml` 即自动发布」的 Action：它每次改 pyproject 都触发，
  版本没 bump 就失败，把 CI 变成噪声源。发布是**显式动作**。
- ❌ 不用 `git add -A` / `git add -u`（本仓多会话共用工作树，会把别人在飞的文件扫进你的提交）。
- ❌ 提交信息里带反引号时不用 `git commit -m "…"`（bash 会做命令替换**吃掉内容**）⇒ 一律 `-F <文件>`。
- ❌ 不让 `.comfyignore` 排掉运行期目录（`exp/` `v3/` `web/` `examples/`）—— H3k 有反向断言。
- ❌ **不在任何记录里出现凭据值**：提交信息、日志、README、command 回显都不行
  （值只存在于 `.comfy_registry_token`）。机检 **H3m** 扫受控文件内容 + 最近 50 条提交信息，
  命中**只报位置、绝不打印值**。⚠️ CI 的 `checkout` 默认 `depth=1` ⇒ CI 里只扫得到当前这一条
  提交信息，**本地跑才扫到 50 条** —— 别把 CI 绿当成"历史干净"。
