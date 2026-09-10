from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from auth import require_role
from database import get_db
from models import (
    AuditEvent, AuditLog, LoyaltyAccount, MenuIngredient, MenuItem, Order, OrderStatus,
    PaymentMethod, Review, SuspiciousActivity, User, UserRole,
)
from receipt import generate_receipt_code, generate_receipt_token
from ws_manager import ws_manager
from schemas import (
    AnalyticsResponse,
    AuditLogResponse,
    MenuIngredientCreate,
    MenuIngredientResponse,
    MenuIngredientUpdate,
    MenuItemCreate,
    MenuItemResponse,
    MenuItemUpdate,
    OrderResponse,
    ResetPickupRequest,
    SuspiciousActivityResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["Admin"])

_PAID_STATUSES = [
    OrderStatus.PAID,
    OrderStatus.PREPARING,
    OrderStatus.READY,
    OrderStatus.PICKED_UP,
    OrderStatus.COMPLETED,
]


# ─── Menu Management ──────────────────────────────────────────────────────────

@router.get("/menu", response_model=List[MenuItemResponse])
def get_all_menu_items(
    db: Session = Depends(get_db),
    _: User = Depends(require_role(UserRole.admin)),
):
    """Return all menu items (including unavailable ones)."""
    return db.query(MenuItem).order_by(MenuItem.category, MenuItem.name).all()


