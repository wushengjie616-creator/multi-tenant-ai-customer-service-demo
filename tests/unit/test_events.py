from app.schemas.event import EventEnvelope, EventType


def test_envelope_roundtrip():
    env = EventEnvelope(
        trace_id="t1", tenant_id="tenant-1", type=EventType.IM_INBOUND, payload={"a": 1}
    )
    restored = EventEnvelope.model_validate_json(env.model_dump_json())
    assert restored.trace_id == "t1"
    assert restored.tenant_id == "tenant-1"
    assert restored.type == EventType.IM_INBOUND
    assert restored.payload == {"a": 1}


def test_envelope_defaults():
    env = EventEnvelope(trace_id="t", tenant_id="x", type="im.inbound")
    assert env.schema_version == "1.0"
    assert env.event_id
    assert env.occurred_at is not None
