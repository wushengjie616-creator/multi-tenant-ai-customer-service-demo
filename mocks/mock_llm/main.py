"""mock-llm：模拟 LLM 推理与流式输出。

故障开关（HTTP 头，供 E2E 注入故障）：
- X-Mock-Delay-Ms: 延迟毫秒
- X-Mock-Status:  强制 HTTP 状态码（如 500）
- X-Mock-Body:    强制返回原始 body（如非法 JSON）
"""

import asyncio

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

app = FastAPI(title="mock-llm")
_control = {
    "delay_ms": 0,
    "delay_every": 0,
    "status": 0,
    "status_every": 0,
    "body": "",
    "request_count": 0,
}


def should_inject(request_number: int, *, every: int) -> bool:
    return every > 0 and request_number > 0 and request_number % every == 0


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


def build_reply(messages: list[ChatMessage]) -> str:
    last = messages[-1].content if messages else ""
    if "可信证据：" in last:
        question = last.split("用户问题：", 1)[-1].split("可信证据：", 1)[0].strip()
        evidence = last.split("可信证据：", 1)[1]
        lines = []
        for raw_line in evidence.splitlines():
            stripped = raw_line.strip()
            if not stripped or stripped.startswith(">"):
                continue
            if stripped.startswith("#") and "Q:" not in stripped:
                continue
            lines.append(stripped.lstrip("#- "))
        normalized = "".join(question.replace("？", "").replace("?", "").split())
        terms = {
            normalized[index : index + 2]
            for index in range(max(len(normalized) - 1, 0))
        }
        best_index = max(
            range(len(lines)),
            key=lambda index: sum(term in "".join(lines[index].split()) for term in terms),
            default=0,
        )
        excerpt = "\n".join(lines[best_index : best_index + 2])
        return f"根据知识库资料：\n{excerpt}（mock LLM 确定性回复）"
    return f"已收到你的消息：{last}（mock LLM 确定性回复）"


@app.get("/health")
async def health():
    return {"status": "ok", "service": "mock-llm"}


@app.put("/control")
async def control(payload: dict):
    _control.update(
        {
            "delay_ms": int(payload.get("delay_ms", 0)),
            "delay_every": int(payload.get("delay_every", 0)),
            "status": int(payload.get("status", 0)),
            "status_every": int(payload.get("status_every", 0)),
            "body": str(payload.get("body", "")),
            "request_count": 0,
        }
    )
    return _control


@app.post("/v1/chat/completions")
async def chat_completions(
    req: ChatRequest,
    x_mock_delay_ms: int = Header(default=0, alias="X-Mock-Delay-Ms"),
    x_mock_status: int = Header(default=0, alias="X-Mock-Status"),
    x_mock_body: str = Header(default="", alias="X-Mock-Body"),
):
    _control["request_count"] += 1
    request_number = _control["request_count"]
    delay_applies = _control["delay_every"] == 0 or should_inject(
        request_number, every=_control["delay_every"]
    )
    status_applies = _control["status_every"] == 0 or should_inject(
        request_number, every=_control["status_every"]
    )
    delay_ms = x_mock_delay_ms or (_control["delay_ms"] if delay_applies else 0)
    status = x_mock_status or (_control["status"] if status_applies else 0)
    body = x_mock_body or _control["body"]
    if delay_ms:
        await asyncio.sleep(delay_ms / 1000)
    if status:
        return JSONResponse(status_code=status, content={"error": "forced by mock"})
    if body:
        return Response(content=body, media_type="application/json")

    reply = build_reply(req.messages)
    return {
        "choices": [{"message": {"role": "assistant", "content": reply}}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0},
    }
