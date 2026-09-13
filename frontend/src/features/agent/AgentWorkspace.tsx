"use client";

/** AgentWorkspace — 对话式创作工作台（J-11，DESIGN §13）。
 *
 * 双栏布局：左栏会话（选择/消息/Composer/内嵌 RunProgress），
 * 右栏项目上下文（产物索引 + active context + ActionPlanCard）。
 * 平板以下右栏收进抽屉；移动端单栏 + Composer 常驻底部。
 *
 * 两种模式共用后端 API 与 Artifact——本组件不复制任何状态，
 * Run 进度复用 useRunEvents，计划卡复用 useAgentAction。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { artifactsApi, conversationsApi, runsApi } from "@/lib/api-client";
import { useAgentConversation } from "@/hooks/use-agent-conversation";
import { useAgentActionEvents } from "@/hooks/use-agent-action";
import { useRunEvents } from "@/hooks/use-run-events";
import { ActionPlanCard } from "./ActionPlanCard";
import { AgentComposer } from "./AgentComposer";
import { ArtifactContextPanel } from "./ArtifactContextPanel";
import { ConversationPanel } from "./ConversationPanel";
import { MessageList } from "./MessageList";
import { RunProgress } from "@/features/runs/RunProgress";
import type { ActiveArtifactContext, Project } from "@/types/api";

interface Props {
  projectId: string;
  project: Project;
}

export function AgentWorkspace({ projectId, project }: Props) {
  const queryClient = useQueryClient();
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [activeContext, setActiveContext] = useState<ActiveArtifactContext | null>(null);
  const [composerFocusSignal, setComposerFocusSignal] = useState(0);
  const [drawerOpen, setDrawerOpen] = useState(false);

  // 会话列表（选择/新建）
  const conversationsQuery = useQuery({
    queryKey: ["agent-conversations", projectId],
    queryFn: () => conversationsApi.list(projectId, 0, 50),
  });
  const currentConversationId =
    selectedConversationId ?? conversationsQuery.data?.items[0]?.id ?? null;

  const createConversation = useMutation({
    mutationFn: () => conversationsApi.create(projectId, { title: "新会话" }),
    onSuccess: (created) => {
      setSelectedConversationId(created.id);
      void queryClient.invalidateQueries({ queryKey: ["agent-conversations", projectId] });
    },
  });

  const conversation = useAgentConversation({
    projectId,
    conversationId: currentConversationId,
  });

  const focusComposer = useCallback(() => {
    setComposerFocusSignal((n) => n + 1);
  }, []);

  const handleSend = useCallback(
    (content: string, options?: { episodeCount?: number }) => {
      void conversation.sendTurn(content, activeContext, options);
    },
    [conversation, activeContext],
  );

  // 待确认/执行中的计划：最近一条 action_plan 消息或刚产出的 Turn Action
  const focusActionId = useMemo(() => {
    if (conversation.activeActionId) return conversation.activeActionId;
    const planMessages = conversation.messages.filter((m) => m.kind === "action_plan");
    const last = planMessages[planMessages.length - 1];
    const actionId = (last?.metadata as { agent_action_id?: string } | undefined)?.agent_action_id;
    return actionId ?? null;
  }, [conversation.activeActionId, conversation.messages]);

  // 澄清轮 → 聚焦输入框回答
  const lastTurnStatus = conversation.lastTurn?.status;
  useEffect(() => {
    if (lastTurnStatus === "needs_input") focusComposer();
  }, [lastTurnStatus, focusComposer]);

  // 计划执行中（queued/running 且有 run_id）→ 内嵌 RunProgress
  const actionForRun = useQuery({
    queryKey: ["agent-action", focusActionId],
    enabled: focusActionId !== null,
    queryFn: async () => {
      const { agentApi } = await import("@/lib/api-client");
      return agentApi.getAction(focusActionId!);
    },
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" ? 2000 : false;
    },
  });
  // L-3/L-4：Action 在门后停在 needs_review 不再变——执行中判定改由 Run 驱动
  //（gated-run 查询与 ActionPlanCard 共享缓存；续跑后 RunProgress 随之出现）。
  const gatedRunId = actionForRun.data?.run_id ?? null;
  const gatedRun = useQuery({
    queryKey: ["gated-run", gatedRunId],
    enabled: gatedRunId !== null,
    queryFn: () => runsApi.get(gatedRunId!),
    refetchInterval: (query) =>
      query.state.data?.status === "queued" || query.state.data?.status === "running"
        ? 2000
        : false,
  });
  const activeRunId =
    gatedRun.data && (gatedRun.data.status === "queued" || gatedRun.data.status === "running")
      ? gatedRunId
      : null;
  const runEvents = useRunEvents(activeRunId);

  // 终态自愈：页面刷新可能落在「Run 终态已提交、结果消息尚未提交」的
  // 竞态窗口内，此时 Run/Action 轮询都已停止且无实时事件——SSE 对新连接
  // 始终重放历史，靠它补收错过的 agent_action.updated 并失效缓存追平
  //（无新事件时重放只会触发一次无害的刷新）。
  const onActionUpdated = useCallback(
    (payload: { agent_action_id?: string; status?: string; goal_status?: string }) => {
      if (payload.agent_action_id && payload.agent_action_id !== focusActionId) return;
      void queryClient.invalidateQueries({ queryKey: ["agent-action"] });
      void queryClient.invalidateQueries({ queryKey: ["agent-messages"] });
    },
    [focusActionId, queryClient],
  );
  useAgentActionEvents(gatedRunId, onActionUpdated);

  // 右栏产物索引随 Run 状态变化自动刷新（门上生成 SB/大纲后立即可见）
  const [contextRefresh, setContextRefresh] = useState(0);
  useEffect(() => {
    setContextRefresh((n) => n + 1);
  }, [gatedRun.data?.status, conversation.activeActionId]);

  // 大纲门：自动把最新大纲设为活动上下文——用户可直接在对话里
  // 提修改意见（如"把第 2 集冲突提前"），无需先去右栏手动选中。
  const outlineAtGate = useQuery({
    queryKey: ["gate-outline", projectId],
    queryFn: () => artifactsApi.getLatest(projectId, "episode_outline_set"),
    enabled: gatedRun.data?.stage_gate === "outline",
    retry: false,
  });
  useEffect(() => {
    if (
      gatedRun.data?.stage_gate === "outline" &&
      outlineAtGate.data &&
      !activeContext
    ) {
      setActiveContext({
        artifact_id: outlineAtGate.data.id,
        artifact_type: "episode_outline_set",
        episode_number: outlineAtGate.data.episode_number,
        version: outlineAtGate.data.version,
        checksum: outlineAtGate.data.checksum ?? null,
      });
    }
  }, [gatedRun.data?.stage_gate, outlineAtGate.data, activeContext]);

  // 消息流里已经内嵌的计划卡不在右栏重复渲染；右栏只兜底展示
  // 刚由 Turn 产出、消息列表尚未刷新的计划。
  const planMessageActionIds = useMemo(() => {
    const ids = new Set<string>();
    for (const m of conversation.messages) {
      if (m.kind === "action_plan") {
        const id = (m.metadata as { agent_action_id?: string }).agent_action_id;
        if (id) ids.add(id);
      }
    }
    return ids;
  }, [conversation.messages]);

  const rightPanel = (
    <>
      <ArtifactContextPanel
        projectId={projectId}
        activeContext={activeContext}
        onActiveContextChange={setActiveContext}
        refreshSignal={contextRefresh}
      />
      {focusActionId && !planMessageActionIds.has(focusActionId) && (
        <ActionPlanCard
          actionId={focusActionId}
          projectId={projectId}
          onAskAgain={() => {
            setDrawerOpen(false);
            focusComposer();
          }}
        />
      )}
    </>
  );

  // 首条消息发送中也要渲染消息流（乐观气泡），不能落在空状态占位上
  const emptyConversation =
    conversation.messages.length === 0 && !conversation.pendingContent;
  // 新消息/发送中自动滚动到底部（loadMore 前拼页不改变最后一条 id，不触发）
  const scrollRef = useRef<HTMLDivElement>(null);
  const lastMessageId = conversation.messages[conversation.messages.length - 1]?.id;
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lastMessageId, conversation.pendingContent]);

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
      {/* 左栏：会话（桌面 7 列 / 1024-1279 8 列） */}
      <div className="lg:col-span-7 xl:col-span-7">
        <ConversationPanel
          conversations={conversationsQuery.data?.items ?? []}
          currentId={currentConversationId}
          onSelect={setSelectedConversationId}
          onCreate={() => createConversation.mutate()}
          creating={createConversation.isPending}
        >
          {/* 消息流 */}
          <div
            ref={scrollRef}
            className="max-h-[52vh] min-h-48 overflow-y-auto rounded-lg bg-[var(--surface-muted)] p-3"
          >
            {conversation.isLoading ? (
              <p className="text-xs text-[var(--text-muted)]">正在加载消息…</p>
            ) : emptyConversation ? (
              <div className="py-8 text-center" data-testid="empty-conversation">
                <p className="message-body text-[var(--text-muted)]">
                  和 Agent 对话完成创作：点击下方示例或直接输入需求。
                </p>
                <p className="mt-1 text-xs text-[var(--text-muted)]">
                  支持创建剧本、解释项目、评估、修改剧本与大纲。
                </p>
              </div>
            ) : (
              <MessageList
                messages={conversation.messages}
                projectId={projectId}
                pendingContent={conversation.pendingContent}
                renderPlanCard={(actionId) => (
                  <ActionPlanCard
                    actionId={actionId}
                    projectId={projectId}
                    onAskAgain={focusComposer}
                  />
                )}
              />
            )}
          </div>

          {/* 内嵌 Run 进度（计划执行中） */}
          {activeRunId && (
            <RunProgress
              runId={activeRunId}
              overallProgress={runEvents.overallProgress}
              nodes={runEvents.nodes}
              eventCount={runEvents.events.length}
              connected={runEvents.connected}
              runStatus={runEvents.runStatus}
              lastError={runEvents.lastError}
              onReconnect={runEvents.reconnect}
              stageGate={gatedRun.data?.stage_gate ?? null}
            />
          )}

          {/* Composer（移动端 sticky 底部） */}
          <div className="sticky bottom-0 z-10 lg:static">
            <AgentComposer
              sending={conversation.sending}
              sendError={conversation.sendError}
              failedContent={conversation.lastFailedContent}
              onSend={handleSend}
              examples={emptyConversation ? undefined : []}
              defaultEpisodeCount={project.target_episode_count}
              focusSignal={composerFocusSignal}
            />
          </div>
        </ConversationPanel>
      </div>

      {/* 右栏：桌面 5 列；平板以下抽屉 */}
      <div className="hidden lg:col-span-5 lg:block xl:col-span-5">
        <aside aria-label="上下文与计划">{rightPanel}</aside>
      </div>

      <button
        type="button"
        onClick={() => setDrawerOpen(true)}
        className="touch-target fixed bottom-24 right-4 z-30 rounded-full border border-[var(--border)] bg-[var(--surface)] px-4 text-xs shadow lg:hidden"
        data-testid="open-context-drawer"
        aria-label="打开上下文面板"
      >
        📋 上下文/计划
      </button>

      {drawerOpen && (
        <div className="fixed inset-0 z-40 flex lg:hidden" role="dialog" aria-modal="true" aria-label="上下文与计划">
          <div
            className="flex-1 bg-black/30"
            onClick={() => setDrawerOpen(false)}
          />
          <div className="transition-drawer h-full w-[85%] max-w-sm overflow-y-auto bg-[var(--surface)] p-4">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-semibold">上下文与计划</h2>
              <button
                type="button"
                onClick={() => setDrawerOpen(false)}
                className="touch-target px-2 text-sm text-[var(--text-muted)]"
                aria-label="关闭"
              >
                ✕
              </button>
            </div>
            {rightPanel}
          </div>
        </div>
      )}

      {/* 次级导航 */}
      <nav className="col-span-full mt-2 flex flex-wrap gap-3 text-xs">
        <Link href={`/projects/${projectId}/story-bible`} className="text-[var(--accent)] underline">Story Bible</Link>
        <Link href={`/projects/${projectId}/outline`} className="text-[var(--accent)] underline">分集大纲</Link>
        <Link href={`/projects/${projectId}/scripts/1`} className="text-[var(--accent)] underline">剧本</Link>
        <Link href={`/projects/${projectId}/versions`} className="text-[var(--accent)] underline">修订与版本</Link>
        <Link href={`/projects/${projectId}/exports`} className="text-[var(--accent)] underline">导出中心</Link>
        <Link href={`/projects/${projectId}/knowledge`} className="text-[var(--accent)] underline">知识库</Link>
      </nav>
    </div>
  );
}
