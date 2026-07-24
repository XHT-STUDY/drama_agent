"""B-07 Skill/SkillRegistry 单元测试。"""

from __future__ import annotations

import pytest

from app.core.errors import AppError
from app.skills.protocol import Skill, SkillMetadata
from app.skills.registry import SkillRegistry


class EchoSkill(Skill):
    """测试用 — 原样返回 context。"""
    metadata = SkillMetadata(name="echo", version="1.0", description="Echo skill")

    async def execute(self, context):
        return context


class TestSkillRegistry:
    """SkillRegistry 功能测试。"""

    def test_register_and_get(self) -> None:
        """注册后可按名获取。"""
        registry = SkillRegistry()
        skill = EchoSkill()
        registry.register(skill)
        assert registry.get("echo") is skill

    def test_get_nonexistent_raises(self) -> None:
        """查询未注册名抛出错误。"""
        registry = SkillRegistry()
        with pytest.raises(AppError) as exc:
            registry.get("nonexistent")
        assert exc.value.code == "SKILL_NOT_FOUND"

    def test_duplicate_register_raises(self) -> None:
        """重复注册抛出错误。"""
        registry = SkillRegistry()
        registry.register(EchoSkill())
        with pytest.raises(AppError) as exc:
            registry.register(EchoSkill())
        assert exc.value.code == "SKILL_ALREADY_REGISTERED"

    def test_metadata_serializable(self) -> None:
        """元数据可序列化。"""
        meta = SkillMetadata(name="story_bible_writer", version="1.2")
        d = meta.model_dump()
        assert d["name"] == "story_bible_writer"
