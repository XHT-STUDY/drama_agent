# DramaAgent 问题排查记录

按时间倒序记录开发过程中遇到的问题及其排查过程。每条记录包含**症状**、**产生原因**、**解决方案**、**学习收获**四部分 —— 其中「学习收获」记录本次排查应该沉淀的经验，方便日后避免重蹈覆辙。

> 模板：
> ```markdown
> ## YYYY-MM-DD — 问题简述
>
> **症状**：
>
> **产生原因**：
>
> **解决方案**：
>
> **学习收获**：（我应该学习到什么）
> ```

---

## 2026-07-26 — 角色校验白名单阻断工作流

**症状**：Episode Writer 在 `_validate_draft()` 中对 LLM 生成的未知角色名抛 `EpisodeWriterValidationError`，导致整个工作流被阻断。

**分析**：白名单机制（只允许 StoryBible 中已注册的角色名）永远追不上 LLM 的开放域输出。LLM 在实际写作中会创造临时配角、群演等不在设定中的角色，这是正常创作行为，不应阻断。

**处理**：将角色校验从阻断改为信息日志（warning），允许 LLM 创建非白名单角色，同时保留日志用于人工审核。

修改文件：[episode_writer.py](backend/app/skills/episode_writer.py)

**学习收获**：对 LLM 的开放域输出，白名单 / 黑名单这类"封闭集合校验"会持续误伤——生成侧的不确定性应靠"警告 + 人工审核"而不是"硬阻断"；硬校验只应施加在确定性数据上。

---

## 2026-07-26 — 集数控制硬编码导致无法自定义集数

**症状**：无论前端传入多少 outline_count / script_count，后端始终生成 10 集大纲和 3 集剧本。前端集数选择器不生效。

**分析**：
- [outline.py:57](backend/app/workflows/nodes/outline.py) 硬编码 `outline_count=10`
- [write_episode.py:24](backend/app/workflows/nodes/write_episode.py) 硬编码 `_MVP_SCRIPT_COUNT=3`
- 前端 ChatInput 未发送 `outline_count` / `script_count`

**处理**：
- outline.py: `outline_count=10` → `ctx.get("outline_count", 10)`
- write_episode.py: `_MVP_SCRIPT_COUNT` → `ctx.get("script_count")`
- runs.py: workflow_config 传入 `script_count` 和 `outline_count`
- ChatInput.tsx: 新增集数下拉选择器（1/2/3/5/10）

修改文件：[outline.py](backend/app/workflows/nodes/outline.py)、[write_episode.py](backend/app/workflows/nodes/write_episode.py)、[runs.py](backend/app/api/v1/runs.py)、[ChatInput.tsx](frontend/src/features/conversation/ChatInput.tsx)

**学习收获**："看起来是常量"的数量常会成为业务参数，硬编码魔法数字会让前端配置形同虚设。开发时先问"这个值该不该由调用方控制"，再决定用常量还是从 config 读取。

---

## 2026-07-26 — 前端进度条永远显示"等待工作流启动"

**症状**：前端 SSE 连接正常，事件也收到了，但进度条一直卡在 0%，节点状态全部 pending。看起来像是没收到任何 node.started 事件。

**分析**：
- 后端 SSE 发送的事件字段叫 `event_type`
- 前端接口定义的类型字段叫 `type`
- 所有事件判断如 `ev.type === "node.started"` 永远为 `false`
- 这是一个前后端字段名不匹配的经典问题

**处理**：
- [use-run-events.ts](frontend/src/hooks/use-run-events.ts): `RunEvent.type` → `event_type`（6处），同时 fetch+ReadableStream → 浏览器原生 EventSource
- [api.ts](frontend/src/types/api.ts): `WorkflowEvent.type` → `event_type`，补充 `stage`/`progress`/`message`/`artifact_id` 字段
- [RunProgress.tsx](frontend/src/features/runs/RunProgress.tsx): 增加调试信息（连接状态 + 事件计数）
- `tests/setup.ts`: 新增 `EventSource` mock

**学习收获**：前后端共享的类型定义（TS 类型 / OpenAPI）是防止字段名漂移的第一道防线；重命名字段时要用 grep 全量排查，不能只改调用点。

---

## 2026-07-26 — SSE 新连接看不到已有事件

**症状**：前端刷新页面后，SSE 全新连接永远收不到任何历史事件，直到工作流产生新事件。看起来像是历史回放根本没执行。

**分析**：
- `_event_generator` Phase 1（历史回放）只在 `last_event_id` 非空时才执行
- 全新连接时 `last_event_id` 为空，Phase 1 完全跳过
- EventSource 连接后的第一个事件是 `: connected` 注释行，但没有历史事件

