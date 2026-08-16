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

## 2026-08-09 — input_hash 只哈希 source ids 导致多集工作流全部幂等复用第 1 集

**症状**：F-05 新增的 `test_revision_workflow.py` 中 happy path 断言 `list_versions(script_draft, ep=2/3) == 1` 失败——ep2/ep3 的剧本版本数恒为 0；调试探针显示播种的 3 个评估报告读回来是**同一个 Artifact ID、同一份低分内容**（overall 73.2）。进一步验证：完整 creation 工作流跑完后 `script_artifact_ids` 的 3 个 key 指向**同一个 ID**，数据库里 ep2/ep3 没有任何剧本行——即**真实管线从 Phase C 起实际只产出并评估了第 1 集**，只是此前测试都断言 dict 的 key 数量（`len(...)==3`）而漏检。

**产生原因**：`ArtifactStore.create` 用 `compute_input_hash(source_artifact_ids)` 做幂等去重，而该哈希的载荷**只有 source_artifact_ids**。多集工作流中每一集剧本都从同一份 outline + story_bible 派生（source 完全相同、版本号相同）→ ep1/ep2/ep3 的 input_hash 完全一致 → 第 2 集起 `find_by_input_hash` 直接返回第 1 集的 Artifact，新行从不插入。评估报告同理：因为 3 集剧本已全部去重成同一个 script id，3 份评估的 source（同 script id）也碰撞。

**解决方案**：把 `episode_number` 与 `artifact_type` 纳入哈希载荷——它们才是"逻辑输入身份"的一部分（同 source、不同集数/类型不是同一产物）。修改 [versions.py](backend/app/artifacts/versions.py)（`compute_input_hash(source, *, episode_number, artifact_type)` 载荷改为 `{"episode_number", "artifact_type", "sources"}`）与 [store.py](backend/app/artifacts/store.py) 调用处。修复后：同集同源重试仍幂等复用（idempotency 语义保留），跨集不再误复用。附带把测试播种里"ep1_report 缺失时把所有集都种成高分"的 helper 笔误一并修正（文档与实现不符，导致 select 选不出候选）。

**学习收获**：
- **幂等键必须覆盖业务输入的身份边界**：只哈希"来源依赖"是不够的——多集剧本同源是常态，缺 episode/type 的键会把不同集误判为同一输入。设计幂等键前先枚举"什么算相同输入"。
- **"断言 key 数量"≠"断言内容存在"**：`len(dict)==3` 对"3 个 key 指向同一对象"毫无防备。验证"每集都有产物"要用按集查询（`list_versions` 计数 / 按集拉取），并断言 ID 独立性。
- **测试 helper 的自证**：播种 helper 的 docstring 说"ep1 低分、ep2/3 高分"，实现却全种高分——调试时单独写"播种→读回→跑纯函数"的探针测试，一步定位是播种错还是节点错，远快于读满图日志。

## 2026-08-09 — git stash 对比 mypy 基线污染（untracked 文件未被藏起）

**症状**：F-06 收尾验证 mypy 时，采用"改动前 `git stash` 跑一遍当基线，改动后跑一遍对比新增"的方法。第一次对比得到"基线 62 错误 vs 当前 59 错误"——新增文件使基线比当前还高，结论自相矛盾，无法判定是否引入了新错误。

**产生原因**：`git stash`（无 `-u`）**默认不藏 untracked 文件**。F-06 新增的 [revisions.py](backend/app/api/v1/revisions.py) 与 [test_revisions.py](backend/tests/integration/api/test_revisions.py) 是全新文件，`git stash` 后仍留在工作区——"基线"跑的是"旧 runs.py + 新 revisions.py"的混合状态，其错误数既包含旧代码的存量错误、又包含新文件尚未修复的错误，所以反而高于改动后的干净状态。此外行号差异也让两份输出难以 diff。

**解决方案**：放弃 stash 法，改用**消息归一化对比**——把错误消息按行号归一化后直接 diff 两份完整输出：

