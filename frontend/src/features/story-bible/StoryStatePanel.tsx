"use client";

/** StoryStatePanel — 剧情状态面板 (M-05 / W3-07).
 *
 * 面向作者的"剧情走到哪了"视图，只消费只读 story-state 查询：
 * - 状态徽章：ready(可用) / pending(派生中) / stale(待复核) /
 *   gap(缺正文) / missing(未建立)，附恢复入口；
 * - 基准：截至第 N 集 + 工作集(当前采用稿 / 指定 Run 快照)；
 * - 分账展示：作者事实(已发生,带来源)、角色已知信息、未闭合/已回收
 *   伏笔、道具归属、作者未来计划(显式标注"未发生")、锁定事实；
 * - 事实来源可跳转：第 X 集第 Y 场(定位到确切 Artifact)。
 *
 * 面向作者的语言；不出现"嵌入/摘要任务/Memory job"等内部概念。
 */

import type { StoryStateResponse } from "@/types/api";

// ============================================================
// 状态徽章
// ============================================================

const STATUS_META: Record<
  StoryStateResponse["status"],
  { label: string; tone: string; hint: string; recoverable: boolean }
> = {
  ready: {
    label: "可用",
    tone: "bg-emerald-100 text-emerald-800 border-emerald-300",
    hint: "剧情状态与当前采用稿一致",
    recoverable: false,
  },
  pending: {
    label: "派生中",
    tone: "bg-amber-100 text-amber-800 border-amber-300",
    hint: "后续集的剧情证据还在生成",
    recoverable: true,
  },
  stale: {
    label: "待复核",
    tone: "bg-orange-100 text-orange-800 border-orange-300",
    hint: "部分集的稿件已变化，相关剧情状态需要重新确认",
    recoverable: true,
  },
  gap: {
    label: "缺正文",
    tone: "bg-red-100 text-red-700 border-red-300",
    hint: "有集数还没有正文，剧情状态无法覆盖",
    recoverable: true,
  },
  missing: {
    label: "未建立",
    tone: "bg-gray-100 text-gray-600 border-gray-300",
    hint: "项目还没有剧情状态",
    recoverable: true,
  },
};

