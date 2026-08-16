"""DramaAgent RAG 知识库模块。

模块边界（DEV_PLAN §4.1）：
- rag 层负责知识文档的加载、切块、向量化与检索；
- 不直接操作 Artifact / 不参与创作控制流（接入点在工作流节点）；
- 所有领域 Schema 放 rag/models.py 与 domain/retrieval.py。
"""
