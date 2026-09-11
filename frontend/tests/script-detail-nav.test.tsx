/** 剧本详情页集数导航测试.
 *
 * 回归背景：左侧 EpisodeNav 的 targetCount 曾硬编码为 10，
 * 与项目实际 target_episode_count 脱节（目标 1 集的项目也显示 10 条）。
 *
 * 测试：
 * - 导航长度跟随项目 target_episode_count，而非固定 10
 * - 各集状态从 Artifact 列表派生：valid 剧本 → ●，无 valid 剧本 → ○
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/lib/api-client", () => ({
  projectsApi: {
    get: vi.fn(),
  },
  artifactsApi: {
    getLatest: vi.fn(),
    listAllByType: vi.fn(),
  },
  runsApi: {
    create: vi.fn(),
  },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useParams: () => ({ id: "p-1", episode: "1" }),
}));

vi.mock("next/link", () => ({
  default: function MockLink({ children, href }: { children: React.ReactNode; href: string }) {
    return React.createElement("a", { href }, children);
  },
}));

import { projectsApi, artifactsApi } from "@/lib/api-client";
import ScriptDetailPage from "@/app/projects/[id]/scripts/[episode]/page";
import type { Artifact, Project } from "@/types/api";

// ============================================================
// 工厂函数
// ============================================================

function makeProject(overrides: Partial<Project> = {}): Project {
  return {
    id: "p-1",
    title: "测试项目",
    status: "writing",
    target_episode_count: 2,
    current_episode_count: 1,
    created_at: "2026-09-01T12:00:00Z",
    updated_at: "2026-09-01T12:00:00Z",
    ...overrides,
  };
}

function makeScriptArtifact(overrides: Partial<Artifact> = {}): Artifact {
  return {
    id: "script-art-1",
    project_id: "p-1",
    type: "script_draft",
    version: 1,
    episode_number: 1,
    status: "valid",
    content: {
      episode_number: 1,
      title: "逆袭的开始",
      opening_hook: "钩子",
      scenes: [
        {
          scene_number: 1,
          location: "球场",
          time_of_day: "日",
          characters: ["林风"],
          action: "训练开始",
          dialogue: [{ speaker: "林风", text: "我会赢" }],
        },
      ],
      ending_hook: "结尾钩子",
      plain_text: "",
      word_count: 100,
      dialogue_ratio: 0.4,
    },
    content_schema_version: "1.0.0",
    prompt_version: "1.0.0",
    created_at: "2026-09-01T12:00:00Z",
    updated_at: "2026-09-01T12:00:00Z",
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
      React.createElement(ScriptDetailPage),
    ),
  );
}

// ============================================================
// 测试
// ============================================================

describe("剧本详情页集数导航", () => {
  beforeEach(() => {
    vi.mocked(projectsApi.get).mockResolvedValue(
      makeProject({ target_episode_count: 2, current_episode_count: 1 }),
    );
    vi.mocked(artifactsApi.getLatest).mockImplementation(
      async (_projectId: string, type: string) => {
        if (type === "script_draft") return makeScriptArtifact();
        // evaluation_report 不存在 → 抛错（页面 retry:false 兼容）
        throw new Error("not found");
      },
    );
    // 第 1 集 valid；第 2 集只有 invalid 版本 → 应显示"未生成"
    vi.mocked(artifactsApi.listAllByType).mockImplementation(
      async (_projectId: string, type: string) => {
        if (type === "script_draft") {
          return [
            makeScriptArtifact({ id: "script-art-2", episode_number: 2, status: "invalid" }),
            makeScriptArtifact(),
          ];
        }
        return [];
      },
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("导航长度跟随项目 target_episode_count（2 集而非硬编码 10）", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("集数 (2)")).toBeTruthy();
    });
    expect(screen.getByRole("button", { name: /第 1 集/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /第 2 集/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /第 3 集/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /第 10 集/ })).toBeNull();
  });

  it("有 valid 剧本的集显示已完成，仅有 invalid 版本的集显示未生成", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("集数 (2)")).toBeTruthy();
    });

    const ep1 = screen.getByRole("button", { name: /第 1 集/ });
    const ep2 = screen.getByRole("button", { name: /第 2 集/ });
    expect(ep1.querySelector('[title="已完成剧本"]')).not.toBeNull();
    expect(ep2.querySelector('[title="未生成"]')).not.toBeNull();
  });

  it("target_episode_count=1 的项目只显示 1 集", async () => {
    vi.mocked(projectsApi.get).mockResolvedValue(
      makeProject({ target_episode_count: 1, current_episode_count: 1 }),
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("集数 (1)")).toBeTruthy();
    });
    expect(screen.queryByRole("button", { name: /第 2 集/ })).toBeNull();
  });
});
