from __future__ import annotations
import logging
import os
import uuid
from typing import Any, Dict, List

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from database import get_db
from models import (
    AuditEvent, AuditLog,
    DiscountType, MenuIngredient, MenuItem,
    Order, OrderStatus, PaymentMethod, PromoCode,
)
from payment_chapa import initialize_payment, verify_payment
from receipt import generate_receipt_code, generate_receipt_token
from schemas import (
    MenuItemResponse,
    OrderCreate,
    OrderResponse,
    PaymentInitResponse,
)
from sms import order_placed_msg, send_sms
from ws_manager import ws_manager

logger  = logging.getLogger(__name__)
router  = APIRouter(prefix="/api", tags=["Customer"])
limiter = Limiter(key_func=get_remote_address)

BASE_URL     = os.getenv("BASE_URL",     "http://localhost:8000")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:8000")


# Security: the payment init endpoint needs a tx_ref in the body to prevent IDOR.
# Without this, anyone who knows an integer order ID could re-initialize
# someone else's payment.
class _PaymentInitBody(BaseModel):
    tx_ref: str


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _generate_order_number() -> str:
    return f"ORD-{uuid.uuid4().hex[:8].upper()}"


async def _do_verify_and_mark_paid(tx_ref: str, db: Session) -> Dict[str, Any]:
    """
    Core server-side verification logic.
    Called from both the webhook and the client-facing poll endpoint.
    NEVER trusts any client-supplied payment status.
    """
    order = db.query(Order).filter(Order.tx_ref == tx_ref).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found for this tx_ref")

    if order.status == OrderStatus.PAID:
        return {
            "status":       "already_paid",
            "order_id":     order.id,
            "order_number": order.order_number,
            "receipt_code": order.receipt_code,
        }

    # Always call Chapa to confirm – never trust client data
    try:
        verification = await verify_payment(tx_ref)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return {"status": "not_found_on_gateway", "order_id": order.id}
        logger.error("Chapa verification HTTP error: %s", exc)
        raise HTTPException(status_code=502, detail="Payment gateway error during verification")
    except httpx.RequestError as exc:
        logger.error("Chapa network error: %s", exc)
        raise HTTPException(status_code=503, detail="Could not reach payment gateway")

    chapa_status = (
        verification.get("status") == "success"
        and verification.get("data", {}).get("status") == "success"
    )

    if not chapa_status:
        return {"status": "pending_or_failed", "order_id": order.id}

    # Verify the amount matches to prevent partial-payment attacks
    try:
        verified_amount = float(verification["data"].get("amount", 0))
    except (TypeError, ValueError):
        verified_amount = 0.0

    if abs(verified_amount - order.total_amount) > 0.50:  # 0.50 ETB tolerance
        order.status = OrderStatus.FAILED
        db.commit()
        logger.warning(
            "Amount mismatch for order %s: expected %.2f got %.2f",
            order.order_number,
            order.total_amount,
            verified_amount,
        )
        raise HTTPException(
            status_code=400,
            detail=f"Payment amount mismatch (expected {order.total_amount}, got {verified_amount})",
        )

    # Mark as PAID and generate receipt
    receipt_code  = generate_receipt_code()
    receipt_token = generate_receipt_token(
        order.id, receipt_code, order.total_amount, order.order_number
    )
    order.status        = OrderStatus.PAID
    order.receipt_code  = receipt_code
    order.receipt_token = receipt_token
    db.commit()
    db.refresh(order)

    logger.info("Order %s marked PAID via Chapa verification", order.order_number)

    # Push to kitchen — online payment confirmed, kitchen can start immediately
    await ws_manager.broadcast_to_room("kitchen", {
        "event":          "new_order",
        "order_number":   order.order_number,
        "customer_name":  order.customer_name,
        "total_amount":   order.total_amount,
        "payment_method": order.payment_method.value,
        "status":         OrderStatus.PAID.value,
        "source":         "online_payment",
    })
    await ws_manager.broadcast_to_room(f"track:{order.order_number}", {
        "event":  "status_change",
        "status": OrderStatus.PAID.value,
    })

    return {
        "status":        "paid",
        "order_id":      order.id,
        "order_number":  order.order_number,
        "receipt_code":  receipt_code,
        "receipt_token": receipt_token,
    }


# ─── Menu ─────────────────────────────────────────────────────────────────────

