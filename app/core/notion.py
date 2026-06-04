from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import httpx
from fastapi import HTTPException, status

from app.core.config import settings

logger = logging.getLogger("cyber_diary.notion")

NOTION_API_BASE_URL = "https://api.notion.com/v1"
DEFAULT_NOTION_VERSION = "2022-06-28"


@dataclass(slots=True)
class NotionFileUploadResult:
    file_upload_id: str
    filename: str


@dataclass(slots=True)
class NotionDietPageResult:
    page_id: str
    page_url: Optional[str]
    file_upload_ids: list[str]


def _require_notion_settings() -> None:
    if settings.notion_ready():
        return

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Notion is not configured",
    )


def _get_headers() -> dict[str, str]:
    _require_notion_settings()
    return {
        "Authorization": f"Bearer {settings.NOTION_API_KEY}",
        "Notion-Version": settings.NOTION_VERSION or DEFAULT_NOTION_VERSION,
    }


def _extract_object_id(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("id", "file_upload_id", "page_id"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        nested = payload.get("file_upload")
        if isinstance(nested, dict):
            value = nested.get("id")
            if isinstance(value, str) and value.strip():
                return value.strip()

    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="Notion response did not contain an id",
    )


def _format_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        payload = None

    if isinstance(payload, dict):
        for key in ("message", "error", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    text = response.text.strip()
    return text or f"HTTP {response.status_code}"


async def _raise_notion_error(response: httpx.Response, action: str) -> None:
    message = _format_error_message(response)
    logger.error("notion.%s_failed status=%s message=%s", action, response.status_code, message)
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"Notion {action} failed: {message}",
    )


async def register_file_upload(client: httpx.AsyncClient, *, filename: str, content_type: str) -> str:
    try:
        response = await client.post(
            "/file_uploads",
            json={
                "filename": filename,
                "content_type": content_type,
                "mode": "single_part",
            },
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Notion register_file_upload request failed: {exc}",
        ) from exc

    if response.is_success:
        return _extract_object_id(response.json())

    await _raise_notion_error(response, "register_file_upload")
    raise AssertionError("unreachable")


async def send_file_upload(
    client: httpx.AsyncClient,
    *,
    file_upload_id: str,
    filename: str,
    content_type: str,
    payload: bytes,
) -> None:
    try:
        response = await client.post(
            f"/file_uploads/{file_upload_id}/send",
            files={"file": (filename, payload, content_type)},
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Notion send_file_upload request failed: {exc}",
        ) from exc

    if response.is_success:
        return

    await _raise_notion_error(response, "send_file_upload")


def _build_page_properties(
    *,
    food_name: str,
    calories: Optional[int],
    meal_type: str,
    date: str,
    feeling: str,
    has_photo: bool,
) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "名称": {
            "title": [
                {
                    "type": "text",
                    "text": {"content": food_name},
                },
            ],
        },
        "日期": {
            "date": {
                "start": date,
            },
        },
        "餐次": {
            "select": {
                "name": meal_type,
            },
        },
        "感受": {
            "rich_text": (
                [
                    {
                        "type": "text",
                        "text": {"content": feeling},
                    },
                ]
                if feeling.strip()
                else []
            ),
        },
        "有照片": {
            "checkbox": has_photo,
        },
    }

    if calories is not None:
        properties["热量"] = {"number": calories}

    return properties


def _build_page_children(feeling: str, file_upload_ids: list[str]) -> list[dict[str, Any]]:
    children: list[dict[str, Any]] = []

    if feeling.strip():
        children.append(
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {"content": feeling},
                        },
                    ],
                },
            }
        )

    for file_upload_id in file_upload_ids:
        children.append(
            {
                "object": "block",
                "type": "image",
                "image": {
                    "type": "file_upload",
                    "file_upload": {
                        "id": file_upload_id,
                    },
                },
            }
        )

    return children


async def create_diet_page(
    client: httpx.AsyncClient,
    *,
    food_name: str,
    calories: Optional[int],
    meal_type: str,
    date: str,
    feeling: str,
    file_upload_ids: list[str],
) -> NotionDietPageResult:
    try:
        response = await client.post(
            "/pages",
            json={
                "parent": {
                    "database_id": settings.NOTION_DIET_DATABASE_ID,
                },
                "properties": _build_page_properties(
                    food_name=food_name,
                    calories=calories,
                    meal_type=meal_type,
                    date=date,
                    feeling=feeling,
                    has_photo=len(file_upload_ids) > 0,
                ),
                "children": _build_page_children(feeling, file_upload_ids),
            },
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Notion create_page request failed: {exc}",
        ) from exc

    if response.is_success:
        payload = response.json()
        return NotionDietPageResult(
            page_id=_extract_object_id(payload),
            page_url=payload.get("url"),
            file_upload_ids=file_upload_ids,
        )

    await _raise_notion_error(response, "create_page")
    raise AssertionError("unreachable")
