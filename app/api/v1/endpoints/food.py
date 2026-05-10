from __future__ import annotations

import logging
from datetime import date as DateType, datetime
from io import BytesIO
from typing import Optional
from uuid import uuid4

import cloudinary
import cloudinary.uploader
import exifread
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
from sqlmodel import Session

from app.api.v1.endpoints.upload import ALLOWED_IMAGE_TYPES, MAX_IMAGE_SIZE
from app.core.auth import get_current_user
from app.core.config import settings
from app.core.diary import (
    build_food_info_block,
    build_food_info_section,
    build_food_section,
    merge_food_info_section,
    merge_food_section,
)
from app.core.food_ai import FoodClassification, classify_food_upload, compose_food_comment
from app.core.timezone import diary_date_for_datetime, diary_today_shanghai, now_shanghai
from app.crud.crud import (
    create_entry,
    create_food_photo,
    get_entry_by_user_date_and_mood,
    get_food_photos_by_user,
    update_entry,
    get_food_photo_comments,
    create_food_photo_comment,
)
from app.db.session import get_db
from app.models.models import Entry, FoodPhoto, FoodPhotoComment, UploadAsset
from app.schemas.schemas import FoodBatchProcessResponse, FoodPhotoDayResponse, FoodPhotoGroupResponse, FoodPhotoResponse, FoodPhotoCommentResponse, FoodPhotoCommentCreate


router = APIRouter()
logger = logging.getLogger("cyber_diary.food")


def _extract_shot_at(payload: bytes) -> Optional[datetime]:
    try:
        tags = exifread.process_file(BytesIO(payload), details=False)
        raw_value = tags.get("EXIF DateTimeOriginal") or tags.get("EXIF DateTimeDigitized") or tags.get("Image DateTime")
        if not raw_value:
            return None

        raw_text = str(raw_value).strip()
        if not raw_text:
            return None

        return datetime.strptime(raw_text, "%Y:%m:%d %H:%M:%S")
    except Exception:
        return None


def _cloudinary_upload(payload: bytes, *, user_id: int, shot_at: datetime, file_name: str) -> str:
    if not settings.cloudinary_ready():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cloudinary is not configured",
        )

    cloudinary.config(
        cloud_name=settings.CLOUDINARY_CLOUD_NAME,
        api_key=settings.CLOUDINARY_API_KEY,
        api_secret=settings.CLOUDINARY_API_SECRET,
        secure=True,
    )
    result = cloudinary.uploader.upload(
        payload,
        folder=f"diary/{user_id}/{shot_at:%Y/%m/%d}",
        public_id=uuid4().hex,
        resource_type="image",
        overwrite=False,
    )
    secure_url = result.get("secure_url")
    if not secure_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Cloudinary response missing secure_url",
        )
    return secure_url


def _ensure_food_entry_section(
    db: Session,
    current_user_id: int,
    target_date: DateType,
    summary: str,
    user_text: str,
) -> Entry:
    existing = get_entry_by_user_date_and_mood(db, current_user_id, target_date, "AI汇总")
    block = build_food_info_block(
        summary=summary,
        user_text=user_text,
        timestamp=now_shanghai(),
    )

    if existing is None:
        existing = Entry(
            title=f"{target_date.isoformat()} 聊天回顾",
            content=build_food_info_section([block]),
            date=target_date,
            district="数字分身",
            mood="AI汇总",
            user_id=current_user_id,
            created_at=now_shanghai(),
            updated_at=now_shanghai(),
        )
        return create_entry(db, existing)

    existing.content = merge_food_info_section(existing.content or "", block)
    existing.updated_at = now_shanghai()
    return update_entry(db, existing)


def _upsert_food_photo_section(db: Session, current_user_id: int, shot_date: DateType, photo: FoodPhoto) -> Entry:
    existing = get_entry_by_user_date_and_mood(db, current_user_id, shot_date, "AI汇总")
    if existing is None:
        existing = Entry(
            title=f"{shot_date.isoformat()} 聊天回顾",
            content="",
            date=shot_date,
            district="数字分身",
            photo_url=photo.photo_url,
            mood="AI汇总",
            user_id=current_user_id,
            created_at=now_shanghai(),
            updated_at=now_shanghai(),
        )
        existing = create_entry(db, existing)
    else:
        existing.photo_url = photo.photo_url

    food_photos = get_food_photos_by_user(db, current_user_id)
    matching_photos = [item for item in food_photos if item.shot_date == shot_date]
    food_section = build_food_section(matching_photos)
    existing.content = merge_food_section(existing.content or "", food_section)
    existing.updated_at = now_shanghai()
    return update_entry(db, existing)


