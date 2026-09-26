"""课程咨询推荐引擎单测：年级硬过滤 + 兴趣/基础/目标打分，确定性可复现。"""

from app.services.course_recommendation import (
    grade_to_level,
    parse_grade_range,
    recommend,
)

# 与 sample-data/tenants/xinghe-future/knowledge/course-catalog.md 六班一致的属性子集。
CATALOG = [
    {"name": "数学思维启蒙班 M1", "attributes": {"科目": "数学", "适合年级": "学前至一年级", "需基础": "none", "目标": "兴趣"}},
    {"name": "数学思维同步班 B2", "attributes": {"科目": "数学", "适合年级": "一至二年级", "需基础": "none", "目标": "提分"}},
    {"name": "数学思维进阶班 A3", "attributes": {"科目": "数学", "适合年级": "三至四年级"}},
    {"name": "科学探究实验班 S2", "attributes": {"科目": "科学", "适合年级": "三至六年级"}},
    {"name": "Python 创意编程 P1", "attributes": {"科目": "编程", "适合年级": "五至八年级"}},
    {"name": "奥数竞赛冲刺班 C1", "attributes": {"科目": "数学", "适合年级": "四至六年级", "需基础": "olympiad", "目标": "竞赛"}},
]


def test_grade_to_level_covers_grade_words_and_age():
    assert grade_to_level("三年级") == 3
    assert grade_to_level("初一") == 7
    assert grade_to_level("高一") == 10
    assert grade_to_level("学前") == 0
    assert grade_to_level("8岁") == 2  # 8 - 6
    assert grade_to_level("") is None
    assert grade_to_level("随便") is None


def test_parse_grade_range_handles_x至y_and_whole_words():
    assert parse_grade_range("学前至一年级") == (0, 1)
    assert parse_grade_range("一至二年级") == (1, 2)
    assert parse_grade_range("四至六年级") == (4, 6)  # 「四」与「六」不构成「四年级」词
    assert parse_grade_range("三至六年级") == (3, 6)
    assert parse_grade_range("五至八年级") == (5, 8)
    assert parse_grade_range("三年级") == (3, 3)
    assert parse_grade_range("") == (0, 12)


def test_recommend_filters_by_grade_and_prefers_subject_match():
    result = recommend(
        {"grade": "三年级", "interest": "数学", "foundation": "无", "has_taken_class": "否", "goal": "提分"},
        CATALOG,
    )
    names = [c["name"] for c in result["candidates"]]
    # 三年级排除 M1(0-1)/B2(1-2)/P1(5-8)/C1(4-6)；数学 A3 胜过科学 S2。
    assert names == ["数学思维进阶班 A3"]
    assert result["total"] == 1
    assert "科目数学匹配" in result["candidates"][0]["reasons"]


def test_recommend_prefers_olympiad_foundation_and_goal_match():
    result = recommend(
        {"grade": "四年级", "interest": "数学", "foundation": "有", "has_taken_class": "是", "goal": "竞赛"},
        CATALOG,
    )
    names = [c["name"] for c in result["candidates"]]
    assert names[0] == "奥数竞赛冲刺班 C1"  # 有基础 + 竞赛目标命中
    assert "有基础可进阶" in result["candidates"][0]["reasons"]


def test_recommend_returns_empty_when_no_grade_match():
    result = recommend(
        {"grade": "高三", "interest": "数学", "foundation": "无", "goal": "提分"},
        CATALOG,
    )
    assert result["candidates"] == []
    assert result["total"] == 0


def test_recommend_returns_top_two_by_score():
    result = recommend(
        {"grade": "四年级", "interest": "数学", "foundation": "无", "goal": "兴趣"},
        CATALOG,
    )
    names = [c["name"] for c in result["candidates"]]
    # A3(三至四) 与 C1(四至六) 均覆盖四年级；无基础时 A3 更高、C1 需奥数基础被降分。
    assert names == ["数学思维进阶班 A3", "奥数竞赛冲刺班 C1"]


def test_recommend_skips_class_nodes_without_grade_range():
    # 政策/FAQ 被误判成 class 的节点（无「适合年级」）不得进入候选。
    result = recommend(
        {"grade": "三年级", "interest": "数学", "foundation": "无", "goal": "提分"},
        CATALOG + [{"name": "请假、补课与转班", "attributes": {"课时数": 3}}],
    )
    names = [c["name"] for c in result["candidates"]]
    assert "请假、补课与转班" not in names
    assert names == ["数学思维进阶班 A3"]
