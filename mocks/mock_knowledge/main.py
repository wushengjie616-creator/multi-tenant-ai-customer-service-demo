"""mock-knowledge：模拟知识检索服务（检索片段、来源和 score）。

第 2 步：实现检索接口，返回片段、来源与分数，支持租户隔离与低分/无命中。
"""

from fastapi import FastAPI

app = FastAPI(title="mock-knowledge")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "mock-knowledge"}
