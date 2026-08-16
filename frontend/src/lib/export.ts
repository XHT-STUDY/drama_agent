/** 客户端本地导出工具 (H-07).
 *
 * 纯函数序列化：把各类 Artifact content 转成稳定 Markdown（不输出内部
 * UUID / schema_version / checksum 等内部字段，标题与字段名用中文）。
 * 下载走浏览器 Blob + URL.createObjectURL（无后端导出依赖）。
 *
 * 模块边界：只做序列化与浏览器下载，不触碰 API / 组件。
 */

import type {
  EpisodeOutlineSetContent,
  EvaluationDimension,
  EvaluationReportContent,
  ExportContentKind,
  ExportFormat,
  RevisionPlanContent,
  ScriptDiff,
  ScriptDraftContent,
  StoryBibleContent,
} from "@/types/api";
import { EVAL_DIMENSION_LABELS } from "@/types/api";

// ============================================================
// 类型
// ============================================================

/** 导出所需的全部内容（由导出页组装，缺数据用 null / 空数组） */
export interface ExportData {
  projectTitle: string;
  storyBible: StoryBibleContent | null;
  outline: EpisodeOutlineSetContent | null;
  /** 按集号升序 */
  scripts: ScriptDraftContent[];
  /** 按集号升序 */
  evaluations: EvaluationReportContent[];
  /** 每份修订计划（含可选 Diff 概览） */
  revisions: Array<{ plan: RevisionPlanContent; diff: ScriptDiff | null }>;
}

/** 序列化结果：文件名 + 内容 Blob（供下载与历史记录使用） */
export interface ExportResult {
  filename: string;
  blob: Blob;
  markdown: string;
  /** 导出时间（ISO，写入文档抬头与历史记录） */
  exportedAt: string;
}

/** 内容类型 → 中文短标签（用于文件名与选择框） */
export const EXPORT_KIND_LABELS: Record<ExportContentKind, string> = {
  story_bible: "StoryBible",
  outline: "大纲",
  script: "剧本",
  evaluation: "评估",
  revision: "修订说明",
};

/** 稳定遍历评估维度（保持展示顺序一致） */
const EVAL_DIM_ORDER = Object.keys(EVAL_DIMENSION_LABELS) as EvaluationDimension[];

// ============================================================
// 输出转义（I-03）
// ============================================================

/** HTML 转义（I-03）：`&` 先转，防重复转义。
 *
 * 把可能由用户/LLM 提供的文本中的 HTML 特殊字符转义为实体，
 * 使导出 Markdown 中的 `<script>` 等以纯文本显示，而非被当作标签。
 */
export function escapeHtml(text: unknown): string {
  if (text === null || text === undefined) return "";
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** 递归转义内容对象的全部字符串叶节点（数字/布尔/null 保持原样）。 */
function escapeDeep<T>(value: T): T {
  if (typeof value === "string") return escapeHtml(value) as T;
  if (Array.isArray(value)) return value.map(escapeDeep) as T;
  if (value !== null && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      out[k] = escapeDeep(v);
    }
    return out as T;
  }
  return value;
}

// ============================================================
// 逐内容类型的 Markdown 序列化（纯函数）
// ============================================================

