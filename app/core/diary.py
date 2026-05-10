import re
from datetime import date, datetime

from sqlmodel import Session

from app.core.llm import generate_text
from app.core.timezone import ensure_shanghai_tz, now_shanghai
from app.crud.crud import create_entry, get_entry_by_user_date_and_mood, update_entry
from app.models.models import ChatLog, Entry, FoodPhoto


DIARY_RESPONSE_PREFIX = "我已经把今天的聊天整理成日记"
FOOD_SECTION_START = "<!-- FOOD_SECTION_START -->"
FOOD_SECTION_END = "<!-- FOOD_SECTION_END -->"
FOOD_INFO_SECTION_START = "<!-- FOOD_INFO_SECTION_START -->"
FOOD_INFO_SECTION_END = "<!-- FOOD_INFO_SECTION_END -->"


def build_conversation_text(logs: list[ChatLog], include_assistant: bool = True) -> str:
    lines: list[str] = []
    for log in logs:
        if log.role == "user":
            lines.append(f"我: {log.content}")
            continue

        if include_assistant and log.role == "assistant":
            lines.append(f"AI(次要参考): {log.content}")
    return "\n".join(lines)


def is_diary_skill_response(log: ChatLog) -> bool:
    return log.role == "assistant" and log.content.startswith(DIARY_RESPONSE_PREFIX)


def build_recent_context(logs: list[ChatLog], limit: int = 12) -> str:
    if not logs:
        return ""

    recent_logs = [log for log in logs if not is_diary_skill_response(log)][-limit:]
    return build_conversation_text(recent_logs, include_assistant=True)


def build_diary_source_logs(logs: list[ChatLog], trigger_message: str | None = None) -> list[ChatLog]:
    filtered_logs = [log for log in logs if not is_diary_skill_response(log)]
    if trigger_message is not None and filtered_logs:
        last_log = filtered_logs[-1]
        if last_log.role == "user" and last_log.content.strip() == trigger_message.strip():
            return filtered_logs[:-1]
    return filtered_logs


def build_diary_prompt(
    target_day: date,
    logs: list[ChatLog],
    existing_entry: Entry | None = None,
) -> str:
    chat_content = build_conversation_text(logs)
    if existing_entry is not None:
        return (
            "你将扮演我的数字分身。下面是一篇已经生成的日记。"
            "现有日记只作为草稿参考。请基于今天聊天记录对它进行修订。"
            "如果现有日记与今天聊天记录冲突，必须按今天聊天记录改正；"
            "无法从聊天记录确认的细节请删除，不要保留。"
            "你需要先判断哪些内容值得写：当天的关键事件、情绪变化、决定、行动和反思；"
            "寒暄、重复表达、无信息量废话请直接排除。"
            "以“我”的发言为主线，AI 回复只作为次要参考，除非它明显影响了我的决定或情绪。"
            "不要长篇扩写，不要虚构细节，不要解释，不要列提纲。"
            "输出 1-2 段中文，控制在 120-220 字。\n\n"
            f"日期: {target_day.isoformat()}\n\n"
            f"现有日记:\n{existing_entry.content}\n\n"
            f"今天聊天记录:\n{chat_content}"
        )

    return (
        "你将扮演我的数字分身。请根据下面的聊天记录，以第一人称‘我’写一篇精简中文日记。"
        "你需要先判断哪些内容值得写：当天的关键事件、情绪变化、决定、行动和反思；"
        "寒暄、重复表达、无信息量废话请直接排除。"
        "内容以“我”说的话为主，AI 回复是次要信息，只有在其明显影响我时才简要提及。"
        "只保留关键事实与感受，不要长篇扩写，不要虚构细节，不要列提纲，不要输出解释。"
        "输出 1-2 段，控制在 100-180 字。\n\n"
        f"日期: {target_day.isoformat()}\n\n"
        f"聊天记录:\n{chat_content}"
    )


def build_empty_diary_prompt(target_day: date) -> str:
    return (
        "你将扮演我的数字分身。今天暂时没有可用的聊天记录。"
        "请写一篇简短、真实、自然的中文日记，明确承认今天没有足够记录。"
        "不要编造具体事件，不要长篇扩写，不要列提纲，不要输出解释。"
        "输出 1 段，控制在 60-120 字。\n\n"
        f"日期: {target_day.isoformat()}"
    )


def build_diary_title_prompt(diary_content: str, target_day: date) -> str:
    """生成提取标题的提示"""
    return (
        "你是我的数字分身。我为你写了一篇日记。"
        "请阅读这篇日记，从中提取最核心的关键词或短语作为标题。"
        "标题应该简洁有力，通常是 2-6 个字，反映这一天的主题或情绪。"
        "例如：如果日记讲的是去咖啡厅放松，标题可能是'咖啡厅午后'或'放松的下午'。"
        "只输出标题本身，不要解释，不要前后加引号或任何符号。\n\n"
        f"日期: {target_day.isoformat()}\n\n"
        f"日记内容:\n{diary_content}"
    )


def generate_diary_title(diary_content: str, target_day: date) -> str:
    """根据日记内容用 AI 生成标题"""
    if not diary_content or not diary_content.strip():
        return f"{target_day.isoformat()}"
    
    try:
        title_prompt = build_diary_title_prompt(diary_content, target_day)
        title = generate_text(title_prompt).strip()
        
        # 清理 AI 可能返回的多余符号
        title = title.strip('"\'"""''')
        
        # 如果标题太长或仍然是日期格式，使用默认值
        if not title or len(title) > 20 or title == target_day.isoformat():
            return f"{target_day.isoformat()}"
        
        return title
    except Exception:
        # 如果生成失败，使用默认值
        return f"{target_day.isoformat()}"


