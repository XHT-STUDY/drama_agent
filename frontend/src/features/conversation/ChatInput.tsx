"use client";

/** ChatInput — 创作输入组件 (H-03).
 *
 * 提供文本输入区和"开始创作"按钮，
 * 触发 POST /projects/{id}/runs (action=create_script)。
 * 支持防重复提交、输入校验、API 错误展示。
 *
 * 文件上传区域预留（G-03 完成后接入）。
 */

import { useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { runsApi, ApiError } from "@/lib/api-client";

interface Props {
  projectId: string;
  /** Run 创建成功后的回调 */
  onRunCreated: (runId: string) => void;
  /** 是否有活跃 Run */
  hasActiveRun: boolean;
  /** 目标剧本集数（默认 3） */
  scriptCount?: number;
}

export function ChatInput({ projectId, onRunCreated, hasActiveRun, scriptCount = 3 }: Props) {
  const [userInput, setUserInput] = useState("");
  const [episodeCount, setEpisodeCount] = useState(scriptCount);
  const [fieldError, setFieldError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      runsApi.create(projectId, {
        action: "create_script",
        options: {
          user_input: userInput.trim(),
          source_type: "idea",
          outline_count: episodeCount,
          script_count: episodeCount,
        },
      }),
    onSuccess: (run) => {
      setUserInput("");
      onRunCreated(run.run_id);
    },
  });

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setFieldError(null);

    const trimmed = userInput.trim();
    if (!trimmed) {
      setFieldError("请输入创作 Idea");
      return;
    }
    if (trimmed.length < 8) {
      setFieldError("创作 Idea 至少需要 8 个字符");
      return;
    }

    mutation.mutate();
  }

  const apiErr = mutation.error as ApiError | null;

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-5">
      <h2 className="mb-3 text-sm font-semibold text-gray-700">创作 Idea</h2>

      <form onSubmit={handleSubmit} className="space-y-3">
        {/* 文本输入 */}
        <textarea
          value={userInput}
          onChange={(e) => { setUserInput(e.target.value); setFieldError(null); }}
          rows={3}
          maxLength={10000}
          placeholder="例如：一个被青训队抛弃的足球少年，靠隐藏天赋逆袭进入职业赛场。要求强爽点、强反派压迫、每集结尾有追更钩子。"
          disabled={mutation.isPending || hasActiveRun}
          className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:bg-gray-50 disabled:opacity-60 resize-none"
        />

        {/* 文件上传占位（G-03 接入） */}
        <div className="flex items-center gap-2 text-xs text-gray-400">
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" />
          </svg>
          <span>文件上传（即将支持 TXT/DOCX）</span>
        </div>

        {/* 生成集数选择 */}
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-500">生成集数：</span>
          <select
            value={episodeCount}
            onChange={(e) => setEpisodeCount(Number(e.target.value))}
            className="rounded border border-gray-300 bg-white px-2 py-1 text-xs text-gray-700"
          >
            {[1, 2, 3, 5, 10].map((n) => (
              <option key={n} value={n}>{n} 集</option>
            ))}
          </select>
        </div>

        {/* 客户端校验错误 */}
        {fieldError && (
          <div className="rounded bg-red-50 px-3 py-2 text-xs text-red-700">{fieldError}</div>
        )}

        {/* API 错误 */}
        {apiErr && (
          <div className="rounded bg-red-50 px-3 py-2 text-xs text-red-700">
            {apiErr.detail || apiErr.message}
            {apiErr.requestId && (
              <span className="ml-2 text-red-400">ID: {apiErr.requestId}</span>
            )}
          </div>
        )}

        {/* 提交按钮 */}
        <div className="flex items-center justify-between">
          <span className="text-xs text-gray-400">
            {userInput.length} 字符
          </span>
          <button
            type="submit"
            disabled={mutation.isPending || hasActiveRun}
            className="rounded-lg bg-blue-600 px-5 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            {mutation.isPending ? "提交中…" : hasActiveRun ? "创作进行中…" : "开始创作"}
          </button>
        </div>
      </form>
    </div>
  );
}
