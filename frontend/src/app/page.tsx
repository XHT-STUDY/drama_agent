"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

/** 首页 — 重定向到项目列表 */
export default function Home() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/projects");
  }, [router]);

  return (
    <div className="flex items-center justify-center py-20">
      <p className="text-gray-400">跳转中…</p>
    </div>
  );
}
