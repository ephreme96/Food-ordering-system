"""
Food Ordering System — FastAPI Application Entry Point
"""
from __future__ import annotations
import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

load_dotenv()

# ─── Startup Validation ───────────────────────────────────────────────────────
# Security: refuse to start if critical secrets are missing or use insecure defaults.
# This prevents accidentally deploying with placeholder keys from .env.example.
_INSECURE_DEFAULTS = {
    "insecure-default-change-me",
    "change-me",
    "secret",
    "your-secret-key",
    "",
}

def _validate_env() -> None:
    """Fail fast if critical environment secrets are missing or insecure."""
    secret_key = os.getenv("SECRET_KEY", "")
    if secret_key in _INSECURE_DEFAULTS or len(secret_key) < 32:
        raise RuntimeError(
            "FATAL: SECRET_KEY is missing or insecure. "
            "Generate one: python -c \"import secrets; print(secrets.token_hex(64))\""
        )
    hmac_secret = os.getenv("RECEIPT_HMAC_SECRET", "")
    if hmac_secret in _INSECURE_DEFAULTS or len(hmac_secret) < 32:
        raise RuntimeError(
            "FATAL: RECEIPT_HMAC_SECRET is missing or insecure. "
            "Generate one: python -c \"import secrets; print(secrets.token_hex(32))\""
        )

_validate_env()  # Called at import time — server will not start with bad secrets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
)
logger = logging.getLogger(__name__)

IS_PRODUCTION = os.getenv("ENVIRONMENT", "development").lower() == "production"


# ─── Security Headers Middleware ─────────────────────────────────────────────
# Security: adds critical HTTP response headers on EVERY response.
# These headers are the first line of defense against several browser-based attacks.
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Prevents browsers from guessing content types (MIME sniffing attacks).
        # Without this, a server returning "text/plain" could be executed as JS.
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Prevents the site from being loaded inside an <iframe> on another domain.
        # Stops clickjacking: attacker overlays invisible iframe to steal clicks.
        response.headers["X-Frame-Options"] = "DENY"

        # Legacy XSS filter for older browsers. Modern browsers use CSP instead.
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Controls what info is sent in the Referer header.
        # Prevents leaking full URLs (which may contain tokens) to external sites.
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Limits which browser APIs are available to the page.
        # We only allow camera (for QR scanning) — nothing else.
        response.headers["Permissions-Policy"] = "camera=(self), microphone=()"

        # Content Security Policy: controls which scripts/styles/images can load.
        # 'unsafe-inline' is required by Tailwind CDN and inline event handlers.
        # For a fully hardened deployment, move scripts to .js files and use nonces.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: https:; "
            "connect-src 'self'; "
            "frame-ancestors 'none';"
        )

        # HSTS: forces HTTPS for 1 year. Only enable when the site actually runs on HTTPS.
        # Enabling on HTTP causes the browser to refuse to load the site at all.
        if IS_PRODUCTION and os.getenv("HTTPS_ENABLED", "false").lower() == "true":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )

        return response


# ─── Default Data Seeding ─────────────────────────────────────────────────────

def _seed_default_accounts(db: Session) -> None:
    """Create default admin / kitchen / cashier accounts on first run."""
    from auth import get_password_hash
    from models import User, UserRole

    accounts = [
        (
            "admin",
            "admin@foodorder.et",
            os.getenv("ADMIN_DEFAULT_PASSWORD", "Admin@1234"),
            UserRole.admin,
        ),
        (
            "kitchen_staff",
            "kitchen@foodorder.et",
            os.getenv("KITCHEN_DEFAULT_PASSWORD", "Kitchen@1234"),
            UserRole.kitchen,
        ),
        (
            "cashier1",
            "cashier@foodorder.et",
            os.getenv("CASHIER_DEFAULT_PASSWORD", "Cashier@1234"),
            UserRole.cashier,
        ),
    ]
    created = []
    for username, email, password, role in accounts:
        if not db.query(User).filter(User.username == username).first():
            db.add(User(
                username=username,
                email=email,
                password_hash=get_password_hash(password),
                role=role,
            ))
            created.append(username)

    if created:
        db.commit()
        logger.info("Seeded default accounts: %s", created)


