"""财务查询策略：失败时确定性降级，成功结果在任何模型处理前脱敏。"""

from app.utils.masking import mask_value


def safe_finance_result(data: dict | None, error: str | None = None) -> dict:
    if error or not data:
        return {
            "status": "unavailable",
            "message": "财务系统暂时无法查询，请稍后重试或转人工处理。",
            "reason": error or "empty_response",
        }
    return {"status": "ok", "data": mask_value(data)}
