"""Reproducible Locust profiles selected with ``LOADTEST_SCENARIO``."""

import json
import os
import time
import uuid

from locust import HttpUser, between, constant_throughput, task
from websockets.sync.client import connect

from loadtests.config import LoadScenario, load_scenario

TENANT_ID = os.getenv("LOADTEST_TENANT_ID", "00000000-0000-0000-0000-000000000001")
USER_ID = os.getenv("LOADTEST_USER_ID", "00000000-0000-0000-0000-000000000002")
CONVERSATION_ID = os.getenv(
    "LOADTEST_CONVERSATION_ID", "00000000-0000-0000-0000-000000000003"
)
SCENARIO = load_scenario()
RATE_PER_USER = float(os.getenv("LOADTEST_RATE_PER_USER", "4"))
MESSAGE_CONTENT = os.getenv("LOADTEST_MESSAGE_CONTENT", "请介绍课程政策")


def load_token() -> str:
    supplied = os.getenv("LOADTEST_TOKEN")
    if supplied:
        return supplied
    from app.core.security import create_access_token

    return create_access_token(tenant_id=TENANT_ID, user_id=USER_ID, role="user")


TOKEN = load_token()


class AuthenticatedUser(HttpUser):
    abstract = True

    def on_start(self):
        self.client.headers.update({"Authorization": f"Bearer {TOKEN}"})


class WebhookUser(AuthenticatedUser):
    abstract = True
    wait_time = constant_throughput(RATE_PER_USER)

    @task
    def send_message(self):
        payload = {
            "message_id": f"load-{uuid.uuid4().hex}",
            "tenant_id": TENANT_ID,
            "user_id": USER_ID,
            "conversation_id": CONVERSATION_ID,
            "content": MESSAGE_CONTENT,
        }
        with self.client.post(
            "/webhooks/im/messages",
            json=payload,
            name="POST /webhooks/im/messages",
            catch_response=True,
        ) as response:
            if response.status_code != 202:
                response.failure(f"unexpected {response.status_code}: {response.text[:200]}")


class StableMessageUser(WebhookUser):
    abstract = SCENARIO is not LoadScenario.STABLE


class BurstMessageUser(WebhookUser):
    abstract = SCENARIO is not LoadScenario.BURST


class FinanceUser(AuthenticatedUser):
    abstract = SCENARIO is not LoadScenario.FINANCE
    wait_time = constant_throughput(RATE_PER_USER)

    @task
    def finance(self):
        with self.client.get(
            "/finance/invoice", name="GET /finance/invoice", catch_response=True
        ) as response:
            if response.status_code != 200:
                response.failure(f"unexpected status: {response.status_code}")
                return
            try:
                status = response.json().get("status")
            except ValueError:
                response.failure("finance response is not JSON")
                return
            if status not in {"ok", "unavailable"}:
                response.failure(f"invalid finance status: {status}")


