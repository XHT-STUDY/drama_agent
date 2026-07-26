/** ErrorMessage 组件 — 错误展示 (H-01).
 *
 * 统一展示 API 错误，包含 request_id 用于日志追踪。
 */

import type { ApiError } from "@/lib/api-client";

interface Props {
  error: Error | ApiError;
  /** 可选的重试回调 */
  onRetry?: () => void;
}

export function ErrorMessage({ error, onRetry }: Props) {
  const apiErr = error as ApiError;

  return (
    <div className="rounded-lg border border-red-200 bg-red-50 p-6 text-red-800">
      <div className="flex items-start gap-3">
        <svg className="mt-0.5 h-5 w-5 shrink-0" fill="currentColor" viewBox="0 0 20 20">
          <path
            fillRule="evenodd"
            d="M10 18a8 8 0 100-16 8 8 0 000 16zm-.707-10.293a1 1 0 011.414 0l2 2a1 1 0 01-1.414 1.414L10 9.414l-1.293 1.293a1 1 0 01-1.414-1.414l2-2a1 1 0 010 0z"
            clipRule="evenodd"
          />
          <path
            fillRule="evenodd"
            d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.28 7.22a.75.75 0 00-1.06 1.06L8.94 10l-1.72 1.72a.75.75 0 101.06 1.06L10 11.06l1.72 1.72a.75.75 0 101.06-1.06L11.06 10l1.72-1.72a.75.75 0 00-1.06-1.06L10 8.94 8.28 7.22z"
            clipRule="evenodd"
          />
        </svg>
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold">
            {apiErr.code || "错误"}
          </h3>
          <p className="mt-1 text-sm">{apiErr.detail || error.message}</p>
          {apiErr.requestId && (
            <p className="mt-1 text-xs text-red-400 truncate">
              Request ID: {apiErr.requestId}
            </p>
          )}
        </div>
        {onRetry && (
          <button
            onClick={onRetry}
            className="shrink-0 rounded bg-red-600 px-3 py-1 text-sm text-white hover:bg-red-700 transition-colors"
          >
            重试
          </button>
        )}
      </div>
    </div>
  );
}
