"""
Two-step login codes (TOTP, RFC 6238): the 6-digit codes shown by Google
Authenticator, Microsoft Authenticator, Authy, etc. Standard library only.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

_STEP_SECONDS = 30
_DIGITS       = 6


def generate_secret() -> str:
    """A new random 160-bit secret, base32-encoded (what authenticator apps expect)."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _code_at(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** _DIGITS)).zfill(_DIGITS)


def current_code(secret: str, at: float | None = None) -> str:
    return _code_at(secret, int((at or time.time()) // _STEP_SECONDS))


def verify(secret: str, code: str, window: int = 1) -> bool:
    """Accept the current code, or one step either side to allow for clock drift."""
    code = (code or "").strip().replace(" ", "")
    if not secret or len(code) != _DIGITS or not code.isdigit():
        return False
    counter = int(time.time() // _STEP_SECONDS)
    return any(
        hmac.compare_digest(_code_at(secret, counter + d), code)
        for d in range(-window, window + 1)
    )


def provisioning_uri(secret: str, username: str, issuer: str) -> str:
    """otpauth:// link that authenticator apps can import."""
    label = quote(f"{issuer}:{username}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&digits={_DIGITS}&period={_STEP_SECONDS}"
