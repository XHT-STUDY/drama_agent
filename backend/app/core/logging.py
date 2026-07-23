"""结构化 JSON 日志配置。

使用 Python 标准库 logging + 自定义 JSON 格式化器，
不引入额外的日志框架依赖。

每条日志输出为一行 JSON，包含以下字段：
- timestamp: UTC 时间戳（ISO 8601）
- level: 日志级别
- logger: logger 名称
- message: 日志消息
- request_id: 当前请求 ID（若在请求上下文中）
- module: 产生日志的模块名
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any


class JsonFormatter(logging.Formatter):
    """将 LogRecord 格式化为一行 JSON。

    包含标准化字段，确保日志可被结构化日志系统（如 ELK、Loki）解析。
    """

    def format(self, record: logging.LogRecord) -> str:
        """将 LogRecord 转换为 JSON 字符串。"""
        from app.core.errors import _request_id_ctx

        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
        }

        # 安全获取当前 request_id（不在请求上下文中时为空字符串）
        try:
            request_id = _request_id_ctx.get()
            if request_id:
                log_entry["request_id"] = request_id
        except (LookupError, ValueError):
            pass

        # 附加异常信息（如果有）
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = str(record.exc_info[1])

        return json.dumps(log_entry, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    """配置根 logger 输出结构化 JSON 到 stdout。

    在应用启动时调用一次即可，所有模块通过
    logging.getLogger(__name__) 获取的 logger 均会使用此配置。

    Args:
        level: 日志级别字符串，如 "DEBUG"、"INFO"、"WARNING"
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 清除已有 handler，避免重复添加
    root_logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root_logger.addHandler(handler)

    # 降低第三方库的日志噪音
    logging.getLogger("uvicorn.access").handlers.clear()
    logging.getLogger("uvicorn.access").addHandler(handler)
    logging.getLogger("uvicorn.access").propagate = False


def get_logger(name: str) -> logging.Logger:
    """获取具名 logger。

    便捷封装，避免各模块直接调用 logging.getLogger。

    Args:
        name: 通常传入 __name__ 即可
    """
    return logging.getLogger(name)
