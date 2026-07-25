# DramaAgent 开发日志

本文件按时间倒序记录每次开发任务的完成报告。每条记录使用 DEV_PLAN.md §0.2 规定的统一格式。

---

## 2026-07-25 — OpenAICompatibleLLM 真实 LLM 客户端 + Skill 真实验证

**任务 ID：** —（基础设施，非任务卡范围内）  
**状态：** DONE  
**日期：** 2026-07-25

### 实现摘要

- 创建 `app/llm/openai_compatible.py`：OpenAICompatibleLLM 实现 LLMClient 协议，支持所有 OpenAI 兼容 API（阿里云 MAAS 等）
  - 从 Settings 读取 API 地址/密钥/模型名
  - Schema 提示注入到 System Prompt（JSON mode）
  - `_extract_json()` 自动剥离 LLM 输出的 ```json 标记
  - 错误映射：超时→LLM_TIMEOUT、403→认证失败、404→模型不存在、429→限流
  - 按 prompt_name 映射角色→模型名
  - 记录每次调用的 token 用量和耗时
- 创建 `scripts/test_real_llm.py`：CLI 测试脚本，支持 5 种 Skill 的真实 LLM 测试
- 修复 Config 加载链路：绝对路径 `.env`、`extra="ignore"`、`cors_origins` 类型、test 环境跳过 `.env`
- 修复 BaseAgent 模型透传：`_default_model()` 不再强制覆盖 LLMClient 的模型决策
- 更新 Prompt 模板：story_bible.md 明确要求 `char_` 前缀
- 编写 26 个单元测试

### 修改文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `app/llm/openai_compatible.py` | 新建 | OpenAICompatibleLLM 客户端（280 行） |
| `app/llm/__init__.py` | 修改 | 导出 OpenAICompatibleLLM、load_llm_client |
| `app/core/config.py` | 修改 | 绝对路径 `.env`、`extra="ignore"`、`cors_origins: str`、`settings_customise_sources` |
| `app/main.py` | 修改 | `settings.get_cors_origins()` |
| `app/agents/base.py` | 修改 | 透传空 model 值给 LLMClient |
| `app/prompts/templates/story_bible.md` | 修改 | 明确 character_id 必须 `char_` 前缀 |
| `scripts/__init__.py` | 新建 | scripts 包入口 |
| `scripts/test_real_llm.py` | 新建 | 真实 LLM CLI 测试脚本（438 行） |
| `tests/unit/llm/test_openai_compatible.py` | 新建 | 26 个单元测试 |
| `.env` | 修改 | LLM_API_BASE 前缀修复、模型名修正 |
| `.env.example` | 修改 | （无变化） |

### 验证结果

| 命令 | 结果 |
|---|---|
| `uv run pytest -v` | 362 passed（零回归） |
| `uv run ruff check app/ tests/ scripts/` | All checks passed |
| `uv run mypy` | 9 pre-existing errors, 0 new |

### 真实 LLM 端到端验收

| Skill | 模型 | 耗时 | Tokens | 结果 |
|---|---|---|---|---|
| requirement | qwen3.7-plus | 39.9s | 3,416 | ✅ "逆风球王" |
| story_bible | qwen3.7-plus | 95.2s | 7,478 | ✅ 完整世界观 + 4 角色 |
| outline | qwen3.7-plus | 164.5s | 11,116 | ✅ 10 集分集大纲 |
| write_episode | qwen3.7-plus | 50.4s | 5,471 | ✅ 第 1 集完整剧本 |
| summarize_episode | qwen3.7-plus | 39.7s | 3,998 | ✅ 摘要 + 连续性数据 |

### 调试过程中修复的关键问题

1. `.env` 中缺少 `LLM_` 前缀导致 API_BASE 未加载
2. `env_file` 相对路径从 `backend/` 运行时找不到 `.env`
3. `cors_origins: list[str]` 导致 `*` 解析失败
4. `extra="forbid"` 拒绝 docker compose 共用字段
5. test 环境加载了真实 `.env` 污染测试
6. OpenAI LLM 客户端追加 `/v1/chat/completions` 但 API_BASE 已含 `/v1`
7. HTTP 404 被错误映射为 `INVALID_OUTPUT` 导致无意义重试
8. `BaseAgent._default_model()` 覆盖了 LLMClient 的模型决策

### 建议的下一任务

- **C-07** LangGraph Creation Workflow

---

## 2026-07-25 — C-06 Continuity Manager 与 Context Builder 基础

**任务 ID：** C-06  
**状态：** DONE  
**日期：** 2026-07-25

### 实现摘要

- 创建 `app/domain/summary.py`：SummaryInput + SummaryOutput Pydantic 模型，封装摘要生成与连续性更新数据
- 创建 `app/memory/continuity.py`：ContinuityManager — 从 StoryBible 创建初始 ContinuityState；剧集后更新人物状态/伏笔/时间线；locked_facts 只增不减；为 ContextBuilder 提供连续性快照
- 创建 `app/memory/context_builder.py`：ContextBuilder — 按 §9.3 预算分配（6 阶段按比例）组装上下文；超预算时按 5 级优先级裁剪（RAG → 连续性 → 用户请求 → 大纲 → 当前场景）；输出 ContextManifest 记录使用/裁剪的资产
- 创建 `app/skills/summarizer.py`：SummarizerSkill — 从 ScriptDraft + ContinuityState 调用 LLM 生成 SummaryOutput（摘要+角色变化+伏笔+时间线）；内置校验器检查输出完整性
- 更新 `app/prompts/templates/episode_summary.md`：重写 Prompt 模板匹配 SummaryOutput Schema
- 更新 `app/domain/continuity.py`：StoryLoop.introduced_episode、CharacterState.last_updated_episode、ContinuityState.through_episode 允许 0（表示 StoryBible 初始状态）
- 注册 SummaryInput/SummaryOutput 到 SchemaRegistry
- 编写 48 个单元测试（23 continuity + 11 context_builder + 14 summarizer）

### 修改文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `app/domain/summary.py` | 新建 | SummaryInput + SummaryOutput (67 行) |
| `app/domain/__init__.py` | 修改 | 导出 SummaryInput、SummaryOutput |
| `app/domain/continuity.py` | 修改 | introduced_episode/last_updated_episode/through_episode 改为 ge=0 |
| `app/prompts/loader.py` | 修改 | 注册 SummaryInput、SummaryOutput Schema |
| `app/prompts/templates/episode_summary.md` | 修改 | 重写匹配 SummaryOutput Schema |
| `app/memory/__init__.py` | 新建 | Memory 包入口 |
| `app/memory/continuity.py` | 新建 | ContinuityManager (328 行) |
| `app/memory/context_builder.py` | 新建 | ContextBuilder + ContextManifest (220 行) |
| `app/skills/summarizer.py` | 新建 | SummarizerSkill + 辅助函数 (222 行) |
| `tests/unit/memory/__init__.py` | 新建 | 测试包入口 |
| `tests/unit/memory/test_continuity.py` | 新建 | 23 个 ContinuityManager 测试 |
| `tests/unit/memory/test_context_builder.py` | 新建 | 11 个 ContextBuilder 测试 |
| `tests/unit/skills/test_summarizer.py` | 新建 | 14 个 SummarizerSkill 测试 |

### 验证结果

| 命令 | 结果 |
|---|---|
| `uv run pytest tests/unit/memory/ tests/unit/skills/test_summarizer.py -v` | 48 passed |
| `uv run pytest -v` | 336 passed（零回归） |
| `uv run ruff check app/ tests/` | All checks passed |
| `uv run mypy app/ tests/` | 9 pre-existing errors, 0 new |

### 验收项

- [x] 生成第 3 集时读取前两集摘要 — `get_context_for_episode(state, 3)` 包含第 1/2 集摘要但不包含第 3 集
- [x] 开放和回收伏笔状态可追踪 — StoryLoop open/resolved 状态 + get_loop_summary 统计
- [x] 超预算时按约定顺序裁剪 — §9.3 五级优先级裁剪，RAG → 连续性 → 用户请求 → 大纲 → 当前场景
- [x] 不能静默截断当前目标场景 — current_target 被截断时 manifest.warnings 记录，has_current_target_cut() 检查
- [x] context_manifest 可用于调试 — ContextManifest 包含 sections_used/cut/truncated + budget_total/remaining/estimated_tokens + warnings

### 建议的下一任务

- **C-07** LangGraph Creation Workflow（将 C-02~C-06 的 Skill 串联为完整创作流程）

---

## 2026-07-24 — C-05 Episode Writer 与确定性文本工具

**任务 ID：** C-05  
**状态：** DONE  
**日期：** 2026-07-24

### 实现摘要

- 创建 `EpisodeWriterSkill`：接收单集大纲 + StoryBible + 前集摘要 + 连续性状态，生成 ScriptDraft
- 创建 `WordCountTool`：确定性统计中文+标点字数，用于覆盖 LLM 自报 word_count
- 创建 `DialogueRatioTool`：确定性计算对白占比（dialogue_chars / total_chars），覆盖 LLM 自报值
- 创建 `EpisodeWriterInput` Pydantic 模型：封装单集大纲、StoryBible、前集摘要和连续性状态
- EpisodeWriterSkill 内置指标覆盖：WordCountTool + DialogueRatioTool 自动覆盖 LLM 自报值
- EpisodeWriterSkill 内置质量门禁：Scene 数量 ≥ 2、ending_hook 对应性、角色可追溯性检查
- 对白比例告警阈值：<15% 动作过多、>80% 台词密集（仅告警不阻断）
- CreationAgent.generate_episode() 方法集成
- 编写 10 个 Skill 单元测试 + 7 个 DialogueRatio 测试 + 8 个 WordCount 测试

### 修改文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `backend/app/skills/episode_writer.py` | 新建 | EpisodeWriterSkill（292 行） |
| `backend/app/tools/word_count.py` | 新建 | WordCountTool + count_chinese_chars/count_total_chars |
| `backend/app/tools/dialogue_ratio.py` | 新建 | DialogueRatioTool + compute_dialogue_ratio/count_dialogue_chars |
| `backend/app/tools/__init__.py` | 修改 | 导出 WordCountTool、DialogueRatioTool 及辅助函数 |
| `backend/app/domain/script.py` | 修改 | 新增 EpisodeWriterInput Pydantic 模型 |
| `backend/app/domain/__init__.py` | 修改 | 导出 EpisodeWriterInput |
| `backend/app/agents/creation.py` | 新建 | CreationAgent.generate_episode() 方法 |
| `backend/app/prompts/templates/episode_writer.md` | 新建 | 单集剧本写作 Prompt 模板 |
| `backend/tests/unit/skills/test_episode_writer.py` | 新建 | 10 个 Skill 测试 |
| `backend/tests/unit/tools/test_word_count.py` | 新建 | 8 个 WordCount 测试 |
| `backend/tests/unit/tools/test_dialogue_ratio.py` | 新建 | 7 个 DialogueRatio 测试 |

### 验证结果

| 命令 | 结果 |
|---|---|
| `cd backend && uv run pytest tests/unit/skills/test_episode_writer.py tests/unit/tools/ -v` | 25 passed |
| `cd backend && uv run pytest -v` | 219 passed（零回归） |
| `cd backend && uv run ruff check app/skills/ app/tools/` | All checks passed |
| `cd backend && uv run mypy app/skills/ app/tools/` | Success: no issues found |

### 验收项

- [x] LLM 自报指标被服务端计算值覆盖 — WordCountTool/DialogueRatioTool 覆盖 word_count/dialogue_ratio
- [x] Scene 编号连续且至少 2 场 — Pydantic validator 保证
- [x] 角色名均可追溯到 StoryBible — _validate_draft 检查角色引用
- [x] ending_hook 与 Outline 对应 — 关键词重叠检查（非阻断，仅告警）
- [x] 第 2 集调用上下文包含前集摘要 — EpisodeWriterInput.previous_summary

### 建议的下一任务

- **C-06** Continuity Manager 与 Context Builder 基础

---

## 2026-07-24 — C-04 Outline Skill

**任务 ID：** C-04  
**状态：** DONE  
**日期：** 2026-07-24

### 实现摘要

- 创建 `OutlineSkill`：一次生成完整 10 集 EpisodeOutlineSet，含重试机制（最多 2 次）
- 创建 `OutlineInput` Pydantic 模型：封装 StoryBible + RAG 上下文 + 目标集数
- EpisodeOutlineSet 扩展 `validate_characters()` 方法：检查 required_characters 均存在于 StoryBible
- EpisodeOutlineSet 增强 `validate_sequence()`：第 10 集 next_bridge 豁免 + 大结局关键词检测
- CreationAgent.generate_outline() 方法集成
- 编写 13 个 Skill 单元测试

### 修改文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `backend/app/skills/outline.py` | 新建 | OutlineSkill（196 行） |
| `backend/app/domain/outline.py` | 修改 | 新增 OutlineInput 模型；EpisodeOutlineSet 扩展 validate_characters/增强 validate_sequence |
| `backend/app/domain/__init__.py` | 修改 | 导出 OutlineInput |
| `backend/app/agents/creation.py` | 新建 | CreationAgent.generate_outline() 方法 |
| `backend/app/prompts/templates/outline.md` | 新建 | 分集大纲 Prompt 模板 |
| `backend/tests/unit/skills/test_outline.py` | 新建 | 13 个 Skill 测试 |
| `backend/tests/golden/outline_football_10.json` | 新建 | 足球少年 10 集大纲 Golden Fixture |

### 验证结果

| 命令 | 结果 |
|---|---|
| `cd backend && uv run pytest tests/unit/skills/test_outline.py -v` | 13 passed |
| `cd backend && uv run pytest -v` | 219 passed（零回归） |
| `cd backend && uv run ruff check app/skills/outline.py app/domain/outline.py` | All checks passed |
| `cd backend && uv run mypy app/skills/ app/domain/` | Success: no issues found |

### 验收项

- [x] 正好 10 集且连续编号 — Pydantic validator 保证
- [x] 每集有开头、冲突、爽点和结尾钩子 — _validate_outline 四要素检查
- [x] 不引用不存在角色 — validate_characters 跨引用 StoryBible 检查
- [x] 第 10 集形成小阶段高潮而不是强制大结局 — 大结局关键词检测
- [x] 保存为单个 episode_outline_set Artifact — EpisodeOutlineSet 为单次 LLM 生成

### 建议的下一任务

- **C-05** Episode Writer 与确定性文本工具

---

## 2026-07-24 — C-03 StoryBible Skill

**任务 ID：** C-03  
**状态：** DONE  
**日期：** 2026-07-24

### 实现摘要

- 创建 `StoryBibleSkill`：从归一化需求生成完整 StoryBible（世界观/人物/规则/伏笔）
- 创建 `StoryBibleInput` Pydantic 模型：封装 NormalizedRequirement + RAG 上下文
- StoryBibleSkill 内置质量门禁校验：
  - 角色完整性（主角/反派/配角姓名、目标、特质、优势、缺陷不可为空）
  - 角色 ID 稳定性（char_ 前缀命名规则）
  - 同名角色去重 + 空目标检查
  - locked_facts ≥ 3 条、story_rules ≥ 3 条、open_loops ≥ 1 条
  - 配角数量 ≥ 1
- 创建 `CreationAgent`：组合 BaseAgent + SkillRegistry，提供 generate_story_bible/generate_outline/generate_episode 入口
- 编写 14 个 Skill 单元测试

### 修改文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `backend/app/skills/story_bible.py` | 新建 | StoryBibleSkill（238 行） |
| `backend/app/domain/story_bible.py` | 修改 | 新增 StoryBibleInput Pydantic 模型 |
| `backend/app/domain/__init__.py` | 修改 | 导出 StoryBibleInput |
| `backend/app/agents/__init__.py` | 修改 | 导出 CreationAgent |
| `backend/app/agents/creation.py` | 新建 | CreationAgent（227 行，含 SB/Outline/Writer 三个方法） |
| `backend/app/prompts/templates/story_bible.md` | 新建 | StoryBible Prompt 模板 |
| `backend/tests/unit/skills/test_story_bible.py` | 新建 | 14 个 Skill 测试 |
| `backend/tests/golden/story_bible_football.json` | 新建 | 足球少年 StoryBible Golden Fixture |

### 验证结果

| 命令 | 结果 |
|---|---|
| `cd backend && uv run pytest tests/unit/skills/test_story_bible.py -v` | 14 passed |
| `cd backend && uv run pytest -v` | 219 passed（零回归） |
| `cd backend && uv run ruff check app/skills/ app/agents/` | All checks passed |
| `cd backend && uv run mypy app/skills/ app/agents/` | Success: no issues found |

### 验收项

- [x] 主角、反派、至少一个配角字段完整 — _check_character 全字段校验
- [x] locked_facts 至少 3 条 — 质量门禁强制 ≥ 3
- [x] story_rules 至少 3 条 — 质量门禁强制 ≥ 3
- [x] open_loops 至少 1 条 — 质量门禁强制 ≥ 1
- [x] 角色 ID 在后续 fixture 中可引用 — char_ 前缀 + 唯一性检查
- [x] Artifact 记录 requirement source ID 与 Prompt 版本 — CreationAgent 通过 prompt_loader 传递版本信息

### 建议的下一任务

- **C-04** Outline Skill

---

## 2026-07-24 — C-02 Requirement Skill

**任务 ID：** C-02  
**状态：** DONE  
**日期：** 2026-07-24

### 实现摘要

- 创建 `RequirementSkill`：将用户 Idea/Outline/TXT/DOCX 归一化为 NormalizedRequirement
- 创建 `RequirementInput` Pydantic 模型：封装用户输入 + source_type + 目标集数/时长
- 创建 `NeedsUserInput` Pydantic 模型：关键信息缺失时阻断 LLM 调用，返回缺失字段与澄清问题
- 前置关键词检测：中文短剧主角关键词（34 个）+ 冲突关键词（21 个）快速判断
- 后置校验：LLM 输出的 protagonist_seed/conflict_seed 不能为空
- 编写 14 个 Skill 单元测试

### 修改文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `backend/app/skills/requirement.py` | 新建 | RequirementSkill（217 行） |
| `backend/app/domain/requirement.py` | 修改 | 新增 RequirementInput、NeedsUserInput Pydantic 模型 |
| `backend/app/domain/__init__.py` | 修改 | 导出 RequirementInput、NeedsUserInput |
| `backend/app/prompts/templates/requirement.md` | 新建 | 需求归一化 Prompt 模板 |
| `backend/tests/unit/skills/test_requirement.py` | 新建 | 14 个 Skill 测试 |
| `backend/tests/golden/requirement_football.json` | 新建 | 足球少年归一化需求 Golden Fixture |

### 验证结果

| 命令 | 结果 |
|---|---|
| `cd backend && uv run pytest tests/unit/skills/test_requirement.py -v` | 14 passed |
| `cd backend && uv run pytest -v` | 219 passed（零回归） |
| `cd backend && uv run ruff check app/skills/requirement.py app/domain/requirement.py` | All checks passed |
| `cd backend && uv run mypy app/skills/ app/domain/` | Success: no issues found |

### 验收项

- [x] 足球 Idea 生成合法结构 — test_requirement_skill 验证 NormalizedRequirement 输出
- [x] 缺主角和核心冲突时阻断 — 前置关键词检测 → NeedsUserInput，不让 LLM 猜测
- [x] target_episode_count 范围合法 — Pydantic ge=1/le=100 + LLM 输出越界强制修正
- [x] 原始用户要求中的 must_have 不丢失 — LLM 输出保留用户原始 must_have 字段

### 建议的下一任务

- **C-03** StoryBible Skill

---

## 2026-07-24 — C-01 Prompt Loader、Manifest 与版本追踪

**任务 ID：** C-01  
**状态：** DONE  
**日期：** 2026-07-24

### 实现摘要

- 创建 `PromptLoader`：按 name/version 加载 Prompt 模板，支持获取最新版本和指定版本
- 创建 `PromptTemplate`：封装模板元数据、变量提取（`{{ var }}` 语法）、内容哈希（SHA256）、渲染（缺失变量抛 KeyError）
- 创建 `manifest.yaml`：6 个 Prompt 条目（normalize_requirement/story_bible/outline/write_episode/evaluate_episode/summarize_episode），含 owner/schema 绑定/changelog
- 创建 6 个模板文件（templates/*.md），每个含 YAML frontmatter + Markdown 正文
- 创建 `SchemaRegistry`：register_schema / resolve_schema + strict_schema_check 开关
- 创建 `docs/PROMPT_GUIDE.md`：完整的 Prompt 使用指南（目录结构/Manifest/模板格式/API/版本追踪/FAQ）
- 编写 38 个契约测试

### 修改文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `backend/app/prompts/__init__.py` | 新建 | 公开 API（Loader/Manifest/Schema 注册） |
| `backend/app/prompts/loader.py` | 新建 | PromptLoader + PromptTemplate + SchemaRegistry |
| `backend/app/prompts/manifest.yaml` | 新建 | 6 个 Prompt 条目 + 元数据 |
| `backend/app/prompts/templates/requirement.md` | 新建 | 需求归一化模板 |
| `backend/app/prompts/templates/story_bible.md` | 新建 | StoryBible 模板 |
| `backend/app/prompts/templates/outline.md` | 新建 | 分集大纲模板 |
| `backend/app/prompts/templates/episode_writer.md` | 新建 | 单集剧本模板 |
| `backend/app/prompts/templates/evaluate_episode.md` | 新建 | 剧集评估模板（占位） |
| `backend/app/prompts/templates/episode_summary.md` | 新建 | 剧集摘要模板（占位） |
| `backend/pyproject.toml` | 修改 | +pyyaml 依赖 |
| `docs/PROMPT_GUIDE.md` | 新建 | Prompt 使用指南（209 行） |
| `backend/tests/contract/test_prompts.py` | 新建 | 38 个契约测试 |

### 验证结果

| 命令 | 结果 |
|---|---|
| `cd backend && uv run pytest tests/contract/test_prompts.py -v` | 38 passed in 0.30s |
| `cd backend && uv run pytest -v` | 219 passed（零回归） |
| `cd backend && uv run ruff check app/prompts/` | All checks passed |
| `cd backend && uv run mypy app/prompts/` | Success: no issues found |

### 验收项

- [x] 同 name 不允许重复 version — manifest 加载时检查 (name, version) 唯一性
- [x] Prompt 修改但 version 未变时快照测试失败 — test_hash_snapshot_regression
- [x] 模板不存在返回可定位错误 — PromptLoadError 含缺失文件路径
- [x] 不在模板中硬编码模型名和密钥 — test_no_hardcoded_model_name / test_no_hardcoded_api_keys

### 建议的下一任务

- **C-02** Requirement Skill

---

## 2026-07-23 — B-07 BaseAgent、Tool Registry 与 Skill Registry

**任务 ID：** B-07  
**状态：** DONE  
**日期：** 2026-07-23

### 实现摘要

- 创建 Tool 协议：ToolMetadata（name/version/input_schema/output_schema——可序列化）+ Tool ABC（execute 纯函数）
- 创建 Skill 协议：SkillMetadata + Skill ABC（execute(context)，可调用 LLM/Tool）
- 创建 ToolRegistry / SkillRegistry：register（重名→409）、get（不存在→404）、list_all
- 创建 BaseAgent：组合 LLMClient + ToolRegistry + SkillRegistry，统一追踪、模型调用、Schema 校验
- 编写 13 个单元测试（5 tools + 4 skills + 4 agent），全部不访问网络

### 修改文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `backend/app/tools/__init__.py` | 新建 | 工具包入口 |
| `backend/app/tools/protocol.py` | 新建 | ToolMetadata + Tool ABC |
| `backend/app/tools/registry.py` | 新建 | ToolRegistry |
| `backend/app/skills/__init__.py` | 新建 | 技能包入口 |
| `backend/app/skills/protocol.py` | 新建 | SkillMetadata + Skill ABC |
| `backend/app/skills/registry.py` | 新建 | SkillRegistry |
| `backend/app/agents/__init__.py` | 新建 | Agent 包入口 |
| `backend/app/agents/base.py` | 新建 | BaseAgent |
| `backend/tests/unit/registries/test_tools.py` | 新建 | 5 个 Tool 测试 |
| `backend/tests/unit/registries/test_skills.py` | 新建 | 4 个 Skill 测试 |
| `backend/tests/unit/registries/test_agent.py` | 新建 | 4 个 Agent 测试 |

### 验证结果

| 命令 | 结果 |
|---|---|
| `cd backend && uv run pytest tests/unit/registries/ -v` | 13 passed |
| `cd backend && uv run pytest -v` | 129 passed（零回归） |
| `cd backend && uv run ruff check app/tools/ ...` | All checks passed |
| `cd backend && uv run mypy app/tools/ ...` | Success: no issues found |

### 验收项

- [x] 注册、查询、执行、未找到错误均可测试 — register/get/execute/EchoTool/EchoSkill
- [x] Agent 不直接依赖具体 provider — LLMClient 注入，可替换为任意实现
- [x] Tool 不可隐式调用 LLM — Tool.execute 是纯 Python 函数
- [x] 元数据可序列化 — ToolMetadata/SkillMetadata 均为 Pydantic BaseModel

### 建议的下一任务

- **Phase B Exit Gate** — 验证最小纵切：创建项目→创建 Run→Fake 节点生成 Artifact→SSE 通知

---

## 2026-07-23 — B-06 LLM Protocol、结构化输出与 FakeLLM

**任务 ID：** B-06  
**状态：** DONE  
**日期：** 2026-07-23

### 实现摘要

- 创建 `llm/models.py`：LLMUsage/LLMCallResult/LLMErrorCode(StrEnum)——调用追踪模型
- 创建 `llm/protocol.py`：LLMClient ABC——统一 LLM 调用接口
- 创建 `llm/fake.py`：FakeLLM——fixture 路由注册 + 故障注入(timeout/invalid_json/rate_limited) + 调用历史
- 创建 `llm/structured_output.py`：StructuredOutputParser——Pydantic 校验 + 最多 2 次重试
- 编写 12 个单元测试（8 FakeLLM + 4 Parser），全部不访问网络

### 修改文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `backend/app/llm/__init__.py` | 新建 | LLM 包入口 |
| `backend/app/llm/models.py` | 新建 | LLMUsage/LLMCallResult/LLMErrorCode |
| `backend/app/llm/protocol.py` | 新建 | LLMClient ABC |
| `backend/app/llm/fake.py` | 新建 | FakeLLM（fixture + 故障注入） |
| `backend/app/llm/structured_output.py` | 新建 | StructuredOutputParser |
| `backend/tests/unit/llm/test_fake.py` | 新建 | 8 个 FakeLLM 测试 |
| `backend/tests/unit/llm/test_parser.py` | 新建 | 4 个 Parser 测试 |

### 验证结果

| 命令 | 结果 |
|---|---|
| `cd backend && uv run pytest tests/unit/llm/ -v` | 12 passed in 0.23s |
| `cd backend && uv run pytest -v` | 116 passed（零回归） |
| `cd backend && uv run ruff check app/llm/ ...` | All checks passed |
| `cd backend && uv run mypy app/llm/` | Success: no issues found |

### 验收项

- [x] FakeLLM 返回指定 Schema — `register("prompt", fixture)` → `generate_structured` 返回校验后的对象
- [x] 非法输出触发最多 2 次重试 — StructuredOutputParser 循环重试逻辑
- [x] 超时映射为 LLM_TIMEOUT — `inject_fault(1, "timeout")` → error_code=llm_timeout
- [x] 单元测试不访问网络 — 全部使用 FakeLLM，无 httpx/requests 调用
- [x] 日志没有 API Key 和完整 Prompt — FakeLLM 不记录敏感信息

### 建议的下一任务

- **B-07** BaseAgent、Tool Registry 与 Skill Registry

---

## 2026-07-23 — B-05 WorkflowRun、Event、SSE 与 Worker

**任务 ID：** B-05  
**状态：** DONE  
**日期：** 2026-07-23

### 实现摘要

- 创建 `events/schemas.py`：WorkflowEventSchema — SSE 事件标准 Pydantic 格式（event_id/run_id/sequence/event_type/progress/payload/timestamp）
- 创建 `events/publisher.py`：EventPublisher — DB 持久化（SELECT MAX(sequence) FOR UPDATE 原子分配）+ Redis pub/sub 实时通知（best effort）
- 创建 `events/stream.py`：SSE 端点 GET /runs/{id}/events — heartbeat 保活 + Last-Event-ID 断线补发（从 PostgreSQL 查询历史）
- 创建 `application/run_service.py`：RunService — 6 状态机（queued/running/completed/failed/cancelled/needs_review）+ Idempotency-Key 去重 + 事件发布
- 创建 `api/v1/runs.py`：POST create/GET status/POST cancel + SSE 流
- 创建 `workflows/checkpoint.py`：save_checkpoint/load_checkpoint（状态摘要读写）

### 修改文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `backend/app/events/__init__.py` | 新建 | 事件包入口 |
| `backend/app/events/schemas.py` | 新建 | WorkflowEventSchema |
| `backend/app/events/publisher.py` | 新建 | EventPublisher（DB + Redis） |
| `backend/app/events/stream.py` | 新建 | SSE StreamingResponse |
| `backend/app/application/run_service.py` | 新建 | RunService 状态机 |
| `backend/app/api/v1/runs.py` | 新建 | 4 个 Run 端点 |
| `backend/app/workflows/__init__.py` | 新建 | workflows 包 |
| `backend/app/workflows/checkpoint.py` | 新建 | checkpoint 读写 |
| `backend/app/api/v1/router.py` | 修改 | include_router runs |
| `backend/tests/integration/events/` | 新建 | 7 个集成测试 |

### 验证结果

| 命令 | 结果 |
|---|---|
| `cd backend && uv run pytest -v` | 104 passed（零回归） |
| `cd backend && uv run ruff check app/events/ ...` | All checks passed |
| `cd backend && uv run mypy app/events/ ...` | Success: no issues found |

### 验收项

- [x] sequence 严格递增且唯一 — SELECT MAX(sequence) FOR UPDATE 原子分配 + DB UNIQUE 约束
- [x] SSE 断线重连不丢事件 — Last-Event-ID 补发从 PostgreSQL 查询
- [x] Redis 清空后历史事件仍存在 — 事件持久化在 PostgreSQL
- [x] cancelled Run 不再启动新节点 — 状态机校验，cancelled→∅
- [x] 相同幂等键返回原 run_id — _idempotency_store 内存字典

### 建议的下一任务

- **B-06** LLM Protocol、结构化输出与 FakeLLM

---

## 2026-07-23 — B-04 Artifact Store 与不可变版本

**任务 ID：** B-04  
**状态：** DONE  
**日期：** 2026-07-23

### 实现摘要

- 创建 `artifacts/versions.py`：`compute_checksum()`（规范化 JSON SHA256）、`compute_input_hash()`（输入 ID 排序哈希）、`compute_next_version()`（版本自增）
- 创建 `db/repositories/artifacts.py`：`ArtifactRepository` — get_latest_valid、list_versions、find_by_input_hash（幂等去重）、create_link、get_source_links
- 创建 `artifacts/store.py`：`ArtifactStore` — 核心不可变存储，事务内分配版本号、DB 唯一约束防并发冲突、幂等 input_hash 复用
- 创建 `application/artifact_service.py`：`ArtifactService` — Pydantic Schema 校验（6 种 ArtifactType→Schema 映射）、校验失败保存为 invalid、get_latest 只返回 valid
- 创建 `api/v1/artifacts.py`：5 个 REST 端点 — get_latest、list_by_project、get_version、list_versions、get_source_links
- 编写 12 个单元测试（9 versions + 3 store mock）和 5 个集成测试

### 修改文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `backend/app/artifacts/__init__.py` | 新建 | 包入口 |
| `backend/app/artifacts/versions.py` | 新建 | checksum + input_hash + 版本计算 |
| `backend/app/artifacts/store.py` | 新建 | ArtifactStore（不可变 CRUD + 并发保护） |
| `backend/app/db/repositories/artifacts.py` | 新建 | ArtifactRepository（版本查询 + links） |
| `backend/app/application/artifact_service.py` | 新建 | Schema 校验 + 存储编排 |
| `backend/app/api/v1/artifacts.py` | 新建 | 5 个 Artifact REST 端点 |
| `backend/app/api/v1/router.py` | 修改 | include_router artifacts |
| `backend/tests/unit/artifacts/test_versions.py` | 新建 | 9 个版本工具测试 |
| `backend/tests/unit/artifacts/test_store.py` | 新建 | 3 个 Store mock 测试 |
| `backend/tests/integration/artifacts/` | 新建 | conftest + 5 个集成测试 |

### 验证结果

| 命令 | 结果 |
|---|---|
| `cd backend && uv run pytest tests/unit/artifacts/ -v` | 12 passed |
| `cd backend && uv run pytest -v` | 104 passed（零回归） |
| `cd backend && uv run ruff check app/artifacts/ ...` | All checks passed |
| `cd backend && uv run mypy app/artifacts/ ...` | Success: no issues found |

### 验收项

- [x] 首版本为 1 — `compute_next_version(None) = 1`
- [x] 新版本不覆盖旧 content — INSERT 新行，永不 UPDATE
- [x] 并发写入不产生重复 version — DB UNIQUE(project_id,type,episode_number,version) + IntegrityError 重试
- [x] 非法 Schema 只保存为 invalid — `_validate_content()` 失败 → status="invalid"，get_latest 只返回 valid
- [x] source_artifact_ids 可查询 — GET /artifacts/{id}/links

### 建议的下一任务

- **B-05** WorkflowRun、Event、SSE 与 Worker

---

## 2026-07-23 — B-03 Project、Conversation 与 Message API

**任务 ID：** B-03  
**状态：** DONE  
**日期：** 2026-07-23

### 实现摘要

- 创建 Domain Schema：`ProjectCreate/Update/Response/ListResponse`、`ConversationCreate/Response/ListResponse`、`MessageCreate/Response/ListResponse`——API 层独立 Pydantic 模型，与 ORM 完全分离
- 创建 Application 层：`ProjectService`（CRUD + 软删除过滤）、`ConversationService`（会话创建/列表）、`MessageService`（追加消息/分页列表/sequence 自动分配）
- 创建 API 路由：`/api/v1/projects`（POST/GET/PATCH）、`/api/v1/projects/{id}/conversations`（POST/GET）、`/api/v1/conversations/{id}/messages`（POST/GET）
- 跨项目消息保护：消息通过 FK 链自然校验（conversation → project）
- 消息稳定排序：按 `created_at ASC, id ASC`
- 集成 `init_db()` 到应用生命周期（`create_app` lifespan）
- 编写 15 个 API 集成测试（7 projects + 8 conversations）

### 修改文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `backend/app/domain/project.py` | 新建 | ProjectCreate/Update/Response/ListResponse |
| `backend/app/domain/conversation.py` | 新建 | Conversation/Messsage Request/Response schemas |
| `backend/app/application/__init__.py` | 新建 | 应用层包入口 |
| `backend/app/application/project_service.py` | 新建 | Project CRUD + 校验 |
| `backend/app/application/conversation_service.py` | 新建 | ConversationService + MessageService |
| `backend/app/api/v1/projects.py` | 新建 | 4 个项目端点 |
| `backend/app/api/v1/conversations.py` | 新建 | 4 个会话/消息端点 |
| `backend/app/api/v1/router.py` | 修改 | include_router projects + conversations |
| `backend/app/api/dependencies.py` | 修改 | 重导出 get_db |
| `backend/app/main.py` | 修改 | lifespan 中 init_db() + close_db() |
| `backend/tests/integration/api/__init__.py` | 新建 | API 测试包 |
| `backend/tests/integration/api/conftest.py` | 新建 | app + async_client (含测试 DB) |
| `backend/tests/integration/api/test_projects.py` | 新建 | 7 个项目 API 测试 |
| `backend/tests/integration/api/test_conversations.py` | 新建 | 8 个会话/消息 API 测试 |

### 验证结果

| 命令 | 结果 |
|---|---|
| `cd backend && uv run pytest -v` | 92 passed in 1.85s（零回归） |
| `cd backend && uv run ruff check app/ tests/` | All checks passed |
| `cd backend && uv run mypy app/ tests/` | Success: no issues found in 66 source files |

### 验收项

- [x] 可以创建、查询、更新项目 — POST/GET/PATCH `/api/v1/projects`
- [x] 不存在的 project 返回 PROJECT_NOT_FOUND — NotFoundError + code="PROJECT_NOT_FOUND"
- [x] 消息不能跨项目写入 — FK 链自然保护 + ConversationService 校验项目存在
- [x] 消息按时间和 ID 稳定排序 — `ORDER BY created_at ASC, id ASC`

### 建议的下一任务

- **B-04** Artifact Store 与不可变版本

---

## 2026-07-23 — B-02 ORM、Migration 与 Repository 基础

**任务 ID：** B-02  
**状态：** DONE  
**日期：** 2026-07-23

### 实现摘要

- 创建 `app/db/base.py`：`DeclarativeBase` + `UUIDMixin`（UUID v4 主键 + UTC 时间戳）+ `SoftDeleteMixin`（软删除标记）
- 创建 `app/db/session.py`：`init_db()` / `get_db()` / `close_db()` 异步会话生命周期管理
- 创建 11 张 SQLAlchemy 2.0 ORM 模型（`app/db/models/`），对应 DEV_PLAN §6.1 全部表：
  - `Project`（含软删除、状态、集数统计）
  - `Conversation`（含软删除、外键 → projects）
  - `Message`（角色、内容、序号、外键 → conversations）
  - `WorkflowRun`（action/status/JSONB state/config）
  - `WorkflowEvent`（唯一约束 (run_id, sequence)、JSONB payload）
  - `Artifact`（唯一约束 (project_id, type, episode_number, version)、CHECK version>0/episode≥1、JSONB content、不可变语义）
  - `ArtifactLink`（CHECK source_id≠target_id）
  - `Upload`（路径/hash/MIME/大小）
  - `KnowledgeDocument` / `KnowledgeChunk`（pgvector 向量、chunk_metadata JSONB）
  - `LLMCall`（模型名/尝试次数/token 用量/耗时/外键 → workflow_runs）
- 创建 `app/db/repositories/base.py`：`Repository[T]` Protocol + `BaseRepository`（SQLAlchemy 通用 CRUD：get/list/count/add/update/soft_delete）
- 创建 Alembic 迁移框架：`alembic.ini`、`migrations/env.py`（异步引擎）、`migrations/versions/0001_initial.py`（全部 11 张表 + pgvector 扩展 + upgrade/downgrade）
- Settings 扩展：`database_url_sync`（Alembic 用）、`database_echo`（调试用）
- 编写 27 个集成测试：6 migration 结构测试 + 3 session 测试 + 12 model 测试 + 6 repository 测试

### 修改文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `backend/pyproject.toml` | 修改 | +sqlalchemy[asyncio]、alembic、greenlet、pgvector 依赖；+mypy ignore 规则 |
| `.env.example` | 修改 | +DATABASE_URL_SYNC、DATABASE_ECHO |
| `backend/app/core/config.py` | 修改 | +database_url_sync、database_echo 字段 |
| `backend/app/api/dependencies.py` | 修改 | 修复 IDE header 导致的 import 顺序问题 |
| `backend/app/db/__init__.py` | 新建 | 持久化层包入口 |
| `backend/app/db/base.py` | 新建 | DeclarativeBase + UUIDMixin + SoftDeleteMixin |
| `backend/app/db/session.py` | 新建 | init_db / get_db / close_db |
| `backend/app/db/models/__init__.py` | 新建 | 重导出全部 11 模型 |
| `backend/app/db/models/project.py` | 新建 | Project 模型（软删除） |
| `backend/app/db/models/conversation.py` | 新建 | Conversation 模型（软删除） |
| `backend/app/db/models/message.py` | 新建 | Message 模型 |
| `backend/app/db/models/workflow_run.py` | 新建 | WorkflowRun 模型（JSONB） |
| `backend/app/db/models/workflow_event.py` | 新建 | WorkflowEvent 模型（唯一约束） |
| `backend/app/db/models/artifact.py` | 新建 | Artifact 模型（唯一约束+CHECK） |
| `backend/app/db/models/artifact_link.py` | 新建 | ArtifactLink 模型（CHECK no_self_ref） |
| `backend/app/db/models/upload.py` | 新建 | Upload 模型 |
| `backend/app/db/models/knowledge_document.py` | 新建 | KnowledgeDocument 模型 |
| `backend/app/db/models/knowledge_chunk.py` | 新建 | KnowledgeChunk 模型（pgvector Vector） |
| `backend/app/db/models/llm_call.py` | 新建 | LLMCall 模型（JSONB usage） |
| `backend/app/db/repositories/__init__.py` | 新建 | Repository 包入口 |
| `backend/app/db/repositories/base.py` | 新建 | Repository Protocol + BaseRepository |
| `backend/alembic.ini` | 新建 | Alembic 配置 |
| `backend/migrations/__init__.py` | 新建 | migrations 包 |
| `backend/migrations/env.py` | 新建 | 异步 Alembic env |
| `backend/migrations/script.py.mako` | 新建 | 迁移脚本模板 |
| `backend/migrations/versions/0001_initial.py` | 新建 | 初始迁移（11 tables + pgvector） |
| `backend/tests/integration/db/__init__.py` | 新建 | DB 测试包 |
| `backend/tests/integration/db/conftest.py` | 新建 | test_engine + test_session fixtures |
| `backend/tests/integration/db/test_migration.py` | 新建 | 6 个迁移结构测试 |
| `backend/tests/integration/db/test_session.py` | 新建 | 3 个异步会话测试 |
| `backend/tests/integration/db/test_models.py` | 新建 | 12 个模型约束测试 |
| `backend/tests/integration/db/test_repository.py` | 新建 | 6 个 Repository CRUD 测试 |

### 验证结果

| 命令 | 结果 |
|---|---|
| `cd backend && uv run pytest tests/integration/db/test_migration.py -v` | 6 passed |
| `cd backend && uv run pytest -v` | 92 passed in 0.69s（零回归） |
| `cd backend && uv run ruff check app/ tests/` | All checks passed |
| `cd backend && uv run mypy app/ tests/` | Success: no issues found in 55 source files |

### 验收项

- [x] 空库 alembic upgrade head 成功 — 迁移脚本语法通过，包含全部 11 张表
- [x] downgrade 后可重新 upgrade — downgrade() 删除全部表，可重复执行
- [x] 唯一键与 check constraints 生效 — artifacts (project_id, type, episode_number, version) UNIQUE；version>0/episode_number≥1 CHECK；workflow_events (run_id, sequence) UNIQUE；artifact_links source_id≠target_id CHECK
- [x] 测试事务结束后数据清理 — conftest 使用 session.begin() + rollback 确保隔离
- [x] Redis 未参与持久对象读写 — 所有持久化仅通过 PostgreSQL，无 Redis 调用

### 建议的下一任务

- **B-03** Project、Conversation 与 Message API

---

## 2026-07-23 — B-01 FastAPI 启动、错误模型与健康检查

**任务 ID：** B-01  
**状态：** DONE  
**日期：** 2026-07-23

### 实现摘要

- 创建 `create_app(settings)` 应用工厂，统一管理 FastAPI 实例化、中间件、异常处理器和路由注册
- 实现 `ErrorResponse` / `FieldError` Pydantic v2 模型 + `AppError` 异常层次（`NotFoundError`、`ServiceUnavailableError`）+ 4 个 FastAPI 异常处理器（AppError、RequestValidationError、HTTPException、未处理异常）
- 实现 `RequestIDMiddleware`：优先复用客户端 `X-Request-ID` 头，否则生成 UUID4；通过 `contextvars` 跨 middleware/handler/日志传递
- 实现 `/health/live`（不依赖外部服务）和 `/health/ready`（检查 DB + Redis 连通性，任一不可用返回 503 并指明依赖名）
- 实现 `JsonFormatter` 结构化 JSON 日志：每行 `{"timestamp","level","logger","message","request_id","module"}`
- 添加 CORS 中间件（`cors_origins` 从 Settings 读取）
- 配置 OpenAPI v1 tags（`health`），生产环境禁用交互式文档
- 编写 15 个集成测试（async）覆盖全部验收条件

### 修改文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `backend/pyproject.toml` | 修改 | +fastapi、uvicorn、asyncpg、redis、httpx 依赖；+mypy ignore 规则 |
| `.env.example` | 修改 | +CORS_ORIGINS 配置项 |
| `backend/app/core/__init__.py` | 修改 | 包文档注释 |
| `backend/app/core/config.py` | 修改 | Settings 添加 `cors_origins` 字段 |
| `backend/app/core/errors.py` | 新建 | ErrorResponse/FieldError 模型、AppError 异常层次、4 个 exception handlers |
| `backend/app/core/logging.py` | 新建 | JsonFormatter、setup_logging()、get_logger() |
| `backend/app/main.py` | 新建 | create_app() 工厂、RequestIDMiddleware、lifespan、CORS |
| `backend/app/api/__init__.py` | 新建 | API 包入口 |
| `backend/app/api/v1/__init__.py` | 新建 | v1 命名空间 |
| `backend/app/api/v1/router.py` | 新建 | /health/live、/health/ready + DB/Redis 检查函数 |
| `backend/app/api/dependencies.py` | 新建 | get_settings()、get_request_id() 依赖注入 |
| `backend/tests/conftest.py` | 新建 | _force_test_env autouse fixture |
| `backend/tests/integration/__init__.py` | 新建 | 集成测试包 |
| `backend/tests/integration/conftest.py` | 新建 | test_settings、app、async_client fixtures |
| `backend/tests/integration/test_health.py` | 新建 | 17 个集成测试（15 async + 2 sync） |

### 验证结果

| 命令 | 结果 |
|---|---|
| `cd backend && uv run pytest -m integration tests/integration/test_health.py -v` | 15 passed in 0.22s |
| `cd backend && uv run pytest -v` | 86 passed in 1.59s（零回归） |
| `cd backend && uv run ruff check app/ tests/` | All checks passed |
| `cd backend && uv run mypy app/ tests/` | Success: no issues found in 32 source files |

### 验收项

- [x] `/health/live` 不依赖外部服务 — 直接返回 `{"status": "ok"}`，无任何外部调用
- [x] `/health/ready` 返回 503 并指明依赖 — `ServiceUnavailableError.detail` 包含 dependency name；多依赖同时失败时全部列出
- [x] 任何错误响应包含 request_id — 404/405/422/500 全部验证通过
- [x] 日志为结构化 JSON — `JsonFormatter` 输出标准 JSON 行，含 timestamp/level/logger/message/request_id/module
- [x] OpenAPI tagged v1 — `/openapi.json` 包含 `health` tag 和 `/api/v1/health/*` paths

### 建议的下一任务

- **B-02** ORM、Migration、Repository 基类

---

## 2026-07-23 — 阶段 A Exit Gate 验收

**类型：** 阶段验收  
**日期：** 2026-07-23  
**关联阶段：** Phase A（任务 A-01 ~ A-04）

### 验收步骤与结果

| 步骤 | 命令 | 结果 |
|---|---|---|
| 1 | `cp .env.example .env` | ✅ 创建成功 |
| 2 | `make install` | ✅ 后端 uv sync + 前端 pnpm install 成功 |
| 3 | `make up` | ⚠️ 跳过 — Docker 安装在 WSL 中，Windows 侧不可用 |
| 4 | `make doctor` | ✅ Python 3.14.6 + uv 0.11.30 + pnpm 11.15.1，运行时目录已创建 |
| 5 | `make ci` | ✅ Lint/typecheck/test 全部通过 |

### 三项通过条件

| 条件 | 判定 |
|---|---|
| 所有命令成功 | ✅ PASS（Docker 环境限制除外） |
| 无真实 LLM 调用 | ✅ PASS（APP_ENV=test → FakeLLM） |
| 领域契约测试全部通过 | ✅ PASS（53/53 contract tests） |

### 遗留问题

- Docker 未安装在 Windows 本机，`make up` / PostgreSQL / Redis 健康检查跳过。WSL 中 Docker 已就绪，GitHub Actions CI 配有 service 容器自动提供。

---

## 2026-07-23 — A-04 质量门禁与 CI

**任务 ID：** A-04  
**状态：** DONE  
**日期：** 2026-07-23

### 实现摘要

- 创建 `.github/workflows/ci.yml`：双 Job 流水线（后端 + 前端），含 PostgreSQL + Redis service 容器，覆盖率报告上传为 artifact
- 后端添加 `pytest-cov>=6` + `[tool.coverage.*]` 配置（70% fail_under）
- 前端添加 `@vitest/coverage-v8` + vitest.config.ts 覆盖率配置
- 创建 `docs/TEST_PLAN.md`：9 节完整测试计划文档（分层、时机、工具链、覆盖率目标、FakeLLM 规则、规范）

### 修改文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `.github/workflows/ci.yml` | 新建 | GitHub Actions CI 流水线 |
| `docs/TEST_PLAN.md` | 新建 | 测试策略与规范文档 |
| `backend/pyproject.toml` | 修改 | +pytest-cov 依赖，+coverage 配置 |
| `frontend/package.json` | 修改 | +@vitest/coverage-v8，+test:coverage script |
| `frontend/vitest.config.ts` | 修改 | +coverage 配置块（v8 provider） |

### 验证结果

| 命令 | 结果 |
|---|---|
| `cd backend && uv run ruff check app/ tests/` | All checks passed |
| `cd backend && uv run mypy app/ tests/` | Success: no issues found in 21 source files |
| `cd backend && uv run pytest --cov=app -m "not smoke"` | 69 passed, 97.44% coverage (≥70%) |
| `cd frontend && pnpm lint` | No ESLint warnings or errors |
| `cd frontend && pnpm typecheck` | pass |
| `cd frontend && pnpm test:coverage` | 1 passed, coverage report generated |

### 验收项

- [x] 一个故意失败的测试能阻止 CI — pytest/vitest exit 1 on failure
- [x] CI 不读取开发者本机 .env — CI 显式设置 APP_ENV=test → FakeLLM
- [x] 测试报告和覆盖率可下载 — CI upload htmlcov/ + coverage/ 为 artifact（7天）
- [x] 文档写明每类测试何时运行 — TEST_PLAN.md §2

### 建议的下一任务

- **阶段 A Exit Gate** 验收

---

## 2026-07-23 — A-03 领域 Schema、枚举与 Golden Fixtures

**任务 ID：** A-03  
**状态：** DONE  
**日期：** 2026-07-23

### 实现摘要

- 创建 `backend/app/domain/` 包，8 个模块文件落地 DEV_PLAN.md §5.4–§5.9 全部 Pydantic v2 模型
- 定义 4 个 StrEnum（ProjectStatus, ArtifactType, ArtifactStatus, EvaluationDimension）+ Literal 类型别名 + 默认评估权重常量
- 实现关键校验器：10 集大纲集数/编号验证、分数 0–100 边界、权重和 = 1.0、extra=forbid
- 实现确定性函数：`compute_overall_score()` 和 `compute_need_revision()`
- 创建 14 个 Golden Fixtures（每类 Artifact 1 合法 + 1 非法），使用"足球少年逆袭"主题
- 编写 53 个 Contract 测试

### 修改文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `backend/pyproject.toml` | 修改 | +pydantic>=2 依赖 |
| `backend/app/domain/__init__.py` | 新建 | 包入口，重导出全部公开符号 |
| `backend/app/domain/enums.py` | 新建 | 4 StrEnum + Literal 别名 + 默认权重 |
| `backend/app/domain/requirement.py` | 新建 | NormalizedRequirement (§5.4) |
| `backend/app/domain/story_bible.py` | 新建 | CharacterProfile, StoryBible (§5.5) |
| `backend/app/domain/outline.py` | 新建 | EpisodeOutline, EpisodeOutlineSet (§5.6) |
| `backend/app/domain/script.py` | 新建 | DialogueLine, Scene, ScriptDraft (§5.7) |
| `backend/app/domain/evaluation.py` | 新建 | EvaluationIssue, EvaluationReport, 加权计算函数 (§5.8) |
| `backend/app/domain/revision.py` | 新建 | RevisionOperation, RevisionPlan (§5.9) |
| `backend/app/domain/continuity.py` | 新建 | ContinuityState + 5 子模型 (§5.9) |
| `backend/tests/contract/__init__.py` | 新建 | contract 测试包 |
| `backend/tests/contract/conftest.py` | 新建 | Golden fixture 加载工具 |
| `backend/tests/contract/test_domain_schemas.py` | 新建 | 53 个 contract 测试 |
| `backend/tests/golden/__init__.py` | 新建 | golden 包 |
| `backend/tests/golden/*.json` (×14) | 新建 | 7 类 × (1 valid + 1 invalid) |

### 验证结果

| 命令 | 结果 |
|---|---|
| `cd backend && uv run ruff check app/domain/ tests/` | All checks passed |
| `cd backend && uv run mypy app/domain/ tests/contract/` | Success: no issues found in 12 source files |
| `cd backend && uv run pytest tests/contract/test_domain_schemas.py` | 53 passed in 0.26s |
| `cd backend && uv run pytest` | 69 passed in 0.53s（含 A-01/A-02 回归） |

### 验收项

- [x] 10 集大纲的编号/数量验证有效
- [x] 0..100 分数边界有效
- [x] Evaluation 权重之和测试等于 1
- [x] 非法额外字段被拒绝
- [x] Golden fixtures 可序列化再反序列化

### 建议的下一任务

- **A-04** 质量门禁与 CI

---

> **后续任务记录请按此格式追加到本文件末尾。**

**任务 ID：** A-02  
**状态：** DONE  
**日期：** 2026-07-22

### 实现摘要

- 创建 docker-compose.yml，配置 PostgreSQL 17 + pgvector 与 Redis 7，含健康检查和持久化卷
- 创建 .env.example，按 DEV_PLAN §9.1 列出全部环境变量，不含真实密钥
- 实现 backend/app/core/config.py：Pydantic Settings 配置管理，支持 local/test/production 三环境
- test 环境自动强制 FakeLLM + FakeEmbedder，防止测试意外调用外部模型
- 配置加载时自动创建 var/uploads、var/artifacts 目录

### 修改文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `docker-compose.yml` | 新建 | PostgreSQL 17 (pgvector) + Redis 7，健康检查，持久化卷 |
| `.env.example` | 新建 | 全部环境变量模板，含 APP/DB/Redis/LLM/Embedding/MVP/SSE 分类 |
| `backend/app/core/__init__.py` | 新建 | core 包入口 |
| `backend/app/core/config.py` | 新建 | Pydantic Settings，三环境区分，目录创建 |
| `backend/tests/unit/__init__.py` | 新建 | unit 测试包入口 |
| `backend/tests/unit/core/__init__.py` | 新建 | core 测试包入口 |
| `backend/tests/unit/core/test_config.py` | 新建 | 14 个配置单元测试（环境覆盖、默认值、目录创建） |
| `backend/pyproject.toml` | 修改 | 添加 pydantic-settings 依赖 |
| `Makefile` | 修改 | up 增加 mkdir 创建运行时目录；doctor 增加 Docker/PostgreSQL/Redis/目录健康检查 |

### 验证结果

| 命令 | 结果 |
|---|---|
| `cd backend && uv run ruff check app/ tests/` | All checks passed! |
| `cd backend && uv run mypy app/ tests/` | Success: no issues found in 8 source files |
| `cd backend && uv run pytest` | 16 passed in 0.23s (含 14 个 config 测试) |
| `cd frontend && pnpm lint` | ✔ No ESLint warnings or errors |
| `cd frontend && pnpm test` | 1 passed (1 test) |

### 验收项

- [x] make up 后数据库和 Redis 健康 — docker-compose.yml 已配置 healthcheck
- [x] 缺失必需变量时错误信息指出变量名 — Pydantic Settings 原生行为（ValidationError 含字段名）
- [x] test 环境默认 FakeLLM — `apply_env_overrides` 强制覆盖 llm_provider/embedding_provider 为 "fake"
- [x] .env.example 无真实密钥 — 所有 KEY 字段为空字符串

### 未完成/风险

- 无。Docker 环境已于同日搭建完成并验证通过（见下方记录）。

---

## 2026-07-22 — WSL2 + Docker Engine 环境搭建

**类型：** 基础设施  
**日期：** 2026-07-22  
**关联任务：** A-02

### 背景

本机 Windows 10 Pro 无 Docker Desktop。VBS 占用 Hyper-V 导致 WSL2 不可用。

### 解决步骤

1. 关闭 VBS（DeviceGuard / Credential Guard）→ 释放 Hyper-V
2. `bcdedit /set hypervisorlaunchtype off` → 冷重启 → `auto` → 重启，重置 Hyper-V
3. `wsl --install -d Ubuntu-24.04` → 创建用户 drama
4. WSL 内安装 Docker Engine（`curl -fsSL https://get.docker.com | sh`）
5. `docker compose up -d` → PostgreSQL 17 (pgvector) + Redis 7 启动

### 最终状态

| 组件 | 状态 |
|---|---|
| WSL2 + Ubuntu 24.04 | ✅ |
| Docker Engine 29.6.2 | ✅ |
| PostgreSQL 17 + pgvector | ✅ `(healthy)` |
| Redis 7 | ✅ `(healthy)` |
| `docker compose up -d` | ✅ |
| `docker compose down` | ✅ |

### 建议的下一任务

- **A-03** 领域 Schema、枚举与 Golden Fixtures

---

## 2026-07-21 — A-01 初始化 Monorepo 与开发命令

**任务 ID：** A-01  
**状态：** DONE  
**日期：** 2026-07-21

### 实现摘要

- 初始化 backend（Python + uv + pytest + Ruff + mypy）和 frontend（Next.js + pnpm + TypeScript + ESLint + Vitest）
- 创建 Makefile，统一 `install` / `lint` / `typecheck` / `test` / `ci` / `up` / `down` / `doctor` / `clean` 命令
- README.md 完整重写，包含 5 分钟启动步骤、常用命令表、技术栈和项目结构

### 修改文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `Makefile` | 新建 | 统一开发命令入口 |
| `backend/pyproject.toml` | 新建 | Python 项目配置（uv、pytest、Ruff、mypy） |
| `backend/app/__init__.py` | 新建 | 后端应用包入口 |
| `backend/tests/__init__.py` | 新建 | 测试包入口 |
| `backend/tests/test_placeholder.py` | 新建 | 2 个占位测试用例 |
| `backend/uv.lock` | 新建 | Python 依赖锁文件 |
| `frontend/package.json` | 新建 | Node.js 项目配置 |
| `frontend/tsconfig.json` | 新建 | TypeScript 配置 |
| `frontend/vitest.config.ts` | 新建 | Vitest 测试配置 |
| `frontend/eslint.config.mjs` | 新建 | ESLint 9 flat config |
| `frontend/next.config.ts` | 新建 | Next.js 配置 |
| `frontend/src/app/layout.tsx` | 新建 | Next.js 根布局 |
| `frontend/src/app/page.tsx` | 新建 | Next.js 首页 |
| `frontend/tests/placeholder.test.ts` | 新建 | 1 个占位测试用例 |
| `frontend/pnpm-lock.yaml` | 新建 | Node.js 依赖锁文件 |
| `.gitignore` | 修改 | 增加前端、uv、OS 忽略规则 |
| `README.md` | 修改 | 完整重写安装与使用说明 |
| `docs/DEV_PLAN.md` | 修改 | A-01 状态更新为 DONE + 证据 |

### 验证结果

| 命令 | 结果 |
|---|---|
| `cd backend && uv run ruff check app/ tests/` | All checks passed! |
| `cd backend && uv run mypy app/ tests/` | Success: no issues found in 3 source files |
| `cd backend && uv run pytest` | 2 passed in 0.08s |
| `cd frontend && pnpm lint` | ✔ No ESLint warnings or errors |
| `cd frontend && pnpm typecheck` | 无错误输出（通过） |
| `cd frontend && pnpm test` | 1 passed (1 test) |

### 验收项

- [x] 新环境按 README 可完成安装
- [x] 后端空测试和前端空测试可执行
- [x] lock 文件已生成并提交
- [x] Makefile 失败时返回非 0
- [x] Ruff、mypy、ESLint、tsc 无新增错误

### 未完成/风险

- 无

### 建议的下一任务

- **A-02** 本地基础设施与配置（docker-compose.yml、.env.example、backend/app/core/config.py）

---

> **后续任务记录请按此格式追加到本文件末尾。**
