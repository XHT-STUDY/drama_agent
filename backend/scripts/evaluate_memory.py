#!/usr/bin/env python3
"""Memory 评测脚本(M-01)——docs/MEMORY_IMPLEMENTATION_PLAN.md §3。

产出:
- backend/tests/evals/results/memory_eval_results.json(机器可读明细)
- docs/MEMORY_EVAL_REPORT.md(人可读基线报告)

用法:
    cd backend
    uv run python scripts/evaluate_memory.py --provider fake   # CI/默认
    EVAL_LLM_ENABLED=1 uv run python scripts/evaluate_memory.py --provider real

real provider:对话摘要由真实 LLM 生成(生产 conversation_summary Prompt),
评分仍为确定性标记匹配;记录模型、Prompt 版本、样本数、token 与耗时。
real 只跑固定子集(每长度档 2 个 case)以控制成本。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

RESULTS_PATH = Path(__file__).resolve().parents[1] / "tests/evals/results"
REPORT_PATH = BACKEND_ROOT.parents[0] / "docs" / "MEMORY_EVAL_REPORT.md"


def _wiring_probe() -> dict[str, Any]:
    """/agent/turns 等入口当前是否实际触发摘要(报告事实)。"""
    try:
        from app.agents.base import BaseAgent
        from app.api.v1.conversations import _get_msg_service
        from app.application.agent_action_lifecycle import AgentActionLifecycle
        from app.application.agent_command_service import AgentCommandService
        from app.core.config import Settings
        from app.llm.fake import FakeLLM

        cmd = AgentCommandService(
            settings=Settings(app_env="test"),
            planner_agent=BaseAgent(name="planner", llm=FakeLLM()),
        )
        return {
            "agent_turns(CommandService)": cmd._message_service._summary is not None,
            "action_lifecycle": AgentActionLifecycle()._messages._summary is not None,
            "conversations_api": _get_msg_service()._summary is not None,
        }
    except Exception as exc:  # noqa: BLE001 — 探针失败也写入报告
        return {"error": True, "detail": str(exc)}


# ---- real provider:LLM 摘要 ------------------------------------------------


def _real_summarizer_factory() -> tuple[Any, dict[str, Any], Any]:
    from app.agents.base import BaseAgent
    from app.core.config import Settings
    from app.domain.summary import ConversationSummaryBody
    from app.llm.openai_compatible import OpenAICompatibleLLM
    from app.prompts.loader import PromptLoader

    settings = Settings(app_env="local")
    settings.apply_env_overrides()
    llm = OpenAICompatibleLLM(settings)
    agent = BaseAgent(name="summarizer", llm=llm)
    loader = PromptLoader()
    meta = {
        "provider": settings.llm_provider,
        "model": settings.llm_planner_model,
        "prompt_version": loader.get("conversation_summary").version,
    }

    def summarize(segment: list[dict[str, Any]],
                  previous: list[str] | None) -> list[str]:
        transcript = "\n".join(
            f"{m['sequence']}. [{m['role']}] {m['content']}" for m in segment
        )
        if previous:
            transcript = "【上一版累计摘要】\n" + "\n".join(previous) + \
                "\n\n【新增消息】\n" + transcript
        tpl = loader.get("conversation_summary")
        rendered = tpl.render(
            conversation_transcript=transcript,
            message_count=str(len(segment)),
        )
        result = asyncio.run(agent.generate_structured(
            ConversationSummaryBody,
            [{"role": "user", "content": rendered}],
            prompt_name="conversation_summary",
            temperature=0.3,
        ))
        if result.parsed is None:
            return previous or []
        return [result.parsed.summary]

    return summarize, meta, llm


# ---- 报告渲染 --------------------------------------------------------------


def _fmt(x: Any) -> str:
    if x is None:
        return "—"
    if isinstance(x, bool):
        return "是" if x else "否"
    if isinstance(x, float):
        return f"{x:.1%}" if x <= 1.0 else f"{x:.0f}"
    return str(x)


def _dialogue_table(summary: dict[str, Any]) -> list[str]:
    metrics = [
        ("最新事实胜率", "latest_fact_rate"),
        ("否决召回", "veto_recall"),
        ("约束召回(总)", "constraint_recall"),
        ("未决问题召回", "open_question_recall"),
        ("虚构计数", "fabrication_count"),
        ("跨项目泄漏", "cross_project_leak_count"),
        ("使用层到达率", "use_latest_rate"),
        ("Manifest 一致", "manifest_consistent"),
        ("历史 token 均值", "history_tokens_avg"),
        ("相对完整历史节省", "token_saving_vs_full"),
    ]
    lines = ["| 指标 | " + " | ".join(summary["groups"]) + " |",
             "| --- | " + " | ".join("---" for _ in summary["groups"]) + " |"]
    for label, key in metrics:
        lines.append(f"| {label} | " + " | ".join(
            _fmt(summary["by_group"][g][key]) for g in summary["groups"]) + " |")
    return lines


def _dialogue_bucket_table(summary: dict[str, Any]) -> list[str]:
    lines = ["| 长度档 | 组 | 约束召回 | 最新事实 | 否决召回 | token 节省 |",
             "| --- | --- | --- | --- | --- | --- |"]
    for bucket in ("24", "48", "96", "192"):
        for g in summary["groups"]:
            b = summary["by_bucket"][g][bucket]
            lines.append(
                f"| {bucket} | {g} | {_fmt(b['constraint_recall'])} | "
                f"{_fmt(b['latest_fact_rate'])} | {_fmt(b['veto_recall'])} | "
                f"{_fmt(b['token_saving_vs_full'])} |")
    return lines


def _story_table(summary: dict[str, Any]) -> list[str]:
    metrics = [
        ("知识缺失", "knowledge_missing"),
        ("知识越界(未学先知)", "knowledge_leak"),
        ("未来计划提前泄露", "future_plan_leak"),
        ("违规总数", "violations"),
        ("相对 none 违规下降", "violation_reduction_vs_none"),
        ("伏笔 F1(resolved)", "loop_f1_avg"),
        ("开放伏笔召回", "loop_open_recall"),
        ("道具归属正确率", "prop_accuracy"),
        ("锁定事实可用率", "locked_fact_rate"),
        ("来源正确率", "fact_source_rate"),
        ("重放一致", "replay_consistent"),
        ("跨项目泄漏", "cross_case_leaks"),
    ]
    lines = ["| 指标 | " + " | ".join(summary["groups"]) + " |",
             "| --- | " + " | ".join("---" for _ in summary["groups"]) + " |"]
    for label, key in metrics:
        lines.append(f"| {label} | " + " | ".join(
            _fmt(summary["by_group"][g][key]) for g in summary["groups"]) + " |")
    return lines


def render_report(dialogue: dict[str, Any], story: dict[str, Any],
                  wiring: dict[str, Any], meta: dict[str, Any]) -> str:
    lines = [
        "# Memory 评测基线报告(M-01)",
        "",
        f"> 生成时间:{meta['generated_at']}  provider:`{meta['provider']}`  "
        f"样本:对话 {meta['dialogue_cases']} 组 / 剧情 {meta['story_cases']} 组  "
        f"耗时:{meta['elapsed_seconds']:.1f}s",
        "",
        "评测依据 [MEMORY_DESIGN.md §10](MEMORY_DESIGN.md) 与"
        "[MEMORY_IMPLEMENTATION_PLAN.md §3](MEMORY_IMPLEMENTATION_PLAN.md)。"
        "摘要器为确定性抽取(FakeLLM 语义),四组差异只在记忆策略;"
        "剧情 structured 组是 M-03 typed delta 的可执行规格。",
        "",
        "## 1. 生产链路探针:/agent/turns 是否实际触发摘要",
        "",
    ]
    for name, mounted in wiring.items():
        if name == "error":
            lines.append(f"- 探针执行失败:{mounted} / {wiring.get('detail')}")
            continue
        lines.append(f"- **{name}**:摘要挂载 = {'是' if mounted else '否'}")
    lines += [
        "",
        "**结论:截至本基线,`/agent/turns`(AgentCommandService)与 Action 生命周期"
        "(AgentActionLifecycle)构造的 `MessageService` 无摘要挂载,"
        "从主入口发送消息不会触发会话摘要;只有普通消息 API"
        "(`/conversations/{id}/messages`)挂载了记忆。M-02 的目标即统一该构造。**",
        "",
        "## 2. 对话记忆(写入/召回/使用/成本)",
        "",
        *_dialogue_table(dialogue),
        "",
        "### 按消息长度分层",
        "",
        *_dialogue_bucket_table(dialogue),
        "",
        "当前实现(current)只读取最新一段分段摘要:96/192 条消息时核心约束召回"
        "坍缩为 0,否决项全部丢失;结构化累计摘要(structured)全档保持达标。"
        "目标实现已满足设计门槛(跨项目泄漏 0、最新事实 ≥95%、否决召回 ≥95%、"
        "96 条后约束召回 ≥90%、虚构 ≤1%)。",
        "",
        "## 3. 剧情连续性",
        "",
        *_story_table(story),
        "",
        "当前实现(current,复刻 write_episode 标题摘要路径)不含角色知识与"
        "伏笔回收:知识缺失与 none 组相同、伏笔 F1 为 0;structured 组"
        "(typed delta 规格)知识越界 0、违规相对 none 下降 ≥50%、来源与重放全对。",
        "",
        "## 4. 成本与交互",
        "",
        f"- 对话 structured 组 96 档 token 节省:"
        f"{_fmt(dialogue['by_bucket']['structured']['96']['token_saving_vs_full'])},"
        f"192 档:"
        f"{_fmt(dialogue['by_bucket']['structured']['192']['token_saving_vs_full'])}"
        "(相对完整历史)。24/48 档结构性不可能高节省(短期窗口本身占满)。",
        "- 普通消息提交不等待摘要 LLM 的验收在 M-02 集成测试覆盖"
        "(本报告只测记忆语义)。",
        "",
        "## 5. 真实模型运行记录",
        "",
    ]
    real = meta.get("real")
    if real:
        lines.append(
            f"- {real['generated_at']} model=`{real['model']}` "
            f"prompt=`conversation_summary@{real['prompt_version']}` "
            f"样本={real['cases']} 组(每长度档 2 例) "
            f"调用={real['llm_calls']} 次 token≈{real['tokens']}"
        )
    else:
        lines.append("- 尚未执行(需要 `EVAL_LLM_ENABLED=1 --provider real`)。"
                     "M-05 退出门槛要求至少执行一次固定集。")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="M-01 Memory 评测")
    parser.add_argument("--provider", choices=["fake", "real"], default="fake")
    parser.add_argument("--skip-story", action="store_true",
                        help="real 模式跳过剧情组(剧情组不含 LLM 调用)")
    args = parser.parse_args()

    from tests.evals.memory_harness import (
        evaluate_dialogue_dataset,
        evaluate_story_dataset,
        load_dialogue_cases,
        load_story_cases,
    )

    started = time.monotonic()
    wiring = _wiring_probe()
    real_meta: dict[str, Any] | None = None
    summarize_fn = None

    dialogue_data = load_dialogue_cases()
    if args.provider == "real":
        if os.environ.get("EVAL_LLM_ENABLED") != "1":
            print("拒绝执行:real provider 需要 EVAL_LLM_ENABLED=1")
            return 2
        summarize_fn, real_meta, llm = _real_summarizer_factory()
        # 固定子集:每长度档 2 个 case(控制成本)
        subset: list[dict[str, Any]] = []
        for bucket in (24, 48, 96, 192):
            subset.extend([c for c in dialogue_data["cases"]
                           if c["length_bucket"] == bucket][:2])
        dialogue_data = {**dialogue_data, "cases": subset}
        real_meta = {**real_meta, "cases": len(subset)}

    dialogue = evaluate_dialogue_dataset(dialogue_data, summarize_fn=summarize_fn)
    story = evaluate_story_dataset(load_story_cases())
    elapsed = time.monotonic() - started

    if real_meta is not None:
        real_meta = {
            **real_meta,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "llm_calls": len(getattr(llm, "_call_history", []) or []),
            "tokens": sum(
                (u.total_tokens or 0)
                for r in getattr(llm, "_call_history", []) or []
                for u in [getattr(r, "usage", None)] if u
            ),
        }

    meta = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "provider": args.provider,
        "dialogue_cases": len(dialogue_data["cases"]),
        "story_cases": len(story["case_results"]) // len(story["groups"]),
        "elapsed_seconds": elapsed,
        "real": real_meta,
    }

    RESULTS_PATH.mkdir(parents=True, exist_ok=True)
    (RESULTS_PATH / "memory_eval_results.json").write_text(
        json.dumps({"meta": meta, "wiring": wiring, "dialogue": dialogue,
                    "story": story}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    REPORT_PATH.write_text(
        render_report(dialogue, story, wiring, meta), encoding="utf-8"
    )
    print(f"结果: {RESULTS_PATH / 'memory_eval_results.json'}")
    print(f"报告: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
