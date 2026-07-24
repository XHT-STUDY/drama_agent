"""B-04 Artifact 版本工具单元测试。

验证 checksum 确定性、input_hash 排序无关性、版本号计算。
"""

from __future__ import annotations

from app.artifacts.versions import compute_checksum, compute_input_hash, compute_next_version


class TestComputeChecksum:
    """checksum 确定性。"""

    def test_same_content_produces_same_checksum(self) -> None:
        """相同 content 产生相同 checksum。"""
        c1 = {"a": 1, "b": 2}
        c2 = {"b": 2, "a": 1}  # 键顺序不同
        assert compute_checksum(c1) == compute_checksum(c2)

    def test_different_content_produces_different_checksum(self) -> None:
        """不同 content 产生不同 checksum。"""
        assert compute_checksum({"a": 1}) != compute_checksum({"a": 2})

    def test_checksum_is_64_hex_chars(self) -> None:
        """checksum 为 64 位十六进制字符串。"""
        result = compute_checksum({"test": "value"})
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_nested_content_deterministic(self) -> None:
        """嵌套 dict 也产生确定性 checksum。"""
        nested = {"outer": {"inner": [1, 2, 3], "flag": True}}
        assert compute_checksum(nested) == compute_checksum(nested)


class TestComputeInputHash:
    """input_hash 计算。"""

    def test_empty_returns_none(self) -> None:
        """空列表返回 None。"""
        assert compute_input_hash(None) is None
        assert compute_input_hash([]) is None

    def test_order_independent(self) -> None:
        """输入顺序不影响哈希。"""
        ids_a = [{"artifact_id": "b", "version": 1}, {"artifact_id": "a", "version": 2}]
        ids_b = [{"artifact_id": "a", "version": 2}, {"artifact_id": "b", "version": 1}]
        assert compute_input_hash(ids_a) == compute_input_hash(ids_b)

    def test_different_inputs_different_hash(self) -> None:
        """不同输入产生不同哈希。"""
        assert compute_input_hash([{"artifact_id": "a"}]) != compute_input_hash([{"artifact_id": "b"}])


class TestComputeNextVersion:
    """版本号计算。"""

    def test_first_version_is_1(self) -> None:
        """首版本为 1。"""
        assert compute_next_version(None) == 1

    def test_increment_from_current(self) -> None:
        """从当前最大版本 +1。"""
        assert compute_next_version(3) == 4
        assert compute_next_version(10) == 11
