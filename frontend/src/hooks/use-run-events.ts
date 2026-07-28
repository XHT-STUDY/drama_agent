"use client";

/** useRunEvents — SSE 进度订阅 Hook (H-03).
 *
 * 使用浏览器原生 EventSource API 连接 GET /runs/{id}/events。
 * EventSource 原生支持自动重连、Last-Event-ID、SSE 协议解析，
 * 比 fetch + ReadableStream 方案更可靠。
 *
 * 职责：
 * - 连接 SSE 端点
 * - 解析 WorkflowEvent，更新进度状态
 * - 自动重连（EventSource 内置）
 * - 返回：当前阶段、进度百分比、事件列表、连接状态
 *
 * 模块边界：纯前端 Hook，不操作 DOM，不直接写 API 类型。
 */

import { useCallback, useEffect, useRef, useState } from "react";

// ============================================================
// 类型
// ============================================================

/** 单个工作流事件（与后端 WorkflowEventSchema JSON 格式一致） */
export interface RunEvent {
  event_id: string;
  run_id: string;
  sequence: number;
  event_type: string;
  stage?: string;
  progress?: number;
  message?: string;
  artifact_id?: string | null;
  payload?: {
    node?: string;
    artifact_id?: string;
    artifact_type?: string;
    episode?: number;
    progress?: number;
    error?: string;
    message?: string;
    missing_fields?: string[];
    needs_user_input?: boolean;
    total_artifacts?: number;
    script_count?: number;
    prompt_versions?: Record<string, string>;
  };
  timestamp: string;
}

/** 工作流阶段状态 */
export type PhaseStatus = "pending" | "running" | "completed" | "failed";

/** 单个节点进度 */
export interface NodeProgress {
  node: string;
  label: string;
  status: PhaseStatus;
  progress: number;
  artifactIds: string[];
  error?: string;
}

/** Hook 返回类型 */
export interface RunEventsState {
  events: RunEvent[];
  nodes: NodeProgress[];
  overallProgress: number;
  runStatus: string | null;
  connected: boolean;
  disconnect: () => void;
  reconnect: () => void;
  lastError: string | null;
}

// ============================================================
// 节点中文标签
// ============================================================

const NODE_LABELS: Record<string, string> = {
  normalize: "需求归一化",
  retrieve: "知识检索",
  story_bible: "故事设定",
  outline: "分集大纲",
  write_episodes: "剧本撰写",
  finalize: "完成收尾",
};

// ============================================================
// Hook
// ============================================================

