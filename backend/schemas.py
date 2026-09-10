from __future__ import annotations
import re
from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import List, Optional, Dict, Any
from datetime import datetime
from models import UserRole, OrderStatus, PaymentMethod, AuditEvent

# Security: maximum lengths for all string inputs.
_MAX_USERNAME    = 50
_MAX_PASSWORD    = 128
_MAX_NAME        = 150
_MAX_PHONE       = 30
_MAX_DESCRIPTION = 1000
_MAX_NOTES       = 500
_MAX_CATEGORY    = 100
_MAX_URL         = 500
_MAX_TERMINAL    = 100

_MAX_ORDER_ITEMS   = 30
_MAX_ITEM_QUANTITY = 50


# ─── Auth ─────────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str
    role: UserRole = UserRole.customer

    @field_validator("username")
    @classmethod
    def username_alphanumeric(cls, v: str) -> str:
        v = v.strip()
        if len(v) > _MAX_USERNAME:
            raise ValueError(f"Username must be at most {_MAX_USERNAME} characters")
        if not v.replace("_", "").isalnum():
            raise ValueError("Username must be alphanumeric (underscores allowed)")
        if len(v) < 3:
            raise ValueError("Username must be at least 3 characters")
        return v.lower()

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 10:
            raise ValueError("Password must be at least 10 characters")
        if len(v) > _MAX_PASSWORD:
            raise ValueError(f"Password must be at most {_MAX_PASSWORD} characters")
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter (A-Z)")
        if not any(c.islower() for c in v):
            raise ValueError("Password must contain at least one lowercase letter (a-z)")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one number (0-9)")
        if not any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in v):
            raise ValueError("Password must contain at least one special character (!@#$%...)")
        return v


class UserLogin(BaseModel):
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def _username_len(cls, v: str) -> str:
        if len(v) > _MAX_USERNAME:
            raise ValueError("Username too long")
        return v.strip()

    @field_validator("password")
    @classmethod
    def _password_len(cls, v: str) -> str:
        if len(v) > _MAX_PASSWORD:
            raise ValueError("Password too long")
        return v


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    role: UserRole
    is_active: bool

    model_config = {"from_attributes": True}


class Token(BaseModel):
    access_token: str
    token_type: str
    user: UserResponse


# ─── Menu Ingredients ─────────────────────────────────────────────────────────

class MenuIngredientCreate(BaseModel):
    group_name:  str   = Field(..., min_length=1, max_length=100)
    name:        str   = Field(..., min_length=1, max_length=200)
    price_delta: float = Field(0.0, ge=0.0, le=10_000.0)
    input_type:  str   = Field("checkbox")
    is_required: bool  = False
    is_default:  bool  = False
    sort_order:  int   = Field(0, ge=0)

    @field_validator("input_type")
    @classmethod
    def validate_input_type(cls, v: str) -> str:
        if v not in ("radio", "checkbox", "toggle"):
            raise ValueError("input_type must be 'radio', 'checkbox', or 'toggle'")
        return v


class MenuIngredientUpdate(BaseModel):
    group_name:   Optional[str]   = None
    name:         Optional[str]   = None
    price_delta:  Optional[float] = None
    input_type:   Optional[str]   = None
    is_required:  Optional[bool]  = None
    is_default:   Optional[bool]  = None
    sort_order:   Optional[int]   = None
    is_available: Optional[bool]  = None


class MenuIngredientResponse(BaseModel):
    id:           int
    menu_item_id: int
    group_name:   str
    name:         str
    price_delta:  float
    input_type:   str
    is_required:  bool
    is_default:   bool
    sort_order:   int
    is_available: bool

    model_config = {"from_attributes": True}


# ─── Menu ─────────────────────────────────────────────────────────────────────

