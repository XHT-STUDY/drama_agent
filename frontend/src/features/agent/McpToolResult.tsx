"use client";

/** McpToolResult — MCP 外部工具结果渲染（MCP-03）。
 *
 * 区分内容类型：
 * - text：正文展示（外部不可信文本，仅作内容）
 * - structured content：折叠的 JSON 详情
 * - resource link：仅列出 URI（不自动下载）
 * - image / audio / embedded resource：显示"暂不支持"的类型标记
 * - truncated：截断提示；is_error 由失败态消息承载，此处不再渲染错误
 */

import type { McpToolResult as McpToolResultData } from "@/types/api";

const KIND_LABEL: Record<string, string> = {
  image: "图片",
  audio: "音频",
  embedded_resource: "嵌入资源",
};

export function McpToolResult({ result }: { result: McpToolResultData }) {
  const textBlocks = result.content.filter((b) => b.kind === "text" && b.text);
  const unsupported = result.content.filter((b) => b.kind in KIND_LABEL);

  return (
    <div
      className="mt-2 space-y-2 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-3 text-xs"
      data-testid="mcp-tool-result"
    >
      <p className="text-[var(--text-muted)]">
        外部工具 {result.tool_name}（来源 {result.server_id}）·{" "}
        {result.duration_ms} ms
        {result.protocol_version ? ` · 协议 ${result.protocol_version}` : ""}
      </p>

      {textBlocks.map((block, i) => (
        <p
          key={i}
          className="message-body whitespace-pre-wrap break-words text-[var(--text)]"
        >
          {block.text}
        </p>
      ))}

      {result.structured_content != null && (
        <details data-testid="mcp-structured-content">
          <summary className="cursor-pointer text-[var(--text-muted)]">
            结构化结果
          </summary>
          <pre className="mt-1 max-h-64 overflow-auto rounded bg-[var(--surface-muted)] p-2 text-[11px] leading-relaxed">
            {JSON.stringify(result.structured_content, null, 2)}
          </pre>
        </details>
      )}

      {result.resource_links.length > 0 && (
        <div className="text-[var(--text-muted)]" data-testid="mcp-resource-links">
          资源链接（{result.resource_links.length} 个，不自动访问）：
          <ul className="mt-1 list-disc pl-4 break-all">
            {result.resource_links.map((uri, i) => (
              <li key={i}>{uri}</li>
            ))}
          </ul>
        </div>
      )}

      {unsupported.length > 0 && (
        <p className="text-[var(--text-muted)]" data-testid="mcp-unsupported">
          {unsupported.length} 个暂不支持直接展示的内容类型：
          {Array.from(
            new Set(unsupported.map((b) => KIND_LABEL[b.kind] ?? b.kind)),
          ).join("、")}
        </p>
      )}

      {result.truncated && (
        <p className="text-[var(--warning)]">结果超出大小上限，已按内容块截断。</p>
      )}

      <p className="text-[var(--text-muted)]">
        以上内容由外部工具返回，请注意甄别；不会直接改动任何稿件。
      </p>
    </div>
  );
}
