"use client";

/** AgentComposer — 对话输入区（J-11）。
 *
 * 由 ChatInput 迁移首次创作能力：
 * - Enter 发送 / Shift+Enter 换行；发送中禁用
 * - 保留目标集数设置（首次创作提示附加到消息，服务端 Planner 解析）
 * - 失败时恢复草稿（lastFailedContent），重发复用幂等 key（Hook 保证）
 * - aria-live 状态播报；空会话命令示例由父组件传入
 */

import { useEffect, useRef, useState, type KeyboardEvent } from "react";

export const COMMAND_EXAMPLES = [
  "创建剧本：一个被青训队抛弃的足球少年逆袭故事",
  "解释当前项目的大纲",
  "评估项目",
  "评估第 1 集",
] as const;

interface Props {
  sending: boolean;
  sendError: string | null;
  failedContent: string | null;
  onSend: (content: string, options?: { episodeCount?: number }) => void;
  /** 空会话示例（点击填入输入框） */
  examples?: readonly string[];
  /** 首次创作保留的目标集数设置 */
  defaultEpisodeCount?: number;
  /** 澄清/重新发起时聚焦输入框 */
  focusSignal?: number;
}

export function AgentComposer({
  sending,
  sendError,
  failedContent,
  onSend,
  examples = COMMAND_EXAMPLES,
  defaultEpisodeCount = 3,
  focusSignal = 0,
}: Props) {
  const [draft, setDraft] = useState("");
  const [episodeCount, setEpisodeCount] = useState(defaultEpisodeCount);
  const [showSettings, setShowSettings] = useState(false);
  // 用户显式调整过集数 → 发送时把目标集数附加为提示（Planner/服务端解析）
  const [settingsTouched, setSettingsTouched] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // 发送失败 → 恢复用户草稿
  useEffect(() => {
    if (failedContent) setDraft(failedContent);
  }, [failedContent]);

  // 澄清 / stale 恢复入口 → 聚焦
  useEffect(() => {
    if (focusSignal > 0) textareaRef.current?.focus();
  }, [focusSignal]);

  function submit() {
    const content = draft.trim();
    if (!content || sending) return;
    // 集数（L-1，显式调整时携带）结构化传给服务端；分阶段已为默认流程
    onSend(content, settingsTouched ? { episodeCount } : undefined);
    setDraft("");
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  }

  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-3 shadow-sm">
      {examples.length > 0 && draft === "" && !sending && (
        <div className="mb-2 flex flex-wrap gap-2" data-testid="command-examples">
          {examples.map((example) => (
            <button
              key={example}
              type="button"
              onClick={() => {
                setDraft(example);
                textareaRef.current?.focus();
              }}
              className="rounded-full border border-[var(--border)] px-3 py-1 text-xs text-[var(--text-muted)] transition-state hover:border-[var(--accent)] hover:text-[var(--accent)]"
            >
              {example}
            </button>
          ))}
        </div>
      )}

      <textarea
        ref={textareaRef}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={handleKeyDown}
        rows={3}
        maxLength={4000}
        disabled={sending}
        aria-label="输入创作指令"
        placeholder="描述你的创作或修改需求…（Enter 发送，Shift+Enter 换行）"
        className="message-body w-full resize-none rounded-lg border border-[var(--border)] px-3 py-2 focus:border-[var(--accent)] focus:outline-none disabled:opacity-60"
      />

      <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
        <button
          type="button"
          onClick={() => setShowSettings((v) => !v)}
          className="text-xs text-[var(--text-muted)] underline transition-state hover:text-[var(--accent)]"
          aria-expanded={showSettings}
        >
          创作设置
        </button>
        <span aria-live="polite" className="text-xs text-[var(--text-muted)]" data-testid="composer-status">
          {sending ? "Agent 正在思考…" : draft.length > 0 ? `${draft.length} 字符` : ""}
          {sendError ? ` ${sendError}` : ""}
        </span>
        <button
          type="button"
          onClick={submit}
          disabled={sending || draft.trim().length === 0}
          data-testid="composer-send"
          className="touch-target rounded-lg bg-[var(--accent)] px-4 text-sm font-medium text-white transition-state hover:bg-[var(--accent-hover)] disabled:opacity-50 sm:min-w-24"
        >
          发送
        </button>
      </div>

      {showSettings && (
        <div className="mt-2 flex items-center gap-2 border-t border-[var(--border)] pt-2 text-xs text-[var(--text-muted)]">
          <span>目标集数：</span>
          <input
            type="number"
            min={1}
            max={50}
            value={episodeCount}
            onChange={(e) => {
              const v = Number(e.target.value);
              if (v >= 1 && v <= 50) {
                setEpisodeCount(v);
                setSettingsTouched(true);
              }
            }}
            aria-label="目标集数"
            className="w-20 rounded border border-[var(--border)] px-2 py-1 text-xs"
          />
          <span>集（首次创作按此集数生成大纲与剧本；未调整时用系统默认）</span>
        </div>
      )}
    </div>
  );
}
