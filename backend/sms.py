"""
SMS notifications via Africa's Talking.

Environment variables:
    AFRICASTALKING_USERNAME  - your AT username ("sandbox" for testing)
    AFRICASTALKING_API_KEY   - your AT API key
    AFRICASTALKING_FROM      - sender ID (optional, short-code or alphanumeric)
    PAYMENT_SANDBOX          - if "true", SMS is logged but NOT sent to AT (default: true)
"""
from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

_SANDBOX    = os.getenv("PAYMENT_SANDBOX", "true").lower() == "true"
_USERNAME   = os.getenv("AFRICASTALKING_USERNAME", "sandbox")
_API_KEY    = os.getenv("AFRICASTALKING_API_KEY", "")
_FROM       = os.getenv("AFRICASTALKING_FROM", "")

# Africa's Talking uses a different base URL for their sandbox
_API_URL = (
    "https://api.sandbox.africastalking.com/version1/messaging"
    if _USERNAME == "sandbox" else
    "https://api.africastalking.com/version1/messaging"
)


def _normalise_phone(phone: str) -> str:
    """Convert Ethiopian local formats to E.164 (+251...)."""
    phone = phone.strip().replace(" ", "").replace("-", "")
    if phone.startswith("09") or phone.startswith("07"):
        return "+251" + phone[1:]
    if phone.startswith("251") and not phone.startswith("+"):
        return "+" + phone
    return phone


async def send_sms(phone: str, message: str) -> bool:
    """
    Send an SMS to `phone`.
    Returns True on success, False on failure.
    In sandbox mode (no real API key configured), logs the message only.
    """
    if not phone:
        return False

    phone = _normalise_phone(phone)

    if _SANDBOX and not _API_KEY:
        logger.info("[SMS SANDBOX] To %s: %s", phone, message)
        return True

    if not _API_KEY:
        logger.warning("AFRICASTALKING_API_KEY not set — SMS skipped for %s", phone)
        return False

    try:
        payload: dict = {
            "username": _USERNAME,
            "to":       phone,
            "message":  message,
        }
        if _FROM:
            payload["from"] = _FROM

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                _API_URL,
                headers={
                    "apiKey":       _API_KEY,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept":       "application/json",
                },
                data=payload,
            )

        if resp.status_code == 201:
            logger.info("SMS sent to %s", phone)
            return True

        logger.warning("SMS failed (%s): %s", resp.status_code, resp.text[:200])
        return False

    except Exception as exc:
        logger.error("SMS error for %s: %s", phone, exc)
        return False


# ─── Message templates ────────────────────────────────────────────────────────

def order_placed_msg(order_number: str, total: float, method: str) -> str:
    return (
        f"ትዕዛዝዎ ተቀብሏል! Order #{order_number} – ETB {total:.2f} "
        f"({method}). ለእርስዎ ትዕዛዝ አመሰግናለሁ!"
    )


def payment_confirmed_msg(order_number: str, amount: float) -> str:
    return (
        f"ክፍያ ተረጋግጧል! ETB {amount:.2f} confirmed for order #{order_number}. "
        "Your food is being prepared."
    )


def order_ready_msg(order_number: str, receipt_code: str) -> str:
    return (
        f"ትዕዛዝዎ ዝግጁ ነው! Order #{order_number} is READY for pickup. "
        f"Show receipt code: {receipt_code} at the counter."
    )


def cash_confirmed_msg(order_number: str, total: float) -> str:
    return (
        f"Cash order #{order_number} confirmed – ETB {total:.2f}. "
        "Please pay at the counter when your order is ready."
    )
