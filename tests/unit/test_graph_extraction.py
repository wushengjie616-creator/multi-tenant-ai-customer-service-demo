"""确定性图谱抽取器单测：真实结构化课程目录 → 节点/边；校验层丢弃悬空边/非法类型。"""

from app.services.graph_extraction import (
    extract_graph,
    stable_node_id,
    validate_graph,
)

COURSE = """# 2026 年秋季课程

## 数学思维进阶班 A3

适合三至四年级，周老师授课，每周四 18:30–20:00，静安校区 3F-305。16 次课程学费 3280 元，含教材与阶段测评。

## Python 创意编程 P1

适合五至八年级，每周日 14:00–15:30，可选校区面授或同步在线课。14 次课程学费 2980 元。
"""


def test_extract_course_catalog_nodes_and_edges():
    graph = extract_graph(
        tenant_id="t", document_id="d", title="2026 年秋季课程", content=COURSE
    )
    by_type: dict[str, list] = {}
    for node in graph["nodes"]:
        by_type.setdefault(node["type"], []).append(node)

    assert set(by_type) == {"class", "teacher", "campus"}
    assert len(by_type["class"]) == 2
    assert len(by_type["teacher"]) == 1  # 周老师
    assert len(by_type["campus"]) == 1  # 静安校区

    a3 = next(n for n in by_type["class"] if n["name"].endswith("A3"))
    assert a3["attributes"]["价格"] == 3280
    assert a3["attributes"]["编码"] == "A3"
    assert a3["attributes"]["课时数"] == 16
    assert a3["attributes"]["适合年级"] == "三至四年级"

    # P1「可选校区」是泛称（母本 §2.1 实体=具名实体），不建 campus 节点
    p1 = next(n for n in by_type["class"] if n["name"].endswith("P1"))
    assert p1["attributes"]["价格"] == 2980

    edge_types = [e["type"] for e in graph["edges"]]
    assert edge_types.count("teaches") == 1  # 周老师 -> A3
    assert edge_types.count("located_at") == 1  # A3 -> 静安校区


def test_stable_node_id_is_deterministic_and_tenant_scoped():
    same = stable_node_id("t", "class", "数学思维进阶班 A3")
    assert stable_node_id("t", "class", "数学思维进阶班 A3") == same
    assert stable_node_id("t2", "class", "数学思维进阶班 A3") != same
    assert stable_node_id("t", "teacher", "数学思维进阶班 A3") != same


def test_validate_graph_drops_dangling_edges_and_unknown_types():
    graph = validate_graph({
        "nodes": [
            {"node_id": "n1", "type": "class", "name": "A3"},
            {"node_id": "n2", "type": "mystery", "name": "bad"},  # 未知类型
        ],
        "edges": [
            {"edge_id": "e1", "type": "teaches", "from_node_id": "n1", "to_node_id": "ghost"},  # 悬空
            {"edge_id": "e2", "type": "unknown_edge", "from_node_id": "n1", "to_node_id": "n1"},  # 未知边类型
        ],
    })
    assert [n["node_id"] for n in graph["nodes"]] == ["n1"]
    assert graph["edges"] == []


FULL_CATALOG = """# 2026 年秋季课程

## 数学思维启蒙班 M1

科目：数学；适合学前至一年级，无需任何基础；目标：兴趣启蒙。王老师授课，每周六 09:30–10:30，静安校区 1F-101。12 次课程学费 1980 元。

## 数学思维同步班 B2

科目：数学；适合一至二年级，无需奥数基础；目标：巩固提分。李老师授课，每周三 17:00–18:30，静安校区 2F-202。14 次课程学费 2380 元。

## 奥数竞赛冲刺班 C1

科目：数学；适合四至六年级，需奥数基础；目标：竞赛冲刺。周老师授课，每周日 09:00–11:00，静安校区 3F-301。20 次课程学费 4280 元。
"""


def test_extract_subject_foundation_goal_attributes():
    graph = extract_graph(
        tenant_id="t", document_id="d", title="2026 年秋季课程", content=FULL_CATALOG
    )
    classes = {n["name"].split()[-1]: n for n in graph["nodes"] if n["type"] == "class"}

    m1 = classes["M1"]
    assert m1["attributes"]["科目"] == "数学"
    assert m1["attributes"]["需基础"] == "none"
    assert m1["attributes"]["目标"] == "兴趣"

    b2 = classes["B2"]
    assert b2["attributes"]["科目"] == "数学"
    assert b2["attributes"]["需基础"] == "none"
    assert b2["attributes"]["目标"] == "提分"

    c1 = classes["C1"]
    assert c1["attributes"]["科目"] == "数学"
    assert c1["attributes"]["需基础"] == "olympiad"
    assert c1["attributes"]["目标"] == "竞赛"


def test_missing_foundation_and_goal_stay_absent():
    # 文档没写「需基础/目标」的班型（如 A3）不填臆造值。
    graph = extract_graph(
        tenant_id="t", document_id="d", title="课程",
        content="## 数学思维进阶班 A3\n\n科目：数学；适合三至四年级。16 次课程学费 3280 元。",
    )
    a3 = next(n for n in graph["nodes"] if n["type"] == "class")
    assert "需基础" not in a3["attributes"]
    assert "目标" not in a3["attributes"]
    assert a3["attributes"]["科目"] == "数学"
