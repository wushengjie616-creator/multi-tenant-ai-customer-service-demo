"""确定性意图路由，以及正式租户使用的受约束 LLM 兜底分类。

两级意图架构（优化后）：
- 规则优先匹配「一级意图」（粗粒度，7 类白名单）；命中即返回，二级意图保持 None。
- 一级意图 unknown 时，才回落 LLM，让 LLM 输出：
  一级意图（仍限白名单）+ 二级意图（LLM 概括的具体意图）。
- RAG 之后带着用户原始消息 + 一级/二级意图一起交给 LLM 合成答案。
"""

import json

from app.schemas.intent import IntentResult

# 一级意图白名单（7 类）。
INTENT_WHITELIST = {
    "platform_command", "finance", "schedule", "human_handoff",
    "knowledge", "chitchat", "unknown", "course_consultation",
}

LLM_ALLOWED_INTENTS = INTENT_WHITELIST
LLM_ALLOWED_PLATFORM_ACTIONS = {
    "submit_leave", "view_course_schedule", "view_study_report", "adjust_course",
}


def classify_intent(text: str) -> IntentResult:
    """规则优先的粗粒度一级意图判定；命中即返回，不生产二级意图。"""
    normalized = "".join(text.lower().split())

    # L0 人工转接：先消解「人工」相关歧义，再做明确转人工判定。
    if "人工智能" in normalized:
        if "课程" in normalized:
            return IntentResult(intent="knowledge", confidence=0.9, source="rule")
        return IntentResult(
            intent="unknown", confidence=0, source="abstain",
            abstain_reason="ambiguous_human_phrase",
        )
    if "人工成本" in normalized or "人工费" in normalized:
        return IntentResult(
            intent="unknown", confidence=0, source="abstain",
            abstain_reason="ambiguous_human_phrase",
        )
    if any(
        term in normalized
        for term in (
            "转人工", "人工客服", "真人客服", "真人处理", "客服人员",
            "人工坐席", "找人工", "转客服", "人工服务", "真人来",
        )
    ):
        return IntentResult(intent="human_handoff", confidence=1, source="rule")

    # L1 请假/考勤/补课「规则类」→ knowledge（先于 L5 的「请假」指令）。
    if any(
        term in normalized
        for term in (
            "请假规则", "请假政策", "请假规定", "请假制度",
            "考勤", "缺课", "补课规则", "补课政策", "补课规定",
        )
    ):
        return IntentResult(intent="knowledge", confidence=0.96, source="rule")

    # L2 政策/FAQ/服务协议 → knowledge（先于 L3 财务的「退费进度/退款进度」）。
    if any(
        term in normalized
        for term in (
            "退费政策", "退款政策", "退费规则", "退款规则", "退费标准",
            "课程政策", "服务协议", "faq", "常见问题", "隐私",
            "线上", "线下", "开课后", "一节课", "多长时间",
            "补课", "转班", "试听", "老带新",
        )
    ):
        return IntentResult(intent="knowledge", confidence=0.96, source="rule")

    # L3 财务（进度/到账/支付类，先于 L6 的「退费/退款」泛知识兜底）。
    if any(
        term in normalized
        for term in (
            "发票", "账单", "余额", "订单",
            "退费进度", "退款进度", "退款到账", "退费到账", "到账",
            "扣款", "支付", "付款", "欠费", "缴费", "消费",
        )
    ):
        return IntentResult(intent="finance", confidence=0.98, source="rule")

    # L4 提醒/日程/闹钟 → schedule（先于 L6 的「课程/上课」知识，保证「课程提醒」判为提醒）。
    if any(term in normalized for term in ("提醒", "日程", "闹钟", "定时", "到点")):
        return IntentResult(intent="schedule", confidence=0.96, source="rule")

    # L4.5 续费「政策/优惠」类 → knowledge（区别于下方 L5 的「停续费」指令）。
    # 但「自动续费」是明确的关闭续费指令，不受「怎么/如何」等词影响。
    if "续费" in normalized and "自动续费" not in normalized and any(
        term in normalized
        for term in ("折扣", "优惠", "政策", "价格", "多少钱", "怎么", "如何", "条件", "规则", "方式")
    ):
        return IntentResult(intent="knowledge", confidence=0.95, source="rule")

    # L5 平台指令（含自动续费高风险）。
    if any(
        term in normalized
        for term in (
            "自动续费", "不要再续费", "续费",
            "课程表", "课表", "请假", "天假", "学习报告", "学习进度", "学习情况", "调课", "改课",
        )
    ):
        risk = "high" if ("自动续费" in normalized or "续费" in normalized) else "low"
        return IntentResult(
            intent="platform_command", confidence=0.96, source="rule", risk_level=risk,
        )

    # L5.5 课程咨询/选课（亲子指代 + 选课意愿），先于 L6 泛「课程」知识。
    if any(
        term in normalized
        for term in ("我家孩子", "我孩子", "我家小孩", "孩子", "小孩", "儿子", "女儿", "娃", "小朋友")
    ) and any(
        term in normalized
        for term in ("适合", "推荐", "报什么", "报哪个", "选什么", "学什么", "上什么", "报班", "选课", "选班")
    ):
        return IntentResult(intent="course_consultation", confidence=0.95, source="rule")

    # L6 知识域（招生/师资/价格/教材/上课时间/账号/课程介绍 + 退费/退款泛兜底）。
    knowledge_terms = (
        "校区地址", "地址", "在哪里", "在哪儿", "怎么走", "联系电话", "热线", "营业时间",
        "报名", "如何入学", "入学流程", "体验课", "试听课",
        "外教", "老师", "教师", "师资", "资质",
        "多少钱", "价格", "收费", "学费", "费用",
        "优惠", "促销", "折扣", "满减",
        "教材", "学习资料", "课件", "讲义",
        "上课时间", "开课时间", "几点上课", "课时", "几节课",
        "账号登录", "忘记密码", "重置密码", "平台使用", "登录",
        "课程介绍", "课程内容", "课程级别", "教什么", "科目", "课程设置",
        "工作时间", "设备",
        "退费", "退款", "活动", "协议", "课程",
    )
    if any(term in normalized for term in knowledge_terms):
        return IntentResult(intent="knowledge", confidence=0.9, source="rule")

    # L7 闲聊（问候 + 身份识别；「真人还是机器人」判闲聊，不判转人工）。
    if any(
        term in normalized
        for term in (
            "你好", "您好", "谢谢", "再见", "早上好", "晚上好",
            "你是谁", "机器人", "是真人吗", "是不是真人", "真人还是",
            "在吗", "hi", "hello", "好的", "知道了", "叫什么名字",
        )
    ):
        return IntentResult(intent="chitchat", confidence=0.95, source="rule")

    # L8 无规则命中 → 交 LLM 兜底。
    return IntentResult(
        intent="unknown",
        confidence=0,
        source="abstain",
        abstain_reason="no_rule_match",
    )


