from opentelemetry import trace

from app.core.tracing import configure_tracing, current_traceparent, event_span


def test_trace_context_survives_event_boundary():
    configure_tracing()
    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("producer") as producer:
        traceparent = current_traceparent()
        producer_trace_id = producer.get_span_context().trace_id

    assert traceparent and traceparent.startswith("00-")
    with event_span("consumer", {"event_id": "event-1", "traceparent": traceparent}):
        assert trace.get_current_span().get_span_context().trace_id == producer_trace_id
