"""EvaluationSkill — 单集剧本评估技能 (E-02; v2 可解释性升级).

职责:
- 接收单集剧本、本集大纲、StoryBible、Rubric 与客观辅助特征
- 调用 LLM 生成 EvaluationReport（9 维评分 + 评估明细 + 问题诊断 + 建议）
- 评估明细（dimension_assessments）：先定位 5 档锚点档位，再给档内分数，
  附原文证据引用——"评分矩阵"的数据层
- 服务端回填 overall_score / need_revision（不信任 LLM 自报总分）
- 后校验:分数-档位分带 clamp、matched_anchor 按 Rubric 回填、
  evidence 逐条溯源校验、低于 70 的维度必有对应 issue、evidence 限长、
  scene_number 有效
- 不注入其他集的评估结论

模块边界:
- Skill 只负责组装 Prompt、调用 LLM、工具计算、后校验
- 不直接访问 ORM、不操作前端
"""

from __future__ import annotations

import json as _json
import logging
import re
from typing import Any, cast
from uuid import UUID

from app.agents.base import BaseAgent
from app.domain.enums import EvaluationDimension
from app.domain.evaluation import (
    EvaluationInput,
    EvaluationIssue,
    EvaluationReport,
    clamp_score_to_band,
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
# 归一化时移除的字符：所有空白 + 中英文标点（引用漂移最常见的差异来源）
_NORMALIZE_STRIP_RE = re.compile(r"[\s，。！？；：、“”‘’（）《》〈〉【】—…·,.\!?;:\"'()\[\]<>{}]")


def _normalize_text(text: str) -> str:
    """归一化文本用于引用匹配：去空白与标点、转小写。

    LLM 摘抄常伴随标点改写或换行差异，归一化后做子串匹配
    可以容忍这类无关差异，只惩罚实质性的内容改写。
    """
    return _NORMALIZE_STRIP_RE.sub("", text).lower()


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

        # 1. 加载 Rubric（权威配置 knowledge/rubric/mvp_v2.yaml）
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

        # 5. 评估明细归一化（分带 clamp / 锚点回填 / 引用溯源）——
        #    必须先于总分计算，clamp 后的维度分才是总分输入
        self._normalize_assessments(report, rubric, ev_input)

        # 6. 服务端回填确定性指标（覆盖 LLM 自报）
        self._service_override(report, rubric)

        # 7. 后校验与规范化
        self._normalize_issues(report, ev_input)

        # 8. 绑定 Artifact 与 Rubric 版本
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

    # ---- 评估明细归一化（可解释性 v2）----

    def _normalize_assessments(
        self,
        report: EvaluationReport,
        rubric: Rubric,
        ev_input: EvaluationInput,
    ) -> None:
        """归一化评估明细，满足评分矩阵质量门禁。

        - 分带 clamp：维度分必须落在所定位档位的分带内（以档位为准）；
        - matched_anchor 权威回填：以 Rubric 原文为准，不采信模型转述；
        - evidence 溯源校验：归一化匹配所引场次原文，跨场自动纠正，
          全文找不到时标记 verified=False（不阻断工作流）。

        空 dimension_assessments（旧版 LLM 输出 / 旧 fixture）直接跳过，
        保持向后兼容。
        """
        if not report.dimension_assessments:
            return

        scene_texts = self._scene_texts(ev_input)
        full_text = _normalize_text("".join(scene_texts.values()))

        for dim, assessment in report.dimension_assessments.items():
            spec = rubric.dimension_spec(dim)

            # 1. 分带 clamp：分数以档位为准，越界拉回分带内
            lo, hi = rubric.score_band(assessment.level)
            original = report.dimension_scores.get(dim)
            clamped = clamp_score_to_band(int(original or 0), assessment.level, (lo, hi))
            if original is not None and clamped != original:
                logger.warning(
                    "第 %d 集 %s 维度分数 %d 超出 %d 档分带 [%d, %d]，clamp 至 %d",
                    report.episode_number, dim.value, original,
                    assessment.level, lo, hi, clamped,
                )
                report.dimension_scores[dim] = clamped

            # 2. matched_anchor 权威回填（模型转述不采信）
            assessment.matched_anchor = spec.anchors[assessment.level]

            # 3. evidence 溯源校验
            self._verify_evidence(report, dim, assessment, scene_texts, full_text)

    @staticmethod
    def _scene_texts(ev_input: EvaluationInput) -> dict[int, str]:
        """按场次汇总可检索文本（动作描写 + 对白）。"""
        texts: dict[int, str] = {}
        for scene in ev_input.script_draft.scenes:
            parts = [scene.action or ""]
            for line in scene.dialogue or []:
                parts.append(line.text or "")
            texts[scene.scene_number] = "".join(parts)
        return texts

    def _verify_evidence(
        self,
        report: EvaluationReport,
        dim: EvaluationDimension,
        assessment: Any,
        scene_texts: dict[int, str],
        full_text: str,
    ) -> None:
        """对单维度评估明细的 evidence 逐条溯源校验（原地修改）。

        策略（软校验，不阻断）：
        - 场次有效且引用能在该场匹配 → verified=True；
        - 所引场次匹配失败但能在其他场找到 → 自动纠正场次号并记录日志；
        - 全文都无法匹配 → verified=False（前端呈现"未验证引用"）。
        """
        for cite in assessment.evidence:
            if len(cite.quote) > _EVIDENCE_MAX_LENGTH:
                cite.quote = cite.quote[:_EVIDENCE_MAX_LENGTH]
            norm_quote = _normalize_text(cite.quote)
            if not norm_quote:
                cite.verified = False
                continue

            if cite.scene_number is not None:
                scene_text = _normalize_text(scene_texts.get(cite.scene_number, ""))
                if norm_quote in scene_text:
                    cite.verified = True
                    continue
                # 跨场漂移：全文检索，命中则纠正场次号
                corrected = self._find_scene(norm_quote, scene_texts)
                if corrected is not None:
                    logger.warning(
                        "第 %d 集 %s 维度 evidence 场次漂移: %s → %s，已纠正",
                        report.episode_number, dim.value,
                        cite.scene_number, corrected,
                    )
                    cite.scene_number = corrected
                    cite.verified = True
                    continue
            else:
                # 全集性证据：与全文匹配即可
                if norm_quote in full_text:
                    cite.verified = True
                    continue

            cite.verified = False
            logger.warning(
                "第 %d 集 %s 维度 evidence 无法在剧本原文中溯源，标记未验证",
                report.episode_number, dim.value,
            )

    @staticmethod
    def _find_scene(
        norm_quote: str, scene_texts: dict[int, str]
    ) -> int | None:
        """在全部场次中检索引用，返回命中的场次号。"""
        for scene_number, text in scene_texts.items():
            if norm_quote in _normalize_text(text):
                return scene_number
        return None

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
