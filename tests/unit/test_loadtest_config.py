from loadtests.config import LoadScenario, load_scenario


def test_load_scenario_defaults_to_mixed(monkeypatch):
    monkeypatch.delenv("LOADTEST_SCENARIO", raising=False)

    assert load_scenario() is LoadScenario.MIXED


def test_load_scenario_accepts_required_interview_profiles(monkeypatch):
    for value, expected in (
        ("stable", LoadScenario.STABLE),
        ("burst", LoadScenario.BURST),
        ("finance", LoadScenario.FINANCE),
        ("llm-timeout", LoadScenario.LLM_TIMEOUT),
    ):
        monkeypatch.setenv("LOADTEST_SCENARIO", value)
        assert load_scenario() is expected


def test_load_scenario_rejects_unknown_value(monkeypatch):
    monkeypatch.setenv("LOADTEST_SCENARIO", "typo")

    try:
        load_scenario()
    except ValueError as exc:
        assert "LOADTEST_SCENARIO" in str(exc)
    else:
        raise AssertionError("unknown load-test profiles must fail closed")
