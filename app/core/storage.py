from __future__ import annotations

import mimetypes
import re
import uuid
from datetime import datetime
from pathlib import Path

DATA_ROOT = Path(__file__).resolve().parent.parent.parent / "data"
UPLOADS_ROOT = DATA_ROOT / "uploads"
UPLOADS_ROOT.mkdir(parents=True, exist_ok=True)


def _sanitize_filename(file_name: str) -> str:
    safe_name = Path(file_name or "upload").name
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", safe_name)
    return safe_name or "upload"


def _detect_extension(content_type: str, file_name: str) -> str:
    suffix = Path(file_name or "").suffix.strip().lower()
    if suffix:
        return suffix

    guess = mimetypes.guess_extension(content_type or "")
    if guess:
        return guess.lower()
    return ""


def save_user_upload(
    *,
    payload: bytes,
    user_id: int,
    file_name: str,
    content_type: str,
    category: str,
) -> tuple[str, str]:
    """Save file under per-user upload folder.

    Returns:
      - public_url: browser URL path for accessing the file
      - storage_path: absolute file path in server filesystem
    """
    safe_name = _sanitize_filename(file_name)
    ext = _detect_extension(content_type, safe_name)
    unique_name = f"{uuid.uuid4().hex}{ext}"

    now = datetime.utcnow()
    relative_dir = Path(f"user_{user_id}") / category / now.strftime("%Y") / now.strftime("%m") / now.strftime("%d")
    absolute_dir = UPLOADS_ROOT / relative_dir
    absolute_dir.mkdir(parents=True, exist_ok=True)

    absolute_path = absolute_dir / unique_name
    absolute_path.write_bytes(payload)

    public_url = f"/uploads/{relative_dir.as_posix()}/{unique_name}"
    return public_url, str(absolute_path)
