"""B-07 BaseAgent 单元测试。"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.agents.base import BaseAgent
from app.llm.fake import FakeLLM


class _Output(BaseModel):
    text: str


@pytest.mark.asyncio
class TestBaseAgent:
    """BaseAgent 测试。"""

    async def test_generate_structured(self) -> None:
        """Agent 通过 FakeLLM 生成结构化输出。"""
        llm = FakeLLM()
        llm.register("test_prompt", _Output(text="hello"))
        agent = BaseAgent(name="writer", llm=llm)

        result = await agent.generate_structured(
            _Output,
            [{"role": "user", "content": "test_prompt"}],
        )
        assert result.parsed is not None
        assert result.parsed.text == "hello"

    async def test_agent_not_depend_on_provider(self) -> None:
        """Agent 不直接依赖具体 provider——通过注入的 LLMClient。"""
        custom_llm = FakeLLM()
        custom_llm.register("test", _Output(text="from_custom"))
        agent = BaseAgent(name="test", llm=custom_llm)

        result = await agent.generate_structured(
            _Output, [{"role": "user", "content": "test"}],
        )
        assert result.parsed.text == "from_custom"

    async def test_call_tool(self) -> None:
        """Agent 可调用已注册 Tool。"""
        from app.tools.protocol import Tool, ToolMetadata
        from app.tools.registry import ToolRegistry

        class AddTool(Tool):
            metadata = ToolMetadata(name="add", version="1.0")
            async def execute(self, **kwargs):
                return kwargs["a"] + kwargs["b"]

        registry = ToolRegistry()
        registry.register(AddTool())

        llm = FakeLLM()
        agent = BaseAgent(name="test", llm=llm, tools=registry)

        result = await agent.call_tool("add", a=1, b=2)
        assert result == 3

    async def test_call_skill(self) -> None:
        """Agent 可调用已注册 Skill。"""
        from app.skills.protocol import Skill, SkillMetadata
        from app.skills.registry import SkillRegistry

        class UpperSkill(Skill):
            metadata = SkillMetadata(name="upper", version="1.0")
            async def execute(self, context):
                return context.get("text", "").upper()

        registry = SkillRegistry()
        registry.register(UpperSkill())

        llm = FakeLLM()
        agent = BaseAgent(name="test", llm=llm, skills=registry)

        result = await agent.call_skill("upper", {"text": "hello"})
        assert result == "HELLO"
