from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlmodel import Session
import json

from app.core.auth import get_current_user
from app.core.diary import generate_diary_title
from app.core.llm import LLMError, generate_text
from app.db.session import get_db
from app.schemas.schemas import DiaryGenerateRequest, DiaryGenerateResponse

router = APIRouter()


DIARY_GENERATION_PROMPT_WITH_IMAGES = """你将扮演我的数字分身。用户提供了图片和一些文字描述。

请根据以下内容，写一篇精简的中文日记。要求：
1. 以第一人称"我"来写
2. 包含用户描述的关键事件、感受
3. 将图片自然地融入日记中合适的位置，使用 Markdown 图片格式：![描述](图片URL)
4. 不要虚构细节，不要长篇扩写
5. 改正错别字和病句，但不要改动用户的表达习惯
6. 不要输出解释，只输出日记正文

{user_content}

日期：{target_date}
"""

DIARY_GENERATION_PROMPT_WITHOUT_IMAGES = """你将扮演我的数字分身。用户提供了文字描述。

请根据以下内容，写一篇精简的中文日记。要求：
1. 以第一人称"我"来写
2. 包含用户描述的关键事件、感受
3. 不要虚构细节，不要长篇扩写
4. 篇幅适中，一般 150-300 字
5. 不要输出解释，只输出日记正文

{user_content}

日期：{target_date}
"""


def _normalize_image_url(raw_url: str, request: Request) -> str:
    url = (raw_url or "").strip()
    if not url:
        return ""

    if url.startswith("http://") or url.startswith("https://"):
        return url

    if url.startswith("/"):
        return str(request.base_url).rstrip("/") + url

    return url


