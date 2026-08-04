"""密码哈希和不透明登录会话 Token 的最小安全实现。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from .errors import DomainValidationError


_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_HASH_BYTES = 32


def hash_password(password: str) -> str:
    """使用随机盐和 scrypt 生成不可逆的密码哈希。"""

    _validate_password(password)
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_HASH_BYTES,
    )
    return "$".join(
        (
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, encoded_hash: str) -> bool:
    """恒定时间比较密码哈希；格式异常按认证失败处理。"""

    if not isinstance(password, str) or not isinstance(encoded_hash, str):
        return False
    try:
        algorithm, n, r, p, encoded_salt, encoded_digest = encoded_hash.split("$")
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(encoded_salt.encode("ascii"))
        expected_digest = base64.urlsafe_b64decode(encoded_digest.encode("ascii"))
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected_digest),
        )
    except (TypeError, ValueError, UnicodeEncodeError):
        return False
    return hmac.compare_digest(digest, expected_digest)


def new_session_token() -> str:
    """生成只写入 HttpOnly Cookie 的随机登录会话 Token。"""

    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """生成可存储到数据库的不透明会话 Token 摘要。"""

    if not isinstance(token, str) or not token.strip():
        raise DomainValidationError("session token 必须是非空字符串")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _validate_password(password: str) -> None:
    if not isinstance(password, str) or not 8 <= len(password) <= 128:
        raise DomainValidationError("密码长度必须在 8 到 128 个字符之间")
