/** 项目卡片组件 (H-02).
 *
 * 在项目列表中展示单个项目的摘要信息。
 * 可选的选择框位于标题左侧，删除按钮位于卡片右下角，
 * 两者均独立于整卡跳转链接之外。
 */

import Link from "next/link";
import type { Project } from "@/types/api";
import { StatusBadge } from "./StatusBadge";

interface Props {
  project: Project;
  onDelete?: (project: Project) => void;
  deleting?: boolean;
  selected?: boolean;
  onToggleSelect?: (id: string) => void;
}

export function ProjectCard({
  project,
  onDelete,
  deleting = false,
  selected = false,
  onToggleSelect,
}: Props) {
  const created = new Date(project.created_at).toLocaleDateString("zh-CN", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
  const name = project.title || "未命名项目";

  return (
    <div className="rounded-lg border border-gray-200 bg-white shadow-sm transition hover:shadow-md hover:border-blue-300">
      <div className="flex items-start justify-between gap-3 p-5">
        {onToggleSelect && (
          <input
            type="checkbox"
            checked={selected}
            onChange={() => onToggleSelect(project.id)}
            aria-label={`选择项目 ${name}`}
            className="mt-1 h-4 w-4 shrink-0 cursor-pointer rounded border-gray-300 accent-blue-600"
          />
        )}
        <Link
          href={`/projects/${project.id}`}
          className="min-w-0 flex-1"
        >
          <h3 className="truncate text-base font-semibold text-gray-900">
            {name}
          </h3>
          <p className="mt-1 text-sm text-gray-500">
            目标 {project.target_episode_count} 集 · 已完成 {project.current_episode_count} 集
          </p>
        </Link>
        <StatusBadge status={project.status} />
      </div>
      <div className="mt-4 flex items-center justify-between px-5 pb-4 text-xs text-gray-400">
        <span>创建于 {created}</span>
        {onDelete && (
          <button
            type="button"
            onClick={() => onDelete(project)}
            disabled={deleting}
            aria-label={`删除项目 ${name}`}
            className="rounded px-2 py-1 text-xs text-gray-400 hover:text-red-600 hover:bg-red-50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {deleting ? "删除中…" : "删除"}
          </button>
        )}
      </div>
    </div>
  );
}
