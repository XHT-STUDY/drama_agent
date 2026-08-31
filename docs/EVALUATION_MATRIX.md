# 剧本评估可解释性升级：证据锚定的评分矩阵（Rubric v2）

> 状态：Phase 1 + Phase 2 已落地（2026-08-30）；Phase 3（客观信号对照）未做。
> 关联：DEV_LOG.md 当日条目；Rubric 权威数据 `knowledge/rubric/mvp_v2.yaml`。

## 1. 背景与问题

升级前的评估是"LLM 给每个维度一个 0-100 的数 + 问题列表"，用户看了难以信服，根因是三个断层：

| 断层 | 表现 |
| --- | --- |
| 标准断层 | 每维度只有 1/3/5 三档、一句话锚点；0-100 连续打分与三档锚点之间没有映射，无法回答"72 和 68 差在哪" |
| 证据断层 | evidence 只存在于"问题"（issues）里，每个维度的分数本身没有评分依据，没有"为什么是 72 不是 85"的解释 |
| 推导断层 | 总分加权计算、修订判定三条规则全是确定性规则，但前端不展示推导过程；客观结构特征只喂给 LLM，用户看不到 |

另一个软肋：evidence 是 LLM 自由转述，用户无法核对"这段话真的在剧本里吗"。

## 2. 方案：三层结构

把每次评估从"LLM 给一个数"变成三件事：**在公开的档位矩阵上定位 + 引用可溯源的原文证据 + 服务端一致性校验**。

### 2.1 标准层 — Rubric v2（`knowledge/rubric/mvp_v2.yaml`，version 2.0.0）

- 锚点从 3 档扩为 **5 档**，每档独立中文描述；
- `score_bands` 定义档位 → 分数区间映射（写在 yaml 里作为数据，服务端读取）：

  | 档位 | 分带 | 含义 |
  | --- | --- | --- |
  | 1 | 0-44 | 明显不达标 |
  | 2 | 45-59 | 低于预期 |
  | 3 | 60-74 | 基本可用 |
  | 4 | 75-89 | 表现良好（75 分即修订阈值，与 4 档下界对齐） |
  | 5 | 90-100 | 出色 |

- 每维度新增 `signals`（可观察信号）：3 条剧本中可核对的具体事实（如"危机/冲突/悬念事件出现在第几场"），随锚点一起注入 Prompt；
- 权重不变（与 `DEFAULT_EVALUATION_WEIGHTS` 保持一致，`ensure_weights_match_enums` 继续把关）；
- `mvp_v1.yaml` 保留为历史数据，`load_rubric()` 默认路径切到 v2。

### 2.2 评分协议层 — 每维度"先定位档位，再给档内分数"

Prompt v1.2 + `EvaluationReport` 新增可选字段 `dimension_assessments`：

```jsonc
"opening_hook": {
  "level": 4,                    // 定位档位 1-5
  "matched_anchor": "…",         // 命中档位描述（服务端按 Rubric 回填，不采信模型转述）
  "rationale": "…为什么不是上一档/下一档…",
  "evidence": [
    { "scene_number": 1, "quote": "剧本原文逐字摘抄≤200字", "verified": true }
  ],
  "signals": { "hook_position": "第1场即危机" }
}
```

### 2.3 服务端校验（机器保证，不靠 LLM 自觉）

`EvaluationSkill._normalize_assessments`（在总分计算**之前**执行）：

1. **分带 clamp**：维度分必须落在所定位档位的分带内，越界拉回（以档位为准——档位是语义判断，分数是数值表达，冲突时语义赢）。clamp 后的维度分才进入 `compute_overall_score`；
2. **matched_anchor 权威回填**：以 Rubric 原文为准；
3. **evidence 溯源校验**（软校验，不阻断）：
   - 引用与所引场次原文归一化匹配（去空白/标点、转小写）→ `verified=true`；
   - 所引场次匹配失败但能在其他场找到 → 自动纠正场次号并记日志；
   - 全文都找不到 → `verified=false`，前端呈现琥珀色"未验证引用"；
   - `scene_number=null` 的全集性证据与全文匹配；
4. 旧版报告（无 `dimension_assessments`，含所有存量 Artifact 与旧 fixture）跳过全部矩阵校验，行为不变——Artifact 不可变规则不受影响。

### 2.4 展示层 — `EvaluationMatrix` 组件

- 9 维 × 5 档矩阵：每行维度名 + 权重 + 5 格档位条（当前档高亮）+ 分数；
- 点击展开：命中档描述与分带、定位理由、信号 chips、证据卡片（✓已溯源 / ⚠未验证，点击跳转场次，复用 `onLocateScene`）、加权贡献（`score × weight`）；
- 总分推导条：分段宽度 = 各维 `score×weight`，`need_revision` 触发规则逐条标出（用户可自行验算）；
- `EvaluationPanel` 有明细时渲染矩阵，旧报告降级为原 ScoreBar；
- Markdown 导出（`markdownFromEvaluation`）新增"评分矩阵（档位定位与证据）"一节。

