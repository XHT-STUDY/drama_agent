"""agent_shortcut.detect_shortcut 的纯函数路由测试。

覆盖：确认短语、续跑短语（数字/中文数字批集数）、否定样例
（带附加请求的长句绝不短路）。
"""

from __future__ import annotations

import pytest

from app.skills.agent_shortcut import detect_shortcut


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    [
        "确认",
        "确认执行",
        "确认修改",
        "确认大纲",
        "同意",
        "通过",
        "好的",
        "好",
        "行吧",
        "嗯",
        "嗯嗯",
        "OK",
        "ok",
        "yes",
        "go",
        "就这样",
        "就按这个来",
        "就按你说的办",
        "开始执行",
        "开始吧",
        "执行吧",
        "确认。",  # 尾部标点不影响
        "  确认  ",  # 首尾空白不影响
    ],
)
def test_confirm_phrases(text: str) -> None:
    assert detect_shortcut(text) == ("confirm", None)


@pytest.mark.unit
@pytest.mark.parametrize(
    "text,batch",
    [
        ("继续", None),
        ("继续写", None),
        ("继续创作", None),
        ("接着写", None),
        ("写吧", None),
        ("开始写吧", None),
        ("写全部", None),
        ("把剩下的写完", None),
        ("一次性写完", None),
        ("写5集", 5),
        ("写下 5 集", 5),
        ("写1集", 1),
        ("写下一集", 1),
        ("下一集", 1),
        ("下一批", 1),
        ("写三集", 3),
        ("写两集", 2),
        ("继续。", None),
    ],
)
def test_continue_phrases(text: str, batch: int | None) -> None:
    assert detect_shortcut(text) == ("continue", batch)


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    [
        "好的，不过我想把主角改成女生",  # 确认 + 附加请求 → 不短路
        "确认改成悬疑风格",
        "确认一下大纲的第二集",  # "确认"后接具体内容
        "帮我确认一下评估结果",
        "继续帮我改第3集",
        "继续写之前先把大纲改一下",
        "我想写一个足球少年的逆袭故事",
        "帮我改一下这里",
        "这个方案不太行",  # 含"行"但不是确认
        "确认" * 10,  # 超长句不短路
        "",
        "   ",
    ],
)
def test_non_shortcut_messages(text: str) -> None:
    assert detect_shortcut(text) is None
