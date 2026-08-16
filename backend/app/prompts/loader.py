"""Prompt Loader — 按 name/version 加载 Prompt 模板。

职责（见 DEV_PLAN §9.2、C-01 任务卡）：
- 解析 manifest.yaml，校验 name/version 唯一性
- 加载模板文件（Markdown + YAML frontmatter）
- 启动时校验 input_schema / output_schema 存在
- 按 {{ variable }} 渲染模板，变量缺失立即失败
- 计算 Prompt 内容 SHA256 哈希（写入 LLM Call 与 Artifact）
- manifest 记录 changelog

模块边界：
- prompts 层只负责加载、校验、渲染 Prompt 模板
- 不直接调用 LLM、不访问数据库、不操作前端
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

# ---- 模板变量匹配正则：{{ variable_name }} ----
_VAR_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")


# ---- Schema 名称 → Pydantic 类 映射表 ----
# 各领域模块在创建 Schema 时应注册到此处。
# 下游任务（C-02 ~ C-05）会陆续填充此表。
_SCHEMA_REGISTRY: dict[str, type[BaseModel]] = {}


def register_schema(name: str, cls: type[BaseModel]) -> None:
    """将 Pydantic Schema 类注册到全局名称表。

    注册后 Prompt Loader 即可校验 manifest 中引用的
    input_schema / output_schema 是否真实存在。

    Args:
        name: Schema 简称（如 "StoryBible"、"NormalizedRequirement"）
        cls: Pydantic v2 BaseModel 子类
    """
    if name in _SCHEMA_REGISTRY:
        existing = _SCHEMA_REGISTRY[name]
        if existing is not cls:
            logger.warning(
                "Schema 名称 '%s' 被重复注册：%s → %s",
                name, existing.__name__, cls.__name__,
            )
    _SCHEMA_REGISTRY[name] = cls


def resolve_schema(name: str) -> type[BaseModel] | None:
    """按名称解析已注册的 Pydantic Schema 类。

    Args:
        name: Schema 简称（如 "StoryBible"）

    Returns:
        对应的 Pydantic 类；未注册时返回 None。
    """
    return _SCHEMA_REGISTRY.get(name)


def _auto_register_domain_schemas() -> None:
    """自动注册 app.domain 中已存在的所有 Schema 类。

    在 PromptLoader 首次初始化时调用。
    只注册已实现且通过 A-03 契约测试的 Schema。
    """
    from app.domain.continuity import (  # noqa: F401
        CharacterState,
        ContinuityState,
        EpisodeSummary,
        RelationshipChange,
        StoryLoop,
        TimelineEvent,
    )
    from app.domain.enums import (  # noqa: F401
        ArtifactStatus,
        ArtifactType,
        EvaluationDimension,
        ProjectStatus,
    )
    from app.domain.evaluation import EvaluationInput, EvaluationIssue, EvaluationReport  # noqa: F401
    from app.domain.outline import EpisodeOutline, EpisodeOutlineSet, OutlineInput  # noqa: F401
    from app.domain.requirement import NeedsUserInput, NormalizedRequirement, RequirementInput  # noqa: F401
    from app.domain.revision import (  # noqa: F401
        ContinuityCheckInput,
        ContinuityCheckResult,
        ContinuitySemanticCheck,
        ContinuityViolation,
        ContinuityWarning,
        OperationExecution,
        RevisionOperation,
        RevisionPlan,
        RevisionPlanInput,
        RevisionResult,
        RevisionTaskInput,
    )
    from app.domain.script import DialogueLine, EpisodeWriterInput, Scene, ScriptDraft  # noqa: F401
    from app.domain.story_bible import CharacterProfile, StoryBible, StoryBibleInput  # noqa: F401
    from app.domain.summary import (  # noqa: F401
        ConversationSummary,
        ConversationSummaryBody,
        ConversationSummaryInput,
        SummaryInput,
        SummaryOutput,
    )

    # 注册核心 Schema
    register_schema("RequirementInput", RequirementInput)
    register_schema("NeedsUserInput", NeedsUserInput)
    register_schema("NormalizedRequirement", NormalizedRequirement)
    register_schema("StoryBible", StoryBible)
    register_schema("StoryBibleInput", StoryBibleInput)
    register_schema("CharacterProfile", CharacterProfile)
    register_schema("EpisodeOutline", EpisodeOutline)
    register_schema("EpisodeOutlineSet", EpisodeOutlineSet)
    register_schema("OutlineInput", OutlineInput)
    register_schema("Scene", Scene)
    register_schema("DialogueLine", DialogueLine)
    register_schema("EpisodeWriterInput", EpisodeWriterInput)
    register_schema("ScriptDraft", ScriptDraft)
    register_schema("EvaluationInput", EvaluationInput)
    register_schema("EvaluationIssue", EvaluationIssue)
    register_schema("EvaluationReport", EvaluationReport)
    register_schema("RevisionOperation", RevisionOperation)
    register_schema("RevisionPlan", RevisionPlan)
    register_schema("RevisionPlanInput", RevisionPlanInput)
    register_schema("OperationExecution", OperationExecution)
    register_schema("RevisionResult", RevisionResult)
    register_schema("RevisionTaskInput", RevisionTaskInput)
    register_schema("ContinuityViolation", ContinuityViolation)
    register_schema("ContinuityWarning", ContinuityWarning)
    register_schema("ContinuitySemanticCheck", ContinuitySemanticCheck)
    register_schema("ContinuityCheckInput", ContinuityCheckInput)
    register_schema("ContinuityCheckResult", ContinuityCheckResult)
    register_schema("EpisodeSummary", EpisodeSummary)
    register_schema("StoryLoop", StoryLoop)
    register_schema("CharacterState", CharacterState)
    register_schema("RelationshipChange", RelationshipChange)
    register_schema("TimelineEvent", TimelineEvent)
    register_schema("ContinuityState", ContinuityState)
    register_schema("SummaryInput", SummaryInput)
    register_schema("SummaryOutput", SummaryOutput)
    register_schema("ConversationSummaryInput", ConversationSummaryInput)
    register_schema("ConversationSummaryBody", ConversationSummaryBody)
    register_schema("ConversationSummary", ConversationSummary)


# ========================================================================
# Pydantic 模型
# ========================================================================


class PromptManifestItem(BaseModel):
    """Manifest 中单条 Prompt 条目。

    对应 manifest.yaml 中 prompts 列表的每一项。
    """

    model_config = {"extra": "forbid"}

    name: str = Field(..., description="Prompt 唯一名称，如 'story_bible'", min_length=1)
    version: str = Field(..., description="语义化版本号，如 '1.0.0'", min_length=1)
    input_schema: str = Field(..., description="输入 Schema 类名", min_length=1)
    output_schema: str = Field(..., description="输出 Schema 类名", min_length=1)
    owner: str = Field(
        ..., description="所属角色：planner / writer / evaluator / summarizer / normalizer", min_length=1
    )
    template: str = Field(..., description="模板文件名（相对于 templates/ 目录）", min_length=1)
    changelog: str = Field(default="", description="本版本的变更说明")


class PromptManifest(BaseModel):
    """完整的 Prompt Manifest 清单。

    从 manifest.yaml 加载并校验。
    """

    model_config = {"extra": "forbid"}

    prompts: list[PromptManifestItem] = Field(
        default_factory=list,
        description="Prompt 条目列表",
        min_length=1,
    )


class PromptTemplate(BaseModel):
    """已加载并可渲染的 Prompt 模板。

    包含 YAML frontmatter 元数据、模板正文和内容哈希。
    """

    model_config = {"extra": "forbid"}

    name: str = Field(..., description="Prompt 唯一名称")
    version: str = Field(..., description="语义化版本号")
    input_schema: str = Field(..., description="输入 Schema 类名")
    output_schema: str = Field(..., description="输出 Schema 类名")
    owner: str = Field(..., description="所属角色")
    template_content: str = Field(..., description="模板正文（含 {{ var }} 占位符）")
    changelog: str = Field(default="", description="本版本变更说明")
    content_hash: str = Field(..., description="模板正文的 SHA256 哈希值", min_length=1)

    @property
    def variables(self) -> set[str]:
        """提取模板中所有 {{ variable_name }} 占位符。"""
        return set(_VAR_PATTERN.findall(self.template_content))

    def render(self, **variables: str) -> str:
        """用给定变量值渲染模板。

        模板中所有 {{ variable_name }} 将被替换为对应值。
        任何在模板中出现但未传入的变量将立即引发 KeyError。

        Args:
            **variables: 变量名 → 替换值的映射

        Returns:
            渲染后的 Prompt 文本

        Raises:
            KeyError: 模板中存在但未提供的变量名
        """
        required = self.variables
        provided = set(variables.keys())
        missing = required - provided
        if missing:
            raise KeyError(
                f"Prompt '{self.name}' v{self.version} 缺少必需变量: "
                f"{', '.join(sorted(missing))}"
            )

        def _replacer(match: re.Match[str]) -> str:
            return variables[match.group(1)]

        return _VAR_PATTERN.sub(_replacer, self.template_content)

    def render_safe(self, **variables: str) -> str:
        """安全渲染——缺失变量用占位符替代而不抛异常。

        仅用于调试和提示场景；正式生成请使用 render()。
        """
        def _replacer(match: re.Match[str]) -> str:
            key = match.group(1)
            return variables.get(key, match.group(0))

        return _VAR_PATTERN.sub(_replacer, self.template_content)


# ========================================================================
# Prompt Loader
# ========================================================================


class PromptLoadError(Exception):
    """Prompt 加载失败——模板不存在、manifest 损坏或 Schema 未找到。"""


class PromptRenderError(Exception):
    """Prompt 渲染失败——模板变量缺失。"""


class PromptLoader:
    """Prompt 模板加载器。

    职责：
    - 一次性加载 manifest.yaml 和所有模板文件
    - 校验 manifest 内 name/version 唯一性
    - 校验模板引用的 Schema 类存在
    - 提供按 name（可选 version）查询 PromptTemplate

    使用示例：
        loader = PromptLoader()
        tpl = loader.get("story_bible")
        rendered = tpl.render(user_input="...")
    """

    def __init__(
        self,
        manifest_path: str | Path | None = None,
        templates_dir: str | Path | None = None,
        *,
        strict_schema_check: bool = False,
    ) -> None:
        """初始化并加载 Prompt Loader。

        Args:
            manifest_path: manifest.yaml 路径。默认为 ../manifest.yaml（相对于本文件）
            templates_dir: 模板目录路径。默认为 ../templates/（相对于本文件）
            strict_schema_check: True 时，Schema 未注册视为加载失败；
                                 默认 False（记录警告），因为下游任务可能尚未创建 Schema。

        Raises:
            PromptLoadError: manifest 无效、模板缺失或 Schema 缺失（strict 模式）
        """
        self.strict_schema_check = strict_schema_check
        self._prompts: dict[str, dict[str, PromptTemplate]] = {}  # name → version → template

        # 自动注册已知的 domain Schema
        _auto_register_domain_schemas()

        # 路径解析
        if manifest_path is None:
            manifest_path = Path(__file__).resolve().parent / "manifest.yaml"
        if templates_dir is None:
            templates_dir = Path(__file__).resolve().parent / "templates"

        self.manifest_path = Path(manifest_path)
        self.templates_dir = Path(templates_dir)

        # 加载
        manifest = self._load_manifest()
        self._validate_manifest(manifest)
        self._load_templates(manifest)
        self._validate_schemas(manifest)

        logger.info(
            "PromptLoader 就绪：已加载 %d 个 Prompt（%d 个唯一名称）",
            sum(len(v) for v in self._prompts.values()),
            len(self._prompts),
        )

    # ---- Manifest 加载 ----

    def _load_manifest(self) -> PromptManifest:
        """从 YAML 文件加载并解析 manifest。

        Raises:
            PromptLoadError: 文件不存在或 YAML 格式错误
        """
        if not self.manifest_path.exists():
            raise PromptLoadError(
                f"Manifest 文件不存在: {self.manifest_path}"
            )

        try:
            raw = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            raise PromptLoadError(
                f"Manifest YAML 解析失败: {self.manifest_path}\n{e}"
            ) from e

        if raw is None or not isinstance(raw, dict):
            raise PromptLoadError(
                f"Manifest 内容为空或格式错误: {self.manifest_path}"
            )

        try:
            return PromptManifest.model_validate(raw)
        except ValidationError as e:
            raise PromptLoadError(
                f"Manifest Schema 校验失败 ({self.manifest_path}):\n{e}"
            ) from e

    def _validate_manifest(self, manifest: PromptManifest) -> None:
        """校验 manifest 内 name/version 唯一性。

        Raises:
            PromptLoadError: 存在重复的 (name, version) 组合
        """
        seen: dict[tuple[str, str], int] = {}
        for idx, item in enumerate(manifest.prompts):
            key = (item.name, item.version)
            if key in seen:
                first_idx = seen[key]
                raise PromptLoadError(
                    f"Prompt '{item.name}' version '{item.version}' 重复："
                    f"第 {first_idx + 1} 条和第 {idx + 1} 条"
                )
            seen[key] = idx

    # ---- 模板加载 ----

    def _load_templates(self, manifest: PromptManifest) -> None:
        """加载 manifest 中引用的所有模板文件。

        每个条目调用 _load_single_template，
        加载成功后按 (name, version) 索引到 self._prompts。

        Raises:
            PromptLoadError: 模板文件不存在或 frontmatter 不匹配
        """
        for item in manifest.prompts:
            tpl = self._load_single_template(item)
            self._prompts.setdefault(item.name, {})[item.version] = tpl

    def _load_single_template(self, item: PromptManifestItem) -> PromptTemplate:
        """加载单个模板文件并校验 frontmatter。

        Args:
            item: manifest 中的 Prompt 条目

        Returns:
            构造好的 PromptTemplate（含元数据、正文、哈希）

        Raises:
            PromptLoadError: 模板文件不存在或 frontmatter 与 manifest 不一致
        """
        template_path = self.templates_dir / item.template

        if not template_path.exists():
            raise PromptLoadError(
                f"Prompt '{item.name}' v{item.version} 的模板文件不存在: {template_path}"
            )

        raw_text = template_path.read_text(encoding="utf-8")

        # 解析 YAML frontmatter（必须在文件最开头，以 --- 起止）
        body = raw_text
        if raw_text.startswith("---"):
            parts = raw_text.split("---", 2)
            if len(parts) >= 3:
                try:
                    frontmatter = yaml.safe_load(parts[1])
                except yaml.YAMLError as e:
                    raise PromptLoadError(
                        f"模板 frontmatter YAML 解析失败: {template_path}\n{e}"
                    ) from e
                body = parts[2].lstrip("\n")

                # 校验 frontmatter 与 manifest 一致
                if frontmatter and isinstance(frontmatter, dict):
                    fm_name = frontmatter.get("name")
                    fm_version = frontmatter.get("version")
                    if fm_name and fm_name != item.name:
                        raise PromptLoadError(
                            f"模板 frontmatter name='{fm_name}' 与 manifest "
                            f"name='{item.name}' 不一致: {template_path}"
                        )
                    if fm_version and fm_version != item.version:
                        raise PromptLoadError(
                            f"模板 frontmatter version='{fm_version}' 与 manifest "
                            f"version='{item.version}' 不一致: {template_path}"
                        )

        # 计算 SHA256 哈希（对模板正文，不含 frontmatter）
        content_hash = _sha256(body)

        return PromptTemplate(
            name=item.name,
            version=item.version,
            input_schema=item.input_schema,
            output_schema=item.output_schema,
            owner=item.owner,
            template_content=body,
            changelog=item.changelog,
            content_hash=content_hash,
        )

    # ---- Schema 校验 ----

    def _validate_schemas(self, manifest: PromptManifest) -> None:
        """校验 manifest 中引用的 input_schema / output_schema 已注册。

        在 strict 模式下，未注册 Schema 引发 PromptLoadError。
        在非 strict 模式下，仅记录 WARNING 日志（允许下游任务逐步填充 Schema）。

        Raises:
            PromptLoadError: strict 模式下有 Schema 未注册
        """
        unresolved: list[str] = []
        all_schemas: set[str] = set()
        for item in manifest.prompts:
            all_schemas.add(item.input_schema)
            all_schemas.add(item.output_schema)

        for name in sorted(all_schemas):
            if resolve_schema(name) is None:
                unresolved.append(name)

        if unresolved:
            msg = (
                f"Prompt Loader：{len(unresolved)} 个 Schema 未注册 – "
                f"{', '.join(unresolved)}"
            )
            if self.strict_schema_check:
                raise PromptLoadError(msg)
            else:
                logger.warning(msg + "（相关 Prompt Skill 尚未创建时属正常）")

    # ---- 查询接口 ----

    def get(self, name: str, version: str | None = None) -> PromptTemplate:
        """按名称（和可选版本）获取 Prompt 模板。

        Args:
            name: Prompt 名称（如 "story_bible"）
            version: 版本号。为 None 时返回最新版本（按 semver 排序）。

        Returns:
            匹配的 PromptTemplate

        Raises:
            KeyError: 该 name 不存在
            KeyError: 指定 version 不存在
        """
        versions = self._prompts.get(name)
        if versions is None:
            available = sorted(self._prompts.keys())
            raise KeyError(
                f"Prompt '{name}' 不存在。可用 Prompt: {', '.join(available)}"
            )

        if version is not None:
            tpl = versions.get(version)
            if tpl is None:
                raise KeyError(
                    f"Prompt '{name}' version '{version}' 不存在。"
                    f"可用版本: {', '.join(sorted(versions.keys()))}"
                )
            return tpl

        # 返回最新版本（按 semver 排序）
        latest_key = sorted(
            versions.keys(),
            key=lambda v: tuple(int(x) for x in v.split(".")),
        )[-1]
        return versions[latest_key]

    def list_all(self) -> list[PromptTemplate]:
        """列出所有已加载的 Prompt 模板（所有版本）。"""
        result: list[PromptTemplate] = []
        for versions in self._prompts.values():
            result.extend(versions.values())
        return result

    def list_names(self) -> list[str]:
        """列出所有 Prompt 名称（去重）。"""
        return sorted(self._prompts.keys())

    def get_manifest_summary(self) -> list[dict[str, str]]:
        """返回 manifest 精简摘要（用于 API 或调试）。"""
        return [
            {
                "name": tpl.name,
                "version": tpl.version,
                "owner": tpl.owner,
                "content_hash": tpl.content_hash[:12],
            }
            for tpl in self.list_all()
        ]


# ========================================================================
# 工具函数
# ========================================================================


def _sha256(text: str) -> str:
    """计算文本的 SHA256 十六进制哈希。

    Args:
        text: 待哈希的文本内容

    Returns:
        64 字符的十六进制哈希字符串
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_prompt_hash(template_content: str) -> str:
    """计算 Prompt 模板内容的 SHA256 哈希（供外部使用）。

    Args:
        template_content: 模板正文（不含 frontmatter）

    Returns:
        64 字符的十六进制哈希字符串
    """
    return _sha256(template_content)