```bash
cd backend
uv run mypy 2>&1 | sed -E 's/(\.py):[0-9]+: error: /\1: /' | sort > /tmp/mypy_current.txt
git stash && uv run mypy 2>&1 | sed -E 's/(\.py):[0-9]+: error: /\1: /' | sort > /tmp/mypy_base.txt && git stash pop
diff /tmp/mypy_base.txt /tmp/mypy_current.txt   # 仅显示被"删除"的行(改掉的存量错误)与被"新增"的行(真正新错误)
```

归一化后只比 `文件: 错误代码` 前缀，行号差异不再污染；确认基线 59 条全部落在未改动的文件（runs.py 的 FakeLLM/ainvoke/unused-ignore、creation.py type-arg、既有测试文件等），改动文件与新增文件零错误 → 判定 0 新增。

**学习收获**：用 git 机制做"前后对比"时，**必须显式确认被藏/被恢复的文件集合覆盖了你关心的全部文件**——untracked 的新增文件是 stash 的盲区。做 mypy（或任何全仓静态检查）的基线对比，直接对两份完整输出做归一化 diff 更简单可靠，不必依赖 git；同时用 `sort` 消除顺序差异。归一化正则要**只去掉行号、保留文件与错误代码**，避免把不同的真实错误混淆。

## 2026-08-09 — mypy 局部推断陷阱：分支类型污染 / 同作用域同名变量 / reexport

**症状**：F-06 的三个 mypy 报错都是"运行正确、类型不洁"：
1. `revision_service.py` 报 `Incompatible types in assignment`——`selected` 变量先被赋 `EvaluationReport`、else 分支再赋 `select_revision_candidate(reports)`（返回 `EvaluationReport | None`）；
2. `runs.py` 报 `Name "latest_per_episode" already defined`（no-redef）——evaluate 与 revise 两个 elif 分支在同一函数作用域各定义了一个同名变量；
3. `test_revisions.py` 报 `"DEFAULT_EVALUATION_WEIGHTS" is not exported`——从 `app.domain.evaluation` import 该常量，但 mypy 严格模式不允许 re-export。

**产生原因**：
1. 变量在第一个分支被推断为窄类型后，后续分支再赋宽类型（`T | None`）时，mypy 沿用首次推断做兼容性检查——控制流虽然保证 else 分支不会同时执行，但静态检查按声明序合并。
2. Python 没有块级作用域，elif 分支里的变量同属函数作用域；mypy 视同名重定义为错误（非 `# type: ignore` 无法共存）。
3. 常量定义在 `app.domain.enums`，`app.domain.evaluation` 只是 `from ... import` 转发；严格模式（`no_implicit_reexport` 默认关闭但对顶层 import 有警告）不接受转发层作为合法来源。

**解决方案**：
1. 显式注解 `selected: EvaluationReport`，并用中间变量 `candidate = select_revision_candidate(reports)` 收窄后再赋给 `selected`（if candidate is None: return None 之后 candidate 已被 narrow 成 EvaluationReport）；
2. 改名切断同作用域冲突——revise 分支的变量改为 `latest_scripts`（语义也更准确：只有 evaluate 分支需要"每集最新"，revise 分支要的是"全部待修订集的剧本集合"）；
3. 改从**真正定义处** import：`from app.domain.enums import DEFAULT_EVALUATION_WEIGHTS`。mypy 的错误消息会直接指出"该符号在哪个模块定义"——按它的提示改 import 来源即可。

**学习收获**：
- mypy 的局部类型推断是**声明序敏感**的：先窄后宽、同作用域重名、跨模块转发 import 都是静态检查的坑，与运行时正确性完全无关——pytest 全绿不代表 mypy 静。
- 应对三件套：①分支内收窄宽类型时用中间变量 + `if x is None: return` 让控制流替你做 narrowing；②同作用域复用变量名一律改名而非加 ignore；③严格模式报 reexport 时看错误提示"该符号定义在哪"，改从源头 import。
- 收尾验证时把 `uv run mypy` 当作第一公民（与 ruff 同级），新增文件尤其要在 mypy 严格模式下保持全类型化——API handler 不返回 `Any`，文档对 `revisions.py` 的要求正是为让新代码不进错误基线的门槛。

## 2026-08-09 — vitest 内所有 useState 组件报 "Invalid hook call"（React 双实例）

