"use client";

/** ScoreBar — 单条评分进度条 (H-05).
 *
 * 显示一个维度的分数：
 * - 维度中文标签
 * - 数值 (0-100)
 * - 彩色进度条（分数高低决定颜色）
 *
 * 用于 EvaluationPanel 中展示 9 维评分。
 */

import { EVAL_DIMENSION_LABELS } from "@/types/api";
import type { EvaluationDimension } from "@/types/api";

// ============================================================
// 分数颜色
// ============================================================

/** 根据分数返回进度条颜色 */
function scoreColor(score: number): string {
  if (score >= 80) return "bg-green-500";
  if (score >= 60) return "bg-yellow-500";
  return "bg-red-500";
}

/** 根据分数返回文字颜色 */
function scoreTextColor(score: number): string {
  if (score >= 80) return "text-green-700";
  if (score >= 60) return "text-yellow-700";
  return "text-red-700";
}

// ============================================================
// Props
// ============================================================

interface Props {
  dimension: EvaluationDimension;
  score: number;
}

// ============================================================
// ScoreBar
// ============================================================

export function ScoreBar({ dimension, score }: Props) {
  const label = EVAL_DIMENSION_LABELS[dimension] || dimension;

  return (
    <div className="flex items-center gap-3">
      {/* 维度名称 */}
      <span className="w-24 shrink-0 text-xs font-medium text-gray-600">
        {label}
      </span>

      {/* 进度条 */}
      <div className="flex-1">
        <div className="h-2 w-full overflow-hidden rounded-full bg-gray-100">
          <div
            className={`h-full rounded-full transition-all duration-300 ${scoreColor(score)}`}
            style={{ width: `${Math.max(score, 2)}%` }}
          />
        </div>
      </div>

      {/* 分数 */}
      <span className={`w-8 text-right text-xs font-bold ${scoreTextColor(score)}`}>
        {score}
      </span>
    </div>
  );
}
