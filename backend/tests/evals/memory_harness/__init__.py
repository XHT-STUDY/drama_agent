"""Memory 评测 harness(M-01)。

被 tests/evals/test_dialogue_memory_eval.py、test_story_memory_eval.py
与 scripts/evaluate_memory.py 共享。纯 Python + FakeLLM 语义,不依赖
Docker;对话层测量记忆策略(无记忆/仅近期/分段摘要/累计摘要),
剧情层测量连续性状态(无状态/仅上一集/标题摘要/结构化 typed delta)。

设计依据:docs/MEMORY_DESIGN.md §10、docs/MEMORY_IMPLEMENTATION_PLAN.md §3。
"""

from tests.evals.memory_harness.dialogue import (
    DialogueResult,
    load_dialogue_cases,
)
from tests.evals.memory_harness.dialogue import (
    evaluate_case as evaluate_dialogue_case,
)
from tests.evals.memory_harness.dialogue import (
    evaluate_dataset as evaluate_dialogue_dataset,
)
from tests.evals.memory_harness.story import (
    StoryResult,
    load_story_cases,
)
from tests.evals.memory_harness.story import (
    evaluate_case as evaluate_story_case,
)
from tests.evals.memory_harness.story import (
    evaluate_dataset as evaluate_story_dataset,
)

__all__ = [
    "DialogueResult",
    "StoryResult",
    "evaluate_dialogue_case",
    "evaluate_dialogue_dataset",
    "evaluate_story_case",
    "evaluate_story_dataset",
    "load_dialogue_cases",
    "load_story_cases",
]
