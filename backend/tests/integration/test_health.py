"""B-01 集成测试 — 健康检查端点与错误模型。

验证 DEV_PLAN.md §B-01 全部验收条件：
- /health/live 不依赖外部服务
- /health/ready 在依赖不可用时返回 503 并指明依赖名
- 所有错误响应包含 request_id
- 日志为结构化 JSON

注意：本文件不使用 `from __future__ import annotations`，
确保 FastAPI 能在运行时解析内联定义的 Pydantic 模型类型。
"""

import json
import logging
from io import StringIO
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

# ========================================================================
# /health/live — 存活检查
# ========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
class TestHealthLive:
    """存活检查不依赖任何外部服务，始终返回 200。"""

    async def test_live_returns_200(self, async_client: AsyncClient) -> None:
        """GET /api/v1/health/live 返回 200 且 body 为 {"status": "ok"}。"""
        response = await async_client.get("/api/v1/health/live")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    async def test_live_includes_request_id_header(self, async_client: AsyncClient) -> None:
        """响应头应包含 X-Request-ID。"""
        response = await async_client.get("/api/v1/health/live")
        assert "x-request-id" in response.headers
        rid = response.headers["x-request-id"]
        assert rid != ""
        # UUID v4 格式：36 字符，含 4 个连字符
        assert len(rid) == 36

    async def test_live_echoes_incoming_request_id(self, async_client: AsyncClient) -> None:
        """当请求携带 X-Request-ID 时，响应应回传相同值。"""
        custom_id = "abcdef12-3456-7890-abcd-ef1234567890"
        response = await async_client.get(
            "/api/v1/health/live",
            headers={"X-Request-ID": custom_id},
        )
        assert response.headers["x-request-id"] == custom_id


# ========================================================================
# /health/ready — 就绪检查
# ========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
class TestHealthReady:
    """就绪检查验证 DB/Redis 可用性。"""

    async def test_ready_200_when_dependencies_ok(self, async_client: AsyncClient) -> None:
        """当 DB 和 Redis 检查通过时，/health/ready 返回 200。

        使用 mock 模拟 asyncpg 和 redis 连接成功。
        """
        with (
            patch("app.api.v1.router._check_database", new_callable=AsyncMock) as mock_db,
            patch("app.api.v1.router._check_redis", new_callable=AsyncMock) as mock_redis,
        ):
            mock_db.return_value = {"name": "database", "status": "ok", "latency_ms": 5.0}
            mock_redis.return_value = {"name": "redis", "status": "ok", "latency_ms": 2.0}

            response = await async_client.get("/api/v1/health/ready")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert len(data["checks"]) == 2
            assert data["checks"][0]["status"] == "ok"
            assert data["checks"][1]["status"] == "ok"

    async def test_ready_503_when_db_unavailable(self, async_client: AsyncClient) -> None:
        """DB 不可用时返回 503，响应体指名 'database' 依赖。"""
        with (
            patch("app.api.v1.router._check_database", new_callable=AsyncMock) as mock_db,
            patch("app.api.v1.router._check_redis", new_callable=AsyncMock) as mock_redis,
        ):
            mock_db.return_value = {
                "name": "database",
                "status": "unavailable",
                "latency_ms": 0,
                "error": "connection refused",
            }
            mock_redis.return_value = {"name": "redis", "status": "ok", "latency_ms": 1.0}

            response = await async_client.get("/api/v1/health/ready")

            assert response.status_code == 503
            data = response.json()
            assert data["code"] == "SERVICE_UNAVAILABLE"
            assert "database" in data["detail"]
            assert "request_id" in data

    async def test_ready_503_when_redis_unavailable(self, async_client: AsyncClient) -> None:
        """Redis 不可用时返回 503，响应体指名 'redis' 依赖。"""
        with (
            patch("app.api.v1.router._check_database", new_callable=AsyncMock) as mock_db,
            patch("app.api.v1.router._check_redis", new_callable=AsyncMock) as mock_redis,
        ):
            mock_db.return_value = {"name": "database", "status": "ok", "latency_ms": 3.0}
            mock_redis.return_value = {
                "name": "redis",
                "status": "unavailable",
                "latency_ms": 0,
                "error": "connection refused",
            }

            response = await async_client.get("/api/v1/health/ready")

            assert response.status_code == 503
            data = response.json()
            assert data["code"] == "SERVICE_UNAVAILABLE"
            assert "redis" in data["detail"]
            assert "request_id" in data

    async def test_ready_503_names_all_failed_dependencies(self, async_client: AsyncClient) -> None:
        """当多个依赖同时不可用时，响应列出所有失败的依赖。"""
        with (
            patch("app.api.v1.router._check_database", new_callable=AsyncMock) as mock_db,
            patch("app.api.v1.router._check_redis", new_callable=AsyncMock) as mock_redis,
        ):
            mock_db.return_value = {
                "name": "database",
                "status": "unavailable",
                "latency_ms": 0,
                "error": "timeout",
            }
            mock_redis.return_value = {
                "name": "redis",
                "status": "unavailable",
                "latency_ms": 0,
                "error": "timeout",
            }

            response = await async_client.get("/api/v1/health/ready")

            assert response.status_code == 503
            data = response.json()
            assert "database" in data["detail"]
            assert "redis" in data["detail"]


