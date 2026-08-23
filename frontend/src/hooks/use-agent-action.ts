"use client";

/** useAgentAction — AgentAction 查询/确认/拒绝 Hook（J-10）。
 *
 * 职责：
 * - Action 轮询：非终态时按间隔 refetch，终态自动停止（卸载即停）
 * - confirm 防重复点击：in-flight 期间忽略再次触发
 * - 重复确认返回原 Run 时不创建重复本地状态（只认服务端返回的 run_id）
 * - 409 ACTION_STALE → isStale 可恢复错误（保留计划供用户重新发起规划）
 * - 确认/拒绝后失效 Action 与消息查询
 * - useAgentActionEvents：SSE 订阅 agent_action.updated（runId 可用时）
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { agentApi, ApiError } from "@/lib/api-client";
import type { AgentConfirmResponse } from "@/types/api";

const NON_TERMINAL_ACTION_STATUSES = new Set(["proposed", "queued", "running"]);

export interface UseAgentActionResult {
  action: import("@/types/api").AgentActionResponse | null;
  isLoading: boolean;
  error: string | null;
  confirm: () => Promise<AgentConfirmResponse | null>;
  confirming: boolean;
  /** 409 ACTION_STALE：计划已过期，可恢复（重新发起规划），输入/计划保留 */
  isStale: boolean;
  confirmError: string | null;
  reject: () => Promise<void>;
  rejecting: boolean;
}

export function useAgentAction(
  actionId: string | null,
  options?: { pollIntervalMs?: number },
): UseAgentActionResult {
  const { pollIntervalMs = 2000 } = options ?? {};
  const queryClient = useQueryClient();
  const [isStale, setIsStale] = useState(false);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const confirmInFlightRef = useRef(false);

  const actionQuery = useQuery({
    queryKey: ["agent-action", actionId],
    enabled: actionId !== null,
    queryFn: () => agentApi.getAction(actionId!),
    // 非终态轮询；终态（completed/failed/...）自动停止，组件卸载即停
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && NON_TERMINAL_ACTION_STATUSES.has(status) ? pollIntervalMs : false;
    },
  });

  const invalidateAll = useCallback(() => {
    if (actionId) {
      void queryClient.invalidateQueries({ queryKey: ["agent-action", actionId] });
    }
    void queryClient.invalidateQueries({ queryKey: ["agent-messages"] });
  }, [actionId, queryClient]);

  const confirmMutation = useMutation({
    mutationFn: async (): Promise<AgentConfirmResponse> => {
      // 防重复点击：in-flight 期间的再次触发直接忽略
      if (confirmInFlightRef.current || actionId === null) {
        throw new ApiError(409, {
          request_id: "",
          detail: "确认请求进行中",
          code: "CONFIRM_IN_FLIGHT",
          path: `/agent/actions/${actionId}/confirm`,
          timestamp: new Date().toISOString(),
        });
      }
      confirmInFlightRef.current = true;
      try {
        return await agentApi.confirm(actionId);
      } finally {
        confirmInFlightRef.current = false;
      }
    },
    onSuccess: () => {
      setIsStale(false);
      setConfirmError(null);
      invalidateAll();
    },
    onError: (error: unknown) => {
      if (error instanceof ApiError && error.code === "ACTION_STALE") {
        setIsStale(true);
        setConfirmError("计划基于的内容已更新，请重新发起规划");
      } else {
        const detail =
          error instanceof ApiError
            ? `${error.detail}（${error.code}）`
            : "确认失败，请重试";
        setConfirmError(detail);
      }
    },
  });

  const rejectMutation = useMutation({
    mutationFn: async () => {
      if (actionId !== null) await agentApi.reject(actionId);
    },
    onSuccess: invalidateAll,
  });

  const confirm = useCallback(async () => {
    if (confirmInFlightRef.current) return null; // 防重复点击
    try {
      return await confirmMutation.mutateAsync();
    } catch {
      return null;
    }
  }, [confirmMutation]);

  return {
    action: actionQuery.data ?? null,
    isLoading: actionQuery.isLoading,
    error:
      actionQuery.error instanceof ApiError
        ? actionQuery.error.detail
        : actionQuery.error
          ? String(actionQuery.error)
          : null,
    confirm,
    confirming: confirmMutation.isPending,
    isStale,
    confirmError,
    reject: async () => {
      await rejectMutation.mutateAsync();
    },
    rejecting: rejectMutation.isPending,
  };
}

/** SSE 订阅 agent_action.updated（Run 确认后可用的实时通道，J-09 事件）。 */
export function useAgentActionEvents(
  runId: string | null,
  onUpdated: (payload: { agent_action_id?: string; status?: string; goal_status?: string }) => void,
): void {
  const callbackRef = useRef(onUpdated);
  callbackRef.current = onUpdated;

  useEffect(() => {
    if (runId === null) return;
    const base = (
      process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000/api/v1"
    ).replace(/\/$/, "");
    const source = new EventSource(`${base}/runs/${runId}/events`);
    const handler = (event: MessageEvent<string>) => {
      try {
        const parsed = JSON.parse(event.data) as { event_type?: string; payload?: Record<string, unknown> };
        if (parsed.event_type === "agent_action.updated" && parsed.payload) {
          callbackRef.current(parsed.payload as Parameters<typeof onUpdated>[0]);
        }
      } catch {
        // 非 JSON 帧忽略（心跳/注释行）
      }
    };
    source.addEventListener("message", handler as EventListener);
    return () => source.close(); // 卸载即断开
  }, [runId]);
}
