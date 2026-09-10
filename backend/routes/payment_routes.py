"""
Payment routes — Telebirr, CBE Birr, Cash, Bank Transfer + Promo codes.

Endpoints:
    GET  /api/promos/{code}                 — validate a promo code (public)
    POST /api/payments/initiate             — start a Telebirr or CBE Birr payment
    POST /api/payments/callback/telebirr    — Telebirr payment callback
    POST /api/payments/callback/cbebirr    — CBE Birr payment callback
    GET  /api/payments/status/{order_number}— poll payment status (public, needs tx_ref)
    GET  /api/orders/track/{order_number}   — public order tracker (no auth required)
    POST /api/payments/bank-transfer/claim  — customer claims they've done a bank transfer
    GET  /api/payments/bank-info            — fetch bank account details for manual transfer
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

import payment_cbebirr
import payment_telebirr
from database import get_db
from models import (
    AuditEvent,
    AuditLog,
    DiscountType,
    Order,
    OrderStatus,
    Payment,
    PaymentMethod,
    PaymentStatus,
    PromoCode,
)
from receipt import generate_receipt_code, generate_receipt_token
from sms import order_placed_msg, payment_confirmed_msg, send_sms
from ws_manager import ws_manager

logger  = logging.getLogger(__name__)
router  = APIRouter(prefix="/api", tags=["Payments"])
limiter = Limiter(key_func=get_remote_address)

BASE_URL     = os.getenv("BASE_URL",     "http://localhost:8000")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:8000")
BANK_NAME    = os.getenv("BANK_NAME",    "Commercial Bank of Ethiopia")
BANK_ACCOUNT = os.getenv("BANK_ACCOUNT_NUMBER", "")
BANK_HOLDER  = os.getenv("BANK_ACCOUNT_NAME",   "Restaurant Account")
_SANDBOX     = os.getenv("PAYMENT_SANDBOX", "true").lower() == "true"


# ─── Request / Response schemas ───────────────────────────────────────────────

class PaymentInitiateRequest(BaseModel):
    order_number: str
    tx_ref:       str          # security: matches what was returned at order creation
    method:       str          # "telebirr" | "cbebirr"
    customer_phone: Optional[str] = None


class BankTransferClaimRequest(BaseModel):
    order_number:     str
    tx_ref:           str
    bank_ref:         str      # customer's bank transaction reference
    customer_phone:   Optional[str] = None


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _audit(
    db:           Session,
    event:        AuditEvent,
    order:        Optional[Order] = None,
    note:         str = "",
    ip_address:   str = "",
) -> None:
    db.add(AuditLog(
        event        = event,
        order_id     = order.id     if order else None,
        order_number = order.order_number if order else None,
        note         = note,
        ip_address   = ip_address,
    ))


async def _mark_order_paid(order: Order, reference: str, method: PaymentMethod, db: Session) -> None:
    """Common logic: generate receipt, update order status, persist Payment row, emit WS."""
    receipt_code  = generate_receipt_code()
    receipt_token = generate_receipt_token(
        order.id, receipt_code, order.total_amount, order.order_number
    )
    order.status        = OrderStatus.PAID
    order.receipt_code  = receipt_code
    order.receipt_token = receipt_token

    db.add(Payment(
        order_id           = order.id,
        payment_method     = method,
        amount_santim      = int(round(order.total_amount * 100)),
        status             = PaymentStatus.succeeded,
        external_reference = reference,
        captured_at        = datetime.now(timezone.utc),
    ))
    db.commit()
    db.refresh(order)

    # Real-time: notify kitchen and cashier
    await ws_manager.broadcast_to_room("kitchen", {
        "event":        "new_order",
        "order_number": order.order_number,
        "status":       order.status.value,
    })
    await ws_manager.broadcast_to_room("cashier", {
        "event":        "payment_confirmed",
        "order_number": order.order_number,
    })
    await ws_manager.broadcast_to_room(f"track:{order.order_number}", {
        "event":  "status_change",
        "status": order.status.value,
    })

    # SMS confirmation
    phone = order.customer_phone
    if phone:
        await send_sms(phone, payment_confirmed_msg(order.order_number, order.total_amount))

    logger.info("Order %s marked PAID via %s (ref=%s)", order.order_number, method.value, reference)


# ─── Promo Codes ──────────────────────────────────────────────────────────────

@router.get("/promos/{code}")
@limiter.limit("20/minute")
async def check_promo(
    request:     Request,
    code:        str,
    order_total: float = 0.0,
    db:          Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Validate a promo code and calculate the discount for a given order total.
    Public endpoint — no authentication required.
    """
    promo = db.query(PromoCode).filter(
        PromoCode.code == code.strip().upper(),
        PromoCode.is_active == True,
    ).first()

    if not promo:
        raise HTTPException(status_code=404, detail="Promo code not found or inactive")

    now = datetime.now(timezone.utc)
    if promo.expires_at and promo.expires_at < now:
        raise HTTPException(status_code=400, detail="This promo code has expired")

    if promo.max_uses is not None and promo.uses_count >= promo.max_uses:
        raise HTTPException(status_code=400, detail="This promo code has reached its usage limit")

    # Minimum order check (santim → ETB)
    min_etb = promo.min_order_total / 100
    if order_total > 0 and order_total < min_etb:
        raise HTTPException(
            status_code=400,
            detail=f"Minimum order of ETB {min_etb:.2f} required for this promo",
        )

    # Calculate discount
    if promo.discount_type == DiscountType.percent:
        discount = round(order_total * promo.discount_value / 100, 2)
    else:
        discount = round(promo.discount_value / 100, 2)  # fixed is stored in santim

    discount = min(discount, order_total)  # cannot discount more than total

    return {
        "valid":          True,
        "code":           promo.code,
        "discount_type":  promo.discount_type.value,
        "discount_value": promo.discount_value,
        "discount_etb":   discount,
        "new_total":      round(order_total - discount, 2),
        "description":    (
            f"{promo.discount_value}% off"
            if promo.discount_type == DiscountType.percent
            else f"ETB {promo.discount_value / 100:.2f} off"
        ),
    }


