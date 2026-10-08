# `review_050.py` 判据台账

> 🔴 **本文件由 `tools/checks_ledger.py --write` 生成，别手改**（改台账 → 重新生成）。
> 机检：`review_050.py` 的 **H4c** 核「本台账 ↔ `review_050.py` 的编号**双向一致**」。

共 **53** 条带编号的判据 ｜ 其中盯**口径数字**的 **14** 条 ｜ 有**变异测试**的 **20** 条。

## 一、盯「口径数字」的判据（**这些数字不许裸写**）

| 编号 | 类别 | 盯什么数字 | 变异测试 | 读环境 |
|---|---|---|---|---|
| **H1** | consistency | 版本号（`__init__.py` / `pyproject.toml` / `CHANGES.md` 顶部 / `README.md` 首部 四处） | — | — |
| **H3b** | consistency | `CONTRIBUTING.md` 的方面数 + 断言数 | — | — |
| **H3c** | consistency | `docs/08-testing.md` 的断言数 + 方面数 + 分组表条数 | — | — |
| **H3d** | consistency | `nodes.py` 模块头注释声明的节点数 | — | — |
| **H3f** | consistency | `ci.yml` 里 `test_relay_core` 的期望数（头注释 + 执行行**每一处**） | 把 `ci.yml` 里 `test_relay_core` 的期望数改成 562 | — |
| **H3g** | consistency | `review_050` 自己的期望数（ci.yml / docs/08 / tools/README / README） | 把 `docs/08-testing.md` 里 `review_050` 的期望数改成 105 | — |
| **H3h** | consistency | V3 真值三元组：节点数 / input 数 / 逐字段项数 | 把 `ci.yml` 里 V3 的 input 数改成 121 | host |
| **H3i** | consistency | `assert_default_exit` 的期望数 | 把 `tools/README.md` 里 `assert_default_exit` 的期望数改成 4/4 | host |
| **H3j** | consistency | `smoke_nodes` 的期望数 | 把 `docs/08-testing.md` 里 `smoke_nodes` 的期望数改成 15/0 | — |
| **H3u** | truth | registry 包文件数（`.comfyignore` 注释里声明） | 把 `.comfyignore` 注释里的包文件数改成 47 | git |
| **H3v** | truth | `tools/` 脚本数（`N 个脚本`） | 把 `tools/README.md` 的「15 个脚本」改成「14 个脚本」 | — |
| **H4d** | truth | **JS 测试期望数 ×4**（`43/0` / `29/0` / `45/0` / `33/0`；ci.yml + docs/08） | 把 `ci.yml` 头注释里 `test_run_id_parity` 的期望数改成 22 | node |
| **H4e** | truth | **dist 白名单文件数**（`make_minimal_bundle.py` 的 `MANIFEST` 总长 vs 文档声明） | 把 `make_minimal_bundle.py` 的 `MANIFEST_RUNTIME` 删一项（42 → 41） | — |
| **I4** | consistency | 示例图节点数（`examples/README.md` 的「N 节点」） | — | — |

## 二、其余判据

