"""OpenTelemetry setup plus W3C context propagation for queued events."""

from contextlib import contextmanager

from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.core.config import settings

_configured = False


def configure_tracing(app=None) -> None:
    global _configured
    if not _configured:
        provider = TracerProvider(resource=Resource.create({"service.name": settings.service_name}))
        if settings.otel_exporter_otlp_endpoint:
            endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/") + "/v1/traces"
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)
        HTTPXClientInstrumentor().instrument()
        _configured = True
    if app is not None and not getattr(app.state, "otel_instrumented", False):
        FastAPIInstrumentor.instrument_app(app, excluded_urls="health/live,health/ready,metrics")
        app.state.otel_instrumented = True


def current_traceparent() -> str | None:
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier.get("traceparent")


@contextmanager
def event_span(name: str, envelope: dict):
    carrier = {"traceparent": envelope.get("traceparent", "")}
    context = propagate.extract(carrier)
    with trace.get_tracer("education-ai-service.events").start_as_current_span(name, context=context) as span:
        span.set_attribute("messaging.system", "nats")
        span.set_attribute("messaging.message.id", str(envelope.get("event_id") or ""))
        yield span