async def classify_intent_with_llm(text: str, llm) -> IntentResult:
    """Use the real tenant LLM only as a constrained classifier, never as a tool executor.

    只在规则判定为 unknown 时调用；输出一级意图（白名单）+ 二级意图（LLM 概括）。
    """
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
                    "字段为 intent、secondary_intent、confidence、parameters。"
                    "intent 必须从以下一级意图白名单中选择："
                    + "、".join(sorted(LLM_ALLOWED_INTENTS))
                    + "。secondary_intent 是你对用户具体意图的简短概括（中文，"
                    "不超过 16 字），用于辅助后续检索。"
                    "platform_command 只允许 action 为 submit_leave、view_course_schedule、"
                    "view_study_report、adjust_course；涉及付费、续费或删除时返回 unknown。"
                    "course_consultation 表示家长为孩子咨询选课/推荐班型；"
                    "泛课程知识介绍仍归 knowledge。"
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
        secondary_intent = data.get("secondary_intent")
        if not isinstance(secondary_intent, str) or not secondary_intent.strip():
            secondary_intent = None
        else:
            secondary_intent = secondary_intent.strip()[:64]
        if intent not in LLM_ALLOWED_INTENTS or confidence < 0.6:
            return fallback
        if intent == "platform_command" and parameters.get("action") not in LLM_ALLOWED_PLATFORM_ACTIONS:
            return fallback
        return IntentResult(
            intent=intent, confidence=min(confidence, 1), source="llm",
            parameters=parameters, model_version="tenant-llm-v2",
            secondary_intent=secondary_intent,
        )
    except Exception:
        # Classification is an optional routing enhancement. Network/model/JSON
        # failures must fall back to tenant RAG instead of failing the message.
        return fallback
