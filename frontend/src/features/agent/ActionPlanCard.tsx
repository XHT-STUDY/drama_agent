"use client";

/** ActionPlanCard — 待确认计划与执行结果卡（J-11）。
 *
 * 展示：目标、目标集、来源版本快照、约束、步骤、预计影响。
 * 状态：
 * - proposed：确认（唯一主按钮）/ 拒绝（次级）；确认期间禁用防重复
 * - queued/running：执行中（内嵌 RunProgress 由工作台渲染）
 * - completed：结果（goal_status/评分变化/剩余约束/证据链接）
 * - needs_review / failed：明确恢复入口（查看版本 / 重新发起）
 * - stale：提示重新发起规划并聚焦输入框
 * - rejected：已取消
 */

import Link from "next/link";
import type { AgentOutcome } from "@/types/api";
import { useMutation } from "@tanstack/react-query";

import { runsApi } from "@/lib/api-client";
import { useAgentAction } from "@/hooks/use-agent-action";

const GOAL_STATUS_LABEL: Record<string, string> = {
  achieved: "已达成",
  partially_achieved: "部分达成",
  blocked: "受阻",
};

const STATUS_LABEL: Record<string, string> = {
  proposed: "等待确认",
  queued: "排队中",
  running: "执行中",
  completed: "已完成",
  needs_review: "需人工复核",
  failed: "失败",
  cancelled: "已取消",
  stale: "计划已过期",
  rejected: "已拒绝",
};

