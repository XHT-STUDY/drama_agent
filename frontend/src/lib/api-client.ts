/** API Client — 后端 HTTP 请求封装 (H-01).
 *
 * 职责：
 * - 统一拼接 API base URL
 * - 自动 JSON 序列化/反序列化
 * - 统一错误处理，提取 request_id
 *
 * 模块边界：仅封装 HTTP 调用，不包含业务逻辑。
 */

import type {
  Artifact,
  CreateRevisionRequest,
  CreateRunRequest,
  ErrorResponse,
  PaginatedList,
  Project,
  ProjectCreate,
  RevisionPlanArtifact,
  Run,
  ScriptDiff,
} from "@/types/api";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000/api/v1";

/** 自定义错误类，携带 API 错误详情 */
export class ApiError extends Error {
  requestId: string;
  code: string;
  statusCode: number;
  detail: string;

  constructor(statusCode: number, err: ErrorResponse) {
    super(err.detail || `API 错误 (HTTP ${statusCode})`);
    this.name = "ApiError";
    this.statusCode = statusCode;
    this.requestId = err.request_id || "";
    this.code = err.code || "";
    this.detail = err.detail || "";
  }
}

/** 内部 fetch 封装 */
async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const url = `${API_BASE}${path}`;
  const headers: HeadersInit = {
    "Content-Type": "application/json",
    ...options.headers,
  };

  const res = await fetch(url, { ...options, headers });

  if (!res.ok) {
    let err: ErrorResponse;
    try {
      err = await res.json();
    } catch {
      err = {
        request_id: "",
        detail: `HTTP ${res.status} ${res.statusText}`,
        code: "NETWORK_ERROR",
        path,
        timestamp: new Date().toISOString(),
      };
    }
    throw new ApiError(res.status, err);
  }

  // 204 No Content
  if (res.status === 204) {
    return undefined as unknown as T;
  }

  return res.json();
}

// ============================================================
// 项目
// ============================================================

export const projectsApi = {
  list(offset?: number, limit?: number): Promise<PaginatedList<Project>> {
    const params = new URLSearchParams();
    if (offset !== undefined) params.set("offset", String(offset));
    if (limit !== undefined) params.set("limit", String(limit));
    const qs = params.toString();
    return request(`/projects${qs ? `?${qs}` : ""}`);
  },

  get(id: string): Promise<Project> {
    return request(`/projects/${id}`);
  },

  create(data: ProjectCreate): Promise<Project> {
    return request("/projects", {
      method: "POST",
      body: JSON.stringify(data),
    });
  },

  update(id: string, data: Partial<ProjectCreate>): Promise<Project> {
    return request(`/projects/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    });
  },
};

// ============================================================
// Artifact
// ============================================================

export const artifactsApi = {
  getLatest(
    projectId: string,
    type: string,
    episode = 1,
  ): Promise<Artifact> {
    return request(
      `/projects/${projectId}/artifacts/latest?type=${type}&episode=${episode}`,
    );
  },

  getById(id: string): Promise<Artifact> {
    return request(`/artifacts/${id}`);
  },

  listVersions(projectId: string, type: string, episode = 1): Promise<Artifact[]> {
    return request(
      `/projects/${projectId}/artifacts?type=${type}&episode=${episode}`,
    ).then((r) => (r as unknown as PaginatedList<Artifact>).items);
  },

  getLinks(artifactId: string): Promise<Array<{ id: string; source_id: string; target_id: string; relation: string }>> {
    return request(`/artifacts/${artifactId}/links`);
  },

  diff(fromId: string, toId: string): Promise<ScriptDiff> {
    return request(
      `/artifacts/diff?from_artifact_id=${encodeURIComponent(fromId)}&to_artifact_id=${encodeURIComponent(toId)}`,
    );
  },
};

// ============================================================
// 修订 (Revision)
// ============================================================

export const revisionsApi = {
  create(projectId: string, body: CreateRevisionRequest): Promise<Run> {
    return request(`/projects/${projectId}/revisions`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  list(projectId: string): Promise<PaginatedList<Artifact>> {
    return request(`/projects/${projectId}/revisions`);
  },

  get(projectId: string, planId: string): Promise<RevisionPlanArtifact> {
    return request(`/projects/${projectId}/revisions/${planId}`);
  },
};

// ============================================================
// Run
// ============================================================

export const runsApi = {
  create(projectId: string, data: CreateRunRequest): Promise<Run> {
    return request(`/projects/${projectId}/runs`, {
      method: "POST",
      body: JSON.stringify(data),
    });
  },

  get(runId: string): Promise<Run> {
    return request(`/runs/${runId}`);
  },

  listByProject(projectId: string): Promise<PaginatedList<Run>> {
    return request(`/projects/${projectId}/runs`);
  },

  cancel(runId: string): Promise<Run> {
    return request(`/runs/${runId}/cancel`, { method: "POST" });
  },
};

// ============================================================
// 健康检查
// ============================================================

export const healthApi = {
  live(): Promise<{ status: string }> {
    return request("/health/live");
  },
  ready(): Promise<{ status: string; checks: unknown[] }> {
    return request("/health/ready");
  },
};
