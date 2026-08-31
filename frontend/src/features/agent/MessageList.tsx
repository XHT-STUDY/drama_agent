"use client";

/** MessageList — 会话消息流（J-11）。
 *
 * 按 kind 渲染：
 * - text：用户/助手正文（role 区分左右）
 * - clarification：澄清问题卡片（无确认按钮——回答靠 Composer）
 * - action_plan：计划卡片（经 renderPlanCard 插槽渲染 ActionPlanCard；
 *   后续计划同样展示但不会自动确认）
 * - action_result：结果摘要（goal_status/评分变化；stage_gate 时为最小化阶段卡）
 * - error：错误提示
 * 发送中：乐观渲染用户消息 + "正在思考"气泡（聊天感）
 */

import type { ReactNode } from "react";
import type { ChatMessage } from "@/types/api";

interface Props {
  messages: ChatMessage[];
  /** action_plan 消息的计划卡插槽（workspace 提供 ActionPlanCard） */
  renderPlanCard?: (actionId: string) => ReactNode;
  /** 发送中尚未上屏的用户消息（乐观渲染） */
  pendingContent?: string | null;
}

function ResultMessage({ message }: { message: ChatMessage }) {
  const meta = message.metadata as {
    goal_status?: string;
    score_delta?: number | null;
    remaining_constraints?: string[];
    stage_gate?: string;
  };
  // 确认门是设计内的阶段暂停：只渲染干净的阶段进展文案，不带状态术语
  if (meta.stage_gate) {
    return (
      <div className="flex justify-start">
        <div className="max-w-[85%] rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2">
          <p className="message-body whitespace-pre-wrap">{message.content}</p>
        </div>
      </div>
    );
  }
  const goal = meta.goal_status ?? "unknown";
  // 未完成约束：blocked 全量展示；partially_achieved 多为"未自动确认"
  // 的语义要求，只给计数不给清单（把用户要求当失败陈列是噪音）
  const constraints = meta.remaining_constraints ?? [];
  const showConstraints = goal === "blocked" && constraints.length > 0;
  const showPartialCount =
    goal === "partially_achieved" && constraints.length > 0;
  return (
    <div className="flex justify-start">
      <div
        className="max-w-[85%] rounded-lg border border-[var(--border)] bg-[var(--surface-muted)] px-3 py-2"
        data-testid="result-message"
      >
        <p className="message-body font-medium">
          {message.content.split("\n")[0]}
        </p>
        {typeof meta.score_delta === "number" && (
          <p className="mt-1 text-xs text-[var(--text-muted)]" data-testid="result-score-delta">
            评分变化 {meta.score_delta > 0 ? "+" : ""}
            {meta.score_delta.toFixed(1)}
          </p>
        )}
        {showConstraints && (
          <ul className="mt-1 list-disc pl-5 text-xs text-[var(--warning)]" data-testid="result-remaining">
            {constraints.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        )}
        {showPartialCount && (
          <p className="mt-1 text-xs text-[var(--text-muted)]" data-testid="result-partial-count">
            有 {constraints.length} 项修改要求未能自动确认，已给出后续修订建议。
          </p>
        )}
      </div>
    </div>
  );
}

export function MessageList({ messages, renderPlanCard, pendingContent }: Props) {
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
          return <ResultMessage key={message.id} message={message} />;
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

      {/* 发送中：用户消息乐观上屏 + LLM 式"正在思考"指示（打字点动画） */}
      {pendingContent && (
        <>
          <div className="flex justify-end" data-testid="pending-user-message">
            <div className="max-w-[85%] rounded-lg bg-[var(--accent)] px-3 py-2 text-white opacity-90">
              <p className="message-body whitespace-pre-wrap">{pendingContent}</p>
            </div>
          </div>
          <div className="flex items-center gap-2 pl-1" data-testid="agent-typing">
            <span className="flex gap-1">
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-[var(--text-muted)] [animation-delay:-0.3s]" />
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-[var(--text-muted)] [animation-delay:-0.15s]" />
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-[var(--text-muted)]" />
            </span>
            <span className="text-xs text-[var(--text-muted)]">正在思考</span>
          </div>
        </>
      )}
    </div>
  );
}