@router.post("/menu", response_model=MenuItemResponse, status_code=201)
def create_menu_item(
    item_data: MenuItemCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(UserRole.admin)),
):
    item = MenuItem(**item_data.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    logger.info("Admin created menu item '%s'", item.name)
    return item


@router.put("/menu/{item_id}", response_model=MenuItemResponse)
def update_menu_item(
    item_id: int,
    item_data: MenuItemUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(UserRole.admin)),
):
    item = db.query(MenuItem).filter(MenuItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Menu item not found")

    for field, value in item_data.model_dump(exclude_unset=True).items():
        setattr(item, field, value)

    db.commit()
    db.refresh(item)
    return item


@router.delete("/menu/{item_id}", status_code=204)
def delete_menu_item(
    item_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(UserRole.admin)),
):
    item = db.query(MenuItem).filter(MenuItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Menu item not found")
    db.delete(item)
    db.commit()
    logger.info("Admin deleted menu item id=%s", item_id)


# ─── Cashier POS Configuration ────────────────────────────────────────────────

@router.get("/cashier-config")
def get_cashier_config(
    db: Session = Depends(get_db),
    _: User     = Depends(require_role(UserRole.admin)),
):
    """Return all menu items with their cashier visibility settings."""
    items = db.query(MenuItem).order_by(MenuItem.category, MenuItem.sort_order, MenuItem.name).all()
    return [
        {
            "id":             i.id,
            "name":           i.name,
            "category":       i.category,
            "price":          i.price,
            "is_available":   i.is_available,
            "cashier_visible":i.cashier_visible,
            "cashier_note":   i.cashier_note,
            "sort_order":     i.sort_order,
        }
        for i in items
    ]


class CashierItemPatch(BaseModel):
    cashier_visible: Optional[bool]    = None
    cashier_note:    Optional[str]     = None
    sort_order:      Optional[int]     = None
    is_available:    Optional[bool]    = None


@router.patch("/cashier-config/{item_id}")
def patch_cashier_item(
    item_id: int,
    body:    CashierItemPatch,
    db:      Session = Depends(get_db),
    _:       User    = Depends(require_role(UserRole.admin)),
):
    """Toggle visibility, note, or availability of a single item on the cashier screen."""
    item = db.query(MenuItem).filter(MenuItem.id == item_id).first()
    if not item:
        raise HTTPException(404, detail="Item not found")
    if body.cashier_visible is not None:
        item.cashier_visible = body.cashier_visible
    if body.cashier_note is not None:
        item.cashier_note = body.cashier_note or None
    if body.sort_order is not None:
        item.sort_order = body.sort_order
    if body.is_available is not None:
        item.is_available = body.is_available
    db.commit()
    return {"id": item.id, "cashier_visible": item.cashier_visible, "is_available": item.is_available}


class BulkCashierConfig(BaseModel):
    updates: List[dict]   # [{id, cashier_visible, cashier_note, is_available, sort_order}]


@router.post("/cashier-config/bulk")
def bulk_cashier_config(
    body: BulkCashierConfig,
    db:   Session = Depends(get_db),
    _:    User    = Depends(require_role(UserRole.admin)),
):
    """Bulk-update cashier visibility for multiple items at once."""
    updated = 0
    for upd in body.updates:
        item = db.query(MenuItem).filter(MenuItem.id == upd.get("id")).first()
        if not item:
            continue
        if "cashier_visible" in upd:
            item.cashier_visible = upd["cashier_visible"]
        if "cashier_note" in upd:
            item.cashier_note = upd.get("cashier_note") or None
        if "is_available" in upd:
            item.is_available = upd["is_available"]
        if "sort_order" in upd:
            item.sort_order = upd["sort_order"]
        updated += 1
    db.commit()
    return {"updated": updated}


# ─── Ingredient Management ────────────────────────────────────────────────────

@router.get("/menu/{item_id}/ingredients", response_model=List[MenuIngredientResponse])
def get_item_ingredients(
    item_id: int,
    db:      Session = Depends(get_db),
    _:       User    = Depends(require_role(UserRole.admin)),
):
    """List all customization options for a menu item (including unavailable)."""
    item = db.query(MenuItem).filter(MenuItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Menu item not found")
    return (
        db.query(MenuIngredient)
        .filter(MenuIngredient.menu_item_id == item_id)
        .order_by(MenuIngredient.sort_order, MenuIngredient.id)
        .all()
    )


@router.post("/menu/{item_id}/ingredients", response_model=MenuIngredientResponse, status_code=201)
def add_ingredient(
    item_id:  int,
    data:     MenuIngredientCreate,
    db:       Session = Depends(get_db),
    _:        User    = Depends(require_role(UserRole.admin)),
):
    """Add a customization option to a menu item."""
    item = db.query(MenuItem).filter(MenuItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Menu item not found")
    ing = MenuIngredient(menu_item_id=item_id, **data.model_dump())
    db.add(ing)
    db.commit()
    db.refresh(ing)
    logger.info("Admin added ingredient '%s' to item '%s'", ing.name, item.name)
    return ing


@router.put("/ingredients/{ing_id}", response_model=MenuIngredientResponse)
def update_ingredient(
    ing_id: int,
    data:   MenuIngredientUpdate,
    db:     Session = Depends(get_db),
    _:      User    = Depends(require_role(UserRole.admin)),
):
    """Update a customization option."""
    ing = db.query(MenuIngredient).filter(MenuIngredient.id == ing_id).first()
    if not ing:
        raise HTTPException(status_code=404, detail="Ingredient not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(ing, field, value)
    db.commit()
    db.refresh(ing)
    return ing


@router.delete("/ingredients/{ing_id}", status_code=204)
def delete_ingredient(
    ing_id: int,
    db:     Session = Depends(get_db),
    _:      User    = Depends(require_role(UserRole.admin)),
):
    """Delete a customization option."""
    ing = db.query(MenuIngredient).filter(MenuIngredient.id == ing_id).first()
    if not ing:
        raise HTTPException(status_code=404, detail="Ingredient not found")
    db.delete(ing)
    db.commit()
    logger.info("Admin deleted ingredient id=%s", ing_id)


# ─── Order Management ─────────────────────────────────────────────────────────

@router.get("/orders", response_model=List[OrderResponse])
def get_all_orders(
    status: Optional[str] = None,
    payment_method: Optional[str] = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(UserRole.admin)),
):
    """Return all orders with optional status and payment_method filters."""
    q = db.query(Order)
    if status:
        try:
            q = q.filter(Order.status == OrderStatus(status))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status '{status}'")
    if payment_method:
        try:
            q = q.filter(Order.payment_method == PaymentMethod(payment_method))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid payment_method '{payment_method}'")

    return q.order_by(Order.created_at.desc()).all()


@router.post("/orders/{order_id}/mark-cash-paid", response_model=OrderResponse)
async def mark_cash_order_paid(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin, UserRole.cashier)),
):
    """
    Cashier / admin marks a cash or bank-transfer order as paid after collecting
    the money.  Generates a receipt token and immediately pushes the order to the
    kitchen display via WebSocket — the kitchen sees it the instant payment is
    confirmed, not before.
    """
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Allow cash AND bank_transfer (both arrive as CASH_PENDING)
    if order.payment_method not in (PaymentMethod.cash, PaymentMethod.bank_transfer):
        raise HTTPException(
            status_code=400,
            detail=f"Payment method '{order.payment_method.value}' cannot be confirmed here",
        )
    if order.status != OrderStatus.CASH_PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Order status is '{order.status.value}' — expected CASH_PENDING",
        )

    receipt_code  = generate_receipt_code()
    receipt_token = generate_receipt_token(
        order.id, receipt_code, order.total_amount, order.order_number
    )
    order.status        = OrderStatus.PAID
    order.receipt_code  = receipt_code
    order.receipt_token = receipt_token

    db.add(AuditLog(
        event        = AuditEvent.CASH_MARKED_PAID,
        order_id     = order.id,
        order_number = order.order_number,
        actor_id     = current_user.id,
        actor_name   = current_user.username,
        actor_role   = current_user.role.value,
        note         = f"Payment confirmed by {current_user.username} — method={order.payment_method.value}",
    ))
    db.commit()
    db.refresh(order)
    logger.info("Order %s marked PAID (%s) by %s",
                order.order_number, order.payment_method.value, current_user.username)

    # ── Push to kitchen IMMEDIATELY — this is the trigger for kitchen to start ──
    event_payload = {
        "event":          "new_order",
        "order_number":   order.order_number,
        "customer_name":  order.customer_name,
        "total_amount":   order.total_amount,
        "payment_method": order.payment_method.value,
        "status":         OrderStatus.PAID.value,
        "source":         "cashier_confirmed",
    }
    await ws_manager.broadcast_to_room("kitchen", event_payload)
    await ws_manager.broadcast_to_room("cashier", {
        "event":        "payment_confirmed",
        "order_number": order.order_number,
        "status":       OrderStatus.PAID.value,
    })
    await ws_manager.broadcast_to_room(f"track:{order.order_number}", {
        "event":  "status_change",
        "status": OrderStatus.PAID.value,
    })

    return order


