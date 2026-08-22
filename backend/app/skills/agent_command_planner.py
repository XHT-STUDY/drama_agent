"""受约束的对话命令 Planner Skill（J-03）。

Planner 只把自然语言请求归一化为可审计的意图、目标和约束，
不生成工具名、Artifact ID、SQL 或可直接执行的 Action。
真正的 AgentActionPlan 由服务端依据 intent/workflow 模板生成。
"""

from __future__ import annotations

import json
import re
from typing import Any, cast

from app.agents.base import BaseAgent
from app.core.errors import AppError
from app.domain.agent_planner import (
    AgentPlannerInput,
    AgentPlannerOutput,
)
from app.prompts.loader import PromptLoader
from app.skills.protocol import Skill, SkillMetadata

KNOWN_AGENT_INTENTS = frozenset(
    {"create_script", "explain", "revise_outline", "revise_script", "evaluate"}
)
DEFAULT_AVAILABLE_INTENTS = ("create_script", "explain", "evaluate")

_REVISION_RE = re.compile(r"(修改|修订|改写|重写|调整|润色|删掉|增加|替换)")
_CONTEXT_REFERENCE_RE = re.compile(r"(这里|此处|这个版本|当前稿|当前剧本|上面)")
_EPISODE_RE = re.compile(
    r"(?:第|ep(?:isode)?[\s_-]*)(\d+)(?:[集话回期])?", re.IGNORECASE
)
_CONFLICT_RE = re.compile(
    r"(?:既[^。！？!?]{0,80}又|同时[^。！？!?]{0,80}(?:保留|删除|改为)|"
    r"(?:保留|删除)[^。！？!?]{0,50}(?:又|同时))"
)
_FORBIDDEN_OUTPUT_RE = re.compile(
    r"(?:\bapi\b|\bsql\b|\btool\b|工具调用|调用接口|"
    r"http[s]?://|artifact[ _-]?id|\buuid\b|数据库查询)",
    re.IGNORECASE,
)
_UUID_RE = re.compile(
    r"(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}(?![0-9a-f])",
    re.IGNORECASE,
)


class InvalidPlannerOutputError(AppError):
    """Planner 输出不符合安全或业务契约。"""

    status_code = 422
    code = "INVALID_OUTPUT"


def requires_confirmation(intent: str) -> bool:
    """服务端确认策略：只有只读 explain 不需要确认。"""

    return intent != "explain"


def _clarification(question: str, unresolved_turn_count: int) -> AgentPlannerOutput:
    if unresolved_turn_count >= 3:
        question = (
            f"{question.rstrip('？?')}。请直接选择一个合法命令："
            "创建剧本；解释项目大纲；评估项目；评估第1集。"
        )
    return AgentPlannerOutput(
        turn_type="clarification",
        clarification_question=question,
    )


def _preflight_clarification(
    planner_input: AgentPlannerInput,
) -> AgentPlannerOutput | None:
    """在调用模型前处理确定性歧义，避免模型猜测目标。"""

    request = planner_input.user_request.strip()
    active = planner_input.active_context
    episode_match = _EPISODE_RE.search(request)
    if episode_match is not None:
        episode_number = int(episode_match.group(1))
        if episode_number > planner_input.target_episode_count:
            return _clarification(
                f"项目只有 {planner_input.target_episode_count} 集，"
                f"请确认你要操作第 {episode_number} 集，还是改为项目范围？",
                planner_input.unresolved_turn_count,
            )

    if _CONFLICT_RE.search(request):
        return _clarification(
            "你的修改要求包含互相冲突的约束；请明确要保留还是删除该内容？",
            planner_input.unresolved_turn_count,
        )

    revision_requested = _REVISION_RE.search(request) is not None
    if revision_requested and active is None:
        return _clarification(
            "你希望修改哪一个目标：大纲、剧本，还是指定集数？",
            planner_input.unresolved_turn_count,
        )

    if _CONTEXT_REFERENCE_RE.search(request) and active is None:
        return _clarification(
            "你提到“这里/当前稿”，但当前没有活动上下文；请先选择大纲、剧本或集数？",
            planner_input.unresolved_turn_count,
        )

    if revision_requested and not (
        {"revise_outline", "revise_script"} & set(planner_input.available_intents)
    ):
        return _clarification(
            "当前阶段尚未开放修订命令；请改用创建剧本、解释项目或评估项目？",
            planner_input.unresolved_turn_count,
        )
    return None


