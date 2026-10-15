"""
Loyalty Points System
=====================
Rules:
  • Earn  : 1 point per 10 ETB spent (floor)
  • Redeem: via admin-managed reward catalog (points_required per reward)
  • Keyed by customer phone number OR email (no login required)
"""
from __future__ import annotations
import logging
import secrets
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from database import get_db
from models import LoyaltyAccount, PointTransaction, RewardItem, RewardClaim

logger  = logging.getLogger(__name__)
router  = APIRouter(prefix="/api/loyalty", tags=["Loyalty"])
limiter = Limiter(key_func=get_remote_address)

# ── Constants ─────────────────────────────────────────────────────────────────
ETB_PER_POINT = 10        # 10 ETB = 1 point (points are awarded in order_paid.py)
MAX_PENDING_CLAIMS = 3    # uncollected rewards allowed per customer


# ── Schemas ───────────────────────────────────────────────────────────────────
class JoinRequest(BaseModel):
    identifier:   str           = Field(..., min_length=3, max_length=255,
                                         description="Phone number or email address")
    display_name: Optional[str] = Field(None, max_length=100)


class BalanceResponse(BaseModel):
    account_id:     int
    identifier:     str           # the phone or email used to look up
    display_name:   Optional[str]
    points_balance: int
    total_earned:   int
    total_redeemed: int
    next_reward_pts: int          # points needed for cheapest reachable reward


class RewardItemResponse(BaseModel):
    id:              int
    name:            str
    description:     Optional[str]
    points_required: int
    image_url:       Optional[str]
    quantity_limit:  Optional[int]
    quantity_claimed: int
    valid_until:     Optional[str]
    can_claim:       bool          # True if viewer has enough points


class ClaimResponse(BaseModel):
    claim_code:  str
    reward_name: str
    points_spent: int
    new_balance: int


# ── Helpers ───────────────────────────────────────────────────────────────────
def _is_email(s: str) -> bool:
    return "@" in s


def _normalize(s: str) -> str:
    s = s.strip()
    if _is_email(s):
        return s.lower()
    return s.replace(" ", "").replace("-", "")


def _get_or_create_account(identifier: str, name: Optional[str], db: Session) -> LoyaltyAccount:
    norm = _normalize(identifier)
    if _is_email(norm):
        acc = db.query(LoyaltyAccount).filter(LoyaltyAccount.email == norm).first()
        if not acc:
            acc = LoyaltyAccount(email=norm, display_name=name,
                                 points_balance=0, total_earned=0, total_redeemed=0)
            db.add(acc)
            db.flush()
        elif name and not acc.display_name:
            acc.display_name = name
    else:
        acc = db.query(LoyaltyAccount).filter(LoyaltyAccount.phone == norm).first()
        if not acc:
            acc = LoyaltyAccount(phone=norm, display_name=name,
                                 points_balance=0, total_earned=0, total_redeemed=0)
            db.add(acc)
            db.flush()
        elif name and not acc.display_name:
            acc.display_name = name
    return acc


def _next_reward_pts(balance: int, db: Session) -> int:
    """Points needed to reach the cheapest active reward above current balance."""
    cheapest = (
        db.query(RewardItem)
        .filter(RewardItem.is_active == True, RewardItem.points_required > balance)
        .order_by(RewardItem.points_required.asc())
        .first()
    )
    if cheapest:
        return cheapest.points_required - balance
    return 0   # 0 means already enough for all rewards (or no rewards defined)


def _make_claim_code() -> str:
    return "RWD-" + secrets.token_hex(3).upper()