@router.post("/orders/{order_id}/cancel", response_model=OrderResponse)
def cancel_order(
    order_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(UserRole.admin)),
):
    """Admin can cancel an order that has not yet been completed."""
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status in (OrderStatus.COMPLETED, OrderStatus.PICKED_UP):
        raise HTTPException(status_code=400, detail="Cannot cancel an order that has already been picked up.")

    order.status = OrderStatus.CANCELLED
    db.commit()
    db.refresh(order)
    return order


# ─── Analytics ────────────────────────────────────────────────────────────────

@router.get("/analytics", response_model=AnalyticsResponse)
def get_analytics(
    db: Session = Depends(get_db),
    _: User = Depends(require_role(UserRole.admin)),
):
    """Aggregate statistics for the admin dashboard."""
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    def _count(status):
        return db.query(func.count(Order.id)).filter(Order.status == status).scalar() or 0

    total_revenue = (
        db.query(func.sum(Order.total_amount))
        .filter(Order.status.in_(_PAID_STATUSES))
        .scalar()
        or 0.0
    )
    today_revenue = (
        db.query(func.sum(Order.total_amount))
        .filter(Order.status.in_(_PAID_STATUSES), Order.created_at >= today_start)
        .scalar()
        or 0.0
    )
    today_orders = (
        db.query(func.count(Order.id))
        .filter(Order.created_at >= today_start)
        .scalar()
        or 0
    )

    return AnalyticsResponse(
        total_orders        = db.query(func.count(Order.id)).scalar() or 0,
        total_revenue       = float(total_revenue),
        pending_orders      = _count(OrderStatus.PENDING),
        cash_pending_orders = _count(OrderStatus.CASH_PENDING),
        paid_orders         = _count(OrderStatus.PAID),
        preparing_orders    = _count(OrderStatus.PREPARING),
        ready_orders        = _count(OrderStatus.READY),
        picked_up_orders    = _count(OrderStatus.PICKED_UP),
        completed_orders    = _count(OrderStatus.COMPLETED),
        today_orders        = today_orders,
        today_revenue       = float(today_revenue),
    )


