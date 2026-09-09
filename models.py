import enum
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Float, Boolean,
    DateTime, Text, JSON, Enum as SQLEnum, ForeignKey, BigInteger
)
from sqlalchemy.sql import func
from database import Base


# ─── Enumerations ─────────────────────────────────────────────────────────────

class UserRole(str, enum.Enum):
    customer = "customer"
    kitchen  = "kitchen"
    admin    = "admin"
    cashier  = "cashier"


class OrderStatus(str, enum.Enum):
    PENDING      = "PENDING"        # Online payment initiated, not yet paid
    CASH_PENDING = "CASH_PENDING"   # Cash order, awaiting cashier confirmation
    PAID         = "PAID"           # Payment verified server-side
    PREPARING    = "PREPARING"      # Kitchen is preparing
    READY        = "READY"          # Ready for pickup
    PICKED_UP    = "PICKED_UP"      # Cashier has handed over the order — receipt burned
    COMPLETED    = "COMPLETED"      # Legacy alias for PICKED_UP (kept for compatibility)
    CANCELLED    = "CANCELLED"
    FAILED       = "FAILED"         # Payment failed or amount mismatch


class PaymentMethod(str, enum.Enum):
    online        = "online"
    cash          = "cash"
    telebirr      = "telebirr"
    cbebirr       = "cbebirr"
    bank_transfer = "bank_transfer"


class PaymentStatus(str, enum.Enum):
    pending    = "pending"
    succeeded  = "succeeded"
    failed     = "failed"
    refunded   = "refunded"


class DiscountType(str, enum.Enum):
    percent = "percent"
    fixed   = "fixed"


class AuditEvent(str, enum.Enum):
    # Redemption events
    REDEEM_SUCCESS      = "REDEEM_SUCCESS"
    REDEEM_ALREADY_USED = "REDEEM_ALREADY_USED"
    REDEEM_INVALID      = "REDEEM_INVALID"
    REDEEM_NOT_READY    = "REDEEM_NOT_READY"
    REDEEM_NOT_FOUND    = "REDEEM_NOT_FOUND"
    REDEEM_RESET        = "REDEEM_RESET"
    # Order lifecycle
    ORDER_CREATED       = "ORDER_CREATED"
    CASH_MARKED_PAID    = "CASH_MARKED_PAID"
    ORDER_CANCELLED     = "ORDER_CANCELLED"
    STATUS_CHANGED      = "STATUS_CHANGED"
    # Payment events
    PAYMENT_INITIATED   = "PAYMENT_INITIATED"
    PAYMENT_CONFIRMED   = "PAYMENT_CONFIRMED"
    PAYMENT_FAILED      = "PAYMENT_FAILED"
    REFUND_ISSUED       = "REFUND_ISSUED"
    PROMO_APPLIED       = "PROMO_APPLIED"
    BANK_TRANSFER_CLAIMED = "BANK_TRANSFER_CLAIMED"
    # Security
    RATE_LIMITED        = "RATE_LIMITED"
    SUSPICIOUS_SCAN     = "SUSPICIOUS_SCAN"


# ─── Models ───────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True, index=True)
    username      = Column(String(50),  unique=True, nullable=False, index=True)
    email         = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role          = Column(SQLEnum(UserRole), nullable=False, default=UserRole.customer)
    is_active     = Column(Boolean, default=True, nullable=False)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    updated_at    = Column(DateTime(timezone=True), onupdate=func.now())


class MenuItem(Base):
    __tablename__ = "menu_items"

    id               = Column(Integer, primary_key=True, index=True)
    name             = Column(String(255), nullable=False)
    description      = Column(Text)
    price            = Column(Float, nullable=False)
    category         = Column(String(100), nullable=False, index=True)
    image_url        = Column(String(500))
    is_available     = Column(Boolean, default=True,  nullable=False, index=True)
    cashier_visible  = Column(Boolean, default=True,  nullable=False, index=True)
    cashier_note     = Column(String(255), nullable=True)   # e.g. "sold out today"
    sort_order       = Column(Integer,  default=0,    nullable=False)
    created_at       = Column(DateTime(timezone=True), server_default=func.now())
    updated_at       = Column(DateTime(timezone=True), onupdate=func.now())


