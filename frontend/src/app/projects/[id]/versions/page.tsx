"use client";

/** 修订与版本页 (H-06).
 *
 * 两个区域：
 * - 修订记录：发起修订（可选 user_instruction）→ 修订计划列表 → 选中查看详情
 *   （计划 / 连续性 / 评分对比 / Diff / 原稿修订稿全文）
 * - 版本对比：集数 → 原稿/修订稿版本选择 → 任意两版本 Diff
 *
 * 全程只读展示，不提供覆盖旧版本按钮。
 */

import { useState, useEffect, useMemo } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { artifactsApi, projectsApi, revisionsApi, runsApi } from "@/lib/api-client";
import { Loading } from "@/components/Loading";
import { ErrorMessage } from "@/components/ErrorMessage";
import { Empty } from "@/components/Empty";
import { RevisionPlanList } from "@/features/revisions/RevisionPlanList";
import { RevisionDetail } from "@/features/revisions/RevisionDetail";
import { DiffView } from "@/features/diff/DiffView";
import type { Artifact } from "@/types/api";

/** Run 终态集合（轮询停止） */
const TERMINAL_STATUSES = new Set(["completed", "failed", "cancelled", "needs_review"]);

/** BackLink — 返回工作台 */
function BackLink({ projectId }: { projectId: string }) {
  return (
    <div className="mb-4">
      <Link
        href={`/projects/${projectId}`}
        className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 transition-colors"
      >
        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
        </svg>
        返回工作台
      </Link>
    </div>
  );
}

/** 单行 Run 状态徽章（发起修订后轮询显示） */
function RunStatusLine({ status }: { status: string }) {
  const map: Record<string, { text: string; cls: string }> = {
    completed: { text: "✅ 修订完成", cls: "border-green-200 bg-green-50 text-green-700" },
    needs_review: { text: "⚠️ 修订需人工复核", cls: "border-amber-200 bg-amber-50 text-amber-700" },
    failed: { text: "❌ 修订失败", cls: "border-red-200 bg-red-50 text-red-700" },
  };
  const m = map[status] || { text: `修订进行中（${status}）…`, cls: "border-blue-200 bg-blue-50 text-blue-700" };
  return (
    <div className={`rounded-lg border px-4 py-2 text-sm font-medium ${m.cls}`}>{m.text}</div>
  );
}

