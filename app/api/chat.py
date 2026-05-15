from datetime import date
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlmodel import Session

from app.core.agent import run_chat_agent
from app.core.auth import get_current_user
from app.core.storage import save_user_upload
from app.core.llm import LLMError
from app.crud.crud import (
    get_chat_logs_by_user_and_date,
    get_chat_logs_page_by_user,
    search_chat_logs_by_user,
    update_user,
)
from app.db.session import get_db
from app.models.models import ChatLog, UploadAsset
from app.schemas.schemas import (
    AgentSettingsResponse,
    AgentSettingsUpdateRequest,
    ChatLogResponse,
    ChatRequest,
    ChatResponse,
)


logger = logging.getLogger("cyber_diary.chat")
router = APIRouter()

ALLOWED_IMAGE_TYPES = {
    'image/jpeg': 'jpg',
    'image/png': 'png',
    'image/webp': 'webp',
    'image/gif': 'gif',
}
MAX_IMAGE_SIZE = 5 * 1024 * 1024
MAX_ATTACHMENT_SIZE = 20 * 1024 * 1024


def _normalize_llm_image_url(raw_url: str, request: Request) -> str:
    url = (raw_url or '').strip()
    if not url:
        return ''

    if url.startswith('http://') or url.startswith('https://'):
        return url

    if url.startswith('/'):
        return str(request.base_url).rstrip('/') + url

    return ''


def _upload_image_bytes(file_bytes: bytes, content_type: str, user_id: int, file_name: str) -> tuple[str, str]:
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Only jpg/png/webp/gif images are allowed',
        )

    public_url, storage_path = save_user_upload(
        payload=file_bytes,
        user_id=user_id,
        file_name=file_name,
        content_type=content_type,
        category='chat-images',
    )
    return public_url, storage_path


async def _parse_chat_request(request: Request, user_id: int, db: Session) -> tuple[str, list[str], str]:
    content_type = request.headers.get('content-type', '')
    if 'multipart/form-data' not in content_type:
        payload = await request.json()
        message = str(payload.get('message') or '').strip()
        image_urls = [str(url).strip() for url in payload.get('image_urls') or [] if str(url).strip()]
        return message, image_urls, ''

    form = await request.form()
    message = str(form.get('message') or '').strip()
    image_urls = []
    attachment_context_blocks: list[str] = []

    uploaded_files = form.getlist('attachments')
    for item in uploaded_files:
        if not hasattr(item, 'read') or not hasattr(item, 'filename'):
            continue

        payload = await item.read()
        if not payload:
            continue

        if len(payload) > MAX_ATTACHMENT_SIZE:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f'{item.filename} is too large')

        content_type = (item.content_type or '').lower()
        if content_type.startswith('image/'):
            if len(payload) > MAX_IMAGE_SIZE:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f'{item.filename} image is too large')
            image_url, image_storage_path = _upload_image_bytes(payload, content_type, user_id, item.filename or 'image')
            image_urls.append(image_url)
            db.add(
                UploadAsset(
                    user_id=user_id,
                    kind='chat-image',
                    original_name=item.filename or 'image',
                    content_type=content_type or 'application/octet-stream',
                    size_bytes=len(payload),
                    storage_path=image_storage_path,
                    public_url=image_url,
                )
            )
            continue

        file_url, file_storage_path = save_user_upload(
            payload=payload,
            user_id=user_id,
            file_name=item.filename or 'attachment',
            content_type=content_type or 'application/octet-stream',
            category='chat-files',
        )
        db.add(
            UploadAsset(
                user_id=user_id,
                kind='chat-file',
                original_name=item.filename or 'attachment',
                content_type=content_type or 'application/octet-stream',
                size_bytes=len(payload),
                storage_path=file_storage_path,
                public_url=file_url,
            )
        )
        attachment_context_blocks.append(
            f'【文件】{item.filename or "attachment"}\n[file_url] {file_url}'
        )

    db.commit()

    attachment_context = '\n\n'.join(attachment_context_blocks).strip()
    return message, image_urls, attachment_context


@router.post('/chat', response_model=ChatResponse)
async def chat(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    track_id = getattr(request.state, 'track_id', '-')
    message, image_urls, attachment_context = await _parse_chat_request(request, current_user.id, db)
    if attachment_context:
        message = f'{message}\n\n{attachment_context}'.strip() if message else attachment_context
    if not message:
        logger.warning('chat.bad_request user_id=%s track_id=%s reason=empty_message', current_user.id, track_id)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='message is required')

    normalized_image_urls = [
        normalized_url
        for normalized_url in (
            _normalize_llm_image_url(image_url, request)
            for image_url in image_urls
        )
        if normalized_url
    ]

    logger.info(
        'chat.request user_id=%s track_id=%s message_len=%s input_images=%s normalized_images=%s',
        current_user.id,
        track_id,
        len(message),
        len(image_urls),
        len(normalized_image_urls),
    )

    try:
        answer = run_chat_agent(
            db,
            current_user.id,
            message,
            user_system_prompt=current_user.agent_system_prompt,
            image_urls=normalized_image_urls,
        )
    except LLMError:
        logger.exception('chat.agent_failed user_id=%s track_id=%s', current_user.id, track_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI service unavailable",
        )
    logger.info('chat.response user_id=%s track_id=%s answer_len=%s', current_user.id, track_id, len(answer))
    return ChatResponse(answer=answer)


@router.get("/chat/logs", response_model=list[ChatLogResponse])
def list_chat_logs(
    day: str = Query(..., description="YYYY-MM-DD in Asia/Shanghai"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        target_date = date.fromisoformat(day)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid date format")

    return get_chat_logs_by_user_and_date(db, current_user.id, target_date)


@router.get("/chat/logs/page", response_model=list[ChatLogResponse])
def list_chat_logs_page(
    limit: int = Query(default=50, ge=1, le=200),
    before_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return get_chat_logs_page_by_user(
        db,
        current_user.id,
        limit=limit,
        before_id=before_id,
    )


@router.get("/chat/logs/search", response_model=list[ChatLogResponse])
def search_chat_logs(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return search_chat_logs_by_user(db, current_user.id, q, limit=limit)


@router.get("/chat/agent/settings", response_model=AgentSettingsResponse)
def get_agent_settings(current_user=Depends(get_current_user)):
    return AgentSettingsResponse(
        agent_name=(current_user.agent_name or "Agent").strip() or "Agent",
        agent_system_prompt=current_user.agent_system_prompt,
    )


@router.patch("/chat/agent/settings", response_model=AgentSettingsResponse)
def update_agent_settings(
    payload: AgentSettingsUpdateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if payload.agent_name is not None:
        next_name = payload.agent_name.strip() or "Agent"
        current_user.agent_name = next_name[:100]

    if payload.agent_system_prompt is not None:
        current_user.agent_system_prompt = payload.agent_system_prompt.strip() or None

    update_user(db, current_user)
    return AgentSettingsResponse(
        agent_name=(current_user.agent_name or "Agent").strip() or "Agent",
        agent_system_prompt=current_user.agent_system_prompt,
    )
