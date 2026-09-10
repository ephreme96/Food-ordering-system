from __future__ import annotations
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from auth import require_role
from database import get_db
from models import Order, OrderStatus, User, UserRole
from schemas import OrderResponse, OrderStatusUpdate
from sms import order_ready_msg, send_sms
from ws_manager import ws_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/kitchen", tags=["Kitchen"])

# Defines the only valid state transitions kitchen staff can make
_VALID_TRANSITIONS: dict[OrderStatus, list[OrderStatus]] = {
    OrderStatus.PAID:      [OrderStatus.PREPARING],
    OrderStatus.PREPARING: [OrderStatus.READY],
    OrderStatus.READY:     [OrderStatus.COMPLETED],
}

# Statuses visible to the kitchen (exclude cash-pending / raw pending)
_KITCHEN_VISIBLE = [
    OrderStatus.PAID,
    OrderStatus.PREPARING,
    OrderStatus.READY,
    OrderStatus.COMPLETED,
]


@router.get("/orders", response_model=List[OrderResponse])
def get_kitchen_orders(
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(UserRole.kitchen, UserRole.admin)),
):
    """
    Return active orders for the kitchen display.
    Pass ?status=PAID|PREPARING|READY|COMPLETED to filter.
    """
    q = db.query(Order)

    if status:
        try:
            q = q.filter(Order.status == OrderStatus(status))
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{status}'. Valid: {[s.value for s in _KITCHEN_VISIBLE]}",
            )
    else:
        q = q.filter(Order.status.in_(_KITCHEN_VISIBLE))

    return q.order_by(Order.created_at.desc()).all()


@router.patch("/orders/{order_id}/status", response_model=OrderResponse)
async def update_order_status(
    order_id: int,
    status_update: OrderStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.kitchen, UserRole.admin)),
):
    """
    Advance an order through the kitchen pipeline:
      PAID → PREPARING → READY → COMPLETED
    Only forward transitions are allowed.
    """
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    allowed = _VALID_TRANSITIONS.get(order.status, [])
    if status_update.status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot change status from '{order.status.value}' to "
                f"'{status_update.status.value}'. "
                f"Allowed next state(s): {[s.value for s in allowed] or 'none'}"
            ),
        )

    old_status   = order.status
    order.status = status_update.status
    db.commit()
    db.refresh(order)
    logger.info(
        "Order %s updated %s → %s by user '%s'",
        order.order_number, old_status.value, order.status.value, current_user.username,
    )

    # Real-time: push status change to all connected clients
    payload = {
        "event":        "status_change",
        "order_number": order.order_number,
        "old_status":   old_status.value,
        "new_status":   order.status.value,
    }
    await ws_manager.broadcast_to_room("kitchen",  payload)
    await ws_manager.broadcast_to_room("cashier",  payload)
    await ws_manager.broadcast_to_room(f"track:{order.order_number}", payload)

    # SMS when order is marked READY
    if order.status == OrderStatus.READY and order.customer_phone and order.receipt_code:
        try:
            await send_sms(order.customer_phone, order_ready_msg(order.order_number, order.receipt_code))
        except Exception:
            pass

    return order


@router.get("/orders/stats")
def kitchen_stats(
    db: Session = Depends(get_db),
    _: User = Depends(require_role(UserRole.kitchen, UserRole.admin)),
):
    """Quick count per status for the kitchen header badges."""
    result = {}
    for s in _KITCHEN_VISIBLE:
        result[s.value] = db.query(Order).filter(Order.status == s).count()
    return result