/** StoryBible → Markdown */
export function markdownFromStoryBible(c: StoryBibleContent): string {
  const lines: string[] = ["# 世界观与人物设定（StoryBible）", ""];

  lines.push("## 基本信息", "");
  lines.push(`- 剧名：${c.title}`);
  lines.push(`- 一句话梗概（Logline）：${c.logline}`);
  lines.push(`- 类型：${c.genre}`);
  lines.push(`- 基调：${c.tone.join("、")}`);
  lines.push(`- 世界观设定：${c.world_setting}`, "");

  const characterSection = (label: string, p: {
    name: string; role: string; age_range?: string; visible_goal: string;
    hidden_need?: string; traits: string[]; strengths: string[]; flaws: string[];
    relationship_notes?: string[]; forbidden_changes?: string[];
  }): void => {
    lines.push(`## ${label}`, "");
    lines.push(`- 姓名：${p.name}`);
    lines.push(`- 定位：${p.role}`);
    if (p.age_range) lines.push(`- 年龄：${p.age_range}`);
    lines.push(`- 外在目标：${p.visible_goal}`);
    if (p.hidden_need) lines.push(`- 内在需求：${p.hidden_need}`);
    lines.push(`- 性格特点：${p.traits.join("、")}`);
    lines.push(`- 优点：${p.strengths.join("、")}`);
    lines.push(`- 缺点：${p.flaws.join("、")}`);
    if (p.relationship_notes?.length) lines.push(`- 关系备注：${p.relationship_notes.join("、")}`);
    if (p.forbidden_changes?.length) lines.push(`- 不可修改事项：${p.forbidden_changes.join("、")}`);
    lines.push("");
  };

  characterSection("主角", c.protagonist);
  characterSection("反派", c.antagonist);

  if (c.supporting_characters.length > 0) {
    lines.push("## 配角", "");
    for (const sc of c.supporting_characters) {
      lines.push(`### ${sc.name}（${sc.role}）`, "");
      lines.push(`- 外在目标：${sc.visible_goal}`);
      if (sc.hidden_need) lines.push(`- 内在需求：${sc.hidden_need}`);
      lines.push(`- 性格特点：${sc.traits.join("、")}`);
      lines.push(`- 优点：${sc.strengths.join("、")}`);
      lines.push(`- 缺点：${sc.flaws.join("、")}`);
      if (sc.relationship_notes?.length) lines.push(`- 关系备注：${sc.relationship_notes.join("、")}`);
      lines.push("");
    }
  }

  lines.push("## 核心冲突", "");
  lines.push(`- 主要冲突：${c.main_conflict}`);
  lines.push(`- 风险赌注：${c.stakes}`, "");

  lines.push("## 故事规则", "");
  c.story_rules.forEach((r) => lines.push(`- ${r}`));
  lines.push("");

  lines.push("## 长期伏笔", "");
  c.long_term_payoffs.forEach((p) => lines.push(`- ${p}`));
  lines.push("");

  lines.push("## 悬念（开放回路）", "");
  c.open_loops.forEach((o) => lines.push(`- ${o}`));
  lines.push("");

  lines.push("## 锁定事实", "");
  c.locked_facts.forEach((f) => lines.push(`- ${f}`));
  lines.push("");

  lines.push("## 合规备注", "");
  if (c.compliance_notes.length === 0) {
    lines.push("- 无");
  } else {
    c.compliance_notes.forEach((n) => lines.push(`- ${n}`));
  }
  lines.push("");

  return lines.join("\n");
}

/** 大纲 → Markdown */
export function markdownFromOutline(c: EpisodeOutlineSetContent): string {
  const lines: string[] = ["# 十集大纲", ""];
  lines.push(c.arc_summary, "");

  for (const ep of c.episodes) {
    lines.push(`## 第 ${ep.episode_number} 集：${ep.title}`, "");
    lines.push(`- 开头钩子：${ep.opening_hook}`);
    lines.push(`- 本集目标：${ep.objective}`);
    lines.push(`- 核心冲突：${ep.core_conflict}`);
    if (ep.key_events.length > 0) {
      lines.push("- 关键事件：");
      ep.key_events.forEach((e) => lines.push(`  - ${e}`));
    }
    lines.push(`- 本集回报：${ep.payoff}`);
    lines.push(`- 结尾钩子：${ep.ending_hook}`);
    lines.push(`- 下一集衔接：${ep.next_bridge}`);
    if (ep.introduced_loops.length > 0) lines.push(`- 引入伏笔：${ep.introduced_loops.join("、")}`);
    if (ep.resolved_loops.length > 0) lines.push(`- 回收伏笔：${ep.resolved_loops.join("、")}`);
    if (ep.required_characters.length > 0) lines.push(`- 出场角色：${ep.required_characters.join("、")}`);
    lines.push("");
  }

  return lines.join("\n");
}

/** 剧本 → Markdown */
export function markdownFromScript(c: ScriptDraftContent): string {
  const lines: string[] = [`# 第 ${c.episode_number} 集剧本：${c.title}`, ""];

  const formatDialogue = (d: { speaker: string; text: string; parenthetical?: string }): string =>
    d.parenthetical ? `${d.speaker}（${d.parenthetical}）：${d.text}` : `${d.speaker}：${d.text}`;

  for (const scene of c.scenes) {
    lines.push(`## 第 ${scene.scene_number} 场：${scene.location}（${scene.time_of_day}）`, "");
    if (scene.action) lines.push(scene.action, "");
    if (scene.dialogue.length > 0) {
      for (const d of scene.dialogue) lines.push(`- ${formatDialogue(d)}`);
      lines.push("");
    }
  }

  return lines.join("\n");
}

