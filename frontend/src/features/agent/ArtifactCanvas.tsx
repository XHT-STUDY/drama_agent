"use client";

/** ArtifactCanvas — 同屏作品画布（W1-02）。
 *
 * 复用详情页的正文组件（StoryBibleView / OutlineListView / ScriptView /
 * EvaluationPanel / DiffView），不复制第二套正文渲染。画布只认 URL 里
 * 的确切 Artifact——消息/任务事件不改阅读选择（由工作台保证）。
 *
 * 工具面板（panel=）：read 正文 / evaluation 评估（匹配
 * source_script_artifact_id，跨版本评估如实标注）/ diff 版本对比 /
 * exports 导出（复用导出中心组件）/ sources 资料（知识库）。
 */

import { useEffect, useMemo } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { artifactsApi } from "@/lib/api-client";
import { ErrorMessage } from "@/components/ErrorMessage";
import { Loading } from "@/components/Loading";
import { StoryBibleView } from "@/features/story-bible/StoryBibleView";
import { OutlineListView } from "@/features/outlines/OutlineListView";
import { ScriptView } from "@/features/scripts/ScriptView";
import { EvaluationPanel } from "@/features/evaluations/EvaluationPanel";
import { DiffView } from "@/features/diff/DiffView";
import { ExportSection } from "@/features/exports/ExportSection";
import { ExportHistory } from "@/features/exports/ExportHistory";
import { loadExportableArtifacts } from "@/features/exports/load-exportable";
import type {
  Artifact,
  EpisodeOutlineSetContent,
  EvaluationReportContent,
  ScriptDraftContent,
  StoryBibleContent,
} from "@/types/api";
import type { WorkspacePanel } from "@/hooks/use-workspace-location";

interface Props {
  projectId: string;
  artifactId: string;
  scene: number | null;
  panel: WorkspacePanel;
  compareId: string | null;
  onOpenArtifact: (artifactId: string, panel?: WorkspacePanel) => void;
  onSetPanel: (panel: WorkspacePanel) => void;
  onSetScene: (scene: number | null) => void;
  onSetCompare: (compareId: string | null) => void;
}

const TYPE_LABEL: Record<string, string> = {
  story_bible: "故事设定",
  episode_outline_set: "分集大纲",
  script_draft: "剧本",
  evaluation_report: "评估报告",
};

function contentAs<T>(artifact: Artifact): T {
  return artifact.content as unknown as T;
}

