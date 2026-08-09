"""Artifact 版本与哈希工具。

提供：
- compute_checksum: 规范化 JSON 的 SHA256 校验和
- compute_input_hash: 输入 Artifact ID 集合的 SHA256（用于幂等去重）
- next_version: 版本号自增
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _canonical_json(obj: dict[str, Any]) -> bytes:
    """将 dict 序列化为确定性 JSON 字节串。

    使用 sort_keys=True 确保键顺序无关，
    separators=(',', ':') 去除空格保证跨语言一致。
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def compute_checksum(content: dict[str, Any]) -> str:
    """计算 content 的规范化 JSON SHA256 校验和。

    用于验证 Artifact 内容完整性——相同 content 必然产生相同 checksum。
    """
    return hashlib.sha256(_canonical_json(content)).hexdigest()


def compute_input_hash(
    source_artifact_ids: list[dict[str, Any]] | None,
    *,
    episode_number: int = 1,
    artifact_type: str = "",
) -> str | None:
    """计算输入 Artifact ID 集合的 SHA256。

    按 artifact_id 排序后哈希，确保顺序无关。
    用于幂等去重：相同输入 → 相同 input_hash → 可复用已有结果。

    哈希载荷同时包含 episode_number 与 artifact_type —— 否则多集工作流中
    各集剧本共享相同的 source（outline + story_bible），input_hash 会跨集
    碰撞，导致第 2 集起全部幂等复用第 1 集的 Artifact（F-05 修复）。

    Args:
        source_artifact_ids: [{"artifact_id": "uuid", "version": 1, "relation": "derived_from"}, ...]
        episode_number: 所属集号（参与幂等键，区分不同集的同源产物）。
        artifact_type: Artifact 类型（参与幂等键，避免不同类型共享源时误复用）。

    Returns:
        64 位 SHA256 十六进制字符串；无输入时返回 None。
    """
    if not source_artifact_ids:
        return None
    payload = {
        "episode_number": episode_number,
        "artifact_type": artifact_type,
        "sources": sorted(source_artifact_ids, key=lambda x: x.get("artifact_id", "")),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def compute_next_version(current_max: int | None) -> int:
    """计算下一个版本号。首版本为 1。

    Args:
        current_max: 当前最大版本号；None 表示尚无记录。
    """
    if current_max is None:
        return 1
    return current_max + 1
