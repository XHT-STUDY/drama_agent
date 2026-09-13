/** W1-02/W1-07 同屏工作台验收：三个 viewport 布局与键盘流程。
 *
 * 覆盖任务卡人工验收的自动化部分：
 * - 1440×900 / 1024×768 / 390×844 三视口下正文可读、无横向溢出、
 *   Composer 可达、窄屏双 tab 切换不丢画布与消息；
 * - 键盘：tab 进入 Composer、Escape 关抽屉类 UI、焦点顺序可遍历。
 *
 * 布局断言用边界几何（不像素级比对）：画布在视口内、无水平滚动。
 */

import { test, expect, type Page } from "@playwright/test";

// 后端 API 直连（frontend baseURL 是 3100，API 在 8010，同 knowledge.spec）
const API = process.env.E2E_API_BASE || "http://localhost:8010/api/v1";

test.describe("同屏工作台响应式与键盘（W1-07）", () => {
  test.beforeEach(async ({ request }) => {
    const resp = await request.post(`${API}/projects`, {
      data: { title: `验收-响应式-${Date.now()}`, target_episode_count: 10 },
    });
    if (resp.ok()) {
      const project = (await resp.json()) as { id: string };
      process.env.E2E_PROJECT_ID = project.id;
    } else {
      throw new Error(`项目创建失败: ${resp.status()} ${await resp.text()}`);
    }
  });

  async function openWorkspace(page: Page): Promise<string> {
    const projectId = process.env.E2E_PROJECT_ID!;
    await page.goto(`/projects/${projectId}`);
    // 工作台挂载即可测布局（tab 栏在窄屏可见；桌面 hidden 不判定可见性）
    await expect(page.getByTestId("work-pane")).toBeAttached({ timeout: 30_000 });
    return projectId;
  }

  for (const viewport of [
    { name: "desktop-1440", width: 1440, height: 900 },
    { name: "tablet-1024", width: 1024, height: 768 },
    { name: "mobile-390", width: 390, height: 844 },
  ]) {
    test(`[${viewport.name}] 布局无横向溢出，核心区可达`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await openWorkspace(page);

      // 无水平溢出（正文可读的底线）
      const overflow = await page.evaluate(() => {
        const el = document.scrollingElement ?? document.documentElement;
        return el.scrollWidth - el.clientWidth;
      });
      expect(overflow).toBeLessThanOrEqual(1);

      // 窄屏：双 tab 可见，切 Agent tab 验证 Composer 可达、切回不卸载；
      // 桌面（≥1024）：tab 栏 hidden，三区直接可见
      if (viewport.width < 1024) {
        const workTab = page.getByTestId("tab-work");
        await expect(workTab).toBeVisible();
        await workTab.click();
        await page.getByTestId("tab-agent").click();
        await expect(page.getByTestId("composer-send")).toBeVisible();
        await page.getByTestId("tab-work").click();
      } else {
        await expect(page.getByTestId("composer-send")).toBeVisible();
      }
      await expect(page.getByTestId("work-pane")).toBeAttached();
    });
  }

  test("键盘：tab 聚焦 Composer 发送链路，focus 不被画布抢走", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const projectId = await openWorkspace(page);
    // 窄屏下 Agent 区通过 tab 可见
    await page.getByTestId("tab-agent").click();

    // 键盘直达输入框并输入（不使用鼠标）
    await page.keyboard.press("Tab");
    const textarea = page.getByRole("textbox");
    let focused = false;
    for (let i = 0; i < 20; i += 1) {
      if (await textarea.evaluate((el) => el === document.activeElement)) {
        focused = true;
        break;
      }
      await page.keyboard.press("Tab");
    }
    expect(focused).toBe(true);

    // 无活动上下文时输入澄清类内容不触发未授权生成（仍可输入）
    await textarea.fill("帮我改一下这里");
    expect(await textarea.inputValue()).toBe("帮我改一下这里");
    await page.keyboard.press("Escape");
    // 项目仍无 Run（明确请求才会创建）
    const runs = await page.request.get(`${API}/projects/${projectId}/runs`);
    const body = (await runs.json()) as { total?: number };
    expect(body.total ?? 0).toBe(0);
  });
});
