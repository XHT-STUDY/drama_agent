"use client";

/** 导出中心 — 导出历史与下载 (H-07).
 *
 * 纯展示组件：接收 ExportRecord[] 列表。重新下载基于实时数据重序列化
 * （由容器通过 onRedownload 触发），不在本组件内持有数据。
 */

import type { ExportRecord } from "@/types/api";
import { EXPORT_KIND_LABELS } from "@/lib/export";

/** 人类可读大小 */
function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function kindLabel(kinds: ExportRecord["kinds"]): string {
  return kinds.map((k) => EXPORT_KIND_LABELS[k]).join("、");
}

interface Props {
  records: ExportRecord[];
  onRedownload: (record: ExportRecord) => void;
  onClear: () => void;
}

export function ExportHistory({ records, onRedownload, onClear }: Props) {
  if (records.length === 0) {
    return (
      <section className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-4 py-6 text-center text-sm text-gray-500">
        暂无导出记录 —— 在上方选择内容并生成下载后，历史将显示在这里
      </section>
    );
  }

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-lg font-semibold text-gray-800">导出历史（{records.length}）</h2>
        <button
          type="button"
          onClick={onClear}
          className="text-xs text-gray-400 hover:text-red-600 transition-colors"
        >
          清空历史
        </button>
      </div>

      <ul className="space-y-2">
        {records.map((record) => (
          <li
            key={record.id}
            className="flex items-center justify-between gap-3 rounded-lg border border-gray-100 bg-gray-50/60 px-3 py-2.5"
          >
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="rounded bg-blue-100 px-1.5 py-0.5 text-xs font-medium text-blue-700">
                  {record.format === "markdown" ? "MD" : "DOCX"}
                </span>
                <span className="truncate text-sm font-medium text-gray-700">{record.filename}</span>
              </div>
              <div className="mt-0.5 text-xs text-gray-400">
                {new Date(record.exportedAt).toLocaleString("zh-CN")} · {kindLabel(record.kinds)} ·{" "}
                {formatSize(record.sizeBytes)}
              </div>
            </div>
            <button
              type="button"
              onClick={() => onRedownload(record)}
              className="shrink-0 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:border-blue-300 hover:text-blue-600 transition-colors"
            >
              重新下载
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
