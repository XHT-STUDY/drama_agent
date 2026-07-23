"""DramaAgent 测试根 conftest。

所有测试目录共享的全局 fixtures 在此定义。
"""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest


@pytest.fixture(autouse=True)
def _force_test_env() -> Generator[None, None, None]:
    """自动确保所有测试在 APP_ENV=test 下运行。

    防止测试意外读取 local .env 中的真实密钥或连接外部服务。
    """
    old = os.environ.get("APP_ENV")
    os.environ["APP_ENV"] = "test"
    yield
    if old is not None:
        os.environ["APP_ENV"] = old
    else:
        os.environ.pop("APP_ENV", None)