# ─── Pickup Reset (admin undo) ────────────────────────────────────────────────

@router.post("/orders/{order_id}/reset-pickup", response_model=OrderResponse)
def reset_pickup(
    order_id: int,
    payload: ResetPickupRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.admin)),
):
    """
    Reset a PICKED_UP order back to READY.
    Only admins can do this. Every reset is permanently logged.
    Use when: cashier gave wrong order, accidental scan, customer dispute.
    """
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(404, "Order not found.")
    if order.status != OrderStatus.PICKED_UP:
        raise HTTPException(400, f"Order is not PICKED_UP (current: {order.status.value}).")

    # Snapshot the previous state for the audit note
    prev_picked_up_by = order.picked_up_by_id
    prev_picked_up_at = order.picked_up_at

    order.status          = OrderStatus.READY
    order.redeemed        = False
    order.redeemed_at     = None
    order.picked_up_at    = None
    order.picked_up_by_id = None
    order.pickup_location = None
    # Intentionally do NOT reset redemption_count — it is evidence in the audit trail

    db.add(AuditLog(
        event        = AuditEvent.REDEEM_RESET,
        order_id     = order.id,
        order_number = order.order_number,
        actor_id     = current_user.id,
        actor_name   = current_user.username,
        actor_role   = current_user.role.value,
        ip_address   = request.client.host,
        terminal_id  = "admin-panel",
        note         = (
            f"Reset by admin. Reason: {payload.reason}. "
            f"Was picked up at {prev_picked_up_at} by user_id={prev_picked_up_by}."
        ),
    ))

    db.commit()
    db.refresh(order)

    logger.info(
        "Order %s pickup RESET by admin '%s'. Reason: %s",
        order.order_number, current_user.username, payload.reason,
    )
    return order


# ─── Audit & Security ─────────────────────────────────────────────────────────

@router.get("/audit", response_model=List[AuditLogResponse])
def get_all_audit_logs(
    order_id: int | None = None,
    event: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(UserRole.admin)),
):
    """Full audit log. Filter by order_id or event type."""
    q = db.query(AuditLog)
    if order_id:
        q = q.filter(AuditLog.order_id == order_id)
    if event:
        try:
            q = q.filter(AuditLog.event == AuditEvent(event))
        except ValueError:
            raise HTTPException(400, f"Invalid event type '{event}'")
    return q.order_by(AuditLog.created_at.desc()).limit(min(limit, 500)).all()


@router.get("/audit/orders/{order_id}", response_model=List[AuditLogResponse])
def get_order_audit_trail(
    order_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(UserRole.admin)),
):
    """Full audit trail for a specific order."""
    return (
        db.query(AuditLog)
        .filter(AuditLog.order_id == order_id)
        .order_by(AuditLog.created_at.asc())
        .all()
    )


@router.get("/suspicious", response_model=List[SuspiciousActivityResponse])
def get_suspicious_activity(
    resolved: bool = False,
    limit: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(UserRole.admin)),
):
    """Return unresolved suspicious activity flags for admin review."""
    return (
        db.query(SuspiciousActivity)
        .filter(SuspiciousActivity.resolved == resolved)
        .order_by(SuspiciousActivity.created_at.desc())
        .limit(min(limit, 200))
        .all()
    )


@router.post("/suspicious/{flag_id}/resolve")
def resolve_suspicious_flag(
    flag_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(UserRole.admin)),
):
    """Mark a suspicious activity flag as reviewed/resolved."""
    flag = db.query(SuspiciousActivity).filter(SuspiciousActivity.id == flag_id).first()
    if not flag:
        raise HTTPException(404, "Flag not found.")
    flag.resolved = True
    db.commit()
    return {"resolved": True, "id": flag_id}


# ── Reviews Management ────────────────────────────────────────────────────────

