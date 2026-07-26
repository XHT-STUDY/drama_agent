"use client";

/** EvaluationPanel — 评估报告面板 (H-05).
 *
 * 右侧区域，展示：
 * - 总分（大数字 + 圆环）
 * - 版本绑定（script_artifact_id + rubric_version）
 * - 9 维评分进度条
 * - strengths 列表
 * - issues 列表（可点击定位 scene）
 * - risk_flags 风险标记
 * - 重新评估按钮
 * - need_revision 提示
 *
 * 状态覆盖：评估中 / 失败 / 无报告
 */

import { ScoreBar } from "./ScoreBar";
import { IssueCard } from "./IssueCard";
import { DEFAULT_EVALUATION_WEIGHTS } from "@/types/api";
import type { EvaluationReportContent, EvaluationDimension } from "@/types/api";

// ============================================================
// 总分圆环
// ============================================================

function ScoreRing({ score }: { score: number }) {
  const radius = 28;
  const circumference = 2 * Math.PI * radius;
  const progress = (score / 100) * circumference;

  let ringColor = "stroke-red-500";
  if (score >= 80) ringColor = "stroke-green-500";
  else if (score >= 60) ringColor = "stroke-yellow-500";

  return (
    <div className="flex flex-col items-center">
      <div className="relative">
        <svg className="h-20 w-20 -rotate-90" viewBox="0 0 64 64">
          <circle
            cx="32" cy="32" r={radius}
            fill="none" stroke="#e5e7eb" strokeWidth="6"
          />
          <circle
            cx="32" cy="32" r={radius}
            fill="none"
            className={`${ringColor} transition-all duration-700`}
            strokeWidth="6"
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={circumference - progress}
          />
        </svg>
        <span className="absolute inset-0 flex items-center justify-center text-lg font-bold text-gray-800">
          {Math.round(score)}
        </span>
      </div>
      <span className="mt-1 text-xs text-gray-500">总评分</span>
    </div>
  );
}

// ============================================================
// Props
// ============================================================

interface Props {
  report: EvaluationReportContent | null;
  isLoading: boolean;
  isError: boolean;
  /** 点击重新评估 */
  onReEvaluate?: () => void;
  /** 评估中状态 */
  isEvaluating?: boolean;
  /** 点击 issue 定位 scene */
  onLocateScene?: (sceneNumber: number) => void;
  /** 重试加载 */
  onRetry?: () => void;
}

// ============================================================
// EvaluationPanel
// ============================================================

