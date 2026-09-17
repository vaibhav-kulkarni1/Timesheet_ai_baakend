"""
Diagnostic: verify the client-credentials auto-refresh auth actually works
against Adobe IMS + your Workfront instance, before relying on it in the
sync scripts or a deployment.

Usage:
    # .env: WORKFRONT_CLIENT_ID and WORKFRONT_CLIENT_SECRET set
    python -m scripts.check_auth
"""
from __future__ import annotations

from app.config import get_settings
from app.services.workfront_auth import WorkfrontTokenAuth


def main() -> None:
    settings = get_settings()

    if not settings.workfront_client_id or not settings.workfront_client_secret:
        print("WORKFRONT_CLIENT_ID / WORKFRONT_CLIENT_SECRET not set in .env — nothing to test.")
        print("(This is fine if you're still using the static WORKFRONT_API_KEY fallback.)")
        return

    print(f"Token URL:   {settings.workfront_token_url}")
    print(f"Client ID:   {settings.workfront_client_id[:8]}...")
    print(f"Scope:       {settings.workfront_token_scope}")
    print()

    auth = WorkfrontTokenAuth(
        token_url=settings.workfront_token_url,
        client_id=settings.workfront_client_id,
        client_secret=settings.workfront_client_secret,
        scope=settings.workfront_token_scope,
    )

    print("Fetching token...")
    try:
        token = auth._ensure_token()
    except Exception as e:  # noqa: BLE001
        print(f"FAILED to fetch token: {e}")
        print("\nCommon causes:")
        print("  - Wrong client_id / client_secret")
        print("  - Scope not enabled for this API integration in the Adobe Developer Console")
        print("  - The integration isn't authorized for Workfront's API")
        return

    print(f"Got a token ({len(token)} chars). Expires at (unix): {auth._expires_at:.0f}\n")

    print("Now testing it against Workfront (/tshet/search, limit 1)...")
    import httpx

    client = httpx.Client(base_url=settings.workfront_base_url.rstrip("/"), auth=auth, timeout=30.0)
    resp = client.get("/tshet/search", params={"fields": "ID,displayName", "$$LIMIT": 1})

    if resp.status_code == 200:
        rows = resp.json().get("data", [])
        print(f"SUCCESS — Workfront returned {len(rows)} row(s).")
        if rows:
            print(f"  Sample: {rows[0].get('displayName')}")
        print("\nClient-credentials auth is working. You can now rely on it for")
        print("the web service and both cron jobs — no more manual token pasting.")
    else:
        print(f"Workfront call failed: {resp.status_code} {resp.text[:300]}")


if __name__ == "__main__":
    main()