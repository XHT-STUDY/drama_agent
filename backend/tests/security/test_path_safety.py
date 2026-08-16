"""路径穿越与文件名净化回归测试（I-03）。

覆盖：
- assert_safe_key 拒绝常见路径穿越 / 非法文件名（I-03 验收「路径穿越 fixture 全被拒绝」）；
- LocalFileStore 打开越权路径被拒、服务端存储键为纯文件名；
- sanitize_filename_part 把路径分隔符 / 控制字符替换为 `_` 并截断。

不依赖 DB / Redis / LLM。
"""

from __future__ import annotations

import pytest

from app.core.security import assert_safe_key, sanitize_filename_part
from app.storage.local import LocalFileStore


class TestAssertSafeKey:
    @pytest.mark.parametrize(
        "key",
        [
            "../../etc/passwd",
            "a/../../b",
            "..\\win\\x",
            "/etc/passwd",
            "sub/dir",
            "a b",
            "a<b>",
            "a;rm -rf /",
            "",
            "x" * 121,
            "a\u0000b",
        ],
    )
    def test_rejects_traversal_and_unsafe_keys(self, key: str) -> None:
        with pytest.raises(ValueError):
            assert_safe_key(key)

    def test_accepts_safe_keys(self) -> None:
        assert_safe_key("abc123")
        assert_safe_key("a-b_c.d")
        assert_safe_key("f" * 120)


class TestLocalFileStoreTraversal:
    async def test_open_rejects_path_traversal(self, tmp_path) -> None:
        store = LocalFileStore(root=str(tmp_path))
        with pytest.raises(ValueError):
            await store.open("../../etc/passwd")

    async def test_save_uses_safe_server_key(self, tmp_path) -> None:
        """服务端存储键为纯文件名（UUID），客户端原始文件名不入盘。"""
        store = LocalFileStore(root=str(tmp_path))
        key = await store.save(b"hello", suffix=".txt")
        assert "/" not in key and "\\" not in key
        assert key.endswith(".txt")
        assert await store.open(key) == b"hello"


class TestSanitizeFilenamePart:
    def test_traversal_chars_replaced(self) -> None:
        assert sanitize_filename_part("../../etc/passwd") == ".._.._etc_passwd"
        assert sanitize_filename_part("a\\b:c*d?e") == "a_b_c_d_e"
        assert "/" not in sanitize_filename_part("a/b/c")

    def test_truncates(self) -> None:
        assert len(sanitize_filename_part("x" * 100)) == 40

    def test_empty_and_non_string(self) -> None:
        assert sanitize_filename_part("") == ""
        assert sanitize_filename_part(None) == ""
