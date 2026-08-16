# DramaAgent API 契约文档

> 版本：v0.1.0-rc1  
> 最后更新：2026-08-16（Phase I 发布候选：retry / diagnostics / metrics + 错误码全集）

## 概述

DramaAgent API 遵循 RESTful 风格，所有端点以 `/api/v1/` 为前缀。
长任务（创作、评估）通过 `Run + SSE` 模式异步执行：
客户端 POST 创建 Run 后立即收到 202 + run_id，通过 SSE 订阅进度事件。

## 基础信息

- Base URL: `http://<host>:8000/api/v1`
- Content-Type: `application/json`
- 认证方式：MVP 阶段无（后续按 I-03 添加）

### 错误响应格式

```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "detail": "人类可读的错误描述",
  "code": "NOT_FOUND",
  "path": "/api/v1/some-endpoint",
  "timestamp": "2026-07-25T12:00:00Z"
}
```

全部错误响应均包含 `request_id` 字段，可用于日志追踪。

### 全局错误码全集（I-01 / I-02 / I-04 补充）

| 状态码 | code | 含义 | 触发 |
|--------|------|------|------|
| 400 | `VALIDATION_ERROR` | 请求体 / 查询参数校验失败 | Pydantic 校验 |
| 404 | `NOT_FOUND` | 资源不存在 | 项目 / Run / Artifact 等 |
| 409 | `RUN_NOT_RETRYABLE` | Run 处于 `completed` / `cancelled` 终态，不可重试 | `POST /runs/{id}/retry` |
| 409 | `RUN_ALREADY_ACTIVE` | Run 正在执行（`queued` / `running`），不可重复重试 | `POST /runs/{id}/retry` |
| 409 | `RUN_BUDGET_EXCEEDED` | 触发 per-run 硬预算（调用数 / Token） | LLM 调用 |
| 409 | `TOOL_ALREADY_REGISTERED` | 工具 / Skill 重名注册冲突 | 扩展注册 |
| 413 | `FILE_TOO_LARGE` | 上传超过 10 MB | 上传 |
| 415 | `INVALID_FILE_TYPE` | 仅 TXT / DOCX | 上传 |
| 422 | `FILE_PARSE_FAILED` | 文件解析失败（含宏 / 加密文档） | 上传 |
| 502 | `EXTERNAL_TOOL_ERROR` | 外部 MCP 工具调用失败（不泄漏内部连接信息） | MCP |
| 504 | `EXTERNAL_TOOL_TIMEOUT` | 外部 MCP 工具调用超时 | MCP |
| 500 | `INTERNAL_ERROR` | 未分类错误 | 兜底 |

**Run 失败时的 `error_code`**（落库到 WorkflowRun，`GET /runs/{id}` 返回）：`RUN_BUDGET_EXCEEDED` / `RUN_CANCELLED` / `LLM_TIMEOUT` / `LLM_RATE_LIMITED` / `LLM_PROVIDER_ERROR` / `LLM_INVALID_OUTPUT` / `EXTERNAL_TOOL_ERROR` 等，见 [backend/app/llm/retry.py](backend/app/llm/retry.py) 与 [backend/app/workflows/checkpoint.py](backend/app/workflows/checkpoint.py)。

## 端点

### 健康检查

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health/live` | 存活检查（不依赖外部服务） |
| GET | `/health/ready` | 就绪检查（检查 DB + Redis） |

### 项目

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/projects` | 创建项目 |
| GET | `/projects/{id}` | 查询项目 |
| PATCH | `/projects/{id}` | 更新项目 |
| GET | `/projects` | 列出项目 |

### 会话与消息

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/projects/{id}/conversations` | 创建会话 |
| GET | `/projects/{id}/conversations` | 列出一个项目的会话 |
| POST | `/conversations/{id}/messages` | 追加消息 |
| GET | `/conversations/{id}/messages` | 列出消息 |

### Artifact

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/projects/{id}/artifacts/latest?type=...&episode=...` | 获取最新 valid Artifact |
| GET | `/artifacts/diff?from_artifact_id=&to_artifact_id=` | 两版本 Diff（F-04，详见下文） |
| GET | `/artifacts/{id}` | 按 ID 获取 Artifact |
| GET | `/artifacts/{id}/versions` | 版本历史 |
| GET | `/artifacts/{id}/links` | 源依赖查询 |

