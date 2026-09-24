"""P3 工具调用安全管线；任何前置失败都不得触发 handler。"""

import pytest
from pydantic import BaseModel, ConfigDict

from app.core.security import AuthContext
from app.services.tool_service import ToolDefinition, ToolPolicyError, ToolRegistry


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource_id: str


@pytest.fixture
def tool_setup():
    calls = []

    async def handler(args):
        calls.append(args)
        return {"ok": True}

    async def owner(auth, args):
        return args["resource_id"] == auth.user_id

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="close_auto_renew",
            args_model=Args,
            allowed_roles=frozenset({"user"}),
            handler=handler,
            ownership_check=owner,
            requires_confirmation=True,
        )
    )
    return registry, calls


@pytest.mark.parametrize(
    ("name", "args", "role", "confirmed", "expected_code"),
    [
        ("unknown", {"resource_id": "u1"}, "user", True, "unknown_tool"),
        ("close_auto_renew", {}, "user", True, "invalid_arguments"),
        (
            "close_auto_renew",
            {"resource_id": "u1", "tenant_id": "forged"},
            "user",
            True,
            "invalid_arguments",
        ),
        ("close_auto_renew", {"resource_id": "u1"}, "admin", True, "role_denied"),
        (
            "close_auto_renew",
            {"resource_id": "other"},
            "user",
            True,
            "ownership_denied",
        ),
        (
            "close_auto_renew",
            {"resource_id": "u1"},
            "user",
            False,
            "confirmation_required",
        ),
    ],
)
async def test_invalid_candidates_never_call_handler(
    tool_setup, name, args, role, confirmed, expected_code
):
    registry, calls = tool_setup
    auth = AuthContext(tenant_id="t1", user_id="u1", role=role)
    with pytest.raises(ToolPolicyError) as error:
        await registry.execute_candidate(
            auth=auth, name=name, raw_args=args, confirmed=confirmed
        )
    assert error.value.code == expected_code
    assert calls == []


async def test_valid_confirmed_candidate_executes_once(tool_setup):
    registry, calls = tool_setup
    result = await registry.execute_candidate(
        auth=AuthContext(tenant_id="t1", user_id="u1", role="user"),
        name="close_auto_renew",
        raw_args={"resource_id": "u1"},
        confirmed=True,
    )
    assert result == {"ok": True}
    assert calls == [{"resource_id": "u1"}]
