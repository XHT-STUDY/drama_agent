"use client";

/** 导出中心 — 服务端导出历史（W1-05）.
 *
 * 历史条目是 export_file Artifact 本体：重新下载经后端固定端点取回
 * 导出时的那份文件（字节一致，sha256 记录在 content 中）——绝不用
 * 当前稿件重新序列化。服务端历史无"清空"：交付记录是审计事实。
 */

import { exportsApi } from "@/lib/api-client";
import { triggerDownload } from "@/lib/export";
import type { Artifact, ExportFileContent } from "@/types/api";

/** 人类可读大小 */
function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

interface Props {
  projectId: string;
  artifacts: Artifact[];
}

export function ExportHistory({ projectId, artifacts }: Props) {
  if (artifacts.length === 0) {
    return (
      <section className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-4 py-6 text-center text-sm text-gray-500">
        暂无导出记录 —— 在上方选择内容并生成下载后，固定版本的交付记录将显示在这里
      </section>
    );
  }

  const redownload = (artifactId: string): void => {
    triggerDownload(exportsApi.downloadUrl(artifactId, projectId));
  };

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4">
      <h2 className="mb-1 text-lg font-semibold text-gray-800">导出历史（{artifacts.length}）</h2>
      <p className="mb-3 text-xs text-gray-400">
        每条记录固定为导出时的稿件版本；重新下载得到与当时完全一致的文件。
      </p>

      <ul className="space-y-2">
        {artifacts.map((artifact) => {
          const content = artifact.content as unknown as ExportFileContent;
          return (
            <li
              key={artifact.id}
              className="flex items-center justify-between gap-3 rounded-lg border border-gray-100 bg-gray-50/60 px-3 py-2.5"
              data-testid="server-export-record"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="rounded bg-blue-100 px-1.5 py-0.5 text-xs font-medium text-blue-700">
                    {content.format === "markdown" ? "MD" : "DOCX"}
                  </span>
                  <span className="truncate text-sm text-gray-700" title={content.filename}>
                    {content.filename}
                  </span>
                </div>
                <div className="mt-0.5 flex flex-wrap gap-2 text-xs text-gray-400">
                  <span>{formatSize(content.size_bytes)}</span>
                  <span>{new Date(artifact.created_at).toLocaleString("zh-CN")}</span>
                  {content.source_artifact_ids.length > 0 && (
                    <span>依据 {content.source_artifact_ids.length} 份稿件版本</span>
                  )}
                </div>
                {(content.warnings ?? []).length > 0 && (
                  <div className="mt-1 text-xs text-amber-600">
                    导出时提示：{(content.warnings ?? []).join("；")}
                  </div>
                )}
              </div>
              <button
                type="button"
                onClick={() => redownload(artifact.id)}
                className="shrink-0 rounded-lg border border-gray-200 px-3 py-1.5 text-xs text-gray-600 transition-colors hover:border-blue-300 hover:text-blue-600"
              >
                重新下载
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
