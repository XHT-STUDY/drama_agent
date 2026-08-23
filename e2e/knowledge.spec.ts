/**
 * K-4 知识库 E2E：上传参考资料 → 自动入库 → 知识库页检索试算 → 删除。
 *
 * 运行环境：scripts/e2e.sh（FakeLLM + FakeEmbedder，确定性）。
 * 上传/导入经 API 铺设（前端无上传 UI——G-03 上传入口在 API 层），
 * 断言全部落在知识库页 UI（K-3）。
 */

import { test, expect } from "@playwright/test";

const API = process.env.E2E_API_BASE || "http://localhost:8010/api/v1";

/** 命中 reference 规则关键词的参考资料文本（规则判定，无需 LLM） */
const REFERENCE_TEXT = [
  "参考资料：短剧《逆袭人生》设定集。",
  "主角林峰拥有战术视野天赋，被青训队抛弃后遇到伯乐教练张德胜。",
  "反派陈浩是林峰青训时期的前队友，两人冲突贯穿全剧。",
  "人物关系参考文档见 http://example.com/chars。",
].join("\n");

test.describe("K-4 知识库", () => {
  test("上传参考资料→自动入库→检索试算→删除", async ({ page, request }) => {
    test.setTimeout(90_000);

    // ---- API 铺设：建项目 → 上传 → 导入分类（route=hold 自动摄取）----
    const projectResp = await request.post(`${API}/projects`, {
      data: { title: `E2E知识库-${Date.now()}` },
    });
    expect(projectResp.ok()).toBeTruthy();
    const project = (await projectResp.json()) as { id: string };

    const uploadResp = await request.post(`${API}/projects/${project.id}/uploads`, {
      multipart: {
        file: {
          name: "reference-material.txt",
          mimeType: "text/plain",
          buffer: Buffer.from(REFERENCE_TEXT, "utf-8"),
        },
      },
    });
    expect(uploadResp.ok()).toBeTruthy();
    const upload = (await uploadResp.json()) as { id: string };

    const runResp = await request.post(`${API}/projects/${project.id}/runs`, {
      data: { action: "import", config: { upload_id: upload.id } },
    });
    expect(runResp.status()).toBe(202);
    const run = (await runResp.json()) as { run_id: string };

    for (let i = 0; i < 60; i += 1) {
      const statusResp = await request.get(`${API}/runs/${run.run_id}`);
      const status = ((await statusResp.json()) as { status: string }).status;
      if (status === "completed") break;
      if (status === "failed" || status === "needs_review") {
        throw new Error(`导入 Run 终态异常: ${status}`);
      }
      await page.waitForTimeout(500);
    }

    // ---- UI：知识库页列表 ----
    await page.goto(`/projects/${project.id}/knowledge`);
    await expect(
      page.getByText("reference-material", { exact: false }).first(),
    ).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("本项目").first()).toBeVisible();
    await expect(page.getByText(/向量齐全/).first()).toBeVisible();

    // ---- UI：检索试算命中（FakeEmbedder 确定性）----
    await page.getByTestId("knowledge-search-input").fill("林峰的战术视野");
    await page.getByTestId("knowledge-search-button").click();
    await expect(page.getByTestId("knowledge-search-hits")).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByText(/相似度/).first()).toBeVisible();
    await expect(page.getByTestId("knowledge-search-trace")).toContainText("ms");

    // ---- UI：删除 → 列表清空（该文档为本项目唯一文档）----
    const deleteButton = page.locator('[data-testid^="knowledge-delete-"]').first();
    await deleteButton.click();
    await expect(page.getByText("知识库还没有文档")).toBeVisible({
      timeout: 30_000,
    });
  });
});
