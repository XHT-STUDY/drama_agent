/** 导出中心测试 (H-07).
 *
 * 覆盖：
 * - 序列化纯函数：StoryBible / 大纲 / 剧本 / 评估 / 修订说明 → Markdown
 *   （中文标题稳定、不输出内部 UUID / schema_version 等内部字段）
 * - buildExportMarkdown 按选中的内容类型拼接
 * - buildExportFilename / formatTimestamp 文件名与时间戳
 * - serializeExport：markdown 与 docx（docx 库 mock）两种格式
 * - ExportSection：选择内容与格式 → 生成并下载触发 downloadBlob + onExported
 * - ExportHistory：历史展示 / 重新下载 / 清空 / 空态
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import React from "react";

import {
  buildExportFilename,
  buildExportMarkdown,
  formatTimestamp,
  markdownFromEvaluation,
  markdownFromOutline,
  markdownFromRevision,
  markdownFromScript,
  markdownFromStoryBible,
  serializeExport,
  type ExportData,
} from "@/lib/export";
import { ExportSection } from "@/features/exports/ExportSection";
import { ExportHistory } from "@/features/exports/ExportHistory";
import type {
  EpisodeOutlineSetContent,
  EvaluationReportContent,
  ExportRecord,
  RevisionPlanContent,
  ScriptDiff,
  ScriptDraftContent,
  StoryBibleContent,
} from "@/types/api";

// ============================================================
// docx 库 mock（运行时动态加载，测试中截获）
// ============================================================

vi.mock("docx", () => ({
  Document: class {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    constructor(public opts: any) {}
  },
  Packer: {
    toBlob: vi.fn(async () =>
      new Blob(["docx-content"], {
        type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      }),
    ),
  },
  Paragraph: class {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    constructor(public opts: any) {}
  },
  TextRun: class {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    constructor(public opts: any) {}
  },
  HeadingLevel: { HEADING_1: "Heading1", HEADING_2: "Heading2", HEADING_3: "Heading3" },
}));

// ============================================================
// 工厂函数
// ============================================================

function makeStoryBible(overrides: Partial<StoryBibleContent> = {}): StoryBibleContent {
  return {
    title: "足球少年逆袭",
    logline: "被青训队抛弃的少年重新证明自己",
    genre: "体育青春",
    tone: ["热血", "励志"],
    world_setting: "现代都市职业足球青训体系",
    protagonist: {
      character_id: "char-001",
      name: "林峰",
      role: "主角",
      age_range: "17",
      visible_goal: "进入一线队",
      hidden_need: "被认可",
      traits: ["倔强", "勤奋"],
      strengths: ["爆发力"],
      flaws: ["冲动"],
      relationship_notes: ["与教练亦敌亦友"],
      forbidden_changes: ["不得改变家庭背景"],
    },
    antagonist: {
      character_id: "char-002",
      name: "赵启",
      role: "反派",
      age_range: "19",
      visible_goal: "保住主力位置",
      traits: ["傲慢"],
      strengths: ["技术全面"],
      flaws: ["轻视对手"],
      relationship_notes: [],
      forbidden_changes: [],
    },
    supporting_characters: [
      {
        character_id: "char-003",
        name: "苏瑶",
        role: "青训队医",
        visible_goal: "帮助林峰恢复",
        traits: ["细心"],
        strengths: ["专业"],
        flaws: ["过度保护"],
        relationship_notes: [],
        forbidden_changes: [],
      },
    ],
    main_conflict: "林峰与赵启争夺一线队名额",
    stakes: "职业足球生涯",
    story_rules: ["不得出现超现实元素"],
    long_term_payoffs: ["决赛致胜进球"],
    open_loops: ["父亲为何反对踢球"],
    locked_facts: ["林峰家境贫寒"],
    compliance_notes: ["无暴力血腥描写"],
    ...overrides,
  };
}

function makeOutline(overrides: Partial<EpisodeOutlineSetContent> = {}): EpisodeOutlineSetContent {
  return {
    episodes: [
      {
        episode_number: 1,
        title: "被淘汰",
        opening_hook: "青训淘汰名单公布",
        objective: "确立主角困境",
        core_conflict: "林峰被告知淘汰",
        key_events: ["公布名单", "林峰顶撞教练"],
        payoff: "转折伏笔",
        ending_hook: "神秘人出现",
        next_bridge: "离开青训基地",
        introduced_loops: ["神秘人身份"],
        resolved_loops: [],
        required_characters: ["林峰", "赵启"],
      },
      {
        episode_number: 2,
        title: "重返球场",
        opening_hook: "街头球赛邀请",
        objective: "主角重燃斗志",
        core_conflict: "是否重返足球",
        key_events: ["接受邀请"],
        payoff: "重获信心",
        ending_hook: "接到试训电话",
        next_bridge: "重返青训",
        introduced_loops: [],
        resolved_loops: ["神秘人身份"],
        required_characters: ["林峰", "苏瑶"],
      },
    ],
    arc_summary: "少年被抛弃后重返赛场的热血弧线",
    validation_notes: ["钩子完整"],
    ...overrides,
  };
}

function makeScript(episode = 1, overrides: Partial<ScriptDraftContent> = {}): ScriptDraftContent {
  return {
    episode_number: episode,
    title: `第${episode}集`,
    opening_hook: "开场冲突",
    scenes: [
      {
        scene_number: 1,
        location: "训练场",
        time_of_day: "白天",
        characters: ["林峰", "赵启"],
        action: "两人对峙",
        dialogue: [
          { speaker: "赵启", text: "你不配站在这里。" },
          { speaker: "林峰", text: "我会证明给你看。", parenthetical: "握紧拳头" },
        ],
      },
    ],
    ending_hook: "悬念结尾",
    plain_text: "全文",
    word_count: 120,
    dialogue_ratio: 0.5,
    referenced_outline_artifact_id: "outline-abc",
    ...overrides,
  };
}

function makeEvaluation(episode = 1, overrides: Partial<EvaluationReportContent> = {}): EvaluationReportContent {
  return {
    episode_number: episode,
    script_artifact_id: "script-uuid",
    rubric_version: "1.0.0",
    dimension_scores: {
      opening_hook: 82,
      main_clarity: 75,
      character_appeal: 80,
      conflict_intensity: 68,
      payoff_density: 72,
      ending_hook: 85,
      pacing: 78,
      visualizability: 70,
      compliance_safety: 90,
    },
    overall_score: 77.6,
    strengths: ["开头钩子有力"],
    issues: [
      {
        issue_id: "iss_001",
        dimension: "conflict_intensity",
        severity: "medium",
        scene_number: 1,
        evidence: "冲突偏弱",
        diagnosis: "缺少尖锐对抗",
        suggestion: "增加对立配角",
      },
    ],
    revision_suggestions: ["加强冲突张力"],
    need_revision: true,
    risk_flags: ["结尾仓促"],
    ...overrides,
  };
}

function makeRevision(episode = 1, overrides: Partial<RevisionPlanContent> = {}): RevisionPlanContent {
  return {
    episode_number: episode,
    source_script_artifact_id: "script-a",
    source_evaluation_artifact_id: "eval-a",
    operations: [
      {
        operation_id: "op-1",
        target_scene_number: 1,
        issue_ids: ["iss_001"],
        instruction: "重写冲突对白",
        preserve: ["保持人物名"],
        expected_effect: "冲突更尖锐",
      },
    ],
    locked_facts: ["林峰家境贫寒"],
    max_change_ratio: 0.2,
    user_instruction: "加强反派动机",
    ...overrides,
  };
}

function makeDiff(): ScriptDiff {
  return {
    mode: "scene",
    from_artifact_id: "a",
    to_artifact_id: "b",
    from_version: 1,
    to_version: 2,
    project_id: "p",
    episode_number: 1,
    change_ratio: 0.2,
    scene_summary: { from_scene_count: 2, to_scene_count: 2, added: 0, removed: 0, modified: 1, unchanged: 1 },
    stats: {
      added_lines: 1, removed_lines: 0, modified_lines: 1,
      added_chars: 10, removed_chars: 0, changed_chars: 10, from_chars: 100, to_chars: 110,
    },
    scene_changes: [],
    line_changes: [],
    truncated: false,
  };
}

function makeExportData(overrides: Partial<ExportData> = {}): ExportData {
  return {
    projectTitle: "足球少年逆袭",
    storyBible: makeStoryBible(),
    outline: makeOutline(),
    scripts: [makeScript(1), makeScript(2)],
    evaluations: [makeEvaluation(1), makeEvaluation(2)],
    revisions: [{ plan: makeRevision(1), diff: makeDiff() }],
    ...overrides,
  };
}

// ============================================================
// 序列化纯函数
// ============================================================

describe("markdown 序列化", () => {
  it("StoryBible：中文标题稳定，不输出内部 ID / schema 字段", () => {
    const md = markdownFromStoryBible(makeStoryBible());
    expect(md).toContain("# 世界观与人物设定（StoryBible）");
    expect(md).toContain("- 剧名：足球少年逆袭");
    expect(md).toContain("## 主角");
    expect(md).toContain("- 姓名：林峰");
    expect(md).toContain("## 反派");
    expect(md).toContain("### 苏瑶（青训队医）");
    expect(md).toContain("## 锁定事实");
    expect(md).not.toContain("char-001");
    expect(md).not.toContain("character_id");
    expect(md).not.toContain("schema_version");
  });

  it("大纲：逐集渲染钩子/冲突/关键事件，不含内部校验字段", () => {
    const md = markdownFromOutline(makeOutline());
    expect(md).toContain("# 十集大纲");
    expect(md).toContain("## 第 1 集：被淘汰");
    expect(md).toContain("- 核心冲突：林峰被告知淘汰");
    expect(md).toContain("  - 公布名单");
    expect(md).toContain("## 第 2 集：重返球场");
    expect(md).not.toContain("validation_notes");
    expect(md).not.toContain("episode_number");
  });

  it("剧本：逐场渲染地点/动作/对白，不输出内部引用", () => {
    const md = markdownFromScript(makeScript(1));
    expect(md).toContain("# 第 1 集剧本：第1集");
    expect(md).toContain("## 第 1 场：训练场（白天）");
    expect(md).toContain("赵启：你不配站在这里。");
    expect(md).toContain("林峰（握紧拳头）：我会证明给你看。");
    expect(md).not.toContain("referenced_outline_artifact_id");
    expect(md).not.toContain("plain_text");
  });

  it("评估：维度中文标签 + 分数 + 问题证据/建议，不含 issue_id", () => {
    const md = markdownFromEvaluation(makeEvaluation(1));
    expect(md).toContain("# 第 1 集评估报告");
    expect(md).toContain("综合评分：77.6 分");
    expect(md).toContain("开头钩子：82 分");
    expect(md).toContain("合规安全：90 分");
    expect(md).toContain("[中等] 第 1 场 冲突强度：缺少尖锐对抗");
    expect(md).toContain("证据：冲突偏弱");
    expect(md).toContain("建议：增加对立配角");
    expect(md).toContain("风险提示");
    expect(md).not.toContain("iss_001");
    expect(md).not.toContain("rubric_version");
  });

  it("修订说明：操作/锁定事实/变更概览，不含内部 ID", () => {
    const md = markdownFromRevision(makeRevision(1), makeDiff());
    expect(md).toContain("# 第 1 集修订说明");
    expect(md).toContain("最大变更比例：20%");
    expect(md).toContain("用户补充要求：加强反派动机");
    expect(md).toContain("锁定事实（修订不得违反）");
    expect(md).toContain("- 林峰家境贫寒");
    expect(md).toContain("操作 1：第 1 场");
    expect(md).toContain("变更概览（原稿 → 修订稿）");
    expect(md).toContain("新增 1 / 删除 0 / 修改 1");
    expect(md).not.toContain("source_script_artifact_id");
    expect(md).not.toContain("op-1");
    expect(md).not.toContain("iss_001");
  });

  it("buildExportMarkdown：按选中类型拼接 + 抬头，未选类型不出现", () => {
    const data = makeExportData();
    const md = buildExportMarkdown({
      projectTitle: data.projectTitle,
      exportedAt: "2026-08-09T00:00:00.000Z",
      data,
      kinds: ["story_bible", "script", "revision"],
    });
    expect(md).toContain("# 足球少年逆袭 — 内容导出");
    expect(md).toContain("> 导出时间：2026-08-09T00:00:00.000Z");
    expect(md).toContain("# 世界观与人物设定（StoryBible）");
    expect(md).toContain("# 第 1 集剧本");
    expect(md).toContain("# 第 1 集修订说明");
    // 未选中的类型不出现
    expect(md).not.toContain("# 十集大纲");
    expect(md).not.toContain("评估报告");
  });

  it("buildExportMarkdown：选中但无数据的内容显示占位", () => {
    const data = makeExportData({ storyBible: null, scripts: [] });
    const md = buildExportMarkdown({
      projectTitle: data.projectTitle,
      exportedAt: "2026-08-09T00:00:00.000Z",
      data,
      kinds: ["story_bible", "script", "evaluation"],
    });
    expect(md).toContain("## StoryBible\n\n（无可用内容）");
    expect(md).toContain("## 剧本\n\n（无可用内容）");
  });
});

describe("文件名与时间戳", () => {
  it("buildExportFilename：项目名 + 内容标签 + 时间戳 + 扩展名", () => {
    expect(buildExportFilename("足球少年逆袭", ["story_bible", "script"], "markdown", "20260809-120000"))
      .toBe("足球少年逆袭-StoryBible-剧本-20260809-120000.md");
    expect(buildExportFilename("足球少年逆袭", ["revision"], "docx", "20260809-120000"))
      .toBe("足球少年逆袭-修订说明-20260809-120000.docx");
  });

  it("buildExportFilename：过滤文件名不安全字符（连续特殊字符合并为下划线）", () => {
    expect(buildExportFilename('a/b:c*?', ["outline"], "markdown", "t"))
      .toBe("a_b_c_-大纲-t.md");
  });

  it("formatTimestamp：yyyyMMdd-HHmmss（本地时间）", () => {
    // 用本地时间构造 Date，与 formatTimestamp 的本地取时一致，避免 CI 时区差异
    expect(formatTimestamp(new Date(2026, 7, 9, 12, 34, 56))).toBe("20260809-123456");
  });
});

describe("serializeExport", () => {
  it("markdown 格式：Blob 内容与文件名正确", async () => {
    const data = makeExportData();
    const now = new Date("2026-08-09T12:00:00Z");
    const result = await serializeExport({ data, kinds: ["outline"], format: "markdown", now });
    expect(result.filename).toContain("大纲");
    expect(result.filename).toMatch(/\.md$/);
    expect(result.markdown).toContain("# 十集大纲");
    expect(result.blob.size).toBeGreaterThan(0);
    expect(result.exportedAt).toBe(now.toISOString());
  });

  it("docx 格式：走 docx 库生成 Blob", async () => {
    const data = makeExportData();
    const result = await serializeExport({
      data,
      kinds: ["story_bible"],
      format: "docx",
      now: new Date("2026-08-09T12:00:00Z"),
    });
    expect(result.filename).toMatch(/\.docx$/);
    expect(result.blob.size).toBeGreaterThan(0);
    // 确认走的是 docx Packer（mock 返回 "docx-content"）
    await expect(result.blob.text()).resolves.toBe("docx-content");
  });
});

// ============================================================
// ExportSection
// ============================================================

describe("ExportSection", () => {
  beforeEach(() => {
    // jsdom 未实现 URL.createObjectURL，用 mock 顶替
    URL.createObjectURL = vi.fn(() => "blob:mock");
    URL.revokeObjectURL = vi.fn();
    HTMLAnchorElement.prototype.click = vi.fn();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("渲染全部内容类型与格式选项", () => {
    render(React.createElement(ExportSection, {
      data: makeExportData(),
      onExported: () => {},
    }));
    expect(screen.getByText("StoryBible")).toBeTruthy();
    expect(screen.getByText("大纲")).toBeTruthy();
    expect(screen.getByText("剧本")).toBeTruthy();
    expect(screen.getByText("评估")).toBeTruthy();
    expect(screen.getByText("修订说明")).toBeTruthy();
    expect(screen.getByText("Markdown (.md)")).toBeTruthy();
    expect(screen.getByText("Word (.docx)")).toBeTruthy();
  });

  it("点击生成并下载：触发 downloadBlob 与 onExported 记录", async () => {
    const onExported = vi.fn();
    render(React.createElement(ExportSection, {
      data: makeExportData(),
      onExported,
    }));
    fireEvent.click(screen.getByText("📦 生成并下载"));

    // 等待异步序列化完成
    await vi.waitFor(() => {
      expect(URL.createObjectURL).toHaveBeenCalled();
      expect(HTMLAnchorElement.prototype.click).toHaveBeenCalled();
    });

    const record: ExportRecord = onExported.mock.calls[0][0];
    expect(record.format).toBe("markdown");
    expect(record.kinds).toHaveLength(5);
    expect(record.filename).toMatch(/\.md$/);
    expect(record.sizeBytes).toBeGreaterThan(0);
    expect(record.exportedAt).toBeTruthy();
  });

  it("无数据的内容类型被禁用（不可勾选）", () => {
    render(React.createElement(ExportSection, {
      data: makeExportData({ outline: null }),
      onExported: () => {},
    }));
    const outlineCheckbox = screen
      .getAllByRole("checkbox")
      .find((cb) => (cb as HTMLInputElement).disabled);
    expect(outlineCheckbox).toBeTruthy();
  });
});

// ============================================================
// ExportHistory
// ============================================================

describe("ExportHistory", () => {
  it("空历史显示占位", () => {
    render(React.createElement(ExportHistory, {
      records: [],
      onRedownload: () => {},
      onClear: () => {},
    }));
    expect(screen.getByText(/暂无导出记录/)).toBeTruthy();
  });

  it("渲染历史记录（格式徽章 / 文件名 / 大小）并触发重新下载与清空", () => {
    const onRedownload = vi.fn();
    const onClear = vi.fn();
    render(React.createElement(ExportHistory, {
      records: [
        {
          id: "r1",
          exportedAt: "2026-08-09T12:00:00.000Z",
          format: "docx",
          kinds: ["story_bible", "script"],
          filename: "足球少年逆袭-StoryBible-剧本-20260809-120000.docx",
          sizeBytes: 2048,
        },
      ],
      onRedownload,
      onClear,
    }));
    expect(screen.getByText("DOCX")).toBeTruthy();
    expect(screen.getByText(/足球少年逆袭-StoryBible-剧本/)).toBeTruthy();
    expect(screen.getByText(/2.0 KB/)).toBeTruthy();
    expect(screen.getByText(/StoryBible、剧本/)).toBeTruthy();

    fireEvent.click(screen.getByText("重新下载"));
    expect(onRedownload).toHaveBeenCalledTimes(1);
    expect(onRedownload.mock.calls[0][0].id).toBe("r1");

    fireEvent.click(screen.getByText("清空历史"));
    expect(onClear).toHaveBeenCalledTimes(1);
  });
});
