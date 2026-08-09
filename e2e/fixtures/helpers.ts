import { expect, type Page } from "@playwright/test";
import { IDEA_TEXT, EXPECTED } from "./data";

/**
 * E2E 辅助函数 (H-07)。
 *
 * 所有「等待」都以 UI 可见文本为准（SSE / 轮询最终都落到页面状态），
 * 不依赖网络时序，保证可重复运行稳定。
 */

/** 工作台首页内容入口链接的定位器（创作 Run 到终态后出现） */
export const workbenchEntry = (page: Page) =>
  page.getByRole("link", { name: "📖 查看 StoryBible" });

/**
 * 等待创作 Run 到达终态（低分场景下会停在「需人工复核」）。
 * 工作台首页出现「📖 查看 StoryBible」入口即视为终态（completed / needs_review 均渲染）。
 */
export async function waitForRunTerminal(page: Page): Promise<void> {
  await expect(workbenchEntry(page).first()).toBeVisible({ timeout: 90_000 });
}

/** 等待修订 Run 终态（versions 页轮询到完成或需复核，并刷新修订列表）。 */
export async function waitForRevisionRunTerminal(page: Page): Promise<void> {
  await expect(
    page.getByText("修订完成").or(page.getByText("修订需人工复核")),
  ).toBeVisible({ timeout: 90_000 });
}

/**
 * 断言修订计划列表恰好 1 条，且为第 1 集（「每次只修订一个低分集」验收）。
 * 低分场景下三集同分 → F-05 平局取最小集号 → 恰好修第 1 集。
 */
export async function expectExactlyOneRevision(page: Page): Promise<void> {
  const plan = page.getByText(
    `第 ${EXPECTED.revisionEpisode} 集 · 修订计划 v`,
  );
  await expect(plan).toBeVisible({ timeout: 15_000 });
  // 恰好 1 条修订记录（RevisionPlanList 每条是一个含「修订计划 v」的按钮）
  await expect(
    page.locator('button:has-text("修订计划 v")'),
  ).toHaveCount(1, { timeout: 15_000 });
}

/**
 * 触发一次下载并断言文件非空（≥1 字节）。
 * trigger 内执行点击等触发动作（必须先注册 waitForEvent，再触发）。
 * 返回建议文件名。
 */
export async function expectDownloadNotEmpty(
  page: Page,
  trigger: () => Promise<void>,
): Promise<string> {
  const downloadPromise = page.waitForEvent("download", { timeout: 30_000 });
  await trigger();
  const download = await downloadPromise;
  const filePath = await download.path();
  expect(filePath, "下载文件应存在且非空").toBeTruthy();
  const { statSync } = await import("node:fs");
  expect(statSync(filePath!).size, "下载文件 size > 0").toBeGreaterThan(0);
  return download.suggestedFilename();
}

let titleCounter = 0;

/** 唯一项目名（repeat-each 多轮共享同一后端 DB，须保证不冲突） */
export function makeProjectName(): string {
  const stamp = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14);
  titleCounter += 1;
  return `E2E足球少年-${stamp}-${titleCounter}`;
}

/**
 * 新建项目 → 输入 Idea（生成 3 集剧本）→ 开始创作，进入工作台起始状态。
 * 返回项目名（断言用）。
 */
export async function startCreation(page: Page): Promise<string> {
  await page.goto("/projects");
  await page.getByRole("link", { name: "创建项目" }).click();
  await page.waitForURL(/\/projects\/new$/);

  const name = makeProjectName();
  await page.locator("#title").fill(name);
  await page.getByRole("button", { name: "创建项目" }).click();

  // 创建成功后跳转到项目工作台
  await page.waitForURL(/\/projects\/[0-9a-f-]{36}$/);
  // 「创作 Idea」同时出现在输入区标题与空态引导 → 取 first 避免 strict mode 冲突
  await expect(page.getByText("创作 Idea").first()).toBeVisible();

  // 生成 3 集剧本（MVP「前 3 集剧本」；大纲仍是 golden 10 集）
  await page.locator("textarea").first().fill(IDEA_TEXT);
  await page.locator('input[type="number"]').fill("3");
  await page.getByRole("button", { name: "开始创作" }).click();

  // SSE 进度面板出现（已连接）
  await expect(page.getByText("创作进度").first()).toBeVisible({
    timeout: 30_000,
  });
  return name;
}

/** 断言大纲集数 == EXPECTED.outlineCount（10 集） */
export async function expectOutlineCount(page: Page): Promise<void> {
  await expect(
    page.getByText(`📋 分集大纲 (${EXPECTED.outlineCount} 集)`),
  ).toBeVisible();
}

export { EXPECTED };