def _ing_to_dict(ing: MenuIngredient) -> dict:
    return {
        "id":           ing.id,
        "menu_item_id": ing.menu_item_id,
        "group_name":   ing.group_name,
        "name":         ing.name,
        "price_delta":  ing.price_delta,
        "input_type":   ing.input_type,
        "is_required":  ing.is_required,
        "is_default":   ing.is_default,
        "sort_order":   ing.sort_order,
        "is_available": ing.is_available,
    }


@router.get("/menu", response_model=List[MenuItemResponse])
def get_menu(category: str | None = None, db: Session = Depends(get_db)):
    """Return all available menu items with their customization options."""
    q = db.query(MenuItem).filter(MenuItem.is_available == True)
    if category:
        q = q.filter(MenuItem.category == category)
    items = q.order_by(MenuItem.category, MenuItem.name).all()

    # Batch-load available ingredients for all returned items
    item_ids = [item.id for item in items]
    ing_map: Dict[int, list] = {}
    if item_ids:
        ings = (
            db.query(MenuIngredient)
            .filter(
                MenuIngredient.menu_item_id.in_(item_ids),
                MenuIngredient.is_available == True,
            )
            .order_by(MenuIngredient.sort_order, MenuIngredient.id)
            .all()
        )
        for ing in ings:
            ing_map.setdefault(ing.menu_item_id, []).append(_ing_to_dict(ing))

    return [
        {
            "id":           item.id,
            "name":         item.name,
            "description":  item.description,
            "price":        item.price,
            "category":     item.category,
            "image_url":    item.image_url,
            "is_available": item.is_available,
            "ingredients":  ing_map.get(item.id, []),
        }
        for item in items
    ]


@router.get("/menu/categories")
def get_categories(db: Session = Depends(get_db)):
    """Return distinct categories that have at least one available item."""
    rows = (
        db.query(MenuItem.category)
        .filter(MenuItem.is_available == True)
        .distinct()
        .order_by(MenuItem.category)
        .all()
    )
    return [r[0] for r in rows]


# ─── Orders ───────────────────────────────────────────────────────────────────

