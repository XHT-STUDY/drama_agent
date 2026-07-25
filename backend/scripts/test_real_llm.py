#!/usr/bin/env python3
"""真实 LLM 测试脚本。

用途：在不启动完整服务的情况下，用真实 LLM API 测试单个 Skill。

使用方式：
    cd backend
    uv run python scripts/test_real_llm.py --skill requirement --idea "足球少年逆袭"
    uv run python scripts/test_real_llm.py --skill story_bible --golden requirement_football
    uv run python scripts/test_real_llm.py --skill outline --golden story_bible_football
    uv run python scripts/test_real_llm.py --skill summarize_episode

环境要求：
    .env 中 LLM_API_BASE 和 LLM_API_KEY 必须已配置。
"""

from __future__ import annotations

import argparse
import asyncio
import json as _json
import sys
from pathlib import Path
from typing import Any

# 确保 backend 在 sys.path 中
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


# ========================================================================
# 命令行参数
# ========================================================================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="DramaAgent 真实 LLM Skill 测试",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  uv run python scripts/test_real_llm.py --skill requirement --idea "足球少年逆袭"
  uv run python scripts/test_real_llm.py --skill story_bible --golden requirement_football
  uv run python scripts/test_real_llm.py --skill outline --golden story_bible_football
  uv run python scripts/test_real_llm.py --skill write_episode --golden outline_football_10 --episode 1
  uv run python scripts/test_real_llm.py --skill summarize_episode
        """,
    )
    parser.add_argument(
        "--skill",
        choices=[
            "requirement",
            "story_bible",
            "outline",
            "write_episode",
            "summarize_episode",
        ],
        default="requirement",
        help="要测试的 Skill 名称（默认: requirement）",
    )
    parser.add_argument(
        "--idea",
        type=str,
        default="",
        help="创作 Idea 文本（仅 requirement skill 使用）",
    )
    parser.add_argument(
        "--golden",
        type=str,
        default="",
        help="Golden Fixture 名称（不含 .json 后缀），用于复用已有测试数据",
    )
    parser.add_argument(
        "--episode",
        type=int,
        default=1,
        help="目标集号（仅 write_episode skill 使用，默认: 1）",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="",
        help="模型名（覆盖 .env 中的配置）",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="",
        help="输出文件路径（默认打印到 stdout）",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.7,
        help="LLM temperature（默认: 0.7）",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=4096,
        help="最大输出 token 数（默认: 4096）",
    )
    return parser.parse_args()


# ========================================================================
# Golden Fixture 加载
# ========================================================================


def load_golden(name: str) -> dict[str, Any] | None:
    """从 tests/golden/ 加载 fixture JSON 文件。

    Args:
        name: fixture 文件名（不含 .json 后缀）

    Returns:
        解析后的 dict；文件不存在时返回 None。
    """
    golden_dir = _BACKEND_DIR / "tests" / "golden"
    path = golden_dir / f"{name}.json"
    if not path.exists():
        print(f"[WARN] Golden fixture 不存在: {path}")
        return None
    with open(path, encoding="utf-8") as f:
        data = _json.load(f)

    # golden fixture 格式为 {input: ..., expected_output: ...}，
    # 返回 expected_output 作为数据体；若不存在则返回原始 data
    if isinstance(data, dict) and "expected_output" in data:
        return data["expected_output"]
    return data


# ========================================================================
# Skill 测试函数
# ========================================================================


async def test_requirement(agent: Any, prompt_loader: Any, args: argparse.Namespace) -> None:
    """测试 RequirementSkill。"""
    from app.domain.requirement import RequirementInput
    from app.skills.requirement import RequirementSkill

    # 准备输入
    user_input = args.idea or "一个被青训队抛弃的足球少年，偶遇前国脚教练后重新出发，最终逆袭成为职业球员。"

    req_input = RequirementInput(
        user_input=user_input,
        source_type="idea",
    )

    skill = RequirementSkill()
    result = await skill.execute({
        "input": req_input,
        "agent": agent,
        "prompt_loader": prompt_loader,
    })

    print_output(result, args, title="归一化需求")


async def test_story_bible(agent: Any, prompt_loader: Any, args: argparse.Namespace) -> None:
    """测试 StoryBibleSkill。"""
    from app.domain.requirement import NormalizedRequirement
    from app.domain.story_bible import StoryBibleInput
    from app.skills.story_bible import StoryBibleSkill

    # 尝试加载 golden fixture 作为需求
    fixture = load_golden(args.golden or "requirement_football")
    if fixture:
        requirement = NormalizedRequirement.model_validate(fixture)
    else:
        # 使用默认结构
        requirement = NormalizedRequirement(
            title="足球少年逆袭",
            logline="一个被青训队抛弃的足球少年通过努力重返球场",
            genre="都市/逆袭",
            tone=["热血", "励志"],
            world_setting="中国都市足球青训圈",
            protagonist_seed="被遗弃的天才足球少年，17岁",
            conflict_seed="被权威打压后重新证明自己",
            target_episode_count=10,
            must_have=["逆袭", "成长"],
            avoid=["过度暴力"],
            source_type="idea",
        )

    sb_input = StoryBibleInput(
        normalized_requirement=requirement.model_dump(),
        rag_context="",
    )

    skill = StoryBibleSkill()
    result = await skill.execute({
        "input": sb_input,
        "agent": agent,
        "prompt_loader": prompt_loader,
    })

    print_output(result, args, title="StoryBible")


async def test_outline(agent: Any, prompt_loader: Any, args: argparse.Namespace) -> None:
    """测试 OutlineSkill。"""
    from app.domain.outline import OutlineInput
    from app.domain.story_bible import StoryBible
    from app.skills.outline import OutlineSkill

    # 加载 Golden StoryBible 或创建默认
    fixture = load_golden(args.golden or "story_bible_football")
    if fixture:
        story_bible = StoryBible.model_validate(fixture)
    else:
        story_bible = StoryBible.model_validate(load_golden("story_bible_football") or {})

    ol_input = OutlineInput(
        story_bible=story_bible.model_dump(),
        rag_context="",
        outline_count=10,
    )

    skill = OutlineSkill()
    result = await skill.execute({
        "input": ol_input,
        "agent": agent,
        "prompt_loader": prompt_loader,
    })

    print_output(result, args, title="分集大纲")


async def test_write_episode(agent: Any, prompt_loader: Any, args: argparse.Namespace) -> None:
    """测试 EpisodeWriterSkill。"""
    from uuid import uuid4

    from app.domain.script import EpisodeWriterInput
    from app.skills.episode_writer import EpisodeWriterSkill

    # 加载 Golden Outline Set 和 StoryBible
    outline_set = load_golden(args.golden or "outline_football_10")
    story_bible = load_golden("story_bible_football")

    if outline_set and "episodes" in outline_set:
        episodes = outline_set["episodes"]
        ep_idx = max(0, min(args.episode - 1, len(episodes) - 1))
        episode_outline = episodes[ep_idx]
    else:
        episode_outline = {
            "episode_number": args.episode,
            "title": "测试剧本",
            "opening_hook": "开场",
            "objective": "目标",
            "core_conflict": "冲突",
            "key_events": ["事件 1", "事件 2"],
            "payoff": "爽点",
            "ending_hook": "结尾钩子",
            "required_characters": [],
        }

    ew_input = EpisodeWriterInput(
        episode_number=args.episode,
        episode_outline=episode_outline,
        story_bible=story_bible or {},
        previous_summary="（第 1 集无前集）" if args.episode == 1 else "前集摘要...",
        continuity_state="（初始状态）",
        rag_context="",
    )

    skill = EpisodeWriterSkill()
    result = await skill.execute({
        "input": ew_input,
        "agent": agent,
        "prompt_loader": prompt_loader,
        "outline_artifact_id": uuid4(),
    })

    print_output(result, args, title=f"剧本草稿（第 {args.episode} 集）")


async def test_summarize_episode(agent: Any, prompt_loader: Any, args: argparse.Namespace) -> None:
    """测试 SummarizerSkill。"""
    from app.domain.summary import SummaryInput
    from app.skills.summarizer import SummarizerSkill

    # 用 golden fixture 构造输入
    script_draft = load_golden("script_draft_valid") or {
        "title": "测试剧本",
        "episode_number": 1,
        "scenes": [
            {
                "scene_number": 1,
                "location": "足球场",
                "time_of_day": "日",
                "characters": ["林峰"],
                "action": "林峰在球场上奔跑训练",
                "dialogue": [{"speaker": "林峰", "text": "我不会放弃。"}],
            },
            {
                "scene_number": 2,
                "location": "教练办公室",
                "time_of_day": "日",
                "characters": ["林峰", "陈教练"],
                "action": "陈教练告知林峰他被淘汰",
                "dialogue": [
                    {"speaker": "陈教练", "text": "你被淘汰了。"},
                    {"speaker": "林峰", "text": "为什么？"},
                ],
            },
        ],
        "plain_text": "林峰在球场上奔跑训练..." * 10,
    }

    sm_input = SummaryInput(
        episode_number=1,
        script_draft=script_draft,
        continuity_state={},
    )

    skill = SummarizerSkill()
    result = await skill.execute({
        "input": sm_input,
        "agent": agent,
        "prompt_loader": prompt_loader,
    })

    print_output(result, args, title="剧集摘要与连续性数据")


# ========================================================================
# 输出
# ========================================================================


def print_output(data: Any, args: argparse.Namespace, title: str = "") -> None:
    """打印或保存结果。"""
    # 如果 data 是 Pydantic 模型，使用 mode='json' 序列化 UUID 等特殊类型
    if hasattr(data, "model_dump"):
        data = data.model_dump(mode="json")
    output = _json.dumps(data, ensure_ascii=False, indent=2)

    if title:
        print(f"\n{'=' * 60}")
        print(f"  {title}")
        print(f"{'=' * 60}\n")

    if args.output:
        out_path = Path(args.output)
        out_path.write_text(output, encoding="utf-8")
        print(f"结果已保存到: {out_path}")
    else:
        # 截断过长输出（剧本可能很长）
        if len(output) > 4000:
            print(output[:2000])
            print(f"\n... [截断 {len(output) - 4000} 字符] ...\n")
            print(output[-2000:])
        else:
            print(output)


# ========================================================================
# 主入口
# ========================================================================


async def main() -> None:
    args = parse_args()

    # 1. 加载 Settings
    from app.core.config import Settings

    settings = Settings()
    print(f"[INFO] APP_ENV={settings.app_env} LLM_PROVIDER={settings.llm_provider}")
    print(f"[INFO] API_BASE={settings.llm_api_base}")

    if settings.app_env == "test" or settings.llm_provider == "fake":
        print("[ERROR] 当前环境为 test/LLM_PROVIDER=fake，不能使用真实 LLM。")
        print("请修改 .env: APP_ENV=local, LLM_PROVIDER=openai_compatible, 并配置 LLM_API_BASE+LLM_API_KEY。")
        sys.exit(1)

    if not settings.llm_api_base:
        print("[ERROR] LLM_API_BASE 未配置。请在 .env 中设置 API 地址。")
        sys.exit(1)

    # 2. 创建 LLM 客户端
    from app.llm.openai_compatible import OpenAICompatibleLLM

    llm = OpenAICompatibleLLM(settings)
    if args.model:
        llm.default_model = args.model

    try:
        # 3. 创建 Agent
        from app.agents.base import BaseAgent

        agent_name = {
            "requirement": "normalizer",
            "story_bible": "planner",
            "outline": "planner",
            "write_episode": "writer",
            "summarize_episode": "summarizer",
        }.get(args.skill, "writer")

        agent = BaseAgent(name=agent_name, llm=llm)

        # 4. 加载 PromptLoader
        from app.prompts.loader import PromptLoader

        prompt_loader = PromptLoader()

        # 5. 分派 Skill
        skill_map = {
            "requirement": test_requirement,
            "story_bible": test_story_bible,
            "outline": test_outline,
            "write_episode": test_write_episode,
            "summarize_episode": test_summarize_episode,
        }

        handler = skill_map[args.skill]
        await handler(agent, prompt_loader, args)

        # 6. 打印统计
        history = llm.get_call_history()
        if history:
            total_tokens = sum(h.usage.total_tokens for h in history)
            total_ms = sum(h.duration_ms for h in history)
            print(f"\n[STATS] 调用次数: {len(history)} | "
                  f"总 tokens: {total_tokens} | "
                  f"总耗时: {total_ms}ms ({total_ms / 1000:.1f}s)")

    finally:
        await llm.close()


if __name__ == "__main__":
    asyncio.run(main())
