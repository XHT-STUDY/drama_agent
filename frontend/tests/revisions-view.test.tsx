/** 修订视图测试 (H-06).
 *
 * 测试：
 * - ScoreComparison 评分对比：下降红（绝不包装成提升）/ 上升绿 / 持平灰；scoreDelta 纯函数
 * - ContinuityCheckView 连续性检查：pass 绿 / fail 红 + kind 中文标签 + source 徽章 + warnings 琥珀
 * - RevisionPlanView 修订计划：集数 / 最大变更比例 / 用户补充要求 / 锁定事实 / 修订操作
 * - RevisionPlanList 列表：选中高亮 + onSelect 触发
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import React from "react";

import { ScoreComparison, scoreDelta } from "@/features/revisions/ScoreComparison";
import { ContinuityCheckView } from "@/features/revisions/ContinuityCheckView";
import { RevisionPlanView } from "@/features/revisions/RevisionPlanView";
import { RevisionPlanList } from "@/features/revisions/RevisionPlanList";
import type {
  Artifact,
  ContinuityCheckContent,
  ContinuityViolation,
  EvaluationDimension,
  EvaluationReportContent,
  RevisionOperation,
  RevisionPlanContent,
} from "@/types/api";

// ============================================================
// 工厂函数
// ============================================================

const ALL_DIMENSIONS: EvaluationDimension[] = [
  "opening_hook",
  "main_clarity",
  "character_appeal",
  "conflict_intensity",
  "payoff_density",
  "ending_hook",
  "pacing",
  "visualizability",
  "compliance_safety",
];

/** 构造维度分数（各维取同一值，便于测试） */
function makeDimScores(value: number): Record<EvaluationDimension, number> {
  const scores = {} as Record<EvaluationDimension, number>;
  for (const d of ALL_DIMENSIONS) {
    scores[d] = value;
  }
  return scores;
}

function makeEvaluationReport(
  overrides: Partial<EvaluationReportContent> = {},
  dimValue = 70,
): EvaluationReportContent {
  return {
    episode_number: 1,
    script_artifact_id: "script-1",
    rubric_version: "rubric-v1",
    dimension_scores: makeDimScores(dimValue),
    overall_score: dimValue,
    strengths: ["优点A"],
    issues: [],
    revision_suggestions: ["建议A"],
    need_revision: false,
    risk_flags: [],
    ...overrides,
  };
}

function makeContinuityViolation(overrides: Partial<ContinuityViolation> = {}): ContinuityViolation {
  return {
    kind: "locked_fact_reversed",
    target: "主角身世",
    expected: "从未透露",
    actual: "改为孤儿",
    evidence: "第 2 场对白改写",
    source: "semantic",
    ...overrides,
  };
}

function makeContinuityCheck(overrides: Partial<ContinuityCheckContent> = {}): ContinuityCheckContent {
  return {
    status: "fail",
    checked_episode_number: 1,
    violations: [makeContinuityViolation()],
    warnings: [],
    rule_checks_run: ["rule-1"],
    semantic_checks_run: ["semantic-1", "semantic-2"],
    ...overrides,
  };
}

function makeRevisionOperation(overrides: Partial<RevisionOperation> = {}): RevisionOperation {
  return {
    operation_id: "op-1",
    target_scene_number: 1,
    issue_ids: ["issue-1"],
    instruction: "示例修订指令",
    preserve: ["保持人物名"],
    expected_effect: "预期效果",
    ...overrides,
  };
}

function makeRevisionPlan(overrides: Partial<RevisionPlanContent> = {}): RevisionPlanContent {
  return {
    episode_number: 1,
    source_script_artifact_id: "script-1",
    source_evaluation_artifact_id: "eval-1",
    operations: [makeRevisionOperation()],
    locked_facts: [],
    max_change_ratio: 0.2,
    user_instruction: null,
    ...overrides,
  };
}

