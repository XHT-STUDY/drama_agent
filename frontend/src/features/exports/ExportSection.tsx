"use client";

/** 导出中心 — 内容选择与后端导出（H-07，W1-05 固定版本版）.
 *
 * 发起时把所选 kind 的当前版本作为显式 Artifact ID 冻结进请求——
 * 排队期间产生的新版本不会混入本次导出；Run 完成后用返回的
 * result_artifact_ids（export_file Artifact）触发固定下载。
 */

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { exportsApi, runsApi } from "@/lib/api-client";
import { EXPORT_KIND_LABELS, triggerDownload } from "@/lib/export";
import type { Artifact, ExportContentKind, ExportFormat } from "@/types/api";
import type { ExportableArtifacts } from "@/types/api";

function availability(available: ExportableArtifacts): Record<ExportContentKind, boolean> {
  return {
    story_bible: available.storyBible !== null,
    outline: available.outline !== null,
    script: available.scripts.length > 0,
    evaluation: available.evaluations.length > 0,
    revision: available.revisions.length > 0,
  };
}

/** 确切版本清单（第N集vM 截断展示）：导出前让作者看到将固定哪些版本 */
function versionList(artifacts: Artifact[]): string {
  const items = artifacts.map((a) => `第${a.episode_number}集v${a.version}`);
  return items.length > 3 ? `${items.slice(0, 3).join("/")}…` : items.join("/");
}

function describe(kind: ExportContentKind, available: ExportableArtifacts): string {
  switch (kind) {
    case "story_bible":
      return available.storyBible
        ? `最新 v${available.storyBible.version}（本次导出将固定此版本）`
        : "尚未生成";
    case "outline":
      return available.outline
        ? `最新 v${available.outline.version}（本次导出将固定此版本）`
        : "尚未生成";
    case "script":
      return available.scripts.length > 0
        ? `已写 ${available.scripts.length} 集（${versionList(available.scripts)}，导出时冻结）`
        : "尚无剧本";
    case "evaluation":
      return available.evaluations.length > 0
        ? `已评估 ${available.evaluations.length} 集（${versionList(available.evaluations)}）`
        : "尚无评估";
    case "revision":
      return available.revisions.length > 0
        ? `共 ${available.revisions.length} 份（${versionList(available.revisions)}）`
        : "暂无修订";
  }
}

const ALL_KINDS: ExportContentKind[] = ["story_bible", "outline", "script", "evaluation", "revision"];

interface Props {
  projectId: string;
  available: ExportableArtifacts;
}

