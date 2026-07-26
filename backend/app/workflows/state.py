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

    # ---- 连续性（轻量文本，非全文）----
    continuity_state_text: str
    """当前连续性状态的文本快照，由 ContinuityManager 生成。仅存文本摘要，非完整结构。"""

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

    # ---- 重试与幂等 ----
    completed_nodes: list[str]
    """已完成节点名称列表，重试时跳过。"""
    input_hashes: dict[str, str]
    """节点 → input_hash 映射，用于判断重试时是否需要重新执行。"""
    prompt_versions: dict[str, str]
    """Skill → prompt_version 映射，用于记录调用追踪。"""
