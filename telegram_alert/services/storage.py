"""MinIO storage: archive media and produce presigned GET links.

minio-py is synchronous; calls are offloaded to a thread so they don't block
the event loop.
"""

from __future__ import annotations

import asyncio
import io
import logging
from datetime import timedelta

from minio import Minio
from minio.error import S3Error

from telegram_alert.config import MinioSettings

log = logging.getLogger(__name__)


class MinioStorage:
    def __init__(self, cfg: MinioSettings) -> None:
        self._cfg = cfg
        self._client = Minio(
            cfg.endpoint,
            access_key=cfg.key,
            secret_key=cfg.secret.get_secret_value(),
            secure=cfg.secure,
        )

    async def ensure_bucket(self) -> None:
        def _ensure() -> None:
            if not self._client.bucket_exists(self._cfg.bucket):
                self._client.make_bucket(self._cfg.bucket)

        await asyncio.to_thread(_ensure)

    @staticmethod
    def snap_key(review_id: str) -> str:
        return f"events/{review_id}/snap.jpg"

    @staticmethod
    def clip_key(review_id: str) -> str:
        return f"events/{review_id}/clip.mp4"

    async def exists(self, key: str) -> bool:
        def _stat() -> bool:
            try:
                self._client.stat_object(self._cfg.bucket, key)
                return True
            except S3Error as e:
                if e.code in ("NoSuchKey", "NoSuchObject", "NotFound"):
                    return False
                raise

        return await asyncio.to_thread(_stat)

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        def _put() -> None:
            self._client.put_object(
                self._cfg.bucket,
                key,
                io.BytesIO(data),
                length=len(data),
                content_type=content_type,
            )

        await asyncio.to_thread(_put)
        log.info("MinIO put %s (%d bytes)", key, len(data))

    async def put_file(self, key: str, path: str, content_type: str) -> None:
        """Upload an on-disk file (used for large /record clips streamed to a
        temp file, so we never hold the whole video in memory)."""
        def _put() -> None:
            self._client.fput_object(self._cfg.bucket, key, path, content_type=content_type)

        await asyncio.to_thread(_put)
        log.info("MinIO put %s (from file %s)", key, path)

    async def get(self, key: str) -> bytes:
        def _get() -> bytes:
            resp = self._client.get_object(self._cfg.bucket, key)
            try:
                return resp.read()
            finally:
                resp.close()
                resp.release_conn()

        return await asyncio.to_thread(_get)

    async def presigned_get(self, key: str) -> str:
        def _presign() -> str:
            return self._client.presigned_get_object(
                self._cfg.bucket,
                key,
                expires=timedelta(seconds=self._cfg.presign_ttl),
            )

        return await asyncio.to_thread(_presign)
