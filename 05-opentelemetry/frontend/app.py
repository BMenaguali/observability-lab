import os
import time
import logging
import requests

from flask import Flask, jsonify, Response

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor

from prometheus_client import (
    Counter,
    Histogram,
    REGISTRY,
)

from prometheus_client.openmetrics.exposition import (
    generate_latest,
    CONTENT_TYPE_LATEST,
)


# -----------------------------
# Logging
# -----------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s"
)

logger = logging.getLogger("frontend-service")


# -----------------------------
# OpenTelemetry
# -----------------------------

resource = Resource.create({
    "service.name": "frontend-service"
})

provider = TracerProvider(resource=resource)

exporter = OTLPSpanExporter(
    endpoint=os.getenv(
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "http://otel-collector:4317"
    ),
    insecure=True
)

provider.add_span_processor(
    BatchSpanProcessor(exporter)
)

trace.set_tracer_provider(provider)

tracer = trace.get_tracer(__name__)


# -----------------------------
# Prometheus Metrics
# -----------------------------

frontend_requests_total = Counter(
    "frontend_requests_total",
    "Total number of requests handled by the frontend",
    ["method", "route", "status"]
)

frontend_request_duration_seconds = Histogram(
    "frontend_request_duration_seconds",
    "Frontend request duration in seconds",
    ["route"],
    buckets=(
        0.05,
        0.1,
        0.25,
        0.5,
        1,
        2,
        3,
        5
    )
)


# -----------------------------
# Flask
# -----------------------------

app = Flask(__name__)

FlaskInstrumentor().instrument_app(app)
RequestsInstrumentor().instrument()


def get_trace_context():
    current_span = trace.get_current_span()
    context = current_span.get_span_context()

    if not context.is_valid:
        return "unknown", "unknown"

    trace_id = format(context.trace_id, "032x")
    span_id = format(context.span_id, "016x")

    return trace_id, span_id


@app.route("/")
def home():
    return "Observability Frontend"


@app.route("/metrics")
def metrics():
    return Response(
        generate_latest(REGISTRY),
        content_type=CONTENT_TYPE_LATEST
    )


@app.route("/order")
def order():
    start_time = time.perf_counter()

    with tracer.start_as_current_span("create-order") as span:

        span.set_attribute("order.type", "lab")

        trace_id, span_id = get_trace_context()

        logger.info(
            f"service=frontend-service "
            f"trace_id={trace_id} "
            f"span_id={span_id} "
            f"event=order_started"
        )

        try:
            response = requests.get(
                "http://backend:5001/process",
                timeout=5
            )

            status_code = response.status_code

            if status_code >= 500:
                span.set_attribute("order.failed", True)

                span.set_status(
                    Status(
                        StatusCode.ERROR,
                        "Backend returned an error"
                    )
                )

                logger.error(
                    f"service=frontend-service "
                    f"trace_id={trace_id} "
                    f"span_id={span_id} "
                    f"event=backend_error "
                    f"status_code={status_code}"
                )

            else:
                logger.info(
                    f"service=frontend-service "
                    f"trace_id={trace_id} "
                    f"span_id={span_id} "
                    f"event=order_completed "
                    f"status_code={status_code}"
                )

            duration = time.perf_counter() - start_time

            frontend_requests_total.labels(
                method="GET",
                route="/order",
                status=str(status_code)
            ).inc()

            frontend_request_duration_seconds.labels(
    route="/order"
).observe(
    duration,
    exemplar={
        "trace_id": trace_id
    }
)
            return jsonify({
                "frontend": "ok",
                "backend": response.json()
            }), status_code

        except requests.RequestException as exc:
            span.record_exception(exc)

            span.set_status(
                Status(
                    StatusCode.ERROR,
                    "Backend connection failed"
                )
            )

            logger.exception(
                f"service=frontend-service "
                f"trace_id={trace_id} "
                f"span_id={span_id} "
                f"event=backend_connection_error "
                f"status_code=502"
            )

            duration = time.perf_counter() - start_time

            frontend_requests_total.labels(
                method="GET",
                route="/order",
                status="502"
            ).inc()

            frontend_request_duration_seconds.labels(
                route="/order"
            ).observe(duration)

            return jsonify({
                "frontend": "error",
                "message": "Backend connection failed"
            }), 502


app.run(
    host="0.0.0.0",
    port=5000
)
