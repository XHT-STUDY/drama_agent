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
  AgentActionResponse,
  AgentConfirmResponse,
  AgentTurnCreate,
  AgentTurnResponse,
  Artifact,
  Conversation,
  ConversationCreate,
  ConversationListResponse,
  CreateRevisionRequest,
  CreateRunRequest,
  ErrorResponse,
  KnowledgeListResponse,
  KnowledgeSearchResponse,
  MessageListResponse,
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

  /** 确认门续跑（L-3/L-4）：batch_size 进入批模式（本批集数），缺省写剩余全部 */
  continueRun(runId: string, body?: { batch_size?: number }): Promise<Run> {
    return request(`/runs/${runId}/continue`, {
      method: "POST",
      body: JSON.stringify(body ?? {}),
    });
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

// ============================================================
// 会话与消息 — J-10
// ============================================================

export const conversationsApi = {
  /** 在项目下创建会话 */
  create(projectId: string, data: ConversationCreate): Promise<Conversation> {
    return request(`/projects/${projectId}/conversations`, {
      method: "POST",
      body: JSON.stringify(data),
    });
  },

  /** 按项目分页查询会话列表 */
  list(
    projectId: string,
    offset = 0,
    limit = 20,
  ): Promise<ConversationListResponse> {
    const params = new URLSearchParams({
      offset: String(offset),
      limit: String(limit),
    });
    return request(
      `/projects/${projectId}/conversations?${params.toString()}`,
    );
  },

  /** 按会话分页查询消息（sequence 升序） */
  messages(
    conversationId: string,
    offset = 0,
    limit = 50,
  ): Promise<MessageListResponse> {
    const params = new URLSearchParams({
      offset: String(offset),
      limit: String(limit),
    });
    return request(
      `/conversations/${conversationId}/messages?${params.toString()}`,
    );
  },
};

// ============================================================
// 对话式 Agent — J-10
// ============================================================

/** createTurn 的结果：202 表示 Turn 仍在他人租约下规划中 */
export interface AgentTurnRequestResult {
  status: number;
  data: AgentTurnResponse;
}

async function requestWithStatus<T>(path: string, options: RequestInit = {}): Promise<{ status: number; data: T }> {
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
  return { status: res.status, data: (await res.json()) as T };
}

export const agentApi = {
  /** 创建 Turn（200 终态 / 202 规划中）；幂等键由调用方生成与复用 */
  async createTurn(
    projectId: string,
    body: AgentTurnCreate,
  ): Promise<AgentTurnRequestResult> {
    return requestWithStatus<AgentTurnResponse>(
      `/projects/${projectId}/agent/turns`,
      { method: "POST", body: JSON.stringify(body) },
    );
  },

  /** 查询 Turn 快照（planning 202 轮询 / 终态确认） */
  getTurn(turnId: string): Promise<AgentTurnResponse> {
    return request(`/agent/turns/${turnId}`);
  },

  /** 查询 Action 快照（状态轮询；GET 兼带 J-09 reconciliation） */
  getAction(actionId: string): Promise<AgentActionResponse> {
    return request(`/agent/actions/${actionId}`);
  },

  /** 确认 proposed Action（重复确认返回原 Run，不创建新 Run） */
  confirm(actionId: string): Promise<AgentConfirmResponse> {
    return request(`/agent/actions/${actionId}/confirm`, { method: "POST" });
  },

  /** 拒绝 proposed Action（仅 proposed→rejected） */
  reject(actionId: string): Promise<AgentActionResponse> {
    return request(`/agent/actions/${actionId}/reject`, { method: "POST" });
  },
};

// ============================================================
// 知识库（K-3）
// ============================================================

export const knowledgeApi = {
  /** 项目可见知识文档列表（自有 + 全局语料；scope 过滤） */
  list(
    projectId: string,
    scope: "project" | "global" | "all" = "all",
  ): Promise<KnowledgeListResponse> {
    return request(
      `/projects/${projectId}/knowledge?scope=${scope}`,
    );
  },

  /** 软删除项目自有文档（全局语料不可删） */
  delete(projectId: string, documentId: string): Promise<{ deleted: boolean }> {
    return request(
      `/projects/${projectId}/knowledge/${documentId}`,
      { method: "DELETE" },
    );
  },

  /** 检索试算（项目作用域：自有 + 全局语料） */
  search(
    projectId: string,
    body: { query: string; top_k?: number; category?: string | null },
  ): Promise<KnowledgeSearchResponse> {
    return request(`/projects/${projectId}/knowledge/search`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
};
