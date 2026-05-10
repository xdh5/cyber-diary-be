import random
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Any, Dict, Optional

import requests
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlmodel import Session

from app.core.config import settings
from app.crud.crud import (
    create_or_update_verification_code,
    create_user,
    get_user_by_email,
    get_user_by_google_id,
    update_user,
)
from app.db.session import get_db
from app.models.models import EmailVerificationCode, User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a JWT access token.

    Behavior:
    - If `expires_delta` is provided and > 0, add an `exp` claim accordingly.
    - Else if `settings.ACCESS_TOKEN_EXPIRE_MINUTES` > 0, add `exp` based on that value.
    - If neither specify a positive expiry (i.e. value is 0 or negative), do NOT add an `exp` claim
      which results in a non-expiring token.
    """
    to_encode = data.copy()

    # explicit expires_delta takes precedence
    if expires_delta is not None:
        try:
            # only add exp if positive
            if expires_delta.total_seconds() > 0:
                expire = datetime.utcnow() + expires_delta
                to_encode.update({"exp": expire})
        except Exception:
            # if expires_delta is not a timedelta, fall back to settings
            pass
    else:
        # use configured minutes; if <= 0 treat as "never expire"
        if getattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES", 0) and settings.ACCESS_TOKEN_EXPIRE_MINUTES > 0:
            expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
            to_encode.update({"exp": expire})

    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> Dict[str, Any]:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])


def authenticate_user(db: Session, email: str, password: str) -> Optional[User]:
    user = get_user_by_email(db, email)
    if not user or not user.hashed_password:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
        email: Optional[str] = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = get_user_by_email(db, email)
    if user is None or not user.is_active:
        raise credentials_exception
    return user


def send_email_message(subject: str, body: str, to_email: str) -> None:
    if not settings.SMTP_SERVER or not settings.SMTP_USERNAME or not settings.SMTP_PASSWORD:
        raise RuntimeError("SMTP configuration is required to send email verification codes.")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.SMTP_FROM or settings.SMTP_USERNAME
    message["To"] = to_email
    message.set_content(body)

    if settings.SMTP_USE_TLS:
        with smtplib.SMTP(settings.SMTP_SERVER, settings.SMTP_PORT, timeout=10) as smtp:
            smtp.starttls()
            smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(settings.SMTP_SERVER, settings.SMTP_PORT, timeout=10) as smtp:
            smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            smtp.send_message(message)


def generate_verification_code() -> str:
    return f"{random.randint(0, 999999):06d}"


def ensure_registration_code(db: Session, email: str) -> str:
    code = generate_verification_code()
    expires_at = datetime.utcnow() + timedelta(minutes=10)
    verification_code = EmailVerificationCode(email=email, code=code, expires_at=expires_at)
    create_or_update_verification_code(db, verification_code)
    return code


def verify_registration_code(db: Session, email: str, code: str) -> bool:
    from app.crud.crud import get_verification_code_by_email, delete_verification_code

    record = get_verification_code_by_email(db, email)
    if not record or record.code != code or record.expires_at < datetime.utcnow():
        return False
    record.code = ""
    update_verification_code = EmailVerificationCode.from_orm(record)
    update_verification_code.code = ""
    create_or_update_verification_code(db, update_verification_code)
    return True


def request_google_token(code: str) -> Dict[str, Any]:
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise RuntimeError("Google OAuth2 environment variables are not configured.")

    response = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        headers={"Accept": "application/json"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def fetch_google_user_info(access_token: str) -> Dict[str, Any]:
    response = requests.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def get_or_create_google_user(db: Session, google_info: Dict[str, Any]) -> User:
    google_id = google_info.get("id") or google_info.get("sub")
    email = google_info.get("email")
    if not google_id or not email:
        raise ValueError("Google authentication failed to return required user information.")

    user = get_user_by_google_id(db, google_id)
    if user:
        return user

    user_by_email = get_user_by_email(db, email)
    nickname = google_info.get("name") or email.split("@")[0]
    avatar_url = google_info.get("picture") or random.choice(settings.DEFAULT_AVATAR_URLS)

    if user_by_email:
        user_by_email.google_id = google_id
        user_by_email.nickname = user_by_email.nickname or nickname
        user_by_email.avatar_url = user_by_email.avatar_url or avatar_url
        return update_user(db, user_by_email)

    user = User(
        email=email,
        nickname=nickname,
        avatar_url=avatar_url,
        google_id=google_id,
        hashed_password=None,
    )
    return create_user(db, user)


def pick_default_avatar() -> str:
    return random.choice(settings.DEFAULT_AVATAR_URLS)