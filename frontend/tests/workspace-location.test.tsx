/** useWorkspaceLocation 测试（W1-02）：URL 字段解析/校验/导航语义。 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import React from "react";

const routerState = {
  pathname: "/projects/p1",
  search: "",
  pushed: [] as string[],
  replaced: [] as string[],
};

vi.mock("next/navigation", () => ({
  usePathname: () => routerState.pathname,
  useRouter: () => ({
    push: (u: string) => {
      routerState.pushed.push(u);
      routerState.search = u.includes("?") ? u.slice(u.indexOf("?")) : "";
    },
    replace: (u: string) => {
      routerState.replaced.push(u);
      routerState.search = u.includes("?") ? u.slice(u.indexOf("?")) : "";
    },
  }),
  useSearchParams: () => new URLSearchParams(routerState.search),
}));

import { useWorkspaceLocation } from "@/hooks/use-workspace-location";

function setup(search = ""): void {
  routerState.search = search;
  routerState.pushed = [];
  routerState.replaced = [];
}

describe("useWorkspaceLocation（W1-02）", () => {
  beforeEach(() => {
    setup();
  });

  it("缺省：无 artifact，panel=read", () => {
    setup("");
    const loc = renderHook(() => useWorkspaceLocation()).result.current;
    expect(loc.artifactId).toBeNull();
    expect(loc.panel).toBe("read");
  });

  it("合法字段全部解析", () => {
    setup(
      "?artifact=00000000-0000-0000-0000-000000000001&scene=3" +
        "&conversation=00000000-0000-0000-0000-000000000002" +
        "&panel=diff&compare=00000000-0000-0000-0000-000000000003",
    );
    const loc = renderHook(() => useWorkspaceLocation()).result.current;
    expect(loc.artifactId).toBe("00000000-0000-0000-0000-000000000001");
    expect(loc.scene).toBe(3);
    expect(loc.conversationId).toBe("00000000-0000-0000-0000-000000000002");
    expect(loc.panel).toBe("diff");
    expect(loc.compareId).toBe("00000000-0000-0000-0000-000000000003");
  });

  it("切稿 push（后退回到上一篇）并重置 scene/compare", () => {
    setup("?artifact=00000000-0000-0000-0000-000000000001&scene=2&panel=evaluation");
    const loc = renderHook(() => useWorkspaceLocation()).result.current;
    act(() => {
      loc.openArtifact("00000000-0000-0000-0000-000000000009");
    });
    expect(routerState.pushed).toHaveLength(1);
    expect(routerState.pushed[0]).toBe(
      "/projects/p1?artifact=00000000-0000-0000-0000-000000000009&panel=read",
    );
  });

  it("面板/场景/基线/会话 replace（不占浏览历史）", () => {
    setup("?artifact=00000000-0000-0000-0000-000000000001");
    // 真实 Next 中导航会触发重渲染；mock 路由需手动 rerender 读取新 searchParams
    const hook_ = renderHook(() => useWorkspaceLocation());
    const loc = () => hook_.result.current;

    act(() => loc().setPanel("diff"));
    expect(routerState.replaced[0]).toBe(
      "/projects/p1?artifact=00000000-0000-0000-0000-000000000001&panel=diff",
    );
    expect(routerState.pushed).toHaveLength(0);

    hook_.rerender();
    act(() => loc().setScene(5));
    expect(routerState.replaced[1]).toContain("scene=5");

    hook_.rerender();
    act(() => loc().setCompare("00000000-0000-0000-0000-000000000003"));
    expect(routerState.replaced[2]).toContain("compare=00000000-0000-0000-0000-000000000003");

    hook_.rerender();
    act(() => loc().setConversation("00000000-0000-0000-0000-000000000002"));
    expect(routerState.replaced[3]).toContain("conversation=00000000-0000-0000-0000-000000000002");
  });

  it("run 字段解析与 setRun replace", () => {
    setup("?run=00000000-0000-0000-0000-000000000007");
    const loc = renderHook(() => useWorkspaceLocation()).result.current;
    expect(loc.runId).toBe("00000000-0000-0000-0000-000000000007");
    act(() => loc.setRun(null));
    expect(routerState.replaced).toContain("/projects/p1");
  });

  it("非法值提示是粘性的：URL 清理后 notice 仍可读，clearNotice 关闭", async () => {
    setup("?artifact=not-a-uuid");
    const hook_ = renderHook(() => useWorkspaceLocation());
    // URL 清理 effect 已 replace 掉非法参数，但提示保留在 state
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(hook_.result.current.notice).toContain("artifact");
    act(() => hook_.result.current.clearNotice());
    expect(hook_.result.current.notice).toBeNull();
  });

  it("非法值：状态置空 + 提示 + URL 清理（不猜测最新稿）", () => {
    setup("?artifact=not-a-uuid&scene=0&panel=bogus");
    const loc = renderHook(() => useWorkspaceLocation()).result.current;
    expect(loc.artifactId).toBeNull();
    expect(loc.scene).toBeNull();
    expect(loc.panel).toBe("read");
    expect(loc.notice).toContain("artifact");
    // effect 触发的 replace 清除非法参数
    expect(
      routerState.replaced.some((u) => u === "/projects/p1"),
    ).toBe(true);
  });
});
