"""A-01 占位测试 — 验证测试框架可运行。

后续阶段添加真实测试后，可删除本文件。
"""


def test_backend_package_imports() -> None:
    """验证 app 包可正常导入。"""
    import app

    assert app is not None


def test_placeholder_always_passes() -> None:
    """确定性占位用例，始终通过。"""
    assert True
