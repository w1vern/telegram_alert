"""Frigate API client: JWT cookie auth with relogin-on-401, media fetch."""

from __future__ import annotations

import asyncio
import logging

import httpx

from telegram_alert.config import FrigateSettings

log = logging.getLogger(__name__)

# Brief in-line retries for single-shot fetches (snapshots) on a transport blip
# — typically Frigate restarting and dropping the connection mid-stream.
_SNAPSHOT_ATTEMPTS = 3
_RETRY_SLEEP = 2.0


class ClipNotReady(Exception):
    """Clip is not available within the timeout — still 404, or the connection
    kept dropping (e.g. Frigate restarting)."""


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

    async def _fetch_bytes(self, path: str, params: dict | None = None) -> bytes:
        """GET expecting a 200 body, with brief retries on transport errors.

        A transport error (``httpx.RequestError`` — e.g. ``RemoteProtocolError``
        when Frigate drops the connection mid-stream during a restart) is
        transient: the pooled keep-alive connection is stale, so we force a
        relogin (fresh connection) and retry a few times.  An HTTP *status*
        error (``raise_for_status``) is not transient and propagates — the
        broker will then schedule a durable retry of the whole job.
        """
        last: httpx.RequestError | None = None
        for attempt in range(_SNAPSHOT_ATTEMPTS):
            try:
                resp = await self._get(path, params)
                resp.raise_for_status()
                return resp.content
            except httpx.RequestError as e:
                last = e
                self._logged_in = False  # drop the stale connection on next try
                log.warning(
                    "Frigate GET %s transport error (%s), retry %d/%d",
                    path, e, attempt + 1, _SNAPSHOT_ATTEMPTS,
                )
                if attempt + 1 < _SNAPSHOT_ATTEMPTS:
                    await asyncio.sleep(_RETRY_SLEEP)
        assert last is not None
        raise last

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
        return await self._fetch_bytes(f"/api/{camera}/latest.jpg", self._snapshot_params("h"))

    async def _poll_clip(self, path: str, what: str, max_delay: float) -> bytes:
        """Poll an on-demand mp4 until it is ready or the timeout elapses.

        Both a not-ready status (404) and a transport error (Frigate restarting
        and dropping the stream) are transient: keep polling within the deadline.
        Only when the whole timeout elapses do we give up with ``ClipNotReady`` —
        which callers treat as 'clip unavailable' (best-effort), so a flapping
        Frigate degrades gracefully instead of escalating into a retry storm.
        """
        deadline = asyncio.get_event_loop().time() + self._cfg.clip_timeout
        delay = 2.0
        last = "?"
        while True:
            try:
                resp = await self._get(path)
                if resp.status_code == 200 and resp.content:
                    return resp.content
                last = f"status {resp.status_code}"
            except httpx.RequestError as e:
                self._logged_in = False  # stale connection after a restart
                last = f"transport error ({e})"
            if asyncio.get_event_loop().time() >= deadline:
                raise ClipNotReady(f"{what} not ready (last: {last})")
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, max_delay)

    async def get_recording_clip(self, camera: str, start: float, end: float) -> bytes:
        """Fixed-length clip cut from continuous recordings [start, end].

        Frigate generates the mp4 on demand, so retry briefly until it's ready.
        """
        path = f"/api/{camera}/start/{start:.3f}/end/{end:.3f}/clip.mp4"
        return await self._poll_clip(path, f"recording clip for {camera}", max_delay=8.0)

    async def download_recording_clip(
        self, camera: str, start: float, end: float, dest_path: str
    ) -> None:
        """Stream a recording clip [start, end] straight to ``dest_path``.

        Like :meth:`get_recording_clip` but built for arbitrarily long clips
        (the /record command): Frigate assembles the mp4 on demand, so poll
        until it is ready, then stream the body to disk in chunks — never
        buffering the whole file in memory. A not-ready status (404) and a
        transport blip (Frigate restarting) are both transient and retried
        within ``record_timeout``; only when that elapses do we give up with
        :class:`ClipNotReady` (callers treat it as 'clip unavailable').
        """
        path = f"/api/{camera}/start/{start:.3f}/end/{end:.3f}/clip.mp4"
        deadline = asyncio.get_event_loop().time() + self._cfg.record_timeout
        delay = 2.0
        last = "?"
        # No read timeout: a large clip streams for a while and the chunks keep
        # the transfer alive; the deadline above bounds the *generation* wait.
        timeout = httpx.Timeout(self._cfg.request_timeout, read=None)
        while True:
            if not self._logged_in:
                await self._login()
            try:
                async with self._client.stream("GET", path, timeout=timeout) as resp:
                    if resp.status_code == 200:
                        with open(dest_path, "wb") as f:
                            async for chunk in resp.aiter_bytes(1 << 20):
                                f.write(chunk)
                        return
                    if resp.status_code == 401:
                        self._logged_in = False
                    last = f"status {resp.status_code}"
                    await resp.aread()  # drain the body so the loop can retry cleanly
            except httpx.RequestError as e:
                self._logged_in = False  # stale connection after a restart
                last = f"transport error ({e})"
            if asyncio.get_event_loop().time() >= deadline:
                raise ClipNotReady(f"recording clip for {camera} not ready (last: {last})")
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 8.0)

    async def get_snapshot(self, event_id: str) -> bytes:
        return await self._fetch_bytes(
            f"/api/events/{event_id}/snapshot.jpg", self._snapshot_params("height")
        )

    async def get_clip(self, event_id: str) -> bytes:
        """Poll for the event clip until it is ready or the timeout elapses."""
        return await self._poll_clip(
            f"/api/events/{event_id}/clip.mp4", f"clip {event_id}", max_delay=10.0
        )
