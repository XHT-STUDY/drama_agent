"use client";

/** EpisodeNav — 集数导航侧栏 (H-05).
 *
 * 左侧集数列表：
 * - 显示所有集的编号和标题（如果有）
 * - 当前选中集高亮
 * - 点击切换集数
 * - 显示剧本/评估状态图标
 */

import React from "react";

// ============================================================
// Props
// ============================================================

export interface EpisodeNavItem {
  episode_number: number;
  title?: string;
  /** 剧本是否存在 */
  hasScript: boolean;
  /** 评估报告是否存在 */
  hasEvaluation: boolean;
}

interface Props {
  episodes: EpisodeNavItem[];
  currentEpisode: number;
  targetCount: number;
  onSelect: (episode: number) => void;
}

// ============================================================
// 状态图标
// ============================================================

function StatusIcon({ hasScript, hasEvaluation }: { hasScript: boolean; hasEvaluation: boolean }) {
  if (hasEvaluation) {
    return (
      <span className="flex h-4 w-4 shrink-0 items-center justify-center text-xs" title="已完成评估">
        ✓
      </span>
    );
  }
  if (hasScript) {
    return (
      <span className="flex h-4 w-4 shrink-0 items-center justify-center text-xs text-blue-500" title="已完成剧本">
        ●
      </span>
    );
  }
  return (
    <span className="flex h-4 w-4 shrink-0 items-center justify-center text-xs text-gray-300" title="未生成">
      ○
    </span>
  );
}

// ============================================================
// EpisodeNav
// ============================================================

export function EpisodeNav({ episodes, currentEpisode, targetCount, onSelect }: Props) {
  // 将已有集数映射为快速查找
  const epMap = new Map(episodes.map((e) => [e.episode_number, e]));

  // 补全所有集数（1～targetCount）
  const allEpisodes: EpisodeNavItem[] = Array.from({ length: targetCount }, (_, i) => {
    const num = i + 1;
    return epMap.get(num) || { episode_number: num, hasScript: false, hasEvaluation: false };
  });

  return (
    <nav className="w-full" aria-label="集数导航">
      <h3 className="mb-2 text-xs font-semibold text-gray-500 uppercase tracking-wide">
        集数 ({allEpisodes.length})
      </h3>
      <ul className="space-y-0.5">
        {allEpisodes.map((ep) => {
          const isActive = ep.episode_number === currentEpisode;
          return (
            <li key={ep.episode_number}>
              <button
                type="button"
                className={`flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors ${
                  isActive
                    ? "bg-blue-100 text-blue-700 font-semibold"
                    : "text-gray-600 hover:bg-gray-100"
                }`}
                onClick={() => onSelect(ep.episode_number)}
              >
                <StatusIcon hasScript={ep.hasScript} hasEvaluation={ep.hasEvaluation} />
                <span className="flex-1 text-left">
                  第 {ep.episode_number} 集
                </span>
                {ep.title && (
                  <span className="max-w-[120px] truncate text-xs text-gray-400">
                    {ep.title}
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
