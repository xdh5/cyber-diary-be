from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlmodel import Session

from app.core.auth import get_current_user
from app.core.storage import save_user_upload
from app.core.timezone import now_shanghai
from app.db.session import get_db
from app.models.models import UploadAsset

router = APIRouter()

ALLOWED_IMAGE_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB


@router.post("/image")
async def upload_image(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    content_type = file.content_type or ""
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only jpg/png/webp/gif images are allowed",
        )

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file")

    if len(payload) > MAX_IMAGE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Image size cannot exceed 5MB",
        )

    public_url, storage_path = save_user_upload(
        payload=payload,
        user_id=current_user.id,
        file_name=file.filename or "image",
        content_type=content_type,
        category="chat-images",
    )

    db.add(
        UploadAsset(
            user_id=current_user.id,
            kind="chat-image",
            original_name=file.filename or "image",
            content_type=content_type,
            size_bytes=len(payload),
            storage_path=storage_path,
            public_url=public_url,
            created_at=now_shanghai(),
        )
    )
    db.commit()

    return {"url": f"{str(request.base_url).rstrip('/')}{public_url}"}
