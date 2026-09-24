"""确定性意图路由，以及正式租户使用的受约束 LLM 兜底分类。"""

import json

from app.schemas.intent import IntentResult


LLM_ALLOWED_INTENTS = {
    "knowledge_qa", "course_info", "enrollment", "schedule_info", "teacher_info",
    "pricing", "promotion", "attendance_policy", "location_contact", "account_support",
    "material_info", "schedule", "finance", "chitchat", "human_handoff",
    "platform_command", "unknown",
}
LLM_ALLOWED_PLATFORM_ACTIONS = {
    "submit_leave", "view_course_schedule", "view_study_report", "adjust_course",
}


def classify_intent(text: str) -> IntentResult:
    normalized = "".join(text.lower().split())
    handoff_negatives = ("人工智能", "人工成本")
    if any(term in normalized for term in handoff_negatives):
        if "人工智能" in normalized and "课程" in normalized:
            return IntentResult(intent="knowledge_qa", confidence=0.9, source="rule")
        return IntentResult(intent="unknown", confidence=0, source="abstain", abstain_reason="ambiguous_human_phrase")
    if not any(term in normalized for term in handoff_negatives) and any(
        term in normalized for term in ("转人工", "人工客服", "真人客服", "真人处理", "客服人员")
    ):
        return IntentResult(intent="human_handoff", confidence=1, source="rule")
    if any(
        term in normalized
        for term in ("请假规则", "请假政策", "缺课", "补课规则", "考勤")
    ):
        return IntentResult(intent="attendance_policy", confidence=0.96, source="rule")
    if any(term in normalized for term in ("退费政策", "退款政策", "课程政策", "服务协议", "faq", "常见问题", "补课", "转班", "试听", "老带新", "隐私", "线上", "线下", "开课后", "一节课", "多长时间")):
        return IntentResult(intent="knowledge_qa", confidence=0.96, source="rule")
    if any(term in normalized for term in ("发票", "账单", "余额", "订单", "退费进度", "退款进度", "到账")):
        return IntentResult(intent="finance", confidence=0.98, source="rule")
    if any(term in normalized for term in ("提醒", "日程", "闹钟")):
        return IntentResult(intent="schedule", confidence=0.96, source="rule")
    if any(
        term in normalized
        for term in ("自动续费", "不要再续费", "课程表", "请假", "学习报告", "调课")
    ):
        risk = "high" if "自动续费" in normalized else "low"
        return IntentResult(
            intent="platform_command",
            confidence=0.96,
            source="rule",
            risk_level=risk,
        )
    knowledge_domains = (
        ("location_contact", ("校区地址", "地址", "在哪里", "在哪儿", "怎么走", "联系电话", "热线", "营业时间")),
        ("enrollment", ("报名", "如何入学", "入学流程", "体验课", "试听课")),
        ("teacher_info", ("外教", "老师", "教师", "师资", "资质")),
        ("pricing", ("多少钱", "价格", "收费", "学费", "费用")),
        ("promotion", ("优惠", "促销", "折扣", "满减", "老带新")),
        ("material_info", ("教材", "学习资料", "课件", "讲义")),
        ("schedule_info", ("上课时间", "开课时间", "几点上课", "课时")),
        ("attendance_policy", ("缺课", "请假规则", "补课规则", "考勤")),
        ("account_support", ("账号登录", "忘记密码", "重置密码", "平台使用")),
        ("course_info", ("课程介绍", "课程内容", "课程级别", "教什么", "科目")),
    )
    for intent, terms in knowledge_domains:
        if any(term in normalized for term in terms):
            return IntentResult(intent=intent, confidence=0.95, source="rule")
    if any(term in normalized for term in ("课程", "退费", "退款", "活动", "协议")):
        return IntentResult(intent="knowledge_qa", confidence=0.9, source="rule")
    if any(term in normalized for term in ("你好", "您好", "谢谢", "再见", "早上好", "你是谁")):
        return IntentResult(intent="chitchat", confidence=0.95, source="rule")
    return IntentResult(
        intent="unknown",
        confidence=0,
        source="abstain",
        abstain_reason="no_rule_match",
    )


async def classify_intent_with_llm(text: str, llm) -> IntentResult:
    """Use the real tenant LLM only as a constrained classifier, never as a tool executor."""
    fallback = IntentResult(
        intent="unknown", confidence=0, source="abstain",
        abstain_reason="llm_classification_failed",
    )
    try:
        raw = await llm.generate([
            {
                "role": "system",
                "content": (
                    "你是教育客服意图分类器，只返回 JSON，不要解释。"
                    "字段为 intent、confidence、parameters。可选 intent："
                    + "、".join(sorted(LLM_ALLOWED_INTENTS))
                    + "。platform_command 只允许 action 为 submit_leave、view_course_schedule、"
                    "view_study_report、adjust_course；涉及付费、续费或删除时返回 unknown。"
                ),
            },
            {"role": "user", "content": text},
        ])
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(cleaned)
        intent = data.get("intent")
        confidence = float(data.get("confidence", 0))
        parameters = data.get("parameters") if isinstance(data.get("parameters"), dict) else {}
        if intent not in LLM_ALLOWED_INTENTS or confidence < 0.6:
            return fallback
        if intent == "platform_command" and parameters.get("action") not in LLM_ALLOWED_PLATFORM_ACTIONS:
            return fallback
        return IntentResult(
            intent=intent, confidence=min(confidence, 1), source="llm",
            parameters=parameters, model_version="tenant-llm-v1",
        )
    except Exception:
        # Classification is an optional routing enhancement. Network/model/JSON
        # failures must fall back to tenant RAG instead of failing the message.
        return fallback
