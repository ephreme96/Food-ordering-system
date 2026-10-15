"""
Regression tests for the 9 issues logged for later in the October 2026
security report (the "tech debt" batch).

Runs the app in-process against the database in DATABASE_URL. Use a throwaway
database: the tests create users, orders, rewards and refunds.

    cd backend
    python -m pytest tests/test_tech_debt_fixes.py -v
"""
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient  # noqa: E402

import database  # noqa: E402
import main  # noqa: E402
import totp  # noqa: E402
from auth import SESSION_COOKIE_NAME  # noqa: E402
from database import SessionLocal  # noqa: E402
from models import (  # noqa: E402
    PointTransaction, Refund, RefundStatus, RewardClaim, RewardItem, UserRole,
)
from routes import auth_routes, payment_routes  # noqa: E402

from tests.test_security_fixes import (  # noqa: E402
    _auth, _make_user, _mark_paid, _order, _phone, client,  # noqa: F401  (client is a fixture)
)

FRONTEND = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")


# ── 1. Database driver ────────────────────────────────────────────────────────

def test_database_url_names_psycopg2_driver():
    assert database.engine.url.drivername == "postgresql+psycopg2"


# ── 2. Loyalty history escaped ────────────────────────────────────────────────

def test_customer_page_escapes_loyalty_history():
    html = open(os.path.join(FRONTEND, "customer.html"), encoding="utf-8").read()
    assert "${escHtml(t.description||'')}" in html
    assert "${t.description||''}" not in html


# ── 3. Lockout lasts the full 15 minutes ──────────────────────────────────────

def test_lockout_lasts_full_duration(monkeypatch):
    key = f"user_{uuid.uuid4().hex[:6]}|203.0.113.50"
    clock = [1_000_000.0]
    monkeypatch.setattr(auth_routes.time, "time", lambda: clock[0])
    for _ in range(auth_routes._LOCKOUT_MAX_ATTEMPTS):
        auth_routes._record_failed_login(key)
    # 6 minutes later: the old code had already unlocked by now.
    clock[0] += 360
    with pytest.raises(Exception) as e:
        auth_routes._check_account_lockout(key)
    assert getattr(e.value, "status_code", None) == 429
    # After 15 minutes it opens again.
    clock[0] += auth_routes._LOCKOUT_DURATION
    auth_routes._check_account_lockout(key)


# ── 4. 2-step codes can't be replayed ─────────────────────────────────────────

def test_totp_code_cannot_be_reused(client):
    user = _make_user(UserRole.admin, password="Replay!Pass123")
    hdr = _auth(user)
    secret = client.post("/api/auth/2fa/setup", headers=hdr).json()["secret"]
    assert client.post("/api/auth/2fa/enable", headers=hdr,
                       json={"code": totp.current_code(secret)}).status_code == 200
    creds = {"username": user.username, "password": "Replay!Pass123"}
    nxt = totp.current_code(secret, at=time.time() + 30)
    assert client.post("/api/auth/login", json={**creds, "otp": nxt}).status_code == 200
    # Same code again (someone watched it being typed): refused.
    assert client.post("/api/auth/login", json={**creds, "otp": nxt}).status_code == 401
    # An older code (the one used to enable) is refused too.
    assert client.post("/api/auth/login", json={**creds, "otp": totp.current_code(secret)}).status_code == 401


# ── 5. Tracking page hides the full name ──────────────────────────────────────

@pytest.mark.parametrize("full,shown", [
    ("Abebe Kebede", "Abebe K."),
    ("Abebe Kebede Tadesse", "Abebe T."),
    ("Abebe", "Abebe"),
    ("", "Customer"),
    (None, "Customer"),
])
def test_public_name(full, shown):
    assert payment_routes._public_name(full) == shown


def test_track_endpoint_masks_name(client):
    order = _order(client)   # customer_name "Test Customer"
    data = client.get(f"/api/orders/track/{order['order_number']}").json()
    assert data["customer_name"] == "Test C."


# ── 6. Public loyalty history leaks nothing that identifies orders ───────────

def test_history_hides_order_numbers(client):
    admin = _make_user(UserRole.admin)
    phone = _phone()
    order = _order(client, phone=phone)
    _mark_paid(client, order, admin)
    hist = client.get(f"/api/loyalty/history?identifier={phone}").json()
    assert hist and hist[0]["type"] == "earn"
    assert hist[0]["order_number"] is None
    assert order["order_number"] not in str(hist)
    assert "ETB" not in str(hist)


# ── 7. Uncollected claims give stock back ─────────────────────────────────────

def _reward(limit=1):
    db = SessionLocal()
    r = RewardItem(name=f"Limited {uuid.uuid4().hex[:4]}", points_required=100,
                   is_active=True, quantity_limit=limit)
    db.add(r)
    db.commit()
    rid = r.id
    db.close()
    return rid


def _quantity_claimed(rid):
    db = SessionLocal()
    try:
        return db.get(RewardItem, rid).quantity_claimed
    finally:
        db.close()


