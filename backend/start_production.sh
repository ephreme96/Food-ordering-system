#!/usr/bin/env bash
# Start the server for the LIVE site (Linux server / cloud VM).
#
# Differences from "Start Server.bat" (which is for your own PC):
#   * no --reload: reload watches files and restarts on any change. Handy while
#     editing, but slow and risky on a live server.
#   * listens on 127.0.0.1 only: put Nginx (or Caddy) in front for HTTPS and let
#     it forward to this port. Nothing else on the internet can reach uvicorn.
#   * --proxy-headers: trust X-Forwarded-For from the proxy so rate limits and
#     the audit log see each visitor's real IP, not the proxy's.
#   * one worker: live order feeds and login lockouts are kept in memory, so
#     they only work correctly with a single process.
#
# Usage (from the backend folder):  ./start_production.sh
set -euo pipefail
cd "$(dirname "$0")"

if ! grep -qi '^ENVIRONMENT=production' .env 2>/dev/null; then
  echo "Set ENVIRONMENT=production in backend/.env before starting the live server." >&2
  exit 1
fi

exec python -m uvicorn main:app \
  --host 127.0.0.1 \
  --port "${PORT:-8000}" \
  --workers 1 \
  --proxy-headers \
  --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}"
