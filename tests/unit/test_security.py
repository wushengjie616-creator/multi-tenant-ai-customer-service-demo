"""JWT 与密码安全单元测试（NFR3-01 / NFR3-02）。"""

import pytest
from jose import jwt

from app.core.config import settings
from app.core.security import (
    ROLES,
    ROLE_ADMIN,
    ROLE_USER,
    AuthError,
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_token_roundtrip():
    token = create_access_token(tenant_id="t-1", user_id="u-1", role=ROLE_USER)
    auth = decode_token(token)
    assert auth.tenant_id == "t-1"
    assert auth.user_id == "u-1"
    assert auth.role == ROLE_USER


def test_decode_rejects_wrong_secret():
    # 用错误密钥签发的 token，decode_token 必须拒绝（而非信任其 claim）
    forged = jwt.encode(
        {"sub": "u-1", "tenant_id": "t-1", "role": "user", "exp": 4102444800},
        "wrong_secret",
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(AuthError):
        decode_token(forged)


def test_decode_rejects_expired_token():
    token = create_access_token(
        tenant_id="t-1", user_id="u-1", role=ROLE_USER, expires_minutes=-1
    )
    with pytest.raises(AuthError):
        decode_token(token)


def test_decode_rejects_missing_claims():
    # 只给 sub，缺 tenant_id / role
    token = jwt.encode({"sub": "u-1"}, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    with pytest.raises(AuthError):
        decode_token(token)


def test_decode_rejects_invalid_role():
    token = create_access_token(tenant_id="t-1", user_id="u-1", role=ROLE_USER)
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    payload["role"] = "superuser"
    forged = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    with pytest.raises(AuthError):
        decode_token(forged)


def test_create_access_token_rejects_invalid_role():
    with pytest.raises(ValueError):
        create_access_token(tenant_id="t", user_id="u", role="not-a-role")


def test_roles_constants():
    assert ROLE_USER == "user"
    assert ROLE_ADMIN == "admin"
    assert ROLES == ("user", "agent", "admin")


def test_password_hash_and_verify():
    encoded = hash_password("S3cret!pass")
    assert encoded != "S3cret!pass"
    assert verify_password("S3cret!pass", encoded) is True
    assert verify_password("wrong", encoded) is False


def test_password_hash_is_salted():
    assert hash_password("same") != hash_password("same")


def test_verify_password_malformed_returns_false():
    assert verify_password("x", "not-a-valid-format") is False
