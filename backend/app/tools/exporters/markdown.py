"""Markdown 序列化器 (G-05) — 移植前端导出序列化逻辑。

参考实现 frontend/src/lib/export.ts 的 markdownFromXxx / buildExportMarkdown：
- 稳定标题层级（H1 文档名 / H2 区块 / H3 子项），标题与字段名用中文；
- 不输出内部 UUID / schema_version / checksum / prompt / token 等内部字段；
- 多集内容按集号升序输出。

模块边界：纯函数序列化 + MarkdownExporter Tool，不触碰 API / 存储。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from app.core.security import escape_html, sanitize_filename_part
from app.tools.protocol import Tool, ToolMetadata

# 评估维度 → 中文标签（与前端 EVAL_DIMENSION_LABELS 一致）
EVAL_DIMENSION_LABELS: dict[str, str] = {
    "opening_hook": "开头钩子",
    "main_clarity": "主线清晰度",
    "character_appeal": "角色吸引力",
    "conflict_intensity": "冲突强度",
    "payoff_density": "爽点密度",
    "ending_hook": "结尾钩子",
    "pacing": "节奏控制",
    "visualizability": "可视化程度",
    "compliance_safety": "合规安全",
}
# 稳定遍历维度（保持展示顺序一致）
EVAL_DIM_ORDER: list[str] = list(EVAL_DIMENSION_LABELS.keys())

# 问题严重程度 → 中文标签（镜像前端 SEVERITY_LABELS）
SEVERITY_LABELS: dict[str, str] = {
    "low": "轻微",
    "medium": "中等",
    "high": "严重",
}


def _join(values: list[Any] | None) -> str:
    """列表 → "、" 连接；空列表返回空串。"""
    if not values:
        return ""
    return "、".join(str(v) for v in values)


# ========================================================================
# 逐内容类型的 Markdown 序列化（纯函数，输入为 Artifact content dict）
# ========================================================================


def markdown_from_story_bible(c: dict[str, Any]) -> str:
    """StoryBible → Markdown（与前端 markdownFromStoryBible 对齐）。"""
    lines: list[str] = ["# 世界观与人物设定（StoryBible）", ""]
    lines += ["## 基本信息", ""]
    lines += [f"- 剧名：{c.get('title', '')}"]
    lines += [f"- 一句话梗概（Logline）：{c.get('logline', '')}"]
    lines += [f"- 类型：{c.get('genre', '')}"]
    lines += [f"- 基调：{_join(c.get('tone', []))}"]
    lines += [f"- 世界观设定：{c.get('world_setting', '')}", ""]

    def character_section(label: str, p: dict[str, Any]) -> None:
        lines.extend([f"## {label}", ""])
        lines.extend([f"- 姓名：{p.get('name', '')}"])
        lines.extend([f"- 定位：{p.get('role', '')}"])
        if p.get("age_range"):
            lines.append(f"- 年龄：{p['age_range']}")
        lines.extend([f"- 外在目标：{p.get('visible_goal', '')}"])
        if p.get("hidden_need"):
            lines.append(f"- 内在需求：{p['hidden_need']}")
        lines.extend([f"- 性格特点：{_join(p.get('traits', []))}"])
        lines.extend([f"- 优点：{_join(p.get('strengths', []))}"])
        lines.extend([f"- 缺点：{_join(p.get('flaws', []))}"])
        if p.get("relationship_notes"):
            lines.append(f"- 关系备注：{_join(p['relationship_notes'])}")
        if p.get("forbidden_changes"):
            lines.append(f"- 不可修改事项：{_join(p['forbidden_changes'])}")
        lines.append("")

    character_section("主角", c.get("protagonist", {}))
    character_section("反派", c.get("antagonist", {}))

    supporting = c.get("supporting_characters", [])
    if supporting:
        lines += ["## 配角", ""]
        for sc in supporting:
            lines += [f"### {sc.get('name', '')}（{sc.get('role', '')}）", ""]
            lines += [f"- 外在目标：{sc.get('visible_goal', '')}"]
            if sc.get("hidden_need"):
                lines.append(f"- 内在需求：{sc['hidden_need']}")
            lines += [f"- 性格特点：{_join(sc.get('traits', []))}"]
            lines += [f"- 优点：{_join(sc.get('strengths', []))}"]
            lines += [f"- 缺点：{_join(sc.get('flaws', []))}"]
            if sc.get("relationship_notes"):
                lines.append(f"- 关系备注：{_join(sc['relationship_notes'])}")
            lines.append("")

    lines += ["## 核心冲突", ""]
    lines += [f"- 主要冲突：{c.get('main_conflict', '')}"]
    lines += [f"- 风险赌注：{c.get('stakes', '')}", ""]

    lines += ["## 故事规则", ""]
    lines += [f"- {r}" for r in c.get("story_rules", [])]
    lines.append("")

    lines += ["## 长期伏笔", ""]
    lines += [f"- {p}" for p in c.get("long_term_payoffs", [])]
    lines.append("")

    lines += ["## 悬念（开放回路）", ""]
    lines += [f"- {o}" for o in c.get("open_loops", [])]
    lines.append("")

    lines += ["## 锁定事实", ""]
    lines += [f"- {f}" for f in c.get("locked_facts", [])]
    lines.append("")

    lines += ["## 合规备注", ""]
    compliance = c.get("compliance_notes", [])
    if compliance:
        lines += [f"- {n}" for n in compliance]
    else:
        lines.append("- 无")
    lines.append("")

    return "\n".join(lines)


def markdown_from_outline(c: dict[str, Any]) -> str:
    """分集大纲 → Markdown（与前端 markdownFromOutline 对齐）。"""
    lines: list[str] = ["# 十集大纲", ""]
    lines.append(str(c.get("arc_summary", "")))
    lines.append("")

    for ep in c.get("episodes", []):
        lines += [f"## 第 {ep.get('episode_number', '')} 集：{ep.get('title', '')}", ""]
        lines += [f"- 开头钩子：{ep.get('opening_hook', '')}"]
        lines += [f"- 本集目标：{ep.get('objective', '')}"]
        lines += [f"- 核心冲突：{ep.get('core_conflict', '')}"]
        key_events = ep.get("key_events", [])
        if key_events:
            lines.append("- 关键事件：")
            lines += [f"  - {e}" for e in key_events]
        lines += [f"- 本集回报：{ep.get('payoff', '')}"]
        lines += [f"- 结尾钩子：{ep.get('ending_hook', '')}"]
        lines += [f"- 下一集衔接：{ep.get('next_bridge', '')}"]
        if ep.get("introduced_loops"):
            lines.append(f"- 引入伏笔：{_join(ep['introduced_loops'])}")
        if ep.get("resolved_loops"):
            lines.append(f"- 回收伏笔：{_join(ep['resolved_loops'])}")
        if ep.get("required_characters"):
            lines.append(f"- 出场角色：{_join(ep['required_characters'])}")
        lines.append("")

    return "\n".join(lines)


def markdown_from_script(c: dict[str, Any]) -> str:
    """单集剧本 → Markdown（与前端 markdownFromScript 对齐）。"""
    lines: list[str] = [f"# 第 {c.get('episode_number', '')} 集剧本：{c.get('title', '')}", ""]

    for scene in c.get("scenes", []):
        lines += [
            f"## 第 {scene.get('scene_number', '')} 场：{scene.get('location', '')}"
            f"（{scene.get('time_of_day', '')}）",
            "",
        ]
        if scene.get("action"):
            lines.append(str(scene["action"]))
            lines.append("")
        dialogue = scene.get("dialogue", [])
        if dialogue:
            for d in dialogue:
                speaker = d.get("speaker", "")
                text = d.get("text", "")
                parenthetical = d.get("parenthetical")
                if parenthetical:
                    lines.append(f"- {speaker}（{parenthetical}）：{text}")
                else:
                    lines.append(f"- {speaker}：{text}")
            lines.append("")

    return "\n".join(lines)


def markdown_from_evaluation(c: dict[str, Any]) -> str:
    """单集评估报告 → Markdown（与前端 markdownFromEvaluation 对齐）。"""
    lines: list[str] = [f"# 第 {c.get('episode_number', '')} 集评估报告", ""]
    lines += [f"- 综合评分：{c.get('overall_score', '')} 分"]
    need_revision = "是" if c.get("need_revision") else "否"
    lines += [f"- 是否需修订：{need_revision}", ""]

    lines += ["## 维度得分", ""]
    dims = c.get("dimension_scores", {})
    for dim in EVAL_DIM_ORDER:
        score = dims.get(dim)
        label = EVAL_DIMENSION_LABELS.get(dim, dim)
        lines.append(f"- {label}：{score} 分")
    lines.append("")

    lines += ["## 优点", ""]
    strengths = c.get("strengths", [])
    if strengths:
        lines += [f"- {s}" for s in strengths]
    else:
        lines.append("- 无")
    lines.append("")

    lines += ["## 问题", ""]
    issues = c.get("issues", [])
    if issues:
        for issue in issues:
            scene = (
                f"第 {issue['scene_number']} 场"
                if issue.get("scene_number") is not None
                else "全剧"
            )
            severity = SEVERITY_LABELS.get(issue.get("severity", ""), issue.get("severity", ""))
            dim_label = EVAL_DIMENSION_LABELS.get(issue.get("dimension", ""), issue.get("dimension", ""))
            lines.append(f"- [{severity}] {scene} {dim_label}：{issue.get('diagnosis', '')}")
            lines.append(f"  - 证据：{issue.get('evidence', '')}")
            lines.append(f"  - 建议：{issue.get('suggestion', '')}")
    else:
        lines.append("- 无")
    lines.append("")

    lines += ["## 修订建议", ""]
    suggestions = c.get("revision_suggestions", [])
    if suggestions:
        lines += [f"- {s}" for s in suggestions]
    else:
        lines.append("- 无")
    lines.append("")

    lines += ["## 风险提示", ""]
    risks = c.get("risk_flags", [])
    if risks:
        lines += [f"- {r}" for r in risks]
    else:
        lines.append("- 无")
    lines.append("")

    return "\n".join(lines)


def markdown_from_revision(plan: dict[str, Any]) -> str:
    """修订计划 → Markdown（对应前端 markdownFromRevision，不含 Diff 概览）。"""
    lines: list[str] = [f"# 第 {plan.get('episode_number', '')} 集修订说明", ""]
    ratio = round(float(plan.get("max_change_ratio", 0) or 0) * 100)
    lines.append(f"- 最大变更比例：{ratio}%")
    if plan.get("user_instruction"):
        lines.append(f"- 用户补充要求：{plan['user_instruction']}")
    lines.append("")

    lines += ["## 锁定事实（修订不得违反）", ""]
    locked = plan.get("locked_facts", [])
    if locked:
        lines += [f"- {f}" for f in locked]
    else:
        lines.append("- 无")
    lines.append("")

    lines += ["## 修订操作", ""]
    operations = plan.get("operations", [])
    if operations:
        for index, op in enumerate(operations, start=1):
            target = (
                f"第 {op['target_scene_number']} 场"
                if op.get("target_scene_number") is not None
                else "跨场景"
            )
            lines.append(f"- 操作 {index}：{target}")
            lines.append(f"  - 指令：{op.get('instruction', '')}")
            if op.get("preserve"):
                lines.append(f"  - 必须保留：{'；'.join(str(p) for p in op['preserve'])}")
            if op.get("expected_effect"):
                lines.append(f"  - 预期效果：{op['expected_effect']}")
    else:
        lines.append("- 无具体修订操作")
    lines.append("")

    return "\n".join(lines)


# ========================================================================
# 组装
# ========================================================================


def _escape_deep(value: Any) -> Any:
    """递归 HTML 转义 dict/list 中的全部字符串叶节点（I-03）。

    仅转义字符串值；数字 / 布尔 / None 保持原样，
    避免把 `episode_number` 等数值型字段意外变成字符串。
    """
    if isinstance(value, dict):
        return {k: _escape_deep(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_escape_deep(v) for v in value]
    if isinstance(value, str):
        return escape_html(value)
    return value


def build_export_markdown(
    *,
    project_title: str,
    exported_at: str,
    data: dict[str, Any],
    kinds: Sequence[str],
) -> str:
    """按选中的内容类型拼成完整导出 Markdown（与前端 buildExportMarkdown 对齐）。

    data 结构（由 ExportService 组装）：
    {
        "story_bible": dict | None,
        "outline": dict | None,
        "scripts": [dict, ...],      # 按集号升序
        "evaluations": [dict, ...],  # 按集号升序
        "revisions": [dict, ...],    # 按集号升序
    }
    """
    # I-03：内容转义——data 全部字符串叶节点过 escape_html，
    # 使剧本/设定中的 <script> 等以纯文本展示（防 Markdown→HTML 注入）。
    # 序列化器的结构性 Markdown 语法是在转义之后才拼接的，不受影响。
    data = _escape_deep(data)
    sections: list[str] = [
        f"# {escape_html(project_title)} — 内容导出",
        "",
        f"> 导出时间：{exported_at}",
        "",
    ]

    if "story_bible" in kinds:
        sb = data.get("story_bible")
        sections += [markdown_from_story_bible(sb) if sb else "## StoryBible\n\n（无可用内容）", ""]
    if "outline" in kinds:
        outline = data.get("outline")
        sections += [markdown_from_outline(outline) if outline else "## 大纲\n\n（无可用内容）", ""]
    if "script" in kinds:
        scripts = data.get("scripts", [])
        if not scripts:
            sections += ["## 剧本\n\n（无可用内容）", ""]
        else:
            for script in sorted(scripts, key=lambda s: s.get("episode_number", 0)):
                sections += [markdown_from_script(script), ""]
    if "evaluation" in kinds:
        evaluations = data.get("evaluations", [])
        if not evaluations:
            sections += ["## 评估\n\n（无可用内容）", ""]
        else:
            for report in sorted(evaluations, key=lambda r: r.get("episode_number", 0)):
                sections += [markdown_from_evaluation(report), ""]
    if "revision" in kinds:
        revisions = data.get("revisions", [])
        if not revisions:
            sections += ["## 修订说明\n\n（无可用内容）", ""]
        else:
            for plan in sorted(revisions, key=lambda p: p.get("episode_number", 0)):
                sections += [markdown_from_revision(plan), ""]

    return "\n".join(sections)


# ========================================================================
# 文件名与时间戳（镜像前端 buildExportFilename / formatTimestamp）
# ========================================================================
# sanitize_filename_part 由 app.core.security 提供（I-03 集中）

def build_export_filename(
    project_title: str,
    kinds: Sequence[str],
    fmt: str,
    timestamp: str,
) -> str:
    """生成导出文件名：{项目名}-{内容标签}-{yyyyMMdd-HHmmss}.{md|docx}。"""
    from app.domain.export import EXPORT_KIND_LABELS

    labels = [EXPORT_KIND_LABELS.get(k, k) for k in kinds]
    kind_label = "导出" if not labels else "-".join(labels)
    ext = "md" if fmt == "markdown" else "docx"
    return f"{sanitize_filename_part(project_title)}-{kind_label}-{timestamp}.{ext}"


def format_timestamp(dt: datetime) -> str:
    """紧凑时间戳（文件名用）：yyyyMMdd-HHmmss。"""
    return f"{dt.year}{dt.month:02d}{dt.day:02d}-{dt.hour:02d}{dt.minute:02d}{dt.second:02d}"


# ========================================================================
# MarkdownExporter Tool（确定性工具，不可隐式调用 LLM）
# ========================================================================


class MarkdownExporter(Tool):
    """Markdown 序列化工具（G-05）。"""

    metadata = ToolMetadata(
        name="export_markdown",
        version="1.0",
        description="把导出的各 kind Artifact content 组装成稳定 Markdown",
    )

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        """组装导出 Markdown。

        Args:
            project_title: 项目名（写入文档标题）
            exported_at: 导出时间（ISO 字符串）
            data: 组装后的各 kind content dict
            kinds: 选中的内容类型列表

        Returns:
            {"markdown": str}
        """
        return {
            "markdown": build_export_markdown(
                project_title=kwargs.get("project_title", ""),
                exported_at=kwargs.get("exported_at", ""),
                data=kwargs["data"],
                kinds=kwargs["kinds"],
            )
        }
