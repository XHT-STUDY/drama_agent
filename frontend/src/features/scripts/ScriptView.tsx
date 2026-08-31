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

/** 台词行：`角色名（情绪）：台词` / `角色名VO（情绪）：旁白` / `角色名OS（情绪）：内心` */
function DialogueLineView({ line }: {
  line: { speaker: string; text: string; parenthetical?: string; line_type?: string };
}) {
  const suffix = line.line_type === "vo" ? "VO" : line.line_type === "os" ? "OS" : "";
  return (
    <p className="mt-1.5 text-sm leading-relaxed text-gray-800">
      <span className={`inline-flex rounded px-1.5 py-0.5 text-xs font-medium ${charColor(line.speaker)}`}>
        {line.speaker}{suffix}
      </span>
      {line.parenthetical && (
        <span className="ml-1 text-xs text-gray-500 italic">（{line.parenthetical}）</span>
      )}
      <span className="ml-1">{line.text}</span>
    </p>
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
                {/* 场景头：`1-1 日 外 冰封荒原-风雪坡` */}
                <div className="mb-1.5 flex items-center gap-2">
                  <span
                    className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-bold ${
                      isHighlighted
                        ? "bg-orange-500 text-white"
                        : "bg-gray-100 text-gray-600"
                    }`}
                  >
                    {scene.scene_number}
                  </span>
                  <span className="text-sm font-semibold tracking-wide text-gray-900">
                    {content.episode_number}-{scene.scene_number} {scene.time_of_day}{" "}
                    {scene.int_ext || "外"} {scene.location || "未指定地点"}
                  </span>
                </div>

                {/* 出场人物：`人物：A、B` */}
                {scene.characters && scene.characters.length > 0 && (
                  <div className="mb-2 text-sm text-gray-500">
                    人物：{scene.characters.join("、")}
                  </div>
                )}

                {/* 动作描述：每行加 △ 前缀 */}
                {scene.action &&
                  scene.action
                    .split("\n")
                    .map((a) => a.trim())
                    .filter((a) => a.length > 0)
                    .map((a, i) => (
                      <p key={i} className="text-sm leading-relaxed text-gray-700">
                        △{a}
                      </p>
                    ))}

                {/* 台词 */}
                {scene.dialogue && scene.dialogue.length > 0 && (
                  <div className="mt-1">
                    {scene.dialogue.map((line, i) => (
                      <DialogueLineView key={i} line={line} />
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
