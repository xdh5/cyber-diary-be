from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass
from typing import Any, Literal

import requests

from app.core.llm import LLMError, _build_doubao_url, _doubao_api_key, _extract_final_answer


FOOD_CLASSIFIER_MODEL = (os.getenv("DOUBAO_FOOD_MODEL", "doubao-seed-2.0-pro").strip() or "doubao-seed-2.0-pro")
FOOD_COMMENT_MODEL = (os.getenv("DOUBAO_FOOD_COMMENT_MODEL", FOOD_CLASSIFIER_MODEL).strip() or FOOD_CLASSIFIER_MODEL)

FOOD_CLASSIFIER_SYSTEM_PROMPT = """你是一个美食日志助手。请分析用户上传的内容：

判定优先级：图片内容 > 用户文字。

如果图片里出现真实的食物、餐厅出品、外卖餐盒、甜点、饮料、摆盘、烧烤、火锅、面食等，哪怕用户文字是吐槽、评价、简短描述，也一律返回 [TYPE: FOOD_PHOTO]。

如果上传内容主要是截图、订单页、聊天记录、菜单文字、收据、非食物场景照片，返回 [TYPE: INFO] 并附带一段 15 字以内的生动美食摘要。

如果一批图片里只要有一张是真实食物照片，整批都判为 [TYPE: FOOD_PHOTO]。

提取照片中的食物名称。

不要因为“好吃”“难吃”“想吃”“今天吃了”之类的文字就把真实食物照片误判成 INFO。

只输出以下标签，不要解释，不要输出多余内容：
[TYPE: FOOD_PHOTO]
[FOOD: 食物名称]

或者：
[TYPE: INFO]
[SUMMARY: 15字以内摘要]
"""

FOOD_COMMENT_SYSTEM_PROMPT = """你是一个美食评论整理助手。请严格执行：
1) 仅修正明显错别字、标点和语序小问题，不要改变原意和情绪。
2) 若提供了非食物图片信息摘要，只补充必要信息，不要凭空扩写。
3) 输出一条完整、自然、通顺的人话中文评论。

硬性限制：
- 只输出一条最终评论，不要解释。
- 不要重复菜名，不要出现“标题·内容”或“A · A”结构。
- 不要输出列表、标签、引号、括号说明。
"""


@dataclass(slots=True)
class FoodClassification:
    type: Literal["FOOD_PHOTO", "INFO"]
    summary: str = ""
    food_name: str = ""
    raw_text: str = ""


def _build_user_prompt(caption: str | None, file_name: str | None) -> str:
    text = (caption or "").strip()
    file_label = (file_name or "").strip()
    return (
        "请判断这次上传属于哪一类，并按系统要求输出标签。\n"
        f"用户文字描述：{text or '无'}\n"
        f"文件名：{file_label or '无'}\n"
        "注意：美团/饿了么订单截图、聊天截图、非食物照片都应判为 INFO。"
    )


def _build_batch_user_prompt(caption: str | None, file_names: list[str]) -> str:
    text = (caption or "").strip()
    labels = [name.strip() for name in file_names if name and name.strip()]
    file_section = "、".join(labels[:12]) if labels else "无"
    return (
        "这是一次批量上传，请综合所有图片和文字，只给一个最终判断。\n"
        f"图片数量：{len(file_names)}\n"
        f"文件名：{file_section}\n"
        f"用户文字描述：{text or '无'}\n"
        "规则：只要任意一张图片是真实食物/餐厅照片，就返回 [TYPE: FOOD_PHOTO]；"
        "只有在整批都明确是截图/订单/菜单/收据/非食物场景时，才返回 [TYPE: INFO] 并给 [SUMMARY]。"
    )


def _build_image_payload(image_bytes: bytes, content_type: str | None) -> str:
    mime_type = (content_type or "image/jpeg").strip() or "image/jpeg"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _extract_multimodal_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return "\n".join(parts).strip()

    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()

    return ""


def _parse_classifier_text(text: str) -> FoodClassification:
    normalized = text.strip()
    type_match = re.search(r"\[TYPE:\s*(FOOD_PHOTO|INFO)\s*\]", normalized, re.I)
    food_match = re.search(r"\[FOOD:\s*(.+?)\s*\]", normalized, re.I)
    summary_match = re.search(r"\[SUMMARY:\s*(.+?)\s*\]", normalized, re.I)

    classification_type = (type_match.group(1).upper() if type_match else "INFO")
    food_name = food_match.group(1).strip() if food_match else ""
    summary = summary_match.group(1).strip() if summary_match else ""

    if classification_type == "FOOD_PHOTO" and not food_name:
        food_name = summary or "食物"

    if classification_type == "INFO" and not summary:
        summary = normalized.replace("\n", " ").strip()[:15]

    return FoodClassification(
        type=classification_type,  # type: ignore[arg-type]
        summary=summary[:15],
        food_name=food_name[:40],
        raw_text=normalized,
    )


def _sanitize_comment_text(text: str) -> str:
    normalized = (text or "").strip()
    if not normalized:
        return ""

    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"\s*([,，.。!！?？;；:：])\s*", r"\1", normalized)
    normalized = re.sub(r"([。！？；，,.!?;])\1+", r"\1", normalized)
    normalized = re.sub(r"([\u4e00-\u9fffA-Za-z0-9]{2,})\s*[·•|｜]\s*\1", r"\1", normalized)
    normalized = normalized.strip(" \t\r\n·•|｜")
    normalized = re.sub(r"^[,，.。;；:：]+", "", normalized)

    if normalized and normalized[-1] not in "。！？!?":
        normalized = f"{normalized}。"
    return normalized


