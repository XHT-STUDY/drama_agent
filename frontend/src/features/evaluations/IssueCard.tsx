"use client";

/** IssueCard — 评估问题卡片 (H-05).
 *
 * 展示单个 EvaluationIssue：
 * - 维度标签 + 严重程度标签
 * - 问题诊断
 * - 证据引用
 * - 改进建议
 * - scene_number 可点击跳转（或标注"全局问题"）
 */

import { EVAL_DIMENSION_LABELS, SEVERITY_COLORS } from "@/types/api";
import type { EvaluationIssue } from "@/types/api";

// ============================================================
// Props
// ============================================================

interface Props {
  issue: EvaluationIssue;
  /** 点击 scene 编号时的回调（跳转到对应场景） */
  onLocateScene?: (sceneNumber: number) => void;
}

// ============================================================
// IssueCard
// ============================================================

export function IssueCard({ issue, onLocateScene }: Props) {
  const dimLabel = EVAL_DIMENSION_LABELS[issue.dimension] || issue.dimension;
  const severityColor = SEVERITY_COLORS[issue.severity] || "bg-gray-100 text-gray-700";

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      {/* 头部：维度 + 严重程度 */}
      <div className="mb-2 flex items-center gap-2">
        <span className="inline-flex rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-600">
          {dimLabel}
        </span>
        <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${severityColor}`}>
          {issue.severity === "high" ? "严重" : issue.severity === "medium" ? "中等" : "轻微"}
        </span>

        {/* Scene 定位 */}
        {issue.scene_number != null ? (
          <button
            type="button"
            className="ml-auto inline-flex items-center gap-1 text-xs text-blue-500 hover:text-blue-700 hover:underline"
            onClick={() => onLocateScene?.(issue.scene_number!)}
          >
            定位到第 {issue.scene_number} 场 →
          </button>
        ) : (
          <span className="ml-auto text-xs text-gray-400 italic">全局问题</span>
        )}
      </div>

      {/* 诊断 */}
      <p className="mb-2 text-sm text-gray-800">{issue.diagnosis}</p>

      {/* 证据 */}
      {issue.evidence && (
        <blockquote className="mb-2 border-l-2 border-gray-200 pl-3 text-xs italic text-gray-500">
          {issue.evidence}
        </blockquote>
      )}

      {/* 建议 */}
      {issue.suggestion && (
        <p className="text-xs text-green-700">
          💡 {issue.suggestion}
        </p>
      )}
    </div>
  );
}
