"use client";

/** 创建项目页 (H-02).
 *
 * 表单校验：
 * - 标题：必填，1-200 字符
 * - 目标集数：1-100，默认 10
 * - 提交中禁用表单防重复提交
 * - API 错误展示（保留用户已输入的内容）
 */

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import Link from "next/link";
import { projectsApi, ApiError } from "@/lib/api-client";
import { ErrorMessage } from "@/components/ErrorMessage";

export default function NewProjectPage() {
  const router = useRouter();

  const [title, setTitle] = useState("");
  const [targetCount, setTargetCount] = useState(10);

  // 表单校验错误（客户端）
  const [fieldError, setFieldError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      projectsApi.create({
        title: title.trim(),
        target_episode_count: targetCount,
      }),
    onSuccess: (project) => {
      router.push(`/projects/${project.id}`);
    },
  });

  /** 校验并提交 */
  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setFieldError(null);

    // 客户端校验
    const trimmed = title.trim();
    if (!trimmed) {
      setFieldError("请输入项目标题");
      return;
    }
    if (trimmed.length > 200) {
      setFieldError("标题不能超过 200 字符");
      return;
    }
    if (targetCount < 1 || targetCount > 100) {
      setFieldError("目标集数需在 1-100 之间");
      return;
    }

    mutation.mutate();
  }

  const apiErr = mutation.error as ApiError | null;

  return (
    <div className="mx-auto max-w-lg">
      {/* 返回链接 */}
      <Link
        href="/projects"
        className="mb-4 inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 transition-colors"
      >
        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
        </svg>
        返回项目列表
      </Link>

      <h1 className="mb-6 text-2xl font-bold text-gray-900">创建新项目</h1>

      {/* API 错误 */}
      {apiErr && (
        <div className="mb-4">
          <ErrorMessage error={apiErr} />
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-5">
        {/* 标题 */}
        <div>
          <label htmlFor="title" className="block text-sm font-medium text-gray-700">
            项目标题 <span className="text-red-500">*</span>
          </label>
          <input
            id="title"
            type="text"
            value={title}
            onChange={(e) => { setTitle(e.target.value); setFieldError(null); }}
            maxLength={200}
            placeholder="例如：足球少年之逆袭人生"
            disabled={mutation.isPending}
            className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50"
          />
          <p className="mt-1 text-xs text-gray-400">
            {title.length}/200
          </p>
        </div>

        {/* 目标集数 */}
        <div>
          <label htmlFor="episodes" className="block text-sm font-medium text-gray-700">
            目标总集数
          </label>
          <input
            id="episodes"
            type="number"
            min={1}
            max={100}
            value={targetCount}
            onChange={(e) => { setTargetCount(Number(e.target.value)); setFieldError(null); }}
            disabled={mutation.isPending}
            className="mt-1 block w-40 rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50"
          />
          <p className="mt-1 text-xs text-gray-400">
            MVP 默认 10 集
          </p>
        </div>

        {/* 客户端校验错误 */}
        {fieldError && (
          <div className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
            {fieldError}
          </div>
        )}

        {/* 提交按钮 */}
        <div className="flex gap-3">
          <button
            type="submit"
            disabled={mutation.isPending}
            className="rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-medium text-white shadow-sm hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            {mutation.isPending ? "创建中…" : "创建项目"}
          </button>
          <Link
            href="/projects"
            className="rounded-lg border border-gray-300 px-5 py-2.5 text-sm text-gray-700 hover:bg-gray-50 transition-colors"
          >
            取消
          </Link>
        </div>
      </form>
    </div>
  );
}