## GET /artifacts/diff — 两版本 Diff

对比同一 Artifact 的两个不可变版本（from → to），输出场景感知的增删改变化。

### 请求

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `from_artifact_id` | UUID | 是 | 旧版本（from）Artifact ID |
| `to_artifact_id` | UUID | 是 | 新版本（to）Artifact ID |

### 响应（200）

`mode="scene"`（content 无法解析为 `script_draft` 时回退 `mode="line"`）：

```jsonc
{
  "mode": "scene",                                  // "scene" | "line"
  "from_artifact_id": "...", "to_artifact_id": "...",
  "from_version": 1, "to_version": 2,
  "project_id": "...", "episode_number": 1,
  "change_ratio": 0.35,                             // [0,1]，对称、方向无关
  "scene_summary": {"from_scene_count": 2, "to_scene_count": 2,
                    "added": 0, "removed": 0, "modified": 1, "unchanged": 1},
  "stats": {"added_lines": 0, "removed_lines": 0, "modified_lines": 3,
            "added_chars": 0, "removed_chars": 0, "changed_chars": 0,
            "from_chars": 1000, "to_chars": 1010},
  "scene_changes": [{
      "change_type": "modified",
      "old_scene_number": 1, "new_scene_number": 1,
      "location": "...", "time_of_day": "...", "similarity": 0.92,
      "added_lines": 0, "removed_lines": 0, "modified_lines": 3,
      "added_chars": 0, "removed_chars": 0,
      "line_changes": [{"change_type": "modified",
                        "old_line_number": 3, "new_line_number": 3,
                        "old_text": "...", "new_text": "..."}],
      "line_changes_truncated": false
  }],
  "truncated": false                               // 变更行 > 2000 时 true，line_changes 清空
}
```

`change_ratio` 语义与 `RevisionPlan.max_change_ratio`（默认 0.35）对齐：`check_change_ratio(actual, max)` 判定 `actual <= max`。

### 错误码

| 状态码 | code | 含义 |
|--------|------|------|
| 400 | `CROSS_PROJECT_DIFF_FORBIDDEN` | from/to 属于不同项目 |
| 400 | `DIFF_UNSUPPORTED_TYPE` | 任一版本不是 `script_draft` 类型 |
| 400 | `DIFF_EPISODE_MISMATCH` | from/to 不是同一集 |
| 404 | `ARTIFACT_NOT_FOUND` | 任一 Artifact 不存在 |

### Run

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/projects/{id}/runs` | 创建 Run |
| GET | `/projects/{id}/runs` | 列出项目的 Run |
| GET | `/runs/{id}` | 查询 Run 状态 |
| POST | `/runs/{id}/cancel` | 取消 Run（协作式，I-01） |
| POST | `/runs/{id}/retry` | 重试失败的 Run（从 checkpoint 恢复，I-01） |
| GET | `/runs/{id}/diagnostics` | Run 运行诊断（节点时间线 / LLM 统计，I-02） |
| GET | `/runs/{id}/events` | SSE 事件流 |

### 运维（I-02）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/metrics` | Prometheus 指标（`metrics_enabled=false` 时 404；不含真实密钥） |

### 修订（F-06）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/projects/{id}/revisions` | 发起修订（自动 / 单集指定，202 + Run） |
| GET | `/projects/{id}/revisions` | 列出项目修订计划 |
| GET | `/projects/{id}/revisions/{plan_id}` | 计划详情 + 解析结果链 |

### 上传（G-03）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/projects/{id}/uploads` | 上传并解析 TXT/DOCX（≤10MB），落盘 + 返回解析元数据 |
| GET | `/projects/{id}/uploads` | 列出项目上传记录 |

### 导入分类（G-04）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/projects/{id}/runs`（`action=import`） | 对上传文件执行导入分类（规则 + LLM 兜底），持久化 `import_classification` 并路由 |

