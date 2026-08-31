"use client";

/** EvaluationMatrix — 证据锚定的评分矩阵 (可解释性 v2).
 *
 * 把"每个维度一个分数"升级为可验证的解释结构：
 * - 9 维 × 5 档矩阵：每行一个维度（含权重），5 格档位条，
 *   当前定位档位高亮并标注分数；
 * - 展开行显示评估明细：命中锚点描述、定位理由（含相邻档对比）、
 *   可观察信号、原文证据卡片（点击跳转场次）；
 * - 总分推导条：overall = Σ(维度分 × 权重)，逐维贡献可视化，
 *   并标出 need_revision 的触发规则；
 * - 未通过溯源校验的证据以琥珀色"未验证"标记。
 *
 * 旧版报告（无 dimension_assessments）由 EvaluationPanel 降级为
 * ScoreBar 展示，本组件不做兜底。
 */

import { useState } from "react";
import {
  DEFAULT_EVALUATION_WEIGHTS,
  EVAL_DIMENSION_LABELS,
  EVAL_LEVEL_BANDS,
  EVAL_LEVEL_LABELS,
  EVAL_SCORE_RULES,
} from "@/types/api";
import type {
  DimensionAssessment,
  EvaluationDimension,
  EvaluationEvidenceCite,
  EvaluationReportContent,
} from "@/types/api";

// ============================================================
// 常量
// ============================================================

const LEVELS = [1, 2, 3, 4, 5] as const;

/** 档位条单元格颜色（未命中为灰阶，命中按分数档配色） */
function levelCellColor(level: number, active: boolean): string {
  if (!active) return "bg-gray-100 text-gray-400";
  if (level >= 4) return "bg-green-500 text-white";
  if (level === 3) return "bg-yellow-500 text-white";
  return "bg-red-500 text-white";
}

// ============================================================
// 子组件：证据卡片
// ============================================================

function EvidenceCard({
  cite,
  onLocateScene,
}: {
  cite: EvaluationEvidenceCite;
  onLocateScene?: (sceneNumber: number) => void;
}) {
  const jumpable = cite.scene_number !== null && onLocateScene;
  return (
    <div
      className={`rounded border p-2 text-xs ${
        cite.verified
          ? "border-gray-200 bg-gray-50"
          : "border-amber-300 bg-amber-50"
      }`}
    >
      <blockquote className="mb-1 border-l-2 border-gray-300 pl-2 text-gray-700">
        “{cite.quote}”
      </blockquote>
      <div className="flex items-center gap-2">
        <span className={cite.verified ? "text-green-700" : "text-amber-700"}>
          {cite.verified ? "✓ 已溯源" : "⚠ 未验证引用"}
        </span>
        {cite.scene_number !== null ? (
          jumpable ? (
            <button
              type="button"
              onClick={() => onLocateScene?.(cite.scene_number as number)}
              className="text-blue-600 underline-offset-2 hover:underline"
            >
              第 {cite.scene_number} 场 ↗
            </button>
          ) : (
            <span className="text-gray-500">第 {cite.scene_number} 场</span>
          )
        ) : (
          <span className="text-gray-400">全集性证据</span>
        )}
      </div>
    </div>
  );
}

// ============================================================
// 子组件：单维度行（可展开）
// ============================================================

