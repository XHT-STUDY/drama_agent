"use client";

/** 项目工作台页 (H-03).
 *
 * 核心功能：
 * - 显示项目标题、状态
 * - 创作输入区（ChatInput）→ 提交 Idea 创建 Run
 * - SSE 进度面板（RunProgress）→ 实时展示工作流状态
 * - 页面刷新后恢复活跃 Run
 */

import { useState, useCallback } from "react";
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

export default function ProjectDetailPage() {
  const params = useParams();
  const projectId = String(params.id);

  // 当前活跃的 runId（null 表示无运行中的 Run）
  const [activeRunId, setActiveRunId] = useState<string | null>(null);

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

  // 页面加载时检查是否有活跃 Run
  useQuery({
    queryKey: ["project-runs", projectId],
    queryFn: async () => {
      const result = await runsApi.listByProject(projectId);
      const active = result.items.find(
        (r) => r.status === "queued" || r.status === "running",
      );
      if (active) {
        setActiveRunId(active.run_id);
      }
      return result;
    },
  });

  // SSE 进度订阅
  const runEvents = useRunEvents(activeRunId);

  const handleRunCreated = useCallback((runId: string) => {
    setActiveRunId(runId);
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

      {/* 无活跃 Run 时的提示 */}
      {!activeRunId && (
        <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 p-8 text-center">
          <p className="text-sm text-gray-500">
            在上方输入创作 Idea，点击「开始创作」启动 AI 创作流程。
          </p>
          <p className="mt-1 text-xs text-gray-400">
            系统将依次执行：需求归一化 → 故事设定 → 分集大纲 → 剧本撰写
          </p>
        </div>
      )}
    </div>
  );
}
