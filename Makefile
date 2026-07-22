# DramaAgent Makefile
# 统一开发命令入口 — 详见 docs/DEV_PLAN.md

.PHONY: install lint typecheck test ci up down doctor clean

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

## CI 流水线（lint + typecheck + test）
ci: lint typecheck test
	@echo "=== CI 全部通过 ==="

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

## 检查环境健康状态
doctor:
	@echo "=== 检查 Python ==="
	@python --version || (echo "ERROR: Python 未安装" && exit 1)
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
	@echo "=== 检查本地运行时目录 ==="
	@(test -d var/uploads && test -d var/artifacts) || echo "WARN: var/uploads 或 var/artifacts 目录不存在，请运行 make up"
	@echo "=== 环境检查完成 ==="

## 清理构建产物与缓存
clean:
	@echo "=== 清理后端 ==="
	rm -rf backend/.mypy_cache backend/.ruff_cache backend/.pytest_cache backend/__pycache__ backend/app/__pycache__ backend/tests/__pycache__
	@echo "=== 清理前端 ==="
	rm -rf frontend/.next frontend/node_modules frontend/out
	@echo "=== 清理完成 ==="