function DimensionRow({
  dimension,
  score,
  assessment,
  onLocateScene,
}: {
  dimension: EvaluationDimension;
  score: number;
  assessment: DimensionAssessment;
  onLocateScene?: (sceneNumber: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const weight = DEFAULT_EVALUATION_WEIGHTS[dimension];
  const contribution = score * weight;
  const band = EVAL_LEVEL_BANDS[assessment.level];

  const signalEntries = Object.entries(assessment.signals ?? {});

  return (
    <div className="border-b border-gray-100 last:border-b-0">
      {/* 行头：维度 / 权重 / 档位条 / 分数 */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 py-2 text-left hover:bg-gray-50"
        aria-expanded={open}
      >
        <span className="w-20 shrink-0 text-xs font-medium text-gray-700">
          {EVAL_DIMENSION_LABELS[dimension]}
        </span>
        <span className="w-10 shrink-0 text-[10px] text-gray-400">
          ×{weight.toFixed(2)}
        </span>
        <span className="flex flex-1 gap-0.5">
          {LEVELS.map((lv) => (
            <span
              key={lv}
              className={`flex h-4 flex-1 items-center justify-center rounded-sm text-[10px] font-medium ${levelCellColor(
                lv,
                lv === assessment.level,
              )}`}
            >
              {lv}
            </span>
          ))}
        </span>
        <span
          className={`w-8 shrink-0 text-right text-xs font-bold ${
            score >= 80
              ? "text-green-700"
              : score >= 60
                ? "text-yellow-700"
                : "text-red-700"
          }`}
        >
          {score}
        </span>
        <span className="w-4 shrink-0 text-center text-[10px] text-gray-400">
          {open ? "▾" : "▸"}
        </span>
      </button>

      {/* 展开区：锚点 / 理由 / 信号 / 证据 / 加权贡献 */}
      {open && (
        <div className="space-y-2 pb-3 pl-1 pr-1">
          <p className="text-xs text-gray-600">
            <span className="font-medium text-gray-800">
              {assessment.level} 档 · {EVAL_LEVEL_LABELS[assessment.level]}（{band[0]}-{band[1]}分）
            </span>
            <span className="mx-1 text-gray-300">|</span>
            {assessment.matched_anchor}
          </p>
          <p className="text-xs leading-relaxed text-gray-500">
            {assessment.rationale}
          </p>
          {signalEntries.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {signalEntries.map(([k, v]) => (
                <span
                  key={k}
                  className="rounded bg-blue-50 px-1.5 py-0.5 text-[10px] text-blue-700"
                >
                  {k}: {String(v)}
                </span>
              ))}
            </div>
          )}
          {assessment.evidence.length > 0 && (
            <div className="space-y-1">
              {assessment.evidence.map((cite, i) => (
                <EvidenceCard key={i} cite={cite} onLocateScene={onLocateScene} />
              ))}
            </div>
          )}
          <p className="text-[10px] text-gray-400">
            加权贡献：{score} × {weight.toFixed(2)} = {contribution.toFixed(1)} 分
          </p>
        </div>
      )}
    </div>
  );
}

// ============================================================
// 子组件：总分推导
// ============================================================

function ScoreDerivation({ report }: { report: EvaluationReportContent }) {
  const triggers: string[] = [];
  if (report.overall_score < EVAL_SCORE_RULES.revision_threshold) {
    triggers.push(
      `总分 ${report.overall_score} < ${EVAL_SCORE_RULES.revision_threshold}`,
    );
  }
  if (report.issues.some((i) => i.severity === "high")) {
    triggers.push("存在 severity=high 的问题");
  }
  const compliance = report.dimension_scores.compliance_safety;
  if (compliance < EVAL_SCORE_RULES.compliance_threshold) {
    triggers.push(
      `合规安全 ${compliance} < ${EVAL_SCORE_RULES.compliance_threshold}`,
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex h-3 w-full overflow-hidden rounded-full bg-gray-100">
        {(Object.keys(EVAL_DIMENSION_LABELS) as EvaluationDimension[]).map(
          (dim) => {
            const score = report.dimension_scores[dim] ?? 0;
            const w = DEFAULT_EVALUATION_WEIGHTS[dim];
            return (
              <div
                key={dim}
                title={`${EVAL_DIMENSION_LABELS[dim]}：${score} × ${w.toFixed(2)} = ${(score * w).toFixed(1)}分`}
                className={`h-full ${
                  score >= 80
                    ? "bg-green-400"
                    : score >= 60
                      ? "bg-yellow-400"
                      : "bg-red-400"
                }`}
                style={{ width: `${score * w}%` }}
              />
            );
          },
        )}
      </div>
      <p className="text-[10px] text-gray-400">
        总分 = Σ(维度分 × 权重)，分段颜色为各维度评分档；悬停查看单维贡献
      </p>
      {report.need_revision && triggers.length > 0 && (
        <p className="text-[10px] text-red-600">
          ⚠️ 触发修订规则：{triggers.join("；")}
        </p>
      )}
    </div>
  );
}

// ============================================================
// 主组件
// ============================================================

interface Props {
  report: EvaluationReportContent;
  /** 点击证据定位 scene（与 IssueCard 的定位机制一致） */
  onLocateScene?: (sceneNumber: number) => void;
}

export function EvaluationMatrix({ report, onLocateScene }: Props) {
  const assessments = report.dimension_assessments ?? {};

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <h4 className="mb-1 text-xs font-semibold text-gray-700">
        📊 评分矩阵（9 维 × 5 档）
      </h4>
      <p className="mb-3 text-[10px] text-gray-400">
        Rubric v{report.rubric_version} · 点击维度行展开定位理由与原文证据
      </p>

      <ScoreDerivation report={report} />

      <div className="mt-3">
        {(Object.keys(EVAL_DIMENSION_LABELS) as EvaluationDimension[]).map(
          (dim) => {
            const assessment = assessments[dim];
            const score = report.dimension_scores[dim] ?? 0;
            if (!assessment) {
              // 个别维度缺明细（理论不该发生）——仅显示分数
              return (
                <div
                  key={dim}
                  className="flex items-center gap-2 border-b border-gray-100 py-2 last:border-b-0"
                >
                  <span className="w-20 text-xs font-medium text-gray-700">
                    {EVAL_DIMENSION_LABELS[dim]}
                  </span>
                  <span className="text-xs text-gray-400">{score} 分（无明细）</span>
                </div>
              );
            }
            return (
              <DimensionRow
                key={dim}
                dimension={dim}
                score={score}
                assessment={assessment}
                onLocateScene={onLocateScene}
              />
            );
          },
        )}
      </div>
    </div>
  );
}
