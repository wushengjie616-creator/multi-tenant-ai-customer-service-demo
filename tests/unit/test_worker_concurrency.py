import asyncio
import json

from app.workers import message_worker


class FakeMessage:
    def __init__(self, message_id: str, conversation_id: str):
        self.data = json.dumps({
            "payload": {
                "tenant_id": "10000000-0000-0000-0000-000000000001",
                "message_id": message_id,
                "conversation_id": conversation_id,
            }
        }).encode()
        self.acked = 0
        self.nacked = 0

    async def ack(self):
        self.acked += 1

    async def nak(self, **kwargs):
        self.nacked += 1


async def test_batch_processing_is_concurrent_but_keeps_each_conversation_ordered():
    messages = [
        FakeMessage("a-1", "conversation-a"),
        FakeMessage("a-2", "conversation-a"),
        FakeMessage("b-1", "conversation-b"),
    ]
    active_total = 0
    max_active_total = 0
    active_by_conversation = {}
    max_by_conversation = {}
    completed = []

    async def processor(msg):
        nonlocal active_total, max_active_total
        payload = json.loads(msg.data)["payload"]
        conversation_id = payload["conversation_id"]
        active_total += 1
        max_active_total = max(max_active_total, active_total)
        active_by_conversation[conversation_id] = active_by_conversation.get(conversation_id, 0) + 1
        max_by_conversation[conversation_id] = max(
            max_by_conversation.get(conversation_id, 0),
            active_by_conversation[conversation_id],
        )
        await asyncio.sleep(0.01)
        completed.append(payload["message_id"])
        active_by_conversation[conversation_id] -= 1
        active_total -= 1
        return "processed"

    await message_worker.process_batch(
        object(), messages, concurrency=2, processor=processor
    )

    assert max_active_total == 2
    assert max_by_conversation == {"conversation-a": 1, "conversation-b": 1}
    assert completed.index("a-1") < completed.index("a-2")
    assert all(message.acked == 1 and message.nacked == 0 for message in messages)
