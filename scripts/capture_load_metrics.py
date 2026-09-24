"""Capture Docker resource usage and NATS consumer backlog during a load run."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from prometheus_client.parser import text_string_to_metric_families

CAPACITY_METRICS = {
    "eduai_outbox_events",
    "eduai_queue_pending",
    "eduai_queue_ack_pending",
    "eduai_dead_letters_open",
}


def parse_prometheus_metrics(payload: str) -> list[dict]:
    """Extract only capacity/backlog samples from a Prometheus exposition."""
    samples = []
    for family in text_string_to_metric_families(payload):
        for sample in family.samples:
            if sample.name not in CAPACITY_METRICS:
                continue
            samples.append(
                {
                    "metric": sample.name,
                    "labels": dict(sample.labels),
                    "value": float(sample.value),
                }
            )
    return samples


def application_metrics(url: str) -> list[dict]:
    with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310 - local monitor
        return parse_prometheus_metrics(response.read().decode("utf-8"))


def docker_stats() -> list[dict]:
    result = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def nats_consumers(url: str) -> list[dict]:
    with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310 - local monitor
        data = json.load(response)
    consumers = []
    for account in data.get("account_details", []):
        for stream in account.get("stream_detail", []):
            for consumer in stream.get("consumer_detail", []):
                consumers.append(
                    {
                        "stream": stream.get("name"),
                        "name": consumer.get("name"),
                        "pending": consumer.get("num_pending", 0),
                        "ack_pending": consumer.get("num_ack_pending", 0),
                        "redelivered": consumer.get("num_redelivered", 0),
                    }
                )
    return consumers


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--duration", type=int, required=True)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument(
        "--nats-url", default="http://localhost:8222/jsz?consumers=true"
    )
    parser.add_argument("--metrics-url", default="http://localhost:8000/metrics")
    args = parser.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + args.duration
    with output.open("w", encoding="utf-8") as handle:
        while time.monotonic() < deadline:
            sample = {"timestamp": datetime.now(timezone.utc).isoformat()}
            try:
                sample["docker"] = docker_stats()
            except Exception as exc:  # noqa: BLE001 - evidence collector stays alive
                sample["docker_error"] = str(exc)
            try:
                sample["nats_consumers"] = nats_consumers(args.nats_url)
            except Exception as exc:  # noqa: BLE001
                sample["nats_error"] = str(exc)
            try:
                sample["application_metrics"] = application_metrics(args.metrics_url)
            except Exception as exc:  # noqa: BLE001
                sample["metrics_error"] = str(exc)
            handle.write(json.dumps(sample, ensure_ascii=False) + "\n")
            handle.flush()
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
