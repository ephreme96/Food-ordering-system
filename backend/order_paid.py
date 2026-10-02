"""
Side effects that must happen exactly when an order becomes PAID.

Loyalty points and promo-code usage are both "money-like", so they are only
ever granted here, on the server, from the order's real total. Nothing the
browser sends can create points or burn a promo code.

Call on_order_paid(order, db) right after setting order.status = PAID and
before db.commit(). It is safe to call more than once for the same order.
"""
from __future__ import annotations

import logging
import math

from sqlalchemy.orm import Session

from models import LoyaltyAccount, Order, PointTransaction, PromoCode

logger = logging.getLogger(__name__)

ETB_PER_POINT = 10  # 10 ETB = 1 point


def _normalize(identifier: str) -> str:
    s = identifier.strip()
    if "@" in s:
        return s.lower()
    return s.replace(" ", "").replace("-", "")


def _get_or_create_account(identifier: str, name: str | None, db: Session) -> LoyaltyAccount:
    norm = _normalize(identifier)
    column = LoyaltyAccount.email if "@" in norm else LoyaltyAccount.phone
    acc = db.query(LoyaltyAccount).filter(column == norm).first()
    if not acc:
        fields = {"email": norm} if "@" in norm else {"phone": norm}
        acc = LoyaltyAccount(display_name=name, points_balance=0,
                             total_earned=0, total_redeemed=0, **fields)
        db.add(acc)
        db.flush()
    elif name and not acc.display_name:
        acc.display_name = name
    return acc


def _already_processed(order: Order, db: Session) -> bool:
    return db.query(PointTransaction).filter(
        PointTransaction.order_id == order.id,
        PointTransaction.txn_type == "earn",
    ).first() is not None


def on_order_paid(order: Order, db: Session) -> None:
    """Count the promo code use and award loyalty points for a PAID order."""
    if _already_processed(order, db):
        return

    if order.promo_code:
        promo = db.query(PromoCode).filter(PromoCode.code == order.promo_code).first()
        if promo:
            promo.uses_count += 1

    identifier = order.customer_phone or order.customer_email
    if not identifier:
        return

    pts = math.floor((order.total_amount or 0) / ETB_PER_POINT)
    acc = _get_or_create_account(identifier, order.customer_name, db)
    if pts > 0:
        acc.points_balance += pts
        acc.total_earned   += pts
    # A row is written even for 0 points, so a second call is a no-op.
    db.add(PointTransaction(
        account_id   = acc.id,
        order_id     = order.id,
        order_number = order.order_number,
        txn_type     = "earn",
        points       = pts,
        description  = f"Order {order.order_number} — ETB {order.total_amount:.2f}",
    ))
    logger.info("Loyalty: +%d pts for order %s", pts, order.order_number)