**症状**：H-06 新增 `DiffView`（内含 `useState`）测试后，**任何一个**渲染含 hook 组件的测试都抛 `Invalid hook call` / `Cannot read properties of null (reading 'useState')`。此前全套测试（H-01..H-05）全绿——因为被测组件全部无状态，从未真正渲染过 hook 组件。连一个最小 `function Probe(){ const [open]=useState(false); return <div/> }` 的探针测试也失败。

**产生原因**：pnpm workspace hoisting 导致**两份 react**：
- `@testing-library/react` 被提升到仓库根 node_modules（`/home/xie/drama_agent/node_modules/.pnpm/@testing-library+react@16.3.2_...`），它 `import "react-dom/client"` 走**根**的 react-dom → 运行时 `require("react")` 命中**根** `.pnpm/react@19.2.8`；
- 测试文件 `import React from "react"`（经 frontend 的 Vite 解析）走 **frontend** `.pnpm/react@19.2.8`。

React 19 的 `ReactSharedInternals.H`（hook dispatcher）挂在**各自模块实例**上：react-dom 在自己的 react 实例上 `H = currentDispatcher`，`useState` 在另一个实例上读 `H` → 渲染期间 dispatcher 为 `null` → "Invalid hook call"。用 `require.resolve` 从测试目录探针看不出问题（它两端都解析到 frontend），必须从 **@testing-library/react 所在目录**解析才看到根副本。验证手段：`require.resolve("react-dom/client", { paths: [TLR路径] })` ≠ `require.resolve("react", { paths: [测试目录] })`。

**解决方案**：在 [vitest.config.ts](frontend/vitest.config.ts) 的 `resolve.alias` 用**数组形式**把 react 系全部 alias 到【与 react-dom 同源的根副本】具体文件，且**更具体前缀在前**：
```ts
resolve: {
  alias: [
    { find: "@", replacement: path.resolve(__dirname, "./src") },
    { find: "react-dom/client",   replacement: path.resolve(__dirname, "../node_modules/.pnpm/react-dom@19.2.8_react@19.2.8/node_modules/react-dom/client.js") },
    { find: "react-dom",          replacement: path.resolve(__dirname, "../node_modules/.pnpm/react-dom@19.2.8_react@19.2.8/node_modules/react-dom/index.js") },
    { find: "react/jsx-dev-runtime", replacement: path.resolve(__dirname, "../node_modules/.pnpm/react@19.2.8/node_modules/react/jsx-dev-runtime.js") },
    { find: "react/jsx-runtime",  replacement: path.resolve(__dirname, "../node_modules/.pnpm/react@19.2.8/node_modules/react/jsx-runtime.js") },
    { find: "react",              replacement: path.resolve(__dirname, "../node_modules/.pnpm/react@19.2.8/node_modules/react/index.js") },
  ],
}
```
注意两个无效方案（踩过）：`resolve.dedupe: ["react","react-dom"]` 单独用不生效——react-dom 是 **externalized CJS**，运行时 `require("react")` 由 Node 解析，dedupe 管不到；`test.server.deps.inline` 也未让 react-dom 走 Vite 图。另外 alias **不能**指向 frontend 副本——react-dom 仍在根，两根不对齐。**必须让测试的 react 对齐 react-dom 的 react（根副本）**。

**学习收获**：
- monorepo + pnpm workspace 下，测试环境很容易出现"渲染方（react-dom）与调用方（useState）各持一份 react"的静默分裂，症状就是**所有 hook 组件 Invalid hook call，而无状态组件全绿**——无状态测试永远暴露不了它。新增第一个带 hook 的测试时，先跑一个 `useState` 探针。
- 排查双实例不要只从测试目录 `require.resolve`，要从真正**加载渲染器的那个依赖**（此处是根 node_modules 里的 @testing-library/react）反向解析，才能看到真实分裂。
- 另一个连带坑：jsdom 不触发 `<details>/<summary>` 的 `toggle` 事件，受控 `<details>` 的交互测试永远点不开。折叠交互组件直接改用按钮 + `onClick` 显式 setState，别依赖原生 details 行为。

---

## 2026-08-09 — uvicorn 起后端报 `Attribute "app" not found in module "app.main"`