export default function VersionsPage() {
  const params = useParams();
  const projectId = String(params.id);
  const queryClient = useQueryClient();

  // ============================================================
  // 修订记录
  // ============================================================

  const {
    data: revisionsData,
    isLoading: revisionsLoading,
    isError: revisionsError,
    error: revisionsErr,
    refetch: refetchRevisions,
  } = useQuery({
    queryKey: ["revisions", projectId],
    queryFn: () => revisionsApi.list(projectId),
  });

  // 后端按集号/版本升序返回 → 前端展示最新的在前
  const items = useMemo<Artifact[]>(
    () => [...(revisionsData?.items ?? [])].reverse(),
    [revisionsData],
  );

  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(null);

  // 首次加载后默认选中最新一条修订计划
  useEffect(() => {
    if (items.length > 0 && selectedPlanId === null) {
      setSelectedPlanId(items[0].id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items.length]);

  // ---- 发起修订 ----
  const [instruction, setInstruction] = useState("");
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [pendingRunId, setPendingRunId] = useState<string | null>(null);

  const createRevision = useMutation({
    mutationFn: () =>
      revisionsApi.create(projectId, {
        script_artifact_id: null,
        user_instruction: instruction.trim() || null,
        idempotency_key: null,
      }),
    onSuccess: (run) => {
      setPendingRunId(run.run_id);
      setShowCreateForm(false);
      setInstruction("");
    },
  });

  // 轮询修订 Run 至终态；终态后刷新修订列表
  const { data: pendingRun } = useQuery({
    queryKey: ["revision-run", pendingRunId],
    queryFn: () => runsApi.get(pendingRunId as string),
    enabled: !!pendingRunId,
    refetchInterval: (query) =>
      query.state.data?.status && TERMINAL_STATUSES.has(query.state.data.status)
        ? false
        : 2000,
  });

  const runStatus = pendingRunId ? pendingRun?.status : undefined;

  useEffect(() => {
    if (runStatus && TERMINAL_STATUSES.has(runStatus)) {
      queryClient.invalidateQueries({ queryKey: ["revisions", projectId] });
    }
  }, [runStatus, queryClient, projectId]);

  // ============================================================
  // 版本对比
  // ============================================================

  const { data: project } = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => projectsApi.get(projectId),
  });

  const targetCount = project?.target_episode_count ?? 1;
  const [episode, setEpisode] = useState(1);

  const {
    data: scriptVersions,
    isLoading: versionsLoading,
    isError: versionsError,
    error: versionsErr,
    refetch: refetchVersions,
  } = useQuery({
    queryKey: ["script-versions", projectId, episode],
    queryFn: () => artifactsApi.listVersions(projectId, "script_draft", episode),
  });

  const sortedVersions = useMemo<Artifact[]>(
    () => [...(scriptVersions ?? [])].sort((a, b) => a.version - b.version),
    [scriptVersions],
  );

  const [baseId, setBaseId] = useState<string | null>(null);
  const [targetId, setTargetId] = useState<string | null>(null);

  // 版本变化 / 集数切换 → 重置默认选择（target=最新，base=前一个）
  useEffect(() => {
    if (sortedVersions.length >= 2) {
      setBaseId(sortedVersions[sortedVersions.length - 2].id);
      setTargetId(sortedVersions[sortedVersions.length - 1].id);
    } else if (sortedVersions.length === 1) {
      setBaseId(sortedVersions[0].id);
      setTargetId(sortedVersions[0].id);
    } else {
      setBaseId(null);
      setTargetId(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [episode, sortedVersions.length]);

  const {
    data: compareDiff,
    isLoading: compareDiffLoading,
    isError: compareDiffError,
    error: compareDiffErr,
    refetch: refetchCompareDiff,
  } = useQuery({
    queryKey: ["diff", baseId, targetId],
    queryFn: () => artifactsApi.diff(baseId as string, targetId as string),
    enabled: !!baseId && !!targetId && baseId !== targetId,
  });

  // ============================================================
  // 渲染
  // ============================================================

  return (
    <div>
      <BackLink projectId={projectId} />
      <h1 className="mb-6 text-2xl font-bold text-gray-900">修订与版本</h1>

      {/* ---- 修订记录 ---- */}
      <section className="mb-10">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-gray-800">修订记录</h2>
          <button
            type="button"
            onClick={() => setShowCreateForm((v) => !v)}
            className="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:border-blue-300 hover:text-blue-600 transition-colors"
          >
            {showCreateForm ? "收起" : "＋ 发起修订"}
          </button>
        </div>

        {/* 发起修订表单（可折叠） */}
        {showCreateForm && (
          <div className="mb-4 rounded-lg border border-gray-200 bg-white p-4">
            <label className="mb-1 block text-xs font-medium text-gray-600">
              用户补充要求（可选，不能违反锁定事实）
            </label>
            <textarea
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              maxLength={2000}
              rows={2}
              placeholder="例如：加强反派动机，但不得改变主角身世"
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
            />
            <div className="mt-2 flex items-center gap-2">
              <button
                type="button"
                onClick={() => createRevision.mutate()}
                disabled={createRevision.isPending || !!runStatus}
                className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 transition-colors disabled:opacity-50"
              >
                {createRevision.isPending ? "正在发起…" : "发起修订（自动选择最低分集）"}
              </button>
              {createRevision.isError && (
                <span className="text-xs text-red-600">
                  {createRevision.error instanceof Error
                    ? createRevision.error.message
                    : "发起失败"}
                </span>
              )}
            </div>
          </div>
        )}

        {/* 修订 Run 状态（轮询中 / 终态） */}
        {runStatus && (
          <div className="mb-4">
            <RunStatusLine status={runStatus} />
          </div>
        )}

        {/* 修订列表状态 */}
        {revisionsLoading ? (
          <Loading text="正在加载修订记录…" />
        ) : revisionsError ? (
          <ErrorMessage
            error={(revisionsErr || new Error("加载修订记录失败")) as Error}
            onRetry={() => refetchRevisions()}
          />
        ) : items.length === 0 ? (
          <Empty
            title="暂无修订记录"
            description="运行修订工作流后，修订计划与结果将显示在这里。可点击右上角「发起修订」。"
          />
        ) : (
          <div className="space-y-4">
            <RevisionPlanList
              items={items}
              selectedId={selectedPlanId}
              onSelect={setSelectedPlanId}
            />

            {selectedPlanId ? (
              <div>
                <button
                  type="button"
                  onClick={() => setSelectedPlanId(null)}
                  className="mb-3 text-xs text-gray-400 hover:text-gray-600 transition-colors"
                >
                  ← 返回修订列表
                </button>
                <RevisionDetail projectId={projectId} planId={selectedPlanId} />
              </div>
            ) : (
              <p className="text-sm text-gray-400">请选择一条修订记录查看详情</p>
            )}
          </div>
        )}
      </section>

      {/* ---- 版本对比 ---- */}
      <section>
        <h2 className="mb-3 text-lg font-semibold text-gray-800">版本对比</h2>

        {/* 集数 / 版本选择 */}
        <div className="mb-4 flex flex-wrap items-end gap-4 rounded-lg border border-gray-200 bg-white p-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-600">集数</label>
            <select
              value={episode}
              onChange={(e) => setEpisode(parseInt(e.target.value, 10))}
              className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-400 focus:outline-none"
            >
              {Array.from({ length: targetCount }, (_, i) => i + 1).map((n) => (
                <option key={n} value={n}>
                  第 {n} 集
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-gray-600">原稿版本</label>
            <select
              value={baseId ?? ""}
              onChange={(e) => setBaseId(e.target.value || null)}
              disabled={sortedVersions.length < 2}
              className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-400 focus:outline-none disabled:opacity-50"
            >
              {sortedVersions.length === 0 && <option value="">（无版本）</option>}
              {sortedVersions.map((v) => (
                <option key={v.id} value={v.id}>
                  v{v.version} {v.status === "valid" ? "✓" : ""} —{" "}
                  {new Date(v.created_at).toLocaleString("zh-CN")}
                  {v.status === "invalid" ? "（候选未通过）" : ""}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-gray-600">修订稿版本</label>
            <select
              value={targetId ?? ""}
              onChange={(e) => setTargetId(e.target.value || null)}
              disabled={sortedVersions.length < 2}
              className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-400 focus:outline-none disabled:opacity-50"
            >
              {sortedVersions.length === 0 && <option value="">（无版本）</option>}
              {sortedVersions.map((v) => (
                <option key={v.id} value={v.id}>
                  v{v.version} {v.status === "valid" ? "✓" : ""} —{" "}
                  {new Date(v.created_at).toLocaleString("zh-CN")}
                  {v.status === "invalid" ? "（候选未通过）" : ""}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* 版本状态 */}
        {versionsLoading ? (
          <Loading text="正在加载剧本版本…" />
        ) : versionsError ? (
          <ErrorMessage
            error={(versionsErr || new Error("加载剧本版本失败")) as Error}
            onRetry={() => refetchVersions()}
          />
        ) : sortedVersions.length <= 1 ? (
          <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-4 py-6 text-center text-sm text-gray-500">
            {sortedVersions.length === 1
              ? "该集仅有 1 个版本，暂无法对比"
              : "该集暂无剧本版本"}
          </div>
        ) : compareDiffLoading ? (
          <Loading text="正在对比版本…" />
        ) : compareDiffError ? (
          <ErrorMessage
            error={(compareDiffErr || new Error("版本对比失败")) as Error}
            onRetry={() => refetchCompareDiff()}
          />
        ) : compareDiff ? (
          <DiffView diff={compareDiff} />
        ) : null}
      </section>
    </div>
  );
}
