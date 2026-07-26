"use client";

/** RunProgress — 工作流进度面板 (H-03).
 *
 * 显示：
 * - 整体进度条
 * - 各节点状态（pending → running → completed ✓ / failed ✗）
 * - 失败节点的错误信息与错误码
 * - 断开重连状态
 * - 取消 / 重试操作
 */

import { useMutation } from "@tanstack/react-query";
import { runsApi } from "@/lib/api-client";
import type { NodeProgress } from "@/hooks/use-run-events";

// ============================================================
// 状态图标
// ============================================================

/** 节点状态 → 图标/颜色 */
function PhaseIcon({ status }: { status: string }) {
  switch (status) {
    case "running":
      return (
        <span className="flex h-5 w-5 items-center justify-center">
          <svg className="h-4 w-4 animate-spin text-blue-500" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
        </span>
      );
    case "completed":
      return <span className="flex h-5 w-5 items-center justify-center rounded-full bg-green-100 text-green-600 text-xs">✓</span>;
    case "failed":
      return <span className="flex h-5 w-5 items-center justify-center rounded-full bg-red-100 text-red-600 text-xs">✗</span>;
    default:
      return <span className="flex h-5 w-5 items-center justify-center rounded-full bg-gray-100 text-gray-300 text-xs">○</span>;
  }
}

// ============================================================
// Props
// ============================================================

interface Props {
  runId: string;
  overallProgress: number;
  nodes: NodeProgress[];
  /** 收到的事件总数 */
  eventCount: number;
  connected: boolean;
  runStatus: string | null;
  lastError: string | null;
  onReconnect: () => void;
}

// ============================================================
// 组件
// ============================================================

export function RunProgress({
  runId,
  overallProgress,
  nodes,
  eventCount,
  connected,
  runStatus,
  lastError,
  onReconnect,
}: Props) {
  const cancelMutation = useMutation({
    mutationFn: () => runsApi.cancel(runId),
  });

  const isRunning = !runStatus;

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-5">
      {/* 头部 */}
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-700">
          {runStatus === "completed"
            ? "创作完成 🎉"
            : runStatus === "failed"
              ? "创作失败"
              : "创作进度"}
        </h2>
        <div className="flex items-center gap-2">
          {/* 连接状态指示 */}
          {isRunning && (
            <span className={`flex items-center gap-1 text-xs ${connected ? "text-green-600" : "text-red-500"}`}>
              <span className={`inline-block h-2 w-2 rounded-full ${connected ? "bg-green-500" : "bg-red-500"}`} />
              {connected ? "已连接" : "已断开"}
            </span>
          )}

          {/* 重连按钮 */}
          {!connected && isRunning && (
            <button
              onClick={onReconnect}
              className="rounded border border-gray-300 px-2 py-0.5 text-xs text-gray-600 hover:bg-gray-50"
            >
              重连
            </button>
          )}

          {/* 取消按钮 */}
          {isRunning && (
            <button
              onClick={() => cancelMutation.mutate()}
              disabled={cancelMutation.isPending}
              className="rounded border border-red-200 px-2 py-0.5 text-xs text-red-600 hover:bg-red-50 disabled:opacity-50"
            >
              {cancelMutation.isPending ? "取消中…" : "取消"}
            </button>
          )}
        </div>
      </div>

      {/* 整体进度条 */}
      {isRunning && (
        <div className="mb-4">
          <div className="flex items-center justify-between text-xs text-gray-500 mb-1">
            <span>整体进度</span>
            <span>{overallProgress}%</span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-gray-100">
            <div
              className="h-full rounded-full bg-blue-500 transition-all duration-500 ease-out"
              style={{ width: `${Math.max(overallProgress, 2)}%` }}
            />
          </div>
        </div>
      )}

      {/* 完成状态 */}
      {runStatus === "completed" && (
        <div className="mb-4 rounded bg-green-50 px-4 py-3 text-sm text-green-700">
          ✅ 全部节点已完成，Artifact 已生成。
        </div>
      )}

      {/* 失败状态 */}
      {runStatus === "failed" && (
        <div className="mb-4 rounded bg-red-50 px-4 py-3 text-sm text-red-700">
          创作过程中发生错误。
          {lastError && <span className="block mt-1 text-xs text-red-400">{lastError}</span>}
        </div>
      )}

      {/* SSE 错误 */}
      {lastError && !runStatus && (
        <div className="mb-4 rounded bg-yellow-50 px-4 py-3 text-xs text-yellow-700">
          SSE 连接错误：{lastError}
        </div>
      )}

      {/* 节点列表 */}
      <div className="space-y-1.5">
        {nodes.map((node) => (
          <div
            key={node.node}
            className={`flex items-center gap-3 rounded-md px-3 py-2 text-sm ${
              node.status === "running" ? "bg-blue-50" : ""
            }`}
          >
            <PhaseIcon status={node.status} />
            <span className={`flex-1 truncate ${node.status === "failed" ? "text-red-700" : "text-gray-700"}`}>
              {node.label}
            </span>
            {node.error && (
              <span className="max-w-[200px] truncate text-xs text-red-500" title={node.error}>
                {node.error}
              </span>
            )}
            {node.status === "completed" && (
              <span className="text-xs text-gray-400">
                {node.artifactIds.length > 0 ? `${node.artifactIds.length} 资产` : "完成"}
              </span>
            )}
          </div>
        ))}

        {/* 无节点时的状态 */}
        {nodes.length === 0 && isRunning && (
          <div className="py-4 text-center">
            <p className="text-sm text-gray-500">
              {connected
                ? `已连接到后端 · 已收到 ${eventCount} 个事件`
                : "正在连接后端服务…"}
            </p>
            {connected && eventCount > 0 && (
              <p className="mt-1 text-xs text-gray-400">正在解析工作流节点…</p>
            )}
            {connected && eventCount === 0 && (
              <p className="mt-1 text-xs text-gray-400">等待工作流启动…</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
