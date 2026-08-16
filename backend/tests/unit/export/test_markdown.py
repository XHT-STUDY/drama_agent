"""Markdown 导出序列化单元测试 (G-05)。

验证移植自前端 export.ts 的序列化逻辑：
- 不输出内部 ID / Prompt / Token 等内部字段（验收项）;
- 3 集按集号升序排序（验收项）;
- 标题层级稳定（H1 文档名 / H2 区块 / H3 子项）;
- 缺数据时输出「（无可用内容）」占位;
- 文件名清洗与时间戳。

全部为纯函数测试，不触碰 DB / 网络。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.tools.exporters.markdown import (
    EVAL_DIM_ORDER,
    build_export_filename,
    build_export_markdown,
    format_timestamp,
    markdown_from_evaluation,
    markdown_from_revision,
    markdown_from_script,
    markdown_from_story_bible,
    sanitize_filename_part,
)

# 文档抬头固定值
_DOC_TITLE = "足球少年逆袭记"


def _story_bible() -> dict[str, Any]:
    return {
        "title": "足球少年逆袭记",
        "logline": "被青训队抛弃的少年重返绿茵场",
        "genre": "热血运动",
        "tone": ["热血", "励志"],
        "world_setting": "现代都市足球青训体系",
        "protagonist": {
            "character_id": "lin_feng",
            "name": "林峰",
            "role": "主角",
            "age_range": "18",
            "visible_goal": "重返职业青训队",
            "hidden_need": "被认可的渴望",
            "traits": ["坚韧", "倔强"],
            "strengths": ["球感好"],
            "flaws": ["冲动"],
            "relationship_notes": ["与教练亦师亦友"],
            "forbidden_changes": ["出身设定"],
        },
        "antagonist": {
            "character_id": "chen_ye",
            "name": "陈野",
            "role": "反派",
            "visible_goal": "守住主力位置",
            "traits": ["算计"],
        },
        "supporting_characters": [
            {
                "character_id": "wang_le",
                "name": "王乐",
                "role": "挚友",
                "visible_goal": "帮助林峰",
                "traits": ["仗义"],
            }
        ],
        "main_conflict": "天赋与出身之辩",
        "stakes": "失去足球生涯",
        "story_rules": ["不得出现超现实元素"],
        "long_term_payoffs": ["决赛伏笔"],
        "open_loops": ["陈野的秘密"],
        "locked_facts": ["林峰出身小镇"],
        "compliance_notes": ["避免血腥镜头"],
    }


def _outline() -> dict[str, Any]:
    return {
        "arc_summary": "从低谷到逆袭的三幕结构",
        "episodes": [
            {
                "episode_number": 1,
                "title": "被抛弃",
                "opening_hook": "训练场公布名单",
                "objective": "离开球队",
                "core_conflict": "去留之争",
                "key_events": ["被开除", "偶遇伯乐"],
                "payoff": "找到方向",
                "ending_hook": "深夜加练",
                "next_bridge": "进入二队",
                "introduced_loops": ["伯乐身份"],
                "required_characters": ["lin_feng"],
            }
        ],
    }


def _script(episode_number: int, title: str) -> dict[str, Any]:
    return {
        "episode_number": episode_number,
        "title": title,
        "opening_hook": "钩子",
        "ending_hook": "悬念",
        "scenes": [
            {
                "scene_number": 1,
                "location": "训练场",
                "time_of_day": "日",
                "characters": ["林峰", "教练"],
                "action": "林峰独自加练射门",
                "dialogue": [
                    {"speaker": "教练", "text": "你被开除了"},
                    {"speaker": "林峰", "text": "我不会放弃", "parenthetical": "低声"},
                ],
            }
        ],
    }


def _evaluation() -> dict[str, Any]:
    return {
        "episode_number": 1,
        "overall_score": 82.5,
        "need_revision": False,
        "dimension_scores": {
            "opening_hook": 90,
            "main_clarity": 85,
            "character_appeal": 80,
            "conflict_intensity": 85,
            "payoff_density": 80,
            "ending_hook": 85,
            "pacing": 78,
            "visualizability": 82,
            "compliance_safety": 90,
        },
        "strengths": ["开头钩子抓人"],
        "issues": [
            {
                "issue_id": "iss_1",
                "dimension": "pacing",
                "severity": "medium",
                "scene_number": 1,
                "evidence": "对白节奏偏慢",
                "diagnosis": "铺垫过长",
                "suggestion": "压缩第一场对白",
            }
        ],
        "revision_suggestions": ["加快节奏"],
        "risk_flags": [],
    }


def _revision() -> dict[str, Any]:
    return {
        "episode_number": 1,
        "operations": [
            {
                "operation_id": "op_001",
                "target_scene_number": 1,
                "instruction": "压缩第一场对白",
                "preserve": ["林峰出身小镇"],
                "expected_effect": "提升节奏控制维度评分",
            }
        ],
        "locked_facts": ["林峰出身小镇"],
        "max_change_ratio": 0.35,
        "user_instruction": "保持热血基调",
    }


def _data(**overrides: Any) -> dict[str, Any]:
    data = {
        "story_bible": _story_bible(),
        "outline": _outline(),
        "scripts": [_script(1, "被抛弃")],
        "evaluations": [_evaluation()],
        "revisions": [_revision()],
    }
    data.update(overrides)
    return data


def _export(kinds: list[str], data: dict[str, Any] | None = None) -> str:
    return build_export_markdown(
        project_title=_DOC_TITLE,
        exported_at="2026-08-16T10:00:00+00:00",
        data=data if data is not None else _data(),
        kinds=kinds,
    )


class TestNoInternalFields:
    """验收项：Markdown 不包含内部 ID / Prompt / Token。"""

    def test_story_bible_no_internal_fields(self) -> None:
        """StoryBible 序列化不输出内部字段。"""
        md = markdown_from_story_bible(_story_bible())
        for forbidden in ("uuid", "schema_version", "checksum", "input_hash"):
            assert forbidden not in md.lower()
        assert "prompt" not in md.lower()
        assert "token" not in md.lower()
        assert "lin_feng" not in md, "character_id 属于内部标识，不应输出"
        assert "character_id" not in md

    def test_full_export_no_internal_ids(self) -> None:
        """完整导出不含内部 Artifact ID 形态（UUID 字符串）。"""
        md = _export(["story_bible", "outline", "script", "evaluation", "revision"])
        uuid_pattern = re.compile(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
        )
        assert uuid_pattern.search(md) is None
        for forbidden in ("issue_id", "operation_id", "schema_version", "prompt_version"):
            assert forbidden not in md


class TestScriptOrdering:
    """验收项：3 集按集号升序排序。"""

    def test_scripts_sorted_by_episode(self) -> None:
        """乱序输入 → 输出按集号升序。"""
        scripts = [
            _script(3, "逆袭"),
            _script(1, "被抛弃"),
            _script(2, "二队"),
        ]
        md = _export(["script"], _data(scripts=scripts))
        ep1 = md.index("# 第 1 集剧本")
        ep2 = md.index("# 第 2 集剧本")
        ep3 = md.index("# 第 3 集剧本")
        assert ep1 < ep2 < ep3


class TestHeadingLevels:
    """标题层级稳定（H1 文档名 / H2 区块 / H3 子项）。"""

    def test_document_title_is_h1(self) -> None:
        """文档抬头为 H1：`# {项目名} — 内容导出`。"""
        md = _export(["story_bible"])
        assert md.startswith(f"# {_DOC_TITLE} — 内容导出\n")

    def test_story_bible_uses_h1_h2_h3_only(self) -> None:
        """StoryBible 标题层级不超过 ###。"""
        md = markdown_from_story_bible(_story_bible())
        for line in md.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                assert not stripped.startswith("####"), f"出现超深标题: {line}"

    def test_evaluation_uses_h1_h2_only(self) -> None:
        """评估报告标题层级不超过 ##。"""
        md = markdown_from_evaluation(_evaluation())
        for line in md.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                assert stripped.startswith(("# ", "## ")), f"出现异常标题: {line}"


