"use client";

/** ContinuityCheckView — 连续性检查结果展示组件 (H-06).
 *
 * 渲染 continuity_check Artifact 的 content：
 * - pass → 绿色通过横幅；fail → 红色横幅 + 具体违规列表
 * - 每个违规：类型中文标签 + 来源徽章（规则/语义）+ 目标/期望/实际/证据
 * - 警告单独琥珀列表；页脚小字显示已执行的检查项
 */

import { CONTINUITY_VIOLATION_LABELS } from "@/types/api";
import type { ContinuityCheckContent } from "@/types/api";

interface Props {
  result: ContinuityCheckContent;
}

export function ContinuityCheckView({ result }: Props) {
  const failed = result.status === "fail";

  return (
    <div className="space-y-3">
      {/* 状态横幅 */}
      <div
        className={`rounded-lg border px-4 py-2 text-sm font-medium ${
          failed
            ? "border-red-200 bg-red-50 text-red-700"
            : "border-green-200 bg-green-50 text-green-700"
        }`}
      >
        {failed ? "❌ 连续性检查失败，需人工复核" : "✅ 连续性检查通过"}
      </div>

      {/* 违规列表（fail 时） */}
      {failed && result.violations.length > 0 && (
        <div className="rounded-lg border border-red-200 bg-white p-4">
          <h4 className="mb-2 text-sm font-semibold text-red-700">违规明细</h4>
          <div className="space-y-3">
            {result.violations.map((v, i) => (
              <div key={i} className="rounded-lg border border-red-100 bg-red-50/50 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="inline-flex rounded bg-red-100 px-1.5 py-0.5 text-xs font-medium text-red-700">
                    {CONTINUITY_VIOLATION_LABELS[v.kind] || v.kind}
                  </span>
                  <span
                    className={`inline-flex rounded px-1.5 py-0.5 text-xs ${
                      v.source === "semantic"
                        ? "bg-purple-100 text-purple-700"
                        : "bg-gray-100 text-gray-500"
                    }`}
                  >
                    {v.source === "semantic" ? "语义检查" : "规则检查"}
                  </span>
                </div>
                <div className="mt-2 space-y-1 text-xs text-gray-600">
                  <p><span className="text-gray-400">目标：</span>{v.target || "—"}</p>
                  <p><span className="text-gray-400">期望：</span>{v.expected || "—"}</p>
                  <p><span className="text-gray-400">实际：</span>{v.actual || "—"}</p>
                </div>
                {v.evidence && (
                  <p className="mt-2 border-l-2 border-red-200 pl-2 text-xs italic text-gray-500">
                    {v.evidence}
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 警告列表 */}
      {result.warnings.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-4">
          <h4 className="mb-2 text-sm font-semibold text-amber-700">警告</h4>
          <ul className="space-y-1">
            {result.warnings.map((w, i) => (
              <li key={i} className="text-xs text-amber-800">
                <span className="font-medium">
                  {CONTINUITY_VIOLATION_LABELS[w.kind] || w.kind}：
                </span>
                {w.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 已执行检查项 */}
      <p className="text-xs text-gray-400">
        已执行规则检查 {result.rule_checks_run.length} 项 · 语义检查{" "}
        {result.semantic_checks_run.length} 项
      </p>
    </div>
  );
}
