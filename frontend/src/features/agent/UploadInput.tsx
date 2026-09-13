"use client";

/** UploadInput — 原稿附件入口（W1-06）。
 *
 * 流程分两步、每步都是明确授权：
 * 1. 选文件 → POST /uploads（只解析存档，不调模型）→ 附件卡展示
 *    原名/字数/警告；
 * 2. 「识别并导入」→ 创建 action=import 的 Run（会做模型分类并可能
 *    入库创作管线）→ 轮询 Run 至终态 → 展示分类结果与后续动作。
 *
 * 诚实性边界：
 * - 分类未出来前不承诺"导入成功"；
 * - unknown 分类只说明无法判断，不自动触发生成；
 * - full_script 转换固定生成第 1 集新版本——已有第 1 集时展示明确说明；
 * - 转换失败（结构不足）如实显示，不伪造成功稿件。
 */

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { uploadsApi, runsApi } from "@/lib/api-client";
import type { Run, UploadRecord } from "@/types/api";

const MAX_BYTES = 10 * 1024 * 1024;
const ACCEPT = ".txt,.docx";

/** 导入分类结果（从分类 Artifact content 提取的字段） */
interface ClassificationView {
  content_type: string;
  confidence: number;
  reason: string;
}

const CONTENT_TYPE_LABEL: Record<string, string> = {
  idea_or_notes: "想法/笔记",
  outline: "大纲",
  full_script: "完整剧本",
  reference: "参考资料",
  unknown: "无法识别",
};

const ROUTE_HINT: Record<string, string> = {
  create: "可以基于此材料进入创作流程（生成故事设定与分集大纲）",
  evaluate: "已转换为第 1 集剧本，可评估或直接阅读",
  hold: "已归档到项目，未进入创作管线",
  needs_user_input: "无法判断内容类型，需要你补充说明",
};

interface Props {
  projectId: string;
  /** 分类确认后打开产物（画布跳转由工作台提供） */
  onOpenArtifact: (artifactId: string) => void;
}

