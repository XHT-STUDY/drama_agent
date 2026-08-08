"""EvaluationSkill — 单集剧本评估技能 (E-02).

职责:
- 接收单集剧本、本集大纲、StoryBible、Rubric 与客观辅助特征
- 调用 LLM 生成 EvaluationReport（9 维评分 + 问题诊断 + 建议）
- 服务端回填 overall_score / need_revision（不信任 LLM 自报总分）
- 后校验:低于 70 的维度必有对应 issue、evidence 限长、scene_number 有效
- 不注入其他集的评估结论

模块边界:
- Skill 只负责组装 Prompt、调用 LLM、工具计算、后校验
- 不直接访问 ORM、不操作前端
"""

from __future__ import annotations

import json as _json
import logging
from typing import Any, cast
from uuid import UUID

from app.agents.base import BaseAgent
from app.domain.enums import EvaluationDimension
from app.domain.evaluation import (
    EvaluationInput,
    EvaluationIssue,
    EvaluationReport,
    compute_need_revision,
    compute_overall_score,
)
from app.domain.rubric import Rubric, load_rubric
from app.prompts.loader import PromptLoader
from app.skills.protocol import Skill, SkillMetadata
from app.tools.script_structure import ScriptStructureTool

logger = logging.getLogger(__name__)

# evidence 引用长度上限 (超过截断，避免上下文膨胀)
_EVIDENCE_MAX_LENGTH = 200
# 低分维度自动补 issue 的阈值
_LOW_DIMENSION_THRESHOLD = 70


class EvaluationSkillValidationError(Exception):
    """Evaluation Skill 后校验失败——报告结构不满足质量门禁。"""


