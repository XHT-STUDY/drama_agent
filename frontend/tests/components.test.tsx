/** 组件单元测试 (H-01).
 *
 * 测试 Loading、ErrorMessage、Empty 三个通用组件。
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";

// ---- Mock next/link ----
vi.mock("next/link", () => ({
  default: function MockLink({
    children,
    href,
  }: {
    children: React.ReactNode;
    href: string;
  }) {
    return React.createElement("a", { href }, children);
  },
}));

import { Loading } from "@/components/Loading";
import { ErrorMessage } from "@/components/ErrorMessage";
import { Empty } from "@/components/Empty";

describe("Loading", () => {
  it("显示默认文案", () => {
    render(React.createElement(Loading));
    expect(screen.getByText("加载中…")).toBeTruthy();
  });

  it("显示自定义文案", () => {
    render(React.createElement(Loading, { text: "正在获取数据…" }));
    expect(screen.getByText("正在获取数据…")).toBeTruthy();
  });
});

describe("ErrorMessage", () => {
  it("显示一般错误消息", () => {
    const err = new Error("网络连接失败");
    render(React.createElement(ErrorMessage, { error: err }));
    expect(screen.getByText("网络连接失败")).toBeTruthy();
  });

  it("显示 ApiError 的 request_id", () => {
    const apiErr = {
      name: "ApiError",
      message: "项目不存在",
      requestId: "req-test-456",
      code: "PROJECT_NOT_FOUND",
      detail: "项目不存在: xxx",
    } as unknown as Error;
    render(React.createElement(ErrorMessage, { error: apiErr }));
    expect(screen.getByText(/req-test-456/)).toBeTruthy();
  });

  it("显示重试按钮", () => {
    const err = new Error("请求失败");
    render(React.createElement(ErrorMessage, { error: err, onRetry: () => undefined }));
    expect(screen.getByText("重试")).toBeTruthy();
  });
});

describe("Empty", () => {
  it("显示标题和描述", () => {
    render(
      React.createElement(Empty, {
        title: "没有数据",
        description: "请先创建项目",
      }),
    );
    expect(screen.getByText("没有数据")).toBeTruthy();
    expect(screen.getByText("请先创建项目")).toBeTruthy();
  });

  it("有操作链接时显示按钮", () => {
    render(
      React.createElement(Empty, {
        title: "还没有项目",
        actionLabel: "创建项目",
        actionHref: "/projects/new",
      }),
    );
    const link = screen.getByText("创建项目");
    expect(link).toBeTruthy();
  });
});
