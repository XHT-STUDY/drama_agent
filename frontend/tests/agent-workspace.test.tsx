/** 对话式创作工作台组件测试（J-11）。
 *
 * TDD anchor: ambiguous_turn_renders_clarification_without_confirmation_button
 * 另覆盖：空会话命令示例、计划卡确认、结果消息、Enter/Shift+Enter、回滚开关。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ---- Mocks ----
vi.mock("next/link", () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) =>
    React.createElement("a", { href }, children),
}));
vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "p1" }),
}));

import { AgentWorkspace } from "@/features/agent/AgentWorkspace";
import { AgentComposer } from "@/features/agent/AgentComposer";
import { MessageList } from "@/features/agent/MessageList";
import { ActionPlanCard } from "@/features/agent/ActionPlanCard";
import type { AgentActionResponse, ChatMessage } from "@/types/api";

// ---- fetch 路由 mock ----

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
        return {
          ok: status >= 200 && status < 300,
          status,
          json: async () => data,
        };
      }
    }
    return {
      ok: false,
      status: 404,
      json: async () => ({
        request_id: "", detail: "未匹配", code: "NOT_FOUND", path: url, timestamp: "",
      }),
    };
  }) as unknown as typeof fetch;
});

afterEach(() => {
  vi.unstubAllEnvs();
});

const NOW = "2026-08-23T00:00:00Z";

function msg(overrides: Partial<ChatMessage>): ChatMessage {
  return {
    id: `m-${Math.random().toString(36).slice(2, 8)}`,
    conversation_id: "c1",
    role: "assistant",
    content: "…",
    kind: "text",
    metadata: {},
    sequence: 1,
    created_at: NOW,
    ...overrides,
  };
}

function actionFixture(): AgentActionResponse {
  return {
    id: "a1",
    project_id: "p1",
    conversation_id: "c1",
    agent_turn_id: "t1",
    replan_depth: 0,
    intent: "revise_script",
    status: "proposed",
    requires_confirmation: true,
    plan: {
      goal: "按用户要求修订第 3 集剧本并重评",
      intent: "revise_script",
      command: { intent: "revise_script", source_script_id: "s1", episode_number: 3, constraints: [] },
      target: { target_type: "script", episode_number: 3 },
      constraints: ["增加正面冲突"],
      steps: [{ step_id: "revise", title: "生成新稿", description: "候选稿连续性通过后生效" }],
      expected_impact: ["第 3 集产生新版本"],
    },
    source_artifact_ids: [
      { artifact_id: "s1", artifact_type: "script_draft", episode_number: 3, version: 2, checksum: null },
    ],
    result: null,
    run_id: null,
    created_at: NOW,
    updated_at: NOW,
  };
}

function qc() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function projectFixture() {
  return {
    id: "p1",
    title: "测试项目",
    status: "writing",
    target_episode_count: 10,
    current_episode_count: 3,
    created_at: NOW,
    updated_at: NOW,
  } as Parameters<typeof AgentWorkspace>[0]["project"];
}

function setupWorkspace(messages: ChatMessage[] = []) {
  route("GET", /\/projects\/p1\/conversations\?/, () => ({
    items: [{ id: "c1", project_id: "p1", title: "会话一", created_at: NOW, updated_at: NOW }],
    total: 1,
    offset: 0,
    limit: 50,
  }));
  route("GET", /\/conversations\/c1\/messages/, () => ({
    items: messages,
    total: messages.length,
    offset: 0,
    limit: 50,
  }));
  return render(
    React.createElement(
      QueryClientProvider,
      { client: qc() },
      React.createElement(AgentWorkspace, { projectId: "p1", project: projectFixture() }),
    ),
  );
}

// ============================================================
// TDD anchor：澄清轮
// ============================================================

describe("AgentWorkspace 澄清轮", () => {
  it("ambiguous_turn_renders_clarification_without_confirmation_button", async () => {
    setupWorkspace([
      msg({ role: "user", content: "帮我改一下这里", kind: "text", sequence: 1 }),
      msg({
        content: "你希望修改哪一个目标：大纲、剧本，还是指定集数？",
        kind: "clarification",
        sequence: 2,
      }),
    ]);

    await waitFor(() =>
      expect(screen.getByTestId("clarification-message")).toBeTruthy(),
    );
    expect(screen.getByText(/你希望修改哪一个目标/)).toBeTruthy();
    // 澄清没有可确认的计划：页面不出现确认按钮
    expect(screen.queryByTestId("confirm-action")).toBeNull();
    expect(screen.queryByText("确认执行")).toBeNull();
  });
});

// ============================================================
// 空会话与命令示例
// ============================================================

describe("空会话命令示例", () => {
  it("展示四个可点击示例，点击后可发送", async () => {
    setupWorkspace();
    route("POST", /\/projects\/p1\/agent\/turns$/, () => [
      {
        id: "t2",
        project_id: "p1",
        conversation_id: "c1",
        user_message_id: "m9",
        idempotency_key: "k",
        request_hash: "0",
        status: "answered",
        turn_type: "answer",
        created_at: NOW,
        updated_at: NOW,
      },
      200,
    ]);

    const examples = await screen.findAllByTestId("command-examples");
    expect(examples.length).toBeGreaterThan(0);
    const buttons = examples[0].querySelectorAll("button");
    expect(buttons.length).toBe(4);

    fireEvent.click(buttons[0]);
    const send = screen.getByTestId("composer-send");
    await waitFor(() => expect((send as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(send);

    await waitFor(() => {
      const posts = calls.filter((c) => c.url.includes("/agent/turns"));
      expect(posts.length).toBe(1);
      expect(String(posts[0].body.content)).toContain("创建剧本");
    });
  });
});

// ============================================================
// 计划卡
// ============================================================

describe("ActionPlanCard", () => {
  it("展示目标/来源/步骤，确认触发 POST confirm", async () => {
    route("GET", /\/agent\/actions\/a1$/, () => actionFixture());
    route("POST", /\/agent\/actions\/a1\/confirm$/, () => [
      {
        action: { ...actionFixture(), status: "queued", run_id: "run-1" },
        run: { run_id: "run-1", project_id: "p1", action: "revise_script", status: "queued", created_at: NOW },
      },
      202,
    ]);
    setupWorkspace([
      msg({
        content: "计划:…",
        kind: "action_plan",
        metadata: { agent_action_id: "a1" },
        sequence: 2,
      }),
    ]);

    await waitFor(() => expect(screen.getByText("按用户要求修订第 3 集剧本并重评")).toBeTruthy());
    expect(screen.getByTestId("plan-sources").textContent).toContain("script_draft v2");
    expect(screen.getByTestId("plan-steps").textContent).toContain("生成新稿");

    fireEvent.click(screen.getByTestId("confirm-action"));
    await waitFor(() => {
      const confirms = calls.filter((c) => c.url.includes("/confirm"));
      expect(confirms.length).toBe(1);
    });
  });

  it("结果消息为自然文案：无状态术语重复、约束仅 blocked 显示、不渲染产物链接", () => {
    const messages = [
      msg({
        content: "本轮任务部分完成。",
        kind: "action_result",
        metadata: {
          goal_status: "partially_achieved",
          score_delta: 2.5,
          remaining_constraints: ["第 3 集剧本仍引用旧大纲"],
          evidence_artifact_ids: ["00000000-0000-0000-0000-000000000003"],
        },
        sequence: 3,
      }),
    ];
    render(
      React.createElement(
        QueryClientProvider,
        { client: qc() },
        React.createElement(MessageList, { messages }),
      ),
    );

    // 自然文案上屏；状态由计划卡 OutcomeView 承载，消息不再重复
    expect(screen.getByTestId("result-message").textContent).toContain("本轮任务部分完成。");
    expect(screen.getByTestId("result-score-delta").textContent).toContain("+2.5");
    expect(screen.queryByTestId("result-goal-status")).toBeNull();
    // 部分达成只给计数不给清单（把用户要求当失败陈列是噪音）
    expect(screen.queryByTestId("result-remaining")).toBeNull();
    expect(screen.getByTestId("result-partial-count").textContent).toContain("1 项");
    // 产物 UUID 链接不再出现在消息流（版本页查看）
    expect(screen.queryByTestId("result-evidence")).toBeNull();
    expect(screen.queryByText(/产物 00000000/)).toBeNull();
  });

  it("确认门结果消息渲染为干净阶段文案（无状态术语）", () => {
    const messages = [
      msg({
        content: "StoryBible 与分集大纲已生成，等待确认后继续创作剧本。",
        kind: "action_result",
        metadata: {
          goal_status: "partially_achieved",
          stage_gate: "outline",
        },
        sequence: 3,
      }),
    ];
    render(
      React.createElement(
        QueryClientProvider,
        { client: qc() },
        React.createElement(MessageList, { messages }),
      ),
    );

    expect(screen.getByText("StoryBible 与分集大纲已生成，等待确认后继续创作剧本。")).toBeTruthy();
    expect(screen.queryByTestId("result-goal-status")).toBeNull();
  });

  it("发送中：用户消息乐观上屏 + 正在思考气泡", () => {
    render(
      React.createElement(
        QueryClientProvider,
        { client: qc() },
        React.createElement(MessageList, { messages: [], pendingContent: "写一个足球故事" }),
      ),
    );

    expect(screen.getByTestId("pending-user-message").textContent).toContain("写一个足球故事");
    expect(screen.getByTestId("agent-typing").textContent).toContain("正在思考");
  });
});

// ============================================================
// Composer 键盘行为
// ============================================================

describe("AgentComposer 键盘", () => {
  function setup(onSend: (content: string) => void) {
    return render(
      React.createElement(AgentComposer, {
        sending: false,
        sendError: null,
        failedContent: null,
        onSend,
      }),
    );
  }

  it("Enter 发送，Shift+Enter 换行不发送", () => {
    const onSend = vi.fn();
    setup(onSend);
    const textarea = screen.getByLabelText("输入创作指令") as HTMLTextAreaElement;

    fireEvent.change(textarea, { target: { value: "评估第 1 集" } });
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
    expect(textarea.value).toBe("评估第 1 集");

    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend).toHaveBeenCalledWith("评估第 1 集", undefined);
    expect(textarea.value).toBe(""); // 发送后清空
  });

  it("失败后恢复草稿并聚焦（focusSignal）", () => {
    const onSend = vi.fn();
    const { rerender } = render(
      React.createElement(AgentComposer, {
        sending: false,
        sendError: "发送失败",
        failedContent: "帮我修第 3 集",
        onSend,
        focusSignal: 0,
      }),
    );
    expect(screen.getByLabelText("输入创作指令").textContent).toContain("帮我修第 3 集");

    rerender(
      React.createElement(AgentComposer, {
        sending: false,
        sendError: null,
        failedContent: null,
        onSend,
        focusSignal: 1,
      }),
    );
    expect(document.activeElement).toBe(screen.getByLabelText("输入创作指令"));
  });
});


// ============================================================
// 回滚开关（DESIGN §15）
// ============================================================

describe("回滚开关", () => {
  it("flag=false 时项目页渲染 legacy ChatInput 而非 AgentWorkspace", async () => {
    vi.stubEnv("NEXT_PUBLIC_AGENT_WORKSPACE_ENABLED", "false");
    // 动态导入使模块级 flag 读取 stub 后的环境变量
    vi.resetModules();
    const { default: ProjectPage } = await import("@/app/projects/[id]/page");

    route("GET", /\/projects\/p1$/, () => ({
      id: "p1", title: "回滚测试", status: "writing",
      target_episode_count: 10, current_episode_count: 0,
      created_at: NOW, updated_at: NOW,
    }));
    route("GET", /\/projects\/p1\/runs$/, () => ({ items: [], total: 0, offset: 0, limit: 20 }));

    render(
      React.createElement(
        QueryClientProvider,
        { client: qc() },
        React.createElement(ProjectPage),
      ),
    );

    await waitFor(() => expect(screen.getByText("开始创作")).toBeTruthy());
    expect(screen.queryByTestId("empty-conversation")).toBeNull();
    expect(screen.queryByTestId("command-examples")).toBeNull();
  });
});


describe("AgentComposer 集数结构化传递（L-1）", () => {
  function setup(onSend: (c: string, o?: { episodeCount?: number }) => void) {
    return render(
      React.createElement(AgentComposer, {
        sending: false, sendError: null, failedContent: null, onSend,
      }),
    );
  }

  it("未调整设置：不携带集数选项", () => {
    const onSend = vi.fn();
    setup(onSend);
    const textarea = screen.getByLabelText("输入创作指令");
    fireEvent.change(textarea, { target: { value: "写个剧本" } });
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("写个剧本", undefined);
  });

  it("调整过设置：集数结构化携带，不拼进消息文本", () => {
    const onSend = vi.fn();
    setup(onSend);
    fireEvent.click(screen.getByText("创作设置"));
    const input = screen.getByLabelText("目标集数") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "6" } });
    const textarea = screen.getByLabelText("输入创作指令");
    fireEvent.change(textarea, { target: { value: "写个剧本" } });
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("写个剧本", { episodeCount: 6 });
    expect(onSend.mock.calls[0][0]).not.toContain("目标");
  });
});




describe("ActionPlanCard 确认门继续按钮（L-3）", () => {
  function actionFixture(overrides: Record<string, unknown>) {
    const base = actionFixtureBase();
    return { ...base, ...overrides } as typeof base;
  }

  function actionFixtureBase() {
    return JSON.parse(JSON.stringify({
      id: "a1", project_id: "p1", conversation_id: "c1", agent_turn_id: "t1",
      replan_depth: 0, intent: "create_script", status: "proposed",
      requires_confirmation: true,
      plan: {
        goal: "创建剧本", intent: "create_script",
        command: { intent: "create_script", user_input: "x", outline_count: 10, script_count: 3, stop_after: "outline" },
        target: { target_type: "project" }, constraints: [], steps: [], expected_impact: [],
      },
      source_artifact_ids: [], result: null, run_id: "run-1",
      created_at: NOW, updated_at: NOW,
    }));
  }

  it("分段门 needs_review 显示继续按钮并 POST continue", async () => {
    const { QueryClient, QueryClientProvider } = await import("@tanstack/react-query");
    route("GET", /\/agent\/actions\/a1$/, () => actionFixture({ status: "needs_review" }));
    route("GET", /\/runs\/run-1$/, () => ({
      run_id: "run-1", project_id: "p1", action: "create_script",
      status: "needs_review", stage_gate: "outline",
      created_at: NOW, updated_at: NOW,
    }));
    let continued = 0;
    route("POST", /\/runs\/run-1\/continue$/, () => {
      continued += 1;
      return { run_id: "run-1", project_id: "p1", action: "create_script", status: "queued", agent_action_id: null, stage_gate: null, created_at: NOW, updated_at: NOW };
    });

    render(
      React.createElement(
        QueryClientProvider,
        { client: new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }) },
        React.createElement(ActionPlanCard, { actionId: "a1", projectId: "p1" }),
      ),
    );

    // L-4 起按钮组替代单按钮：大纲门提供 先写第1集/前5集/写全部
    const btn = await screen.findByTestId("continue-all");
    expect(btn).toBeTruthy();
    expect(screen.getByTestId("continue-batch-1")).toBeTruthy();
    expect(screen.getByTestId("continue-batch-5")).toBeTruthy();


    fireEvent.click(btn);
    await waitFor(() => expect(continued).toBe(1));
  });

  it("大纲门内嵌内容预览（SB 摘要 + 分集列表），不必跳页查看", async () => {
    const { QueryClient, QueryClientProvider } = await import("@tanstack/react-query");
    route("GET", /\/agent\/actions\/a1$/, () => actionFixture({ status: "needs_review" }));
    route("GET", /\/runs\/run-1$/, () => ({
      run_id: "run-1", project_id: "p1", action: "create_script",
      status: "needs_review", stage_gate: "outline",
      created_at: NOW, updated_at: NOW,
    }));
    route("GET", /\/projects\/p1\/artifacts\/latest\?type=story_bible.*$/, () => ({
      id: "sb-1", project_id: "p1", type: "story_bible", version: 1,
      episode_number: 1, status: "valid", created_at: NOW, updated_at: NOW,
      content: {
        title: "逆风少年", genre: "热血", logline: "被弃用后逆袭",
        protagonist: { name: "林风" }, antagonist: { name: "赵教练" },
      },
    }));
    route("GET", /\/projects\/p1\/artifacts\/latest\?type=episode_outline_set.*$/, () => ({
      id: "ol-1", project_id: "p1", type: "episode_outline_set", version: 1,
      episode_number: 1, status: "valid", created_at: NOW, updated_at: NOW,
      content: {
        episodes: [
          { episode_number: 1, title: "落选", objective: "建立冲突" },
          { episode_number: 2, title: "转机", objective: "遇见伯乐" },
        ],
      },
    }));

    render(
      React.createElement(
        QueryClientProvider,
        { client: new QueryClient({ defaultOptions: { queries: { retry: false } } }) },
        React.createElement(ActionPlanCard, { actionId: "a1", projectId: "p1" }),
      ),
    );

    await screen.findByTestId("gate-preview");
    // 数据异步到达:等 SB 摘要与分集列表出现
    await screen.findByText(/逆风少年/);
    expect(screen.getByTestId("gate-preview").textContent).toContain("林风");
    const episodes = screen.getByTestId("gate-preview-episodes");
    expect(episodes.textContent).toContain("第 1 集");
    expect(episodes.textContent).toContain("落选");
    expect(episodes.textContent).toContain("第 2 集");
  });

  it("非分段计划不显示继续按钮", async () => {
    const { QueryClient, QueryClientProvider } = await import("@tanstack/react-query");
    const fixture = actionFixture({ status: "needs_review" });
    delete (fixture.plan.command as Record<string, unknown>).stop_after;
    route("GET", /\/agent\/actions\/a1$/, () => fixture);

    render(
      React.createElement(
        QueryClientProvider,
        { client: new QueryClient({ defaultOptions: { queries: { retry: false } } }) },
        React.createElement(ActionPlanCard, { actionId: "a1", projectId: "p1" }),
      ),
    );
    await waitFor(() => expect(screen.getByText("创建剧本")).toBeTruthy());
    expect(screen.queryByTestId("continue-creation")).toBeNull();
  });
});


describe("ActionPlanCard 剧本分批门（L-4）", () => {
  it("scripts 门显示三个批次按钮并按选择 POST batch_size", async () => {
    const { QueryClient, QueryClientProvider } = await import("@tanstack/react-query");
    const base = actionFixture();
    const fixture: AgentActionResponse = {
      ...base,
      status: "needs_review",
      run_id: "run-1",
      plan: {
        ...base.plan,
        command: {
          intent: "create_script", user_input: "x",
          outline_count: 10, script_count: 2, stop_after: "scripts",
        } as unknown as AgentActionResponse["plan"]["command"],
      },
    };
    route("GET", /\/agent\/actions\/a1$/, () => fixture);
    route("GET", /\/runs\/run-1$/, () => ({
      run_id: "run-1", project_id: "p1", action: "create_script",
      status: "needs_review", stage_gate: "scripts",
      created_at: NOW, updated_at: NOW,
    }));
    const bodies: Array<Record<string, unknown>> = [];
    route("POST", /\/runs\/run-1\/continue$/, (body) => {
      bodies.push(body);
      return {
        run_id: "run-1", project_id: "p1", action: "create_script",
        status: "queued", stage_gate: null, created_at: NOW, updated_at: NOW,
      };
    });

    render(
      React.createElement(
        QueryClientProvider,
        { client: new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }) },
        React.createElement(ActionPlanCard, { actionId: "a1", projectId: "p1" }),
      ),
    );

    await waitFor(() => expect(screen.getByTestId("continue-batch-1")).toBeTruthy());
    expect(screen.getByTestId("continue-batch-5")).toBeTruthy();
    expect(screen.getByTestId("continue-all")).toBeTruthy();

    console.log('L4 DEBUG calls:', JSON.stringify(calls.filter(c => c.url.includes("runs"))));
    fireEvent.click(screen.getByTestId("continue-batch-5"));
    await waitFor(() => expect(bodies.length).toBe(1));
    expect(bodies[0].batch_size).toBe(5);
  });
});
