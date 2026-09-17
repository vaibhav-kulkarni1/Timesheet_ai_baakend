"""
FastAPI app entrypoint. Run with: uvicorn app.main:app --reload
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import anomalies, events, health, productivity, recommendations, utilization, webhooks
from app.db.session import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    # Explicit tag order controls the section order shown on the /docs page.
    openapi_tags = [
        {"name": "recommendations", "description": "Approval queue"},
        {"name": "anomalies", "description": "Anomaly detection"},
        {"name": "utilization", "description": "Utilization trends"},
        {"name": "productivity", "description": "Productivity insights"},
        {"name": "employee-health", "description": "Employee health / burnout risk"},
        {"name": "webhooks", "description": "Workfront triggers"},
        {"name": "events", "description": "Live updates"},
    ]
    app = FastAPI(
        title="AI Timesheet Approval API",
        description="Serving API for the AI-assisted timesheet approval dashboard.",
        version="0.1.0",
        lifespan=lifespan,
        openapi_tags=openapi_tags,
    )

    # Loosen for the embedded Workfront dashboard's origin in prod as needed.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(recommendations.router)
    app.include_router(anomalies.router)
    app.include_router(utilization.router)
    app.include_router(productivity.router)
    app.include_router(health.router)
    app.include_router(webhooks.router)
    app.include_router(events.router)

    @app.get("/", include_in_schema=False)
    def root():
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url="/dashboard")

    @app.get("/dashboard", include_in_schema=False)
    def dashboard():
        from pathlib import Path

        from fastapi.responses import FileResponse, PlainTextResponse

        path = Path(__file__).resolve().parent.parent / "dashboard.html"
        if not path.exists():
            return PlainTextResponse("dashboard.html not found next to the project root.", status_code=404)
        return FileResponse(str(path))

    @app.get("/health")
    def health_check():
        return {"status": "ok"}

    return app


app = create_app()
