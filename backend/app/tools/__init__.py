"""DramaAgent 工具系统。

Tool 是确定性能力函数（统计、解析、Diff 等），不隐式调用 LLM。
"""

from app.tools.protocol import Tool, ToolMetadata
from app.tools.registry import ToolRegistry

__all__ = ["Tool", "ToolMetadata", "ToolRegistry"]
