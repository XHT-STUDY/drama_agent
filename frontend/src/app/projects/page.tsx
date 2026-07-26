"use client";

/** 项目列表页 (H-02).
 *
 * 使用 TanStack Query 从后端拉取项目列表，
 * 展示 ProjectCard 卡片网格，支持空状态引导。
 */

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { projectsApi } from "@/lib/api-client";
import { ErrorMessage } from "@/components/ErrorMessage";
import { Loading } from "@/components/Loading";
import { Empty } from "@/components/Empty";
import { ProjectCard } from "@/features/projects/ProjectCard";

export default function ProjectsPage() {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["projects"],
    queryFn: () => projectsApi.list(),
  });

  return (
    <div>
      {/* 页面头部 */}
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">项目列表</h1>
          <p className="mt-1 text-sm text-gray-500">管理你的短剧创作项目</p>
        </div>
        <Link
          href="/projects/new"
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 transition-colors"
        >
          + 创建项目
        </Link>
      </div>

      {/* 加载中 */}
      {isLoading && <Loading text="正在加载项目列表…" />}

      {/* 错误 */}
      {isError && (
        <ErrorMessage
          error={error as Error}
          onRetry={() => refetch()}
        />
      )}

      {/* 空列表 */}
      {data && data.items.length === 0 && (
        <Empty
          title="还没有项目"
          description="创建你的第一个短剧项目，开始 AI 辅助创作。"
          actionLabel="创建项目"
          actionHref="/projects/new"
        />
      )}

      {/* 项目卡片网格 */}
      {data && data.items.length > 0 && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {data.items.map((project) => (
            <ProjectCard key={project.id} project={project} />
          ))}
        </div>
      )}
    </div>
  );
}
