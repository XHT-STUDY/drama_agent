"""DramaAgent Prompt 管理模块。

提供 Prompt 模板的加载、校验、版本追踪和渲染功能。

核心组件：
- PromptLoader：一次性加载 manifest.yaml 和所有模板文件
- PromptTemplate：已加载的模板（含元数据、正文和内容哈希）
- PromptManifest / PromptManifestItem：Manifest 数据模型
- register_schema / resolve_schema：Schema 名称注册表
- compute_prompt_hash：计算模板内容的 SHA256 哈希

使用示例：
    from app.prompts import PromptLoader

    loader = PromptLoader()
    tpl = loader.get("story_bible")
    rendered = tpl.render(
        normalized_requirement="...",
        rag_context="...",
    )
"""

from app.prompts.loader import (
    PromptLoader,
    PromptLoadError,
    PromptManifest,
    PromptManifestItem,
    PromptRenderError,
    PromptTemplate,
    compute_prompt_hash,
    register_schema,
    resolve_schema,
)

__all__ = [
    "PromptLoader",
    "PromptTemplate",
    "PromptManifest",
    "PromptManifestItem",
    "PromptLoadError",
    "PromptRenderError",
    "register_schema",
    "resolve_schema",
    "compute_prompt_hash",
]
