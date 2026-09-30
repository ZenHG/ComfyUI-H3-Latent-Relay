# 发布规范（RELEASING）

> 本文件是**发布流程的唯一真相源**。执行体 = [`tools/release.py`](tools/release.py)（两边必须一致；
> 改了流程先改这里）。CI 的期望数、门槛清单见 [`docs/08-testing.md`](docs/08-testing.md)。

## 0. 一句话

**一次发布 = 两个渠道同时更新，且回头验证过。**

```bash
python tools/release.py            # 预演（本地前置 + 改动面，不推不发）
python tools/release.py --go       # 真发布：push → 等 CI 绿 → registry publish → 交叉验证
python tools/release.py --verify-only   # 只回答「两边现在同步吗」（纯只读）
```

## 1. 两个渠道各是什么

| 渠道 | 发布物 | "完成"的判据 |
|---|---|---|
| **GitHub**（`ZenHG/ComfyUI-H3-Latent-Relay`） | 提交历史 + 源码 | 远端 `main` 的 sha **等于**本地 HEAD，且**那一轮的 CI 是绿的** |
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
3. **`CHANGES.md` 顶部那一节就是本版** —— registry 的 changelog 直接取它（版本不符会被脚本报出来）。
4. **本地门槛全绿**：跑 `h3relay_check_all.py`（本机工具，在**仓库之外的** tools 目录里；
   12 项，与 CI 逐步对应）。`tools/release.py` 自己只跑最会翻车的两项（`review_050` / `en_sync`），
   全量请你手跑。
5. **Registry 元数据合规**（`review_050` 的 H3k 盯着）：`name` 不带 `ComfyUI`、`license` 为
   `{ file = … }`/`{ text = … }`、`PublisherId` 非占位、`Icon` 是能取到的 https 直链、
   `.comfyignore` 排开发件而**不排运行期目录**。

## 3. 执行体做的五步（`tools/release.py --go`）

| 步 | 动作 | 失败时的状态 |
|---|---|---|
| ① 前置 | 干净工作树 · 版本四处 · `review_050` · `en_sync` | 什么都没发生 |
| ② GitHub | `git push origin HEAD:refs/heads/main`（失败自动换 HTTP/1.1 / schannel / **去代理直连**重试） | 什么都没发生 |
| ③ 等 CI | 找 `headSha == 本地 HEAD` 的那一轮，`gh run watch --exit-status` | **已 push、未 publish** ⇒ 修完重跑，安全 |
| ④ Registry | `comfy node validate` → `node pack` 预览（开发件混入即拒）→ `publish --token --changelog` | 已 push、未 publish ⇒ 重跑 |
| ⑤ 交叉验证 | 远端 `main` sha == HEAD ？registry 版本列表含本版 ？（给了 `H3RELAY_DEPLOY` 则再比部署副本） | 会**点名**哪一项没过 |

> ⚠️ **③ 的失败是本流程唯一的"半成品"状态**（GitHub 有了、registry 没有）。这是**有意如此**：
> 宁可在 registry 上少一版，也不要在 CI 红的提交上发版。修完再跑一遍即可，registry 那边不会被弄脏。

## 4. 失败处置速查

| 症状 | 含义 | 怎么办 |
|---|---|---|
| `git: 'remote-https' is not a git command` | 用的是便携版 git（缺助手） | 设 `H3RELAY_GIT` 指向完整版 git 再跑 |
| `CONNECT tunnel failed, response 502` | 本机代理对 git 的 CONNECT/POST 打回 | 脚本会自动**去代理直连**重试一次；仍失败就手动 `env -u http_proxy … git push …` |
| `comfy node publish` 报 publisher 不匹配 | registry 上 `PublisherId` 与 `pyproject.toml` 不一致 | 改成网站上 `@` 后面那串（**两边都不可改，别猜**） |
| `comfy node validate` 报警 | 见 [`docs/08-testing.md`](docs/08-testing.md) 的 S 规则说明 | 行级 `# noqa` + 在代码里写清理由，**别为消警告换拼法** |
| registry 说版本已存在 | 同版本不能重发 | bump 一版（**已发布的版本与 changelog 都不可改**） |

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

## 6. 版本与 changelog 规则

- `version` 用语义化三位：`X.Y.Z`。**`name` 与已发布的 version/changelog 都不可改**。
- changelog 取 `CHANGES.md` 顶部那一节 ⇒ **发布前先把它写对**（发布后改不动公开页面上的那份）。
- 事实类的数字（断言数、文件数、体积）**不要**在 changelog 里复述 —— 它们会被机检盯着，
  而 changelog 是冻结的，容易变成永久错误的陈述。

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
