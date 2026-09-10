from __future__ import annotations
import logging
import uuid
from collections import defaultdict
from datetime import datetime, date, timezone, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from auth import require_role
from database import get_db
from models import (
    AuditEvent, AuditLog, MenuItem, MenuIngredient,
    Order, OrderStatus, PaymentMethod,
    Refund, RefundStatus,
    SuspiciousActivity, User, UserRole,
)
from receipt import generate_receipt_code, generate_receipt_token, verify_receipt_token
from schemas import (
    AuditLogResponse,
    CheckReceiptRequest,
    OrderResponse,
    RedeemRequest,
    RedeemResponse,
    VerifyReceiptRequest,
    VerifyReceiptResponse,
)
from sms import order_placed_msg, send_sms
from ws_manager import ws_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cashier", tags=["Cashier / Verifier"])

limiter = Limiter(key_func=get_remote_address)

# Statuses in which an order's receipt can be redeemed
_REDEEMABLE_STATUSES = {OrderStatus.PAID, OrderStatus.PREPARING, OrderStatus.READY}

# Track per-IP invalid attempt counts for suspicious-activity flagging
# (in-memory; resets on server restart — suitable for single-server deployment)
_invalid_attempts: dict[str, int] = defaultdict(int)
_SUSPICIOUS_THRESHOLD = 5  # flag after 5 consecutive invalid attempts from same IP


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _write_audit(
    db: Session,
    event: AuditEvent,
    actor: User,
    ip: str,
    order: Order | None = None,
    terminal: str | None = None,
    note: str = "",
) -> None:
    """Insert one immutable audit row. Never call db.commit() here — caller owns the tx."""
    db.add(AuditLog(
        event        = event,
        order_id     = order.id          if order else None,
        order_number = order.order_number if order else None,
        actor_id     = actor.id,
        actor_name   = actor.username,
        actor_role   = actor.role.value,
        ip_address   = ip,
        terminal_id  = terminal,
        note         = note,
    ))


def _flag_suspicious(
    db: Session,
    type_: str,
    description: str,
    ip: str,
    actor: User | None = None,
    order: Order | None = None,
) -> None:
    db.add(SuspiciousActivity(
        type        = type_,
        description = description,
        ip_address  = ip,
        actor_id    = actor.id       if actor else None,
        actor_name  = actor.username if actor else None,
        order_id    = order.id       if order else None,
    ))


def _resolve_order(
    db: Session,
    token: str | None,
    receipt_code: str | None,
) -> tuple[Order | None, str | None]:
    """
    Look up an order by token or receipt_code.
    Returns (order, error_message). error_message is None on success.
    """
    if not token and not receipt_code:
        return None, "Provide either a QR token or a receipt code."

    if token:
        is_valid, reason, payload = verify_receipt_token(token.strip())
        if not is_valid:
            return None, reason
        order = db.query(Order).filter(Order.id == payload.get("oid")).first()
        if not order:
            return None, "Order referenced in token not found."
        if payload.get("rc") != order.receipt_code:
            return None, "Token receipt code mismatch — possible tampering."
        return order, None

    # Short code lookup
    order = db.query(Order).filter(
        Order.receipt_code == receipt_code.upper()
    ).first()
    if not order:
        return None, "Receipt code not found."
    return order, None


# ─── Check (preview only — no state change) ───────────────────────────────────

@router.post("/check")
@limiter.limit("60/minute")
def check_receipt(
    request: Request,
    payload: CheckReceiptRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(
        UserRole.cashier, UserRole.admin, UserRole.kitchen
    )),
):
    """
    Look up an order by token or receipt_code.
    Returns full order details WITHOUT marking it as picked up.
    Use this when a customer asks 'is my order ready?'
    """
    order, err = _resolve_order(db, payload.token, payload.receipt_code)
    if err:
        raise HTTPException(404, detail=err)

    return {
        "order_number":   order.order_number,
        "customer_name":  order.customer_name,
        "status":         order.status.value,
        "total_amount":   order.total_amount,
        "payment_method": order.payment_method.value,
        "items":          order.items_snapshot,
        "redeemed":       order.redeemed,
        "picked_up_at":   order.picked_up_at.isoformat() if order.picked_up_at else None,
    }


