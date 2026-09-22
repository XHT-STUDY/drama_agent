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
  StoryStateResponse,
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

  remove(id: string): Promise<{ deleted: boolean; project_id: string }> {
    return request(`/projects/${id}`, {
      method: "DELETE",
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

  /** 按 ID 读取 Artifact；提供 projectId 时服务端校验归属（跨项目 404，
   * W1-02：工作台固定传当前项目） */
  getById(id: string, projectId?: string): Promise<Artifact> {
    const suffix = projectId ? `?project_id=${projectId}` : "";
    return request(`/artifacts/${id}${suffix}`);
  },

  listVersions(projectId: string, type: string, episode = 1): Promise<Artifact[]> {
    return request(
      `/projects/${projectId}/artifacts?type=${type}&episode=${episode}`,
    ).then((r) => (r as unknown as PaginatedList<Artifact>).items);
  },

  /** 按类型拉取项目下全部 Artifact（跨集、跨版本）。
   *
   * 服务端 total 返回的是当页条数，无法据此判断是否还有下一页，
   * 因此按"当页满页则继续拉"翻页，并设页数上限防御死循环。
   */
  async listAllByType(projectId: string, type: string): Promise<Artifact[]> {
    const pageSize = 100;
    const all: Artifact[] = [];
    let offset = 0;
    for (let page = 0; page < 50; page++) {
      const res = await request<PaginatedList<Artifact>>(
        `/projects/${projectId}/artifacts?type=${encodeURIComponent(type)}&offset=${offset}&limit=${pageSize}`,
      );
      all.push(...res.items);
      if (res.items.length < pageSize) break;
      offset += pageSize;
    }
    return all;
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

  /**
   * 确认门续跑（L-3/L-4，W1-01 幂等收据版）：batch_size 进入批模式
   * （本批集数），缺省写剩余全部。expected_stage_generation 为调用方
   * 所见的 Run 世代（GET Run 返回），idempotency_key 每次用户点击生成
   * 新键——同键同参数重放返回原接受，世代不符返回 409 RUN_STAGE_STALE。
   */
  continueRun(
    runId: string,
    body: { batch_size?: number; expected_stage_generation: number; idempotency_key: string },
  ): Promise<Run> {
    return request(`/runs/${runId}/continue`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
};

// ============================================================
// 剧情状态（M-05：只读，不触发任何生成）
// ============================================================

export const storyStateApi = {
  /** 解析剧情状态；默认当前采用集合，run_id 为该 Run 的冻结工作集 */
  get(
    projectId: string,
    options?: { throughEpisode?: number; runId?: string },
  ): Promise<StoryStateResponse> {
    const params = new URLSearchParams();
    if (options?.throughEpisode) params.set("through_episode", String(options.throughEpisode));
    if (options?.runId) params.set("run_id", options.runId);
    const qs = params.toString();
    return request(`/projects/${projectId}/story-state${qs ? `?${qs}` : ""}`);
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

// ============================================================
// 上传（W1-06：TXT/DOCX 附件 → 导入分类）
// ============================================================

export const uploadsApi = {
  /** 上传并解析 TXT/DOCX（≤10MB；只解析存档，不调模型） */
  async create(projectId: string, file: File): Promise<import("@/types/api").UploadRecord> {
    const form = new FormData();
    form.append("file", file);
    const url = `${API_BASE}/projects/${projectId}/uploads`;
    const response = await fetch(url, { method: "POST", body: form });
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = (data as { detail?: string } | null)?.detail ?? "上传失败";
      const code = (data as { code?: string } | null)?.code ?? "UPLOAD_FAILED";
      throw new Error(`${detail}（${code}）`);
    }
    return data as import("@/types/api").UploadRecord;
  },

  /** 项目上传记录（恢复附件卡） */
  list(projectId: string, offset = 0, limit = 10): Promise<PaginatedList<import("@/types/api").UploadRecord>> {
    return request(
      `/projects/${projectId}/uploads?offset=${offset}&limit=${limit}`,
    );
  },
};

// ============================================================
// 导出（W1-05：固定版本后端导出 + 服务端历史）
// ============================================================

export const exportsApi = {
  /** 发起后端导出：选择在请求接受时冻结为显式 Artifact ID（kind → ID
   * 列表），排队后的新版本不改变本次导出内容 */
  create(
    projectId: string,
    body: {
      kinds: string[];
      format: string;
      artifact_ids?: Record<string, string[]>;
      idempotency_key?: string;
    },
  ): Promise<import("@/types/api").Run> {
    return request(`/projects/${projectId}/exports`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  /** 服务端导出历史：export_file Artifact 分页列表（最新在前） */
  list(
    projectId: string,
    offset = 0,
    limit = 20,
  ): Promise<PaginatedList<import("@/types/api").Artifact>> {
    return request(
      `/projects/${projectId}/exports?offset=${offset}&limit=${limit}`,
    );
  },

  /** 固定下载地址：重下导出时的那份文件（字节一致） */
  downloadUrl(artifactId: string, projectId: string): string {
    return `${API_BASE}/exports/${artifactId}/download?project_id=${projectId}`;
  },
};
