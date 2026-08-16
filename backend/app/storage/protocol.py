"""FileStore 协议 — 文件持久化抽象 (G-03 / G-05)。

设计要点（见 Phase G 计划决策 5）：
- 上传（G-03）与导出（G-05）共用同一 FileStore 抽象；
- 存储键由服务端生成（UUID 文件名），**永不使用客户端原始文件名**；
- 实现必须保证原子落盘与路径穿越防护。

模块边界：纯存储契约，不含解析/业务逻辑。
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class FileStore(ABC):
    """文件存储协议。

    key 是服务端生成的存储键（相对路径字符串），
    不包含任何客户端输入；实现方负责防路径穿越。
    """

    @abstractmethod
    async def save(self, data: bytes, *, suffix: str = "") -> str:
        """保存字节内容，返回服务端生成的存储键。

        Args:
            data: 待持久化的文件字节
            suffix: 可选扩展名（如 ".txt"），仅允许安全字符

        Returns:
            服务端存储键（相对路径，可回传给 open/exists/delete）
        """

    @abstractmethod
    async def open(self, key: str) -> bytes:
        """按存储键读取文件字节；不存在时抛出 FileNotFoundError。"""

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """存储键是否存在。"""

    @abstractmethod
    async def delete(self, key: str) -> None:
        """删除存储对象（best effort，键不存在静默忽略）。"""
