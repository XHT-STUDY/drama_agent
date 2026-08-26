"""日志 span 关联字段单元测试。

验证 Formatter 从 tracing span 上下文读取关联标识：
- push_request / push_run / push_node 期间，Console 与 JSON 输出
  分别包含 rid / run / node；
- 无上下文时不输出对应字段（JSON 契约增量兼容，不出现空键）。
不依赖 DB / Redis / LLM。
"""

from __future__ import annotations

import json
import logging

from app.core.logging import ConsoleFormatter, JsonFormatter
from app.observability.tracing import push_node, push_request, push_run


def _record(msg: str = "测试消息") -> logging.LogRecord:
    return logging.LogRecord(
        name="test.span", level=logging.INFO, pathname=__file__, lineno=1,
        msg=msg, args=(), exc_info=None,
    )


class TestJsonFormatterSpanFields:
    def test_no_context_omits_fields(self) -> None:
        entry = json.loads(JsonFormatter().format(_record()))
        assert "rid" not in entry
        assert "run" not in entry
        assert "node" not in entry

    def test_run_and_node_fields_present(self) -> None:
        with push_run("01234567-89ab-cdef-0123-456789abcdef"), push_node("outline"):
            entry = json.loads(JsonFormatter().format(_record()))
        assert entry["run"] == "01234567"
        assert entry["node"] == "outline"
        assert "rid" not in entry

    def test_request_id_field_present(self) -> None:
        with push_request("fedcba98-7654-3210-fedc-ba9876543210"):
            entry = json.loads(JsonFormatter().format(_record()))
        assert entry["rid"] == "fedcba98"

    def test_context_reset_after_exit(self) -> None:
        with push_run("01234567-89ab-cdef-0123-456789abcdef"):
            pass
        entry = json.loads(JsonFormatter().format(_record()))
        assert "run" not in entry


class TestConsoleFormatterSpanFields:
    def test_no_context_plain_line(self) -> None:
        line = ConsoleFormatter().format(_record())
        assert "run=" not in line
        assert "[write_episodes]" not in line

    def test_run_and_node_in_line(self) -> None:
        with push_run("01234567-89ab-cdef-0123-456789abcdef"), push_node("write_episodes"):
            line = ConsoleFormatter().format(_record())
        assert "run=01234567" in line
        assert "[write_episodes]" in line
