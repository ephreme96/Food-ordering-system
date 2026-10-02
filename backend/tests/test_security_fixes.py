"""
Regression tests for the October 2026 security review fixes.

Runs the app in-process (no server needed) against the database in DATABASE_URL.
Use a throwaway database: the tests create orders, users and refunds.

    cd backend
    python -m pytest tests/test_security_fixes.py -v
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import payment_telebirr  # noqa: E402
import totp  # noqa: E402
from auth import create_access_token, get_password_hash  # noqa: E402
from database import SessionLocal  # noqa: E402
from models import (  # noqa: E402
    LoyaltyAccount, MenuIngredient, MenuItem, Order, OrderStatus,
    PromoCode, Review, RewardItem, User, UserRole,
)
from routes import (  # noqa: E402
    auth_routes, cashier_routes, customer_routes, loyalty_routes,
    payment_routes, review_routes,
)

FRONTEND = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")


@pytest.fixture(scope="module")
def client():
    # Per-route rate limits would trip during a fast test run; they are
    # separate from what is being tested here.
    for mod in (auth_routes, cashier_routes, customer_routes, loyalty_routes,
                payment_routes, review_routes):
        mod.limiter.enabled = False
    main.limiter.enabled = False
    with TestClient(main.app) as c:
        yield c


def _phone() -> str:
    return "09" + uuid.uuid4().int.__str__()[:8]


def _make_user(role: UserRole, password: str = "Str0ng!Passw0rd") -> User:
    db = SessionLocal()
    try:
        u = User(username=f"t_{role.value}_{uuid.uuid4().hex[:6]}",
                 email=f"{uuid.uuid4().hex[:8]}@test.local",
                 password_hash=get_password_hash(password), role=role)
        db.add(u)
        db.commit()
        db.refresh(u)
        return u
    finally:
        db.close()


def _auth(user: User) -> dict:
    token = create_access_token({"sub": user.username, "role": user.role.value,
                                 "ver": user.token_version or 0})
    return {"Authorization": f"Bearer {token}"}


def _menu_item_id(name: str = "Egg Chaofan") -> int:
    db = SessionLocal()
    try:
        return db.query(MenuItem).filter(MenuItem.name == name).first().id
    finally:
        db.close()


def _required_options(item_id: int) -> list[int]:
    """Pick the default choice of every required option group."""
    db = SessionLocal()
    try:
        ings = db.query(MenuIngredient).filter(MenuIngredient.menu_item_id == item_id,
                                               MenuIngredient.is_required == True,
                                               MenuIngredient.is_default == True).all()
        return [i.id for i in ings]
    finally:
        db.close()


def _order(client, method="cash", phone=None, promo=None, qty=2):
    item = _menu_item_id()
    body = {
        "customer_name": "Test Customer",
        "customer_phone": phone,
        "items": [{"menu_item_id": item, "quantity": qty, "customizations": _required_options(item)}],
        "payment_method": method,
        "promo_code": promo,
    }
    r = client.post("/api/orders", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _mark_paid(client, order, admin):
    r = client.post(f"/api/admin/orders/{order['id']}/mark-cash-paid", headers=_auth(admin))
    assert r.status_code == 200, r.text
    return r.json()


# ── 1. Loyalty points ─────────────────────────────────────────────────────────

def test_public_earn_endpoint_is_gone(client):
    r = client.post("/api/loyalty/earn", json={"phone": "0911000000", "amount": 1_000_000})
    assert r.status_code in (404, 405)


def test_points_only_after_payment(client):
    admin = _make_user(UserRole.admin)
    phone = _phone()
    order = _order(client, phone=phone)
    assert client.get(f"/api/loyalty/balance?identifier={phone}").json()["points_balance"] == 0

    _mark_paid(client, order, admin)
    expected = int(order["total_amount"] // 10)
    assert client.get(f"/api/loyalty/balance?identifier={phone}").json()["points_balance"] == expected


def test_claim_reserves_and_fulfil_deducts(client):
    admin = _make_user(UserRole.admin)
    phone = _phone()
    _mark_paid(client, _order(client, phone=phone, qty=5), admin)   # 5 x 300 = 1500 ETB -> 150 pts
    db = SessionLocal()
    reward = RewardItem(name=f"Test reward {uuid.uuid4().hex[:4]}", points_required=100, is_active=True)
    db.add(reward)
    db.commit()
    reward_id = reward.id
    db.close()

    r = client.post(f"/api/loyalty/rewards/{reward_id}/claim", json={"identifier": phone})
    assert r.status_code == 200, r.text
    # Points are only reserved: the balance is unchanged until staff hand it over.
    assert client.get(f"/api/loyalty/balance?identifier={phone}").json()["points_balance"] == 150
    # A second claim cannot use the reserved points.
    r2 = client.post(f"/api/loyalty/rewards/{reward_id}/claim", json={"identifier": phone})
    assert r2.status_code == 400

    claims = client.get("/api/admin/rewards/claims?fulfilled=false", headers=_auth(admin)).json()
    claim_id = next(c["id"] for c in claims if c["identifier"] == phone)
    assert client.patch(f"/api/admin/rewards/claims/{claim_id}/fulfill", headers=_auth(admin)).status_code == 200
    assert client.get(f"/api/loyalty/balance?identifier={phone}").json()["points_balance"] == 50


# ── 2. Payment callbacks ──────────────────────────────────────────────────────

def test_telebirr_callback_rejected_without_key(client, monkeypatch):
    monkeypatch.setattr(payment_telebirr, "_APP_KEY", "")
    monkeypatch.setattr(payment_telebirr, "_SANDBOX", False)
    order = _order(client, method="telebirr")
    payload = {"outTradeNo": order["order_number"], "tradeStatus": "SUCCESS",
               "totalAmount": f"{order['total_amount']:.2f}"}
    payload["sign"] = payment_telebirr._sign(payload)   # "signed" with the empty key
    r = client.post("/api/payments/callback/telebirr", json=payload)
    assert r.status_code == 400
    db = SessionLocal()
    assert db.get(Order, order["id"]).status == OrderStatus.PENDING
    db.close()


def test_telebirr_callback_amount_must_match(client, monkeypatch):
    monkeypatch.setattr(payment_telebirr, "_APP_KEY", "real-merchant-key")
    monkeypatch.setattr(payment_telebirr, "_SANDBOX", False)
    order = _order(client, method="telebirr")
    payload = {"outTradeNo": order["order_number"], "tradeStatus": "SUCCESS", "totalAmount": "1.00"}
    payload["sign"] = payment_telebirr._sign(payload)
    r = client.post("/api/payments/callback/telebirr", json=payload)
    assert r.json()["msg"] == "amount_mismatch"

    payload = {"outTradeNo": order["order_number"], "tradeStatus": "SUCCESS",
               "totalAmount": f"{order['total_amount']:.2f}"}
    payload["sign"] = payment_telebirr._sign(payload)
    assert client.post("/api/payments/callback/telebirr", json=payload).json()["msg"] == "success"


def test_initiate_rejects_wrong_method(client):
    order = _order(client, method="cash")
    r = client.post("/api/payments/initiate", json={
        "order_number": order["order_number"], "tx_ref": order["tx_ref"], "method": "telebirr"})
    assert r.status_code == 400


# ── 3. Placeholder secrets ────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [
    "", "change-me", "CHANGE_ME_USE_SECRETS_TOKEN_HEX_64",
    "CHANGE_ME_USE_SECRETS_TOKEN_HEX_32", "your-secret-key-your-secret-key-1234", "short",
])
def test_placeholder_secrets_rejected(value):
    assert main._looks_like_placeholder(value)


def test_real_secret_accepted():
    assert not main._looks_like_placeholder(os.urandom(32).hex())


# ── 4. XSS on the tracking page ───────────────────────────────────────────────

def test_track_page_escapes_customer_name():
    html = open(os.path.join(FRONTEND, "track.html"), encoding="utf-8").read()
    assert "${order.customer_name}" not in html
    assert "${escHtml(order.customer_name)}" in html
    assert "function escHtml" in html


# ── 5. Refund controls ────────────────────────────────────────────────────────

def test_refund_rules(client):
    admin, cashier = _make_user(UserRole.admin), _make_user(UserRole.cashier)
    r = client.post("/api/cashier/refunds", headers=_auth(cashier),
                    json={"order_number": "ORD-FAKE0000", "amount": 100})
    assert r.status_code == 404

    unpaid = _order(client)
    r = client.post("/api/cashier/refunds", headers=_auth(cashier),
                    json={"order_number": unpaid["order_number"], "amount": 10})
    assert r.status_code == 400

    paid = _mark_paid(client, _order(client), admin)
    half = round(paid["total_amount"] / 2, 2)
    first = client.post("/api/cashier/refunds", headers=_auth(cashier),
                        json={"order_number": paid["order_number"], "amount": half})
    assert first.status_code == 200 and first.json()["status"] == "pending"
    assert client.post("/api/cashier/refunds", headers=_auth(cashier),
                       json={"order_number": paid["order_number"], "amount": half}).status_code == 200
    assert client.post("/api/cashier/refunds", headers=_auth(cashier),
                       json={"order_number": paid["order_number"], "amount": 1}).status_code == 400

    rid = first.json()["id"]
    assert client.patch(f"/api/cashier/refunds/{rid}/mark-processed", headers=_auth(cashier)).status_code == 403
    assert client.patch(f"/api/cashier/refunds/{rid}/mark-processed", headers=_auth(admin)).status_code == 200


# ── 6. POS input validation ───────────────────────────────────────────────────

def test_pos_rejects_bad_input(client):
    cashier = _make_user(UserRole.cashier)
    item = _menu_item_id()
    neg = client.post("/api/cashier/pos/charge", headers=_auth(cashier),
                      json={"items": [{"menu_item_id": item, "quantity": -3}]})
    assert neg.status_code == 422

    other_item_option = _required_options(_menu_item_id("Beef Chaofan"))[0]
    foreign = client.post("/api/cashier/pos/charge", headers=_auth(cashier),
                          json={"items": [{"menu_item_id": item, "quantity": 1,
                                           "customization_ids": [other_item_option]}]})
    assert foreign.status_code == 400

    ok = client.post("/api/cashier/pos/charge", headers=_auth(cashier),
                     json={"items": [{"menu_item_id": item, "quantity": 1}], "tax_rate": -100})
    assert ok.status_code == 200, ok.text
    assert ok.json()["total_amount"] > 0   # tax_rate from the browser is ignored


# ── 7. Review moderation ──────────────────────────────────────────────────────

def test_reviews_wait_for_approval(client):
    name = f"Reviewer {uuid.uuid4().hex[:6]}"
    r = client.post("/api/reviews", json={"reviewer_name": name, "rating": 5, "comment": "great"})
    assert r.status_code == 201
    body = r.json()
    listed = [x["reviewer_name"] for x in client.get("/api/reviews?limit=100").json()]
    assert name not in listed

    db = SessionLocal()
    db.get(Review, body["id"]).is_approved = True
    db.commit()
    db.close()
    client.put(f"/api/reviews/{body['id']}?token={body['edit_token']}",
               json={"reviewer_name": name, "rating": 1, "comment": "edited"})
    listed = [x["reviewer_name"] for x in client.get("/api/reviews?limit=100").json()]
    assert name not in listed   # editing hides it again


# ── 8. Lockout is per username + IP ───────────────────────────────────────────

def test_lockout_does_not_block_other_ips():
    user = _make_user(UserRole.admin, password="Right!Pass123")
    attacker = TestClient(main.app, client=("203.0.113.9", 5000))
    owner    = TestClient(main.app, client=("198.51.100.7", 5000))
    auth_routes.limiter.enabled = False
    for _ in range(10):
        attacker.post("/api/auth/login", json={"username": user.username, "password": "wrong"})
    assert attacker.post("/api/auth/login", json={"username": user.username,
                                                  "password": "wrong"}).status_code == 429
    assert owner.post("/api/auth/login", json={"username": user.username,
                                               "password": "Right!Pass123"}).status_code == 200


# ── 10. Promo uses counted only when paid ─────────────────────────────────────

def test_promo_counted_on_payment(client):
    admin = _make_user(UserRole.admin)
    code = f"T{uuid.uuid4().hex[:6].upper()}"
    db = SessionLocal()
    db.add(PromoCode(code=code, discount_type="percent", discount_value=10, max_uses=5))
    db.commit()
    db.close()

    order = _order(client, promo=code)
    db = SessionLocal()
    assert db.query(PromoCode).filter(PromoCode.code == code).first().uses_count == 0
    db.close()
    _mark_paid(client, order, admin)
    db = SessionLocal()
    assert db.query(PromoCode).filter(PromoCode.code == code).first().uses_count == 1
    db.close()


# ── 12. Global rate limit middleware is installed ─────────────────────────────

def test_slowapi_middleware_installed():
    from slowapi.middleware import SlowAPIMiddleware
    assert any(m.cls is SlowAPIMiddleware for m in main.app.user_middleware)


# ── 13. Password change logs out old sessions ─────────────────────────────────

def test_password_change_revokes_old_tokens(client):
    user = _make_user(UserRole.kitchen, password="Old!Passw0rd1")
    old = _auth(user)
    assert client.get("/api/auth/me", headers=old).status_code == 200
    r = client.patch("/api/auth/change-password", headers=old,
                     json={"current_password": "Old!Passw0rd1", "new_password": "New!Passw0rd2"})
    assert r.status_code == 200
    assert client.get("/api/auth/me", headers=old).status_code == 401
    fresh = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get("/api/auth/me", headers=fresh).status_code == 200


# ── 15. Two-step login ────────────────────────────────────────────────────────

def test_two_step_login(client):
    user = _make_user(UserRole.admin, password="Two!Step1234")
    hdr = _auth(user)
    secret = client.post("/api/auth/2fa/setup", headers=hdr).json()["secret"]
    assert client.post("/api/auth/2fa/enable", headers=hdr, json={"code": "000000"}).status_code == 400
    r = client.post("/api/auth/2fa/enable", headers=hdr, json={"code": totp.current_code(secret)})
    assert r.status_code == 200

    creds = {"username": user.username, "password": "Two!Step1234"}
    no_code = client.post("/api/auth/login", json=creds)
    assert no_code.status_code == 401 and no_code.json()["detail"] == "OTP_REQUIRED"
    assert client.post("/api/auth/login", json={**creds, "otp": "123456"}).status_code == 401
    ok = client.post("/api/auth/login", json={**creds, "otp": totp.current_code(secret)})
    assert ok.status_code == 200


def test_totp_matches_rfc6238_vector():
    # RFC 6238 appendix B, SHA-1, T=59 -> 94287082 (last 6 digits: 287082)
    import base64
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert totp.current_code(secret, at=59) == "287082"
