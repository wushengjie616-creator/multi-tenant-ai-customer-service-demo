"""健康检查：存活 / 就绪。"""

import asyncio
import nats
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from qdrant_client import AsyncQdrantClient
from sqlalchemy import text

from app.core.logging import get_logger

router = APIRouter(tags=["health"])
log = get_logger(__name__)


async def check_nats(url: str, *, timeout: float = 2.0) -> str:
    """Probe NATS without allowing DNS/connect retries to stall readiness."""
    nc = None
    try:
        async with asyncio.timeout(timeout):
            nc = await nats.connect(
                url,
                allow_reconnect=False,
                max_reconnect_attempts=0,
                connect_timeout=min(timeout, 1.0),
            )
            await nc.close()
        return "ok"
    except TimeoutError:
        return f"error: timeout after {timeout:g}s"
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"
    finally:
        if nc is not None and not nc.is_closed:
            await nc.close()


@router.get("/health/live")
async def live():
    """存活探针：进程能响应即健康。"""
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request):
    """就绪探针：依赖（Postgres / Redis / NATS / Qdrant）全部可用才返回 200。"""
    checks: dict[str, str] = {}

    try:
        async with request.app.state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["postgres"] = f"error: {exc}"

    try:
        await request.app.state.redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {exc}"

    checks["nats"] = await check_nats(request.app.state.settings.nats_url)

    try:
        qdrant = AsyncQdrantClient(
            url=request.app.state.settings.qdrant_url, timeout=2
        )
        await qdrant.get_collections()
        await qdrant.close()
        checks["qdrant"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["qdrant"] = f"error: {exc}"

    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ok" if healthy else "degraded", "checks": checks},
    )
