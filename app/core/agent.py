import os
from typing import Any

import requests
from sqlmodel import Session

from app.core.llm import LLMError, _build_doubao_url
from app.crud.crud import create_chat_log, get_chat_logs_by_user
from app.models.models import ChatLog
from app.core.timezone import diary_today_shanghai


CHAT_ONLY_SYSTEM_PROMPT = """你是 Cyber Diary 的聊天助手。
请直接自然回复用户，不要输出工具调用、JSON、代码块、action 字段或系统指令文本。
"""
CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = int(os.getenv("DOUBAO_READ_TIMEOUT_SECONDS", "45"))


def _normalize_llm_image_url(url: str) -> str:
    candidate = (url or "").strip()
    if not candidate:
        return ""
    if candidate.startswith("http://") or candidate.startswith("https://"):
        return candidate
    if candidate.startswith("data:"):
        return candidate
    return ""


def _extract_text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            text = _extract_text_from_content(item)
            if text:
                parts.append(text)
        return "\n".join(parts).strip()

    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()

        nested_content = content.get("content")
        nested_text = _extract_text_from_content(nested_content)
        if nested_text:
            return nested_text

        return ""

    return ""


def _doubao_api_key() -> str:
    api_key = (os.getenv("DOUBAO_API_KEY") or "").strip()
    if not api_key:
        raise LLMError("Missing DOUBAO_API_KEY")
    return api_key

def _doubao_model() -> str:
    return (os.getenv("DOUBAO_CHAT_MODEL", "doubao-1-5-thinking-vision-pro-250428").strip() or "doubao-1-5-thinking-vision-pro-250428")


def _call_doubao_chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": _doubao_model(),
        "messages": messages,
        "temperature": 0.2,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    response = requests.post(
        _build_doubao_url("chat/completions"),
        headers={"Authorization": f"Bearer {_doubao_api_key()}", "Content-Type": "application/json"},
        json=payload,
        timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
    )

    if response.status_code >= 400:
        try:
            error_data = response.json()
            error_message = error_data.get("error", {}).get("message") or response.text
        except Exception:
            error_message = response.text
        raise LLMError(f"Doubao {response.status_code}: {error_message}")

    try:
        return response.json()
    except Exception as exc:
        raise LLMError(f"Doubao invalid JSON: {exc.__class__.__name__}") from exc


def _extract_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            text = _extract_message_content(item)
            if text:
                parts.append(text)
        return "\n".join(parts).strip()

    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()

        nested_content = content.get("content")
        nested_text = _extract_message_content(nested_content)
        if nested_text:
            return nested_text

    return ""


def _generate_chat_only_reply(
    message: str,
    recent_context: str,
    target_day: str,
    user_system_prompt: str | None = None,
    image_urls: list[str] | None = None,
) -> str:
    chat_input = (
        f"当前日期: {target_day}\n\n"
        f"最近聊天上下文:\n{recent_context or '无'}\n\n"
        f"用户最新消息:\n{message}"
    )

    user_content: str | list[dict[str, Any]] = chat_input
    clean_image_urls = [
        normalized_url
        for normalized_url in (
            _normalize_llm_image_url(url)
            for url in (image_urls or [])
        )
        if normalized_url
    ]
    if clean_image_urls:
        user_content = [
            *[
                {"type": "image_url", "image_url": {"url": image_url}}
                for image_url in clean_image_urls
            ],
            {"type": "text", "text": chat_input},
        ]

    messages: list[dict[str, Any]] = []
    custom_prompt = (user_system_prompt or "").strip()
    if custom_prompt:
        messages.append({"role": "system", "content": custom_prompt})
    messages.extend(
        [
            {"role": "system", "content": CHAT_ONLY_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
    )

    result = _call_doubao_chat(messages, tools=None)
    choices = result.get("choices") or []
    if not choices:
        raise LLMError("Empty agent response")

    message_data = choices[0].get("message") or {}
    answer = _extract_message_content(message_data.get("content", "")).strip()
    if not answer:
        raise LLMError("Empty agent response")

    return answer


def run_chat_agent(
    db: Session,
    current_user_id: int,
    message: str,
    user_system_prompt: str | None = None,
    image_urls: list[str] | None = None,
) -> str:
    clean_image_urls = [
        normalized_url
        for normalized_url in (
            _normalize_llm_image_url(url)
            for url in (image_urls or [])
        )
        if normalized_url
    ]
    user_log_content = message
    if clean_image_urls:
        image_lines = "\n".join(f"[image] {url}" for url in clean_image_urls)
        user_log_content = f"{message}\n{image_lines}".strip()

    create_chat_log(
        db,
        ChatLog(user_id=current_user_id, role="user", content=user_log_content),
    )

    recent_logs = get_chat_logs_by_user(db, current_user_id, limit=30)
    recent_context_lines: list[str] = []
    for log in recent_logs:
        if log.role == "user":
            recent_context_lines.append(f"我: {log.content}")
        elif log.role == "assistant":
            recent_context_lines.append(f"AI: {log.content}")
    recent_context = "\n".join(recent_context_lines)
    target_day = diary_today_shanghai()

    answer = _generate_chat_only_reply(
        message,
        recent_context,
        target_day.isoformat(),
        user_system_prompt=user_system_prompt,
        image_urls=clean_image_urls,
    )
    create_chat_log(
        db,
        ChatLog(user_id=current_user_id, role="assistant", content=answer),
    )
    return answer