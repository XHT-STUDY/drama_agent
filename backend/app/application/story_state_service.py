"""StoryStateService — 持久化剧情证据与 ContinuityState(M-03,W3-02)。

职责(Agent Native 阶段三 / MEMORY_IMPLEMENTATION_PLAN §5):
- 调用 Summarizer 生成单集 typed delta(episode_summary_v2 Prompt);
- 服务端校验引用(角色/事实/伏笔/道具存在性,未来计划隔离);
- 执行纯 reducer(ContinuityManager.apply_episode_delta);
- 在调用方事务内保存 EpisodeSummary + ContinuityState Artifact 与链接;
- 以 (project, type, episode, input_hash) 幂等——相同输入重试零模型调用,
  源稿/Prompt 版本/前态任一变化产生新派生。

边界:
- Skill 不访问 DB;本服务不修改正文 Artifact(派生失败保留正文,
  调用方标记 derivation_pending_episode,恢复只补派生);
- 模型调用前后不持有长事务(调用方保证);失败抛出可恢复错误。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent
from app.application.artifact_service import ArtifactService
from app.artifacts.versions import compute_input_hash
from app.core.errors import StoryStateGapError
from app.db.models.artifact import Artifact
from app.db.repositories.artifacts import ArtifactRepository
from app.domain.continuity import ContinuityState, StateBasis
from app.domain.story_bible import StoryBible
from app.domain.summary import EpisodeDelta, EpisodeSummaryV2
from app.memory.continuity import ContinuityManager
from app.prompts.loader import PromptLoader
from app.skills.summarizer import SummarizerSkill

logger = logging.getLogger(__name__)

_PROMPT_NAME = "episode_summary_v2"


@dataclass(frozen=True)
class StoryWorkset:
    """一次派生使用的确切作品工作集。"""

    project_id: uuid.UUID
    story_bible_artifact_id: uuid.UUID
    outline_artifact_id: uuid.UUID
    scripts: dict[int, uuid.UUID] = field(default_factory=dict)

    @classmethod
    def from_script_ids(
        cls,
        *,
        project_id: uuid.UUID,
        story_bible_artifact_id: uuid.UUID,
        outline_artifact_id: uuid.UUID,
        script_artifact_ids: dict[str, str],
        max_episode: int | None = None,
    ) -> StoryWorkset:
        """从 Run state 的 str 集号映射构建(workset 组装的唯一入口)。

        max_episode 限定纳入的集数上限(如修订前态只取 < N);
        非数字键跳过。
        """
        scripts: dict[int, uuid.UUID] = {}
        for key, artifact_id in script_artifact_ids.items():
            if not key.isdigit():
                continue
            episode = int(key)
            if max_episode is not None and episode > max_episode:
                continue
            scripts[episode] = uuid.UUID(artifact_id)
        return cls(
            project_id=project_id,
            story_bible_artifact_id=story_bible_artifact_id,
            outline_artifact_id=outline_artifact_id,
            scripts=scripts,
        )

    def source_refs(self) -> list[dict[str, str]]:
        """StoryBible/大纲引用(幂等输入的一部分)。"""
        return [
            {
                "artifact_id": str(self.story_bible_artifact_id),
                "version": "0",
                "relation": "references",
            },
            {
                "artifact_id": str(self.outline_artifact_id),
                "version": "0",
                "relation": "references",
            },
        ]


@dataclass
class DerivationOutcome:
    """ensure_state_through 的结果。"""

    state: ContinuityState
    state_artifact_id: str
    episode_summary_artifact_ids: dict[int, str] = field(default_factory=dict)
    model_calls: int = 0
    reused_episodes: list[int] = field(default_factory=list)


class StoryStateService:
    """剧情状态应用服务(M-03)。"""

    def __init__(
        self,
        agent: BaseAgent | None = None,
        prompt_loader: PromptLoader | None = None,
        artifact_service: ArtifactService | None = None,
    ) -> None:
        # agent 仅派生路径需要;resolve_status 等只读路径可不传
        # (GET story-state 零模型调用的构造前提)。
        self._agent = agent
        self._prompt_loader = prompt_loader or PromptLoader()
        self._artifact_service = artifact_service or ArtifactService()
        self._skill = SummarizerSkill()
        self._prompt_version = self._prompt_loader.get(_PROMPT_NAME).version

    def _require_agent(self) -> BaseAgent:
        if self._agent is None:
            raise RuntimeError("StoryStateService 派生路径需要 agent(只读路径不应到达)") 
        return self._agent

    # ---- 读取 ----

    async def load_latest_state(
        self,
        db: AsyncSession,
        workset: StoryWorkset,
    ) -> tuple[ContinuityState, Artifact] | None:
        """加载该工作集最新的有效 v2 状态链头(只读,不调用模型)。

        状态链以 basis 识别:script 引用完全一致的前缀可复用;
        找不到匹配链头返回 None(由 initial/派生补齐)。
        """
        repo = ArtifactRepository(db)
        artifacts = await repo.list_by_project(
            workset.project_id, "continuity_state", offset=0, limit=100
        )
        candidates = [
            a for a in artifacts
            if a.status == "valid"
            and a.content_schema_version == "2.0"
            and isinstance(a.content, dict)
            and (a.content.get("basis") or {}).get("story_bible_artifact_id")
            == str(workset.story_bible_artifact_id)
            and (a.content.get("basis") or {}).get("outline_artifact_id")
            == str(workset.outline_artifact_id)
            and _script_refs_match(
                a.content.get("basis") or {}, workset, a.episode_number
            )
        ]
        if not candidates:
            return None
        head = max(candidates, key=lambda a: (a.episode_number, a.version))
        try:
            state = ContinuityState.model_validate(head.content)
        except Exception:  # noqa: BLE001 — 坏状态不阻塞,按缺失处理
            logger.warning("continuity_state %s 内容校验失败,按缺失处理", head.id)
            return None
        return state, head

    async def resolve_status(
        self,
        db: AsyncSession,
        workset: StoryWorkset,
        through_episode: int,
    ) -> dict[str, Any]:
        """只读状态解析(M-04/W3-04):不调用模型、不写库。

        Returns:
            {status, through_episode(链头), requested_through, missing_episodes,
             stale_from_episode, state_artifact_id, basis, warnings}
            status: ready(链头覆盖到请求) | pending(派生落后) |
                    stale(前缀来源不同,后缀待复核) | gap(正文缺失) |
                    missing(尚无链)
        """
        warnings: list[str] = []
        missing = [
            ep for ep in range(1, through_episode + 1)
            if workset.scripts.get(ep) is None
        ]
        result: dict[str, Any] = {
            "requested_through": through_episode,
            "missing_episodes": missing,
            "stale_from_episode": None,
            "state_artifact_id": None,
            "basis": None,
            "warnings": warnings,
        }
        if missing:
            result["status"] = "gap"
            warnings.append(f"第 {missing[0]} 集正文 Artifact 缺失")
            return result

        repo = ArtifactRepository(db)
        artifacts = await repo.list_by_project(
            workset.project_id, "continuity_state", offset=0, limit=100
        )
        v2_states = [
            a for a in artifacts
            if a.status == "valid" and a.content_schema_version == "2.0"
        ]
        if not v2_states:
            result["status"] = "missing"
            warnings.append("尚无 v2 剧情状态链(首次创作/旧项目未派生)")
            return result

        loaded = await self.load_latest_state(db, workset)
        longest_through = max(a.episode_number for a in v2_states)
        if loaded is None:
            # 存在状态链但与当前工作集前缀不匹配 → 相对新工作集 stale
            closest = self._closest_prefix_mismatch(
                v2_states, workset, through_episode
            )
            result["status"] = "stale"
            result["stale_from_episode"] = closest
            warnings.append(
                f"已有状态与当前工作集来源不同,自第 {closest} 集起待复核"
            )
            return result

        state, head = loaded
        result["state_artifact_id"] = str(head.id)
        result["through_episode"] = state.through_episode
        result["basis"] = (
            state.basis.model_dump(mode="json") if state.basis else None
        )
        result["episode_summary_artifact_ids"] = _summary_refs(state)
        if state.through_episode >= through_episode:
            result["status"] = "ready"
        elif longest_through > state.through_episode:
            # 曾有更长链但其来源与当前工作集不同(采用变化)→ 后缀待复核
            result["status"] = "stale"
            result["stale_from_episode"] = state.through_episode + 1
            warnings.append(
                f"第 {state.through_episode + 1} 集起的派生相对当前工作集"
                "待复核(来源已变化)"
            )
        else:
            result["status"] = "pending"
            warnings.append(
                f"状态链头仅到第 {state.through_episode} 集,"
                f"第 {state.through_episode + 1}..{through_episode} 集待派生"
            )
        return result

    @staticmethod
    def _closest_prefix_mismatch(
        states: list[Artifact], workset: StoryWorkset, through: int
    ) -> int:
        """跨全部状态链取最早的来源不一致集数(保守:最早变化集)。"""
        earliest = through
        for artifact in states:
            basis = (artifact.content or {}).get("basis") or {}
            refs = basis.get("script_artifact_ids") or {}
            for ep in range(1, min(through, artifact.episode_number) + 1):
                if refs.get(str(ep)) != str(workset.scripts.get(ep)):
                    earliest = min(earliest, ep)
                    break
            else:
                earliest = min(earliest, artifact.episode_number + 1)
        return max(1, min(earliest, through))

    # ---- 派生 ----

    async def ensure_state_through(
        self,
        db: AsyncSession,
        workset: StoryWorkset,
        through_episode: int,
    ) -> DerivationOutcome:
        """把状态链推进到 through_episode(含),幂等。

        逐集执行:正文 Artifact 缺失 → StoryStateGapError(fail closed);
        已有相同 input_hash 的派生 → 复用(零模型调用);否则调用
        Summarizer → 校验 → reduce → 保存两个 Artifact。
        """
        if through_episode < 0:
            raise ValueError("through_episode 不能为负")

        loaded = await self.load_latest_state(db, workset)
        if loaded is not None:
            state, state_artifact = loaded
            if state.through_episode >= through_episode:
                return DerivationOutcome(
                    state=state,
                    state_artifact_id=str(state_artifact.id),
                    episode_summary_artifact_ids=_summary_refs(state),
                    model_calls=0,
                    reused_episodes=list(range(1, through_episode + 1)),
                )
            start_state, start_ref = state, str(state_artifact.id)
        else:
            # 初态从 StoryBible 确定性派生,不持久化(Artifact episode
            # 语义为"截至第 N 集",证据链从第 1 集开始);
            # 第 1 集派生的前态引用使用 StoryBible Artifact。
            start_state = await self._initial_state(db, workset)
            start_ref = str(workset.story_bible_artifact_id)

        outcome = DerivationOutcome(state=start_state, state_artifact_id=start_ref)
        outcome.episode_summary_artifact_ids = _summary_refs(start_state)

        for episode in range(outcome.state.through_episode + 1, through_episode + 1):
            script_artifact = await self._get_script(db, workset, episode)
            prev_state_ref = {
                "artifact_id": outcome.state_artifact_id,
                "version": "0",
                "relation": "derived_from",
            }
            script_ref = {
                "artifact_id": str(script_artifact.id),
                "version": "0",
                "relation": "derived_from",
            }
            es_sources = [script_ref, prev_state_ref]
            es_hash = compute_input_hash(
                es_sources,
                episode_number=episode,
                artifact_type="episode_summary",
                dedup_extra=f"v2|{self._prompt_version}",
            )
            repo = ArtifactRepository(db)
            existing = (
                await repo.find_by_input_hash(es_hash, workset.project_id)
                if es_hash
                else None
            )

            if existing is not None and existing.type == "episode_summary":
                delta = EpisodeDelta.model_validate(existing.content["delta"])
                advanced = await self._advance(
                    db, workset, outcome.state, outcome.state_artifact_id,
                    episode, script_artifact, delta, es_sources,
                )
                outcome.reused_episodes.append(episode)
            else:
                raw_delta = await self._skill.execute_delta(
                    self._require_agent(),
                    self._prompt_loader,
                    episode_number=episode,
                    script_content=dict(script_artifact.content or {}),
                    previous_state=outcome.state,
                )
                self._validate_semantics(outcome.state, raw_delta, episode)
                advanced = await self._advance(
                    db, workset, outcome.state, outcome.state_artifact_id,
                    episode, script_artifact, raw_delta, es_sources,
                )
                outcome.model_calls += 1

            outcome.state = advanced.state
            outcome.state_artifact_id = str(advanced.state_artifact_id)
            outcome.episode_summary_artifact_ids[episode] = str(
                advanced.summary_artifact_id
            )

        return outcome

    # ---- 内部步骤 ----

    async def _initial_state(
        self, db: AsyncSession, workset: StoryWorkset
    ) -> ContinuityState:
        """从 StoryBible 确定性派生 v2 初态(不落库;校验来源归属)。"""
        sb_artifact = await self._get_artifact(db, workset.story_bible_artifact_id)
        self._check_project(sb_artifact, workset.project_id, "story_bible")
        outline_artifact = await self._get_artifact(db, workset.outline_artifact_id)
        self._check_project(outline_artifact, workset.project_id, "outline")
        return ContinuityManager.create_initial_state_v2(
            StoryBible.model_validate(sb_artifact.content),
            basis=StateBasis(
                story_bible_artifact_id=str(workset.story_bible_artifact_id),
                outline_artifact_id=str(workset.outline_artifact_id),
            ),
        )

    async def _advance(
        self,
        db: AsyncSession,
        workset: StoryWorkset,
        state: ContinuityState,
        state_artifact_id: str,
        episode: int,
        script_artifact: Artifact,
        delta: EpisodeDelta,
        es_sources: list[dict[str, Any]],
    ) -> AdvanceResult:
        """reduce + 原子保存 episode_summary 与 continuity_state。"""
        es_content = EpisodeSummaryV2(
            episode_number=episode,
            summary=delta.summary,
            key_events=delta.key_events,
            ending_state=delta.ending_state,
            delta=delta,
            source_script_artifact_id=str(script_artifact.id),
        ).model_dump(mode="json")

        summary_artifact = await self._artifact_service.create_validated_artifact(
            db,
            project_id=workset.project_id,
            artifact_type="episode_summary",
            episode_number=episode,
            content=es_content,
            content_schema_version="2.0",
            prompt_version=self._prompt_version,
            source_artifact_ids=es_sources,
            dedup_extra=f"v2|{self._prompt_version}",
        )

        # 来源引用带真实版本号重算哈希与 store.create 一致性无关——
        # create 内部按传入 sources 计算;此处直接复用相同 sources。
        new_state = ContinuityManager.apply_episode_delta(
            state,
            delta,
            source_script_artifact_id=str(script_artifact.id),
            summary_artifact_id=str(summary_artifact.id),
        )
        basis = new_state.basis
        assert basis is not None
        basis.previous_state_artifact_id = state_artifact_id

        state_artifact = await self._artifact_service.create_validated_artifact(
            db,
            project_id=workset.project_id,
            artifact_type="continuity_state",
            episode_number=episode,
            content=new_state.model_dump(mode="json"),
            content_schema_version="2.0",
            prompt_version=self._prompt_version,
            source_artifact_ids=[
                {
                    "artifact_id": str(summary_artifact.id),
                    "version": 0,
                    "relation": "derived_from",
                },
                {
                    "artifact_id": str(script_artifact.id),
                    "version": 0,
                    "relation": "references",
                },
            ],
            dedup_extra=f"v2|{self._prompt_version}",
        )
        return AdvanceResult(
            state=new_state,
            summary_artifact_id=summary_artifact.id,
            state_artifact_id=state_artifact.id,
        )

    async def _get_script(
        self, db: AsyncSession, workset: StoryWorkset, episode: int
    ) -> Artifact:
        script_id = workset.scripts.get(episode)
        if script_id is None:
            raise StoryStateGapError(
                detail=(
                    f"第 {episode} 集正文 Artifact 缺失,无法推进剧情状态"
                    f"(workset={workset.story_bible_artifact_id})"
                ),
            )
        artifact = await self._get_artifact(db, script_id)
        self._check_project(artifact, workset.project_id, f"script@{episode}")
        return artifact

    async def _get_artifact(self, db: AsyncSession, artifact_id: uuid.UUID) -> Artifact:
        repo = ArtifactRepository(db)
        artifact = await repo.get(artifact_id)
        if artifact is None:
            raise StoryStateGapError(detail=f"Artifact 不存在: {artifact_id}")
        return cast("Artifact", artifact)

    @staticmethod
    def _check_project(artifact: Artifact, project_id: uuid.UUID, label: str) -> None:
        if artifact.project_id != project_id:
            raise StoryStateGapError(
                detail=(
                    f"{label} Artifact {artifact.id} 不属于项目 {project_id}"
                    "——跨项目来源被拒绝"
                ),
            )

    @staticmethod
    def _validate_semantics(
        state: ContinuityState, delta: EpisodeDelta, episode: int
    ) -> None:
        """reduce 前的语义引用校验(fail closed,不修正模型输出)。

        - 角色引用必须存在于前态(不能凭空创建角色);
        - facts.fact_id / loops_introduced.loop_id(重开)必须存在;
        - loops_resolved 必须当前 open;
        - props 持有者必须是已知角色;
        - 未来计划不得同时出现在 facts(模型层已校验,这里兜底)。
        """
        characters = set(state.character_states)
        for knowledge in delta.knowledge:
            if knowledge.character_id not in characters:
                raise ValueError(
                    f"knowledge 引用了未知角色 {knowledge.character_id}"
                )
            if knowledge.fact_id and knowledge.fact_id not in state.facts:
                raise ValueError(
                    f"knowledge 引用了不存在的事实 {knowledge.fact_id}"
                )
        for relationship in delta.relationships:
            for cid in (relationship.from_character_id, relationship.to_character_id):
                if cid not in characters:
                    raise ValueError(f"relationships 引用了未知角色 {cid}")
        for prop in delta.props:
            if prop.holder_character_id not in characters:
                raise ValueError(
                    f"props 引用了未知角色 {prop.holder_character_id}"
                )
        for fact in delta.facts:
            if fact.fact_id and fact.fact_id not in state.facts:
                raise ValueError(f"facts 引用了不存在的事实 {fact.fact_id}")
        open_ids = {loop.loop_id for loop in state.open_loops}
        known_ids = open_ids | {loop.loop_id for loop in state.resolved_loops}
        for lid in delta.loops_resolved:
            if lid not in open_ids:
                raise ValueError(f"loops_resolved 引用了非开放伏笔 {lid}")
        for intro in delta.loops_introduced:
            if intro.loop_id and intro.loop_id not in known_ids:
                raise ValueError(
                    f"loops_introduced 引用了未知伏笔 {intro.loop_id}"
                )
        plan_texts = {p.text.strip() for p in delta.future_plans}
        for fact in delta.facts:
            if fact.text.strip() in plan_texts:
                raise ValueError(
                    f"未来计划「{fact.text[:20]}」不得写成已发生事实"
                )


@dataclass(frozen=True)
class AdvanceResult:
    """单集派生的保存结果。"""

    state: ContinuityState
    summary_artifact_id: uuid.UUID
    state_artifact_id: uuid.UUID


def _script_refs_match(
    basis_content: dict[str, Any], workset: StoryWorkset, through: int
) -> bool:
    """basis 的剧本引用是否与 workset 在 [1..through] 前缀上一致。

    workset 允许携带比已派生集数更多的剧本(分批写作);
    已派生区间内引用不同(采用变化)则不匹配——由调用方重建后缀。
    """
    refs = basis_content.get("script_artifact_ids") or {}
    for ep, aid in workset.scripts.items():
        if ep <= through and refs.get(str(ep)) != str(aid):
            return False
    for key, ref in refs.items():
        try:
            ep = int(key)
        except ValueError:
            continue
        if ep <= through and workset.scripts.get(ep) is not None and str(
            workset.scripts[ep]
        ) != ref:
            return False
    return True


def _summary_refs(state: ContinuityState) -> dict[int, str]:
    basis = state.basis
    if basis is None:
        return {}
    return {int(k): v for k, v in basis.episode_summary_artifact_ids.items()}
