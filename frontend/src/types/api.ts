/** DramaAgent API 类型定义 (H-01).
 *
 * 与后端 Pydantic Schema 保持同步。
 * 后续阶段可通过 OpenAPI 自动生成。
 */

// ============================================================
// 通用
// ============================================================

/** API 错误响应结构 */
export interface ErrorResponse {
  request_id: string;
  detail: string;
  code: string;
  path: string;
  timestamp: string;
}

/** 分页列表响应 */
export interface PaginatedList<T> {
  items: T[];
  total: number;
  offset: number;
  limit: number;
}

// ============================================================
// 项目 (Project)
// ============================================================

export type ProjectStatus = "draft" | "planning" | "writing" | "evaluating" | "revising" | "completed" | "archived";

export interface Project {
  id: string;
  title: string;
  status: ProjectStatus;
  target_episode_count: number;
  current_episode_count: number;
  created_at: string;
  updated_at: string;
}

export interface ProjectCreate {
  title: string;
  target_episode_count?: number;
}

// ============================================================
// Artifact
// ============================================================

export type ArtifactType =
  | "normalized_requirement"
  | "story_bible"
  | "episode_outline_set"
  | "script_draft"
  | "evaluation_report"
  | "revision_plan"
  | "continuity_check"
  | "continuity_state"
  | "conversation_summary";

export type ArtifactStatus = "draft" | "valid" | "invalid";

export interface Artifact {
  id: string;
  project_id: string;
  type: ArtifactType;
  version: number;
  episode_number: number;
  status: ArtifactStatus;
  content: Record<string, unknown>;
  content_schema_version: string;
  prompt_version: string;
  input_hash?: string;
  checksum?: string;
  source_artifact_ids?: Array<{ artifact_id: string; version: number; relation: string }>;
  created_at: string;
  updated_at: string;
}

// ============================================================
// StoryBible 内容
// ============================================================

export interface CharacterProfile {
  character_id: string;
  name: string;
  role: string;
  age_range?: string;
  visible_goal: string;
  hidden_need?: string;
  traits: string[];
  strengths: string[];
  flaws: string[];
  relationship_notes?: string[];
  forbidden_changes?: string[];
}

export interface StoryBibleContent {
  title: string;
  logline: string;
  genre: string;
  tone: string[];
  world_setting: string;
  protagonist: CharacterProfile;
  antagonist: CharacterProfile;
  supporting_characters: CharacterProfile[];
  main_conflict: string;
  stakes: string;
  story_rules: string[];
  long_term_payoffs: string[];
  open_loops: string[];
  locked_facts: string[];
  compliance_notes: string[];
}

// ============================================================
// 大纲
// ============================================================

export interface EpisodeOutline {
  episode_number: number;
  title: string;
  opening_hook: string;
  objective: string;
  core_conflict: string;
  key_events: string[];
  payoff: string;
  ending_hook: string;
  next_bridge: string;
  introduced_loops: string[];
  resolved_loops: string[];
  required_characters: string[];
}

export interface EpisodeOutlineSetContent {
  episodes: EpisodeOutline[];
  arc_summary: string;
  validation_notes: string[];
}

// ============================================================
// 剧本
// ============================================================

export interface DialogueLine {
  speaker: string;
  text: string;
  parenthetical?: string;
}

export interface Scene {
  scene_number: number;
  location: string;
  time_of_day: string;
  characters: string[];
  action: string;
  dialogue: DialogueLine[];
}

export interface ScriptDraftContent {
  episode_number: number;
  title: string;
  opening_hook: string;
  scenes: Scene[];
  ending_hook: string;
  plain_text: string;
  word_count: number;
  dialogue_ratio: number;
  referenced_outline_artifact_id: string;
}

// ============================================================
// Run
// ============================================================

export type RunStatus = "queued" | "running" | "completed" | "failed" | "cancelled" | "needs_review";

