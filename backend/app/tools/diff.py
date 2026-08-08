"""确定性 Diff 工具 — 场景对齐 + 行 diff + change_ratio (F-04).

纯 Python 实现，不调用 LLM。domain/diff.py 只放数据模型，算法在此：
- diff_lines: SequenceMatcher opcode → 行级变化明细与统计
- diff_script_drafts: 场景感知 diff（mode="scene"），两阶段场景对齐
- diff_texts: 无法解析 ScriptDraft 时的全文行 diff（mode="line"）
- compute_change_ratio / check_change_ratio: 对称变化比例与门禁判定
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from app.domain.diff import (
    DiffLineStats,
    LineChange,
    SceneChange,
    SceneChangeType,
    SceneDiffSummary,
    ScriptDiff,
)
from app.domain.script import Scene, ScriptDraft
from app.tools.protocol import Tool, ToolMetadata

# ---- 阈值常量 ----
MIN_SCENE_SIMILARITY = 0.35  # 阶段二：低于此不视为匹配（整场重写 → removed+added）
ANCHOR_SCENE_SIMILARITY = 0.60  # 阶段一：编号相同且相似度达此值视为确定匹配
MAX_DIFF_LINE_CHANGES = 2000  # 变更行数超过此值 → truncated=True 限制响应体


# ---- 内部工具 ----

def _scene_lines(scene: Scene) -> list[str]:
    """把 Scene 转成与 plain_text 同构的行列表（确定性，不依赖 plain_text 渲染）。"""
    lines = [f"【第{scene.scene_number}场 {scene.location} {scene.time_of_day}】", scene.action]
    lines.extend(f"{d.speaker}：{d.text}" for d in scene.dialogue)
    return lines


# ---- 行级 diff 核心 ----


# ---- 行级 diff 核心 ----

def diff_lines(
    old_lines: list[str], new_lines: list[str]
) -> tuple[list[LineChange], DiffLineStats]:
    """对两段行列表做 SequenceMatcher diff。

    replace 块：m 旧行 / n 新行，配对 min(m, n) 行为 "modified"，
    多余旧行为 "removed"、多余新行为 "added"。
    字符统计 replace 块两侧全计（removed_chars 含全部旧行、added_chars 含全部新行），
    避免 change_ratio 低估。

    Returns:
        (LineChange 列表, DiffLineStats)
    """
    matcher = SequenceMatcher(None, old_lines, new_lines)
    changes: list[LineChange] = []
    added_lines = removed_lines = modified_lines = 0
    added_chars = removed_chars = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                changes.append(
                    LineChange(
                        change_type="unchanged",
                        old_line_number=i1 + k + 1,
                        new_line_number=j1 + k + 1,
                        old_text=old_lines[i1 + k],
                        new_text=new_lines[j1 + k],
                    )
                )
        elif tag == "insert":
            for k in range(j2 - j1):
                text = new_lines[j1 + k]
                changes.append(
                    LineChange(
                        change_type="added",
                        new_line_number=j1 + k + 1,
                        new_text=text,
                    )
                )
                added_lines += 1
                added_chars += len(text)
        elif tag == "delete":
            for k in range(i2 - i1):
                text = old_lines[i1 + k]
                changes.append(
                    LineChange(
                        change_type="removed",
                        old_line_number=i1 + k + 1,
                        old_text=text,
                    )
                )
                removed_lines += 1
                removed_chars += len(text)
        elif tag == "replace":
            m = i2 - i1
            n = j2 - j1
            paired = min(m, n)
            for k in range(paired):
                changes.append(
                    LineChange(
                        change_type="modified",
                        old_line_number=i1 + k + 1,
                        new_line_number=j1 + k + 1,
                        old_text=old_lines[i1 + k],
                        new_text=new_lines[j1 + k],
                    )
                )
                modified_lines += 1
            for k in range(paired, m):
                text = old_lines[i1 + k]
                changes.append(
                    LineChange(
                        change_type="removed",
                        old_line_number=i1 + k + 1,
                        old_text=text,
                    )
                )
                removed_lines += 1
            for k in range(paired, n):
                text = new_lines[j1 + k]
                changes.append(
                    LineChange(
                        change_type="added",
                        new_line_number=j1 + k + 1,
                        new_text=text,
                    )
                )
                added_lines += 1
            # replace 块字符统计：两侧全计
            removed_chars += sum(len(old_lines[i1 + k]) for k in range(m))
            added_chars += sum(len(new_lines[j1 + k]) for k in range(n))
        else:  # pragma: no cover - difflib 仅产出以上四种 tag
            raise AssertionError(f"unexpected opcode tag: {tag}")

    stats = DiffLineStats(
        added_lines=added_lines,
        removed_lines=removed_lines,
        modified_lines=modified_lines,
        added_chars=added_chars,
        removed_chars=removed_chars,
        changed_chars=added_chars + removed_chars,
        from_chars=sum(len(line) for line in old_lines),
        to_chars=sum(len(line) for line in new_lines),
    )
    return changes, stats


def _pair_matched_chars(a: str, b: str) -> int:
    """单对字符串的匹配字符数（一侧口径，与 SequenceMatcher.ratio 的 M 一致）。

    短串逐对计算（autojunk=False 避免高频中文字符被当垃圾丢弃），
    避免对整段长中文文本做 SequenceMatcher 的 O(n²) 与相似度虚低。
    """
    matcher = SequenceMatcher(None, a, b, autojunk=False)
    return sum(block.size for block in matcher.get_matching_blocks())


def _similarity(a_lines: list[str], b_lines: list[str]) -> float:
    """文本相似度（匹配字符占比，范围 [0,1]，对称）。

    先做行级对齐（SequenceMatcher 对可哈希行列表 O(n)），
    再对对齐后的行逐对做字符级匹配——等效于整段 ratio，
    但避开 autojunk 把长中文文本中高频字符当垃圾导致的相似度虚低。
    空对空视为 1.0。
    """
    if not a_lines and not b_lines:
        return 1.0
    changes, _ = diff_lines(a_lines, b_lines)
    matched = 0
    for change in changes:
        if change.change_type == "unchanged":
            matched += len(change.old_text or "")
        elif change.change_type == "modified":
            matched += _pair_matched_chars(change.old_text or "", change.new_text or "")
    from_chars = sum(len(line) for line in a_lines)
    to_chars = sum(len(line) for line in b_lines)
    return 2 * matched / max(1, from_chars + to_chars)


def compute_change_ratio(stats: DiffLineStats) -> float:
    """对称 change_ratio：(removed_chars + added_chars) / max(1, from_chars + to_chars)。

    范围 [0,1]；A/B 颠倒时 removed/added 互换、分子分母不变 → 方向无关。
    与 RevisionPlan.max_change_ratio（默认 0.35）语义对齐。
    """
    denominator = max(1, stats.from_chars + stats.to_chars)
    return (stats.removed_chars + stats.added_chars) / denominator


def check_change_ratio(change_ratio: float, max_change_ratio: float) -> bool:
    """判定 change_ratio 是否未超过上限（<=，相等不超限）。

    F-05 Revision Gate 复用此函数：文本变化比例不超过
    RevisionPlan.max_change_ratio 时放行。
    """
    return change_ratio <= max_change_ratio


# ---- 场景对齐 ----

def _align_scenes(
    from_draft: ScriptDraft, to_draft: ScriptDraft
) -> list[tuple[Scene | None, Scene | None]]:
    """两阶段场景对齐，返回按 to 顺序的有序匹配表（A 场景 | B 场景）。

    阶段一（编号锚定）：scene_number 相同且内容相似度 >= ANCHOR_SCENE_SIMILARITY
    视为确定匹配，覆盖"改台词/改动作但编号不变"。
    阶段二（内容兜底）：未匹配场景按原序做 Needleman-Wunsch 加权序列比对
    （得分 = SequenceMatcher.ratio），回溯要求 >= MIN_SCENE_SIMILARITY，
    解决中间插入/删除导致的编号位移。

    Returns:
        [(Scene | None, Scene | None), ...]，None 表示该侧无对应场景
        （added 场景 A 侧为 None；removed 场景 B 侧为 None）。
    """
    a = from_draft.scenes
    b = to_draft.scenes

    # 阶段一：编号锚定
    a_to_b: dict[int, int] = {}  # A 索引 → B 索引
    used_b: set[int] = set()
    for i, scene_a in enumerate(a):
        idx = scene_a.scene_number - 1
        if 0 <= idx < len(b) and _similarity(
            _scene_lines(scene_a), _scene_lines(b[idx])
        ) >= ANCHOR_SCENE_SIMILARITY:
            a_to_b[i] = idx
            used_b.add(idx)

    # 阶段二：未匹配场景的有序加权比对（Needleman-Wunsch，gap=0）
    rest_a = [i for i in range(len(a)) if i not in a_to_b]
    rest_b = [j for j in range(len(b)) if j not in used_b]
    if rest_a and rest_b:
        n_a, n_b = len(rest_a), len(rest_b)
        score = [[0.0] * (n_b + 1) for _ in range(n_a + 1)]
        for i in range(1, n_a + 1):
            for j in range(1, n_b + 1):
                sim = _similarity(
                    _scene_lines(a[rest_a[i - 1]]), _scene_lines(b[rest_b[j - 1]])
                )
                score[i][j] = max(score[i - 1][j], score[i][j - 1], score[i - 1][j - 1] + sim)

        # 回溯：仅采纳相似度达标的匹配
        i, j = n_a, n_b
        while i > 0 and j > 0:
            sim = _similarity(
                _scene_lines(a[rest_a[i - 1]]), _scene_lines(b[rest_b[j - 1]])
            )
            if score[i][j] == score[i - 1][j - 1] + sim and sim >= MIN_SCENE_SIMILARITY:
                a_to_b[rest_a[i - 1]] = rest_b[j - 1]
                used_b.add(rest_b[j - 1])
                i -= 1
                j -= 1
            elif score[i][j] == score[i - 1][j]:
                i -= 1
            else:
                j -= 1

    # 按 B 顺序生成有序匹配表，缺失的 A 场景（removed）顺位补入
    b_to_a = {v: k for k, v in a_to_b.items()}
    out: list[tuple[Scene | None, Scene | None]] = []
    a_cursor = 0
    for j, scene_b in enumerate(b):
        if j in b_to_a:
            i = b_to_a[j]
            while a_cursor < i:
                out.append((a[a_cursor], None))
                a_cursor += 1
            out.append((a[i], scene_b))
            a_cursor += 1
        else:
            out.append((None, scene_b))
    while a_cursor < len(a):
        out.append((a[a_cursor], None))
        a_cursor += 1
    return out


# ---- 公开 Diff 入口 ----

def diff_script_drafts(
    from_draft: ScriptDraft,
    to_draft: ScriptDraft,
    *,
    max_line_changes: int = MAX_DIFF_LINE_CHANGES,
) -> ScriptDiff:
    """结构化场景 diff（mode="scene"）。

    Artifact 元数据字段（from_artifact_id 等）留 None，由 diff_service 填充。
    变更行总数超过 max_line_changes 时 truncated=True 并清空行明细。
    """
    aligned = _align_scenes(from_draft, to_draft)

    scene_changes: list[SceneChange] = []
    total_added = total_removed = total_modified = 0
    total_added_chars = total_removed_chars = 0
    n_added = n_removed = n_modified = n_unchanged = 0

    for scene_a, scene_b in aligned:
        if scene_a is None:
            # 新增场景（对齐保证 scene_b 非空）
            assert scene_b is not None
            lines = _scene_lines(scene_b)
            added_chars = sum(len(line) for line in lines)
            total_added += len(lines)
            total_added_chars += added_chars
            n_added += 1
            scene_changes.append(
                SceneChange(
                    change_type="added",
                    new_scene_number=scene_b.scene_number,
                    location=scene_b.location,
                    time_of_day=scene_b.time_of_day,
                    similarity=0.0,
                    added_lines=len(lines),
                    added_chars=added_chars,
                )
            )
        elif scene_b is None:
            # 删除场景（对齐保证 scene_a 非空）
            assert scene_a is not None
            lines = _scene_lines(scene_a)
            removed_chars = sum(len(line) for line in lines)
            total_removed += len(lines)
            total_removed_chars += removed_chars
            n_removed += 1
            scene_changes.append(
                SceneChange(
                    change_type="removed",
                    old_scene_number=scene_a.scene_number,
                    location=scene_a.location,
                    time_of_day=scene_a.time_of_day,
                    similarity=0.0,
                    removed_lines=len(lines),
                    removed_chars=removed_chars,
                )
            )
        else:
            # 匹配场景：场景内行 diff
            changes, stats = diff_lines(_scene_lines(scene_a), _scene_lines(scene_b))
            sim = _similarity(_scene_lines(scene_a), _scene_lines(scene_b))
            changed = stats.added_lines + stats.removed_lines + stats.modified_lines
            ctype: SceneChangeType = "modified" if changed > 0 else "unchanged"
            total_added += stats.added_lines
            total_removed += stats.removed_lines
            total_modified += stats.modified_lines
            total_added_chars += stats.added_chars
            total_removed_chars += stats.removed_chars
            if ctype == "modified":
                n_modified += 1
            else:
                n_unchanged += 1
            truncated_scene = changed > max_line_changes
            scene_changes.append(
                SceneChange(
                    change_type=ctype,
                    old_scene_number=scene_a.scene_number,
                    new_scene_number=scene_b.scene_number,
                    location=scene_b.location,
                    time_of_day=scene_b.time_of_day,
                    similarity=round(sim, 4),
                    added_lines=stats.added_lines,
                    removed_lines=stats.removed_lines,
                    modified_lines=stats.modified_lines,
                    added_chars=stats.added_chars,
                    removed_chars=stats.removed_chars,
                    line_changes=[] if truncated_scene else changes,
                    line_changes_truncated=truncated_scene,
                )
            )

    stats = DiffLineStats(
        added_lines=total_added,
        removed_lines=total_removed,
        modified_lines=total_modified,
        added_chars=total_added_chars,
        removed_chars=total_removed_chars,
        changed_chars=total_added_chars + total_removed_chars,
        from_chars=sum(len(line) for scene in from_draft.scenes for line in _scene_lines(scene)),
        to_chars=sum(len(line) for scene in to_draft.scenes for line in _scene_lines(scene)),
    )
    scene_summary = SceneDiffSummary(
        from_scene_count=len(from_draft.scenes),
        to_scene_count=len(to_draft.scenes),
        added=n_added,
        removed=n_removed,
        modified=n_modified,
        unchanged=n_unchanged,
    )

    total_changed_lines = total_added + total_removed + total_modified
    truncated = total_changed_lines > max_line_changes
    if truncated:
        # 整体截断：清空所有场景的行明细（字符统计已算完，不受影响）
        scene_changes = [
            sc.model_copy(update={"line_changes": [], "line_changes_truncated": True})
            for sc in scene_changes
        ]

    return ScriptDiff(
        mode="scene",
        change_ratio=compute_change_ratio(stats),
        scene_summary=scene_summary,
        stats=stats,
        scene_changes=scene_changes,
        truncated=truncated,
    )


def diff_texts(
    from_text: str,
    to_text: str,
    *,
    max_line_changes: int = MAX_DIFF_LINE_CHANGES,
) -> ScriptDiff:
    """无法解析 ScriptDraft 时的全文行 diff（mode="line"）。

    顶层 line_changes 填充行级明细；超限时 truncated=True 并清空明细。
    场景统计置零（无结构化场景信息）。
    """
    from_lines = from_text.splitlines()
    to_lines = to_text.splitlines()
    changes, stats = diff_lines(from_lines, to_lines)

    total_changed = stats.added_lines + stats.removed_lines + stats.modified_lines
    truncated = total_changed > max_line_changes
    if truncated:
        changes = []

    return ScriptDiff(
        mode="line",
        change_ratio=compute_change_ratio(stats),
        scene_summary=SceneDiffSummary(
            from_scene_count=0,
            to_scene_count=0,
            added=0,
            removed=0,
            modified=0,
            unchanged=0,
        ),
        stats=stats,
        line_changes=changes,
        truncated=truncated,
    )


# ---- Tool 包装（供注册表一致性；diff_service 直接调纯函数）----

class ScriptDiffTool(Tool):
    """确定性 Diff 工具。"""

    metadata = ToolMetadata(
        name="compute_script_diff",
        version="1.0",
        description="两段剧本文本的行级 diff 与 change_ratio——纯 Python 实现",
    )

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        """计算两段纯文本的 diff。

        Args:
            from_plain_text: str — from 侧全文
            to_plain_text: str — to 侧全文

        Returns:
            ScriptDiff.model_dump(mode="json")
        """
        result = diff_texts(
            kwargs.get("from_plain_text", ""),
            kwargs.get("to_plain_text", ""),
        )
        return result.model_dump(mode="json")
