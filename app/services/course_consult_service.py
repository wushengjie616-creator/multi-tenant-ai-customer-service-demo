"""课程咨询技能：多轮引导收集孩子信息 + 图谱驱动班型推荐。

意图命中 ``course_consultation`` 后进入本技能。状态机在 Redis 维护（可过期，
PG 消息历史仍是事实源），逐步收集槽位（年级/兴趣/基础/是否上过课外班/目标），
收满后调 ``course_recommendation`` 确定性查图谱推荐班型。

本技能把「语义抽取」与「话术合成」交给 LLM（通过下方 ``COURSE_CONSULT_SKILL``
能力契约约束），但把「班型事实」牢牢留在确定性侧：

- 槽位枚举与 ``recommend`` 是唯一班型事实源，LLM 不参与事实决策；
- LLM 只负责把家长自然语言抽取成槽位、把确定性候选渲染成话术；
- 确定性规则抽取 + 确定性模板始终是兜底，LLM 失败 / 输出非法一律回退，
  不引入编造班型风险（演示租户的 mock LLM 也会自然走到兜底，保证录屏稳定）。
"""

import json
import re

from app.core.config import settings
from app.core.redis import redis_client
from app.services.course_recommendation import GRADE_LEVELS, grade_to_level, recommend
from app.services.graph_store import graph_store

_KEY_PREFIX = "skill:course_consult"

_EXIT_TERMS = ("算了", "不用了", "不需要了", "不用推荐", "取消", "不问了")

# 引导顺序：年级必填且放在首位；其余按推荐影响度排列。
SLOTS: list[dict] = [
    {"key": "grade", "prompt": "为了帮孩子匹配合适的班型，先了解一下：孩子目前读几年级呀？"},
    {"key": "interest", "prompt": "孩子对哪个方向更感兴趣呢？数学、科学、编程还是阅读？"},
    {"key": "foundation", "prompt": "孩子之前有没有奥数或思维训练的基础？"},
    {"key": "has_taken_class", "prompt": "孩子之前上过课外班吗？"},
    {"key": "goal", "prompt": "您希望孩子主要达到什么目标？提分、思维拓展、竞赛还是兴趣培养？"},
]


def _key(tenant_id: str, conversation_id: str) -> str:
    return f"{_KEY_PREFIX}:{tenant_id}:{conversation_id}"


def _extract_grade(text: str) -> str | None:
    t = "".join(text.split())
    age = re.search(r"(\d{1,2})\s*岁", t)
    if age:
        return age.group(0).replace(" ", "")
    for name in sorted(GRADE_LEVELS, key=len, reverse=True):
        if name in t:
            return name
    m = re.search(r"([一二三四五六七八九1-9])\s*年级", t)
    if m:
        return m.group(0).replace(" ", "")
    if "小学" in t:
        return "小学"
    if "初中" in t:
        return "初中"
    if "高中" in t:
        return "高中"
    return None


def _extract_interest(text: str) -> str | None:
    if any(k in text for k in ("编程", "python", "scratch", "机器人", "少儿编程")):
        return "编程"
    if any(k in text for k in ("科学", "实验")):
        return "科学"
    if any(k in text for k in ("数学", "奥数", "思维")):
        return "数学"
    if any(k in text for k in ("阅读", "语文", "作文")):
        return "阅读"
    return None


def _extract_foundation(text: str) -> str | None:
    # 否定优先：「没有奥数基础」含「有奥数基础」子串，必须先判否定。
    if any(k in text for k in (
        "没有奥数基础", "没奥数基础", "没学过奥数", "没有学过奥数",
        "零基础", "无基础", "没基础", "没有基础", "没学过", "没接触过",
        "从没学过", "从未学过",
    )):
        return "无"
    if any(k in text for k in (
        "有奥数基础", "有基础", "学过奥数", "上过奥数", "学过思维", "基础不错", "学过",
    )):
        return "有"
    return None


def _extract_has_taken(text: str) -> str | None:
    if any(k in text for k in ("没上过课外班", "没报过班", "没上过", "没报过", "第一次", "没有上过", "没学过课外")):
        return "否"
    if any(k in text for k in ("上过课外班", "报过班", "上过", "报过", "学过课外", "之前在")):
        return "是"
    return None


def _extract_goal(text: str) -> str | None:
    if any(k in text for k in ("竞赛", "奥赛", "杯赛", "冲刺")):
        return "竞赛"
    if any(k in text for k in ("提分", "成绩", "补差", "提高", "跟上", "巩固")):
        return "提分"
    if any(k in text for k in ("思维", "逻辑", "拓展")):
        return "思维"
    if any(k in text for k in ("兴趣", "启蒙", "培养", "喜欢")):
        return "兴趣"
    return None


