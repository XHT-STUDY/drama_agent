"use client";

/** OutlineListView — 分集大纲列表组件 (H-04).
 *
 * 展示完整的分集大纲集合，包括：
 * - 版本选择器（切换历史版本）
 * - 篇章摘要 (arc_summary)
 * - 验证备注 (validation_notes)
 * - 10 集大纲卡片，按 episode_number 稳定排序
 * - 每集可展开查看详情
 */

import { EpisodeCard } from "./EpisodeCard";
import type { Artifact, EpisodeOutlineSetContent } from "@/types/api";

// ============================================================
// Props
// ============================================================

interface Props {
  content: EpisodeOutlineSetContent;
  artifact: Artifact;
  versions: Artifact[];
  onVersionChange: (artifactId: string) => void;
}

// ============================================================
// OutlineListView
// ============================================================

export function OutlineListView({ content, artifact, versions, onVersionChange }: Props) {
  // 集号稳定排序
  const sortedEpisodes = [...(content.episodes || [])].sort(
    (a, b) => a.episode_number - b.episode_number,
  );

  return (
    <div className="space-y-6">
      {/* 版本选择器 */}
      {versions.length > 1 && (
        <div className="flex items-center gap-3 rounded-lg border border-gray-200 bg-white px-4 py-3">
          <label htmlFor="outline-version-select" className="text-sm font-medium text-gray-600">
            版本：
          </label>
          <select
            id="outline-version-select"
            className="rounded border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700 focus:border-blue-400 focus:outline-none"
            value={artifact.id}
            onChange={(e) => onVersionChange(e.target.value)}
          >
            {versions.map((v) => (
              <option key={v.id} value={v.id}>
                v{v.version} — {new Date(v.created_at).toLocaleString("zh-CN")}
                {v.status === "valid" ? " ✓" : v.status === "invalid" ? " ✗" : " ○"}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* 篇章摘要 */}
      {content.arc_summary && (
        <div className="rounded-lg border border-gray-200 bg-white p-5">
          <h2 className="mb-2 text-sm font-semibold text-gray-800">📖 篇章摘要</h2>
          <p className="text-sm leading-relaxed text-gray-700 whitespace-pre-wrap">
            {content.arc_summary}
          </p>
        </div>
      )}

      {/* 大纲列表 */}
      <div>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-gray-800">
            📋 分集大纲 ({sortedEpisodes.length} 集)
          </h2>
        </div>

        {sortedEpisodes.length > 0 ? (
          <div className="space-y-2">
            {sortedEpisodes.map((ep) => (
              <EpisodeCard key={ep.episode_number} outline={ep} />
            ))}
          </div>
        ) : (
          <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 p-8 text-center">
            <p className="text-sm text-gray-500">暂无分集大纲</p>
          </div>
        )}
      </div>

      {/* 验证备注 */}
      {content.validation_notes && content.validation_notes.length > 0 && (
        <div className="rounded-lg border border-gray-200 bg-white p-4">
          <h3 className="mb-2 text-sm font-semibold text-gray-800">✅ 验证备注</h3>
          <ul className="space-y-1">
            {content.validation_notes.map((note, i) => (
              <li key={i} className="text-sm text-gray-600">
                · {note}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 底部元信息 */}
      <div className="border-t border-gray-100 pt-4 text-xs text-gray-400">
        <span>版本 {artifact.version}</span>
        <span className="mx-2">·</span>
        <span>状态：{artifact.status}</span>
        <span className="mx-2">·</span>
        <span>Schema {artifact.content_schema_version}</span>
      </div>
    </div>
  );
}