_OLD_ETHIOPIAN_ITEMS = {
    "Tibs", "Doro Wat", "Kitfo", "Shiro", "Alicha", "Gored Gored",
    "Misir", "Gomen", "Fosolia", "Beyainatu", "Firfir", "Foul",
    "Enkulal Firfir", "Ethiopian Coffee", "Tej", "Fresh Juice",
    "Ambo Water", "Injera (Extra)", "Ayib",
}


def _migrate_menu_to_chaofan(db: Session) -> None:
    """One-time migration: remove old Ethiopian seed items so Chaofan can be seeded."""
    from models import MenuItem, MenuIngredient

    old_items = db.query(MenuItem).filter(MenuItem.name.in_(_OLD_ETHIOPIAN_ITEMS)).all()
    if not old_items:
        return

    old_ids = [item.id for item in old_items]
    db.query(MenuIngredient).filter(MenuIngredient.menu_item_id.in_(old_ids)).delete(synchronize_session=False)
    db.query(MenuItem).filter(MenuItem.id.in_(old_ids)).delete(synchronize_session=False)
    db.commit()
    logger.info("Migrated old Ethiopian menu — removed %d items, re-seeding with Chaofan menu.", len(old_items))


def _seed_sample_menu(db: Session) -> None:
    """Seed Chaofan restaurant menu on first run."""
    from models import MenuItem

    if db.query(MenuItem).count() > 0:
        return  # already seeded

    sample_items = [
        # Chaofan (Fried Rice)
        {
            "name": "Chicken Chaofan",
            "description": "Wok-fried rice with tender chicken strips, mixed vegetables and savory soy sauce",
            "price": 500.0,
            "category": "Chaofan",
            "image_url": "https://images.unsplash.com/photo-1603133872878-684f208fb84b?w=500&q=80",
            "sort_order": 1,
        },
        {
            "name": "Beef Chaofan",
            "description": "Wok-fried rice with juicy beef strips, scrambled egg and fresh vegetables",
            "price": 450.0,
            "category": "Chaofan",
            "image_url": "https://images.unsplash.com/photo-1512058564366-18510be2db19?w=500&q=80",
            "sort_order": 2,
        },
        {
            "name": "Egg Chaofan",
            "description": "Classic egg fried rice — fluffy scrambled eggs, green onions, light soy seasoning",
            "price": 300.0,
            "category": "Chaofan",
            "image_url": "https://images.unsplash.com/photo-1603133872878-684f208fb84b?w=500&q=80",
            "sort_order": 3,
        },
        {
            "name": "Veggie Chaofan",
            "description": "Wok-fried rice with seasonal vegetables, garlic and light sauce — no meat",
            "price": 280.0,
            "category": "Chaofan",
            "image_url": "https://images.unsplash.com/photo-1563245372-f21724e3856d?w=500&q=80",
            "sort_order": 4,
        },
        {
            "name": "Chicken Thigh with Preferred Chaofan",
            "description": "Juicy marinated chicken thigh served alongside your choice of chaofan style",
            "price": 250.0,
            "category": "Chaofan",
            "image_url": "https://images.unsplash.com/photo-1598515214211-89d3c73ae83b?w=500&q=80",
            "sort_order": 5,
        },
        # Extras / Add-ons
        {
            "name": "Extra Egg",
            "description": "Add an extra egg to any dish",
            "price": 40.0,
            "category": "Extras",
            "image_url": "https://images.unsplash.com/photo-1482049016688-2d3e1b311543?w=500&q=80",
            "sort_order": 1,
        },
        {
            "name": "Take Away",
            "description": "Packaging fee for take-away orders",
            "price": 40.0,
            "category": "Extras",
            "sort_order": 2,
        },
    ]

    for data in sample_items:
        db.add(MenuItem(**data, is_available=True, cashier_visible=True))
    db.commit()
    logger.info("Seeded %d Chaofan menu items", len(sample_items))


