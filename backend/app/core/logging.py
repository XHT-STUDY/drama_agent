"""结构化日志配置。

双格式输出：
- Console: 彩色人类可读格式（local 环境默认）
- JSON:   一行 JSON 格式（production 环境，供 ELK/Loki 解析）

每条 JSON 日志包含以下字段：
- timestamp: 北京时间（Asia/Shanghai）
- level: 日志级别
- logger: logger 名称简写
- message: 日志消息
- rid: 当前请求 ID（截取前 8 位）
- exception: 异常信息（仅异常日志）

I-02 新增 RedactFilter 与 mask_secret：对日志消息做密钥/令牌脱敏
（sk-*、api_key、Bearer、Authorization）与超长截断，避免敏感信息
泄漏到 ELK/Loki 等日志采集系统。
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

# 北京时区 (UTC+8)
_CST = timezone(timedelta(hours=8))

# 单条日志消息最大长度：超过截断，避免全文泄漏到日志采集系统
_MAX_LOG_CONTENT = 2000
# 异常堆栈文本最大长度
_MAX_EXC_TEXT = 4000

# 需脱敏的密钥/令牌模式（I-02）。每项为 (正则, 替换串)，
# 正则首组捕获字段前缀（如 api_key= / bearer ），替换保留前缀、掩蔽值本身。
_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # OpenAI 风格 API Key：保留 sk- 前缀，掩蔽后续值
    (re.compile(r"(sk-)[A-Za-z0-9_\-]{6,}"), r"\1***"),
    # api_key / apikey 字段赋值
    (re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,;&\"']+"), r"\1***"),
    # Authorization: Bearer 授权头
    (re.compile(r"(?i)(authorization\s*[=:]\s*)bearer\s+[A-Za-z0-9._~+/=\-]+"), r"\1***"),
    # 裸 Bearer 令牌
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=\-]{6,}"), r"\1***"),
    # access_token / token 字段赋值
    (re.compile(r"(?i)(access[_-]?token\s*[=:]\s*)[^\s,;&\"']+"), r"\1***"),
]


def mask_secret(text: str, *, max_len: int | None = None) -> str:
    """掩蔽文本中的密钥/令牌，可选截断超长内容（I-02）。

    覆盖 sk-* API Key、api_key 字段、Bearer 令牌与 Authorization 头；
    保留字段名前缀（如 api_key=），只掩蔽值本身。非字符串输入原样返回。
    """
    if not isinstance(text, str) or not text:
        return text
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    if max_len is not None and len(text) > max_len:
        text = text[:max_len] + "…（已截断）"
    return text


def _exc_text(record: logging.LogRecord) -> str:
    """取异常文本并脱敏截断；无异常时返回空字符串。"""
    if record.exc_info and record.exc_info[1]:
        return mask_secret(str(record.exc_info[1]), max_len=_MAX_EXC_TEXT)
    return ""

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

        # 异常信息（脱敏后展示）
        if record.exc_info and record.exc_info[1]:
            parts.append(f"\n{_COLORS['ERROR']}{_exc_text(record)}{_RESET}")

        return " ".join(parts)


class RedactFilter(logging.Filter):
    """日志脱敏过滤器（I-02）。

    在格式化之前改写 record：先渲染消息再掩蔽密钥/令牌并截断超长内容。
    因在 handler 层生效，同一进程内所有日志（console 与 json）均自动脱敏。
    异常文本在 formatter 中经 _exc_text 统一处理。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
            if isinstance(rendered, str):
                record.msg = mask_secret(rendered, max_len=_MAX_LOG_CONTENT)
                # 已渲染并脱敏，避免 formatter 二次 % 格式化或重新取原始 args
                record.args = ()
        except Exception:
            pass  # 脱敏失败不得阻断日志输出
        return True


class JsonFormatter(logging.Formatter):
    """结构化 JSON 格式，适合生产环境 / 日志采集系统。

    输出契约：timestamp / level / logger / message [+ rid / exception]。
    该键名即 ELK/Loki 解析字段名，与 TestStructuredLogging 断言保持一致。
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": _now_cst(),
            "level": record.levelname,
            "logger": _logger_short(record.name),
            "message": record.getMessage(),
        }
        req_id = _request_id_short()
        if req_id:
            log_entry["rid"] = req_id
        exc_text = _exc_text(record)
        if exc_text:
            log_entry["exception"] = exc_text

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
    # I-02：所有经根 handler 的日志统一脱敏（掩 sk-*/api_key/Bearer + 超长截断）
    handler.addFilter(RedactFilter())
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
