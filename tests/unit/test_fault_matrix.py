from scripts.fault_matrix import FaultCheck, summarize_consumers


def test_summarize_consumers_aggregates_backlog_without_high_cardinality_data():
    payload = {
        "account_details": [
            {
                "stream_detail": [
                    {
                        "consumer_detail": [
                            {
                                "name": "worker-a",
                                "num_pending": 7,
                                "num_ack_pending": 2,
                                "num_redelivered": 1,
                            },
                            {
                                "name": "worker-b",
                                "num_pending": 3,
                                "num_ack_pending": 0,
                                "num_redelivered": 0,
                            },
                        ]
                    }
                ]
            }
        ]
    }

    assert summarize_consumers(payload) == {
        "pending": 10,
        "ack_pending": 2,
        "redelivered": 1,
        "consumers": {
            "worker-a": {"pending": 7, "ack_pending": 2, "redelivered": 1},
            "worker-b": {"pending": 3, "ack_pending": 0, "redelivered": 0},
        },
    }


def test_fault_check_serializes_observed_evidence():
    check = FaultCheck(
        name="redis-stop",
        passed=True,
        duration_seconds=1.25,
        evidence={"live": 200, "ready": 503},
    )

    assert check.to_dict() == {
        "name": "redis-stop",
        "passed": True,
        "duration_seconds": 1.25,
        "evidence": {"live": 200, "ready": 503},
        "error": None,
    }