class WebSocketUser(AuthenticatedUser):
    abstract = True
    wait_time = between(0.05, 0.1)

    def on_start(self):
        super().on_start()
        self.ws = None
        self._connect()

    def _connect(self):
        ws_host = self.host.replace("https://", "wss://").replace("http://", "ws://")
        started = time.perf_counter()
        try:
            self.ws = connect(
                f"{ws_host}/ws?conversation_id={CONVERSATION_ID}",
                additional_headers={"Authorization": f"Bearer {TOKEN}"},
                open_timeout=5,
                proxy=None,
                legacy=True,
            )
            error = None
        except Exception as exc:
            self.ws = None
            error = exc
        self.environment.events.request.fire(
            request_type="WS",
            name="connect",
            response_time=(time.perf_counter() - started) * 1000,
            response_length=0,
            exception=error,
        )

    def on_stop(self):
        if self.ws is not None:
            self.ws.close()

    @task
    def websocket_message(self):
        if self.ws is None:
            self._connect()
            if self.ws is None:
                return
        message_id = f"load-ws-{uuid.uuid4().hex}"
        started = time.perf_counter()
        total_bytes = 0
        error = None
        ack_recorded = False
        first_response_recorded = False
        try:
            self.ws.send(
                json.dumps(
                    {
                        "type": "message.send",
                        "payload": {"message_id": message_id, "content": "你好"},
                    },
                    ensure_ascii=False,
                )
            )
            while True:
                raw = self.ws.recv(timeout=15)
                total_bytes += len(raw)
                event = json.loads(raw)
                event_type = event.get("type")
                payload = event.get("payload", {})
                if (
                    not ack_recorded
                    and event_type == "message.accepted"
                    and event.get("message_id") == message_id
                ):
                    self.environment.events.request.fire(
                        request_type="WS",
                        name="message ACK",
                        response_time=(time.perf_counter() - started) * 1000,
                        response_length=len(raw),
                        exception=None,
                    )
                    ack_recorded = True
                if (
                    not first_response_recorded
                    and event_type == "reply.chunk"
                    and payload.get("in_reply_to") == message_id
                ):
                    self.environment.events.request.fire(
                        request_type="WS",
                        name="first response",
                        response_time=(time.perf_counter() - started) * 1000,
                        response_length=len(raw),
                        exception=None,
                    )
                    first_response_recorded = True
                if (
                    event_type == "reply.end"
                    and payload.get("in_reply_to") == message_id
                ):
                    break
        except Exception as exc:
            error = exc
            try:
                self.ws.close()
            finally:
                self.ws = None
        self.environment.events.request.fire(
            request_type="WS",
            name="full response",
            response_time=(time.perf_counter() - started) * 1000,
            response_length=total_bytes,
            exception=error,
        )


class LLMTimeoutUser(WebSocketUser):
    abstract = SCENARIO is not LoadScenario.LLM_TIMEOUT


class WebSocketCapacityUser(AuthenticatedUser):
    """Open and hold one authenticated WebSocket per simulated user."""

    abstract = SCENARIO is not LoadScenario.WS_CAPACITY
    wait_time = between(1, 2)

    def on_start(self):
        super().on_start()
        self.ws = None
        self._connect()

    def _connect(self):
        ws_host = self.host.replace("https://", "wss://").replace("http://", "ws://")
        started = time.perf_counter()
        try:
            self.ws = connect(
                f"{ws_host}/ws?conversation_id={CONVERSATION_ID}",
                additional_headers={"Authorization": f"Bearer {TOKEN}"},
                open_timeout=5,
                proxy=None,
                legacy=True,
            )
            error = None
        except Exception as exc:
            self.ws = None
            error = exc
        self.environment.events.request.fire(
            request_type="WS",
            name="connect and hold",
            response_time=(time.perf_counter() - started) * 1000,
            response_length=0,
            exception=error,
        )

    def on_stop(self):
        if self.ws is not None:
            self.ws.close()

    @task
    def websocket_message(self):
        if self.ws is None:
            self._connect()


class MixedCustomerUser(WebSocketUser):
    """Small end-to-end smoke profile retained for local development."""

    abstract = SCENARIO is not LoadScenario.MIXED

    @task(8)
    def send_message(self):
        payload = {
            "message_id": f"load-{uuid.uuid4().hex}",
            "tenant_id": TENANT_ID,
            "user_id": USER_ID,
            "conversation_id": CONVERSATION_ID,
            "content": "请介绍课程政策",
        }
        with self.client.post(
            "/webhooks/im/messages",
            json=payload,
            name="POST /webhooks/im/messages",
            catch_response=True,
        ) as response:
            if response.status_code != 202:
                response.failure(f"unexpected {response.status_code}: {response.text[:200]}")

    @task(2)
    def finance(self):
        self.client.get("/finance/invoice", name="GET /finance/invoice")

    @task(1)
    def knowledge(self):
        self.client.post(
            "/knowledge/query",
            json={"question": "如何申请退费？"},
            name="POST /knowledge/query",
        )
