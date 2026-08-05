# DramaAgent 问题排查记录

按时间倒序记录开发过程中遇到的问题及其排查过程。每条记录包含**症状**、**分析**、**处理**三部分。

> 模板：
> ```markdown
> ## YYYY-MM-DD — 问题简述
>
> **症状**：
>
> **分析**：
>
> **处理**：
> ```

---

## 2026-07-26 — 角色校验白名单阻断工作流

**症状**：Episode Writer 在 `_validate_draft()` 中对 LLM 生成的未知角色名抛 `EpisodeWriterValidationError`，导致整个工作流被阻断。

**分析**：白名单机制（只允许 StoryBible 中已注册的角色名）永远追不上 LLM 的开放域输出。LLM 在实际写作中会创造临时配角、群演等不在设定中的角色，这是正常创作行为，不应阻断。

**处理**：将角色校验从阻断改为信息日志（warning），允许 LLM 创建非白名单角色，同时保留日志用于人工审核。

修改文件：[episode_writer.py](backend/app/skills/episode_writer.py)

---

## 2026-07-26 — 集数控制硬编码导致无法自定义集数

**症状**：无论前端传入多少 outline_count / script_count，后端始终生成 10 集大纲和 3 集剧本。前端集数选择器不生效。

**分析**：
- [outline.py:57](backend/app/workflows/nodes/outline.py) 硬编码 `outline_count=10`
- [write_episode.py:24](backend/app/workflows/nodes/write_episode.py) 硬编码 `_MVP_SCRIPT_COUNT=3`
- 前端 ChatInput 未发送 `outline_count` / `script_count`

**处理**：
- outline.py: `outline_count=10` → `ctx.get("outline_count", 10)`
- write_episode.py: `_MVP_SCRIPT_COUNT` → `ctx.get("script_count")`
- runs.py: workflow_config 传入 `script_count` 和 `outline_count`
- ChatInput.tsx: 新增集数下拉选择器（1/2/3/5/10）

修改文件：[outline.py](backend/app/workflows/nodes/outline.py)、[write_episode.py](backend/app/workflows/nodes/write_episode.py)、[runs.py](backend/app/api/v1/runs.py)、[ChatInput.tsx](frontend/src/features/conversation/ChatInput.tsx)

---

## 2026-07-26 — 前端进度条永远显示"等待工作流启动"

**症状**：前端 SSE 连接正常，事件也收到了，但进度条一直卡在 0%，节点状态全部 pending。看起来像是没收到任何 node.started 事件。

**分析**：
- 后端 SSE 发送的事件字段叫 `event_type`
- 前端接口定义的类型字段叫 `type`
- 所有事件判断如 `ev.type === "node.started"` 永远为 `false`
- 这是一个前后端字段名不匹配的经典问题

**处理**：
- [use-run-events.ts](frontend/src/hooks/use-run-events.ts): `RunEvent.type` → `event_type`（6处），同时 fetch+ReadableStream → 浏览器原生 EventSource
- [api.ts](frontend/src/types/api.ts): `WorkflowEvent.type` → `event_type`，补充 `stage`/`progress`/`message`/`artifact_id` 字段
- [RunProgress.tsx](frontend/src/features/runs/RunProgress.tsx): 增加调试信息（连接状态 + 事件计数）
- `tests/setup.ts`: 新增 `EventSource` mock

---

## 2026-07-26 — SSE 新连接看不到已有事件

**症状**：前端刷新页面后，SSE 全新连接永远收不到任何历史事件，直到工作流产生新事件。看起来像是历史回放根本没执行。

**分析**：
- `_event_generator` Phase 1（历史回放）只在 `last_event_id` 非空时才执行
- 全新连接时 `last_event_id` 为空，Phase 1 完全跳过
- EventSource 连接后的第一个事件是 `: connected` 注释行，但没有历史事件

**处理**：
- [stream.py](backend/app/events/stream.py): Phase 1 始终执行，不再检查 `last_event_id` 是否为空
- 新增 `_db_poller()` 作为 Redis Pub/Sub 的数据库回退
- 开头 `yield ": connected\n\n"` 确保 EventSource 立即建立连接

---

## 2026-07-26 — 事件对 SSE 不可见（事务隔离问题）

**症状**：SSE 连接正常，但所有 WorkflowEvent 都等到整个工作流完成后才一次性出现在 SSE 流中。进度无法实时展示。

**分析**：
- `EventPublisher.publish()` 只在 Worker 的事务内 flush
- 事件只在 Worker 事务内可见，SSE 连接的独立事务无法读取
- 只有整个 Workflow 完成后事务提交，全部事件才对外可见

**处理**：
- [publisher.py](backend/app/events/publisher.py): 新增 `autocommit=True` 参数，commit + re-begin 使事件立即对 SSE 可见
- 测试环境仅 flush 不 commit，避免破坏测试事务隔离
- 全部 workflow nodes（6 文件 23 处）+ runs.py（4 处）加 `autocommit=True`

---

## 2026-07-25 — OpenAI API Base URL 重复拼接

**症状**：调用真实 LLM 时报 HTTP 404，实际请求 URL 变成了 `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`（末尾多了 `/v1/chat/completions`）。

**分析**：
- 阿里云 MAAS 的 base URL 已经包含 `/compatible-mode/v1`
- OpenAI Python SDK 默认会在 base URL 后追加 `/v1/chat/completions`
- 导致 URL 变为 `.../compatible-mode/v1/v1/chat/completions`

**处理**：HTTP 404 不应被映射为 `INVALID_OUTPUT` 导致无意义重试。修复错误映射：404 → 模型/端点不存在，区别于 Schema 校验失败。

修改文件：[openai_compatible.py](backend/app/llm/openai_compatible.py)

---

## 2026-07-25 — .env 文件加载失败

**症状**：真实 LLM 调用时所有环境变量为空字符串，LLM_API_BASE、LLM_API_KEY 等全部未加载。

**分析**（多层排查）：
1. `.env` 中变量名缺少 `LLM_` 前缀 → API_BASE 未加载
2. `env_file` 使用相对路径 → 从 `backend/` 子目录运行时找不到
3. `cors_origins: list[str]` → `*` 被解析为单个字符列表而非字符串
4. `extra="forbid"` → Docker Compose 中的共用环境变量（非 Pydantic 字段）被拒绝
5. `APP_ENV=test` 时加载了真实 `.env` → 污染测试环境

**处理**：
- [config.py](backend/app/core/config.py): env_file 使用绝对路径、`extra="ignore"`、`cors_origins: str`、test 环境跳过 `.env`
- [main.py](backend/app/main.py): `settings.get_cors_origins()` 解析逗号分隔字符串
- `.env`: 所有变量加 `LLM_` 前缀，API_BASE 去掉末尾 `/v1`
