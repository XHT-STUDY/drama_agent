---
status: proposed
---

# Memory 效果评测必须重放生产链路

Memory 的主评测必须通过真实 API、PostgreSQL、Redis、累计摘要、StoryStateService、工作流、ContextBuilder 和实际模型调用执行；参考 harness 只负责提供真值和评分，不得复制生产写入、召回或状态更新逻辑。因果消融在同一批冻结输入上只替换 Memory 视图，避免各组因上游作品不同而把模型随机性误判为 Memory 收益。这个选择比纯 harness 更慢、更贵，但能防止“评测实现正确、生产接线错误”的假阳性。

## Consequences

- 真实模型评测使用独立数据库和 Redis，禁止连接生产数据。
- 所有结果必须保存模型、Prompt、数据集、迁移版本、Artifact 来源和上下文证据。
- CI 继续运行 FakeLLM 契约评测；真实链路套件按 smoke、release、audit 三档控制费用。
- 不新增面向用户的 API；评测开关和追踪能力只在显式评测模式可用。
