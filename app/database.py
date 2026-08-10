from __future__ import annotations

from contextlib import asynccontextmanager

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import settings
from .crypto import account_cipher_context, vault
from .models import Account, Base

engine_options: dict = {"pool_pre_ping": True}
if settings.database_url.startswith("postgresql"):
    engine_options.update(pool_size=10, max_overflow=10, pool_recycle=1800)

engine = create_async_engine(settings.database_url, **engine_options)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await migrate_account_ciphertexts()


async def migrate_account_ciphertexts(batch_size: int = 500) -> int:
    migrated = 0
    cursor = 0
    while True:
        async with SessionLocal() as session:
            rows = list(
                (
                    await session.scalars(
                        select(Account)
                        .where(Account.id > cursor)
                        .order_by(Account.id.asc())
                        .limit(batch_size)
                    )
                ).all()
            )
            if not rows:
                return migrated
            for account in rows:
                cursor = account.id
                for attribute, field in (
                    ("email_encrypted", "email"),
                    ("client_id_encrypted", "client_id"),
                    ("refresh_token_encrypted", "refresh_token"),
                ):
                    value = getattr(account, attribute)
                    if vault.needs_upgrade(value):
                        setattr(
                            account,
                            attribute,
                            vault.upgrade(
                                value,
                                account_cipher_context(account.email_hash, field),
                            ),
                        )
                        migrated += 1
            await session.commit()


@asynccontextmanager
async def session_scope():
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