/** 评估报告 → Markdown */
export function markdownFromEvaluation(c: EvaluationReportContent): string {
  const lines: string[] = [`# 第 ${c.episode_number} 集评估报告`, ""];

  lines.push(`- 综合评分：${c.overall_score} 分`);
  lines.push(`- 是否需修订：${c.need_revision ? "是" : "否"}`, "");

  lines.push("## 维度得分", "");
  for (const dim of EVAL_DIM_ORDER) {
    const score = c.dimension_scores[dim];
    lines.push(`- ${EVAL_DIMENSION_LABELS[dim]}：${score} 分`);
  }
  lines.push("");

  lines.push("## 优点", "");
  if (c.strengths.length === 0) lines.push("- 无");
  c.strengths.forEach((s) => lines.push(`- ${s}`));
  lines.push("");

  lines.push("## 问题", "");
  if (c.issues.length === 0) lines.push("- 无");
  c.issues.forEach((issue) => {
    const scene = issue.scene_number !== null ? `第 ${issue.scene_number} 场` : "全剧";
    const severity = SEVERITY_LABELS[issue.severity];
    lines.push(`- [${severity}] ${scene} ${EVAL_DIMENSION_LABELS[issue.dimension]}：${issue.diagnosis}`);
    lines.push(`  - 证据：${issue.evidence}`);
    lines.push(`  - 建议：${issue.suggestion}`);
  });
  lines.push("");

  lines.push("## 修订建议", "");
  if (c.revision_suggestions.length === 0) lines.push("- 无");
  c.revision_suggestions.forEach((s) => lines.push(`- ${s}`));
  lines.push("");

  lines.push("## 风险提示", "");
  if (c.risk_flags.length === 0) lines.push("- 无");
  c.risk_flags.forEach((r) => lines.push(`- ${r}`));
  lines.push("");

  return lines.join("\n");
}

const SEVERITY_LABELS: Record<string, string> = {
  low: "轻微",
  medium: "中等",
  high: "严重",
};

/** 修订说明（含可选 Diff 概览） → Markdown */
export function markdownFromRevision(
  plan: RevisionPlanContent,
  diff: ScriptDiff | null = null,
): string {
  const lines: string[] = [`# 第 ${plan.episode_number} 集修订说明`, ""];

  lines.push(`- 最大变更比例：${Math.round(plan.max_change_ratio * 100)}%`);
  if (plan.user_instruction) lines.push(`- 用户补充要求：${plan.user_instruction}`);
  lines.push("");

  lines.push("## 锁定事实（修订不得违反）", "");
  if (plan.locked_facts.length === 0) lines.push("- 无");
  plan.locked_facts.forEach((f) => lines.push(`- ${f}`));
  lines.push("");

  lines.push("## 修订操作", "");
  if (plan.operations.length === 0) {
    lines.push("- 无具体修订操作");
  } else {
    for (const [index, op] of plan.operations.entries()) {
      const target = op.target_scene_number !== null ? `第 ${op.target_scene_number} 场` : "跨场景";
      lines.push(`- 操作 ${index + 1}：${target}`);
      lines.push(`  - 指令：${op.instruction}`);
      if (op.preserve.length > 0) lines.push(`  - 必须保留：${op.preserve.join("；")}`);
      if (op.expected_effect) lines.push(`  - 预期效果：${op.expected_effect}`);
    }
  }
  lines.push("");

  if (diff !== null) {
    lines.push("## 变更概览（原稿 → 修订稿）", "");
    lines.push(`- 变更比例：${(diff.change_ratio * 100).toFixed(1)}%`);
    lines.push(`- 行变更：新增 ${diff.stats.added_lines} / 删除 ${diff.stats.removed_lines} / 修改 ${diff.stats.modified_lines}`);
    if (diff.mode === "scene") {
      lines.push(`- 场景：${diff.scene_summary.from_scene_count} → ${diff.scene_summary.to_scene_count}`);
      lines.push(`- 场景变更：新增 ${diff.scene_summary.added} / 删除 ${diff.scene_summary.removed} / 修改 ${diff.scene_summary.modified} / 未变 ${diff.scene_summary.unchanged}`);
    }
    lines.push("");
  }

  return lines.join("\n");
}

// ============================================================
// 组装
// ============================================================

/** 按选中的内容类型拼成完整导出 Markdown */
export function buildExportMarkdown(opts: {
  projectTitle: string;
  exportedAt: string;
  data: ExportData;
  kinds: ExportContentKind[];
}): string {
  const { projectTitle, exportedAt, kinds } = opts;
  // I-03：内容转义——data 全部字符串叶节点过 escapeHtml，
  // 使剧本/设定中的 <script> 等以纯文本展示（防 Markdown→HTML 注入）。
  // 序列化器的结构性 Markdown 语法在转义之后才拼接，不受影响。
  const data = escapeDeep(opts.data);
  const sections: string[] = [
    `# ${escapeHtml(projectTitle)} — 内容导出`,
    "",
    `> 导出时间：${exportedAt}`,
    "",
  ];

  if (kinds.includes("story_bible")) {
    sections.push(
      data.storyBible ? markdownFromStoryBible(data.storyBible) : "## StoryBible\n\n（无可用内容）",
      "",
    );
  }
  if (kinds.includes("outline")) {
    sections.push(
      data.outline ? markdownFromOutline(data.outline) : "## 大纲\n\n（无可用内容）",
      "",
    );
  }
  if (kinds.includes("script")) {
    if (data.scripts.length === 0) {
      sections.push("## 剧本\n\n（无可用内容）", "");
    } else {
      for (const script of data.scripts) {
        sections.push(markdownFromScript(script), "");
      }
    }
  }
  if (kinds.includes("evaluation")) {
    if (data.evaluations.length === 0) {
      sections.push("## 评估\n\n（无可用内容）", "");
    } else {
      for (const evalReport of data.evaluations) {
        sections.push(markdownFromEvaluation(evalReport), "");
      }
    }
  }
  if (kinds.includes("revision")) {
    if (data.revisions.length === 0) {
      sections.push("## 修订说明\n\n（无可用内容）", "");
    } else {
      for (const r of data.revisions) {
        sections.push(markdownFromRevision(r.plan, r.diff), "");
      }
    }
  }

  return sections.join("\n");
}

