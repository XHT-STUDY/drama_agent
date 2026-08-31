/** 项目列表页测试 — 多选/全选批量删除.
 *
 * 测试：
 * - 卡片选择框勾选与取消
 * - 全选/取消全选
 * - 批量删除确认后逐个调用 remove 并移除卡片
 * - 确认弹窗取消时不删除
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/lib/api-client", () => ({
  projectsApi: {
    list: vi.fn(),
    remove: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
  },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("next/link", () => ({
  default: function MockLink({ children, href }: { children: React.ReactNode; href: string }) {
    return React.createElement("a", { href }, children);
  },
}));

import { projectsApi } from "@/lib/api-client";
import ProjectsPage from "@/app/projects/page";
import type { Project } from "@/types/api";

function makeProject(id: string, overrides: Partial<Project> = {}): Project {
  return {
    id,
    title: `项目 ${id}`,
    status: "draft",
    target_episode_count: 10,
    current_episode_count: 0,
    created_at: "2026-07-25T12:00:00Z",
    updated_at: "2026-07-25T12:00:00Z",
    ...overrides,
  };
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    React.createElement(
      QueryClientProvider,
      { client: queryClient },
      React.createElement(ProjectsPage),
    ),
  );
}

describe("ProjectsPage 批量删除", () => {
  const items = [makeProject("p-1"), makeProject("p-2"), makeProject("p-3")];

  beforeEach(() => {
    vi.mocked(projectsApi.list).mockResolvedValue({
      items,
      total: items.length,
      offset: 0,
      limit: 20,
    });
    vi.mocked(projectsApi.remove).mockImplementation(async (id: string) => {
      return { deleted: true, project_id: id };
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
  });

  async function waitForCards() {
    await waitFor(() => {
      expect(screen.getByText("项目 p-1")).toBeTruthy();
    });
  }

  it("全选后显示已选数量，批量删除移除全部卡片", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();
    await waitForCards();

    fireEvent.click(screen.getByRole("checkbox", { name: "全选项目" }));
    expect(screen.getByText("已选择 3 个项目")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "批量删除" }));

    await waitFor(() => {
      expect(vi.mocked(projectsApi.remove)).toHaveBeenCalledTimes(3);
    });
    expect(confirmSpy).toHaveBeenCalledWith(
      "确定要删除选中的 3 个项目吗？删除后列表中不再显示。",
    );

    await waitFor(() => {
      expect(screen.queryByText("项目 p-1")).toBeNull();
      expect(screen.queryByText("项目 p-2")).toBeNull();
      expect(screen.queryByText("项目 p-3")).toBeNull();
    });
  });

  it("勾选部分卡片后批量删除只删除选中的项目", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();
    await waitForCards();

    fireEvent.click(screen.getByRole("checkbox", { name: "选择项目 项目 p-1" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "选择项目 项目 p-3" }));
    expect(screen.getByText("已选择 2 个项目")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "批量删除" }));

    await waitFor(() => {
      expect(vi.mocked(projectsApi.remove)).toHaveBeenCalledTimes(2);
    });
    const removedIds = vi.mocked(projectsApi.remove).mock.calls.map((c) => c[0]);
    expect(removedIds.sort()).toEqual(["p-1", "p-3"]);

    await waitFor(() => {
      expect(screen.queryByText("项目 p-1")).toBeNull();
      expect(screen.getByText("项目 p-2")).toBeTruthy();
      expect(screen.queryByText("项目 p-3")).toBeNull();
    });
  });

  it("全选后可取消全选", async () => {
    renderPage();
    await waitForCards();

    const selectAll = screen.getByRole("checkbox", { name: "全选项目" }) as HTMLInputElement;
    fireEvent.click(selectAll);
    expect(selectAll.checked).toBe(true);
    expect(screen.getByText("已选择 3 个项目")).toBeTruthy();

    fireEvent.click(screen.getByRole("checkbox", { name: "全选项目" }));
    expect(screen.queryByText(/已选择/)).toBeNull();
  });

  it("确认弹窗取消时不执行删除", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPage();
    await waitForCards();

    fireEvent.click(screen.getByRole("checkbox", { name: "选择项目 项目 p-2" }));
    fireEvent.click(screen.getByRole("button", { name: "批量删除" }));

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(vi.mocked(projectsApi.remove)).not.toHaveBeenCalled();
    expect(screen.getByText("项目 p-2")).toBeTruthy();
  });

  it("部分删除失败时显示错误提示，成功的仍被移除", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(projectsApi.remove).mockImplementation(async (id: string) => {
      if (id === "p-2") throw new Error("network error");
      return { deleted: true, project_id: id };
    });
    renderPage();
    await waitForCards();

    fireEvent.click(screen.getByRole("checkbox", { name: "全选项目" }));
    fireEvent.click(screen.getByRole("button", { name: "批量删除" }));

    await waitFor(() => {
      expect(screen.queryByText("项目 p-1")).toBeNull();
    });
    expect(screen.getByText("项目 p-2")).toBeTruthy();
    expect(screen.getByText(/1 个项目删除失败/)).toBeTruthy();
  });
});