def _seed_sample_ingredients(db: Session) -> None:
    """Seed customisation options for the Chaofan menu items."""
    from models import MenuItem, MenuIngredient

    if db.query(MenuIngredient).count() > 0:
        return

    def _item(name: str) -> "MenuItem | None":
        return db.query(MenuItem).filter(MenuItem.name == name).first()

    def _add(item, group_name, name, input_type="toggle", is_required=False,
             is_default=True, price_delta=0.0, sort_order=0):
        if item:
            db.add(MenuIngredient(
                menu_item_id=item.id,
                group_name=group_name,
                name=name,
                input_type=input_type,
                is_required=is_required,
                is_default=is_default,
                price_delta=price_delta,
                sort_order=sort_order,
            ))

    # ── Chicken Chaofan ───────────────────────────────────────────────────────
    ch = _item("Chicken Chaofan")
    _add(ch, "Spice Level", "Not Spicy", "radio", is_required=True, is_default=True,  sort_order=0)
    _add(ch, "Spice Level", "Medium",    "radio", is_required=True, is_default=False, sort_order=1)
    _add(ch, "Spice Level", "Spicy",     "radio", is_required=True, is_default=False, sort_order=2)
    _add(ch, "Size",        "Regular",   "radio", is_required=True, is_default=True,  sort_order=0)
    _add(ch, "Size",        "Large",     "radio", is_required=True, is_default=False, price_delta=50.0, sort_order=1)
    _add(ch, "Extras",      "Extra Egg", "checkbox", is_default=False, price_delta=40.0, sort_order=0)
    _add(ch, "Extras",      "Take Away", "checkbox", is_default=False, price_delta=40.0, sort_order=1)

    # ── Beef Chaofan ──────────────────────────────────────────────────────────
    bf = _item("Beef Chaofan")
    _add(bf, "Spice Level", "Not Spicy", "radio", is_required=True, is_default=True,  sort_order=0)
    _add(bf, "Spice Level", "Medium",    "radio", is_required=True, is_default=False, sort_order=1)
    _add(bf, "Spice Level", "Spicy",     "radio", is_required=True, is_default=False, sort_order=2)
    _add(bf, "Size",        "Regular",   "radio", is_required=True, is_default=True,  sort_order=0)
    _add(bf, "Size",        "Large",     "radio", is_required=True, is_default=False, price_delta=50.0, sort_order=1)
    _add(bf, "Extras",      "Extra Egg", "checkbox", is_default=False, price_delta=40.0, sort_order=0)
    _add(bf, "Extras",      "Take Away", "checkbox", is_default=False, price_delta=40.0, sort_order=1)

    # ── Egg Chaofan ───────────────────────────────────────────────────────────
    eg = _item("Egg Chaofan")
    _add(eg, "Spice Level", "Not Spicy", "radio", is_required=True, is_default=True,  sort_order=0)
    _add(eg, "Spice Level", "Medium",    "radio", is_required=True, is_default=False, sort_order=1)
    _add(eg, "Spice Level", "Spicy",     "radio", is_required=True, is_default=False, sort_order=2)
    _add(eg, "Size",        "Regular",   "radio", is_required=True, is_default=True,  sort_order=0)
    _add(eg, "Size",        "Large",     "radio", is_required=True, is_default=False, price_delta=50.0, sort_order=1)
    _add(eg, "Extras",      "Extra Egg", "checkbox", is_default=False, price_delta=40.0, sort_order=0)
    _add(eg, "Extras",      "Take Away", "checkbox", is_default=False, price_delta=40.0, sort_order=1)

    # ── Veggie Chaofan ────────────────────────────────────────────────────────
    vg = _item("Veggie Chaofan")
    _add(vg, "Spice Level", "Not Spicy", "radio", is_required=True, is_default=True,  sort_order=0)
    _add(vg, "Spice Level", "Medium",    "radio", is_required=True, is_default=False, sort_order=1)
    _add(vg, "Spice Level", "Spicy",     "radio", is_required=True, is_default=False, sort_order=2)
    _add(vg, "Size",        "Regular",   "radio", is_required=True, is_default=True,  sort_order=0)
    _add(vg, "Size",        "Large",     "radio", is_required=True, is_default=False, price_delta=50.0, sort_order=1)
    _add(vg, "Extras",      "Extra Egg", "checkbox", is_default=False, price_delta=40.0, sort_order=0)
    _add(vg, "Extras",      "Take Away", "checkbox", is_default=False, price_delta=40.0, sort_order=1)

    # ── Chicken Thigh with Preferred Chaofan ──────────────────────────────────
    ct = _item("Chicken Thigh with Preferred Chaofan")
    _add(ct, "Chaofan Style", "Chicken Chaofan", "radio", is_required=True, is_default=True,  sort_order=0)
    _add(ct, "Chaofan Style", "Beef Chaofan",    "radio", is_required=True, is_default=False, sort_order=1)
    _add(ct, "Chaofan Style", "Egg Chaofan",     "radio", is_required=True, is_default=False, sort_order=2)
    _add(ct, "Chaofan Style", "Veggie Chaofan",  "radio", is_required=True, is_default=False, sort_order=3)
    _add(ct, "Spice Level",   "Not Spicy", "radio", is_required=True, is_default=True,  sort_order=0)
    _add(ct, "Spice Level",   "Medium",    "radio", is_required=True, is_default=False, sort_order=1)
    _add(ct, "Spice Level",   "Spicy",     "radio", is_required=True, is_default=False, sort_order=2)
    _add(ct, "Extras",        "Extra Egg", "checkbox", is_default=False, price_delta=40.0, sort_order=0)
    _add(ct, "Extras",        "Take Away", "checkbox", is_default=False, price_delta=40.0, sort_order=1)

    db.commit()
    logger.info("Seeded Chaofan customisation options.")


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    from database import Base, engine, SessionLocal
    import models  # noqa: F401 – ensure all models are registered before create_all

    logger.info("Starting up – creating database tables…")
    Base.metadata.create_all(bind=engine)

    # ── Schema migrations (add new columns to existing tables if missing) ─────
    # create_all only creates brand-new tables; it never alters existing ones.
    # Any column added to a model AFTER the table was first created must be
    # added here so deployments/restarts pick it up automatically.
    from sqlalchemy import text
    try:
        with engine.connect() as _conn:
            _conn.execute(text("""
                ALTER TABLE orders
                    ADD COLUMN IF NOT EXISTS picked_up_at     TIMESTAMPTZ,
                    ADD COLUMN IF NOT EXISTS picked_up_by_id  INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    ADD COLUMN IF NOT EXISTS pickup_location  VARCHAR(100),
                    ADD COLUMN IF NOT EXISTS redemption_count INTEGER NOT NULL DEFAULT 0;
            """))
            _conn.commit()
        logger.info("Schema migration (orders) complete.")
    except Exception as _exc:
        logger.warning("Schema migration (orders) skipped or failed: %s", _exc)

    # Loyalty + Review tables (created by create_all above, migration is safety net)
    try:
        with engine.connect() as _conn:
            _conn.execute(text("""
                CREATE TABLE IF NOT EXISTS loyalty_accounts (
                    id             SERIAL PRIMARY KEY,
                    phone          VARCHAR(30) NOT NULL UNIQUE,
                    display_name   VARCHAR(100),
                    points_balance INTEGER NOT NULL DEFAULT 0,
                    total_earned   INTEGER NOT NULL DEFAULT 0,
                    total_redeemed INTEGER NOT NULL DEFAULT 0,
                    created_at     TIMESTAMPTZ DEFAULT now(),
                    updated_at     TIMESTAMPTZ
                );
                CREATE TABLE IF NOT EXISTS point_transactions (
                    id           SERIAL PRIMARY KEY,
                    account_id   INTEGER NOT NULL REFERENCES loyalty_accounts(id) ON DELETE CASCADE,
                    order_id     INTEGER REFERENCES orders(id) ON DELETE SET NULL,
                    order_number VARCHAR(20),
                    txn_type     VARCHAR(10) NOT NULL,
                    points       INTEGER NOT NULL,
                    description  VARCHAR(255),
                    created_at   TIMESTAMPTZ DEFAULT now()
                );
                CREATE TABLE IF NOT EXISTS reviews (
                    id            SERIAL PRIMARY KEY,
                    reviewer_name VARCHAR(100) NOT NULL,
                    rating        INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
                    comment       TEXT,
                    order_number  VARCHAR(20),
                    is_approved   BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at    TIMESTAMPTZ DEFAULT now()
                );
            """))
            _conn.commit()
        logger.info("Schema migration (loyalty + reviews) complete.")
    except Exception as _exc:
        logger.warning("Schema migration (loyalty + reviews) skipped or failed: %s", _exc)

    # Reviews v2: edit token + admin reply
    try:
        with engine.connect() as _conn:
            _conn.execute(text("""
                ALTER TABLE reviews
                    ADD COLUMN IF NOT EXISTS edit_token  VARCHAR(64),
                    ADD COLUMN IF NOT EXISTS admin_reply TEXT,
                    ADD COLUMN IF NOT EXISTS replied_at  TIMESTAMPTZ,
                    ADD COLUMN IF NOT EXISTS updated_at  TIMESTAMPTZ;
            """))
            _conn.commit()
        logger.info("Schema migration (reviews v2) complete.")
    except Exception as _exc:
        logger.warning("Schema migration (loyalty + reviews) skipped or failed: %s", _exc)

    # Loyalty v2: email on accounts + reward catalog + claims
    try:
        with engine.connect() as _conn:
            _conn.execute(text("""
                ALTER TABLE loyalty_accounts
                    ADD COLUMN IF NOT EXISTS email VARCHAR(255);
                CREATE INDEX IF NOT EXISTS ix_loyalty_accounts_email ON loyalty_accounts(email);
                ALTER TABLE loyalty_accounts
                    ALTER COLUMN phone DROP NOT NULL;
                CREATE TABLE IF NOT EXISTS reward_items (
                    id               SERIAL PRIMARY KEY,
                    name             VARCHAR(200) NOT NULL,
                    description      TEXT,
                    points_required  INTEGER NOT NULL,
                    image_url        VARCHAR(500),
                    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
                    quantity_limit   INTEGER,
                    quantity_claimed INTEGER NOT NULL DEFAULT 0,
                    sort_order       INTEGER NOT NULL DEFAULT 0,
                    valid_until      TIMESTAMPTZ,
                    created_at       TIMESTAMPTZ DEFAULT now(),
                    updated_at       TIMESTAMPTZ
                );
                CREATE TABLE IF NOT EXISTS reward_claims (
                    id           SERIAL PRIMARY KEY,
                    account_id   INTEGER NOT NULL REFERENCES loyalty_accounts(id) ON DELETE CASCADE,
                    reward_id    INTEGER REFERENCES reward_items(id) ON DELETE SET NULL,
                    reward_name  VARCHAR(200) NOT NULL,
                    points_spent INTEGER NOT NULL,
                    claim_code   VARCHAR(20) NOT NULL UNIQUE,
                    identifier   VARCHAR(255),
                    is_fulfilled BOOLEAN NOT NULL DEFAULT FALSE,
                    fulfilled_at TIMESTAMPTZ,
                    created_at   TIMESTAMPTZ DEFAULT now()
                );
                CREATE INDEX IF NOT EXISTS ix_reward_claims_account  ON reward_claims(account_id);
                CREATE INDEX IF NOT EXISTS ix_reward_claims_fulfilled ON reward_claims(is_fulfilled);
            """))
            _conn.commit()
        logger.info("Schema migration (loyalty v2 + rewards) complete.")
    except Exception as _exc:
        logger.warning("Schema migration (loyalty v2 + rewards) skipped or failed: %s", _exc)

    # Menu ingredients table (customization options per menu item)
    try:
        with engine.connect() as _conn:
            _conn.execute(text("""
                CREATE TABLE IF NOT EXISTS menu_ingredients (
                    id           SERIAL PRIMARY KEY,
                    menu_item_id INTEGER NOT NULL REFERENCES menu_items(id) ON DELETE CASCADE,
                    group_name   VARCHAR(100) NOT NULL DEFAULT 'Options',
                    name         VARCHAR(200) NOT NULL,
                    price_delta  FLOAT NOT NULL DEFAULT 0.0,
                    input_type   VARCHAR(20) NOT NULL DEFAULT 'checkbox',
                    is_required  BOOLEAN NOT NULL DEFAULT FALSE,
                    is_default   BOOLEAN NOT NULL DEFAULT FALSE,
                    sort_order   INTEGER NOT NULL DEFAULT 0,
                    is_available BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at   TIMESTAMPTZ DEFAULT now(),
                    updated_at   TIMESTAMPTZ
                );
                CREATE INDEX IF NOT EXISTS ix_menu_ingredients_menu_item_id
                    ON menu_ingredients(menu_item_id);
            """))
            _conn.commit()
        logger.info("Schema migration (menu_ingredients) complete.")
    except Exception as _exc:
        logger.warning("Schema migration (menu_ingredients) skipped or failed: %s", _exc)

    # Payments, promo codes, and kitchen stations
    try:
        with engine.connect() as _conn:
            _conn.execute(text("""
                CREATE TABLE IF NOT EXISTS payments (
                    id                   SERIAL PRIMARY KEY,
                    order_id             INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                    payment_method       VARCHAR(20) NOT NULL,
                    amount_santim        BIGINT NOT NULL,
                    currency             VARCHAR(10) NOT NULL DEFAULT 'ETB',
                    status               VARCHAR(20) NOT NULL DEFAULT 'pending',
                    external_reference   VARCHAR(255),
                    callback_payload     JSONB,
                    refund_amount_santim BIGINT,
                    captured_at          TIMESTAMPTZ,
                    created_at           TIMESTAMPTZ DEFAULT now(),
                    updated_at           TIMESTAMPTZ
                );
                CREATE INDEX IF NOT EXISTS ix_payments_order_id          ON payments(order_id);
                CREATE INDEX IF NOT EXISTS ix_payments_status            ON payments(status);
                CREATE INDEX IF NOT EXISTS ix_payments_external_reference ON payments(external_reference);

                CREATE TABLE IF NOT EXISTS promo_codes (
                    id              SERIAL PRIMARY KEY,
                    code            VARCHAR(50) NOT NULL UNIQUE,
                    discount_type   VARCHAR(10) NOT NULL,
                    discount_value  INTEGER NOT NULL,
                    min_order_total BIGINT NOT NULL DEFAULT 0,
                    max_uses        INTEGER,
                    uses_count      INTEGER NOT NULL DEFAULT 0,
                    expires_at      TIMESTAMPTZ,
                    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at      TIMESTAMPTZ DEFAULT now(),
                    updated_at      TIMESTAMPTZ
                );
                CREATE INDEX IF NOT EXISTS ix_promo_codes_code      ON promo_codes(code);
                CREATE INDEX IF NOT EXISTS ix_promo_codes_is_active  ON promo_codes(is_active);

                CREATE TABLE IF NOT EXISTS stations (
                    id           SERIAL PRIMARY KEY,
                    name         VARCHAR(50) NOT NULL UNIQUE,
                    display_name VARCHAR(100) NOT NULL,
                    color        VARCHAR(20) DEFAULT '#0f172a',
                    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at   TIMESTAMPTZ DEFAULT now()
                );
            """))
            _conn.commit()
        logger.info("Schema migration (payments + promo_codes + stations) complete.")
    except Exception as _exc:
        logger.warning("Schema migration (payments/promos/stations) skipped or failed: %s", _exc)

    # Cashier POS columns on menu_items + refunds table
    try:
        with engine.connect() as _conn:
            _conn.execute(text("""
                ALTER TABLE menu_items
                    ADD COLUMN IF NOT EXISTS cashier_visible BOOLEAN NOT NULL DEFAULT TRUE,
                    ADD COLUMN IF NOT EXISTS cashier_note    VARCHAR(255),
                    ADD COLUMN IF NOT EXISTS sort_order      INTEGER NOT NULL DEFAULT 0;
                CREATE INDEX IF NOT EXISTS ix_menu_items_cashier_visible ON menu_items(cashier_visible);

                CREATE TABLE IF NOT EXISTS refunds (
                    id                SERIAL PRIMARY KEY,
                    order_id          INTEGER REFERENCES orders(id) ON DELETE SET NULL,
                    order_number      VARCHAR(20) NOT NULL,
                    amount            FLOAT NOT NULL,
                    reason            TEXT,
                    refund_method     VARCHAR(30) NOT NULL,
                    dest_bank_name    VARCHAR(100),
                    dest_account_no   VARCHAR(50),
                    dest_account_name VARCHAR(100),
                    dest_phone        VARCHAR(30),
                    dest_card_last4   VARCHAR(4),
                    status            VARCHAR(20) NOT NULL DEFAULT 'pending',
                    processed_by_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    processed_by_name VARCHAR(50),
                    processed_at      TIMESTAMPTZ,
                    notes             TEXT,
                    created_at        TIMESTAMPTZ DEFAULT now()
                );
                CREATE INDEX IF NOT EXISTS ix_refunds_order_number ON refunds(order_number);
                CREATE INDEX IF NOT EXISTS ix_refunds_status       ON refunds(status);
                CREATE INDEX IF NOT EXISTS ix_refunds_created_at   ON refunds(created_at);
            """))
            _conn.commit()
        logger.info("Schema migration (cashier POS columns + refunds) complete.")
    except Exception as _exc:
        logger.warning("Schema migration (cashier POS) skipped: %s", _exc)

    # Order table: discount columns for promo code support
    try:
        with engine.connect() as _conn:
            _conn.execute(text("""
                ALTER TABLE orders
                    ADD COLUMN IF NOT EXISTS promo_code        VARCHAR(50),
                    ADD COLUMN IF NOT EXISTS discount_amount   FLOAT DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS original_amount   FLOAT;
            """))
            _conn.commit()
        logger.info("Schema migration (orders promo columns) complete.")
    except Exception as _exc:
        logger.warning("Schema migration (orders promo columns) skipped or failed: %s", _exc)

    db = SessionLocal()
    try:
        _seed_default_accounts(db)
        _migrate_menu_to_chaofan(db)
        _seed_sample_menu(db)
        _seed_sample_ingredients(db)
    except Exception as exc:
        logger.error("Seeding error: %s", exc)
        db.rollback()
    finally:
        db.close()

    logger.info("Application ready.")
    yield
    logger.info("Shutting down.")


