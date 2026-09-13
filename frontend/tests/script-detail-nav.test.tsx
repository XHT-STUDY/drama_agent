/** 兼容入口重定向测试（W1-02）：/scripts/[episode] → 工作台画布。 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const replaceMock = vi.fn();

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "p1", episode: "2" }),
  useRouter: () => ({ replace: replaceMock }),
}));

// fetch mock：最新剧本
const fetchMock = vi.fn(async (url: string) => {
  if (url.includes("/artifacts/latest")) {
    return new Response(
      JSON.stringify({
        id: "00000000-0000-0000-0000-000000000012",
        project_id: "p1",
        type: "script_draft",
        version: 2,
        episode_number: 2,
        status: "valid",
        content: {},
        content_schema_version: "1.0",
        prompt_version: "1.0",
        created_at: "2026-09-13T00:00:00Z",
        updated_at: "2026-09-13T00:00:00Z",
      }),
      { status: 200 },
    );
  }
  return new Response("{}", { status: 200 });
});
vi.stubGlobal("fetch", fetchMock);

import ScriptRedirectPage from "@/app/projects/[id]/scripts/[episode]/page";

function qc(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

describe("兼容入口：/scripts/[episode]（W1-02）", () => {
  beforeEach(() => {
    replaceMock.mockReset();
  });

  it("重定向到工作台画布的该集最新剧本（replace，不留跳板历史）", async () => {
    render(
      React.createElement(
        QueryClientProvider,
        { client: qc() },
        React.createElement(ScriptRedirectPage),
      ),
    );
    expect(screen.getByText("正在打开剧本…")).toBeTruthy();
    await waitFor(() => {
      expect(replaceMock).toHaveBeenCalledWith(
        "/projects/p1?artifact=00000000-0000-0000-0000-000000000012",
      );
    });
  });
});
