# 🍽️ Taste of Ethiopia — Food Ordering System

A full production-ready food ordering and delivery web application for Ethiopian restaurants.

---

## Features

| Interface | Description |
|---|---|
| **Customer** | Browse menu, add to cart, pay online (Chapa) or cash |
| **Kitchen** | Live order feed, status updates (Paid → Preparing → Ready → Completed) |
| **Admin** | Menu CRUD, order management, cash payment confirmation, analytics, staff management |
| **Cashier / Verifier** | QR scan or receipt code entry, anti-fraud single-redemption system |

---

## Tech Stack

- **Backend:** Python 3.11 · FastAPI · SQLAlchemy ORM
- **Database:** PostgreSQL
- **Auth:** JWT (HS256) · bcrypt passwords
- **Payment:** Chapa (Ethiopia) — swap `payment_chapa.py` for any other provider
- **Frontend:** HTML · Tailwind CSS · Vanilla JS
- **Rate limiting:** slowapi
- **Deployment:** Render / Railway / any Linux VPS

---

## Project Structure

```
Food ordering system/
├── backend/
│   ├── main.py               ← FastAPI app, seeding, static serving
│   ├── database.py           ← SQLAlchemy engine + session
│   ├── models.py             ← DB models (User, MenuItem, Order)
│   ├── schemas.py            ← Pydantic request/response schemas
│   ├── auth.py               ← JWT creation, password hashing, role guards
│   ├── payment_chapa.py      ← Chapa API integration (swap for another provider here)
│   ├── receipt.py            ← HMAC-signed receipt token generation & verification
│   └── routes/
│       ├── auth_routes.py    ← /api/auth/login, /register, /me
│       ├── customer_routes.py← /api/menu, /api/orders, /api/payment/*
│       ├── kitchen_routes.py ← /api/kitchen/orders (JWT-protected)
│       ├── admin_routes.py   ← /api/admin/menu, /orders, /analytics
│       └── cashier_routes.py ← /api/cashier/verify (rate-limited)
├── frontend/
│   ├── customer.html         ← Public ordering page
│   ├── kitchen.html          ← Kitchen dashboard (auto-polls every 15s)
│   ├── admin.html            ← Admin panel (menu, orders, staff, analytics)
│   ├── verify.html           ← Cashier receipt verifier (QR scan + manual)
│   └── login.html            ← Staff login
├── .env                      ← Your secrets (never commit this)
├── .env.example              ← Template — copy to .env and fill in
├── requirements.txt
└── README.md
```

---

## Quick Start (Local)

### 1. Prerequisites

- Python 3.10+
- PostgreSQL running locally
- Git

### 2. Clone / set up

```bash
cd "Food ordering system"

# Create virtual environment
python -m venv venv

# Activate it
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Create the database

```sql
-- In psql or pgAdmin:
CREATE DATABASE food_ordering;
```

### 4. Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in at minimum:

```env
DATABASE_URL=postgresql://postgres:yourpassword@localhost:5432/food_ordering
SECRET_KEY=<generate with: python -c "import secrets; print(secrets.token_hex(64))">
RECEIPT_HMAC_SECRET=<generate with: python -c "import secrets; print(secrets.token_hex(32))">
CHAPA_SECRET_KEY=CHASECK_TEST-your-test-key-from-dashboard.chapa.co
BASE_URL=http://localhost:8000
FRONTEND_URL=http://localhost:8000
```

### 5. Run the server

```bash
cd backend
python main.py
```

Or with uvicorn directly:

```bash
cd backend
uvicorn main:app --reload --port 8000
```

On first start the server automatically:
- Creates all database tables
- Seeds 3 default staff accounts
- Seeds 19 sample Ethiopian menu items

### 6. Open in browser

| URL | Description |
|---|---|
| `http://localhost:8000` | Customer ordering page |
| `http://localhost:8000/kitchen` | Kitchen dashboard |
| `http://localhost:8000/admin-panel` | Admin panel |
| `http://localhost:8000/verify` | Receipt verifier |
| `http://localhost:8000/login` | Staff login |
| `http://localhost:8000/api/docs` | Interactive API docs (Swagger) |

### 7. Default accounts

| Role | Username | Password |
|---|---|---|
| Admin | `admin` | `Admin@1234` |
| Kitchen | `kitchen_staff` | `Kitchen@1234` |
| Cashier | `cashier1` | `Cashier@1234` |

> **Important:** Change all passwords immediately after first login via Admin → Staff panel.

---

## Chapa Payment Integration