## 3. 为什么这样能"让人信服"

1. **证据可溯源**——每条引用服务端验证过、一键跳回原文；验证失败如实标注，不伪装。
2. **先定位档位再给分**——压缩随意性，"为什么是 72"变成"为什么是 L3 偏上"，可用相邻档对比回答（rationale 强制要求）。
3. **确定性与主观性分离**——总分、修订判定、分带 clamp 全是公开规则，主观部分被证据包围。

## 4. 修改文件清单

### Phase 1 后端

| 文件 | 操作 | 说明 |
| --- | --- | --- |
| `knowledge/rubric/mvp_v2.yaml` | 新建 | 5 档锚点 + 分带 + 信号（v2.0.0） |
| `backend/app/domain/rubric.py` | 修改 | `ANCHOR_LEVELS=(1..5)`、`signals` 字段、`score_bands` 解析与无缝覆盖校验、`score_band()` 查询、`anchors_text()` 注入分带与信号 |
| `backend/app/domain/evaluation.py` | 修改 | `EvidenceCite` / `DimensionAssessment` 模型、`dimension_assessments` 可选字段（非空时必须覆盖 9 维）、`clamp_score_to_band()` |
| `backend/app/skills/evaluator.py` | 修改 | `_normalize_assessments`（clamp/锚点回填/溯源三分支），执行顺序调整到总分计算之前 |
| `backend/app/prompts/templates/evaluate_episode.md` | 修改 | v1.2：输出评估明细 + 分带映射与信号注入 |
| `backend/app/prompts/manifest.yaml` | 修改 | evaluate_episode → 1.2.0 |
| `backend/tests/golden/evaluation_report_v2_valid.json` | 新建 | 含评估明细的 LLM fixture（引用取自剧本真实原文，含跨场漂移/无法溯源/分带越界三个分支样本） |
| `backend/tests/unit/evaluation/test_dimension_assessment.py` | 新建 | 25 个用例：Rubric v2 结构、clamp、锚点回填、溯源三分支、总分一致性、向后兼容 |
| `backend/tests/unit/evaluation/test_rubric.py` | 修改 | 适配 5 档结构 |
| `backend/tests/contract/test_prompts.py` | 修改 | 哈希快照 key → evaluate_episode:1.2.0 |
| `backend/tests/integration/api/test_evaluations.py` | 修改 | prompt_version 断言 → 1.2.0 |

### Phase 2 前端

| 文件 | 操作 | 说明 |
| --- | --- | --- |
| `frontend/src/types/api.ts` | 修改 | `DimensionAssessment` / `EvaluationEvidenceCite` 类型、`EVAL_LEVEL_BANDS` / `EVAL_LEVEL_LABELS` / `EVAL_SCORE_RULES` 常量、修正 `DEFAULT_EVALUATION_WEIGHTS` 与后端不一致的问题 |
| `frontend/src/features/evaluations/EvaluationMatrix.tsx` | 新建 | 评分矩阵组件 |
| `frontend/src/features/evaluations/EvaluationPanel.tsx` | 修改 | 矩阵集成 + 旧报告降级 |
| `frontend/src/lib/export.ts` | 修改 | Markdown 导出矩阵一节 |
| `frontend/tests/script-evaluation.test.tsx` | 修改 | 新增 7 个矩阵用例（渲染/展开/溯源标记/跳转/触发规则/降级） |

## 5. 验证结果

| 命令 | 结果 |
| --- | --- |
| `uv run pytest tests/unit/evaluation/ tests/unit/skills/test_evaluator.py tests/contract/…` | 全绿（128 例） |
| `uv run pytest`（后端全量） | 978 passed / 0 failed |
| `pnpm test`（前端全量） | 15 files / 219 passed |
| `npx tsc --noEmit` | 通过 |
| `ruff check app tests` | 通过 |
| mypy（本次改动文件） | 通过（`make typecheck` 存在 11 个**既有**错误，位于本次未触碰的 `tests/unit/tools/test_script_render.py` 与前会话遗留的 `tests/integration/api/test_agent_shortcut.py`，非本次引入） |

## 6. 已知限制与后续方向

1. **前端仅展示"命中档"文本**：5 档完整梯子文本未在前端常量化（9 维 × 5 档重复维护成本高），矩阵单元悬停查看相邻档全文需要先提供 Rubric 下发 API（`GET /rubric`）——暂缓。
2. **Phase 3 客观信号对照未做**：ScriptStructureTool 扩展自动信号计算（结构性信号如钩子位置可用，语义类如"数爽点"用关键词启发式不可靠），且定位为"参考对照"而非"验证"。
3. **溯源率需实测**：引用归一化匹配对真实 LLM 的命中率（设计假设 ≥80%）需在真实 LLM smoke 时统计；若偏低，退路是降级为场次级证据（只锚定场次号，跳转让用户自己看原文），机制已内建（`verified=false` 路径）。
4. 分带边界值（44/59/74/89）是设计示意值，调整只需改 `mvp_v2.yaml` 并升级 `rubric_version`，服务端自动跟随。
