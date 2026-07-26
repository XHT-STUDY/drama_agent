/** 项目卡片组件 (H-02).
 *
 * 在项目列表中展示单个项目的摘要信息。
 */

import Link from "next/link";
import type { Project } from "@/types/api";
import { StatusBadge } from "./StatusBadge";

interface Props {
  project: Project;
}

export function ProjectCard({ project }: Props) {
  const created = new Date(project.created_at).toLocaleDateString("zh-CN", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });

  return (
    <Link
      href={`/projects/${project.id}`}
      className="block rounded-lg border border-gray-200 bg-white p-5 shadow-sm transition hover:shadow-md hover:border-blue-300"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-base font-semibold text-gray-900">
            {project.title || "未命名项目"}
          </h3>
          <p className="mt-1 text-sm text-gray-500">
            目标 {project.target_episode_count} 集 · 已完成 {project.current_episode_count} 集
          </p>
        </div>
        <StatusBadge status={project.status} />
      </div>
      <div className="mt-4 text-xs text-gray-400">
        创建于 {created}
      </div>
    </Link>
  );
}
