# DramaAgent Makefile
# 统一开发命令入口 — 详见 docs/DEV_PLAN.md

.PHONY: install lint typecheck test cov ci perf up down migrate migrate-check doctor clean e2e-setup e2e e2e-down

## 安装全部依赖
install:
	@echo "=== 安装后端依赖 (uv) ==="
	cd backend && uv sync
	@echo "=== 安装前端依赖 (pnpm) ==="
	cd frontend && pnpm install
	@echo "=== 安装完成，运行 make doctor 检查环境 ==="

## 代码风格检查
lint:
	@echo "=== 后端 lint (Ruff) ==="
	cd backend && uv run ruff check app/ tests/
	@echo "=== 前端 lint (ESLint) ==="
	cd frontend && pnpm lint

## 类型检查
typecheck:
	@echo "=== 后端类型检查 (mypy) ==="
	cd backend && uv run mypy app/ tests/
	@echo "=== 前端类型检查 (tsc) ==="
	cd frontend && pnpm typecheck

## 运行全部测试
test:
	@echo "=== 后端测试 (pytest) ==="
	cd backend && uv run pytest
	@echo "=== 前端测试 (vitest) ==="
	cd frontend && pnpm test

## 覆盖率门禁（总体 ≥75% + 核心 domain/workflows/artifacts ≥85%）
cov:
	@echo "=== 后端总体覆盖率 (fail_under=75) ==="
	cd backend && uv run pytest --cov=app --cov-report=term-missing --cov-report=html --cov-report=xml
	@echo "=== 核心覆盖率门禁 (domain/workflows/artifacts ≥85%) ==="
	cd backend && uv run coverage report --include="app/domain/*,app/workflows/*,app/artifacts/*" --fail-under=85
	@echo "=== 覆盖率门禁全部通过 ==="

## CI 流水线（lint + typecheck + 覆盖率门禁）
ci: lint typecheck cov
	@echo "=== CI 全部通过 ==="

## 性能测试（需 make up：PostgreSQL + Redis）
## §1.6：普通 API p95<300ms / 100 并发 SSE / 1000 Artifact 查询
perf:
	@echo "=== 性能测试 (make up 需先就绪) ==="
	cd backend && uv run pytest -m performance -v
	@echo "=== 性能测试完成 ==="

## 启动本地基础设施（PostgreSQL + Redis）
up:
	mkdir -p var/uploads var/artifacts
	docker compose up -d
	@echo "=== PostgreSQL + Redis 已启动 ==="
	@echo "=== 运行 make doctor 检查服务状态 ==="

## 停止本地基础设施
down:
	docker compose down
	@echo "=== PostgreSQL + Redis 已停止 ==="

## 应用数据库迁移（alembic upgrade head，幂等）
migrate:
	@echo "=== 应用数据库迁移 ==="
	cd backend && uv run alembic upgrade head
	cd backend && uv run python -m app.cli.checkpoints setup
	@echo "=== 迁移完成 ==="

## 检查数据库迁移是否落后（head 是否已应用）
migrate-check:
	@cd backend && uv run alembic current 2>&1 | grep -q "(head)" \
		&& echo "=== 数据库迁移已是最新 ===" \
		|| (echo "ERROR: 数据库迁移落后，请运行 make migrate" && exit 1)
	@cd backend && uv run python -m app.cli.checkpoints check

## 检查环境健康状态
doctor:
	@echo "=== 检查 Python ==="
	@(python3 --version 2>/dev/null || python --version 2>/dev/null || (echo "ERROR: Python 未安装" && exit 1))
	@echo "=== 检查 Node ==="
	@node --version || (echo "ERROR: Node.js 未安装" && exit 1)
	@echo "=== 检查 uv ==="
	@uv --version || (echo "ERROR: uv 未安装，请运行 pip install uv" && exit 1)
	@echo "=== 检查 pnpm ==="
	@pnpm --version || (echo "ERROR: pnpm 未安装，请运行 npm install -g pnpm" && exit 1)
	@echo "=== 检查 Docker ==="
	@docker --version || (echo "WARN: Docker 未安装，跳过服务健康检查" && exit 0)
	@echo "=== 检查 docker-compose.yml ==="
	@test -f docker-compose.yml || (echo "WARN: docker-compose.yml 不存在" && exit 0)
	@echo "=== 检查 PostgreSQL ==="
	@docker compose exec -T postgres pg_isready -U drama -d drama 2>/dev/null || echo "WARN: PostgreSQL 未运行或未就绪，请运行 make up"
	@echo "=== 检查 Redis ==="
	@docker compose exec -T redis redis-cli ping 2>/dev/null || echo "WARN: Redis 未运行或未就绪，请运行 make up"
	@echo "=== 检查数据库迁移 ==="
	@cd backend && uv run alembic current 2>&1 | grep -q "(head)" \
		&& uv run python -m app.cli.checkpoints check \
		&& echo "OK: 迁移与 checkpoint schema 已就绪" \
		|| echo "WARN: 迁移落后，请运行 make migrate"
	@echo "=== 检查本地运行时目录 ==="
	@(test -d var/uploads && test -d var/artifacts) || echo "WARN: var/uploads 或 var/artifacts 目录不存在，请运行 make up"
	@echo "=== 环境检查完成 ==="

## 安装 Playwright 浏览器（E2E 前置，一次性）
e2e-setup:
	@echo "=== 安装 Playwright Chromium ==="
	cd frontend && pnpm exec playwright install chromium --with-deps
	@echo "=== Playwright 浏览器安装完成 ==="

## 运行 Playwright E2E（全链路 Demo，FakeLLM）
## 用法：make e2e           # 冒烟 1 次
##       make e2e REPEAT=5  # 验收：可重复运行至少 5 次
e2e:
	@bash scripts/e2e.sh --repeat-each=$(REPEAT)

## 停止 E2E 基础设施（PostgreSQL:5433 / Redis:6380）
e2e-down:
	docker compose -f docker-compose.e2e.yml down -v
	@echo "=== E2E 基础设施已停止 ==="

## 清理构建产物与缓存
clean:
	@echo "=== 清理后端 ==="
	rm -rf backend/.mypy_cache backend/.ruff_cache backend/.pytest_cache backend/__pycache__ backend/app/__pycache__ backend/tests/__pycache__
	@echo "=== 清理前端 ==="
	rm -rf frontend/.next frontend/node_modules frontend/out
	@echo "=== 清理完成 ==="
