"""Message schemas exchanged over the internal RabbitMQ work queues.

Two stages:
  * ``MediaJob``   -> ``jobs`` queue   (download/archive media from Frigate)
  * ``OutboxJob``  -> ``outbox`` queue (the only proxy-dependent step: send TG)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class MediaJob(BaseModel):
    """Produced by the MQTT ingress from a frigate/reviews message."""

    kind: Literal["media"] = "media"
    type: Literal["new", "end"]
    review_id: str
    camera: str
    # First detection event id; media is fetched by event_id from Frigate.
    # Empty for /test jobs, which pull camera recordings by time instead.
    event_id: str = ""
    # epoch seconds of the review start (for the caption timestamp)
    ts: float
    # Manual /clip request: bypass the schedule/mode, pull a fixed-length clip
    # from the camera's continuous recordings instead of an event.
    on_demand: bool = False
    # Requested clip length in seconds (None -> use FRIGATE_CLIP_SECONDS).
    clip_seconds: int | None = None
    # Absolute start of an on-demand clip (epoch seconds). When set, the worker
    # cuts [start_ts, start_ts + clip_seconds] from continuous recordings — the
    # /record command, an arbitrary past timecode. When None, on_demand pulls the
    # *last* clip_seconds ending ~now (the /clip command).
    start_ts: float | None = None


class OutboxJob(BaseModel):
    """A pending Telegram action.  Lives in a durable queue so it survives a
    dead proxy and a service restart; media is already in MinIO."""

    kind: Literal["outbox"] = "outbox"
    action: Literal["photo_alert", "attach_clip", "clip", "record"]
    review_id: str
    camera: str
    ts: float
    snap_key: str | None = None
    clip_key: str | None = None
    # Clip length in seconds — used by the /record caption (action == "record").
    clip_seconds: int | None = None
