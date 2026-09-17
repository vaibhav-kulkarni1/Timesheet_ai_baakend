"""
Auto-refreshing Adobe IMS OAuth token, using the client_credentials grant
(server-to-server auth — no user login, no manual token pasting).

Adobe access tokens are valid for 24h; Adobe's own guidance is to refresh
every ~23h. This module fetches a token on first use, caches it in memory,
and transparently fetches a fresh one shortly before it expires — every
request just works, indefinitely, with zero manual intervention.

Implemented as an httpx.Auth so it plugs into httpx.Client(auth=...) and
applies to every request automatically, including retries.
"""
from __future__ import annotations

import threading
import time

import httpx


class TokenExpiredOrMissing(RuntimeError):
    pass


class WorkfrontTokenAuth(httpx.Auth):
    """
    httpx.Auth implementation that fetches and refreshes an Adobe IMS access
    token via the client_credentials grant, then attaches it as a Bearer
    token on every outgoing request.

    Thread-safe: a lock guards the refresh so concurrent requests during a
    refresh don't each fire their own token request.
    """

    # Refresh this many seconds BEFORE the token's stated expiry, so a
    # request never gets caught using a token that expires mid-flight.
    REFRESH_MARGIN_SECONDS = 300  # 5 minutes

    def __init__(self, token_url: str, client_id: str, client_secret: str, scope: str):
        if not client_id or not client_secret:
            raise ValueError("WorkfrontTokenAuth requires a client_id and client_secret")
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.scope = scope

        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = threading.Lock()

    def _needs_refresh(self) -> bool:
        return self._token is None or time.time() >= (self._expires_at - self.REFRESH_MARGIN_SECONDS)

    def _fetch_token(self) -> None:
        """Blocking token fetch. Called under self._lock."""
        resp = httpx.post(
            self.token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": self.scope,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30.0,
        )
        if resp.status_code != 200:
            # Adobe puts the real reason (invalid_scope, invalid_client, etc.)
            # in the response body — raise_for_status() alone discards it, so
            # surface it explicitly for anyone debugging a 400/401 here.
            raise TokenExpiredOrMissing(
                f"Adobe IMS token request failed ({resp.status_code}): {resp.text}"
            )
        payload = resp.json()

        token = payload.get("access_token")
        if not token:
            raise TokenExpiredOrMissing(f"Adobe IMS token response had no access_token: {payload}")

        expires_in = float(payload.get("expires_in", 86399))
        self._token = token
        self._expires_at = time.time() + expires_in

    def _ensure_token(self) -> str:
        if self._needs_refresh():
            with self._lock:
                # Re-check inside the lock — another thread may have just refreshed.
                if self._needs_refresh():
                    self._fetch_token()
        assert self._token is not None
        return self._token

    # -- httpx.Auth interface ------------------------------------------------
    def auth_flow(self, request: httpx.Request):
        token = self._ensure_token()
        request.headers["Authorization"] = f"Bearer {token}"
        response = yield request

        # If the token was rejected mid-life (revoked, clock skew, etc.),
        # force one refresh and retry the request once.
        if response.status_code == 401:
            with self._lock:
                self._token = None  # force refresh on next _ensure_token()
            token = self._ensure_token()
            request.headers["Authorization"] = f"Bearer {token}"
            yield request