function StatusBadge({ status }: { status: StoryStateResponse["status"] }) {
  const meta = STATUS_META[status];
  return (
    <span
      role="status"
      aria-label={`剧情状态：${meta.label}`}
      className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium ${meta.tone}`}
    >
      {meta.label}
    </span>
  );
}

// ============================================================
// 分区小组件
// ============================================================

function Section({
  title,
  note,
  children,
}: {
  title: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-gray-200 bg-white p-3">
      <h4 className="text-sm font-semibold text-gray-800">{title}</h4>
      {note && <p className="mt-0.5 text-xs text-gray-400">{note}</p>}
      <div className="mt-2">{children}</div>
    </section>
  );
}

function Empty({ text }: { text: string }) {
  return <p className="py-1 text-sm text-gray-400 italic">{text}</p>;
}

export type OpenSourceHandler = (
  artifactId: string,
  episode: number,
  scene: number,
) => void;

/** 作者事实：带"第 X 集第 Y 场"来源跳转 */
function AuthorFactList({
  state,
  onOpenSource,
}: {
  state: StoryStateResponse;
  onOpenSource?: OpenSourceHandler;
}) {
  const facts = state.projection?.author_facts ?? [];
  if (facts.length === 0) return <Empty text="还没有从正文提取的已发生事实" />;
  return (
    <ul className="space-y-1.5" data-testid="author-facts">
      {facts.map((fact) => (
        <li key={fact.fact_id} className="text-sm text-gray-700">
          <span className="mr-1.5">·</span>
          {fact.text}
          {onOpenSource && (
            <button
              type="button"
              onClick={() =>
                onOpenSource(
                  fact.source_artifact_id,
                  fact.source_episode,
                  fact.source_scene,
                )
              }
              className="ml-1.5 rounded border border-gray-200 px-1.5 py-0.5 text-xs text-gray-500 hover:bg-gray-50"
              aria-label={`查看来源：第 ${fact.source_episode} 集第 ${fact.source_scene} 场`}
            >
              第 {fact.source_episode} 集第 {fact.source_scene} 场
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}

/** 角色已知信息：与作者事实分账（未列出的角色不知道） */
function CharacterKnowledgeList({ state }: { state: StoryStateResponse }) {
  const known = state.projection?.character_known_facts ?? [];
  if (known.length === 0) return <Empty text="暂无角色已知信息" />;
  return (
    <ul className="space-y-1.5" data-testid="character-knowledge">
      {known.map((entry) => (
        <li key={entry.character_id} className="text-sm text-gray-700">
          <span className="font-medium">{entry.character_id}</span> 已知道：
          {entry.facts.map((f) => (
            <span key={f.fact_id} className="mr-2 inline-block text-gray-500">
              {f.fact_id}（第 {f.learned_episode} 集得知）
            </span>
          ))}
        </li>
      ))}
    </ul>
  );
}

// ============================================================
// StoryStatePanel
// ============================================================

export interface StoryStatePanelProps {
  state: StoryStateResponse;
  /** 恢复入口（pending/stale/gap 时显示）：由宿主接到继续创作/刷新 Run */
  onRefresh?: () => void;
  /** 事实来源跳转：定位到确切 Artifact 与场次 */
  onOpenSource?: OpenSourceHandler;
}

export function StoryStatePanel({
  state,
  onRefresh,
  onOpenSource,
}: StoryStatePanelProps) {
  const meta = STATUS_META[state.status];
  const through = state.projection?.through_episode ?? state.through_episode ?? 0;
  const projection = state.projection;

  return (
    <div
      className="space-y-3 rounded-xl border border-gray-200 bg-gray-50 p-4"
      data-testid="story-state-panel"
    >
      {/* 头部：状态 + 基准 */}
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-gray-900">剧情状态</h3>
        <StatusBadge status={state.status} />
        <span className="text-xs text-gray-500" data-testid="story-state-basis">
          截至第 {through} 集
          {state.run_id ? " · 按 Run 工作集" : " · 按当前采用稿"}
          {state.stale_from_episode != null && state.status === "stale" && (
            <>（第 {state.stale_from_episode} 集起待复核）</>
          )}
        </span>
        {meta.recoverable && onRefresh && (
          <button
            type="button"
            onClick={onRefresh}
            className="ml-auto rounded-md bg-gray-900 px-2.5 py-1 text-xs font-medium text-white hover:bg-gray-700"
            data-testid="story-state-refresh"
          >
            刷新剧情状态
          </button>
        )}
      </div>
      <p className="text-xs text-gray-500">{meta.hint}</p>
      {state.warnings.length > 0 && (
        <ul className="space-y-0.5" data-testid="story-state-warnings">
          {state.warnings.map((w, i) => (
            <li key={i} className="text-xs text-amber-700">
              ⚠ {w}
            </li>
          ))}
        </ul>
      )}

      {projection && (
        <div className="grid gap-3 md:grid-cols-2">
          <Section title="已发生的事实（作者视角）" note="来自正文；点击来源定位到具体场次">
            <AuthorFactList state={state} onOpenSource={onOpenSource} />
          </Section>
          <Section title="角色已知信息" note="作者知道 ≠ 角色知道；未列出的角色不知道这些事">
            <CharacterKnowledgeList state={state} />
          </Section>
          <Section title="未闭合伏笔">
            {projection.open_loops.length === 0 ? (
              <Empty text="没有未闭合伏笔" />
            ) : (
              <ul data-testid="open-loops" className="space-y-1">
                {projection.open_loops.map((lp) => (
                  <li key={lp.loop_id} className="text-sm text-gray-700">
                    · {lp.description}
                  </li>
                ))}
              </ul>
            )}
            {projection.resolved_loops.length > 0 && (
              <p className="mt-1 text-xs text-gray-400">
                已回收 {projection.resolved_loops.length} 条
              </p>
            )}
          </Section>
          <Section title="道具归属">
            {projection.props.length === 0 ? (
              <Empty text="暂无道具记录" />
            ) : (
              <ul className="space-y-1">
                {projection.props.map((p) => (
                  <li key={p.prop_id} className="text-sm text-gray-700">
                    · {p.prop_id} → {p.holder_character_id}
                  </li>
                ))}
              </ul>
            )}
          </Section>
          <Section title="作者后续计划" note="尚未发生；不会当作已发生事实进入正文">
            {projection.future_plans.length === 0 ? (
              <Empty text="暂无后续计划" />
            ) : (
              <ul data-testid="future-plans" className="space-y-1">
                {projection.future_plans.map((plan, i) => (
                  <li key={i} className="text-sm text-gray-700">
                    · <span className="text-gray-400">（计划，第 {plan.reveal_episode} 集揭示）</span>{" "}
                    {plan.text}
                  </li>
                ))}
              </ul>
            )}
          </Section>
          <Section title="锁定事实（不可修改）">
            {projection.locked_facts.length === 0 ? (
              <Empty text="暂无锁定事实" />
            ) : (
              <ul className="space-y-1">
                {projection.locked_facts.map((fact, i) => (
                  <li key={i} className="text-sm text-gray-700">
                    🔒 {fact}
                  </li>
                ))}
              </ul>
            )}
          </Section>
        </div>
      )}
    </div>
  );
}
