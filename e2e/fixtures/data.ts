/**
 * E2E Demo 常量 (H-07)。
 *
 * 全部取值对齐 FakeLLM golden fixture（backend/tests/golden/*），
 * 保证低分场景下评估 need_revision=true、select_revision 选第 1 集。
 */

/** 创建项目时的项目名（每轮重复加时间戳保证唯一） */
export function makeProjectName(): string {
  const stamp = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14);
  return `E2E足球少年-${stamp}`;
}

/** ChatInput 的 Idea（≥8 字符，进入 normalize 后按 golden fixture 产出） */
export const IDEA_TEXT =
  "被青训队抛弃的足球少年林峰，凭借被所有人忽视的战术视野天赋，从底层联赛一路逆袭至职业巅峰。要求强爽点、强反派压迫、每集结尾有追更钩子。";

// ---- 期望的 golden 内容（用于断言） ----
export const EXPECTED = {
  /** StoryBible 标题（story_bible_valid.json） */
  storyBibleTitle: "足球少年之逆袭人生",
  /** StoryBible 主角 */
  protagonist: "林峰",
  /** 大纲集数（outline_set_valid.json 10 集） */
  outlineCount: 10,
  /** 第 1 集标题 */
  episode1Title: "被抛弃的天才",
  /** 第 10 集标题 */
  episode10Title: "新的起点",
  /** 剧本集数（MVP 前 3 集） */
  scriptCount: 3,
  /** 低分场景评估 need_revision=true → 修订恰好 1 条（第 1 集，平局取最小集号） */
  revisionEpisode: 1,
  /** 评估报告维度数（EvaluationReport 9 维） */
  evalDimensionCount: 9,
  /** 综合评分阈值：低分场景 overall < 75 → 需修订 */
  evalLowScoreBelow: 75,
} as const;
