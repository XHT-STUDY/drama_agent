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
import type { Artifact } from "@/types/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { artifactsApi, runsApi } from "@/lib/api-client";
import { useAgentAction } from "@/hooks/use-agent-action";
import { McpToolPlanDetails } from "./McpToolActionCard";
import { OutcomeEvidenceView } from "./OutcomeEvidenceView";

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

interface Props {
  actionId: string;
  projectId: string;
  /** stale / 重新发起时聚焦 Composer（恢复入口） */
  onAskAgain?: () => void;
  /** 计划执行确认后的刷新（继续按钮成功后失效查询） */
  onContinued?: () => void;
}

type SBContent = {
  title?: string;
  genre?: string;
  logline?: string;
  protagonist?: { name?: string };
  antagonist?: { name?: string };
};
type EpisodeItem = { episode_number?: number; title?: string; objective?: string };
type ScriptContent = { title?: string; word_count?: number };

/** 门上内嵌预览：SB/大纲/剧本就地可看，不必跳页再回来 */
function GatePreview(props: {
  stageGate: "outline" | "scripts";
  projectId: string;
  storyBible?: Artifact | null;
  outline?: Artifact | null;
  scripts?: Artifact[];
}) {
  const { stageGate, projectId, storyBible, outline, scripts } = props;
  const sb = storyBible?.content as SBContent | undefined;
  const episodes = (outline?.content?.episodes ?? []) as EpisodeItem[];
  const latestPerEpisode = new Map<number, Artifact>();
  for (const s of scripts ?? []) {
    if (s.status !== "valid") continue;
    const existing = latestPerEpisode.get(s.episode_number);
    if (!existing || s.version > existing.version) latestPerEpisode.set(s.episode_number, s);
  }
  return (
    <details
      className="rounded-lg border border-[var(--border)] bg-[var(--surface-muted)] p-3"
      open
      data-testid="gate-preview"
    >
      <summary className="cursor-pointer text-xs font-medium">本次生成的内容预览</summary>
      <div className="mt-2 space-y-2 text-xs">
        {stageGate === "outline" && (
          <>
            {sb && (
              <div>
                <p className="font-medium">
                  {sb.title ?? "未命名"}
                  {sb.genre ? `（${sb.genre}）` : ""}
                </p>
                {sb.logline && (
                  <p className="mt-0.5 text-[var(--text-muted)]">{sb.logline}</p>
                )}
                {(sb.protagonist?.name || sb.antagonist?.name) && (
                  <p className="mt-0.5 text-[var(--text-muted)]">
                    主角：{sb.protagonist?.name ?? "—"} · 反派：{sb.antagonist?.name ?? "—"}
                  </p>
                )}
              </div>
            )}
            {episodes.length > 0 && (
              <ol className="space-y-1" data-testid="gate-preview-episodes">
                {episodes.map((ep) => (
                  <li key={ep.episode_number} className="flex gap-2">
                    <span className="shrink-0 font-medium text-[var(--accent)]">
                      第 {ep.episode_number} 集
                    </span>
                    <span>
                      <span className="font-medium">{ep.title ?? ""}</span>
                      {ep.objective && (
                        <span className="text-[var(--text-muted)]"> — {ep.objective}</span>
                      )}
                    </span>
                  </li>
                ))}
              </ol>
            )}
            <p className="flex gap-3">
              <Link href={`/projects/${projectId}/story-bible`} className="text-[var(--accent)] underline">
                查看完整 Story Bible
              </Link>
              <Link href={`/projects/${projectId}/outline`} className="text-[var(--accent)] underline">
                查看完整大纲
              </Link>
            </p>
          </>
        )}
        {stageGate === "scripts" && (
          <>
            {latestPerEpisode.size > 0 ? (
              <ul className="space-y-1" data-testid="gate-preview-scripts">
                {[...latestPerEpisode.values()]
                  .sort((a, b) => a.episode_number - b.episode_number)
                  .map((s) => {
                    const c = s.content as ScriptContent;
                    return (
                      <li key={s.id} className="flex items-center justify-between gap-2">
                        <span>
                          第 {s.episode_number} 集 · {c.title ?? "未命名"}
                          {c.word_count ? ` · ${c.word_count} 字` : ""}
                        </span>
                        <Link
                          href={`/projects/${projectId}/scripts/${s.episode_number}`}
                          className="text-[var(--accent)] underline"
                        >
                          查看
                        </Link>
                      </li>
                    );
                  })}
              </ul>
            ) : (
              <p className="text-[var(--text-muted)]">暂无剧本。</p>
            )}
          </>
        )}
      </div>
    </details>
  );
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

  const queryClient = useQueryClient();
  // L-3/L-4 确认门：needs_review 且 Run 停在门上 → 查 run.stage_gate 提供续跑选项
  const runQuery = useQuery({
    queryKey: ["gated-run", action?.run_id],
    enabled: action?.status === "needs_review" && !!action?.run_id,
    queryFn: () => runsApi.get(action!.run_id!),
    // 续跑后 Run 回到 queued/running → 轮询至下一门/终态（门按钮随 stage_gate 消失/复现）
    refetchInterval: (query) =>
      query.state.data?.status === "queued" || query.state.data?.status === "running"
        ? 2000
        : false,
  });
  const stageGate =
    action?.status === "needs_review" ? runQuery.data?.stage_gate ?? null : null;
  // 门后续跑中：Action 停在 needs_review 不再变化，但 Run 还在执行——
  // 徽章必须如实显示"执行中"而非"需人工复核"，否则等待终态的界面
  // （含 E2E）会在机器仍在工作时提前放行
  const runResuming =
    action?.status === "needs_review" &&
    (runQuery.data?.status === "queued" ||
      runQuery.data?.status === "running");

  // 门上内嵌预览：SB/大纲/剧本就地可看，不必跳页再回来（门上聊天修订
  // 会产生新版本，轻量轮询保持预览新鲜）
  const gateStoryBible = useQuery({
    queryKey: ["gate-preview", projectId, "story_bible"],
    queryFn: () => artifactsApi.getLatest(projectId, "story_bible"),
    enabled: stageGate === "outline",
    retry: false,
    refetchInterval: 10_000,
  });
  const gateOutline = useQuery({
    queryKey: ["gate-preview", projectId, "episode_outline_set"],
    queryFn: () => artifactsApi.getLatest(projectId, "episode_outline_set"),
    enabled: stageGate === "outline",
    retry: false,
    refetchInterval: 10_000,
  });
  const gateScripts = useQuery({
    queryKey: ["gate-preview", projectId, "script_draft"],
    queryFn: () => artifactsApi.listVersions(projectId, "script_draft"),
    enabled: stageGate === "scripts",
    retry: false,
    refetchInterval: 10_000,
  });

  const continueMutation = useMutation({
    // 每次用户点击生成新幂等键；expected 世代取本组件所见 Run——旧门上的
    // 重复点击由后端收据/世代校验兜底（W1-01：重放不多写一批）
    mutationFn: (batchSize?: number | undefined) =>
      runsApi.continueRun(action!.run_id!, {
        expected_stage_generation: runQuery.data?.stage_generation ?? 0,
        idempotency_key: crypto.randomUUID(),
        ...(batchSize ? { batch_size: batchSize } : {}),
      }),
    onSuccess: () => {
      // Action 停在 needs_review 不再变——门 UI 由 Run 驱动，立即失效以切走按钮
      void queryClient.invalidateQueries({
        queryKey: ["gated-run", action?.run_id],
      });
      void queryClient.invalidateQueries({
        queryKey: ["agent-action", actionId],
      });
      onContinued?.();
    },
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
          {/* 确认门是设计内的阶段暂停：展示"等待确认"；门后 Run 仍在
              执行时展示"执行中"——两者都不是人可操作的终态 */}
          {stageGate
            ? "等待确认"
            : runResuming
              ? "执行中"
              : STATUS_LABEL[action.status] ?? action.status}
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

      {plan.command.intent === "use_external_tool" && (
        <McpToolPlanDetails command={plan.command} />
      )}

      {plan.constraints.length > 0 && (
        <div className="mt-2 text-xs">
          <span className="text-[var(--text-muted)]">约束：</span>
          {plan.constraints.join("；")}
        </div>
      )}

      {plan.user_request && (
        <div className="mt-2 text-xs text-[var(--text-muted)]" data-testid="plan-user-request">
          原始请求：{plan.user_request}
          {/* IR-3 §8.3：确认前可对照原文发现遗漏的约束 */}
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

      {/* L-3/L-4 确认门续跑：Run 停在门上 → 内嵌预览 + 按门类型提供继续选项 */}
      {stageGate === "outline" && (
        <div className="mt-4">
          <GatePreview
            stageGate="outline"
            projectId={projectId}
            storyBible={gateStoryBible.data ?? null}
            outline={gateOutline.data ?? null}
          />
          <p className="mt-2 text-xs text-[var(--text-muted)]">
            不满意可直接在下方对话框输入修改意见（如『把第 2 集冲突提前』），修订确认后再继续创作。
          </p>
          <div className="mt-2 flex flex-col gap-2 sm:flex-row">
            <button
              type="button"
              onClick={() => continueMutation.mutate(1)}
              disabled={continueMutation.isPending}
              data-testid="continue-batch-1"
              className="touch-target rounded-lg border border-[var(--border)] px-4 text-sm text-[var(--text)] transition-state hover:border-[var(--accent)] disabled:opacity-50"
            >
              先写第 1 集
            </button>
            <button
              type="button"
              onClick={() => continueMutation.mutate(5)}
              disabled={continueMutation.isPending}
              data-testid="continue-batch-5"
              className="touch-target rounded-lg border border-[var(--border)] px-4 text-sm text-[var(--text)] transition-state hover:border-[var(--accent)] disabled:opacity-50"
            >
              先写前 5 集
            </button>
            <button
              type="button"
              onClick={() => continueMutation.mutate(undefined)}
              disabled={continueMutation.isPending}
              data-testid="continue-all"
              className="touch-target rounded-lg bg-[var(--accent)] px-4 text-sm font-medium text-white transition-state hover:bg-[var(--accent-hover)] disabled:opacity-50 sm:min-w-32"
            >
              {continueMutation.isPending ? "继续中…" : "写全部剧本"}
            </button>
          </div>
          {continueMutation.isError && (
            <p className="mt-2 text-xs text-[var(--danger)]" role="alert">
              {(continueMutation.error as Error).message}
            </p>
          )}
        </div>
      )}
      {stageGate === "scripts" && (
        <div className="mt-4">
          <GatePreview stageGate="scripts" projectId={projectId} scripts={gateScripts.data ?? []} />
          <p className="mt-2 text-xs text-[var(--text-muted)]" aria-live="polite">
            可继续下一批，或直接在对话框输入修改意见（先在右栏选中要改的集数）。
          </p>
          <div className="mt-2 flex flex-col gap-2 sm:flex-row">
            <button
              type="button"
              onClick={() => continueMutation.mutate(1)}
              disabled={continueMutation.isPending}
              data-testid="continue-batch-1"
              className="touch-target rounded-lg border border-[var(--border)] px-4 text-sm text-[var(--text)] transition-state hover:border-[var(--accent)] disabled:opacity-50"
            >
              下一集
            </button>
            <button
              type="button"
              onClick={() => continueMutation.mutate(5)}
              disabled={continueMutation.isPending}
              data-testid="continue-batch-5"
              className="touch-target rounded-lg border border-[var(--border)] px-4 text-sm text-[var(--text)] transition-state hover:border-[var(--accent)] disabled:opacity-50"
            >
              下 5 集
            </button>
            <button
              type="button"
              onClick={() => continueMutation.mutate(undefined)}
              disabled={continueMutation.isPending}
              data-testid="continue-all"
              className="touch-target rounded-lg bg-[var(--accent)] px-4 text-sm font-medium text-white transition-state hover:bg-[var(--accent-hover)] disabled:opacity-50 sm:min-w-32"
            >
              {continueMutation.isPending ? "继续中…" : "写完剩余全部"}
            </button>
          </div>
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

      {/* 恢复入口：stale / failed / needs_review（确认门除外——门上有专属按钮） */}
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
      {(action.status === "failed" || (action.status === "needs_review" && !stageGate)) && (
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

      {/* 确认门上只保留门按钮：结果/后续建议是噪音，流程就是"确认 → 下一步" */}
      {terminalWithOutcome && action.result && !stageGate && (
        <OutcomeEvidenceView outcome={action.result} projectId={projectId} />
      )}
    </section>
  );
}