class MenuItemCreate(BaseModel):
    name: str
    description: Optional[str] = None
    price: float
    category: str
    image_url: Optional[str] = None
    is_available: bool = True

    @field_validator("price")
    @classmethod
    def price_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Price must be positive")
        if v > 100_000:
            raise ValueError("Price is unrealistically high")
        return round(v, 2)

    @field_validator("name")
    @classmethod
    def name_valid(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name cannot be empty")
        if len(v) > 255:
            raise ValueError("Name must be at most 255 characters")
        return v

    @field_validator("category")
    @classmethod
    def category_valid(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Category cannot be empty")
        if len(v) > _MAX_CATEGORY:
            raise ValueError(f"Category must be at most {_MAX_CATEGORY} characters")
        return v

    @field_validator("description")
    @classmethod
    def description_len(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v) > _MAX_DESCRIPTION:
            raise ValueError(f"Description must be at most {_MAX_DESCRIPTION} characters")
        return v

    @field_validator("image_url")
    @classmethod
    def validate_image_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            return None
        if not re.match(r"^https?://", v, re.IGNORECASE):
            raise ValueError("image_url must start with http:// or https://")
        if len(v) > _MAX_URL:
            raise ValueError(f"image_url must be at most {_MAX_URL} characters")
        return v


class MenuItemUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    category: Optional[str] = None
    image_url: Optional[str] = None
    is_available: Optional[bool] = None

    @field_validator("price")
    @classmethod
    def price_positive(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v <= 0:
            raise ValueError("Price must be positive")
        if v is not None and v > 100_000:
            raise ValueError("Price is unrealistically high")
        return v

    @field_validator("image_url")
    @classmethod
    def validate_image_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            return None
        if not re.match(r"^https?://", v, re.IGNORECASE):
            raise ValueError("image_url must start with http:// or https://")
        if len(v) > _MAX_URL:
            raise ValueError(f"image_url must be at most {_MAX_URL} characters")
        return v


class MenuItemResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    price: float
    category: str
    image_url: Optional[str]
    is_available: bool
    ingredients: List["MenuIngredientResponse"] = []

    model_config = {"from_attributes": True}


# ─── Orders ───────────────────────────────────────────────────────────────────

class OrderItemInput(BaseModel):
    menu_item_id:          int
    quantity:              int
    customizations:        List[int] = []  # selected add-on/choice ingredient IDs
    removals:              List[int] = []  # toggle ingredient IDs the customer wants removed
    special_instructions:  Optional[str] = None

    @field_validator("quantity")
    @classmethod
    def qty_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("Quantity must be at least 1")
        if v > _MAX_ITEM_QUANTITY:
            raise ValueError(f"Quantity cannot exceed {_MAX_ITEM_QUANTITY} per item")
        return v

    @field_validator("special_instructions")
    @classmethod
    def special_instructions_len(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            if not v:
                return None
            if len(v) > _MAX_NOTES:
                raise ValueError(f"Special instructions must be at most {_MAX_NOTES} characters")
        return v


class OrderCreate(BaseModel):
    customer_name: str
    customer_phone: Optional[str] = None
    customer_email: Optional[str] = None
    items: List[OrderItemInput]
    payment_method: PaymentMethod
    notes: Optional[str] = None
    promo_code: Optional[str] = None     # validated server-side on order creation

    @field_validator("customer_name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Customer name cannot be empty")
        if len(v) > _MAX_NAME:
            raise ValueError(f"Customer name must be at most {_MAX_NAME} characters")
        return v

    @field_validator("customer_phone")
    @classmethod
    def phone_format(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            return None
        if len(v) > _MAX_PHONE:
            raise ValueError(f"Phone number must be at most {_MAX_PHONE} characters")
        if not re.match(r"^[\d\s\+\-\(\)]+$", v):
            raise ValueError("Phone number contains invalid characters")
        return v

    @field_validator("notes")
    @classmethod
    def notes_len(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v) > _MAX_NOTES:
            raise ValueError(f"Notes must be at most {_MAX_NOTES} characters")
        return v

    @field_validator("items")
    @classmethod
    def items_not_empty(cls, v: List[OrderItemInput]) -> List[OrderItemInput]:
        if not v:
            raise ValueError("Order must contain at least one item")
        if len(v) > _MAX_ORDER_ITEMS:
            raise ValueError(f"Order cannot contain more than {_MAX_ORDER_ITEMS} different items")
        return v


class OrderResponse(BaseModel):
    id: int
    order_number: str
    customer_name: str
    customer_phone: Optional[str]
    customer_email: Optional[str]
    items_snapshot: List[Dict[str, Any]]
    total_amount: float
    status: OrderStatus
    payment_method: PaymentMethod
    tx_ref: Optional[str]
    chapa_checkout_url: Optional[str]
    receipt_code: Optional[str]
    receipt_token: Optional[str]
    redeemed: bool
    redeemed_at: Optional[datetime]
    notes: Optional[str]
    # Promo / discount fields
    promo_code: Optional[str] = None
    discount_amount: Optional[float] = None
    original_amount: Optional[float] = None
    # Pickup tracking fields
    picked_up_at: Optional[datetime]
    picked_up_by_id: Optional[int]
    pickup_location: Optional[str]
    redemption_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class OrderStatusUpdate(BaseModel):
    status: OrderStatus


# ─── Verify / Redeem ──────────────────────────────────────────────────────────

class CheckReceiptRequest(BaseModel):
    """Preview-only: look up an order without marking it as picked up."""
    token: Optional[str] = None        # full signed token (from QR scan)
    receipt_code: Optional[str] = None  # short human-readable code (manual entry)

    @field_validator("receipt_code")
    @classmethod
    def code_clean(cls, v: Optional[str]) -> Optional[str]:
        if v:
            return v.strip().upper()
        return v


class RedeemRequest(BaseModel):
    """Verify + atomically mark the order as PICKED_UP in one step."""
    token: Optional[str] = None
    receipt_code: Optional[str] = None
    terminal_id: Optional[str] = "register-1"  # which register/device

    @field_validator("receipt_code")
    @classmethod
    def code_clean(cls, v: Optional[str]) -> Optional[str]:
        if v:
            return v.strip().upper()
        return v

    @field_validator("terminal_id")
    @classmethod
    def terminal_clean(cls, v: Optional[str]) -> Optional[str]:
        if v and len(v) > _MAX_TERMINAL:
            raise ValueError("terminal_id too long")
        return v


class RedeemResponse(BaseModel):
    """Response from the redeem endpoint."""
    status: str          # REDEEMED | ALREADY_REDEEMED | INVALID | NOT_READY | NOT_FOUND
    order_number: Optional[str] = None
    customer_name: Optional[str] = None
    items: Optional[List[Dict[str, Any]]] = None
    total_amount: Optional[float] = None
    payment_method: Optional[str] = None
    picked_up_at: Optional[datetime] = None
    picked_up_by: Optional[str] = None   # username of cashier who redeemed
    message: str = ""


class ResetPickupRequest(BaseModel):
    """Admin-only: reset a PICKED_UP order back to READY."""
    reason: str

    @field_validator("reason")
    @classmethod
    def reason_required(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Reason is required for audit trail")
        if len(v) > 500:
            raise ValueError("Reason must be at most 500 characters")
        return v


# ─── Audit ────────────────────────────────────────────────────────────────────

class AuditLogResponse(BaseModel):
    id: int
    event: AuditEvent
    order_id: Optional[int]
    order_number: Optional[str]
    actor_name: Optional[str]
    actor_role: Optional[str]
    ip_address: Optional[str]
    terminal_id: Optional[str]
    note: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class SuspiciousActivityResponse(BaseModel):
    id: int
    type: str
    description: Optional[str]
    ip_address: Optional[str]
    actor_name: Optional[str]
    order_id: Optional[int]
    resolved: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── Payment ──────────────────────────────────────────────────────────────────

class PaymentInitResponse(BaseModel):
    checkout_url: str
    tx_ref: str
    order_id: int


# ─── Receipt / Verification (legacy — kept for compatibility) ─────────────────

class VerifyReceiptRequest(BaseModel):
    token: str


class VerifyReceiptResponse(BaseModel):
    valid: bool
    message: str
    order: Optional[OrderResponse] = None


# ─── Analytics ────────────────────────────────────────────────────────────────

class AnalyticsResponse(BaseModel):
    total_orders: int
    total_revenue: float
    pending_orders: int
    cash_pending_orders: int
    paid_orders: int
    preparing_orders: int
    ready_orders: int
    picked_up_orders: int
    completed_orders: int
    today_orders: int
    today_revenue: float