export function ExportSection({ projectId, available }: Props) {
  const [selected, setSelected] = useState<Set<ExportContentKind>>(
    () => new Set(ALL_KINDS),
  );
  const [format, setFormat] = useState<ExportFormat>("markdown");
  const [error, setError] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const downloadedRef = useRef<string | null>(null);

  const avail = availability(available);
  const hasSelection = selected.size > 0;

  const toggleKind = (kind: ExportContentKind): void => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) {
        next.delete(kind);
      } else {
        next.add(kind);
      }
      return next;
    });
  };

  const exportMutation = useMutation({
    mutationFn: async () => {
      // 显式冻结：所选 kind 的当前最新版本（可用性已保证非空）
      const artifactIds: Record<string, string[]> = {};
      const kinds = ALL_KINDS.filter((k) => selected.has(k) && avail[k]);
      for (const kind of kinds) {
        if (kind === "story_bible" && available.storyBible) {
          artifactIds[kind] = [available.storyBible.id];
        } else if (kind === "outline" && available.outline) {
          artifactIds[kind] = [available.outline.id];
        } else if (kind === "script") {
          artifactIds[kind] = available.scripts.map((a) => a.id);
        } else if (kind === "evaluation") {
          artifactIds[kind] = available.evaluations.map((a) => a.id);
        } else if (kind === "revision") {
          artifactIds[kind] = available.revisions.map((a) => a.id);
        }
      }
      return exportsApi.create(projectId, {
        kinds,
        format,
        artifact_ids: artifactIds,
        idempotency_key: crypto.randomUUID(),
      });
    },
    onError: (e) => {
      setError(e instanceof Error ? e.message : "导出发起失败，请重试");
    },
  });

  const runId = exportMutation.data?.run_id ?? null;
  const runQuery = useQuery({
    queryKey: ["export-run", runId],
    enabled: !!runId,
    queryFn: () => runsApi.get(runId!),
    refetchInterval: (query) =>
      query.state.data?.status === "queued" || query.state.data?.status === "running"
        ? 1500
        : false,
  });

  const run = runQuery.data;
  const exportArtifactId = run?.result_artifact_ids?.[0] ?? null;
  // 接受时的选择告警（如错配评估被剔除）必须显式提醒，不静默少导。
  // 发起响应（202 Run）即携带，轮询 Run 同源补充
  const warningsSource = exportMutation.data?.config_snapshot ?? run?.config_snapshot;
  const selectionWarnings: string[] =
    ((warningsSource?.options as Record<string, unknown> | undefined)
      ?.selection_warnings as string[] | undefined) ?? [];

  // 完成 → 固定下载 + 刷新历史（每次导出只触发一次）
  useEffect(() => {
    if (run?.status === "completed" && exportArtifactId && downloadedRef.current !== run.run_id) {
      downloadedRef.current = run.run_id;
      triggerDownload(exportsApi.downloadUrl(exportArtifactId, projectId));
      void queryClient.invalidateQueries({ queryKey: ["server-exports", projectId] });
    }
    if (run?.status === "failed") {
      setError(
        `导出失败${run.error_code ? `（${run.error_code}）` : ""}：${run.error_detail ?? "请重试"}`,
      );
    }
  }, [run, exportArtifactId, projectId, queryClient]);

  const exporting =
    exportMutation.isPending ||
    run?.status === "queued" ||
    run?.status === "running";

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4">
      <h2 className="mb-3 text-lg font-semibold text-gray-800">选择导出内容</h2>

      {/* 内容类型多选 */}
      <div className="mb-4 space-y-2">
        {ALL_KINDS.map((kind) => {
          const checked = selected.has(kind);
          const has = avail[kind];
          return (
            <label
              key={kind}
              className={`flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-2.5 transition-colors ${
                checked
                  ? "border-blue-300 bg-blue-50/50"
                  : "border-gray-200 hover:border-gray-300"
              } ${has ? "" : "opacity-60"}`}
            >
              <input
                type="checkbox"
                checked={checked}
                onChange={() => toggleKind(kind)}
                disabled={!has}
                className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-400"
              />
              <span className="text-sm font-medium text-gray-700">
                {EXPORT_KIND_LABELS[kind]}
              </span>
              <span className="text-xs text-gray-400">{describe(kind, available)}</span>
            </label>
          );
        })}
      </div>

      {/* 格式单选 */}
      <div className="mb-4">
        <span className="mb-2 block text-xs font-medium text-gray-600">导出格式</span>
        <div className="flex gap-4">
          {(["markdown", "docx"] as ExportFormat[]).map((f) => (
            <label
              key={f}
              className={`flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm ${
                format === f
                  ? "border-blue-300 bg-blue-50/50 text-blue-700"
                  : "border-gray-200 text-gray-600 hover:border-gray-300"
              }`}
            >
              <input
                type="radio"
                name="export-format"
                value={f}
                checked={format === f}
                onChange={() => setFormat(f)}
                className="h-4 w-4 text-blue-600 focus:ring-blue-400"
              />
              {f === "markdown" ? "Markdown (.md)" : "Word (.docx)"}
            </label>
          ))}
        </div>
      </div>

      {/* 生成下载 */}
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => {
            setError(null);
            downloadedRef.current = null;
            exportMutation.mutate();
          }}
          disabled={!hasSelection || exporting}
          className="rounded-lg bg-blue-600 px-5 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 transition-colors disabled:cursor-not-allowed disabled:opacity-50"
        >
          {exporting ? "正在生成…" : "📦 生成并下载"}
        </button>
        {run?.status === "queued" || run?.status === "running" ? (
          <span className="text-xs text-gray-500" data-testid="export-run-status">
            导出任务执行中，完成后自动下载
          </span>
        ) : null}
        {error && <span className="text-xs text-red-600" data-testid="export-error">{error}</span>}
      </div>

      {selectionWarnings.length > 0 && (
        <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700" data-testid="selection-warnings">
          {selectionWarnings.map((w) => (
            <p key={w}>{w}</p>
          ))}
        </div>
      )}

      {!hasSelection && (
        <p className="mt-2 text-xs text-amber-600">请至少选择一种要导出的内容</p>
      )}
    </section>
  );
}
