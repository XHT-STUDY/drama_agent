"use client";

/** StoryBible 页面 (H-04).
 *
 * 功能：
 * - 从 API 获取最新 story_bible Artifact
 * - 展示角色设定、世界观、锁定事实、伏笔等
 * - 支持切换历史版本
 */

import { useState, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { artifactsApi } from "@/lib/api-client";
import { Loading } from "@/components/Loading";
import { ErrorMessage } from "@/components/ErrorMessage";
import { Empty } from "@/components/Empty";
import { StoryBibleView } from "@/features/story-bible/StoryBibleView";
import type { Artifact, StoryBibleContent } from "@/types/api";

export default function StoryBiblePage() {
  const params = useParams();
  const projectId = String(params.id);

  // 当前查看的 artifact ID（null = 最新版本）
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // 获取 StoryBible 最新版本
  const {
    data: latestArtifact,
    isLoading: latestLoading,
    isError: latestError,
    error: latestErr,
    refetch: refetchLatest,
  } = useQuery({
    queryKey: ["artifact", projectId, "story_bible", "latest"],
    queryFn: () => artifactsApi.getLatest(projectId, "story_bible"),
  });

  // 获取所有版本
  const { data: versions = [] } = useQuery({
    queryKey: ["artifact", projectId, "story_bible", "versions"],
    queryFn: () => artifactsApi.listVersions(projectId, "story_bible"),
    enabled: !!latestArtifact,
  });

  // 获取指定版本的 Artifact
  const {
    data: selectedArtifact,
    isLoading: selectedLoading,
  } = useQuery({
    queryKey: ["artifact", selectedId],
    queryFn: () => artifactsApi.getById(selectedId!),
    enabled: !!selectedId,
  });

  // 当前实际显示的 artifact 和 content
  const artifact: Artifact | undefined = selectedId ? selectedArtifact : latestArtifact;
  const isLoading = latestLoading || (!!selectedId && selectedLoading);
  const content = artifact?.content as StoryBibleContent | undefined;

  const handleVersionChange = useCallback((artifactId: string) => {
    setSelectedId(artifactId);
  }, []);

  // 版本列表：如果选中版本不在列表中，则合并
  const allVersions = artifact && !versions.find((v) => v.id === artifact.id)
    ? [artifact, ...versions]
    : versions;

  // 加载中
  if (isLoading) {
    return (
      <div>
        <BackLink projectId={projectId} />
        <Loading text="正在加载 StoryBible…" />
      </div>
    );
  }

  // 错误
  if (latestError || !artifact) {
    return (
      <div>
        <BackLink projectId={projectId} />
        <ErrorMessage
          error={(latestErr as Error) || new Error("Artifact 不存在")}
          onRetry={() => {
            setSelectedId(null);
            refetchLatest();
          }}
        />
      </div>
    );
  }

  // 空内容
  if (!content || !content.title) {
    return (
      <div>
        <BackLink projectId={projectId} />
        <Empty
          title="StoryBible 尚未生成"
          description="请先运行创作工作流，系统将在「故事设定」节点完成后自动生成 StoryBible。"
          actionLabel="返回项目工作台"
          actionHref={`/projects/${projectId}`}
        />
      </div>
    );
  }

  // 正常展示
  return (
    <div>
      <BackLink projectId={projectId} />
      <StoryBibleView
        content={content}
        artifact={artifact}
        versions={allVersions}
        onVersionChange={handleVersionChange}
      />
    </div>
  );
}

/** 返回工作台链接 */
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
