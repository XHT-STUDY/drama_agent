"use client";

/** useAgentConversation — 对话式创作的会话/消息/发送 Hook（J-10）。
 *
 * 职责：
 * - 管理当前会话（外部指定 conversationId，或首条消息自动创建）
 * - 分页加载消息（sequence 升序 + loadMore）
 * - 发送 Turn：客户端幂等 key（失败重发复用同一 key → 服务端返回原 Turn）
 * - 202 planning 轮询直至终态；终态后失效消息查询
 * - 终态产出 action_id 时失效对应 Action 查询（供 useAgentAction 消费）
 *
 * 模块边界：纯数据 Hook，不渲染 UI；输入框草稿由调用方保留
 * （sendTurn 失败时通过 lastFailedContent 交还调用方恢复）。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { agentApi, ApiError, conversationsApi } from "@/lib/api-client";
import type {
  ActiveArtifactContext,
  AgentTurnResponse,
  ChatMessage,
} from "@/types/api";

const TERMINAL_TURN_STATUSES = new Set([
  "needs_input",
  "answered",
  "action_proposed",
  "failed",
]);

export interface UseAgentConversationOptions {
  projectId: string;
  /** 指定会话；为空时首条消息自动创建会话并回填 */
  conversationId?: string | null;
  pollIntervalMs?: number;
  maxPolls?: number;
}

export interface UseAgentConversationResult {
  conversationId: string | null;
  messages: ChatMessage[];
  total: number;
  hasMore: boolean;
  loadMore: () => void;
  isLoading: boolean;
  sendTurn: (
    content: string,
    activeContext?: ActiveArtifactContext | null,
    options?: { episodeCount?: number },
  ) => Promise<AgentTurnResponse | null>;
  sending: boolean;
  /** 发送中尚未上屏的用户消息（乐观渲染，让消息流呈现聊天感） */
  pendingContent: string | null;
  sendError: string | null;
  /** 发送失败时保留的用户输入（调用方用于恢复输入框草稿） */
  lastFailedContent: string | null;
  /** 最近一次终态 Turn 产出的 Action（proposed 计划等待确认） */
  activeActionId: string | null;
  lastTurn: AgentTurnResponse | null;
}

function generateIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `turn-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export function useAgentConversation(
  options: UseAgentConversationOptions,
): UseAgentConversationResult {
  const {
    projectId,
    conversationId: externalConversationId = null,
    pollIntervalMs = 1500,
    maxPolls = 40,
  } = options;

  const queryClient = useQueryClient();
  const [conversationId, setConversationId] = useState<string | null>(
    externalConversationId,
  );
  const [sendError, setSendError] = useState<string | null>(null);
  const [lastFailedContent, setLastFailedContent] = useState<string | null>(null);
  const [pendingContent, setPendingContent] = useState<string | null>(null);
  const [activeActionId, setActiveActionId] = useState<string | null>(null);
  const [lastTurn, setLastTurn] = useState<AgentTurnResponse | null>(null);
  const [pageLimit, setPageLimit] = useState(50);

  useEffect(() => {
    setConversationId(externalConversationId);
    // 会话切换不残留另一会话的失败内容/发送中状态/活动计划（W1-02）
    setSendError(null);
    setLastFailedContent(null);
    setActiveActionId(null);
    setLastTurn(null);
    setPendingContent(null);
  }, [externalConversationId]);

  // 失败重发复用同一幂等 key：同一逻辑发送不会被重复扣费/重复建 Turn
  const pendingKeyRef = useRef<{ content: string; key: string } | null>(null);

  const messagesQuery = useQuery({
    queryKey: ["agent-messages", conversationId],
    enabled: conversationId !== null,
    queryFn: async () => {
      const first = await conversationsApi.messages(conversationId!, 0, pageLimit);
      // 分页：剩余更早的消息按页向前拼接（最新一页在尾部）
      if (first.total > first.items.length) {
        const rest = await conversationsApi.messages(
          conversationId!,
          first.items.length,
          first.total - first.items.length,
        );
        return { ...first, items: [...rest.items, ...first.items] };
      }
      return first;
    },
  });

  const invalidateConversation = useCallback(
    (convId: string | null) => {
      void queryClient.invalidateQueries({ queryKey: ["agent-messages", convId] });
    },
    [queryClient],
  );

  const pollTurnUntilTerminal = useCallback(
    async (turnId: string): Promise<AgentTurnResponse> => {
      for (let i = 0; i < maxPolls; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
        const turn = await agentApi.getTurn(turnId);
        if (TERMINAL_TURN_STATUSES.has(turn.status)) return turn;
      }
      throw new ApiError(504, {
        request_id: "",
        detail: "Turn 规划超时，请稍后重试查看结果",
        code: "TURN_POLL_TIMEOUT",
        path: `/agent/turns/${turnId}`,
        timestamp: new Date().toISOString(),
      });
    },
    [maxPolls, pollIntervalMs],
  );

  const sendMutation = useMutation({
    mutationFn: async ({
      content,
      activeContext,
      episodeCount,
    }: {
      content: string;
      activeContext?: ActiveArtifactContext | null;
      episodeCount?: number;
    }) => {
      // 失败重发相同内容时复用原 key → 服务端返回原 Turn（幂等收据）
      const pending = pendingKeyRef.current;
      const key =
        pending && pending.content === content
          ? pending.key
          : generateIdempotencyKey();
      pendingKeyRef.current = { content, key };

      let targetConversationId = conversationId;
      if (targetConversationId === null) {
        const created = await conversationsApi.create(projectId, {
          title: content.slice(0, 30),
        });
        targetConversationId = created.id;
        setConversationId(created.id);
      }

      const { status, data } = await agentApi.createTurn(projectId, {
        conversation_id: targetConversationId,
        content,
        active_context: activeContext ?? null,
        idempotency_key: key,
        target_episode_count: episodeCount ?? null,
      });
      if (status === 202 && !TERMINAL_TURN_STATUSES.has(data.status)) {
        return pollTurnUntilTerminal(data.id);
      }
      return data;
    },
    onSuccess: (turn) => {
      pendingKeyRef.current = null;
      setSendError(null);
      setLastFailedContent(null);
      setLastTurn(turn);
      if (turn.action_id) {
        setActiveActionId(turn.action_id);
        void queryClient.invalidateQueries({
          queryKey: ["agent-action", turn.action_id],
        });
      }
      invalidateConversation(turn.conversation_id);
    },
    onError: (error: unknown) => {
      // 保留输入与幂等 key，供用户重发
      const detail =
        error instanceof ApiError
          ? `${error.detail}（${error.code}）`
          : "发送失败，请检查网络后重试";
      setSendError(detail);
    },
  });

  // 同一逻辑发送的并发重复提交守卫（双击/双 Enter）：in-flight 期间忽略，
  // 与 useAgentAction.confirm 的防重复策略一致。
  const sendInFlightRef = useRef(false);
  const sendTurn = useCallback(
    async (
      content: string,
      activeContext?: ActiveArtifactContext | null,
      options?: { episodeCount?: number },
    ) => {
      if (sendInFlightRef.current) return null;
      sendInFlightRef.current = true;
      // 乐观上屏：消息流立即显示用户消息与"正在思考"气泡（聊天感）
      setPendingContent(content);
      try {
        return await sendMutation.mutateAsync({
          content,
          activeContext,
          episodeCount: options?.episodeCount,
        });
      } finally {
        sendInFlightRef.current = false;
        setPendingContent(null);
      }
    },
    [sendMutation],
  );

  // 失败时记录原始输入（在 mutateAsync reject 之后由本 effect 捕获变量）
  useEffect(() => {
    if (sendMutation.isError && pendingKeyRef.current) {
      setLastFailedContent(pendingKeyRef.current.content);
    }
  }, [sendMutation.isError]);

  const messages = useMemo(
    () => messagesQuery.data?.items ?? [],
    [messagesQuery.data],
  );

  return {
    conversationId,
    messages,
    total: messagesQuery.data?.total ?? 0,
    hasMore: (messagesQuery.data?.total ?? 0) > messages.length,
    loadMore: () => setPageLimit((n) => n + 50),
    isLoading: messagesQuery.isLoading,
    sendTurn,
    sending: sendMutation.isPending,
    pendingContent,
    sendError,
    lastFailedContent,
    activeActionId,
    lastTurn,
  };
}
