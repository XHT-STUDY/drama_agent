/** UploadInput 组件测试（W1-06）：两步授权流、unknown 不自动生成、
 * 分类动作路由、转换失败如实提示。 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";

const createUpload = vi.fn();
const createRun = vi.fn();
const getRun = vi.fn();
const getArtifact = vi.fn();
const openArtifact = vi.fn();

vi.mock("@/lib/api-client", () => ({
  uploadsApi: {
    create: (...args: unknown[]) => createUpload(...args),
    list: vi.fn(async () => ({ items: [], total: 0, offset: 0, limit: 10 })),
  },
  runsApi: {
    create: (...args: unknown[]) => createRun(...args),
    get: (id: string) => getRun(id),
  },
  artifactsApi: {
    getById: (id: string) => getArtifact(id),
  },
  projectsApi: {
    get: vi.fn(async () => ({ id: "p1", target_episode_count: 10 })),
  },
}));

import { UploadInput } from "@/features/agent/UploadInput";
import type { Run, UploadRecord } from "@/types/api";

const NOW = "2026-09-13T00:00:00Z";

function uploadRecord(): UploadRecord {
  return {
    id: "00000000-0000-0000-0000-00000000000a",
    project_id: "p1",
    path: "stored-key.txt",
    sha256: "0".repeat(64),
    mime_type: "text/plain",
    size_bytes: 100,
    original_name: "我的原稿.txt",
    parse_status: "parsed",
    char_count: 1200,
    warnings: [],
    created_at: NOW,
  };
}

function runFixture(overrides: Partial<Run> = {}): Run {
  return {
    run_id: "00000000-0000-0000-0000-0000000000b1",
    project_id: "p1",
    action: "import",
    status: "queued",
    stage_generation: 0,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

function qc(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function setup(): void {
  render(
    React.createElement(
      QueryClientProvider,
      { client: qc() },
      React.createElement(UploadInput, {
        projectId: "p1",
        onOpenArtifact: openArtifact,
      }),
    ),
  );
}

/** 模拟用户选择文件（jsdom 无法触发原生文件对话框，直接对 input 派发 change） */
function pickFile(): void {
  const input = screen.getByTestId("upload-file-input") as HTMLInputElement;
  const file = new File(["第1场 天台 夜"], "我的原稿.txt", { type: "text/plain" });
  Object.defineProperty(input, "files", { value: [file] });
  fireEvent.change(input);
}

