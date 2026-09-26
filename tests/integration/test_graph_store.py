"""知识图谱四层存储集成测试（真实 PG + Base.metadata.create_all 建表）。

覆盖：抽取落库 / 只读视图 / 租户隔离 / 同文档替换无孤儿。
"""

from app.core.database import async_session
from app.services.graph_store import graph_store

COURSE = """# 2026 年秋季课程

## 数学思维进阶班 A3

适合三至四年级，周老师授课，每周四 18:30–20:00，静安校区 3F-305。16 次课程学费 3280 元，含教材与阶段测评。

## 科学探究实验班 S2

适合三至六年级，陈老师授课，每周六 10:00–11:30，静安校区 2F-Lab。12 次课程学费 2680 元，材料费已包含。
"""


async def test_replace_and_list_graph_produces_nodes_and_edges(make_user):
    user = await make_user()
    tenant_id = user["tenant_id"]

    async with async_session() as s:
        counts = await graph_store.replace_document(
            s, tenant_id,
            document_id="course-catalog", title="2026 年秋季课程",
            source="xinghe://courses", content=COURSE, version=1,
        )

    assert counts == {"nodes": 5, "edges": 4, "es_documents": 1}  # 2 class + 2 teacher + 1 campus

    async with async_session() as s:
        graph = await graph_store.list_graph(s, tenant_id)

    node_types = {n["type"] for n in graph["nodes"]}
    assert node_types == {"class", "teacher", "campus"}
    class_nodes = [n for n in graph["nodes"] if n["type"] == "class"]
    assert len(class_nodes) == 2
    a3 = next(n for n in class_nodes if n["name"].endswith("A3"))
    assert a3["attributes"]["价格"] == 3280
    assert a3["attributes"]["编码"] == "A3"
    assert a3["document_id"] == "course-catalog"

    edge_types = [e["type"] for e in graph["edges"]]
    assert edge_types.count("teaches") == 2
    assert edge_types.count("located_at") == 2


async def test_graph_is_tenant_isolated(make_user):
    tenant_a = await make_user()
    tenant_b = await make_user()

    async with async_session() as s:
        await graph_store.replace_document(
            s, tenant_a["tenant_id"],
            document_id="d", title="t", source="s", content=COURSE, version=1,
        )

    async with async_session() as s:
        graph_b = await graph_store.list_graph(s, tenant_b["tenant_id"])

    assert graph_b["nodes"] == []
    assert graph_b["edges"] == []


async def test_replace_is_idempotent_without_orphans(make_user):
    user = await make_user()
    tenant_id = user["tenant_id"]

    async with async_session() as s:
        await graph_store.replace_document(
            s, tenant_id,
            document_id="d", title="t", source="s", content=COURSE, version=1,
        )

    async with async_session() as s:
        counts = await graph_store.replace_document(
            s, tenant_id,
            document_id="d", title="t", source="s",
            content="# 空\n\n## 基础班 B1\n\n适合一年级，学费 1000 元。", version=2,
        )

    assert counts["nodes"] == 1  # 只剩 B1 class

    async with async_session() as s:
        graph = await graph_store.list_graph(s, tenant_id)

    assert len(graph["nodes"]) == 1
    assert graph["nodes"][0]["name"] == "基础班 B1"
    assert graph["nodes"][0]["attributes"]["价格"] == 1000
    assert graph["edges"] == []