# ─── Redeem (atomic pickup — the main cashier action) ─────────────────────────

@router.post("/redeem", response_model=RedeemResponse)
@limiter.limit("30/minute")
def redeem_order(
    request: Request,
    payload: RedeemRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(
        UserRole.cashier, UserRole.admin,
    )),
):
    """
    Verify the receipt AND atomically mark the order as PICKED_UP.

    This is the single action the cashier performs when handing over food.

    Security guarantees:
    - Row-level SELECT FOR UPDATE prevents two simultaneous redemptions of the
      same order on different registers (PostgreSQL serialises them; the second
      one sees PICKED_UP and returns 409).
    - AuditLog row is written inside the same transaction so it is never lost.
    - Suspicious-activity flags are raised on duplicate scans and invalid-token
      floods, giving the admin a real-time fraud signal.
    """
    ip = request.client.host

    # ── 1. Resolve order ──────────────────────────────────────────────────────
    order, err = _resolve_order(db, payload.token, payload.receipt_code)

    if err:
        # Count invalid attempts per IP for flood detection
        _invalid_attempts[ip] += 1
        if _invalid_attempts[ip] >= _SUSPICIOUS_THRESHOLD:
            _flag_suspicious(
                db, "INVALID_TOKEN_FLOOD",
                f"{_invalid_attempts[ip]} invalid attempts from {ip}",
                ip, current_user,
            )
            db.commit()
            _invalid_attempts[ip] = 0  # reset after flagging
        else:
            db.commit()

        _write_audit(db, AuditEvent.REDEEM_INVALID, current_user, ip,
                     terminal=payload.terminal_id, note=err)
        db.commit()

        raise HTTPException(404, detail={"status": "INVALID", "message": err})

    # Reset invalid-attempt counter on a successful lookup
    _invalid_attempts[ip] = 0

    # ── 2. Early check for already-redeemed (fast path, before lock) ──────────
    if order.status == OrderStatus.PICKED_UP or order.redeemed:
        picked_by_name = None
        if order.picked_up_by_id:
            u = db.query(User).filter(User.id == order.picked_up_by_id).first()
            picked_by_name = u.username if u else None

        _write_audit(
            db, AuditEvent.REDEEM_ALREADY_USED, current_user, ip, order,
            payload.terminal_id,
            f"Already picked up at {order.picked_up_at} by user_id={order.picked_up_by_id}",
        )
        # Duplicate scan may indicate receipt sharing — flag it
        _flag_suspicious(
            db, "DUPLICATE_SCAN",
            f"Order {order.order_number} scanned again by {current_user.username} from {ip}",
            ip, current_user, order,
        )
        db.commit()

        raise HTTPException(409, detail={
            "status":       "ALREADY_REDEEMED",
            "message":      "This order has already been picked up.",
            "order_number": order.order_number,
            "picked_up_at": order.picked_up_at.isoformat() if order.picked_up_at else None,
            "picked_up_by": picked_by_name,
        })

    # ── 3. Check order is in a redeemable payment state ───────────────────────
    if order.status not in _REDEEMABLE_STATUSES:
        _write_audit(
            db, AuditEvent.REDEEM_NOT_READY, current_user, ip, order,
            payload.terminal_id,
            f"Status was {order.status.value}",
        )
        db.commit()

        raise HTTPException(400, detail={
            "status":         "NOT_READY",
            "message":        _not_ready_message(order.status),
            "current_status": order.status.value,
        })

    # ── 4. Acquire row-level lock and re-verify status atomically ─────────────
    #    SELECT ... FOR UPDATE locks this specific row in PostgreSQL.
    #    If two cashiers scan simultaneously, only one gets the lock;
    #    the other waits, then sees PICKED_UP and hits the 409 branch.
    locked_order = (
        db.execute(
            select(Order)
            .where(Order.id == order.id)
            .where(Order.status.in_(list(_REDEEMABLE_STATUSES)))
            .with_for_update()
        )
        .scalars()
        .first()
    )

    if not locked_order:
        # Another concurrent request grabbed the lock first and changed the status
        raise HTTPException(409, detail={
            "status":  "ALREADY_REDEEMED",
            "message": "Order was just redeemed by another terminal.",
        })

    # ── 5. Mark as PICKED_UP ──────────────────────────────────────────────────
    now = datetime.now(timezone.utc)
    locked_order.status           = OrderStatus.PICKED_UP
    locked_order.redeemed         = True
    locked_order.redeemed_at      = now
    locked_order.picked_up_at     = now
    locked_order.picked_up_by_id  = current_user.id
    locked_order.pickup_location  = payload.terminal_id
    locked_order.redemption_count = (locked_order.redemption_count or 0) + 1

    _write_audit(
        db, AuditEvent.REDEEM_SUCCESS, current_user, ip, locked_order,
        payload.terminal_id,
        f"Order handed over at terminal '{payload.terminal_id}'",
    )
    db.commit()
    db.refresh(locked_order)

    logger.info(
        "Order %s PICKED UP by '%s' at terminal '%s'",
        locked_order.order_number, current_user.username, payload.terminal_id,
    )

    return RedeemResponse(
        status         = "REDEEMED",
        order_number   = locked_order.order_number,
        customer_name  = locked_order.customer_name,
        items          = locked_order.items_snapshot,
        total_amount   = locked_order.total_amount,
        payment_method = locked_order.payment_method.value,
        picked_up_at   = locked_order.picked_up_at,
        picked_up_by   = current_user.username,
        message        = f"Order {locked_order.order_number} handed over successfully.",
    )


