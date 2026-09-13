#!/usr/bin/env bash
# ============================================================
# DramaAgent E2E 编排 (H-07)
#
# 起隔离基础设施（PostgreSQL:5433 / Redis:6380）→ 迁移 e2e 库 →
# 以 FakeLLM + 低分场景起后端(8010) → 构建并启动前端(3100) →
# 运行 Playwright 全链路 Demo（可选 --repeat-each=N）→ 清理。
#
# 用法：
#   scripts/e2e.sh [--repeat-each=N] [--no-build]
#
# 验收要求：可重复运行至少 5 次（make e2e REPEAT=5）。
# 注意：E2E 数据库 drama_e2e 每次运行前 drop + 重建，保证确定性。
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COMPOSE_FILE="docker-compose.e2e.yml"
REPEAT="${REPEAT:-1}"
BUILD=1
for arg in "$@"; do
  case "$arg" in
    --repeat-each=*) REPEAT="${arg#*=}"; REPEAT="${REPEAT:-1}" ;;
    --no-build) BUILD=0 ;;
  esac
done

BACKEND_PORT=8010
FRONTEND_PORT=3100
E2E_DB_URL="postgresql+asyncpg://drama:drama@localhost:5433/drama_e2e"
E2E_DB_URL_SYNC="postgresql://drama:drama@localhost:5433/drama_e2e"
E2E_REDIS_URL="redis://localhost:6380/0"

BACKEND_PID=""
FRONTEND_PID=""
STARTED=0

cleanup() {
  echo "=== 清理 E2E 进程 ==="
  if [ -f "$ROOT/.e2e-process/frontend.pid" ]; then
    # e2e_process.py 启动：按记录的进程组 TERM→限时 KILL（只清本轮进程）
    (cd "$ROOT" && python3 scripts/e2e_process.py stop frontend --timeout 10)
  else
    [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null || true
    if [ -n "$FRONTEND_PID" ]; then
      kill -- "-$FRONTEND_PID" 2>/dev/null || kill "$FRONTEND_PID" 2>/dev/null || true
    fi
    wait "$BACKEND_PID" 2>/dev/null || true
    wait "$FRONTEND_PID" 2>/dev/null || true
  fi
  echo "=== 清理 E2E 基础设施 ==="
  docker compose -f "$COMPOSE_FILE" down -v 2>/dev/null || true
}
trap cleanup EXIT

echo "=== [1/6] 启动 E2E 基础设施（PostgreSQL:5433 / Redis:6380） ==="
docker compose -f "$COMPOSE_FILE" up -d --wait

echo "=== [2/6] 重建 e2e 数据库并执行迁移 ==="
docker compose -f "$COMPOSE_FILE" exec -T postgres psql -U drama -d postgres -c "DROP DATABASE IF EXISTS drama_e2e WITH (FORCE);" >/dev/null
docker compose -f "$COMPOSE_FILE" exec -T postgres psql -U drama -d postgres -c "CREATE DATABASE drama_e2e;" >/dev/null
(
  cd backend
  APP_ENV=test \
    DATABASE_URL="$E2E_DB_URL" \
    DATABASE_URL_SYNC="$E2E_DB_URL_SYNC" \
    uv run alembic upgrade head
  # LangGraph PostgreSQL saver 表（与 make migrate 等价）
  DATABASE_URL="$E2E_DB_URL" \
    uv run python -m app.cli.checkpoints setup
)

echo "=== [3/6] 启动后端（FakeLLM + FAKE_LLM_SCENARIO=agent_e2e，端口 $BACKEND_PORT） ==="
(
  cd backend
  APP_ENV=test \
    FAKE_LLM_SCENARIO=agent_e2e \
    DATABASE_URL="$E2E_DB_URL" \
    DATABASE_URL_SYNC="$E2E_DB_URL_SYNC" \
    REDIS_URL="$E2E_REDIS_URL" \
    uv run uvicorn "app.main:create_app" --factory --host 127.0.0.1 --port "$BACKEND_PORT" --log-level warning
) &
BACKEND_PID=$!

# 等待后端就绪
for i in $(seq 1 60); do
  if curl -fsS "http://localhost:$BACKEND_PORT/api/v1/health/live" >/dev/null 2>&1; then
    echo "后端已就绪 (${i}0ms 内)"
    break
  fi
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "ERROR: 后端进程意外退出" >&2
    exit 1
  fi
  sleep 0.5
done

if [ "$BUILD" = "1" ]; then
  echo "=== [4/6] 构建前端（NEXT_PUBLIC_API_BASE=$BACKEND_PORT） ==="
  (
    cd frontend
    NEXT_PUBLIC_API_BASE="http://localhost:$BACKEND_PORT/api/v1" \
    NEXT_PUBLIC_AGENT_WORKSPACE_ENABLED=true pnpm build
  )
else
  echo "=== [4/6] 跳过前端构建（--no-build，复用 .next） ==="
fi

echo "=== [5/6] 启动前端（端口 $FRONTEND_PORT） ==="
# 独立进程组：cleanup 组杀 pnpm→node→next-server 整棵树，否则 kill 包装
# 进程后 next-server 孤儿继续占用端口（EADDRINUSE 连环失败）。
# W1-07：setsid 在 macOS 不存在——有 setsid 用原快路径，否则回退
# scripts/e2e_process.py（Python 标准库 start_new_session，语义等价）。
if command -v setsid >/dev/null 2>&1; then
  setsid bash -c '
    cd frontend
    NEXT_PUBLIC_API_BASE="http://localhost:'"$BACKEND_PORT"'/api/v1" \
    NEXT_PUBLIC_AGENT_WORKSPACE_ENABLED=true exec pnpm exec next start -p "'"$FRONTEND_PORT"'"
  ' &
  FRONTEND_PID=$!
else
  echo "  （无 setsid——使用 e2e_process.py 跨平台进程组管理）"
  (cd "$ROOT" && python3 scripts/e2e_process.py start frontend -- \
    bash -c 'cd frontend && NEXT_PUBLIC_API_BASE="http://localhost:'"$BACKEND_PORT"'/api/v1" NEXT_PUBLIC_AGENT_WORKSPACE_ENABLED=true exec pnpm exec next start -p "'"$FRONTEND_PORT"'"')
  FRONTEND_PID=$(cat "$ROOT/.e2e-process/frontend.pid")
fi

# 等待前端就绪
for i in $(seq 1 60); do
  if curl -fsS "http://localhost:$FRONTEND_PORT" >/dev/null 2>&1; then
    echo "前端已就绪"
    break
  fi
  if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
    echo "ERROR: 前端进程意外退出" >&2
    exit 1
  fi
  sleep 0.5
done

echo "=== [6/6] 运行 Playwright E2E（repeat-each=$REPEAT） ==="

# WSL 环境缺 Chromium 系统库时的临时库目录（var/pw-libs，见 TROUBLESHOOTING）
# 存在则注入 LD_LIBRARY_PATH；普通 Linux 无此目录，不影响。
PW_LIBS="$ROOT/var/pw-libs/usr/lib/x86_64-linux-gnu"
if [ -d "$PW_LIBS" ]; then
  export LD_LIBRARY_PATH="$PW_LIBS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  echo "  注入 LD_LIBRARY_PATH=$PW_LIBS（WSL Chromium 系统库 workaround）"
fi

cd frontend
pnpm exec playwright test --config ../e2e/playwright.config.ts --repeat-each="$REPEAT"

echo "=== E2E 全部通过 ==="
STARTED=1
