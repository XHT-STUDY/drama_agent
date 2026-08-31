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

  it("未传 onDelete 时不显示删除按钮", () => {
    render(React.createElement(ProjectCard, { project: makeProject() }));
    expect(screen.queryByRole("button", { name: /删除项目/ })).toBeNull();
  });

  it("传入 onDelete 时显示删除按钮并回调", () => {
    const onDelete = vi.fn();
    const project = makeProject();
    render(React.createElement(ProjectCard, { project, onDelete }));

    fireEvent.click(screen.getByRole("button", { name: "删除项目 足球少年之逆袭人生" }));
    expect(onDelete).toHaveBeenCalledWith(project);
  });

  it("deleting 时删除按钮禁用并显示删除中", () => {
    const onDelete = vi.fn();
    render(
      React.createElement(ProjectCard, {
        project: makeProject(),
        onDelete,
        deleting: true,
      }),
    );
    const button = screen.getByRole("button", { name: "删除项目 足球少年之逆袭人生" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.getByText("删除中…")).toBeTruthy();
  });

  it("点击删除按钮不触发卡片跳转链接", () => {
    const onDelete = vi.fn();
    render(React.createElement(ProjectCard, { project: makeProject(), onDelete }));

    const button = screen.getByRole("button", { name: "删除项目 足球少年之逆袭人生" });
    // 按钮不在 <a> 内部（HTML 不允许链接嵌套交互元素）
    expect(button.closest("a")).toBeNull();
  });

  it("未传 onToggleSelect 时不显示选择框", () => {
    render(React.createElement(ProjectCard, { project: makeProject() }));
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("传入 onToggleSelect 时显示选择框并回调项目 ID", () => {
    const onToggleSelect = vi.fn();
    const project = makeProject();
    render(React.createElement(ProjectCard, { project, onToggleSelect }));

    const checkbox = screen.getByRole("checkbox", { name: "选择项目 足球少年之逆袭人生" }) as HTMLInputElement;
    expect(checkbox.checked).toBe(false);

    fireEvent.click(checkbox);
    expect(onToggleSelect).toHaveBeenCalledWith(project.id);
  });

  it("selected 为 true 时选择框呈勾选状态", () => {
    render(
      React.createElement(ProjectCard, {
        project: makeProject(),
        selected: true,
        onToggleSelect: vi.fn(),
      }),
    );
    const checkbox = screen.getByRole("checkbox") as HTMLInputElement;
    expect(checkbox.checked).toBe(true);
  });

  it("选择框不在跳转链接内部（点击不触发导航）", () => {
    render(
      React.createElement(ProjectCard, {
        project: makeProject(),
        onToggleSelect: vi.fn(),
      }),
    );
    const checkbox = screen.getByRole("checkbox", { name: "选择项目 足球少年之逆袭人生" });
    expect(checkbox.closest("a")).toBeNull();
  });
});