**处理**：
- [stream.py](backend/app/events/stream.py): Phase 1 始终执行，不再检查 `last_event_id` 是否为空
- 新增 `_db_poller()` 作为 Redis Pub/Sub 的数据库回退
- 开头 `yield ": connected\n\n"` 确保 EventSource 立即建立连接

**学习收获**：流式 / 重连类功能要单独验证"全新连接"与"断线重连"两条路径，不能只测"长连接正常"这一个场景。

---

## 2026-07-26 — 事件对 SSE 不可见（事务隔离问题）

**症状**：SSE 连接正常，但所有 WorkflowEvent 都等到整个工作流完成后才一次性出现在 SSE 流中。进度无法实时展示。

**分析**：
- `EventPublisher.publish()` 只在 Worker 的事务内 flush
- 事件只在 Worker 事务内可见，SSE 连接的独立事务无法读取
- 只有整个 Workflow 完成后事务提交，全部事件才对外可见

**处理**：
- [publisher.py](backend/app/events/publisher.py): 新增 `autocommit=True` 参数，commit + re-begin 使事件立即对 SSE 可见
- 测试环境仅 flush 不 commit，避免破坏测试事务隔离
- 全部 workflow nodes（6 文件 23 处）+ runs.py（4 处）加 `autocommit=True`

**学习收获**：同一事务内的写入对其他事务不可见——SSE / 异步消费者需要主动 commit 才能让事件"实时"可见。遇到"延迟才可见"的问题，优先怀疑事务边界，而不是网络。

---

## 2026-07-25 — OpenAI API Base URL 重复拼接

**症状**：调用真实 LLM 时报 HTTP 404，实际请求 URL 变成了 `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`（末尾多了 `/v1/chat/completions`）。

**分析**：
- 阿里云 MAAS 的 base URL 已经包含 `/compatible-mode/v1`
- OpenAI Python SDK 默认会在 base URL 后追加 `/v1/chat/completions`
- 导致 URL 变为 `.../compatible-mode/v1/v1/chat/completions`

**处理**：HTTP 404 不应被映射为 `INVALID_OUTPUT` 导致无意义重试。修复错误映射：404 → 模型/端点不存在，区别于 Schema 校验失败。

修改文件：[openai_compatible.py](backend/app/llm/openai_compatible.py)

**学习收获**：对接外部 SDK 时，先确认 base URL 与 SDK 自动追加路径的拼接规则；HTTP 404 应区分"端点不存在"与"输出非法"，避免把 404 映射成校验失败而触发无意义重试。

---

## 2026-07-25 — .env 文件加载失败

**症状**：真实 LLM 调用时所有环境变量为空字符串，LLM_API_BASE、LLM_API_KEY 等全部未加载。

**分析**（多层排查）：
1. `.env` 中变量名缺少 `LLM_` 前缀 → API_BASE 未加载
2. `env_file` 使用相对路径 → 从 `backend/` 子目录运行时找不到
3. `cors_origins: list[str]` → `*` 被解析为单个字符列表而非字符串
4. `extra="forbid"` → Docker Compose 中的共用环境变量（非 Pydantic 字段）被拒绝
5. `APP_ENV=test` 时加载了真实 `.env` → 污染测试环境

**处理**：
- [config.py](backend/app/core/config.py): env_file 使用绝对路径、`extra="ignore"`、`cors_origins: str`、test 环境跳过 `.env`
- [main.py](backend/app/main.py): `settings.get_cors_origins()` 解析逗号分隔字符串
- `.env`: 所有变量加 `LLM_` 前缀，API_BASE 去掉末尾 `/v1`

**学习收获**：环境变量排查按"前缀 → 路径 → 解析类型 → extra 策略 → 环境隔离"逐层排查；`extra="forbid"` 会让未声明的共享变量直接报错，在共享环境变量多的场景是隐患。

## 2026-08-08 — mypy 对 for 循环变量复用的类型误判

**症状**：`normalize_executions()` 中，mypy 在第二层循环的 `record = by_op.get(op.operation_id)` 处报 `Incompatible types in assignment (expression has type "OperationExecution | None", variable has type "OperationExecution")`，但代码逻辑正确、pytest 全部通过。重构为 if/else 后错误仍在，无法靠控制流消解。

**产生原因**：同一变量名 `record` 先在第一个 `for record in llm_executions` 循环中被推断为 `OperationExecution`（迭代变量类型），后续循环中再被赋 `by_op.get()`（返回 `Optional[OperationExecution]`）时，mypy 沿用前一个循环推断出的"已声明类型"做兼容性检查——跨循环复用变量名导致类型继承冲突，与运行时行为无关。

**解决方案**：为两层循环使用不同变量名，切断类型推断的跨循环关联：
- 第一层迭代变量改名 `llm_record`
- 第二层循环变量改名 `op_record`（并显式 `by_op.get()` 的 Optional 语义走 if/else）

修改文件：[revision.py](backend/app/domain/revision.py)

