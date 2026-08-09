"use client";

/** RevisionPlanView — 修订计划展示组件 (H-06).
 *
 * 渲染 revision_plan Artifact 的 content：
 * - 集数、最大变更比例
 * - 用户补充要求（user_instruction，琥珀块）
 * - 锁定事实（locked_facts，琥珀块，注明修订不得违反）
 * - 修订操作列表（operation：目标场景 / 依据 issue / 指令 / 必须保留 / 预期效果）
 */

import type { RevisionPlanContent } from "@/types/api";

interface Props {
  plan: RevisionPlanContent;
}

export function RevisionPlanView({ plan }: Props) {
  return (
    <div className="space-y-4">
      {/* 头部 */}
      <div className="rounded-lg border border-gray-200 bg-white p-5">
        <div className="flex flex-wrap items-center gap-3">
          <h3 className="text-base font-bold text-gray-900">
            第 {plan.episode_number} 集修订计划
          </h3>
          <span className="text-xs text-gray-400">
            最大变更比例 {(plan.max_change_ratio * 100).toFixed(0)}%
          </span>
        </div>

        {/* 用户补充要求 */}
        {plan.user_instruction && (
          <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
            <span className="text-xs font-medium text-amber-700">📝 用户补充要求</span>
            <p className="mt-1 text-sm text-amber-800">{plan.user_instruction}</p>
          </div>
        )}

        {/* 锁定事实 */}
        {plan.locked_facts.length > 0 && (
          <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
            <span className="text-xs font-medium text-amber-700">
              🔒 锁定事实（修订不得违反）
            </span>
            <ul className="mt-1 space-y-1">
              {plan.locked_facts.map((fact, i) => (
                <li key={i} className="text-sm text-amber-800">· {fact}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* 修订操作 */}
      <div className="rounded-lg border border-gray-200 bg-white p-5">
        <h4 className="mb-3 text-sm font-semibold text-gray-700">修订操作</h4>
        {plan.operations.length === 0 ? (
          <p className="text-sm text-gray-400">无具体修订操作</p>
        ) : (
          <div className="space-y-3">
            {plan.operations.map((op) => (
              <div key={op.operation_id} className="rounded-lg border border-gray-100 bg-gray-50 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-xs text-gray-400">{op.operation_id}</span>
                  <span className="inline-flex rounded bg-blue-100 px-1.5 py-0.5 text-xs font-medium text-blue-700">
                    {op.target_scene_number !== null
                      ? `第 ${op.target_scene_number} 场`
                      : "跨场景"}
                  </span>
                  {op.issue_ids.map((id) => (
                    <span
                      key={id}
                      className="inline-flex rounded bg-gray-100 px-1.5 py-0.5 text-xs text-gray-500"
                    >
                      #{id}
                    </span>
                  ))}
                </div>
                <p className="mt-2 text-sm text-gray-800">{op.instruction}</p>
                {op.preserve.length > 0 && (
                  <p className="mt-1 text-xs text-amber-700">
                    必须保留：{op.preserve.join("；")}
                  </p>
                )}
                {op.expected_effect && (
                  <p className="mt-1 text-xs text-gray-400">
                    预期效果：{op.expected_effect}
                  </p>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