# ========================================================================
# 错误响应 — request_id 验证
# ========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
class TestErrorResponseRequestId:
    """验证所有 HTTP 错误响应均包含 request_id。"""

    async def test_404_includes_request_id(self, async_client: AsyncClient) -> None:
        """访问不存在的路径时，404 响应包含 request_id。"""
        response = await async_client.get("/api/v1/nonexistent-endpoint-xyz")
        assert response.status_code == 404
        data = response.json()
        assert "request_id" in data
        assert data["request_id"] != ""
        assert data["code"] == "NOT_FOUND"

    async def test_405_includes_request_id(self, async_client: AsyncClient) -> None:
        """使用不允许的方法时，405 响应包含 request_id。"""
        # /health/live 只接受 GET
        response = await async_client.post("/api/v1/health/live")
        assert response.status_code == 405
        data = response.json()
        assert "request_id" in data
        assert data["request_id"] != ""

    async def test_422_includes_request_id_and_field_errors(self) -> None:
        """请求参数校验失败时，422 响应包含 request_id 和字段错误列表。

        通过注册一个临时验证路由来触发 422——因为 B-01 没有带请求体的端点。
        """
        from pydantic import BaseModel

        from app.core.config import Settings
        from app.main import create_app

        # 创建一个独立 app，注入需要 JSON body 的临时端点
        test_settings = Settings(app_env="test")
        test_settings.apply_env_overrides()
        temp_app = create_app(settings=test_settings)

        class _TestBody(BaseModel):
            required_field: str

        @temp_app.post("/api/v1/_test_validation")
        async def _test_endpoint(body: _TestBody) -> dict[str, str]:
            return {"received": body.required_field}

        from httpx import ASGITransport
        from httpx import AsyncClient as AsyncHTTPClient

        transport = ASGITransport(app=temp_app)
        async with AsyncHTTPClient(transport=transport, base_url="http://test") as client:
            # 发送缺少 required_field 的 JSON — 触发 422
            response = await client.post(
                "/api/v1/_test_validation",
                json={"wrong_field": "value"},
            )

        assert response.status_code == 422
        data = response.json()
        assert "request_id" in data
        assert data["request_id"] != ""
        assert data["code"] == "VALIDATION_ERROR"
        assert data["errors"] is not None
        assert len(data["errors"]) >= 1
        assert any("required_field" in e["field"] for e in data["errors"])

    async def test_error_response_has_consistent_structure(self, async_client: AsyncClient) -> None:
        """所有错误响应的 JSON 结构一致（request_id, detail, code, path, timestamp）。"""
        response = await async_client.get("/api/v1/not-found-page")
        data = response.json()

        required_keys = {"request_id", "detail", "code", "path", "timestamp"}
        assert required_keys.issubset(data.keys())
        assert data["path"] == "/api/v1/not-found-page"
        # timestamp 应为 ISO 8601 格式
        assert "T" in data["timestamp"]


# ========================================================================
# Request ID 回传
# ========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
class TestRequestIdPropagation:
    """验证 request_id 在整个请求生命周期中的传播。"""

    async def test_custom_request_id_preserved_on_error(self, async_client: AsyncClient) -> None:
        """即使请求失败（404），自定义 X-Request-ID 也被保留在响应头和 body 中。"""
        custom_id = "custom-req-id-1234567890ab"
        response = await async_client.get(
            "/api/v1/nonexistent",
            headers={"X-Request-ID": custom_id},
        )
        assert response.headers["x-request-id"] == custom_id
        data = response.json()
        assert data["request_id"] == custom_id


# ========================================================================
# OpenAPI Schema
# ========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
class TestOpenAPI:
    """验证 OpenAPI schema 结构。"""

    async def test_openapi_schema_has_health_tag(self, async_client: AsyncClient) -> None:
        """OpenAPI schema 应包含 'health' 标签。"""
        response = await async_client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        tags = [t["name"] for t in schema.get("tags", [])]
        assert "health" in tags

    async def test_openapi_schema_has_health_paths(self, async_client: AsyncClient) -> None:
        """OpenAPI schema 应包含 /api/v1/health/live 和 /api/v1/health/ready。"""
        response = await async_client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        paths = schema.get("paths", {})
        assert "/api/v1/health/live" in paths
        assert "/api/v1/health/ready" in paths

    async def test_openapi_version(self, async_client: AsyncClient) -> None:
        """OpenAPI schema 版本信息正确。"""
        response = await async_client.get("/openapi.json")
        schema = response.json()
        assert schema["info"]["title"] == "DramaAgent"
        assert schema["info"]["version"] == "0.1.0"


# ========================================================================
# 结构化日志
# ========================================================================


class TestStructuredLogging:
    """验证日志配置为结构化 JSON 输出（纯同步测试）。"""

    def test_log_output_is_json(self) -> None:
        """setup_logging 后，logger 输出应为有效的 JSON 行。

        测试通过捕获 handler 输出来验证格式化器行为。
        """
        from app.core.logging import JsonFormatter, setup_logging

        setup_logging(level="DEBUG")
        logger = logging.getLogger("drama_agent_test")
        logger.setLevel(logging.DEBUG)

        # 捕获输出
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        logger.handlers.clear()
        logger.addHandler(handler)

        try:
            logger.info("这是一条测试日志")
            output = stream.getvalue().strip()
            assert output, "日志应产生输出"
            record = json.loads(output)
            assert record["level"] == "INFO"
            assert record["message"] == "这是一条测试日志"
            assert "timestamp" in record
            assert record["logger"] == "drama_agent_test"
        finally:
            logger.handlers.clear()

    def test_log_output_includes_exception(self) -> None:
        """异常日志应包含 exception 字段。"""
        from app.core.logging import JsonFormatter

        logger = logging.getLogger("drama_agent_error_test")
        logger.setLevel(logging.DEBUG)
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        logger.handlers.clear()
        logger.addHandler(handler)

        try:
            try:
                raise ValueError("测试异常")
            except ValueError:
                logger.exception("捕获到异常")

            output = stream.getvalue().strip()
            record = json.loads(output)
            assert "exception" in record
            assert "测试异常" in record["exception"]
        finally:
            logger.handlers.clear()
