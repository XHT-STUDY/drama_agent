"use client";

/** 兼容入口（W1-02）：/scripts/[episode] → 工作台画布的该集最新剧本。 */

import { useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { artifactsApi } from "@/lib/api-client";
import { Loading } from "@/components/Loading";

export default function ScriptRedirectPage() {
  const params = useParams();
  const projectId = String(params.id);
  const episode = Number.parseInt(String(params.episode), 10);
  const router = useRouter();
  const latest = useQuery({
    queryKey: ["script-latest", projectId, episode],
    queryFn: () => artifactsApi.getLatest(projectId, "script_draft", episode),
    // 单次瞬时失败会让页面落到项目首页(选错稿)——允许一次重试
    retry: 1,
    retryDelay: 300,
    enabled: Number.isInteger(episode) && episode >= 1,
  });

  useEffect(() => {
    if (latest.data) {
      router.replace(`/projects/${projectId}?artifact=${latest.data.id}`);
    } else if (latest.isError) {
      router.replace(`/projects/${projectId}`);
    }
  }, [latest.data, latest.isError, projectId, router]);

  return <Loading text="正在打开剧本…" />;
}