// ============================================================
// 文件名与下载
// ============================================================

/** 过滤文件名中不安全字符 */
function sanitizeFilenamePart(s: string): string {
  return s.replace(/[\\/:*?"<>|\s]+/g, "_").slice(0, 40);
}

/** 生成导出文件名：{项目名}-{内容标签}-{yyyyMMdd-HHmmss}.{ext} */
export function buildExportFilename(
  projectTitle: string,
  kinds: ExportContentKind[],
  format: ExportFormat,
  timestamp: string,
): string {
  const labels = kinds.map((k) => EXPORT_KIND_LABELS[k]);
  const kindLabel = labels.length === 0 ? "导出" : labels.join("-");
  return `${sanitizeFilenamePart(projectTitle)}-${kindLabel}-${timestamp}.${format === "markdown" ? "md" : "docx"}`;
}

/** 紧凑时间戳（文件名用）：yyyyMMdd-HHmmss */
export function formatTimestamp(date: Date): string {
  const p = (n: number): string => String(n).padStart(2, "0");
  return `${date.getFullYear()}${p(date.getMonth() + 1)}${p(date.getDate())}-${p(date.getHours())}${p(date.getMinutes())}${p(date.getSeconds())}`;
}

/** 浏览器触发下载（隐藏 <a download>） */
export function downloadBlob(filename: string, blob: Blob): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** Markdown → .docx Blob（docx 库运行时加载，避免进入 SSR bundle） */
export async function buildDocxBlob(markdown: string): Promise<Blob> {
  const { Document, Packer, Paragraph, TextRun, HeadingLevel } = await import("docx");
  const children: unknown[] = [];

  const pushParagraph = (text: string): void => {
    children.push(new Paragraph({ children: [new TextRun({ text })] }));
  };

  for (const rawLine of markdown.split(/\r?\n/)) {
    const line = rawLine.trimEnd();
    if (line.trim() === "") {
      children.push(new Paragraph({ text: "" }));
      continue;
    }
    if (line.startsWith("### ")) {
      children.push(new Paragraph({ heading: HeadingLevel.HEADING_3, children: [new TextRun({ text: line.slice(4), bold: true })] }));
    } else if (line.startsWith("## ")) {
      children.push(new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun({ text: line.slice(3), bold: true })] }));
    } else if (line.startsWith("# ")) {
      children.push(new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun({ text: line.slice(2), bold: true })] }));
    } else if (line.startsWith("  - ")) {
      children.push(new Paragraph({ text: line.slice(4), bullet: { level: 1 } }));
    } else if (line.startsWith("- ") || line.startsWith("* ")) {
      children.push(new Paragraph({ text: line.slice(2), bullet: { level: 0 } }));
    } else if (line.startsWith("> ")) {
      children.push(new Paragraph({ children: [new TextRun({ text: line.slice(2), italics: true })] }));
    } else {
      pushParagraph(line);
    }
  }

  const doc = new Document({
    sections: [{ children: children as never[] }],
  });
  return Packer.toBlob(doc);
}

// ============================================================
// 统一入口
// ============================================================

/** 序列化 + 生成下载 Blob（页面与测试共用） */
export async function serializeExport(opts: {
  data: ExportData;
  kinds: ExportContentKind[];
  format: ExportFormat;
  now?: Date;
}): Promise<ExportResult> {
  const { data, kinds, format } = opts;
  const now = opts.now ?? new Date();
  const exportedAt = now.toISOString();
  const markdown = buildExportMarkdown({
    projectTitle: data.projectTitle,
    exportedAt,
    data,
    kinds,
  });
  const filename = buildExportFilename(
    data.projectTitle,
    kinds,
    format,
    formatTimestamp(now),
  );

  const blob =
    format === "markdown"
      ? new Blob([markdown], { type: "text/markdown;charset=utf-8" })
      : await buildDocxBlob(markdown);

  return { filename, blob, markdown, exportedAt };
}
