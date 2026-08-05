# AI 辅助开发记录

## 使用范围

本项目使用 Codex 协助进行需求拆分、代码实现、测试补充、文档整理和本地命令执行。所有代码修改均经过离线测试验证；真实模型请求只保留为显式启用的 Smoke Test。

## 代表性任务描述

- 按阶段构建最小 Agent Runtime，而不引入接管 Runtime 的框架。
- 为 Provider、ToolRegistry、SQLite Session/Todo、Context 压缩、Trace 和 Web UI 分别实现可测试模块。
- 要求所有普通 pytest 测试离线、工具调用由结构化 ToolCall 决定、跨 Session 数据不能泄露。
- 要求所有代码注释、文档和 Git 提交信息使用中文，并在每个阶段后报告实际测试结果与下一步。

## 关键工程决策

| 决策 | 原因 |
| --- | --- |
| 采用内部 DTO，而不把 SDK 响应传入 Runtime | 便于替换 Provider，避免原始响应、鉴权与推理内容泄露。 |
| 使用 `ScriptedLLMProvider` 作为普通测试替身 | 让测试离线、确定，不根据用户输入关键词伪造工具选择。 |
| 使用 DeepSeek OpenAI-compatible Adapter | 复用官方 OpenAI Python SDK 的 Chat Completions 工具调用接口。 |
| 对 DeepSeek 请求关闭 thinking mode | 工具回合需要回传完整 reasoning 内容会违反项目不保存完整思维链的约束。 |
| 使用 SQLite 和复合 `user_id + session_id` 查询 | 为多窗口 Session 与 Todo 建立清晰的所有权边界。 |
| 使用确定性 Context 压缩 | 无需额外模型调用，且可借助覆盖游标避免重复摘要。 |
| 采用脱敏 JSONL Trace | 可按 run_id 调试生命周期，同时避免保存密钥、Traceback 和原始模型内容。 |
| Web 层使用 HTMX 局部片段 | 保持 FastAPI/Jinja2 架构简单，无需 SPA 构建链。 |

## 问题解决记录

以下记录只保留问题现象、修复策略和验证结果，不记录真实 API Key、用户内容、原始模型响应或完整推理过程。

| 编号 | 问题现象 | 定位与处理 | 验证方式与结果 |
| --- | --- | --- | --- |
| 1 | 在浏览器设置了 DeepSeek Key 后，服务端仍提示未配置模型。 | 将浏览器 Key 按当前用户 ID 命名空间保存；发送消息时随本次请求临时传给 Provider，不写入 `.env`、SQLite 或 Trace。缺少 Key 时给出跳转至设置页的安全提示。 | Web 路由测试覆盖有 Key 与无 Key 两条路径；页面可识别当前账号的浏览器 Key。 |
| 2 | 模型回答中的 `**`、表格等 Markdown 标记直接显示，阅读体验不佳。 | 对助手最终回答使用白名单 Markdown 渲染，并保留原始 Markdown 供复制；用户输入仍按纯文本渲染，防止脚本注入。 | Web 测试覆盖加粗、列表、表格、代码与危险 HTML 的渲染边界。 |
| 3 | 同一用户在不同窗口创建会话后，Todo 或历史可能相互影响。 | 所有 Session、消息、工具结果和 Todo 查询均使用 `user_id + session_id` 限定；ContextBuilder 只读取当前会话。 | Storage、Todo、Context 和 Web 集成测试覆盖跨会话隔离与越权拒绝。 |
| 4 | 历史对话过长会让请求持续增大，也可能重复摘要旧消息。 | 在超过阈值时只保留近期原文，较早内容生成确定性摘要；用覆盖游标记录已摘要到的最后一条消息。 | 压缩测试验证近期消息保留、原文不删除、重复运行不重复摘要。 |
| 5 | 工具执行、Provider 失败或工具参数错误时，页面缺少可诊断信息且不能暴露敏感数据。 | Runtime 把异常转换为安全的 Provider/工具错误；Trace 以 `run_id` 写入脱敏 JSONL 生命周期事件，并采用最佳努力写入。 | Trace 与 Runtime 测试覆盖成功、失败、工具错误、敏感字段脱敏和 Trace 写入失败不影响主流程。 |
| 6 | 真实 API 的网络调用不适合放进日常测试，会造成不稳定和配额消耗。 | 普通测试统一使用 `ScriptedLLMProvider`；真实 DeepSeek 调用仅保留为显式环境变量启用的 smoke test，无 Key 自动跳过。 | 默认 pytest 离线运行；设置 `RUN_LLM_SMOKE=1` 且本机配置 Key 后才执行真实请求。 |
| 7 | 静态文件访问日志出现 `304 Not Modified`，容易误认为模型或页面调用失败。 | 明确将其识别为浏览器缓存协商结果，并将 Uvicorn 控制台输出追加保存到 `logs/server.log` 以便区分页面访问、应用错误和模型调用事件。 | README 说明 304 的含义；服务启动后访问静态资源可在日志中看到对应记录。 |

## 验证原则

- 每个实现阶段均运行对应的单元或 HTTP 集成测试，并在阶段结束前运行完整离线 pytest。
- 使用临时 SQLite 和 Trace 路径验证持久化、Session 隔离、Todo 副作用和 JSONL 事件，而不以静态检查替代功能测试。
- `RUN_LLM_SMOKE=1` 与本机 `OPENAI_API_KEY` 同时存在时，真实 Smoke Test 才会运行；未执行时明确报告为跳过。

## 数据与安全限制

本记录不包含 API Key、Authorization Header、真实用户数据、完整隐式推理链、原始 Provider 响应或异常堆栈。AI 只输出和持久化项目允许的内部模型、测试夹具及安全摘要。