def _scan_strings(value: Any) -> None:
    if isinstance(value, str):
        if _FORBIDDEN_OUTPUT_RE.search(value) or _UUID_RE.search(value):
            raise InvalidPlannerOutputError(
                "Planner 输出包含工具、API、SQL 或 Artifact 标识，已拒绝"
            )
    elif isinstance(value, dict):
        for item in value.values():
            _scan_strings(item)
    elif isinstance(value, list):
        for item in value:
            _scan_strings(item)


def _validate_output(
    output: AgentPlannerOutput,
    planner_input: AgentPlannerInput,
) -> AgentPlannerOutput:
    """执行模型无法控制的服务器侧白名单与语义校验。"""

    _scan_strings(output.model_dump(mode="json"))
    available = set(planner_input.available_intents)
    if not available or not available.issubset(KNOWN_AGENT_INTENTS):
        raise InvalidPlannerOutputError("available_intents 不是服务端认可的意图白名单")

    if output.turn_type == "clarification":
        if not output.clarification_question:
            raise InvalidPlannerOutputError("clarification 必须包含一个问题")
        if output.intent or output.target or output.answer:
            raise InvalidPlannerOutputError(
                "clarification 不得同时携带 intent、target 或 answer"
            )
    elif output.turn_type == "plan":
        if output.intent not in available:
            raise InvalidPlannerOutputError(
                f"Planner 意图不在服务端白名单中: {output.intent}"
            )
        if output.target is None or not output.steps:
            raise InvalidPlannerOutputError("plan 必须包含目标和可读步骤")
        if output.clarification_question or output.answer:
            raise InvalidPlannerOutputError("plan 不得同时返回澄清问题或 answer")
    elif output.turn_type == "answer":
        if not output.answer:
            raise InvalidPlannerOutputError("answer 必须包含可读答复")
        if output.intent is not None and output.intent not in available:
            raise InvalidPlannerOutputError(
                f"answer 意图不在服务端白名单中: {output.intent}"
            )
        if output.clarification_question:
            raise InvalidPlannerOutputError("answer 不得同时返回澄清问题")
    return output


class AgentCommandPlannerSkill(Skill):
    """自然语言请求 → 受限 Planner 输出。"""

    metadata = SkillMetadata(
        name="agent_command_planner",
        version="1.0",
        description="把对话请求规划为白名单意图、目标、约束和可读步骤",
    )

    async def execute(self, context: dict[str, Any]) -> AgentPlannerOutput:
        raw_input = context["input"]
        planner_input = (
            raw_input
            if isinstance(raw_input, AgentPlannerInput)
            else AgentPlannerInput.model_validate(raw_input)
        )
        available = set(planner_input.available_intents)
        if not available or not available.issubset(KNOWN_AGENT_INTENTS):
            raise InvalidPlannerOutputError("available_intents 必须由服务端提供合法白名单")

        preflight = _preflight_clarification(planner_input)
        if preflight is not None:
            return preflight

        agent: BaseAgent = context["agent"]
        prompt_loader: PromptLoader = context["prompt_loader"]
        try:
            template = prompt_loader.get("agent_command_planner")
            rendered = template.render(
                user_request=planner_input.user_request,
                project_title=planner_input.project_title,
                target_episode_count=str(planner_input.target_episode_count),
                available_intents=json.dumps(
                    planner_input.available_intents, ensure_ascii=False
                ),
                active_context=json.dumps(
                    planner_input.active_context.model_dump(mode="json")
                    if planner_input.active_context
                    else None,
                    ensure_ascii=False,
                ),
                project_context=planner_input.project_context,
                unresolved_turn_count=str(planner_input.unresolved_turn_count),
            )
            result = await agent.generate_structured(
                AgentPlannerOutput,
                [{"role": "user", "content": rendered}],
                prompt_name="agent_command_planner",
                temperature=0.1,
                max_tokens=1600,
            )
        except InvalidPlannerOutputError:
            raise
        except Exception as exc:
            raise InvalidPlannerOutputError(f"Planner 调用失败: {exc}") from exc

        if result.error_code or result.parsed is None:
            raise InvalidPlannerOutputError(
                f"Planner 输出无效: {result.error_code or 'INVALID_OUTPUT'}"
            )
        output = cast(AgentPlannerOutput, result.parsed)
        return _validate_output(output, planner_input)


AgentCommandPlanner = AgentCommandPlannerSkill