def _validated_food_files(files: list[UploadFile]) -> tuple[list[tuple[UploadFile, bytes, str]], list[str]]:

    validated: list[tuple[UploadFile, bytes, str]] = []
    empty_files: list[str] = []
    for candidate in files:
        payload = candidate.file.read() if candidate.file else b""
        if not payload:
            empty_files.append(candidate.filename or "<unnamed>")
            continue

        content_type = (candidate.content_type or "").lower()
        if content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only jpg/png/webp/gif images are allowed",
            )

        if len(payload) > MAX_IMAGE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Image size cannot exceed 5MB",
            )

        validated.append((candidate, payload, content_type))

    return validated, empty_files


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
    files: list[UploadFile] = File(default=[]),
    caption: str | None = Form(default=None),
    comment: str | None = Form(default=None),
    x_track_id: str | None = Header(default=None, alias="X-Track-Id"),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    track_id = (x_track_id or x_request_id or uuid4().hex).strip() or uuid4().hex
    caption_text = (comment or caption or "").strip()
    validated_files, empty_file_names = _validated_food_files(files)
    file_sizes = [len(payload) for _file_obj, payload, _content_type in validated_files]

    logger.info(
        "food_upload_start track_id=%s user_id=%s files=%s non_empty_files=%s empty_files=%s caption_len=%s file_sizes=%s",
        track_id,
        current_user.id,
        len(files),
        len(validated_files),
        len(empty_file_names),
        len(caption_text),
        file_sizes,
    )

    if empty_file_names:
        logger.warning(
            "food_upload_empty_files track_id=%s user_id=%s filenames=%s caption_len=%s",
            track_id,
            current_user.id,
            empty_file_names,
            len(caption_text),
        )

    if not validated_files and not caption_text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="file or caption is required")

    if not validated_files:
        summary = caption_text[:15] or "美食记录"
        entry = _ensure_food_entry_section(
            db,
            current_user.id,
            diary_today_shanghai(),
            summary=summary,
            user_text=caption_text,
        )
        logger.info(
            "food_upload_persisted track_id=%s user_id=%s branch=INFO info_count=1",
            track_id,
            current_user.id,
        )
        return FoodBatchProcessResponse(
            type="INFO",
            summary=summary,
            photos=[],
            track_id=track_id,
            entry_id=entry.id,
            date=entry.date,
            processed_count=0,
            photo_count=0,
            info_count=1,
        )

    created_photos: list[FoodPhoto] = []
    info_count = 0
    info_summaries: list[str] = []
    food_name_candidates: list[str] = []
    photo_group_id = track_id

    for index, (file_obj, payload, content_type) in enumerate(validated_files, start=1):
        item_track_id = f"{track_id}:{index}"
        classification: FoodClassification = classify_food_upload(
            image_bytes=payload,
            content_type=content_type,
            caption=caption_text or None,
            file_name=file_obj.filename,
        )

        logger.info(
            "food_upload_item_classified track_id=%s user_id=%s index=%s filename=%s type=%s summary=%s food_name=%s raw=%s",
            item_track_id,
            current_user.id,
            index,
            file_obj.filename or "<unnamed>",
            classification.type,
            (classification.summary or "")[:80],
            (classification.food_name or "")[:80],
            (classification.raw_text or "")[:180],
        )

        if classification.type == "FOOD_PHOTO":
            if classification.food_name:
                food_name_candidates.append(classification.food_name)

            shot_at = _extract_shot_at(payload) or now_shanghai()
            shot_date = diary_date_for_datetime(shot_at)
            cloudinary_url = _cloudinary_upload(
                payload,
                user_id=current_user.id,
                shot_at=shot_at,
                file_name=file_obj.filename or "food-image",
            )

            food_photo = create_food_photo(
                db,
                FoodPhoto(
                    user_id=current_user.id,
                    group_id=photo_group_id,
                    photo_url=cloudinary_url,
                    caption=None,
                    shot_date=shot_date,
                    shot_at=shot_at,
                    created_at=now_shanghai(),
                    updated_at=now_shanghai(),
                ),
            )
            created_photos.append(food_photo)

            db.add(
                UploadAsset(
                    user_id=current_user.id,
                    kind="food-image",
                    original_name=file_obj.filename or "food-image",
                    content_type=file_obj.content_type or "image/jpeg",
                    size_bytes=len(payload),
                    storage_path=cloudinary_url,
                    public_url=cloudinary_url,
                    created_at=now_shanghai(),
                )
            )
            db.commit()
            _upsert_food_photo_section(db, current_user.id, shot_date, food_photo)
            continue

        summary = classification.summary or caption_text[:15] or "美食记录"
        entry = _ensure_food_entry_section(
            db,
            current_user.id,
            diary_today_shanghai(),
            summary=summary,
            user_text=caption_text,
        )
        info_count += 1
        info_summaries.append(summary)
        logger.info(
            "food_upload_item_persisted track_id=%s user_id=%s index=%s branch=INFO entry_id=%s",
            item_track_id,
            current_user.id,
            index,
            entry.id,
        )

    if created_photos:
        final_comment = compose_food_comment(
            raw_comment=caption_text or None,
            info_summaries=info_summaries,
            food_name=food_name_candidates[0] if food_name_candidates else None,
        )
        if final_comment:
            create_food_photo_comment(
                db,
                FoodPhotoComment(
                    group_id=photo_group_id,
                    user_id=current_user.id,
                    content=final_comment,
                    created_at=now_shanghai(),
                ),
            )

        logger.info(
            "food_upload_persisted track_id=%s user_id=%s branch=FOOD_PHOTO photo_count=%s info_count=%s comment_len=%s",
            track_id,
            current_user.id,
            len(created_photos),
            info_count,
            len(final_comment),
        )
        return FoodBatchProcessResponse(
            type="FOOD_PHOTO",
            food_name=food_name_candidates[0] if food_name_candidates else None,
            summary="；".join(info_summaries[:3]) or None,
            track_id=track_id,
            photos=created_photos,
            processed_count=len(validated_files),
            photo_count=len(created_photos),
            info_count=info_count,
        )

    summary = "；".join(info_summaries[:3]) or caption_text[:15] or "美食记录"
    logger.info(
        "food_upload_persisted track_id=%s user_id=%s branch=INFO info_count=%s",
        track_id,
        current_user.id,
        info_count,
    )
    return FoodBatchProcessResponse(
        type="INFO",
        summary=summary,
        photos=[],
        track_id=track_id,
        processed_count=len(validated_files),
        photo_count=0,
        info_count=info_count,
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


@router.post("/comments/clean-duplicates")
def clean_duplicate_comments(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """清理重复的美食照片评论（仅供管理员使用）"""
    from sqlmodel import func
    
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