**学习收获**：Python 循环变量类型会被 mypy 跨循环"记住"——在同一函数内复用迭代变量名去承接不同/可选类型时，应换名而非依赖控制流消解。这也是区分"运行正确"与"类型清洁"的典型案例：pytest 绿不代表 mypy 静。

## 2026-08-08 — 停用词表误伤复合词导致覆盖率误判

**症状**：连续性检查的 `extract_content_chars()` 把锁定事实「林峰的核心天赋是战术视野，不是超能力」滤成 `林峰核心天赋战术视野超力`——「能」被当作停用词滤掉了，导致「超能力」残缺为「超力」。测试 `test_extract_content_chars_drops_stopwords` 断言 `== "林峰核心天赋战术视野超能力"` 失败，暴露出字符级停用词过滤与复合词的冲突。

**产生原因**：最初的停用词表把「能」「会」「要」「有」「中」「为」「于」「到」「一」「将」等字一并滤除。这些字在普通话里既是虚词（能、会=情态动词；要=助动词），又高频出现在内容复合词中（超能力 / 能力、机会 / 社会、重要 / 要求、有效 / 所有、中心 / 其中、成为 / 行为、达到 / 回到、一起 / 一定）。字符级过滤无法区分「能力」里的「能」和「我能踢球」里的「能」——一旦滤除，内容字符集残缺，覆盖率匹配随之失真，轻微措辞改变被误判为事实丢失。

**解决方案**：停用词表只保留纯虚词 / 代词（是、的、了、在、和、与、或、把、被、让、对、从、向、而、但、却、并、也、都、就、才、只、还、又、再、这、那、你、我、他、她、它、不、没、个、之、其、所、着、过），移除所有可能出现在复合词中的字（能、会、要、有、中、为、于、到、一、将）。宁可少滤几个虚词让匹配更保守，也不因滤错内容字造成误判。修改文件：[continuity.py](backend/app/memory/continuity.py)

**学习收获**：中文"字"级的停用词过滤与"词"级语义天然冲突——**没有分词器时，停用词表只能收最安全的纯虚词**，任何高频出现在复合词中的字（能/要/有/会/中/为/于/到/一）都不应放入。设计规则类文本匹配时，应先跑一遍真实语料确认内容字符提取结果，再定阈值；验收测试要直接断言提取结果，让这类失真在测试层就暴露。

## 2026-08-08 — SequenceMatcher autojunk 把高频中文字符当垃圾导致相似度虚低

**症状**：F-04 场景 diff 的相似度计算在"2100 行对白、每行仅尾缀 甲/乙 不同"的测试输入上，`SequenceMatcher(None, a, b).ratio()` 只返回 0.001（期望 ~0.9）。结果两个大场景被判为 removed+added 而非 modified，超大 diff 截断测试的 `stats.modified_lines > 2000` 断言失败；5 行的小输入却返回 0.92 正常。`_similarity` 输入是两段 ~24k 字符的 joined 场景文本。

**产生原因**：`difflib.SequenceMatcher` 默认开启 `autojunk`：对长度 > 200 的序列，把出现次数超过 `len(a)//100 + 1` 的元素自动标记为 junk 并从 `b2j` 索引剔除。24k 字符的中文文本里，「第」「句」「对」「白」各出现 2100 次，远超阈值（24110//100+1=242），几乎全部被当垃圾丢弃 → `find_longest_match` 找不到有效匹配 → ratio 趋近 0。而 `autojunk=False` 时这些高频字符参与匹配，`find_longest_match` 对每个候选组合做二次探测，长文本上退化为 O(n²)，24k×24k 直接挂起（实测 120s 不返回）。

**解决方案**：相似度不再对整段长字符串做 `SequenceMatcher`，改在**行列表**上计算——`SequenceMatcher` 对可哈希的行列表构建 `b2j` 是 O(n)（每行哈希一次）；对齐后把 replace 块配对的相邻行再做**短串**（几十字符）字符级匹配 `SequenceMatcher(a, b, autojunk=False)`。等效于整段 ratio（`2*matched/(from_chars+to_chars)`），但既避开 autojunk 把高频字符当垃圾，又避开长文本 O(n²)。修改文件：[diff.py](backend/app/tools/diff.py)

**学习收获**：`difflib` 的相似度度量只保证语义正确、不保证在任意长度/字符分布上行为直观——**autojunk 是给英文等长序列设计的启发式，对高频重复元素（尤其中文）是隐蔽陷阱**。用 `SequenceMatcher` 做长文本相似度时，应优先构造"短单位"再聚合（行级对齐 + 逐对短串比对），而不是对整段长字符串直接 `ratio()`；同时牢记 `autojunk=False` 只适合短输入（O(n²)），切勿用于长文本。度量算法要在"接近大输入规模"的测试用例上验证阈值行为，而非只做小样本单测。
