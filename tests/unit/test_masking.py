"""PII 脱敏单元测试（PLAN §7.2 邮箱/手机号/银行卡/身份证 literal 期望）。"""

from app.utils.masking import (
    is_sensitive_key,
    mask_bank_card,
    mask_email,
    mask_id_card,
    mask_phone,
    mask_pii,
    mask_value,
)


def test_mask_email_literal():
    assert mask_email("zhangsan@example.com") == "z***@example.com"
    assert mask_email("a@b.co") == "a***@b.co"


def test_mask_email_unchanged_without_at():
    assert mask_email("no-email") == "no-email"
    assert mask_email("") == ""


def test_mask_phone_literal():
    assert mask_phone("13812345678") == "138****5678"
    assert mask_phone("15900001111") == "159****1111"


def test_mask_phone_unchanged_when_not_mobile():
    assert mask_phone("12345") == "12345"
    assert mask_phone("23812345678") == "23812345678"  # 2 开头非手机号
    assert mask_phone("1381234567") == "1381234567"  # 10 位


def test_mask_bank_card_literal():
    assert mask_bank_card("6222021234567890") == "622202******7890"  # 16 位
    assert mask_bank_card("6222021234567890123") == "622202*********0123"  # 19 位
    assert mask_bank_card("6222 0212 3456 7890") == "622202******7890"  # 容忍空格


def test_mask_bank_card_unchanged_when_short():
    assert mask_bank_card("123456789012345") == "123456789012345"  # 15 位


def test_mask_id_card_literal():
    assert mask_id_card("110101199001011234") == "110101********1234"
    assert mask_id_card("11010119900101123x") == "110101********123X"  # 校验位 X 归一


def test_mask_id_card_unchanged_when_wrong_length():
    assert mask_id_card("11010119900101123") == "11010119900101123"


def test_mask_pii_free_text():
    assert (
        mask_pii("请联系 13812345678 或 zhangsan@example.com")
        == "请联系 138****5678 或 z***@example.com"
    )
    assert (
        mask_pii("身份证 110101199001011234 银行卡 6222021234567890")
        == "身份证 110101********1234 银行卡 622202******7890"
    )


def test_mask_pii_no_sensitive_content():
    assert mask_pii("今天天气不错") == "今天天气不错"
    assert mask_pii("") == ""


def test_is_sensitive_key():
    assert is_sensitive_key("password")
    assert is_sensitive_key("Authorization")
    assert is_sensitive_key("access_token")
    assert not is_sensitive_key("email")
    assert not is_sensitive_key("content")


def test_mask_value_recursive():
    assert mask_value({"phone": "13812345678", "nested": {"email": "a@b.co"}}) == {
        "phone": "138****5678",
        "nested": {"email": "a***@b.co"},
    }


def test_mask_value_redacts_sensitive_key_value():
    assert mask_value({"password": "super-secret", "email": "a@b.co"}) == {
        "password": "[REDACTED]",
        "email": "a***@b.co",
    }


def test_mask_value_non_string_passthrough():
    assert mask_value(123) == 123
    assert mask_value(None) is None
