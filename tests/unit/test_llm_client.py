import httpx
import json

from app.clients.llm_client import LLMClient, extract_reply


def test_extract_reply():
    data = {"choices": [{"message": {"role": "assistant", "content": "你好"}}]}
    assert extract_reply(data) == "你好"


async def test_llm_client_uses_configured_url_key_and_model():
    observed = {}

    async def handler(request: httpx.Request):
        observed["url"] = str(request.url)
        observed["authorization"] = request.headers.get("authorization")
        observed["body"] = __import__("json").loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "回答"}}]},
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = LLMClient(
        api_url="https://api.deepseek.example/chat/completions",
        api_key="secret-key",
        model="deepseek-chat",
        client=http_client,
    )
    try:
        assert await client.generate([{"role": "user", "content": "你好"}]) == "回答"
    finally:
        await client.close()

    assert observed == {
        "url": "https://api.deepseek.example/chat/completions",
        "authorization": "Bearer secret-key",
        "body": {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": "你好"}],
            "stream": False,
            "temperature": 0.2,
            "max_tokens": 800,
        },
    }


async def test_llm_client_can_disable_thinking_for_structured_json_tasks():
    observed = {}

    async def handler(request: httpx.Request):
        observed.update(__import__("json").loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok":true}'}}]},
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = LLMClient(client=http_client)
    try:
        reply = await client.generate(
            [{"role": "user", "content": "返回 JSON"}], thinking=False
        )
    finally:
        await client.close()

    assert reply == '{"ok":true}'
    assert observed["thinking"] == {"type": "disabled"}


async def test_llm_client_masks_pii_at_the_provider_boundary():
    observed = {}

    async def handler(request: httpx.Request):
        observed.update(__import__("json").loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = LLMClient(client=http_client)
    try:
        await client.generate(
            [{"role": "user", "content": "联系 13812345678 或 parent@example.com"}]
        )
    finally:
        await client.close()

    sent = observed["messages"][0]["content"]
    assert "13812345678" not in sent
    assert "parent@example.com" not in sent
    assert "138****5678" in sent
    assert "p***@example.com" in sent


async def test_llm_client_streams_provider_deltas_before_returning_full_reply():
    observed = []

    async def handler(request: httpx.Request):
        body = json.loads(request.content)
        assert body["stream"] is True
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'data: {"choices":[{"delta":{"content":"你好"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"，同学"}}]}\n\n'
                'data: [DONE]\n\n'
            ),
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = LLMClient(client=http_client)
    try:
        reply = await client.generate_stream(
            [{"role": "user", "content": "你好"}], observed.append
        )
    finally:
        await client.close()

    assert observed == ["你好", "，同学"]
    assert reply == "你好，同学"


async def test_llm_client_rejects_200_response_without_any_sse_delta():
    async def handler(request: httpx.Request):
        return httpx.Response(200, content="not-an-sse-stream")

    client = LLMClient(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    try:
        import pytest
        with pytest.raises(ValueError, match="no content delta"):
            await client.generate_stream([{"role": "user", "content": "你是谁"}], lambda _: None)
    finally:
        await client.close()