**症状**：E2E 编排里 `uvicorn app.main:app` 启动即失败，`Error loading ASGI app. Attribute "app" not found in module "app.main"`。

**产生原因**：本仓库后端用的是 **app factory 模式**——[main.py](backend/app/main.py) 只有 `create_app(settings)`，没有模块级 `app` 对象。`uvicorn app.main:app` 期望模块里有一个名为 `app` 的 ASGI 实例。开发时一直没暴露，因为此前没有统一的后端启动脚本（`make run` 不存在，文档也未记录 uvicorn 命令）。

**解决方案**：用 uvicorn 的 **factory 模式**：`uvicorn "app.main:create_app" --factory`。工厂字符串要求可调用对象返回 ASGI app，uvicorn 加载后调用之。

**学习收获**：`app.factory:create_app` 与 `app.factory:app` 是两种不同的 uvicorn 入口。遇到 `Attribute not found` 先看目标模块是工厂还是实例；用 `--factory` 或写一个模块级 `app = create_app()` 入口。E2E 编排这类"基础设施脚本"最容易踩，启动命令应该在第一个集成脚本里就验证。

---

## 2026-08-09 — `pnpm start -- -p 3100` 把 `--` 原样传给 next，报 `Invalid project directory: -p`

**症状**：E2E 前端启动日志显示 `next start "--" "-p" "3100"`，立即报 `Invalid project directory provided, no such directory: /home/xie/drama_agent/frontend/-p`。

**产生原因**：pnpm（9.x）转发参数到 npm script 时会把 `--` 也一并传下去，`next start` 不认 `--` 后面的位置参数分割，把 `-p` 当成了目录参数。

**解决方案**：改用 `pnpm exec next start -p 3100`——直接调用 node_modules 里的 next 可执行文件传参，不走 npm script 的 `--` 转发语义。

**学习收获**：`pnpm <script> -- args` 与 `pnpm exec <binary> args` 的传参语义不同；前者把 `--` 透传给脚本（许多 CLI 不接受），后者干净。编排脚本里调用项目自带二进制，优先 `pnpm exec` / `npx` 形式。

---

## 2026-08-09 — WSL 下 Playwright Chromium 缺系统库：libnspr4/libnss3/libasound.so.2 not found

**症状**：`playwright install chromium` 装好了浏览器二进制，但 `chromium.launch()` 立即失败：`error while loading shared libraries: libnspr4.so ... libnss3.so ... libnssutil3.so ... libasound.so.2 ...`。`playwright install chromium --with-deps` 需要 sudo，而 WSL 的 sudo 要密码，无法在非交互脚本里安装。

**产生原因**：Chromium 依赖 NSS/NSPR/ALSA 系统库，WSL 精简环境默认未装；这些库属于 root 管理包，普通用户装不了。

**解决方案**（**无需 sudo 的用户级解**）：
```bash
mkdir -p var/pw-libs/debs && cd var/pw-libs/debs
apt-get download libnss3 libnspr4 libasound2t64   # Ubuntu 24.04 是 t64 变体（libasound2 无 candidate）
for d in *.deb; do dpkg-deb -x "$d" ../root/; done
```
然后在启动 Playwright 时注入：
```bash
export LD_LIBRARY_PATH="$ROOT/var/pw-libs/usr/lib/x86_64-linux-gnu"
```
[e2e.sh](scripts/e2e.sh) 检测到 `var/pw-libs` 存在即注入，普通 Linux 无此目录不受影响。

**学习收获**：WSL / 容器这类精简 Linux 跑 headless Chromium 几乎必然缺 NSS 系库。遇到 sudo 不可用时，`apt-get download + dpkg-deb -x + LD_LIBRARY_PATH` 是干净的用户级替代——不改系统、不污染环境，还能随项目目录分发（已加 .gitignore 则本地自持）。

---

## 2026-08-09 — Playwright `getByText("创作 Idea")` strict mode violation: resolved to 2 elements

**症状**：E2E 第一步断言工作台出现「创作 Idea」失败：`strict mode violation: getByText('创作 Idea') resolved to 2 elements`——同时命中输入区 `<h2>创作 Idea</h2>` 和空态引导段 `<p>在上方输入创作 Idea，点击…</p>`。

