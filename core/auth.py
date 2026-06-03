"""
OAuth2 token manager для Snapchat Marketing API.
Access token живёт 60 минут — обновляется автоматически.
"""
from __future__ import annotations
import os
import time
import logging
import requests
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

TOKEN_URL = "https://accounts.snapchat.com/login/oauth2/access_token"


class SnapAuth:
    def __init__(self):
        self._client_id = os.environ["SNAP_CLIENT_ID"]
        self._client_secret = os.environ["SNAP_CLIENT_SECRET"]
        self._refresh_token = os.environ["SNAP_REFRESH_TOKEN"]
        self._access_token: str = ""
        self._expires_at: float = 0.0

    def token(self) -> str:
        """Возвращает актуальный access token, обновляя при необходимости."""
        if time.time() >= self._expires_at - 60:
            self._refresh()
        return self._access_token

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token()}"}

    def _refresh(self) -> None:
        r = requests.post(TOKEN_URL, data={
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }, timeout=15)
        r.raise_for_status()
        data = r.json()
        if "access_token" not in data:
            raise RuntimeError(f"Token refresh failed: {data}")
        self._access_token = data["access_token"]
        self._expires_at = time.time() + data.get("expires_in", 3600)
        log.debug("Snapchat token refreshed, expires in %ss", data.get("expires_in"))
