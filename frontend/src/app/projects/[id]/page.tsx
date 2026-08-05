"use client";

/** 项目工作台页 (H-03).
 *
 * 核心功能：
 * - 显示项目标题、状态
 * - 创作输入区（ChatInput）→ 提交 Idea 创建 Run
 * - SSE 进度面板（RunProgress）→ 实时展示工作流状态
 * - 页面刷新后恢复活跃 Run
 */

import { useState, useCallback, useMemo } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { projectsApi, runsApi } from "@/lib/api-client";
import { ErrorMessage } from "@/components/ErrorMessage";
import { Loading } from "@/components/Loading";
import { StatusBadge } from "@/features/projects/StatusBadge";
import { ChatInput } from "@/features/conversation/ChatInput";
import { RunProgress } from "@/features/runs/RunProgress";
import { useRunEvents } from "@/hooks/use-run-events";
import type { Run } from "@/types/api";

export default function ProjectDetailPage() {
  const params = useParams();
  const projectId = String(params.id);

  // 加载项目信息
  const {
    data: project,
    isLoading: projLoading,
    isError: projError,
    error: projErr,
    refetch: refetchProject,
  } = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => projectsApi.get(projectId),
  });

  // 页面加载时检查 Run 历史
  const {
    data: runsData,
    isLoading: runsLoading,
    isError: runsError,
    error: runsErr,
    refetch: refetchRuns,
  } = useQuery({
    queryKey: ["project-runs", projectId],
    queryFn: () => runsApi.listByProject(projectId),
  });

  // runs 是否已加载完成（非 loading 且有数据或确定为空）
  const runsReady = !runsLoading;

  // 手动触发的 runId（创建 Run 后立即显示进度，不等 runsData 刷新）
  const [pendingRunId, setPendingRunId] = useState<string | null>(null);

  // 从 runsData 派生 activeRunId（不在 queryFn 中 setState，避免 staleTime 导致丢失）
  const activeRunId: string | null = useMemo(() => {
    // 优先取 runsData 中的活跃 Run
    if (runsData?.items?.length) {
      const active = runsData.items.find(
        (r: Run) => r.status === "queued" || r.status === "running",
      );
      if (active) return active.run_id;
      // runsData 中无活跃 Run，清除 pending
      return null;
    }
    // runsData 尚未加载 → 信任 pendingRunId（刚创建 Run 时）
    if (pendingRunId) return pendingRunId;
    return null;
  }, [runsData, pendingRunId]);

  // 从 runsData 派生 latestRun（不在 queryFn 中 setState，避免 staleTime 导致丢失）
  const latestRun: { run_id: string; status: string } | null = useMemo(() => {
    if (!runsData?.items?.length) return null;
    const active = runsData.items.find(
      (r: Run) => r.status === "queued" || r.status === "running",
    );
    if (active) return null;
    const latest = runsData.items[0];
    return { run_id: latest.run_id, status: latest.status };
  }, [runsData]);

  // SSE 进度订阅
  const runEvents = useRunEvents(activeRunId);

  const handleRunCreated = useCallback((runId: string) => {
    setPendingRunId(runId);
  }, []);

  if (projLoading) {
    return <Loading text="正在加载项目…" />;
  }

  if (projError || !project) {
    return (
      <div>
        <Link href="/projects" className="mb-4 inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700">
          ← 返回项目列表
        </Link>
        <ErrorMessage error={(projErr || new Error("项目不存在")) as Error} onRetry={() => refetchProject()} />
      </div>
    );
  }

  return (
    <div>
      {/* 顶部导航 */}
      <div className="mb-6">
        <Link
          href="/projects"
          className="mb-2 inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 transition-colors"
        >
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
          返回项目列表
        </Link>

        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-gray-900">{project.title || "未命名项目"}</h1>
          <StatusBadge status={project.status} />
        </div>
        <p className="mt-1 text-sm text-gray-500">
          目标 {project.target_episode_count} 集 · 已完成 {project.current_episode_count} 集
        </p>
      </div>

      {/* 创作输入 */}
      <div className="mb-6">
        <ChatInput
          projectId={projectId}
          onRunCreated={handleRunCreated}
          hasActiveRun={!!activeRunId && !runEvents.runStatus}
          scriptCount={project.target_episode_count}
        />
      </div>

      {/* SSE 进度 */}
      {activeRunId && (
        <div className="mb-6">
          <RunProgress
            runId={activeRunId}
            overallProgress={runEvents.overallProgress}
            nodes={runEvents.nodes}
            eventCount={runEvents.events.length}
            connected={runEvents.connected}
            runStatus={runEvents.runStatus}
            lastError={runEvents.lastError}
            onReconnect={runEvents.reconnect}
          />

          {/* 完成后显示导航链接 */}
          {runEvents.runStatus === "completed" && (
            <div className="mt-4 flex flex-wrap gap-3">
              <Link
                href={`/projects/${projectId}/story-bible`}
                className="rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm text-gray-700 shadow-sm hover:border-blue-300 hover:text-blue-600 transition-colors"
              >
                📖 查看 StoryBible
              </Link>
              <Link
                href={`/projects/${projectId}/outline`}
                className="rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm text-gray-700 shadow-sm hover:border-blue-300 hover:text-blue-600 transition-colors"
              >
                📋 查看分集大纲
              </Link>
            </div>
          )}
        </div>
      )}

      {/* 无活跃 Run 时的展示 */}
      {!activeRunId && (
        <>
          {/* runs 数据加载中 → 显示轻量加载指示器（避免闪现空状态） */}
          {!runsReady && (
            <div className="mb-6 rounded-lg border border-gray-200 bg-white p-5">
              <div className="flex items-center gap-3">
                <div className="h-4 w-4 animate-spin rounded-full border-2 border-blue-500 border-t-transparent" />
                <span className="text-sm text-gray-500">正在检查创作历史…</span>
              </div>
            </div>
          )}

          {/* runs 加载失败 → 显示错误 + 重试 */}
          {runsReady && runsError && (
            <div className="mb-6 rounded-lg border border-red-200 bg-red-50 p-5">
              <h3 className="mb-2 text-sm font-semibold text-red-700">⚠️ 加载创作历史失败</h3>
              <p className="mb-3 text-xs text-red-500">
                {(runsErr as Error)?.message || "无法获取 Run 列表，请检查后端服务是否正常运行。"}
              </p>
              <button
                onClick={() => refetchRuns()}
                className="rounded border border-red-300 bg-white px-3 py-1 text-xs text-red-600 hover:bg-red-50 transition-colors"
              >
                重试
              </button>
            </div>
          )}

          {/* runs 加载完成且无错误 → 根据 latestRun 状态展示 */}
          {runsReady && !runsError && (
          <>
          {/* 上次运行已完成 → 显示结果入口 */}
          {latestRun?.status === "completed" && (
            <div className="mb-6 rounded-lg border border-green-200 bg-green-50 p-5">
              <h3 className="mb-2 text-sm font-semibold text-green-800">✅ 上次创作已完成</h3>
              <div className="flex flex-wrap gap-3">
                <Link
                  href={`/projects/${projectId}/story-bible`}
                  className="rounded-lg border border-green-300 bg-white px-4 py-2 text-sm text-gray-700 shadow-sm hover:border-blue-300 hover:text-blue-600 transition-colors"
                >
                  📖 查看 StoryBible
                </Link>
                <Link
                  href={`/projects/${projectId}/outline`}
                  className="rounded-lg border border-green-300 bg-white px-4 py-2 text-sm text-gray-700 shadow-sm hover:border-blue-300 hover:text-blue-600 transition-colors"
                >
                  📋 查看分集大纲
                </Link>
                <Link
                  href={`/projects/${projectId}/scripts/1`}
                  className="rounded-lg border border-green-300 bg-white px-4 py-2 text-sm text-gray-700 shadow-sm hover:border-blue-300 hover:text-blue-600 transition-colors"
                >
                  📝 查看剧本
                </Link>
              </div>
            </div>
          )}

          {/* 上次运行失败 → 显示失败信息 */}
          {latestRun?.status === "failed" && (
            <div className="mb-6 rounded-lg border border-red-200 bg-red-50 p-5">
              <h3 className="mb-1 text-sm font-semibold text-red-700">⚠️ 上次创作未完成</h3>
              <p className="text-xs text-red-500">Run ID: {latestRun.run_id.slice(0, 8)}…</p>
            </div>
          )}

          {/* 没有任何 Run → 引导提示 */}
          {!latestRun && (
            <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 p-8 text-center">
              <p className="text-sm text-gray-500">
                在上方输入创作 Idea，点击「开始创作」启动 AI 创作流程。
              </p>
              <p className="mt-1 text-xs text-gray-400">
                系统将依次执行：需求归一化 → 故事设定 → 分集大纲 → 剧本撰写
              </p>
            </div>
          )}
            </>
          )}
        </>
      )}
    </div>
  );
}