### 导出（G-06）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/projects/{id}/exports` | 发起导出（kinds / format 可选显式版本），202 + Run |
| GET | `/exports/{artifact_id}/download` | 下载导出文件（归属 + 类型 + 存储三层校验） |

## POST /projects/{id}/runs — 创建 Run

### 请求体

```json
{
  "action": "create_script",
  "options": {
    "user_input": "一个被青训队抛弃的足球少年逆袭故事",
    "source_type": "idea",
    "outline_count": 10,
    "script_count": 3
  },
  "idempotency_key": "optional-client-generated-key"
}
```

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `action` | string | 是 | `create_script` / `evaluate` / `revise` / `platform_smoke` / `import` / `export` |
| `options` | object | `create_script` 时必需 | 创作选项 |
| `options.user_input` | string (1-10000) | 是 | 用户创作的 Idea/Outline |
| `options.source_type` | string | 否 | 默认 `"idea"` |
| `options.outline_count` | int (1-100) | 否 | 默认 10 |
| `options.script_count` | int (1-50) | 否 | 默认 3 |
| `idempotency_key` | string (≤128) | 否 | 幂等去重键 |

### 响应

**202 Accepted** — Run 已创建并进入队列，后台 Worker 将执行 Workflow。

```json
{
  "run_id": "550e8400-e29b-41d4-a716-446655440000",
  "project_id": "550e8400-e29b-41d4-a716-446655440001",
  "action": "create_script",
  "status": "queued",
  "config_snapshot": {
    "options": {
      "user_input": "...",
      "source_type": "idea",
      "outline_count": 10,
      "script_count": 3
    }
  },
  "created_at": "2026-07-25T12:00:00Z",
  "updated_at": "2026-07-25T12:00:00Z"
}
```

**404 Not Found** — 项目不存在  
**422 Unprocessable Entity** — 请求体校验失败（如 user_input 为空）

### 工作机制

1. POST → Run 状态 `queued` → 后台调度 `asyncio.create_task`
2. Worker：`queued` → `running` → 执行 LangGraph Creation Workflow
3. Workflow：normalize → retrieve → story_bible → outline → write 1..3 → finalize
4. 最终：`running` → `completed`（或 `failed`）
5. 每个节点发布 `node.started` / `node.completed` / `artifact.created` 事件

### 幂等性

相同 `idempotency_key` 的重复请求返回同一个 `run_id`（内存级去重）。

## GET /runs/{id}/events — SSE 事件流

### 请求

```
GET /api/v1/runs/{id}/events
Headers:
  Accept: text/event-stream
  Last-Event-ID: <上次收到的事件 ID>  (可选，用于断线重连)
```

### 事件格式

```
data: {"event_id":"...","run_id":"...","sequence":1,"type":"run.created","payload":{...}}

: heartbeat

```

事件类型：`run.created` | `run.running` | `node.started` | `node.completed` | `artifact.created` | `run.completed` | `run.failed`

### 断线重连

传入 `Last-Event-ID` header 后，服务端从 PostgreSQL 补发之后的所有事件。

## POST /runs/{id}/retry — 重试失败的 Run（I-01）

### 请求

```
POST /api/v1/runs/{run_id}/retry
```

无请求体。

### 响应

**200 OK** — Run 回到 `queued` 并重新调度后台 Worker：

```json
{
  "run_id": "550e8400-e29b-41d4-a716-446655440000",
  "action": "create_script",
  "status": "queued",
  "error_code": null,
  "error_detail": null
}
```

### 工作机制

1. 仅 `failed` / `needs_review` 状态可重试；`completed` / `cancelled` → `409 RUN_NOT_RETRYABLE`，`queued` / `running` → `409 RUN_ALREADY_ACTIVE`。
2. 以 `state_summary`（completed_nodes / input_hashes / status 快照）为初始状态重放：已完成节点早退 → **不重调 LLM、不重复创建 Artifact、不重复推进 revision_round**（I-01 验收"恢复不重复成功节点"）。
3. 重试前清空上一轮 `error_code` / `error_detail`。

