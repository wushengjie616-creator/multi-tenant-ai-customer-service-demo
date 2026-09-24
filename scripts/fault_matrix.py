"""Repeatable Docker fault matrix for the local interview environment.

The script is intentionally opt-in because it stops Redis, NATS and the worker.
Every stopped service is restarted from ``finally`` even when a check fails.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.core.security import create_access_token

API = "http://127.0.0.1:8000"
NATS_MONITOR = "http://127.0.0.1:8222"
TENANT_ID = "00000000-0000-0000-0000-000000000001"
USER_ID = "00000000-0000-0000-0000-000000000002"
CONVERSATION_ID = "00000000-0000-0000-0000-000000000003"


@dataclass(slots=True)
class FaultCheck:
    name: str
    passed: bool
    duration_seconds: float
    evidence: dict[str, Any]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarize_consumers(payload: dict[str, Any]) -> dict[str, Any]:
    consumers: dict[str, dict[str, int]] = {}
    for account in payload.get("account_details", []):
        for stream in account.get("stream_detail", []):
            for consumer in stream.get("consumer_detail", []):
                consumers[str(consumer["name"])] = {
                    "pending": int(consumer.get("num_pending", 0)),
                    "ack_pending": int(consumer.get("num_ack_pending", 0)),
                    "redelivered": int(consumer.get("num_redelivered", 0)),
                }
    return {
        "pending": sum(item["pending"] for item in consumers.values()),
        "ack_pending": sum(item["ack_pending"] for item in consumers.values()),
        "redelivered": sum(item["redelivered"] for item in consumers.values()),
        "consumers": consumers,
    }


def compose(*args: str) -> str:
    completed = subprocess.run(
        ["docker", "compose", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def http_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    token: str | None = None,
    timeout: float = 5,
) -> tuple[int, dict[str, Any]]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        url,
        method=method,
        headers=headers,
        data=json.dumps(payload).encode() if payload is not None else None,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode()
            return response.status, json.loads(body) if body else {}
    except HTTPError as exc:
        body = exc.read().decode()
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = {"raw": body}
        return exc.code, parsed


def wait_until(
    probe: Callable[[], Any],
    predicate: Callable[[Any], bool],
    *,
    timeout: float = 45,
    interval: float = 0.5,
) -> Any:
    deadline = time.monotonic() + timeout
    last: Any = None
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            last = probe()
            if predicate(last):
                return last
        except Exception as exc:  # service transitions are expected here
            last_error = exc
        time.sleep(interval)
    detail = f"last={last!r}"
    if last_error:
        detail += f", last_error={last_error!r}"
    raise TimeoutError(detail)


def ready() -> tuple[int, dict[str, Any]]:
    return http_json("GET", f"{API}/health/ready")


def nats_backlog() -> dict[str, Any]:
    _, payload = http_json("GET", f"{NATS_MONITOR}/jsz?consumers=true")
    return summarize_consumers(payload)


def send_message(token: str, prefix: str) -> tuple[str, int, dict[str, Any]]:
    message_id = f"fault-{prefix}-{uuid.uuid4().hex}"
    status, body = http_json(
        "POST",
        f"{API}/webhooks/im/messages",
        token=token,
        payload={
            "message_id": message_id,
            "tenant_id": TENANT_ID,
            "user_id": USER_ID,
            "conversation_id": CONVERSATION_ID,
            "content": "查询发票",
        },
    )
    return message_id, status, body


def run_check(name: str, action: Callable[[], dict[str, Any]]) -> FaultCheck:
    started = time.monotonic()
    try:
        evidence = action()
        return FaultCheck(name, True, round(time.monotonic() - started, 3), evidence)
    except Exception as exc:
        return FaultCheck(
            name,
            False,
            round(time.monotonic() - started, 3),
            {},
            f"{type(exc).__name__}: {exc}",
        )


def execute() -> list[FaultCheck]:
    token = create_access_token(tenant_id=TENANT_ID, user_id=USER_ID, role="user")
    stopped: set[str] = set()
    results: list[FaultCheck] = []

    def redis_outage() -> dict[str, Any]:
        compose("stop", "redis")
        stopped.add("redis")
        try:
            degraded = wait_until(
                ready,
                lambda item: item[0] == 503
                and str(item[1].get("checks", {}).get("redis", "")).startswith("error:"),
            )
            live_status, _ = http_json("GET", f"{API}/health/live")
            message_id, ack_status, ack_body = send_message(token, "redis")
            if live_status != 200 or ack_status != 202:
                raise AssertionError(
                    f"live={live_status}, ack={ack_status}, body={ack_body}"
                )
        finally:
            compose("start", "redis")
            stopped.discard("redis")
        recovered = wait_until(ready, lambda item: item[0] == 200)
        return {
            "degraded_ready": degraded,
            "live_status": live_status,
            "ack_status": ack_status,
            "message_id": message_id,
            "recovered_ready": recovered,
        }

    def nats_outage() -> dict[str, Any]:
        compose("stop", "nats")
        stopped.add("nats")
        try:
            degraded = wait_until(
                ready,
                lambda item: item[0] == 503
                and str(item[1].get("checks", {}).get("nats", "")).startswith("error:"),
                timeout=30,
                interval=1,
            )
            message_id, ack_status, ack_body = send_message(token, "nats")
            if ack_status != 202:
                raise AssertionError(f"ack={ack_status}, body={ack_body}")
        finally:
            compose("start", "nats")
            stopped.discard("nats")
        recovered = wait_until(ready, lambda item: item[0] == 200, timeout=60)
        return {
            "degraded_ready": degraded,
            "ack_status": ack_status,
            "message_id": message_id,
            "recovered_ready": recovered,
        }

    def queue_recovery() -> dict[str, Any]:
        baseline = wait_until(
            nats_backlog,
            lambda item: "im-inbound-worker" in item["consumers"],
            timeout=60,
        )
        compose("stop", "worker")
        stopped.add("worker")
        try:
            accepted = []
            for _ in range(12):
                message_id, status, body = send_message(token, "backlog")
                if status != 202:
                    raise AssertionError(
                        f"message={message_id}, status={status}, body={body}"
                    )
                accepted.append(message_id)
            accumulated = wait_until(
                nats_backlog,
                lambda item: (
                    item["consumers"].get("im-inbound-worker", {}).get("pending", 0)
                    + item["consumers"]
                    .get("im-inbound-worker", {})
                    .get("ack_pending", 0)
                )
                >= len(accepted),
                timeout=30,
            )
        finally:
            compose("start", "worker")
            stopped.discard("worker")
        recovered = wait_until(
            nats_backlog,
            lambda item: (
                item["consumers"].get("im-inbound-worker", {}).get("pending", 0)
                == 0
                and item["consumers"]
                .get("im-inbound-worker", {})
                .get("ack_pending", 0)
                == 0
            ),
            timeout=90,
        )
        return {
            "accepted": len(accepted),
            "baseline": baseline,
            "accumulated": accumulated,
            "recovered": recovered,
        }

    try:
        baseline = ready()
        if baseline[0] != 200:
            raise RuntimeError(f"baseline is not ready: {baseline}")
        results.append(run_check("redis-stop-recover", redis_outage))
        results.append(run_check("nats-stop-recover", nats_outage))
        results.append(run_check("queue-backlog-recover", queue_recovery))
    finally:
        for service in sorted(stopped):
            try:
                compose("start", service)
            except Exception:
                pass
        try:
            wait_until(ready, lambda item: item[0] == 200, timeout=60)
        except Exception:
            pass
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="acknowledge that Redis/NATS/worker containers will be restarted",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.apply:
        parser.error("--apply is required because this script restarts containers")

    results = execute()
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "passed": all(item.passed for item in results),
        "checks": [item.to_dict() for item in results],
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
