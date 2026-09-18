/** StoryStatePanel 组件测试（M-05 / W3-07）.
 *
 * 覆盖：
 * - 状态徽章与恢复入口（stale/pending/gap/missing 显示刷新，ready 不显示）
 * - 基准信息：截至第 N 集 + 工作集口径
 * - 分账展示：作者事实（来源跳转）、角色已知、作者未来计划（标注未发生）、
 *   锁定事实、未闭合伏笔
 * - 作者语言：不出现"嵌入/摘要任务/Memory job"等内部概念
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import React from "react";

import { StoryStatePanel } from "@/features/story-bible/StoryStatePanel";
import type { StoryStateResponse } from "@/types/api";

function makeState(
  overrides: Partial<StoryStateResponse> = {},
): StoryStateResponse {
  return {
    project_id: "proj-1",
    run_id: null,
    status: "ready",
    requested_through: 5,
    through_episode: 5,
    missing_episodes: [],
    stale_from_episode: null,
    state_artifact_id: "state-1",
    warnings: [],
    projection: {
      through_episode: 5,
      author_facts: [
        {
          fact_id: "fact_01_01",
          text: "旧宅夹层藏有契约",
          source_episode: 1,
          source_scene: 2,
          source_artifact_id: "script-1",
        },
        {
          fact_id: "fact_03_01",
          text: "调包照片被采信",
          source_episode: 3,
          source_scene: 1,
          source_artifact_id: "script-3",
        },
      ],
      character_known_facts: [
        {
          character_id: "char_lin",
          facts: [{ fact_id: "fact_01_01", learned_episode: 2, source_scene: 1 }],
        },
        {
          character_id: "char_su",
          facts: [{ fact_id: "fact_03_01", learned_episode: 5, source_scene: 2 }],
        },
      ],
      open_loops: [{ loop_id: "loop_01_01", description: "契约为何被藏" }],
      resolved_loops: [{ loop_id: "loop_02_01", description: "玉佩之谜" }],
      props: [{ prop_id: "prop_01_01", holder_character_id: "char_lin" }],
      future_plans: [
        { text: "第 8 集当庭揭示", reveal_episode: 8, revealed: false },
      ],
      locked_facts: ["母亲签名的契约真实存在"],
    },
    ...overrides,
  };
}

describe("StoryStatePanel", () => {
  it("ready 状态显示徽章与基准,不显示恢复入口", () => {
    render(<StoryStatePanel state={makeState()} />);
    expect(screen.getByRole("status", { name: /可用/ })).toBeTruthy();
    expect(screen.getByTestId("story-state-basis").textContent).toContain(
      "截至第 5 集",
    );
    expect(screen.getByTestId("story-state-basis").textContent).toContain(
      "当前采用稿",
    );
    expect(screen.queryByTestId("story-state-refresh")).toBeNull();
  });

  it("stale 显示待复核徽章、起始集数与恢复入口", () => {
    const onRefresh = vi.fn();
    render(
      <StoryStatePanel
        state={makeState({
          status: "stale",
          stale_from_episode: 3,
          warnings: ["第 3 集起的派生相对当前工作集待复核(来源已变化)"],
        })}
        onRefresh={onRefresh}
      />,
    );
    expect(screen.getByRole("status", { name: /待复核/ })).toBeTruthy();
    expect(screen.getByTestId("story-state-basis").textContent).toContain(
      "第 3 集起待复核",
    );
    expect(screen.getByTestId("story-state-warnings").textContent).toContain(
      "待复核",
    );
    fireEvent.click(screen.getByTestId("story-state-refresh"));
    expect(onRefresh).toHaveBeenCalledOnce();
  });

  it("pending/gap/missing 均提供恢复入口", () => {
    for (const status of ["pending", "gap", "missing"] as const) {
      const { unmount } = render(
        <StoryStatePanel
          state={makeState({ status, projection: null })}
          onRefresh={() => {}}
        />,
      );
      expect(screen.getByTestId("story-state-refresh")).toBeTruthy();
      unmount();
    }
  });

  it("作者事实带来源跳转并回调确切 Artifact", () => {
    const onOpenSource = vi.fn();
    render(
      <StoryStatePanel state={makeState()} onOpenSource={onOpenSource} />,
    );
    const facts = screen.getByTestId("author-facts");
    expect(facts.textContent).toContain("旧宅夹层藏有契约");
    const button = screen.getByRole("button", {
      name: "查看来源：第 1 集第 2 场",
    });
    fireEvent.click(button);
    expect(onOpenSource).toHaveBeenCalledWith("script-1", 1, 2);
  });

  it("角色已知与作者事实分账展示", () => {
    render(<StoryStatePanel state={makeState()} />);
    const knowledge = screen.getByTestId("character-knowledge");
    expect(knowledge.textContent).toContain("char_lin");
    expect(knowledge.textContent).toContain("第 2 集得知");
    // 作者视角事实区不含"得知"措辞
    expect(screen.getByTestId("author-facts").textContent).not.toContain(
      "得知",
    );
  });

  it("作者未来计划标注未发生,不与已发生事实混淆", () => {
    render(<StoryStatePanel state={makeState()} />);
    const plans = screen.getByTestId("future-plans");
    expect(plans.textContent).toContain("第 8 集当庭揭示");
    expect(plans.textContent).toContain("计划");
    expect(screen.getByTestId("author-facts").textContent).not.toContain(
      "当庭揭示",
    );
  });

  it("展示锁定事实与伏笔", () => {
    render(<StoryStatePanel state={makeState()} />);
    expect(screen.getByText(/母亲签名的契约真实存在/)).toBeTruthy();
    expect(screen.getByTestId("open-loops").textContent).toContain(
      "契约为何被藏",
    );
  });

  it("使用作者语言,不暴露内部实现概念", () => {
    const { container } = render(<StoryStatePanel state={makeState()} />);
    const text = container.textContent ?? "";
    for (const banned of ["嵌入", "摘要任务", "Memory job", "embedding"]) {
      expect(text).not.toContain(banned);
    }
  });

  it("missing(无投影)只显示状态与提示", () => {
    render(
      <StoryStatePanel
        state={makeState({
          status: "missing",
          projection: null,
          state_artifact_id: null,
        })}
      />,
    );
    expect(screen.getByRole("status", { name: /未建立/ })).toBeTruthy();
    expect(screen.queryByTestId("author-facts")).toBeNull();
  });
});
