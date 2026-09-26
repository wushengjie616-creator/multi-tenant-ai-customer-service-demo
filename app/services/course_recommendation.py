"""课程咨询推荐引擎：确定性槽位匹配 + 图谱班型打分。

不依赖 LLM——只从槽位与班型 `attributes` 做年级硬过滤 + 兴趣/基础/目标加分，
输出带理由的候选班型。LLM 只负责把候选合成自然语言话术（见 course_consult_service）。

年级统一映射为数字等级：学前=0，一年级=1 … 六年级=6，初一=7 … 高三=12。
班型「适合年级」属性解析为 [min_level, max_level] 区间做包含匹配。
"""

import re

# 年级词 → 数字等级。长词优先匹配（如「一年级」不会被「年级」误伤）。
GRADE_LEVELS: dict[str, int] = {
    "学前": 0, "幼儿园": 0, "托班": 0,
    "一年级": 1, "二年级": 2, "三年级": 3,
    "四年级": 4, "五年级": 5, "六年级": 6,
    "七年级": 7, "八年级": 8, "九年级": 9,
    "初一": 7, "初二": 8, "初三": 9,
    "高一": 10, "高二": 11, "高三": 12,
}

SUBJECTS = ("数学", "科学", "编程", "阅读")
GOALS = ("提分", "思维", "竞赛", "兴趣")

_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_to_int(token: str) -> int | None:
    if token in _CN_NUM:
        return _CN_NUM[token]
    if token.isdigit():
        return int(token)
    return None


def grade_to_level(raw: str) -> int | None:
    """家长表达的年级/年龄 → 数字等级；无法识别返回 None。"""
    text = "".join(str(raw).split())
    if not text:
        return None
    age = re.search(r"(\d{1,2})\s*岁", text)
    if age:
        return max(0, min(12, int(age.group(1)) - 6))
    for name, level in sorted(GRADE_LEVELS.items(), key=lambda kv: -len(kv[0])):
        if name in text:
            return level
    m = re.search(r"([一二三四五六七八九1-9])\s*年级", text)
    if m:
        level = _cn_to_int(m.group(1))
        if level is not None:
            return level
    if "小学" in text:
        return 3
    if "初中" in text:
        return 7
    if "高中" in text:
        return 10
    return None


def parse_grade_range(value: str) -> tuple[int, int]:
    """解析班型「适合年级」为 [min_level, max_level]；无法解析视为不限 (0, 12)。"""
    text = "".join(str(value).split())
    levels: list[int] = []
    for name, level in GRADE_LEVELS.items():
        if name in text:
            levels.append(level)
    for m in re.finditer(r"([一二三四五六七八九1-9])\s*年级", text):
        level = _cn_to_int(m.group(1))
        if level is not None:
            levels.append(level)
    range_match = re.search(r"([一二三四五六七八九1-9])至([一二三四五六七八九1-9])", text)
    if range_match:
        lo = _cn_to_int(range_match.group(1))
        hi = _cn_to_int(range_match.group(2))
        if lo is not None and hi is not None:
            levels.extend([lo, hi])
    if not levels:
        return (0, 12)
    return (min(levels), max(levels))


def _score(attrs: dict, interest, foundation, has_taken, goal) -> tuple[int, list[str]]:
    score = 1  # 年级匹配基础分（已通过硬过滤）
    reasons = ["年级匹配"]
    subject = attrs.get("科目")
    if interest:
        if subject == interest:
            score += 2
            reasons.append(f"科目{interest}匹配")
        elif subject:
            score -= 2
            reasons.append(f"科目不符（{subject}）")
    req = attrs.get("需基础")
    if foundation == "无" and req == "none":
        score += 2
        reasons.append("零基础可入")
    elif foundation == "无" and req == "olympiad":
        score -= 2
        reasons.append("需奥数基础")
    elif foundation == "有" and req == "olympiad":
        score += 2
        reasons.append("有基础可进阶")
    elif foundation == "有" and req == "none":
        score -= 1
        reasons.append("班型偏基础")
    if goal and attrs.get("目标") == goal:
        score += 1
        reasons.append("目标匹配")
    if has_taken == "是" and (req == "olympiad" or attrs.get("目标") == "竞赛"):
        score += 1
        reasons.append("有基础可挑战")
    return score, reasons


def recommend(slots: dict, classes: list[dict]) -> dict:
    """确定性推荐：年级硬过滤 + 打分，返回候选班型与理由。

    slots 键：grade / interest / foundation / has_taken_class / goal。
    返回 ``{"candidates": [...], "total": N}``；候选为空表示该租户无匹配班型。
    """
    grade_level = grade_to_level(slots.get("grade", ""))
    interest = slots.get("interest")
    foundation = slots.get("foundation")
    has_taken = slots.get("has_taken_class")
    goal = slots.get("goal")

    scored: list[tuple[int, list[str], dict]] = []
    for cls in classes:
        attrs = cls.get("attributes") or {}
        # 只推荐带「适合年级」的班型；政策/FAQ 等被抽取器误判成 class 的节点（无适合年级）
        # 不得进入候选（母本「不产生班型之外的结果」）。
        grade_range = attrs.get("适合年级")
        if not grade_range:
            continue
        if grade_level is not None:
            lo, hi = parse_grade_range(grade_range)
            if not (lo <= grade_level <= hi):
                continue
        score, reasons = _score(attrs, interest, foundation, has_taken, goal)
        if score > 0:
            scored.append((score, reasons, cls))

    scored.sort(key=lambda item: item[0], reverse=True)
    candidates = [
        {
            "name": cls["name"],
            "attributes": cls.get("attributes") or {},
            "score": score,
            "reasons": reasons,
        }
        for score, reasons, cls in scored
    ]
    return {"candidates": candidates[:2], "total": len(scored)}
