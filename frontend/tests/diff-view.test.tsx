/** Diff 视图测试 (H-06).
 *
 * 测试 DiffView：
 * - scene 模式：change_ratio / stats / scene_summary 渲染
 * - 新增（绿）/ 删除（红）/ 修改（琥珀）徽章与行渲染
 * - 大 diff 防卡死：场景 >20 默认折叠，行明细惰性渲染（展开后才出现）
 * - 截断分级：diff.truncated 全局 / line_changes_truncated 单场景
 * - mode=line 回退渲染顶层行，不显示误导的场景摘要
 * - 空 diff / 缺失文本占位不崩
 */

import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import React from "react";

import { DiffView } from "@/features/diff/DiffView";
import type { LineChange, SceneChange, ScriptDiff } from "@/types/api";

// ============================================================
// 工厂函数
// ============================================================

function makeLineChange(overrides: Partial<LineChange> = {}): LineChange {
  return {
    change_type: "modified",
    old_line_number: 1,
    new_line_number: 1,
    old_text: "旧行内容",
    new_text: "新行内容",
    ...overrides,
  };
}

function makeSceneChange(overrides: Partial<SceneChange> = {}): SceneChange {
  return {
    change_type: "modified",
    old_scene_number: 1,
    new_scene_number: 1,
    location: "球场",
    time_of_day: "白天",
    similarity: 0.8,
    added_lines: 0,
    removed_lines: 0,
    modified_lines: 1,
    added_chars: 0,
    removed_chars: 0,
    line_changes: [makeLineChange()],
    line_changes_truncated: false,
    ...overrides,
  };
}

function makeScriptDiff(overrides: Partial<ScriptDiff> = {}): ScriptDiff {
  return {
    mode: "scene",
    from_artifact_id: "script-a",
    to_artifact_id: "script-b",
    from_version: 1,
    to_version: 2,
    project_id: "proj-1",
    episode_number: 1,
    change_ratio: 0.2,
    scene_summary: {
      from_scene_count: 2,
      to_scene_count: 2,
      added: 0,
      removed: 0,
      modified: 1,
      unchanged: 1,
    },
    stats: {
      added_lines: 1,
      removed_lines: 0,
      modified_lines: 1,
      added_chars: 10,
      removed_chars: 0,
      changed_chars: 10,
      from_chars: 100,
      to_chars: 110,
    },
    scene_changes: [makeSceneChange()],
    line_changes: [],
    truncated: false,
    ...overrides,
  };
}

// ============================================================
// DiffView
// ============================================================

