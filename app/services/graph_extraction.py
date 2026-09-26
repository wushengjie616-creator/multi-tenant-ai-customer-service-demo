"""确定性知识图谱抽取器（无 LLM 依赖，作为基线 + LLM 提案的兜底）。

母本：docs/optimization/rag-knowledge-graph-extraction-schema.md。本模块只实现
确定性可证的 class / teacher / campus 三类抽取，覆盖「课程目录」这类结构化源；
policy / order / invoice / customer / activity / institution 六类需要语义判断，
由后续 LLM 抽取器补位，二者输出都过 `validate_graph` 同一校验层再入图。

节点 id / 边 id 用稳定哈希生成，保证「跨文档同一实体可对齐」（母本 §9 实体消歧的基础）。
"""

import re
import uuid
from typing import Any

SCHEMA_VERSION = "v1"

# 母本 §3 节点类型注册表枚举
NODE_TYPE_REGISTRY = frozenset({
    "class", "teacher", "campus", "policy", "order",
    "invoice", "customer", "activity", "institution",
})

# 母本 §4 边类型注册表：edge_type -> (允许的 from 类型, 允许的 to 类型)
EDGE_TYPE_REGISTRY: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "teaches": (frozenset({"teacher"}), frozenset({"class"})),
    "located_at": (frozenset({"class"}), frozenset({"campus"})),
    "governed_by": (frozenset({"class", "order"}), frozenset({"policy"})),
    "has_order": (frozenset({"customer"}), frozenset({"order"})),
    "invoices": (frozenset({"order"}), frozenset({"invoice"})),
    "enrolls": (frozenset({"customer"}), frozenset({"class"})),
    "applies_to": (frozenset({"activity"}), frozenset({"class"})),
    "recommends": (frozenset({"class"}), frozenset({"class"})),
}

# 抽取负规则（母本 §6）：这些词是「角色/泛称」而非具体实体，不得建成 teacher/campus 节点。
_TEACHER_NAME_BLOCKLIST = frozenset({"授课", "代课", "任课", "主讲", "外教"})
_CAMPUS_NAME_BLOCKLIST = frozenset({"可选", "本", "该", "每", "各", "任意"})

_CLASS_CODE_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{1,6}\d{1,4}(?![A-Za-z0-9])")
_PRICE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*元")
_SESSION_COUNT_RE = re.compile(r"(\d+)\s*次")
_GRADE_RE = re.compile(r"适合([^，,。；;]+)")
_TEACHER_RE = re.compile(r"([一-龥]{1,3})老师")
_CAMPUS_RE = re.compile(r"([一-龥]{2,6})校区")
_SUBJECT_RE = re.compile(r"科目[:：]\s*([一-龥]{1,4})")
_FOUNDATION_RE = re.compile(r"(无需[一-龥]*基础|零基础|需[一-龥]{0,4}基础)")
_GOAL_RE = re.compile(r"目标[:：]\s*([一-龥]{1,8})")


def _normalize_name(name: str) -> str:
    """归一化实体名：压缩空白、去 Markdown 标记与首尾标点，用于节点名与稳定 id。"""
    normalized = " ".join(name.strip().split()).strip("#-*`，。；：,.;: ")
    return normalized


def stable_node_id(tenant_id: str, node_type: str, name: str) -> str:
    """稳定节点 id：同租户 + 同类型 + 同归一化名 → 同 id（母本 §9）。"""
    key = f"{tenant_id}:{node_type}:{_normalize_name(name)}"
    return f"{node_type}:{uuid.uuid5(uuid.NAMESPACE_URL, key).hex[:12]}"


def stable_edge_id(tenant_id: str, edge_type: str, from_node_id: str, to_node_id: str) -> str:
    key = f"{tenant_id}:{edge_type}:{from_node_id}:{to_node_id}"
    return f"{edge_type}:{uuid.uuid5(uuid.NAMESPACE_URL, key).hex[:12]}"


def _split_sections(content: str) -> list[tuple[str, str]]:
    """按 Markdown 二级标题切分文档，返回 [(heading, body)]；无标题的内容归入首段。"""
    sections: list[tuple[str, str]] = []
    current_heading = ""
    current_body: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        heading_match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if heading_match:
            if current_heading or current_body:
                sections.append((current_heading, "\n".join(current_body).strip()))
            current_heading = _normalize_name(heading_match.group(1))
            current_body = []
        else:
            current_body.append(line)
    if current_heading or current_body:
        sections.append((current_heading, "\n".join(current_body).strip()))
    return sections


def _extract_teachers(body: str) -> list[str]:
    names: list[str] = []
    for match in _TEACHER_RE.finditer(body):
        name = match.group(1)
        if name in _TEACHER_NAME_BLOCKLIST:
            continue
        full = f"{name}老师"
        if full not in names:
            names.append(full)
    return names


def _extract_campus(body: str) -> list[str]:
    names: list[str] = []
    for match in _CAMPUS_RE.finditer(body):
        prefix = match.group(1)
        # 「可选校区」「本校区」等是泛称，不是具体校区（母本 §2.1 实体 = 具名实体）
        if any(prefix.startswith(blocked) for blocked in _CAMPUS_NAME_BLOCKLIST):
            continue
        full = f"{prefix}校区"
        if full not in names:
            names.append(full)
    return names


