/** Artifact 集合的"最新有效版本"选取（W1-02 共用）。 */

import type { Artifact } from "@/types/api";

/** 每集最新 valid（同集多版本取 version 最大；返回按集号升序） */
export function latestValidPerEpisode(artifacts: Artifact[]): Artifact[] {
  const byEpisode = new Map<number, Artifact>();
  for (const a of artifacts) {
    if (a.status !== "valid") continue;
    const cur = byEpisode.get(a.episode_number);
    if (!cur || a.version > cur.version) byEpisode.set(a.episode_number, a);
  }
  return [...byEpisode.values()].sort((x, y) => x.episode_number - y.episode_number);
}

/** 单槽类型（设定/大纲）的最新 valid */
export function latestValidSingle(artifacts: Artifact[]): Artifact | null {
  return latestValidPerEpisode(artifacts)[0] ?? null;
}