**产生原因**：`getByText` 默认子串匹配，同一文案在不同 UI 位置重复出现（标题 + 引导文案）即报严格模式冲突。Playwright 1.28+ 对定位到多元素默认抛错，防止断言语义模糊。

**解决方案**：对易重复文案用 `.first()`（`page.getByText("创作 Idea").first()`），或换更精确的 role locator（`getByRole("heading", { name: "创作 Idea" })`）。

**学习收获**：写 E2E 断言前先想"这段文本在页面上可能出现几次"——标题、空态引导、面包屑、占位符都常复用同一关键词。能精确就精确（role/name），不能精确就用 `.first()` 并注释原因，别让严格模式冲突反复打断冒烟。

---

## 2026-08-09 — 下载断言死锁：先 `await download` 再点按钮，事件永远等不到

**症状**：E2E 下载步骤偶发/必现超时——`page.waitForEvent("download")` 挂住不动，直到 Playwright 超时。

**产生原因**：把 `waitForEvent("download")` 写成"先注册 Promise → 但把它放进 await 链的后面，等点击完成后才拿到 Promise"或"先 await 点击再 await download"——download 事件在点击瞬间就已触发，晚到的监听错过它（Playwright 事件队列里 `download` 不会重放）。

**解决方案**：**必须先注册 waitForEvent，再触发下载**。把触发封装成回调，helper 内先 `const p = page.waitForEvent("download"); await trigger(); const d = await p;`。[e2e/fixtures/helpers.ts](e2e/fixtures/helpers.ts) 的 `expectDownloadNotEmpty(page, trigger)` 即此模式。

**学习收获**：凡是"事件发生在动作瞬间"的断言（download、filechooser、response、request），都要先挂监听再触发动作，且用回调延迟触发时机。这个坑对 `waitForEvent("popup")`、`page.on("filechooser")` 同样适用。

---

## 2026-08-16 — RetrievalTrace 三阶段 trace 被幂等去重成一条：ArtifactStore input_hash 碰撞

**症状**：`test_full_creation_persists_retrieval_traces` 断言三阶段 trace 应各一条,实际 `stages == ['story_bible']`——只有 story_bible 阶段的 RetrievalTrace Artifact 落库。调试发现 retrieve 节点三阶段都正常返回块(5/5/4),说明检索没问题,是持久化丢记录。

**产生原因**：`ArtifactStore.create` 的幂等去重逻辑按 `input_hash = compute_input_hash(source_artifact_ids, episode_number, artifact_type, dedup_extra)` 判重——三阶段 RetrievalTrace 的 source 都是同一个需求 Artifact、artifact_type 都是 `retrieval_trace`、episode_number 都是 None,于是三者的 input_hash 完全相同。create 命中 `find_by_input_hash` 后直接返回既有记录,**后面的两条 trace 被静默当成"重复提交"丢弃**,只有第一条真正落库。

**解决方案**：在 [retrieve.py](../backend/app/workflows/nodes/retrieve.py) 的 `_persist_trace` 里给 `create_validated_artifact` 传 `dedup_extra=stage`,让幂等键带上阶段维度(story_bible/outline/writer 各不相同)。同 source 派生的多实例 Artifact 用 dedup_extra 显式区分。

**学习收获**："Artifact 不可变版本 + 幂等去重"双约束下,凡是一个 source 派生**多个"同类型但语义不同"的 Artifact**,都必须用 `dedup_extra` 把幂等键区分开;否则输入哈希相同会被静默合并成一条,且不报错(表面看起来"成功"),只在断言数量时才暴露。写多实例派生 Artifact 的持久化时,先问一句"它们的幂等键会不会撞"。

---

## 2026-08-16 — `.docx 非 zip` 传成 500：`except (BadZipFile, X): raise` 把原始异常透传

**症状**：`test_docx_not_a_zip` 失败——对 `.docx` 但内容不是 zip 的文件,`FileParserTool._parse_docx` 抛出的不是业务错误 `FileParseFailedError`,而是裸 `zipfile.BadZipFile: File is not a zip file` 直达测试断言层。

**产生原因**：解析器里写成了 `except (zipfile.BadZipFile, FileParseFailedError): raise`。意图是「宏/缺部件抛出的 FileParseFailedError 直接放行、其他异常统一映射」，但 `BadZipFile` 也落进了这个分支——`raise` 原样重抛,绕过映射,上层 FastAPI 异常处理器不认它,最终 500。

**解决方案**：把 except 拆开——`FileParseFailedError` 单列 `raise` 保序;`BadZipFile` 单独捕获并显式转 `raise FileParseFailedError("不是有效的 DOCX 文件（损坏的压缩包）") from None`;其余异常才 `raise ... from exc`。模板：
```python
except FileParseFailedError:
    raise
except zipfile.BadZipFile:
    raise FileParseFailedError(...) from None
except Exception as exc:  # noqa: BLE001
    logger.warning(...)
    raise FileParseFailedError(...) from exc
```

**学习收获**：「catch 后 `raise` 原异常」是「把责任交给上层」，只有上层真的有对应 handler 才成立;做「统一映射成业务异常」时,`except 业务异常: raise` 只该列真正需要保序的自家异常,库抛的异常必须显式转成业务异常(`from None` 避免内部链)。写完 try/except 的映射先自问:这个 except 分支到底是想「放行」还是「转换」?

---

## 2026-08-16 — import_classification 幂等去重失效：`compute_input_hash` 无源短路返回 None

**症状**：G-04 集成测试 `test_idempotent_same_upload_no_duplicate_artifact` 失败——同 upload 跑两次 import，`classification_artifact_id` 不一致，Artifact 版本号也不停 +1。期望同 upload 只产一个分类版本。

**产生原因**：`ArtifactStore.create` 的幂等检查是 `input_hash = compute_input_hash(source_artifact_ids, ...)`;若 `input_hash is not None` 才走 `find_by_input_hash`。而 `compute_input_hash` 开头是 `if not source_artifact_ids: return None`——import_classification **没有 source**(不派生自任何 Artifact),dedup_extra 只在有源时才进哈希载荷,于是无源产物永远得到 `input_hash=None`,幂等分支从不触发。G-01 会话摘要(同样无源、依赖 dedup_extra 幂等)存在同一潜伏 bug,只是被 covered_to 递增掩盖未暴露。

**解决方案**：改 [versions.py](../backend/app/artifacts/versions.py) `compute_input_hash`——把短路条件从 `if not source_artifact_ids` 改为 `if not source_artifact_ids and not dedup_extra`;有源分支保持原样(哈希逐字节不变),dedup_extra 单独出现(无源)时也构造载荷并参与哈希。这样「无源独立产物(会话摘要/导入分类)」仅凭 dedup_extra 即可幂等,而「无源且无 dedup_extra」仍返回 None(避免同类型同集的无输入产物相互碰撞)。补了 `test_dedup_extra_without_sources_hashes` 回归测试。

**学习收获**：**幂等键不仅要看「传了什么参数」，还要看参数是否真的触达判重逻辑**——注释写「dedup_extra 幂等」不等于代码真的幂等，前置短路会把整个机制架空。凡是「无源产物 + dedup_extra」的用例，先确认 `compute_input_hash` 不会因无源提前返回 None；并给这类无源产物补「重复触发只产一条」的回归测试。

---

## 2026-08-16 — 嵌套函数 `lines += [...]` 触发 UnboundLocalError

**症状**：G-05 编写 `markdown_from_story_bible` 时,单元测试运行报 `UnboundLocalError: local variable 'lines' referenced before assignment`——外层函数明明已 `lines = [...]` 初始化,嵌套的 `character_section` 块构造函数里做 `lines += [...]` 却报 unbound。

**产生原因**：Python 对函数的局部变量判定是「凡在函数体内被赋值的名字都是局部变量」,而 `+=` 对 list 虽然语义是 in-place 修改,字节码仍会生成 `STORE` 指令(它按 `lines = lines + [...]` 对待,先取再赋)。于是嵌套函数里 `lines += [...]` 让解释器把 `lines` 标记为**该嵌套函数的局部变量**,在外层作用域的同名变量对它不可见——第一次执行取 `lines` 时还未赋值,抛 unbound。它不像 `list.append()` 那样只是「读取」外层变量,而是「绑定」了局部名。

