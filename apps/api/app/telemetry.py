"""Week 10: OpenTelemetry tracing. Off unless OTEL_EXPORTER_OTLP_ENDPOINT is set.

Spans carry ids, counts and verdicts, never prompt, document or reply text: a trace
store is a second copy of the record with weaker access control than the database.
"""

import contextlib
import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

tracer = trace.get_tracer("logbook.api")
_propagator = TraceContextTextMapPropagator()


@contextlib.contextmanager
def traced(name, **kwargs):
    """A span that records that a call failed and the exception's type, not its message.
    The SDK default stores the message, and a database error can quote the row's content."""
    with tracer.start_as_current_span(
        name, record_exception=False, set_status_on_exception=False, **kwargs
    ) as span:
        try:
            yield span
        except Exception as exc:
            span.set_status(Status(StatusCode.ERROR))
            span.set_attribute("error.type", type(exc).__name__)
            raise


def configure_tracing() -> None:
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    provider = TracerProvider(resource=Resource.create({"service.name": "logbook-api"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)


def flush_tracing() -> None:
    # Replay scripts exit right after the last request; the batch would be lost.
    provider = trace.get_tracer_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush()


def _link_from(traceparent: str | None) -> list[trace.Link]:
    if not traceparent:
        return []
    context = _propagator.extract({"traceparent": traceparent})
    span_context = trace.get_current_span(context).get_span_context()
    return [trace.Link(span_context)] if span_context.is_valid else []


def client_link(traceparent: str | None) -> list[trace.Link]:
    """A browser's trace context is a claim, so it becomes a link and never a parent.
    As a parent it would let a client place its request inside someone else's trace."""
    return _link_from(traceparent)


def current_traceparent() -> str | None:
    carrier: dict[str, str] = {}
    _propagator.inject(carrier)
    return carrier.get("traceparent")


def proposal_link(trace_context: str | None) -> list[trace.Link]:
    """An approval is a later request; this ties its trace back to the proposal's."""
    return _link_from(trace_context)
