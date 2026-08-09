"use client";

/** DiffView — 版本 Diff 展示组件 (H-06).
 *
 * 渲染 GET /artifacts/diff 返回的 ScriptDiff：
 * - mode=scene：按场景卡片展示变更（新增绿 / 删除红 / 修改琥珀 / 不变灰）
 * - mode=line：原稿无法结构化解析，回退渲染全文行对比
 * - 大 diff 防卡死：后端已按 >2000 行截断；场景卡片仅在展开时渲染行明细
 * - 截断分级：diff.truncated（全局）/ scene.line_changes_truncated（单场景）
 */

import { useState } from "react";
import type { LineChange, SceneChange, ScriptDiff, SceneChangeType } from "@/types/api";

/** 场景数超过该阈值时默认折叠（防大 diff 一次性建 DOM） */
const AUTO_OPEN_LIMIT = 20;

// ============================================================
// 变更类型 → 徽章样式
// ============================================================

const CHANGE_BADGES: Record<SceneChangeType, { label: string; cls: string }> = {
  added: { label: "新增", cls: "bg-green-100 text-green-700" },
  removed: { label: "删除", cls: "bg-red-100 text-red-700" },
  modified: { label: "修改", cls: "bg-amber-100 text-amber-700" },
  unchanged: { label: "不变", cls: "bg-gray-100 text-gray-500" },
};

/** 单行变更展示（新增绿 / 删除红 / 修改两行堆叠） */
function LineChangeRow({ change }: { change: LineChange }) {
  const lineNo =
    change.change_type === "added" ? change.new_line_number : change.old_line_number;
  const text =
    change.change_type === "added" ? change.new_text : change.old_text;

  return (
    <div className="space-y-1 py-1">
      {/* 修改：旧行（红 + 删除线）在上，新行（绿）在下 */}
      {change.change_type === "modified" && (
        <>
          <div className="flex gap-2 rounded bg-red-50 px-2 py-0.5 text-xs text-red-700 line-through">
            <span className="w-12 shrink-0 text-right font-mono text-red-400">
              L{change.old_line_number ?? "?"}
            </span>
            <span className="whitespace-pre-wrap">{change.old_text || "（空）"}</span>
          </div>
          <div className="flex gap-2 rounded bg-green-50 px-2 py-0.5 text-xs text-green-800">
            <span className="w-12 shrink-0 text-right font-mono text-green-500">
              L{change.new_line_number ?? "?"}
            </span>
            <span className="whitespace-pre-wrap">{change.new_text || "（空）"}</span>
          </div>
        </>
      )}

      {/* 新增 / 删除：单行 */}
      {change.change_type !== "modified" && (
        <div
          className={`flex gap-2 rounded px-2 py-0.5 text-xs ${
            change.change_type === "added"
              ? "bg-green-50 text-green-800"
              : "bg-red-50 text-red-700 line-through"
          }`}
        >
          <span
            className={`w-12 shrink-0 text-right font-mono ${
              change.change_type === "added" ? "text-green-500" : "text-red-400"
            }`}
          >
            L{lineNo ?? "?"}
          </span>
          <span className="whitespace-pre-wrap">{text || "（空）"}</span>
        </div>
      )}
    </div>
  );
}

// ============================================================
// 单场景变更卡片（受控 <details>，body 惰性渲染）
// ============================================================

