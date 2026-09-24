"""mock-finance：模拟财务系统（财务查询、鉴权和故障注入）。

第 2 步：实现订单/账单/发票/退费/余额查询，支持超时、500、越权等故障开关。
"""

import asyncio

from fastapi import FastAPI, HTTPException, Query

app = FastAPI(title="mock-finance")
_control = {"delay_ms": 0, "fail": False}
_calls: list[dict] = []


@app.get("/health")
async def health():
    return {"status": "ok", "service": "mock-finance"}


@app.get("/finance/{kind}")
async def finance(kind: str, tenant_id: str, user_id: str, delay_ms: int = Query(0), fail: bool = Query(False)):
    _calls.append({"kind": kind, "tenant_id": tenant_id, "user_id": user_id})
    effective_delay = delay_ms or _control["delay_ms"]
    if effective_delay:
        await asyncio.sleep(effective_delay / 1000)
    if fail or _control["fail"]:
        raise HTTPException(500, "forced failure")
    fixtures = {
        "invoice": {"order_id": "XH-2026-0901", "invoice_id": "FP-XH-0901", "amount": 3280.0, "status": "issued", "email": "lin-parent@xinghe-future.example.com"},
        "order": {"order_id": "XH-2026-0901", "course_name": "数学思维进阶班 A3", "amount": 3280.0, "status": "paid", "paid_at": "2026-09-01T10:26:00+08:00"},
        "bill": {"bill_id": "BILL-XH-0901", "description": "2026 年秋季课程学费", "amount": 3280.0, "status": "settled"},
        "refund": {"refund_id": "REF-XH-001", "amount": 600.0, "status": "reviewing", "submitted_at": "2026-09-22T14:10:00+08:00"},
        "balance": {"balance": 320.0, "currency": "CNY"},
    }
    if kind not in fixtures:
        raise HTTPException(404, "unknown query")
    return {"tenant_id": tenant_id, "user_id": user_id, **fixtures[kind]}


@app.put("/control")
async def control(payload: dict):
    _control.update({"delay_ms": int(payload.get("delay_ms", 0)), "fail": bool(payload.get("fail", False))})
    return _control


@app.get("/calls")
async def calls():
    return {"count": len(_calls), "calls": _calls}