class TestContent:
    """内容细节与占位。"""

    def test_missing_kind_placeholder(self) -> None:
        """选中 kind 但无数据 → 输出「（无可用内容）」。"""
        md = _export(["story_bible", "script"], _data(story_bible=None, scripts=[]))
        assert "（无可用内容）" in md
        assert "世界观与人物设定" not in md

    def test_evaluation_dimension_labels_chinese(self) -> None:
        """维度得分用中文标签且顺序固定。"""
        md = markdown_from_evaluation(_evaluation())
        first = md.index("## 维度得分")
        for dim in EVAL_DIM_ORDER:
            label = {
                "opening_hook": "开头钩子",
                "main_clarity": "主线清晰度",
                "character_appeal": "角色吸引力",
                "conflict_intensity": "冲突强度",
                "payoff_density": "爽点密度",
                "ending_hook": "结尾钩子",
                "pacing": "节奏控制",
                "visualizability": "可视化程度",
                "compliance_safety": "合规安全",
            }[dim]
            assert f"- {label}：" in md[first:]

    def test_script_parenthetical_dialogue(self) -> None:
        """带括注对白格式：`- 说话人（提示）：台词`。"""
        md = markdown_from_script(_script(1, "被抛弃"))
        assert "- 林峰（低声）：我不会放弃" in md
        assert "- 教练：你被开除了" in md

    def test_revision_operations(self) -> None:
        """修订操作渲染含指令 / 保留 / 预期效果。"""
        md = markdown_from_revision(_revision())
        assert "- 操作 1：第 1 场" in md
        assert "指令：压缩第一场对白" in md
        assert "必须保留：林峰出身小镇" in md
        assert "预期效果：提升节奏控制维度评分" in md


class TestFilename:
    """文件名清洗与时间戳。"""

    def test_sanitize_filename_part(self) -> None:
        """危险字符替换为 _，且被截断到 40 字符。"""
        assert sanitize_filename_part("a/b\\c:d*e") == "a_b_c_d_e"
        assert len(sanitize_filename_part("x" * 100)) == 40

    def test_build_export_filename(self) -> None:
        """文件名格式：{项目名}-{内容标签}-{时间戳}.{ext}。"""
        name = build_export_filename(
            "足球少年逆袭记", ["script", "evaluation"], "markdown", "20260816-100000"
        )
        assert name.endswith(".md")
        assert name.startswith("足球少年逆袭记-")
        assert "剧本-评估" in name
        assert "20260816-100000" in name

    def test_docx_extension(self) -> None:
        """docx 格式使用 .docx 扩展名。"""
        assert build_export_filename("项目", ["outline"], "docx", "20260816-100000").endswith(".docx")

    def test_format_timestamp(self) -> None:
        """时间戳格式：yyyyMMdd-HHmmss。"""
        dt = datetime(2026, 8, 16, 9, 5, 3)
        assert format_timestamp(dt) == "20260816-090503"
