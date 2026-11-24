"""剧本模型 — EpisodeWriterInput、DialogueLine、Scene 与 ScriptDraft (§5.7, C-05).

word_count 和 dialogue_ratio 由确定性 Tool 计算，
不可信任 LLM 自报数值。
"""

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)


class EpisodeWriterInput(BaseModel):
    """Episode Writer Skill 的输入模型 (C-05).

    封装单集大纲、StoryBible、前集摘要和连续性状态。
    """

    model_config = {"extra": "forbid"}

    episode_number: int = Field(..., description="目标集号", ge=1)
    episode_outline: dict[str, Any] = Field(
        ..., description="本集大纲 (EpisodeOutline 的 dict 表示)"
    )
    story_bible: dict[str, Any] = Field(
        ..., description="完整 StoryBible (dict 表示)"
    )
    previous_summary: str = Field(
        default="", description="前集摘要文本 (第1集为空)"
    )
    continuity_state: str = Field(
        default="", description="当前连续性状态 (人物状态/伏笔/时间线)"
    )
    rag_context: str = Field(
        default="", description="知识库检索片段 (MVP 可为空)"
    )
    assembled_context: str = Field(
        default="",
        description="G-02：由 ContextBuilder 按预算组装的完整创作上下文（write_episode "
        "节点注入；为空时 Skill 回退旧版分段渲染）",
    )


class DialogueLine(BaseModel):
    """单句台词。"""

    model_config = {"extra": "forbid"}

    speaker: str = Field(..., description="说话角色名", min_length=1)
    text: str = Field(..., description="台词文本", min_length=1)
    parenthetical: str | None = Field(
        default=None, description="括号内情绪/动作提示，如“冷静”"
    )
    line_type: Literal["dialogue", "vo", "os"] = Field(
        default="dialogue",
        description="台词类型：dialogue=对白，vo=画外音旁白，os=内心独白",
    )


class ActionBlock(BaseModel):
    """场景中的一段可拍摄动作。"""

    model_config = {"extra": "forbid"}

    kind: Literal["action"]
    text: str = Field(min_length=1, pattern=r"\S")


class DialogueBlock(DialogueLine):
    """场景中的一段对白、旁白或独白。"""

    kind: Literal["dialogue"]
    speaker: str = Field(..., description="说话角色名", min_length=1, pattern=r"\S")
    text: str = Field(..., description="台词文本", min_length=1, pattern=r"\S")


SceneBlock = Annotated[ActionBlock | DialogueBlock, Field(discriminator="kind")]
ORDERED_SCRIPT_SCHEMA_VERSION = "2.0"


class SceneHeading(BaseModel):
    """新旧场景共用的场次和出场信息。"""

    model_config = {"extra": "forbid"}

    scene_number: int = Field(..., description="场次编号", ge=1)
    location: str = Field(..., description="场景地点", min_length=1)
    time_of_day: str = Field(..., description="时间，如“日/夜/傍晚”", min_length=1)
    int_ext: str | None = Field(
        default=None,
        description="内/外景：内、外、内/外；缺省按 外 处理",
    )
    characters: list[str] = Field(default_factory=list, description="出场角色列表")


class OrderedScene(SceneHeading):
    """模型必须为每场提交有序正文。"""

    blocks: list[SceneBlock] = Field(
        min_length=1, description="按剧情发生顺序排列的动作和台词"
    )


class Scene(SceneHeading):
    """含历史字段及可选有序正文的场景。"""

    action: str = Field(default="", description="动作描写，按行分隔")
    dialogue: list[DialogueLine] = Field(default_factory=list, description="台词列表")
    blocks: list[SceneBlock] | None = Field(
        default=None, min_length=1, description="按观众所见所闻顺序排列的动作和台词"
    )

    @model_validator(mode="after")
    def _derive_legacy_fields(self) -> "Scene":
        if self.blocks is None:
            if not self.action:
                raise ValueError("旧格式场景需要动作描写")
            return self

        self.action = "\n".join(block.text for block in self.blocks if block.kind == "action")
        self.dialogue = [
            DialogueLine(
                speaker=block.speaker, text=block.text,
                parenthetical=block.parenthetical, line_type=block.line_type,
            )
            for block in self.blocks if block.kind == "dialogue"
        ]
        return self

    @model_serializer(mode="wrap")
    def _serialize(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if self.blocks is None:
            data.pop("blocks", None)
        return data


class ScriptDraftHeading(BaseModel):
    """生成输出和持久化稿件共用的剧集信息。"""

    model_config = {"extra": "forbid"}

    episode_number: int = Field(..., description="集号", ge=1)
    title: str = Field(..., description="本集标题", min_length=1)
    opening_hook: str = Field(..., description="开头钩子文本", min_length=1)
    ending_hook: str = Field(..., description="结尾钩子文本", min_length=1)

    @field_validator("scenes", check_fields=False)
    @classmethod
    def _check_scene_numbers_consecutive(
        cls, scenes: list[SceneHeading]
    ) -> list[SceneHeading]:
        """场次编号应连续且不重复，从 1 开始。"""
        numbers = [scene.scene_number for scene in scenes]
        expected = list(range(1, len(numbers) + 1))
        if sorted(numbers) != expected:
            raise ValueError(f"场次编号应连续且不重复，期望 {expected}，实际 {numbers}")
        return scenes


class OrderedScriptDraft(ScriptDraftHeading):
    """Writer 和 Reviser 的有序剧本输出。"""

    scenes: list[OrderedScene] = Field(min_length=2, description="场次列表")


class ScriptDraft(ScriptDraftHeading):
    """持久化的剧本草稿。正文和统计由服务端生成。"""

    scenes: list[Scene] = Field(..., description="场次列表")
    plain_text: str = Field(..., description="纯文本全文", min_length=1)
    word_count: int = Field(default=0, description="服务端计算的字数", ge=0)
    dialogue_ratio: float = Field(
        default=0.0, description="服务端计算的对白占比", ge=0.0, le=1.0
    )
    referenced_outline_artifact_id: UUID = Field(
        ..., description="引用的分集大纲 Artifact ID"
    )

    @model_validator(mode="after")
    def _check_min_scenes(self) -> "ScriptDraft":
        if len(self.scenes) < 2:
            raise ValueError(
                f"第 {self.episode_number} 集至少需要 2 场戏，当前为 {len(self.scenes)}"
            )
        return self

    @property
    def content_schema_version(self) -> str:
        """标记包含有序场景的稿件，包括局部升级的修订稿。"""
        if any(scene.blocks is not None for scene in self.scenes):
            return ORDERED_SCRIPT_SCHEMA_VERSION
        return "1.0"
