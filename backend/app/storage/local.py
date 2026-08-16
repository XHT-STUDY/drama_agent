"""LocalFileStore — 本地文件系统实现 (G-03 / G-05)。

设计要点：
- 存储键 = 服务端 UUID 文件名（+ 安全后缀），客户端原始文件名永不入盘；
- 原子落盘：先写同目录 `.tmp` 文件再 `os.replace`，避免半截文件；
- 防路径穿越：任何读/删操作前把 key 解析为根目录内的纯文件名，
  拒绝绝对路径、`..`、分隔符与空字节。

模块边界：只做字节持久化，不做解析/校验（由 file_parser / uploads API 负责）。
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from pathlib import Path

from app.storage.protocol import FileStore

logger = logging.getLogger(__name__)

# 允许的扩展名片段（防注入；其余字符一律剥离）
_SAFE_SUFFIX_RE = re.compile(r"^[A-Za-z0-9]{0,10}$")
# key 必须是"纯文件名"：不含路径分隔符、不含 `..`、不含空字节
_SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


class LocalFileStore(FileStore):
    """把字节保存到本地目录，返回服务端存储键。"""

    def __init__(self, root: str = "./var/uploads") -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    # ---- 私有 ----

    def _resolve(self, key: str) -> Path:
        """把存储键解析为根目录内的安全路径。

        Raises:
            ValueError: key 含路径穿越 / 非法字符时
        """
        if not _SAFE_KEY_RE.match(key):
            raise ValueError(f"非法的存储键: {key!r}")
        path = (self._root / key).resolve()
        root = self._root.resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"存储键逃逸根目录: {key!r}")
        return path

    @staticmethod
    def _sanitize_suffix(suffix: str) -> str:
        """清理客户端扩展名，只保留字母数字片段（≤10 位）。"""
        if not suffix:
            return ""
        bare = suffix.lstrip(".")
        return f".{bare}" if _SAFE_SUFFIX_RE.match(bare) else ""

    # ---- FileStore 协议 ----

    async def save(self, data: bytes, *, suffix: str = "") -> str:
        """原子写盘，返回服务端存储键。"""
        safe_suffix = self._sanitize_suffix(suffix)
        key = f"{uuid.uuid4().hex}{safe_suffix}"
        final = self._resolve(key)
        tmp = final.with_name(f"{final.name}.tmp-{uuid.uuid4().hex[:8]}")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, final)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
        return key

    async def open(self, key: str) -> bytes:
        """按存储键读取字节。"""
        path = self._resolve(key)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            raise FileNotFoundError(f"存储键不存在: {key!r}") from None

    async def exists(self, key: str) -> bool:
        """存储键是否存在。"""
        return self._resolve(key).exists()

    async def delete(self, key: str) -> None:
        """删除存储对象（best effort）。"""
        path = self._resolve(key)
        if path.exists():
            path.unlink()
