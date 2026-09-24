"""结构化 JSON 日志 + trace 上下文。

用法:
    from app.core.logging import get_logger, log_with_context, set_trace_id
    log = get_logger(__name__)
    log_with_context(log, logging.INFO, "msg", k=v)
"""

import contextvars
import json
import logging
import sys
from datetime import datetime, timezone

from app.utils.masking import mask_pii, mask_value

trace_id_var = contextvars.ContextVar("trace_id", default=None)
tenant_id_var = contextvars.ContextVar("tenant_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": mask_pii(record.getMessage()),
            "trace_id": trace_id_var.get(),
            "tenant_id": tenant_id_var.get(),
        }
        if record.exc_info:
            payload["exc_info"] = mask_pii(self.formatException(record.exc_info))
        extra = getattr(record, "extra_fields", {})
        if extra:
            # 整体交给 mask_value：敏感字段名（token/password/authorization/...）整值打码，
            # 其余值递归脱敏。逐 value 单独调 mask_value 会漏掉按 key 打码的场景。
            for key, value in mask_value(dict(extra)).items():
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def set_trace_id(trace_id: str | None) -> None:
    trace_id_var.set(trace_id)


def get_trace_id() -> str | None:
    return trace_id_var.get()


def set_tenant_id(tenant_id: str | None) -> None:
    tenant_id_var.set(tenant_id)


def get_tenant_id() -> str | None:
    return tenant_id_var.get()


def log_with_context(logger: logging.Logger, level: int, msg: str, **fields) -> None:
    logger.log(level, msg, extra={"extra_fields": fields})
