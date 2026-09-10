"""
Customer Reviews
================
Public endpoints for submitting and reading reviews.
Admin can approve/hide/reply to reviews via admin_routes.
"""
from __future__ import annotations
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session
from typing import Optional, List

from database import get_db
from models import Review

logger  = logging.getLogger(__name__)
router  = APIRouter(prefix="/api/reviews", tags=["Reviews"])
limiter = Limiter(key_func=get_remote_address)


# ── Schemas ───────────────────────────────────────────────────────────────────
class ReviewCreate(BaseModel):
    reviewer_name: str  = Field(..., min_length=1, max_length=100)
    rating:        int  = Field(..., ge=1, le=5)
    comment:       Optional[str] = Field(None, max_length=1000)
    order_number:  Optional[str] = Field(None, max_length=20)


class ReviewResponse(BaseModel):
    """Returned in list endpoints — no edit_token exposed."""
    id:            int
    reviewer_name: str
    rating:        int
    comment:       Optional[str]
    order_number:  Optional[str]
    admin_reply:   Optional[str]
    replied_at:    Optional[str]
    created_at:    str

    class Config:
        from_attributes = True


class ReviewCreateResponse(ReviewResponse):
    """Returned only on POST — includes edit_token for the browser to store."""
    edit_token: str


# ── Helpers ───────────────────────────────────────────────────────────────────
def _to_response(r: Review) -> ReviewResponse:
    return ReviewResponse(
        id            = r.id,
        reviewer_name = r.reviewer_name,
        rating        = r.rating,
        comment       = r.comment,
        order_number  = r.order_number,
        admin_reply   = r.admin_reply,
        replied_at    = r.replied_at.isoformat() if r.replied_at else None,
        created_at    = r.created_at.isoformat() if r.created_at else "",
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=List[ReviewResponse])
def list_reviews(
    limit:  int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """Return approved reviews, newest first."""
    reviews = (
        db.query(Review)
        .filter(Review.is_approved == True)
        .order_by(Review.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_to_response(r) for r in reviews]


@router.get("/summary")
def review_summary(db: Session = Depends(get_db)):
    """Return average rating and total count for the hero badge."""
    reviews = db.query(Review).filter(Review.is_approved == True).all()
    if not reviews:
        return {"average": 0.0, "count": 0}
    avg = sum(r.rating for r in reviews) / len(reviews)
    return {"average": round(avg, 1), "count": len(reviews)}


@router.post("", response_model=ReviewCreateResponse, status_code=201)
@limiter.limit("5/minute")
def create_review(
    request: Request,
    body: ReviewCreate,
    db: Session = Depends(get_db),
):
    """
    Submit a new customer review.
    Returns an edit_token the browser stores in localStorage so the customer
    can edit their own review later without needing an account.
    """
    token  = secrets.token_hex(24)   # 48-char hex, never shown in UI
    review = Review(
        reviewer_name = body.reviewer_name.strip(),
        rating        = body.rating,
        comment       = body.comment.strip() if body.comment else None,
        order_number  = body.order_number.strip().upper() if body.order_number else None,
        is_approved   = True,
        edit_token    = token,
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    logger.info("New review id=%d rating=%d from %s", review.id, review.rating, review.reviewer_name)
    return ReviewCreateResponse(
        **_to_response(review).model_dump(),
        edit_token = token,
    )


@router.put("/{review_id}", response_model=ReviewResponse)
@limiter.limit("10/minute")
def update_review(
    request:   Request,
    review_id: int,
    token:     str,
    body:      ReviewCreate,
    db: Session = Depends(get_db),
):
    """
    Customer edits their own review using the edit_token returned at creation.
    Editing resets approval to True (re-publishes it).
    """
    review = db.query(Review).filter(
        Review.id         == review_id,
        Review.edit_token == token,
    ).first()
    if not review:
        raise HTTPException(status_code=404, detail="Review not found or invalid edit token.")

    review.reviewer_name = body.reviewer_name.strip()
    review.rating        = body.rating
    review.comment       = body.comment.strip() if body.comment else None
    review.order_number  = body.order_number.strip().upper() if body.order_number else None
    review.is_approved   = True   # re-approve after edit
    db.commit()
    db.refresh(review)
    logger.info("Review id=%d updated by customer", review.id)
    return _to_response(review)
