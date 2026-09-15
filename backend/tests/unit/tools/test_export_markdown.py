"""导出 Markdown 转义回归（I-03；W1-05 前端序列化器移除后转此后端）。

剧本/设定字段含 `<script>` 等注入载体时必须以纯文本实体输出——
导出的 .md 在阅读器/HTML 预览中不得出现可执行标签。
"""

from __future__ import annotations

from typing import Any

from app.tools.exporters.markdown import build_export_markdown


def _script_content() -> dict[str, Any]:
    return {
        "episode_number": 1,
        "title": "<script>alert('title')</script>",
        "scenes": [
            {
                "scene_number": 1,
                "location": "天台",
                "time": "夜",
                "action_lines": ['△ 少年<img src=x onerror="alert(1)">'],
                "dialogues": [
                    {"character": "林峰", "line_type": "对白", "text": "<script>alert(1)</script>"},
                ],
            }
        ],
    }


def test_markdown_escapes_script_injection_in_script_content() -> None:
    md = build_export_markdown(
        project_title="项目 <b>加粗</b>",
        exported_at="2026-09-13T00:00:00+00:00",
        data={"scripts": [_script_content()]},
        kinds=["script"],
    )
    assert "<script>" not in md
    assert "&lt;script&gt;" in md
    assert "<img" not in md
    # 结构性 Markdown 语法不受转义影响
    assert md.startswith("# ")


def test_markdown_escapes_story_bible_fields() -> None:
    md = build_export_markdown(
        project_title="正常项目",
        exported_at="2026-09-13T00:00:00+00:00",
        data={"story_bible": {"title": "设定", "logline": "<iframe src=evil></iframe>"}},
        kinds=["story_bible"],
    )
    assert "<iframe" not in md
    assert "&lt;iframe" in md
