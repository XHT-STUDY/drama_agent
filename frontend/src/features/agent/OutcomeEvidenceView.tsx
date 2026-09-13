"use client";

/** OutcomeEvidenceView — 结果证据视图（W1-04，ActionPlanCard 与 MessageList 共用）。
 *
 * 诚实性契约：
 * - 执行结束 ≠ 要求达成：completed + 评分上涨 + 无核验证据时，逐条创作
 *   要求如实显示"待判断"（unverified），不伪装成已失败（"未完成"只留
 *   给已知确定性失败）；
 * - 证据入口锚定本轮实际产出/依据（evidence_refs 带角色），不再让读者
 *   去通用版本页猜哪份是本轮结果；
 * - compact 模式（消息流）：无链接、可展开，保留完整内容不截断。
 */

import type { AgentOutcome, ConstraintCheck } from "@/types/api";

const GOAL_STATUS_LABEL: Record<string, string> = {
  achieved: "已达成",
  partially_achieved: "部分达成",
  blocked: "受阻",
};

const EVIDENCE_ROLE_LABEL: Record<string, string> = {
  source_script: "修订依据剧本",
  source_outline: "修订依据大纲",
  story_bible: "故事设定",
  outline: "分集大纲",
  script: "本轮剧本",
  evaluation: "评估报告",
  continuity: "连续性检查",
  revision_plan: "修订计划",
};

const CHECK_STATUS_LABEL: Record<ConstraintCheck["status"], string> = {
  satisfied: "已满足",
  unsatisfied: "未满足",
  unverified: "待判断",
};

interface Props {
  outcome:
    | AgentOutcome
    | {
        goal_status: string;
        verification_status?: "verified" | "unverified";
        constraint_checks?: ConstraintCheck[];
        evidence_refs?: AgentOutcome["evidence_refs"];
        remaining_constraints: string[];
        score_delta?: number | null;
      };
  /** 旧消息（W1-04 之前）没有核验字段——如实标注，不冒充新语义 */
  /** 提供时证据锚点渲染为版本页链接（消息流 compact 模式可不传） */
  projectId?: string;
  /** 消息流紧凑模式：不渲染链接与外层边框 */
  compact?: boolean;
}

export function OutcomeEvidenceView({ outcome, projectId, compact }: Props) {
  const statusColor =
    outcome.goal_status === "achieved"
      ? "text-[var(--success)]"
      : outcome.goal_status === "partially_achieved"
        ? "text-[var(--warning)]"
        : "text-[var(--danger)]";

  const legacyNoChecks = outcome.constraint_checks === undefined;
  const unverified = (outcome.constraint_checks ?? []).filter(
    (c) => c.status === "unverified",
  );
  const checked = (outcome.constraint_checks ?? []).filter(
    (c) => c.status !== "unverified",
  );
  const remaining = outcome.remaining_constraints ?? [];
  // 证据入口：同角色去重后展示（一批多集时折叠为"本轮剧本 ×3"）
  const refsByRole = new Map<string, string[]>();
  for (const ref of outcome.evidence_refs ?? []) {
    const list = refsByRole.get(ref.role) ?? [];
    list.push(ref.artifact_id);
    refsByRole.set(ref.role, list);
  }

  return (
    <div
      className={
        compact
          ? "mt-1 space-y-1"
          : "mt-3 space-y-2 border-t border-[var(--border)] pt-3"
      }
      data-testid={compact ? "message-outcome" : "action-outcome"}
    >
      <p className="text-sm font-medium">
        结果：
        <span className={statusColor} data-testid="goal-status">
          {GOAL_STATUS_LABEL[outcome.goal_status] ?? outcome.goal_status}
        </span>
        {typeof outcome.score_delta === "number" && (
          <span
            className="ml-2 text-xs text-[var(--text-muted)]"
            data-testid="score-delta"
          >
            评分变化 {outcome.score_delta > 0 ? "+" : ""}
            {outcome.score_delta.toFixed(1)}
          </span>
        )}
      </p>

      {/* 待判断：缺核验证据的创作要求——不是失败，交给作者阅读本轮稿件 */}
      {unverified.length > 0 && (
        <div data-testid="unverified-checks">
          <p className="text-xs text-[var(--text-muted)]">
            以下 {unverified.length} 项创作要求未能自动核验，需要你阅读本轮稿件后判断：
          </p>
          <ul className="mt-1 list-disc pl-5 text-xs text-[var(--text)]">
            {unverified.map((c) => (
              <li key={c.constraint}>{c.constraint}</li>
            ))}
          </ul>
        </div>
      )}

      {/* 已执行检查的结果（satisfied/unsatisfied 只来自真实检查） */}
      {checked.length > 0 && (
        <ul className="list-disc pl-5 text-xs" data-testid="checked-constraints">
          {checked.map((c) => (
            <li
              key={c.constraint}
              className={
                c.status === "unsatisfied" ? "text-[var(--danger)]" : "text-[var(--success)]"
              }
            >
              {c.constraint}（{CHECK_STATUS_LABEL[c.status]}）
            </li>
          ))}
        </ul>
      )}

      {legacyNoChecks && (
        <p className="text-xs text-[var(--text-muted)]" data-testid="legacy-outcome-note">
          历史结果未记录逐项核验。
        </p>
      )}

      {/* 已知确定性失败（与待判断分开陈述） */}
      {remaining.length > 0 && (
        <ul
          className="list-disc pl-5 text-xs text-[var(--warning)]"
          data-testid="remaining-constraints"
        >
          {remaining.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
      )}

      {/* 本轮证据入口 */}
      {refsByRole.size > 0 && (
        <div className="text-xs" data-testid="evidence-refs">
          <span className="text-[var(--text-muted)]">本轮依据：</span>
          {[...refsByRole.entries()].map(([role, ids]) => (
            <span key={role} className="mr-3 inline-flex items-center gap-1">
              {projectId ? (
                <a
                  href={`/projects/${projectId}/versions?artifact=${ids[0]}`}
                  className="text-[var(--accent)] underline-offset-2 hover:underline"
                  data-testid={`evidence-link-${role}`}
                >
                  {EVIDENCE_ROLE_LABEL[role] ?? role}
                  {ids.length > 1 ? ` ×${ids.length}` : ""}
                </a>
              ) : (
                <span>
                  {EVIDENCE_ROLE_LABEL[role] ?? role}
                  {ids.length > 1 ? ` ×${ids.length}` : ""}
                </span>
              )}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
