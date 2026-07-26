"use client";

/** StoryBibleView — StoryBible 内容展示组件 (H-04).
 *
 * 展示完整的 StoryBible 设定，包括：
 * - 版本选择器（切换历史版本）
 * - 故事标题、类型、基调、一句话梗概
 * - 世界观设定
 * - 主角 / 反派 / 配角卡片
 * - 🔒 锁定事实（locked_facts 视觉上可识别）
 * - 长期伏笔 (long_term_payoffs) 与开放循环 (open_loops)
 * - 故事规则、合规备注
 */

import { CharacterCard } from "./CharacterCard";
import type { Artifact, StoryBibleContent } from "@/types/api";

// ============================================================
// 辅助组件
// ============================================================

/** 文本列表，空时显示占位 */
function TextList({
  items,
  emptyText,
  icon,
}: {
  items: string[] | undefined;
  emptyText: string;
  icon?: string;
}) {
  if (!items || items.length === 0) {
    return <p className="py-2 text-sm text-gray-400 italic">{emptyText}</p>;
  }
  return (
    <ul className="space-y-1.5">
      {items.map((item, i) => (
        <li key={i} className="flex items-start gap-2 text-sm text-gray-700">
          {icon && <span className="mt-0.5 shrink-0">{icon}</span>}
          <span>{item}</span>
        </li>
      ))}
    </ul>
  );
}

// ============================================================
// Props
// ============================================================

interface Props {
  content: StoryBibleContent;
  artifact: Artifact;
  versions: Artifact[];
  onVersionChange: (artifactId: string) => void;
}

// ============================================================
// StoryBibleView
// ============================================================

export function StoryBibleView({ content, artifact, versions, onVersionChange }: Props) {
  return (
    <div className="space-y-6">
      {/* 版本选择器 */}
      <VersionSelector
        artifact={artifact}
        versions={versions}
        onVersionChange={onVersionChange}
      />

      {/* 头部：标题 + 梗概 + 类型 */}
      <HeaderSection content={content} />

      {/* 世界观设定 */}
      {content.world_setting && (
        <Section title="🌍 世界观设定">
          <p className="text-sm leading-relaxed text-gray-700 whitespace-pre-wrap">
            {content.world_setting}
          </p>
        </Section>
      )}

      {/* 主要冲突与赌注 */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {content.main_conflict && (
          <Section title="⚔️ 主要冲突">
            <p className="text-sm text-gray-700">{content.main_conflict}</p>
          </Section>
        )}
        {content.stakes && (
          <Section title="🎯 赌注">
            <p className="text-sm text-gray-700">{content.stakes}</p>
          </Section>
        )}
      </div>

      {/* 角色卡片 */}
      <Section title="👥 角色设定">
        <div className="space-y-4">
          {content.protagonist && (
            <CharacterCard character={content.protagonist} roleLabel="主角" />
          )}
          {content.antagonist && (
            <CharacterCard character={content.antagonist} roleLabel="反派" />
          )}
          {content.supporting_characters && content.supporting_characters.length > 0 && (
            <>
              <h4 className="text-sm font-medium text-gray-600">配角</h4>
              {content.supporting_characters.map((char) => (
                <CharacterCard key={char.character_id} character={char} roleLabel="配角" />
              ))}
            </>
          )}
          {!content.protagonist && !content.antagonist && (!content.supporting_characters || content.supporting_characters.length === 0) && (
            <p className="py-4 text-center text-sm text-gray-400">暂无角色设定</p>
          )}
        </div>
      </Section>

      {/* 🔒 锁定事实 — 视觉上可识别 */}
      <Section title="🔒 锁定事实">
        {content.locked_facts && content.locked_facts.length > 0 ? (
          <div className="rounded-lg border-2 border-amber-200 bg-amber-50 p-4">
            <p className="mb-2 text-xs text-amber-600">
              以下事实已在创作过程中锁定，后续修订必须保持一致。
            </p>
            <TextList
              items={content.locked_facts}
              emptyText="暂无锁定事实"
              icon="🔒"
            />
          </div>
        ) : (
          <p className="py-2 text-sm text-gray-400 italic">暂无锁定事实</p>
        )}
      </Section>

      {/* 长期伏笔 */}
      <Section title="📌 长期伏笔 (Long-term Payoffs)">
        <TextList
          items={content.long_term_payoffs}
          emptyText="暂无长期伏笔"
        />
      </Section>

      {/* 开放循环 */}
      <Section title="🔗 开放循环 (Open Loops)">
        <TextList
          items={content.open_loops}
          emptyText="暂无开放循环"
        />
      </Section>

      {/* 故事规则 */}
      {content.story_rules && content.story_rules.length > 0 && (
        <Section title="📏 故事规则">
          <TextList items={content.story_rules} emptyText="暂无规则" />
        </Section>
      )}

      {/* 合规备注 */}
      {content.compliance_notes && content.compliance_notes.length > 0 && (
        <Section title="⚠️ 合规备注">
          <TextList items={content.compliance_notes} emptyText="暂无合规备注" />
        </Section>
      )}

      {/* 底部元信息 */}
      <div className="border-t border-gray-100 pt-4 text-xs text-gray-400">
        <span>版本 {artifact.version}</span>
        <span className="mx-2">·</span>
        <span>状态：{artifact.status}</span>
        <span className="mx-2">·</span>
        <span>Schema {artifact.content_schema_version}</span>
      </div>
    </div>
  );
}

// ============================================================
// 内部子组件
// ============================================================

/** 区块包裹 */
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <h3 className="mb-3 text-sm font-semibold text-gray-800">{title}</h3>
      {children}
    </div>
  );
}

/** 版本选择器 */
function VersionSelector({
  artifact,
  versions,
  onVersionChange,
}: {
  artifact: Artifact;
  versions: Artifact[];
  onVersionChange: (artifactId: string) => void;
}) {
  if (versions.length <= 1) return null;

  return (
    <div className="flex items-center gap-3 rounded-lg border border-gray-200 bg-white px-4 py-3">
      <label htmlFor="version-select" className="text-sm font-medium text-gray-600">
        版本：
      </label>
      <select
        id="version-select"
        className="rounded border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700 focus:border-blue-400 focus:outline-none"
        value={artifact.id}
        onChange={(e) => onVersionChange(e.target.value)}
      >
        {versions.map((v) => (
          <option key={v.id} value={v.id}>
            v{v.version} — {new Date(v.created_at).toLocaleString("zh-CN")}
            {v.status === "valid" ? " ✓" : v.status === "invalid" ? " ✗" : " ○"}
          </option>
        ))}
      </select>
    </div>
  );
}

/** 头部信息 */
function HeaderSection({ content }: { content: StoryBibleContent }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-5">
      <h1 className="text-xl font-bold text-gray-900">
        {content.title || <span className="text-gray-400 italic">未命名故事</span>}
      </h1>

      {content.logline && (
        <p className="mt-2 text-sm leading-relaxed text-gray-600">{content.logline}</p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-gray-500">
        {content.genre && (
          <span className="inline-flex rounded-full bg-purple-100 px-3 py-1 font-medium text-purple-700">
            {content.genre}
          </span>
        )}
        {content.tone && content.tone.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {content.tone.map((t, i) => (
              <span
                key={i}
                className="inline-flex rounded-full bg-blue-100 px-2 py-0.5 text-blue-700"
              >
                {t}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
