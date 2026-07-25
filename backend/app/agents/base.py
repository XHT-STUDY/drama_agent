"""BaseAgent — 通用 Agent 基类。

职责（DEV_PLAN §2.2）：
- 统一追踪（记录调用到 llm_calls 表）
- 模型调用（通过注入的 LLMClient）
- Schema 校验（通过 StructuredOutputParser）
- 重试与错误处理

不负责：
- 自由决定主工作流（由 Orchestrator 决定）
- 具体业务逻辑（在 Skill 中实现）
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.llm.models import LLMCallResult
from app.llm.protocol import LLMClient
from app.llm.structured_output import StructuredOutputParser
from app.skills.registry import SkillRegistry
from app.tools.registry import ToolRegistry


class BaseAgent:
    """通用 Agent 基类。

    通过组合注入 LLMClient、ToolRegistry 和 SkillRegistry，
    不直接依赖具体 provider。

    Usage:
        agent = BaseAgent(name="writer", llm=fake_llm)
        result = await agent.generate_structured(ScriptDraft, messages)
    """

    def __init__(
        self,
        name: str,
        llm: LLMClient,
        *,
        tools: ToolRegistry | None = None,
        skills: SkillRegistry | None = None,
    ) -> None:
        self.name = name
        self.llm = llm
        self.tools = tools or ToolRegistry()
        self.skills = skills or SkillRegistry()
        self.parser = StructuredOutputParser(llm)
        self._call_count = 0

    async def generate_structured(
        self,
        schema: type[BaseModel],
        messages: list[dict[str, str]],
        *,
        model: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        timeout_seconds: int = 180,
        **kwargs: Any,
    ) -> LLMCallResult:
        """调用 LLM 并返回经 Schema 校验的结构化结果。

        通过 StructuredOutputParser 自动处理重试。
        所有调用记录可通过 get_call_history() 查询。
        """
        self._call_count += 1
        return await self.parser.parse(
            schema,
            messages,
            model=model,  # 空字符串时由 LLMClient 自行决定模型
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            agent_name=self.name,
            call_index=self._call_count,
            **kwargs,
        )

    async def call_tool(self, name: str, **kwargs: Any) -> Any:
        """调用已注册的 Tool。"""
        tool = self.tools.get(name)
        return await tool.execute(**kwargs)

    async def call_skill(self, name: str, context: dict[str, Any]) -> Any:
        """调用已注册的 Skill。"""
        skill = self.skills.get(name)
        return await skill.execute(context)

    def get_call_history(self) -> list[LLMCallResult]:
        """获取 LLM 调用历史。"""
        return self.llm.get_call_history()

    def _default_model(self) -> str:
        """根据 agent 名称返回默认模型。"""
        return f"drama-{self.name}"

    def reset(self) -> None:
        """重置调用计数。"""
        self._call_count = 0
