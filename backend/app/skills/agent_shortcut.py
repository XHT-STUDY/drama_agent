"""确定性对话短路（A：确认/续跑短语直达动作，不经 Planner）。

设计动机：计划消息承诺"回复「确认」后开始执行"，确认门的按钮文案是
"写 1 集 / 写 5 集 / 写全部"——这些高频短语无需语义理解，走 Planner
反而会被意图白名单卡住（白名单没有"确认/继续"）。短路层在 Planner
之前整句匹配：

- 确认类短语 → 会话内最新 proposed Action 走 confirm；无 proposed
  Action 但项目有停在确认门的 Run 时直接续跑；
- 续跑类短语 → 项目停在 stage_gate 的 Run 续跑（可带批集数）。

安全原则：
- 整句匹配（首尾锚定 + 长度上限），"好的,改成悬疑吧"这类带附加
  请求的消息绝不短路，回落 Planner 正常处理；
- 最新 pending 优先（proposed Action 与门上 Run 按时间取新）；
- 未命中或无可执行目标时返回 None，行为与改动前完全一致。
"""

from __future__ import annotations

import re
import uuid
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.agent_action import AgentAction
from app.db.models.workflow_run import WorkflowRun

ShortcutKind = Literal["confirm", "continue"]
Shortcut = tuple[ShortcutKind, int | None]

# 整句长度上限：确认/续跑都是短语，超出即不再尝试短路
_MAX_SHORTCUT_LEN = 16

_STRIP_WRAP_RE = re.compile(r"^[\s\"'“”‘’「」『』【】\*]+|[\s\"'“”‘’「」『』【】\*]+$")

_CONFIRM_RE = re.compile(
    r"^(?:"
    r"好[的呀啊吧嘞哦]?|行[吧的]?|嗯+|中|成|要得|ok(?:ay)?|yes|go|"
    r"确认(?:执行|修改|修订|计划|大纲|方案|生成|开始)?|同意|通过|批准|"
    r"就(?:按|照)(?:这个|这样|这么|你说的)(?:办|来|执行|改)?|就这样|这么办|"
    r"开始(?:执行|生成)?(?:吧|呗)?|执行吧|开干|干吧|搞起"
    r")"
    r"[。,，!！?？~\s]*$",
    re.IGNORECASE,
)

_BATCH_NUM_RE = re.compile(r"^写(?:下)?\s*(\d{1,2})\s*集[吧呗啊呢。,，!！]?$", re.IGNORECASE)
_BATCH_WORD_RE = re.compile(r"^写(?:下)?\s*([一二两三四五六七八九十])\s*集[吧呗啊呢。,，!！]?$")
_NEXT_RE = re.compile(r"^(?:下一集|下一批|写下一集|先写一集|先写1集|先写1集吧)[吧呗啊呢。,，!！]?$")
_ALL_RE = re.compile(
    r"^(?:写全部|全写完|都写完吧?|全部写(?:完|完吧)?|"
    r"把?剩下的?(?:全?都)?写完|写完剩下(?:的)?(?:全部)?|一次性?写完|写完剩下的?全部)"
    r"[吧呗啊呢。,，!！]*$"
)
_BARE_RE = re.compile(
    r"^(?:继续|继续吧|继续写|继续创作|继续生成|接着?(?:写|创作|生成)|"
    r"写吧|开始写(?:吧|呗)?|resume|go on|continue)"
    r"[吧呗啊呢。,，!！~]*$",
    re.IGNORECASE,
)

_NUM_WORD: dict[str, int] = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

VALID_STAGE_GATES = frozenset({"outline", "scripts"})


def detect_shortcut(content: str) -> Shortcut | None:
    """识别确认/续跑短语；返回 (类别, 批集数) 或 None。

    批集数仅 continue 类携带：None = 写完剩余全部。
    """
    text = _STRIP_WRAP_RE.sub("", content or "").strip()
    if not text or len(text) > _MAX_SHORTCUT_LEN:
        return None
    if _CONFIRM_RE.match(text):
        return ("confirm", None)
    if _NEXT_RE.match(text):
        return ("continue", 1)
    for regex in (_BATCH_NUM_RE, _BATCH_WORD_RE):
        match = regex.match(text)
        if match:
            raw = match.group(1)
            batch = int(raw) if raw.isdigit() else _NUM_WORD.get(raw)
            if batch is not None and 1 <= batch <= 50:
                return ("continue", batch)
    if _ALL_RE.match(text) or _BARE_RE.match(text):
        return ("continue", None)
    return None


async def find_gated_run(
    db: AsyncSession, project_id: uuid.UUID
) -> WorkflowRun | None:
    """项目内最新一个停在确认门（outline/scripts）的 needs_review Run。

    模块级复用点：对话短路、动态 available_intents（continue 白名单）、
    continue 意图计划构建三处共用同一判定。
    """
    result = await db.execute(
        select(WorkflowRun)
        .where(WorkflowRun.project_id == project_id, WorkflowRun.status == "needs_review")
        .order_by(WorkflowRun.created_at.desc())
        .limit(10)
    )
    for run in result.scalars():
        gate = (run.state_summary or {}).get("stage_gate")
        if gate in VALID_STAGE_GATES:
            return run
    return None


async def find_active_run(
    db: AsyncSession, project_id: uuid.UUID
) -> WorkflowRun | None:
    """项目内正在执行（queued/running）的最新 Run。

    用于短路层区分"没有任务"与"任务正在跑"——后者应回答执行中
    而非回落 Planner 产出"没有可继续任务"的误导性澄清。
    """
    result = await db.execute(
        select(WorkflowRun)
        .where(
            WorkflowRun.project_id == project_id,
            WorkflowRun.status.in_(("queued", "running")),
        )
        .order_by(WorkflowRun.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def find_latest_proposed_action(
    db: AsyncSession, conversation_id: uuid.UUID
) -> AgentAction | None:
    """会话内最新一个待确认（proposed）的 Action。"""
    result = await db.execute(
        select(AgentAction)
        .where(
            AgentAction.conversation_id == conversation_id,
            AgentAction.status == "proposed",
        )
        .order_by(AgentAction.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def render_continue_answer(
    *,
    gate: str | None,
    written: int,
    target: int,
    batch: int | None,
) -> str:
    """短路续跑成功后的可读答复。"""
    if gate == "outline":
        head = "大纲已确认，继续创作剧本。"
    else:
        head = f"继续创作剧本（已完成 {written}/{target} 集）。"
    tail = (
        f"本批先写 {batch} 集，完成后会再次暂停等你确认。"
        if batch is not None
        else "将写完剩余全部集数。"
    )
    return head + tail
