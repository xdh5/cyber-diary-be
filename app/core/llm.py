import os
from typing import Any
from urllib.parse import urlparse

import requests


DEFAULT_MODEL = "doubao-1-5-thinking-vision-pro-250428"
CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = int(os.getenv("DOUBAO_READ_TIMEOUT_SECONDS", "45"))


class LLMError(RuntimeError):
    pass


def _extract_final_answer(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    for choice in choices:
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            answer = content.strip()
        elif isinstance(content, list):
            answer = "\n".join(
                (part.get("text") or "").strip()
                for part in content
                if isinstance(part, dict) and (part.get("text") or "").strip()
            ).strip()
        else:
            answer = ""

        if answer:
            return answer

    return ""


def _build_doubao_url(path: str) -> str:
    base_url = (os.getenv("DOUBAO_BASE_URL") or "https://ark.cn-beijing.volces.com/api/v3").strip()
    if not base_url:
        raise LLMError("DOUBAO_BASE_URL is empty")

    if "://" not in base_url:
        base_url = f"https://{base_url}"

    parsed = urlparse(base_url)
    if not parsed.netloc:
        raise LLMError("Invalid DOUBAO_BASE_URL")

    endpoint = f"{parsed.netloc}{parsed.path}".rstrip("/")
    if not endpoint:
        raise LLMError("Invalid DOUBAO_BASE_URL")

    return f"{parsed.scheme or 'https'}://{endpoint}/{path.lstrip('/')}"


def _doubao_api_key() -> str:
    api_key = (os.getenv("DOUBAO_API_KEY") or "").strip()
    if not api_key:
        raise LLMError("Missing DOUBAO_API_KEY")
    return api_key


def generate_text(
    user_prompt: str,
    system_prompt: str | None = None,
    temperature: float = 0.7,
) -> str:
    api_key = _doubao_api_key()

    model_list_env = os.getenv("DOUBAO_MODEL_LIST", "")
    model_list = [m.strip() for m in model_list_env.split(",") if m.strip()]
    if DEFAULT_MODEL not in model_list:
        model_list.insert(0, DEFAULT_MODEL)
    if not model_list:
        model_list = [DEFAULT_MODEL]

    payload: dict[str, Any] = {
        "messages": [],
        "temperature": temperature,
        "stream": False,
    }
    if system_prompt:
        payload["messages"].append({"role": "system", "content": system_prompt})
    payload["messages"].append({"role": "user", "content": user_prompt})

    errors: list[str] = []
    for model_name in model_list:
        try:
            response = requests.post(
                _build_doubao_url("chat/completions"),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={**payload, "model": model_name},
                timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
            )
        except requests.Timeout:
            errors.append(f"{model_name}: timeout")
            continue
        except requests.RequestException as exc:
            errors.append(f"{model_name}: {exc.__class__.__name__}")
            continue

        if response.status_code >= 400:
            try:
                error_data = response.json()
                error_message = error_data.get("error", {}).get("message") or response.text
            except Exception:
                error_message = response.text
            errors.append(f"{model_name}: {response.status_code} {error_message}")
            continue

        try:
            data = response.json()
        except Exception as exc:
            errors.append(f"{model_name}: invalid json {exc.__class__.__name__}")
            continue
        answer = _extract_final_answer(data)
        if answer:
            return answer

        errors.append(f"{model_name}: empty answer")

    raise LLMError("LLM all models failed: " + "; ".join(errors))
