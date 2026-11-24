---
name: revise_episode
version: "2.0.0"
input_schema: RevisionTaskInput
output_schema: OrderedRevisionResult
owner: reviser
changelog: "修订输出有序动作与台词块，沿用修订约束和操作记录"
---

# 单集剧本修订

按修订计划改写第 {{ episode_number }} 集剧本。保留集号、标题、原场次编号和锁定事实。只对计划授权的场次做要求的改动，其余场次沿用原稿。涉及前后因果的重排时，先确定发言和行动的触发关系，再按发生顺序排列。

## 原稿剧本

{{ script_draft }}

## 修订计划

{{ revision_plan }}

## 保留与禁止修改项

{{ protection_block }}

## 当前集大纲

{{ episode_outline }}

## StoryBible 参考

{{ story_bible }}

## 当前连续性状态

{{ continuity_state }}

## JSON 输出

- script_draft 包含 episode_number、title、opening_hook、ending_hook 和全部 scenes。
- 每场写 scene_number、location、time_of_day、int_ext、characters、blocks。逐块按观众所见所闻的先后顺序写。kind 为 action 时填写 text。kind 为 dialogue 时填写 speaker、text、可选 parenthetical 和 line_type，line_type 使用 dialogue、vo 或 os。动作块正文由渲染器添加三角标记。
- 若任务只要求重排，保留动作文字、台词、角色和语气，将台词放到对应动作附近。旧稿中未指定重排的场次沿用已有内容。
- operation_executions 对应计划内的每个 operation_id。填写 status（applied、partial 或 skipped）和可核对的 note。
- 服务端生成兼容正文、派生统计和来源引用。模型只需输出上述字段。
