from pathlib import Path

from sqlmodel import create_engine, Session

from app.core.config import settings

# If no DATABASE_URL, use SQLite
if not settings.DATABASE_URL:
    database_dir = Path(__file__).resolve().parent.parent.parent / "data"
    database_dir.mkdir(parents=True, exist_ok=True)
    database_path = database_dir / "diary.db"
    settings.DATABASE_URL = f"sqlite:///{database_path.as_posix()}"

connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(settings.DATABASE_URL, connect_args=connect_args, future=True)


def get_db():
    with Session(engine) as session:
        yield session