_EXTRACTORS = {
    "grade": _extract_grade,
    "interest": _extract_interest,
    "foundation": _extract_foundation,
    "has_taken_class": _extract_has_taken,
    "goal": _extract_goal,
}


def _render_recommendation(result: dict) -> str:
    candidates = result["candidates"]
    if not candidates:
        return (
            "根据您提供的信息，暂时没有完全匹配的班型。建议为孩子预约一次免费能力测评，"
            "课程顾问会做更精准的建议；也可以回复「转人工」联系顾问。"
        )
    lines = ["根据孩子的情况，为您推荐以下班型："]
    for c in candidates:
        attrs = c["attributes"]
        line = f"· {c['name']}"
        code = attrs.get("编码")
        grade = attrs.get("适合年级")
        price = attrs.get("价格")
        if code:
            line += f"（{code}）"
        if grade:
            line += f"，适合{grade}"
        if price:
            line += f"，学费 {price} 元"
        lines.append(line)
    lines.append("如需报名或预约免费测评，我可以帮您转接课程顾问。")
    return "\n".join(lines)


# ── 课程咨询「技能」定义：交给 LLM 的能力契约 ─────────────────────────────
#
# 通过结构化提示词把「课程咨询」这个技能交给 LLM：LLM 读自然语言、写 JSON，
# 应用侧校验枚举、再用确定性推荐引擎出班型。LLM 输出的任何非法国标值都会被
# ``_normalize_slot_value`` 拒绝，从根上杜绝「编造班型 / 注入脏槽位」。

_EXTRACTION_SYSTEM_PROMPT = (
    "你是教育机构的课程咨询顾问，正在收集孩子信息以匹配合适的班型。\n"
    "需要收集 5 个信息（槽位）：年级、兴趣方向、奥数/思维基础、是否上过课外班、目标。\n"
    "各槽位取值规范：\n"
    "- grade（年级）：保留家长原话，如「三年级」「8岁」「初一」「学前」；\n"
    "- interest（兴趣方向）：只能是 数学/科学/编程/阅读 之一；\n"
    "- foundation（奥数/思维基础）：只能是 无/有；\n"
    "- has_taken_class（是否上过课外班）：只能是 否/是；\n"
    "- goal（目标）：只能是 提分/思维/竞赛/兴趣 之一。\n"
    "仅从用户这句话里抽取能确定的信息，没提到的槽位不要输出。\n"
    "不得编造班型、价格、校区等任何事实。\n"
    "只返回 JSON：{\"slots\":{\"grade\":\"…\",\"interest\":\"…\",\"foundation\":\"…\","
    "\"has_taken_class\":\"…\",\"goal\":\"…\"}}（没有的键省略）。"
)

_RECOMMENDATION_SYSTEM_PROMPT = (
    "你是教育机构的课程咨询顾问。下面是系统根据孩子情况确定的候选班型，"
    "请据此组织一段自然、亲切、简短的推荐话术，并引导报名或预约免费测评。\n"
    "必须严格基于候选班型，不得改动或编造班型名称、编码、价格、校区等任何事实。\n"
    "只返回 JSON：{\"reply\":\"…\"}。"
)

# 各槽位合法取值（grade 另由 grade_to_level 校验）。
_ALLOWED_SLOT_VALUES = {
    "interest": ("数学", "科学", "编程", "阅读"),
    "foundation": ("无", "有"),
    "has_taken_class": ("否", "是"),
    "goal": ("提分", "思维", "竞赛", "兴趣"),
}

COURSE_CONSULT_SKILL = {
    "name": "course_consultation",
    "description": (
        "多轮引导收集孩子信息（年级/兴趣/基础/是否上过课外班/目标），"
        "据此推荐匹配班型；班型事实由确定性推荐引擎保证，LLM 只做语义抽取与话术合成。"
    ),
    "slots": SLOTS,
    "extraction_prompt": _EXTRACTION_SYSTEM_PROMPT,
    "recommendation_prompt": _RECOMMENDATION_SYSTEM_PROMPT,
}


