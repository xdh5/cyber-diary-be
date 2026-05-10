from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import Session
from urllib.parse import urlencode

from app.core.auth import (
    authenticate_user,
    create_access_token,
    ensure_registration_code,
    fetch_google_user_info,
    get_current_user,
    get_or_create_google_user,
    get_password_hash,
    request_google_token,
    send_email_message,
    verify_registration_code,
)
from app.core.config import settings
from app.crud.crud import create_user, get_user_by_email, update_user
from app.db.session import get_db
from app.models.models import User
from app.schemas.schemas import RegisterRequest, SendCodeRequest, SetPasswordRequest, TokenResponse, UpdateProfileRequest, UserResponse

router = APIRouter()


@router.post("/send-code", response_model=dict)
def send_registration_code(payload: SendCodeRequest, db: Session = Depends(get_db)):
    existing_user = get_user_by_email(db, payload.email)
    if existing_user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    code = ensure_registration_code(db, payload.email)
    send_email_message(
        subject="Cyber Diary 注册验证码",
        body=f"您的注册验证码是：{code}\n有效期 10 分钟。",
        to_email=payload.email,
    )
    return {"detail": "Verification code sent"}


@router.post("/register", response_model=dict)
def register_user(payload: RegisterRequest, db: Session = Depends(get_db)):
    if get_user_by_email(db, payload.email):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    if not verify_registration_code(db, payload.email, payload.code):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired verification code")

    user = User(
        email=payload.email,
        nickname=payload.nickname,
        hashed_password=get_password_hash(payload.password),
        avatar_url=None,  # Will be set by pick_default_avatar if needed
    )
    create_user(db, user)
    return {"detail": "User registered successfully"}


@router.post("/login", response_model=TokenResponse)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")

    token = create_access_token({"sub": user.email})
    return {"access_token": token, "token_type": "bearer"}


@router.get("/google/authorize", response_model=dict)
def google_authorize():
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Google OAuth2 is not configured")
    query = urlencode(
        {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            "response_type": "code",
            "scope": "openid email profile",
            "access_type": "offline",
            "prompt": "select_account",
        }
    )
    return {"url": f"https://accounts.google.com/o/oauth2/v2/auth?{query}"}


@router.get("/google/callback", response_model=TokenResponse)
def google_callback(code: str, db: Session = Depends(get_db)):
    token_data = request_google_token(code)
    google_info = fetch_google_user_info(token_data["access_token"])
    user = get_or_create_google_user(db, google_info)
    access_token = create_access_token({"sub": user.email})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "password_required": user.hashed_password is None,
    }


@router.post("/set-password", response_model=dict)
def set_password(request: SetPasswordRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.core.auth import verify_password, get_password_hash

    if current_user.hashed_password:
        if not request.old_password or not verify_password(request.old_password, current_user.hashed_password):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Old password is incorrect")

    current_user.hashed_password = get_password_hash(request.new_password)
    from app.crud.crud import update_user
    update_user(db, current_user)
    return {"detail": "Password set successfully"}


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.patch("/me", response_model=UserResponse)
def update_me(
    payload: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.nickname is not None:
        nickname = payload.nickname.strip()
        if not nickname:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nickname cannot be empty")
        current_user.nickname = nickname

    if payload.avatar_url is not None:
        avatar_url = payload.avatar_url.strip()
        current_user.avatar_url = avatar_url or None

    return update_user(db, current_user)