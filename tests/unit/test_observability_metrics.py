import httpx
from prometheus_client import REGISTRY

from app.clients.llm_client import LLMClient
from app.core import metrics
from app.services.metrics_collector import collect_nats_metrics


def sample(name: str, labels: dict[str, str]) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


async def test_llm_metrics_change_at_the_real_client_boundary():
    async def handler(request: httpx.Request):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "回答"}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
        })

    client = LLMClient(
        model="metric-test-model",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    labels = {"provider": "openai-compatible", "model": "metric-test-model", "outcome": "success"}
    before = sample("eduai_llm_requests_total", labels)
    try:
        await client.generate([{"role": "user", "content": "你好"}])
    finally:
        await client.close()

    assert sample("eduai_llm_requests_total", labels) == before + 1


class FakeConsumerInfo:
    def __init__(self, pending: int, ack_pending: int):
        self.num_pending = pending
        self.num_ack_pending = ack_pending


class FakeJetStream:
    async def consumer_info(self, stream, consumer):
        assert stream == "EVENTS"
        return FakeConsumerInfo(17, 4)


async def test_nats_collector_exports_backlog_gauges():
    await collect_nats_metrics(FakeJetStream(), consumers=("worker-a",))

    assert sample("eduai_queue_pending", {"stream": "EVENTS", "consumer": "worker-a"}) == 17
    assert sample("eduai_queue_ack_pending", {"stream": "EVENTS", "consumer": "worker-a"}) == 4
    assert metrics.QUEUE_PENDING is not None
