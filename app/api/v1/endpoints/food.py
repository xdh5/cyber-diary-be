from __future__ import annotations

import logging
import threading
from datetime import date as DateType, datetime
from io import BytesIO
from typing import Optional
from uuid import uuid4

import cloudinary
import cloudinary.uploader
import cv2
import exifread
import numpy as np
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
from sqlmodel import Session

from app.api.v1.endpoints.upload import ALLOWED_IMAGE_TYPES, MAX_IMAGE_SIZE
from app.core.auth import get_current_user
from app.core.config import settings
from app.core.timezone import diary_date_for_datetime, now_shanghai
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


def _estimate_image_sharpness(image: np.ndarray) -> float:
    """
    评估图片清晰度：计算 Laplacian 方差
    返回值范围: 0-1000+
    - 50 以下：非常模糊
    - 50-150：中等模糊
    - 150+ : 清晰
    """
    try:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        laplacian = cv2.Laplacian(gray, cv2.CV_64F)
        return float(laplacian.var())
    except Exception:
        return 200.0  # 默认假设清晰


def _enhance_food_photo(payload: bytes) -> bytes:
    """
    食品特化修图：CLAHE + 白平衡 + 色温矫正 + 饱和度增强 + 自适应锐化
    处理逆光、暗光、黄光、蓝光等常见食物拍照场景
    """
    try:
        # 解码图片
        nparr = np.frombuffer(payload, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if image is None:
            return payload
        
        # 1. 自适应直方图均衡 (CLAHE) - 强力处理暗光和逆光
        image_lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(image_lab)
        
        # clipLimit 从 2.5 提高到 3.8，更强的提亮效果
        clahe = cv2.createCLAHE(clipLimit=3.8, tileGridSize=(8, 8))
        l = clahe.apply(l)
        
        # 在 LAB 空间直接增加 L 值 (+15)，确保整体不会太暗
        l = np.clip(l.astype(np.float32) + 15, 0, 255).astype(np.uint8)
        
        image_lab = cv2.merge([l, a, b])
        image = cv2.cvtColor(image_lab, cv2.COLOR_LAB2BGR)
        
        # 2. 白平衡调整 (灰度世界假设) - 修正色温
        image_float = image.astype(np.float32) / 255.0
        b_avg = np.mean(image_float[:, :, 0])
        g_avg = np.mean(image_float[:, :, 1])
        r_avg = np.mean(image_float[:, :, 2])
        
        color_avg = (b_avg + g_avg + r_avg) / 3.0
        
        # 白平衡调整
        if color_avg > 0:
            image_float[:, :, 0] = np.clip(image_float[:, :, 0] * color_avg / (b_avg + 1e-6), 0, 1)
            image_float[:, :, 1] = np.clip(image_float[:, :, 1] * color_avg / (g_avg + 1e-6), 0, 1)
            image_float[:, :, 2] = np.clip(image_float[:, :, 2] * color_avg / (r_avg + 1e-6), 0, 1)
        
        # 3. 色温矫正 - 压低蓝通道，增加红/绿，消除蓝色偏差
        image_float[:, :, 0] = image_float[:, :, 0] * 0.92  # B 通道降低 8%
        image_float[:, :, 2] = np.clip(image_float[:, :, 2] * 1.06, 0, 1)  # R 通道提升 6%
        
        image = (image_float * 255).astype(np.uint8)
        
        # 4. HSV 空间饱和度增强 - 食物看起来更诱人
        image_hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV).astype(np.float32)
        h, s, v = cv2.split(image_hsv)
        
        # 饱和度提升 20%（从 1.12 提高到 1.20）
        s = np.clip(s * 1.20, 0, 255)
        
        image_hsv = cv2.merge([h, s, v]).astype(np.uint8)
        image = cv2.cvtColor(image_hsv, cv2.COLOR_HSV2BGR)
        
        # 5. 自适应锐化 (Unsharp Mask) - 根据清晰度自动调整强度
        sharpness = _estimate_image_sharpness(image)
        
        if sharpness < 80:  # 非常模糊
            strength = 0.6
        elif sharpness < 150:  # 中等模糊
            strength = 0.4
        elif sharpness < 300:  # 略微模糊
            strength = 0.2
        else:  # 清晰
            strength = 0.1
        
        gaussian = cv2.GaussianBlur(image, (0, 0), 0.8)
        image = cv2.addWeighted(image, 1.0 + strength, gaussian, -strength, 0)
        image = np.clip(image, 0, 255).astype(np.uint8)
        
        # 编码为 JPEG
        success, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if success:
            return encoded.tobytes()
        else:
            logger.warning("food_photo_enhancement_encode_failed")
            return payload
            
    except Exception as e:
        # 修图失败则返回原图，不影响上传
        logger.warning("food_photo_enhancement_failed error=%s", str(e)[:100])
        return payload