**解决方案**：嵌套块构造函数里一律用 `lines.extend([...])`(只读外层变量 + 原地扩展,不触发局部绑定);若确实需要重新绑定,先在嵌套函数里 `nonlocal lines` 声明。项目 markdown 组装大量用 list 拼接,凡 `build_xxx_markdown` 内部再分块构造的段落,统一用 extend。

**学习收获**：**「看起来是 in-place」的增强赋值在函数作用域里是绑定语句**——`list += [...]` 与 `list.extend([...])` 在作用域语义上完全不同:前者把名字变成该函数局部变量,后者只是读取。排查 UnboundLocalError 时,先找函数体内所有出现赋值符号(`=`、`+=`、`*=`、`|=`)的名字,尤其是嵌套闭包里的,而不是怀疑外层初始化丢了。

---

## 2026-08-16 — 嵌套函数内 `lines +=` 误报 unbound 的补充：DOCX 东亚字体必须写进 `w:eastAsia`

**症状**：`test_docx_chinese_preserved` 起初想用 `font.name = "宋体"` 设中文字体,中文段落渲染后字体仍回落默认(OpenXML 只写了 ascii/hAnsi,rFonts 缺 eastAsia),python-docx 重开测试虽不报错但字体不生效。

**产生原因**：OpenXML 的 `rPr/rFonts` 分 ascii、hAnsi、eastAsia 三个槽位。python-docx 的 `font.name` 只写 ascii/hAnsi;中文属于 eastAsia 槽位,不写 `w:eastAsia` 就默认西文字体,中文显示为 fallback。这不是 python-docx 的 bug,而是 docx 格式对中文本来的要求。

**解决方案**：写一个 `_apply_east_asia_font(style)` 帮助函数——先 `style.font.name = "Calibri"`(Latin),再用 `style.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")` 显式设置东亚字体;Normal 与 Heading 1/2/3 样式统一应用。

**学习收获**：**「字体设置」在 docx 里是三分槽的**,中文化(或任何 CJK)导出必须显式写 `w:eastAsia`,只设 `font.name` 是常见坑;测试要验证中文字体生效,应检查 XML 里 `w:eastAsia` 属性值而非依赖重开成功。

---

## 2026-08-16 — 直接创建 evaluate Run 永久停在 queued

**症状**：G-06 端到端测试 `test_full_script_upload_to_evaluate_to_export` 里 `POST /projects/{id}/runs`(action=`evaluate`)返回 202 后,轮询 `GET /runs/{id}` 直到 80×0.2s 超时状态仍是 `queued`,断言 `AssertionError: Run ... 未完成: queued`。HTTP 层一切正常(202、Run 行存在、无错误日志),只有状态永远不前进。

**产生原因**：`create_run` 里 `schedule_worker(run.id, action, ...)` 有 allow 名单(白名单),名单只含当时接入的 action(create_script/platform_smoke/revise/import),**`evaluate` 不在名单里**——于是直接以 action=evaluate 创建的 Run 只落库、不派发 Worker,永远停在 queued。这是既有缺口:评估在标准管线里由创作 Run 的 elif 链内部触发,从未有人以独立 action 创建过 evaluate Run,所以名单一直漏它;G-06 的「完整剧本→评估→导出」第二条导入路径恰好需要独立评估,缺口立刻暴露。

**解决方案**：把 `"evaluate"` 补进 `create_run` 的 schedule_worker 名单(与 `"export"` 一同加入)。修复后两条端到端路径都过。同时确认名单与 `_execute_workflow` 支持的所有 action 对齐,防再次遗漏。

**学习收获**：**"永不执行"类的异步故障,最明显的暴露点是"等待终态超时",而不是报错**——HTTP 返回 202、Run 行存在、无异常日志,只有轮询等不到终态。凡新增一个 action,必须同时：(1) 进 `create_run` 的 schedule_worker 名单；(2) 有端到端测试断言"轮询到终态"。名单与执行分支是两份数据,靠人脑同步必漏,靠"轮询到终态"的测试兜底。