def _not_ready_message(status: OrderStatus) -> str:
    return {
        OrderStatus.PENDING:      "Payment not confirmed. Order is awaiting online payment.",
        OrderStatus.CASH_PENDING: "Cash not yet collected. Mark as paid before handing over.",
        OrderStatus.PREPARING:    "Kitchen is still preparing this order. Please wait.",
        OrderStatus.CANCELLED:    "This order has been cancelled.",
        OrderStatus.FAILED:       "Payment failed for this order.",
        OrderStatus.PICKED_UP:    "This order has already been picked up.",
        OrderStatus.COMPLETED:    "This order has already been completed.",
    }.get(status, f"Order status is {status.value}.")


# ─── Legacy verify endpoint (kept for backward compatibility) ─────────────────

@router.post("/verify", response_model=VerifyReceiptResponse)
@limiter.limit("30/minute")
def verify_receipt(
    request: Request,
    verify_data: VerifyReceiptRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.cashier, UserRole.admin)),
):
    """
    Legacy endpoint — now delegates to the atomic redeem logic.
    Kept so existing integrations do not break.
    """
    raw = verify_data.token.strip()
    if not raw:
        return VerifyReceiptResponse(valid=False, message="No token or code provided")

    # Determine token vs short code
    is_full_token = raw.count(".") == 2
    redeem_payload = RedeemRequest(
        token        = raw if is_full_token else None,
        receipt_code = raw if not is_full_token else None,
        terminal_id  = "legacy-verify",
    )

    try:
        result = redeem_order(request, redeem_payload, db, current_user)
        # Re-fetch the order for the legacy response shape
        order = db.query(Order).filter(
            Order.order_number == result.order_number
        ).first()
        return VerifyReceiptResponse(
            valid   = True,
            message = result.message,
            order   = order,
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        return VerifyReceiptResponse(
            valid   = False,
            message = detail.get("message", str(exc.detail)),
        )


# ─── Supporting cashier queries ───────────────────────────────────────────────

@router.get("/orders/cash-pending", response_model=List[OrderResponse])
def get_cash_pending_orders(
    db: Session = Depends(get_db),
    _: User = Depends(require_role(UserRole.cashier, UserRole.admin)),
):
    """Return all cash orders awaiting payment confirmation."""
    return (
        db.query(Order)
        .filter(Order.status == OrderStatus.CASH_PENDING)
        .order_by(Order.created_at.desc())
        .all()
    )


@router.get("/orders/recent", response_model=List[OrderResponse])
def get_recent_picked_up_orders(
    limit: int = 20,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(UserRole.cashier, UserRole.admin)),
):
    """Return the most recently picked-up orders for shift review."""
    return (
        db.query(Order)
        .filter(Order.status == OrderStatus.PICKED_UP)
        .order_by(Order.picked_up_at.desc())
        .limit(min(limit, 100))
        .all()
    )


