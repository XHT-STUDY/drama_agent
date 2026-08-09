"use client";

/** RevisionDetail — 修订计划详情容器 (H-06).
 *
 * 获取 plan 详情（含 result_chain）并编排展示：
 * - needs_manual_review 提示（连续性失败 或 评分下降超过 5 分）
 * - 修订计划 / 连续性检查 / 评分对比 / Diff
 * - 「查看原稿 / 查看修订稿」全文切换（原稿始终可查看）
 *
 * 数据获取集中在本组件，叶子组件均为纯 props。
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { artifactsApi, revisionsApi } from "@/lib/api-client";
import { Loading } from "@/components/Loading";
import { ErrorMessage } from "@/components/ErrorMessage";
import { ScriptView } from "@/features/scripts/ScriptView";
import { RevisionPlanView } from "@/features/revisions/RevisionPlanView";
import { ContinuityCheckView } from "@/features/revisions/ContinuityCheckView";
import { ScoreComparison } from "@/features/revisions/ScoreComparison";
import { DiffView } from "@/features/diff/DiffView";
import type {
  ContinuityCheckContent,
  EvaluationReportContent,
  ScriptDraftContent,
} from "@/types/api";

/** 评分下降超过该阈值视为需人工复核（对齐后端 _SCORE_DROP_MANUAL_REVIEW_THRESHOLD） */
const SCORE_DROP_MANUAL_REVIEW_THRESHOLD = 5;

interface Props {
  projectId: string;
  planId: string;
}

export function RevisionDetail({ projectId, planId }: Props) {
  const [viewScript, setViewScript] = useState<"original" | "revised" | null>(null);

  // ---- 修订计划详情（含 result_chain） ----
  const {
    data: planArtifact,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: ["revision", projectId, planId],
    queryFn: () => revisionsApi.get(projectId, planId),
  });

  const chain = planArtifact?.result_chain;

  const sourceScript = chain?.source_script?.content as ScriptDraftContent | undefined;
  const candidateScript = chain?.candidate_script?.content as ScriptDraftContent | undefined;
  const cont = chain?.continuity_check?.content as ContinuityCheckContent | undefined;
  const sourceEval = chain?.source_evaluation?.content as EvaluationReportContent | undefined;
  const newEval = chain?.new_evaluation?.content as EvaluationReportContent | undefined;

  // 需人工复核：连续性失败 或 评分下降超过阈值
  const needsManualReview =
    cont?.status === "fail" ||
    (!!sourceEval &&
      !!newEval &&
      newEval.overall_score < sourceEval.overall_score - SCORE_DROP_MANUAL_REVIEW_THRESHOLD);

  // ---- 版本 Diff（依赖 result_chain.diff_ids） ----
  const diffIds = chain?.diff_ids ?? null;
  const {
    data: diff,
    isLoading: diffLoading,
    isError: diffError,
    error: diffErr,
    refetch: refetchDiff,
  } = useQuery({
    queryKey: ["diff", diffIds?.base, diffIds?.target],
    queryFn: () => artifactsApi.diff(diffIds!.base, diffIds!.target),
    enabled: !!diffIds,
  });

  if (isLoading) {
    return <Loading text="正在加载修订详情…" />;
  }

  if (isError || !planArtifact) {
    return (
      <ErrorMessage error={(error || new Error("修订计划不存在")) as Error} onRetry={() => refetch()} />
    );
  }

  const plan = planArtifact.content;

  return (
    <div className="space-y-5">
      {/* 头部 */}
      <div className="rounded-lg border border-gray-200 bg-white p-5">
        <div className="flex flex-wrap items-center gap-3">
          <h3 className="text-base font-bold text-gray-900">
            第 {plan.episode_number} 集修订详情
          </h3>
          <span className="text-xs text-gray-400">
            计划 v{planArtifact.version} ·{" "}
            {new Date(planArtifact.created_at).toLocaleString("zh-CN")}
          </span>
        </div>

        {/* needs_manual_review 提示 */}
        {needsManualReview && (
          <div className="mt-3 rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700">
            ⚠️ 需人工复核 — 连续性检查失败或评分下降超过 {SCORE_DROP_MANUAL_REVIEW_THRESHOLD}{" "}
            分，请人工确认是否接受此修订
          </div>
        )}
      </div>

      {/* 修订计划 */}
      <RevisionPlanView plan={plan} />

      {/* 连续性检查 */}
      {cont && <ContinuityCheckView result={cont} />}

      {/* 重新评分对比 */}
      {sourceEval && newEval ? (
        <ScoreComparison source={sourceEval} revised={newEval} />
      ) : (
        <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-4 py-3 text-sm text-gray-400">
          暂无重评结果
        </div>
      )}

      {/* 版本 Diff */}
      <div className="rounded-lg border border-gray-200 bg-white p-5">
        <h4 className="mb-3 text-sm font-semibold text-gray-700">
          版本 Diff（原稿 v{diff?.from_version ?? "?"} → 修订稿 v{diff?.to_version ?? "?"}）
        </h4>
        {diffIds === null ? (
          <p className="text-sm text-gray-400">尚未生成修订稿，暂无可对比内容</p>
        ) : diffLoading ? (
          <p className="text-sm text-gray-400">正在加载 Diff…</p>
        ) : diffError ? (
          <ErrorMessage
            error={(diffErr || new Error("Diff 加载失败")) as Error}
            onRetry={() => refetchDiff()}
          />
        ) : diff ? (
          <DiffView diff={diff} />
        ) : null}
      </div>

      {/* 原稿 / 修订稿全文查看 */}
      <div className="rounded-lg border border-gray-200 bg-white p-5">
        <div className="mb-3 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() =>
              setViewScript(viewScript === "original" ? null : "original")
            }
            className="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:border-blue-300 hover:text-blue-600 transition-colors"
          >
            {viewScript === "original" ? "收起原稿" : "查看原稿"}
          </button>
          <button
            type="button"
            onClick={() =>
              setViewScript(viewScript === "revised" ? null : "revised")
            }
            className="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:border-blue-300 hover:text-blue-600 transition-colors"
          >
            {viewScript === "revised" ? "收起修订稿" : "查看修订稿"}
          </button>
        </div>

        {viewScript === "original" &&
          (sourceScript ? (
            <ScriptView content={sourceScript} />
          ) : (
            <p className="text-sm text-gray-400">（无原稿内容）</p>
          ))}
        {viewScript === "revised" &&
          (candidateScript ? (
            <ScriptView content={candidateScript} />
          ) : (
            <p className="text-sm text-gray-400">（无修订稿内容）</p>
          ))}
      </div>
    </div>
  );
}
