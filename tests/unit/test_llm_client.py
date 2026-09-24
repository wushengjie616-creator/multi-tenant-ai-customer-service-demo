import httpx

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