@router.get("/audit", response_model=List[AuditLogResponse])
def get_audit_logs(
    order_id: int | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(UserRole.cashier, UserRole.admin)),
):
    """Return audit log entries, optionally filtered by order."""
    q = db.query(AuditLog)
    if order_id:
        q = q.filter(AuditLog.order_id == order_id)
    return q.order_by(AuditLog.created_at.desc()).limit(min(limit, 200)).all()


# ═══════════════════════════════════════════════════════════════════════════════
# POS TERMINAL ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

# ─── POS Menu (cashier-visible items + their options) ────────────────────────

@router.get("/menu")
def pos_menu(
    db: Session = Depends(get_db),
    _: User = Depends(require_role(UserRole.cashier, UserRole.admin)),
) -> List[Dict[str, Any]]:
    """
    Full menu for the POS terminal.
    Returns only cashier_visible=True items, each with their ingredient options.
    """
    items = (
        db.query(MenuItem)
        .filter(MenuItem.cashier_visible == True)
        .order_by(MenuItem.category, MenuItem.sort_order, MenuItem.name)
        .all()
    )

    result = []
    for item in items:
        ings = (
            db.query(MenuIngredient)
            .filter(
                MenuIngredient.menu_item_id == item.id,
                MenuIngredient.is_available == True,
            )
            .order_by(MenuIngredient.group_name, MenuIngredient.sort_order)
            .all()
        )
        # Group ingredients by group_name
        groups: Dict[str, list] = {}
        for ing in ings:
            groups.setdefault(ing.group_name, []).append({
                "id":          ing.id,
                "name":        ing.name,
                "price_delta": ing.price_delta,
                "input_type":  ing.input_type,
                "is_required": ing.is_required,
                "is_default":  ing.is_default,
                "sort_order":  ing.sort_order,
            })

        result.append({
            "id":            item.id,
            "name":          item.name,
            "description":   item.description,
            "price":         item.price,
            "category":      item.category,
            "is_available":  item.is_available,
            "cashier_note":  item.cashier_note,
            "option_groups": [
                {"group_name": gn, "options": opts}
                for gn, opts in groups.items()
            ],
        })
    return result


# ─── POS Charge (walk-up order — create + immediately paid) ──────────────────

class POSLineItem(BaseModel):
    menu_item_id: int
    quantity: int = 1
    customization_ids: List[int] = []
    special_instructions: Optional[str] = None


class POSChargeRequest(BaseModel):
    customer_name:  str = "Walk-in"
    customer_phone: Optional[str] = None
    items:          List[POSLineItem]
    payment_method: str = "cash"        # cash | telebirr | cbebirr | bank_transfer | card
    amount_tendered: Optional[float] = None   # cash given by customer (for change calc)
    notes:          Optional[str] = None
    tax_rate:       float = 0.0         # percentage, e.g. 15.0 for 15% — reserved for later


