"""SkillRegistry — 技能注册与发现。"""

from __future__ import annotations

from app.core.errors import AppError
from app.skills.protocol import Skill


class SkillRegistry:
    """Skill 注册表。"""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        """注册技能。重名时抛出 409。"""
        if skill.metadata.name in self._skills:
            raise AppError(
                detail=f"技能已注册: {skill.metadata.name}",
                status_code=409,
                code="SKILL_ALREADY_REGISTERED",
            )
        self._skills[skill.metadata.name] = skill

    def get(self, name: str) -> Skill:
        """按名称获取技能。"""
        if name not in self._skills:
            raise AppError(
                detail=f"技能未注册: {name}",
                status_code=404,
                code="SKILL_NOT_FOUND",
            )
        return self._skills[name]

    def list_all(self) -> list[Skill]:
        """列出所有已注册技能。"""
        return list(self._skills.values())
