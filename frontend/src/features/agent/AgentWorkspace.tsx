"use client";

/** AgentWorkspace — 同屏共创工作台（J-11 → W1-02 三区改造）。
 *
 * 布局：左侧作品导航 / 中央作品画布（正文最大面积）/ 右侧常驻 Agent
 * 会话。窄屏用「作品 / Agent」两个原生 tab 切换（两区都保持挂载，
 * 切换不卸载会话数据）。
 *
 * 导航事实在 URL（useWorkspaceLocation）：刷新/后退恢复确切稿件与
 * 工具面板；消息与任务事件不改阅读选择——新稿完成只出现「打开本轮
 * 新稿」按钮，点击才切换。活动上下文从正在阅读的稿件派生（打开作品
 * 即上下文），不再有单独的"选中上下文"操作。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { artifactsApi, conversationsApi, runsApi, storyStateApi } from "@/lib/api-client";
import { latestValidPerEpisode } from "@/lib/artifact-latest";
import { EpisodeNav } from "@/features/episodes/EpisodeNav";
import type { EpisodeNavItem } from "@/features/episodes/EpisodeNav";
import { useAgentConversation } from "@/hooks/use-agent-conversation";
import { useAgentActionEvents } from "@/hooks/use-agent-action";
import { useRunEvents } from "@/hooks/use-run-events";
import {
  useWorkspaceLocation,
  type WorkspacePanel,
} from "@/hooks/use-workspace-location";
import { ActionPlanCard } from "./ActionPlanCard";
import { AgentComposer } from "./AgentComposer";
import { ArtifactCanvas } from "./ArtifactCanvas";
import { StoryStatePanel } from "@/features/story-bible/StoryStatePanel";
import { UploadInput } from "./UploadInput";
import { ConversationPanel } from "./ConversationPanel";
import { MessageList } from "./MessageList";
import { RunProgress } from "@/features/runs/RunProgress";
import type { ActiveArtifactContext, Project } from "@/types/api";

interface Props {
  projectId: string;
  project: Project;
}

const RUN_ACTION_LABEL: Record<string, string> = {
  create_script: "创作",
  evaluate: "评估",
  revise: "修订",
  revise_script: "剧本修订",
  revise_outline: "大纲修订",
  import: "导入",
  export: "导出",
};

export function AgentWorkspace({ projectId, project }: Props) {
  const queryClient = useQueryClient();
  const location = useWorkspaceLocation();
  const [composerFocusSignal, setComposerFocusSignal] = useState(0);
  const [mobileTab, setMobileTab] = useState<"work" | "agent">("work");

  // ---- 会话：URL conversation 是恢复事实；未带时选第一个 ----
  const conversationsQuery = useQuery({
    queryKey: ["agent-conversations", projectId],
    queryFn: () => conversationsApi.list(projectId, 0, 50),
  });
  const currentConversationId =
    location.conversationId ?? conversationsQuery.data?.items[0]?.id ?? null;

  const createConversation = useMutation({
    mutationFn: () => conversationsApi.create(projectId, { title: "新会话" }),
    onSuccess: (created) => {
      // Composer 按会话 remount（草稿按会话隔离）——把用户在创建点击
      // 前后已输入的未发送草稿迁移到新会话，避免 remount 丢字
      if (typeof window !== "undefined") {
        const oldKey = `draft:${projectId}:${currentConversationId ?? "new"}`;
        const newKey = `draft:${projectId}:${created.id}`;
        try {
          const draft = window.sessionStorage.getItem(oldKey);
          if (draft) {
            window.sessionStorage.setItem(newKey, draft);
            window.sessionStorage.removeItem(oldKey);
          }
        } catch {
          // sessionStorage 不可用时静默跳过
        }
      }
      location.setConversation(created.id);
      void queryClient.invalidateQueries({ queryKey: ["agent-conversations", projectId] });
    },
  });

  const conversation = useAgentConversation({
    projectId,
    conversationId: currentConversationId,
  });

  const focusComposer = useCallback(() => {
    setComposerFocusSignal((n) => n + 1);
    setMobileTab("agent");
  }, []);

  // ---- 活动上下文：从正在阅读的稿件派生（打开作品即上下文，W1-02） ----
  const readingArtifact = useQuery({
    queryKey: ["artifact", location.artifactId, projectId],
    enabled: location.artifactId !== null,
    queryFn: () => artifactsApi.getById(location.artifactId!, projectId),
    retry: false,
  });
  const activeContext: ActiveArtifactContext | null = useMemo(() => {
    const a = readingArtifact.data;
    if (!a) return null;
    return {
      artifact_id: a.id,
      artifact_type: a.type,
      episode_number: a.episode_number,
      scene_number: a.type === "script_draft" ? location.scene : null,
      version: a.version,
      checksum: a.checksum ?? null,
    };
  }, [readingArtifact.data, location.scene]);

  const handleSend = useCallback(
    (content: string, options?: { episodeCount?: number }) => {
      void conversation.sendTurn(content, activeContext, options);
    },
    [conversation, activeContext],
  );

  // ---- 首载默认选稿：未带 artifact 时选最新可读作品并 replace 到确切 ID ----
  const scriptsIndex = useQuery({
    queryKey: ["project-scripts-index", projectId],
    queryFn: () => artifactsApi.listAllByType(projectId, "script_draft"),
  });
  // M-05:剧情状态只读查询(零模型调用;分批/采用变化后面板给出恢复入口)
  const storyStateQuery = useQuery({
    queryKey: ["story-state", projectId],
    queryFn: () => storyStateApi.get(projectId),
    staleTime: 30_000,
  });

  const defaultResolvedRef = useRef(false);
  useEffect(() => {
    if (location.artifactId !== null || defaultResolvedRef.current) return;
    if (scriptsIndex.isLoading) return;
    // 最新可读作品 = 已写集中集号最大的最新 valid 剧本
    const picks = latestValidPerEpisode(scriptsIndex.data ?? []);
    const pick = picks.length > 0 ? picks[picks.length - 1] : null;
    defaultResolvedRef.current = true;
    if (pick) {
      location.replaceArtifact(pick.id);
    }
    // 无任何可读作品：保持无 artifact（空项目态，会话为主）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.artifactId, scriptsIndex.isLoading, scriptsIndex.data]);

  // ---- 焦点计划卡（与消息流去重） ----
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

  // ---- 焦点 Run（计划的执行段）：SSE 进度仍只订阅这一个 ----
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

  // W1-07：Run 到达门/终态时失效消息缓存——门上结果消息与终态消息由
  // Worker 回写，SSE 事件先于消息落库或被错过时，靠 Run 状态变化驱动
  // 消息刷新（正常完成不依赖用户刷新页面）
  const runStatusSignal = gatedRun.data?.status;
  useEffect(() => {
    if (
      runStatusSignal === "needs_review" ||
      runStatusSignal === "completed" ||
      runStatusSignal === "failed"
    ) {
      void queryClient.invalidateQueries({ queryKey: ["agent-messages"] });
      void queryClient.invalidateQueries({ queryKey: ["agent-action"] });
    }
  }, [runStatusSignal, queryClient]);

  // 终态自愈（bda1302）：SSE 重放补收错过的 agent_action.updated
  const onActionUpdated = useCallback(
    (payload: { agent_action_id?: string; status?: string; goal_status?: string }) => {
      if (payload.agent_action_id && payload.agent_action_id !== focusActionId) return;
      void queryClient.invalidateQueries({ queryKey: ["agent-action"] });
      void queryClient.invalidateQueries({ queryKey: ["agent-messages"] });
    },
    [focusActionId, queryClient],
  );
  useAgentActionEvents(gatedRunId, onActionUpdated);

  // ---- 项目 Run 状态区（W1-02）：全部 queued/running（含导入/导出） ----
  const projectRuns = useQuery({
    queryKey: ["project-runs", projectId],
    queryFn: () => runsApi.listByProject(projectId),
    refetchInterval: (query) => {
      const items = (query.state.data?.items ?? []) as Array<{ status: string }>;
      return items.some((r) => r.status === "queued" || r.status === "running")
        ? 2500
        : false;
    },
  });
  const activeRuns = useMemo(
    () =>
      ((projectRuns.data?.items ?? []) as Array<{
        run_id: string;
        action: string;
        status: string;
      }>).filter((r) => r.status === "queued" || r.status === "running"),
    [projectRuns.data],
  );
  // 任一 Run 到终态：刷新作品索引/当前稿/项目计数（新稿可见性）
  const activeRunIds = activeRuns.map((r) => r.run_id).join(",");
  useEffect(() => {
    if (!activeRunIds) return;
    return () => {
      // 活跃集合变化（有 Run 结束）时失效相关缓存：作品/当前稿/
      // 消息/导出内容与历史/项目计数
      void queryClient.invalidateQueries({ queryKey: ["project-scripts-index", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["artifact"] });
      void queryClient.invalidateQueries({ queryKey: ["agent-messages"] });
      void queryClient.invalidateQueries({ queryKey: ["export-data", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["server-exports", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["project-runs", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    };
  }, [activeRunIds, projectId, queryClient]);

  // ---- 新稿提示：不改阅读选择，只给入口（W1-02） ----
  const reading = readingArtifact.data ?? null;
  const latestOfReading = useQuery({
    queryKey: ["latest-for-reading", projectId, reading?.type, reading?.episode_number],
    enabled: reading != null,
    queryFn: () =>
      artifactsApi.getLatest(projectId, reading!.type, reading!.episode_number),
    retry: false,
  });
  const [newDraftDismissedFor, setNewDraftDismissedFor] = useState<string | null>(null);
  const newDraftAvailable =
    reading != null &&
    latestOfReading.data != null &&
    latestOfReading.data.id !== reading.id &&
    newDraftDismissedFor !== latestOfReading.data.id
      ? latestOfReading.data
      : null;

  // ---- 会话区（右栏；窄屏 Agent tab） ----
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

  const emptyConversation =
    conversation.messages.length === 0 && !conversation.pendingContent;
  const scrollRef = useRef<HTMLDivElement>(null);
  const lastMessageId = conversation.messages[conversation.messages.length - 1]?.id;
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lastMessageId, conversation.pendingContent]);

  const agentPane = (
    <ConversationPanel
      conversations={conversationsQuery.data?.items ?? []}
      currentId={currentConversationId}
      onSelect={(id) => location.setConversation(id)}
      onCreate={() => createConversation.mutate()}
      creating={createConversation.isPending}
    >
      <div
        ref={scrollRef}
        className="max-h-[38vh] min-h-36 flex-1 overflow-y-auto rounded-lg bg-[var(--surface-muted)] p-3 lg:max-h-none"
      >
        {conversation.isLoading ? (
          <p className="text-xs text-[var(--text-muted)]">正在加载消息…</p>
        ) : emptyConversation ? (
          <div className="py-8 text-center" data-testid="empty-conversation">
            <p className="message-body text-[var(--text-muted)]">
              和 Agent 对话完成创作：直接输入想法或修改要求。
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

      {/* Composer：按会话 remount（各自草稿），sticky 底部 */}
      <div className="sticky bottom-0 z-10 lg:static">
        <AgentComposer
          key={currentConversationId ?? "new"}
          draftKey={`draft:${projectId}:${currentConversationId ?? "new"}`}
          sending={conversation.sending || createConversation.isPending}
          sendError={conversation.sendError}
          failedContent={conversation.lastFailedContent}
          onSend={handleSend}
          examples={emptyConversation ? undefined : []}
          defaultEpisodeCount={project.target_episode_count}
          focusSignal={composerFocusSignal}
        />
      </div>

      {/* W1-06：原稿附件入口（解析存档 → 识别并导入 → 分类动作） */}
      <UploadInput
        projectId={projectId}
        onOpenArtifact={(id) => {
          location.openArtifact(id);
          setMobileTab("work");
        }}
      />

      {/* 兜底计划卡：消息流尚未刷新的刚产出计划 */}
      {focusActionId && !planMessageActionIds.has(focusActionId) && (
        <ActionPlanCard
          actionId={focusActionId}
          projectId={projectId}
          onAskAgain={focusComposer}
        />
      )}
    </ConversationPanel>
  );

  // ---- 作品导航（左栏）：剧集区复用 EpisodeNav 的导航表现（W1-02） ----
  const episodeNav = useMemo(() => {
    const latest = latestValidPerEpisode(scriptsIndex.data ?? []);
    const byEp = new Map(latest.map((a) => [a.episode_number, a]));
    const episodes: EpisodeNavItem[] = [];
    for (let ep = 1; ep <= project.target_episode_count; ep += 1) {
      episodes.push({
        episode_number: ep,
        hasScript: byEp.has(ep),
        hasEvaluation: false,
      });
    }
    // 目标集数之外的已写集（超出目标的项目）也要可达
    for (const a of latest) {
      if (a.episode_number > project.target_episode_count) {
        episodes.push({
          episode_number: a.episode_number,
          hasScript: true,
          hasEvaluation: false,
        });
      }
    }
    return { items: episodes, byEpisode: byEp };
  }, [scriptsIndex.data, project.target_episode_count]);
  const currentEpisode =
    reading?.type === "script_draft" ? reading.episode_number : 0;
  const openEpisode = useCallback(
    (episode: number) => {
      const target = episodeNav.byEpisode.get(episode);
      if (target) location.openArtifact(target.id);
    },
    [episodeNav, location],
  );
  const currentReadingId = location.artifactId;

  const navPane = (
    <nav aria-label="作品导航" className="space-y-3 text-sm" data-testid="work-nav">
      <WorkNavLinks
        projectId={projectId}
        onOpen={(id) => location.openArtifact(id)}
        episodeItems={episodeNav.items}
        currentEpisode={currentEpisode}
        targetCount={project.target_episode_count}
        onSelectEpisode={openEpisode}
        currentReadingId={currentReadingId}
      />
    </nav>
  );

  // ---- 画布区（中央；窄屏作品 tab） ----
  const workPane = (
    <div className="flex h-full min-h-0 flex-col gap-2" data-testid="work-pane">
      {/* 项目 Run 状态区：全部活跃 Run（不依赖计划卡） */}
      {activeRuns.length > 0 && (
        <div
          className="flex flex-wrap gap-2 text-xs"
          data-testid="project-runs-strip"
          aria-live="polite"
        >
          {activeRuns.map((r) => (
            <span
              key={r.run_id}
              className="rounded-full border border-[var(--border)] bg-[var(--surface)] px-2.5 py-0.5 text-[var(--text-muted)]"
            >
              <span className="mr-1 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--accent)]" />
              {RUN_ACTION_LABEL[r.action] ?? r.action} · {r.status === "queued" ? "排队中" : "执行中"}
            </span>
          ))}
        </div>
      )}

      {location.notice && (
        <p
          className="flex items-center justify-between gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs text-amber-700"
          data-testid="workspace-notice"
        >
          <span>{location.notice}</span>
          <button
            type="button"
            onClick={() => location.clearNotice()}
            className="text-[var(--text-muted)]"
            aria-label="关闭提示"
          >
            ✕
          </button>
        </p>
      )}

      {newDraftAvailable && (
        <div
          className="flex items-center justify-between gap-2 rounded-lg border border-[var(--accent)]/40 bg-[var(--accent)]/5 px-3 py-1.5 text-xs"
          data-testid="new-draft-banner"
        >
          <span>
            本轮任务产生了新版本（v{newDraftAvailable.version}）——你仍在看 v{reading?.version}。
          </span>
          <span className="flex gap-2">
            <button
              type="button"
              onClick={() => location.openArtifact(newDraftAvailable.id)}
              className="rounded border border-[var(--accent)] px-2 py-0.5 text-[var(--accent)]"
            >
              打开本轮新稿
            </button>
            <button
              type="button"
              onClick={() => setNewDraftDismissedFor(newDraftAvailable.id)}
              className="text-[var(--text-muted)]"
              aria-label="暂不切换"
            >
              稍后
            </button>
          </span>
        </div>
      )}

      {storyStateQuery.data && storyStateQuery.data.status !== "missing" && (
        <details className="mb-3 rounded-lg" data-testid="story-state-details">
          <summary className="cursor-pointer select-none text-xs text-[var(--text-muted)]">
            剧情状态（截至第 {storyStateQuery.data.projection?.through_episode ?? 0} 集）
          </summary>
          <div className="mt-2">
            <StoryStatePanel
              state={storyStateQuery.data}
              onRefresh={() =>
                queryClient.invalidateQueries({ queryKey: ["story-state", projectId] })
              }
              onOpenSource={(artifactId) => location.openArtifact(artifactId)}
            />
          </div>
        </details>
      )}

      {location.artifactId ? (
        <div className="min-h-0 flex-1">
          <ArtifactCanvas
            projectId={projectId}
            artifactId={location.artifactId}
            scene={location.scene}
            panel={location.panel}
            compareId={location.compareId}
            onOpenArtifact={(id, panel) => location.openArtifact(id, panel)}
            onSetPanel={(p: WorkspacePanel) => location.setPanel(p)}
            onSetScene={location.setScene}
            onSetCompare={location.setCompare}
          />
        </div>
      ) : (location.panel === "exports" || location.panel === "sources") ? (
        // W1-06/W1-02：/exports 等工具面板重定向无 artifact——导出与资料
        // 不依赖具体稿件，直接渲染画布面板（读稿区为空）
        <div className="min-h-0 flex-1">
          <ArtifactCanvas
            projectId={projectId}
            artifactId=""
            scene={null}
            panel={location.panel}
            compareId={null}
            onOpenArtifact={(id, panel) => location.openArtifact(id, panel)}
            onSetPanel={(p) => location.setPanel(p)}
            onSetScene={location.setScene}
            onSetCompare={location.setCompare}
          />
        </div>
      ) : scriptsIndex.isLoading ? (
        <p className="p-6 text-center text-sm text-[var(--text-muted)]">正在载入作品…</p>
      ) : (
        <div
          className="flex min-h-0 flex-1 items-center justify-center rounded-lg border border-dashed border-[var(--border)] p-8 text-center"
          data-testid="empty-project-workspace"
        >
          <div>
            <p className="text-sm text-[var(--text-muted)]">
              这个项目还没有可阅读的稿件。
            </p>
            <p className="mt-1 text-xs text-[var(--text-muted)]">
              在右侧对话里输入想法开始创作；产出会出现在这里。
            </p>
            <button
              type="button"
              onClick={focusComposer}
              className="mt-3 rounded-lg bg-[var(--accent)] px-3 py-1.5 text-xs text-white"
            >
              输入创作想法
            </button>
          </div>
        </div>
      )}
    </div>
  );

  return (
    <div>
      {/* 窄屏 tab（两区保持挂载，只切换可见性） */}
      <div className="mb-3 flex gap-2 lg:hidden" role="tablist" aria-label="工作台视图">
        {(["work", "agent"] as const).map((tab) => (
          <button
            key={tab}
            type="button"
            role="tab"
            aria-selected={mobileTab === tab}
            onClick={() => setMobileTab(tab)}
            className={
              mobileTab === tab
                ? "rounded-lg border border-[var(--accent)] bg-[var(--accent)]/10 px-3 py-1 text-xs text-[var(--accent)]"
                : "rounded-lg border border-[var(--border)] px-3 py-1 text-xs text-[var(--text-muted)]"
            }
            data-testid={`tab-${tab}`}
          >
            {tab === "work" ? "📖 作品" : "💬 Agent"}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:h-[calc(100vh-13rem)] lg:grid-cols-12">
        {/* 左：作品导航 */}
        <div className={`${mobileTab === "work" ? "" : "hidden"} lg:col-span-2 lg:block`}>
          {navPane}
        </div>
        {/* 中：作品画布（正文最大面积） */}
        <div className={`${mobileTab === "work" ? "" : "hidden"} min-h-0 lg:col-span-6 lg:block`}>
          {workPane}
        </div>
        {/* 右：常驻 Agent 会话 */}
        <div className={`${mobileTab === "agent" ? "" : "hidden"} min-h-0 lg:col-span-4 lg:block`}>
          {agentPane}
        </div>
      </div>
    </div>
  );
}

/** 作品导航链接组：设定/大纲按需加载最新，各集直达最新 valid 版本 */
function WorkNavLinks(props: {
  projectId: string;
  episodeItems: EpisodeNavItem[];
  currentEpisode: number;
  targetCount: number;
  onSelectEpisode: (episode: number) => void;
  currentReadingId: string | null;
  onOpen: (id: string) => void;
}) {
  const {
    projectId,
    episodeItems,
    currentEpisode,
    targetCount,
    onSelectEpisode,
    currentReadingId,
    onOpen,
  } = props;
  const storyBible = useQuery({
    queryKey: ["nav-sb", projectId],
    queryFn: () => artifactsApi.getLatest(projectId, "story_bible"),
    retry: false,
    staleTime: 30_000,
  });
  const outline = useQuery({
    queryKey: ["nav-outline", projectId],
    queryFn: () => artifactsApi.getLatest(projectId, "episode_outline_set"),
    retry: false,
    staleTime: 30_000,
  });

  const itemClass = (active: boolean) =>
    `block w-full rounded px-2 py-1 text-left text-xs transition-colors ${
      active
        ? "bg-[var(--accent)]/10 text-[var(--accent)]"
        : "text-[var(--text-muted)] hover:bg-[var(--surface-muted)]"
    }`;

  return (
    <>
      <div>
        <p className="mb-1 px-2 text-[10px] font-medium uppercase tracking-wide text-[var(--text-muted)]">
          作品
        </p>
        {storyBible.data && (
          <button
            type="button"
            className={itemClass(currentReadingId === storyBible.data.id)}
            onClick={() => onOpen(storyBible.data!.id)}
          >
            故事设定
          </button>
        )}
        {outline.data && (
          <button
            type="button"
            className={itemClass(currentReadingId === outline.data.id)}
            onClick={() => onOpen(outline.data!.id)}
          >
            分集大纲
          </button>
        )}
        {episodeItems.length > 0 && (
          <div className="mt-1 max-h-56 overflow-y-auto">
            <EpisodeNav
              episodes={episodeItems}
              currentEpisode={currentEpisode}
              targetCount={targetCount}
              onSelect={onSelectEpisode}
            />
          </div>
        )}
        {!storyBible.data && !outline.data && episodeItems.every((e) => !e.hasScript) && (
          <p className="px-2 text-xs text-[var(--text-muted)]">暂无作品</p>
        )}
      </div>
      <div>
        <p className="mb-1 px-2 text-[10px] font-medium uppercase tracking-wide text-[var(--text-muted)]">
          工具
        </p>
        <Link
          href={`/projects/${projectId}/versions`}
          className={itemClass(false)}
        >
          修订与版本
        </Link>
        <Link
          href={`/projects/${projectId}/knowledge`}
          className={itemClass(false)}
        >
          知识库
        </Link>
      </div>
    </>
  );
}
