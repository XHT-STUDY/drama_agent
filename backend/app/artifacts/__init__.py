"""DramaAgent Artifact 不可变存储层。

提供：
- ArtifactStore：不可变版本管理（创建/查询/版本历史）
- versions：SHA256 校验和、输入哈希、版本号计算

模块边界（DEV_PLAN §4.1）：
- artifacts 模块负责不可变版本、依赖和 Diff；
- 不修改历史版本。
"""

from app.artifacts.store import ArtifactStore
from app.artifacts.versions import compute_checksum, compute_input_hash

__all__ = [
    "ArtifactStore",
    "compute_checksum",
    "compute_input_hash",
]
