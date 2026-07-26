"use client";

/** EpisodeCard — 单集大纲卡片 (H-04).
 *
 * 可展开/折叠的单集大纲卡片，使用原生 <details> 元素避免 React 多实例冲突。
 * 展示：
 * - 集号 + 标题（始终可见）
 * - 展开后：开头钩子、本集目标、核心冲突、关键事件、爽点、结尾钩子、下一集衔接
 * - 引入/解决的伏笔、所需角色
 *
 * 空字段显示明确提示，不会崩溃。
 */

import type { EpisodeOutline } from "@/types/api";

// ============================================================
// 辅助
// ============================================================

/** 带标签的字段 */
function Field({
  label,
  value,
}: {
  label: string;
  value: string | undefined;
}) {
  return (
    <div>
      <dt className="text-xs font-medium text-gray-500">{label}</dt>
      <dd className="mt-0.5 text-sm text-gray-800">
        {value || <span className="text-gray-400 italic">未设置</span>}
      </dd>
    </div>
  );
}

/** 文本列表，空时占位 */
function TextList({
  items,
  emptyText,
}: {
  items: string[] | undefined;
  emptyText: string;
}) {
  if (!items || items.length === 0) {
    return <span className="text-xs text-gray-400 italic">{emptyText}</span>;
  }
  return (
    <ul className="list-inside list-disc space-y-0.5">
      {items.map((item, i) => (
        <li key={i} className="text-sm text-gray-700">
          {item}
        </li>
      ))}
    </ul>
  );
}

// ============================================================
// Props
// ============================================================

interface Props {
  outline: EpisodeOutline;
  defaultExpanded?: boolean;
}

// ============================================================
// EpisodeCard
// ============================================================

export function EpisodeCard({ outline, defaultExpanded = false }: Props) {
  return (
    <details
      className="group rounded-lg border border-gray-200 bg-white transition-shadow hover:shadow-sm"
      open={defaultExpanded}
    >
      {/* 折叠头部：始终可见 */}
      <summary className="flex w-full cursor-pointer items-center gap-3 px-4 py-3 list-none focus:outline-none">
        {/* 集号 */}
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-blue-100 text-sm font-bold text-blue-700">
          {outline.episode_number}
        </span>

        {/* 标题 */}
        <span className="flex-1 text-sm font-semibold text-gray-900">
          {outline.title || <span className="text-gray-400 italic">未命名</span>}
        </span>

        {/* 展开/折叠图标 — 使用 group-open 自动旋转 */}
        <svg
          className="h-4 w-4 shrink-0 text-gray-400 transition-transform group-open:rotate-180"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </summary>

      {/* 展开内容 */}
      <div className="border-t border-gray-100 px-4 pb-4 pt-3">
        <dl className="space-y-3">
          <Field label="🎬 开头钩子" value={outline.opening_hook} />
          <Field label="🎯 本集目标" value={outline.objective} />
          <Field label="⚡ 核心冲突" value={outline.core_conflict} />

          <div>
            <dt className="text-xs font-medium text-gray-500">📋 关键事件</dt>
            <dd className="mt-1">
              <TextList items={outline.key_events} emptyText="暂无关键事件" />
            </dd>
          </div>

          <Field label="✨ 爽点 (Payoff)" value={outline.payoff} />
          <Field label="🔚 结尾钩子" value={outline.ending_hook} />
          {outline.next_bridge && <Field label="🔗 下一集衔接" value={outline.next_bridge} />}

          {/* 伏笔管理 */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <dt className="text-xs font-medium text-gray-500">📌 引入伏笔</dt>
              <dd className="mt-1">
                <TextList items={outline.introduced_loops} emptyText="无" />
              </dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-gray-500">✅ 解决伏笔</dt>
              <dd className="mt-1">
                <TextList items={outline.resolved_loops} emptyText="无" />
              </dd>
            </div>
          </div>

          {/* 所需角色 */}
          <div>
            <dt className="text-xs font-medium text-gray-500">👤 出场角色</dt>
            <dd className="mt-1">
              {outline.required_characters && outline.required_characters.length > 0 ? (
                <div className="flex flex-wrap gap-1">
                  {outline.required_characters.map((name, i) => (
                    <span
                      key={i}
                      className="inline-flex rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-700"
                    >
                      {name}
                    </span>
                  ))}
                </div>
              ) : (
                <span className="text-xs text-gray-400 italic">未指定</span>
              )}
            </dd>
          </div>
        </dl>
      </div>
    </details>
  );
}
