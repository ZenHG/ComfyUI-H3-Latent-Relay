## 这个 PR 改了什么

<!-- 一句话。行为变化请点名：默认值 / 连线 / 界面文字 / 只内部。 -->

## 自查（勾完再请求评审）

- [ ] `ruff check . --exclude dist`
- [ ] `python tests/test_relay_core.py` · `python tools/review_050.py`
- [ ] 改了提示 / 标签 / 开关文案 ⇒ `python tools/check_tooltips.py` + `python tools/check_frontend.py`
- [ ] 改了前端 JS ⇒ `python tools/check_frontend.py`（F1–F5）+ `node tests/test_i18n.mjs`
- [ ] 新增 / 改名用户可见文字 ⇒ 已补表 + 补判据（见 `CONTRIBUTING.md` §可见字符串渠道）
- [ ] 计数 / 清单变化 ⇒ 让机检报真值，没有手改单处
- [ ] 中文文档改了 ⇒ `python tools/en_sync.py --check`
- [ ] 版本号 / 发布说明 ⇒ 内外分工按 `RELEASING.md` §6.2（内部项只写 `CHANGES.md`）

## 没跑的那一档

<!-- 宿主 / 浏览器 / py3.10 等跑不了的，点名写出来，别当已覆盖。 -->
