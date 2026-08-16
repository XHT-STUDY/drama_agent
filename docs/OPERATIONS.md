# DramaAgent 运维手册

> 面向部署与值守人员：如何安装、启动、配置、监控、备份与排查 DramaAgent。
> 开发契约与任务状态见 [DEV_PLAN.md](DEV_PLAN.md)；API 契约见 [API_CONTRACT.md](API_CONTRACT.md)。

## 1. 环境与依赖

| 组件 | 版本 / 说明 |
| --- | --- |
| 操作系统 | Linux（WSL2 / 容器均可），Python 3.12+，Node.js 20+ |
| PostgreSQL | 14+，需 `pgvector` 扩展（`CREATE EXTENSION vector`） |
| Redis | 6+（SSE pub/sub、短期记忆、限流） |
| 构建工具 | `make`、`uv`、`pnpm` |

首次准备：

```bash
cp .env.example .env        # 按需修改，.env 不入库
make install                # uv sync + pnpm install
make up                     # 启动 PostgreSQL + Redis（Docker Compose）
make doctor                 # 健康检查（DB / Redis / 配置）
```

## 2. 启动 / 停止

| 操作 | 命令 |
| --- | --- |
| 启动后端 + 前端 | `make dev`（详见 Makefile；前端 3000 端口，后端 8000 端口） |
| 仅后端 | `cd backend && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| 仅前端 | `cd frontend && pnpm dev` |
| 停止依赖容器 | `make down` |

健康检查：

```bash
curl http://localhost:8000/health/live   # 存活（不依赖外部服务）
curl http://localhost:8000/health/ready  # 就绪（检查 DB + Redis）
```

## 3. 关键配置

全部配置集中在 `.env`（模板见 `.env.example`），启动时由 Pydantic Settings 读取。

### 3.1 可观测性（I-02）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `METRICS_ENABLED` | `true` | `GET /metrics` 开关；`false` 时该端点返回 404，但埋点仍累积（便于按需开启） |
| `LOG_FORMAT` | `console` | `console` 彩色人类可读 / `json` 单行 JSON（供 ELK/Loki 解析，见 §5.3） |

### 3.2 LLM 与成本保护（I-01）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `LLM_API_BASE` / `LLM_API_KEY` | - | OpenAI 兼容 API 地址与密钥（真实 LLM 必需） |
| `LLM_MAX_RETRIES` | `2` | 可重试错误（429 / 超时 / 5xx）的指数退避最大重试次数 |
| `RUN_MAX_LLM_CALLS` | `18` | 单 Run 软上限：超过只发 `run.warning` 事件，不阻断 |
| `RUN_MAX_LLM_CALLS_HARD` | `24` | 单 Run 硬上限：超过抛 `RUN_BUDGET_EXCEEDED`，Run 失败 |
| `RUN_MAX_LLM_TOKENS_HARD` | `200000` | 单 Run token 硬上限，同上 |

> 预算 registry 为进程内实现：重启即清零，且只在单 worker 部署下准确。多 worker 部署的预算与限流见 [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md)。

## 4. 监控：指标（GET /metrics）

### 4.1 端点与格式

```
GET /metrics
```

返回 Prometheus 文本格式（`Content-Type: text/plain; version=0.0.4`），可由 Prometheus 直接抓取。
关闭方式：`.env` 设 `METRICS_ENABLED=false` 后重启。

### 4.2 指标清单

| 指标 | 类型 | 标签 | 含义 |
| --- | --- | --- | --- |
| `workflow_runs_total` | counter | `action,status` | Run 创建与状态变更总数 |
| `workflow_node_duration_seconds` | histogram | `node` | 创作节点执行耗时（秒） |
| `llm_calls_total` | counter | `node,model,status` | LLM 调用结果（`status=ok` 或错误码） |
| `llm_retry_total` | counter | `reason` | 可重试错误触发的重试次数 |
| `llm_token_usage_total` | counter | `kind` | LLM token 用量（`prompt` / `completion`） |
| `artifact_created_total` | counter | `artifact_type` | 新建 Artifact 数 |
| `export_total` | counter | `format,status` | 导出结果（Markdown/DOCX 成败） |
| `sse_connections_active` | gauge | - | 当前活跃 SSE 连接数 |
| `rag_retrieval_duration_seconds` | histogram | - | 知识库检索耗时（秒） |

**标签纪律**：所有标签均为低基数枚举（action / status / node / model / format 等）。`project_id`、`run_id` 等每请求变化的值**禁止入标签**（会打爆 Prometheus），请用诊断接口（§5.1）按 run 查询明细。

### 4.3 常见查询

```promql
# 创作 Run 成功率（按 action）
sum(workflow_runs_total{status="completed"}) / sum(workflow_runs_total)

