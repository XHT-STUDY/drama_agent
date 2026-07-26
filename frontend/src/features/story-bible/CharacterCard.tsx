"use client";

/** CharacterCard — 角色卡片组件 (H-04).
 *
 * 展示单个角色的详细信息，包含：
 * - 姓名、角色定位、年龄段
 * - 性格特征、优势、缺陷
 * - 表层目标和深层需求
 * - 禁止修改标记（forbidden_changes）
 *
 * 空字段显示明确提示，不会崩溃。
 */

import type { CharacterProfile } from "@/types/api";

// ============================================================
// 辅助组件
// ============================================================

/** 标签列表（可选为空） */
function TagList({ items, emptyText }: { items: string[] | undefined; emptyText: string }) {
  if (!items || items.length === 0) {
    return <span className="text-xs text-gray-400">{emptyText}</span>;
  }
  return (
    <div className="flex flex-wrap gap-1">
      {items.map((item, i) => (
        <span
          key={i}
          className="inline-flex rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-700"
        >
          {item}
        </span>
      ))}
    </div>
  );
}

/** 带标签的字段，空时显示占位提示 */
function Field({
  label,
  value,
  className,
}: {
  label: string;
  value: string | undefined;
  className?: string;
}) {
  return (
    <div className={className}>
      <dt className="text-xs font-medium text-gray-500">{label}</dt>
      <dd className="mt-0.5 text-sm text-gray-800">
        {value || <span className="text-gray-400 italic">未设置</span>}
      </dd>
    </div>
  );
}

// ============================================================
// 角色角色标签颜色映射
// ============================================================

const ROLE_COLORS: Record<string, string> = {
  "主角": "bg-amber-100 text-amber-800",
  "反派": "bg-red-100 text-red-800",
  "配角": "bg-blue-100 text-blue-800",
};

function roleBadgeColor(role: string): string {
  // 模糊匹配中文角色名
  if (role.includes("主角") || role.includes("protagonist")) return ROLE_COLORS["主角"];
  if (role.includes("反派") || role.includes("antagonist")) return ROLE_COLORS["反派"];
  return ROLE_COLORS["配角"];
}

// ============================================================
// CharacterCard
// ============================================================

interface Props {
  character: CharacterProfile;
  /** 角色类型标签，如 "主角"、"反派"、"配角" */
  roleLabel: string;
}

export function CharacterCard({ character, roleLabel }: Props) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      {/* 头部：姓名 + 角色标签 */}
      <div className="mb-3 flex items-center gap-2">
        <h3 className="text-base font-semibold text-gray-900">
          {character.name || <span className="text-gray-400 italic">未命名角色</span>}
        </h3>
        <span
          className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${roleBadgeColor(roleLabel)}`}
        >
          {roleLabel}
        </span>
        {character.age_range && (
          <span className="text-xs text-gray-500">{character.age_range}</span>
        )}
      </div>

      {/* 特征标签 */}
      <div className="mb-3 space-y-2">
        <div>
          <span className="text-xs font-medium text-gray-500">性格特征</span>
          <div className="mt-1">
            <TagList items={character.traits} emptyText="未设置性格特征" />
          </div>
        </div>
        <div className="flex gap-4">
          <div className="flex-1">
            <span className="text-xs font-medium text-gray-500">优势</span>
            <div className="mt-1">
              <TagList items={character.strengths} emptyText="未设置" />
            </div>
          </div>
          <div className="flex-1">
            <span className="text-xs font-medium text-gray-500">缺陷</span>
            <div className="mt-1">
              <TagList items={character.flaws} emptyText="未设置" />
            </div>
          </div>
        </div>
      </div>

      {/* 目标与需求 */}
      <dl className="space-y-2">
        <Field label="表层目标" value={character.visible_goal} />
        <Field label="深层需求" value={character.hidden_need} />
      </dl>

      {/* 关系备注 */}
      {character.relationship_notes && character.relationship_notes.length > 0 && (
        <div className="mt-3 border-t border-gray-100 pt-3">
          <span className="text-xs font-medium text-gray-500">关系备注</span>
          <ul className="mt-1 list-inside list-disc space-y-0.5">
            {character.relationship_notes.map((note, i) => (
              <li key={i} className="text-sm text-gray-700">
                {note}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 禁止修改项 */}
      {character.forbidden_changes && character.forbidden_changes.length > 0 && (
        <div className="mt-3 border-t border-red-100 pt-3">
          <span className="text-xs font-medium text-red-600">🚫 禁止修改</span>
          <ul className="mt-1 space-y-0.5">
            {character.forbidden_changes.map((item, i) => (
              <li key={i} className="text-xs text-red-700">
                · {item}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