def test_admin_can_cancel_claim_and_stock_returns(client):
    admin = _make_user(UserRole.admin)
    phone = _phone()
    _mark_paid(client, _order(client, phone=phone, qty=5), admin)   # 150 pts
    rid = _reward(limit=1)
    assert client.post(f"/api/loyalty/rewards/{rid}/claim", json={"identifier": phone}).status_code == 200
    assert _quantity_claimed(rid) == 1

    claims = client.get("/api/admin/rewards/claims", headers=_auth(admin)).json()
    cid = next(c["id"] for c in claims if c["identifier"] == phone)
    assert client.patch(f"/api/admin/rewards/claims/{cid}/cancel", headers=_auth(admin)).status_code == 200
    assert _quantity_claimed(rid) == 0
    # A cancelled claim can't be handed over, and the customer can claim again.
    assert client.patch(f"/api/admin/rewards/claims/{cid}/fulfill", headers=_auth(admin)).status_code == 400
    assert client.post(f"/api/loyalty/rewards/{rid}/claim", json={"identifier": phone}).status_code == 200
    # Only admins may cancel.
    cashier = _make_user(UserRole.cashier)
    assert client.patch(f"/api/admin/rewards/claims/{cid}/cancel", headers=_auth(cashier)).status_code == 403


def test_stale_claims_expire(client):
    admin = _make_user(UserRole.admin)
    phone = _phone()
    _mark_paid(client, _order(client, phone=phone, qty=5), admin)
    rid = _reward(limit=1)
    assert client.post(f"/api/loyalty/rewards/{rid}/claim", json={"identifier": phone}).status_code == 200
    db = SessionLocal()
    claim = db.query(RewardClaim).filter(RewardClaim.reward_id == rid).first()
    claim.created_at = datetime.now(timezone.utc) - timedelta(days=8)
    db.commit()
    db.close()
    client.get("/api/loyalty/rewards")      # any read releases expired claims
    assert _quantity_claimed(rid) == 0


# ── 8. Cash flow only subtracts approved refunds ──────────────────────────────

def test_cash_flow_ignores_pending_refunds(client):
    admin = _make_user(UserRole.admin)
    before = client.get("/api/cashier/cash-flow", headers=_auth(admin)).json()
    db = SessionLocal()
    db.add(Refund(order_number="PENDTEST", amount=77.0, refund_method="cash",
                  status=RefundStatus.pending))
    db.add(Refund(order_number="DONETEST", amount=11.0, refund_method="cash",
                  status=RefundStatus.processed))
    db.commit()
    db.close()
    after = client.get("/api/cashier/cash-flow", headers=_auth(admin)).json()
    assert round(after["total_refunds"] - before["total_refunds"], 2) == 11.0
    assert round(after["pending_refunds_total"] - before["pending_refunds_total"], 2) == 77.0
    assert round(before["net_revenue"] - after["net_revenue"], 2) == 11.0


# ── 9. Login lives in an HttpOnly cookie ──────────────────────────────────────

def test_login_sets_httponly_cookie_and_cookie_authenticates(client):
    user = _make_user(UserRole.kitchen, password="Cookie!Pass123")
    c = TestClient(main.app)
    r = c.post("/api/auth/login", json={"username": user.username, "password": "Cookie!Pass123"})
    assert r.status_code == 200
    set_cookie = r.headers["set-cookie"]
    assert SESSION_COOKIE_NAME in set_cookie
    assert "HttpOnly" in set_cookie and "SameSite=strict" in set_cookie
    # No Authorization header: the cookie alone logs the browser in.
    assert c.get("/api/auth/me").status_code == 200
    # Logout removes it.
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/auth/me").status_code == 401


def test_cookie_request_from_other_site_blocked(client):
    user = _make_user(UserRole.kitchen, password="Cookie!Pass123")
    c = TestClient(main.app)
    c.post("/api/auth/login", json={"username": user.username, "password": "Cookie!Pass123"})
    body = {"current_password": "Cookie!Pass123", "new_password": "Other!Pass456"}
    evil = c.patch("/api/auth/change-password", json=body, headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403
    same = c.patch("/api/auth/change-password", json=body, headers={"Origin": "http://testserver"})
    assert same.status_code == 200


def test_ws_accepts_cookie_and_rejects_foreign_origin(client):
    user = _make_user(UserRole.kitchen, password="Cookie!Pass123")
    c = TestClient(main.app)
    c.post("/api/auth/login", json={"username": user.username, "password": "Cookie!Pass123"})
    with c.websocket_connect("/ws/kitchen") as ws:
        assert "authenticated" in ws.receive_text()
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws/kitchen", headers={"Origin": "https://evil.example"}) as ws:
            ws.receive_text()


def test_staff_pages_no_longer_store_token():
    for page in ("admin", "cashier", "kitchen", "verify", "login"):
        html = open(os.path.join(FRONTEND, f"{page}.html"), encoding="utf-8").read()
        assert "setItem('token'" not in html, page
        assert "Bearer ${" not in html, page
