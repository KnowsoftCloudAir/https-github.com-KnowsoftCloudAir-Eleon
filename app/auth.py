from datetime import datetime, timedelta
from typing import Optional
from fastapi import Depends, HTTPException, Request
from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlmodel import Session, select
import os
import hashlib
from app.database import get_session
from app.models import User, UserRole, UserStatus, UserSubscription

SECRET = os.getenv("ELEON_SECRET", "eleon-knowsoft-secret-change-me")
ALGO = "HS256"
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

def _bcrypt_safe(p: str) -> str:
    raw = (p or "").encode("utf-8")
    if len(raw) > 72:
        return hashlib.sha256(raw).hexdigest()
    return p or ""

def hash_password(p: str) -> str:
    return pwd.hash(_bcrypt_safe(p))

def verify_password(plain: str, hashed: str) -> bool:
    try:
        return pwd.verify(_bcrypt_safe(plain), hashed)
    except Exception:
        return False

def create_token(user_id: int) -> str:
    payload = {"sub": str(user_id), "exp": datetime.utcnow() + timedelta(days=14)}
    return jwt.encode(payload, SECRET, algorithm=ALGO)

def user_from_request(request: Request, session: Session) -> Optional[User]:
    token = request.cookies.get("eleon_token")
    if not token:
        auth = request.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()
    if not token:
        return None
    try:
        data = jwt.decode(token, SECRET, algorithms=[ALGO])
        uid = int(data.get("sub"))
    except (JWTError, ValueError, TypeError):
        return None
    return session.get(User, uid)

def ensure_user_subscription(session: Session, user: User) -> User:
    """12h free trial at registration → free 1 month once → expired until renew."""
    if user.role == UserRole.general_admin:
        return user
    now = datetime.utcnow()
    # legacy users without sub fields filled
    try:
        ends = user.sub_ends_at
        status = user.sub_status
    except Exception:
        return user
    if ends is None:
        user.sub_status = "trial_12h"
        user.sub_ends_at = now + timedelta(hours=12)
        user.sub_plan = "trial_12h"
        if not hasattr(user, "free_month_used") or user.free_month_used is None:
            user.free_month_used = False
        session.add(user)
        session.commit()
        session.refresh(user)
        return user
    if ends > now:
        if status == "expired":
            user.sub_status = user.sub_plan or "active"
            session.add(user)
            session.commit()
            session.refresh(user)
        return user
    # time elapsed
    if not user.free_month_used:
        user.free_month_used = True
        user.sub_status = "free_month"
        user.sub_plan = "free_month"
        user.sub_ends_at = now + timedelta(days=30)
        session.add(user)
        session.add(UserSubscription(
            user_id=user.id, plan="free_month", amount=0.0, duration_days=30,
            status="active", starts_at=now, ends_at=user.sub_ends_at,
            note="Automatic free first month after 12-hour trial",
        ))
        session.commit()
        session.refresh(user)
        return user
    user.sub_status = "expired"
    session.add(user)
    session.commit()
    session.refresh(user)
    return user

def user_sub_active(user: User) -> bool:
    if user.role == UserRole.general_admin:
        return True
    if not getattr(user, "sub_ends_at", None):
        return False
    return user.sub_ends_at > datetime.utcnow() and user.sub_status != "expired"

# Paths always allowed when subscription expired
_SUB_ALLOW = (
    "/subscription", "/logout", "/login", "/register", "/static",
    "/manifest", "/sw.js", "/api/download-links",
)

def require_user(request: Request, session: Session = Depends(get_session)) -> User:
    user = user_from_request(request, session)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    if user.role == UserRole.general_admin:
        return user
    if user.status == UserStatus.suspended:
        raise HTTPException(status_code=403, detail="Account suspended")
    if user.status != UserStatus.approved:
        raise HTTPException(status_code=403, detail="Account not approved")
    if user.access_expires_at and user.access_expires_at < datetime.utcnow():
        raise HTTPException(status_code=403, detail="Access expired")
    user = ensure_user_subscription(session, user)
    path = request.url.path or ""
    allowed = any(path == p or path.startswith(p + "/") for p in _SUB_ALLOW) or path.startswith("/static")
    if not user_sub_active(user) and not allowed:
        raise HTTPException(status_code=307, headers={"Location": "/subscription?expired=1"})
    return user

def require_admin(request: Request, session: Session = Depends(get_session)) -> User:
    user = require_user(request, session)
    if user.role != UserRole.general_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    return user
