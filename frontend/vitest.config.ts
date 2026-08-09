import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
    coverage: {
      provider: "v8",
      reporter: ["text", "html"],
      reportsDirectory: "./coverage",
      include: ["src/**"],
      thresholds: {
        lines: 0,
        branches: 0,
        functions: 0,
        statements: 0,
      },
    },
  },
  resolve: {
    alias: [
      { find: "@", replacement: path.resolve(__dirname, "./src") },
      // 统一 react 到【仓库根】的那一份（workspace root，与 @testing-library/react
      // 加载的 react-dom 同源）。根与 frontend 各有一份 react@19.2.8；@testing-library/react
      // 从根解析 → 根 react-dom（externalized CJS，运行时 require 根 react）。
      // 若测试 import 的 react 走 frontend 副本 → React 19 ReactSharedInternals.H
      // 不共享 → "Invalid hook call"。这里把 react 系全部 alias 到根副本对齐。
      // 注意：数组形式保证「更具体前缀在前」，否则 find:"react" 会抢先吞掉
      // react/jsx-dev-runtime 等子路径。react 为 CJS，需 alias 到具体文件。
      { find: "react-dom/client", replacement: path.resolve(__dirname, "../node_modules/.pnpm/react-dom@19.2.8_react@19.2.8/node_modules/react-dom/client.js") },
      { find: "react-dom", replacement: path.resolve(__dirname, "../node_modules/.pnpm/react-dom@19.2.8_react@19.2.8/node_modules/react-dom/index.js") },
      { find: "react/jsx-dev-runtime", replacement: path.resolve(__dirname, "../node_modules/.pnpm/react@19.2.8/node_modules/react/jsx-dev-runtime.js") },
      { find: "react/jsx-runtime", replacement: path.resolve(__dirname, "../node_modules/.pnpm/react@19.2.8/node_modules/react/jsx-runtime.js") },
      { find: "react", replacement: path.resolve(__dirname, "../node_modules/.pnpm/react@19.2.8/node_modules/react/index.js") },
    ],
  },
});