@router.get("/reviews")
def get_all_reviews(
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(UserRole.admin)),
):
    """Admin: get all reviews (approved and unapproved), including replies."""
    reviews = (
        db.query(Review)
        .order_by(Review.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        {
            "id":            r.id,
            "reviewer_name": r.reviewer_name,
            "rating":        r.rating,
            "comment":       r.comment,
            "order_number":  r.order_number,
            "is_approved":   r.is_approved,
            "admin_reply":   r.admin_reply,
            "replied_at":    r.replied_at.isoformat() if r.replied_at else None,
            "created_at":    r.created_at.isoformat() if r.created_at else "",
        }
        for r in reviews
    ]


@router.patch("/reviews/{review_id}/approval")
def set_review_approval(
    review_id: int,
    body: dict,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(UserRole.admin)),
):
    """Admin: approve or hide a review."""
    review = db.query(Review).filter(Review.id == review_id).first()
    if not review:
        raise HTTPException(404, "Review not found.")
    review.is_approved = bool(body.get("is_approved", True))
    db.commit()
    return {"id": review_id, "is_approved": review.is_approved}


@router.patch("/reviews/{review_id}/reply")
def reply_to_review(
    review_id: int,
    body: dict,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(UserRole.admin)),
):
    """Admin: post or update a reply to a customer review."""
    from datetime import datetime, timezone
    review = db.query(Review).filter(Review.id == review_id).first()
    if not review:
        raise HTTPException(404, "Review not found.")
    reply = (body.get("reply") or "").strip()
    review.admin_reply = reply if reply else None
    review.replied_at  = datetime.now(timezone.utc) if reply else None
    db.commit()
    logger.info("Admin %s reply on review id=%d", "set" if reply else "cleared", review_id)
    return {
        "id":          review_id,
        "admin_reply": review.admin_reply,
        "replied_at":  review.replied_at.isoformat() if review.replied_at else None,
    }


@router.delete("/reviews/{review_id}", status_code=204)
def delete_review(
    review_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(UserRole.admin)),
):
    """Admin: permanently delete a review."""
    review = db.query(Review).filter(Review.id == review_id).first()
    if not review:
        raise HTTPException(404, "Review not found.")
    db.delete(review)
    db.commit()
    logger.info("Admin deleted review id=%d", review_id)


# ── Loyalty Admin ─────────────────────────────────────────────────────────────

@router.get("/loyalty/accounts")
def get_loyalty_accounts(
    limit: int = 200,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(UserRole.admin)),
):
    """Admin: list all loyalty accounts sorted by balance desc."""
    accounts = (
        db.query(LoyaltyAccount)
        .order_by(LoyaltyAccount.points_balance.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id":             a.id,
            "phone":          a.phone,
            "display_name":   a.display_name,
            "points_balance": a.points_balance,
            "total_earned":   a.total_earned,
            "total_redeemed": a.total_redeemed,
            "created_at":     a.created_at.isoformat() if a.created_at else "",
        }
        for a in accounts
    ]


# ── Reward Catalog Management ─────────────────────────────────────────────────

class RewardItemCreate(BaseModel):
    name:            str           = Field(..., min_length=1, max_length=200)
    description:     Optional[str] = Field(None, max_length=1000)
    points_required: int           = Field(..., ge=1)
    image_url:       Optional[str] = Field(None, max_length=500)
    is_active:       bool          = True
    quantity_limit:  Optional[int] = Field(None, ge=1)
    sort_order:      int           = 0
    valid_until:     Optional[str] = None   # ISO date string or None


@router.get("/rewards")
def get_rewards(
    db: Session = Depends(get_db),
    _: User = Depends(require_role(UserRole.admin)),
):
    """Admin: list all reward items (active and inactive)."""
    from models import RewardItem
    rewards = db.query(RewardItem).order_by(RewardItem.sort_order.asc(), RewardItem.id.asc()).all()
    return [
        {
            "id":               r.id,
            "name":             r.name,
            "description":      r.description,
            "points_required":  r.points_required,
            "image_url":        r.image_url,
            "is_active":        r.is_active,
            "quantity_limit":   r.quantity_limit,
            "quantity_claimed": r.quantity_claimed,
            "sort_order":       r.sort_order,
            "valid_until":      r.valid_until.isoformat() if r.valid_until else None,
            "created_at":       r.created_at.isoformat() if r.created_at else "",
        }
        for r in rewards
    ]


