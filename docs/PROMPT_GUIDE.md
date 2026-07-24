# DramaAgent Prompt 使用指南

> 文档版本：1.1.0  
> 对应任务：C-01 ~ C-05（Prompt Loader + 创作 Skills 已完成）  
> 最后更新：2026-07-24

## 概述

DramaAgent 的 Prompt 是版本化的代码资产，不是散落在代码中的魔法字符串。所有 Prompt 模板集中管理在 `backend/app/prompts/` 目录下，通过 `PromptLoader` 按名称和版本加载。

### 核心原则

1. **Prompt 即代码** — 每次修改必须更新 version 与 changelog
2. **模板与元数据分离** — YAML frontmatter 描述元数据，Markdown 正文是模板
3. **变量缺失立即失败** — 不静默使用空值或默认值
4. **不硬编码模型名/密钥** — 模型名通过 `{{ model_name }}` 变量传入
5. **内容哈希不可变** — 相同版本的 Prompt 必须产生相同哈希

## 目录结构

```
backend/app/prompts/
├── __init__.py          # 公开 API
├── manifest.yaml        # Prompt 总清单（名称、版本、Schema 绑定、模板路径、changelog）
├── loader.py            # PromptLoader 实现
└── templates/           # 模板文件目录
    ├── requirement.md       # 需求归一化
    ├── story_bible.md       # StoryBible 生成
    ├── outline.md           # 分集大纲
    ├── episode_writer.md    # 单集剧本写作
    ├── evaluate_episode.md  # 剧集评估
    └── episode_summary.md   # 剧集摘要
```

## Manifest 格式

`manifest.yaml` 是一个 YAML 文件，包含 `prompts` 列表。每个条目：

```yaml
prompts:
  - name: story_bible           # 唯一名称（kebab-case）
    version: "1.0.0"            # 语义化版本号
    input_schema: StoryBibleInput   # 输入 Pydantic Schema 类名
    output_schema: StoryBible       # 输出 Pydantic Schema 类名
    owner: planner              # 所属角色：normalizer | planner | writer | evaluator | summarizer
    template: story_bible.md    # 模板文件名（相对于 templates/ 目录）
    changelog: "初始版本：从归一化需求生成完整故事设定"
```

### 约束

- 同一 `(name, version)` 组合不允许重复
- `output_schema` 必须已在 `app.domain` 中注册（通过 `register_schema`）
- 模板文件必须存在且 frontmatter 与 manifest 一致

## 模板文件格式

每个模板文件包含 YAML frontmatter（可选，但推荐）和 Markdown 正文：

```markdown
---
name: story_bible
version: "1.0.0"
input_schema: StoryBibleInput
output_schema: StoryBible
owner: planner
changelog: 初始版本
---

# 故事设定生成

你是一位经验丰富的短剧编剧...

## 当前任务

根据归一化需求生成 StoryBible：

{{ normalized_requirement }}
```

### 模板变量

使用 `{{ variable_name }}` 语法（双花括号）声明变量。渲染时：

- 所有变量必须提供值，否则抛出 `KeyError`（列出缺失变量名）
- 支持任意数量的变量
- 变量名只含字母、数字、下划线

### Frontmatter 校验

- 若 frontmatter 中的 `name` 或 `version` 与 manifest 不一致，加载失败
- 若模板文件不存在，加载失败并指出缺失文件路径

## PromptLoader API

### 初始化

```python
from app.prompts import PromptLoader

# 使用默认路径（backend/app/prompts/ 下的 manifest.yaml 和 templates/）
loader = PromptLoader()

# 或指定自定义路径
loader = PromptLoader(
    manifest_path="/custom/manifest.yaml",
    templates_dir="/custom/templates",
)
```

### 加载 Prompt

```python
# 获取最新版本
tpl = loader.get("story_bible")

# 获取指定版本
tpl = loader.get("story_bible", version="1.0.0")

# 列出所有可用 Prompt 名称
names = loader.list_names()  # ['evaluate_episode', 'normalize_requirement', ...]

# 获取所有 Prompt 模板
all_tpls = loader.list_all()
```

### 渲染 Prompt

```python
tpl = loader.get("story_bible")

# 正常渲染 — 变量缺失抛 KeyError
rendered = tpl.render(
    normalized_requirement="...",
    rag_context="...",
)

# 安全渲染 — 缺失变量保留 {{ var }} 占位符（仅调试用）
debug = tpl.render_safe(normalized_requirement="...")
```

### 提取变量名

```python
tpl.variables  # {'normalized_requirement', 'rag_context'}
```

### 获取内容哈希

```python
tpl.content_hash  # SHA256 十六进制字符串（64 字符）
```

## Schema 注册

Prompt Loader 启动时自动注册 `app.domain` 中已有的 Schema。新增 Schema 需在对应模块中调用 `register_schema`：

```python
from app.prompts import register_schema

register_schema("NewSchema", NewSchema)
```

## 版本追踪

每次 LLM 调用记录 `prompt_version` 和 `prompt_name`：

- `LLMCall.prompt_version` — 使用的 Prompt 版本号
- `Artifact.prompt_version` — 生成的 Artifact 关联的 Prompt 版本
- `Artifact.content_hash` 可与 `PromptTemplate.content_hash` 交叉校验

## 修改 Prompt 的流程

1. 编辑模板文件（`templates/*.md`）
2. 更新 `manifest.yaml` 中对应条目的 `version` 和 `changelog`
3. 可选：更新模板 frontmatter 中的 `version` 和 `changelog`
4. 运行 `pytest tests/contract/test_prompts.py` 确认所有测试通过
5. 更新 `TestRealManifest.test_hash_snapshot_regression` 中的预期哈希

### 如果 snapshot 测试失败

```
AssertionError: Prompt '{name}' v{version} 的哈希与快照不一致！
  当前哈希: <new_hash>
  快照哈希: <old_hash>
  如果模板内容确实被修改，请同时更新 version 和 changelog。
```

**操作**：
- 如果是有意修改：更新 `manifest.yaml` 的 `version` → 重新运行 `test_hash_snapshot_regression` → 更新测试中的预期哈希
- 如果是意外修改：还原模板文件 → 哈希恢复 → 测试通过

## 添加新 Prompt

1. 创建模板文件 `templates/new_prompt.md`
2. 在 `manifest.yaml` 中注册条目
3. 确保相应的 `input_schema` / `output_schema` 已注册
4. 在 `TestRealManifest.test_hash_snapshot_regression` 中添加预期哈希

## 常见问题

**Q: 为什么 Schema 未注册只是警告而非错误？**

A: 因为下游任务（C-02 ~ C-05）会逐步创建 `input_schema`（如 `RequirementInput`、`StoryBibleInput`）。在 `strict_schema_check=False`（默认）时，未注册的 Schema 仅记录警告。在所有 Schema 就绪后，可启用 `strict_schema_check=True`。

**Q: 如何在 CI 中保护 Prompt 不变性？**

A: `TestRealManifest.test_hash_snapshot_regression` 测试记录了当前所有 Prompt 的 SHA256 哈希。任何模板内容修改都会导致哈希不匹配，CI 将失败，提示开发者更新 version。
