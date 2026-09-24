"""日志脱敏单测（NFR3-07 / NFR4-05）：日志落地前不含完整 PII / token / password。"""

import json
import logging

from app.core.logging import JsonFormatter


def _format(msg, extra_fields=None):
    rec = logging.LogRecord(
        name="test", level=logging.INFO, pathname="x", lineno=1, msg=msg, args=(), exc_info=None
    )
    rec.extra_fields = extra_fields or {}
    return json.loads(JsonFormatter().format(rec))


def test_message_pii_is_masked():
    d = _format("zhangsan@example.com 打来 13812345678")
    assert "zhangsan@example.com" not in d["msg"]
    assert "z***@example.com" in d["msg"]
    assert "13812345678" not in d["msg"]
    assert "138****5678" in d["msg"]


def test_sensitive_extra_keys_are_redacted():
    d = _format("op", {"authorization": "Bearer eyJhbGciOi.abc.def", "password": "pw123"})
    assert d["authorization"] == "[REDACTED]"
    assert d["password"] == "[REDACTED]"


def test_nested_extra_values_mask_pii_and_keys():
    d = _format("op", {"meta": {"token": "secret-jwt", "email": "zhangsan@example.com"}})
    assert d["meta"]["token"] == "[REDACTED]"
    assert d["meta"]["email"] == "z***@example.com"


def test_plain_fields_not_sensitive_pass_through():
    d = _format("op", {"trace": "t-1", "count": 5})
    assert d["trace"] == "t-1"
    assert d["count"] == 5
