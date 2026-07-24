"""DramaAgent 技能系统。

Skill 是单一可复用任务单元，组装上下文与输出 Schema。
"""

from app.skills.protocol import Skill, SkillMetadata
from app.skills.registry import SkillRegistry

__all__ = ["Skill", "SkillMetadata", "SkillRegistry"]