function SceneCard({ scene, defaultOpen }: { scene: SceneChange; defaultOpen: boolean }) {
  const badge = CHANGE_BADGES[scene.change_type];
  const sceneLabel =
    scene.old_scene_number !== null && scene.new_scene_number !== null
      ? scene.old_scene_number === scene.new_scene_number
        ? `第 ${scene.new_scene_number} 场`
        : `第 ${scene.old_scene_number} → ${scene.new_scene_number} 场`
      : scene.new_scene_number !== null
        ? `第 ${scene.new_scene_number} 场`
        : scene.old_scene_number !== null
          ? `第 ${scene.old_scene_number} 场`
          : "场景";

  // 受控 open：仅当展开时才渲染行明细，避免大 diff 一次性建 DOM。
  // 用按钮 + 显式 onClick 折叠/展开（不用 <details> 原生 onToggle——
  // jsdom 不触发 toggle 事件，且真实浏览器行为也不一致）。
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className="rounded-lg border border-gray-200 bg-white">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full cursor-pointer select-none items-center gap-3 px-4 py-3 text-left hover:bg-gray-50"
      >
        <span className={`inline-flex rounded px-1.5 py-0.5 text-xs font-medium ${badge.cls}`}>
          {badge.label}
        </span>
        <span className="text-sm font-medium text-gray-700">{sceneLabel}</span>
        <span className="text-xs text-gray-400">
          {[scene.location, scene.time_of_day].filter(Boolean).join(" · ") || "未知地点"}
        </span>
        <span className="ml-auto text-xs text-gray-400">
          相似度 {Math.round(scene.similarity * 100)}% · +{scene.added_lines} −{scene.removed_lines} ~{scene.modified_lines} 行
        </span>
      </button>

      {/* 仅在展开时渲染（防卡死关键） */}
      {open && (
        <div className="border-t border-gray-100 px-4 py-2">
          {scene.line_changes_truncated && (
            <p className="py-1 text-xs text-amber-600">该场景行明细已截断，仅显示场景级摘要</p>
          )}
          {scene.change_type === "unchanged" && (
            <p className="py-1 text-xs text-gray-400">该场景内容未变化</p>
          )}
          {scene.line_changes.length === 0 && !scene.line_changes_truncated && scene.change_type !== "unchanged" && (
            <p className="py-1 text-xs text-gray-400">该场景无逐行明细（整体{CHANGE_BADGES[scene.change_type].label}）</p>
          )}
          {scene.line_changes.map((lc, i) => (
            <LineChangeRow key={i} change={lc} />
          ))}
        </div>
      )}
    </div>
  );
}

// ============================================================
// DiffView
// ============================================================

interface Props {
  diff: ScriptDiff;
}

export function DiffView({ diff }: Props) {
  const isLine = diff.mode === "line";
  const hasChanges =
    diff.scene_changes.length > 0 || diff.line_changes.length > 0;

  return (
    <div className="space-y-3">
      {/* mode=line 回退横幅 */}
      {isLine && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-700">
          原稿无法结构化解析，已回退为全文行对比
        </div>
      )}

      {/* 全局截断提示 */}
      {diff.truncated && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-700">
          变更行过多（超过 2000 行），已截断行明细，仅保留场景级摘要
        </div>
      )}

      {/* 统计行 */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-gray-200 bg-white px-4 py-2 text-xs text-gray-600">
        <span>
          变更比例{" "}
          <span className="font-bold text-gray-900">
            {(diff.change_ratio * 100).toFixed(1)}%
          </span>
        </span>
        {!isLine && (
          <span className="text-gray-400">
            场景 {diff.scene_summary.from_scene_count} → {diff.scene_summary.to_scene_count}
          </span>
        )}
        <span className="text-gray-400">
          新增 {diff.stats.added_lines} 行 · 删除 {diff.stats.removed_lines} 行 · 修改 {diff.stats.modified_lines} 行
        </span>
        <span className="text-gray-400">
          字符 增 {diff.stats.added_chars} · 删 {diff.stats.removed_chars}
        </span>
      </div>

      {/* 场景级摘要（scene 模式） */}
      {!isLine && (
        <div className="flex flex-wrap gap-2 text-xs">
          <span className="rounded bg-green-100 px-2 py-0.5 text-green-700">新增 {diff.scene_summary.added}</span>
          <span className="rounded bg-red-100 px-2 py-0.5 text-red-700">删除 {diff.scene_summary.removed}</span>
          <span className="rounded bg-amber-100 px-2 py-0.5 text-amber-700">修改 {diff.scene_summary.modified}</span>
          <span className="rounded bg-gray-100 px-2 py-0.5 text-gray-500">未变 {diff.scene_summary.unchanged}</span>
        </div>
      )}

      {/* 变更明细 */}
      {isLine ? (
        <div className="space-y-0.5 rounded-lg border border-gray-200 bg-white px-4 py-2">
          {diff.line_changes.map((lc, i) => (
            <LineChangeRow key={i} change={lc} />
          ))}
        </div>
      ) : (
        <div className="space-y-2">
          {diff.scene_changes.map((sc, i) => (
            <SceneCard key={i} scene={sc} defaultOpen={diff.scene_changes.length <= AUTO_OPEN_LIMIT} />
          ))}
        </div>
      )}

      {/* 空 diff */}
      {!hasChanges && !diff.truncated && (
        <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-4 py-6 text-center text-sm text-gray-500">
          两个版本无差异
        </div>
      )}
    </div>
  );
}
