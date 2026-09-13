/** 导出前端共用常量（W1-05 后）。
 *
 * 历史说明：H-07 曾在浏览器把稿件内容序列化为 Markdown/DOCX 并下载，
 * W1-05 起导出全部走后端 ExportService——选择在请求接受时冻结为显式
 * Artifact ID，文件与 sha256 固化在 export_file Artifact 中重新下载字
 * 节一致。浏览器序列化路径（serializeExport/buildExportMarkdown 等）
 * 已无产品调用方，按"最后确认无调用再删除未用代码"移除；内容转义的
 * 安全回归由后端 tools/exporters/markdown 的测试承担。
 */

import type { ExportContentKind } from "@/types/api";

export const EXPORT_KIND_LABELS: Record<ExportContentKind, string> = {
  story_bible: "StoryBible",
  outline: "大纲",
  script: "剧本",
  evaluation: "评估",
  revision: "修订说明",
};

/** 触发浏览器下载（导出固定端点共用）：临时锚点点击，不离开当前页。 */
export function triggerDownload(url: string): void {
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}
