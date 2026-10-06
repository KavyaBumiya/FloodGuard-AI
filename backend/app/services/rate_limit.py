"""Per-device rate limiting for `POST /api/iot/telemetry`.

The ESP32 posts on a fixed cadence, so the number of accepted requests per
minute is predictable. Anything above that is a malfunctioning or spoofed
device, and it is expensive to serve: one accepted reading runs four flood
simulations inline before the response is written.

Counters are per-process. That matches the documented deployment (a single
uvicorn worker, see the CMD comment in backend/Dockerfile), where this is an
exact limit. If the worker count is ever raised, the effective allowance
multiplies by that count - move this to Redis before doing so.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException

_WINDOW_SECONDS = 60.0

# node_code -> deque of accepted-at monotonic timestamps
_hits: dict[str, deque[float]] = defaultdict(deque)
_last_sweep = 0.0
_SWEEP_EVERY_SECONDS = 300.0


def _sweep(now: float) -> None:
    """Drop buckets that have gone quiet, so node codes cannot leak memory.

    A device that registers, sends one reading and disappears would otherwise
    keep its deque for the lifetime of the process.
    """
    global _last_sweep
    if now - _last_sweep < _SWEEP_EVERY_SECONDS:
        return
    _last_sweep = now
    for code in [c for c, d in _hits.items() if not d or now - d[-1] > _WINDOW_SECONDS]:
        del _hits[code]


def enforce(node_code: str, limit_per_minute: int) -> None:
    """Raise 429 if `node_code` has exceeded `limit_per_minute` this window."""
    if limit_per_minute <= 0:
        return

    now = time.monotonic()
    _sweep(now)

    bucket = _hits[node_code]
    cutoff = now - _WINDOW_SECONDS
    while bucket and bucket[0] <= cutoff:
        bucket.popleft()

    if len(bucket) >= limit_per_minute:
        retry_after = max(1, int(round(_WINDOW_SECONDS - (now - bucket[0]))))
        raise HTTPException(
            status_code=429,
            detail=f"Telemetry rate limit exceeded ({limit_per_minute}/minute).",
            headers={"Retry-After": str(retry_after)},
        )

    bucket.append(now)


def reset() -> None:
    """Clear all counters. Tests only."""
    _hits.clear()