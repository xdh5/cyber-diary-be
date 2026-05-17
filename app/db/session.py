from sqlmodel import create_engine, Session

from app.core.config import settings

connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(settings.DATABASE_URL, connect_args=connect_args, future=True)


def get_db():
    with Session(engine) as session:
        yield session
