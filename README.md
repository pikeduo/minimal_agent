# Minimal Agent Runtime

一个从零实现的最小 Web Agent Runtime。项目不依赖 LangChain、LangGraph 或其他 Agent 框架；Runtime、工具调度、Session、Context、压缩和 Trace 均由本仓库实现。

## 功能概览

- 基于 OpenAI-compatible SDK 的 DeepSeek Provider，默认模型为 `deepseek-v4-flash`。
- 内部 DTO 隔离供应商 SDK；支持最终回答、单/多工具调用、工具失败修正和最大步数终止。
- 内置 `calculator`、确定性 Mock `search`/`weather` 与 SQLite `todo` 工具。
- SQLite 持久化 Session、用户/助手消息、Todo、工具结果与会话摘要。
- Context 只组合当前 Session 的摘要、近期消息和相关工具结果；超限历史使用确定性压缩。
- 脱敏 JSONL Trace，记录运行、LLM 和工具生命周期，不记录密钥、原始响应、Traceback 或完整推理内容。
- FastAPI + Jinja2 + HTMX 的 Session 列表、聊天、Todo 和设置页面。

## 运行环境

- Python 3.11 或更高版本。
- 可选：DeepSeek API Key。未配置 Key 时，Web 页面仍可运行，但发送消息会返回安全配置提示，不会访问网络。

## 安装

建议在虚拟环境中安装：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

如果 PowerShell 阻止激活脚本，可在当前终端执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

## 配置

复制示例配置后，只在本机 `.env` 中填入真实密钥：

```powershell
Copy-Item .env.example .env
```

`.env` 示例：

```dotenv
OPENAI_API_KEY=你的_DeepSeek_API_Key
OPENAI_MODEL=deepseek-v4-flash
DEEPSEEK_BASE_URL=https://api.deepseek.com
DATABASE_PATH=data/minimal_agent.sqlite3
TRACE_PATH=logs/agent-trace.jsonl
MAX_AGENT_STEPS=8
MAX_CONTEXT_MESSAGES=24
CONTEXT_KEEP_RECENT=12
```

虽然环境变量名称为 `OPENAI_API_KEY`，它在本项目中用于 OpenAI-compatible SDK 的 DeepSeek Key。请勿提交 `.env`、`data/` 或 `logs/`。

## 启动 Web 应用

```powershell
python -m uvicorn minimal_agent.app:create_app --factory --host 127.0.0.1 --port 8000
```

浏览器打开 <http://127.0.0.1:8000>，创建 Session 后即可聊天。不同浏览器标签页使用不同的 `/sessions/{session_id}` URL，因此同一开发身份可并行维护多个独立会话。

当前版本的身份仅用于本地演示：默认身份为 `demo-user`，也可由开发请求头 `X-User-ID` 指定。路由、表单和 URL 都不能指定目标用户；每次 Session/Todo 访问仍由 `user_id + session_id` 双重授权。生产环境必须替换为真实认证，不应信任客户端自报身份。

## 测试

普通测试全部离线，不调用真实模型服务：

```powershell
python -m pytest --basetemp .pytest-tmp
```

其中 DeepSeek Smoke Test 默认跳过。仅在你明确希望发起一次真实请求、并已设置本机 Key 时才执行：

```powershell
$env:RUN_LLM_SMOKE = "1"
python -m pytest tests/test_deepseek_smoke.py -m smoke --basetemp .pytest-tmp
Remove-Item Env:RUN_LLM_SMOKE
```

Smoke Test 会消耗真实 API 配额；未设置 `RUN_LLM_SMOKE=1` 或没有 `OPENAI_API_KEY` 时不会宣称通过。

## 架构与数据边界

```text
FastAPI / Jinja2 / HTMX
           │
ConversationService ── ContextBuilder ── SQLite
           │                    │
      AgentRuntime ───── ContextCompressor
       │       │
Provider  ToolRegistry ── calculator / search / weather / todo
       │
DeepSeek OpenAI-compatible API
```

`AgentRuntime` 只接收内部 `LLMRequest`，Provider 返回 `FinalAnswer`、`ToolCallBatch` 或安全 `ProviderError`。Provider 原始响应不会传递到 Runtime、存储或页面。

### Context 与 Memory

每次请求只使用当前 Session 的：

1. 确定性摘要；
2. 最近原始消息；
3. 对追问有价值的近期 `ToolResult`；
4. 当前 Run 的工具交互。

当消息数超过阈值时，`ContextCompressor` 保留最近消息，将较早、尚未覆盖的消息追加到摘要，并记录覆盖游标；原始消息不会删除，也不会重复压缩。项目当前实现的是 Session 内短期记忆，不包含跨 Session 的长期向量检索。

### Trace 与安全

Trace 使用 `TRACE_PATH` 所指向的 JSONL 文件，按 `run_id` 关联 `run.started`、`context.built`、`llm.requested`、工具和结束事件。Trace 仅保留最小状态元数据；会清除 API Key、Authorization、token、Traceback、原始响应及完整思维链。

页面仅显示用户消息、最终回答、Todo 和简化工具状态，不显示工具参数、SDK 响应或推理文本。

## 已知边界

- `search` 与 `weather` 是确定性 Mock 工具，不访问互联网。
- Web 身份是开发期占位方案；尚未实现生产认证、同一 Session 的并发串行化或流式输出。
- DeepSeek 工具调用显式关闭 thinking mode，以避免保存或回传完整 reasoning 内容。

## 演示与 AI 记录

- 本地录屏步骤见 `docs/recording-guide.md`（该目录按项目规则不上传 Git）。
- AI 辅助开发记录见 `docs/ai-prompts-and-decisions.md`（不包含密钥、完整推理或原始模型响应）。