export function UploadInput({ projectId, onOpenArtifact }: Props) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [importError, setImportError] = useState<string | null>(null);

  // 上传（解析存档，无模型）
  const uploadMutation = useMutation({
    mutationFn: (file: File) => uploadsApi.create(projectId, file),
    onError: (e) =>
      setUploadError(e instanceof Error ? e.message : "上传失败，请重试"),
  });

  // 已有上传记录（刷新后恢复附件卡）
  const uploadsQuery = useQuery({
    queryKey: ["project-uploads", projectId],
    queryFn: () => uploadsApi.list(projectId),
  });
  const latestUpload: UploadRecord | null =
    uploadsQuery.data?.items?.[0] ?? null;
  const upload: UploadRecord | null = uploadMutation.data ?? latestUpload;

  // 导入 Run（识别并导入）
  const importMutation = useMutation({
    mutationFn: async (): Promise<Run> =>
      // 幂等键按 upload 固定（W1-06：同文件重发/重试复用同一 Run——
      // 服务端收据幂等，不会重复扣一次模型分类）
      runsApi.create(projectId, {
        action: "import",
        config: { upload_id: upload!.id },
        idempotency_key: `import:${upload!.id}`,
      }),
    onError: (e) =>
      setImportError(e instanceof Error ? e.message : "导入发起失败"),
  });
  const importRunQuery = useQuery({
    queryKey: ["import-run", importMutation.data?.run_id],
    enabled: importMutation.data != null,
    queryFn: () => runsApi.get(importMutation.data!.run_id),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" ? 1500 : false;
    },
  });
  const importRun: Run | null = importRunQuery.data ?? importMutation.data ?? null;

  // 分类 Artifact（Run 完成后读取）
  const resultIdList = importRun?.result_artifact_ids ?? [];
  const classificationId = resultIdList[0] ?? null;
  const classificationQuery = useQuery({
    queryKey: ["import-classification", classificationId],
    enabled: classificationId !== null && importRun?.status === "completed",
    queryFn: async () => {
      const { artifactsApi } = await import("@/lib/api-client");
      return artifactsApi.getById(classificationId!);
    },
  });
  const scriptArtifactId = resultIdList[1] ?? null;

  // 终态刷新项目 Run 区/作品索引
  useEffect(() => {
    if (importRun?.status === "completed" || importRun?.status === "failed") {
      void queryClient.invalidateQueries({ queryKey: ["project-runs", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["project-scripts-index", projectId] });
    }
  }, [importRun?.status, projectId, queryClient]);

  function pickFile(file: File | null): void {
    setUploadError(null);
    setImportError(null);
    if (!file) return;
    if (file.size > MAX_BYTES) {
      setUploadError("文件超过 10MB 上限");
      return;
    }
    setSelectedName(file.name);
    uploadMutation.mutate(file);
  }

  const classification: ClassificationView | null =
    (classificationQuery.data?.content as unknown as ClassificationView | undefined) ?? null;
  const route: string | null = importRun?.route ?? null;
  const needsUserInput = importRun?.status === "needs_review";

  return (
    <section
      className="rounded-lg border border-dashed border-[var(--border)] bg-[var(--surface)] p-3"
      data-testid="upload-input"
    >
      <input
        ref={fileInputRef}
        type="file"
        accept={ACCEPT}
        className="hidden"
        data-testid="upload-file-input"
        onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
      />
      {!upload ? (
        <div className="flex items-center justify-between gap-2">
          <p className="text-xs text-[var(--text-muted)]">
            有现成的 TXT/DOCX 原稿？先附加文件（只解析存档，不会改动项目）。
          </p>
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploadMutation.isPending}
            className="shrink-0 rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs transition-colors hover:border-[var(--accent)] hover:text-[var(--accent)]"
            data-testid="upload-pick"
          >
            {uploadMutation.isPending ? "解析中…" : "📎 附加 TXT/DOCX"}
          </button>
        </div>
      ) : (
        <div className="space-y-2" data-testid="upload-card">
          <div className="flex items-center justify-between gap-2">
            <div className="min-w-0 text-xs">
              <p className="truncate font-medium text-[var(--text)]" title={upload.original_name}>
                {upload.original_name}
              </p>
              <p className="text-[var(--text-muted)]">
                已解析存档 · {upload.char_count.toLocaleString()} 字
                {upload.warnings.length > 0 ? ` · ${upload.warnings.length} 条警告` : ""}
              </p>
            </div>
            {importMutation.isPending || importRunQuery.isLoading ? (
              <span className="shrink-0 text-xs text-[var(--text-muted)]">发起导入…</span>
            ) : importRun == null ? (
              <button
                type="button"
                onClick={() => importMutation.mutate()}
                disabled={upload.parse_status !== "parsed"}
                className="shrink-0 rounded-lg bg-[var(--accent)] px-3 py-1.5 text-xs text-white disabled:opacity-50"
                data-testid="import-confirm"
              >
                识别并导入
              </button>
            ) : null}
          </div>

          {upload.warnings.length > 0 && (
            <ul className="list-disc pl-5 text-xs text-amber-600">
              {upload.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
          {uploadError && (
            <p className="text-xs text-red-600" data-testid="upload-error">{uploadError}</p>
          )}

          {/* 导入执行中 */}
          {importRun != null &&
            (importRun.status === "queued" || importRun.status === "running") && (
              <p className="text-xs text-[var(--text-muted)]" data-testid="import-running">
                正在识别内容类型（会调用模型分类）…
              </p>
            )}

          {/* unknown → needs_review：如实说明；提供"按想法创作"与放弃，
              不自动生成、不伪造分类 */}
          {needsUserInput && (
            <div
              className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs text-amber-700"
              data-testid="import-unknown"
            >
              <p>
                无法自动判断这份材料的内容类型。可以直接按「想法/笔记」基于它创作，
                或放弃这次导入（文件保留，可重新发起）。
              </p>
              <div className="mt-1.5 flex gap-2">
                <button
                  type="button"
                  onClick={() => importMutation.mutate()}
                  disabled={importMutation.isPending}
                  className="rounded border border-amber-300 bg-white px-2 py-1"
                  data-testid="unknown-as-idea"
                >
                  按想法创作（重新导入）
                </button>
                <button
                  type="button"
                  onClick={() => {
                    importMutation.reset();
                    setSelectedName(null);
                  }}
                  className="rounded border border-amber-300 bg-white px-2 py-1"
                  data-testid="unknown-dismiss"
                >
                  放弃本次导入
                </button>
              </div>
            </div>
          )}

          {/* 分类结果 */}
          {importRun?.status === "completed" && classification && (
            <div className="rounded-lg border border-[var(--border)] bg-[var(--surface-muted)] p-2.5" data-testid="import-result">
              <p className="text-xs font-medium">
                识别为：
                {CONTENT_TYPE_LABEL[classification.content_type] ?? classification.content_type}
                <span className="ml-2 text-[var(--text-muted)]">
                  置信度 {Math.round(classification.confidence * 100)}%
                </span>
              </p>
              <p className="mt-0.5 text-xs text-[var(--text-muted)]">{classification.reason}</p>
              <p className="mt-1 text-xs">
                {ROUTE_HINT[route ?? ""] ?? ""}
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                {/* full_script：阅读/评估转换稿；转换失败如实提示 */}
                {classification.content_type === "full_script" &&
                  (scriptArtifactId ? (
                    <>
                      <button
                        type="button"
                        onClick={() => onOpenArtifact(scriptArtifactId)}
                        className="rounded border border-[var(--accent)] px-2 py-1 text-xs text-[var(--accent)]"
                        data-testid="open-imported-script"
                      >
                        打开第 1 集剧本
                      </button>
                      <span className="self-center text-xs text-[var(--text-muted)]">
                        导入会生成第 1 集新版本（不可变版本，不覆盖旧稿）
                      </span>
                    </>
                  ) : (
                    <span className="text-xs text-amber-600">
                      该文件结构不足，未能转换为可评估的剧本（原文件已保留）。
                    </span>
                  ))}
                {/* outline / idea：基于此材料创作（走既有 create_script + upload_id 路径） */}
                {(classification.content_type === "outline" ||
                  classification.content_type === "idea_or_notes") && (
                  <CreateFromUpload
                    projectId={projectId}
                    uploadId={upload.id}
                    episodeCountHint={classification.content_type === "outline"}
                  />
                )}
                {classification.content_type === "reference" && (
                  <ReferenceIngestStatus projectId={projectId} />
                )}
              </div>
            </div>
          )}

          {importRun?.status === "failed" && (
            <p className="text-xs text-red-600" data-testid="import-failed">
              导入失败：{importRun.error_detail ?? importRun.error_code ?? "未知错误"}
            </p>
          )}
          {importError && (
            <p className="text-xs text-red-600" data-testid="import-error">{importError}</p>
          )}
        </div>
      )}
      {selectedName && !upload && !uploadMutation.isPending && (
        <p className="mt-1 text-xs text-[var(--text-muted)]">{selectedName}</p>
      )}
    </section>
  );
}

/** 基于此材料创作（outline/idea）：走既有 create_script + upload_id 路径 */
function CreateFromUpload({
  projectId,
  uploadId,
  episodeCountHint,
}: {
  projectId: string;
  uploadId: string;
  episodeCountHint: boolean;
}) {
  const queryClient = useQueryClient();
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState<string | null>(null);

  const createMutation = useMutation({
    mutationFn: async () => {
      // 服务端固定允许字段构造（upload_id 只作来源引用；集数用项目目标）
      const { projectsApi } = await import("@/lib/api-client");
      const project = await projectsApi.get(projectId);
      return runsApi.create(projectId, {
        action: "create_script",
        config: { upload_id: uploadId },
        options: {
          user_input: `基于上传的材料创作（upload:${uploadId.slice(0, 8)}）`,
          source_type: "txt",
          outline_count: project.target_episode_count,
          script_count: project.target_episode_count,
          // 大纲类材料：确认门停在大纲（先看大纲再写剧本，spec W1-06）
          ...(episodeCountHint ? { stop_after: "outline" } : {}),
        },
        idempotency_key: `create-from-upload:${uploadId}`,
      });
    },
    onSuccess: (created) => setRun(created),
    onError: (e) => setError(e instanceof Error ? e.message : "创作发起失败"),
  });

  const runQuery = useQuery({
    queryKey: ["create-from-upload-run", run?.run_id],
    enabled: run != null,
    queryFn: () => runsApi.get(run!.run_id),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" ? 2500 : false;
    },
  });
  const status = runQuery.data?.status ?? run?.status;

  useEffect(() => {
    if (status === "completed" || status === "needs_review") {
      void queryClient.invalidateQueries({ queryKey: ["project-runs", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["project-scripts-index", projectId] });
    }
  }, [status, projectId, queryClient]);

  return (
    <div className="w-full">
      {run == null ? (
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => createMutation.mutate()}
            disabled={createMutation.isPending}
            className="rounded border border-[var(--accent)] px-2 py-1 text-xs text-[var(--accent)]"
            data-testid="create-from-upload"
          >
            基于此材料创作
          </button>
          <span className="text-xs text-[var(--text-muted)]" data-testid="create-from-upload-scope">
            来源：这份上传 · 目标 {episodeCountHint ? "先生成大纲，你确认后再写剧本" : "按项目目标集数分阶段创作"}
          </span>
        </div>
      ) : (
        <span className="text-xs text-[var(--text-muted)]" data-testid="create-from-upload-status">
          创作任务{status === "queued" ? "排队中" : status === "running" ? "执行中" : `已${status === "needs_review" ? "停在确认门" : "完成"}`}——进度见上方状态区，产物完成后在作品导航打开。
        </span>
      )}
      {error && <p className="text-xs text-red-600">{error}</p>}
    </div>
  );
}

/** reference 入库状态（真实查询，不把"分类完成"当"检索可用"） */
function ReferenceIngestStatus({ projectId }: { projectId: string }) {
  const docsQuery = useQuery({
    queryKey: ["reference-ingest", projectId],
    queryFn: async () => {
      const { knowledgeApi } = await import("@/lib/api-client");
      return knowledgeApi.list(projectId, "all");
    },
    retry: false,
  });
  if (docsQuery.isError) {
    return (
      <span className="text-xs text-amber-600" data-testid="reference-status">
        资料入库状态确认失败——文件已保留；可稍后在知识库查看。
      </span>
    );
  }
  if (docsQuery.isLoading) {
    return (
      <span className="text-xs text-[var(--text-muted)]" data-testid="reference-status">
        正在确认资料入库状态…
      </span>
    );
  }
  const count = (docsQuery.data as { total?: number } | undefined)?.total ?? 0;
  return (
    <span className="text-xs text-[var(--text-muted)]" data-testid="reference-status">
      {count > 0
        ? `已入资料库（项目资料 ${count} 份），可用于创作检索。`
        : "尚未在资料库中找到该文档（入库可能失败，文件已保留）。"}
    </span>
  );
}
