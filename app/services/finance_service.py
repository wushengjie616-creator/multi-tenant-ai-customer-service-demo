"""财务查询策略：失败时确定性降级，成功结果在任何模型处理前脱敏。"""

import uuid

from app.services import audit_service
from app.core.metrics import TOOL_CALLS
from app.utils.masking import mask_value


def safe_finance_result(data: dict | None, error: str | None = None) -> dict:
    if error or not data:
        return {
            "status": "unavailable",
            "message": "财务系统暂时无法查询，请稍后重试或转人工处理。",
            "reason": error or "empty_response",
        }
    return {"status": "ok", "data": mask_value(data)}


def format_finance_reply(kind: str, result: dict) -> str:
    if result.get("status") != "ok":
        return result.get("message", "财务系统暂时无法查询，请稍后重试。")
    data = result.get("data") or {}
    labels = {"invoice": "发票", "order": "订单", "bill": "账单", "refund": "退费", "balance": "余额"}
    parts = [f"已查到您的{labels.get(kind, '财务')}信息"]
    if data.get("order_id"):
        parts.append(f"订单号 {data['order_id']}")
    if data.get("amount") is not None:
        try:
            parts.append(f"金额 ¥{float(data['amount']):,.0f}")
        except (TypeError, ValueError):
            parts.append(f"金额 {data['amount']}")
    if data.get("balance") is not None:
        parts.append(f"余额 ¥{data['balance']}")
    if data.get("status"):
        status = {"issued": "已开具", "pending": "处理中", "refunded": "已退款"}.get(str(data["status"]), str(data["status"]))
        parts.append(f"状态：{status}")
    if data.get("email"):
        parts.append(f"联系邮箱 {data['email']}")
    return "，".join(parts) + "。"


async def query_with_audit(session, client, *, kind: str, tenant_id: str, user_id: str) -> dict:
    try:
        data = await client.query(kind, tenant_id, user_id)
        result = safe_finance_result(data)
        outcome, detail = "success", None
    except Exception as exc:
        result = safe_finance_result(None, error=type(exc).__name__)
        outcome, detail = "failed", {"error": type(exc).__name__}
    TOOL_CALLS.labels(f"finance.{kind}", outcome).inc()
    await audit_service.record_audit(
        session, tenant_id=uuid.UUID(tenant_id), actor_id=user_id,
        action=f"finance.{kind}", target=f"user:{user_id}", outcome=outcome, detail=detail,
    )
    return result
