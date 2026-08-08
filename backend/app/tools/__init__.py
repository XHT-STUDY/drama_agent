"""DramaAgent 工具系统。

Tool 是确定性能力函数（统计、解析、Diff 等），不隐式调用 LLM。
"""

from app.tools.dialogue_ratio import DialogueRatioTool, compute_dialogue_ratio, count_dialogue_chars
from app.tools.protocol import Tool, ToolMetadata
from app.tools.registry import ToolRegistry
from app.tools.script_structure import ScriptStructureTool, compute_script_features
from app.tools.word_count import WordCountTool, count_chinese_chars, count_total_chars

__all__ = [
    "Tool",
    "ToolMetadata",
    "ToolRegistry",
    "WordCountTool",
    "count_chinese_chars",
    "count_total_chars",
    "DialogueRatioTool",
    "compute_dialogue_ratio",
    "count_dialogue_chars",
    "ScriptStructureTool",
    "compute_script_features",
]