@router.post("/pos/charge")
async def pos_charge(
    request: Request,
    body:    POSChargeRequest,
    db:      Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.cashier, UserRole.admin)),
) -> Dict[str, Any]:
    """
    Create a walk-up (in-person) order and mark it PAID in one atomic step.
    Generates a receipt code immediately.
    """
    if not body.items:
        raise HTTPException(400, detail="Order must contain at least one item")

    # ── Build items snapshot ───────────────────────────────────────────────────
    items_snapshot = []
    subtotal = 0.0

    for line in body.items:
        menu_item = db.query(MenuItem).filter(
            MenuItem.id == line.menu_item_id,
            MenuItem.is_available == True,
        ).first()
        if not menu_item:
            raise HTTPException(404, detail=f"Menu item {line.menu_item_id} not found or unavailable")

        # Resolve customisations
        customizations = []
        price_adj = menu_item.price
        for cid in line.customization_ids:
            ing = db.query(MenuIngredient).filter(MenuIngredient.id == cid).first()
            if ing:
                price_adj += ing.price_delta
                customizations.append({"id": ing.id, "name": ing.name, "price_delta": ing.price_delta})

        line_total = round(price_adj * line.quantity, 2)
        subtotal  += line_total
        items_snapshot.append({
            "id":                   menu_item.id,
            "name":                 menu_item.name,
            "price":                menu_item.price,
            "price_adjusted":       round(price_adj, 2),
            "quantity":             line.quantity,
            "subtotal":             line_total,
            "customizations":       customizations,
            "removals":             [],
            "special_instructions": line.special_instructions,
        })

    # Tax placeholder (stored as 0 until admin enables it)
    tax_amount   = round(subtotal * body.tax_rate / 100, 2)
    total_amount = round(subtotal + tax_amount, 2)

    # Change calculation
    change = None
    if body.payment_method == "cash" and body.amount_tendered is not None:
        change = round(body.amount_tendered - total_amount, 2)
        if change < 0:
            raise HTTPException(400, detail=f"Amount tendered (ETB {body.amount_tendered:.2f}) is less than total (ETB {total_amount:.2f})")

    # ── Create order as already PAID ──────────────────────────────────────────
    receipt_code  = generate_receipt_code()
    order_number  = f"POS-{uuid.uuid4().hex[:8].upper()}"
    receipt_token = generate_receipt_token(0, receipt_code, total_amount, order_number)  # id=0 placeholder

    try:
        pay_method = PaymentMethod(body.payment_method)
    except ValueError:
        pay_method = PaymentMethod.cash

    order = Order(
        order_number    = order_number,
        customer_name   = body.customer_name.strip() or "Walk-in",
        customer_phone  = body.customer_phone,
        items_snapshot  = items_snapshot,
        total_amount    = total_amount,
        status          = OrderStatus.PAID,
        payment_method  = pay_method,
        tx_ref          = f"pos-{uuid.uuid4().hex}",
        notes           = body.notes,
        receipt_code    = receipt_code,
        receipt_token   = receipt_token,
        picked_up_by_id = current_user.id,
    )
    db.add(order)
    db.flush()  # get order.id

    # Fix receipt token with real order id
    order.receipt_token = generate_receipt_token(order.id, receipt_code, total_amount, order_number)

    db.add(AuditLog(
        event        = AuditEvent.ORDER_CREATED,
        order_id     = order.id,
        order_number = order_number,
        actor_id     = current_user.id,
        actor_name   = current_user.username,
        actor_role   = current_user.role.value,
        ip_address   = request.client.host,
        note         = f"POS charge by {current_user.username} — method={pay_method.value} total={total_amount}",
    ))
    db.commit()
    db.refresh(order)

    # ── Push to kitchen immediately — payment confirmed at POS counter ──────────
    await ws_manager.broadcast_to_room("kitchen", {
        "event":          "new_order",
        "order_number":   order_number,
        "customer_name":  order.customer_name,
        "total_amount":   total_amount,
        "payment_method": pay_method.value,
        "status":         "PAID",
        "source":         "pos",
    })
    await ws_manager.broadcast_to_room("cashier", {
        "event":        "payment_confirmed",
        "order_number": order_number,
        "status":       "PAID",
    })

    # SMS if phone provided
    if body.customer_phone:
        try:
            await send_sms(
                body.customer_phone,
                order_placed_msg(order_number, total_amount, pay_method.value),
            )
        except Exception:
            pass  # SMS failure must never break the POS transaction

    logger.info("POS charge: %s by %s — ETB %.2f", order_number, current_user.username, total_amount)

    return {
        "order_number":   order_number,
        "receipt_code":   receipt_code,
        "total_amount":   total_amount,
        "tax_amount":     tax_amount,
        "subtotal":       subtotal,
        "change":         change,
        "payment_method": pay_method.value,
        "items":          items_snapshot,
        "customer_name":  order.customer_name,
    }


# ─── Cash Flow / Daily Summary ────────────────────────────────────────────────

