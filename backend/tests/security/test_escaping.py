"""HTML 转义与导出转义回归测试（I-03）。

覆盖：
- escape_html 五个特殊字符 & 优先转义、None/空串/非字符串；
- 导出 Markdown 中用户/LLM 提供的文本（剧本对白、设定字段）被转义为纯文本，
  `<script>` 不以可执行标签形式出现在输出里；
- 序列化器结构性 Markdown 语法不受转义影响（标题、列表行仍稳定）。

不依赖 DB / Redis / LLM。
"""

from __future__ import annotations

from app.core.security import escape_html
from app.tools.exporters.markdown import build_export_markdown


class TestEscapeHtml:
    def test_script_tag_escaped(self) -> None:
        assert escape_html("<script>alert(1)</script>") == (
            "&lt;script&gt;alert(1)&lt;/script&gt;"
        )

    def test_amp_escaped_first(self) -> None:
        assert escape_html("a & b < c > d") == "a &amp; b &lt; c &gt; d"

    def test_quotes_escaped(self) -> None:
        assert escape_html('"') == "&quot;"
        assert escape_html("'") == "&#39;"
        assert escape_html('他说"你好"\'啊\'') == "他说&quot;你好&quot;&#39;啊&#39;"

    def test_no_double_escape(self) -> None:
        """已转义的实体不再二次转义。"""
        assert escape_html("&lt;") == "&amp;lt;"

    def test_non_string_inputs(self) -> None:
        assert escape_html(None) == ""
        assert escape_html("") == ""
        assert escape_html(123) == "123"


class TestExportMarkdownEscaping:
    def _script_data(self) -> dict[str, object]:
        return {
            "scripts": [
                {
                    "episode_number": 1,
                    "title": "第1集",
                    "scenes": [
                        {
                            "scene_number": 1,
                            "location": "天台",
                            "time_of_day": "夜",
                            "action": "风吹过",
                            "dialogue": [
                                {
                                    "speaker": "主角",
                                    "text": "<script>alert('xss')</script>",
                                    "parenthetical": None,
                                },
                                {
                                    "speaker": "反派",
                                    "text": "引号 \" 与 & 与 ' 单引号",
                                    "parenthetical": None,
                                },
                            ],
                        }
                    ],
                }
            ]
        }

    def _render(self, data: dict[str, object], kinds: list[str]) -> str:
        return build_export_markdown(
            project_title="测试项目",
            exported_at="2026-08-16T00:00:00",
            data=data,
            kinds=kinds,
        )

    def test_script_tag_in_dialogue_escaped(self) -> None:
        md = self._render(self._script_data(), ["script"])
        assert "&lt;script&gt;alert(&#39;xss&#39;)&lt;/script&gt;" in md
        # 不允许出现可执行的原始标签 / 原始脚本文本
        assert "<script>" not in md
        assert "alert('xss')" not in md

    def test_structural_markdown_preserved(self) -> None:
        """转义只作用于内容叶节点，序列化器结构语法不受影响。"""
        md = self._render(self._script_data(), ["script"])
        assert md.startswith("# 测试项目 — 内容导出")
        assert "# 第 1 集剧本：第1集" in md
        assert "## 第 1 场：天台（夜）" in md
        assert "- 主角：" in md
        assert "- 反派：引号 &quot; 与 &amp; 与 &#39; 单引号" in md
        # 集号仍为数值（未因转义变成字符串）
        assert "第 1 集剧本" in md

    def test_story_bible_fields_escaped(self) -> None:
        data: dict[str, object] = {
            "story_bible": {
                "title": "项目<title>注入",
                "logline": "<img src=x onerror=alert(1)>",
            }
        }
        md = self._render(data, ["story_bible"])
        assert "&lt;img src=x onerror=alert(1)&gt;" in md
        assert "<img src=x" not in md
        assert "项目&lt;title&gt;注入" in md

    def test_project_title_escaped(self) -> None:
        md = build_export_markdown(
            project_title="<b>剧名</b>",
            exported_at="2026-08-16T00:00:00",
            data={"scripts": []},
            kinds=["script"],
        )
        assert "# &lt;b&gt;剧名&lt;/b&gt; — 内容导出" in md
        assert "<b>剧名</b>" not in md
