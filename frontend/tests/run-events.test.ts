/** SSE Hook 与进度组件测试 (H-03). */

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ---- Mocks ----
vi.mock("next/link", () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) =>
    React.createElement("a", { href }, children),
}));

global.fetch = vi.fn().mockResolvedValue({
  ok: true,
  body: { getReader: () => ({ read: () => Promise.resolve({ done: true, value: null }), cancel: vi.fn() }) },
});

import { RunProgress } from "@/features/runs/RunProgress";
import type { NodeProgress } from "@/hooks/use-run-events";

// ---- helpers ----
function qc() { return new QueryClient({ defaultOptions: { queries: { retry: false } } }); }

function mkNodes(overrides: Partial<NodeProgress>[] = []): NodeProgress[] {
  const d: NodeProgress[] = [
    { node: "normalize", label: "需求归一化", status: "completed", progress: 100, artifactIds: ["a1"] },
    { node: "story_bible", label: "故事设定", status: "running", progress: 50, artifactIds: [] },
    { node: "outline", label: "分集大纲", status: "pending", progress: 0, artifactIds: [] },
    { node: "write_episodes", label: "剧本撰写", status: "pending", progress: 0, artifactIds: [] },
    { node: "finalize", label: "完成收尾", status: "pending", progress: 0, artifactIds: [] },
  ];
  return d.map((x, i) => ({ ...x, ...(overrides[i] || {}) }));
}

function nop() {}

function wrap(el: React.ReactElement) {
  return React.createElement(QueryClientProvider, { client: qc() }, el);
}

describe("RunProgress", () => {
  it("显示节点中文标签", () => {
    render(wrap(React.createElement(RunProgress, {
      runId: "r1", overallProgress: 30, nodes: mkNodes(),
      connected: true, runStatus: null, lastError: null, eventCount: 0, onReconnect: nop,
    })));
    expect(screen.getByText("需求归一化")).toBeTruthy();
    expect(screen.getByText("故事设定")).toBeTruthy();
  });

  it("显示整体进度百分比", () => {
    render(wrap(React.createElement(RunProgress, {
      runId: "r1", overallProgress: 45, nodes: mkNodes(),
      connected: true, runStatus: null, lastError: null, eventCount: 0, onReconnect: nop,
    })));
    expect(screen.getByText("45%")).toBeTruthy();
  });

  it("completed 状态显示完成消息", () => {
    const nodes = mkNodes([
      { status: "completed" }, { status: "completed" }, { status: "completed" },
      { status: "completed" }, { status: "completed" },
    ]);
    render(wrap(React.createElement(RunProgress, {
      runId: "r1", overallProgress: 100, nodes,
      connected: true, runStatus: "completed", lastError: null, eventCount: 0, onReconnect: nop,
    })));
    expect(screen.getByText(/全部节点已完成/)).toBeTruthy();
  });

  it("failed 状态显示错误码", () => {
    const nodes = mkNodes([
      { status: "completed" },
      { status: "failed", error: "LLM_OUTPUT_INVALID" },
    ]);
    render(wrap(React.createElement(RunProgress, {
      runId: "r1", overallProgress: 20, nodes,
      connected: true, runStatus: "failed", lastError: "LLM_OUTPUT_INVALID", eventCount: 0, onReconnect: nop,
    })));
    expect(screen.getByText(/创作过程中发生错误/)).toBeTruthy();
    expect(screen.getAllByText(/LLM_OUTPUT_INVALID/).length).toBeGreaterThanOrEqual(1);
  });

  it("运行中显示断开状态和重连按钮", () => {
    render(wrap(React.createElement(RunProgress, {
      runId: "r1", overallProgress: 50, nodes: mkNodes(),
      connected: false, runStatus: null, lastError: "连接中断", eventCount: 0, onReconnect: nop,
    })));
    expect(screen.getByText("已断开")).toBeTruthy();
    expect(screen.getByText("重连")).toBeTruthy();
  });

  it("运行中显示取消按钮", () => {
    render(wrap(React.createElement(RunProgress, {
      runId: "r1", overallProgress: 50, nodes: mkNodes(),
      connected: true, runStatus: null, lastError: null, eventCount: 0, onReconnect: nop,
    })));
    expect(screen.getByText("取消")).toBeTruthy();
  });

  it("完成后不显示取消和重连", () => {
    const nodes = mkNodes([
      { status: "completed" }, { status: "completed" }, { status: "completed" },
      { status: "completed" }, { status: "completed" },
    ]);
    render(wrap(React.createElement(RunProgress, {
      runId: "r1", overallProgress: 100, nodes,
      connected: true, runStatus: "completed", lastError: null, eventCount: 0, onReconnect: nop,
    })));
    expect(screen.queryByText("取消")).toBeNull();
    expect(screen.queryByText("重连")).toBeNull();
  });

  it("failed 节点错误码可见", () => {
    const nodes = mkNodes([
      { status: "completed" },
      { status: "failed", error: "LLM_OUTPUT_INVALID" },
    ]);
    render(wrap(React.createElement(RunProgress, {
      runId: "r1", overallProgress: 20, nodes,
      connected: true, runStatus: "failed", lastError: null, eventCount: 0, onReconnect: nop,
    })));
    expect(screen.getAllByText("LLM_OUTPUT_INVALID").length).toBeGreaterThanOrEqual(1);
  });
});
