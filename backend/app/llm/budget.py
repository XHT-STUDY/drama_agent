"""per-run LLM 预算 — 软/硬上限与调用计数 (I-01)。

按 run 跟踪 LLM 调用次数与 token 用量，防止单次 Run 无限调用付费 LLM：
- 软上限（run_max_llm_calls）：跨越时置 soft_warned，Worker 结束后发 run.warning；
- 硬上限（run_max_llm_calls_hard / run_max_llm_tokens_hard）：check_budget 抛
  BudgetExceededError → 409 RUN_BUDGET_EXCEEDED，Run 失败落库。

MVP 进程内实现（KNOWN_LIMITATIONS：多进程部署时预算不共享）：
- 模块级 RunBudgetRegistry 按 run_id 关联预算；
- contextvar 记录当前 run，LLM 客户端无需感知 run_id。

预算挂钩点：
- LLM 客户端每次真实尝试前 check_budget()、尝试后 record_call(usage)；
- Worker 进入时 enter_run(run_id, ...)、finally 中 exit_run(run_id)。
"""

from __future__ import annotations

import threading
from contextvars import ContextVar

from app.core.errors import BudgetExceededError  # noqa: F401  # 重导出供调用方使用

# 当前 run 上下文（由 Worker 在 workflow.ainvoke 前 enter_run 设置）
_run_id_ctx: ContextVar[str | None] = ContextVar("budget_run_id", default=None)


class RunBudget:
    """单个 run 的 LLM 预算状态。"""

    __slots__ = (
        "soft_calls",
        "hard_calls",
        "hard_tokens",
        "calls",
        "prompt_tokens",
        "completion_tokens",
        "soft_warned",
    )

    def __init__(self, soft_calls: int, hard_calls: int, hard_tokens: int) -> None:
        self.soft_calls = soft_calls
        self.hard_calls = hard_calls
        self.hard_tokens = hard_tokens
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.soft_warned = False

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def record_call(self, *, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        """记录一次真实 LLM 尝试。"""
        self.calls += 1
        self.prompt_tokens += max(0, prompt_tokens)
        self.completion_tokens += max(0, completion_tokens)
        if self.soft_calls and self.calls > self.soft_calls:
            self.soft_warned = True

    def check_hard(self) -> None:
        """硬上限检查；超限抛 BudgetExceededError。"""
        if self.hard_calls and self.calls >= self.hard_calls:
            raise BudgetExceededError(
                detail=f"Run LLM 调用数超预算（{self.calls}/{self.hard_calls}）"
            )
        if self.hard_tokens and self.total_tokens >= self.hard_tokens:
            raise BudgetExceededError(
                detail=f"Run LLM token 超预算（{self.total_tokens}/{self.hard_tokens}）"
            )


class RunBudgetRegistry:
    """进程内 per-run 预算注册表（线程安全）。"""

    def __init__(self) -> None:
        self._budgets: dict[str, RunBudget] = {}
        self._lock = threading.Lock()

    def begin(
        self,
        run_id: str,
        *,
        soft_calls: int,
        hard_calls: int,
        hard_tokens: int,
    ) -> RunBudget:
        budget = RunBudget(soft_calls, hard_calls, hard_tokens)
        with self._lock:
            self._budgets[run_id] = budget
        _run_id_ctx.set(run_id)
        return budget

    def end(self, run_id: str) -> None:
        with self._lock:
            self._budgets.pop(run_id, None)
        if _run_id_ctx.get() == run_id:
            _run_id_ctx.set(None)

    def get(self, run_id: str | None) -> RunBudget | None:
        if run_id is None:
            return None
        with self._lock:
            return self._budgets.get(run_id)

    def clear(self) -> None:
        """清空所有预算（测试隔离用）。"""
        with self._lock:
            self._budgets.clear()
        _run_id_ctx.set(None)


_registry = RunBudgetRegistry()


def enter_run(
    run_id: str,
    *,
    soft_calls: int = 0,
    hard_calls: int = 0,
    hard_tokens: int = 0,
) -> RunBudget:
    """Worker 进入 run 时建立预算并设置当前 run 上下文。"""
    return _registry.begin(
        run_id, soft_calls=soft_calls, hard_calls=hard_calls, hard_tokens=hard_tokens
    )


def exit_run(run_id: str) -> None:
    """Worker 结束时释放预算并清除上下文。"""
    _registry.end(run_id)


def current_run_id() -> str | None:
    """当前上下文关联的 run_id（无则 None）。"""
    return _run_id_ctx.get()


def check_budget() -> None:
    """当前 run 的硬上限检查；无活动 run 时 no-op。"""
    run_id = current_run_id()
    if run_id is None:
        return
    budget = _registry.get(run_id)
    if budget is not None:
        budget.check_hard()


def record_call(*, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
    """记录当前 run 的一次真实 LLM 调用；无活动 run 时 no-op。"""
    run_id = current_run_id()
    if run_id is None:
        return
    budget = _registry.get(run_id)
    if budget is not None:
        budget.record_call(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)


def get_budget(run_id: str) -> RunBudget | None:
    """查询某 run 的预算状态（Worker 结束后读取 soft_warned 用）。"""
    return _registry.get(run_id)
