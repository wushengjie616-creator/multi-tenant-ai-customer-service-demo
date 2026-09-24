.PHONY: up down build logs ps migrate test acceptance seed seed-loadtest demo faulttest loadtest loadtest-recorded loadtest-stable loadtest-burst loadtest-finance loadtest-llm-timeout loadtest-ws-500 loadtest-stable-200 loadtest-burst-1000 evaluate clean

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

seed-loadtest: ## 初始化可重复的多租户压测身份池
	docker compose run --rm -e LOADTEST_POOL_SIZE=$${LOADTEST_POOL_SIZE:-500} api python -m scripts.seed_loadtest

test:          ## 运行单元/集成覆盖率门禁 + E2E（自动测试仅用 Mock）
	docker compose build api
	docker compose run --rm -e DEEPSEEK_API_URL=http://mock-llm:8101/v1/chat/completions -e DEEPSEEK_API_KEY=test-key api \
		pytest tests/unit tests/integration --cov=app --cov-report=term --cov-fail-under=70 -q
	@set -e; \
		restore() { docker compose up -d --force-recreate api worker api-replica api-replica-2 >/dev/null; }; \
		trap restore EXIT INT TERM; \
		docker compose -f docker-compose.yml -f docker-compose.test.yml up -d --force-recreate \
			api worker scheduler mock-im mock-llm mock-knowledge mock-platform mock-finance >/dev/null; \
		docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api python -m scripts.seed_data >/dev/null; \
		docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api \
			python -m scripts.import_knowledge --root sample-data/tenants/demo-school --apply >/dev/null; \
		docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api pytest tests/e2e -q

acceptance:    ## 面试交付门禁：测试 + 50 条 LLM 评测 + 健康检查
	$(MAKE) test
	docker compose run --rm -v "$(CURDIR)/evaluation:/app/evaluation" api python -m evaluation.evaluate
	curl -fsS http://localhost:8000/health/ready

demo:          ## 运行消息闭环演示（seed + 发送 + 幂等 + 轮询回复）
	docker compose run --rm api python -m scripts.demo

faulttest:     ## 重启 Redis/NATS/worker 并生成可恢复性证据
	.venv/bin/python -m scripts.fault_matrix --apply --output report/fault-matrix.json

loadtest:      ## 运行可配置 Locust smoke/压测
	LOADTEST_POOL_SIZE=$${LOADTEST_POOL_SIZE:-$${USERS:-10}} $(MAKE) seed-loadtest
	docker compose run --rm --no-deps -e LOADTEST_SCENARIO -e LOADTEST_RATE_PER_USER -e LOADTEST_MESSAGE_CONTENT \
		-e LOADTEST_POOL_SIZE=$${LOADTEST_POOL_SIZE:-$${USERS:-10}} api \
		locust -f loadtests/locustfile.py --headless \
		--host http://api:8000 --users $${USERS:-10} --spawn-rate $${SPAWN_RATE:-2} \
		--run-time $${RUN_TIME:-30s} --only-summary

loadtest-recorded: ## 压测并将 CSV 原始证据写入 report/raw/
	LOADTEST_POOL_SIZE=$${LOADTEST_POOL_SIZE:-$${USERS:-10}} $(MAKE) seed-loadtest
	docker compose run --rm --no-deps -v "$(CURDIR)/report:/app/report" \
		-e LOADTEST_SCENARIO -e LOADTEST_RATE_PER_USER -e LOADTEST_MESSAGE_CONTENT \
		-e LOADTEST_POOL_SIZE=$${LOADTEST_POOL_SIZE:-$${USERS:-10}} api \
		locust -f loadtests/locustfile.py --headless \
		--host http://api:8000 --users $${USERS:-10} --spawn-rate $${SPAWN_RATE:-2} \
		--run-time $${RUN_TIME:-30s} --csv report/raw/$${CSV_PREFIX:-loadtest} \
		--csv-full-history --only-summary

loadtest-stable: ## 面试规模：20 msg/s，持续 60s
	LOADTEST_SCENARIO=stable LOADTEST_RATE_PER_USER=4 USERS=5 SPAWN_RATE=5 RUN_TIME=60s $(MAKE) loadtest

loadtest-burst: ## 面试规模：100 msg/s，持续 15s
	LOADTEST_SCENARIO=burst LOADTEST_RATE_PER_USER=10 USERS=10 SPAWN_RATE=10 RUN_TIME=15s $(MAKE) loadtest

loadtest-finance: ## 题目目标：100 QPS 财务查询，持续 30s
	LOADTEST_SCENARIO=finance LOADTEST_RATE_PER_USER=5 USERS=20 SPAWN_RATE=20 RUN_TIME=30s $(MAKE) loadtest

loadtest-llm-timeout: ## 20% Mock LLM 超时时的 WebSocket 降级链路
	LOADTEST_SCENARIO=llm-timeout USERS=5 SPAWN_RATE=5 RUN_TIME=30s $(MAKE) loadtest

loadtest-ws-500: ## Word 目标：500 个 WebSocket 长连接保持 60s
	LOADTEST_SCENARIO=ws-capacity USERS=500 SPAWN_RATE=100 RUN_TIME=60s CSV_PREFIX=ws500 $(MAKE) loadtest-recorded

loadtest-stable-200: ## Word 目标：200 msg/s 稳定流，持续 5 分钟
	LOADTEST_SCENARIO=stable LOADTEST_RATE_PER_USER=5 LOADTEST_MESSAGE_CONTENT="你好" USERS=40 SPAWN_RATE=40 RUN_TIME=5m CSV_PREFIX=stable200 $(MAKE) loadtest-recorded

loadtest-burst-1000: ## Word 目标：1000 msg/s 突发流，持续 30s
	LOADTEST_SCENARIO=burst LOADTEST_RATE_PER_USER=10 LOADTEST_MESSAGE_CONTENT="你好" USERS=500 SPAWN_RATE=500 RUN_TIME=30s CSV_PREFIX=burst1000 $(MAKE) loadtest-recorded

evaluate:      ## 运行 50 条固定离线评测
	docker compose run --rm -v "$(CURDIR)/evaluation:/app/evaluation" api python -m evaluation.evaluate

clean:         ## 清理容器与数据卷
	docker compose down -v
