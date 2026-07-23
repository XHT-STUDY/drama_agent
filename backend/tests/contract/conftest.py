"""Contract 测试共享工具 — Golden fixture 加载与资源路径。"""

import json
from pathlib import Path
from typing import Any, cast

# Golden fixtures 目录
GOLDEN_DIR = Path(__file__).parent.parent / "golden"


def load_fixture(name: str) -> dict[str, Any]:
    """加载一个 golden JSON fixture 并返回其字典。

    Args:
        name: fixture 文件名（不含路径），如 "story_bible_valid.json"。

    Returns:
        解析后的 JSON 字典。
    """
    path = GOLDEN_DIR / name
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
