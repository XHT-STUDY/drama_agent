"use client";

/** ScriptView — 剧本内容展示组件 (H-05).
 *
 * 中央区域，展示完整的单集剧本：
 * - 标题、开头钩子
 * - 按场景 (Scene) 渲染：地点、时间、出场角色、动作描述、对白
 * - Scene 锚点（`scene-N` id），支持 issue 点击定位
 * - 结尾钩子
 * - 剧本统计（字数 / 对白比例）
 */

import React from "react";
import type { ScriptDraftContent } from "@/types/api";

// ============================================================
// 辅助组件
// ============================================================

/** 角色标签颜色池（按名字 hash 取色） */
function charColor(name: string): string {
  const colors = [
    "bg-blue-100 text-blue-700",
    "bg-green-100 text-green-700",
    "bg-purple-100 text-purple-700",
    "bg-amber-100 text-amber-700",
    "bg-pink-100 text-pink-700",
    "bg-teal-100 text-teal-700",
  ];
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash);
  }
  return colors[Math.abs(hash) % colors.length];
}

/** 对白行 */
function DialogueBlock({ speaker, text, parenthetical }: {
  speaker: string;
  text: string;
  parenthetical?: string;
}) {
  return (
    <div className="ml-6 mt-1.5">
      <div className="flex items-baseline gap-2">
        <span className={`inline-flex rounded px-1.5 py-0.5 text-xs font-medium ${charColor(speaker)}`}>
          {speaker}
        </span>
        {parenthetical && (
          <span className="text-xs text-gray-400 italic">({parenthetical})</span>
        )}
      </div>
      <p className="mt-1 text-sm leading-relaxed text-gray-800">{text}</p>
    </div>
  );
}

// ============================================================
// Props
// ============================================================

interface Props {
  content: ScriptDraftContent;
  /** 高亮的 scene_number 集合（评估 issue 定位） */
  highlightedScenes?: number[];
}

// ============================================================
// ScriptView
// ============================================================

export function ScriptView({ content, highlightedScenes = [] }: Props) {
  const hlSet = new Set(highlightedScenes);

  return (
    <div className="space-y-4">
      {/* 剧集标题与元信息 */}
      <div className="rounded-lg border border-gray-200 bg-white p-5">
        <h1 className="text-lg font-bold text-gray-900">
          第 {content.episode_number} 集 · {content.title || "未命名"}
        </h1>
        {content.opening_hook && (
          <p className="mt-2 text-sm italic text-gray-500">
            「{content.opening_hook}」
          </p>
        )}
        {/* 统计 */}
        <div className="mt-3 flex gap-3 text-xs text-gray-400">
          {content.word_count > 0 && (
            <span>{content.word_count.toLocaleString()} 字</span>
          )}
          {content.dialogue_ratio > 0 && (
            <span>对白占比 {(content.dialogue_ratio * 100).toFixed(0)}%</span>
          )}
        </div>
      </div>

      {/* 场景列表 */}
      {content.scenes && content.scenes.length > 0 ? (
        <div className="space-y-3">
          {content.scenes.map((scene) => {
            const isHighlighted = hlSet.has(scene.scene_number);
            return (
              <div
                key={scene.scene_number}
                id={`scene-${scene.scene_number}`}
                className={`rounded-lg border bg-white p-5 transition-colors ${
                  isHighlighted
                    ? "border-orange-300 ring-2 ring-orange-100"
                    : "border-gray-200"
                }`}
              >
                {/* Scene 头部：编号 + 地点 + 时间 */}
                <div className="mb-3 flex items-center gap-3 border-b border-gray-100 pb-3">
                  <span
                    className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold ${
                      isHighlighted
                        ? "bg-orange-500 text-white"
                        : "bg-gray-100 text-gray-600"
                    }`}
                  >
                    {scene.scene_number}
                  </span>
                  <span className="text-sm font-medium text-gray-700">
                    {scene.location || "未指定地点"}
                  </span>
                  {scene.time_of_day && (
                    <span className="text-xs text-gray-400">
                      {scene.time_of_day}
                    </span>
                  )}
                  {scene.characters && scene.characters.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {scene.characters.map((name) => (
                        <span
                          key={name}
                          className={`inline-flex rounded px-1.5 py-0.5 text-xs font-medium ${charColor(name)}`}
                        >
                          {name}
                        </span>
                      ))}
                    </div>
                  )}
                </div>

                {/* 动作描述 */}
                {scene.action && (
                  <p className="mb-3 text-sm leading-relaxed text-gray-700 whitespace-pre-wrap">
                    {scene.action}
                  </p>
                )}

                {/* 对白 */}
                {scene.dialogue && scene.dialogue.length > 0 && (
                  <div className="space-y-1">
                    {scene.dialogue.map((line, i) => (
                      <DialogueBlock
                        key={i}
                        speaker={line.speaker}
                        text={line.text}
                        parenthetical={line.parenthetical}
                      />
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 p-8 text-center">
          <p className="text-sm text-gray-500">暂无场景内容</p>
        </div>
      )}

      {/* 结尾钩子 */}
      {content.ending_hook && (
        <div className="rounded-lg border border-gray-200 bg-gray-50 p-4">
          <span className="text-xs font-medium text-gray-500">🔚 结尾钩子</span>
          <p className="mt-1 text-sm italic text-gray-600">「{content.ending_hook}」</p>
        </div>
      )}
    </div>
  );
}
