import asyncio
from logging.config import fileConfig

import alembic.context as context
from sqlalchemy.ext.asyncio import create_async_engine

# Import all models so Alembic can detect them
from app.core.database import Base
import app.models  # noqa: F401 — registers all ORM models

config = context.config
# disable_existing_loggers must stay False: _stamp_alembic_head() runs inside the
# uvicorn process during startup, and the default would silence uvicorn.error /
# uvicorn.access for the rest of the process — including crash tracebacks.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def include_object(object_, name, type_, reflected, compare_to):
    """Only ever manage tables that this application defines.

    The postgis/postgis image ships Tiger/TopoDB topology tables
    (`spatial_ref_sys`, `tiger_faces`, `place_lookup`, ...) that are part of
    the PostGIS extension itself. Without this filter autogenerate sees them as
    "removed tables" and writes DROP statements that would break the database.
    Anything not in our metadata is left strictly alone.
    """
    if type_ == "table":
        return name in target_metadata.tables
    if type_ == "index" and reflected:
        # Indexes we did not create (e.g. GiST indexes PostGIS ships).
        return False
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    from app.core.config import get_settings

    # Resolved through the app's Settings rather than `os.getenv` on purpose.
    # Settings searches CWD .env -> repo root .env -> backend/.env, so a
    # developer running `alembic` from backend/ gets the same database the app
    # is configured to serve. A bare `os.getenv` skipped all of that and fell
    # through to alembic.ini's Docker-Compose-only `db:5432`, which fails DNS
    # off-Docker -- and, worse, would silently migrate a different database than
    # the one serving traffic if `db` happened to resolve.
    #
    # `database_dsn` already carries the asyncpg scheme and the ssl decision
    # (Settings._force_asyncpg / Settings.database_requires_ssl), so this no
    # longer needs to rewrite the URL itself — the two paths cannot drift.
    db_url = get_settings().database_dsn
    connectable = create_async_engine(db_url)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
