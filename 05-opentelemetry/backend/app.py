import os
import random
import time
import logging

from flask import Flask, jsonify, Response

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor

from prometheus_client import Counter, Histogram, REGISTRY
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

logger = logging.getLogger("backend-service")


# -----------------------------
# OpenTelemetry
# -----------------------------

resource = Resource.create({
    "service.name": "backend-service"
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

backend_requests_total = Counter(
    "backend_requests_total",
    "Total number of requests handled by the backend",
    ["method", "route", "status"]
)

backend_request_duration_seconds = Histogram(
    "backend_request_duration_seconds",
    "Backend request duration in seconds",
    ["route"],
    buckets=(
        0.05,
        0.1,
        0.25,
        0.5,
        1,
        2,
        2.5,
        3,
        5
    )
)


# -----------------------------
# Flask
# -----------------------------

app = Flask(__name__)

FlaskInstrumentor().instrument_app(app)


def get_trace_context():
    current_span = trace.get_current_span()
    context = current_span.get_span_context()

    if not context.is_valid:
        return "unknown", "unknown"

    trace_id = format(context.trace_id, "032x")
    span_id = format(context.span_id, "016x")

    return trace_id, span_id


@app.route("/metrics")
def metrics():
    return Response(
        generate_latest(REGISTRY),
        content_type=CONTENT_TYPE_LATEST
    )


@app.route("/process")
def process():
    start_time = time.perf_counter()

    with tracer.start_as_current_span("process-order") as span:
        trace_id, span_id = get_trace_context()

        # 20% de chance de erro
        if random.random() < 0.20:

            span.set_attribute(
                "order.failed",
                True
            )

            span.set_status(
                Status(
                    StatusCode.ERROR,
                    "Simulated backend failure"
                )
            )

            duration = time.perf_counter() - start_time

            backend_requests_total.labels(
                method="GET",
                route="/process",
                status="500"
            ).inc()

            backend_request_duration_seconds.labels(
                route="/process"
            ).observe(
                duration,
                exemplar={
                    "trace_id": trace_id
                }
            )

            logger.error(
                f"service=backend-service "
                f"trace_id={trace_id} "
                f"span_id={span_id} "
                f"event=order_failed "
                f"status_code=500"
            )

            return jsonify({
                "status": "error",
                "message": "Simulated backend failure"
            }), 500

        # Latência variável
        processing_time = random.uniform(
            0.1,
            2.5
        )

        span.set_attribute(
            "order.processing_time",
            processing_time
        )

        time.sleep(processing_time)

        duration = time.perf_counter() - start_time

        backend_requests_total.labels(
            method="GET",
            route="/process",
            status="200"
        ).inc()

        backend_request_duration_seconds.labels(
            route="/process"
        ).observe(
            duration,
            exemplar={
                "trace_id": trace_id
            }
        )

        logger.info(
            f"service=backend-service "
            f"trace_id={trace_id} "
            f"span_id={span_id} "
            f"event=order_processed "
            f"processing_time={processing_time:.3f} "
            f"status_code=200"
        )

        return jsonify({
            "status": "processed",
            "processing_time": processing_time
        })


app.run(
    host="0.0.0.0",
    port=5001
)