## GET /runs/{id}/diagnostics — Run 运行诊断（I-02）

### 请求

```
GET /api/v1/runs/{run_id}/diagnostics
```

### 响应（200）

聚合事件表输出节点时间线 / LLM 调用统计 / 失败信息（无新增存储）：

```json
{
  "run_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "total_duration_ms": 42315,
  "nodes": [
    {"node_name": "normalize", "status": "completed", "duration_ms": 820},
    {"node_name": "story_bible", "status": "completed", "duration_ms": 14100}
  ],
  "llm_calls": 14,
  "llm_tokens": {"prompt": 18420, "completion": 5201},
  "errors": [{"error_code": "LLM_TIMEOUT", "node_name": "write_episode"}]
}
```

`errors` 仅在失败时非空；配合 `GET /metrics`（Prometheus 文本）满足可观测需求（详见 [OPERATIONS.md](OPERATIONS.md)）。

## POST /projects/{id}/revisions — 发起修订

F-06：通过 HTTP 暴露修订闭环。返回 **202 + Run**，进度经
`GET /runs/{id}` 轮询或 `GET /runs/{id}/events`（SSE）观察。

支持两种模式：
- **自动修订**：不传 `script_artifact_id`，确定性选最低分集（仅 `need_revision=true` 的集）；
- **单集修订**：传 `script_artifact_id` 指定一个**合法剧本版本**（任意版本，不要求最新），
  可选 `user_instruction`（不能绕过锁定事实）。

### 请求体

```json
{
  "script_artifact_id": "550e8400-e29b-41d4-a716-446655440010",
  "user_instruction": "加强反派动机，但不得改变主角身世",
  "idempotency_key": "optional-client-generated-key"
}
```

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `script_artifact_id` | UUID | 否 | 指定待修订剧本版本；缺省 → 自动修订 |
| `user_instruction` | string (≤2000) | 否 | 用户补充要求（**不可违反锁定事实**，服务端硬性并入 preserve） |
| `idempotency_key` | string (≤128) | 否 | 幂等去重键 |

### 响应

**202 Accepted** — 修订 Run 已创建并进入队列（同 `POST /runs` 的 `RunResponse` 结构）。

### 错误码

| 状态码 | code | 说明 |
|------|------|------|
| 404 | `SCRIPT_NOT_FOUND` | 指定剧本不存在 / 非 `script_draft` / 非 `valid` |
| 403 | `CROSS_PROJECT_ACCESS` | 指定剧本不属于当前项目 |
| 404 | `EVALUATION_NOT_FOUND` | 指定剧本尚无绑定评估（"已过期评估不匹配"拒绝） |

### 工作机制

1. POST → 同步校验（剧本存在 / 类型 / 状态 / 归属 / 绑定评估）→ Run 状态 `queued`
2. Worker：`queued` → `running` → 执行独立 `build_revision_workflow()`
   （select_revision → revise → continuity_check → re_evaluate）
3. 最终：`running` → `completed`（或 `needs_review`：连续性失败 / 重评显著下降 /
   修订轮次已用满仍存在需修订集）

## GET /projects/{id}/revisions — 修订计划列表

按集号升序、版本升序返回项目全部 `revision_plan` Artifact。

**响应（200）**

```json
{
  "items": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440020",
      "project_id": "...",
      "type": "revision_plan",
      "version": 1,
      "episode_number": 1,
      "status": "valid",
      "content": {
        "episode_number": 1,
        "source_script_artifact_id": "...",
        "source_evaluation_artifact_id": "...",
        "operations": [],
        "locked_facts": [],
        "max_change_ratio": 0.3,
        "user_instruction": null
      },
      "created_at": "...",
      "updated_at": "..."
    }
  ],
  "total": 1,
  "offset": 0,
  "limit": 20
}
```

## GET /projects/{id}/revisions/{plan_id} — 修订计划详情

返回计划本身 + 沿 `ArtifactLink` 反查解析的**结果链**（每段防御式置空）：