export function EvaluationPanel({
  report,
  isLoading,
  isError,
  onReEvaluate,
  isEvaluating = false,
  onLocateScene,
  onRetry,
}: Props) {
  // ---- 加载中 ----
  if (isLoading || isEvaluating) {
    return (
      <aside className="rounded-lg border border-gray-200 bg-white p-5">
        <div className="flex flex-col items-center py-8">
          <svg className="mb-3 h-8 w-8 animate-spin text-blue-500" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          <p className="text-sm text-gray-500">
            {isEvaluating ? "评估进行中…" : "加载评估报告…"}
          </p>
        </div>
      </aside>
    );
  }

  // ---- 错误 ----
  if (isError) {
    return (
      <aside className="rounded-lg border border-red-200 bg-red-50 p-5">
        <div className="text-center">
          <p className="mb-2 text-sm text-red-600">评估报告加载失败</p>
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="rounded border border-red-300 px-3 py-1 text-xs text-red-600 hover:bg-red-100"
            >
              重试
            </button>
          )}
        </div>
      </aside>
    );
  }

  // ---- 无报告 ----
  if (!report) {
    return (
      <aside className="rounded-lg border border-dashed border-gray-300 bg-gray-50 p-5">
        <div className="text-center">
          <p className="mb-1 text-sm text-gray-500">暂无评估报告</p>
          <p className="mb-3 text-xs text-gray-400">剧本完成后可发起评估</p>
          {onReEvaluate && (
            <button
              type="button"
              onClick={onReEvaluate}
              className="rounded bg-blue-500 px-4 py-1.5 text-xs font-medium text-white hover:bg-blue-600 transition-colors"
            >
              发起评估
            </button>
          )}
        </div>
      </aside>
    );
  }

  // ---- 正常展示 ----
  return (
    <aside className="space-y-4">
      {/* 总分与版本信息 */}
      <div className="rounded-lg border border-gray-200 bg-white p-5">
        <div className="flex items-center justify-between">
          <ScoreRing score={report.overall_score} />
          <div className="text-right text-xs text-gray-400">
            <p>版本 {report.rubric_version}</p>
            {report.need_revision && (
              <span className="inline-flex items-center gap-1 mt-1 rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700">
                ⚠️ 需修订
              </span>
            )}
          </div>
        </div>

        {/* 重新评估按钮 */}
        {onReEvaluate && (
          <button
            type="button"
            onClick={onReEvaluate}
            disabled={isEvaluating}
            className="mt-3 w-full rounded border border-gray-300 bg-white py-1.5 text-xs text-gray-600 hover:bg-gray-50 disabled:opacity-50 transition-colors"
          >
            {isEvaluating ? "评估中…" : "🔄 重新评估"}
          </button>
        )}
      </div>

      {/* 风险标记 — 明显展示 */}
      {report.risk_flags && report.risk_flags.length > 0 && (
        <div className="rounded-lg border-2 border-red-300 bg-red-50 p-4">
          <h4 className="mb-2 text-xs font-semibold text-red-700">🚨 风险标记</h4>
          <ul className="space-y-1">
            {report.risk_flags.map((flag, i) => (
              <li key={i} className="text-xs text-red-600">
                · {flag}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 9 维评分 */}
      <div className="rounded-lg border border-gray-200 bg-white p-4">
        <h4 className="mb-3 text-xs font-semibold text-gray-700">📊 维度评分</h4>
        <div className="space-y-2">
          {Object.entries(DEFAULT_EVALUATION_WEIGHTS).map(([dim]) => {
            const dimension = dim as EvaluationDimension;
            const score = report.dimension_scores[dimension] ?? 0;
            return (
              <ScoreBar key={dimension} dimension={dimension} score={score} />
            );
          })}
        </div>
      </div>

      {/* 亮点 */}
      <div className="rounded-lg border border-gray-200 bg-white p-4">
        <h4 className="mb-2 text-xs font-semibold text-gray-700">✨ 亮点</h4>
        {report.strengths && report.strengths.length > 0 ? (
          <ul className="space-y-1">
            {report.strengths.map((s, i) => (
              <li key={i} className="text-sm text-gray-600">
                · {s}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-gray-400 italic">暂无亮点</p>
        )}
      </div>

      {/* 问题列表 */}
      <div className="rounded-lg border border-gray-200 bg-white p-4">
        <h4 className="mb-3 text-xs font-semibold text-gray-700">
          🐛 问题 ({report.issues?.length ?? 0})
        </h4>
        {report.issues && report.issues.length > 0 ? (
          <div className="space-y-2">
            {report.issues.map((issue) => (
              <IssueCard
                key={issue.issue_id}
                issue={issue}
                onLocateScene={onLocateScene}
              />
            ))}
          </div>
        ) : (
          <p className="text-sm text-gray-400 italic">无问题</p>
        )}
      </div>

      {/* 修订建议 */}
      {report.revision_suggestions && report.revision_suggestions.length > 0 && (
        <div className="rounded-lg border border-gray-200 bg-white p-4">
          <h4 className="mb-2 text-xs font-semibold text-gray-700">💡 修订建议</h4>
          <ul className="space-y-1">
            {report.revision_suggestions.map((s, i) => (
              <li key={i} className="text-sm text-gray-600">
                · {s}
              </li>
            ))}
          </ul>
        </div>
      )}
    </aside>
  );
}
