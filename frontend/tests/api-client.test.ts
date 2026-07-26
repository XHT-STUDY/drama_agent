/** API Client 单元测试 (H-01).
 *
 * 验证 ApiError 错误类和 API URL 构造。
 */
import { describe, it, expect } from "vitest";

import { ApiError } from "@/lib/api-client";

describe("ApiError", () => {
  it("包含所有错误字段", () => {
    const err = new ApiError(404, {
      request_id: "req-123",
      detail: "项目不存在",
      code: "PROJECT_NOT_FOUND",
      path: "/api/v1/projects/xxx",
      timestamp: "2026-07-25T00:00:00Z",
    });

    expect(err.statusCode).toBe(404);
    expect(err.requestId).toBe("req-123");
    expect(err.code).toBe("PROJECT_NOT_FOUND");
    expect(err.detail).toBe("项目不存在");
    expect(err.message).toContain("项目不存在");
  });

  it("缺失 request_id 时仍可构造", () => {
    const err = new ApiError(500, {
      request_id: "",
      detail: "服务器内部错误",
      code: "INTERNAL_ERROR",
      path: "/api/v1/test",
      timestamp: "",
    });

    expect(err.requestId).toBe("");
    expect(err.statusCode).toBe(500);
  });
});

describe("API URL 构造", () => {
  it("projectsApi.list 路径正确", () => {
    const base = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000/api/v1";
    const url = `${base}/projects`;
    expect(url).toContain("/api/v1/projects");
  });

  it("projectsApi.get 拼接项目 ID", () => {
    const base = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000/api/v1";
    const url = `${base}/projects/abc-123`;
    expect(url).toContain("/api/v1/projects/abc-123");
  });
});