export interface Run {
  run_id: string;
  project_id: string;
  action: string;
  status: RunStatus;
  config_snapshot?: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface CreateScriptOptions {
  user_input: string;
  source_type?: "idea" | "outline" | "txt" | "docx";
  outline_count?: number;
  script_count?: number;
}

export interface CreateRunRequest {
  action: "create_script" | "evaluate" | "revise" | "platform_smoke";
  options?: CreateScriptOptions;
  config?: Record<string, unknown>;
  idempotency_key?: string;
}

// ============================================================
// SSE 事件
// ============================================================

export interface WorkflowEvent {
  event_id: string;
  run_id: string;
  sequence: number;
  /** 后端 WorkflowEventSchema 使用 event_type 字段名 */
  event_type: string;
  stage: string;
  progress: number;
  message: string;
  artifact_id: string | null;
  payload?: Record<string, unknown>;
  timestamp: string;
}

// ============================================================
// 评估 (Evaluation) — 与后端 app/domain/evaluation.py 同步
// ============================================================

/** 评估九个维度 */
export type EvaluationDimension =
  | "opening_hook"
  | "main_clarity"
  | "character_appeal"
  | "conflict_intensity"
  | "payoff_density"
  | "ending_hook"
  | "pacing"
  | "visualizability"
  | "compliance_safety";

/** 维度中文标签映射 */
export const EVAL_DIMENSION_LABELS: Record<EvaluationDimension, string> = {
  opening_hook: "开头钩子",
  main_clarity: "主线清晰度",
  character_appeal: "角色吸引力",
  conflict_intensity: "冲突强度",
  payoff_density: "爽点密度",
  ending_hook: "结尾钩子",
  pacing: "节奏控制",
  visualizability: "可视化程度",
  compliance_safety: "合规安全",
};

/** 问题严重程度 */
export type Severity = "low" | "medium" | "high";

/** 严重程度 → 颜色映射 */
export const SEVERITY_COLORS: Record<Severity, string> = {
  low: "bg-yellow-100 text-yellow-700",
  medium: "bg-orange-100 text-orange-700",
  high: "bg-red-100 text-red-700",
};

/** 评估中发现的问题 */
export interface EvaluationIssue {
  issue_id: string;
  dimension: EvaluationDimension;
  severity: Severity;
  scene_number: number | null;
  evidence: string;
  diagnosis: string;
  suggestion: string;
}

/** 单集评估报告 */
export interface EvaluationReportContent {
  episode_number: number;
  script_artifact_id: string;
  rubric_version: string;
  dimension_scores: Record<EvaluationDimension, number>;
  overall_score: number;
  strengths: string[];
  issues: EvaluationIssue[];
  revision_suggestions: string[];
  need_revision: boolean;
  risk_flags: string[];
}

/** 默认九个维度权重（与后端 DEFAULT_EVALUATION_WEIGHTS 一致） */
export const DEFAULT_EVALUATION_WEIGHTS: Record<EvaluationDimension, number> = {
  opening_hook: 0.12,
  main_clarity: 0.14,
  character_appeal: 0.14,
  conflict_intensity: 0.14,
  payoff_density: 0.12,
  ending_hook: 0.10,
  pacing: 0.08,
  visualizability: 0.08,
  compliance_safety: 0.08,
};

// ============================================================
// 修订 (Revision) — 与后端 app/domain/revision.py 同步 (H-06)
// ============================================================

/** 修订计划中的一条操作 */
export interface RevisionOperation {
  operation_id: string;
  target_scene_number: number | null;
  issue_ids: string[];
  instruction: string;
  preserve: string[];
  expected_effect: string;
}

/** revision_plan Artifact 的 content */
export interface RevisionPlanContent {
  episode_number: number;
  source_script_artifact_id: string;
  source_evaluation_artifact_id: string;
  operations: RevisionOperation[];
  locked_facts: string[];
  max_change_ratio: number;
  user_instruction: string | null;
}

/** GET /projects/{pid}/revisions/{plan_id} 详情响应：Artifact + 顶层 result_chain */
export interface RevisionPlanArtifact extends Omit<Artifact, "content"> {
  content: RevisionPlanContent;
  result_chain: ResultChain;
}

/** 连续性违规类型 */
export type ContinuityViolationKind =
  | "locked_fact_missing"
  | "locked_fact_reversed"
  | "required_event_missing"
  | "required_character_missing"
  | "loop_inconsistent"
  | "character_state_change"
  | "semantic_inconsistency";

/** 违规类型 → 中文标签 */
export const CONTINUITY_VIOLATION_LABELS: Record<ContinuityViolationKind, string> = {
  locked_fact_missing: "锁定事实缺失",
  locked_fact_reversed: "锁定事实被反转",
  required_event_missing: "必需事件缺失",
  required_character_missing: "必需角色缺失",
  loop_inconsistent: "伏笔状态不一致",
  character_state_change: "人物状态矛盾",
  semantic_inconsistency: "语义不一致",
};

/** 连续性违规 */
export interface ContinuityViolation {
  kind: ContinuityViolationKind;
  target: string;
  expected: string;
  actual: string;
  evidence: string;
  source: "rule" | "semantic";
}

/** 连续性警告 */
export interface ContinuityWarning {
  kind: ContinuityViolationKind;
  target: string;
  message: string;
  source: "rule" | "semantic";
}

/** continuity_check Artifact 的 content */
export interface ContinuityCheckContent {
  status: "pass" | "fail";
  checked_episode_number: number;
  violations: ContinuityViolation[];
  warnings: ContinuityWarning[];
  rule_checks_run: string[];
  semantic_checks_run: string[];
}

/** result_chain：plan 详情沿 Artifact 链反查的结果（每段可 null） */
export interface ResultChain {
  source_script: Artifact | null;
  source_evaluation: Artifact | null;
  candidate_script: Artifact | null;
  continuity_check: Artifact | null;
  new_evaluation: Artifact | null;
  diff_ids: { base: string; target: string } | null;
}

/** 发起修订请求体 */
export interface CreateRevisionRequest {
  script_artifact_id: string | null;
  user_instruction: string | null;
  idempotency_key: string | null;
}

// ============================================================
// Diff — 与后端 app/domain/diff.py 同步 (H-06)
// ============================================================

/** Diff 模式：scene=结构化场景对比；line=全文行对比（无法解析时回退） */
export type DiffMode = "scene" | "line";

/** 变更类型 */
export type SceneChangeType = "added" | "removed" | "modified" | "unchanged";

/** 单行变更 */
export interface LineChange {
  change_type: SceneChangeType;
  old_line_number: number | null;
  new_line_number: number | null;
  old_text: string | null;
  new_text: string | null;
}

/** 单场景变更 */
export interface SceneChange {
  change_type: SceneChangeType;
  old_scene_number: number | null;
  new_scene_number: number | null;
  location: string;
  time_of_day: string;
  similarity: number;
  added_lines: number;
  removed_lines: number;
  modified_lines: number;
  added_chars: number;
  removed_chars: number;
  line_changes: LineChange[];
  line_changes_truncated: boolean;
}

/** 行/字符统计 */
export interface DiffLineStats {
  added_lines: number;
  removed_lines: number;
  modified_lines: number;
  added_chars: number;
  removed_chars: number;
  changed_chars: number;
  from_chars: number;
  to_chars: number;
}

/** 场景级摘要 */
export interface SceneDiffSummary {
  from_scene_count: number;
  to_scene_count: number;
  added: number;
  removed: number;
  modified: number;
  unchanged: number;
}

/** GET /artifacts/diff 响应 */
export interface ScriptDiff {
  mode: DiffMode;
  from_artifact_id: string | null;
  to_artifact_id: string | null;
  from_version: number | null;
  to_version: number | null;
  project_id: string | null;
  episode_number: number | null;
  change_ratio: number;
  scene_summary: SceneDiffSummary;
  stats: DiffLineStats;
  scene_changes: SceneChange[];
  line_changes: LineChange[];
  truncated: boolean;
}

// ============================================================
// 导出中心 (Export) — H-07（前端本地导出）
// ============================================================

/** 导出文件格式 */
export type ExportFormat = "markdown" | "docx";

/** 可导出的内容类型（粒度：整类内容，而非单集） */
export type ExportContentKind =
  | "story_bible"
  | "outline"
  | "script"
  | "evaluation"
  | "revision";

/** 导出历史记录（本地存储，H-07） */
export interface ExportRecord {
  id: string;
  exportedAt: string;
  format: ExportFormat;
  kinds: ExportContentKind[];
  filename: string;
  sizeBytes: number;
}
