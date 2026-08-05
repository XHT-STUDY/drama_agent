"use client";

/** 项目列表页 (H-02).
 *
 * 使用 TanStack Query 从后端拉取项目列表，
 * 展示 ProjectCard 卡片网格，支持分页加载。
 */

import { useState, useCallback, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { projectsApi } from "@/lib/api-client";
import { ErrorMessage } from "@/components/ErrorMessage";
import { Loading } from "@/components/Loading";
import { Empty } from "@/components/Empty";
import { ProjectCard } from "@/features/projects/ProjectCard";
import type { Project } from "@/types/api";

const PAGE_SIZE = 20;

export default function ProjectsPage() {
  // 当前请求的页偏移量
  const [pageOffsets, setPageOffsets] = useState<number[]>([0]);

  // 累积所有已加载页的项目
  const [allItems, setAllItems] = useState<Project[]>([]);

  // API 返回的真实总项目数
  const [totalCount, setTotalCount] = useState<number | null>(null);

  // 请求当前最后一页（最新的一页）
  const currentOffset = pageOffsets[pageOffsets.length - 1];

  const {
    data,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: ["projects", currentOffset],
    queryFn: () => projectsApi.list(currentOffset, PAGE_SIZE),
  });

  // 当新数据到达时，累积到 allItems
  useEffect(() => {
    if (!data?.items) return;

    setAllItems((prev) => {
      const existingIds = new Set(prev.map((p) => p.id));
      const newItems = data.items.filter(
        (p) => !existingIds.has(p.id),
      );
      if (newItems.length === 0) return prev;
      return [...prev, ...newItems];
    });

    if (data.total != null) {
      setTotalCount(data.total);
    }
  }, [data]);

  const hasMore = totalCount != null ? allItems.length < totalCount : false;

  const handleLoadMore = useCallback(() => {
    const nextOffset = currentOffset + PAGE_SIZE;
    setPageOffsets((prev) => [...prev, nextOffset]);
  }, [currentOffset]);

  // 首次加载中，无任何数据
  const isFirstLoad = isLoading && allItems.length === 0;

  // 首次加载失败，无任何数据
  const isFirstError = isError && allItems.length === 0;

  return (
    <div>
      {/* 页面头部 */}
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">项目列表</h1>
          <p className="mt-1 text-sm text-gray-500">
            管理你的短剧创作项目
            {totalCount != null && ` · 共 ${totalCount} 个项目`}
          </p>
        </div>
        <Link
          href="/projects/new"
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 transition-colors"
        >
          + 创建项目
        </Link>
      </div>

      {/* 首次加载中 */}
      {isFirstLoad && <Loading text="正在加载项目列表…" />}

      {/* 首次加载失败 */}
      {isFirstError && (
        <ErrorMessage
          error={error as Error}
          onRetry={() => refetch()}
        />
      )}

      {/* 空列表 */}
      {!isLoading && !isError && allItems.length === 0 && (
        <Empty
          title="还没有项目"
          description="创建你的第一个短剧项目，开始 AI 辅助创作。"
          actionLabel="创建项目"
          actionHref="/projects/new"
        />
      )}

      {/* 项目卡片网格 */}
      {allItems.length > 0 && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {allItems.map((project) => (
              <ProjectCard key={project.id} project={project} />
            ))}
          </div>

          {/* 分页区域 */}
          <div className="mt-6 flex justify-center">
            {isLoading && (
              <div className="flex items-center gap-2 text-sm text-gray-500">
                <div className="h-4 w-4 animate-spin rounded-full border-2 border-blue-500 border-t-transparent" />
                正在加载…
              </div>
            )}
            {!isLoading && hasMore && (
              <button
                onClick={handleLoadMore}
                className="rounded-lg border border-gray-300 bg-white px-6 py-2 text-sm text-gray-700 shadow-sm hover:bg-gray-50 transition-colors"
              >
                加载更多项目
              </button>
            )}
            {!isLoading && !hasMore && allItems.length > 0 && totalCount != null && (
              <p className="text-xs text-gray-400">已显示全部 {allItems.length} 个项目</p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
