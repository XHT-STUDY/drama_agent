"""L-1 补漏：normalize 节点从 config 透传目标集数（用户实测反馈修复）。

验证链：config.outline_count → RequirementInput.target_episode_count →
需求 Prompt 渲染"固定为 N 集"。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, cast

import pytest
from langchain_core.runnables import RunnableConfig

from app.agents.base import BaseAgent
from app.domain.requirement import NormalizedRequirement
from app.llm.fake import FakeLLM
from app.prompts.loader import PromptLoader
from app.workflows.state import CreationState

GOLDEN = Path(__file__).resolve().parents[2] / "golden"


@pytest.mark.unit
@pytest.mark.asyncio
class TestNormalizeEpisodeCount:
    async def _run_with_count(
        self, test_project: uuid.UUID, test_engine: Any, outline_count: int | None
    ) -> str:
        """跑 normalize 节点（DB 持久化走测试会话），返回渲染后的 Prompt 文本。"""
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from app.application.artifact_service import ArtifactService
        from app.events.publisher import EventPublisher

        llm = FakeLLM(seed=42)
        raw = cast(dict[str, Any], json.loads(
            (GOLDEN / "requirement_football.json").read_text(encoding="utf-8")))
        golden = NormalizedRequirement.model_validate(
            raw.get("expected_output", raw))
        llm.register("normalize_requirement", golden)

        captured: dict[str, str] = {}
        original = llm.generate_structured

        async def spy(schema: Any, messages: list[dict[str, str]], **kw: Any) -> Any:
            # 只捕获需求归一化调用（后续 story_bible 等调用不覆盖）
            if kw.get("prompt_name") == "normalize_requirement":
                captured["prompt"] = messages[-1]["content"]
            return await original(schema, messages, **kw)

        llm.generate_structured = spy  # type: ignore[method-assign]

        configurable: dict[str, Any] = {
            "agent": BaseAgent(name="planner", llm=llm),
            "prompt_loader": PromptLoader(),
            "artifact_service": ArtifactService(),
            "event_publisher": EventPublisher(),
            "user_input": "一个被青训队抛弃的足球少年，靠隐藏天赋逆袭进入职业赛场的热血短剧。",
            "source_type": "idea",
            "progress_callback": lambda *a: None,
            "progress_log": [],
        }
        if outline_count is not None:
            configurable["outline_count"] = outline_count

        from datetime import UTC, datetime, timedelta

        from app.application.run_service import RunService
        from app.db.models.workflow_run import WorkflowRun
        from app.workflows.creation import build_creation_workflow

        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            configurable["db"] = db
            # 事件表 FK 指向 workflow_runs——先种 Run 行
            run = await RunService().create_run(
                db=db, project_id=test_project, action="create_script",
                config={"options": {"user_input": configurable["user_input"]}},
            )
            from sqlalchemy import update
            await db.execute(update(WorkflowRun).where(WorkflowRun.id == run.id).values(
                status="running", lease_owner="probe",
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=5)))
            await db.commit()
            state = CreationState(
                run_id=str(run.id), project_id=str(test_project),
                action="create_script", status="running",
                completed_nodes=[], input_hashes={}, prompt_versions={},
            )
            # LangGraph 节点经 get_config() 读配置——必须经图调用（同 workflow 测试模式）
            workflow = build_creation_workflow()
            # 只验证 Prompt 渲染；后续节点（无 FakeLLM fixture）失败不关心
            import contextlib
            with contextlib.suppress(Exception):
                await workflow.ainvoke(state, cast(RunnableConfig, {"configurable": configurable}))

        return captured.get("prompt", "")

    async def test_user_count_flows_into_requirement_prompt(
        self, test_project: uuid.UUID, test_engine: Any
    ) -> None:
        """config.outline_count=3 → 需求 Prompt 渲染"固定为 3 集"。"""
        prompt = await self._run_with_count(test_project, test_engine, outline_count=3)
        assert "固定为 3 集" in prompt, prompt[:200]

    async def test_default_falls_back_to_10(
        self, test_project: uuid.UUID, test_engine: Any
    ) -> None:
        """未提供 outline_count → 保持默认 10（兼容旧行为）。"""
        prompt = await self._run_with_count(test_project, test_engine, outline_count=None)
        assert "固定为 10 集" in prompt, prompt[:200]