describe("UploadInput（W1-06）", () => {
  beforeEach(() => {
    createUpload.mockReset();
    createRun.mockReset();
    getRun.mockReset();
    getArtifact.mockReset();
    openArtifact.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("初始态：附件提示与选择按钮，无导入按钮", () => {
    setup();
    expect(screen.getByTestId("upload-input").textContent).toContain("只解析存档");
    expect(screen.queryByTestId("import-confirm")).toBeNull();
  });

  it("上传成功显示附件卡与「识别并导入」两步授权按钮", async () => {
    createUpload.mockResolvedValue(uploadRecord());
    setup();
    pickFile();
    await waitFor(() => {
      expect(screen.getByTestId("upload-card")).toBeTruthy();
    });
    expect(screen.getByTestId("upload-card").textContent).toContain("我的原稿.txt");
    expect(createUpload).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("import-confirm")).toBeTruthy();
    // 第二步之前没有创建任何 Run
    expect(createRun).not.toHaveBeenCalled();
  });

  it("识别并导入：创建 import Run 并携带 upload_id", async () => {
    createUpload.mockResolvedValue(uploadRecord());
    createRun.mockResolvedValue(runFixture({ status: "running" }));
    setup();
    pickFile();
    await waitFor(() => expect(screen.getByTestId("import-confirm")).toBeTruthy());
    fireEvent.click(screen.getByTestId("import-confirm"));

    await waitFor(() => {
      expect(createRun).toHaveBeenCalledTimes(1);
    });
    const [projectId, body] = createRun.mock.calls[0];
    expect(projectId).toBe("p1");
    expect(body.action).toBe("import");
    expect(body.config.upload_id).toBe("00000000-0000-0000-0000-00000000000a");
    expect(screen.getByTestId("import-running").textContent).toContain("模型分类");
  });

  it("unknown 分类（needs_review）：如实说明，不自动生成任何创作 Run", async () => {
    createUpload.mockResolvedValue(uploadRecord());
    createRun.mockResolvedValue(
      runFixture({
        status: "needs_review",
        route: "needs_user_input",
      }),
    );
    setup();
    pickFile();
    await waitFor(() => expect(screen.getByTestId("import-confirm")).toBeTruthy());
    fireEvent.click(screen.getByTestId("import-confirm"));

    await waitFor(() => {
      expect(screen.getByTestId("import-unknown")).toBeTruthy();
    });
    // 不出现任何路由动作按钮（创作/打开剧本）
    expect(screen.queryByTestId("create-from-upload")).toBeNull();
    expect(screen.queryByTestId("open-imported-script")).toBeNull();
    expect(createRun).toHaveBeenCalledTimes(1); // 只有导入本身
  });

  it("full_script 分类：展示转换稿入口，点击回调打开画布", async () => {
    createUpload.mockResolvedValue(uploadRecord());
    createRun.mockResolvedValue(
      runFixture({
        status: "completed",
        result_artifact_ids: [
          "00000000-0000-0000-0000-0000000000c1",
          "00000000-0000-0000-0000-0000000000c2",
        ],
        route: "evaluate",
      }),
    );
    getArtifact.mockResolvedValue({
      id: "00000000-0000-0000-0000-0000000000c1",
      content: {
        content_type: "full_script",
        confidence: 0.9,
        reason: "包含完整场景结构与对白",
      },
    });
    setup();
    pickFile();
    await waitFor(() => expect(screen.getByTestId("import-confirm")).toBeTruthy());
    fireEvent.click(screen.getByTestId("import-confirm"));

    await waitFor(() => {
      expect(screen.getByTestId("import-result")).toBeTruthy();
    });
    expect(screen.getByTestId("import-result").textContent).toContain("完整剧本");
    fireEvent.click(screen.getByTestId("open-imported-script"));
    expect(openArtifact).toHaveBeenCalledWith("00000000-0000-0000-0000-0000000000c2");
  });

  it("full_script 转换失败：如实提示结构不足，不提供打开按钮", async () => {
    createUpload.mockResolvedValue(uploadRecord());
    createRun.mockResolvedValue(
      runFixture({
        status: "completed",
        result_artifact_ids: ["00000000-0000-0000-0000-0000000000c1"],
        route: "evaluate",
      }),
    );
    getArtifact.mockResolvedValue({
      id: "00000000-0000-0000-0000-0000000000c1",
      content: {
        content_type: "full_script",
        confidence: 0.85,
        reason: "有剧本特征",
      },
    });
    setup();
    pickFile();
    await waitFor(() => expect(screen.getByTestId("import-confirm")).toBeTruthy());
    fireEvent.click(screen.getByTestId("import-confirm"));

    await waitFor(() => {
      expect(screen.getByTestId("import-result").textContent).toContain("结构不足");
    });
    expect(screen.queryByTestId("open-imported-script")).toBeNull();
  });

  it("导入 Run 失败：显示错误详情", async () => {
    createUpload.mockResolvedValue(uploadRecord());
    createRun.mockResolvedValue(
      runFixture({ status: "failed", error_code: "WORKFLOW_EXECUTOR_ERROR" }),
    );
    setup();
    pickFile();
    await waitFor(() => expect(screen.getByTestId("import-confirm")).toBeTruthy());
    fireEvent.click(screen.getByTestId("import-confirm"));

    await waitFor(() => {
      expect(screen.getByTestId("import-failed").textContent).toContain("导入失败");
    });
  });
});
