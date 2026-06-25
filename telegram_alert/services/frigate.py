"""Frigate API client: JWT cookie auth with relogin-on-401, media fetch."""

from __future__ import annotations

import asyncio
import logging

import httpx

from telegram_alert.config import FrigateSettings

log = logging.getLogger(__name__)


class ClipNotReady(Exception):
    """Clip is not available yet (still 404 after the timeout)."""


class FrigateClient:
    def __init__(self, cfg: FrigateSettings) -> None:
        self._cfg = cfg
        self._client = httpx.AsyncClient(
            base_url=cfg.url.rstrip("/"),
            timeout=cfg.request_timeout,
            follow_redirects=True,
        )
        self._lock = asyncio.Lock()
        self._logged_in = False

    async def close(self) -> None:
        await self._client.aclose()

    async def _login(self) -> None:
        async with self._lock:
            resp = await self._client.post(
                "/api/login",
                json={
                    "user": self._cfg.user,
                    "password": self._cfg.password.get_secret_value(),
                },
            )
            resp.raise_for_status()
            # JWT lands in the client's cookie jar automatically.
            self._logged_in = True
            log.info("Frigate login ok")

    async def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        """GET with one transparent relogin on 401."""
        if not self._logged_in:
            await self._login()
        resp = await self._client.get(path, params=params)
        if resp.status_code == 401:
            log.info("Frigate 401, re-logging in")
            self._logged_in = False
            await self._login()
            resp = await self._client.get(path, params=params)
        return resp

    async def list_cameras(self) -> list[str]:
        resp = await self._get("/api/config")
        resp.raise_for_status()
        cfg = resp.json()
        return list((cfg.get("cameras") or {}).keys())

    def _snapshot_params(self, height_key: str) -> dict[str, int]:
        """Quality/height overrides for the snapshot endpoints.  ``latest.jpg``
        names the height param ``h``; ``events/.../snapshot.jpg`` names it
        ``height`` — hence the key argument.  A 0 value means 'omit'."""
        params: dict[str, int] = {}
        if self._cfg.snapshot_quality:
            params["quality"] = self._cfg.snapshot_quality
        if self._cfg.snapshot_height:
            params[height_key] = self._cfg.snapshot_height
        return params

    async def get_camera_snapshot(self, camera: str) -> bytes:
        """Current frame from the camera (independent of any event)."""
        resp = await self._get(f"/api/{camera}/latest.jpg", self._snapshot_params("h"))
        resp.raise_for_status()
        return resp.content

    async def get_recording_clip(self, camera: str, start: float, end: float) -> bytes:
        """Fixed-length clip cut from continuous recordings [start, end].

        Frigate generates the mp4 on demand, so retry briefly until it's ready.
        """
        path = f"/api/{camera}/start/{start:.3f}/end/{end:.3f}/clip.mp4"
        deadline = asyncio.get_event_loop().time() + self._cfg.clip_timeout
        delay = 2.0
        last_status = None
        while True:
            resp = await self._get(path)
            if resp.status_code == 200 and resp.content:
                return resp.content
            last_status = resp.status_code
            if asyncio.get_event_loop().time() >= deadline:
                raise ClipNotReady(
                    f"recording clip for {camera} not ready (last status {last_status})"
                )
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 8.0)

    async def get_snapshot(self, event_id: str) -> bytes:
        resp = await self._get(
            f"/api/events/{event_id}/snapshot.jpg", self._snapshot_params("height")
        )
        resp.raise_for_status()
        return resp.content

    async def get_clip(self, event_id: str) -> bytes:
        """Poll for the clip until it is ready or the timeout elapses."""
        deadline = asyncio.get_event_loop().time() + self._cfg.clip_timeout
        delay = 2.0
        last_status = None
        while True:
            resp = await self._get(f"/api/events/{event_id}/clip.mp4")
            if resp.status_code == 200:
                return resp.content
            last_status = resp.status_code
            if asyncio.get_event_loop().time() >= deadline:
                raise ClipNotReady(
                    f"clip {event_id} not ready (last status {last_status})"
                )
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 10.0)
