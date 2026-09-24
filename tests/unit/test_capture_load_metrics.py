from scripts import capture_load_metrics


def test_parse_prometheus_metrics_keeps_outbox_and_runtime_backlog_only():
    payload = """
# HELP eduai_outbox_events Persisted outbox events by status
eduai_outbox_events{status="pending"} 17
eduai_outbox_events{status="published"} 83
eduai_queue_pending{consumer="im-inbound-worker",stream="EVENTS"} 4
python_gc_objects_collected_total{generation="0"} 999
"""

    parser = getattr(capture_load_metrics, "parse_prometheus_metrics", lambda _: [])
    assert parser(payload) == [
        {"metric": "eduai_outbox_events", "labels": {"status": "pending"}, "value": 17.0},
        {"metric": "eduai_outbox_events", "labels": {"status": "published"}, "value": 83.0},
        {
            "metric": "eduai_queue_pending",
            "labels": {"consumer": "im-inbound-worker", "stream": "EVENTS"},
            "value": 4.0,
        },
    ]
