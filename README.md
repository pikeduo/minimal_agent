# Minimal Agent

Minimal Agent 是一个轻量的 Web Agent：它可以维护多个会话、调用受控工具、保存 Todo，并在本地保留脱敏的运行记录。核心 Runtime、工具调度、Session、Context 和 Trace 都在本项目内实现。

## 快速开始

### 1. 创建 Conda 环境

首次使用时创建 Python 3.11 环境；已存在同名环境时直接激活即可。

```powershell
conda create -n minimal_agent python=3.11 -y
conda activate minimal_agent
python -m pip install -e ".[dev]"
```

### 2. 配置本地环境

```powershell
Copy-Item .env.example .env
```

如需使用 DeepSeek，在 `.env` 中填写自己的 Key：

```dotenv
OPENAI_API_KEY=你的_DeepSeek_API_Key
OPENAI_MODEL=deepseek-v4-flash
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

`OPENAI_API_KEY` 是 OpenAI-compatible SDK 使用的变量名，在这里填写 DeepSeek Key。`.env`、数据库和 Trace 日志仅保留在本机，不应提交到 Git。

未配置 Key 也可以启动页面、创建会话和管理 Todo；发送聊天消息时会显示安全的配置提示，不会发起网络请求。也可在“设置”页把 Key 保存到当前浏览器的本地缓存：它仅随聊天请求临时发送，不会由页面写入 `.env`、SQLite 或 Trace；需要移除时可在同一页面清除。

### 3. 启动应用

```powershell
python -m uvicorn minimal_agent.app:create_app --factory --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>，创建会话后即可开始使用。

## 使用说明

- **账户**：首次打开会进入注册页；登录会话只保存在 HttpOnly Cookie，密码仅以 scrypt 哈希保存到本地 SQLite。
- **会话**：创建时不填写标题，默认显示为“新会话”；第一条有效用户消息会自动成为会话标题。点击“返回会话列表”离开尚未输入消息的新会话时，系统会自动删除该空会话；首页仍可在确认后删除任意会话及其关联数据。
- **聊天**：消息通过 HTMX 局部更新；页面只显示用户消息、最终回答和简化工具状态。助手回复支持安全渲染的常用 Markdown（加粗、列表、行内代码、代码块和三级以内标题），用户输入始终按纯文本展示。
- **Todo**：可在当前会话中新增和完成待办，不会出现在其他会话。
- **设置**：显示模型与运行限制；可选择将 DeepSeek Key 保存在当前账号对应的浏览器缓存，切换账号不会读取该 Key，页面不会回显密钥。旧版未绑定账号的浏览器缓存不会自动迁移。

服务端从登录会话取得用户身份，并以 `user_id + session_id` 校验 Session 和 Todo 所有权。`AUTH_COOKIE_SECURE=false` 仅适用于本地 HTTP 开发；生产 HTTPS 部署必须改为 `true`。

## 可用能力

| 能力 | 说明 |
| --- | --- |
| DeepSeek Provider | 通过 OpenAI-compatible SDK 调用，默认模型为 `deepseek-v4-flash`。 |
| `calculator` | 使用受限 AST 计算四则运算和括号表达式，不执行任意代码。 |
| `search`、`weather` | 使用确定性 Mock 数据，适合离线演示和测试。 |
| `todo` | 使用 SQLite 保存当前会话的待办。 |
| Context | 使用当前会话摘要、近期消息与相关工具结果支持追问。 |
| Trace | 以 JSONL 记录运行事件，并自动清除敏感字段。 |

## 测试

普通测试完全离线：

```powershell
python -m pytest --basetemp .pytest-tmp
```

真实 DeepSeek Smoke Test 默认跳过。只有在已设置本机 Key 且明确同意消耗 API 配额时才运行：

```powershell
$env:RUN_LLM_SMOKE = "1"
python -m pytest tests/test_deepseek_smoke.py -m smoke --basetemp .pytest-tmp
Remove-Item Env:RUN_LLM_SMOKE
```

## 数据与安全

- 会话、消息、Todo 和工具结果存储在 SQLite；历史过长时会生成确定性摘要，原始消息不会被删除。
- Trace 按 `run_id` 关联运行、模型和工具事件，不记录 API Key、Authorization、原始模型响应、Traceback 或完整推理内容。
- `logs/server.log` 追加保存 Uvicorn 的访问与错误输出；浏览器缓存命中时的 HTTP `304 Not Modified` 属于正常响应，不代表页面或模型调用失败。
- `search` 与 `weather` 不访问互联网；它们是稳定的本地 Mock 工具。
- 当前不包含生产认证、流式输出或同一 Session 的并发串行化。
