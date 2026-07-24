"""Skill 协议 — 可复用任务单元抽象。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class SkillMetadata(BaseModel):
    """Skill 元数据——可序列化。"""

    model_config = {"extra": "forbid"}

    name: str = Field(..., description="技能唯一名称，如 'story_bible_writer'")
    version: str = Field(default="1.0", description="技能版本号")
    description: str = Field(default="", description="技能功能描述")


class Skill(ABC):
    """可复用技能抽象。

    每个 Skill 完成单一业务任务（如写 StoryBible），
    内部可调用 LLM 和 Tool，但不直接操作 HTTP 或前端。
    """

    metadata: SkillMetadata

    @abstractmethod
    async def execute(self, context: dict[str, Any]) -> Any:
        """执行技能逻辑。

        Args:
            context: 上下文 dict（含 project_id, db, llm 等）

        Returns:
            执行结果（通常是 Pydantic 模型实例）
        """
        ...