def _parse_json_object(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("LLM 回复不是 JSON 对象")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("LLM 回复必须是 JSON 对象")
    return data


def _normalize_slot_value(slot_key: str, value) -> str | None:
    """把 LLM 抽取的槽位值归一化并校验到合法枚举，非法值返回 None（拒绝注入）。"""
    if value is None:
        return None
    value = "".join(str(value).split())
    if not value or len(value) > 20:
        return None
    allowed = _ALLOWED_SLOT_VALUES.get(slot_key)
    if allowed is not None:
        return value if value in allowed else None
    if slot_key == "grade":
        # 年级交给 grade_to_level 校验：解析得通才接受，解析不通拒绝，避免脏值污染推荐。
        return value if grade_to_level(value) is not None else None
    return None


async def _extract_slots_with_llm(text: str, tenant_llm) -> dict[str, str]:
    """LLM 技能：把家长自然语言抽取成槽位，只返回校验通过的键值。"""
    raw = await tenant_llm.generate(
        [
            {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        thinking=False,
    )
    data = _parse_json_object(raw)
    llm_slots = data.get("slots")
    if not isinstance(llm_slots, dict):
        return {}
    result: dict[str, str] = {}
    for slot_key in ("grade", "interest", "foundation", "has_taken_class", "goal"):
        value = _normalize_slot_value(slot_key, llm_slots.get(slot_key))
        if value is not None:
            result[slot_key] = value
    return result


async def _phrase_recommendation_with_llm(tenant_llm, result: dict) -> str | None:
    """LLM 技能：在确定性候选班型之上合成推荐话术；无候选返回 None 走模板。"""
    candidates = result.get("candidates") or []
    if not candidates:
        return None
    raw = await tenant_llm.generate(
        [
            {"role": "system", "content": _RECOMMENDATION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "候选班型：" + json.dumps(candidates, ensure_ascii=False),
            },
        ],
        thinking=False,
    )
    data = _parse_json_object(raw)
    reply = str(data.get("reply") or "").strip()
    return reply[:1000] if reply else None


class CourseConsultSkill:
    """课程咨询引导状态机（Redis 状态 + 规则优先抽取 + LLM 技能兜底/合成）。"""

    async def is_active(self, tenant_id: str, conversation_id: str) -> bool:
        try:
            return bool(await redis_client.exists(_key(tenant_id, conversation_id)))
        except Exception:
            return False

    async def clear(self, tenant_id: str, conversation_id: str) -> None:
        try:
            await redis_client.delete(_key(tenant_id, conversation_id))
        except Exception:
            pass

    async def _load(self, tenant_id: str, conversation_id: str) -> dict:
        try:
            raw = await redis_client.get(_key(tenant_id, conversation_id))
        except Exception:
            raw = None
        return json.loads(raw) if raw else {"slots": {}}

    async def _save(self, tenant_id: str, conversation_id: str, state: dict) -> None:
        try:
            await redis_client.set(
                _key(tenant_id, conversation_id),
                json.dumps(state, ensure_ascii=False),
                ex=settings.context_ttl_seconds,
            )
        except Exception:
            # Redis 状态是可重建的短期副本，写失败不阻断会话。
            pass

    async def handle(self, session, payload: dict, text: str, *, tenant_llm=None) -> str:
        tenant_id = payload["tenant_id"]
        conversation_id = payload["conversation_id"]

        if any(term in text for term in _EXIT_TERMS):
            await self.clear(tenant_id, conversation_id)
            return "好的，随时可以再问我课程相关的问题。"

        state = await self._load(tenant_id, conversation_id)
        slots: dict = state.get("slots", {})

        # 1) 规则优先：确定性抽取器先填能识别的槽位（家长可能一句话带多个信息）。
        for slot_key, extractor in _EXTRACTORS.items():
            if slot_key in slots:
                continue
            value = extractor(text)
            if value:
                slots[slot_key] = value

        # 2) LLM 兜底：规则没抽到的槽位交给 LLM 技能做语义补位（校验通过才合并）。
        if tenant_llm is not None and any(s["key"] not in slots for s in SLOTS):
            try:
                llm_slots = await _extract_slots_with_llm(text, tenant_llm)
            except Exception:
                llm_slots = {}
            for slot_key, value in llm_slots.items():
                if slot_key not in slots:
                    slots[slot_key] = value

        # 找下一个未填槽位。
        next_slot = next((s for s in SLOTS if s["key"] not in slots), None)
        if next_slot is not None:
            state["slots"] = slots
            await self._save(tenant_id, conversation_id, state)
            return next_slot["prompt"]

        # 槽位收满 → 查图谱推荐。
        if session is None:
            await self.clear(tenant_id, conversation_id)
            return "信息已收到，但暂时无法查询课程库，请稍后再试或回复「转人工」。"
        classes = await graph_store.list_classes(session, tenant_id)
        result = recommend(slots, classes)
        await self.clear(tenant_id, conversation_id)

        # 3) LLM 合成推荐话术（基于确定性候选；失败/无候选回退确定性模板）。
        if tenant_llm is not None and result["candidates"]:
            try:
                phrased = await _phrase_recommendation_with_llm(tenant_llm, result)
                if phrased:
                    return phrased
            except Exception:
                pass
        return _render_recommendation(result)


course_consult_skill = CourseConsultSkill()
