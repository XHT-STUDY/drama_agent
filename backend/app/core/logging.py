"""结构化日志配置。

双格式输出：
- Console: 彩色人类可读格式（local 环境默认）
- JSON:   一行 JSON 格式（production 环境，供 ELK/Loki 解析）

每条日志包含以下字段：
- time: 北京时间（Asia/Shanghai）
- level: 日志级别（彩色）
- logger: logger 名称简写
- msg: 日志消息
- request_id: 当前请求 ID（截取前 8 位）
- module: 产生日志的模块名
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

# 北京时区 (UTC+8)
_CST = timezone(timedelta(hours=8))

# ANSI 颜色码
_COLORS = {
    "DEBUG": "\033[36m",     # cyan
    "INFO": "\033[32m",      # green
    "WARNING": "\033[33m",   # yellow
    "ERROR": "\033[31m",     # red
    "CRITICAL": "\033[35m",  # magenta
}
_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"

# 日志级别简写
_LEVEL_SHORT: dict[str, str] = {
    "DEBUG": "DBG",
    "INFO": "INF",
    "WARNING": "WRN",
    "ERROR": "ERR",
    "CRITICAL": "CRT",
}


def _now_cst() -> str:
    """返回北京时间 ISO 格式字符串 (精确到秒)。"""
    return datetime.now(_CST).strftime("%Y-%m-%d %H:%M:%S")


def _logger_short(name: str) -> str:
    """缩短 logger 名称：app.workflows.nodes.normalize → w.nodes.normalize"""
    replacements = {
        "app.workflows.nodes.": "w.",
        "app.workflows.": "w.",
        "app.skills.": "sk.",
        "app.agents.": "ag.",
        "app.application.": "ap.",
        "app.events.": "ev.",
        "app.memory.": "mm.",
        "app.prompts.": "pm.",
        "app.llm.": "llm.",
        "app.api.v1.": "api.",
        "app.core.": "core.",
        "app.db.": "db.",
        "app.": "",
        "httpx": "http",
        "httptools_impl": "http",
    }
    result = name
    for old, new in replacements.items():
        if result.startswith(old):
            result = new + result[len(old):]
            break
    return result


def _request_id_short() -> str:
    """获取当前请求 ID 的前 8 位。"""
    try:
        from app.core.errors import _request_id_ctx
        rid = _request_id_ctx.get()
        return rid[:8] if rid else ""
    except (LookupError, ValueError):
        return ""


class ConsoleFormatter(logging.Formatter):
    """彩色人类可读格式，适合本地开发终端。"""

    def format(self, record: logging.LogRecord) -> str:
        ts = _now_cst()
        level = record.levelname
        color = _COLORS.get(level, "")
        lvl_short = _LEVEL_SHORT.get(level, level)
        logger_short = _logger_short(record.name)
        msg = record.getMessage()
        req_id = _request_id_short()

        parts: list[str] = []
        # 时间
        parts.append(f"{_DIM}{ts}{_RESET}")
        # 级别
        parts.append(f"{color}{_BOLD}{lvl_short}{_RESET}")
        # logger
        parts.append(f"{_DIM}{logger_short:<20}{_RESET}")
        # request_id
        if req_id:
            parts.append(f"{_DIM}{req_id}{_RESET}")
        # 消息
        parts.append(msg)

        # 异常信息
        if record.exc_info and record.exc_info[1]:
            parts.append(f"\n{_COLORS['ERROR']}{record.exc_info[1]}{_RESET}")

        return " ".join(parts)


class JsonFormatter(logging.Formatter):
    """结构化 JSON 格式，适合生产环境 / 日志采集系统。"""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "time": _now_cst(),
            "level": record.levelname,
            "logger": _logger_short(record.name),
            "msg": record.getMessage(),
        }
        req_id = _request_id_short()
        if req_id:
            log_entry["rid"] = req_id
        if record.exc_info and record.exc_info[1]:
            log_entry["exc"] = str(record.exc_info[1])

        return json.dumps(log_entry, ensure_ascii=False)


def setup_logging(level: str = "INFO", fmt: str = "console") -> None:
    """配置根 logger。

    在应用启动时调用一次，所有模块通过 logging.getLogger(__name__)
    获取的 logger 均会使用此配置。

    Args:
        level: 日志级别 ("DEBUG" / "INFO" / "WARNING" / "ERROR")
        fmt: 输出格式 ("console" / "json")
    """
    log_level = getattr(logging, level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()

    if fmt == "json":
        formatter: logging.Formatter = JsonFormatter()
    else:
        formatter = ConsoleFormatter()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    # ---- 抑制第三方库日志噪音 ----
    for noisy in ["uvicorn", "uvicorn.error"]:
        lg = logging.getLogger(noisy)
        lg.handlers.clear()
        lg.addHandler(handler)
        lg.propagate = False
        lg.setLevel(logging.WARNING)  # 只显示警告和错误

    # uvicorn.access 完全关闭（请求日志由我们自己的中间件处理）
    logging.getLogger("uvicorn.access").handlers.clear()
    logging.getLogger("uvicorn.access").addHandler(logging.NullHandler())
    logging.getLogger("uvicorn.access").propagate = False

    # HTTP 客户端库降噪
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """获取具名 logger。便捷封装，避免各模块直接调用 logging.getLogger。"""
    return logging.getLogger(name)
