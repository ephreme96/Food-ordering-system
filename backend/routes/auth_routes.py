from __future__ import annotations
import logging
import os
import time
from collections import defaultdict
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

import totp
from auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    create_access_token,
    get_current_user,
    get_password_hash,
    require_role,
    revoke_sessions,
    verify_password,
)
from database import get_db
from models import User, UserRole
from schemas import Token, UserCreate, UserLogin, UserResponse

logger = logging.getLogger(__name__)
router  = APIRouter(prefix="/api/auth", tags=["Authentication"])
limiter = Limiter(key_func=get_remote_address)

# ─── In-memory login attempt tracker ─────────────────────────────────────────
# Security: tracks failed login attempts per username to prevent brute force.
# Uses in-memory storage (resets on server restart) — sufficient for single-server
# deployments. For multi-server production, replace with Redis.
_LOCKOUT_MAX_ATTEMPTS  = 10   # allow 10 failures before lockout
_LOCKOUT_WINDOW        = 300  # within a 5-minute window
_LOCKOUT_DURATION      = 900  # lock out for 15 minutes after too many failures
_failed_attempts: dict = defaultdict(list)  # { username: [unix_timestamp, ...] }

# Security: bcrypt dummy hash used for timing-safe login checks.
# Without this, an attacker can tell if a username exists by measuring response
# time: "user not found" returns instantly; "wrong password" takes bcrypt time.
# Must be a REAL bcrypt hash — a malformed string makes passlib raise ValueError
# (500 error), which itself becomes a username-enumeration oracle.
# Generated once at import time; the plaintext is irrelevant, it's never matched.
from auth import get_password_hash as _hash_fn
_DUMMY_HASH = _hash_fn("timing-equalizer-dummy-password")


def _check_account_lockout(username: str) -> None:
    """Raise 429 if this username has exceeded failed login attempts."""
    now = time.time()
    # Purge attempts older than the window
    _failed_attempts[username] = [
        t for t in _failed_attempts[username] if now - t < _LOCKOUT_WINDOW
    ]
    if len(_failed_attempts[username]) >= _LOCKOUT_MAX_ATTEMPTS:
        wait = int(_LOCKOUT_DURATION - (now - min(_failed_attempts[username])))
        logger.warning("Account '%s' locked out due to too many failed logins", username)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed login attempts. Try again in {max(1, wait)} seconds.",
        )


def _record_failed_login(username: str) -> None:
    """Record one failed attempt for this username."""
    _failed_attempts[username].append(time.time())
    logger.warning("Failed login attempt for username='%s'", username)


def _clear_login_attempts(username: str) -> None:
    """Clear attempts after a successful login."""
    _failed_attempts.pop(username, None)


OTP_REQUIRED = "OTP_REQUIRED"   # login.html shows the code field when it sees this


def _issue_token(user: User) -> str:
    return create_access_token(
        data={"sub": user.username, "role": user.role.value, "ver": user.token_version or 0},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )


@router.post("/login", response_model=Token)
@limiter.limit("10/minute")  # Security: max 10 login attempts per IP per minute
def login(request: Request, credentials: UserLogin, db: Session = Depends(get_db)):
    """Authenticate staff and return a JWT. Rate-limited and lockout-protected."""

    # Security: check lockout BEFORE hitting the database or running bcrypt.
    # This prevents DoS via CPU-exhaustion through many parallel bcrypt calls.
    # Count failures per (username, IP): a stranger guessing passwords only
    # locks themselves out, not the real owner on a different network.
    lock_key = f"{credentials.username}|{get_remote_address(request)}"
    _check_account_lockout(lock_key)

    user = db.query(User).filter(User.username == credentials.username).first()

    # Security: ALWAYS call verify_password even if the user doesn't exist.
    # This makes both code paths take the same amount of time (bcrypt timing).
    # Without this, an attacker can enumerate valid usernames by measuring response time.
    hash_to_check = user.password_hash if user else _DUMMY_HASH
    password_ok   = verify_password(credentials.password, hash_to_check)

    if not user or not password_ok:
        _record_failed_login(lock_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    if not user.is_active:
        # Security: use 403 (Forbidden) not 400 (Bad Request) for disabled accounts.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled. Contact your administrator.",
        )

    # Two-step login: the password alone is not enough once a code is set up.
    if user.totp_enabled:
        if not credentials.otp:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=OTP_REQUIRED)
        if not totp.verify(user.totp_secret, credentials.otp):
            _record_failed_login(lock_key)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid 2-step code")

    # Successful login — clear any recorded failures
    _clear_login_attempts(lock_key)
    logger.info("Successful login for user '%s' (role=%s)", user.username, user.role.value)

    return {"access_token": _issue_token(user), "token_type": "bearer", "user": user}