def _extract_foundation(body: str) -> str | None:
    """归一化「基础要求」：无需/零基础 → none，需 X 基础 → olympiad。"""
    match = _FOUNDATION_RE.search(body)
    if not match:
        return None
    raw = match.group(1)
    return "none" if ("无需" in raw or "零基础" in raw) else "olympiad"


def _extract_goal(body: str) -> str | None:
    """归一化「目标」到推荐槽位同一枚举：兴趣 / 提分 / 竞赛 / 思维。"""
    match = _GOAL_RE.search(body)
    if not match:
        return None
    raw = match.group(1)
    if "兴趣" in raw:
        return "兴趣"
    if "竞赛" in raw or "冲刺" in raw:
        return "竞赛"
    if "提分" in raw or "巩固" in raw or "同步" in raw:
        return "提分"
    if "思维" in raw:
        return "思维"
    return raw


def extract_graph(
    *,
    tenant_id: str,
    document_id: str,
    title: str,
    content: str,
) -> dict[str, Any]:
    """确定性抽取 class / teacher / campus 节点及 teaches / located_at 边。

    返回 ``{"nodes": [...], "edges": [...]}``，字段与 DB 列对应；
    不填臆造值——文档没写的属性缺省 null（母本 §6.1）。
    """
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_nodes: set[str] = set()
    teacher_by_name: dict[str, str] = {}
    campus_by_name: dict[str, str] = {}

    def add_node(node_type: str, name: str, *, aliases=None, attributes=None, es_doc_id=None) -> str:
        node_id = stable_node_id(tenant_id, node_type, name)
        if node_id not in seen_nodes:
            seen_nodes.add(node_id)
            nodes.append({
                "node_id": node_id,
                "type": node_type,
                "name": name,
                "aliases": aliases or [],
                "attributes": attributes or {},
                "es_doc_id": es_doc_id,
            })
        return node_id

    def add_edge(edge_type: str, from_node_id: str, to_node_id: str) -> None:
        edges.append({
            "edge_id": stable_edge_id(tenant_id, edge_type, from_node_id, to_node_id),
            "type": edge_type,
            "from_node_id": from_node_id,
            "to_node_id": to_node_id,
        })

    for heading, body in _split_sections(content):
        if not heading:
            continue
        code_match = _CLASS_CODE_RE.search(heading)
        is_class = bool(code_match) or "班" in heading
        if not is_class:
            continue

        attributes: dict[str, Any] = {}
        code = code_match.group(0).upper() if code_match else None
        if code:
            attributes["编码"] = code
        price_match = _PRICE_RE.search(body)
        if price_match:
            attributes["价格"] = int(float(price_match.group(1)))
        session_match = _SESSION_COUNT_RE.search(body)
        if session_match:
            attributes["课时数"] = int(session_match.group(1))
        grade_match = _GRADE_RE.search(body)
        if grade_match:
            attributes["适合年级"] = grade_match.group(1).strip()
        subject_match = _SUBJECT_RE.search(body)
        if subject_match:
            attributes["科目"] = subject_match.group(1).strip()
        foundation = _extract_foundation(body)
        if foundation:
            attributes["需基础"] = foundation
        goal = _extract_goal(body)
        if goal:
            attributes["目标"] = goal

        teachers = _extract_teachers(body)
        campuses = _extract_campus(body)

        class_id = add_node(
            "class",
            heading,
            aliases=[code] if code else [],
            attributes=attributes or None,
        )

        for teacher_name in teachers:
            teacher_id = teacher_by_name.get(teacher_name)
            if teacher_id is None:
                teacher_id = add_node("teacher", teacher_name)
                teacher_by_name[teacher_name] = teacher_id
            add_edge("teaches", teacher_id, class_id)

        for campus_name in campuses:
            campus_id = campus_by_name.get(campus_name)
            if campus_id is None:
                campus_id = add_node("campus", campus_name)
                campus_by_name[campus_name] = campus_id
            add_edge("located_at", class_id, campus_id)

    return validate_graph({"nodes": nodes, "edges": edges})


def validate_graph(graph: dict[str, Any]) -> dict[str, Any]:
    """确定性校验层（母本 §8）：枚举锁定 + 悬空边丢弃。入图前必过，不信任原始输出。

    不抛异常（抽取失败不应阻断入库主流程），而是丢弃非法节点/边并保留合法子集。
    """
    raw_nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
    raw_edges = graph.get("edges", []) if isinstance(graph, dict) else []

    valid_nodes: list[dict[str, Any]] = []
    known_ids: set[str] = set()
    for node in raw_nodes:
        if not isinstance(node, dict):
            continue
        if node.get("type") not in NODE_TYPE_REGISTRY:
            continue
        name = _normalize_name(str(node.get("name", "")))
        if not name:
            continue
        node["name"] = name
        node.setdefault("aliases", [])
        node.setdefault("attributes", {})
        node.setdefault("es_doc_id", None)
        valid_nodes.append(node)
        known_ids.add(node["node_id"])

    valid_edges: list[dict[str, Any]] = []
    for edge in raw_edges:
        if not isinstance(edge, dict):
            continue
        edge_type = edge.get("type")
        allowed = EDGE_TYPE_REGISTRY.get(edge_type)
        if allowed is None:
            continue
        from_id, to_id = edge.get("from_node_id"), edge.get("to_node_id")
        if from_id not in known_ids or to_id not in known_ids:
            continue  # 悬空边丢弃（母本 §8.2）
        valid_edges.append(edge)

    return {"nodes": valid_nodes, "edges": valid_edges}
