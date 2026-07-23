"""统一错误模型与异常处理。

定义项目的标准错误响应格式、自定义异常层次结构，
以及 FastAPI 异常处理器注册函数。

设计原则：
- 所有错误响应均包含 request_id（从 contextvars 获取），便于排查；
- 错误码使用大写蛇形命名，前端可根据 code 做分支处理；
- 自定义异常继承 AppError，handler 按异常类型精准匹配。
"""

from __future__ import annotations

import traceback
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

# ---- contextvars：跨 middleware / handler 传递 request_id ----

_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")

# ---- 错误响应模型 ----


class FieldError(BaseModel):
    """单个字段的校验错误详情。

    用于 422 响应中逐字段列出校验失败原因。
    """

    model_config = {"extra": "forbid"}

    field: str = Field(..., description="字段路径，如 body.title 或 query.page")
    message: str = Field(..., description="人类可读的错误描述")
    type: str = Field(..., description="错误类型标识，如 missing、string_type")


class ErrorResponse(BaseModel):
    """统一 API 错误响应体。

    所有非预期的 HTTP 错误均使用此格式返回，
    确保客户端能从中获取 request_id 以便排查。
    """

    model_config = {"extra": "forbid"}

    request_id: str = Field(..., description="当前请求的唯一标识")
    detail: str = Field(..., description="人类可读错误描述")
    code: str = Field(default="INTERNAL_ERROR", description="机器可读错误码")
    path: str = Field(default="", description="触发错误的请求路径")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="UTC 时间戳（ISO 8601）",
    )
    errors: list[FieldError] | None = Field(default=None, description="字段级错误列表（422 时填充）")


# ---- 自定义异常层次 ----


class AppError(Exception):
    """应用层自定义异常基类。

    所有可控的业务异常均应继承此类，
    异常处理器会将其映射为 ErrorResponse。
    """

    status_code: int = 500
    code: str = "INTERNAL_ERROR"

    def __init__(self, detail: str = "", *, status_code: int | None = None, code: str | None = None) -> None:
        self.detail = detail
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code
        super().__init__(detail)


class NotFoundError(AppError):
    """资源未找到（404）。"""

    status_code = 404
    code = "NOT_FOUND"


class ServiceUnavailableError(AppError):
    """依赖服务不可用（503）。

    用于 /health/ready 发现 DB/Redis 不可用等场景。
    detail 中应指明具体不可用的服务名称。
    """

    status_code = 503
    code = "SERVICE_UNAVAILABLE"


# ---- 辅助函数 ----


def _build_error_response(
    request: Request,
    *,
    status_code: int = 500,
    code: str = "INTERNAL_ERROR",
    detail: str = "",
    errors: list[FieldError] | None = None,
) -> JSONResponse:
    """构建 ErrorResponse 并包装为 JSONResponse。

    自动从 request 对象中获取路径和 request_id，
    确保错误响应与 middleware 的一致性。
    """
    # 尝试从 contextvar 获取 request_id；若未设置则标记为 unknown
    request_id = _request_id_ctx.get() or "unknown"

    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            request_id=request_id,
            detail=detail,
            code=code,
            path=request.url.path,
            errors=errors,
        ).model_dump(),
    )


# ---- FastAPI 异常处理器 ----


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """处理所有 AppError 子类异常。

    AppError 是可控的业务异常，直接使用其 status_code 和 code。
    """
    return _build_error_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        detail=exc.detail or str(exc),
    )


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """处理 FastAPI 请求校验失败（422）。

    将 Pydantic 校验错误转换为 FieldError 列表，
    便于前端逐字段展示错误信息。
    """
    field_errors: list[FieldError] = []
    for error in exc.errors():
        field_errors.append(
            FieldError(
                field=".".join(str(loc) for loc in error["loc"]),
                message=error["msg"],
                type=error["type"],
            )
        )

    return _build_error_response(
        request,
        status_code=422,
        code="VALIDATION_ERROR",
        detail="请求参数校验失败",
        errors=field_errors,
    )


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """处理 Starlette HTTPException。

    包括 404（路由未匹配）、405（方法不允许）等标准 HTTP 错误。
    """
    code_map: dict[int, str] = {
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        409: "CONFLICT",
        429: "TOO_MANY_REQUESTS",
    }
    return _build_error_response(
        request,
        status_code=exc.status_code,
        code=code_map.get(exc.status_code, "HTTP_ERROR"),
        detail=exc.detail or str(exc),
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """处理所有未被上述 handler 捕获的异常（500）。

    内部异常不向外暴露堆栈细节，仅记录日志；
    返回通用 500 错误，内部 traceback 通过日志模块输出。
    """
    # 使用 print 兜底 — 正式日志在 logging 模块配置后由 structlog 接管
    traceback.print_exc()
    return _build_error_response(
        request,
        status_code=500,
        code="INTERNAL_ERROR",
        detail="服务器内部错误",
    )


# ---- 注册所有异常处理器 ----


def register_exception_handlers(app: Any) -> None:
    """将自定义异常处理器注册到 FastAPI app 实例。

    注册顺序决定匹配优先级——先注册的优先匹配。
    AppError 必须在 HTTPException 之前注册，
    否则 Starlette 会抢先处理。
    """
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
