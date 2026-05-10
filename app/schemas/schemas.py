from datetime import date as DateType, datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class EntryBase(BaseModel):
    title: Optional[str] = Field(None, max_length=255)
    content: str = Field(..., min_length=1)
    content_format: Optional[str] = Field(default="html", max_length=20)
    date: Optional[DateType] = None
    district: Optional[str] = Field(None, max_length=255)
    photo_url: Optional[str] = Field(None, max_length=1024)
    mood: Optional[str] = Field(None, max_length=50)


class EntryCreate(EntryBase):
    pass


class EntryUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=255)
    content: Optional[str] = Field(None, min_length=1)
    content_format: Optional[str] = Field(default=None, max_length=20)
    date: Optional[DateType] = None
    district: Optional[str] = Field(None, max_length=255)
    photo_url: Optional[str] = Field(None, max_length=1024)
    mood: Optional[str] = Field(None, max_length=50)


class EntryResponse(EntryBase):
    id: int
    created_at: datetime
    updated_at: datetime
    preview_text: Optional[str] = None

    class Config:
        from_attributes = True


class FoodPhotoResponse(BaseModel):
    id: int
    user_id: int
    group_id: Optional[str] = None
    photo_url: str
    caption: Optional[str] = None
    shot_date: DateType
    shot_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class FoodPhotoCommentResponse(BaseModel):
    id: int
    group_id: str
    user_id: int
    content: str
    created_at: datetime

    class Config:
        from_attributes = True


class FoodPhotoCommentCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)


class FoodPhotoGroupResponse(BaseModel):
    group_id: str
    caption: Optional[str] = None
    photos: list[FoodPhotoResponse] = Field(default_factory=list)
    comments: list[FoodPhotoCommentResponse] = Field(default_factory=list)


class FoodPhotoDayResponse(BaseModel):
    date: DateType
    photos_count: int = 0
    groups: list[FoodPhotoGroupResponse] = Field(default_factory=list)


class FoodProcessResponse(BaseModel):
    type: str
    summary: Optional[str] = None
    food_name: Optional[str] = None
    track_id: Optional[str] = None
    photo: Optional[FoodPhotoResponse] = None
    entry_id: Optional[int] = None
    date: Optional[DateType] = None
    shot_at: Optional[datetime] = None


class FoodBatchProcessResponse(BaseModel):
    type: str
    summary: Optional[str] = None
    food_name: Optional[str] = None
    track_id: Optional[str] = None
    photos: list[FoodPhotoResponse] = Field(default_factory=list)
    entry_id: Optional[int] = None
    date: Optional[DateType] = None
    processed_count: int = 0
    photo_count: int = 0
    info_count: int = 0


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    password_required: Optional[bool] = False


class GoogleVerifyRequest(BaseModel):
    credential: str  # id_token from Google (browser-side)


class SendCodeRequest(BaseModel):
    email: EmailStr


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6)
    nickname: str = Field(..., min_length=1, max_length=50)
    code: str = Field(..., min_length=6, max_length=6)


class SetPasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=6)
    old_password: Optional[str] = None

class UpdateProfileRequest(BaseModel):
    nickname: Optional[str] = Field(default=None, min_length=1, max_length=50)
    avatar_url: Optional[str] = Field(default=None, max_length=1024)


class UserResponse(BaseModel):
    email: str
    nickname: str
    avatar_url: Optional[str]
    is_superuser: bool

    class Config:
        from_attributes = True


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=5000)
    image_urls: list[str] = Field(default_factory=list, max_length=9)


class ChatResponse(BaseModel):
    answer: str


class AgentSettingsResponse(BaseModel):
    agent_name: str
    agent_system_prompt: Optional[str] = None


class AgentSettingsUpdateRequest(BaseModel):
    agent_name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    agent_system_prompt: Optional[str] = Field(default=None, max_length=4000)


class DiaryGenerationResponse(BaseModel):
    answer: str
    entry_id: int
    date: DateType
    updated: bool


class ChatLogResponse(BaseModel):
    id: int
    user_id: int
    role: str
    content: str
    created_at: datetime

    class Config:
        from_attributes = True