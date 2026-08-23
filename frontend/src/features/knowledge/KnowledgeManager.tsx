"use client";

/** KnowledgeManager — 知识库管理（K-3）。
 *
 * 三块能力（消费 K-2 API）：
 * - 文档列表：scope 过滤（全部/本项目/全局）、块数与向量化状态、来源；
 * - 软删除：仅项目自有文档可删（全局灰显说明），删除后失效列表；
 * - 检索试算：输入查询 → 命中（相似度/作用域/来源/摘要）+ trace 元数据。
 *
 * 纯叶子组件：数据经 props 注入，便于测试。
 */

import Link from "next/link";
import { useState } from "react";
import type {
  KnowledgeDocumentItem,
  KnowledgeScope,
} from "@/types/api";

const SCOPE_LABEL: Record<KnowledgeScope, string> = {
  project: "本项目",
  global: "全局语料",
};

interface Props {
  projectId: string;
  documents: KnowledgeDocumentItem[];
  loading: boolean;
  error: string | null;
  scopeFilter: "project" | "global" | "all";
  onScopeFilterChange: (scope: "project" | "global" | "all") => void;
  onDelete: (documentId: string) => Promise<void>;
  deletingId: string | null;
  deleteError: string | null;
  onSearch: (query: string) => Promise<void>;
  searching: boolean;
  searchError: string | null;
  searchHits: import("@/types/api").KnowledgeSearchHit[];
  searchTrace: { corpus_version: string; elapsed_ms: number } | null;
}

export function KnowledgeManager({
  projectId,
  documents,
  loading,
  error,
  scopeFilter,
  onScopeFilterChange,
  onDelete,
  deletingId,
  deleteError,
  onSearch,
  searching,
  searchError,
  searchHits,
  searchTrace,
}: Props) {
  const [query, setQuery] = useState("");

  return (
    <section aria-label="知识库管理" className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold text-gray-900">知识库</h1>
        <p className="mt-1 text-sm text-gray-500">
          上传的参考资料自动入库参与创作检索；全局语料由平台统一维护。
        </p>
      </header>

      {/* 检索试算 */}
      <div className="rounded-lg border border-gray-200 bg-white p-5">
        <h2 className="mb-3 text-sm font-semibold text-gray-700">检索试算</h2>
        <div className="flex gap-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && query.trim()) void onSearch(query.trim());
            }}
            placeholder="输入查询（如：主角的人物关系）"
            aria-label="检索试算查询"
            data-testid="knowledge-search-input"
            className="flex-1 rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none"
          />
          <button
            type="button"
            disabled={searching || !query.trim()}
            onClick={() => void onSearch(query.trim())}
            data-testid="knowledge-search-button"
            className="touch-target rounded-lg bg-blue-600 px-4 text-sm font-medium text-white shadow-sm hover:bg-blue-700 disabled:opacity-50"
          >
            {searching ? "检索中…" : "试算"}
          </button>
        </div>
        {searchError && (
          <p className="mt-2 text-xs text-red-600" role="alert">{searchError}</p>
        )}
        {searchTrace && (
          <p className="mt-2 text-xs text-gray-400" data-testid="knowledge-search-trace">
            corpus {searchTrace.corpus_version} · {searchTrace.elapsed_ms}ms ·{" "}
            {searchHits.length} 条命中
          </p>
        )}
        {searchHits.length > 0 && (
          <ul className="mt-3 space-y-2" data-testid="knowledge-search-hits">
            {searchHits.map((hit) => (
              <li
                key={hit.chunk_id}
                className="rounded border border-gray-200 bg-gray-50 p-3"
              >
                <div className="flex items-center justify-between gap-2 text-xs">
                  <span className="font-medium text-gray-700">{hit.document_title}</span>
                  <span className="text-gray-400">
                    {SCOPE_LABEL[hit.scope]} · {hit.category} · {hit.source}
                  </span>
                </div>
                <p className="message-body mt-1 text-gray-600">{hit.content}</p>
                <p className="mt-1 text-xs text-blue-600">相似度 {hit.score.toFixed(4)}</p>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* 文档列表 */}
      <div className="rounded-lg border border-gray-200 bg-white p-5">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-gray-700">
            文档（{documents.length}）
          </h2>
          <div className="flex gap-1" role="group" aria-label="作用域过滤">
            {(["all", "project", "global"] as const).map((scope) => (
              <button
                key={scope}
                type="button"
                onClick={() => onScopeFilterChange(scope)}
                aria-pressed={scopeFilter === scope}
                data-testid={`knowledge-scope-${scope}`}
                className={`rounded px-2 py-1 text-xs transition-colors ${
                  scopeFilter === scope
                    ? "bg-blue-600 text-white"
                    : "border border-gray-200 text-gray-500 hover:border-blue-300"
                }`}
              >
                {scope === "all" ? "全部" : SCOPE_LABEL[scope]}
              </button>
            ))}
          </div>
        </div>

        {error && <p className="text-xs text-red-600" role="alert">{error}</p>}
        {deleteError && (
          <p className="mb-2 text-xs text-red-600" role="alert">{deleteError}</p>
        )}
        {loading ? (
          <p className="text-xs text-gray-400">正在加载…</p>
        ) : documents.length === 0 ? (
          <div className="py-6 text-center">
            <p className="text-sm text-gray-500">知识库还没有文档。</p>
            <p className="mt-1 text-xs text-gray-400">
              上传参考资料（导入分类为「参考资料」）会自动入库，参与创作检索。
            </p>
          </div>
        ) : (
          <ul className="space-y-2" data-testid="knowledge-doc-list">
            {documents.map((doc) => (
              <li
                key={doc.id}
                className="flex items-center justify-between gap-3 rounded border border-gray-200 p-3"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs ${
                        doc.scope === "project"
                          ? "bg-blue-50 text-blue-700"
                          : "bg-gray-100 text-gray-500"
                      }`}
                    >
                      {SCOPE_LABEL[doc.scope]}
                    </span>
                    <span className="truncate text-sm font-medium text-gray-800">
                      {doc.title}
                    </span>
                  </div>
                  <p className="mt-0.5 text-xs text-gray-400">
                    {doc.category} · 来源 {doc.source} · {doc.chunk_count} 块 ·{" "}
                    {doc.fully_embedded ? "向量齐全" : `已向量化 ${doc.embedded_chunk_count}/${doc.chunk_count}`}
                  </p>
                </div>
                {doc.scope === "project" ? (
                  <button
                    type="button"
                    onClick={() => void onDelete(doc.id)}
                    disabled={deletingId === doc.id}
                    data-testid={`knowledge-delete-${doc.id}`}
                    className="shrink-0 rounded border border-gray-200 px-2 py-1 text-xs text-gray-500 transition-colors hover:border-red-300 hover:text-red-600 disabled:opacity-50"
                  >
                    {deletingId === doc.id ? "删除中…" : "删除"}
                  </button>
                ) : (
                  <span className="shrink-0 text-xs text-gray-300">平台维护</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <Link
        href={`/projects/${projectId}`}
        className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700"
      >
        ← 返回项目工作台
      </Link>
    </section>
  );
}
