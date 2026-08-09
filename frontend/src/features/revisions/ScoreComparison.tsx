"use client";

/** ScoreComparison — 重新评分对比组件 (H-06).
 *
 * 对比原稿评估与修订稿评估：
 * - 两个大分数 + 中间 delta 徽章（提升绿 / 下降红 / 持平灰）
 * - 每列复用 ScoreBar 渲染 9 维
 * - 分数下降明确显示为「↓ 下降」，绝不包装成提升
 */

import { DEFAULT_EVALUATION_WEIGHTS } from "@/types/api";
import type { EvaluationReportContent } from "@/types/api";
import { ScoreBar } from "@/features/evaluations/ScoreBar";

/** 纯函数：修订分相对原稿分的变化（正=提升，负=下降） */
export function scoreDelta(source: number, revised: number): number {
  return revised - source;
}

interface Props {
  source: EvaluationReportContent;
  revised: EvaluationReportContent;
}

export function ScoreComparison({ source, revised }: Props) {
  const delta = scoreDelta(source.overall_score, revised.overall_score);

  const deltaBadge =
    delta > 0 ? (
      <span className="inline-flex items-center rounded bg-green-100 px-2 py-1 text-sm font-bold text-green-700">
        ↑ 提升 +{Math.round(delta)} 分
      </span>
    ) : delta < 0 ? (
      <span className="inline-flex items-center rounded bg-red-100 px-2 py-1 text-sm font-bold text-red-700">
        ↓ 下降 {Math.round(Math.abs(delta))} 分
      </span>
    ) : (
      <span className="inline-flex items-center rounded bg-gray-100 px-2 py-1 text-sm font-bold text-gray-500">
        持平
      </span>
    );

  const dimensions = Object.keys(DEFAULT_EVALUATION_WEIGHTS) as Array<
    keyof typeof DEFAULT_EVALUATION_WEIGHTS
  >;

  const col = (label: string, report: EvaluationReportContent) => (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="mb-3 flex items-baseline justify-between">
        <span className="text-sm font-medium text-gray-600">{label}</span>
        <span className="text-2xl font-bold text-gray-900">
          {Math.round(report.overall_score)}
        </span>
      </div>
      <div className="space-y-2">
        {dimensions.map((dim) => (
          <ScoreBar key={dim} dimension={dim} score={report.dimension_scores[dim] ?? 0} />
        ))}
      </div>
    </div>
  );

  return (
    <div className="space-y-3">
      {/* delta 徽章 */}
      <div className="flex justify-center">{deltaBadge}</div>

      {/* 两列对比 */}
      <div className="grid gap-4 md:grid-cols-2">
        {col("原稿评分", source)}
        {col("修订稿评分", revised)}
      </div>
    </div>
  );
}