| 编号 | 类别 | 管什么 | 变异测试 | 读环境 |
|---|---|---|---|---|
| **E1** | consistency | relay_core.MASK_MODES | — | — |
| **E2** | consistency | 节点选项与 MASK_MODES 一致 | — | — |
| **E3** | hygiene | 四个权重函数齐备 | — | — |
| **H3e** | hygiene | 开源卫生：受版本控制的文本里无作者本机路径 | 在任一受控文本里插一行本机盘符路径（如 `X:/tmp/a`） | git |
| **H3k** | gate | Comfy Registry 元数据齐备 | 把 `pyproject.toml` 的 `license` 改成裸 SPDX 字符串 | — |
| **H3l** | hygiene | 发布规范在位且成对 | — | — |
| **H3m** | hygiene | 仓库内无凭据字面量 | — | git |
| **H3n** | hygiene | 词表完整性：每个节点的**参数名**都有中文词条 | — | — |
| **H3o** | hygiene | 文档里的仓库内链接 / 反引号路径都真实存在 | 把某个受控 `*.md` 里的仓库内链接改成不存在的路径 | git |
| **H3p** | hygiene | 悬浮提示：每个 | — | — |
| **H3q** | hygiene | 前端 JS | — | git |
| **H3r** | hygiene | 防覆盖兜底成对存在 | — | — |
| **H3s** | hygiene | link 端点字段两代都读 | — | — |
| **H3t** | hygiene | 官方语言入口走 `app.extensionManager.setting` | — | — |
| **H3w** | hygiene | 无未跟踪的受检类型文件 | 在 `tools/` 下造一个未跟踪的探针脚本（`tools/<probe>.py`） | git |
| **H3x** | hygiene | 无「`setdefault` 到 `PYTHONPATH`」 | 在任一受控 `*.py` 里写 `setdefault` 到 `PYTHONPATH` | — |
| **H3y** | truth | `tools/` 顶层每个脚本都在 `tools/README.md` 里登记过 | 把 `tools/README.md` 里某个脚本名的所有出现删掉 | git |
| **H3z** | consistency | `tools/release.py` 的每个 `--flag` 都在 `RELEASING.md` 里被写到 | 把 `RELEASING.md` 里某个 `release.py` 的 flag 改名 | — |
| **H4a** | hygiene | 跑 Python 的子进程都**显式**给了 `env=` | 加一处 `subprocess.run([sys.executable, "-c", "pass"])`（不传 `env=`） | — |
| **H4b** | truth | `review_050.py` 自身判据表自检 | 注入 `ck("X", True)`（恒真）或两条同名 `ck()` | — |
| **H4c** | truth | 判据台账 | 从本台账删掉一个编号（或 `review_050` 里加一条未登记的编号） | git |
| **I1** | hygiene | 示例图 widgets_values **未超出** schema 槽位数 | — | — |
| **I2** | hygiene | 取值都在候选/范围内 | — | host |
| **I3** | hygiene | **全部**示例图的 widget 输入带 `widget` 标记 | — | — |
| **J1** | hygiene | 存量工作流无『输出数组过期』的节点 | — | — |
| **J3** | hygiene | custom_nodes 无「同包第二份副本」 | — | — |
| **L1** | contract | H3RelayAudioSeam 已注册 | — | — |
| **L2** | contract | 默认全关 | — | — |
| **L3** | contract | patch=0 ⇒ 音频逐位直通 | — | — |
| **L4** | contract | 长度守恒 + N 之后逐位不动 | — | — |
| **L5** | contract | 替换区 = 床源尾部窗 × 单一增益 | — | — |
| **L6** | contract | 边界窗是 blend | — | — |
| **L7** | contract | 音频落盘/读回逐位一致 + 节点 stage 0 直通 | — | — |
| **L8** | contract | 两道守卫都 raise | — | — |
| **L9** | hygiene | docs/10 的音频缝章节口径：节点实现 | — | — |
| **L10** | hygiene | 双轨铁律在位：UI 与 API 同一套节点实现 | — | — |
| **L11** | hygiene | 铁律三在位 | — | — |
| **L12** | hygiene | docs/10 §7.2 判据能力边界与兜底措施在位 | — | — |
| **L13** | gate | 英文文档同步闸：`*_EN.md` 与中文源节级一致 | 改一个中文源节而不动对应 `*_EN.md` | none |

## 三、未覆盖变异测试的编号（**目标，不是门槛**）

共 33 条：`E1` `E2` `E3` `H1` `H3b` `H3c` `H3d` `H3l` `H3m` `H3n` `H3p` `H3q` `H3r` `H3s` `H3t` `I1` `I2` `I3` `I4` `J1` `J3` `L1` `L2` `L3` `L4` `L5` `L6` `L7` `L8` `L9` `L10` `L11` `L12`

> 变异测试 = 「注入必违规 ⇒ 这条必须变红」。没有它的判据，**强弱未被验证过** ——
> 实测 2026-10-08：`H4a` 第一版只认 `subprocess.*` / `_sp.*` 别名，**换名就绕过**，
> 是变异测试打回来的；`H3v` 第一版因为文档写中文数字而**整条空转**，也是变异测试抓的。
