/** StoryBible 与分集大纲视图测试 (H-04).
 *
 * 测试：
 * - CharacterCard 角色卡片渲染
 * - StoryBibleView 完整展示
 * - EpisodeCard 展开/折叠（使用原生 <details> 元素）
 * - OutlineListView 排序与版本选择
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import React from "react";

// ---- Mock next/link ----
vi.mock("next/link", () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) =>
    React.createElement("a", { href }, children),
}));

// ---- Mock next/navigation ----
vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "proj-1" }),
  useRouter: () => ({ push: vi.fn() }),
}));

import { CharacterCard } from "@/features/story-bible/CharacterCard";
import { StoryBibleView } from "@/features/story-bible/StoryBibleView";
import { EpisodeCard } from "@/features/outlines/EpisodeCard";
import { OutlineListView } from "@/features/outlines/OutlineListView";
import type { Artifact, StoryBibleContent, EpisodeOutlineSetContent, CharacterProfile, EpisodeOutline } from "@/types/api";

// ============================================================
// 工厂函数
// ============================================================

function makeArtifact(overrides: Partial<Artifact> = {}): Artifact {
  return {
    id: "art-1",
    project_id: "proj-1",
    type: "story_bible",
    version: 1,
    episode_number: 1,
    status: "valid",
    content: {},
    content_schema_version: "1.0.0",
    prompt_version: "1.0.0",
    created_at: "2026-07-26T10:00:00Z",
    updated_at: "2026-07-26T10:00:00Z",
    ...overrides,
  };
}

function makeCharacter(overrides: Partial<CharacterProfile> = {}): CharacterProfile {
  return {
    character_id: "char-1",
    name: "林风",
    role: "主角",
    age_range: "18-22",
    visible_goal: "成为职业球员",
    traits: ["坚韧", "乐观"],
    strengths: ["速度快", "意志强"],
    flaws: ["冲动"],
    ...overrides,
  };
}

function makeStoryBibleContent(overrides: Partial<StoryBibleContent> = {}): StoryBibleContent {
  return {
    title: "足球少年之逆袭人生",
    logline: "一个足球少年从谷底走向巅峰的热血故事",
    genre: "体育励志",
    tone: ["热血", "感动"],
    world_setting: "现代都市，高中到职业联赛",
    protagonist: makeCharacter(),
    antagonist: makeCharacter({ character_id: "char-2", name: "方寒", role: "反派", visible_goal: "阻止林风成功" }),
    supporting_characters: [],
    main_conflict: "林风与方寒的足球对决",
    stakes: "全国冠军的荣誉",
    story_rules: ["比赛结果不能靠运气"],
    long_term_payoffs: ["林风父亲的身份"],
    open_loops: ["神秘的教练"],
    locked_facts: ["林风来自单亲家庭", "方寒家境优越"],
    compliance_notes: [],
    ...overrides,
  };
}

function makeEpisodeOutline(ep: number, overrides: Partial<EpisodeOutline> = {}): EpisodeOutline {
  return {
    episode_number: ep,
    title: `第 ${ep} 集标题`,
    opening_hook: "开头钩子",
    objective: "本集目标",
    core_conflict: "核心冲突",
    key_events: ["事件1", "事件2"],
    payoff: "爽点",
    ending_hook: "结尾钩子",
    next_bridge: "下一集衔接",
    introduced_loops: ["新伏笔"],
    resolved_loops: [],
    required_characters: ["林风"],
    ...overrides,
  };
}

function makeOutlineSetContent(overrides: Partial<EpisodeOutlineSetContent> = {}): EpisodeOutlineSetContent {
  return {
    episodes: [makeEpisodeOutline(1), makeEpisodeOutline(2), makeEpisodeOutline(3)],
    arc_summary: "三集篇章摘要",
    validation_notes: ["已验证"],
    ...overrides,
  };
}

// ============================================================
// CharacterCard
// ============================================================

describe("CharacterCard", () => {
  it("显示角色姓名", () => {
    render(React.createElement(CharacterCard, {
      character: makeCharacter({ name: "林风" }),
      roleLabel: "主角",
    }));
    expect(screen.getByText("林风")).toBeTruthy();
  });

  it("显示角色标签", () => {
    render(React.createElement(CharacterCard, {
      character: makeCharacter(),
      roleLabel: "反派",
    }));
    expect(screen.getByText("反派")).toBeTruthy();
  });

  it("显示特征标签", () => {
    render(React.createElement(CharacterCard, {
      character: makeCharacter({ traits: ["坚韧", "乐观", "不服输"] }),
      roleLabel: "主角",
    }));
    expect(screen.getByText("坚韧")).toBeTruthy();
    expect(screen.getByText("乐观")).toBeTruthy();
    expect(screen.getByText("不服输")).toBeTruthy();
  });

  it("显示优势和缺陷", () => {
    render(React.createElement(CharacterCard, {
      character: makeCharacter({ strengths: ["速度快"], flaws: ["冲动"] }),
      roleLabel: "主角",
    }));
    expect(screen.getByText("速度快")).toBeTruthy();
    expect(screen.getByText("冲动")).toBeTruthy();
  });

  it("显示表层目标和深层需求", () => {
    render(React.createElement(CharacterCard, {
      character: makeCharacter({ visible_goal: "成为职业球员", hidden_need: "获得父爱" }),
      roleLabel: "主角",
    }));
    expect(screen.getByText("成为职业球员")).toBeTruthy();
    expect(screen.getByText("获得父爱")).toBeTruthy();
  });

  it("空字段显示占位提示", () => {
    render(React.createElement(CharacterCard, {
      character: makeCharacter({ traits: undefined, strengths: undefined, flaws: undefined, visible_goal: "", hidden_need: "" }),
      roleLabel: "配角",
    }));
    // 空字段应有"未设置"占位
    expect(screen.getByText("未设置性格特征")).toBeTruthy();
  });

  it("显示禁止修改项", () => {
    render(React.createElement(CharacterCard, {
      character: makeCharacter({ forbidden_changes: ["不能改变出身", "不能死亡"] }),
      roleLabel: "主角",
    }));
    expect(screen.getByText("🚫 禁止修改")).toBeTruthy();
    expect(screen.getByText(/不能改变出身/)).toBeTruthy();
  });

  it("禁止修改项为空时不显示该区块", () => {
    render(React.createElement(CharacterCard, {
      character: makeCharacter({ forbidden_changes: undefined }),
      roleLabel: "主角",
    }));
    expect(screen.queryByText("🚫 禁止修改")).toBeNull();
  });

  it("未命名角色显示占位", () => {
    render(React.createElement(CharacterCard, {
      character: makeCharacter({ name: "" }),
      roleLabel: "配角",
    }));
    expect(screen.getByText("未命名角色")).toBeTruthy();
  });
});

// ============================================================
// StoryBibleView
// ============================================================

describe("StoryBibleView", () => {
  function renderView(content?: Partial<StoryBibleContent>, versions?: Artifact[]) {
    const art = makeArtifact({ content: {} });
    const c = makeStoryBibleContent(content || {});
    const v = versions || [art];
    return render(React.createElement(StoryBibleView, {
      content: c,
      artifact: art,
      versions: v,
      onVersionChange: vi.fn(),
    }));
  }

  it("显示故事标题", () => {
    renderView({ title: "足球少年之逆袭人生" });
    expect(screen.getByText("足球少年之逆袭人生")).toBeTruthy();
  });

  it("显示 story梗概", () => {
    renderView({ logline: "热血足球故事" });
    expect(screen.getByText("热血足球故事")).toBeTruthy();
  });

  it("显示类型标签", () => {
    renderView({ genre: "体育励志" });
    expect(screen.getByText("体育励志")).toBeTruthy();
  });

  it("显示基调标签", () => {
    renderView({ tone: ["热血", "感动"] });
    expect(screen.getByText("热血")).toBeTruthy();
    expect(screen.getByText("感动")).toBeTruthy();
  });

  it("显示世界观设定", () => {
    renderView({ world_setting: "现代都市" });
    expect(screen.getByText("🌍 世界观设定")).toBeTruthy();
    expect(screen.getByText("现代都市")).toBeTruthy();
  });

  it("显示主要冲突和赌注", () => {
    renderView({ main_conflict: "足球对决", stakes: "冠军荣誉" });
    expect(screen.getByText("足球对决")).toBeTruthy();
    expect(screen.getByText("冠军荣誉")).toBeTruthy();
  });

  it("🔒 锁定事实视觉可识别", () => {
    renderView({ locked_facts: ["事实A", "事实B"] });
    // 锁定事实区域标题包含🔒
    expect(screen.getByText("🔒 锁定事实")).toBeTruthy();
    // 锁定事实区域在 amber 样式中（边框色为 amber-200）
    const amberBox = document.querySelector(".border-amber-200");
    expect(amberBox).toBeTruthy();
    // 每个锁定事实前有🔒图标（精确匹配图标 span）
    const lockIcons = screen.getAllByText("🔒", { exact: true });
    expect(lockIcons.length).toBeGreaterThanOrEqual(2);
  });

  it("锁定事实为空时显示占位", () => {
    renderView({ locked_facts: [] });
    expect(screen.getByText("暂无锁定事实")).toBeTruthy();
  });

  it("显示长期伏笔", () => {
    renderView({ long_term_payoffs: ["伏笔1", "伏笔2"] });
    expect(screen.getByText("📌 长期伏笔 (Long-term Payoffs)")).toBeTruthy();
    expect(screen.getByText("伏笔1")).toBeTruthy();
  });

  it("显示开放循环", () => {
    renderView({ open_loops: ["循环1"] });
    expect(screen.getByText("循环1")).toBeTruthy();
  });

  it("多版本时显示版本选择器", () => {
    const v1 = makeArtifact({ id: "art-1", version: 1 });
    const v2 = makeArtifact({ id: "art-2", version: 2 });
    renderView({}, [v1, v2]);
    expect(screen.getByText("版本：")).toBeTruthy();
  });

  it("单版本时隐藏版本选择器", () => {
    const v1 = makeArtifact({ id: "art-1", version: 1 });
    renderView({}, [v1]);
    expect(screen.queryByText("版本：")).toBeNull();
  });

  it("显示版本元信息", () => {
    renderView();
    expect(screen.getByText(/版本 1/)).toBeTruthy();
    expect(screen.getByText(/状态：valid/)).toBeTruthy();
  });

  it("显示主角和反派角色", () => {
    renderView();
    expect(screen.getByText("👥 角色设定")).toBeTruthy();
    expect(screen.getByText("林风")).toBeTruthy();
    expect(screen.getByText("方寒")).toBeTruthy();
  });
});

// ============================================================
// EpisodeCard（使用原生 <details> 元素）
// ============================================================

describe("EpisodeCard", () => {
  function renderCard(outline?: Partial<EpisodeOutline>, expanded = false) {
    const ep = makeEpisodeOutline(1, outline || {});
    return render(React.createElement(EpisodeCard, { outline: ep, defaultExpanded: expanded }));
  }

  it("显示集号和标题", () => {
    renderCard({ episode_number: 3, title: "逆袭的开始" });
    expect(screen.getByText("3")).toBeTruthy();
    expect(screen.getByText("逆袭的开始")).toBeTruthy();
  });

  it("默认折叠：details.open 为 false", () => {
    renderCard({ episode_number: 1 }, false);
    const details = document.querySelector("details");
    expect(details).toBeTruthy();
    expect(details!.open).toBe(false);
  });

  it("defaultExpanded=true 时 details.open 为 true", () => {
    renderCard({ episode_number: 1 }, true);
    const details = document.querySelector("details");
    expect(details!.open).toBe(true);
  });

  it("点击 summary 可展开", () => {
    renderCard({ episode_number: 1 }, false);
    const details = document.querySelector("details")!;
    const summary = details.querySelector("summary")!;
    expect(details.open).toBe(false);
    fireEvent.click(summary);
    expect(details.open).toBe(true);
  });

  it("点击 summary 可折叠", () => {
    renderCard({ episode_number: 1 }, true);
    const details = document.querySelector("details")!;
    const summary = details.querySelector("summary")!;
    expect(details.open).toBe(true);
    fireEvent.click(summary);
    expect(details.open).toBe(false);
  });

  it("展开后显示关键事件文本", () => {
    renderCard({ key_events: ["事件A", "事件B"] }, true);
    expect(screen.getByText("事件A")).toBeTruthy();
    expect(screen.getByText("事件B")).toBeTruthy();
  });

  it("展开后显示爽点和结尾钩子", () => {
    renderCard({ payoff: "赢得比赛", ending_hook: "神秘人出现" }, true);
    expect(screen.getByText("赢得比赛")).toBeTruthy();
    expect(screen.getByText("神秘人出现")).toBeTruthy();
  });

  it("空字段显示占位", () => {
    renderCard({ objective: "", opening_hook: "" }, true);
    const placeholders = screen.getAllByText("未设置");
    expect(placeholders.length).toBeGreaterThanOrEqual(2);
  });

  it("空关键事件显示占位", () => {
    renderCard({ key_events: [] }, true);
    expect(screen.getByText("暂无关键事件")).toBeTruthy();
  });

  it("未命名集显示占位", () => {
    renderCard({ title: "" });
    expect(screen.getByText("未命名")).toBeTruthy();
  });

  it("显示伏笔和角色标签", () => {
    renderCard({
      introduced_loops: ["新角色登场"],
      resolved_loops: ["父亲身份"],
      required_characters: ["林风", "方寒"],
    }, true);
    expect(screen.getByText("新角色登场")).toBeTruthy();
    expect(screen.getByText("父亲身份")).toBeTruthy();
    expect(screen.getByText("林风")).toBeTruthy();
    expect(screen.getByText("方寒")).toBeTruthy();
  });
});

// ============================================================
// OutlineListView
// ============================================================

describe("OutlineListView", () => {
  function renderView(content?: Partial<EpisodeOutlineSetContent>, versions?: Artifact[]) {
    const art = makeArtifact({ type: "episode_outline_set" });
    const c = makeOutlineSetContent(content || {});
    const v = versions || [art];
    return render(React.createElement(OutlineListView, {
      content: c,
      artifact: art,
      versions: v,
      onVersionChange: vi.fn(),
    }));
  }

  it("显示篇章摘要", () => {
    renderView({ arc_summary: "这是一个关于足球的故事" });
    expect(screen.getByText("📖 篇章摘要")).toBeTruthy();
    expect(screen.getByText("这是一个关于足球的故事")).toBeTruthy();
  });

  it("显示分集数量", () => {
    renderView({
      episodes: [makeEpisodeOutline(1), makeEpisodeOutline(2), makeEpisodeOutline(3)],
    });
    expect(screen.getByText(/分集大纲 \(3 集\)/)).toBeTruthy();
  });

  it("按集号稳定排序", () => {
    const episodes = [
      makeEpisodeOutline(5, { title: "第 5 集" }),
      makeEpisodeOutline(1, { title: "第 1 集" }),
      makeEpisodeOutline(3, { title: "第 3 集" }),
    ];
    renderView({ episodes });
    // 使用 details 元素，检查它们的顺序
    const details = document.querySelectorAll("details");
    expect(details.length).toBe(3);
    // 按集号升序：1, 3, 5
    const texts = Array.from(details).map((d) => d.textContent?.trim() || "");
    expect(texts[0]).toContain("第 1 集");
    expect(texts[1]).toContain("第 3 集");
    expect(texts[2]).toContain("第 5 集");
  });

  it("多版本时显示版本选择器", () => {
    const v1 = makeArtifact({ id: "art-1", version: 1, type: "episode_outline_set" });
    const v2 = makeArtifact({ id: "art-2", version: 2, type: "episode_outline_set" });
    renderView({}, [v1, v2]);
    expect(screen.getByText("版本：")).toBeTruthy();
  });

  it("空集列表显示占位", () => {
    renderView({ episodes: [] });
    expect(screen.getByText("暂无分集大纲")).toBeTruthy();
  });

  it("显示验证备注", () => {
    renderView({ validation_notes: ["所有集数结构完整", "连续性问题已修复"] });
    expect(screen.getByText("✅ 验证备注")).toBeTruthy();
    expect(screen.getByText(/所有集数结构完整/)).toBeTruthy();
  });

  it("显示版本元信息", () => {
    renderView();
    expect(screen.getByText(/版本 1/)).toBeTruthy();
  });
});
