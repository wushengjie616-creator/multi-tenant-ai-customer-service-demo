from mocks.mock_llm.main import ChatMessage, build_reply, should_inject


def test_mock_llm_summarizes_grounded_prompt_instead_of_echoing_all_evidence():
    reply = build_reply(
        [
            ChatMessage(
                role="user",
                content=(
                    "用户问题：退费政策？\n\n可信证据：\n"
                    "# 退费政策\n\n- 3 个工作日内审核。\n"
                    "- 退款 7-15 个工作日原路退回。\n"
                    "## 其他\n- 已发放的教材费不退。"
                ),
            )
        ]
    )

    assert reply.startswith("根据知识库资料：")
    assert "3 个工作日内审核" in reply
    assert "用户问题" not in reply
    assert "# 退费政策" not in reply


def test_mock_llm_selects_the_evidence_lines_closest_to_the_question():
    reply = build_reply(
        [
            ChatMessage(
                role="user",
                content=(
                    "用户问题：开课 30 天后还能退费吗？\n\n可信证据：\n"
                    "### Q: 你们只教英语吗？\nA: 是的。\n"
                    "### Q: 开课 30 天后还能退费吗？\n"
                    "A: 原则上不予退费，特殊情况可申请人工审核。"
                ),
            )
        ]
    )

    assert "原则上不予退费" in reply
    assert "你们只教英语" not in reply


def test_mock_llm_can_inject_exactly_twenty_percent_deterministically():
    injected = [number for number in range(1, 11) if should_inject(number, every=5)]

    assert injected == [5, 10]
    assert not should_inject(1, every=0)
