"""
FLOODGUARD AI — Replay & Demo API
==================================
Demo mode control, replay recordings, and system reset.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.database import get_db
from app.models.floodguard import ReplayRecording
from app.services.demo_controller import demo_controller

router = APIRouter(prefix="/api/demo", tags=["demo"])


@router.get("/status")
async def demo_status(
    _: dict = Depends(get_current_user),
):
    """Get current demo mode status."""
    return await demo_controller.get_status()


@router.post("/mode")
async def set_demo_mode(
    mode: str = Query(..., pattern="^(live|replay|scenario)$"),
    _: dict = Depends(get_current_user),
):
    """Set demo mode: live, replay, or scenario."""
    await demo_controller.set_mode(mode)
    return {"mode": mode, "message": f"Mode set to {mode}"}


@router.post("/reset")
async def reset_demo(
    _: dict = Depends(get_current_user),
):
    """Complete demo reset: clears all dynamic data, returns to clean state."""
    await demo_controller.reset_demo()
    return {"message": "Demo reset completed"}


@router.post("/seed")
async def seed_demo_data(
    _: dict = Depends(get_current_user),
):
    """Seed demo with baseline sensor nodes and virtual nodes."""
    await demo_controller.seed_demo_data()
    return {"message": "Demo data seeded"}


@router.get("/recordings")
async def list_recordings(
    _: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List available replay recordings."""
    recordings = await demo_controller.list_recordings()
    return {
        "recordings": [
            {
                "id": r.id,
                "name": r.name,
                "description": r.description,
                "origin": r.origin,
                "duration_seconds": r.duration_seconds,
                "created_at": r.created_at.isoformat(),
            }
            for r in recordings
        ]
    }


@router.post("/recordings")
async def create_recording(
    name: str,
    duration_seconds: int = 300,
    _: dict = Depends(get_current_user),
):
    """Create a recording from recent live data."""
    recording = await demo_controller.create_demo_recording(name, duration_seconds)
    return {
        "id": recording.id,
        "name": recording.name,
        "duration_seconds": recording.duration_seconds,
        "message": "Recording created",
    }


@router.post("/replay/{recording_id}/start")
async def start_replay(
    recording_id: int,
    speed: float = Query(1.0, ge=0.1, le=10.0),
    _: dict = Depends(get_current_user),
):
    """Start replay of a recorded session."""
    try:
        await demo_controller.start_replay(recording_id, speed)
        return {"message": f"Replay started at {speed}x speed"}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/replay/stop")
async def stop_replay(
    _: dict = Depends(get_current_user),
):
    """Stop current replay."""
    await demo_controller.stop_replay()
    return {"message": "Replay stopped"}