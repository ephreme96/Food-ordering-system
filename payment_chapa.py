"""
Chapa Payment Gateway Integration
-----------------------------------
Docs: https://developer.chapa.co/docs

To switch to a different provider later, replace initialize_payment() and
verify_payment() keeping the same function signatures.

Add your keys to .env:
    CHAPA_SECRET_KEY=CHASECK_TEST-xxxxx     (test)
    CHAPA_SECRET_KEY=CHASECK-xxxxx          (live)
"""
from __future__ import annotations
import os
import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

CHAPA_SECRET_KEY = os.getenv("CHAPA_SECRET_KEY", "")
CHAPA_BASE_URL   = os.getenv("CHAPA_BASE_URL",   "https://api.chapa.co/v1")


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {CHAPA_SECRET_KEY}",
        "Content-Type":  "application/json",
    }


async def initialize_payment(
    *,
    amount:          float,
    currency:        str  = "ETB",
    email:           str  = "",
    first_name:      str  = "Customer",
    last_name:       str  = "Order",
    tx_ref:          str,
    callback_url:    str  = "",
    return_url:      str  = "",
    customization:   Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    POST /transaction/initialize
    Returns the full Chapa JSON response.
    Raises httpx.HTTPStatusError on non-2xx responses.
    """
    payload = {
        "amount":       str(round(amount, 2)),
        "currency":     currency,
        "email":        email or "customer@example.et",
        "first_name":   first_name,
        "last_name":    last_name,
        "tx_ref":       tx_ref,
        "callback_url": callback_url,
        "return_url":   return_url,
        "customization": customization or {
            "title":       "Food Order Payment",
            "description": "Secure online payment for your food order",
            "logo":        "",
        },
    }

    logger.info("Initializing Chapa payment for tx_ref=%s amount=%s", tx_ref, amount)

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{CHAPA_BASE_URL}/transaction/initialize",
            json=payload,
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()


async def verify_payment(tx_ref: str) -> Dict[str, Any]:
    """
    GET /transaction/verify/{tx_ref}
    Returns the full Chapa verification JSON response.
    NEVER trust the frontend – always call this server-side before marking paid.
    """
    logger.info("Verifying Chapa payment tx_ref=%s", tx_ref)

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{CHAPA_BASE_URL}/transaction/verify/{tx_ref}",
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()
