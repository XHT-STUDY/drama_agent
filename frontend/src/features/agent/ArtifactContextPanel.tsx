"use client";

/** ArtifactContextPanel — 右栏：最新产物索引 + 当前活动上下文（J-11）。
 *
 * - 最新 Story Bible / 分集大纲 / 各集剧本索引（点击设为 active context，
 *   下一轮 Turn 携带该上下文；同时链接到现有页面）
 * - 当前 active context 展示与清除
 */

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { artifactsApi } from "@/lib/api-client";
import type { ActiveArtifactContext, Artifact } from "@/types/api";

interface Props {
  projectId: string;
  activeContext: ActiveArtifactContext | null;
  onActiveContextChange: (context: ActiveArtifactContext | null) => void;
}

function toContext(artifact: Artifact): ActiveArtifactContext {
  return {
    artifact_id: artifact.id,
    artifact_type: artifact.type,
    episode_number: artifact.episode_number,
    version: artifact.version,
    checksum: artifact.checksum ?? null,
  };
}

const TYPE_LABEL: Record<string, string> = {
  story_bible: "Story Bible",
  episode_outline_set: "分集大纲",
  script_draft: "剧本",
};

function pageHref(projectId: string, artifact: Artifact): string {
  if (artifact.type === "story_bible") return `/projects/${projectId}/story-bible`;
  if (artifact.type === "episode_outline_set") return `/projects/${projectId}/outline`;
  return `/projects/${projectId}/scripts/${artifact.episode_number}`;
}

export function ArtifactContextPanel({
  projectId,
  activeContext,
  onActiveContextChange,
}: Props) {
  const storyBible = useQuery({
    queryKey: ["agent-context", projectId, "story_bible"],
    queryFn: () => artifactsApi.getLatest(projectId, "story_bible"),
    retry: false,
  });
  const outline = useQuery({
    queryKey: ["agent-context", projectId, "episode_outline_set"],
    queryFn: () => artifactsApi.getLatest(projectId, "episode_outline_set"),
    retry: false,
  });
  const scripts = useQuery({
    queryKey: ["agent-context", projectId, "script_draft"],
    queryFn: () => artifactsApi.listVersions(projectId, "script_draft"),
    retry: false,
  });

  const latestScriptsPerEpisode = new Map<number, Artifact>();
  for (const s of scripts.data ?? []) {
    const existing = latestScriptsPerEpisode.get(s.episode_number);
    if (!existing || s.version > existing.version) {
      latestScriptsPerEpisode.set(s.episode_number, s);
    }
  }
  const scriptList = [...latestScriptsPerEpisode.values()].sort(
    (a, b) => a.episode_number - b.episode_number,
  );

  function ContextRow({ artifact }: { artifact: Artifact }) {
    const selected = activeContext?.artifact_id === artifact.id;
    const label = `${TYPE_LABEL[artifact.type] ?? artifact.type} v${artifact.version}${
      artifact.type === "script_draft" ? ` · 第 ${artifact.episode_number} 集` : ""
    }`;
    return (
      <div
        className={`flex items-center justify-between gap-2 rounded border px-2 py-1.5 text-xs transition-state ${
          selected
            ? "border-[var(--accent)] bg-[var(--surface-muted)] text-[var(--accent)]"
            : "border-[var(--border)]"
        }`}
      >
        <button
          type="button"
          onClick={() => onActiveContextChange(selected ? null : toContext(artifact))}
          aria-pressed={selected}
          className="min-w-0 flex-1 truncate text-left"
          data-testid={`context-${artifact.type}-${artifact.episode_number}`}
        >
          {selected ? "◉ " : "○ "}
          {label}
        </button>
        <Link
          href={pageHref(projectId, artifact)}
          className="shrink-0 text-[var(--accent)] underline"
        >
          查看
        </Link>
      </div>
    );
  }

  return (
    <section aria-label="项目上下文" className="space-y-4">
      <h2 className="text-sm font-semibold">项目上下文</h2>

      {activeContext && (
        <div
          className="rounded-lg border border-[var(--accent)] bg-[var(--surface-muted)] p-2 text-xs"
          data-testid="active-context"
        >
          <p className="font-medium">当前选中：{TYPE_LABEL[activeContext.artifact_type] ?? activeContext.artifact_type}</p>
          <p className="mt-0.5 text-[var(--text-muted)]">
            {activeContext.episode_number != null ? `第 ${activeContext.episode_number} 集 · ` : ""}
            版本 {activeContext.version ?? "—"}
          </p>
          <button
            type="button"
            onClick={() => onActiveContextChange(null)}
            className="mt-1 underline text-[var(--text-muted)]"
          >
            清除选中
          </button>
        </div>
      )}

      <div className="space-y-2">
        {storyBible.data && <ContextRow artifact={storyBible.data} />}
        {outline.data && <ContextRow artifact={outline.data} />}
        {scriptList.map((s) => (
          <ContextRow key={s.id} artifact={s} />
        ))}
        {!storyBible.data && !outline.data && scriptList.length === 0 && (
          <p className="text-xs text-[var(--text-muted)]">项目还没有产物，先发送一条创作指令。</p>
        )}
      </div>
    </section>
  );
}
