from datetime import date
from typing import List, Optional

from sqlmodel import Session, select

from app.core.content import extract_first_image_url, normalize_entry_content
from app.core.timezone import diary_date_for_datetime, ensure_shanghai_tz, now_shanghai
from app.models.models import ChatLog, EmailVerificationCode, Entry, FoodPhoto, FoodPhotoComment, User


# User CRUD
def get_user_by_email(db: Session, email: str) -> Optional[User]:
    return db.exec(select(User).where(User.email == email)).first()


def get_user_by_google_id(db: Session, google_id: str) -> Optional[User]:
    return db.exec(select(User).where(User.google_id == google_id)).first()


def create_user(db: Session, user: User) -> User:
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def update_user(db: Session, user: User) -> User:
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# Email Verification Code CRUD
def get_verification_code_by_email(db: Session, email: str) -> Optional[EmailVerificationCode]:
    return db.exec(select(EmailVerificationCode).where(EmailVerificationCode.email == email)).first()


def create_or_update_verification_code(db: Session, code: EmailVerificationCode) -> EmailVerificationCode:
    existing = get_verification_code_by_email(db, code.email)
    if existing:
        existing.code = code.code
        existing.expires_at = code.expires_at
        db.add(existing)
        db.commit()
        return existing
    else:
        db.add(code)
        db.commit()
        db.refresh(code)
        return code


def delete_verification_code(db: Session, code: EmailVerificationCode):
    db.delete(code)
    db.commit()


# Entry CRUD
def get_entries_by_user(db: Session, user_id: int) -> List[Entry]:
    return db.exec(
        select(Entry)
        .where(Entry.user_id == user_id)
        .order_by(Entry.created_at.desc())
    ).all()


def get_entry_by_id_and_user(db: Session, entry_id: int, user_id: int) -> Optional[Entry]:
    return db.exec(
        select(Entry).where(Entry.id == entry_id, Entry.user_id == user_id)
    ).first()


def get_entry_by_user_date_and_mood(db: Session, user_id: int, entry_date: date, mood: str) -> Optional[Entry]:
    return db.exec(
        select(Entry).where(
            Entry.user_id == user_id,
            Entry.date == entry_date,
            Entry.mood == mood,
        )
    ).first()


def get_entries_by_user_and_date(db: Session, user_id: int, entry_date: date) -> List[Entry]:
    """Get all entries for a specific user and date, ordered by creation time."""
    return db.exec(
        select(Entry).where(
            Entry.user_id == user_id,
            Entry.date == entry_date,
        ).order_by(Entry.created_at.asc())
    ).all()


def create_entry(db: Session, entry: Entry) -> Entry:
    entry.content = normalize_entry_content(entry.content)
    entry.content_format = "html"
    if (not entry.photo_url) and entry.content:
        first_image = extract_first_image_url(entry.content)
        if first_image:
            entry.photo_url = first_image
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def update_entry(db: Session, entry: Entry) -> Entry:
    entry.content = normalize_entry_content(entry.content)
    entry.content_format = "html"
    if (not entry.photo_url) and entry.content:
        first_image = extract_first_image_url(entry.content)
        if first_image:
            entry.photo_url = first_image
    entry.updated_at = now_shanghai()
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def delete_entry(db: Session, entry: Entry):
    db.delete(entry)
    db.commit()


def create_food_photo(db: Session, photo: FoodPhoto) -> FoodPhoto:
    db.add(photo)
    db.commit()
    db.refresh(photo)
    return photo


def get_food_photos_by_user(db: Session, user_id: int) -> List[FoodPhoto]:
    return db.exec(
        select(FoodPhoto)
        .where(FoodPhoto.user_id == user_id)
        .order_by(FoodPhoto.created_at.desc())
    ).all()


def get_food_photos_by_user_and_date(db: Session, user_id: int, shot_date: date) -> List[FoodPhoto]:
    return db.exec(
        select(FoodPhoto)
        .where(
            FoodPhoto.user_id == user_id,
            FoodPhoto.shot_date == shot_date,
        )
        .order_by(FoodPhoto.created_at.asc())
    ).all()


def create_chat_log(db: Session, chat_log: ChatLog) -> ChatLog:
    db.add(chat_log)
    db.commit()
    db.refresh(chat_log)
    return chat_log


def get_chat_logs_by_user(db: Session, user_id: int, limit: int = 50) -> List[ChatLog]:
    return db.exec(
        select(ChatLog)
        .where(ChatLog.user_id == user_id)
        .order_by(ChatLog.created_at.asc())
    ).all()[-limit:]


def get_chat_logs_page_by_user(
    db: Session,
    user_id: int,
    limit: int = 50,
    before_id: Optional[int] = None,
) -> List[ChatLog]:
    statement = select(ChatLog).where(ChatLog.user_id == user_id)
    if before_id is not None:
        statement = statement.where(ChatLog.id < before_id)

    logs = db.exec(
        statement
        .order_by(ChatLog.id.desc())
        .limit(limit)
    ).all()
    return list(reversed(logs))


def get_chat_logs_by_user_and_date(db: Session, user_id: int, day: date) -> List[ChatLog]:
    logs = db.exec(
        select(ChatLog)
        .where(ChatLog.user_id == user_id)
        .order_by(ChatLog.created_at.asc())
    ).all()
    return [
        log for log in logs
        if diary_date_for_datetime(log.created_at) == day
    ]


def search_chat_logs_by_user(
    db: Session,
    user_id: int,
    keyword: str,
    limit: int = 50,
) -> List[ChatLog]:
    query = keyword.strip()
    if not query:
        return []

    return db.exec(
        select(ChatLog)
        .where(
            ChatLog.user_id == user_id,
            ChatLog.content.contains(query),
        )
        .order_by(ChatLog.id.desc())
        .limit(limit)
    ).all()


def get_all_users(db: Session) -> List[User]:
    return db.exec(select(User)).all()


# FoodPhotoComment CRUD
def get_food_photo_comments(db: Session, group_id: str) -> List[FoodPhotoComment]:
    return db.exec(
        select(FoodPhotoComment)
        .where(FoodPhotoComment.group_id == group_id)
        .order_by(FoodPhotoComment.created_at.asc())
    ).all()


def create_food_photo_comment(db: Session, comment: FoodPhotoComment) -> FoodPhotoComment:
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return comment
