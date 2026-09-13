"use client";

/** 兼容入口（W1-02）：/outline → 工作台画布的最新分集大纲。 */

import { useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { artifactsApi } from "@/lib/api-client";
import { Loading } from "@/components/Loading";

export default function OutlineRedirectPage() {
  const params = useParams();
  const projectId = String(params.id);
  const router = useRouter();
  const latest = useQuery({
    queryKey: ["nav-outline", projectId],
    queryFn: () => artifactsApi.getLatest(projectId, "episode_outline_set"),
    retry: false,
  });

  useEffect(() => {
    if (latest.data) {
      router.replace(`/projects/${projectId}?artifact=${latest.data.id}`);
    } else if (!latest.isLoading && latest.isError) {
      router.replace(`/projects/${projectId}`);
    }
  }, [latest.data, latest.isLoading, latest.isError, projectId, router]);

  return <Loading text="正在打开分集大纲…" />;
}