@router.post("/orders", response_model=OrderResponse, status_code=201)
@limiter.limit("10/minute")  # Security: prevent fake-order flooding / DB abuse
async def create_order(request: Request, order_data: OrderCreate, db: Session = Depends(get_db)):
    """
    Create a new order.
    - Online payment  → status = PENDING, tx_ref generated for Chapa
    - Cash payment    → status = CASH_PENDING
    """
    items_snapshot: List[Dict[str, Any]] = []
    total = 0.0

    for item_in in order_data.items:
        menu_item = db.query(MenuItem).filter(
            MenuItem.id == item_in.menu_item_id,
            MenuItem.is_available == True,
        ).first()
        if not menu_item:
            raise HTTPException(
                status_code=404,
                detail=f"Menu item id={item_in.menu_item_id} not found or unavailable",
            )

        # ── Process customization options ───────────────────────────────────
        customization_data: List[Dict[str, Any]] = []
        price_delta = 0.0

        if item_in.customizations:
            # Fetch selected ingredients — must belong to this menu item
            selected_ings = db.query(MenuIngredient).filter(
                MenuIngredient.id.in_(item_in.customizations),
                MenuIngredient.menu_item_id == item_in.menu_item_id,
                MenuIngredient.is_available == True,
            ).all()

            # Guard against unknown/unavailable ingredient IDs
            if len(selected_ings) != len(set(item_in.customizations)):
                raise HTTPException(
                    status_code=400,
                    detail=f"One or more customization options are invalid for '{menu_item.name}'",
                )

            for ing in selected_ings:
                price_delta += ing.price_delta
                customization_data.append({
                    "id":          ing.id,
                    "group":       ing.group_name,
                    "name":        ing.name,
                    "price_delta": ing.price_delta,
                })

        # Validate that all required option groups have been satisfied
        all_ings = db.query(MenuIngredient).filter(
            MenuIngredient.menu_item_id == item_in.menu_item_id,
            MenuIngredient.is_available == True,
        ).all()
        required_groups  = {ing.group_name for ing in all_ings if ing.is_required}
        selected_groups  = {c["group"] for c in customization_data}
        missing_groups   = required_groups - selected_groups
        if missing_groups:
            raise HTTPException(
                status_code=400,
                detail=f"Required options not selected for '{menu_item.name}': {', '.join(sorted(missing_groups))}",
            )

        # ── Process toggle removals (default ingredients the customer removed) ──
        removal_data: List[Dict[str, Any]] = []
        if item_in.removals:
            removed_ings = db.query(MenuIngredient).filter(
                MenuIngredient.id.in_(item_in.removals),
                MenuIngredient.menu_item_id == item_in.menu_item_id,
                MenuIngredient.input_type == "toggle",
                MenuIngredient.is_available == True,
            ).all()
            found_removal_ids = {ing.id for ing in removed_ings}
            for rid in item_in.removals:
                if rid not in found_removal_ids:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid removal ingredient id={rid} for '{menu_item.name}'",
                    )
            for ing in removed_ings:
                removal_data.append({"id": ing.id, "name": ing.name})

        adjusted_price = round(menu_item.price + price_delta, 2)
        subtotal = round(adjusted_price * item_in.quantity, 2)
        total   += subtotal
        items_snapshot.append({
            "id":                   menu_item.id,
            "name":                 menu_item.name,
            "price":                menu_item.price,
            "price_adjusted":       adjusted_price,
            "quantity":             item_in.quantity,
            "subtotal":             subtotal,
            "customizations":       customization_data,
            "removals":             removal_data,
            "special_instructions": item_in.special_instructions,
        })

    # ── Promo code validation ─────────────────────────────────────────────────
    discount_amount = 0.0
    original_amount = round(total, 2)
    promo_code_used: str | None = None

    if order_data.promo_code:
        code = order_data.promo_code.strip().upper()
        from datetime import datetime, timezone
        now   = datetime.now(timezone.utc)
        promo = db.query(PromoCode).filter(
            PromoCode.code      == code,
            PromoCode.is_active == True,
        ).first()

        if promo:
            expired   = promo.expires_at and promo.expires_at < now
            maxed_out = promo.max_uses is not None and promo.uses_count >= promo.max_uses
            min_ok    = total >= (promo.min_order_total / 100)

            if not expired and not maxed_out and min_ok:
                if promo.discount_type == DiscountType.percent:
                    discount_amount = round(total * promo.discount_value / 100, 2)
                else:
                    discount_amount = round(promo.discount_value / 100, 2)
                discount_amount = min(discount_amount, total)
                total          -= discount_amount
                promo_code_used = code
                promo.uses_count += 1
                logger.info("Promo %s applied: -%.2f ETB", code, discount_amount)

    # ── Determine initial order status ────────────────────────────────────────
    initial_status = (
        OrderStatus.PENDING
        if order_data.payment_method in (
            PaymentMethod.online, PaymentMethod.telebirr,
            PaymentMethod.cbebirr, PaymentMethod.bank_transfer,
        )
        else OrderStatus.CASH_PENDING
    )

    order = Order(
        order_number    = _generate_order_number(),
        customer_name   = order_data.customer_name,
        customer_phone  = order_data.customer_phone,
        customer_email  = order_data.customer_email,
        items_snapshot  = items_snapshot,
        total_amount    = round(total, 2),
        status          = initial_status,
        payment_method  = order_data.payment_method,
        tx_ref          = f"order-{uuid.uuid4().hex}",
        notes           = order_data.notes,
        promo_code      = promo_code_used,
        discount_amount = discount_amount,
        original_amount = original_amount,
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    logger.info(
        "Created order %s method=%s total=%.2f (promo=%s discount=%.2f)",
        order.order_number, order_data.payment_method, order.total_amount,
        promo_code_used or "none", discount_amount,
    )

    # Real-time: notify cashier of new cash/bank orders (NOT kitchen — kitchen
    # only receives the order AFTER the cashier confirms payment)
    if initial_status == OrderStatus.CASH_PENDING:
        await ws_manager.broadcast_to_room("cashier", {
            "event":          "new_cash_order",
            "order_number":   order.order_number,
            "customer_name":  order.customer_name,
            "total_amount":   order.total_amount,
            "payment_method": order.payment_method.value,
        })

    # SMS order confirmation (fire-and-forget; do not block the response)
    if order.customer_phone:
        try:
            await send_sms(
                order.customer_phone,
                order_placed_msg(order.order_number, order.total_amount, order.payment_method.value),
            )
        except Exception:
            pass

    return order


@router.get("/orders/{order_id}", response_model=OrderResponse)
def get_order(
    order_id: int,
    tx_ref: str = Query(..., description="Transaction reference — returned when order was created"),
    db: Session = Depends(get_db),
):
    """
    Retrieve a single order by ID for status polling.

    Security: requires tx_ref to prevent IDOR (Insecure Direct Object Reference).
    Without this check, any anonymous caller could iterate integer IDs and read
    every customer's name, phone, email, and receipt token.
    The tx_ref is a UUID generated at order creation — only the customer who placed
    the order has it, making it act as an unguessable bearer credential.
    """
    # Security: require tx_ref to match — prevents enumeration of orders by integer ID
    order = db.query(Order).filter(Order.id == order_id, Order.tx_ref == tx_ref).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


# ─── Payment ──────────────────────────────────────────────────────────────────

@router.post("/payment/initialize/{order_id}", response_model=PaymentInitResponse)
@limiter.limit("5/minute")  # Security: prevent repeated payment init attempts
async def init_payment(
    request: Request,
    order_id: int,
    body: _PaymentInitBody,
    db: Session = Depends(get_db),
):
    """
    Initialize a Chapa payment for an online order.
    Returns the Chapa checkout URL the frontend should redirect to.

    Security: requires tx_ref in the request body to prevent IDOR.
    Without this, an attacker who knows any integer order ID could trigger
    payment re-initialization for someone else's order.
    The tx_ref is the UUID returned at order creation — only the original
    customer has it.
    """
    # Security: match both order_id AND tx_ref — prevents IDOR via integer enumeration
    order = db.query(Order).filter(Order.id == order_id, Order.tx_ref == body.tx_ref).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.payment_method != PaymentMethod.online:
        raise HTTPException(status_code=400, detail="This order uses cash payment")
    if order.status not in [OrderStatus.PENDING, OrderStatus.FAILED]:
        raise HTTPException(
            status_code=400,
            detail=f"Order cannot be paid (current status: {order.status.value})",
        )

    name_parts  = order.customer_name.strip().split(" ", 1)
    first_name  = name_parts[0]
    last_name   = name_parts[1] if len(name_parts) > 1 else "Customer"
    email       = order.customer_email or f"customer_{order.id}@order.et"

    callback_url = f"{BASE_URL}/api/payment/webhook"
    return_url   = (
        f"{FRONTEND_URL}/frontend/customer.html"
        f"?order_id={order_id}&tx_ref={order.tx_ref}&payment=return"
    )

    try:
        result = await initialize_payment(
            amount       = order.total_amount,
            currency     = "ETB",
            email        = email,
            first_name   = first_name,
            last_name    = last_name,
            tx_ref       = order.tx_ref,
            callback_url = callback_url,
            return_url   = return_url,
            customization = {
                "title":       "Food Order Payment",
                "description": f"Order {order.order_number} – {order.customer_name}",
                "logo":        "",
            },
        )
    except httpx.HTTPStatusError as exc:
        logger.error("Chapa init error %s: %s", exc.response.status_code, exc.response.text)
        raise HTTPException(status_code=502, detail="Payment gateway error during initialization")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail=f"Could not reach payment gateway: {exc}")

    if result.get("status") != "success":
        raise HTTPException(
            status_code=400,
            detail=f"Chapa initialization failed: {result.get('message', 'Unknown error')}",
        )

    checkout_url = result["data"]["checkout_url"]
    order.chapa_checkout_url = checkout_url
    db.commit()

    return PaymentInitResponse(
        checkout_url = checkout_url,
        tx_ref       = order.tx_ref,
        order_id     = order.id,
    )