@router.get("/cash-flow")
def cash_flow(
    day: Optional[str] = Query(None, description="ISO date YYYY-MM-DD, defaults to today"),
    db:  Session = Depends(get_db),
    _:   User    = Depends(require_role(UserRole.cashier, UserRole.admin)),
) -> Dict[str, Any]:
    """
    Daily cash flow summary.
    Returns gross revenue, refunds, net, and a breakdown by payment method.
    """
    if day:
        try:
            target = date.fromisoformat(day)
        except ValueError:
            raise HTTPException(400, detail="Invalid date format. Use YYYY-MM-DD")
    else:
        target = date.today()

    tz     = timezone.utc
    day_start = datetime(target.year, target.month, target.day, 0,  0,  0, tzinfo=tz)
    day_end   = datetime(target.year, target.month, target.day, 23, 59, 59, tzinfo=tz)

    # Orders completed (paid / preparing / ready / picked up) on this day
    paid_statuses = [
        OrderStatus.PAID, OrderStatus.PREPARING,
        OrderStatus.READY, OrderStatus.PICKED_UP, OrderStatus.COMPLETED,
    ]
    orders = (
        db.query(Order)
        .filter(
            Order.status.in_(paid_statuses),
            Order.created_at >= day_start,
            Order.created_at <= day_end,
        )
        .all()
    )

    gross_revenue = sum(o.total_amount for o in orders)
    order_count   = len(orders)

    # Breakdown by payment method
    method_totals: Dict[str, float] = {}
    method_counts: Dict[str, int]   = {}
    for o in orders:
        m = o.payment_method.value
        method_totals[m] = round(method_totals.get(m, 0) + o.total_amount, 2)
        method_counts[m] = method_counts.get(m, 0) + 1

    # Refunds on this day
    refunds = (
        db.query(Refund)
        .filter(
            Refund.created_at >= day_start,
            Refund.created_at <= day_end,
        )
        .all()
    )
    total_refunds  = round(sum(r.amount for r in refunds), 2)
    refund_count   = len(refunds)
    net_revenue    = round(gross_revenue - total_refunds, 2)

    # Average order value
    avg_order = round(gross_revenue / order_count, 2) if order_count else 0.0

    # Picked-up orders count
    handover_count = sum(1 for o in orders if o.status in (OrderStatus.PICKED_UP, OrderStatus.COMPLETED))

    return {
        "date":           target.isoformat(),
        "gross_revenue":  round(gross_revenue, 2),
        "total_refunds":  total_refunds,
        "net_revenue":    net_revenue,
        "order_count":    order_count,
        "handover_count": handover_count,
        "avg_order_value":avg_order,
        "refund_count":   refund_count,
        "by_method":      [
            {"method": m, "total": method_totals[m], "count": method_counts[m]}
            for m in method_totals
        ],
        "refunds": [
            {
                "id":            r.id,
                "order_number":  r.order_number,
                "amount":        r.amount,
                "reason":        r.reason,
                "method":        r.refund_method,
                "status":        r.status.value,
                "created_at":    r.created_at.isoformat() if r.created_at else None,
            }
            for r in refunds
        ],
    }


# ─── Refunds ──────────────────────────────────────────────────────────────────

class RefundCreateRequest(BaseModel):
    order_number:      str
    amount:            float
    reason:            Optional[str] = None
    refund_method:     str = "cash"   # cash | telebirr | cbebirr | bank_transfer | card
    dest_bank_name:    Optional[str] = None
    dest_account_no:   Optional[str] = None
    dest_account_name: Optional[str] = None
    dest_phone:        Optional[str] = None
    # PCI: we NEVER accept or store a full card number — last 4 digits only, as a
    # human reference. Pattern rejects anything longer at the validation layer.
    dest_card_last4:   Optional[str] = Field(None, pattern=r"^\d{4}$")
    notes:             Optional[str] = None
    station_id:        Optional[str] = Field(None, max_length=100)  # register / terminal label


