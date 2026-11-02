from __future__ import annotations
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordBearer
import jwt
from jwt import InvalidTokenError as JWTError
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from database import get_db
from models import User, UserRole

# ─── Configuration ────────────────────────────────────────────────────────────

SECRET_KEY = os.getenv("SECRET_KEY", "insecure-default-change-me")
ALGORITHM  = os.getenv("ALGORITHM", "HS256")

# Security: reduced default from 480 min (8 hrs) to 60 min (1 hr).
# A stolen JWT is valid until it expires — shorter window limits blast radius.
# Set ACCESS_TOKEN_EXPIRE_MINUTES in .env to override (e.g. 120 for 2 hours).
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

pwd_context   = CryptContext(schemes=["bcrypt"], deprecated="auto")
# auto_error=False: a request may carry the login in the session cookie instead
# of an Authorization header (see get_current_user).
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

# ─── Session cookie ───────────────────────────────────────────────────────────
# Security: the staff web pages keep their login in an HttpOnly cookie, which
# page JavaScript cannot read. A script injected by an XSS bug can no longer copy
# the token and use it from the attacker's own computer (it could from
# localStorage). SameSite=Strict means other websites can't make the browser
# send it. Secure (HTTPS-only) is on when HTTPS_ENABLED=true; override with
# SESSION_COOKIE_SECURE=true/false.
SESSION_COOKIE_NAME   = "staff_session"
SESSION_COOKIE_SECURE = os.getenv(
    "SESSION_COOKIE_SECURE", os.getenv("HTTPS_ENABLED", "false")
).lower() == "true"


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME, token,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="strict",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE_NAME, path="/", httponly=True,
        secure=SESSION_COOKIE_SECURE, samesite="strict",
    )


# ─── Password helpers ─────────────────────────────────────────────────────────

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


# ─── JWT helpers ──────────────────────────────────────────────────────────────

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


# ─── FastAPI dependencies ─────────────────────────────────────────────────────

def user_from_token(token: str, db: Session) -> Optional[User]:
    """Return the active user a JWT belongs to, or None if the token is invalid."""
    try:
        payload  = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
    except JWTError:
        return None
    if not username:
        return None

    user = db.query(User).filter(User.username == username).first()
    if user is None or not user.is_active:
        return None
    # A password change (or disable) bumps token_version, which logs out
    # every token issued before it.
    if payload.get("ver", 0) != (user.token_version or 0):
        return None
    return user


def revoke_sessions(user: User) -> None:
    """Invalidate all of this user's existing login tokens."""
    user.token_version = (user.token_version or 0) + 1


def get_current_user(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    # An Authorization header (scripts, tests, a future native app) is used on
    # its own when present. Otherwise the browser's HttpOnly session cookie.
    if not token:
        token = request.cookies.get(SESSION_COOKIE_NAME)
    user = user_from_token(token, db) if token else None
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_role(*roles: UserRole):
    """
    Returns a FastAPI dependency that enforces role-based access.
    Usage:  current_user: User = Depends(require_role(UserRole.admin))
    """
    def _checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            # Security: generic 403 message — do NOT reveal which roles are required.
            # Disclosing role names helps attackers plan privilege escalation attacks.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied. Insufficient permissions.",
            )
        return current_user
    return _checker
