from datetime import date as DateType, datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.core.timezone import diary_today_shanghai, now_shanghai


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    email: str = Field(index=True, unique=True, max_length=320)
    hashed_password: Optional[str] = Field(default=None, max_length=255)
    nickname: str = Field(default="用户", max_length=50)
    avatar_url: Optional[str] = Field(default=None, max_length=1024)
    agent_name: str = Field(default="Agent", max_length=100)
    agent_system_prompt: Optional[str] = Field(default=None, max_length=4000)
    is_active: bool = Field(default=True)
    is_superuser: bool = Field(default=False)
    google_id: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=now_shanghai)


class EmailVerificationCode(SQLModel, table=True):
    __tablename__ = "email_verification_codes"

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, max_length=320)
    code: str = Field(max_length=6)
    expires_at: datetime = Field(default_factory=now_shanghai)


class Entry(SQLModel, table=True):
    __tablename__ = "entries"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    title: Optional[str] = Field(default=None, max_length=255)
    content: str
    content_format: str = Field(default="html", max_length=20, index=True)
    content_legacy: Optional[str] = Field(default=None)
    district: Optional[str] = Field(default=None, max_length=255)
    photo_url: Optional[str] = Field(default=None, max_length=1024)
    mood: str = Field(default="平静", max_length=50)
    date: DateType = Field(default_factory=diary_today_shanghai, index=True)
    user_id: Optional[int] = Field(default=None, foreign_key="users.id", index=True)
    created_at: datetime = Field(default_factory=now_shanghai)
    updated_at: datetime = Field(default_factory=now_shanghai)


class FoodPhoto(SQLModel, table=True):
    __tablename__ = "food_photos"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    group_id: Optional[str] = Field(default=None, max_length=64, index=True)
    photo_url: str = Field(max_length=1024)
    caption: Optional[str] = Field(default=None, max_length=255)
    shot_date: DateType = Field(index=True)
    shot_at: Optional[datetime] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=now_shanghai)
    updated_at: datetime = Field(default_factory=now_shanghai)


class FoodPhotoComment(SQLModel, table=True):
    __tablename__ = "food_photo_comments"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    group_id: str = Field(max_length=64, index=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    content: str = Field(max_length=2000)
    created_at: datetime = Field(default_factory=now_shanghai, index=True)


class ChatLog(SQLModel, table=True):
    __tablename__ = "chat_logs"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    role: str = Field(max_length=20, index=True)
    content: str
    created_at: datetime = Field(default_factory=now_shanghai, index=True)


class UploadAsset(SQLModel, table=True):
    __tablename__ = "upload_assets"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    kind: str = Field(max_length=32, index=True)
    original_name: str = Field(max_length=255)
    content_type: str = Field(max_length=120)
    size_bytes: int = Field(default=0)
    storage_path: str = Field(max_length=1024)
    public_url: str = Field(max_length=1024)
    created_at: datetime = Field(default_factory=now_shanghai, index=True)


class Countdown(SQLModel, table=True):
    __tablename__ = "countdowns"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    name: str = Field(max_length=255)
    target_date: DateType = Field(index=True)
    emoji: str = Field(default="📅", max_length=10)
    created_at: datetime = Field(default_factory=now_shanghai)
    updated_at: datetime = Field(default_factory=now_shanghai)