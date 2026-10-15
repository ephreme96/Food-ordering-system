"""
Receipt tests for Telebirr / CBE Birr payments.

The mobile app only shows the pickup QR when it receives receipt_token, so
both the sandbox auto-pay response and the status poll must return it.

Runs without a live server, against a throwaway SQLite database:
    cd backend
    python -m pytest tests/test_payment_receipt.py -v
"""
import os
import sys
import tempfile

# database.py builds a pooled engine at import time; point it at a file so the
# pool arguments are accepted. Tests use their own in-memory engine below.
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.gettempdir(), "receipt_test_unused.db")
os.environ["PAYMENT_SANDBOX"] = "true"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from database import Base, get_db  # noqa: E402
from models import Order, OrderStatus, PaymentMethod  # noqa: E402
from receipt import verify_receipt_token  # noqa: E402
from routes import payment_routes  # noqa: E402

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(bind=engine, autoflush=False)


def _override_get_db():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


app = FastAPI()
app.state.limiter = payment_routes.limiter
app.include_router(payment_routes.router)
app.dependency_overrides[get_db] = _override_get_db
client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    payment_routes.limiter.reset()


def _make_order(method=PaymentMethod.telebirr, number="ORD-1001", tx_ref="tx-1001"):
    db = TestingSession()
    db.add(Order(
        order_number=number, customer_name="Abebe", items_snapshot=[],
        total_amount=250.0, payment_method=method, tx_ref=tx_ref,
    ))
    db.commit()
    db.close()


def _set_status(status, number="ORD-1001"):
    db = TestingSession()
    db.query(Order).filter(Order.order_number == number).update({"status": status})
    db.commit()
    db.close()


@pytest.mark.parametrize("method", ["telebirr", "cbebirr"])
def test_sandbox_paid_returns_valid_receipt_token(method):
    _make_order(PaymentMethod(method))
    res = client.post("/api/payments/initiate", json={
        "order_number": "ORD-1001", "tx_ref": "tx-1001", "method": method,
    })
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "sandbox_paid"
    assert body["receipt_code"]
    ok, reason, payload = verify_receipt_token(body["receipt_token"])
    assert ok, reason
    assert payload["rc"] == body["receipt_code"]
    assert payload["on"] == "ORD-1001"


def test_status_poll_returns_receipt_token_after_payment():
    _make_order()
    client.post("/api/payments/initiate", json={
        "order_number": "ORD-1001", "tx_ref": "tx-1001", "method": "telebirr",
    })
    body = client.get("/api/payments/status/ORD-1001", params={"tx_ref": "tx-1001"}).json()
    assert body["paid"] is True
    assert body["receipt_code"]
    assert verify_receipt_token(body["receipt_token"])[0]


@pytest.mark.parametrize("status", [OrderStatus.PREPARING, OrderStatus.READY])
def test_status_poll_still_paid_once_kitchen_starts(status):
    _make_order()
    client.post("/api/payments/initiate", json={
        "order_number": "ORD-1001", "tx_ref": "tx-1001", "method": "telebirr",
    })
    _set_status(status)
    body = client.get("/api/payments/status/ORD-1001", params={"tx_ref": "tx-1001"}).json()
    assert body["paid"] is True
    assert body["receipt_token"]


def test_status_poll_hides_receipt_before_payment_and_after_pickup():
    _make_order()
    body = client.get("/api/payments/status/ORD-1001", params={"tx_ref": "tx-1001"}).json()
    assert body["paid"] is False
    assert body["receipt_code"] is None and body["receipt_token"] is None

    client.post("/api/payments/initiate", json={
        "order_number": "ORD-1001", "tx_ref": "tx-1001", "method": "telebirr",
    })
    _set_status(OrderStatus.PICKED_UP)
    body = client.get("/api/payments/status/ORD-1001", params={"tx_ref": "tx-1001"}).json()
    assert body["paid"] is True
    assert body["receipt_code"] is None and body["receipt_token"] is None


def test_status_poll_requires_matching_tx_ref():
    _make_order()
    res = client.get("/api/payments/status/ORD-1001", params={"tx_ref": "wrong"})
    assert res.status_code == 404
