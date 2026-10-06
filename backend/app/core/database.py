"""
Async SQLAlchemy engine and session factory.
All DB access goes through `get_db()` dependency.
"""
from collections.abc import AsyncGenerator

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    try:
        session = AsyncSessionLocal()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable. Check DATABASE_URL and that migrations ran.",
        ) from exc
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    else:
        try:
            await session.commit()
        except Exception:
            await session.rollback()
    finally:
        await session.close()
