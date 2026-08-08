#!/usr/bin/env python3
"""真实 LLM 评估 Rubric Smoke 脚本 (E-05)。

对 high / medium / low 三个固定剧本重复调用真实 evaluator，
输出各维度分数的均值 / 标准差与问题交集，用于人工诊断评估稳定性。
**不进 CI**——真实 LLM 调用成本高、结果不幂等。

用法：
    cd backend
    uv run python scripts/evaluate_rubric_smoke.py                # 全部 case
    uv run python scripts/evaluate_rubric_smoke.py --case low     # 指定 case
    uv run python scripts/evaluate_rubric_smoke.py --rounds 3 --model qwen3.7-plus

环境要求：
    .env 中 LLM_API_BASE 和 LLM_API_KEY 已配置（脚本不打印密钥）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from pathlib import Path
from typing import Any

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_CASES_DIR = _BACKEND_DIR / "tests" / "golden" / "evaluation_cases"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="DramaAgent 真实 LLM 评估 Rubric Smoke",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  uv run python scripts/evaluate_rubric_smoke.py
  uv run python scripts/evaluate_rubric_smoke.py --case low --rounds 3
        """,
    )
    parser.add_argument(
        "--case",
        choices=["high", "medium", "low"],
        default="",
        help="指定要评估的 case（默认: 全部三个）",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=3,
        help="每个 case 的重复评估次数（默认: 3）",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="",
        help="模型名（覆盖 .env 中的配置）",
    )
    return parser.parse_args()


def load_case(name: str) -> dict[str, Any]:
    """加载 case fixture。"""
    path = _CASES_DIR / f"{name}.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


async def evaluate_case(
    name: str,
    rounds: int,
    agent: Any,
    prompt_loader: Any,
    model: str,
) -> dict[str, Any]:
    """对单个 case 重复评估，返回维度分统计与问题交集。"""
    from uuid import UUID

    from app.domain.evaluation import EvaluationInput
    from app.domain.script import ScriptDraft
    from app.skills.evaluator import EvaluationSkill

    case = load_case(name)
    script = ScriptDraft.model_validate(case["script_draft"])
    skill = EvaluationSkill()
    sid = UUID("00000000-0000-0000-0000-000000000000")

    dim_scores: dict[str, list[int]] = {}
    all_issues: dict[str, list[str]] = {}
    overalls: list[float] = []
    need_revisions: list[bool] = []

    for r in range(1, rounds + 1):
        print(f"  [{name}] 第 {r}/{rounds} 轮评估中…")
        ev_input = EvaluationInput(
            episode_number=1,
            script_draft=script,
            episode_outline={},
            story_bible={},
        )
        report = await skill.execute(
            {
                "input": ev_input,
                "agent": agent,
                "prompt_loader": prompt_loader,
                "script_artifact_id": sid,
            }
        )
        for dim, score in report.dimension_scores.items():
            dim_scores.setdefault(dim.value, []).append(score)
        for issue in report.issues:
            all_issues.setdefault(issue.dimension.value, []).append(issue.diagnosis[:40])
        overalls.append(report.overall_score)
        need_revisions.append(report.need_revision)

    # 统计
    stats = {
        "case": name,
        "model": model or "default",
        "prompt_version": prompt_loader.get("evaluate_episode").version,
        "rubric_version": report.rubric_version,
        "rounds": rounds,
        "overall": {
            "mean": round(statistics.mean(overalls), 1),
            "std": round(statistics.stdev(overalls), 1) if rounds > 1 else 0.0,
            "all": overalls,
        },
        "need_revision": need_revisions,
        "dimensions": {
            dim: {
                "mean": round(statistics.mean(vals), 1),
                "std": round(statistics.stdev(vals), 1) if rounds > 1 else 0.0,
            }
            for dim, vals in dim_scores.items()
        },
        # 问题交集：全部轮次都出现的问题维度
        "issue_intersection": [
            dim for dim, diags in all_issues.items() if len(set(diags)) == len(diags)
        ],
    }
    return stats


async def main() -> None:
    args = parse_args()

    from app.core.config import Settings

    settings = Settings()
    print(f"[INFO] APP_ENV={settings.app_env} LLM_PROVIDER={settings.llm_provider}")
    if settings.app_env == "test" or settings.llm_provider == "fake":
        print("[ERROR] 当前环境为 test/fake，不能使用真实 LLM。请配置 .env 后重试。")
        sys.exit(1)
    if not settings.llm_api_base:
        print("[ERROR] LLM_API_BASE 未配置。请在 .env 中设置。")
        sys.exit(1)

    from app.llm.openai_compatible import OpenAICompatibleLLM

    llm = OpenAICompatibleLLM(settings)
    if args.model:
        llm.default_model = args.model

    try:
        from app.agents.base import BaseAgent
        from app.prompts.loader import PromptLoader

        agent = BaseAgent(name="evaluator", llm=llm)
        prompt_loader = PromptLoader()

        cases = ["high", "medium", "low"] if not args.case else [args.case]
        results = []
        for name in cases:
            print(f"\n{'=' * 60}\n  评估 case: {name}\n{'=' * 60}")
            stats = await evaluate_case(name, args.rounds, agent, prompt_loader, args.model)
            results.append(stats)

        print(f"\n\n{'#' * 60}\n  评估稳定性汇总\n{'#' * 60}")
        for stats in results:
            print(f"\n--- {stats['case']} ({stats['rounds']} 轮) ---")
            print(f"  model/prompt/rubric: {stats['model']} / "
                  f"{stats['prompt_version']} / {stats['rubric_version']}")
            print(f"  overall: mean={stats['overall']['mean']} std={stats['overall']['std']}")
            print(f"  need_revision 序列: {stats['need_revision']}")
            print("  维度分 (mean ± std):")
            for dim, d in sorted(stats["dimensions"].items()):
                print(f"    {dim:20s} {d['mean']:>6} ± {d['std']:<6}")
            print(f"  问题交集维度: {stats['issue_intersection'] or '无'}")
            # 区分模型判断与确定性指标
            print("  [模型判断] dimension_scores / [确定性指标] overall & need_revision")

        history = llm.get_call_history()
        if history:
            total_tokens = sum(h.usage.total_tokens for h in history)
            total_ms = sum(h.duration_ms for h in history)
            print(f"\n[STATS] 调用次数: {len(history)} | 总 tokens: {total_tokens} | "
                  f"总耗时: {total_ms / 1000:.1f}s")
    finally:
        await llm.close()


if __name__ == "__main__":
    asyncio.run(main())
