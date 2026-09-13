"use client";

/** 兼容入口（W1-02）：/story-bible → 工作台画布的最新故事设定。
 *
 * 旧深链接解析为确切 Artifact 后导向同一工作台 URL（replace——
 * 不给浏览器历史塞一层跳板），不落到空白首页。
 */

import { useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { artifactsApi } from "@/lib/api-client";
import { Loading } from "@/components/Loading";

export default function StoryBibleRedirectPage() {
  const params = useParams();
  const projectId = String(params.id);
  const router = useRouter();
  const latest = useQuery({
    queryKey: ["nav-sb", projectId],
    queryFn: () => artifactsApi.getLatest(projectId, "story_bible"),
    retry: false,
  });

  useEffect(() => {
    if (latest.data) {
      router.replace(`/projects/${projectId}?artifact=${latest.data.id}`);
    } else if (!latest.isLoading && latest.isError) {
      router.replace(`/projects/${projectId}`);
    }
  }, [latest.data, latest.isLoading, latest.isError, projectId, router]);

  return <Loading text="正在打开故事设定…" />;
}