def _enhance_and_replace_food_photo(
    food_photo_id: int,
    original_payload: bytes,
    user_id: int,
    shot_at: datetime,
    file_name: str,
    track_id: str,
):
    """
    后台异步修图并替换 Cloudinary URL
    失败时保持原图，不影响用户
    """
    try:
        enhanced_payload = _enhance_food_photo(original_payload)

        enhanced_url = _cloudinary_upload(
            enhanced_payload,
            user_id=user_id,
            shot_at=shot_at,
            file_name=file_name,
        )

        # 使用独立会话更新数据库
        db = Session(engine)
        try:
            food_photo = db.get(FoodPhoto, food_photo_id)
            if food_photo:
                food_photo.photo_url = enhanced_url
                food_photo.updated_at = now_shanghai()
                db.commit()
                logger.info(
                    "food_photo_enhancement_replaced track_id=%s photo_id=%s",
                    track_id,
                    food_photo_id,
                )
        finally:
            db.close()

    except Exception as e:
        logger.warning(
            "food_photo_enhancement_async_failed track_id=%s photo_id=%s error=%s",
            track_id,
            food_photo_id,
            str(e)[:100],
        )


def _schedule_food_photo_enhancement(
    food_photo_id: int,
    original_payload: bytes,
    user_id: int,
    shot_at: datetime,
    file_name: str,
    track_id: str,
):
    """在线程中运行后台修图任务"""
    thread = threading.Thread(
        target=_enhance_and_replace_food_photo,
        args=(food_photo_id, original_payload, user_id, shot_at, file_name, track_id),
        daemon=True,
    )
    thread.start()


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
    shot_date: str | None = Form(default=None),
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

    if not validated_files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="files is required")

    created_photos: list[FoodPhoto] = []
    photo_group_id = track_id

    # Parse shot_date parameter
    parsed_shot_date: Optional[DateType] = None
    if shot_date:
        try:
            parsed_shot_date = DateType.fromisoformat(shot_date)
        except ValueError:
            logger.warning("food_upload_invalid_shot_date track_id=%s user_id=%s shot_date=%s", track_id, current_user.id, shot_date)

    for index, (file_obj, payload, content_type) in enumerate(validated_files, start=1):
        item_track_id = f"{track_id}:{index}"
        
        # Extract shot time from EXIF
        shot_at = _extract_shot_at(payload) or now_shanghai()
        
        # Determine shot_date: use parameter if provided, otherwise derive from shot_at
        if parsed_shot_date:
            # Replace date part with provided shot_date, keep time part from EXIF
            shot_at = datetime.combine(parsed_shot_date, shot_at.time())
            shot_date_val = parsed_shot_date
        else:
            shot_date_val = diary_date_for_datetime(shot_at)

        # 先上传原始图到 Cloudinary（快速），立即返回给前端
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
                caption=caption_text or None,
                shot_date=shot_date_val,
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

        # 后台异步修图并替换（不阻塞响应）
        _schedule_food_photo_enhancement(
            food_photo.id,
            payload,
            current_user.id,
            shot_at,
            file_obj.filename or "food-image",
            track_id,
        )

        logger.info(
            "food_upload_item_persisted track_id=%s user_id=%s index=%s filename=%s shot_date=%s",
            item_track_id,
            current_user.id,
            index,
            file_obj.filename or "<unnamed>",
            shot_date_val.isoformat(),
        )

    logger.info(
        "food_upload_completed track_id=%s user_id=%s photo_count=%s",
        track_id,
        current_user.id,
        len(created_photos),
    )

    return FoodBatchProcessResponse(
        type="FOOD_PHOTO",
        track_id=track_id,
        photos=created_photos,
        processed_count=len(validated_files),
        photo_count=len(created_photos),
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

