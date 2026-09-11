# 安全策略

## 本仓库不含任何密钥 / 凭证
- 本包**不读取、不写入**任何 API Key、Token、密码或私有配置文件。
- 所有路径都在**运行时**通过 ComfyUI 的 `folder_paths` 或环境变量 `COMFYUI_PATH` 解析，**无硬编码的本地绝对路径**。
- 请勿在本仓库的 Issue / PR 中粘贴你的本地路径、模型路径或任何凭证。

## 依赖与网络行为
- 仅依赖 `torch` 与 `safetensors`（ComfyUI 自带或 pip 安装），无第三方网络依赖，运行时不发起任何远端请求。
- 随包工具 `tools/check_ui_workflow.py` 仅连接**本机**正在运行的 ComfyUI（`127.0.0.1:8188`）以读取节点 schema，不对外发送任何数据。

## 报告漏洞
如发现安全问题，请在 GitHub 上以 **Security Advisory（私有）** 形式提交；或在 Issue 中描述问题时**不要附带可复现的敏感信息**。我们会在收到后尽快回复。
