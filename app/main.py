"""FastAPI 应用入口。

第 1 步：骨架 + 健康检查 + trace 中间件。
第 2 步：消息闭环 — WS /ws + HTTP webhook + 事务型 outbox + 出站推送消费者。
"""

import asyncio
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response
from fastapi.staticfiles import StaticFiles
from app.api import admin, auth, business, demo, health, knowledge, messages, websocket
from app.api.dependencies import authenticate_bearer_token
from app.core import nats as nats_core
from app.core.config import settings
from app.core.database import async_session, engine
from app.core.exceptions import AppError
from app.core.logging import get_logger, set_trace_id, setup_logging
from app.core.metrics import HTTP_LATENCY, HTTP_REQUESTS
from app.core.redis import redis_client
from app.core.tracing import configure_tracing
from app.middleware.rate_limit import allowed as rate_allowed
from app.schemas.event import SCHEMA_VERSION
from app.services.outbox_relay import relay_loop

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(settings.log_level)
    app.state.settings = settings
    app.state.engine = engine
    app.state.redis = redis_client

    nc = await nats_core.connect()
    js = nc.jetstream()
    await nats_core.ensure_stream(js)
    app.state.js = js

    tasks: list[asyncio.Task] = []
    if settings.enable_background_tasks:
        # 出站推送消费者（im.outbound -> WS）
        tasks.append(asyncio.create_task(websocket.run_outbound_consumer()))
        # 发布本进程写入的 im.inbound outbox 事件
        tasks.append(asyncio.create_task(relay_loop(js, async_session)))

    log.info("api started, schema_version=%s", SCHEMA_VERSION)
    yield

    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
    await nc.close()
    await app.state.redis.aclose()
    await engine.dispose()


app = FastAPI(title="education-ai-service", version="0.1.0", lifespan=lifespan)
configure_tracing(app)


@app.middleware("http")
async def authenticated_rate_limit(request: Request, call_next):
    authorization = request.headers.get("authorization", "")
    redis = getattr(request.app.state, "redis", None)
    if redis is not None and authorization.lower().startswith("bearer "):
        try:
            identity = authenticate_bearer_token(authorization.split(" ", 1)[1])
        except AppError:
            identity = None
        if identity and not await rate_allowed(
            redis,
            identity.tenant_id,
            identity.user_id,
            settings.tenant_rate_limit_per_minute,
            settings.user_rate_limit_per_minute,
        ):
            return JSONResponse(status_code=429, content={"error": "RATE_LIMITED", "message": "请求过于频繁，请稍后重试"})
    return await call_next(request)


@app.middleware("http")
async def trace_context(request: Request, call_next):
    started = time.perf_counter()
    trace_id = request.headers.get("X-Trace-Id") or str(uuid.uuid4())
    set_trace_id(trace_id)
    response = await call_next(request)
    response.headers["X-Trace-Id"] = trace_id
    path = request.scope.get("route").path if request.scope.get("route") else request.url.path
    HTTP_REQUESTS.labels(request.method, path, str(response.status_code)).inc()
    HTTP_LATENCY.labels(request.method, path).observe(time.perf_counter() - started)
    return response


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.code, "message": exc.message, "detail": exc.detail},
    )


app.include_router(health.router)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(messages.router)
app.include_router(websocket.router)
app.include_router(business.router)
app.include_router(knowledge.router)
app.include_router(demo.router)
app.mount("/ui", StaticFiles(directory="frontend", html=True), name="ui")


@app.get("/")
async def root():
    return {
        "service": "education-ai-service",
        "version": "0.1.0",
        "schema_version": SCHEMA_VERSION,
    }


@app.get("/metrics", include_in_schema=False)
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