@router.post("/rewards", status_code=201)
def create_reward(
    body: RewardItemCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(UserRole.admin)),
):
    """Admin: create a new reward catalog item."""
    from models import RewardItem
    valid_until = None
    if body.valid_until:
        try:
            from datetime import datetime
            valid_until = datetime.fromisoformat(body.valid_until.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, "Invalid valid_until date format.")
    item = RewardItem(
        name            = body.name.strip(),
        description     = body.description,
        points_required = body.points_required,
        image_url       = body.image_url,
        is_active       = body.is_active,
        quantity_limit  = body.quantity_limit,
        sort_order      = body.sort_order,
        valid_until     = valid_until,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    logger.info("Admin created reward id=%d name=%s", item.id, item.name)
    return {"id": item.id, "name": item.name}


@router.put("/rewards/{reward_id}")
def update_reward(
    reward_id: int,
    body: RewardItemCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(UserRole.admin)),
):
    """Admin: update a reward item."""
    from models import RewardItem
    item = db.query(RewardItem).filter(RewardItem.id == reward_id).first()
    if not item:
        raise HTTPException(404, "Reward not found.")
    valid_until = None
    if body.valid_until:
        try:
            from datetime import datetime
            valid_until = datetime.fromisoformat(body.valid_until.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, "Invalid valid_until date format.")
    item.name            = body.name.strip()
    item.description     = body.description
    item.points_required = body.points_required
    item.image_url       = body.image_url
    item.is_active       = body.is_active
    item.quantity_limit  = body.quantity_limit
    item.sort_order      = body.sort_order
    item.valid_until     = valid_until
    db.commit()
    logger.info("Admin updated reward id=%d", reward_id)
    return {"id": reward_id, "name": item.name}


@router.delete("/rewards/{reward_id}", status_code=204)
def delete_reward(
    reward_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(UserRole.admin)),
):
    """Admin: delete a reward item. Cannot delete if claims exist."""
    from models import RewardItem, RewardClaim
    item = db.query(RewardItem).filter(RewardItem.id == reward_id).first()
    if not item:
        raise HTTPException(404, "Reward not found.")
    # Soft-check for claims
    claims = db.query(RewardClaim).filter(RewardClaim.reward_id == reward_id).count()
    if claims > 0:
        # Deactivate instead of deleting to preserve claim history
        item.is_active = False
        db.commit()
        return
    db.delete(item)
    db.commit()
    logger.info("Admin deleted reward id=%d", reward_id)


@router.get("/rewards/claims")
def get_reward_claims(
    fulfilled: Optional[bool] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(UserRole.admin)),
):
    """Admin: list reward claims. Filter by fulfilled status."""
    from models import RewardClaim
    q = db.query(RewardClaim).order_by(RewardClaim.created_at.desc())
    if fulfilled is not None:
        q = q.filter(RewardClaim.is_fulfilled == fulfilled)
    claims = q.limit(limit).all()
    return [
        {
            "id":           c.id,
            "reward_name":  c.reward_name,
            "points_spent": c.points_spent,
            "claim_code":   c.claim_code,
            "identifier":   c.identifier,
            "is_fulfilled": c.is_fulfilled,
            "fulfilled_at": c.fulfilled_at.isoformat() if c.fulfilled_at else None,
            "created_at":   c.created_at.isoformat() if c.created_at else "",
        }
        for c in claims
    ]


@router.patch("/rewards/claims/{claim_id}/fulfill")
def fulfill_claim(
    claim_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(UserRole.admin)),
):
    """Admin: mark a reward claim as fulfilled."""
    from models import RewardClaim
    from datetime import datetime, timezone
    claim = db.query(RewardClaim).filter(RewardClaim.id == claim_id).first()
    if not claim:
        raise HTTPException(404, "Claim not found.")
    claim.is_fulfilled = True
    claim.fulfilled_at = datetime.now(timezone.utc)
    db.commit()
    return {"id": claim_id, "is_fulfilled": True}
