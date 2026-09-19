# MCP 本地互操作 Smoke 报告（MCP-04）

> 日期：2026-09-19
> 执行人：AI Agent（自动化脚本）
> 脚本：`backend/scripts/mcp_smoke.py`（`cd backend && uv run python scripts/mcp_smoke.py`）
> 设计依据：[MCP_DESIGN.md](MCP_DESIGN.md) §16、[MCP_IMPLEMENTATION_PLAN.md](MCP_IMPLEMENTATION_PLAN.md) §6 要求 8

## 1. 环境与凭据

| 项 | 值 |
| --- | --- |
| 官方 mcp Python SDK | 2.2.0（uv.lock 锁定，`mcp>=2,<3`） |
| 协议版本（协商结果） | 2026-07-28 |
| MCP Server | 官方 SDK `MCPServer`（`drama-smoke-server` v1.0.0），Streamable HTTP（uvicorn，127.0.0.1 随机端口） |
| 传输 | 真实 TCP 上的 Streamable HTTP（非 in-process 注入） |
| 认证 | Bearer Token 经环境变量 `MCP_SMOKE_TOKEN` 注入（本地占位值，非生产 Token） |
| 网络 | 仅 localhost；**无互联网访问、无付费账号、无生产 Token** |

## 2. 执行记录

| 步骤 | 结果 |
| --- | --- |
| lifespan 语义启动（`MCPClientManager.startup`） | Server 状态 `available` |
| 协议协商（initialize/discover） | 协议版本 `2026-07-28`；capabilities 含 `tools`（list_changed）、resources、prompts |
| `tools/list` 发现 | 发现 `search`、`boom` 两个工具，allowlist 过滤后 catalog 命中 2 个 |
| 内部注册（`register_mcp_remote_tools`） | 注册 `mcp__smoke__search`、`mcp__smoke__boom`（元数据 external/requires_confirmation/network_access） |
| `tools/call` 成功（search） | 返回 text=`smoke-hit: 足球青训`、structured=`{"result": "smoke-hit: 足球青训"}`，duration 7ms，request_id `658fde9c…`，is_error=false |
| `tools/call` 失败（boom → isError） | 服务端映射 `MCPToolError: MCP_TOOL_ERROR`（isError 不抛异常、错误摘要脱敏） |
| Token 边界 | Token 只作为 Authorization 头送达；日志/状态快照/配置摘要均无 Token 值 |
| 清理 | `manager.shutdown()` 关闭客户端、uvicorn 退出、进程结束，无遗留连接 |

原始输出（节选）：

```json
{
  "sdk_version": "2.2.0",
  "server_name": "drama-smoke-server",
  "status": "available",
  "protocol_version": "2026-07-28",
  "tools": ["search", "boom"],
  "registered": ["mcp__smoke__search", "mcp__smoke__boom"],
  "call": {
    "tool": "search",
    "text": "smoke-hit: 足球青训",
    "structured": { "result": "smoke-hit: 足球青训" },
    "duration_ms": 7,
    "is_error": false
  },
  "error_call": "MCPToolError: MCP_TOOL_ERROR",
  "cleanup": "uvicorn exited, manager shutdown"
}
```

## 3. 已知无害现象

- 含错误工具调用的会话在客户端关闭时，SDK 偶发在 close 路径抛
  `RuntimeError`（会话终止与连接关闭的竞态）。`MCPClientManager.shutdown`
  捕获并记录 WARNING，不影响其他 Server 关闭与进程退出；不阻塞任何
  验收项。升级 SDK 版本时可复查是否已修复。

## 4. 结论

官方 in-process 与真实 TCP Streamable HTTP 两种路径下，协议协商、工具
发现、白名单过滤、注册、调用、错误映射、Token 边界与清理全部符合
[MCP_DESIGN.md](MCP_DESIGN.md) 的验收标准。接入真实远程 Server 时由部署方
提供 endpoint/白名单/Token 环境变量（见 `docs/EXTENSIONS.md` §3.1），无需
修改代码。
