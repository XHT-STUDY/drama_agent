import { defineConfig } from "@playwright/test";

/**
 * DramaAgent E2E 配置 (H-07)。
 *
 * - 全链路 Demo 串行依赖，单 worker 顺序执行
 * - 截图 / trace 仅在失败时保留（验收要求：失败才留产物，避免 CI 磁盘膨胀）
 * - baseURL 指向 `pnpm start` 的宿主前端（3100）；后端由 scripts/e2e.sh 起在 8010
 * - `--repeat-each=N` 由脚本透传，用于「E2E 可重复运行 ≥5 次」验收
 */
export default defineConfig({
  testDir: ".",
  testMatch: "**/*.spec.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://localhost:3100",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    locale: "zh-CN",
  },
  outputDir: "./test-results",
});
