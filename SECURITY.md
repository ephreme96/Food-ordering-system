# Security & Production Deployment Checklist

## Running in production

**Never use the dev command in production.** Development uses `--reload`; production must not:

```bash
# Production (behind Nginx, localhost bind only):
ENVIRONMENT=production uvicorn main:app --host 127.0.0.1 --port 8000 --workers 4 --app-dir backend
```

- `ENVIRONMENT=production` in `.env` disables the interactive API docs
  (`/api/docs`, `/api/redoc`, `/api/openapi.json`).
- Bind to `127.0.0.1`, not `0.0.0.0` — only the reverse proxy should reach uvicorn.
- FastAPI debug tracebacks are already off (we never pass `debug=True`).

## Reverse proxy (required)

Terminate TLS at Nginx (or Caddy) in front of uvicorn:

```nginx
server {
    listen 443 ssl http2;
    server_name yourdomain.et;
    ssl_certificate     /etc/letsencrypt/live/yourdomain.et/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.et/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
server {  # redirect all HTTP to HTTPS
    listen 80;
    server_name yourdomain.et;
    return 301 https://$host$request_uri;
}
```

Then set in `.env`:

```
ENVIRONMENT=production
HTTPS_ENABLED=true          # enables the Strict-Transport-Security header
ALLOWED_ORIGINS=https://yourdomain.et
```

Firewall: allow only 80/443 inbound (e.g. `ufw allow 80,443/tcp && ufw enable`).

## Secrets

- All secrets live in `.env` (gitignored). Never commit `.env`.
- `SECRET_KEY` / `RECEIPT_HMAC_SECRET` are validated at startup — the server
  refuses to boot with missing or placeholder values.
- Rotate any secret immediately if it is ever pasted into a chat, log, or commit.
- Change the seeded default staff passwords (`Admin@1234` etc.) before going live,
  or set `ADMIN_DEFAULT_PASSWORD` / `KITCHEN_DEFAULT_PASSWORD` / `CASHIER_DEFAULT_PASSWORD`
  in `.env` before first boot.

## Payment data (PCI scope)

- We never accept, transmit, or store full card numbers or CVVs.
- All electronic payments go through tokenized processors (Chapa, Telebirr, CBE Birr);
  we store only order numbers, transaction references, and amounts.
- Card refunds record the **last 4 digits only** (server-validated `^\d{4}$`).
- Every refund requires an authenticated cashier/admin session and writes an
  immutable audit row (actor, role, IP, station, amount, timestamp).

## Dependency audit (monthly)

```bash
python -m pip_audit -r requirements.txt
```

Last run: 2026-07-15 — 0 known vulnerabilities
(after migrating python-jose → PyJWT, which removed the vulnerable `ecdsa` pull-in).

## Authorization tests

```bash
cd backend
python -m pytest tests/test_authz.py -v
```

Verifies anonymous users and cross-role tokens (kitchen→cashier, cashier→admin, …)
are rejected with 401/403 on every staff endpoint. Run after any change to
routes or auth code.
