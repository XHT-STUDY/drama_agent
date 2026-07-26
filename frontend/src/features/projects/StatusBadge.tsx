/** 项目状态标签组件 (H-02).
 *
 * 根据项目状态显示不同颜色的徽章。
 */

import type { ProjectStatus } from "@/types/api";

/** 状态 → 颜色映射 */
const STATUS_STYLE: Record<ProjectStatus, string> = {
  draft:      "bg-gray-100 text-gray-600",
  planning:   "bg-blue-50 text-blue-700",
  writing:    "bg-yellow-50 text-yellow-700",
  evaluating: "bg-purple-50 text-purple-700",
  revising:   "bg-orange-50 text-orange-700",
  completed:  "bg-green-50 text-green-700",
  archived:   "bg-gray-200 text-gray-500",
};

/** 状态 → 中文标签 */
const STATUS_LABEL: Record<ProjectStatus, string> = {
  draft:      "草稿",
  planning:   "规划中",
  writing:    "创作中",
  evaluating: "评估中",
  revising:   "修订中",
  completed:  "已完成",
  archived:   "已归档",
};

export function StatusBadge({ status }: { status: ProjectStatus }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${STATUS_STYLE[status] || STATUS_STYLE.draft}`}
    >
      {STATUS_LABEL[status] || status}
    </span>
  );
}
