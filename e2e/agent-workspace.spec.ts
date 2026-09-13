/**
 * J-12 对话式创作工作台 E2E（Agent Workspace）。
 *
 * 运行环境：scripts/e2e.sh —— FAKE_LLM_SCENARIO=agent_e2e
 * （内容感知 planner 桩 + 低分评估 + outline_reviser fixtures），
 * 前端 NEXT_PUBLIC_AGENT_WORKSPACE_ENABLED=true。
 *
 * 覆盖验收项（PLAN Task 12）：
 * - 首次创作计划→确认→完成（含刷新恢复消息与进行中的 Run）
 * - 模糊修改→澄清且无 Run
 * - 重复 Turn 不重复消息
 * - 指定第三集修改→必要评估→新稿→Diff（TDD anchor）
 * - 修改大纲→新版本→受影响剧集警告
 * - 重复确认只产生一个 Run
 * - 部分达成→证据与剩余约束→一个后续计划→再次确认（TDD anchor）
 *
 * 串行共享同一项目，避免重复执行完整创作链路。
 */

import { test, expect, type Page } from "@playwright/test";
import { IDEA_TEXT, EXPECTED } from "./fixtures/data";
import { makeProjectName, sendAndConfirmPlan } from "./fixtures/helpers";

test.describe.configure({ mode: "serial" });

/** 串行共享上下文：T1 创建后被后续用例复用 */
const ctx: { projectId: string | null } = { projectId: null };

async function openProject(page: Page): Promise<string> {
  const pid = ctx.projectId;
  if (!pid) throw new Error("前置用例未创建项目");
  await page.goto(`/projects/${pid}`);
  return pid;
}

async function createProjectViaWorkspace(page: Page): Promise<string> {
  await page.goto("/projects");
  await page.getByRole("link", { name: "+ 创建项目" }).click();
  await page.waitForURL(/\/projects\/new$/);
  await page.locator("#title").fill(makeProjectName());
  await page.getByRole("button", { name: "创建项目" }).click();
  await page.waitForURL(/\/projects\/[0-9a-f-]{36}$/);
  const pid = page.url().match(/\/projects\/([0-9a-f-]{36})/)![1];
  ctx.projectId = pid;
  return pid;
}

/** 等待工作台计划卡到达终态徽标 */
async function waitCardTerminal(page: Page, timeout = 90_000): Promise<void> {
  await expect(
    page.getByText("已完成", { exact: true }).or(
      page.getByText("需人工复核", { exact: true }),
    ),
  ).toBeVisible({ timeout });
}