# ─── Bank Info (public) ───────────────────────────────────────────────────────

@router.get("/payments/bank-info")
async def bank_info() -> Dict[str, str]:
    """Return bank account details for manual transfer orders."""
    return {
        "bank_name":       BANK_NAME,
        "account_number":  BANK_ACCOUNT,
        "account_name":    BANK_HOLDER,
        "instructions":    (
            "Please transfer the exact order amount to the account above. "
            "Use your Order Number as the transfer reference. "
            "Take a screenshot of the confirmation and bring it to the counter."
        ),
    }


# ─── Initiate Telebirr / CBE Birr ─────────────────────────────────────────────

@router.post("/payments/initiate")
@limiter.limit("5/minute")
async def initiate_payment(
    request: Request,
    body:    PaymentInitiateRequest,
    db:      Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Initiate a Telebirr or CBE Birr payment for an existing order.

    The order must already exist (created via POST /api/orders) with
    payment_method == telebirr or cbebirr. The tx_ref from order creation
    is required to prevent IDOR.
    """
    order = db.query(Order).filter(
        Order.order_number == body.order_number,
        Order.tx_ref       == body.tx_ref,
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status not in (OrderStatus.PENDING, OrderStatus.FAILED):
        raise HTTPException(
            status_code=400,
            detail=f"Order cannot be paid (status: {order.status.value})",
        )

    method   = body.method.lower()
    phone    = body.customer_phone or order.customer_phone or ""
    ret_url  = f"{FRONTEND_URL}/track/{order.order_number}"
    ntfy_url = f"{BASE_URL}/api/payments/callback/{method}"

    if method == "telebirr":
        result = await payment_telebirr.initiate_payment(
            order_number   = order.order_number,
            amount_etb     = order.total_amount,
            customer_phone = phone,
            return_url     = ret_url,
            notify_url     = ntfy_url,
        )
    elif method == "cbebirr":
        result = await payment_cbebirr.initiate_payment(
            order_number   = order.order_number,
            amount_etb     = order.total_amount,
            customer_phone = phone,
            return_url     = ret_url,
            notify_url     = ntfy_url,
        )
    else:
        raise HTTPException(status_code=400, detail="method must be 'telebirr' or 'cbebirr'")

    if not result["success"]:
        _audit(db, AuditEvent.PAYMENT_FAILED, order, note=result.get("error", ""), ip_address=request.client.host)
        db.commit()
        raise HTTPException(status_code=502, detail=result.get("error", "Payment initiation failed"))

    # Log initiation
    _audit(
        db, AuditEvent.PAYMENT_INITIATED, order,
        note=f"method={method} ref={result.get('reference')}",
        ip_address=request.client.host,
    )
    db.commit()

    # In sandbox mode, immediately mark as paid
    if _SANDBOX:
        pay_method = PaymentMethod.telebirr if method == "telebirr" else PaymentMethod.cbebirr
        await _mark_order_paid(order, result["reference"], pay_method, db)
        return {
            "status":  "sandbox_paid",
            "message": "Sandbox mode: payment auto-confirmed",
            "order_number": order.order_number,
            "receipt_code": order.receipt_code,
        }

    return {
        "status":       "initiated",
        "checkout_url": result.get("checkout_url"),
        "qr_code":      result.get("qr_code"),
        "reference":    result.get("reference"),
        "order_number": order.order_number,
    }


# ─── Telebirr Callback ────────────────────────────────────────────────────────

@router.post("/payments/callback/telebirr")
async def telebirr_callback(request: Request, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Webhook called by Telebirr after a payment is completed.
    NEVER trusts the payload — always verify signature and amount.
    """
    try:
        payload = await request.json()
    except Exception:
        payload = dict(await request.form())

    if not payment_telebirr.verify_callback(payload):
        logger.warning("Telebirr callback signature mismatch: %s", payload)
        raise HTTPException(status_code=400, detail="Invalid signature")

    trade_status  = payload.get("tradeStatus", "").upper()
    out_trade_no  = payload.get("outTradeNo", "")
    trade_no      = payload.get("tradeNo", out_trade_no)

    if not out_trade_no:
        raise HTTPException(status_code=400, detail="Missing outTradeNo")

    order = db.query(Order).filter(Order.order_number == out_trade_no).first()
    if not order:
        logger.warning("Telebirr callback for unknown order %s", out_trade_no)
        return {"code": "0", "msg": "ignored"}

    if order.status == OrderStatus.PAID:
        return {"code": "0", "msg": "already_paid"}

    if trade_status in ("SUCCESS", "TRADE_SUCCESS"):
        _audit(db, AuditEvent.PAYMENT_CONFIRMED, order,
               note=f"Telebirr trade_no={trade_no}", ip_address=request.client.host)
        await _mark_order_paid(order, trade_no, PaymentMethod.telebirr, db)
        return {"code": "0", "msg": "success"}

    if trade_status in ("FAIL", "CLOSED"):
        order.status = OrderStatus.FAILED
        _audit(db, AuditEvent.PAYMENT_FAILED, order,
               note=f"Telebirr trade_status={trade_status}", ip_address=request.client.host)
        db.commit()
        return {"code": "0", "msg": "noted"}

    return {"code": "0", "msg": "pending"}


# ─── CBE Birr Callback ────────────────────────────────────────────────────────

@router.post("/payments/callback/cbebirr")
async def cbebirr_callback(request: Request, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Webhook called by CBE Birr after a payment is completed.
    """
    try:
        payload = await request.json()
    except Exception:
        payload = dict(await request.form())

    if not payment_cbebirr.verify_callback(dict(payload)):
        logger.warning("CBE Birr callback signature mismatch")
        raise HTTPException(status_code=400, detail="Invalid signature")

    status    = payload.get("status", "").upper()
    order_no  = payload.get("orderNo", "")
    txn_id    = payload.get("transactionId", order_no)

    if not order_no:
        raise HTTPException(status_code=400, detail="Missing orderNo")

    order = db.query(Order).filter(Order.order_number == order_no).first()
    if not order:
        logger.warning("CBE Birr callback for unknown order %s", order_no)
        return {"code": "SUCCESS", "msg": "ignored"}

    if order.status == OrderStatus.PAID:
        return {"code": "SUCCESS", "msg": "already_paid"}

    if status == "SUCCESS":
        _audit(db, AuditEvent.PAYMENT_CONFIRMED, order,
               note=f"CBE Birr txn_id={txn_id}", ip_address=request.client.host)
        await _mark_order_paid(order, txn_id, PaymentMethod.cbebirr, db)
        return {"code": "SUCCESS", "msg": "ok"}

    if status in ("FAIL", "FAILED", "CLOSED"):
        order.status = OrderStatus.FAILED
        _audit(db, AuditEvent.PAYMENT_FAILED, order,
               note=f"CBE Birr status={status}", ip_address=request.client.host)
        db.commit()
        return {"code": "SUCCESS", "msg": "noted"}

    return {"code": "SUCCESS", "msg": "pending"}


# ─── Payment Status Polling ───────────────────────────────────────────────────

@router.get("/payments/status/{order_number}")
@limiter.limit("30/minute")
async def payment_status(
    request:      Request,
    order_number: str,
    tx_ref:       str,
    db:           Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Poll the payment status for an order.
    Requires tx_ref to prevent IDOR.
    """
    order = db.query(Order).filter(
        Order.order_number == order_number,
        Order.tx_ref       == tx_ref,
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    return {
        "order_number": order.order_number,
        "status":       order.status.value,
        "paid":         order.status == OrderStatus.PAID,
        "receipt_code": order.receipt_code if order.status == OrderStatus.PAID else None,
        "total_amount": order.total_amount,
    }


# ─── Order Tracking (public) ─────────────────────────────────────────────────

@router.get("/orders/track/{order_number}")
@limiter.limit("60/minute")
async def track_order(
    request:      Request,
    order_number: str,
    db:           Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Public endpoint for the /track/{order_number} page.
    Returns enough info to render the step tracker.
    Does NOT expose receipt codes or tokens.
    """
    order = db.query(Order).filter(Order.order_number == order_number).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Status → step index (0-based)
    step_map = {
        OrderStatus.PENDING:      0,
        OrderStatus.CASH_PENDING: 0,
        OrderStatus.PAID:         1,
        OrderStatus.PREPARING:    2,
        OrderStatus.READY:        3,
        OrderStatus.PICKED_UP:    4,
        OrderStatus.COMPLETED:    4,
        OrderStatus.CANCELLED:    -1,
        OrderStatus.FAILED:       -1,
    }

    return {
        "order_number":   order.order_number,
        "customer_name":  order.customer_name,
        "status":         order.status.value,
        "step":           step_map.get(order.status, 0),
        "total_amount":   order.total_amount,
        "payment_method": order.payment_method.value,
        "items_count":    sum(i.get("quantity", 1) for i in (order.items_snapshot or [])),
        "created_at":     order.created_at.isoformat() if order.created_at else None,
        "picked_up_at":   order.picked_up_at.isoformat() if order.picked_up_at else None,
    }


# ─── Bank Transfer Claim ──────────────────────────────────────────────────────

@router.post("/payments/bank-transfer/claim")
@limiter.limit("3/minute")
async def bank_transfer_claim(
    request: Request,
    body:    BankTransferClaimRequest,
    db:      Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Customer signals they have completed a bank transfer.
    Sets the order to CASH_PENDING so a cashier can confirm.
    Stores their bank reference for admin verification.
    """
    order = db.query(Order).filter(
        Order.order_number == body.order_number,
        Order.tx_ref       == body.tx_ref,
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.payment_method != PaymentMethod.bank_transfer:
        raise HTTPException(status_code=400, detail="Order is not a bank transfer order")

    if order.status not in (OrderStatus.PENDING, OrderStatus.FAILED):
        raise HTTPException(status_code=400, detail=f"Order status is {order.status.value}")

    # Store the bank reference in notes
    claim_note = f"Bank transfer claimed. Customer ref: {body.bank_ref}"
    order.notes  = (order.notes + "\n" + claim_note) if order.notes else claim_note
    order.status = OrderStatus.CASH_PENDING   # awaits cashier confirmation

    _audit(
        db, AuditEvent.BANK_TRANSFER_CLAIMED, order,
        note=claim_note,
        ip_address=request.client.host,
    )
    db.commit()

    # Notify cashier in real-time
    await ws_manager.broadcast_to_room("cashier", {
        "event":        "bank_transfer_claim",
        "order_number": order.order_number,
        "bank_ref":     body.bank_ref,
    })

    return {
        "status":  "claim_received",
        "message": "Your transfer claim has been received. A cashier will verify and confirm your order.",
        "order_number": order.order_number,
    }