def build_food_section(photos: list[FoodPhoto]) -> str:
    if not photos:
        return ""

    lines = [FOOD_SECTION_START, "## 美食"]
    for index, photo in enumerate(photos, start=1):
        label = photo.caption.strip() if photo.caption and photo.caption.strip() else f"美食照片 {index}"
        shot_time = getattr(photo, "shot_at", None)
        time_label = shot_time.strftime("%H:%M") if shot_time is not None else ""
        lines.append(f"![{label}]({photo.photo_url})")
        lines.append(f"- {time_label} {label}".strip())
    lines.append(FOOD_SECTION_END)
    return "\n".join(lines).strip()


def build_food_info_block(*, summary: str, user_text: str, timestamp: datetime) -> str:
    lines = [
        "- " + timestamp.strftime("%H:%M"),
    ]
    if summary.strip():
        lines.append(f"  - 摘要：{summary.strip()}")
    if user_text.strip():
        lines.append(f"  - 原文：{user_text.strip()}")
    return "\n".join(lines).strip()


def build_food_info_section(blocks: list[str]) -> str:
    if not blocks:
        return ""

    lines = [FOOD_INFO_SECTION_START, "## 美食日记"]
    lines.extend(blocks)
    lines.append(FOOD_INFO_SECTION_END)
    return "\n".join(lines).strip()


def extract_food_section(content: str | None) -> str:
    if not content:
        return ""

    pattern = re.compile(
        rf"\n?{re.escape(FOOD_SECTION_START)}.*?{re.escape(FOOD_SECTION_END)}\n?",
        re.S,
    )
    match = pattern.search(content)
    if not match:
        return ""

    return content[match.start():match.end()].strip()


def extract_food_info_section(content: str | None) -> str:
    if not content:
        return ""

    pattern = re.compile(
        rf"\n?{re.escape(FOOD_INFO_SECTION_START)}.*?{re.escape(FOOD_INFO_SECTION_END)}\n?",
        re.S,
    )
    match = pattern.search(content)
    if not match:
        return ""

    return content[match.start():match.end()].strip()


def merge_food_section(content: str, food_section: str) -> str:
    base = content.strip()
    if not food_section.strip():
        return base

    existing_section = extract_food_section(base)
    if existing_section:
        base = base.replace(existing_section, "").strip()

    if base:
        return f"{base}\n\n{food_section.strip()}".strip()
    return food_section.strip()


def merge_food_info_section(content: str, food_info_block: str) -> str:
    base = content.strip()
    if not food_info_block.strip():
        return base

    existing_section = extract_food_info_section(base)
    if existing_section:
        updated_section = existing_section.replace(
            FOOD_INFO_SECTION_END,
            f"{food_info_block.strip()}\n{FOOD_INFO_SECTION_END}",
        )
        return base.replace(existing_section, updated_section).strip()

    if base:
        return f"{base}\n\n{FOOD_INFO_SECTION_START}\n## 美食日记\n{food_info_block.strip()}\n{FOOD_INFO_SECTION_END}".strip()

    return f"{FOOD_INFO_SECTION_START}\n## 美食日记\n{food_info_block.strip()}\n{FOOD_INFO_SECTION_END}".strip()


def get_new_logs_since(existing_entry: Entry | None, logs: list[ChatLog]) -> list[ChatLog]:
    if existing_entry is None:
        return logs

    entry_updated_at = ensure_shanghai_tz(existing_entry.updated_at)
    return [
        log for log in logs
        if ensure_shanghai_tz(log.created_at) > entry_updated_at
    ]


def generate_or_update_daily_diary(
    db: Session,
    user_id: int,
    target_day: date,
    logs: list[ChatLog],
    *,
    preserve_food_sections: bool = True,
) -> tuple[Entry, bool, str]:
    existing = get_entry_by_user_date_and_mood(db, user_id, target_day, "AI汇总")
    prompt_logs = logs
    prompt_logs = build_diary_source_logs(prompt_logs)
    if not prompt_logs:
        if existing is not None:
            return existing, False, existing.content
        diary_prompt = build_empty_diary_prompt(target_day)
    else:
        diary_prompt = build_diary_prompt(target_day, prompt_logs, existing)
    diary_text = generate_text(diary_prompt)
    
    # 生成标题
    generated_title = generate_diary_title(diary_text, target_day)

    now = now_shanghai()
    if existing is not None:
        existing.title = generated_title
        existing.content = diary_text
        if preserve_food_sections:
            preserved_food_section = extract_food_section(existing.content)
            preserved_food_info_section = extract_food_info_section(existing.content)
            if preserved_food_section:
                existing.content = merge_food_section(existing.content, preserved_food_section)
            if preserved_food_info_section:
                existing.content = merge_food_info_section(existing.content, preserved_food_info_section)
        existing.date = target_day
        existing.district = "数字分身"
        existing.mood = "AI汇总"
        existing.updated_at = now
        entry = update_entry(db, existing)
        return entry, True, diary_text

    entry = Entry(
        title=generated_title,
        content=diary_text,
        date=target_day,
        district="数字分身",
        mood="AI汇总",
        user_id=user_id,
        created_at=now,
        updated_at=now,
    )
    entry = create_entry(db, entry)
    return entry, False, diary_text