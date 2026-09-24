.PHONY: up down build logs ps migrate test acceptance seed demo faulttest loadtest loadtest-stable loadtest-burst loadtest-finance loadtest-llm-timeout evaluate clean

up:            ## 一键启动完整演示环境
	docker compose up -d --build

down:          ## 停止并移除容器
	docker compose down

build:         ## 仅构建镜像
	docker compose build

logs:          ## 跟踪所有服务日志
	docker compose logs -f

ps:            ## 查看服务状态
	docker compose ps

migrate:       ## 执行数据库迁移
	docker compose run --rm api alembic upgrade head

seed:          ## 初始化演示数据（租户/用户/会话）
	docker compose run --rm api python -m scripts.seed_data

test:          ## 运行单元/集成覆盖率门禁 + E2E（自动测试仅用 Mock）
	docker compose build api
	docker compose run --rm -e DEEPSEEK_API_URL=http://mock-llm:8101/v1/chat/completions -e DEEPSEEK_API_KEY=test-key api \
		pytest tests/unit tests/integration --cov=app --cov-report=term --cov-fail-under=70 -q
	@set -e; \
		restore() { docker compose up -d --force-recreate api worker >/dev/null; }; \
		trap restore EXIT INT TERM; \
		docker compose -f docker-compose.yml -f docker-compose.test.yml up -d --force-recreate api worker >/dev/null; \
		docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/e2e -q

acceptance:    ## 面试交付门禁：测试 + 50 条 LLM 评测 + 健康检查
	$(MAKE) test
	docker compose run --rm api python -m evaluation.evaluate
	curl -fsS http://localhost:8000/health/ready

demo:          ## 运行消息闭环演示（seed + 发送 + 幂等 + 轮询回复）
	docker compose run --rm api python -m scripts.demo

faulttest:     ## 重启 Redis/NATS/worker 并生成可恢复性证据
	.venv/bin/python -m scripts.fault_matrix --apply --output report/fault-matrix.json

loadtest:      ## 运行可配置 Locust smoke/压测
	docker compose run --rm --no-deps -e LOADTEST_SCENARIO -e LOADTEST_RATE_PER_USER api \
		locust -f loadtests/locustfile.py --headless \
		--host http://api:8000 --users $${USERS:-10} --spawn-rate $${SPAWN_RATE:-2} \
		--run-time $${RUN_TIME:-30s} --only-summary

loadtest-stable: ## 面试规模：20 msg/s，持续 60s
	LOADTEST_SCENARIO=stable LOADTEST_RATE_PER_USER=4 USERS=5 SPAWN_RATE=5 RUN_TIME=60s $(MAKE) loadtest

loadtest-burst: ## 面试规模：100 msg/s，持续 15s
	LOADTEST_SCENARIO=burst LOADTEST_RATE_PER_USER=10 USERS=10 SPAWN_RATE=10 RUN_TIME=15s $(MAKE) loadtest

loadtest-finance: ## 题目目标：100 QPS 财务查询，持续 30s
	LOADTEST_SCENARIO=finance LOADTEST_RATE_PER_USER=5 USERS=20 SPAWN_RATE=20 RUN_TIME=30s $(MAKE) loadtest

loadtest-llm-timeout: ## 20% Mock LLM 超时时的 WebSocket 降级链路
	LOADTEST_SCENARIO=llm-timeout USERS=5 SPAWN_RATE=5 RUN_TIME=30s $(MAKE) loadtest

evaluate:      ## 运行 50 条固定离线评测
	docker compose run --rm api python -m evaluation.evaluate

clean:         ## 清理容器与数据卷
	docker compose down -v
