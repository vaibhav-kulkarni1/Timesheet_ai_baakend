from app.db.session import get_db  # re-exported so routers only import from app.api.deps

__all__ = ["get_db"]
