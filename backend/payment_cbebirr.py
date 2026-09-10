"""
CBE Birr (Commercial Bank of Ethiopia) payment integration.

Environment variables:
    CBE_BIRR_MERCHANT_ID   - Your merchant ID
    CBE_BIRR_API_KEY       - Your API key / secret
    CBE_BIRR_NOTIFY_URL    - Webhook URL for callbacks
    CBE_BIRR_API_BASE_URL  - API base (default: https://api.cbebirr.et/v1)
    PAYMENT_SANDBOX        - if "true", all API calls are mocked (default: true)

CBE Birr uses HMAC-SHA256 signatures with sorted key=value pairs.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_SANDBOX     = os.getenv("PAYMENT_SANDBOX", "true").lower() == "true"
_MERCHANT_ID = os.getenv("CBE_BIRR_MERCHANT_ID", "")
_API_KEY     = os.getenv("CBE_BIRR_API_KEY", "")
_NOTIFY_URL  = os.getenv("CBE_BIRR_NOTIFY_URL", "")
_API_BASE    = os.getenv("CBE_BIRR_API_BASE_URL", "https://api.cbebirr.et/v1")


def _sign(data: dict) -> str:
    """HMAC-SHA256 signature over sorted key=value pairs (excluding 'sign')."""
    sorted_str = "&".join(
        f"{k}={v}"
        for k, v in sorted(data.items())
        if k != "sign" and v is not None
    )
    return hmac.new(
        _API_KEY.encode("utf-8"),
        sorted_str.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


async def initiate_payment(
    order_number: str,
    amount_etb: float,
    customer_phone: str,
    return_url: str,
    notify_url: str = "",
) -> dict:
    """
    Initiate a CBE Birr payment request (QR code or redirect flow).

    Returns:
        {
          "success":     bool,
          "qr_code":     str | None,    # base64-encoded QR image (if available)
          "payment_url": str | None,    # redirect URL (if available)
          "reference":   str | None,    # transaction ID from CBE Birr
          "error":       str | None,
        }
    """
    if _SANDBOX:
        logger.info("[CBE BIRR SANDBOX] Order %s | ETB %.2f", order_number, amount_etb)
        return {
            "success":     True,
            "qr_code":     None,
            "payment_url": None,
            "reference":   f"CBE-SBX-{order_number}",
            "error":       None,
        }

    if not all([_MERCHANT_ID, _API_KEY]):
        return {
            "success":     False,
            "qr_code":     None,
            "payment_url": None,
            "reference":   None,
            "error":       "CBE Birr credentials not configured (MERCHANT_ID / API_KEY missing)",
        }

    timestamp = str(int(time.time() * 1000))
    payload = {
        "merchantId":    _MERCHANT_ID,
        "orderNo":       order_number,
        "amount":        f"{amount_etb:.2f}",
        "currency":      "ETB",
        "notifyUrl":     notify_url or _NOTIFY_URL,
        "returnUrl":     return_url,
        "timestamp":     timestamp,
        "subject":       f"Food Order #{order_number}",
        "customerPhone": customer_phone,
    }
    payload["sign"] = _sign(payload)

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{_API_BASE}/payment/create",
                json=payload,
                headers={
                    "Authorization": f"Bearer {_API_KEY}",
                    "Content-Type":  "application/json",
                },
            )
        data = resp.json()
        if data.get("code") == "SUCCESS":
            inner = data.get("data", {})
            return {
                "success":     True,
                "qr_code":     inner.get("qrCode"),
                "payment_url": inner.get("paymentUrl"),
                "reference":   inner.get("transactionId", order_number),
                "error":       None,
            }
        return {
            "success":     False,
            "qr_code":     None,
            "payment_url": None,
            "reference":   None,
            "error":       data.get("message", "CBE Birr initiation failed"),
        }
    except Exception as exc:
        logger.error("CBE Birr initiate error for %s: %s", order_number, exc)
        return {"success": False, "qr_code": None, "payment_url": None, "reference": None, "error": str(exc)}


def verify_callback(payload: dict) -> bool:
    """
    Verify the HMAC signature on an incoming CBE Birr callback.
    In sandbox mode always returns True.
    """
    if _SANDBOX:
        return True
    received_sign = payload.pop("sign", "")
    expected      = _sign(payload)
    return hmac.compare_digest(expected, received_sign)


async def query_status(order_number: str) -> Optional[dict]:
    """
    Query CBE Birr for the current payment status.
    Returns raw data dict or None on failure.
    """
    if _SANDBOX:
        return {"status": "SUCCESS", "orderNo": order_number}

    if not all([_MERCHANT_ID, _API_KEY]):
        return None

    timestamp = str(int(time.time() * 1000))
    payload   = {
        "merchantId": _MERCHANT_ID,
        "orderNo":    order_number,
        "timestamp":  timestamp,
    }
    payload["sign"] = _sign(payload)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{_API_BASE}/payment/query",
                params=payload,
                headers={"Authorization": f"Bearer {_API_KEY}"},
            )
        data = resp.json()
        return data.get("data") if data.get("code") == "SUCCESS" else None
    except Exception as exc:
        logger.error("CBE Birr query error for %s: %s", order_number, exc)
        return None
