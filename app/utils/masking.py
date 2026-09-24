"""集中式 PII 脱敏与日志过滤（NFR3-07）。

所有日志、审计、LLM 输入在落地前统一走这里；禁止在别处散落手写脱敏。
Literal 期望（§7.2 验收）：

- 邮箱   zhangsan@example.com        -> z***@example.com
- 手机号 13812345678                 -> 138****5678
- 银行卡 6222021234567890 (16 位)     -> 622202******7890
- 身份证 110101199001011234 (18 位)   -> 110101********1234
"""

import re

REDACTED = "[REDACTED]"

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_BANK_CARD_RE = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
_ID_CARD_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_SENSITIVE_KEY_RE = re.compile(
    r"(password|passwd|pwd|secret|token|authorization|api[-_]?key|access[-_]?key)",
    re.IGNORECASE,
)


def mask_email(value: str) -> str:
    """保留首字符 + `***` + 域名。无 @ 时原样返回。"""
    if "@" not in value:
        return value
    local, _, domain = value.partition("@")
    if not local:
        return value
    return f"{local[0]}***@{domain}"


def mask_phone(value: str) -> str:
    """11 位大陆手机号 -> 前 3 + `****` + 后 4；否则原样返回。"""
    if not re.fullmatch(r"1[3-9]\d{9}", value):
        return value
    return f"{value[:3]}****{value[-4:]}"


def mask_bank_card(value: str) -> str:
    """16–19 位卡号 -> 前 6 + N* + 后 4（容忍内部空格/连字符）。"""
    digits = re.sub(r"[ -]", "", value)
    if not re.fullmatch(r"\d{16,19}", digits):
        return value
    mid = "*" * (len(digits) - 10)
    return f"{digits[:6]}{mid}{digits[-4:]}"


def mask_id_card(value: str) -> str:
    """18 位身份证 -> 前 6 + 8* + 后 4（校验位容忍 X）。"""
    digits = value.upper()
    if not re.fullmatch(r"\d{17}[\dX]", digits):
        return value
    return f"{digits[:6]}********{digits[-4:]}"


def mask_pii(text: str) -> str:
    """扫描自由文本，替换内嵌的身份证 / 银行卡 / 手机号 / 邮箱。"""
    if not text:
        return text
    text = _ID_CARD_RE.sub(lambda m: mask_id_card(m.group(0)), text)
    text = _BANK_CARD_RE.sub(lambda m: mask_bank_card(m.group(0)), text)
    text = _PHONE_RE.sub(lambda m: mask_phone(m.group(0)), text)
    text = _EMAIL_RE.sub(lambda m: mask_email(m.group(0)), text)
    return text


def is_sensitive_key(key: str) -> bool:
    """判断字段名是否承载敏感信息（password/token/secret/authorization 等）。"""
    return bool(_SENSITIVE_KEY_RE.search(key))


def mask_value(value):
    """对字符串递归脱敏；对敏感字段名的值整值打码；非字符串原样返回。"""
    if isinstance(value, str):
        return mask_pii(value)
    if isinstance(value, dict):
        return {
            k: REDACTED if is_sensitive_key(k) else mask_value(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [mask_value(v) for v in value]
    return value
