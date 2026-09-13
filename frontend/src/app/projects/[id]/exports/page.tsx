"use client";

/** 兼容入口（W1-02）：/exports → 工作台画布的导出面板。
 *
 * 导出发起与服务端历史已由工作台 panel=exports 承载（同一套
 * ExportSection/ExportHistory 组件）；旧深链接导向工作台 URL。
 */

import { useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { Loading } from "@/components/Loading";

export default function ExportsRedirectPage() {
  const params = useParams();
  const projectId = String(params.id);
  const router = useRouter();

  useEffect(() => {
    router.replace(`/projects/${projectId}?panel=exports`);
  }, [projectId, router]);

  return <Loading text="正在打开导出中心…" />;
}
