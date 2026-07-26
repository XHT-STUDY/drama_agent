/** 项目功能测试 (H-02).
 *
 * 测试：
 * - StatusBadge 各状态显示
 * - ProjectCard 组件渲染
 * - 表单校验逻辑
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ---- Mock next/navigation ----
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

// ---- Mock next/link ----
vi.mock("next/link", () => ({
  default: function MockLink({ children, href }: { children: React.ReactNode; href: string }) {
    return React.createElement("a", { href }, children);
  },
}));

import { StatusBadge } from "@/features/projects/StatusBadge";
import { ProjectCard } from "@/features/projects/ProjectCard";
import type { Project } from "@/types/api";

// ============================================================
// StatusBadge
// ============================================================

describe("StatusBadge", () => {
  it("显示 draft 状态中文标签", () => {
    render(React.createElement(StatusBadge, { status: "draft" }));
    expect(screen.getByText("草稿")).toBeTruthy();
  });

  it("显示 completed 状态中文标签", () => {
    render(React.createElement(StatusBadge, { status: "completed" }));
    expect(screen.getByText("已完成")).toBeTruthy();
  });

  it("显示 writing 状态中文标签", () => {
    render(React.createElement(StatusBadge, { status: "writing" }));
    expect(screen.getByText("创作中")).toBeTruthy();
  });

  it("显示 revising 状态中文标签", () => {
    render(React.createElement(StatusBadge, { status: "revising" }));
    expect(screen.getByText("修订中")).toBeTruthy();
  });
});

// ============================================================
// ProjectCard
// ============================================================

describe("ProjectCard", () => {
  function makeProject(overrides: Partial<Project> = {}): Project {
    return {
      id: "proj-1",
      title: "足球少年之逆袭人生",
      status: "draft",
      target_episode_count: 10,
      current_episode_count: 0,
      created_at: "2026-07-25T12:00:00Z",
      updated_at: "2026-07-25T12:00:00Z",
      ...overrides,
    };
  }

  it("显示项目标题", () => {
    render(React.createElement(ProjectCard, { project: makeProject() }));
    expect(screen.getByText("足球少年之逆袭人生")).toBeTruthy();
  });

  it("显示目标集数", () => {
    render(React.createElement(ProjectCard, { project: makeProject({ target_episode_count: 20 }) }));
    expect(screen.getByText(/目标 20 集/)).toBeTruthy();
  });

  it("显示已完成集数", () => {
    render(React.createElement(ProjectCard, { project: makeProject({ current_episode_count: 3 }) }));
    expect(screen.getByText(/已完成 3 集/)).toBeTruthy();
  });

  it("空标题显示未命名", () => {
    render(React.createElement(ProjectCard, { project: makeProject({ title: "" }) }));
    expect(screen.getByText("未命名项目")).toBeTruthy();
  });

  it("链接指向项目详情页", () => {
    render(React.createElement(ProjectCard, { project: makeProject() }));
    const link = screen.getByText("足球少年之逆袭人生").closest("a");
    expect(link?.getAttribute("href")).toBe("/projects/proj-1");
  });

  it("包含状态标签", () => {
    render(React.createElement(ProjectCard, { project: makeProject({ status: "writing" }) }));
    expect(screen.getByText("创作中")).toBeTruthy();
  });
});
