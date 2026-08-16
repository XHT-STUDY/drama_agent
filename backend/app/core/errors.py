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


class InvalidFileTypeError(AppError):
    """文件类型不被接受（415，G-03）。

    上传的扩展名 / MIME / 内容签名不匹配 TXT|DOCX 时抛出。
    """

    status_code = 415
    code = "INVALID_FILE_TYPE"


class FileTooLargeError(AppError):
    """文件超过大小上限（413，G-03）。"""

    status_code = 413
    code = "FILE_TOO_LARGE"


class FileParseFailedError(AppError):
    """文件解析失败（422，G-03）。

    内容损坏（DOCX 非 zip）、编码无法识别、宏文档等场景抛出。
    """

    status_code = 422
    code = "FILE_PARSE_FAILED"


class ExportFileMissingError(NotFoundError):
    """导出文件不存在或已丢失（404，G-06）。

    下载导出文件时 Artifact 缺失 / 不是 export_file / 存储文件被清理
    均归为此错误，便于前端统一提示"导出文件不可用"。
    """

    code = "EXPORT_FILE_MISSING"


class RunNotRetryableError(AppError):
    """Run 处于终态，不可重试（409 RUN_NOT_RETRYABLE，I-01）。

    completed / cancelled 状态的 Run 没有可恢复的中间状态，拒绝 retry。
    """

    status_code = 409
    code = "RUN_NOT_RETRYABLE"


class RunAlreadyActiveError(AppError):
    """Run 正在执行，不可重试（409 RUN_ALREADY_ACTIVE，I-01）。

    queued / running 状态已有活跃 Worker，重复 retry 会造成并发执行。
    """

    status_code = 409
    code = "RUN_ALREADY_ACTIVE"


class BudgetExceededError(AppError):
    """Run 的 LLM 预算超限（409 RUN_BUDGET_EXCEEDED，I-01）。

    超过 run_max_llm_calls_hard / run_max_llm_tokens_hard 时抛出，
    由节点失败路径落库到 WorkflowRun.error_code。
    """

    status_code = 409
    code = "RUN_BUDGET_EXCEEDED"


class ExternalToolTimeoutError(AppError):
    """外部 MCP 工具调用超时（504 EXTERNAL_TOOL_TIMEOUT，I-04）。

    MCPToolAdapter 在配置的超时时间内未收到外部工具响应时抛出。
    detail 只说明超时，不泄漏外部服务内部信息。
    """

    status_code = 504
    code = "EXTERNAL_TOOL_TIMEOUT"


class ExternalToolError(AppError):
    """外部 MCP 工具调用失败（502 EXTERNAL_TOOL_ERROR，I-04）。

    外部工具返回错误 / 连接失败 / 响应无法解析时抛出。
    detail 使用泛化描述，不泄漏内部连接信息（地址、凭据、内部异常文本）。
    """

    status_code = 502
    code = "EXTERNAL_TOOL_ERROR"


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
