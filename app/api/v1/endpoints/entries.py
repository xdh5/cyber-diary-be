from datetime import datetime
from typing import List
import html
import re

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.core.auth import get_current_user
from app.core.content import extract_preview_text, normalize_entry_content
from app.core.timezone import now_shanghai
from app.crud.crud import (
    create_entry,
    delete_entry,
    get_entries_by_user,
    get_entry_by_id_and_user,
    update_entry,
)
from app.db.session import get_db
from app.models.models import Entry
from app.schemas.schemas import EntryCreate, EntryResponse, EntryUpdate

router = APIRouter()


def _is_image_or_url_line(text: str) -> bool:
    return bool(
        re.match(r'^!\[[\s\S]*?\]\([\s\S]*?\)$', text)
        or re.match(r'^https?://\S+$', text)
    )


def _normalize_candidate_title(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith('#'):
        cleaned = cleaned.lstrip('#').strip()

    cleaned = re.sub(r'<[^>]+>', '', cleaned)
    cleaned = html.unescape(cleaned)

    cleaned = re.sub(r'!\[[\s\S]*?\]\([\s\S]*?\)', '', cleaned)
    cleaned = re.sub(r'\[(.*?)\]\([\s\S]*?\)', r'\1', cleaned)
    cleaned = re.sub(r'https?://\S+', '', cleaned)
    cleaned = re.sub(r'`{1,3}(.*?)`{1,3}', r'\1', cleaned)
    cleaned = re.sub(r'(\*\*|__)(.*?)\1', r'\2', cleaned)
    cleaned = re.sub(r'(\*|_)(.*?)\1', r'\2', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned[:255]


def _resolve_preview_text(content: str) -> str:
    return extract_preview_text(content, limit=255)


def _autofix_dirty_title(entry: Entry) -> bool:
    normalized_title = resolve_entry_title(entry.content, entry.title)
    if entry.title != normalized_title:
        entry.title = normalized_title
        entry.updated_at = now_shanghai()
        return True
    return False


def _to_entry_response(entry: Entry) -> dict:
    return {
        'id': entry.id,
        'title': entry.title,
        'content': entry.content,
        'content_format': entry.content_format,
        'date': entry.date,
        'district': entry.district,
        'photo_url': entry.photo_url,
        'mood': entry.mood,
        'created_at': entry.created_at,
        'updated_at': entry.updated_at,
        'preview_text': _resolve_preview_text(entry.content),
    }


@router.get("/", response_model=List[EntryResponse])
def list_entries(db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    entries = get_entries_by_user(db, current_user.id)

    changed = False
    for entry in entries:
        if _autofix_dirty_title(entry):
            db.add(entry)
            changed = True

    if changed:
        db.commit()
        for entry in entries:
            db.refresh(entry)

    return [_to_entry_response(entry) for entry in entries]


@router.get("/{entry_id}", response_model=EntryResponse)
def get_entry(entry_id: int, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    entry = get_entry_by_id_and_user(db, entry_id, current_user.id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")

    if _autofix_dirty_title(entry):
        db.add(entry)
        db.commit()
        db.refresh(entry)

    return _to_entry_response(entry)


def resolve_entry_title(content: str, title: str | None = None) -> str:
    if title:
        normalized_title = _normalize_candidate_title(title)
        if normalized_title:
            return normalized_title

    for line in content.splitlines():
        clean_line = line.strip()
        if not clean_line:
            continue

        if _is_image_or_url_line(clean_line):
            continue

        normalized_line = _normalize_candidate_title(clean_line)
        if normalized_line:
            return normalized_line

    return now_shanghai().strftime('%Y-%m-%d 日记')


@router.post("/", status_code=status.HTTP_201_CREATED, response_model=EntryResponse)
def create_entry_endpoint(entry_in: EntryCreate, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    normalized_content = normalize_entry_content(entry_in.content)
    title = resolve_entry_title(normalized_content, entry_in.title)
    entry_data = entry_in.dict(exclude={'title'}, exclude_none=True)
    entry_data['content'] = normalized_content
    entry = Entry(**entry_data, title=title, user_id=current_user.id)
    entry = create_entry(db, entry)
    return _to_entry_response(entry)


@router.put("/{entry_id}", response_model=EntryResponse)
def update_entry_endpoint(entry_id: int, entry_in: EntryUpdate, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    entry = get_entry_by_id_and_user(db, entry_id, current_user.id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")

    update_data = entry_in.dict(exclude_unset=True)
    if 'content' in update_data and update_data['content'] is not None:
        update_data['content'] = normalize_entry_content(update_data['content'])

    for field, value in update_data.items():
        setattr(entry, field, value)

    if 'title' in update_data or 'content' in update_data:
        entry.title = resolve_entry_title(entry.content, entry.title)

    entry = update_entry(db, entry)
    return _to_entry_response(entry)


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_entry_endpoint(entry_id: int, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    entry = get_entry_by_id_and_user(db, entry_id, current_user.id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")

    delete_entry(db, entry)