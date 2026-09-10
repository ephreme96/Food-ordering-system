"""
Cryptographically-signed receipt token system.

Token format:  <header_b64>.<payload_b64>.<hmac_sha256_hex>

Properties:
- Tamper-evident: HMAC signature over header + payload
- Time-limited:   exp claim enforced on every verification
- One-time use:   `redeemed` flag in database prevents replay
- Inspectable:    cashier page can decode the payload to show order details
                  without trusting any data – they always re-read from DB.
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import string
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

RECEIPT_HMAC_SECRET    = os.getenv("RECEIPT_HMAC_SECRET", "insecure-default-change-me")
RECEIPT_EXPIRY_SECONDS = int(os.getenv("RECEIPT_EXPIRY_SECONDS", str(7 * 24 * 3600)))  # 7 days


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _b64_encode(data: Dict[str, Any]) -> str:
    raw = json.dumps(data, separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _b64_decode(s: str) -> Dict[str, Any]:
    # Restore stripped padding
    padded = s + "=" * (-len(s) % 4)
    return json.loads(base64.urlsafe_b64decode(padded).decode())


def _sign(message: str) -> str:
    return hmac.new(
        RECEIPT_HMAC_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# ─── Public API ───────────────────────────────────────────────────────────────

def generate_receipt_code(length: int = 10) -> str:
    """Generate a short, URL-safe, human-readable receipt code (uppercase alphanumeric)."""
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_receipt_token(
    order_id: int,
    receipt_code: str,
    amount: float,
    order_number: str,
) -> str:
    """
    Build and sign a receipt token.
    Returns a string in the form:  <header>.<payload>.<signature>
    """
    now = int(time.time())

    header  = _b64_encode({"alg": "HS256", "typ": "RCT"})
    payload = _b64_encode({
        "oid":  order_id,
        "rc":   receipt_code,
        "on":   order_number,
        "amt":  round(amount, 2),
        "iat":  now,
        "exp":  now + RECEIPT_EXPIRY_SECONDS,
    })

    message   = f"{header}.{payload}"
    signature = _sign(message)
    token     = f"{message}.{signature}"

    logger.info("Generated receipt token for order_id=%s code=%s", order_id, receipt_code)
    return token


def verify_receipt_token(
    token: str,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Verify a receipt token.

    Returns:
        (is_valid, reason_string, decoded_payload_or_None)

    Checks performed (in order):
    1. Token has exactly 3 parts separated by '.'
    2. HMAC signature matches
    3. Token has not expired
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return False, "Malformed token (expected 3 parts)", None

        header_b64, payload_b64, received_sig = parts

        # 1. Signature verification (constant-time compare)
        message      = f"{header_b64}.{payload_b64}"
        expected_sig = _sign(message)
        if not hmac.compare_digest(received_sig, expected_sig):
            return False, "Invalid signature – receipt may have been tampered with", None

        # 2. Decode payload
        try:
            payload = _b64_decode(payload_b64)
        except Exception:
            return False, "Could not decode token payload", None

        # 3. Expiry check
        if int(time.time()) > payload.get("exp", 0):
            return False, "Receipt token has expired", None

        return True, "Signature valid", payload

    except Exception as exc:
        logger.warning("Receipt token verification error: %s", exc)
        return False, f"Verification error: {exc}", None
