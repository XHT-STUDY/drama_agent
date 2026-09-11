"use client";

/** 剧本详情页 (H-05).
 *
 * 三栏布局：
 * - 左侧：EpisodeNav 集数导航
 * - 中央：ScriptView 剧本正文（场景 + 对白）
 * - 右侧：EvaluationPanel 评估报告
 *
 * 功能：
 * - 从 API 获取 script_draft Artifact
 * - 从 API 获取 evaluation_report Artifact（如有）
 * - issue 点击 → 滚动定位到场景
 * - 手动发起重新评估（POST runs action=evaluate）
 * - 版本与评估绑定显示
 */

import { useState, useCallback, useMemo, useRef } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { useQuery, useMutation } from "@tanstack/react-query";
import { artifactsApi, projectsApi, runsApi } from "@/lib/api-client";
import { Loading } from "@/components/Loading";
import { ErrorMessage } from "@/components/ErrorMessage";
import { Empty } from "@/components/Empty";
import { EpisodeNav } from "@/features/episodes/EpisodeNav";
import type { EpisodeNavItem } from "@/features/episodes/EpisodeNav";
import { ScriptView } from "@/features/scripts/ScriptView";
import { EvaluationPanel } from "@/features/evaluations/EvaluationPanel";
import type { ScriptDraftContent, EvaluationReportContent } from "@/types/api";

