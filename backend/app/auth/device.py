"""
FLOODGUARD AI — device authentication.

A physical node authenticates with `Authorization: Bearer <token>` against the
SHA-256 hash stored in `sensor_nodes.token_hash`. Tokens are issued once at
registration and never stored in plaintext.

Virtual nodes have no token; they are not reachable over HTTP, only through
the internal simulation services.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.floodguard import SensorNode

TOKEN_BYTES = 32


def generate_device_token() -> str:
    """Issue a fresh bearer token. Returned to the caller exactly once."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_device_token(token: str) -> str:
    """Hash a bearer token for storage/comparison.

    SHA-256 is appropriate here because the token is high-entropy random
    output, not a user-chosen password, so there is nothing to brute-force.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _extract_bearer(request: Request) -> str | None:
    header = request.headers.get("Authorization") or request.headers.get("authorization")
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


async def get_current_device(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> SensorNode:
    """Authenticate a physical device and reject devices that are
    known but deactivated, so a decommissioned sensor cannot silently keep
    writing readings.
    """
    token = _extract_bearer(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token. Send 'Authorization: Bearer <device_token>'.",
        )

    token_hash = hash_device_token(token)
    result = await db.execute(
        select(SensorNode).where(SensorNode.__table__.c.token_hash == token_hash)
    )
    node = result.scalar_one_or_none()

    if node is None:
        # Constant-time compare against a dummy value so a missing node and a
        # wrong token take the same time and cannot be told apart by timing.
        hmac.compare_digest(token_hash, "0" * 64)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unknown device token.",
        )

    if not node.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Node {node.node_code} is deactivated.",
        )

    request.state.operator = {"sub": node.node_code, "role": "device", "device": node.node_code}
    return node