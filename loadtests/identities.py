"""Deterministic identities shared by the load-data seeder and Locust."""

import uuid
from dataclasses import dataclass

NAMESPACE = uuid.UUID("7d819719-bc62-4b03-813d-77dfe9be2178")


@dataclass(frozen=True)
class LoadIdentity:
    index: int
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    conversation_id: uuid.UUID


def identity_for(index: int) -> LoadIdentity:
    if index < 0:
        raise ValueError("load-test identity index must be non-negative")
    return LoadIdentity(
        index=index,
        tenant_id=uuid.uuid5(NAMESPACE, f"tenant:{index}"),
        user_id=uuid.uuid5(NAMESPACE, f"user:{index}"),
        conversation_id=uuid.uuid5(NAMESPACE, f"conversation:{index}"),
    )
