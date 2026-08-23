/** K-3 知识库页测试：列表渲染/作用域过滤/删除/检索试算。 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) =>
    React.createElement("a", { href }, children),
}));
vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "p1" }),
}));

import KnowledgePage from "@/app/projects/[id]/knowledge/page";

interface Route {
  method: string;
  pattern: RegExp;
  handler: (body: Record<string, unknown>) => unknown;
}
let routes: Route[];
let calls: Array<{ method: string; url: string; body: Record<string, unknown> }>;

function route(method: string, pattern: RegExp, handler: Route["handler"]) {
  routes.push({ method, pattern, handler });
}

beforeEach(() => {
  routes = [];
  calls = [];
  global.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
    calls.push({ method, url, body });
    for (const r of routes) {
      if (r.method === method && r.pattern.test(url)) {
        const result = r.handler(body);
        const status = Array.isArray(result) ? result[1] : 200;
        const data = Array.isArray(result) ? result[0] : result;
        return { ok: status >= 200 && status < 300, status, json: async () => data };
      }
    }
    return {
      ok: false,
      status: 404,
      json: async () => ({ request_id: "", detail: "未匹配", code: "NOT_FOUND", path: url, timestamp: "" }),
    };
  }) as unknown as typeof fetch;
});

afterEach(() => vi.restoreAllMocks());

const NOW = "2026-08-23T00:00:00Z";

const DOCS = {
  items: [
    {
      id: "d1", scope: "project", title: "林峰设定资料", category: "reference",
      source: "upload:u1", chunk_count: 3, embedded_chunk_count: 3,
      fully_embedded: true, corpus_version: "uploads_v1", created_at: NOW, deleted: false,
    },
    {
      id: "d2", scope: "global", title: "全局题材模板", category: "genre_template",
      source: "corpus", chunk_count: 2, embedded_chunk_count: 2,
      fully_embedded: true, corpus_version: "mvp_v1", created_at: NOW, deleted: false,
    },
  ],
  total: 2,
};

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    React.createElement(QueryClientProvider, { client }, React.createElement(KnowledgePage)),
  );
}

function setupDocs() {
  route("GET", /\/projects\/p1\/knowledge\?scope=all/, () => DOCS);
  route("GET", /\/projects\/p1\/knowledge\?scope=project/, () => ({
    items: [DOCS.items[0]], total: 1,
  }));
}

describe("知识库页", () => {
  it("列出项目与全局文档：作用域徽标、块数、向量化状态", async () => {
    setupDocs();
    renderPage();

    await waitFor(() => expect(screen.getByText("林峰设定资料")).toBeTruthy());
    expect(screen.getByText("全局题材模板")).toBeTruthy();
    expect(screen.getAllByText("本项目").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("全局语料").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/3 块/).textContent).toContain("向量齐全");
    // 全局文档：无删除按钮，仅"平台维护"
    expect(screen.queryByTestId("knowledge-delete-d2")).toBeNull();
    expect(screen.getByText("平台维护")).toBeTruthy();
  });

  it("作用域过滤触发重新请求", async () => {
    setupDocs();
    renderPage();
    await waitFor(() => expect(screen.getByText("林峰设定资料")).toBeTruthy());

    fireEvent.click(screen.getByTestId("knowledge-scope-project"));
    await waitFor(() => {
      const scoped = calls.filter((c) => c.url.includes("scope=project"));
      expect(scoped.length).toBe(1);
    });
    await waitFor(() => expect(screen.queryByText("全局题材模板")).toBeNull());
  });

  it("删除项目文档调用 DELETE 并失效列表", async () => {
    setupDocs();
    route("DELETE", /\/projects\/p1\/knowledge\/d1$/, () => ({ deleted: true }));
    renderPage();
    await waitFor(() => expect(screen.getByText("林峰设定资料")).toBeTruthy());

    fireEvent.click(screen.getByTestId("knowledge-delete-d1"));
    await waitFor(() => {
      const del = calls.filter((c) => c.method === "DELETE");
      expect(del.length).toBe(1);
    });
  });

  it("检索试算展示命中与 trace", async () => {
    setupDocs();
    route("POST", /\/projects\/p1\/knowledge\/search$/, (body) => ({
      query: String(body.query),
      corpus_version: "uploads_v1",
      filters: { top_k: 5 },
      elapsed_ms: 12,
      hits: [
        {
          chunk_id: "c1", content: "林峰拥有战术视野天赋……", score: 0.8123,
          document_id: "d1", document_title: "林峰设定资料",
          category: "reference", source: "upload:u1", scope: "project",
        },
      ],
    }));
    renderPage();
    await waitFor(() => expect(screen.getByText("林峰设定资料")).toBeTruthy());

    fireEvent.change(screen.getByTestId("knowledge-search-input"), {
      target: { value: "林峰的战术视野" },
    });
    fireEvent.click(screen.getByTestId("knowledge-search-button"));

    await waitFor(() => expect(screen.getByTestId("knowledge-search-hits")).toBeTruthy());
    expect(screen.getByText("相似度 0.8123")).toBeTruthy();
    expect(screen.getByTestId("knowledge-search-trace").textContent).toContain("uploads_v1");
    const posts = calls.filter((c) => c.url.includes("/knowledge/search"));
    expect(posts.length).toBe(1);
    expect(posts[0].body.query).toBe("林峰的战术视野");
  });
});
