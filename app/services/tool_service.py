"""LLM 候选工具调用的确定性安全执行管线。"""

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ValidationError

from app.core.security import AuthContext
from app.core.metrics import TOOL_CALLS


class ToolPolicyError(ValueError):
    """可安全暴露给上层编排器的确定性策略拒绝。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


ToolHandler = Callable[[dict[str, Any]], Awaitable[Any]]
OwnershipCheck = Callable[[AuthContext, dict[str, Any]], Awaitable[bool]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    args_model: type[BaseModel]
    allowed_roles: frozenset[str]
    handler: ToolHandler
    ownership_check: OwnershipCheck
    requires_confirmation: bool = False


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._tools:
            raise ValueError(f"duplicate tool: {definition.name}")
        self._tools[definition.name] = definition

    async def execute_candidate(
        self,
        *,
        auth: AuthContext,
        name: str,
        raw_args: dict[str, Any],
        confirmed: bool = False,
    ) -> Any:
        definition = self._tools.get(name)
        if definition is None:
            TOOL_CALLS.labels(name, "unknown_tool").inc()
            raise ToolPolicyError("unknown_tool", "tool is not allowlisted")

        try:
            validated = definition.args_model.model_validate(raw_args)
        except ValidationError as exc:
            TOOL_CALLS.labels(name, "invalid_arguments").inc()
            raise ToolPolicyError(
                "invalid_arguments", "tool arguments failed schema validation"
            ) from exc
        arguments = validated.model_dump()

        if auth.role not in definition.allowed_roles:
            TOOL_CALLS.labels(name, "role_denied").inc()
            raise ToolPolicyError("role_denied", "role is not allowed to call tool")
        if not await definition.ownership_check(auth, arguments):
            TOOL_CALLS.labels(name, "ownership_denied").inc()
            raise ToolPolicyError(
                "ownership_denied", "resource does not belong to authenticated user"
            )
        if definition.requires_confirmation and not confirmed:
            TOOL_CALLS.labels(name, "confirmation_required").inc()
            raise ToolPolicyError(
                "confirmation_required", "explicit confirmation is required"
            )
        try:
            result = await definition.handler(arguments)
        except Exception:
            TOOL_CALLS.labels(name, "failed").inc()
            raise
        TOOL_CALLS.labels(name, "success").inc()
        return result
