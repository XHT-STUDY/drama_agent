/** 剧本编辑视图与评估报告测试 (H-05).
 *
 * 测试：
 * - EpisodeNav 集数导航、选中状态、状态图标
 * - ScriptView 剧本展示、场景渲染、对白、highlight 定位
 * - ScoreBar 维度评分条、颜色分级
 * - IssueCard 问题卡片、scene 定位按钮
 * - EvaluationPanel 四态覆盖（加载/错误/无报告/有报告）
 * - risk_flags 明显展示
 * - need_revision 标记
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import React from "react";

// ---- Mock next/link ----
vi.mock("next/link", () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) =>
    React.createElement("a", { href }, children),
}));

import { EpisodeNav } from "@/features/episodes/EpisodeNav";
import type { EpisodeNavItem } from "@/features/episodes/EpisodeNav";
import { ScriptView } from "@/features/scripts/ScriptView";
import { ScoreBar } from "@/features/evaluations/ScoreBar";
import { IssueCard } from "@/features/evaluations/IssueCard";
import { EvaluationPanel } from "@/features/evaluations/EvaluationPanel";
import type { ScriptDraftContent, EvaluationReportContent, EvaluationIssue } from "@/types/api";

// ============================================================
// 工厂函数
// ============================================================

function makeScene(num: number) {
  return {
    scene_number: num,
    location: `球场${num}`,
    time_of_day: "白天",
    characters: ["林风", "教练"],
    action: `激烈的训练开始了，林风展现出惊人的速度。这是第${num}场。`,
    dialogue: [
      { speaker: "林风", text: "我会赢下这场比赛！" },
      { speaker: "教练", text: "我相信你。" },
    ],
  };
}

function makeScriptContent(overrides: Partial<ScriptDraftContent> = {}): ScriptDraftContent {
  return {
    episode_number: 1,
    title: "逆袭的开始",
    opening_hook: "一个被抛弃的少年，要如何证明自己？",
    scenes: [makeScene(1), makeScene(2), makeScene(3)],
    ending_hook: "训练结束的哨声响起，林风的命运也即将改变。",
    plain_text: "",
    word_count: 3500,
    dialogue_ratio: 0.42,
    referenced_outline_artifact_id: "outline-art-1",
    ...overrides,
  };
}

function makeIssue(overrides: Partial<EvaluationIssue> = {}): EvaluationIssue {
  return {
    issue_id: "iss-1",
    dimension: "opening_hook",
    severity: "medium",
    scene_number: 1,
    evidence: "开头不够抓人",
    diagnosis: "钩子缺乏悬念",
    suggestion: "用倒叙开场制造悬念",
    ...overrides,
  };
}

function makeEvaluationReport(overrides: Partial<EvaluationReportContent> = {}): EvaluationReportContent {
  return {
    episode_number: 1,
    script_artifact_id: "script-art-1",
    rubric_version: "1.0.0",
    dimension_scores: {
      opening_hook: 72,
      main_clarity: 85,
      character_appeal: 80,
      conflict_intensity: 78,
      payoff_density: 70,
      ending_hook: 75,
      pacing: 82,
      visualizability: 68,
      compliance_safety: 90,
    },
    overall_score: 77.5,
    strengths: ["角色塑造鲜明", "对白自然流畅"],
    issues: [
      makeIssue({ issue_id: "iss-1" }),
      makeIssue({ issue_id: "iss-2", dimension: "visualizability", severity: "low", scene_number: null, evidence: "场景描述偏少", diagnosis: "部分场景可视化程度不足", suggestion: "增加动作和场景细节描写" }),
      makeIssue({ issue_id: "iss-3", dimension: "compliance_safety", severity: "high", scene_number: 2, evidence: "涉及敏感话题", diagnosis: "第二场有轻微合规风险", suggestion: "修改相关表述" }),
    ],
    revision_suggestions: ["加强开头钩子", "增加视觉化描写"],
    need_revision: false,
    risk_flags: [],
    ...overrides,
  };
}

// ============================================================
// EpisodeNav
// ============================================================

describe("EpisodeNav", () => {
  const onSelect = vi.fn();

  beforeEach(() => {
    onSelect.mockClear();
  });

  it("渲染所有集数按钮（默认10集）", () => {
    render(React.createElement(EpisodeNav, {
      episodes: [],
      currentEpisode: 1,
      targetCount: 10,
      onSelect,
    }));
    for (let i = 1; i <= 10; i++) {
      expect(screen.getByText(`第 ${i} 集`)).toBeTruthy();
    }
  });

  it("当前集高亮", () => {
    render(React.createElement(EpisodeNav, {
      episodes: [],
      currentEpisode: 5,
      targetCount: 10,
      onSelect,
    }));
    const btn5 = screen.getByText("第 5 集").closest("button");
    expect(btn5?.className).toContain("bg-blue-100");
  });

  it("非当前集不高亮", () => {
    render(React.createElement(EpisodeNav, {
      episodes: [],
      currentEpisode: 3,
      targetCount: 10,
      onSelect,
    }));
    const btn1 = screen.getByText("第 1 集").closest("button");
    expect(btn1?.className).not.toContain("bg-blue-100");
  });

  it("点击集数触发 onSelect", () => {
    render(React.createElement(EpisodeNav, {
      episodes: [],
      currentEpisode: 1,
      targetCount: 10,
      onSelect,
    }));
    fireEvent.click(screen.getByText("第 7 集"));
    expect(onSelect).toHaveBeenCalledWith(7);
  });

  it("已完成剧本且有评估的集显示 ✓", () => {
    const items: EpisodeNavItem[] = [
      { episode_number: 1, title: "开局", hasScript: true, hasEvaluation: true },
    ];
    render(React.createElement(EpisodeNav, {
      episodes: items,
      currentEpisode: 1,
      targetCount: 3,
      onSelect,
    }));
    // 已完成状态有 title="已完成评估"
    const icon = document.querySelector("[title='已完成评估']");
    expect(icon).toBeTruthy();
  });

  it("有剧本无评估的集显示 ●", () => {
    const items: EpisodeNavItem[] = [
      { episode_number: 1, hasScript: true, hasEvaluation: false },
    ];
    render(React.createElement(EpisodeNav, {
      episodes: items,
      currentEpisode: 1,
      targetCount: 3,
      onSelect,
    }));
    const icon = document.querySelector("[title='已完成剧本']");
    expect(icon).toBeTruthy();
  });

  it("无剧本的集显示 ○", () => {
    render(React.createElement(EpisodeNav, {
      episodes: [],
      currentEpisode: 1,
      targetCount: 3,
      onSelect,
    }));
    const icon = document.querySelector("[title='未生成']");
    expect(icon).toBeTruthy();
  });
});

// ============================================================
// ScriptView
// ============================================================

describe("ScriptView", () => {
  it("显示集号和标题", () => {
    render(React.createElement(ScriptView, { content: makeScriptContent({ episode_number: 3, title: "决战时刻" }) }));
    expect(screen.getByText(/第 3 集/)).toBeTruthy();
    expect(screen.getByText(/决战时刻/)).toBeTruthy();
  });

  it("显示开头钩子", () => {
    render(React.createElement(ScriptView, { content: makeScriptContent({ opening_hook: "热血开场" }) }));
    expect(screen.getByText(/热血开场/)).toBeTruthy();
  });

  it("显示结尾钩子", () => {
    render(React.createElement(ScriptView, { content: makeScriptContent({ ending_hook: "悬念结尾" }) }));
    expect(screen.getByText(/悬念结尾/)).toBeTruthy();
  });

  it("渲染所有场景", () => {
    const scenes = [makeScene(1), makeScene(2), makeScene(3)];
    render(React.createElement(ScriptView, { content: makeScriptContent({ scenes }) }));
    expect(screen.getByText("球场1")).toBeTruthy();
    expect(screen.getByText("球场2")).toBeTruthy();
    expect(screen.getByText("球场3")).toBeTruthy();
  });

  it("每个场景有 scene-N 锚点", () => {
    render(React.createElement(ScriptView, { content: makeScriptContent() }));
    expect(document.getElementById("scene-1")).toBeTruthy();
    expect(document.getElementById("scene-2")).toBeTruthy();
  });

  it("显示角色对白（每个场景都出现）", () => {
    render(React.createElement(ScriptView, { content: makeScriptContent() }));
    const lines = screen.getAllByText("我会赢下这场比赛！");
    // 3 个场景各 1 条相同对白
    expect(lines.length).toBeGreaterThanOrEqual(3);
  });

  it("显示角色名标签", () => {
    render(React.createElement(ScriptView, { content: makeScriptContent() }));
    // 每个场景有 2 个角色标签（林风+教练），共 3 个场景
    const linfengTags = screen.getAllByText("林风");
    const coachTags = screen.getAllByText("教练");
    expect(linfengTags.length).toBeGreaterThanOrEqual(3);
    expect(coachTags.length).toBeGreaterThanOrEqual(3);
  });

  it("显示场景动作描述", () => {
    render(React.createElement(ScriptView, { content: makeScriptContent() }));
    // 3 个场景各有一段动作描述，匹配"激烈的训练开始了"
    const actions = screen.getAllByText(/激烈的训练开始了/);
    expect(actions.length).toBeGreaterThanOrEqual(3);
  });

  it("显示字号统计", () => {
    render(React.createElement(ScriptView, { content: makeScriptContent({ word_count: 5000, dialogue_ratio: 0.35 }) }));
    expect(screen.getByText("5,000 字")).toBeTruthy();
    expect(screen.getByText(/对白占比 35%/)).toBeTruthy();
  });

  it("高亮指定场景（highlightedScenes）", () => {
    render(React.createElement(ScriptView, {
      content: makeScriptContent(),
      highlightedScenes: [2],
    }));
    const scene2 = document.getElementById("scene-2");
    expect(scene2?.className).toContain("border-orange-300");
  });

  it("空场景列表显示占位", () => {
    render(React.createElement(ScriptView, { content: makeScriptContent({ scenes: [] }) }));
    expect(screen.getByText("暂无场景内容")).toBeTruthy();
  });

  it("缺失标题显示占位", () => {
    render(React.createElement(ScriptView, { content: makeScriptContent({ title: "" }) }));
    // 标题为 "第 1 集 · 未命名"，使用 heading role 匹配
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading.textContent).toContain("未命名");
  });
});

// ============================================================
// ScoreBar
// ============================================================

describe("ScoreBar", () => {
  it("显示维度中文标签和分数", () => {
    render(React.createElement(ScoreBar, { dimension: "opening_hook", score: 85 }));
    expect(screen.getByText("开头钩子")).toBeTruthy();
    expect(screen.getByText("85")).toBeTruthy();
  });

  it("≥80 分显示绿色", () => {
    render(React.createElement(ScoreBar, { dimension: "main_clarity", score: 88 }));
    const scoreEl = screen.getByText("88");
    expect(scoreEl.className).toContain("text-green-700");
  });

  it("60-79 分显示黄色", () => {
    render(React.createElement(ScoreBar, { dimension: "pacing", score: 65 }));
    const scoreEl = screen.getByText("65");
    expect(scoreEl.className).toContain("text-yellow-700");
  });

  it("<60 分显示红色", () => {
    render(React.createElement(ScoreBar, { dimension: "conflict_intensity", score: 42 }));
    const scoreEl = screen.getByText("42");
    expect(scoreEl.className).toContain("text-red-700");
  });
});

// ============================================================
// IssueCard
// ============================================================

describe("IssueCard", () => {
  it("显示维度标签和严重程度", () => {
    render(React.createElement(IssueCard, {
      issue: makeIssue({ dimension: "opening_hook", severity: "medium" }),
    }));
    expect(screen.getByText("开头钩子")).toBeTruthy();
    expect(screen.getByText("中等")).toBeTruthy();
  });

  it("显示诊断和建议", () => {
    render(React.createElement(IssueCard, {
      issue: makeIssue({ diagnosis: "问题诊断", suggestion: "改进建议" }),
    }));
    expect(screen.getByText("问题诊断")).toBeTruthy();
    expect(screen.getByText(/改进建议/)).toBeTruthy();
  });

  it("显示证据引用", () => {
    render(React.createElement(IssueCard, {
      issue: makeIssue({ evidence: "来自剧本的证据" }),
    }));
    expect(screen.getByText("来自剧本的证据")).toBeTruthy();
  });

  it("有 scene_number 时显示定位按钮", () => {
    render(React.createElement(IssueCard, {
      issue: makeIssue({ scene_number: 3 }),
    }));
    expect(screen.getByText(/定位到第 3 场/)).toBeTruthy();
  });

  it("scene_number 为 null 时显示全局问题", () => {
    render(React.createElement(IssueCard, {
      issue: makeIssue({ scene_number: null }),
    }));
    expect(screen.getByText("全局问题")).toBeTruthy();
  });

  it("点击定位按钮触发 onLocateScene", () => {
    const onLocate = vi.fn();
    render(React.createElement(IssueCard, {
      issue: makeIssue({ scene_number: 2 }),
      onLocateScene: onLocate,
    }));
    fireEvent.click(screen.getByText(/定位到第 2 场/));
    expect(onLocate).toHaveBeenCalledWith(2);
  });

  it("high 严重程度显示'严重'标签", () => {
    render(React.createElement(IssueCard, {
      issue: makeIssue({ severity: "high" }),
    }));
    expect(screen.getByText("严重")).toBeTruthy();
  });

  it("low 严重程度显示'轻微'标签", () => {
    render(React.createElement(IssueCard, {
      issue: makeIssue({ severity: "low" }),
    }));
    expect(screen.getByText("轻微")).toBeTruthy();
  });
});

// ============================================================
// EvaluationPanel — 四态覆盖
// ============================================================

describe("EvaluationPanel", () => {
  const nop = vi.fn();

  // ---- 加载中 ----
  it("加载中状态显示旋转器", () => {
    render(React.createElement(EvaluationPanel, {
      report: null, isLoading: true, isError: false, onReEvaluate: nop,
    }));
    expect(screen.getByText("加载评估报告…")).toBeTruthy();
  });

  // ---- 评估中 ----
  it("评估中状态显示提示", () => {
    render(React.createElement(EvaluationPanel, {
      report: null, isLoading: false, isError: false, isEvaluating: true, onReEvaluate: nop,
    }));
    expect(screen.getByText("评估进行中…")).toBeTruthy();
  });

  // ---- 错误 ----
  it("错误状态显示提示和重试按钮", () => {
    const onRetry = vi.fn();
    render(React.createElement(EvaluationPanel, {
      report: null, isLoading: false, isError: true, onReEvaluate: nop, onRetry,
    }));
    expect(screen.getByText("评估报告加载失败")).toBeTruthy();
    expect(screen.getByText("重试")).toBeTruthy();
  });

  it("点击重试触发 onRetry", () => {
    const onRetry = vi.fn();
    render(React.createElement(EvaluationPanel, {
      report: null, isLoading: false, isError: true, onReEvaluate: nop, onRetry,
    }));
    fireEvent.click(screen.getByText("重试"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  // ---- 无报告 ----
  it("无报告状态显示发起评估按钮", () => {
    render(React.createElement(EvaluationPanel, {
      report: null, isLoading: false, isError: false, onReEvaluate: nop,
    }));
    expect(screen.getByText("暂无评估报告")).toBeTruthy();
    expect(screen.getByText("发起评估")).toBeTruthy();
  });

  // ---- 有报告 ----

  function renderPanel(report?: Partial<EvaluationReportContent>) {
    const r = makeEvaluationReport(report || {});
    return render(React.createElement(EvaluationPanel, {
      report: r, isLoading: false, isError: false, onReEvaluate: nop,
    }));
  }

  it("显示总评分", () => {
    renderPanel({ overall_score: 77.5, dimension_scores: {
      opening_hook: 70, main_clarity: 70, character_appeal: 70,
      conflict_intensity: 70, payoff_density: 70, ending_hook: 70,
      pacing: 70, visualizability: 70, compliance_safety: 70,
    }});
    // 77.5 四舍五入为 78，所有维度分数均为 70（不与总分冲突）
    expect(screen.getByText("78")).toBeTruthy();
    expect(screen.getByText("总评分")).toBeTruthy();
  });

  it("显示所有 9 个维度评分", () => {
    renderPanel();
    // 维度标签在 ScoreBar + IssueCard 中可能出现多次，只要至少出现 1 次即通过
    const dims = ["开头钩子", "主线清晰度", "角色吸引力", "冲突强度", "爽点密度", "结尾钩子", "节奏控制", "可视化程度", "合规安全"];
    for (const dim of dims) {
      const els = screen.getAllByText(dim);
      expect(els.length).toBeGreaterThanOrEqual(1);
    }
  });

  it("显示亮点 (strengths)", () => {
    renderPanel({ strengths: ["亮点A", "亮点B"] });
    expect(screen.getByText(/亮点A/)).toBeTruthy();
    expect(screen.getByText(/亮点B/)).toBeTruthy();
  });

  it("空亮点显示占位", () => {
    renderPanel({ strengths: [] });
    expect(screen.getByText("暂无亮点")).toBeTruthy();
  });

  it("显示问题列表", () => {
    renderPanel();
    // 问题中包含"钩子缺乏悬念"
    expect(screen.getByText("钩子缺乏悬念")).toBeTruthy();
  });

  it("问题总数正确", () => {
    renderPanel();
    expect(screen.getByText(/问题 \(3\)/)).toBeTruthy();
  });

  it("显示修订建议", () => {
    renderPanel({ revision_suggestions: ["建议A", "建议B"] });
    expect(screen.getByText(/建议A/)).toBeTruthy();
    expect(screen.getByText(/建议B/)).toBeTruthy();
  });

  it("need_revision 为 true 时显示需修订标签", () => {
    renderPanel({ need_revision: true });
    expect(screen.getByText("⚠️ 需修订")).toBeTruthy();
  });

  it("need_revision 为 false 时不显示需修订标签", () => {
    renderPanel({ need_revision: false });
    expect(screen.queryByText("⚠️ 需修订")).toBeNull();
  });

  // ---- risk_flags 明显展示 ----
  it("risk_flags 明显展示（红色边框区域）", () => {
    renderPanel({ risk_flags: ["合规风险：暴力描写", "内容安全：疑似违规"] });
    expect(screen.getByText("🚨 风险标记")).toBeTruthy();
    expect(screen.getByText(/合规风险：暴力描写/)).toBeTruthy();
    expect(screen.getByText(/内容安全：疑似违规/)).toBeTruthy();
    // 边框为红色 red-300
    const riskBox = document.querySelector(".border-red-300");
    expect(riskBox).toBeTruthy();
  });

  it("空 risk_flags 不显示风险区块", () => {
    renderPanel({ risk_flags: [] });
    expect(screen.queryByText("🚨 风险标记")).toBeNull();
  });

  // ---- 重新评估按钮 ----
  it("有报告时显示重新评估按钮", () => {
    renderPanel();
    expect(screen.getByText("🔄 重新评估")).toBeTruthy();
  });

  it("点击重新评估触发 onReEvaluate", () => {
    const onReEval = vi.fn();
    const r = makeEvaluationReport();
    render(React.createElement(EvaluationPanel, {
      report: r, isLoading: false, isError: false, onReEvaluate: onReEval,
    }));
    fireEvent.click(screen.getByText("🔄 重新评估"));
    expect(onReEval).toHaveBeenCalledTimes(1);
  });

  // ---- 版本绑定 ----
  it("显示 rubric_version", () => {
    renderPanel({ rubric_version: "2.0.0" });
    // rubric_version 渲染为 "版本 2.0.0"，使用 regex 匹配
    expect(screen.getByText(/2\.0\.0/)).toBeTruthy();
  });

  // ---- Issue → Scene 定位 ----
  it("issue 点击触发 onLocateScene", () => {
    const onLocate = vi.fn();
    const r = makeEvaluationReport({
      issues: [makeIssue({ issue_id: "iss-1", scene_number: 1, diagnosis: "定位测试问题" })],
    });
    render(React.createElement(EvaluationPanel, {
      report: r, isLoading: false, isError: false, onReEvaluate: nop, onLocateScene: onLocate,
    }));
    fireEvent.click(screen.getByText(/定位到第 1 场/));
    expect(onLocate).toHaveBeenCalledWith(1);
  });
});