test.describe("J-12 Agent Workspace", () => {
  test("首次创作：计划→确认→完成（W1-07：结果自动回写，不依赖刷新）", async ({ page }) => {
    await createProjectViaWorkspace(page);

    await page.getByLabel("输入创作指令").fill(IDEA_TEXT);
    await page.getByTestId("composer-send").click();
    await expect(page.getByTestId("confirm-action")).toBeVisible({ timeout: 30_000 });
    await page.getByTestId("confirm-action").click();

    // 分阶段默认：大纲门 → "写全部"后进入执行
    const continueAll = page.getByTestId("continue-all");
    await expect(continueAll).toBeVisible({ timeout: 90_000 });
    await continueAll.click();
    await expect(continueAll).toBeHidden({ timeout: 30_000 });

    // W1-07 主断言：结果消息自动回写——不刷新页面，等 SSE/轮询追平
    await expect(page.getByTestId("result-message").first()).toBeVisible({
      timeout: 120_000,
    });

    // 额外恢复验收：刷新后消息仍可见（服务端是事实源）
    await page.reload();
    await expect(page.getByTestId("result-message").first()).toBeVisible({
      timeout: 30_000,
    });
  });

  test("W1-01 分批续写：写 1 集 → 再写一批，每批各有结果消息", async ({ page }) => {
    await createProjectViaWorkspace(page);

    await page.getByLabel("输入创作指令").fill(IDEA_TEXT);
    await page.getByTestId("composer-send").click();
    await expect(page.getByTestId("confirm-action")).toBeVisible({ timeout: 30_000 });
    await page.getByTestId("confirm-action").click();

    // 大纲门：写 1 集（批模式）
    const continueOne = page.getByTestId("continue-batch-1");
    await expect(continueOne).toBeVisible({ timeout: 90_000 });
    await continueOne.click();
    await expect(continueOne).toBeHidden({ timeout: 30_000 });

    // 第一批 scripts 门消息自动回写（不刷新）。门消息渲染为最小化阶段卡
    // （stage_gate metadata，无 result-message testid）——按文本断言
    await expect(
      page.locator(".message-body", { hasText: "本批剧本已完成（共 1/10 集）" }),
    ).toBeVisible({ timeout: 120_000 });

    // scripts 门复现 → 再写一批（连续批门不吞消息：W1-01 幕次世代）
    await expect(continueOne).toBeVisible({ timeout: 120_000 });
    await continueOne.click();
    await expect(continueOne).toBeHidden({ timeout: 30_000 });

    // 第二批门消息追加（共 2/10）——连续两个 scripts 门各一条结果消息
    await expect(
      page.locator(".message-body", { hasText: "本批剧本已完成（共 2/10 集）" }),
    ).toBeVisible({ timeout: 120_000 });
  });

  test("模糊修改→澄清且无 Run（W1-02 后在无稿项目上测）", async ({ request, page }) => {
    // W1-02 起 active_context 从画布派生——有稿项目上"这里"有指代不澄清。
    // 澄清语义改在空项目上验证（同时覆盖"空项目直接对话"验收）。
    const resp = await request.post(
      `${process.env.E2E_API_BASE || "http://localhost:8010/api/v1"}/projects`,
      { data: { title: `澄清-空项目-${Date.now()}`, target_episode_count: 10 } },
    );
    expect(resp.ok()).toBeTruthy();
    const { id: pid } = await resp.json();
    await page.goto(`/projects/${pid}`);
    await page.getByLabel("输入创作指令").fill("帮我改一下这里");
    await page.getByTestId("composer-send").click();

    await expect(page.getByTestId("clarification-message")).toBeVisible({
      timeout: 30_000,
    });
    // 澄清轮没有可确认的计划、没有 Run 进度
    await expect(page.getByTestId("confirm-action")).toHaveCount(0);
    await expect(page.getByText("创作进度")).toHaveCount(0);
  });

  test("重复发送不重复消息", async ({ page }) => {
    await openProject(page);
    // 独立会话：消息计数断言不受前序用例消息影响——等新会话落 URL
    //（Composer remount 完成）再输入，避免发到旧会话
    await page.getByTestId("new-conversation").click();
    await page.waitForURL(/conversation=/, { timeout: 15_000 });
    const composer = page.getByLabel("输入创作指令");
    await composer.fill("解释一下当前项目的大纲");
    // 同一逻辑动作的重复提交：同一 tick 双 Enter（Hook in-flight 守卫拦截）
    await page.keyboard.press("Enter");
    await page.keyboard.press("Enter");
    await expect(
      page.locator(".message-body", { hasText: "解释一下当前项目的大纲" }),
    ).toHaveCount(1, { timeout: 30_000 });
  });

  test("script_revision_from_chat_produces_version_diff", async ({ page }) => {
    const pid = await openProject(page);
    await page.getByTestId("new-conversation").click();
    // W1-02：活动上下文从画布派生——在作品导航打开第 2 集（"这里"即第 2 集；
    // ctx 项目由分批续写用例写了 1+1 集，第 2 集已存在）
    const ep2 = page.getByRole("button", { name: "第 2 集" }).first();
    await expect(ep2).toBeEnabled({ timeout: 30_000 });
    await ep2.click();
    await expect(page.getByTestId("canvas-artifact-meta")).toContainText("第 2 集");
    await sendAndConfirmPlan(page, "修改第 2 集剧本，增加主角与教练的正面冲突");

    await waitCardTerminal(page);
    // 对话式修订产生新版本：versions 页切到第 2 集 → v1 → v2 Diff
    await page.goto(`/projects/${pid}/versions`);
    await page.locator("select").first().selectOption("2");
    await expect(page.getByText("版本 Diff（原稿 v1 → 修订稿 v2）").first()).toBeVisible({
      timeout: 30_000,
    });
  });

  test("partial_outcome_proposes_one_confirmable_follow_up", async ({ page }) => {
    await openProject(page);
    // 前置：ctx 项目停在 scripts 门（分批用例写了 1+1 集）——写完剩余集，
    // 让大纲修订（fixture 固定变化第 3 集）能命中第 3 集剧本 → 部分达成
    const continueAll = page.getByTestId("continue-all");
    await expect(continueAll).toBeVisible({ timeout: 30_000 });
    await continueAll.click();
    await expect(continueAll).toBeHidden({ timeout: 30_000 });
    await expect(page.getByTestId("result-message").first()).toBeVisible({
      timeout: 120_000,
    });
    await page.getByTestId("new-conversation").click();
    // W1-02：打开大纲画布即活动上下文（替代旧 context-* 手动选中按钮）
    await page.getByRole("button", { name: "分集大纲" }).click();
    await expect(page.getByTestId("canvas-artifact-meta")).toContainText("分集大纲");
    await sendAndConfirmPlan(page, "修改大纲，第 3 集节奏太慢，冲突提前");
    await expect(page.getByTestId("canvas-artifact-meta")).toContainText("分集大纲");
    await sendAndConfirmPlan(page, "修改大纲，第 3 集节奏太慢，冲突提前");

    await waitCardTerminal(page);
    // W1-04 契约：部分达成 + 已知影响如实列出（仍引用旧大纲的剧本）+
    // 本轮证据入口（带角色锚点）可见——不再"降噪"到隐藏事实
    await expect(page.getByText("部分达成").first()).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("remaining-constraints").first()).toBeVisible();
    await expect(page.getByTestId("evidence-refs").first()).toBeVisible();
    await expect(page.getByTestId("evidence-link-outline").first()).toBeVisible();

    // 后续计划：proposed、可确认、恰好一个
    const followUpConfirm = page.getByTestId("confirm-action");
    await expect(followUpConfirm).toHaveCount(1, { timeout: 30_000 });
    await followUpConfirm.click();

    // 再次确认的后续计划执行至终态（revise_script 第 3 集）
    await waitCardTerminal(page);
  });

  test("重复确认只产生一个 Run", async ({ page }) => {
    // 独立空项目：评估计划无来源快照（不依赖前序产物，规避版本竞态）
    await page.goto("/projects");
    await page.getByRole("link", { name: "+ 创建项目" }).click();
    await page.waitForURL(/\/projects\/new$/);
    await page.locator("#title").fill(makeProjectName());
    await page.getByRole("button", { name: "创建项目" }).click();
    await page.waitForURL(/\/projects\/[0-9a-f-]{36}$/);
    await page.getByLabel("输入创作指令").fill("评估项目");
    await page.getByTestId("composer-send").click();

    // 统计 confirm POST：快速双击（in-flight 防重复）只应发出一次请求
    let confirmPosts = 0;
    page.on("request", (request) => {
      if (request.url().includes("/agent/actions/") && request.url().endsWith("/confirm")) {
        confirmPosts += 1;
      }
    });

    const confirm = page.getByTestId("confirm-action");
    await expect(confirm).toBeVisible({ timeout: 30_000 });
    await confirm.click();
    // 确认中按钮禁用 + in-flight 守卫：短超时二次点击不应发出第二个请求
    await confirm.click({ timeout: 1_500 }).catch(() => undefined);

    // 卡片 data-status 到达任一终态（evaluate Run 很快，进度面板可能一闪而过）
    await expect(
      page.locator(
        'section[aria-label="执行计划"][data-status="completed"], '
        + 'section[aria-label="执行计划"][data-status="needs_review"], '
        + 'section[aria-label="执行计划"][data-status="failed"], '
        + 'section[aria-label="执行计划"][data-status="stale"]',
      ),
    ).toBeVisible({ timeout: 60_000 });
    expect(confirmPosts).toBe(1);
  });
});
