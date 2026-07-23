"""DramaAgent API 层。

包含 FastAPI 路由、依赖注入和中间件。
API 层仅负责参数解析、认证和调用应用服务；
禁止直接调用 LLM 或直接写 ORM（见 DEV_PLAN.md §4.1）。
"""