function makeRevisionArtifact(
  id: string,
  episode: number,
  version: number,
  operationCount = 1,
  overrides: Partial<Artifact> = {},
): Artifact {
  return {
    id,
    project_id: "proj-1",
    type: "revision_plan",
    version,
    episode_number: episode,
    status: "valid",
    content: {
      episode_number: episode,
      source_script_artifact_id: "script-1",
      source_evaluation_artifact_id: "eval-1",
      operations: Array.from({ length: operationCount }, (_, i) =>
        makeRevisionOperation({ operation_id: `op-${i + 1}` }),
      ),
      locked_facts: [],
      max_change_ratio: 0.2,
      user_instruction: null,
    },
    content_schema_version: "1.0",
    prompt_version: "p1",
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    ...overrides,
  };
}

// ============================================================
// ScoreComparison
// ============================================================

describe("ScoreComparison", () => {
  it("scoreDelta 纯函数：修订分减原稿分", () => {
    expect(scoreDelta(80, 72)).toBe(-8);
    expect(scoreDelta(72, 80)).toBe(8);
    expect(scoreDelta(80, 80)).toBe(0);
  });

  it("分数下降 → 红色「↓ 下降 -X 分」且绝不出现「提升」", () => {
    const source = makeEvaluationReport({ overall_score: 80 }, 80);
    const revised = makeEvaluationReport({ overall_score: 72 }, 72);
    render(React.createElement(ScoreComparison, { source, revised }));
    expect(screen.getByText("↓ 下降 8 分")).toBeTruthy();
    expect(screen.queryByText(/提升/)).toBeNull();
    expect(screen.getByText("原稿评分")).toBeTruthy();
    expect(screen.getByText("修订稿评分")).toBeTruthy();
  });

  it("分数上升 → 绿色「↑ 提升 +X 分」", () => {
    const source = makeEvaluationReport({ overall_score: 70 }, 70);
    const revised = makeEvaluationReport({ overall_score: 78 }, 78);
    render(React.createElement(ScoreComparison, { source, revised }));
    expect(screen.getByText("↑ 提升 +8 分")).toBeTruthy();
  });
});

// ============================================================
// ContinuityCheckView
// ============================================================

describe("ContinuityCheckView", () => {
  it("pass → 绿色通过横幅 + 已执行检查计数", () => {
    render(React.createElement(ContinuityCheckView, {
      result: makeContinuityCheck({
        status: "pass",
        violations: [],
        warnings: [],
        rule_checks_run: ["rule-1"],
        semantic_checks_run: ["semantic-1", "semantic-2"],
      }),
    }));
    expect(screen.getByText("✅ 连续性检查通过")).toBeTruthy();
    expect(screen.getByText("已执行规则检查 1 项 · 语义检查 2 项")).toBeTruthy();
    expect(screen.queryByText("违规明细")).toBeNull();
  });

  it("fail → 红色横幅 + 违规明细（中文标签 / source 徽章 / 目标/期望/实际 / 证据）", () => {
    render(React.createElement(ContinuityCheckView, { result: makeContinuityCheck() }));
    expect(screen.getByText("❌ 连续性检查失败，需人工复核")).toBeTruthy();
    expect(screen.getByText("违规明细")).toBeTruthy();
    // kind → 中文标签
    expect(screen.getByText("锁定事实被反转")).toBeTruthy();
    // source 徽章
    expect(screen.getByText("语义检查")).toBeTruthy();
    // 目标 / 期望 / 实际（标签为独立 span，断言值文本即可）
    expect(screen.getByText("主角身世")).toBeTruthy();
    expect(screen.getByText("从未透露")).toBeTruthy();
    expect(screen.getByText("改为孤儿")).toBeTruthy();
    // 证据引用
    expect(screen.getByText("第 2 场对白改写")).toBeTruthy();
  });

  it("warnings 以琥珀块渲染 kind 中文标签与 message", () => {
    render(React.createElement(ContinuityCheckView, {
      result: makeContinuityCheck({
        status: "pass",
        violations: [],
        warnings: [
          { kind: "loop_inconsistent", target: "伏笔A", message: "状态前后矛盾", source: "rule" },
        ],
      }),
    }));
    expect(screen.getByText("警告")).toBeTruthy();
    // label（kind 中文标签）与 message 为独立文本节点，分开断言
    expect(screen.getByText(/伏笔状态不一致/)).toBeTruthy();
    expect(screen.getByText("状态前后矛盾")).toBeTruthy();
  });
});