export default function ScriptDetailPage() {
  const params = useParams();
  const router = useRouter();
  const projectId = String(params.id);
  const episodeNum = parseInt(String(params.episode), 10);
  const contentRef = useRef<HTMLDivElement>(null);

  // ---- Script Artifact ----
  const {
    data: scriptArtifact,
    isLoading: scriptLoading,
    isError: scriptError,
    error: scriptErr,
    refetch: refetchScript,
  } = useQuery({
    queryKey: ["artifact", projectId, "script_draft", episodeNum, "latest"],
    queryFn: () => artifactsApi.getLatest(projectId, "script_draft", episodeNum),
  });

  const scriptContent = scriptArtifact?.content as ScriptDraftContent | undefined;

  // ---- Evaluation Artifact (阶段 E 未实现，此处兼容不存在的情况) ----
  const {
    data: evalArtifact,
    isLoading: evalLoading,
    isError: evalError,
    refetch: refetchEval,
  } = useQuery({
    queryKey: ["artifact", projectId, "evaluation_report", episodeNum, "latest"],
    queryFn: () => artifactsApi.getLatest(projectId, "evaluation_report", episodeNum),
    retry: false, // 评估可能不存在，不重试
  });

  const evalContent = evalArtifact?.content as EvaluationReportContent | undefined;

  // ---- 项目信息（集数导航的长度来源：目标总集数） ----
  const { data: project } = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => projectsApi.get(projectId),
  });

  // ---- 各集剧本/评估状态（填充左侧导航的 ●/✓ 图标） ----
  const { data: scriptArtifacts } = useQuery({
    queryKey: ["artifacts", projectId, "script_draft", "all"],
    queryFn: () => artifactsApi.listAllByType(projectId, "script_draft"),
  });
  const { data: evalArtifacts } = useQuery({
    queryKey: ["artifacts", projectId, "evaluation_report", "all"],
    queryFn: () => artifactsApi.listAllByType(projectId, "evaluation_report"),
    retry: false,
  });

  // ---- 评估中状态 ----
  const [isEvaluating, setIsEvaluating] = useState(false);

  // ---- 重新评估 Mutation ----
  const evaluateMutation = useMutation({
    mutationFn: async () => {
      return runsApi.create(projectId, {
        action: "evaluate",
        options: {
          user_input: "",
          source_type: "idea",
        },
      });
    },
    onSuccess: () => {
      setIsEvaluating(true);
    },
  });

  const handleReEvaluate = useCallback(() => {
    evaluateMutation.mutate();
  }, [evaluateMutation]);

  // ---- Issue → Scene 定位 ----
  const handleLocateScene = useCallback((sceneNumber: number) => {
    const el = document.getElementById(`scene-${sceneNumber}`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      // 短暂高亮后清除
      el.classList.add("ring-2", "ring-orange-400");
      setTimeout(() => {
        el.classList.remove("ring-2", "ring-orange-400");
      }, 2000);
    }
  }, []);

  // ---- 集数切换 ----
  const handleEpisodeSelect = useCallback((ep: number) => {
    router.push(`/projects/${projectId}/scripts/${ep}`);
  }, [projectId, router]);

  // ---- 构建 EpisodeNav 数据 ----
  // 长度 = 项目目标总集数（项目加载前先以当前集号兜底，避免闪现错误数量）；
  // 各集状态从 Artifact 列表派生（列表按创建时间倒序，每集取最新 valid 版本），
  // 当前集额外以已加载的最新 Artifact 为准
  const scriptEpisodes = useMemo(() => {
    const titles = new Map<number, string | undefined>();
    for (const a of scriptArtifacts ?? []) {
      if (a.status !== "valid" || titles.has(a.episode_number)) continue;
      const t = (a.content as { title?: unknown } | null)?.title;
      titles.set(a.episode_number, typeof t === "string" ? t : undefined);
    }
    return titles;
  }, [scriptArtifacts]);

  const evalEpisodes = useMemo(
    () =>
      new Set(
        (evalArtifacts ?? [])
          .filter((a) => a.status === "valid")
          .map((a) => a.episode_number),
      ),
    [evalArtifacts],
  );

  const targetCount = Math.max(project?.target_episode_count ?? 0, episodeNum, 1);

  const navItems: EpisodeNavItem[] = Array.from({ length: targetCount }, (_, i) => {
    const num = i + 1;
    const isCurrent = num === episodeNum;
    return {
      episode_number: num,
      title: isCurrent ? scriptContent?.title : scriptEpisodes.get(num),
      hasScript: isCurrent ? !!scriptContent : scriptEpisodes.has(num),
      hasEvaluation: isCurrent
        ? !!evalContent && !evalError
        : evalEpisodes.has(num),
    };
  });

  // ---- 加载 ----
  if (scriptLoading && !scriptContent) {
    return (
      <div>
        <BackLink projectId={projectId} />
        <Loading text="正在加载剧本…" />
      </div>
    );
  }

  // ---- 错误 ----
  if (scriptError && !scriptContent) {
    return (
      <div>
        <BackLink projectId={projectId} />
        <ErrorMessage
          error={(scriptErr as Error) || new Error("剧本加载失败")}
          onRetry={() => refetchScript()}
        />
      </div>
    );
  }

  // ---- 无剧本 ----
  if (!scriptContent) {
    return (
      <div>
        <BackLink projectId={projectId} />
        <Empty
          title={`第 ${episodeNum} 集剧本未生成`}
          description={`该集尚未生成剧本（目标共 ${project?.target_episode_count ?? "？"} 集），请先运行创作工作流生成第 ${episodeNum} 集。`}
          actionLabel="返回项目工作台"
          actionHref={`/projects/${projectId}`}
        />
      </div>
    );
  }

  // ---- 正常三栏布局 ----
  return (
    <div>
      <BackLink projectId={projectId} />

      <div className="flex gap-6">
        {/* 左侧：集数导航 */}
        <aside className="w-44 shrink-0">
          <div className="sticky top-6">
            <EpisodeNav
              episodes={navItems}
              currentEpisode={episodeNum}
              targetCount={targetCount}
              onSelect={handleEpisodeSelect}
            />
          </div>
        </aside>

        {/* 中央：剧本正文 */}
        <div className="min-w-0 flex-1" ref={contentRef}>
          <ScriptView content={scriptContent} />
        </div>

        {/* 右侧：评估面板 */}
        <aside className="w-72 shrink-0">
          <div className="sticky top-6 max-h-[calc(100vh-6rem)] overflow-auto">
            <EvaluationPanel
              report={evalContent ?? null}
              isLoading={evalLoading}
              isError={!!evalError}
              isEvaluating={isEvaluating}
              onReEvaluate={handleReEvaluate}
              onLocateScene={handleLocateScene}
              onRetry={() => refetchEval()}
            />
          </div>
        </aside>
      </div>
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
