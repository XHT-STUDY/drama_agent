"""知识库摄取与管理 CLI（D-02）。

用法：
    uv run python -m app.cli.knowledge ingest <path> [--corpus-version VERSION]
    uv run python -m app.cli.knowledge status

说明：
- ingest 幂等：相同 document_hash 跳过、变更只重建变化 chunk、源文件删除不物理删除线上记录；
- test 环境拒绝执行真实摄取（与 scripts/evaluate_rubric_smoke.py 一致）；
- status 为只读查询，可在任意环境运行。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import load_settings
from app.db.repositories.knowledge import KnowledgeRepository
from app.rag.chunker import chunk_document
from app.rag.loader import (
    discover_knowledge_files,
    load_knowledge_file,
)
from app.rag.models import load_corpus_version

_PROG = "knowledge"


def _build_parser() -> argparse.ArgumentParser:
    """构造子命令解析器：ingest / status。"""
    parser = argparse.ArgumentParser(
        prog=_PROG,
        description="DramaAgent 知识库摄取与管理",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="摄取知识文档（幂等）")
    ingest.add_argument("path", help="语料目录或单个知识文档路径")
    ingest.add_argument(
        "--corpus-version",
        default=None,
        help="覆盖语料版本（默认读取 knowledge/VERSION）",
    )

    sub.add_parser("status", help="查看文档/块计数与语料版本")
    return parser


async def _run_ingest(path: str, corpus_version: str | None) -> int:
    """执行 ingest：加载 → 切块 → 幂等写入。返回进程退出码。"""
    settings = load_settings()
    if settings.app_env == "test":
        print("拒绝执行：test 环境禁止真实摄取。", file=sys.stderr)
        return 1

    corpus = corpus_version or load_corpus_version()
    target = Path(path)
    if not target.exists():
        print(f"路径不存在: {target}", file=sys.stderr)
        return 1
    files = (
        discover_knowledge_files(target)
        if target.is_dir()
        else [target]
    )
    if not files:
        print("未发现可摄取的知识文档（支持 .md / .json，跳过 README/VERSION/rubric）。")
        return 0

    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    created = updated = skipped = failed = 0
    try:
        async with session_factory() as session:
            repo = KnowledgeRepository(session)
            for file_path in files:
                try:
                    loaded = load_knowledge_file(file_path)
                    chunks = chunk_document(loaded.content)
                except Exception as e:  # 元数据/切块失败仅跳过该文件
                    failed += 1
                    print(f"  ✗ {file_path.name}: {e}", file=sys.stderr)
                    continue
                _doc, is_created, changed = await repo.ingest_document(
                    loaded, chunks, corpus_version=corpus
                )
                if is_created:
                    created += 1
                elif changed:
                    updated += 1
                else:
                    skipped += 1
            await session.commit()
    finally:
        await engine.dispose()

    print(
        f"摄取完成 [corpus={corpus}]：新增 {created}，更新 {updated}，"
        f"跳过 {skipped}，失败 {failed}（共 {len(files)} 个文件）"
    )
    return 0 if failed == 0 else 1


async def _run_status() -> int:
    """执行 status：输出文档/块计数与语料版本。"""
    settings = load_settings()
    engine = create_async_engine(settings.database_url)
    try:
        async with async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )() as session:
            repo = KnowledgeRepository(session)
            doc_count = await repo.count_documents()
            chunk_count = await repo.count_chunks()
    finally:
        await engine.dispose()
    print(f"语料版本: {load_corpus_version()}")
    print(f"文档数:   {doc_count}")
    print(f"Chunk 数: {chunk_count}")
    return 0


async def _main(argv: list[str] | None = None) -> int:
    """命令分发入口。"""
    args = _build_parser().parse_args(argv)
    if args.command == "ingest":
        return await _run_ingest(args.path, args.corpus_version)
    if args.command == "status":
        return await _run_status()
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
