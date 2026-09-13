#!/usr/bin/env python3
"""E2E 进程启动/清理助手（W1-07，跨平台）。

setsid 仅在 Linux util-linux 存在，macOS 没有——用 Python 标准库
subprocess 的 start_new_session 达成同样的"独立进程组"语义：
- start_new_session=True：子进程成为新会话首进程（等价 setsid）；
- 进程组粒度清理：先 TERM 后限时 KILL，只杀本轮启动的进程组，
  绝不 pkill/按端口杀，用户自己的开发服务不受影响。

用法：
    python3 scripts/e2e_process.py start <name> -- <command...>
    # 启动后台进程，PID 写入 .e2e-process/<name>.pid
    python3 scripts/e2e_process.py stop <name> [--timeout 10]
    # 按记录的进程组 TERM → 等 timeout 秒 → 仍存活则 KILL
"""

from __future__ import annotations

import argparse
import os
import pathlib
import signal
import subprocess
import sys
import time

PID_DIR = pathlib.Path(__file__).resolve().parent.parent / ".e2e-process"


def _pid_file(name: str) -> pathlib.Path:
    PID_DIR.mkdir(exist_ok=True)
    return PID_DIR / f"{name}.pid"


def start(name: str, command: list[str]) -> int:
    pid_file = _pid_file(name)
    if pid_file.exists():
        existing = int(pid_file.read_text().strip() or 0)
        try:
            os.killpg(existing, 0)
            print(f"进程 {name} 已在运行（pgid={existing}）", file=sys.stderr)
            return 0
        except ProcessLookupError:
            pid_file.unlink()  # 陈旧记录

    proc = subprocess.Popen(
        command,
        start_new_session=True,  # 跨平台的 setsid 等价物
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    # 记录进程组 ID（= 子进程 PID，因 start_new_session 使其成为组长）
    pid_file.write_text(str(proc.pid))
    print(f"已启动 {name}: pgid={proc.pid}")
    return 0


def stop(name: str, timeout: float = 10.0) -> int:
    pid_file = _pid_file(name)
    if not pid_file.exists():
        print(f"无 {name} 的运行记录", file=sys.stderr)
        return 0
    pgid = int(pid_file.read_text().strip() or 0)
    pid_file.unlink()
    if pgid <= 1:
        return 0
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return 0
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            print(f"已停止 {name}（pgid={pgid}，TERM）")
            return 0
        time.sleep(0.2)
    try:
        os.killpg(pgid, signal.SIGKILL)
        print(f"已强杀 {name}（pgid={pgid}，TERM 超时）")
    except ProcessLookupError:
        pass
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_start = sub.add_parser("start")
    p_start.add_argument("name")
    p_start.add_argument("command", nargs=argparse.REMAINDER)
    p_stop = sub.add_parser("stop")
    p_stop.add_argument("name")
    p_stop.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    if args.cmd == "start":
        command = args.command
        if command and command[0] == "--":
            command = command[1:]
        if not command:
            parser.error("start 需要命令")
        return start(args.name, command)
    return stop(args.name, timeout=args.timeout)


if __name__ == "__main__":
    sys.exit(main())
