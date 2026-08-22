"""Checkpoint schema 运维 CLI。

用法：
    python -m app.cli.checkpoints setup
    python -m app.cli.checkpoints check
"""

from __future__ import annotations

import argparse
import asyncio

from app.core.config import load_settings
from app.workflows.persistence import (
    checkpoint_schema_ready,
    setup_checkpoint_schema,
    verify_checkpoint_read_write,
)


async def _run(command: str) -> None:
    settings = load_settings()
    if command == "setup":
        await setup_checkpoint_schema(settings)
    if not await checkpoint_schema_ready(settings):
        raise RuntimeError("LangGraph checkpoint schema 未初始化")
    await verify_checkpoint_read_write(settings)
    print("checkpoint schema read/write OK")


def main() -> None:
    parser = argparse.ArgumentParser(description="管理 LangGraph checkpoint schema")
    parser.add_argument("command", choices=("setup", "check"))
    args = parser.parse_args()
    asyncio.run(_run(args.command))


if __name__ == "__main__":
    main()
