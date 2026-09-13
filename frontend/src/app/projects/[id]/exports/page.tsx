"use client";

/** 导出中心页（H-07，W1-05 固定版本版）.
 *
 * 后端导出：选择在请求接受时冻结为显式 Artifact ID；导出完成后经
 * export_file Artifact 的下载端点拿文件——历史重新下载字节一致。
 * - ExportSection：内容/格式选择 → 发起后端导出 → 轮询 Run → 下载
 * - ExportHistory：服务端历史（export_file Artifact 列表）+ 固定重下
 * 旧版 localStorage 历史只读展示为"未保存固定文件"的浏览器记录。
 */

import { useMemo } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { artifactsApi, exportsApi } from "@/lib/api-client";
import { Loading } from "@/components/Loading";
import { ErrorMessage } from "@/components/ErrorMessage";
import { Empty } from "@/components/Empty";
import { ExportSection } from "@/features/exports/ExportSection";
import { ExportHistory } from "@/features/exports/ExportHistory";
import type { Artifact, ExportRecord, ExportableArtifacts } from "@/types/api";

/** 按集取最新 valid（同集多版本时取 version 最大者；不依赖
 * current_episode_count——已写集数以实际存在的 Artifact 为准） */
function latestValidPerEpisode(artifacts: Artifact[]): Artifact[] {
  const byEpisode = new Map<number, Artifact>();
  for (const a of artifacts) {
    if (a.status !== "valid") continue;
    const cur = byEpisode.get(a.episode_number);
    if (!cur || a.version > cur.version) byEpisode.set(a.episode_number, a);
  }
  return [...byEpisode.values()].sort((x, y) => x.episode_number - y.episode_number);
}

function latestValidSingle(artifacts: Artifact[]): Artifact | null {
  return latestValidPerEpisode(artifacts)[0] ?? null;
}

async function loadExportableArtifacts(projectId: string): Promise<ExportableArtifacts> {
  // 读取失败整体报错（W1-05：静默吞掉某类型=悄悄少导一部分）
  const [sbAll, outlineAll, scriptsAll, evaluationsAll, revisionsAll] =
    await Promise.all([
      artifactsApi.listAllByType(projectId, "story_bible"),
      artifactsApi.listAllByType(projectId, "episode_outline_set"),
      artifactsApi.listAllByType(projectId, "script_draft"),
      artifactsApi.listAllByType(projectId, "evaluation_report"),
      artifactsApi.listAllByType(projectId, "revision_plan"),
    ]);
  return {
    storyBible: latestValidSingle(sbAll),
    outline: latestValidSingle(outlineAll),
    scripts: latestValidPerEpisode(scriptsAll),
    evaluations: latestValidPerEpisode(evaluationsAll),
    revisions: latestValidPerEpisode(revisionsAll),
  };
}

/** 旧版浏览器历史（只读：未保存固定文件，不再支持重下） */
function readLegacyRecords(projectId: string): ExportRecord[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(`drama-exports:${projectId}`);
    const parsed = raw ? (JSON.parse(raw) as ExportRecord[]) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

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

export default function ExportsPage() {
  const params = useParams();
  const projectId = String(params.id);

  const {
    data,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: ["export-data", projectId],
    queryFn: () => loadExportableArtifacts(projectId),
  });

  const history = useQuery({
    queryKey: ["server-exports", projectId],
    queryFn: () => exportsApi.list(projectId),
  });

  const legacyRecords = useMemo(() => readLegacyRecords(projectId), [projectId]);

  const isEmpty = useMemo(
    () =>
      !!data &&
      data.storyBible === null &&
      data.outline === null &&
      data.scripts.length === 0 &&
      data.evaluations.length === 0 &&
      data.revisions.length === 0,
    [data],
  );

  return (
    <div>
      <BackLink projectId={projectId} />
      <h1 className="mb-6 text-2xl font-bold text-gray-900">导出中心</h1>

      {isLoading ? (
        <Loading text="正在加载导出内容…" />
      ) : isError || !data ? (
        <ErrorMessage
          error={(error || new Error("加载导出内容失败")) as Error}
          onRetry={() => refetch()}
        />
      ) : isEmpty ? (
        <Empty
          title="暂无内容可导出"
          description="完成创作流程后，StoryBible、大纲、剧本、评估与修订说明将可在这里导出为 Markdown / DOCX。"
        />
      ) : (
        <div className="space-y-6">
          <ExportSection projectId={projectId} available={data} />

          {history.isError ? (
            <ErrorMessage
              error={(history.error || new Error("导出历史加载失败")) as Error}
              onRetry={() => history.refetch()}
            />
          ) : history.isLoading ? (
            <Loading text="正在加载导出历史…" />
          ) : (
            <ExportHistory
              projectId={projectId}
              artifacts={history.data?.items ?? []}
            />
          )}

          {legacyRecords.length > 0 && (
            <p className="text-xs text-gray-400" data-testid="legacy-exports-note">
              另有 {legacyRecords.length} 条旧版浏览器导出记录（未保存固定文件，
              内容已随稿件变化，不再支持重新下载）。
            </p>
          )}
        </div>
      )}
    </div>
  );
}