# ─── App factory ──────────────────────────────────────────────────────────────

app = FastAPI(
    title       = os.getenv("RESTAURANT_NAME", "Taste of Ethiopia") + " — Ordering System",
    description = "Production-ready food ordering system for Ethiopian restaurants",
    version     = "1.0.0",
    lifespan    = lifespan,
    # Security: disable interactive API docs in production.
    # Exposed docs let attackers map every endpoint, schema, and parameter.
    # Set ENVIRONMENT=production in .env when deploying publicly.
    docs_url    = "/api/docs"         if not IS_PRODUCTION else None,
    redoc_url   = "/api/redoc"        if not IS_PRODUCTION else None,
    openapi_url = "/api/openapi.json" if not IS_PRODUCTION else None,
)

# ── Rate limiting ────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── Security headers ─────────────────────────────────────────────────────────
# Must be added BEFORE CORS so headers appear on all responses including preflight.
app.add_middleware(SecurityHeadersMiddleware)

# ── CORS ─────────────────────────────────────────────────────────────────────
_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:8000").split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins     = _origins,
    allow_credentials = True,
    # Security: only allow the HTTP methods actually used by this API.
    # ["*"] would allow DELETE/TRACE/CONNECT from cross-origin — not needed.
    allow_methods     = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    # Security: only allow the headers the frontend actually sends.
    allow_headers     = ["Authorization", "Content-Type", "Accept", "X-Requested-With"],
)