class EvaluationSkill(Skill):
    """单集剧本评估 Skill。

    从剧本/大纲/StoryBible 生成 9 维 EvaluationReport，
    overall_score 与 need_revision 由服务端确定性规则回填。
    """

    metadata = SkillMetadata(
        name="evaluate_episode",
        version="1.0",
        description="对单集剧本进行九维度结构化评估，输出评分、诊断与建议",
    )

    def __init__(self) -> None:
        super().__init__()
        self._structure_tool = ScriptStructureTool()

    # ---- 公开 API ----

    async def execute(self, context: dict[str, Any]) -> EvaluationReport:
        """执行单集评估。

        context 必需键:
            input: EvaluationInput — 剧本/大纲/StoryBible
            agent: BaseAgent — 用于调用 LLM
            prompt_loader: PromptLoader — 用于加载 Prompt 模板
            script_artifact_id: UUID — 被评估的 Script Artifact ID

        Returns:
            服务端已回填 overall_score / need_revision 的 EvaluationReport

        Raises:
            RuntimeError: LLM 调用失败
            EvaluationSkillValidationError: 报告结构不满足门禁
        """
        ev_input: EvaluationInput = context["input"]
        agent: BaseAgent = context["agent"]
        prompt_loader: PromptLoader = context["prompt_loader"]
        script_artifact_id: UUID = context["script_artifact_id"]

        # 1. 加载 Rubric（权威配置 knowledge/rubric/mvp_v1.yaml）
        rubric = load_rubric()

        # 2. 计算客观辅助特征（未预传时才计算）
        features = ev_input.script_features or await self._compute_features(ev_input)

        # 3. 渲染 Prompt
        try:
            tpl = prompt_loader.get("evaluate_episode")
        except KeyError as e:
            logger.error("Prompt 加载失败: %s", e)
            raise

        rendered = tpl.render(
            episode_number=str(ev_input.episode_number),
            script_draft=_json.dumps(
                ev_input.script_draft.model_dump(mode="json"), ensure_ascii=False, indent=2
            ),
            episode_outline=_json.dumps(ev_input.episode_outline, ensure_ascii=False, indent=2),
            story_bible=_json.dumps(ev_input.story_bible, ensure_ascii=False, indent=2),
            rubric_anchors=rubric.anchors_text(),
            script_features=_json.dumps(features, ensure_ascii=False, indent=2),
        )

        # 4. 调用 LLM 生成结构化输出
        messages: list[dict[str, str]] = [
            {"role": "user", "content": rendered},
        ]
        result = await agent.generate_structured(
            EvaluationReport,
            messages,
            prompt_name="evaluate_episode",
            temperature=0.3,
        )

        if result.error_code or result.parsed is None:
            logger.error(
                "LLM 评估失败: code=%s detail=%s",
                result.error_code,
                result.error_detail,
            )
            raise RuntimeError(
                f"Evaluation Skill LLM 调用失败: {result.error_code} - {result.error_detail}"
            )

        report = cast(EvaluationReport, result.parsed)

        # 5. 服务端回填确定性指标（覆盖 LLM 自报）
        self._service_override(report, rubric)

        # 6. 后校验与规范化
        self._normalize_issues(report, ev_input)

        # 7. 绑定 Artifact 与 Rubric 版本
        report.script_artifact_id = script_artifact_id
        report.rubric_version = rubric.version

        return report

    # ---- 客观特征 ----

    async def _compute_features(self, ev_input: EvaluationInput) -> dict[str, Any]:
        """使用 ScriptStructureTool 计算客观结构特征。

        Args:
            ev_input: 评估输入

        Returns:
            客观特征字典
        """
        script = ev_input.script_draft.model_dump(mode="json")
        return await self._structure_tool.execute(script=script)

    # ---- 服务端回填 ----

    def _service_override(self, report: EvaluationReport, rubric: Rubric) -> None:
        """用确定性规则回填 overall_score 与 need_revision。

        Args:
            report: LLM 生成的报告（会被原地修改）
            rubric: 已加载的 Rubric（提供权重）
        """
        report.overall_score = compute_overall_score(report.dimension_scores, rubric.weights())
        report.need_revision = compute_need_revision(
            report.overall_score,
            report.issues,
            report.dimension_scores,
        )
        logger.info(
            "第 %d 集服务端回填: overall=%.1f need_revision=%s",
            report.episode_number,
            report.overall_score,
            report.need_revision,
        )

    # ---- 后校验与规范化 ----

    def _normalize_issues(self, report: EvaluationReport, ev_input: EvaluationInput) -> None:
        """规范化 issues，满足评估质量门禁。

        - 每个低于 70 的维度补一条 issue（LLM 可能漏报，不阻断）；
        - evidence 超长截断至 200 字；
        - scene_number 超出现有场景范围时降级为 null（软校验）。

        Args:
            report: 待规范化的报告（会被原地修改）
            ev_input: 评估输入（提供剧本场景范围）
        """
        self._ensure_issues_for_low_dimensions(report, ev_input)
        self._clamp_evidence(report)
        self._validate_scene_numbers(report, ev_input)

    def _ensure_issues_for_low_dimensions(
        self,
        report: EvaluationReport,
        ev_input: EvaluationInput,
    ) -> None:
        """为评分低于 70 且无对应 issue 的维度补充一条诊断。

        不阻断工作流——LLM 开放域输出可能漏报，自动补全保证
        "每个低分维度都有对应 issue" 的门禁恒成立。

        Args:
            report: 报告（原地修改 issues）
            ev_input: 评估输入（用于提取证据）
        """
        covered: set[EvaluationDimension] = {i.dimension for i in report.issues}
        for dim, score in report.dimension_scores.items():
            if score >= _LOW_DIMENSION_THRESHOLD or dim in covered:
                continue
            evidence = self._first_scene_action(ev_input)
            report.issues.append(
                EvaluationIssue(
                    issue_id=f"auto_low_{dim.value}",
                    dimension=dim,
                    severity="high" if score < 50 else "medium",
                    scene_number=None,
                    evidence=evidence or f"第 {report.episode_number} 集 {dim.value} 维度表现不足",
                    diagnosis=(
                        f"{dim.value} 维度得分 {score} 偏低，但评估中缺少对应的问题定位，"
                        "请人工复核并针对该维度进行专项强化"
                    ),
                    suggestion="针对该维度进行专项修订后重新评估",
                )
            )
            logger.info(
                "第 %d 集 %s 维度 (%d 分) 自动补充 issue",
                report.episode_number, dim.value, score,
            )

    def _first_scene_action(self, ev_input: EvaluationInput) -> str:
        """提取第一场的动作描写作为补全 issue 的 evidence。"""
        scenes = ev_input.script_draft.scenes
        if scenes:
            return scenes[0].action[:50]
        return ""

    def _clamp_evidence(self, report: EvaluationReport) -> None:
        """evidence 超过上限时截断，保持引用可控。"""
        for issue in report.issues:
            if len(issue.evidence) > _EVIDENCE_MAX_LENGTH:
                logger.warning(
                    "第 %d 集 issue %s 的 evidence 超长 (%d 字)，截断至 %d 字",
                    report.episode_number, issue.issue_id,
                    len(issue.evidence), _EVIDENCE_MAX_LENGTH,
                )
                issue.evidence = issue.evidence[:_EVIDENCE_MAX_LENGTH]

    def _validate_scene_numbers(
        self,
        report: EvaluationReport,
        ev_input: EvaluationInput,
    ) -> None:
        """scene_number 超出现有场景范围时降级为 null（软校验，不阻断）。"""
        max_scene = max(
            (s.scene_number for s in ev_input.script_draft.scenes), default=0
        )
        for issue in report.issues:
            if issue.scene_number is not None and issue.scene_number > max_scene:
                logger.warning(
                    "第 %d 集 issue %s 的 scene_number=%d 超出范围 (%d)，降级为 null",
                    report.episode_number, issue.issue_id, issue.scene_number, max_scene,
                )
                issue.scene_number = None
