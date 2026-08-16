# DramaAgent 知识库（RAG Corpus）

内部短剧创作知识库（Phase D）。StoryBible / Outline / EpisodeWriter 三个创作节点按任务检索相关知识片段，而不是把全部资料塞进 Prompt。

> rubric 分类是特例：由 [rubric/mvp_v1.yaml](rubric/mvp_v1.yaml)（E-01）单独承载，不按本文档的知识文档格式建模。

## 目录结构

```
knowledge/
├── README.md             本说明
├── VERSION               语料版本号（corpus_version）
├── rubric/               评分标准（E-01，特殊资产）
├── templates/            题材模板（genre_template）
├── hooks/                开头/结尾钩子（opening_hook / ending_hook）
├── examples/             爽点与人物原型片段（payoff / character_archetype）
└── compliance/           合规规则（compliance）
```

## 知识分类（KnowledgeCategory）

| category | 含义 | 适用阶段 | 目录 |
| --- | --- | --- | --- |
| genre_template | 题材模板：类型化叙事结构 | story_bible / outline | templates/ |
| opening_hook | 开头钩子技法 | outline / writer | hooks/ |
| ending_hook | 结尾钩子技法 | outline / writer | hooks/ |
| payoff | 爽点设计 | writer | examples/ |
| character_archetype | 人物原型 | story_bible / writer | examples/ |
| compliance | 合规规则 | 全程 | compliance/ |
| rubric | 评估标准 | evaluation | rubric/（特殊资产） |

## 元数据约定（每篇文档 frontmatter）

所有知识文档以 YAML frontmatter 开头，字段如下（`extra=forbid`，多填会校验失败）：

```yaml
---
category: genre_template      # 必填：知识分类（与上表一致）
title: 战神逆袭题材模板        # 必填：文档标题
source: drama-agent-self-auth  # 必填：内容来源（合规）
license: MIT                  # 必填：授权许可证（合规）
language: zh                  # 选填：语言代码，默认 zh
genre: 都市                   # 选填：题材
stage: story_bible            # 选填：适用创作阶段
tags: [战神, 逆袭]             # 选填：检索标签
version: "1.0.0"              # 选填：文档版本，默认 1.0.0
---
```

## 语料版本（corpus_version）

- 语料整体版本号写入 `VERSION` 文件（当前：`mvp_v1`）。
- 每次摄取会把 `corpus_version` 写入 `knowledge_documents.corpus_version`。
- 检索时 `corpus_version` 写入 `RetrievalTrace`，保证 Artifact 可追溯检索依据。

## 合规要求（内容治理）

- 只纳入有授权使用的内容；每篇必须填写 `source` 与 `license`。
- **测试资料不包含完整商业剧本**：每篇为可测试短片段（正文上限约 3000 字），禁止粘贴受版权保护的完整剧本。
- 本仓库语料全部为自建原创内容（source 均标注 `drama-agent-self-auth`）。

## 摄取与校验

```bash
cd backend
uv run python -m app.cli.knowledge ingest ../knowledge   # 幂等摄取（重复跳过）
uv run python -m app.cli.knowledge status                 # 查看文档/块计数与语料版本
```
