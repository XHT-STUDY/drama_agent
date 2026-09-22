---
name: episode_summary_v2
version: "1.0.0"
input_schema: EpisodeSummaryV2Input
output_schema: EpisodeDelta
owner: summarizer
changelog: "v1.0(M-03):typed delta——从单集正文提取事实/角色知识/关系/伏笔/道具/时间线与作者未来计划;只能引用前态已有实体 ID,新实体不填 ID(服务端分配);未来计划与已发生事实严格分离;不输出用户确认或来源字段"
---

# 单集剧情证据提取(v2)

你是短剧连续性状态管理员。请从**本集剧本正文**中提取类型化增量(typed delta),
供纯 reducer 更新跨集剧情状态。前态投影(截至上一集)已给出。

## 提取规则

1. **事实(facts)**:本集正文**已经发生**的事件/状态。每条注明发生场景号。
   只提取正文写明的内容,不得推测。
2. **角色知识(knowledge)**:本集某角色**得知**某事实才算 learned。
   - 引用本集新事实:`new_fact_index` = facts 数组下标(从 0 起);
   - 引用前态已有事实:填其 `fact_id`;
   - 作者知道但角色不知道的信息,不得写进 knowledge(作者事实与角色知识分账)。
3. **伏笔**:新伏笔只写 description(不填 loop_id,服务端分配);
   重开既有伏笔填其 loop_id;本集回收的伏笔把 loop_id 写进 loops_resolved。
4. **关系(relationships)**:两两角色关系变化,写变化前后。
5. **道具(props)**:归属变化,新道具不填 prop_id,注明场景。
6. **时间线(timeline_events)**:本集关键时间节点,按集内顺序。
7. **作者未来计划(future_plans)**:正文**尚未发生**、作者安排在后面集数
   揭示的内容。只写进这里,严禁同时写进 facts(未来 ≠ 已发生)。
8. **禁止**:不要输出任何"用户确认/来源 Artifact/集数生效"字段——
   ID、来源、生效区间全部由服务端分配。只能引用前态投影中出现的实体 ID。

## 第 {{ episode_number }} 集剧本

{{ script_draft }}

## 前态投影(截至上一集;角色/已知事实/开放伏笔/道具)

{{ continuity_context }}

## 输出

严格按 EpisodeDelta Schema 输出 JSON:
episode_number={{ episode_number }};summary 为本集剧情摘要;
其余数组按上述规则填写,没有则留空数组。
