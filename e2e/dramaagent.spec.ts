/**
 * H-07 全链路 E2E Demo（阶段 H Exit Gate 路径）。
 *
 * 运行环境：scripts/e2e.sh 编排 —— 隔离 PostgreSQL/Redis + FakeLLM 后端(8010)
 * + 前端(3100)。FAKE_LLM_SCENARIO=revision → 评估走低分 fixture，触发
 * F-05 自动修订（恰好 1 集）。
 *
 * 覆盖验收项：
 * - 空项目 → Idea → SSE 实时进度 → 刷新（SSE 重连）→ 内容 → 修订 → Diff → 导出
 * - 自动生成 10 集大纲 + 前 3 集剧本 + 评估 + 修订 + Diff
 * - 每次只修订一个低分集（修订列表恰好 1 条，第 1 集）
 * - 下载文件存在且非空
 * - `--repeat-each=N` 可重复运行（make e2e REPEAT=5 验收）
 *
 * 截图 / trace 只在失败时保留（playwright.config.ts）。
 */

import { test, expect } from "@playwright/test";
import { EXPECTED } from "./fixtures/data";
import {
  startCreation,
  waitForRunTerminal,
  expectExactlyOneRevision,
  expectDownloadNotEmpty,
  workbenchEntry,
} from "./fixtures/helpers";

/** 从当前 URL 提取项目 ID */
function projectId(page: { url(): string }): string {
  const m = page.url().match(/\/projects\/([0-9a-f-]{36})/);
  if (!m) throw new Error(`无法从 URL 解析项目 ID: ${page.url()}`);
  return m[1];
}

test.describe("H-07 全链路 Demo", () => {
  test("空项目 → 创作 → SSE → 刷新 → 内容 → 修订 → Diff → 导出下载", async ({ page }) => {
    // ============================================================
    // 1. 创建项目 + 输入 Idea + 开始创作（SSE 实时进度）
    // ============================================================
    await test.step("创建项目并开始创作", async () => {
      await startCreation(page);
      // SSE 进度面板出现节点（需求归一化 → 故事设定…）
      await expect(page.getByText("需求归一化").first()).toBeVisible({
        timeout: 30_000,
      });
    });

    // ============================================================
    // 2. 刷新 + SSE 重连：重载后恢复进度并到达终态
    //    低分场景下创作 Run 自动修订 1 集后停在「需人工复核」，
    //    工作台仍展示全部内容入口（completed / needs_review 均渲染）。
    // ============================================================
    await test.step("刷新页面（SSE 重连）并等待 Run 终态", async () => {
      await page.reload();
      await waitForRunTerminal(page);
    });
    const pid = projectId(page);

    // ============================================================
    // 3. StoryBible
    // ============================================================
    await test.step("查看 StoryBible", async () => {
      await workbenchEntry(page).click();
      await expect(
        page.getByText(EXPECTED.storyBibleTitle).first(),
      ).toBeVisible();
      await expect(page.getByText(EXPECTED.protagonist).first()).toBeVisible();
    });

    // ============================================================
    // 4. 分集大纲（10 集）
    // ============================================================
    await test.step("查看 10 集大纲", async () => {
      await page.goto(`/projects/${pid}/outline`);
      await expect(
        page.getByText(`📋 分集大纲 (${EXPECTED.outlineCount} 集)`),
      ).toBeVisible();
      await expect(
        page.getByText(EXPECTED.episode1Title).first(),
      ).toBeVisible();
      await expect(
        page.getByText(EXPECTED.episode10Title).first(),
      ).toBeVisible();
    });

    // ============================================================
    // 5. 剧本（前 3 集）+ 评分（低分 → 需修订徽章）
    // ============================================================
    await test.step("查看第 1 集剧本与评估", async () => {
      await page.goto(`/projects/${pid}/scripts/1`);
      await expect(
        page.getByText(`第 1 集 · ${EXPECTED.episode1Title}`),
      ).toBeVisible();
      // 低分场景：评估报告 need_revision=true → 「⚠️ 需修订」
      await expect(page.getByText("需修订").first()).toBeVisible();
      await expect(page.getByText("📊 维度评分")).toBeVisible();
    });

    // ============================================================
    // 6. 修订与版本：恰好 1 条修订计划（第 1 集）+ 详情全链路
    // ============================================================
    await test.step("查看修订计划（每次只修订一个低分集）", async () => {
      await page.goto(`/projects/${pid}/versions`);
      await expectExactlyOneRevision(page);

      // 详情：连续性检查 + 评分对比 + 版本 Diff
      await expect(page.getByText("第 1 集修订详情")).toBeVisible();
      await expect(page.getByText("✅ 连续性检查通过")).toBeVisible();
      await expect(page.getByText("原稿评分")).toBeVisible();
      await expect(page.getByText("修订稿评分")).toBeVisible();
      await expect(page.getByText("版本 Diff（原稿 v1 → 修订稿 v2）")).toBeVisible();
      // DiffView 统计行
      await expect(page.getByText("变更比例").first()).toBeVisible();
    });

    // ============================================================
    // 7. 版本对比：第 1 集原稿 v1 → 修订稿 v2
    // ============================================================
    await test.step("对比版本（v1 → v2）", async () => {
      // versions 页默认集数=第 1 集、原稿=最新前一个、修订稿=最新 → DiffView 已渲染
      await expect(page.getByText("变更比例").first()).toBeVisible();
      await expect(
        page.getByText("修订记录", { exact: true }).first(),
      ).toBeVisible();
    });

    // ============================================================
    // 8. 导出下载：Markdown + DOCX 均非空，历史显示 2 条
    // ============================================================
    await test.step("导出 Markdown / DOCX 并校验下载文件", async () => {
      await page.goto(`/projects/${pid}/exports`);
      // 数据加载完成（ExportSection 出现）
      await expect(page.getByText("选择导出内容")).toBeVisible();
      await expect(page.getByRole("button", { name: "📦 生成并下载" })).toBeVisible();

      // 第一次导出：Markdown（默认全选内容类型）
      const downloadBtn = page.getByRole("button", { name: "📦 生成并下载" });
      const mdName = await expectDownloadNotEmpty(page, () => downloadBtn.click());
      expect(mdName.endsWith(".md"), "Markdown 文件名应以 .md 结尾").toBe(true);
      // 等待历史出现第 1 条记录
      await expect(page.getByText("导出历史（1）")).toBeVisible();

      // 第二次导出：DOCX
      await page.getByText("Word (.docx)").click();
      const docxName = await expectDownloadNotEmpty(page, () => downloadBtn.click());
      expect(docxName.endsWith(".docx"), "DOCX 文件名应以 .docx 结尾").toBe(true);
      await expect(page.getByText("导出历史（2）")).toBeVisible();

      // 历史记录含两种格式徽章
      await expect(page.getByText("MD", { exact: true }).first()).toBeVisible();
      await expect(page.getByText("DOCX", { exact: true }).first()).toBeVisible();
    });
  });
});
