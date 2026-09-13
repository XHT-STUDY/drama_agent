"use client";

/** 可导出内容加载（W1-02 抽取：导出页与工作台导出面板共用）。
 *
 * 按类型拉取项目全部 Artifact，取每集最新 valid——已写集数以实际
 * 存在的 Artifact 为准，不依赖 project.current_episode_count。
 */

import { artifactsApi } from "@/lib/api-client";
import {
  latestValidPerEpisode,
  latestValidSingle,
} from "@/lib/artifact-latest";
import type { Artifact } from "@/types/api";

export interface ExportableArtifacts {
  storyBible: Artifact | null;
  outline: Artifact | null;
  scripts: Artifact[];
  evaluations: Artifact[];
  revisions: Artifact[];
}

export async function loadExportableArtifacts(
  projectId: string,
): Promise<ExportableArtifacts> {
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
