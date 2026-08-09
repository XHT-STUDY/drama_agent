"use client";

/** 导出中心 — 内容选择与生成下载 (H-07).
 *
 * 纯叶子组件：接收已组装好的 ExportData，提供「内容类型多选 + 格式单选 +
 * 生成并下载」。生成走客户端本地序列化（lib/export），成功后通过
 * onExported 回传一条 ExportRecord 供历史记录使用。
 */

import { useState } from "react";
import type { ExportContentKind, ExportFormat, ExportRecord } from "@/types/api";
import { EXPORT_KIND_LABELS, serializeExport, downloadBlob, type ExportData } from "@/lib/export";

/** 各内容类型的可用性（无数据时置灰） */
function availability(data: ExportData): Record<ExportContentKind, boolean> {
  return {
    story_bible: data.storyBible !== null,
    outline: data.outline !== null,
    script: data.scripts.length > 0,
    evaluation: data.evaluations.length > 0,
    revision: data.revisions.length > 0,
  };
}

/** 各内容类型的描述文案（含可用计数） */
function describe(kind: ExportContentKind, data: ExportData): string {
  switch (kind) {
    case "story_bible":
      return data.storyBible ? `《${data.storyBible.title}》世界观与人物设定` : "尚未生成";
    case "outline":
      return data.outline ? `共 ${data.outline.episodes.length} 集大纲` : "尚未生成";
    case "script":
      return data.scripts.length > 0 ? `已写前 ${data.scripts.length} 集剧本` : "尚无剧本";
    case "evaluation":
      return data.evaluations.length > 0 ? `已评估 ${data.evaluations.length} 集` : "尚无评估";
    case "revision":
      return data.revisions.length > 0 ? `共 ${data.revisions.length} 份修订说明` : "暂无修订";
  }
}

const ALL_KINDS: ExportContentKind[] = ["story_bible", "outline", "script", "evaluation", "revision"];

interface Props {
  data: ExportData;
  onExported: (record: ExportRecord) => void;
}

export function ExportSection({ data, onExported }: Props) {
  const [selected, setSelected] = useState<Set<ExportContentKind>>(
    () => new Set(ALL_KINDS),
  );
  const [format, setFormat] = useState<ExportFormat>("markdown");
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const avail = availability(data);
  const hasSelection = selected.size > 0;

  const toggleKind = (kind: ExportContentKind): void => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) {
        next.delete(kind);
      } else {
        next.add(kind);
      }
      return next;
    });
  };

  const handleExport = async (): Promise<void> => {
    if (!hasSelection || exporting) return;
    setExporting(true);
    setError(null);
    try {
      const result = await serializeExport({
        data,
        kinds: ALL_KINDS.filter((k) => selected.has(k)),
        format,
      });
      downloadBlob(result.filename, result.blob);
      onExported({
        id:
          typeof crypto !== "undefined" && "randomUUID" in crypto
            ? crypto.randomUUID()
            : `export-${Date.now()}`,
        exportedAt: result.exportedAt,
        format,
        kinds: ALL_KINDS.filter((k) => selected.has(k)),
        filename: result.filename,
        sizeBytes: result.blob.size,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "导出失败，请重试");
    } finally {
      setExporting(false);
    }
  };

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4">
      <h2 className="mb-3 text-lg font-semibold text-gray-800">选择导出内容</h2>

      {/* 内容类型多选 */}
      <div className="mb-4 space-y-2">
        {ALL_KINDS.map((kind) => {
          const checked = selected.has(kind);
          const has = avail[kind];
          return (
            <label
              key={kind}
              className={`flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-2.5 transition-colors ${
                checked
                  ? "border-blue-300 bg-blue-50/50"
                  : "border-gray-200 hover:border-gray-300"
              } ${has ? "" : "opacity-60"}`}
            >
              <input
                type="checkbox"
                checked={checked}
                onChange={() => toggleKind(kind)}
                disabled={!has}
                className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-400"
              />
              <span className="text-sm font-medium text-gray-700">
                {EXPORT_KIND_LABELS[kind]}
              </span>
              <span className="text-xs text-gray-400">{describe(kind, data)}</span>
            </label>
          );
        })}
      </div>

      {/* 格式单选 */}
      <div className="mb-4">
        <span className="mb-2 block text-xs font-medium text-gray-600">导出格式</span>
        <div className="flex gap-4">
          {(["markdown", "docx"] as ExportFormat[]).map((f) => (
            <label
              key={f}
              className={`flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm ${
                format === f
                  ? "border-blue-300 bg-blue-50/50 text-blue-700"
                  : "border-gray-200 text-gray-600 hover:border-gray-300"
              }`}
            >
              <input
                type="radio"
                name="export-format"
                value={f}
                checked={format === f}
                onChange={() => setFormat(f)}
                className="h-4 w-4 text-blue-600 focus:ring-blue-400"
              />
              {f === "markdown" ? "Markdown (.md)" : "Word (.docx)"}
            </label>
          ))}
        </div>
      </div>

      {/* 生成下载 */}
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={handleExport}
          disabled={!hasSelection || exporting}
          className="rounded-lg bg-blue-600 px-5 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 transition-colors disabled:cursor-not-allowed disabled:opacity-50"
        >
          {exporting ? "正在生成…" : "📦 生成并下载"}
        </button>
        {error && <span className="text-xs text-red-600">{error}</span>}
      </div>

      {!hasSelection && (
        <p className="mt-2 text-xs text-amber-600">请至少选择一种要导出的内容</p>
      )}
    </section>
  );
}