function OutcomeView({
  projectId,
  outcome,
}: {
  projectId: string;
  outcome: AgentOutcome;
}) {
  const statusColor =
    outcome.goal_status === "achieved"
      ? "text-[var(--success)]"
      : outcome.goal_status === "partially_achieved"
        ? "text-[var(--warning)]"
        : "text-[var(--danger)]";
  return (
    <div className="mt-3 space-y-2 border-t border-[var(--border)] pt-3" data-testid="action-outcome">
      <p className="text-sm font-medium">
        结果：
        <span className={statusColor} data-testid="goal-status">
          {GOAL_STATUS_LABEL[outcome.goal_status] ?? outcome.goal_status}
        </span>
        {typeof outcome.score_delta === "number" && (
          <span className="ml-2 text-xs text-[var(--text-muted)]" data-testid="score-delta">
            评分变化 {outcome.score_delta > 0 ? "+" : ""}
            {outcome.score_delta.toFixed(1)}
          </span>
        )}
      </p>
      {outcome.remaining_constraints.length > 0 && (
        <ul className="list-disc pl-5 text-xs text-[var(--warning)]" data-testid="remaining-constraints">
          {outcome.remaining_constraints.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
      )}
      {outcome.evidence_artifact_ids.length > 0 && (
        <div className="flex flex-wrap gap-2" data-testid="evidence-links">
          {outcome.evidence_artifact_ids.slice(0, 8).map((id) => (
            <Link
              key={id}
              href={`/projects/${projectId}/versions?artifact=${id}`}
              className="rounded border border-[var(--border)] px-2 py-1 text-xs text-[var(--accent)] transition-state hover:border-[var(--accent)]"
            >
              产物 {id.slice(0, 8)}…
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

interface Props {
  actionId: string;
  projectId: string;
  /** stale / 重新发起时聚焦 Composer（恢复入口） */
  onAskAgain?: () => void;
  /** 计划执行确认后的刷新（继续按钮成功后失效查询） */
  onContinued?: () => void;
}

export function ActionPlanCard({
  actionId,
  projectId,
  onAskAgain,
  onContinued,
}: Props) {
  const {
    action,
    isLoading,
    confirm,
    confirming,
    confirmError,
    isStale,
    reject,
    rejecting,
  } = useAgentAction(actionId);

  // L-3 确认门续跑：分段创作 needs_review 时"继续创作剧本"
  const continueMutation = useMutation({
    mutationFn: () => runsApi.continueRun(action!.run_id!),
    onSuccess: () => onContinued?.(),
  });

  if (isLoading || !action) {
    return (
      <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4 text-sm text-[var(--text-muted)]">
        正在加载计划…
      </div>
    );
  }

  const { plan } = action;
  const episode = plan.target.episode_number;
  const terminalWithOutcome =
    (action.status === "completed" || action.status === "needs_review") &&
    action.result;

  return (
    <section
      aria-label="执行计划"
      data-status={action.status}
      className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4 shadow-sm"
    >
      <header className="mb-2 flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">{plan.goal}</h3>
        <span className="shrink-0 rounded-full border border-[var(--border)] px-2 py-0.5 text-xs text-[var(--text-muted)]">
          {STATUS_LABEL[action.status] ?? action.status}
        </span>
      </header>

      {episode != null && (
        <p className="text-xs text-[var(--text-muted)]">目标：第 {episode} 集</p>
      )}

      {action.source_artifact_ids.length > 0 && (
        <p className="mt-1 text-xs text-[var(--text-muted)]" data-testid="plan-sources">
          来源版本：
          {action.source_artifact_ids
            .map((s) => `${s.artifact_type} v${s.version}`)
            .join("、")}
        </p>
      )}

      {plan.constraints.length > 0 && (
        <div className="mt-2 text-xs">
          <span className="text-[var(--text-muted)]">约束：</span>
          {plan.constraints.join("；")}
        </div>
      )}

      <ol className="mt-3 space-y-1 text-xs text-[var(--text)]" data-testid="plan-steps">
        {plan.steps.map((step, i) => (
          <li key={step.step_id} className="flex gap-2">
            <span className="text-[var(--text-muted)]">{i + 1}.</span>
            <span>
              <span className="font-medium">{step.title}</span>
              <span className="text-[var(--text-muted)]"> — {step.description}</span>
            </span>
          </li>
        ))}
      </ol>

      {plan.expected_impact.length > 0 && (
        <p className="mt-2 text-xs text-[var(--text-muted)]" data-testid="plan-impact">
          预计影响：{plan.expected_impact.join("；")}（执行将产生新版本与模型调用费用）
        </p>
      )}

      {/* proposed：确认是唯一主按钮 */}
      {action.status === "proposed" && !isStale && (
        <div className="mt-4 flex flex-col gap-2 sm:flex-row">
          <button
            type="button"
            onClick={() => void confirm()}
            disabled={confirming}
            data-testid="confirm-action"
            className="touch-target rounded-lg bg-[var(--accent)] px-4 text-sm font-medium text-white transition-state hover:bg-[var(--accent-hover)] disabled:opacity-50 sm:min-w-32"
          >
            {confirming ? "确认中…" : "确认执行"}
          </button>
          <button
            type="button"
            onClick={() => void reject()}
            disabled={rejecting}
            data-testid="reject-action"
            className="touch-target rounded-lg border border-[var(--border)] px-4 text-sm text-[var(--text-muted)] transition-state hover:border-[var(--danger)] hover:text-[var(--danger)] disabled:opacity-50"
          >
            拒绝
          </button>
        </div>
      )}

      {/* L-3 分段确认门：needs_review 且计划为分段创作 → 继续按钮（唯一主操作） */}
      {action.status === "needs_review" &&
        plan.command.intent === "create_script" &&
        plan.command.stop_after === "outline" &&
        action.run_id && (
          <div className="mt-4">
            <p className="mb-2 text-xs text-[var(--text-muted)]">
              StoryBible 与大纲已生成（暂停期间如有修改，将以最新版本继续），确认后继续创作剧本。
            </p>
            <button
              type="button"
              onClick={() => continueMutation.mutate()}
              disabled={continueMutation.isPending}
              data-testid="continue-creation"
              className="touch-target w-full rounded-lg bg-[var(--accent)] px-4 text-sm font-medium text-white transition-state hover:bg-[var(--accent-hover)] disabled:opacity-50 sm:w-auto sm:min-w-40"
            >
              {continueMutation.isPending ? "继续中…" : "继续创作剧本"}
            </button>
            {continueMutation.isError && (
              <p className="mt-2 text-xs text-[var(--danger)]" role="alert">
                {(continueMutation.error as Error).message}
              </p>
            )}
          </div>
        )}

      {/* queued/running 提示（进度由工作台内嵌 RunProgress 展示） */}
      {(action.status === "queued" || action.status === "running") && (
        <p className="mt-3 text-xs text-[var(--text-muted)]" aria-live="polite">
          计划执行中，进度见下方…
        </p>
      )}

      {/* 恢复入口：stale / failed / needs_review */}
      {isStale && (
        <div className="mt-3 rounded border border-[var(--warning)] bg-[var(--warning-bg)] p-2 text-xs text-[var(--warning)]" role="alert">
          {confirmError}
          <button
            type="button"
            onClick={onAskAgain}
            className="ml-2 underline"
            data-testid="ask-again"
          >
            重新描述需求
          </button>
        </div>
      )}
      {!isStale && confirmError && (
        <p className="mt-2 text-xs text-[var(--danger)]" role="alert">{confirmError}</p>
      )}
      {(action.status === "failed" || action.status === "needs_review") && (
        <div className="mt-3 flex flex-wrap items-center gap-3 text-xs">
          <Link
            href={`/projects/${projectId}/versions`}
            className="rounded border border-[var(--border)] px-2 py-1 text-[var(--accent)] transition-state hover:border-[var(--accent)]"
          >
            查看版本与诊断
          </Link>
          <button
            type="button"
            onClick={onAskAgain}
            className="rounded border border-[var(--border)] px-2 py-1 text-[var(--text-muted)] transition-state hover:border-[var(--accent)]"
            data-testid="replan-entry"
          >
            重新发起规划
          </button>
        </div>
      )}

      {terminalWithOutcome && action.result && (
        <OutcomeView projectId={projectId} outcome={action.result} />
      )}
      {terminalWithOutcome && action.result?.recommended_next_action && (
        <p className="mt-2 text-xs text-[var(--text-muted)]">
          建议的后续动作：{action.result.recommended_next_action.intent}
          （见下方后续计划，需手动确认）
        </p>
      )}
    </section>
  );
}