def _balance_response(acc: LoyaltyAccount, db: Session) -> BalanceResponse:
    ident = acc.email or acc.phone or "?"
    return BalanceResponse(
        account_id      = acc.id,
        identifier      = ident,
        display_name    = acc.display_name,
        points_balance  = acc.points_balance,
        total_earned    = acc.total_earned,
        total_redeemed  = acc.total_redeemed,
        next_reward_pts = _next_reward_pts(acc.points_balance, db),
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/join", response_model=BalanceResponse)
@limiter.limit("20/minute")
def join_rewards(request: Request, body: JoinRequest, db: Session = Depends(get_db)):
    """
    Sign in to (or create) a loyalty account using phone or email.
    Returns current balance and membership info.
    """
    acc = _get_or_create_account(body.identifier, body.display_name, db)
    db.commit()
    db.refresh(acc)
    return _balance_response(acc, db)


@router.get("/balance", response_model=BalanceResponse)
@limiter.limit("30/minute")
def get_balance(
    request:    Request,
    identifier: str = Query(..., min_length=3, max_length=255,
                             description="Phone number or email address"),
    db: Session = Depends(get_db),
):
    """Look up a loyalty account by phone or email. Returns zero-balance placeholder if not found."""
    norm = _normalize(identifier)
    if _is_email(norm):
        acc = db.query(LoyaltyAccount).filter(LoyaltyAccount.email == norm).first()
    else:
        acc = db.query(LoyaltyAccount).filter(LoyaltyAccount.phone == norm).first()

    if not acc:
        return BalanceResponse(
            account_id      = 0,
            identifier      = norm,
            display_name    = None,
            points_balance  = 0,
            total_earned    = 0,
            total_redeemed  = 0,
            next_reward_pts = 0,
        )
    return _balance_response(acc, db)


@router.get("/rewards", response_model=List[RewardItemResponse])
def list_rewards(
    identifier: Optional[str] = Query(None, description="Phone or email — used to show can_claim"),
    db: Session = Depends(get_db),
):
    """Return active rewards from the catalog. Optionally pass identifier to compute can_claim."""
    now = datetime.now(timezone.utc)
    rewards = (
        db.query(RewardItem)
        .filter(
            RewardItem.is_active == True,
            (RewardItem.valid_until == None) | (RewardItem.valid_until >= now),
        )
        .order_by(RewardItem.sort_order.asc(), RewardItem.points_required.asc())
        .all()
    )
    balance = 0
    if identifier:
        norm = _normalize(identifier)
        if _is_email(norm):
            acc = db.query(LoyaltyAccount).filter(LoyaltyAccount.email == norm).first()
        else:
            acc = db.query(LoyaltyAccount).filter(LoyaltyAccount.phone == norm).first()
        if acc:
            balance = acc.points_balance

    return [
        RewardItemResponse(
            id               = r.id,
            name             = r.name,
            description      = r.description,
            points_required  = r.points_required,
            image_url        = r.image_url,
            quantity_limit   = r.quantity_limit,
            quantity_claimed = r.quantity_claimed,
            valid_until      = r.valid_until.isoformat() if r.valid_until else None,
            can_claim        = (
                balance >= r.points_required and
                (r.quantity_limit is None or r.quantity_claimed < r.quantity_limit)
            ),
        )
        for r in rewards
    ]


@router.post("/rewards/{reward_id}/claim", response_model=ClaimResponse)
@limiter.limit("10/minute")
def claim_reward(
    request:   Request,
    reward_id: int,
    body:      JoinRequest,
    db: Session = Depends(get_db),
):
    """Claim a reward from the catalog. Deducts points and returns a claim code."""
    now = datetime.now(timezone.utc)
    reward = db.query(RewardItem).filter(
        RewardItem.id       == reward_id,
        RewardItem.is_active == True,
    ).first()
    if not reward:
        raise HTTPException(404, "Reward not found or no longer active.")
    if reward.valid_until and reward.valid_until < now:
        raise HTTPException(400, "This reward has expired.")
    if reward.quantity_limit is not None and reward.quantity_claimed >= reward.quantity_limit:
        raise HTTPException(400, "This reward is no longer available (sold out).")

    acc = _get_or_create_account(body.identifier, body.display_name, db)
    db.flush()

    # Security: anyone can type a phone number, so a claim made here only
    # RESERVES the points. They are deducted when staff hand the reward over
    # at the counter (admin "fulfill"), after checking the customer in person.
    pending = db.query(RewardClaim).filter(
        RewardClaim.account_id   == acc.id,
        RewardClaim.is_fulfilled == False,
    ).all()
    if len(pending) >= MAX_PENDING_CLAIMS:
        raise HTTPException(400, "You already have rewards waiting to be collected. Collect them at the counter first.")
    available = acc.points_balance - sum(c.points_spent for c in pending)
    if available < reward.points_required:
        raise HTTPException(400, f"Not enough points. You need {reward.points_required} pts but have {max(available, 0)} available.")

    reward.quantity_claimed += 1

    # Generate unique claim code (retry on collision)
    for _ in range(5):
        code = _make_claim_code()
        if not db.query(RewardClaim).filter(RewardClaim.claim_code == code).first():
            break

    claim = RewardClaim(
        account_id   = acc.id,
        reward_id    = reward.id,
        reward_name  = reward.name,
        points_spent = reward.points_required,
        claim_code   = code,
        identifier   = _normalize(body.identifier),
        is_fulfilled = False,
    )
    db.add(claim)

    db.commit()
    db.refresh(acc)

    logger.info("Reward claimed: %s code=%s pts=%d", reward.name, code, reward.points_required)
    return ClaimResponse(
        claim_code   = code,
        reward_name  = reward.name,
        points_spent = reward.points_required,
        new_balance  = available - reward.points_required,
    )


@router.get("/history")
@limiter.limit("10/minute")
def get_history(
    request:    Request,
    identifier: str = Query(..., min_length=3, max_length=255),
    db: Session = Depends(get_db),
):
    """Return the last 20 point transactions for a phone number or email."""
    norm = _normalize(identifier)
    if _is_email(norm):
        acc = db.query(LoyaltyAccount).filter(LoyaltyAccount.email == norm).first()
    else:
        acc = db.query(LoyaltyAccount).filter(LoyaltyAccount.phone == norm).first()
    if not acc:
        return []
    txns = (
        db.query(PointTransaction)
        .filter(PointTransaction.account_id == acc.id)
        .order_by(PointTransaction.created_at.desc())
        .limit(20)
        .all()
    )
    return [
        {
            "type":        t.txn_type,
            "points":      t.points,
            "description": t.description,
            "order_number": t.order_number,
            "created_at":  t.created_at.isoformat() if t.created_at else None,
        }
        for t in txns
    ]