class Order(Base):
    __tablename__ = "orders"

    id                 = Column(Integer, primary_key=True, index=True)
    order_number       = Column(String(20),  unique=True, nullable=False, index=True)
    customer_name      = Column(String(255), nullable=False)
    customer_phone     = Column(String(20))
    customer_email     = Column(String(255))
    # Snapshot of cart items at order time (list of dicts with id/name/price/qty/subtotal)
    items_snapshot     = Column(JSON, nullable=False)
    total_amount       = Column(Float, nullable=False)
    status             = Column(SQLEnum(OrderStatus), nullable=False, default=OrderStatus.PENDING, index=True)
    payment_method     = Column(SQLEnum(PaymentMethod), nullable=False)
    # Chapa transaction reference – unique per payment attempt
    tx_ref             = Column(String(255), unique=True, index=True)
    chapa_checkout_url = Column(String(500))
    # Receipt fields (populated after payment is verified)
    receipt_code       = Column(String(20), unique=True, index=True)
    receipt_token      = Column(Text)
    redeemed           = Column(Boolean, default=False, nullable=False)
    redeemed_at        = Column(DateTime(timezone=True))
    notes              = Column(Text)
    # ── Promo / discount ──────────────────────────────────────────────────────
    promo_code         = Column(String(50), nullable=True)
    discount_amount    = Column(Float, default=0.0, nullable=True)
    original_amount    = Column(Float, nullable=True)
    # ── Pickup tracking (Phase 1) ─────────────────────────────────────────────
    picked_up_at       = Column(DateTime(timezone=True))          # exact handover time
    picked_up_by_id    = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    pickup_location    = Column(String(100))                      # register / terminal ID
    redemption_count   = Column(Integer, default=0, nullable=False)
    # ─────────────────────────────────────────────────────────────────────────
    created_at         = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at         = Column(DateTime(timezone=True), onupdate=func.now())


class AuditLog(Base):
    """
    Immutable audit trail for every redemption attempt and sensitive action.
    Rows are NEVER updated or deleted — only inserted.
    """
    __tablename__ = "audit_logs"

    id           = Column(Integer, primary_key=True, index=True)
    event        = Column(SQLEnum(AuditEvent), nullable=False, index=True)
    order_id     = Column(Integer, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True)
    order_number = Column(String(20))          # denormalised — survives order deletion
    actor_id     = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_name   = Column(String(50))          # denormalised — survives user deletion
    actor_role   = Column(String(20))
    ip_address   = Column(String(45))          # supports IPv6
    terminal_id  = Column(String(100))         # register / device label
    note         = Column(Text)                # human-readable context
    created_at   = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class SuspiciousActivity(Base):
    """
    Flagged events that may indicate fraud or abuse.
    Reviewed by admin in the security dashboard.
    """
    __tablename__ = "suspicious_activity"

    id          = Column(Integer, primary_key=True, index=True)
    type        = Column(String(50), nullable=False, index=True)
    # DUPLICATE_SCAN | RAPID_ATTEMPTS | INVALID_TOKEN_FLOOD | BRUTE_FORCE
    description = Column(Text)
    ip_address  = Column(String(45))
    actor_id    = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_name  = Column(String(50))
    order_id    = Column(Integer, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True)
    resolved    = Column(Boolean, default=False, nullable=False)
    created_at  = Column(DateTime(timezone=True), server_default=func.now(), index=True)


# ─── Loyalty Points ────────────────────────────────────────────────────────────

class LoyaltyAccount(Base):
    """
    One account per unique phone number.
    Customers earn 1 point per ETB spent.
    100 points = free item or ETB 10 discount (Phase 2 redemption).
    """
    __tablename__ = "loyalty_accounts"

    id             = Column(Integer, primary_key=True, index=True)
    phone          = Column(String(30), unique=True, nullable=False, index=True)
    display_name   = Column(String(100))
    email          = Column(String(255), nullable=True, index=True)
    points_balance = Column(Integer, default=0, nullable=False)
    total_earned   = Column(Integer, default=0, nullable=False)
    total_redeemed = Column(Integer, default=0, nullable=False)
    created_at     = Column(DateTime(timezone=True), server_default=func.now())
    updated_at     = Column(DateTime(timezone=True), onupdate=func.now())


