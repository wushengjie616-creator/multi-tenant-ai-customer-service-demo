"""Curated five-tenant catalog used by the interview demo surface."""

import uuid

from app.core.config import settings


def demo_tenant_catalog() -> list[dict]:
    return [
        {"code": "T01", "id": uuid.UUID(settings.demo_customer_tenant_id), "name": "星河未来成长中心", "scenario": "全功能综合演示", "features": ["knowledge", "assistant", "learning", "finance", "services", "reminders", "handoff", "operations"], "customer_name": "林小满家长"},
        {"code": "T02", "id": uuid.UUID(settings.demo_tenant_id), "name": "云杉学业服务中心", "scenario": "知识库与运营工作台", "features": ["knowledge", "assistant", "operations"], "customer_name": "周可欣家长"},
        {"code": "T03", "id": uuid.UUID("30000000-0000-0000-0000-000000000001"), "name": "拾光艺术成长营", "scenario": "课程学习与日程提醒", "features": ["knowledge", "assistant", "learning", "reminders"], "customer_name": "陈一诺家长"},
        {"code": "T04", "id": uuid.UUID("40000000-0000-0000-0000-000000000001"), "name": "远航国际语言学院", "scenario": "财务查询与办事服务", "features": ["knowledge", "assistant", "finance", "services"], "customer_name": "许文博家长"},
        {"code": "T05", "id": uuid.UUID("50000000-0000-0000-0000-000000000001"), "name": "启明星科学探索馆", "scenario": "智能问答与人工协作", "features": ["knowledge", "assistant", "learning", "services", "handoff"], "customer_name": "孙晨宇家长"},
    ]


def demo_tenant_ids() -> set[uuid.UUID]:
    return {item["id"] for item in demo_tenant_catalog()}
