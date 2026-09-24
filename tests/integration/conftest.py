"""集成测试基座：独立测试库 + 表结构 + HTTP 客户端 + 造数助手。

必须运行在 compose 网络内（`docker compose run --rm api pytest tests/integration`），
本机无 postgres 时不应运行本目录测试。
"""

import os
import uuid

import pytest_asyncio
from sqlalchemy.engine import make_url

# ---------------------------------------------------------------------------
# 1) 在导入 app 之前，把 DATABASE_URL 指向独立测试库（<db>_test），
#    确保 app.core.database 与测试共享同一套引擎/连接池。
# ---------------------------------------------------------------------------


def _test_urls() -> tuple[str, str, str]:
    async_base = os.environ.get(
        "DATABASE_URL", "postgresql+asyncpg://eduai:eduai_password@postgres:5432/eduai"
    )
    sync_base = os.environ.get(
        "DATABASE_URL_SYNC", "postgresql+psycopg2://eduai:eduai_password@postgres:5432/eduai"
    )
    au = make_url(async_base)
    su = make_url(sync_base)
    test_db = f"{au.database}_test"
    # 注意：不能用 str(URL)，SQLAlchemy 2.x 的 __str__ 会把密码掩码成 ***，
    # 导致 app 引擎与 psycopg2 用错密码。改用 render_as_string(hide_password=False)。
    return (
        au.set(database=test_db).render_as_string(hide_password=False),
        su.set(database=test_db).render_as_string(hide_password=False),
        test_db,
    )


_ASYNC_URL, _SYNC_URL, _TEST_DB = _test_urls()
os.environ["DATABASE_URL"] = _ASYNC_URL
os.environ["DATABASE_URL_SYNC"] = _SYNC_URL


def _ensure_test_database() -> None:
    """用 psycopg2 连 postgres 维护库，确保 <db>_test 存在。"""
    import psycopg2

    u = make_url(_SYNC_URL)
    conn = psycopg2.connect(
        host=u.host,
        port=u.port or 5432,
        user=u.username,
        password=u.password,
        dbname="postgres",
    )
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (_TEST_DB,))
            if cur.fetchone() is None:
                cur.execute(f'CREATE DATABASE "{_TEST_DB}"')
    finally:
        conn.close()


_ensure_test_database()

# ---------------------------------------------------------------------------
# 2) 之后才 import app（引擎绑定测试库）。用 NullPool 重建引擎：asyncpg 连接
#    绑定其创建时的事件循环，而 pytest-asyncio 每个测试一个 loop，若复用连接会
#    触发 "attached to a different loop" / "unknown protocol state"。NullPool
#    每次 checkout 新建连接、checkin 即关，彻底规避跨 loop 复用。
# ---------------------------------------------------------------------------
import app.core.database as db  # noqa: E402
import app.models  # noqa: E402,F401  — 注册全部表
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

db.engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
db.async_session = async_sessionmaker(db.engine, expire_on_commit=False)

from app.core.database import async_session, engine  # noqa: E402
from app.core.security import create_access_token, hash_password  # noqa: E402
from app.models import Conversation, Tenant, User  # noqa: E402
from app.models.base import Base  # noqa: E402


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _schema():
    """会话级：重建测试库表结构。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _cleanup(_schema):
    """每个测试后清空数据，保证用例隔离。"""
    from sqlalchemy import text

    yield
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(text(f'TRUNCATE TABLE "{table.name}" CASCADE'))


@pytest_asyncio.fixture
async def client(_schema):
    """ASGI 内存客户端（不启动 lifespan，NATS/Redis 不参与）。"""
    import httpx

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def make_user(_schema):
    """造数助手：创建租户 + 用户，返回含 token 的 dict。

    可传 tenant_id 在同一租户下多建用户；不传则每次新建租户。
    """

    async def _make(*, role="user", email=None, phone=None, tenant_id=None, password="pass123"):
        async with async_session() as s:
            if tenant_id is not None:
                t = await s.get(Tenant, uuid.UUID(tenant_id))
            else:
                t = Tenant(name=f"tenant-{uuid.uuid4().hex[:10]}")
                s.add(t)
                await s.flush()
            u = User(
                tenant_id=t.id,
                role=role,
                email=email or f"{uuid.uuid4().hex[:10]}@example.com",
                phone=phone,
                password_hash=hash_password(password),
            )
            s.add(u)
            await s.commit()
            token = create_access_token(
                tenant_id=str(u.tenant_id), user_id=str(u.id), role=u.role
            )
            return {
                "tenant_id": str(u.tenant_id),
                "user_id": str(u.id),
                "email": u.email,
                "phone": phone,
                "role": u.role,
                "token": token,
            }

    return _make


@pytest_asyncio.fixture
async def make_conversation(_schema):
    """造数助手：在指定租户/用户下建会话，返回会话 id。"""

    async def _make(tenant_id: str, user_id: str):
        async with async_session() as s:
            c = Conversation(tenant_id=uuid.UUID(tenant_id), user_id=uuid.UUID(user_id))
            s.add(c)
            await s.commit()
            return str(c.id)

    return _make
