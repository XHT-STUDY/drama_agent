"use client";

/** ConversationPanel — 左栏：会话选择 + 消息流 + Composer + 内嵌进度（J-11）。
 *
 * 会话选择：项目会话列表 + 新建会话；当前会话由父组件管理。
 */

import type { ReactNode } from "react";
import type { Conversation } from "@/types/api";

interface Props {
  conversations: Conversation[];
  currentId: string | null;
  onSelect: (id: string) => void;
  onCreate: () => void;
  creating: boolean;
  children: ReactNode;
}

export function ConversationPanel({
  conversations,
  currentId,
  onSelect,
  onCreate,
  creating,
  children,
}: Props) {
  return (
    <section aria-label="对话" className="flex min-h-0 flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">对话</h2>
        <button
          type="button"
          onClick={onCreate}
          disabled={creating}
          data-testid="new-conversation"
          className="touch-target rounded-lg border border-[var(--border)] px-3 text-xs text-[var(--text-muted)] transition-state hover:border-[var(--accent)] hover:text-[var(--accent)] disabled:opacity-50"
        >
          + 新会话
        </button>
      </div>

      {conversations.length > 1 && (
        <select
          value={currentId ?? ""}
          onChange={(e) => onSelect(e.target.value)}
          aria-label="选择会话"
          className="rounded-lg border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-xs"
          data-testid="conversation-select"
        >
          {conversations.map((c) => (
            <option key={c.id} value={c.id}>
              {c.title || "未命名会话"}
            </option>
          ))}
        </select>
      )}

      {children}
    </section>
  );
}
