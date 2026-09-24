"""Prometheus 指标。"""

from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter("eduai_http_requests_total", "HTTP requests", ("method", "path", "status"))
HTTP_LATENCY = Histogram("eduai_http_request_seconds", "HTTP request latency", ("method", "path"))
TOOL_CALLS = Counter("eduai_tool_calls_total", "Tool calls", ("tool", "outcome"))
DEAD_LETTERS = Counter("eduai_dead_letters_total", "Dead letters", ("subject",))