export function ArtifactCanvas(props: Props) {
  const { projectId, artifactId, scene, panel, compareId } = props;

  const artifactQuery = useQuery({
    queryKey: ["artifact", artifactId, projectId],
    queryFn: () => artifactsApi.getById(artifactId, projectId),
    retry: false,
  });
  const artifact = artifactQuery.data;

  // 版本列表（版本切换器 + diff 基线选择共用）
  const versionsQuery = useQuery({
    queryKey: ["artifact-versions", projectId, artifact?.type, artifact?.episode_number],
    enabled: artifact != null,
    queryFn: () =>
      artifactsApi.listVersions(projectId, artifact!.type, artifact!.episode_number),
  });

  // 评估：绑定到该剧本版本的报告（source_script_artifact_id 匹配）
  const evaluationsQuery = useQuery({
    queryKey: ["project-evaluations", projectId],
    enabled: artifact?.type === "script_draft",
    queryFn: () => artifactsApi.listAllByType(projectId, "evaluation_report"),
  });

  const diffQuery = useQuery({
    queryKey: ["artifact-diff", compareId, artifactId],
    enabled: panel === "diff" && compareId !== null,
    queryFn: () => artifactsApi.diff(compareId!, artifactId),
    retry: false,
  });

  // 场景锚点：滚动到该场（ScriptView 渲染 id=scene-N）
  useEffect(() => {
    if (artifact?.type !== "script_draft" || scene == null) return;
    const el = document.getElementById(`scene-${scene}`);
    if (el) el.scrollIntoView({ block: "start" });
  }, [artifact?.type, scene, artifact?.id]);

  // 历史版本提示：该 (type, episode) 存在更新的 valid 版本
  const latestVersion = useMemo(() => {
    const versions = versionsQuery.data ?? [];
    const valid = versions.filter((v) => v.status === "valid");
    return valid.length > 0 ? valid[valid.length - 1] : null;
  }, [versionsQuery.data]);
  const isStaleRead =
    artifact != null &&
    latestVersion != null &&
    latestVersion.id !== artifact.id &&
    artifact.status === "valid";

  if (artifactQuery.isLoading) {
    return <Loading text="正在载入稿件…" />;
  }
  if (artifactQuery.isError || !artifact) {
    return (
      <ErrorMessage
        error={(artifactQuery.error || new Error("稿件读取失败")) as Error}
        onRetry={() => artifactQuery.refetch()}
      />
    );
  }

  const matchedEvaluation =
    artifact.type === "script_draft"
      ? (evaluationsQuery.data ?? []).find(
          (e) =>
            (e.content as unknown as EvaluationReportContent).script_artifact_id === artifact.id,
        ) ?? null
      : null;
  const otherEvaluation =
    artifact.type === "script_draft" && !matchedEvaluation
      ? (evaluationsQuery.data ?? []).find(
          (e) => e.episode_number === artifact.episode_number && e.status === "valid",
        ) ?? null
      : null;

  const openArtifact = props.onOpenArtifact;
  const onVersionChange = (id: string) => openArtifact(id);

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="artifact-canvas">
      {/* 稿件标题条：类型/集数/版本 + 工具面板切换 */}
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-xs text-[var(--text-muted)]" data-testid="canvas-artifact-meta">
          {TYPE_LABEL[artifact.type] ?? artifact.type}
          {artifact.type === "script_draft" ? ` · 第 ${artifact.episode_number} 集` : ""} ·
          v{artifact.version}
        </span>
        <nav className="ml-auto flex flex-wrap gap-1 text-xs" aria-label="画布工具">
          {(["read", "evaluation", "diff", "exports", "sources"] as WorkspacePanel[]).map(
            (p) => (
              <button
                key={p}
                type="button"
                onClick={() => props.onSetPanel(p)}
                className={
                  panel === p
                    ? "rounded-full border border-[var(--accent)] bg-[var(--accent)]/10 px-2.5 py-0.5 text-[var(--accent)]"
                    : "rounded-full border border-[var(--border)] px-2.5 py-0.5 text-[var(--text-muted)] transition-colors hover:border-[var(--accent)]"
                }
                aria-pressed={panel === p}
              >
                {PANEL_LABEL[p]}
              </button>
            ),
          )}
        </nav>
      </div>

      {isStaleRead && (
        <div
          className="mb-2 flex flex-wrap items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs text-amber-700"
          data-testid="stale-read-banner"
        >
          <span>
            你正在看历史版本 v{artifact.version}（最新有效版本 v{latestVersion!.version}）。
            历史版本可阅读与解释；修改请基于最新稿。
          </span>
          <button
            type="button"
            onClick={() => openArtifact(latestVersion!.id)}
            className="rounded border border-amber-300 bg-white px-2 py-0.5 transition-colors hover:bg-amber-100"
          >
            切到 v{latestVersion!.version}
          </button>
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
        {panel === "read" && (
          <>
            {artifact.type === "story_bible" && (
              <StoryBibleView
                content={contentAs<StoryBibleContent>(artifact)}
                artifact={artifact}
                versions={versionsQuery.data ?? [artifact]}
                onVersionChange={onVersionChange}
              />
            )}
            {artifact.type === "episode_outline_set" && (
              <OutlineListView
                content={contentAs<EpisodeOutlineSetContent>(artifact)}
                artifact={artifact}
                versions={versionsQuery.data ?? [artifact]}
                onVersionChange={onVersionChange}
              />
            )}
            {artifact.type === "script_draft" && (
              <div>
                <ScriptView content={contentAs<ScriptDraftContent>(artifact)} />
                {scene != null && (
                  <p className="mt-2 text-xs text-[var(--text-muted)]" data-testid="scene-anchor-hint">
                    已定位到第 {scene} 场（只读锚点）
                  </p>
                )}
              </div>
            )}
            {artifact.type === "evaluation_report" && (
              <EvaluationPanel
                report={contentAs<EvaluationReportContent>(artifact)}
                isLoading={false}
                isError={false}
              />
            )}
          </>
        )}

        {panel === "evaluation" && (
          <div>
            {artifact.type !== "script_draft" ? (
              <p className="text-sm text-[var(--text-muted)]">
                评估面板针对剧本——请在左侧选择某一集剧本后查看。
              </p>
            ) : matchedEvaluation ? (
              <EvaluationPanel
                report={contentAs<EvaluationReportContent>(matchedEvaluation)}
                isLoading={false}
                isError={evaluationsQuery.isError}
                onRetry={() => evaluationsQuery.refetch()}
              />
            ) : otherEvaluation ? (
              <div>
                <p
                  className="mb-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs text-amber-700"
                  data-testid="cross-version-evaluation-hint"
                >
                  这份 v{artifact.version} 还没有绑定的评估——下面展示 v
                  {otherEvaluation.version} 稿的评估（来源版本不同，场景定位已禁用）。
                </p>
                <EvaluationPanel
                  report={contentAs<EvaluationReportContent>(otherEvaluation)}
                  isLoading={false}
                  isError={false}
                />
                <Link
                  href={`/projects/${projectId}/versions?artifact=${otherEvaluation.id}`}
                  className="text-xs text-[var(--accent)] underline-offset-2 hover:underline"
                >
                  在修订工作台查看 v{otherEvaluation.version}
                </Link>
              </div>
            ) : (
              <EvaluationPanel
                report={null}
                isLoading={evaluationsQuery.isLoading}
                isError={evaluationsQuery.isError}
                onRetry={() => evaluationsQuery.refetch()}
              />
            )}
          </div>
        )}

        {panel === "diff" && (
          <div>
            {artifact.type !== "script_draft" ? (
              <p className="text-sm text-[var(--text-muted)]">
                版本对比针对剧本——请选择某一集剧本后对比。
              </p>
            ) : (
              <>
                <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
                  <span className="text-[var(--text-muted)]">对比基线：</span>
                  <select
                    value={compareId ?? ""}
                    onChange={(e) => props.onSetCompare(e.target.value || null)}
                    className="rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1"
                    aria-label="选择对比基线版本"
                  >
                    <option value="">选择基线版本…</option>
                    {(versionsQuery.data ?? [])
                      .filter((v) => v.id !== artifact.id)
                      .map((v) => (
                        <option key={v.id} value={v.id}>
                          v{v.version}（{v.status === "valid" ? "有效" : v.status}）
                        </option>
                      ))}
                  </select>
                  <Link
                    href={`/projects/${projectId}/versions`}
                    className="text-[var(--accent)] underline-offset-2 hover:underline"
                  >
                    打开修订工作台
                  </Link>
                </div>
                {compareId == null ? (
                  <p className="text-sm text-[var(--text-muted)]">
                    选择一个基线版本，与当前 v{artifact.version} 对比。
                  </p>
                ) : diffQuery.data ? (
                  <DiffView diff={diffQuery.data} />
                ) : diffQuery.isError ? (
                  <ErrorMessage
                    error={(diffQuery.error || new Error("对比失败")) as Error}
                    onRetry={() => diffQuery.refetch()}
                  />
                ) : (
                  <Loading text="正在对比…" />
                )}
              </>
            )}
          </div>
        )}

        {panel === "exports" && (
          <ExportsPanel projectId={projectId} />
        )}

        {panel === "sources" && (
          <div className="text-sm">
            <p className="mb-2 text-[var(--text-muted)]">
              参考资料（知识库）影响创作与检索；完整管理在知识库页。
            </p>
            <Link
              href={`/projects/${projectId}/knowledge`}
              className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs text-[var(--accent)] transition-colors hover:border-[var(--accent)]"
            >
              打开知识库管理
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}

const PANEL_LABEL: Record<WorkspacePanel, string> = {
  read: "正文",
  evaluation: "评估",
  diff: "对比",
  exports: "导出",
  sources: "资料",
};

/** 导出面板：复用导出中心组件（自带数据加载与后端导出流程） */
function ExportsPanel({ projectId }: { projectId: string }) {
  const history = useQuery({
    queryKey: ["server-exports", projectId],
    queryFn: async () => {
      const { exportsApi } = await import("@/lib/api-client");
      return exportsApi.list(projectId);
    },
  });
  const available = useQuery({
    queryKey: ["export-data", projectId],
    queryFn: () => loadExportableArtifacts(projectId),
  });

  if (available.isLoading) return <Loading text="正在加载导出内容…" />;
  if (available.isError || !available.data) {
    return (
      <ErrorMessage
        error={(available.error || new Error("导出内容加载失败")) as Error}
        onRetry={() => available.refetch()}
      />
    );
  }
  return (
    <div className="space-y-4">
      <ExportSection projectId={projectId} available={available.data} />
      {history.data && (
        <ExportHistory projectId={projectId} artifacts={history.data.items} />
      )}
    </div>
  );
}