# ── Routers ──────────────────────────────────────────────────────────────────
from routes.auth_routes     import router as auth_router
from routes.customer_routes import router as customer_router
from routes.kitchen_routes  import router as kitchen_router
from routes.admin_routes    import router as admin_router
from routes.cashier_routes  import router as cashier_router
from routes.loyalty_routes  import router as loyalty_router
from routes.review_routes   import router as review_router
from routes.payment_routes  import router as payment_router

app.include_router(auth_router)
app.include_router(customer_router)
app.include_router(kitchen_router)
app.include_router(admin_router)
app.include_router(cashier_router)
app.include_router(loyalty_router)
app.include_router(review_router)
app.include_router(payment_router)


# ── WebSocket endpoints ───────────────────────────────────────────────────────
from fastapi import WebSocket, WebSocketDisconnect
from ws_manager import ws_manager


@app.websocket("/ws/kitchen")
async def ws_kitchen(websocket: WebSocket):
    """Kitchen display — receives new_order, status_change events."""
    await ws_manager.connect(websocket, "kitchen")
    try:
        while True:
            await websocket.receive_text()  # keep alive; clients send pings
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, "kitchen")


@app.websocket("/ws/cashier")
async def ws_cashier(websocket: WebSocket):
    """Cashier POS — receives payment_confirmed, bank_transfer_claim events."""
    await ws_manager.connect(websocket, "cashier")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, "cashier")


