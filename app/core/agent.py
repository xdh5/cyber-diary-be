import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import date as DateType
from typing import Any

import requests
from sqlmodel import Session

from app.core.diary import (
    DIARY_RESPONSE_PREFIX,
    build_recent_context,
    build_diary_source_logs,
    generate_or_update_daily_diary,
)
from app.core.llm import LLMError, _build_doubao_url
from app.crud.crud import create_chat_log, get_chat_logs_by_user, get_chat_logs_by_user_and_date
from app.models.models import ChatLog
from app.core.timezone import diary_today_shanghai


SYSTEM_PROMPT = """你是 Cyber Diary 的聊天 agent。

你有一个可用工具 generate_diary，用于把聊天整理或更新成日记。
你需要根据用户最新输入和上下文自行判断：
- 如果用户是在正常聊天，就直接自然回答，不调用工具。
- 如果用户是在要求写日记/整理日记/更新日记，就调用 generate_diary。

关键：用户可能会指定想要生成的日期（如"昨天"、"5月1日"、"上周一"、"两周前"等）。
你要仔细理解用户说的日期，将其转换为 ISO 格式 (YYYY-MM-DD)，并通过 target_date 参数传给 generate_diary。
- 如果用户没有明确指定日期，就不填 target_date，系统会默认用当天。
- 如果用户说的是相对日期（如"昨天"），你要根据当前日期计算出具体的 ISO 日期。

如果调用 generate_diary，直接把工具返回内容作为最终回复。
"""

CHAT_ONLY_SYSTEM_PROMPT = """你是 Cyber Diary 的聊天助手。
用户当前是在正常聊天，不是在要求写日记。
请直接自然回复，不要输出工具调用、JSON、代码块、action 字段或系统指令文本。
"""

AGENT_TIMEOUT_SECONDS = int(os.getenv("DOUBAO_AGENT_TIMEOUT_SECONDS", "45"))
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


def _parse_target_date(target_date_str: str) -> DateType | None:
    """尝试解析ISO格式的日期字符串"""
    if not target_date_str or not target_date_str.strip():
        return None
    try:
        return DateType.fromisoformat(target_date_str.strip())
    except (ValueError, AttributeError):
        return None

def _is_diary_intent(user_message: str) -> bool:
    text = user_message.strip()
    if not text:
        return False

    direct_patterns = [
        r"(写|记|生成|整理|更新|汇总|总结).{0,8}日记",
        r"(把|将).{0,12}(聊天|今天|这些).{0,8}(写|整理|汇总).{0,6}成日记",
        r"日记.{0,6}(写|整理|更新|生成)",
    ]
    for pattern in direct_patterns:
        if re.search(pattern, text):
            return True

    exact_commands = {
        "写日记",
        "日记",
        "帮我写日记",
        "整理日记",
        "更新日记",
        "生成日记",
    }
    return text in exact_commands


def _generate_normal_reply(messages: list[dict[str, Any]]) -> str:
    result = _call_doubao_chat(messages)
    choices = result.get("choices") or []
    if not choices:
        raise LLMError("Empty agent response")

    message_data = choices[0].get("message") or {}
    answer = _extract_message_content(message_data.get("content", "")).strip()
    if not answer:
        raise LLMError("Empty agent response")

    return answer


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
    recent_context = build_recent_context(recent_logs)
    target_day = diary_today_shanghai()

    if not _is_diary_intent(message):
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

    agent_input = (
        f"当前日期: {target_day.isoformat()}\n\n"
        f"最近聊天上下文:\n{recent_context or '无'}\n\n"
        f"用户最新消息:\n{message}"
    )

    messages: list[dict[str, Any]] = []
    custom_prompt = (user_system_prompt or "").strip()
    if custom_prompt:
        messages.append({"role": "system", "content": custom_prompt})
    messages.extend(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": agent_input},
        ]
    )

    tools = [
        {
            "type": "function",
            "function": {
                "name": "generate_diary",
                "description": "Generate or update diary from chat history. Can target a specific date or today.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_request": {
                            "type": "string",
                            "description": "The user's original request that triggered diary generation.",
                        },
                        "target_date": {
                            "type": "string",
                            "description": "Target date in ISO format (YYYY-MM-DD). If user specified a relative date like '昨天', '5月1日', convert to ISO format. Default is today.",
                        }
                    },
                    "required": ["user_request"],
                    "additionalProperties": False,
                },
            },
        }
    ]

    try:
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(_call_doubao_chat, messages, tools)
        try:
            result = future.result(timeout=AGENT_TIMEOUT_SECONDS)
        except FuturesTimeoutError as exc:
            future.cancel()
            raise LLMError(
                f"Agent request timed out after {AGENT_TIMEOUT_SECONDS}s"
            ) from exc
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        choices = result.get("choices") or []
        if not choices:
            raise LLMError("Empty agent response")

        message_data = choices[0].get("message") or {}
        tool_calls = message_data.get("tool_calls") or []
        if tool_calls:
            first_tool_call = tool_calls[0] or {}
            function_data = first_tool_call.get("function") or {}
            if function_data.get("name") != "generate_diary":
                raise LLMError("Unsupported tool call")

            raw_arguments = function_data.get("arguments") or "{}"
            try:
                parsed_arguments = json.loads(raw_arguments)
            except Exception:
                parsed_arguments = {}

            user_request = parsed_arguments.get("user_request") or message
            target_date_str = parsed_arguments.get("target_date") or ""
            
            # LLM 应该已经转换了日期，尝试解析它
            target_day_value = _parse_target_date(target_date_str) or diary_today_shanghai()
            
            all_logs = get_chat_logs_by_user_and_date(db, current_user_id, target_day_value)
            diary_logs = build_diary_source_logs(all_logs, trigger_message=user_request)

            entry, updated, diary_text = generate_or_update_daily_diary(
                db,
                current_user_id,
                target_day_value,
                diary_logs,
                preserve_food_sections=False,
            )

            answer = f"{DIARY_RESPONSE_PREFIX}{'，并更新到同一篇里了' if updated else '了'}。\n\n{diary_text}"
            create_chat_log(
                db,
                ChatLog(user_id=current_user_id, role="assistant", content=answer),
            )
            return answer

        # Diary intent but model didn't issue a proper tool call.
        # Fallback to deterministic diary generation to avoid weird JSON/tool text.
        # 直接用当天，AI应该已经理解了日期意图
        target_day_value = diary_today_shanghai()
        all_logs = get_chat_logs_by_user_and_date(db, current_user_id, target_day_value)
        diary_logs = build_diary_source_logs(all_logs, trigger_message=message)
        entry, updated, diary_text = generate_or_update_daily_diary(
            db,
            current_user_id,
            target_day_value,
            diary_logs,
            preserve_food_sections=False,
        )
        answer = f"{DIARY_RESPONSE_PREFIX}{'，并更新到同一篇里了' if updated else '了'}。\n\n{diary_text}"
    except LLMError:
        raise
    except Exception as exc:
        raise LLMError(str(exc)) from exc

    if not answer:
        raise LLMError("Empty agent response")
    
    create_chat_log(
        db,
        ChatLog(user_id=current_user_id, role="assistant", content=answer),
    )
    return answer