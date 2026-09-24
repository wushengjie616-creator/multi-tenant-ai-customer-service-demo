"""Reproducible Locust profiles selected with ``LOADTEST_SCENARIO``."""

import json
import itertools
import os
import threading
import time
import uuid

from locust import HttpUser, between, constant_throughput, task
from websockets.sync.client import connect

from loadtests.config import LoadScenario, load_scenario
from loadtests.identities import endpoint_for, identity_for

SCENARIO = load_scenario()
RATE_PER_USER = float(os.getenv("LOADTEST_RATE_PER_USER", "4"))
MESSAGE_CONTENT = os.getenv("LOADTEST_MESSAGE_CONTENT", "请介绍课程政策")
POOL_SIZE = int(os.getenv("LOADTEST_POOL_SIZE", "500"))
API_HOSTS = tuple(
    host.strip()
    for host in os.getenv("LOADTEST_API_HOSTS", "").split(",")
    if host.strip()
)
_identity_counter = itertools.count()
_identity_lock = threading.Lock()


def load_token(tenant_id: str, user_id: str) -> str:
    supplied = os.getenv("LOADTEST_TOKEN")
    if supplied:
        return supplied
    from app.core.security import create_access_token

    return create_access_token(tenant_id=tenant_id, user_id=user_id, role="user")


class AuthenticatedUser(HttpUser):
    abstract = True

    def on_start(self):
        with _identity_lock:
            index = next(_identity_counter) % POOL_SIZE
        identity = identity_for(index)
        self.tenant_id = str(identity.tenant_id)
        self.user_id = str(identity.user_id)
        self.conversation_id = str(identity.conversation_id)
        self.api_host = endpoint_for(index, API_HOSTS)
        self.token = load_token(self.tenant_id, self.user_id)
        self.client.headers.update({"Authorization": f"Bearer {self.token}"})

    def api_url(self, path: str) -> str:
        return f"{self.api_host}{path}" if self.api_host else path


class WebhookUser(AuthenticatedUser):
    abstract = True
    wait_time = constant_throughput(RATE_PER_USER)

    @task
    def send_message(self):
        payload = {
            "message_id": f"load-{uuid.uuid4().hex}",
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "conversation_id": self.conversation_id,
            "content": MESSAGE_CONTENT,
        }
        with self.client.post(
            self.api_url("/webhooks/im/messages"),
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
            self.api_url("/finance/invoice"),
            name="GET /finance/invoice",
            catch_response=True,
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
        ws_host = (self.api_host or self.host).replace("https://", "wss://").replace(
            "http://", "ws://"
        )
        started = time.perf_counter()
        try:
            self.ws = connect(
                f"{ws_host}/ws?conversation_id={self.conversation_id}",
                additional_headers={"Authorization": f"Bearer {self.token}"},
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
        ws_host = (self.api_host or self.host).replace("https://", "wss://").replace(
            "http://", "ws://"
        )
        started = time.perf_counter()
        try:
            self.ws = connect(
                f"{ws_host}/ws?conversation_id={self.conversation_id}",
                additional_headers={"Authorization": f"Bearer {self.token}"},
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
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "conversation_id": self.conversation_id,
            "content": "请介绍课程政策",
        }
        with self.client.post(
            self.api_url("/webhooks/im/messages"),
            json=payload,
            name="POST /webhooks/im/messages",
            catch_response=True,
        ) as response:
            if response.status_code != 202:
                response.failure(f"unexpected {response.status_code}: {response.text[:200]}")

    @task(2)
    def finance(self):
        self.client.get(
            self.api_url("/finance/invoice"), name="GET /finance/invoice"
        )

    @task(1)
    def knowledge(self):
        self.client.post(
            self.api_url("/knowledge/query"),
            json={"question": "如何申请退费？"},
            name="POST /knowledge/query",
        )