def _fallback_compose_comment(raw_comment: str | None, info_summaries: list[str]) -> str:
    base = (raw_comment or "").strip()
    infos = [item.strip() for item in info_summaries if item and item.strip()]

    if not base and not infos:
        return ""

    if base and infos:
        info_part = "、".join(infos[:2])
        return _sanitize_comment_text(f"{base}，另外补充了{info_part}。")

    if base:
        return _sanitize_comment_text(base)

    return _sanitize_comment_text("、".join(infos[:2]))


def compose_food_comment(
    *,
    raw_comment: str | None,
    info_summaries: list[str],
    food_name: str | None = None,
) -> str:
    base_comment = (raw_comment or "").strip()
    clean_info = [item.strip() for item in info_summaries if item and item.strip()]
    dish = (food_name or "").strip()

    if not base_comment and not clean_info:
        return ""

    api_key = _doubao_api_key()
    user_prompt = (
        "请输出一条最终评论。\n"
        f"原始评论：{base_comment or '无'}\n"
        f"识别到的食物名称：{dish or '无'}\n"
        f"非食物图片补充信息：{'；'.join(clean_info) if clean_info else '无'}\n"
        "要求：尽量保留原句语气，仅修错别字和标点；若有补充信息则自然并入一句话。"
    )

    payload: dict[str, Any] = {
        "model": FOOD_COMMENT_MODEL,
        "messages": [
            {"role": "system", "content": FOOD_COMMENT_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "stream": False,
    }

    try:
        response = requests.post(
            _build_doubao_url("chat/completions"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=(5, int(os.getenv("DOUBAO_READ_TIMEOUT_SECONDS", "45"))),
        )
        if response.status_code >= 400:
            return _fallback_compose_comment(base_comment, clean_info)

        data = response.json()
        answer = _extract_final_answer(data)
        if not answer:
            return _fallback_compose_comment(base_comment, clean_info)

        return _sanitize_comment_text(answer)
    except Exception:
        return _fallback_compose_comment(base_comment, clean_info)


def classify_food_upload(*, image_bytes: bytes | None, content_type: str | None, caption: str | None, file_name: str | None) -> FoodClassification:
    api_key = _doubao_api_key()
    payload: dict[str, Any] = {
        "model": FOOD_CLASSIFIER_MODEL,
        "messages": [
            {"role": "system", "content": FOOD_CLASSIFIER_SYSTEM_PROMPT},
        ],
        "temperature": 0.0,
        "stream": False,
    }

    user_prompt = _build_user_prompt(caption, file_name)
    if image_bytes:
        payload["messages"].append(
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": _build_image_payload(image_bytes, content_type)}},
                    {"type": "text", "text": user_prompt},
                ],
            }
        )
    else:
        payload["messages"].append({"role": "user", "content": user_prompt})

    try:
        response = requests.post(
            _build_doubao_url("chat/completions"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=(5, int(os.getenv("DOUBAO_READ_TIMEOUT_SECONDS", "45"))),
        )
    except requests.RequestException as exc:
        raise LLMError(f"Food classifier request failed: {exc.__class__.__name__}") from exc

    if response.status_code >= 400:
        try:
            error_data = response.json()
            error_message = error_data.get("error", {}).get("message") or response.text
        except Exception:
            error_message = response.text
        raise LLMError(f"Food classifier {response.status_code}: {error_message}")

    try:
        data = response.json()
    except Exception as exc:
        raise LLMError(f"Food classifier invalid JSON: {exc.__class__.__name__}") from exc

    text = _extract_final_answer(data)
    if not text:
        raise LLMError("Food classifier returned empty answer")

    return _parse_classifier_text(text)


def classify_food_upload_batch(
    *,
    images: list[tuple[bytes, str | None, str | None]],
    caption: str | None,
) -> FoodClassification:
    api_key = _doubao_api_key()
    payload: dict[str, Any] = {
        "model": FOOD_CLASSIFIER_MODEL,
        "messages": [
            {"role": "system", "content": FOOD_CLASSIFIER_SYSTEM_PROMPT},
        ],
        "temperature": 0.0,
        "stream": False,
    }

    if images:
        user_content: list[dict[str, Any]] = []
        file_names: list[str] = []
        for image_bytes, content_type, file_name in images:
            file_names.append(file_name or "")
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _build_image_payload(image_bytes, content_type)},
                }
            )
        user_content.append(
            {
                "type": "text",
                "text": _build_batch_user_prompt(caption, file_names),
            }
        )
        payload["messages"].append({"role": "user", "content": user_content})
    else:
        payload["messages"].append({"role": "user", "content": _build_batch_user_prompt(caption, [])})

    try:
        response = requests.post(
            _build_doubao_url("chat/completions"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=(5, int(os.getenv("DOUBAO_READ_TIMEOUT_SECONDS", "45"))),
        )
    except requests.RequestException as exc:
        raise LLMError(f"Food classifier request failed: {exc.__class__.__name__}") from exc

    if response.status_code >= 400:
        try:
            error_data = response.json()
            error_message = error_data.get("error", {}).get("message") or response.text
        except Exception:
            error_message = response.text
        raise LLMError(f"Food classifier {response.status_code}: {error_message}")

    try:
        data = response.json()
    except Exception as exc:
        raise LLMError(f"Food classifier invalid JSON: {exc.__class__.__name__}") from exc

    text = _extract_final_answer(data)
    if not text:
        raise LLMError("Food classifier returned empty answer")

    return _parse_classifier_text(text)