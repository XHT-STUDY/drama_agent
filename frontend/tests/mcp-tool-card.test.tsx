/** MCP 外部工具计划卡与结果组件单元测试（MCP-03）。 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";

import { McpToolPlanDetails } from "@/features/agent/McpToolActionCard";
import { McpToolResult } from "@/features/agent/McpToolResult";
import type { McpToolResult as McpToolResultData } from "@/types/api";

const command = {
  intent: "use_external_tool" as const,
  server_id: "research",
  tool_name: "web_search",
  qualified_tool_name: "mcp__research__web_search",
  arguments: { query: "足球青训", limit: 5, api_token: "super-secret-value" },
  tool_definition_digest: "0".repeat(64),
  purpose: "检索青训背景资料",
};

describe("McpToolPlanDetails", () => {
  it("展示 Server、工具名与用途", () => {
    render(React.createElement(McpToolPlanDetails, { command }));
    expect(screen.getByTestId("mcp-plan-tool").textContent).toContain("web_search");
    expect(screen.getByTestId("mcp-plan-tool").textContent).toContain("research");
    expect(screen.getByTestId("mcp-plan-purpose").textContent).toContain("检索青训背景资料");
  });

  it("参数默认折叠且疑似密钥字段完全隐藏", () => {
    render(React.createElement(McpToolPlanDetails, { command }));
    // 参数区默认折叠（details 未展开）
    const details = document.querySelector("details");
    expect(details).toBeTruthy();
    expect(details?.hasAttribute("open")).toBe(false);
    // 密钥字段的值永远不出现在 DOM，只显示掩码说明
    expect(document.body.textContent).not.toContain("super-secret-value");
    expect(screen.getByTestId("mcp-plan-secret-field").textContent).toContain("已隐藏");
  });
});

describe("McpToolResult", () => {
  const base: McpToolResultData = {
    server_id: "research",
    tool_name: "web_search",
    content: [
      { kind: "text", text: "青训体系资料若干" },
      { kind: "image", mime_type: "image/png", size_bytes: 1024 },
      { kind: "resource_link", resource_uri: "mcp://assets/1" },
    ],
    structured_content: { result: "青训体系资料若干" },
    resource_links: ["mcp://assets/1"],
    is_error: false,
    duration_ms: 120,
    protocol_version: "2026-07-28",
    request_id: "abc",
    truncated: false,
  };

  it("区分 text、结构化结果、资源链接与暂不支持类型", () => {
    render(React.createElement(McpToolResult, { result: base }));
    expect(screen.getByTestId("mcp-tool-result").textContent).toContain("青训体系资料若干");
    expect(screen.getByTestId("mcp-structured-content")).toBeTruthy();
    expect(screen.getByTestId("mcp-resource-links").textContent).toContain("mcp://assets/1");
    expect(screen.getByTestId("mcp-unsupported").textContent).toContain("图片");
  });

  it("截断结果给出明确提示", () => {
    render(React.createElement(McpToolResult, { result: { ...base, truncated: true } }));
    expect(screen.getByText(/已按内容块截断/)).toBeTruthy();
  });

  it("提示内容为外部不可信文本", () => {
    render(React.createElement(McpToolResult, { result: base }));
    expect(screen.getByText(/请注意甄别/)).toBeTruthy();
  });
});
