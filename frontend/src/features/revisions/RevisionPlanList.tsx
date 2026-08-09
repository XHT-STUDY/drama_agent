"use client";

/** RevisionPlanList — 修订计划列表组件 (H-06).
 *
 * 渲染项目全部修订计划的卡片列表（点击选中）：
 * - 集数 chip + 计划版本 + 创建时间 + 操作数量
 * - 选中项高亮
 */

import type { Artifact } from "@/types/api";
import type { RevisionPlanContent } from "@/types/api";

interface Props {
  items: Artifact[];
  selectedId: string | null;
  onSelect: (artifactId: string) => void;
}

/** 从 Artifact 提取修订计划 content（防御式，取不到时返回占位） */
export function planContentOf(artifact: Artifact): RevisionPlanContent | null {
  const c = artifact.content as unknown;
  if (c && typeof c === "object" && "episode_number" in c) {
    return c as RevisionPlanContent;
  }
  return null;
}

export function RevisionPlanList({ items, selectedId, onSelect }: Props) {
  if (items.length === 0) {
    return <p className="text-sm text-gray-400">暂无修订记录</p>;
  }

  return (
    <div className="space-y-2">
      {items.map((item) => {
        const plan = planContentOf(item);
        const selected = item.id === selectedId;
        return (
          <button
            key={item.id}
            type="button"
            onClick={() => onSelect(item.id)}
            className={`flex w-full items-center gap-3 rounded-lg border bg-white px-4 py-3 text-left transition-colors ${
              selected
                ? "border-blue-400 ring-2 ring-blue-100"
                : "border-gray-200 hover:border-blue-300"
            }`}
          >
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-blue-100 text-xs font-bold text-blue-700">
              {item.episode_number}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-gray-800">
                  第 {item.episode_number} 集 · 修订计划 v{item.version}
                </span>
                {plan && (
                  <span className="text-xs text-gray-400">
                    {plan.operations?.length ?? 0} 条修订操作
                  </span>
                )}
              </div>
              <p className="mt-0.5 text-xs text-gray-400">
                {new Date(item.created_at).toLocaleString("zh-CN")}
              </p>
            </div>
            {selected && (
              <span className="shrink-0 text-xs font-medium text-blue-600">已选中</span>
            )}
          </button>
        );
      })}
    </div>
  );
}