@router.post("/register", response_model=UserResponse, status_code=201)
def register(
    user_data: UserCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(UserRole.admin)),
):
    """Admin-only: create a new staff account (kitchen / cashier / admin)."""
    if db.query(User).filter(User.username == user_data.username).first():
        raise HTTPException(status_code=409, detail="Username already exists")
    if db.query(User).filter(User.email == user_data.email).first():
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(
        username=user_data.username,
        email=user_data.email,
        password_hash=get_password_hash(user_data.password),
        role=user_data.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    """Return the currently authenticated user's profile."""
    return current_user


@router.get("/users", response_model=list[UserResponse])
def list_users(
    db: Session = Depends(get_db),
    _: User = Depends(require_role(UserRole.admin)),
):
    """Admin-only: list all staff accounts."""
    return db.query(User).order_by(User.created_at.desc()).all()


# ─── Password management ──────────────────────────────────────────────────────

from pydantic import BaseModel as _BM

class _ChangePasswordBody(_BM):
    current_password: str
    new_password:     str


class _SetPasswordBody(_BM):
    new_password: str


@router.patch("/change-password", status_code=200)
@limiter.limit("5/minute")
def change_own_password(
    request:      Request,
    body:         _ChangePasswordBody,
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
):
    """
    Any logged-in staff member can change their own password.
    Requires the current password to confirm identity.
    """
    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")
    if body.current_password == body.new_password:
        raise HTTPException(status_code=400, detail="New password must differ from the current one")

    current_user.password_hash = get_password_hash(body.new_password)
    revoke_sessions(current_user)   # other devices using the old password are logged out
    db.commit()
    logger.info("User '%s' changed their own password", current_user.username)
    # A fresh token so this device stays logged in.
    return {"detail": "Password updated successfully", "access_token": _issue_token(current_user)}


@router.patch("/users/{user_id}/set-password", status_code=200)
def admin_set_password(
    user_id:  int,
    body:     _SetPasswordBody,
    db:       Session = Depends(get_db),
    _:        User    = Depends(require_role(UserRole.admin)),
):
    """
    Admin-only: set a new password for any staff account without needing the old one.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    user.password_hash = get_password_hash(body.new_password)
    revoke_sessions(user)
    db.commit()
    logger.info("Admin set new password for user '%s' (id=%d)", user.username, user_id)
    return {"detail": f"Password for '{user.username}' updated successfully"}


# ─── Two-step login (TOTP) ────────────────────────────────────────────────────

class _OtpBody(_BM):
    code: str


class _DisableOtpBody(_BM):
    password: str
    code:     str


@router.get("/2fa/status")
def otp_status(current_user: User = Depends(get_current_user)):
    return {"enabled": bool(current_user.totp_enabled)}


@router.post("/2fa/setup")
@limiter.limit("5/minute")
def otp_setup(
    request:      Request,
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
):
    """Start two-step setup: returns a key to add to an authenticator app."""
    if current_user.totp_enabled:
        raise HTTPException(status_code=400, detail="2-step login is already on. Turn it off first.")
    secret = totp.generate_secret()
    current_user.totp_secret = secret
    db.commit()
    issuer = os.getenv("RESTAURANT_NAME", "Food Ordering")
    return {
        "secret":  secret,
        "otpauth": totp.provisioning_uri(secret, current_user.username, issuer),
    }


@router.post("/2fa/enable")
@limiter.limit("5/minute")
def otp_enable(
    request:      Request,
    body:         _OtpBody,
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
):
    """Finish setup by proving the authenticator app shows the right code."""
    if not current_user.totp_secret:
        raise HTTPException(status_code=400, detail="Start setup first.")
    if not totp.verify(current_user.totp_secret, body.code):
        raise HTTPException(status_code=400, detail="That code is not correct. Check the time on your phone and try again.")
    current_user.totp_enabled = True
    revoke_sessions(current_user)
    db.commit()
    logger.info("User '%s' turned on 2-step login", current_user.username)
    return {"enabled": True, "access_token": _issue_token(current_user)}


@router.post("/2fa/disable")
@limiter.limit("5/minute")
def otp_disable(
    request:      Request,
    body:         _DisableOtpBody,
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
):
    """Turn two-step login off. Needs the password AND a current code."""
    if not current_user.totp_enabled:
        return {"enabled": False}
    if not verify_password(body.password, current_user.password_hash) or \
            not totp.verify(current_user.totp_secret, body.code):
        raise HTTPException(status_code=400, detail="Password or code is not correct.")
    current_user.totp_enabled = False
    current_user.totp_secret  = None
    revoke_sessions(current_user)
    db.commit()
    logger.info("User '%s' turned off 2-step login", current_user.username)
    return {"enabled": False, "access_token": _issue_token(current_user)}