1. Sign up at [dashboard.chapa.co](https://dashboard.chapa.co)
2. Copy your **test** secret key (`CHASECK_TEST-…`)
3. Paste it into `.env` as `CHAPA_SECRET_KEY`
4. For production use your **live** key (`CHASECK-…`)
5. In production set `BASE_URL` to your actual domain so Chapa can reach your webhook

### Switching payment providers

All Chapa logic is isolated in `backend/payment_chapa.py`.
To switch providers, replace `initialize_payment()` and `verify_payment()` keeping the same function signatures. No other files need to change.

---

## Anti-Fraud Receipt System

The receipt system is designed to prevent fraud:

1. **Server-side only** — Payment status is never trusted from the frontend
2. **Chapa verification** — Every payment is independently verified via the Chapa API before marking as PAID
3. **HMAC-signed tokens** — Receipts are signed with `RECEIPT_HMAC_SECRET` using HMAC-SHA256
4. **One-time redemption** — Each receipt can only be redeemed once; reuse is logged and rejected
5. **Expiry** — Tokens expire after 7 days (configurable via `RECEIPT_EXPIRY_SECONDS`)
6. **Amount verification** — Verified amount from Chapa must match order total (±0.50 ETB tolerance)
7. **Rate limiting** — `/api/cashier/verify` is limited to 30 requests/minute per IP

---

## Deployment (Render.com — Free Tier)

### Option A: Web Service + Managed PostgreSQL

1. Push your code to a GitHub repo (exclude `.env` — add it to `.gitignore`)
2. Go to [render.com](https://render.com) → New → **Web Service**
3. Connect your GitHub repo
4. Settings:
   - **Root Directory:** `backend`
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r ../requirements.txt`
   - **Start Command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Add a **PostgreSQL** database service on Render
6. Copy the Internal Database URL into your web service's environment variables as `DATABASE_URL`
7. Add all other env vars from `.env.example` in the Render dashboard
8. Set `BASE_URL` and `FRONTEND_URL` to your Render service URL
9. Deploy

### Option B: Railway.app

```bash
# Install Railway CLI
npm install -g @railway/cli

railway login
railway init
railway add postgresql
railway up
```

Set environment variables in the Railway dashboard under your service → Variables.

### Custom Domain

1. In Render/Railway dashboard → Settings → Custom Domains
2. Add your domain (e.g. `orders.mytasteofethiopia.et`)
3. Set the CNAME record in your DNS provider
4. Update `.env` / service env vars:
   ```env
   BASE_URL=https://orders.mytasteofethiopia.et
   FRONTEND_URL=https://orders.mytasteofethiopia.et
   ALLOWED_ORIGINS=https://orders.mytasteofethiopia.et
   ```

---

## API Reference

All protected endpoints require `Authorization: Bearer <token>` header.

### Authentication
| Method | Endpoint | Auth | Description |
|---|---|---|---|
| POST | `/api/auth/login` | None | Login, returns JWT |
| POST | `/api/auth/register` | Admin | Create staff account |
| GET | `/api/auth/me` | Any | Current user info |

### Customer
| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/api/menu` | None | List available menu items |
| GET | `/api/menu/categories` | None | List categories |
| POST | `/api/orders` | None | Place an order |
| GET | `/api/orders/{id}` | None | Get order status |
| POST | `/api/payment/initialize/{order_id}` | None | Init Chapa payment |
| GET | `/api/payment/verify/{tx_ref}` | None | Verify & confirm payment |
| POST | `/api/payment/webhook` | Chapa | Webhook (auto-verify) |

### Kitchen
| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/api/kitchen/orders` | Kitchen/Admin | Active orders |
| PATCH | `/api/kitchen/orders/{id}/status` | Kitchen/Admin | Update order status |

### Admin
| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET/POST | `/api/admin/menu` | Admin | List / create menu items |
| PUT/DELETE | `/api/admin/menu/{id}` | Admin | Update / delete item |
| GET | `/api/admin/orders` | Admin | All orders |
| POST | `/api/admin/orders/{id}/mark-cash-paid` | Admin/Cashier | Confirm cash payment |
| POST | `/api/admin/orders/{id}/cancel` | Admin | Cancel an order |
| GET | `/api/admin/analytics` | Admin | Statistics |

### Cashier
| Method | Endpoint | Auth | Description |
|---|---|---|---|
| POST | `/api/cashier/verify` | Cashier/Admin | Verify & redeem receipt |
| GET | `/api/cashier/orders/cash-pending` | Cashier/Admin | Cash orders list |

---

## Security Checklist (Production)

- [ ] Change all default passwords immediately
- [ ] Generate strong `SECRET_KEY` (64 hex chars)
- [ ] Generate strong `RECEIPT_HMAC_SECRET` (32 hex chars)
- [ ] Use HTTPS only (Render/Railway provide this automatically)
- [ ] Set `ALLOWED_ORIGINS` to your exact frontend domain
- [ ] Use Chapa **live** key, not test key
- [ ] Set `SQL_ECHO=false` in production
- [ ] Back up your PostgreSQL database regularly

---

## License

MIT — free to use, modify, and deploy for your restaurant.
