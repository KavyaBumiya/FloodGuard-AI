"""
FLOODGUARD AI — operator identity dependency.

The system has no user accounts or login flow: the PRD models a single
operator dashboard, not a multi-tenant system. Write endpoints therefore take
this dependency so that operator identity (from the authenticated device
token, or "dashboard" when anonymous) is recorded and available, and so a
real auth scheme can be dropped in later without touching route signatures.

Acknowledgement records store `acknowledged_by`, so this value is what ends
up in the alert audit trail.
"""
from __future__ import annotations

from typing import Any

from fastapi import Depends, Request


def get_current_operator(request: Request) -> dict[str, Any]:
    """Resolve the acting operator.

    Anonymous browsers get "dashboard". A registered node posting with its
    bearer token is attributed to that node code, so telemetry-driven
    acknowledgements are traceable back to the hardware.
    """
    identity = getattr(request.state, "operator", None)
    if identity:
        return identity
    return {"sub": "dashboard", "role": "operator", "device": None}


async def get_current_user(
    request: Request,
    operator: dict[str, Any] = Depends(get_current_operator),
) -> dict[str, Any]:
    """Route-facing dependency kept under its original name."""
    return operator