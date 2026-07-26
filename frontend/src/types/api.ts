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
