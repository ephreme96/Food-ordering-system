"""
Authorization tests — verify role checks are enforced SERVER-SIDE.

Confirms:
  1. Unauthenticated requests (a customer has no account/token) are rejected
     with 401 on every staff endpoint.
  2. A forged/garbage token is rejected with 401.
  3. A kitchen token cannot reach cashier or admin endpoints (403).
  4. A cashier token cannot reach admin-only or kitchen endpoints (403).
  5. The live kitchen/cashier WebSocket feeds reject missing, forged and
     wrong-role tokens, and accept the right staff role.

Run against a live server:
    cd backend
    python -m pytest tests/test_authz.py -v
Requires the server running on BASE_URL (default http://localhost:8000)
and the default seeded accounts (kitchen_staff / cashier1) to exist.
Tokens are minted locally with the same SECRET_KEY the server uses (.env),
so no passwords are needed.
"""
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

from auth import create_access_token  # noqa: E402

BASE_URL = os.getenv("TEST_BASE_URL", "http://localhost:8000")

# Endpoints that must NEVER be reachable without the right role.
ADMIN_ENDPOINTS = [
    ("GET",  "/api/admin/orders"),
    ("GET",  "/api/admin/analytics"),
    ("GET",  "/api/admin/audit"),
    ("GET",  "/api/admin/cashier-config"),
    ("GET",  "/api/auth/users"),
    ("POST", "/api/auth/register"),
]
CASHIER_ENDPOINTS = [
    ("GET",  "/api/cashier/orders/cash-pending"),
    ("GET",  "/api/cashier/cash-flow"),
    ("GET",  "/api/cashier/refunds"),
    ("POST", "/api/cashier/refunds"),
    ("POST", "/api/cashier/pos/charge"),
    ("GET",  "/api/cashier/menu"),
]
KITCHEN_ENDPOINTS = [
    ("GET",   "/api/kitchen/orders"),
    ("PATCH", "/api/kitchen/orders/1/status"),
]
ALL_STAFF_ENDPOINTS = ADMIN_ENDPOINTS + CASHIER_ENDPOINTS + KITCHEN_ENDPOINTS


def _request(method: str, path: str, token: str | None = None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(base_url=BASE_URL, timeout=10) as client:
        return client.request(method, path, headers=headers, json={})


def _token_for(username: str) -> str:
    return create_access_token({"sub": username})


# ── 1. No token (anonymous customer) ─────────────────────────────────────────

@pytest.mark.parametrize("method,path", ALL_STAFF_ENDPOINTS)
def test_anonymous_rejected(method, path):
    r = _request(method, path)
    assert r.status_code == 401, f"{method} {path} allowed anonymous access: {r.status_code}"


# ── 2. Garbage / forged token ─────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", ALL_STAFF_ENDPOINTS)
def test_garbage_token_rejected(method, path):
    r = _request(method, path, token="not.a.real.jwt")
    assert r.status_code == 401, f"{method} {path} accepted a garbage token: {r.status_code}"


# ── 3. Kitchen token must not reach cashier/admin endpoints ──────────────────

@pytest.mark.parametrize("method,path", ADMIN_ENDPOINTS + CASHIER_ENDPOINTS)
def test_kitchen_cannot_cross_roles(method, path):
    r = _request(method, path, token=_token_for("kitchen_staff"))
    assert r.status_code == 403, (
        f"kitchen token got {r.status_code} on {method} {path} (expected 403)"
    )


# ── 4. Cashier token must not reach admin-only or kitchen endpoints ──────────

CASHIER_FORBIDDEN = ADMIN_ENDPOINTS + KITCHEN_ENDPOINTS

@pytest.mark.parametrize("method,path", CASHIER_FORBIDDEN)
def test_cashier_cannot_cross_roles(method, path):
    r = _request(method, path, token=_token_for("cashier1"))
    assert r.status_code == 403, (
        f"cashier token got {r.status_code} on {method} {path} (expected 403)"
    )


# ── 5. Live kitchen/cashier feeds must require a staff token ─────────────────
# The client sends its JWT as the first message; the server closes with 1008
# (policy violation) for anything else.

from websockets.exceptions import ConnectionClosed  # noqa: E402
from websockets.sync.client import connect as ws_connect  # noqa: E402

WS_BASE_URL = BASE_URL.replace("http", "ws", 1)


def _ws_first_reply(path: str, token: str | None) -> str | int:
    """Return the server's first message, or the close code if it hangs up."""
    with ws_connect(WS_BASE_URL + path, open_timeout=10) as ws:
        try:
            if token is not None:
                ws.send(token)
            return ws.recv(timeout=10)
        except ConnectionClosed as exc:
            return exc.rcvd.code if exc.rcvd else -1


@pytest.mark.parametrize("path", ["/ws/kitchen", "/ws/cashier"])
@pytest.mark.parametrize("token", ["", "not.a.real.jwt"])
def test_ws_feed_rejects_bad_token(path, token):
    assert _ws_first_reply(path, token) == 1008


@pytest.mark.parametrize("path,username", [
    ("/ws/kitchen", "cashier1"),
    ("/ws/cashier", "kitchen_staff"),
])
def test_ws_feed_rejects_wrong_role(path, username):
    assert _ws_first_reply(path, _token_for(username)) == 1008


@pytest.mark.parametrize("path,username", [
    ("/ws/kitchen", "kitchen_staff"),
    ("/ws/cashier", "cashier1"),
])
def test_ws_feed_accepts_staff(path, username):
    assert '"authenticated"' in _ws_first_reply(path, _token_for(username))
