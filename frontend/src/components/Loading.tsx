/** Loading 组件 — 加载中状态 (H-01).
 *
 * 用于数据加载过程中显示骨架/旋转器。
 */

export function Loading({ text = "加载中…" }: { text?: string }) {
  return (
    <div className="flex items-center justify-center py-12 text-gray-400">
      <svg
        className="mr-3 h-5 w-5 animate-spin"
        viewBox="0 0 24 24"
        fill="none"
      >
        <circle
          className="opacity-25"
          cx="12" cy="12" r="10"
          stroke="currentColor" strokeWidth="4"
        />
        <path
          className="opacity-75"
          fill="currentColor"
          d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
        />
      </svg>
      <span>{text}</span>
    </div>
  );
}