@app.websocket("/ws/track/{order_number}")
async def ws_track(websocket: WebSocket, order_number: str):
    """Per-order tracking — client at /track/{order_number} subscribes here."""
    room = f"track:{order_number}"
    await ws_manager.connect(websocket, room)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, room)

# ── Static frontend ──────────────────────────────────────────────────────────
_frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(_frontend_dir):
    app.mount("/frontend", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
    logger.info("Serving frontend from %s", _frontend_dir)


# ── Root redirect ────────────────────────────────────────────────────────────
@app.get("/", include_in_schema=False)
def root():
    return FileResponse(os.path.join(_frontend_dir, "customer.html"))


@app.get("/kitchen", include_in_schema=False)
def kitchen_page():
    return FileResponse(os.path.join(_frontend_dir, "kitchen.html"))


@app.get("/admin-panel", include_in_schema=False)
def admin_page():
    return FileResponse(os.path.join(_frontend_dir, "admin.html"))


@app.get("/verify", include_in_schema=False)
def verify_page():
    return FileResponse(os.path.join(_frontend_dir, "verify.html"))


@app.get("/cashier-pos", include_in_schema=False)
def cashier_pos_page():
    return FileResponse(os.path.join(_frontend_dir, "cashier.html"))


@app.get("/login", include_in_schema=False)
def login_page():
    return FileResponse(os.path.join(_frontend_dir, "login.html"))


@app.get("/track/{order_number}", include_in_schema=False)
def track_page(order_number: str):
    return FileResponse(os.path.join(_frontend_dir, "track.html"))


# ── Health check ─────────────────────────────────────────────────────────────
@app.get("/health", tags=["Health"])
def health():
    return {"status": "healthy", "service": "Food Ordering System", "version": "1.0.0"}


# ── Dev entry point ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host    = "0.0.0.0",
        port    = int(os.getenv("PORT", "8000")),
        reload  = True,
        workers = 1,
    )
