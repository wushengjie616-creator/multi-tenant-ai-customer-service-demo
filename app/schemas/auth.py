"""身份与认证相关 Schema。"""

from pydantic import BaseModel


class LoginRequest(BaseModel):
    tenant_id: str
    email: str
    password: str


class UserProfile(BaseModel):
    id: str
    tenant_id: str
    role: str
    email: str
    phone: str | None = None
    full_name: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserProfile
