"""
Step 7 — Real-time updates. Redis pub/sub + SSE, matching the existing
pattern described in the architecture doc: when the pipeline writes a new
recommendation row, publish an event; the dashboard subscribes via SSE and
updates the manager's queue live instead of polling.

Degrades gracefully: if Redis isn't reachable (e.g. local dev without it
running), publish becomes a no-op and the stream endpoint simply never
yields events — the dashboard's normal GET /api/recommendations polling
still works, it just isn't push-updated.
"""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import datetime

CHANNEL = "timesheet_recommendation_updates"


def _get_redis():
    import redis

    from app.config import get_settings

    return redis.from_url(get_settings().redis_url, decode_responses=True)


def publish_recommendation_update(row) -> None:
    """`row` is a TimesheetRecommendation ORM instance. Publish a small
    JSON-serializable event, not the whole row — subscribers refetch details
    via the Serving API, keeping the pub/sub payload cheap and consistent
    with the pre-computed-store-is-truth pattern."""
    payload = {
        "employee_id": row.employee_id,
        "period_start": row.period_start.isoformat(),
        "recommendation": row.recommendation,
        "manager_id": row.manager_id,
        "computed_at": (row.computed_at or datetime.utcnow()).isoformat(),
    }
    client = _get_redis()
    client.publish(CHANNEL, json.dumps(payload))


async def subscribe_recommendation_updates() -> AsyncGenerator[str, None]:
    """Async generator of SSE-ready JSON strings, for use with sse-starlette
    in the API route. Yields nothing (blocks) if Redis is unavailable rather
    than raising, so the endpoint stays open and harmless."""
    import asyncio

    try:
        client = _get_redis()
        pubsub = client.pubsub()
        pubsub.subscribe(CHANNEL)
    except Exception:
        return

    try:
        while True:
            message = pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message.get("type") == "message":
                yield message["data"]
            await asyncio.sleep(0.1)
    finally:
        pubsub.close()
