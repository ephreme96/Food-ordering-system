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


def matching_step(secret: str, code: str, window: int = 1) -> int | None:
    """
    Return the time step the code belongs to (current step, or one either side
    to allow for clock drift), or None if it doesn't match.
    """
    code = (code or "").strip().replace(" ", "")
    if not secret or len(code) != _DIGITS or not code.isdigit():
        return None
    counter = int(time.time() // _STEP_SECONDS)
    for d in range(-window, window + 1):
        if hmac.compare_digest(_code_at(secret, counter + d), code):
            return counter + d
    return None


def verify(secret: str, code: str, window: int = 1) -> bool:
    return matching_step(secret, code, window) is not None


def use_code(user, code: str) -> bool:
    """
    Check a code for this user AND remember it, so the same code can't be used
    twice. Without this, a code someone watched you type (or copied from a
    phishing page) works again for up to ~90 seconds. The caller commits.
    """
    step = matching_step(user.totp_secret, code)
    if step is None or step <= (user.totp_last_step or -1):
        return False
    user.totp_last_step = step
    return True


def provisioning_uri(secret: str, username: str, issuer: str) -> str:
    """otpauth:// link that authenticator apps can import."""
    label = quote(f"{issuer}:{username}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&digits={_DIGITS}&period={_STEP_SECONDS}"