| 字段 | 说明 |
|------|------|
| `result_chain.source_script` | 计划引用的原稿 Artifact |
| `result_chain.source_evaluation` | 计划引用的评估报告 |
| `result_chain.candidate_script` | `relation="revises"` 指向该计划的候选新稿 |
| `result_chain.continuity_check` | 候选稿派生的连续性检查结果 |
| `result_chain.new_evaluation` | 绑定候选稿的重评报告 |
| `result_chain.diff_ids` | Diff 两端：`{"base": 原稿, "target": 候选稿}`，供 `/artifacts/diff` 使用 |

**错误码**：跨项目 403 `CROSS_PROJECT_ACCESS`；非修订计划 / 不存在 404 `ARTIFACT_NOT_FOUND`。

## POST /projects/{id}/exports — 发起导出

异步导出（确定性，不调 LLM）：Worker 组装各 kind 的 latest valid Artifact → 序列化 → 落盘 → 生成 `export_file` Artifact，随后 SSE 推送 `run.completed`。

### 请求体

```json
{
  "kinds": ["story_bible", "outline", "script", "evaluation", "revision"],
  "format": "markdown",
  "artifact_ids": null,
  "idempotency_key": "optional-client-generated-key"
}
```

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `kinds` | string[] | 是（≥1） | `story_bible` / `outline` / `script` / `evaluation` / `revision` |
| `format` | string | 否 | `markdown`（默认）/ `docx` |
| `artifact_ids` | `dict<kind, artifact_id[]>` | 否 | 缺省各 kind 取 latest valid；提供时显式指定 Artifact 版本 |
| `idempotency_key` | string | 否 | ≤128，幂等键 |

### 响应（202）

```json
{
  "run_id": "...",
  "project_id": "...",
  "action": "export",
  "status": "queued",
  "config_snapshot": {"options": {"kinds": [...], "format": "markdown"}},
  "created_at": "...", "updated_at": "..."
}
```

**错误码**：404 `PROJECT_NOT_FOUND`（项目不存在）；422 `VALIDATION_ERROR`（非法 kind / 空 kinds）。

## GET /exports/{artifact_id}/download — 下载导出文件

校验顺序：Artifact 存在且为 `export_file` → `project_id` 归属 → 本地文件存在 → 返回文件字节。

### 请求

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `artifact_id` | UUID | 是 | 路径参数：`export_file` Artifact ID |
| `project_id` | UUID | 是 | 查询参数：归属校验用，跨项目 403 |

### 响应（200）

- `content-type`：`text/markdown; charset=utf-8`（markdown）/ `application/vnd.openxmlformats-officedocument.wordprocessingml.document`（docx）；
- `Content-Disposition: attachment; filename="ascii_fallback"; filename*=UTF-8''<percent-encoded>`：中文文件名经 RFC 5987 `filename*` 编码，ASCII 兜底，不含路径分隔符 / 控制字符。

### 错误码

| 状态码 | code | 含义 |
|--------|------|------|
| 403 | `CROSS_PROJECT_ACCESS` | artifact 不属于给定 project_id |
| 404 | `EXPORT_FILE_MISSING` | Artifact 不存在 / 非 export_file / 存储文件已丢失 |

## Artifact 类型

| Type | 说明 | 集数 |
|------|------|------|
| `normalized_requirement` | 归一化需求 | — |
| `story_bible` | StoryBible | — |
| `episode_outline_set` | 10 集分集大纲 | — |
| `script_draft` | 单集剧本 | 1-3 |
| `continuity_state` | 连续性状态 | — |
| `evaluation_report` | 评估报告（绑定被评估剧本版本） | 1-3 |
| `revision_plan` | 修订计划（引用原稿 / 评估 / 锁定事实） | 1-3 |
| `continuity_check` | 修订稿连续性检查结果 | 1-3 |
| `conversation_summary` | 会话滚动摘要（G-01，消息数达阈值触发） | — |
| `import_classification` | 导入分类结果（G-04，`content_type` + 路由依据） | — |
| `export_file` | 导出文件元数据（G-05/G-06，`storage_key` 指向 FileStore） | — |
