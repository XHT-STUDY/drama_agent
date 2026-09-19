"use client";

/** McpToolActionCard — MCP 外部工具计划详情（MCP-03）。
 *
 * 在 ActionPlanCard 内渲染（确认/拒绝按钮与状态由计划卡统一承载）：
 * - Server ID、工具名、用途；
 * - 参数摘要：默认折叠，疑似密钥字段完全隐藏；
 * - 风险提示（来自服务端计划，非工具自述的可信声明）。
 */

import type { AgentCommand } from "@/types/api";

type McpCommand = Extract<AgentCommand, { intent: "use_external_tool" }>;

/** 疑似密钥字段：键名命中即整段隐藏（值不出现在 DOM） */
const SECRET_KEY_RE = /(token|secret|password|passwd|api[-_]?key|authorization|credential)/i;

function renderValue(value: unknown): string {
  if (value == null) return "（空）";
  if (typeof value === "string") return value.length > 120 ? `${value.slice(0, 120)}…` : value;
  return JSON.stringify(value);
}

export function McpToolPlanDetails({ command }: { command: McpCommand }) {
  const entries = Object.entries(command.arguments ?? {});
  const secretEntries = entries.filter(([key]) => SECRET_KEY_RE.test(key));
  const normalEntries = entries.filter(([key]) => !SECRET_KEY_RE.test(key));

  return (
    <div
      className="mt-2 rounded-lg border border-[var(--border)] bg-[var(--surface-muted)] p-3 text-xs"
      data-testid="mcp-plan-details"
    >
      <p data-testid="mcp-plan-tool">
        外部工具：<span className="font-medium">{command.tool_name}</span>
        <span className="text-[var(--text-muted)]">（来源 {command.server_id}）</span>
      </p>
      {command.purpose && (
        <p className="mt-1 text-[var(--text-muted)]" data-testid="mcp-plan-purpose">
          用途：{command.purpose}
        </p>
      )}
      {entries.length > 0 && (
        <details className="mt-2" data-testid="mcp-plan-arguments">
          <summary className="cursor-pointer text-[var(--text-muted)]">
            参数（{entries.length} 项，默认折叠）
          </summary>
          <dl className="mt-1 space-y-1 break-all">
            {normalEntries.map(([key, value]) => (
              <div key={key} className="flex gap-2">
                <dt className="min-w-16 text-[var(--text-muted)]">{key}</dt>
                <dd className="text-[var(--text)]">{renderValue(value)}</dd>
              </div>
            ))}
            {secretEntries.map(([key]) => (
              <div key={key} className="flex gap-2" data-testid="mcp-plan-secret-field">
                <dt className="min-w-16 text-[var(--text-muted)]">{key}</dt>
                <dd className="text-[var(--text-muted)]">••••••（疑似密钥，已隐藏）</dd>
              </div>
            ))}
          </dl>
        </details>
      )}
      <p className="mt-2 text-[var(--text-muted)]">
        确认后只调用一次该工具；结果作为消息返回，不会直接改动稿件内容。
      </p>
    </div>
  );
}
