"""DramaAgent API 依赖注入。

提供 FastAPI Depends() 可用的公共依赖：
- get_settings：获取应用配置
- get_request_id：获取当前请求 ID
- get_db：获取数据库异步会话
- get_agent_command_service：惰性构建进程级 Agent 命令服务（J-04）
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.core.config import Settings
from app.db.session import get_db as get_db

__all__ = ["get_settings", "get_request_id", "get_db", "get_agent_command_service"]


def get_settings(request: Request) -> Settings:
    """从 app.state 获取当前应用配置。

    配置实例在 create_app() 时挂载到 app.state.settings。

    Usage:
        @router.get("/example")
        async def example(settings: Settings = Depends(get_settings)): ...
    """
    return request.app.state.settings  # type: ignore[no-any-return]


def get_request_id(request: Request) -> str:
    """获取当前请求的唯一 ID。

    由 RequestIDMiddleware 分配并注入到响应头 X-Request-ID。
    """
    from app.core.errors import _request_id_ctx

    rid = _request_id_ctx.get()
    return rid if rid else "unknown"


# AgentCommandService 进程级单例(J-04);测试通过 dependency_overrides 覆盖。
_agent_command_service: Any = None


def get_agent_command_service(request: Request) -> Any:
    """惰性构建进程级 AgentCommandService。

    test 环境 Planner 使用 FakeLLM 并注册默认 create_script plan fixture
    (沿用 conversations.py / WorkflowDispatcher 的环境分支模式)。
    """
    global _agent_command_service
    if _agent_command_service is None:
        from app.agents.base import BaseAgent
        from app.application.agent_command_service import AgentCommandService

        settings: Settings = request.app.state.settings
        llm: Any
        if settings.app_env == "test":
            from app.domain.agent_planner import (
                AgentPlannerOutput,
                PlannerStep,
                PlannerTarget,
            )
            from app.llm.fake import FakeLLM

            llm = FakeLLM(seed=42)
            llm.register(
                "agent_command_planner",
                AgentPlannerOutput(
                    turn_type="plan",
                    intent="create_script",
                    target=PlannerTarget(target_type="project"),
                    steps=[PlannerStep(title="整理需求", description="确认项目创作范围")],
                    expected_impact=["生成新的创作计划"],
                ),
            )
        else:
            from app.llm.openai_compatible import OpenAICompatibleLLM

            llm = OpenAICompatibleLLM(settings)
        _agent_command_service = AgentCommandService(
            settings=settings,
            planner_agent=BaseAgent(name="planner", llm=llm),
        )
    return _agent_command_service