// ============================================================
// RevisionPlanView
// ============================================================

describe("RevisionPlanView", () => {
  it("渲染集数 / 最大变更比例 / 用户补充要求 / 锁定事实", () => {
    render(React.createElement(RevisionPlanView, {
      plan: makeRevisionPlan({
        episode_number: 2,
        max_change_ratio: 0.2,
        user_instruction: "加强反派动机，但不得改变主角身世",
        locked_facts: ["主角身世不得改变", "反派必须活到大结局"],
      }),
    }));
    expect(screen.getByText("第 2 集修订计划")).toBeTruthy();
    expect(screen.getByText("最大变更比例 20%")).toBeTruthy();
    expect(screen.getByText("📝 用户补充要求")).toBeTruthy();
    expect(screen.getByText("加强反派动机，但不得改变主角身世")).toBeTruthy();
    expect(screen.getByText("🔒 锁定事实（修订不得违反）")).toBeTruthy();
    expect(screen.getByText("· 主角身世不得改变")).toBeTruthy();
    expect(screen.getByText("· 反派必须活到大结局")).toBeTruthy();
  });

  it("渲染修订操作：目标场景 / issue 依据 / 指令 / 必须保留 / 预期效果", () => {
    render(React.createElement(RevisionPlanView, {
      plan: makeRevisionPlan({
        operations: [
          makeRevisionOperation({
            operation_id: "op-3",
            target_scene_number: 3,
            issue_ids: ["issue-1", "issue-2"],
            instruction: "重写反派动机独白",
            preserve: ["保持人物名", "保留时间线"],
            expected_effect: "反派动机更清晰",
          }),
        ],
      }),
    }));
    expect(screen.getByText("修订操作")).toBeTruthy();
    expect(screen.getByText("op-3")).toBeTruthy();
    expect(screen.getByText("第 3 场")).toBeTruthy();
    expect(screen.getByText("#issue-1")).toBeTruthy();
    expect(screen.getByText("#issue-2")).toBeTruthy();
    expect(screen.getByText("重写反派动机独白")).toBeTruthy();
    expect(screen.getByText("必须保留：保持人物名；保留时间线")).toBeTruthy();
    expect(screen.getByText("预期效果：反派动机更清晰")).toBeTruthy();
  });

  it("无修订操作 → 「无具体修订操作」", () => {
    render(React.createElement(RevisionPlanView, {
      plan: makeRevisionPlan({ operations: [] }),
    }));
    expect(screen.getByText("无具体修订操作")).toBeTruthy();
  });
});

// ============================================================
// RevisionPlanList
// ============================================================

describe("RevisionPlanList", () => {
  const items = [
    makeRevisionArtifact("plan-1", 1, 1, 2),
    makeRevisionArtifact("plan-2", 2, 1, 3),
  ];

  it("渲染列表卡片并高亮选中项（已选中标记）", () => {
    render(React.createElement(RevisionPlanList, {
      items,
      selectedId: "plan-1",
      onSelect: () => {},
    }));
    expect(screen.getByText("第 1 集 · 修订计划 v1")).toBeTruthy();
    expect(screen.getByText("2 条修订操作")).toBeTruthy();
    expect(screen.getByText("第 2 集 · 修订计划 v1")).toBeTruthy();
    // 只有选中项显示「已选中」
    expect(screen.getByText("已选中")).toBeTruthy();
    expect(screen.getAllByText("已选中").length).toBe(1);
  });

  it("点击卡片触发 onSelect 并传入对应 id", () => {
    const onSelect = vi.fn();
    render(React.createElement(RevisionPlanList, { items, selectedId: null, onSelect }));
    fireEvent.click(screen.getByText("第 2 集 · 修订计划 v1"));
    expect(onSelect).toHaveBeenCalledWith("plan-2");
  });
});
