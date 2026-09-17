"""SSE endpoint the dashboard subscribes to for live queue updates."""
from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from app.realtime.events import subscribe_recommendation_updates

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("/stream")
async def stream_updates():
    return EventSourceResponse(subscribe_recommendation_updates())
