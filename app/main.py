import logging
import time
import uuid
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, SQLModel, select

from app.api.chat import router as chat_router
from app.api.diary import router as diary_router
from app.api.v1.api import api_router
from app.core.auth import get_password_hash, pick_default_avatar
from app.core.config import settings
from app.db.session import engine, get_db
from app.models.models import Entry, User
from app.crud.crud import create_user

LOG_LEVEL = settings.LOG_LEVEL
track_id_ctx_var: ContextVar[str] = ContextVar("track_id", default="-")


class TrackIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "track_id"):
            record.track_id = track_id_ctx_var.get("-")
        return True


logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s [track=%(track_id)s] %(name)s - %(message)s",
)
for _handler in logging.getLogger().handlers:
    _handler.addFilter(TrackIdFilter())

logger = logging.getLogger("cyber_diary")

app = FastAPI(title="Cyber Diary API", version="1.0.0")


def _pick_track_id(header_track_id: str | None) -> str:
    candidate = (header_track_id or "").strip()
    if candidate:
        return candidate[:128]
    return uuid.uuid4().hex

data_root = Path(__file__).resolve().parent.parent / "data"
uploads_root = data_root / "uploads"
uploads_root.mkdir(parents=True, exist_ok=True)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_track_id_and_request_logging(request, call_next):
    incoming_track_id = request.headers.get("x-track-id") or request.headers.get("x-request-id")
    track_id = _pick_track_id(incoming_track_id)
    token = track_id_ctx_var.set(track_id)
    request.state.track_id = track_id
    start_time = time.perf_counter()

    client_ip = request.client.host if request.client else "-"
    logger.info("request.start method=%s path=%s client=%s", request.method, request.url.path, client_ip)

    try:
        response = await call_next(request)
        response.headers["X-Track-Id"] = track_id
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        logger.info(
            "request.end method=%s path=%s status=%s duration_ms=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response
    except Exception:
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        logger.exception("request.error method=%s path=%s duration_ms=%s", request.method, request.url.path, duration_ms)
        raise
    finally:
        track_id_ctx_var.reset(token)

# Include API router
app.include_router(api_router, prefix="/api/v1")
app.include_router(chat_router, prefix="/api", tags=["chat"])
app.include_router(diary_router, prefix="/api", tags=["diary"])
app.mount("/uploads", StaticFiles(directory=uploads_root), name="uploads")


def ensure_database_schema(engine):
    SQLModel.metadata.create_all(engine)


def seed_data(session: Session):
    admin = session.exec(select(User).where(User.email == "1@qq.com")).first()
    if admin is None:
        admin = User(
            email="1@qq.com",
            nickname="admin",
            hashed_password=get_password_hash("123456"),
            is_superuser=True,
            avatar_url=pick_default_avatar(),
        )
        create_user(session, admin)
        session.refresh(admin)

    existing_entry = session.exec(select(Entry)).first()
    if existing_entry:
        for entry in session.exec(select(Entry)).all():
            if entry.user_id is None:
                entry.user_id = admin.id
                session.add(entry)
        session.commit()
        return

    entries = [
        Entry(
            title='上海冬季漫游',
            content="""# 上海冬季漫游

> 📍 地点：上海市浦东新区
> 🎭 心情：愉快

早上在外滩散步，欣赏了浦江的晨光。午餐去了一家老字号小馆，尝到了地道的本帮菜风味。

下午在浦东滨江拍了很多照片，记录了城市的美景和河畔的安宁。

晚上品尝了更多经典本帮菜，整天都沉浸在上海独特的魅力中。""",
            district='上海市浦东新区',
            photo_url='https://images.unsplash.com/photo-1512436991641-6745cdb1723f?auto=format&fit=crop&w=240&q=80',
            mood='愉快',
            date=datetime.fromisoformat('2025-12-28T00:00:00').date(),
            user_id=admin.id,
            created_at=datetime.fromisoformat('2025-12-28T00:00:00'),
            updated_at=datetime.fromisoformat('2025-12-28T00:00:00'),
        ),
        Entry(
            title='秋天的北京胡同',
            content="""# 秋天的北京胡同

> 📍 地点：北京市东城区
> 🎭 心情：宁静

午后在胡同里漫游，感受古巷的宁静和历史的厚重。

石板路上留下了秋日的脚印，老墙上爬满了岁月的痕迹。

晚餐在小店吃了炸酱面，天气微凉却很舒服，仿佛回到了老北京的时光。""",
            district='北京市东城区',
            photo_url='https://images.unsplash.com/photo-1500530855697-b586d89ba3ee?auto=format&fit=crop&w=240&q=80',
            mood='宁静',
            date=datetime.fromisoformat('2025-11-10T00:00:00').date(),
            user_id=admin.id,
            created_at=datetime.fromisoformat('2025-11-10T00:00:00'),
            updated_at=datetime.fromisoformat('2025-11-10T00:00:00'),
        ),
        Entry(
            title='杭州冬日茶馆',
            content="""# 杭州冬日茶馆

> 📍 地点：浙江省杭州市
> 🎭 心情：惬意

在西湖边的茶馆里读书，喝了一壶龙井茶。茶香缭绕，思绪万千。

下午游览了灵隐寺，感受了古刹的宁谧和城市的对话。

杭州的冬日显得格外温柔，仿佛一切都在诉说着古老的故事。""",
            district='浙江省杭州市',
            photo_url='',
            mood='惬意',
            date=datetime.fromisoformat('2025-11-03T00:00:00').date(),
            user_id=admin.id,
            created_at=datetime.fromisoformat('2025-11-03T00:00:00'),
            updated_at=datetime.fromisoformat('2025-11-03T00:00:00'),
        ),
        Entry(
            title='苏州园林一日',
            content="""# 苏州园林一日

> 📍 地点：江苏省苏州市
> 🎭 心情：放松

## 上午游园

苏州园林里的石桥和假山很有意思，构思精妙，处处有景。

桥畔的秋色让人放松，黄叶飘落在水面上，画面如诗。

## 下午感悟

晚上回忆起古典庭院的布局，每一个角落都蕴含着智慧和美学。

苏州的园林文化真是无穷的财富。""",
            district='江苏省苏州市',
            photo_url='https://images.unsplash.com/photo-1494526585095-c41746248156?auto=format&fit=crop&w=240&q=80',
            mood='放松',
            date=datetime.fromisoformat('2025-10-22T00:00:00').date(),
            user_id=admin.id,
            created_at=datetime.fromisoformat('2025-10-22T00:00:00'),
            updated_at=datetime.fromisoformat('2025-10-22T00:00:00'),
        ),
    ]

    for entry in entries:
        session.add(entry)
    session.commit()


@app.get("/", response_model=dict)
def read_root():
    return {"message": "Welcome to Cyber Diary API"}


@app.get("/health", response_model=dict)
def health_check():
    return {"status": "ok"}


# Initialize database
ensure_database_schema(engine)

with Session(engine) as session:
    seed_data(session)