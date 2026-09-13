/** 导出中心测试（W1-05 固定版本版）.
 *
 * 覆盖：
 * - ExportSection：发起后端导出时冻结显式 artifact_ids + 幂等键；
 *   Run 完成后经 result_artifact_ids 触发固定下载（不用当前稿件重序列化）；
 *   Run 失败显示错误；无数据的内容类型禁用
 * - ExportHistory：服务端历史（export_file Artifact）渲染与固定重下；
 *   空态占位；无"清空"（交付记录是审计事实）
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";

import type { Artifact, Run } from "@/types/api";
import type { ExportableArtifacts } from "@/types/api";

// api-client mock：捕获导出发起请求，Run 查询可控返回
const createMock = vi.fn();
const getRunMock = vi.fn();
let downloadUrlCalls: string[] = [];

vi.mock("@/lib/api-client", () => ({
  exportsApi: {
    create: (...args: unknown[]) => createMock(...args),
    list: vi.fn(async () => ({ items: [], total: 0, offset: 0, limit: 20 })),
    downloadUrl: (artifactId: string, projectId: string) => {
      const url = `http://test/api/v1/exports/${artifactId}/download?project_id=${projectId}`;
      downloadUrlCalls.push(url);
      return url;
    },
  },
  runsApi: {
    get: (runId: string) => getRunMock(runId),
  },
  artifactsApi: {},
}));

import { ExportSection } from "@/features/exports/ExportSection";
import { ExportHistory } from "@/features/exports/ExportHistory";

function artifact(
  id: string,
  type: string,
  episode: number,
  version = 1,
): Artifact {
  return {
    id,
    project_id: "p1",
    type: type as Artifact["type"],
    version,
    episode_number: episode,
    status: "valid",
    content: {},
    content_schema_version: "1.0",
    prompt_version: "1.0.0",
    created_at: "2026-09-13T10:00:00Z",
    updated_at: "2026-09-13T10:00:00Z",
  };
}

function available(overrides: Partial<ExportableArtifacts> = {}): ExportableArtifacts {
  return {
    storyBible: artifact("sb-1", "story_bible", 1),
    outline: artifact("ol-1", "episode_outline_set", 1),
    scripts: [artifact("sc-1", "script_draft", 1), artifact("sc-2", "script_draft", 2)],
    evaluations: [artifact("ev-1", "evaluation_report", 1)],
    revisions: [],
    ...overrides,
  };
}

function queuedRun(): Run {
  return {
    run_id: "run-1",
    project_id: "p1",
    action: "export",
    status: "queued",
    stage_generation: 0,
    created_at: "2026-09-13T10:00:00Z",
    updated_at: "2026-09-13T10:00:00Z",
  };
}

function qc(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
}

function withQc(el: React.ReactElement): React.ReactElement {
  return React.createElement(QueryClientProvider, { client: qc() }, el);
}

describe("ExportSection（W1-05 后端固定版本导出）", () => {
  beforeEach(() => {
    createMock.mockReset();
    getRunMock.mockReset();
    downloadUrlCalls = [];
    HTMLAnchorElement.prototype.click = vi.fn();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("渲染全部内容类型与格式选项，无数据类型禁用", () => {
    render(
      withQc(
        React.createElement(ExportSection, {
          projectId: "p1",
          available: available({ outline: null, revisions: [] }),
        }),
      ),
    );
    expect(screen.getByText("StoryBible")).toBeTruthy();
    expect(screen.getByText("Markdown (.md)")).toBeTruthy();
    const disabled = screen
      .getAllByRole("checkbox")
      .filter((cb) => (cb as HTMLInputElement).disabled);
    expect(disabled.length).toBeGreaterThanOrEqual(2); // 大纲 + 修订
  });

  it("发起导出：冻结显式 artifact_ids 与幂等键，完成后固定下载", async () => {
    createMock.mockResolvedValue(queuedRun());
    getRunMock.mockResolvedValue({
      ...queuedRun(),
      status: "completed",
      result_artifact_ids: ["ex-file-1"],
    } satisfies Run);

    render(
      withQc(
        React.createElement(ExportSection, {
          projectId: "p1",
          available: available(),
        }),
      ),
    );
    fireEvent.click(screen.getByText("📦 生成并下载"));

    await vi.waitFor(() => {
      expect(createMock).toHaveBeenCalledTimes(1);
    });
    const [projectId, body] = createMock.mock.calls[0];
    expect(projectId).toBe("p1");
    expect(body.kinds).toEqual(["story_bible", "outline", "script", "evaluation"]);
    expect(body.format).toBe("markdown");
    expect(body.artifact_ids).toEqual({
      story_bible: ["sb-1"],
      outline: ["ol-1"],
      script: ["sc-1", "sc-2"],
      evaluation: ["ev-1"],
    });
    expect(body.idempotency_key).toMatch(/^[0-9a-f-]{36}$/);

    await vi.waitFor(() => {
      expect(HTMLAnchorElement.prototype.click).toHaveBeenCalled();
    });
    expect(downloadUrlCalls[0]).toContain("/exports/ex-file-1/download?project_id=p1");
  });

  it("接受时的选择告警显式提醒（错配评估剔除不静默）", async () => {
    createMock.mockResolvedValue({
      ...queuedRun(),
      config_snapshot: {
        options: {
          kinds: ["script", "evaluation"],
          artifact_ids: { script: ["sc-2"], evaluation: [] },
          selection_warnings: ["评估报告 ev-1 绑定的剧本版本不在本次导出的剧本集合中，已默认剔除"],
        },
      },
    } satisfies Run);
    getRunMock.mockResolvedValue({ ...queuedRun(), status: "completed", result_artifact_ids: ["ex-file-1"] } satisfies Run);

    render(
      withQc(
        React.createElement(ExportSection, {
          projectId: "p1",
          available: available(),
        }),
      ),
    );
    fireEvent.click(screen.getByText("📦 生成并下载"));
    await vi.waitFor(() => {
      expect(screen.getByTestId("selection-warnings").textContent).toContain("已默认剔除");
    });
  });

  it("Run 失败：显示错误码，不触发下载", async () => {
    createMock.mockResolvedValue(queuedRun());
    getRunMock.mockResolvedValue({
      ...queuedRun(),
      status: "failed",
      error_code: "EXPORT_FAILED",
      error_detail: "序列化失败",
    } satisfies Run);

    render(
      withQc(
        React.createElement(ExportSection, {
          projectId: "p1",
          available: available(),
        }),
      ),
    );
    fireEvent.click(screen.getByText("📦 生成并下载"));

    await vi.waitFor(() => {
      expect(screen.getByTestId("export-error").textContent).toContain("EXPORT_FAILED");
    });
    expect(downloadUrlCalls).toHaveLength(0);
  });
});

describe("ExportHistory（W1-05 服务端历史）", () => {
  beforeEach(() => {
    downloadUrlCalls = [];
    HTMLAnchorElement.prototype.click = vi.fn();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("空历史显示占位", () => {
    render(
      React.createElement(ExportHistory, { projectId: "p1", artifacts: [] }),
    );
    expect(screen.getByText(/暂无导出记录/)).toBeTruthy();
  });

  it("渲染服务端记录（格式徽章/文件名/大小/依据版本数），重下走固定端点且无清空", () => {
    const record: Artifact = {
      ...artifact("ex-file-1", "export_file", 1),
      content: {
        storage_key: "uuid.md",
        format: "docx",
        filename: "足球少年-全集-20260913-120000.docx",
        size_bytes: 2048,
        sha256: "0".repeat(64),
        source_artifact_ids: [
          { artifact_id: "sc-1", version: 1, relation: "derived_from" },
          { artifact_id: "sb-1", version: 2, relation: "derived_from" },
        ],
        warnings: [],
      },
    };
    render(
      React.createElement(ExportHistory, { projectId: "p1", artifacts: [record] }),
    );
    expect(screen.getByText("DOCX")).toBeTruthy();
    expect(screen.getByText(/足球少年-全集/)).toBeTruthy();
    expect(screen.getByText(/2.0 KB/)).toBeTruthy();
    expect(screen.getByText(/依据 2 份稿件版本/)).toBeTruthy();
    // 服务端历史无清空（交付记录是审计事实）
    expect(screen.queryByText("清空历史")).toBeNull();

    fireEvent.click(screen.getByText("重新下载"));
    expect(downloadUrlCalls[0]).toContain("/exports/ex-file-1/download?project_id=p1");
    expect(HTMLAnchorElement.prototype.click).toHaveBeenCalled();
  });
});
