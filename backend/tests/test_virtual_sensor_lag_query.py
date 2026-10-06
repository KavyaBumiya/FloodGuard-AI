"""Regression tests for the virtual-sensor upstream-lag lookup.

The bug: the "reading closest to <lag target>" ordering used
``func.abs(TelemetryReading.recorded_at - lag_until)``. Subtracting two
timestamps yields an INTERVAL, and PostgreSQL has no ``abs(interval)``. Every
virtual-sensor cycle raised ``UndefinedFunctionError``, so no virtual node ever
ingested a reading, every risk assessment froze on stale data, and the console
showed an identical score for every node.

These tests import ``nearest_reading_to`` from the production module, so a
regression in the real query is caught rather than a copy of it.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.virtual_sensors import nearest_reading_to  # noqa: E402

LAG_SECONDS = 45


def _sql() -> str:
    target = datetime.now(timezone.utc) - timedelta(seconds=LAG_SECONDS)
    return str(
        nearest_reading_to(1, target).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def test_ordering_extracts_epoch_from_the_interval():
    """`abs()` must wrap a number, never a bare interval."""
    sql = _sql().lower()
    assert "abs(" in sql
    assert "extract(epoch from" in sql, (
        "the interval must be converted to seconds before abs(); "
        "PostgreSQL has no abs(interval)"
    )


def test_ordering_does_not_wrap_a_bare_interval():
    sql = _sql().lower()
    assert "abs(telemetry_readings.recorded_at -" not in sql, (
        "abs(recorded_at - target) is the original bug"
    )


def test_filters_by_node_and_limits_to_one_row():
    sql = _sql().lower()
    assert "where telemetry_readings.node_id" in sql
    assert "order by" in sql
    assert "limit 1" in sql


def test_ordering_executes_against_postgres():
    """Live check: the real database must accept the generated SQL.

    Only a failure to REACH the database skips. A SQL error is a genuine
    regression and must fail loudly — skipping it would hide exactly the
    ``abs(interval)`` class of bug this file exists to catch.
    """
    import asyncio

    from app.core.database import engine

    async def run():
        target = datetime.now(timezone.utc) - timedelta(seconds=LAG_SECONDS)
        async with engine.connect() as conn:
            await conn.execute(nearest_reading_to(1, target))

    try:
        asyncio.run(run())
    except Exception as exc:  # noqa: BLE001
        unreachable = isinstance(
            exc, (ConnectionError, OSError, TimeoutError, asyncio.TimeoutError)
        ) or "Connect" in type(exc).__name__
        if unreachable:
            pytest.skip(f"database not available: {type(exc).__name__}: {exc}")
        raise
    finally:
        asyncio.run(engine.dispose())


def test_config_module_imports():
    """Cheap guard that app config still loads (catches import regressions)."""
    from app.core.config import get_settings

    assert get_settings() is not None