"""安全：PBKDF2 密码哈希 + HMAC 签名 token（结构同 JWT）。"""

import base64
import hashlib
import hmac
import json
import time

PBKDF2_ROUNDS = 120_000


class AuthError(Exception):
    def __init__(self, message, status=401):
        super().__init__(message)
        self.message = message
        self.status = status


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ROUNDS).hex()


def make_salt(username: str) -> str:
    return hashlib.sha256(("salt:" + username).encode()).hexdigest()[:16]


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(body: str, secret: str) -> str:
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]


def issue_token(username: str, role: str, secret: str, ttl: int):
    exp = int(time.time()) + ttl
    payload = json.dumps({"u": username, "r": role, "exp": exp}, separators=(",", ":"), ensure_ascii=False)
    body = _b64(payload.encode())
    return f"{body}.{_sign(body, secret)}", exp


def verify_token(token: str, secret: str) -> dict:
    if not token or "." not in token:
        raise AuthError("缺少或格式错误的 token")
    body, _, sig = token.rpartition(".")
    if not hmac.compare_digest(sig, _sign(body, secret)):
        raise AuthError("token 签名无效")
    try:
        data = json.loads(_unb64(body).decode())
    except Exception:
        raise AuthError("token 内容无法解析")
    if int(data.get("exp", 0)) < int(time.time()):
        raise AuthError("token 已过期")
    return data


def require_admin(claims: dict):
    if claims.get("r") != "admin":
        raise AuthError("需要管理员权限", status=403)
