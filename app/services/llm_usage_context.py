from contextvars import ContextVar, Token

_calls: ContextVar[list[dict] | None] = ContextVar("llm_usage_calls", default=None)


def begin() -> Token:
    return _calls.set([])


def add(call: dict) -> None:
    calls = _calls.get()
    if calls is not None:
        calls.append(call)


def finish(token: Token) -> list[dict]:
    calls = list(_calls.get() or [])
    _calls.reset(token)
    return calls