# 节点 p95 耗时（秒）
histogram_quantile(0.95, sum by (le,node) (workflow_node_duration_seconds_bucket))

# LLM 错误率
sum(workflow_runs_total{status="failed"}) / sum(workflow_runs_total)
```

## 5. 排查：Run 诊断与日志

### 5.1 Run 诊断接口

```
GET /api/v1/runs/{run_id}/diagnostics
```

聚合 `workflow_events` 表（事件表 = 事实记录，Redis 丢失后仍可补），不新增存储。响应：

```json
{
  "run_id": "…",
  "status": "failed",
  "total_duration_ms": 42000,
  "nodes": [
    {"node_name": "normalize", "duration_ms": 800, "status": "completed"},
    {"node_name": "outline", "duration_ms": 12000, "status": "completed"},
    {"node_name": "write_episodes", "duration_ms": 15000, "status": "failed"}
  ],
  "llm_calls": 5,
  "llm_tokens": {"prompt": 12000, "completion": 4000},
  "errors": [{"error_code": "LLM_TIMEOUT", "node_name": "write_episodes"}]
}
```

用途：
- 按 run_id 复现完整节点时间线（哪一步最慢 / 在哪失败）；
- 统计一次 Demo / 一次创作调用了多少次 LLM、花了多少 token（`llm_calls` / `llm_tokens`）；
- 快速定位失败点：`errors[].error_code` + 回退到的失败节点名。

> `llm_calls` 由 Worker 在 Run 结束（`finally`）从预算 registry 发布 `run.llm_stats` 事件；无该事件时字段为 `null`。

### 5.2 常见错误码

| 错误码 | 含义 | 处理 |
| --- | --- | --- |
| `LLM_TIMEOUT` / `LLM_RATE_LIMITED` / `LLM_PROVIDER_ERROR` | LLM 服务端瞬时问题 | 重试 Run（`POST /runs/{id}/retry`）或等待后重试 |
| `RUN_BUDGET_EXCEEDED` | 超过硬预算 | 提高 `RUN_MAX_LLM_CALLS_HARD` 或优化 Prompt |
| `RUN_CANCELLED` | 用户取消 | 属正常路径 |
| `INVALID_OUTPUT` | LLM 输出多次不合法 | 检查 Prompt / 增加重试反馈 |

### 5.3 日志与脱敏

- `LOG_FORMAT=json` 时每条日志一行 JSON，字段契约：`timestamp` / `level` / `logger` / `message`（+ 可选 `rid` / `exception`）。
- 日志经 `RedactFilter` 统一脱敏：`sk-*` API Key、`api_key` 字段、Bearer 令牌、`access_token` 字段被掩蔽（保留前缀、掩蔽值），超长内容截断（正文 2000 字符 / 异常 4000 字符）。
- 排查问题请用 `rid`（request_id 前 8 位）跨服务串联，用 `GET /runs/{id}/diagnostics` 看 run 级明细。

## 6. 备份 / 恢复

PostgreSQL 是唯一事实源（Redis 丢失不造成资产损失，仅短期记忆 / 实时推送重建）。

```bash
# 备份（pg_dump 逻辑备份，含 schema + 数据）
pg_dump -U drama drama > drama_backup_$(date +%F).sql

# 恢复
createdb -U drama drama_restore
psql -U drama drama_restore < drama_backup_$(date +%F).sql
```

如需恢复后重建 `pgvector` 扩展：目标库先 `CREATE EXTENSION IF NOT EXISTS vector`。

## 7. 数据库迁移

```bash
cd backend
uv run alembic upgrade head     # 升级到最新 schema
uv run alembic downgrade -1     # 回退一个版本（谨慎，先备份）
uv run alembic current          # 查看当前版本
```

**Artifact 不可变**：修订从不覆盖旧 Artifact，而是产生新版本。数据库 / 对象存储误删会丢失该历史版本，请勿手动清理 `artifacts` 相关表。

## 8. 常见运维问题速查

| 症状 | 排查 |
| --- | --- |
| `/health/ready` 返回 503 | DB 或 Redis 未启动：`make up`；`make doctor` 看详情 |
| 创作 Run 一直 `running` | `GET /runs/{id}/diagnostics` 看停在哪个节点；查日志 `rid` 对应异常 |
| `/metrics` 404 | `.env` 设了 `METRICS_ENABLED=false`（见 §3.1） |
| 日志出现 `RUN_BUDGET_EXCEEDED` | 单 Run LLM 调用/Token 超预算，见 §5.2 |
| 上传文件失败 | 仅支持 TXT/DOCX、≤10MB；`core/file_parser.py` 的扩展名/魔数/路径穿越检查会拒绝异常输入 |
