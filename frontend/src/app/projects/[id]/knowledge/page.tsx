"use client";

/** 知识库页面（K-3）— 数据容器：加载/删除/检索试算，渲染 KnowledgeManager。 */

import { useState } from "react";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { knowledgeApi, ApiError } from "@/lib/api-client";
import { KnowledgeManager } from "@/features/knowledge/KnowledgeManager";
import type { KnowledgeSearchHit } from "@/types/api";

type ScopeFilter = "project" | "global" | "all";

export default function KnowledgePage() {
  const params = useParams();
  const projectId = String(params.id);
  const queryClient = useQueryClient();
  const [scopeFilter, setScopeFilter] = useState<ScopeFilter>("all");
  const [searchHits, setSearchHits] = useState<KnowledgeSearchHit[]>([]);
  const [searchTrace, setSearchTrace] = useState<{
    corpus_version: string;
    elapsed_ms: number;
  } | null>(null);

  const listQuery = useQuery({
    queryKey: ["knowledge", projectId, scopeFilter],
    queryFn: () => knowledgeApi.list(projectId, scopeFilter),
    retry: false,
  });

  const deleteMutation = useMutation({
    mutationFn: (documentId: string) =>
      knowledgeApi.delete(projectId, documentId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["knowledge", projectId] });
    },
  });

  const searchMutation = useMutation({
    mutationFn: (query: string) =>
      knowledgeApi.search(projectId, { query, top_k: 5 }),
    onSuccess: (response) => {
      setSearchHits(response.hits);
      setSearchTrace({
        corpus_version: response.corpus_version,
        elapsed_ms: response.elapsed_ms,
      });
    },
  });

  const apiError = (error: unknown): string => {
    if (error instanceof ApiError) return `${error.detail}（${error.code}）`;
    if (error instanceof Error) return error.message;
    return "请求失败，请重试";
  };

  return (
    <KnowledgeManager
      projectId={projectId}
      documents={listQuery.data?.items ?? []}
      loading={listQuery.isLoading}
      error={listQuery.isError ? apiError(listQuery.error) : null}
      scopeFilter={scopeFilter}
      onScopeFilterChange={setScopeFilter}
      onDelete={async (documentId) => {
        await deleteMutation.mutateAsync(documentId).catch(() => undefined);
      }}
      deletingId={deleteMutation.isPending ? deleteMutation.variables ?? null : null}
      deleteError={deleteMutation.isError ? apiError(deleteMutation.error) : null}
      onSearch={async (query: string) => {
        await searchMutation.mutateAsync(query).catch(() => undefined);
      }}
      searching={searchMutation.isPending}
      searchError={searchMutation.isError ? apiError(searchMutation.error) : null}
      searchHits={searchHits}
      searchTrace={searchTrace}
    />
  );
}
