/** Agent API 与 Hooks 测试（J-10）.
 *
 * 覆盖：
 * - TDD anchor: confirming_same_action_twice_reuses_run
 * - 发送幂等 key 复用（失败重发同 key）
 * - Turn 202 planning 轮询直至终态并失效消息
 * - 409 ACTION_STALE 可恢复错误（保留计划与输入）
 * - 绸件卸载停止 Action 轮询
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { agentApi } from "@/lib/api-client";
import { useAgentAction } from "@/hooks/use-agent-action";
import { useAgentConversation } from "@/hooks/use-agent-conversation";
import type { AgentActionResponse, AgentTurnResponse } from "@/types/api";

// ---- helpers ----

function qc() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function withProvider(client: QueryClient) {
  return function Provider({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client }, children);
  };
}

interface Route {
  method: string;
  pattern: RegExp;
  handler: (body: Record<string, unknown>, url: string) => unknown;
}

let routes: Route[];
let calls: Array<{ method: string; url: string; body: Record<string, unknown> }>;

function jsonRes(status: number, data: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => data,
  };
}

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
        const result = r.handler(body, url);
        if (result instanceof Response) return result;
        const status = Array.isArray(result) ? result[1] : 200;
        const data = Array.isArray(result) ? result[0] : result;
        return jsonRes(status, data);
      }
    }
    return jsonRes(404, {
      request_id: "", detail: "未匹配路由", code: "NOT_FOUND", path: url, timestamp: "",
    });
  }) as unknown as typeof fetch;
});

afterEach(() => {
  vi.useRealTimers();
});

const NOW = "2026-08-23T00:00:00Z";

function turn(overrides: Partial<AgentTurnResponse>): AgentTurnResponse {
  return {
    id: "turn-1",
    project_id: "p1",
    conversation_id: "c1",
    user_message_id: "m1",
    idempotency_key: "key-1",
    request_hash: "0".repeat(64),
    status: "action_proposed",
    turn_type: "plan",
    response_message_id: "m2",
    action_id: null,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

function action(overrides: Partial<AgentActionResponse>): AgentActionResponse {
  return {
    id: "a1",
    project_id: "p1",
    conversation_id: "c1",
    agent_turn_id: "turn-1",
    replan_depth: 0,
    intent: "revise_script",
    status: "proposed",
    requires_confirmation: true,
    plan: {
      goal: "按用户要求修订第 3 集剧本并重评",
      intent: "revise_script",
      command: {
        intent: "revise_script",
        source_script_id: "s-1",
        episode_number: 3,
        constraints: [],
      },
      target: { target_type: "script", episode_number: 3 },
      constraints: [],
      steps: [{ step_id: "revise", title: "修订", description: "生成新稿" }],
      expected_impact: [],
    },
    source_artifact_ids: [],
    result: null,
    run_id: null,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

// ============================================================
// TDD anchor：重复确认复用 Run
// ============================================================

describe("useAgentAction confirm", () => {
  it("confirming_same_action_twice_reuses_run", async () => {
    const confirmed = action({ status: "queued", run_id: "run-1" });
    let confirmCount = 0;

    route("POST", /\/agent\/actions\/a2\/confirm$/, () => [
      {
        action: action({ id: "a2", status: "queued", run_id: "run-1" }),
        run: {
          run_id: "run-1",
          project_id: "p1",
          action: "revise_script",
          status: "queued",
          created_at: NOW,
        },
      },
      202,
    ]);
    route("GET", /\/agent\/actions\/a1$/, () =>
      confirmCount > 0
        ? action({ status: "queued", run_id: "run-1" })
        : action({ status: "proposed" }),
    );
    route("POST", /\/agent\/actions\/a1\/confirm$/, () => {
      confirmCount += 1;
      return [
        {
          action: confirmed,
          run: {
            run_id: "run-1",
            project_id: "p1",
            action: "revise_script",
            status: "queued",
            created_at: NOW,
          },
        },
        202,
      ];
    });

    // API 层：两次 confirm 返回同一 Run（服务端幂等；独立 action id 隔离状态）
    const apiFirst = await agentApi.confirm("a2");
    const apiSecond = await agentApi.confirm("a2");
    expect(apiFirst.run.run_id).toBe("run-1");
    expect(apiSecond.run.run_id).toBe(apiFirst.run.run_id);

    // Hook 层：并行双击只发一次请求；随后再次确认返回原 Run，本地无重复状态
    const { result } = renderHook(() => useAgentAction("a1"), {
      wrapper: withProvider(qc()),
    });
    await waitFor(() => expect(result.current.action?.status).toBe("proposed"));
    const confirmCallsBefore = confirmCount;

    await act(async () => {
      await Promise.all([result.current.confirm(), result.current.confirm()]);
    });
    expect(confirmCount).toBe(confirmCallsBefore + 1); // 防重复点击：只发一次

    await act(async () => {
      await result.current.confirm();
    });
    expect(confirmCount).toBe(confirmCallsBefore + 2);

    // 唯一 Run：重复确认复用服务端返回，不产生重复本地状态
    await waitFor(() => expect(result.current.action?.run_id).toBe("run-1"));
    expect(result.current.confirmError).toBeNull();
    expect(result.current.isStale).toBe(false);
  });

  it("409 ACTION_STALE 显示可恢复错误并保留计划", async () => {
    route("GET", /\/agent\/actions\/a1$/, () => action({ status: "proposed" }));
    route("POST", /\/agent\/actions\/a1\/confirm$/, () => [
      {
        request_id: "r",
        detail: "计划基于的 Artifact 已更新",
        code: "ACTION_STALE",
        path: "",
        timestamp: NOW,
      },
      409,
    ]);

    const { result } = renderHook(() => useAgentAction("a1"), {
      wrapper: withProvider(qc()),
    });
    await waitFor(() => expect(result.current.action).not.toBeNull());

    await act(async () => {
      await result.current.confirm();
    });

    expect(result.current.isStale).toBe(true);
    expect(result.current.confirmError).toContain("重新发起规划");
    // 计划仍保留（可展示给用户对照重新发起）
    expect(result.current.action?.plan.intent).toBe("revise_script");
  });
});

// ============================================================
// 发送 / 202 轮询 / 幂等 key
// ============================================================

describe("useAgentConversation", () => {
  it("202 planning 轮询直至终态并失效消息", async () => {
    let turnPolls = 0;

    route("POST", /\/projects\/p1\/conversations$/, () => [
      { id: "c1", project_id: "p1", title: "帮我修第3集", created_at: NOW, updated_at: NOW },
      201,
    ]);
    route("POST", /\/projects\/p1\/agent\/turns$/, () => [
      turn({ status: "planning", action_id: null }),
      202,
    ]);
    route("GET", /\/agent\/turns\/turn-1$/, () => {
      turnPolls += 1;
      return turnPolls < 2
        ? turn({ status: "planning", action_id: null })
        : turn({ status: "action_proposed", action_id: "a1" });
    });
    route("GET", /\/conversations\/c1\/messages/, () => [
      {
        items: [
          {
            id: "m1",
            conversation_id: "c1",
            role: "user",
            content: "帮我修第3集",
            kind: "text",
            metadata: {},
            sequence: 1,
            created_at: NOW,
          },
        ],
        total: 1,
        offset: 0,
        limit: 50,
      },
      200,
    ]);

    const { result } = renderHook(
      () => useAgentConversation({ projectId: "p1", pollIntervalMs: 10 }),
      { wrapper: withProvider(qc()) },
    );

    await act(async () => {
      await result.current.sendTurn("帮我修第3集");
    });

    expect(result.current.conversationId).toBe("c1");
    expect(result.current.activeActionId).toBe("a1");
    expect(result.current.lastTurn?.status).toBe("action_proposed");
    await waitFor(() => expect(result.current.messages.length).toBe(1));
    expect(turnPolls).toBeGreaterThanOrEqual(2);
  });

  it("发送失败保留输入，重发复用同一幂等 key", async () => {
    route("POST", /\/projects\/p1\/conversations$/, () => [
      { id: "c1", project_id: "p1", title: "t", created_at: NOW, updated_at: NOW },
      201,
    ]);
    route("POST", /\/projects\/p1\/agent\/turns$/, () => [
      {
        request_id: "r",
        detail: "服务暂不可用",
        code: "INTERNAL_ERROR",
        path: "",
        timestamp: NOW,
      },
      500,
    ]);

    const { result } = renderHook(
      () => useAgentConversation({ projectId: "p1" }),
      { wrapper: withProvider(qc()) },
    );

    await act(async () => {
      await result.current.sendTurn("帮我修第3集").catch(() => undefined);
    });
    expect(result.current.lastFailedContent).toBe("帮我修第3集");
    expect(result.current.sendError).toContain("INTERNAL_ERROR");

    await act(async () => {
      await result.current.sendTurn("帮我修第3集").catch(() => undefined);
    });

    const turnPosts = calls.filter((c) => c.url.includes("/agent/turns"));
    expect(turnPosts.length).toBe(2);
    expect(turnPosts[0].body.idempotency_key).toBe(turnPosts[1].body.idempotency_key);
  });
});

// ============================================================
// 卸载停止轮询
// ============================================================

describe("useAgentAction polling lifecycle", () => {
  it("非终态轮询，卸载后停止", async () => {
    let fetchCount = 0;
    route("GET", /\/agent\/actions\/a1$/, () => {
      fetchCount += 1;
      return action({ status: "running", run_id: "run-1" });
    });

    const { result, unmount } = renderHook(
      () => useAgentAction("a1", { pollIntervalMs: 20 }),
      { wrapper: withProvider(qc()) },
    );

    await waitFor(() => expect(result.current.action?.status).toBe("running"));
    const initial = fetchCount;
    expect(initial).toBeGreaterThanOrEqual(1);

    // 运行中持续轮询
    await waitFor(
      () => expect(fetchCount).toBeGreaterThan(initial),
      { timeout: 2000 },
    );

    unmount();
    const afterUnmount = fetchCount;
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(fetchCount).toBe(afterUnmount); // 卸载后不再请求
  });
});
