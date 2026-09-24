"""mock-platform：模拟平台指令服务（接收指令并返回结果）。

第 2 步：实现指令执行接口，支持超时、失败、幂等键等故障开关。
"""

from fastapi import FastAPI, Header, HTTPException

app = FastAPI(title="mock-platform")
_results: dict[str, dict] = {}
_calls: list[dict] = []
_auto_renew_enabled: dict[tuple[str, str], bool] = {}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "mock-platform"}


@app.get("/queries/{kind}")
async def query(kind: str, tenant_id: str, user_id: str):
    if kind == "course_schedule":
        return {"status": "ok", "data": [
            {"course_id": "math-thinking-a3", "course_name": "数学思维进阶班 A3", "teacher": "周老师", "campus": "星河中心·静安校区", "classroom": "3F-305", "starts_at": "2026-09-24T18:30:00+08:00"},
            {"course_id": "science-lab-s2", "course_name": "科学探究实验班 S2", "teacher": "陈老师", "campus": "星河中心·静安校区", "classroom": "2F-Lab", "starts_at": "2026-09-26T10:00:00+08:00"},
        ]}
    if kind == "study_report":
        return {"status": "ok", "data": {"student_name": "林小满", "completed_lessons": 12, "attendance_rate": 0.96, "homework_rate": 0.92, "skill_growth": "+18%", "teacher_comment": "逻辑推理进步明显，建议继续加强应用题表达。"}}
    if kind == "subscription_status":
        enabled = _auto_renew_enabled.get((tenant_id, user_id), True)
        return {"status": "ok", "data": {"resource_id": "membership-2026", "plan": "2026 年秋季联报计划", "auto_renew": enabled, "next_charge_at": "2026-12-15T08:00:00+08:00"}}
    raise HTTPException(404, "unknown query")


@app.post("/tools/{action}")
async def execute_tool(action: str, payload: dict, idempotency_key: str = Header(alias="Idempotency-Key")):
    if action not in {"open_auto_renew", "close_auto_renew", "submit_leave", "update_course_reminder"}:
        raise HTTPException(404, "unknown tool")
    if idempotency_key in _results:
        return _results[idempotency_key]
    if action in {"open_auto_renew", "close_auto_renew"}:
        tenant_id = str(payload.get("tenant_id") or "")
        user_id = str(payload.get("user_id") or "")
        if not tenant_id or not user_id:
            raise HTTPException(422, "tenant_id and user_id are required")
        _auto_renew_enabled[(tenant_id, user_id)] = action == "open_auto_renew"
    result = {"status": "succeeded", "action": action, "resource_id": payload.get("resource_id")}
    if action in {"open_auto_renew", "close_auto_renew"}:
        result["enabled"] = action == "open_auto_renew"
    _calls.append({"action": action, "payload": payload, "idempotency_key": idempotency_key})
    _results[idempotency_key] = result
    return result


@app.get("/calls")
async def calls():
    return {"count": len(_calls), "calls": _calls}