@router.post("/refunds")
def create_refund(
    request: Request,
    body:    RefundCreateRequest,
    db:      Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.cashier, UserRole.admin)),
) -> Dict[str, Any]:
    """
    Issue a refund against an order.
    The cashier records the refund; for electronic methods the details are stored
    so the admin/finance team can process the actual transfer.
    Cash refunds are marked 'processed' immediately.
    """
    if body.amount <= 0:
        raise HTTPException(400, detail="Refund amount must be greater than zero")

    # Look up the order (optional — order may have been deleted)
    order = db.query(Order).filter(Order.order_number == body.order_number).first()
    if order and body.amount > order.total_amount:
        raise HTTPException(400, detail=f"Refund amount exceeds order total (ETB {order.total_amount:.2f})")

    # Cash refunds are processed immediately at the counter
    is_immediate = body.refund_method == "cash"
    now = datetime.now(timezone.utc)

    refund = Refund(
        order_id          = order.id if order else None,
        order_number      = body.order_number,
        amount            = round(body.amount, 2),
        reason            = body.reason,
        refund_method     = body.refund_method,
        dest_bank_name    = body.dest_bank_name,
        dest_account_no   = body.dest_account_no,
        dest_account_name = body.dest_account_name,
        dest_phone        = body.dest_phone,
        dest_card_last4   = body.dest_card_last4,
        status            = RefundStatus.processed if is_immediate else RefundStatus.pending,
        processed_by_id   = current_user.id   if is_immediate else None,
        processed_by_name = current_user.username if is_immediate else None,
        processed_at      = now               if is_immediate else None,
        notes             = body.notes,
    )
    db.add(refund)

    db.add(AuditLog(
        event        = AuditEvent.REFUND_ISSUED,
        order_id     = order.id if order else None,
        order_number = body.order_number,
        actor_id     = current_user.id,
        actor_name   = current_user.username,
        actor_role   = current_user.role.value,
        ip_address   = request.client.host,
        terminal_id  = body.station_id,
        note         = f"Refund ETB {body.amount:.2f} via {body.refund_method} — {body.reason or 'no reason'}",
    ))
    db.commit()
    db.refresh(refund)

    logger.info(
        "Refund %d issued by %s — ETB %.2f for order %s via %s",
        refund.id, current_user.username, refund.amount, body.order_number, body.refund_method,
    )

    return {
        "id":            refund.id,
        "order_number":  refund.order_number,
        "amount":        refund.amount,
        "refund_method": refund.refund_method,
        "status":        refund.status.value,
        "message":       (
            f"Cash refund of ETB {refund.amount:.2f} processed."
            if is_immediate else
            f"Refund of ETB {refund.amount:.2f} via {body.refund_method} recorded. Finance team will process the transfer."
        ),
    }


@router.get("/refunds")
def list_refunds(
    day:    Optional[str] = Query(None),
    limit:  int = 50,
    db:     Session = Depends(get_db),
    _:      User    = Depends(require_role(UserRole.cashier, UserRole.admin)),
) -> List[Dict[str, Any]]:
    """List refunds, optionally filtered by date."""
    q = db.query(Refund)
    if day:
        try:
            target = date.fromisoformat(day)
        except ValueError:
            raise HTTPException(400, detail="Invalid date format")
        tz = timezone.utc
        q  = q.filter(
            Refund.created_at >= datetime(target.year, target.month, target.day, tzinfo=tz),
            Refund.created_at <  datetime(target.year, target.month, target.day, tzinfo=tz) + timedelta(days=1),
        )
    refunds = q.order_by(Refund.created_at.desc()).limit(min(limit, 200)).all()
    return [
        {
            "id":               r.id,
            "order_number":     r.order_number,
            "amount":           r.amount,
            "reason":           r.reason,
            "refund_method":    r.refund_method,
            "dest_bank_name":   r.dest_bank_name,
            "dest_account_no":  r.dest_account_no,
            "dest_account_name":r.dest_account_name,
            "dest_phone":       r.dest_phone,
            "dest_card_last4":  r.dest_card_last4,
            "status":           r.status.value,
            "processed_by":     r.processed_by_name,
            "processed_at":     r.processed_at.isoformat() if r.processed_at else None,
            "notes":            r.notes,
            "created_at":       r.created_at.isoformat() if r.created_at else None,
        }
        for r in refunds
    ]


@router.patch("/refunds/{refund_id}/mark-processed")
def mark_refund_processed(
    refund_id:    int,
    db:           Session = Depends(get_db),
    current_user: User    = Depends(require_role(UserRole.cashier, UserRole.admin)),
) -> Dict[str, Any]:
    """Mark an electronic refund as processed (money has been sent)."""
    refund = db.query(Refund).filter(Refund.id == refund_id).first()
    if not refund:
        raise HTTPException(404, detail="Refund not found")
    if refund.status == RefundStatus.processed:
        raise HTTPException(400, detail="Refund already marked as processed")

    refund.status            = RefundStatus.processed
    refund.processed_by_id   = current_user.id
    refund.processed_by_name = current_user.username
    refund.processed_at      = datetime.now(timezone.utc)
    db.commit()
    return {"id": refund.id, "status": "processed"}
