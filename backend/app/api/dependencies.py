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


class _AgentE2EPlannerStub:
    """FAKE_LLM_SCENARIO=agent_e2e 的内容感知 planner 桩（仅测试环境）。

    把用户请求路由为白名单意图；歧义（无集数/无上下文的修改请求）
    由 AgentCommandPlannerSkill 的确定性 preflight 在调用桩之前拦截。
    非 planner 的 prompt_name 全部委托内部 FakeLLM（沿用全局 fixtures）。
    """

    _EPISODE_RE: Any = None  # 惰性编译（避免模块导入期正则成本）

    def __init__(self, base: Any) -> None:
        self._base = base

    async def generate_structured(self, schema: Any, messages: list[dict[str, str]], **kwargs: Any) -> Any:
        prompt_name = str(kwargs.get("prompt_name", ""))
        if prompt_name != "agent_command_planner":
            return await self._base.generate_structured(schema, messages, **kwargs)
        import re

        from app.domain.agent_planner import (
            AgentPlannerOutput,
            PlannerStep,
            PlannerTarget,
        )

        if self._EPISODE_RE is None:
            type(self)._EPISODE_RE = re.compile(r"第\s*(\d+)\s*集")

        rendered = messages[-1]["content"] if messages else ""
        # 只认注入边界内的用户原文——渲染后的 Prompt 指令文本本身
        # 含"修改/评估"等词，直接全文匹配会误路由。模板中 user_request
        # 是最后一个 user_content_vars 变量，取最后一个边界段即用户本轮请求
        # （前面两段是 active_context / project_context，可能含历史消息）。
        begin = rendered.rfind("\u3010\u7528\u6237\u5185\u5bb9\u5f00\u59cb\u3011")
        end = rendered.find("\u3010\u7528\u6237\u5185\u5bb9\u7ed3\u675f\u3011", begin)
        request = (
            rendered[begin + len("\u3010\u7528\u6237\u5185\u5bb9\u5f00\u59cb\u3011"):end]
            if 0 <= begin < end
            else ""
        )
        match = self._EPISODE_RE.search(request)
        episode = int(match.group(1)) if match else None
        revision_like = bool(re.search(r"修改|修订|改写|调整|润色", request))
        outline_like = bool(re.search(r"大纲", request))
        evaluate_like = bool(re.search(r"评估|评分|打分", request)) and not revision_like
        explain_like = bool(re.search(r"解释|讲讲|说说|介绍", request)) and not (
            revision_like or evaluate_like
        )

        if revision_like and outline_like:
            output = AgentPlannerOutput(
                turn_type="plan",
                intent="revise_outline",
                target=PlannerTarget(target_type="outline"),
                steps=[PlannerStep(title="修订大纲", description="输出完整新大纲并分析影响")],
                expected_impact=["大纲产生新版本，受影响剧本收到后续建议"],
            )
        elif revision_like:
            target_ep = episode or 1
            output = AgentPlannerOutput(
                turn_type="plan",
                intent="revise_script",
                target=PlannerTarget(target_type="script", episode_number=target_ep),
                steps=[PlannerStep(title=f"修订第 {target_ep} 集", description="生成候选新稿并重评")],
                expected_impact=[f"第 {target_ep} 集产生新版本"],
            )
        elif evaluate_like:
            output = AgentPlannerOutput(
                turn_type="plan",
                intent="evaluate",
                target=PlannerTarget(
                    target_type="evaluation", episode_number=episode
                ),
                steps=[PlannerStep(title="评估", description="逐集评分并产出报告")],
                expected_impact=["产出评估报告"],
            )
        elif explain_like:
            # explain 归一化为只读答复（服务端不创建 Action/Run）
            output = AgentPlannerOutput(
                turn_type="plan",
                intent="explain",
                target=PlannerTarget(target_type="project"),
                steps=[PlannerStep(title="解释项目", description="只读答复当前项目状态")],
            )
        else:
            output = AgentPlannerOutput(
                turn_type="plan",
                intent="create_script",
                target=PlannerTarget(target_type="project"),
                steps=[PlannerStep(title="整理需求", description="确认项目创作范围")],
                expected_impact=["生成新的创作计划"],
            )
        # 直接构造成功结果，绕过 FakeLLM registry（内容路由而非固定返回）
        from app.llm.models import LLMCallResult, LLMUsage

        content = output.model_dump_json()
        return LLMCallResult(
            content=content,
            parsed=output,
            usage=LLMUsage(
                prompt_tokens=len(content) // 2,
                completion_tokens=len(content) // 2,
                total_tokens=len(content),
            ),
            model="fake-agent-e2e-planner",
            duration_ms=1,
            attempt=1,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


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
            import os as _os

            from app.domain.agent_planner import (
                AgentPlannerOutput,
                PlannerStep,
                PlannerTarget,
            )
            from app.llm.fake import FakeLLM

            llm = FakeLLM(seed=42)
            if _os.environ.get("FAKE_LLM_SCENARIO") == "agent_e2e":
                # E2E 确定性 planner 桩（J-12）：按用户请求内容路由意图，
                # 与服务端 preflight 澄清叠加（歧义请求在桩之前就被拦截）。
                llm = _AgentE2EPlannerStub(base=FakeLLM(seed=42))
            else:
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
