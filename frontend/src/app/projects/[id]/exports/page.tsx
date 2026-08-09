"use client";

/** 导出中心页 (H-07).
 *
 * 客户端本地导出：复用现有 GET artifacts 接口取内容，在浏览器序列化为
 * Markdown / DOCX 并下载。提供：
 * - ExportSection：选择导出内容与格式 → 生成并下载
 * - ExportHistory：导出历史（localStorage 按项目隔离）+ 重新下载 + 清空
 *
 * 数据获取集中在页面容器，ExportSection / ExportHistory 为纯叶子组件。
 */

import { useState, useMemo } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { artifactsApi, projectsApi, revisionsApi } from "@/lib/api-client";
import { Loading } from "@/components/Loading";
import { ErrorMessage } from "@/components/ErrorMessage";
import { Empty } from "@/components/Empty";
import { ExportSection } from "@/features/exports/ExportSection";
import { ExportHistory } from "@/features/exports/ExportHistory";
import { downloadBlob, serializeExport, type ExportData } from "@/lib/export";
import type {
  Artifact,
  EpisodeOutlineSetContent,
  EvaluationReportContent,
  ExportContentKind,
  ExportFormat,
  ExportRecord,
  RevisionPlanContent,
  ScriptDraftContent,
  StoryBibleContent,
} from "@/types/api";

/** Artifact.content → 强类型（后端 content 为宽松 Record，先过一层） */
function contentAs<T>(artifact: Artifact): T {
  return artifact.content as unknown as T;
}

/** 加载导出所需全部内容（缺数据容错置空，不阻塞导出） */
async function loadExportData(projectId: string): Promise<ExportData> {
  const project = await projectsApi.get(projectId);

  const [storyBible, outline, revisionsRes] = await Promise.all([
    artifactsApi
      .getLatest(projectId, "story_bible", 1)
      .catch(() => null),
    artifactsApi
      .getLatest(projectId, "episode_outline_set", 1)
      .catch(() => null),
    revisionsApi.list(projectId).catch(() => ({ items: [] as Artifact[] })),
  ]);

  // 已撰写的集数 = current_episode_count（finalize 更新）
  const written = Math.max(0, project.current_episode_count);
  const episodes = Array.from({ length: written }, (_, i) => i + 1);

  const [scripts, evaluations] = await Promise.all([
    Promise.all(
      episodes.map((ep) =>
        artifactsApi
          .getLatest(projectId, "script_draft", ep)
          .then((a) => contentAs<ScriptDraftContent>(a))
          .catch(() => null),
      ),
    ),
    Promise.all(
      episodes.map((ep) =>
        artifactsApi
          .getLatest(projectId, "evaluation_report", ep)
          .then((a) => contentAs<EvaluationReportContent>(a))
          .catch(() => null),
      ),
    ),
  ]);

  return {
    projectTitle: project.title,
    storyBible: storyBible ? contentAs<StoryBibleContent>(storyBible) : null,
    outline: outline ? contentAs<EpisodeOutlineSetContent>(outline) : null,
    scripts: scripts.filter((s): s is ScriptDraftContent => s !== null),
    evaluations: evaluations.filter((e): e is EvaluationReportContent => e !== null),
    revisions: revisionsRes.items.map((a) => ({
      plan: contentAs<RevisionPlanContent>(a),
      diff: null,
    })),
  };
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

// ============================================================
// localStorage 导出历史（按项目隔离）
// ============================================================

const historyKey = (projectId: string): string => `drama-exports:${projectId}`;

function readHistory(projectId: string): ExportRecord[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(historyKey(projectId));
    if (!raw) return [];
    const parsed = JSON.parse(raw) as ExportRecord[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function writeHistory(projectId: string, records: ExportRecord[]): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(historyKey(projectId), JSON.stringify(records));
  } catch {
    // localStorage 不可用（隐私模式等）时静默降级
  }
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
    queryFn: () => loadExportData(projectId),
  });

  // 导出历史（localStorage）
  const [records, setRecords] = useState<ExportRecord[]>(() => readHistory(projectId));
  const [historyError, setHistoryError] = useState<string | null>(null);

  const persistRecords = (next: ExportRecord[]): void => {
    setRecords(next);
    writeHistory(projectId, next);
  };

  const handleExported = (record: ExportRecord): void => {
    persistRecords([record, ...records].slice(0, 50));
    setHistoryError(null);
  };

  const handleRedownload = async (record: ExportRecord): Promise<void> => {
    if (!data) return;
    setHistoryError(null);
    try {
      const result = await serializeExport({
        data,
        kinds: record.kinds as ExportContentKind[],
        format: record.format as ExportFormat,
      });
      downloadBlob(result.filename, result.blob);
    } catch (e) {
      setHistoryError(e instanceof Error ? e.message : "重新下载失败，请重试");
    }
  };

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
          <ExportSection data={data} onExported={handleExported} />

          {historyError && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-xs text-red-600">
              {historyError}
            </div>
          )}

          <ExportHistory
            records={records}
            onRedownload={(record) => void handleRedownload(record)}
            onClear={() => persistRecords([])}
          />
        </div>
      )}
    </div>
  );
}