describe("DiffView", () => {
  it("渲染 change_ratio 与统计（新增/删除/修改行数）", () => {
    render(React.createElement(DiffView, {
      diff: makeScriptDiff(),
    }));
    expect(screen.getByText(/变更比例/)).toBeTruthy();
    expect(screen.getByText("20.0%")).toBeTruthy();
    expect(screen.getByText(/新增 1 行 · 删除 0 行 · 修改 1 行/)).toBeTruthy();
  });

  it("scene 模式渲染场景级摘要计数", () => {
    render(React.createElement(DiffView, {
      diff: makeScriptDiff(),
    }));
    expect(screen.getByText("新增 0")).toBeTruthy();
    expect(screen.getByText("删除 0")).toBeTruthy();
    expect(screen.getByText("修改 1")).toBeTruthy();
    expect(screen.getByText("未变 1")).toBeTruthy();
  });

  it("新增场景渲染绿色「新增」徽章与地点", () => {
    const diff = makeScriptDiff({
      scene_changes: [
        makeSceneChange({
          change_type: "added",
          old_scene_number: null,
          new_scene_number: 4,
          location: "训练场",
          line_changes: [],
        }),
      ],
    });
    render(React.createElement(DiffView, { diff }));
    expect(screen.getByText("新增")).toBeTruthy();
    expect(screen.getByText(/训练场/)).toBeTruthy();
  });

  it("删除场景渲染红色「删除」徽章", () => {
    const diff = makeScriptDiff({
      scene_changes: [
        makeSceneChange({
          change_type: "removed",
          old_scene_number: 2,
          new_scene_number: null,
          line_changes: [],
        }),
      ],
    });
    render(React.createElement(DiffView, { diff }));
    expect(screen.getByText("删除")).toBeTruthy();
  });

  it("modified 场景展开后渲染旧行（删除线）与新行", () => {
    const diff = makeScriptDiff({
      scene_changes: [makeSceneChange({
        line_changes: [
          makeLineChange({ change_type: "modified", old_text: "旧的冲突对白", new_text: "新的冲突对白" }),
        ],
      })],
    });
    render(React.createElement(DiffView, { diff }));
    // 默认展开（场景数 <= 20），行明细可见
    expect(screen.getByText("旧的冲突对白")).toBeTruthy();
    expect(screen.getByText("新的冲突对白")).toBeTruthy();
  });

  it("added / removed 行正确渲染（行号与文本）", () => {
    const diff = makeScriptDiff({
      scene_changes: [
        makeSceneChange({
          line_changes: [
            makeLineChange({
              change_type: "added",
              old_line_number: null,
              new_line_number: 5,
              old_text: null,
              new_text: "新增的一句台词",
            }),
            makeLineChange({
              change_type: "removed",
              old_line_number: 3,
              new_line_number: null,
              old_text: "被删掉的对白",
              new_text: null,
            }),
          ],
        }),
      ],
    });
    render(React.createElement(DiffView, { diff }));
    expect(screen.getByText("新增的一句台词")).toBeTruthy();
    expect(screen.getByText("被删掉的对白")).toBeTruthy();
  });

  it("大 diff 防卡死：21 个场景默认折叠，行明细不渲染，点击展开后才出现", () => {
    const scenes = Array.from({ length: 21 }, (_, i) =>
      makeSceneChange({
        old_scene_number: i + 1,
        new_scene_number: i + 1,
        location: `场景${i + 1}`,
        line_changes: [
          makeLineChange({ new_text: `第${i + 1}场的行内容` }),
        ],
      }),
    );
    const diff = makeScriptDiff({ scene_changes: scenes });
    render(React.createElement(DiffView, { diff }));

    // 默认折叠 → 行明细不渲染（防卡死核心断言）
    expect(screen.queryByText("第21场的行内容")).toBeNull();

    // 点击某个场景的 summary → 该场景行明细出现
    fireEvent.click(screen.getByText("第 21 场"));
    expect(screen.getByText("第21场的行内容")).toBeTruthy();
  });

  it("单场景行明细截断时显示提示", () => {
    const diff = makeScriptDiff({
      scene_changes: [
        makeSceneChange({ line_changes_truncated: true, line_changes: [] }),
      ],
    });
    render(React.createElement(DiffView, { diff }));
    expect(screen.getByText(/该场景行明细已截断/)).toBeTruthy();
  });

  it("diff.truncated 显示全局截断提示", () => {
    const diff = makeScriptDiff({
      truncated: true,
      scene_changes: [makeSceneChange({ line_changes: [] })],
    });
    render(React.createElement(DiffView, { diff }));
    expect(screen.getByText(/变更行过多（超过 2000 行）/)).toBeTruthy();
  });

  it("mode=line 回退渲染顶层行，且不显示误导的场景摘要", () => {
    const diff = makeScriptDiff({
      mode: "line",
      scene_changes: [],
      line_changes: [
        makeLineChange({ change_type: "added", new_line_number: 1, old_text: null, new_text: "全文对比行" }),
      ],
    });
    render(React.createElement(DiffView, { diff }));
    expect(screen.getByText(/已回退为全文行对比/)).toBeTruthy();
    expect(screen.getByText("全文对比行")).toBeTruthy();
    // scene 摘要徽章（新增/删除计数）不应出现在 line 模式
    expect(screen.queryByText(/场景 2 → 2/)).toBeNull();
  });

  it("空 diff 显示「两个版本无差异」占位", () => {
    const diff = makeScriptDiff({
      scene_changes: [],
      line_changes: [],
      truncated: false,
    });
    render(React.createElement(DiffView, { diff }));
    expect(screen.getByText("两个版本无差异")).toBeTruthy();
  });

  it("缺失文本的行显示「（空）」占位不崩溃", () => {
    const diff = makeScriptDiff({
      scene_changes: [
        makeSceneChange({
          line_changes: [
            makeLineChange({ change_type: "removed", old_text: null, new_text: null }),
          ],
        }),
      ],
    });
    render(React.createElement(DiffView, { diff }));
    expect(screen.getAllByText("（空）").length).toBeGreaterThan(0);
  });
});
