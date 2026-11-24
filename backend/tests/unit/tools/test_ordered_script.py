"""有序剧本格式的端到端序列化契约。"""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from docx import Document
from pydantic import ValidationError

from app.domain.script import OrderedScriptDraft, Scene, ScriptDraft
from app.tools.diff import diff_script_drafts
from app.tools.exporters.docx import build_docx_bytes
from app.tools.exporters.markdown import markdown_from_script
from app.tools.script_render import materialize_script_draft
from app.tools.script_text import full_script_to_script_draft
from app.tools.text_evidence import scene_texts_from_content

_GOLDEN = Path(__file__).resolve().parents[2] / "golden"


def load_fixture(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((_GOLDEN / name).read_text(encoding="utf-8")))


def test_order_survives_serialization_export_and_evidence() -> None:
    ordered = OrderedScriptDraft.model_validate(load_fixture("script_draft_ordered.json"))
    script = materialize_script_draft(ordered, uuid4())
    restored = ScriptDraft.model_validate_json(script.model_dump_json())
    scene = restored.scenes[0]
    assert scene.blocks is not None
    assert [block.kind for block in scene.blocks[:4]] == ["action", "dialogue", "action", "dialogue"]
    expected = [block.text for block in scene.blocks]
    content = restored.model_dump(mode="json")
    markdown = markdown_from_script(content)
    word = Document(BytesIO(build_docx_bytes(markdown)))
    actual_texts = [
        restored.plain_text,
        markdown,
        "\n".join(p.text for p in word.paragraphs),
        scene_texts_from_content(content)[1],
    ]
    for actual in actual_texts:
        positions = [actual.index(fragment) for fragment in expected]
        assert positions == sorted(positions)
    assert restored.content_schema_version == "2.0"
    assert restored.scenes[0].dialogue[0].text == expected[1]

    changed = restored.model_dump()
    blocks = changed["scenes"][0]["blocks"]
    blocks[1], blocks[2] = blocks[2], blocks[1]
    comparison = diff_script_drafts(restored, ScriptDraft.model_validate(changed))
    assert comparison.scene_summary is not None
    assert comparison.scene_summary.modified == 1


def test_legacy_artifact_keeps_its_shape() -> None:
    script = ScriptDraft.model_validate(load_fixture("script_draft_valid.json"))
    assert script.content_schema_version == "1.0"
    assert all("blocks" not in scene for scene in script.model_dump()["scenes"])
    assert script.plain_text == load_fixture("script_draft_valid.json")["plain_text"]


@pytest.mark.parametrize(
    "blocks", [[], [{"kind": "action", "text": "  "}], [{"kind": "unknown", "text": "跑。"}]]
)
def test_invalid_blocks_do_not_pass_writer_schema(blocks: list[dict[str, str]]) -> None:
    data = load_fixture("script_draft_ordered.json")
    data["scenes"][0]["blocks"] = blocks
    with pytest.raises(ValidationError, match="blocks"):
        OrderedScriptDraft.model_validate(data)


def test_missing_blocks_are_rejected_for_generation() -> None:
    data = load_fixture("script_draft_ordered.json")
    del data["scenes"][0]["blocks"]
    with pytest.raises(ValidationError, match="blocks"):
        OrderedScriptDraft.model_validate(data)


def test_import_preserves_order_and_allows_dialogue_only() -> None:
    content = full_script_to_script_draft(
        "1-1 日 外 球场\n△带球。\n林曜：给我！\n△传球。\n陈屿OS：稳住。\n"
        "1-2 日 内 更衣室\n教练VO：比赛结束。\n",
        title="比赛",
    )
    assert content is not None
    restored = ScriptDraft.model_validate(content)
    first, second = restored.scenes
    assert first.blocks is not None
    assert [block.text for block in first.blocks] == ["带球。", "给我！", "传球。", "稳住。"]
    assert first.dialogue[-1].line_type == "os"
    assert second.action == ""
    assert second.dialogue[0].line_type == "vo"
    assert Scene.model_validate(second.model_dump()).blocks == second.blocks
