from __future__ import annotations

import asyncio
import logging
from datetime import date as DateType, datetime
from typing import Optional
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, UploadFile, status
from sqlmodel import Session

from app.api.v1.endpoints.upload import ALLOWED_IMAGE_TYPES, MAX_IMAGE_SIZE
from app.core.auth import get_current_user
from app.core.config import settings
from app.core.notion import create_diet_page, register_file_upload, send_file_upload
from app.core.timezone import now_shanghai
from app.crud.crud import (
    create_food_photo,
    get_food_photos_by_user,
    get_food_photo_comments,
    create_food_photo_comment,
)
from app.db.session import engine, get_db
from app.models.models import FoodPhoto, FoodPhotoComment, UploadAsset
from app.schemas.schemas import FoodBatchProcessResponse, FoodPhotoDayResponse, FoodPhotoGroupResponse, FoodPhotoResponse, FoodPhotoCommentResponse, FoodPhotoCommentCreate


router = APIRouter()
logger = logging.getLogger("cyber_diary.food")
ALLOWED_DIET_MEAL_TYPES = {"早餐", "午餐", "晚餐", "加餐"}

def _normalize_date_value(value: str) -> str:
    try:
        return DateType.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="date must be in YYYY-MM-DD format",
        ) from exc


def _validate_diet_form(
    *,
    food_name: str,
    calories: Optional[int],
    meal_type: str,
    date: str,
    feeling: str,
) -> tuple[str, Optional[int], str, str, str]:
    food_name_text = food_name.strip()
    if not food_name_text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="food_name is required")

    meal_type_text = meal_type.strip()
    if meal_type_text not in ALLOWED_DIET_MEAL_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="meal_type must be one of 早餐/午餐/晚餐/加餐",
        )

    if calories is not None and calories <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="calories must be a positive integer")

    return food_name_text, calories, meal_type_text, _normalize_date_value(date), feeling.strip()


def _coerce_optional_int(value: object) -> Optional[int]:
    if value is None:
        return None

    if isinstance(value, bool):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="calories must be a positive integer")

    if isinstance(value, int):
        return value

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if not text.isdigit():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="calories must be a positive integer")
        return int(text)

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="calories must be a positive integer")


async def _parse_diet_upload_request(request: Request) -> tuple[str, Optional[int], str, str, str, list[UploadFile]]:
    content_type = (request.headers.get("content-type") or "").lower()

    if "application/json" in content_type:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON body")

        return (
            str(payload.get("food_name", "")),
            _coerce_optional_int(payload.get("calories")),
            str(payload.get("meal_type", "")),
            str(payload.get("date", "")),
            str(payload.get("feeling", "")),
            [],
        )

    form = await request.form()
    images: list[UploadFile] = []
    for key, value in form.multi_items():
        if key == "images" and isinstance(value, UploadFile):
            images.append(value)

    return (
        str(form.get("food_name", "")),
        _coerce_optional_int(form.get("calories")),
        str(form.get("meal_type", "")),
        str(form.get("date", "")),
        str(form.get("feeling", "")),
        images,
    )


async def _upload_single_diet_image(
    client: httpx.AsyncClient,
    *,
    file: UploadFile,
    track_id: str,
    index: int,
    semaphore: asyncio.Semaphore,
) -> str:
    async with semaphore:
        content_type = (file.content_type or "").lower()
        if content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only jpg/png/webp/gif images are allowed",
            )

        filename = file.filename or f"diet-image-{index}"
        file_upload_id = await register_file_upload(
            client,
            filename=filename,
            content_type=content_type,
        )

        try:
            payload = await file.read()
            if not payload:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Empty file: {filename}")

            if len(payload) > MAX_IMAGE_SIZE:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image size cannot exceed 5MB")

            await send_file_upload(
                client,
                file_upload_id=file_upload_id,
                filename=filename,
                content_type=content_type,
                payload=payload,
            )

            logger.info(
                "food_diet_file_uploaded track_id=%s index=%s filename=%s file_upload_id=%s bytes=%s",
                track_id,
                index,
                filename,
                file_upload_id,
                len(payload),
            )
            return file_upload_id
        finally:
            try:
                await file.close()
            except Exception:
                pass


