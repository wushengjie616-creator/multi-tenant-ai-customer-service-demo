"""业务异常基类与常用异常。"""


class AppError(Exception):
    """业务异常基类，带错误码和状态码。"""

    def __init__(
        self,
        message: str,
        *,
        code: str = "APP_ERROR",
        status_code: int = 500,
        detail: dict | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.detail = detail or {}


class NotFoundError(AppError):
    def __init__(self, message: str = "资源不存在", **kwargs):
        super().__init__(message, code="NOT_FOUND", status_code=404, **kwargs)


class ForbiddenError(AppError):
    def __init__(self, message: str = "无权访问", **kwargs):
        super().__init__(message, code="FORBIDDEN", status_code=403, **kwargs)


class UnauthorizedError(AppError):
    def __init__(self, message: str = "未认证", **kwargs):
        super().__init__(message, code="UNAUTHORIZED", status_code=401, **kwargs)


class DependencyError(AppError):
    def __init__(self, message: str = "依赖服务不可用", **kwargs):
        super().__init__(message, code="DEPENDENCY_UNAVAILABLE", status_code=503, **kwargs)


class ConflictError(AppError):
    def __init__(self, message: str = "请求与已有状态冲突", *, code: str = "CONFLICT", **kwargs):
        super().__init__(message, code=code, status_code=409, **kwargs)