export function useRunEvents(
  runId: string | null,
  apiBase?: string,
): RunEventsState {
  const base =
    apiBase ||
    (typeof window !== "undefined"
      ? process.env.NEXT_PUBLIC_API_BASE
      : undefined) ||
    "http://localhost:8000/api/v1";

  const [events, setEvents] = useState<RunEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [overallProgress, setOverallProgress] = useState(0);
  const [runStatus, setRunStatus] = useState<string | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);

  const esRef = useRef<EventSource | null>(null);
  const eventsRef = useRef<RunEvent[]>([]);
  const manualCloseRef = useRef(false);

  /** 处理收到的 SSE 事件数据 */
  const handleEvent = useCallback((data: RunEvent) => {
    eventsRef.current = [...eventsRef.current, data];
    setEvents([...eventsRef.current]);

    // 更新进度
    if (data.payload?.progress !== undefined) {
      const pct = Math.round(data.payload.progress * 100);
      setOverallProgress(pct);
    }
    if (data.progress !== undefined) {
      const pct = Math.round(data.progress * 100);
      setOverallProgress(pct);
    }

    // 检查终态
    if (data.event_type === "run.completed") {
      console.log("[SSE] 工作流完成", data.payload);
      setRunStatus("completed");
    } else if (data.event_type === "run.failed") {
      console.log("[SSE] 工作流失败", data.payload);
      setRunStatus("failed");
    }
  }, []);

  /** 启动 / 重连 SSE */
  const connect = useCallback(() => {
    if (!runId) return;

    // 关闭已有连接
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }

    manualCloseRef.current = false;
    setConnected(false);
    setLastError(null);

    const url = `${base}/runs/${runId}/events`;
    console.log("[SSE] 正在连接:", url);

    const es = new EventSource(url);
    esRef.current = es;

    // --- 连接打开 ---
    es.onopen = () => {
      console.log("[SSE] 连接已建立");
      setConnected(true);
      setLastError(null);
    };

    // --- 收到消息（data: 行自动解析） ---
    es.onmessage = (msg) => {
      try {
        const data: RunEvent = JSON.parse(msg.data);
        console.log("[SSE] 收到事件:", data.event_type, data.payload?.node || "");
        handleEvent(data);
      } catch {
        // 非 JSON 消息（heartbeat 等），忽略
      }
    };

    // --- 命名事件：run_ended ---
    es.addEventListener("run_ended", (msg: MessageEvent) => {
      console.log("[SSE] 服务端通知 run_ended:", msg.data);
      try {
        const info = JSON.parse(msg.data);
        if (info.status) {
          setRunStatus(info.status);
        }
      } catch {
        // ignore
      }
      es.close();
    });

    // --- 错误 / 断开 ---
    es.onerror = () => {
      // EventSource 在连接失败或服务端关闭连接时触发 onerror
      // 如果 readyState === CLOSED 且非手动关闭，说明服务端关闭了连接
      if (es.readyState === EventSource.CLOSED) {
        console.log("[SSE] 连接已关闭 (readyState=CLOSED)");
        setConnected(false);
        if (!manualCloseRef.current) {
          // 如果是 run 已完成，服务端正常关闭，不算错误
          // EventSource 会自动重连，但我们不希望已完成时重连
          setLastError("连接已关闭（工作流可能已完成）");
        }
      } else {
        // readyState === CONNECTING — 正在自动重连
        console.log("[SSE] 连接中断，EventSource 正在自动重连…");
        setConnected(false);
        setLastError("连接中断，正在重连…");
      }
    };
  }, [runId, base, handleEvent]);

  // 自动连接 + 清理
  useEffect(() => {
    connect();
    return () => {
      manualCloseRef.current = true;
      if (esRef.current) {
        esRef.current.close();
        esRef.current = null;
      }
    };
  }, [connect]);

  /** 手动断开 */
  const disconnect = useCallback(() => {
    manualCloseRef.current = true;
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }
    setConnected(false);
  }, []);

  /** 手动重连 */
  const reconnect = useCallback(() => {
    eventsRef.current = [];
    setEvents([]);
    setOverallProgress(0);
    setRunStatus(null);
    setLastError(null);
    connect();
  }, [connect]);

  // 从事件推导节点进度
  const nodes = _deriveNodeProgress(events);

  return {
    events,
    nodes,
    overallProgress,
    runStatus,
    connected,
    disconnect,
    reconnect,
    lastError,
  };
}

/** 从事件列表推导各节点进度（纯函数，不依赖 React） */
function _deriveNodeProgress(events: RunEvent[]): NodeProgress[] {
  const nodeMap = new Map<string, NodeProgress>();

  for (const ev of events) {
    const nodeName = ev.payload?.node;
    if (!nodeName || !NODE_LABELS[nodeName]) continue;

    if (!nodeMap.has(nodeName)) {
      nodeMap.set(nodeName, {
        node: nodeName,
        label: NODE_LABELS[nodeName] || nodeName,
        status: "pending",
        progress: 0,
        artifactIds: [],
      });
    }
    const np = nodeMap.get(nodeName)!;

    if (ev.event_type === "node.started") {
      np.status = "running";
    } else if (ev.event_type === "node.completed") {
      np.status = "completed";
      np.progress = 100;
      if (ev.payload?.artifact_id) {
        np.artifactIds.push(ev.payload.artifact_id);
      }
    } else if (ev.event_type === "node.failed") {
      np.status = "failed";
      np.error = ev.payload?.error;
    }
  }

  // run.completed → 将所有 running 节点标记为 completed
  const hasRunCompleted = events.some((ev) => ev.event_type === "run.completed");
  if (hasRunCompleted) {
    for (const np of nodeMap.values()) {
      if (np.status === "running") {
        np.status = "completed";
        np.progress = 100;
      }
    }
  }

  // 按固定顺序排列
  const order = ["normalize", "retrieve", "story_bible", "outline", "write_episodes", "finalize"];
  return order
    .filter((n) => nodeMap.has(n))
    .map((n) => nodeMap.get(n)!);
}
