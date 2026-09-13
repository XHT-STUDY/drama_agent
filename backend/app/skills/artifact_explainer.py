"""ArtifactExplainerSkill — 有原文依据的作品内容解释（W1-03）。

输入是服务端组装的"已读原文"（剧本场景/设定/大纲片段，带来源编号），
输出是回答 + 引文（source_index + 逐字原文）。模型不输出任何
Artifact ID——服务端验证引文并回填完整引用结构。

诚实性契约：
- 只依据给出的原文回答，原文没有的就说无法确认；
- 每条关键论断都要有引文，引文必须是逐字原文；
- 引文经 tools/text_evidence 归一化匹配验证（与评估共用同一算法），
  无法定位的引文被剔除并计数；
- 没有任何有效引文的回答由服务端降级为"原文不足以确认"的有限答复。
"""

from __future__ import annotations

from typing import Any, cast

from app.agents.base import BaseAgent
from app.core.errors import AppError
from app.domain.agent_planner import (
    ArtifactExplanationInput,
    ArtifactExplanationOutput,
    ExplanationCitationOutput,
)
from app.prompts.loader import PromptLoader
from app.skills.protocol import Skill, SkillMetadata
from app.tools.text_evidence import normalize_text, verify_quote


class ArtifactExplanationError(AppError):
    """解释 Skill 输出不符合契约。"""

    status_code = 422
    code = "EXPLANATION_INVALID"


class VerifiedExplanation:
    """验证后的解释结果（服务端回填前的中间结构）。"""

    def __init__(
        self,
        answer: str,
        citations: list[dict[str, Any]],
        dropped_citations: int,
    ) -> None:
        self.answer = answer
        # 每条：source_index/scene_number/quote（已验证存在于原文）
        self.citations = citations
        self.dropped_citations = dropped_citations


class ArtifactExplainerSkill(Skill):
    """基于确切原文的作品内容解释。"""

    metadata = SkillMetadata(
        name="artifact_explainer",
        version="1.0",
        description="读取指定稿件/场景原文，回答内容性问题并逐条引用原文",
    )

    async def execute(self, context: dict[str, Any]) -> VerifiedExplanation:
        exp_input: ArtifactExplanationInput = context["input"]
        agent: BaseAgent = context["agent"]
        prompt_loader: PromptLoader = context["prompt_loader"]

        template = prompt_loader.get("artifact_explainer")
        rendered = template.render(
            question=exp_input.question,
            target_label=exp_input.target_label,
            sources=_render_sources(exp_input),
        )
        result = await agent.generate_structured(
            ArtifactExplanationOutput,
            [{"role": "user", "content": rendered}],
            prompt_name="artifact_explainer",
            temperature=0.2,
            max_tokens=4096,
        )
        if result.error_code or result.parsed is None:
            raise ArtifactExplanationError(
                detail=f"解释模型调用失败: {result.error_code or 'INVALID_OUTPUT'}"
            )
        output = cast(ArtifactExplanationOutput, result.parsed)
        return self.verify_citations(output, exp_input)

    @staticmethod
    def verify_citations(
        output: ArtifactExplanationOutput, exp_input: ArtifactExplanationInput
    ) -> VerifiedExplanation:
        """逐条验证引文：quote 必须出现在 source_index 对应来源的原文中。

        剧本场景来源按"整集场景文本"验证（模型引用可能标错场次——
        命中其他场次时纠正场次号，与评估 evidence 溯源同一策略）。
        """
        sources = {s.source_index: s for s in exp_input.sources}
        # 场景文本池：剧本类来源本身是场景粒度（scene_number → 该场原文），
        # 供引文的"指定场未命中→跨场纠正"策略使用（与评估溯源一致）
        scene_pool: dict[int, str] = {
            s.scene_number: s.text
            for s in exp_input.sources
            if s.kind == "script_scene" and s.scene_number is not None
        }
        verified: list[dict[str, Any]] = []
        dropped = 0
        seen_quotes: set[str] = set()
        for cite in output.citations:
            source = sources.get(cite.source_index)
            if source is None:
                dropped += 1
                continue
            norm_quote = normalize_text(cite.quote)
            if not norm_quote or norm_quote in seen_quotes:
                dropped += 1
                continue
            if source.kind == "script_scene":
                ok, corrected = verify_quote(cite.quote, cite.scene_number, scene_pool)
                if not ok:
                    dropped += 1
                    continue
                scene_number = (
                    corrected if corrected is not None
                    else (cite.scene_number or source.scene_number)
                )
            else:
                if norm_quote not in normalize_text(source.text):
                    dropped += 1
                    continue
                scene_number = None
            seen_quotes.add(norm_quote)
            verified.append(
                {
                    "source_index": cite.source_index,
                    "scene_number": scene_number,
                    "quote": cite.quote[:200],
                }
            )
        return VerifiedExplanation(
            answer=output.answer, citations=verified, dropped_citations=dropped
        )


def _render_sources(exp_input: ArtifactExplanationInput) -> str:
    blocks = []
    for source in exp_input.sources:
        scene_hint = (
            f"（第 {source.scene_number} 场）" if source.scene_number is not None else ""
        )
        blocks.append(
            f"[来源 {source.source_index}] {source.label}{scene_hint}\n{source.text}"
        )
    return "\n\n".join(blocks)


__all__ = [
    "ArtifactExplainerSkill",
    "ArtifactExplanationError",
    "VerifiedExplanation",
    "ExplanationCitationOutput",
]
