"""DramaAgent Agent 系统。

BaseAgent 提供统一追踪、模型调用和 Schema 校验能力。
具体 Agent（Normalizer、Planner、Writer 等）继承 BaseAgent。
"""

from app.agents.base import BaseAgent

__all__ = ["BaseAgent"]