class PointTransaction(Base):
    """Immutable ledger of every points earn/redeem event."""
    __tablename__ = "point_transactions"

    id          = Column(Integer, primary_key=True, index=True)
    account_id  = Column(Integer, ForeignKey("loyalty_accounts.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    order_id    = Column(Integer, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True)
    order_number = Column(String(20))          # denormalised — survives order deletion
    txn_type    = Column(String(10), nullable=False)   # "earn" | "redeem"
    points      = Column(Integer, nullable=False)      # always positive
    description = Column(String(255))
    created_at  = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class RewardItem(Base):
    """Admin-managed catalog of redeemable rewards (refreshed monthly)."""
    __tablename__ = "reward_items"

    id               = Column(Integer, primary_key=True, index=True)
    name             = Column(String(200), nullable=False)
    description      = Column(Text)
    points_required  = Column(Integer, nullable=False)
    image_url        = Column(String(500))
    is_active        = Column(Boolean, default=True, nullable=False, index=True)
    quantity_limit   = Column(Integer, nullable=True)     # None = unlimited
    quantity_claimed = Column(Integer, default=0, nullable=False)
    sort_order       = Column(Integer, default=0, nullable=False)
    valid_until      = Column(DateTime(timezone=True), nullable=True)
    created_at       = Column(DateTime(timezone=True), server_default=func.now())
    updated_at       = Column(DateTime(timezone=True), onupdate=func.now())


class RewardClaim(Base):
    """Record of a customer claiming a reward from the catalog."""
    __tablename__ = "reward_claims"

    id           = Column(Integer, primary_key=True, index=True)
    account_id   = Column(Integer, ForeignKey("loyalty_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    reward_id    = Column(Integer, ForeignKey("reward_items.id", ondelete="SET NULL"), nullable=True)
    reward_name  = Column(String(200), nullable=False)   # denormalised
    points_spent = Column(Integer, nullable=False)
    claim_code   = Column(String(20), unique=True, nullable=False, index=True)
    identifier   = Column(String(255))                   # phone or email (denormalised)
    is_fulfilled = Column(Boolean, default=False, nullable=False, index=True)
    fulfilled_at = Column(DateTime(timezone=True))
    created_at   = Column(DateTime(timezone=True), server_default=func.now(), index=True)


# ─── Menu Ingredients ─────────────────────────────────────────────────────────

class MenuIngredient(Base):
    """Customization options / toppings / add-ons for a menu item."""
    __tablename__ = "menu_ingredients"

    id           = Column(Integer, primary_key=True, index=True)
    menu_item_id = Column(Integer, ForeignKey("menu_items.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    group_name   = Column(String(100), nullable=False, default="Options")
    name         = Column(String(200), nullable=False)
    price_delta  = Column(Float, default=0.0, nullable=False)
    input_type   = Column(String(20), default="checkbox", nullable=False)  # "radio" | "checkbox"
    is_required  = Column(Boolean, default=False, nullable=False)
    is_default   = Column(Boolean, default=False, nullable=False)
    sort_order   = Column(Integer, default=0, nullable=False)
    is_available = Column(Boolean, default=True, nullable=False)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    updated_at   = Column(DateTime(timezone=True), onupdate=func.now())


# ─── Customer Reviews ──────────────────────────────────────────────────────────

class Review(Base):
    """Customer-submitted star ratings and comments."""
    __tablename__ = "reviews"

    id            = Column(Integer, primary_key=True, index=True)
    reviewer_name = Column(String(100), nullable=False)
    rating        = Column(Integer, nullable=False)       # 1–5 stars
    comment       = Column(Text)
    order_number  = Column(String(20))                    # optional link to order
    is_approved   = Column(Boolean, default=True, nullable=False, index=True)
    # Customer self-edit token — returned once on POST, stored in browser localStorage
    edit_token    = Column(String(64), nullable=True)
    # Admin reply — shown publicly below the review
    admin_reply   = Column(Text, nullable=True)
    replied_at    = Column(DateTime(timezone=True), nullable=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at    = Column(DateTime(timezone=True), onupdate=func.now())


# ─── Payment Records ──────────────────────────────────────────────────────────

class Payment(Base):
    """
    One row per payment attempt against an order.
    Amounts stored as integer SANTIM (1 ETB = 100 santim) to avoid float rounding.
    Never use floats for money — display by dividing by 100.
    """
    __tablename__ = "payments"

    id                 = Column(Integer, primary_key=True, index=True)
    order_id           = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"),
                                nullable=False, index=True)
    payment_method     = Column(SQLEnum(PaymentMethod), nullable=False)
    # Amount in SANTIM (integer). ETB 150.00 → 15000 santim
    amount_santim      = Column(BigInteger, nullable=False)
    currency           = Column(String(10), default="ETB", nullable=False)
    status             = Column(SQLEnum(PaymentStatus), nullable=False,
                                default=PaymentStatus.pending, index=True)
    # External reference from the payment provider (Telebirr txn ID, CBE ref, etc.)
    external_reference = Column(String(255), nullable=True, index=True)
    # Raw callback payload stored for audit — never trust, always verify
    callback_payload   = Column(JSON, nullable=True)
    refund_amount_santim = Column(BigInteger, nullable=True)
    captured_at        = Column(DateTime(timezone=True), nullable=True)
    created_at         = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at         = Column(DateTime(timezone=True), onupdate=func.now())


# ─── Promo Codes ──────────────────────────────────────────────────────────────

class PromoCode(Base):
    """Discount codes admin can create. Applied at checkout."""
    __tablename__ = "promo_codes"

    id              = Column(Integer, primary_key=True, index=True)
    code            = Column(String(50), unique=True, nullable=False, index=True)
    discount_type   = Column(SQLEnum(DiscountType), nullable=False)
    discount_value  = Column(Integer, nullable=False)  # percent 0-100 or fixed santim
    # Minimum order total in santim before promo is valid
    min_order_total = Column(BigInteger, default=0, nullable=False)
    max_uses        = Column(Integer, nullable=True)   # None = unlimited
    uses_count      = Column(Integer, default=0, nullable=False)
    expires_at      = Column(DateTime(timezone=True), nullable=True)
    is_active       = Column(Boolean, default=True, nullable=False, index=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), onupdate=func.now())


# ─── Kitchen Stations ──────────────────────────────────────────────────────────

class Station(Base):
    """
    Named kitchen stations (e.g. Grill, Drinks, Cold).
    Menu items can be assigned to a station so the right screen shows them.
    """
    __tablename__ = "stations"

    id           = Column(Integer, primary_key=True, index=True)
    name         = Column(String(50), unique=True, nullable=False)
    display_name = Column(String(100), nullable=False)
    color        = Column(String(20), default="#0f172a")  # hex for UI badge
    is_active    = Column(Boolean, default=True, nullable=False)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())


# ─── Refunds ──────────────────────────────────────────────────────────────────

class RefundStatus(str, enum.Enum):
    pending    = "pending"     # logged, not yet sent
    processed  = "processed"   # money sent to customer
    failed     = "failed"      # attempt failed


class Refund(Base):
    """
    One row per refund issued against an order.
    Amount stored as ETB float (display-level) for simplicity — not used in
    accounting aggregation, only in reporting.
    """
    __tablename__ = "refunds"

    id                = Column(Integer, primary_key=True, index=True)
    order_id          = Column(Integer, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True)
    order_number      = Column(String(20), nullable=False, index=True)   # denormalised
    amount            = Column(Float, nullable=False)                     # ETB
    reason            = Column(Text, nullable=True)
    # How the refund is returned to the customer
    refund_method     = Column(String(30), nullable=False)               # cash | telebirr | cbebirr | bank_transfer | card
    # Bank / card destination (filled when refund_method != cash)
    dest_bank_name    = Column(String(100), nullable=True)
    dest_account_no   = Column(String(50),  nullable=True)
    dest_account_name = Column(String(100), nullable=True)
    dest_phone        = Column(String(30),  nullable=True)               # for mobile money
    dest_card_last4   = Column(String(4),   nullable=True)               # for card refunds (reference only)
    # Lifecycle
    status            = Column(SQLEnum(RefundStatus), default=RefundStatus.pending, nullable=False, index=True)
    processed_by_id   = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    processed_by_name = Column(String(50), nullable=True)               # denormalised
    processed_at      = Column(DateTime(timezone=True), nullable=True)
    notes             = Column(Text, nullable=True)                      # internal admin note
    created_at        = Column(DateTime(timezone=True), server_default=func.now(), index=True)
