"""ORM 模型统一出口，保证 Base.metadata 注册全部表。"""

from app.models.audit_log import AuditLog
from app.models.base import Base
from app.models.business import Confirmation, Handoff, Reminder, ReminderDelivery, ToolExecution
from app.models.conversation import Conversation
from app.models.dead_letter import DeadLetter
from app.models.knowledge_graph import (
    KnowledgeDocument,
    KnowledgeESDocument,
    KnowledgeGraphEdge,
    KnowledgeGraphNode,
)
from app.models.message import Message
from app.models.llm_usage import LLMUsage
from app.models.outbox import OutboxEvent
from app.models.user import Tenant, User

__all__ = [
    "Base", "Tenant", "User", "Conversation", "Message", "OutboxEvent", "AuditLog",
    "Confirmation", "ToolExecution", "Reminder", "ReminderDelivery", "Handoff", "DeadLetter", "LLMUsage",
    "KnowledgeDocument", "KnowledgeESDocument", "KnowledgeGraphNode", "KnowledgeGraphEdge",
]