@router.post("/diary/generate", response_model=DiaryGenerateResponse)
async def generate_diary_from_content(
    request: Request,
    payload: DiaryGenerateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    根据用户提供的文字和图片生成日记内容和标题。

    - **text**: 用户提供的文字描述（可选）
    - **image_urls**: 图片URL列表（可选）
    - **date**: 日记日期，格式 YYYY-MM-DD
    """
    user_content_parts = []

    if payload.text and payload.text.strip():
        user_content_parts.append(f"用户描述：\n{payload.text.strip()}")

    normalized_image_urls = [
        url
        for url in (_normalize_image_url(url, request) for url in (payload.image_urls or []))
        if url
    ]

    if normalized_image_urls:
        image_count = len(normalized_image_urls)
        user_content_parts.append(f"用户上传了 {image_count} 张图片，请根据图片内容补充日记。")

    if not user_content_parts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请提供文字描述或图片",
        )

    user_content = "\n\n".join(user_content_parts)

    try:
        prompt_template = DIARY_GENERATION_PROMPT_WITH_IMAGES if normalized_image_urls else DIARY_GENERATION_PROMPT_WITHOUT_IMAGES
        prompt = prompt_template.format(
            user_content=user_content,
            target_date=payload.date.isoformat() if payload.date else "今天",
        )

        messages = [
            {"role": "user", "content": prompt},
        ]

        if normalized_image_urls:
            image_content = [
                {"type": "image_url", "image_url": {"url": url}}
                for url in normalized_image_urls
            ]
            text_content = {"type": "text", "text": prompt}
            messages = [
                {
                    "role": "user",
                    "content": [
                        *image_content,
                        text_content,
                    ],
                }
            ]

        from app.core.llm import _build_doubao_url, _doubao_api_key
        from app.core.agent import _doubao_model
        import requests

        api_key = _doubao_api_key()
        model = _doubao_model()

        response = requests.post(
            _build_doubao_url("chat/completions"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": messages, "temperature": 0.3},
            timeout=(5, 120),  # 连接超时5秒，读超时120秒
        )

        if response.status_code >= 400:
            raise LLMError(f"Doubao API error: {response.status_code}")

        result = response.json()
        choices = result.get("choices") or []
        if not choices:
            raise LLMError("Empty response from AI")

        message_data = choices[0].get("message") or {}
        content = message_data.get("content", "")

        if isinstance(content, list):
            text_parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
            content = "\n".join(text_parts).strip()
        elif not isinstance(content, str):
            content = ""

        content = content.strip()

        if not content:
            raise LLMError("AI 返回内容为空")

        target_date = payload.date
        title = generate_diary_title(content, target_date)

        return DiaryGenerateResponse(
            content=content,
            title=title,
            date=target_date,
        )

    except LLMError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI 服务暂时不可用，请稍后重试",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"生成日记失败: {str(exc)}",
        )


@router.post("/diary/generate-stream")
async def generate_diary_stream(
    request: Request,
    payload: DiaryGenerateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    流式生成日记内容。使用Server-Sent Events返回实时生成进度。
    """
    import requests
    from app.core.llm import _build_doubao_url, _doubao_api_key
    from app.core.agent import _doubao_model

    user_content_parts = []
    if payload.text and payload.text.strip():
        user_content_parts.append(f"用户描述：\n{payload.text.strip()}")

    normalized_image_urls = [
        url
        for url in (_normalize_image_url(url, request) for url in (payload.image_urls or []))
        if url
    ]

    if normalized_image_urls:
        image_count = len(normalized_image_urls)
        user_content_parts.append(f"用户上传了 {image_count} 张图片，请根据图片内容补充日记。")

    if not user_content_parts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请提供文字描述或图片",
        )

    user_content = "\n\n".join(user_content_parts)
    prompt_template = DIARY_GENERATION_PROMPT_WITH_IMAGES if normalized_image_urls else DIARY_GENERATION_PROMPT_WITHOUT_IMAGES
    prompt = prompt_template.format(
        user_content=user_content,
        target_date=payload.date.isoformat() if payload.date else "今天",
    )

    messages = [{"role": "user", "content": prompt}]
    if normalized_image_urls:
        image_content = [
            {"type": "image_url", "image_url": {"url": url}}
            for url in normalized_image_urls
        ]
        text_content = {"type": "text", "text": prompt}
        messages = [
            {
                "role": "user",
                "content": [*image_content, text_content],
            }
        ]

    async def event_generator():
        try:
            api_key = _doubao_api_key()
            model = _doubao_model()

            # 发送初始事件
            yield f"data: {json.dumps({'status': 'generating', 'message': '开始生成日记...', 'content': ''})}\n\n"

            # 调用API流式请求
            response = requests.post(
                _build_doubao_url("chat/completions"),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": 0.3,
                    "stream": True,
                },
                timeout=(5, 120),
                stream=True,
            )

            if response.status_code >= 400:
                yield f"data: {json.dumps({'status': 'error', 'message': f'API错误: {response.status_code}'})}\n\n"
                return

            full_content = ""
            for line in response.iter_lines():
                if not line:
                    continue

                line_str = line.decode("utf-8") if isinstance(line, bytes) else line
                if line_str.startswith("data: "):
                    try:
                        chunk_data = json.loads(line_str[6:])
                        choices = chunk_data.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                full_content += content
                                # 实时推送生成的内容
                                yield f"data: {json.dumps({'status': 'streaming', 'content': full_content, 'delta': content})}\n\n"
                    except (json.JSONDecodeError, KeyError):
                        continue

            if full_content.strip():
                # 生成标题
                title = generate_diary_title(full_content.strip(), payload.date)
                yield f"data: {json.dumps({'status': 'complete', 'content': full_content.strip(), 'title': title, 'date': payload.date.isoformat() if payload.date else None})}\n\n"
            else:
                yield f"data: {json.dumps({'status': 'error', 'message': 'AI返回内容为空'})}\n\n"

        except LLMError as e:
            yield f"data: {json.dumps({'status': 'error', 'message': str(e)})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'status': 'error', 'message': f'生成失败: {str(e)}'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
