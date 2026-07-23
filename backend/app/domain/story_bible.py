"""故事设定模型 — CharacterProfile 与 StoryBible（§5.5）。

StoryBible 是整部短剧的世界观、人物和规则集合，
一经创建不可原地覆盖，修订时必须产生新版本。
"""

from pydantic import BaseModel, Field, model_validator


class CharacterProfile(BaseModel):
    """角色档案。

    包含角色的外在目标、内在需求、性格特质和禁止修改项。
    """

    model_config = {"extra": "forbid"}

    character_id: str = Field(
        ..., description="角色唯一标识，稳定且可在后续 fixture 中引用", min_length=1
    )
    name: str = Field(..., description="角色姓名", min_length=1)
    role: str = Field(..., description="角色定位，如“主角”“反派”“导师”", min_length=1)
    age_range: str | None = Field(default=None, description="年龄范围")
    visible_goal: str = Field(..., description="外在目标", min_length=1)
    hidden_need: str | None = Field(default=None, description="内在需求")
    traits: list[str] = Field(default_factory=list, description="性格特质")
    strengths: list[str] = Field(default_factory=list, description="优势")
    flaws: list[str] = Field(default_factory=list, description="缺陷")
    relationship_notes: list[str] = Field(
        default_factory=list, description="与其他角色的关系说明"
    )
    forbidden_changes: list[str] = Field(
        default_factory=list,
        description="禁止修改的设定项，修订时必须保留",
    )


class StoryBible(BaseModel):
    """故事宝典。

    包含完整的世界观、人物、冲突、规则和锁定事实。
    locked_facts 在修订连续性检查中作为硬约束。
    """

    model_config = {"extra": "forbid"}

    title: str = Field(..., description="作品标题", min_length=1)
    logline: str = Field(..., description="一句话故事梗概", min_length=1)
    genre: str = Field(..., description="题材标签", min_length=1)
    tone: list[str] = Field(default_factory=list, description="调性标签")
    world_setting: str = Field(..., description="世界观设定", min_length=1)
    protagonist: CharacterProfile = Field(..., description="主角档案")
    antagonist: CharacterProfile = Field(..., description="反派档案")
    supporting_characters: list[CharacterProfile] = Field(
        default_factory=list, description="配角档案列表"
    )
    main_conflict: str = Field(..., description="主要冲突", min_length=1)
    stakes: str = Field(..., description="失败代价", min_length=1)
    story_rules: list[str] = Field(default_factory=list, description="故事规则")
    long_term_payoffs: list[str] = Field(
        default_factory=list, description="长期伏笔与回收计划"
    )
    open_loops: list[str] = Field(
        default_factory=list, description="当前未闭合的故事线索"
    )
    locked_facts: list[str] = Field(
        default_factory=list, description="锁定事实 — 连续性检查的硬约束"
    )
    compliance_notes: list[str] = Field(
        default_factory=list, description="合规注意事项"
    )

    @model_validator(mode="after")
    def _protagonist_not_antagonist(self) -> "StoryBible":
        """主角和反派不能是同一角色。"""
        if self.protagonist.character_id == self.antagonist.character_id:
            raise ValueError(
                f"主角和反派不能为同一角色: {self.protagonist.character_id}"
            )
        return self

    @model_validator(mode="after")
    def _supporting_no_duplicate_ids(self) -> "StoryBible":
        """配角 ID 不能与主角或反派重复。"""
        core_ids = {self.protagonist.character_id, self.antagonist.character_id}
        for char in self.supporting_characters:
            if char.character_id in core_ids:
                raise ValueError(
                    f"配角 {char.name} 的 character_id '{char.character_id}' "
                    f"与主角或反派重复"
                )
        return self
