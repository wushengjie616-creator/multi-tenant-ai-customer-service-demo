"""Prometheus metrics with bounded labels and real call-site wiring."""

from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter("eduai_http_requests_total", "HTTP requests", ("method", "path", "status"))
HTTP_LATENCY = Histogram("eduai_http_request_seconds", "HTTP request latency", ("method", "path"))
TOOL_CALLS = Counter("eduai_tool_calls_total", "Tool calls", ("tool", "outcome"))
DEAD_LETTERS = Counter("eduai_dead_letters_total", "Dead letters", ("subject",))
MESSAGE_PROCESSING = Histogram(
    "eduai_message_processing_seconds", "Inbound message processing latency", ("outcome",)
)
QUEUE_PENDING = Gauge(
    "eduai_queue_pending", "JetStream messages waiting for delivery", ("stream", "consumer")
)
QUEUE_ACK_PENDING = Gauge(
    "eduai_queue_ack_pending", "JetStream delivered messages awaiting ACK", ("stream", "consumer")
)
LLM_REQUESTS = Counter(
    "eduai_llm_requests_total", "LLM requests", ("provider", "model", "outcome")
)
LLM_LATENCY = Histogram(
    "eduai_llm_request_seconds", "LLM request latency", ("provider", "model", "outcome")
)
LLM_TOKEN_USAGE = Gauge(
    "eduai_llm_tokens_stored", "Persisted LLM token usage", ("tenant", "model", "token_type")
)
LLM_GATE_INFLIGHT = Gauge(
    "eduai_llm_gate_inflight", "Currently admitted LLM calls", ("gate",)
)
CIRCUIT_BREAKER_STATE = Gauge(
    "eduai_circuit_breaker_state", "Circuit state: closed=0 half_open=1 open=2", ("downstream",)
)
OUTBOX_STATUS = Gauge(
    "eduai_outbox_events", "Persisted outbox events by status", ("status",)
)
DEAD_LETTER_OPEN = Gauge("eduai_dead_letters_open", "Open persisted dead letters")
HANDOFF_ACTIVE = Gauge("eduai_handoffs_active", "Active handoffs")
