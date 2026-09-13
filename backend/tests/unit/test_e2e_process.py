"""e2e_process 进程所有权与信号清理自校验（W1-07，无 Docker 依赖）。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2].parent / "scripts" / "e2e_process.py"
NAME = "e2e-process-selftest"


def _run(*args: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False


@pytest.mark.unit
class TestE2EProcess:
    def test_start_creates_session_leader_and_stop_terminates_tree(self) -> None:
        """启动的进程成为独立会话首进程；stop 后整组（含子进程）退出。"""
        # 起一个会再派生孩子的进程树（sh -c 'sleep 300 & wait'）
        result = _run(
            "start", NAME, "--", "sh", "-c", "sleep 300 & sleep 300 & wait"
        )
        assert result.returncode == 0, result.stderr
        pgid = int((Path(__file__).resolve().parents[2].parent / ".e2e-process" / f"{NAME}.pid").read_text())

        try:
            assert _alive(pgid), "启动后进程组应存活"
            # 组长与子进程同一进程组
            assert os.getpgid(pgid) == pgid
        finally:
            stop = _run("stop", NAME, "--timeout", "5")
            assert stop.returncode == 0, stop.stderr

        assert not _alive(pgid), "stop 后进程组应整体退出"

    def test_stop_without_record_is_noop(self) -> None:
        result = _run("stop", "no-such-process-name")
        assert result.returncode == 0

    def test_stale_pid_record_is_cleaned(self) -> None:
        """陈旧 PID 记录（进程早已不存在）被清理并可重新 start。"""
        pid_file = Path(__file__).resolve().parents[2].parent / ".e2e-process" / f"{NAME}.pid"
        pid_file.parent.mkdir(exist_ok=True)
        # 找一个几乎肯定不存在的 pgid
        stale = 4194300
        pid_file.write_text(str(stale))
        result = _run(
            "start", NAME, "--", "sh", "-c", "sleep 300 & wait"
        )
        assert result.returncode == 0, result.stderr
        new_pgid = int(pid_file.read_text())
        assert new_pgid != stale
        _run("stop", NAME, "--timeout", "5")
        assert not _alive(new_pgid)

    def test_signal_cleanup_does_not_touch_unrelated_processes(self) -> None:
        """stop 只杀记录的进程组——本测试进程自身存活（未被误杀）。"""
        result = _run("start", NAME, "--", "sh", "-c", "sleep 300 & wait")
        assert result.returncode == 0
        stop = _run("stop", NAME, "--timeout", "5")
        assert stop.returncode == 0
        # 能走到这里 = pytest 进程未被误杀；确认记录文件已清
        assert os.getpid() > 1
        assert not (
            Path(__file__).resolve().parents[2].parent / ".e2e-process" / f"{NAME}.pid"
        ).exists()
