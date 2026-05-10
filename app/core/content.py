import html
import re
from typing import Optional

import bleach
from bs4 import BeautifulSoup
from markdown_it import MarkdownIt


_MARKDOWN = MarkdownIt("commonmark", {"html": True, "linkify": True, "breaks": True})

ALLOWED_TAGS = list(bleach.sanitizer.ALLOWED_TAGS) + [
    "p",
    "br",
    "strong",
    "em",
    "u",
    "s",
    "blockquote",
    "ul",
    "ol",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "img",
    "span",
    "div",
]

ALLOWED_ATTRIBUTES = {
    "*": ["class"],
    "a": ["href", "title", "target", "rel"],
    "img": ["src", "alt", "title"],
}


def is_probably_html(content: Optional[str]) -> bool:
    if not content:
        return False
    return bool(re.search(r"<\s*/?\s*[a-zA-Z][^>]*>", content))


def looks_like_markdown(content: str) -> bool:
    text = content.strip()
    if not text:
        return False
    patterns = [
        r"^#{1,6}\s+",
        r"^\s*[-*+]\s+",
        r"^\s*\d+\.\s+",
        r"^>\s+",
        r"!\[[^\]]*\]\([^\)]+\)",
        r"\[[^\]]+\]\([^\)]+\)",
        r"`{1,3}[^`]+`{1,3}",
        r"(\*\*|__)[^\n]+(\*\*|__)",
    ]
    return any(re.search(pattern, text, re.M) for pattern in patterns)


def markdown_to_html(content: str) -> str:
    return _MARKDOWN.render(content)


def plain_text_to_html(content: str) -> str:
    stripped = content.strip()
    if not stripped:
        return "<p></p>"

    paragraphs = [item.strip() for item in re.split(r"\n{2,}", stripped) if item.strip()]
    if not paragraphs:
        return "<p></p>"

    rendered: list[str] = []
    for paragraph in paragraphs:
        escaped = html.escape(paragraph).replace("\n", "<br />")
        rendered.append(f"<p>{escaped}</p>")
    return "".join(rendered)


def sanitize_html(content: str) -> str:
    cleaned = bleach.clean(
        content,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=["http", "https", "data"],
        strip=True,
    )
    return cleaned.strip()


def normalize_entry_content(content: str) -> str:
    text = (content or "").strip()
    if not text:
        return "<p></p>"

    if is_probably_html(text):
        rendered = text
    elif looks_like_markdown(text):
        rendered = markdown_to_html(text)
    else:
        rendered = plain_text_to_html(text)

    cleaned = sanitize_html(rendered)
    return cleaned or "<p></p>"


def extract_plain_text(content: str, *, limit: int = 0) -> str:
    text = (content or "").strip()
    if not text:
        return ""

    if is_probably_html(text):
        rendered = text
    elif looks_like_markdown(text):
        rendered = markdown_to_html(text)
    else:
        rendered = plain_text_to_html(text)

    soup = BeautifulSoup(rendered, "html.parser")
    plain = " ".join(soup.stripped_strings)
    plain = re.sub(r"\s+", " ", plain).strip()
    if limit > 0:
        return plain[:limit]
    return plain


def extract_preview_text(content: str, *, limit: int = 255) -> str:
    return extract_plain_text(content, limit=limit)


def extract_first_image_url(content: str) -> Optional[str]:
    text = (content or "").strip()
    if not text:
        return None

    if is_probably_html(text):
        soup = BeautifulSoup(text, "html.parser")
        image = soup.find("img")
        if image:
            src = str(image.get("src") or "").strip()
            if src:
                return src

    markdown_match = re.search(r"!\[[^\]]*\]\(([^\s)]+(?:\?[^)\s]*)?)\)", text)
    if markdown_match:
        return markdown_match.group(1)

    return None