def _build_httpx_client() -> httpx.AsyncClient:
    if not settings.notion_ready():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Notion is not configured",
        )

    timeout_seconds = max(int(settings.NOTION_TIMEOUT_SECONDS or 60), 10)
    timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 15.0))
    headers = {
        "Authorization": f"Bearer {settings.NOTION_API_KEY}",
        "Notion-Version": settings.NOTION_VERSION,
        "Accept": "application/json",
    }
    return httpx.AsyncClient(base_url="https://api.notion.com/v1", timeout=timeout, headers=headers)


@router.get("/photos", response_model=list[FoodPhotoDayResponse])
def list_food_photos(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    food_photos = get_food_photos_by_user(db, current_user.id)

    def sort_key(photo: FoodPhoto) -> datetime:
        return photo.shot_at or photo.created_at

    day_map: dict[DateType, dict[str, list[FoodPhoto]]] = {}
    for photo in food_photos:
        group_key = (photo.group_id or f"single-{photo.id}").strip() or f"single-{photo.id}"
        day_groups = day_map.setdefault(photo.shot_date, {})
        day_groups.setdefault(group_key, []).append(photo)

    result: list[FoodPhotoDayResponse] = []
    for shot_date in sorted(day_map.keys(), reverse=True):
        groups: list[FoodPhotoGroupResponse] = []
        total_count = 0
        for group_id, group_photos in sorted(
            day_map[shot_date].items(),
            key=lambda item: min(sort_key(photo) for photo in item[1]),
            reverse=True,
        ):
            sorted_photos = sorted(group_photos, key=sort_key)
            total_count += len(sorted_photos)
            captions = [photo.caption.strip() for photo in sorted_photos if photo.caption and photo.caption.strip()]
            caption = captions[0] if captions else None
            comments = get_food_photo_comments(db, group_id)
            groups.append(
                FoodPhotoGroupResponse(
                    group_id=group_id,
                    caption=caption,
                    photos=sorted_photos,
                    comments=comments,
                )
            )

        result.append(
            FoodPhotoDayResponse(
                date=shot_date,
                photos_count=total_count,
                groups=groups,
            )
        )

    return result


@router.post("/photos", response_model=FoodBatchProcessResponse)
async def upload_food_photo(
    request: Request,
    x_track_id: str | None = Header(default=None, alias="X-Track-Id"),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
    current_user=Depends(get_current_user),
):
    track_id = (x_track_id or x_request_id or uuid4().hex).strip() or uuid4().hex
    food_name, calories, meal_type, date, feeling, images = await _parse_diet_upload_request(request)
    food_name_text, calories_value, meal_type_text, normalized_date, feeling_text = _validate_diet_form(
        food_name=food_name,
        calories=calories,
        meal_type=meal_type,
        date=date,
        feeling=feeling,
    )

    logger.info(
        "food_diet_upload_start track_id=%s user_id=%s food_name=%s meal_type=%s date=%s images=%s calories=%s feeling_len=%s",
        track_id,
        current_user.id,
        food_name_text,
        meal_type_text,
        normalized_date,
        len(images),
        calories_value,
        len(feeling_text),
    )

    async with _build_httpx_client() as client:
        semaphore = asyncio.Semaphore(4)
        upload_tasks = [
            _upload_single_diet_image(
                client,
                file=file,
                track_id=track_id,
                index=index,
                semaphore=semaphore,
            )
            for index, file in enumerate(images, start=1)
        ]

        file_upload_ids: list[str] = []
        if upload_tasks:
            file_upload_ids = await asyncio.gather(*upload_tasks)

        page_result = await create_diet_page(
            client,
            food_name=food_name_text,
            calories=calories_value,
            meal_type=meal_type_text,
            date=normalized_date,
            feeling=feeling_text,
            file_upload_ids=file_upload_ids,
        )

    logger.info(
        "food_diet_upload_completed track_id=%s user_id=%s file_upload_ids=%s page_id=%s",
        track_id,
        current_user.id,
        len(file_upload_ids),
        page_result.page_id,
    )

    return FoodBatchProcessResponse(
        type="DIET_PAGE",
        summary=f"已写入 Notion 页面，图片 {len(file_upload_ids)} 张",
        food_name=food_name_text,
        track_id=track_id,
        page_id=page_result.page_id,
        page_url=page_result.page_url,
        file_upload_ids=file_upload_ids,
        photos=[],
        date=DateType.fromisoformat(normalized_date),
        processed_count=len(file_upload_ids),
        photo_count=len(file_upload_ids),
        info_count=0,
    )


@router.get("/photos/{photo_id}/comments", response_model=list[FoodPhotoCommentResponse])
def get_photo_comments(
    photo_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    comments = get_food_photo_comments(db, photo_id)
    return comments


@router.post("/photos/{photo_id}/comments", response_model=FoodPhotoCommentResponse)
def add_photo_comment(
    photo_id: int,
    request: FoodPhotoCommentCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    comment = FoodPhotoComment(
        food_photo_id=photo_id,
        content=request.content,
        created_at=now_shanghai(),
    )
    return create_food_photo_comment(db, comment)


@router.delete("/groups/{group_id}")
def delete_food_group(
    group_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """删除整个照片群组（包括所有照片和评论）"""
    from sqlmodel import select
    
    # 检查该群组中是否有用户的照片
    photos = db.exec(
        select(FoodPhoto).where(
            FoodPhoto.group_id == group_id,
            FoodPhoto.user_id == current_user.id
        )
    ).all()
    
    if not photos:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="群组不存在或无权删除",
        )
    
    # 删除上传资产
    upload_assets = db.exec(
        select(UploadAsset).where(
            UploadAsset.user_id == current_user.id,
            UploadAsset.kind == "food-image",
        )
    ).all()
    
    # 匹配url进行删除
    photo_urls = {photo.photo_url for photo in photos}
    for asset in upload_assets:
        if asset.public_url in photo_urls:
            db.delete(asset)
    
    # 删除评论
    comments = db.exec(
        select(FoodPhotoComment).where(FoodPhotoComment.group_id == group_id)
    ).all()
    for comment in comments:
        db.delete(comment)
    
    # 删除照片
    for photo in photos:
        db.delete(photo)
    
    db.commit()
    
    logger.info(
        "food_group_deleted group_id=%s user_id=%s photos=%s comments=%s",
        group_id,
        current_user.id,
        len(photos),
        len(comments),
    )
    
    return {"message": "群组已删除", "deleted_photos": len(photos), "deleted_comments": len(comments)}


@router.post("/comments/clean-duplicates")
def clean_duplicate_comments(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """清理重复的美食照片评论（仅供管理员使用）"""
    from sqlmodel import func, select
    
    # 查询重复的评论组
    duplicate_groups = db.exec(
        select(
            FoodPhotoComment.group_id,
            FoodPhotoComment.content,
            func.count(FoodPhotoComment.id).label('count')
        ).group_by(FoodPhotoComment.group_id, FoodPhotoComment.content).having(func.count(FoodPhotoComment.id) > 1)
    ).all()
    
    if not duplicate_groups:
        return {"message": "没有找到重复的评论", "deleted_count": 0}
    
    delete_count = 0
    for group_id, content, _ in duplicate_groups:
        comments = db.exec(
            select(FoodPhotoComment)
            .where(FoodPhotoComment.group_id == group_id, FoodPhotoComment.content == content)
            .order_by(FoodPhotoComment.id)
        ).all()
        
        for comment in comments[1:]:
            db.delete(comment)
            delete_count += 1
    
    db.commit()
    return {"message": f"已成功删除 {delete_count} 条重复评论", "deleted_count": delete_count}

