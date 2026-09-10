"""
Telebirr (Ethio Telecom) payment integration.

Environment variables:
    TELEBIRR_APP_ID        - App ID from Ethio Telecom developer portal
    TELEBIRR_APP_KEY       - App secret key
    TELEBIRR_SHORT_CODE    - Merchant short code
    TELEBIRR_NOTIFY_URL    - Webhook URL for payment callbacks
    TELEBIRR_API_BASE_URL  - API base (default: https://196.188.120.3:38443/apiaccess/payment/gateway)
    PAYMENT_SANDBOX        - if "true", all API calls are mocked (default: true)

Telebirr uses HMAC-SHA256 signatures. Payload keys are sorted alphabetically,
joined as "key=value&key=value", then signed with the App Key.
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

_SANDBOX    = os.getenv("PAYMENT_SANDBOX", "true").lower() == "true"
_APP_ID     = os.getenv("TELEBIRR_APP_ID", "")
_APP_KEY    = os.getenv("TELEBIRR_APP_KEY", "")
_SHORT_CODE = os.getenv("TELEBIRR_SHORT_CODE", "")
_NOTIFY_URL = os.getenv("TELEBIRR_NOTIFY_URL", "")
_API_BASE   = os.getenv(
    "TELEBIRR_API_BASE_URL",
    "https://196.188.120.3:38443/apiaccess/payment/gateway",
)


def _sign(payload: dict) -> str:
    """Create HMAC-SHA256 signature — keys sorted, joined as key=value&..."""
    sorted_str = "&".join(
        f"{k}={v}"
        for k, v in sorted(payload.items())
        if v is not None and k not in ("sign", "appKey")
    )
    return hmac.new(
        _APP_KEY.encode("utf-8"),
        sorted_str.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest().upper()


async def initiate_payment(
    order_number: str,
    amount_etb: float,
    customer_phone: str,
    return_url: str,
    notify_url: str = "",
) -> dict:
    """
    Push a USSD payment request to the customer's Telebirr wallet.

    Returns:
        {
          "success": bool,
          "checkout_url": str | None,   # redirect URL for web-based flow
          "reference":    str | None,   # our order_number echoed back
          "error":        str | None,
        }
    """
    if _SANDBOX:
        logger.info("[TELEBIRR SANDBOX] Order %s | ETB %.2f", order_number, amount_etb)
        return {
            "success":      True,
            "checkout_url": None,          # USSD push — no redirect URL in sandbox
            "reference":    f"TELE-SBX-{order_number}",
            "error":        None,
        }

    if not all([_APP_ID, _APP_KEY, _SHORT_CODE]):
        return {
            "success": False,
            "checkout_url": None,
            "reference": None,
            "error": "Telebirr credentials not configured (APP_ID / APP_KEY / SHORT_CODE missing)",
        }

    timestamp = str(int(time.time() * 1000))
    nonce     = hashlib.md5(f"{order_number}{timestamp}".encode()).hexdigest()

    payload = {
        "appId":          _APP_ID,
        "timestamp":      timestamp,
        "nonce":          nonce,
        "shortCode":      _SHORT_CODE,
        "notifyUrl":      notify_url or _NOTIFY_URL,
        "returnUrl":      return_url,
        "outTradeNo":     order_number,
        "subject":        f"Food Order #{order_number}",
        "totalAmount":    f"{amount_etb:.2f}",
        "timeoutExpress": "30",
        "receiverIdentifier": customer_phone,
    }
    payload["sign"] = _sign(payload)

    try:
        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
            resp = await client.post(
                f"{_API_BASE}/c2bPayment/singlePayment",
                json={"appId": _APP_ID, **payload},
                headers={"Content-Type": "application/json"},
            )
        data = resp.json()
        if data.get("code") == "0":
            biz = data.get("data", {})
            return {
                "success":      True,
                "checkout_url": biz.get("toPayUrl"),
                "reference":    biz.get("outTradeNo", order_number),
                "error":        None,
            }
        return {
            "success":      False,
            "checkout_url": None,
            "reference":    None,
            "error":        data.get("msg", "Telebirr initiation failed"),
        }
    except Exception as exc:
        logger.error("Telebirr initiate error for %s: %s", order_number, exc)
        return {"success": False, "checkout_url": None, "reference": None, "error": str(exc)}


def verify_callback(payload: dict) -> bool:
    """
    Verify the HMAC signature on an incoming Telebirr callback.
    In sandbox mode always returns True.
    """
    if _SANDBOX:
        return True
    received_sign = payload.get("sign", "")
    expected_sign = _sign({k: v for k, v in payload.items() if k != "sign"})
    return hmac.compare_digest(expected_sign, received_sign.upper())


async def query_status(order_number: str) -> Optional[dict]:
    """
    Query Telebirr for the current payment status of an order.
    Returns raw data dict or None on failure.
    Sandbox always returns a succeeded status.
    """
    if _SANDBOX:
        return {"tradeStatus": "SUCCESS", "outTradeNo": order_number}

    if not all([_APP_ID, _APP_KEY, _SHORT_CODE]):
        return None

    timestamp = str(int(time.time() * 1000))
    nonce     = hashlib.md5(f"{order_number}{timestamp}q".encode()).hexdigest()
    payload   = {
        "appId":      _APP_ID,
        "timestamp":  timestamp,
        "nonce":      nonce,
        "shortCode":  _SHORT_CODE,
        "outTradeNo": order_number,
    }
    payload["sign"] = _sign(payload)

    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
            resp = await client.post(
                f"{_API_BASE}/c2bPayment/queryPayment",
                json=payload,
            )
        data = resp.json()
        return data.get("data") if data.get("code") == "0" else None
    except Exception as exc:
        logger.error("Telebirr query error for %s: %s", order_number, exc)
        return None
