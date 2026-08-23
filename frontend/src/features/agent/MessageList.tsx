"use client";

/** MessageList — 会话消息流（J-11）。
 *
 * 按 kind 渲染：
 * - text：用户/助手正文（role 区分左右）
 * - clarification：澄清问题卡片（无确认按钮——回答靠 Composer）
 * - action_plan：计划卡片（经 renderPlanCard 插槽渲染 ActionPlanCard；
 *   后续计划同样展示但不会自动确认）
 * - action_result：结果摘要（goal_status/评分变化/剩余约束/证据链接）
 * - error：错误提示
 */

import Link from "next/link";
import type { ReactNode } from "react";
import type { ChatMessage } from "@/types/api";

const GOAL_STATUS_LABEL: Record<string, string> = {
  achieved: "已达成",
  partially_achieved: "部分达成",
  blocked: "受阻",
};

interface Props {
  messages: ChatMessage[];
  projectId: string;
  /** action_plan 消息的计划卡插槽（workspace 提供 ActionPlanCard） */
  renderPlanCard?: (actionId: string) => ReactNode;
}

function ResultMessage({ message, projectId }: { message: ChatMessage; projectId: string }) {
  const meta = message.metadata as {
    goal_status?: string;
    score_delta?: number | null;
    remaining_constraints?: string[];
    evidence_artifact_ids?: string[];
  };
  const goal = meta.goal_status ?? "unknown";
  const color =
    goal === "achieved"
      ? "text-[var(--success)]"
      : goal === "partially_achieved"
        ? "text-[var(--warning)]"
        : "text-[var(--danger)]";
  return (
    <div
      className="rounded-lg border border-[var(--border)] bg-[var(--surface-muted)] p-3"
      data-testid="result-message"
    >
      <p className="message-body font-medium">
        {message.content.split("\n")[0]}（
        <span className={color} data-testid="result-goal-status">
          {GOAL_STATUS_LABEL[goal] ?? goal}
        </span>
        ）
      </p>
      {typeof meta.score_delta === "number" && (
        <p className="mt-1 text-xs text-[var(--text-muted)]" data-testid="result-score-delta">
          评分变化 {meta.score_delta > 0 ? "+" : ""}
          {meta.score_delta.toFixed(1)}
        </p>
      )}
      {meta.remaining_constraints && meta.remaining_constraints.length > 0 && (
        <ul className="mt-1 list-disc pl-5 text-xs text-[var(--warning)]" data-testid="result-remaining">
          {meta.remaining_constraints.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
      )}
      {meta.evidence_artifact_ids && meta.evidence_artifact_ids.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-2" data-testid="result-evidence">
          {meta.evidence_artifact_ids.slice(0, 8).map((id) => (
            <Link
              key={id}
              href={`/projects/${projectId}/versions?artifact=${id}`}
              className="rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-xs text-[var(--accent)] transition-state hover:border-[var(--accent)]"
            >
              产物 {id.slice(0, 8)}…
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

export function MessageList({ messages, projectId, renderPlanCard }: Props) {
  return (
    <div className="space-y-3" aria-label="消息历史">
      {messages.map((message) => {
        const meta = message.metadata as { agent_action_id?: string };
        if (message.kind === "clarification") {
          return (
            <div
              key={message.id}
              className="rounded-lg border border-[var(--warning)] bg-[var(--warning-bg)] p-3"
              data-testid="clarification-message"
            >
              <p className="text-xs font-medium text-[var(--warning)]">需要补充信息</p>
              <p className="message-body mt-1 whitespace-pre-wrap">{message.content}</p>
              {/* 澄清没有确认按钮：用户用输入框回答 */}
            </div>
          );
        }
        if (message.kind === "action_result") {
          return (
            <div key={message.id} className="flex justify-start">
              <div className="max-w-full">
                <ResultMessage message={message} projectId={projectId} />
              </div>
            </div>
          );
        }
        if (message.kind === "action_plan") {
          return (
            <div key={message.id} data-testid="action-plan-message">
              {meta.agent_action_id && renderPlanCard ? (
                renderPlanCard(meta.agent_action_id)
              ) : (
                <p className="message-body whitespace-pre-wrap">{message.content}</p>
              )}
            </div>
          );
        }
        if (message.kind === "error") {
          return (
            <div
              key={message.id}
              className="rounded-lg border border-[var(--danger)] bg-[var(--danger-bg)] p-3 text-sm text-[var(--danger)]"
              role="alert"
            >
              {message.content}
            </div>
          );
        }
        const isUser = message.role === "user";
        return (
          <div key={message.id} className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
            <div
              className={`max-w-[85%] rounded-lg px-3 py-2 ${
                isUser
                  ? "bg-[var(--accent)] text-white"
                  : "border border-[var(--border)] bg-[var(--surface)]"
              }`}
            >
              <p className="message-body whitespace-pre-wrap">{message.content}</p>
            </div>
          </div>
        );
      })}
    </div>
  );
}
