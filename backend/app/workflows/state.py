"""CreationWorkflow 状态模型 (C-07).

State 只存储 Artifact ID 和轻量字段，大文本不存入 State，
避免 LangGraph checkpoint 膨胀（见 DEV_PLAN §2.2）。
"""

from __future__ import annotations

from typing import TypedDict


class CreationState(TypedDict, total=False):
    """创作工作流状态。

    所有字段使用简单类型确保序列化兼容 checkpointer。
    Artifact 内容通过 ArtifactService 按 ID 查询，不存入 State。
    """

    # ---- 标识 ----
    run_id: str
    """当前 WorkflowRun 的 UUID 字符串。"""
    project_id: str
    """关联的 Project UUID 字符串。"""
    action: str
    """触发的 action 名称（如 "create_script"）。"""

    # ---- Artifact ID（仅存 ID，内容通过 Service 查询）----
    requirement_artifact_id: str | None
    """归一化需求 Artifact UUID 字符串。"""
    story_bible_artifact_id: str | None
    """StoryBible Artifact UUID 字符串。"""
    outline_set_artifact_id: str | None
    """分集大纲 Artifact UUID 字符串。"""
    script_artifact_ids: dict[str, str]
    """集号 → ScriptDraft Artifact UUID 字符串映射。例: {"1": "uuid1", "2": "uuid2"}。"""
    evaluation_artifact_ids: dict[str, str]
    """集号 → EvaluationReport Artifact UUID 字符串映射（Phase E）。例: {"1": "uuidE1"}。"""

    # ---- 连续性（轻量文本，非全文）----
    continuity_state_text: str
    """当前连续性状态的文本快照，由 ContinuityManager 生成。仅存文本摘要，非完整结构。"""

    # ---- 评估与修订决策 ----
    needs_revision_decision: bool
    """3 集评估完成后为 True：存在需修订的集（F-05 起由修订分支实际处理）。"""

    # ---- 修订（F-05）----
    revision_round: int
    """已选定并推进的修订轮数（0 起，select_revision 成功选定后自增）。"""
    revision_candidate_episode: int | None
    """当前轮选中的待修订集号（1-based）。"""
    revision_plan_artifact_id: str | None
    """当前轮修订计划 Artifact UUID 字符串。"""
    continuity_check_artifact_id: str | None
    """当前轮连续性检查结果 Artifact UUID 字符串（J-06，供结果消息/diff 引用）。"""
    user_instruction: str | None
    """用户补充要求（不可违反锁定事实；进 RevisionPlanInput 供修订计划使用）。"""

    # ---- 对话式剧本修订（J-06）----
    source_script_artifact_id: str | None
    """服务端解析的修订目标剧本 Artifact UUID（对话式修订入口，不由 Planner 决定）。"""
    user_constraints: list[str]
    """用户约束（来自确认的 ActionPlan），拼接后写入 RevisionPlan 的 user_instruction。"""
    needs_manual_review: bool
    """连续性失败或重评分显著下降（>5 分）时为 True，转人工审查。"""
    needs_manual_review_reason: str | None
    """人工审查原因（连续性违规摘要 / 分数下降说明）。"""

    # ---- 流程控制 ----
    current_episode: int
    """当前正在处理的集号（1-based）。"""
    status: str
    """工作流整体状态: running | completed | failed | needs_user_input。"""
    needs_user_input: bool
    """normalize 节点检测到关键输入缺失时为 True。"""
    error_node: str | None
    """失败时记录失败的节点名称。"""
    error_detail: str | None
    """失败时记录错误详情。"""
    error_code: str | None
    """失败时记录机器可读错误码（I-01，如 RUN_BUDGET_EXCEEDED / LLM_TIMEOUT）。"""

    # ---- 重试与幂等 ----
    completed_nodes: list[str]
    """已完成节点名称列表，重试时跳过。"""
    input_hashes: dict[str, str]
    """节点 → input_hash 映射，用于判断重试时是否需要重新执行。"""
    prompt_versions: dict[str, str]
    """Skill → prompt_version 映射，用于记录调用追踪。"""