@router.post("/payment/webhook")
@limiter.limit("60/minute")  # Security: prevent Chapa API exhaustion via fake webhook flooding
async def payment_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Chapa webhook endpoint.
    We NEVER trust the webhook body alone — we always call Chapa's verify API.

    Security: rate-limited to prevent an attacker from sending thousands of fake
    tx_refs, which would exhaust our Chapa API quota and cause real payments to fail.
    """
    try:
        body = await request.json()
    except Exception:
        return {"status": "ignored", "reason": "invalid JSON"}

    tx_ref = body.get("tx_ref") or body.get("trx_ref") or body.get("transaction_id")
    if not tx_ref:
        return {"status": "ignored", "reason": "no tx_ref in payload"}

    try:
        result = await _do_verify_and_mark_paid(tx_ref, db)
        return {"status": "processed", "result": result.get("status")}
    except HTTPException as exc:
        logger.warning("Webhook verification raised HTTPException: %s", exc.detail)
        return {"status": "error", "detail": exc.detail}


@router.get("/payment/verify/{tx_ref}")
@limiter.limit("10/minute")  # Security: prevent exhausting Chapa API via rapid polling
async def verify_payment_status(request: Request, tx_ref: str, db: Session = Depends(get_db)):
    """
    Client-callable polling endpoint.
    Frontend calls this after returning from Chapa to confirm payment status.
    Rate-limited to prevent Chapa API exhaustion attacks.
    """
    return await _do_verify_and_mark_paid(tx_ref, db)
