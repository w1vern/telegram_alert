"""Application configuration.

Env variables are split into groups, one ``BaseSettings`` subclass per group,
each with its own ``env_prefix`` so the variable names stay flat and readable
(e.g. ``MQTT_HOST``, ``FRIGATE_URL``).  The top-level :class:`Settings` simply
composes the groups together.
"""

from __future__ import annotations

from datetime import timedelta, timezone, tzinfo
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = ".env"


def _cfg(prefix: str) -> SettingsConfigDict:
    return SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        env_prefix=prefix,
    )


class _Base(BaseSettings):
    model_config = _cfg("")


class AppSettings(_Base):
    model_config = _cfg("APP_")

    # UTC offset (hours) of the place where the cameras are — the dacha local
    # time. Used to interpret the schedule and to format times in messages.
    # E.g. Moscow = 3, Magadan = 11. Fractional ok (e.g. 5.5).
    utc_offset: float = 3
    log_level: str = "INFO"

    @property
    def tzinfo(self) -> tzinfo:
        return timezone(timedelta(hours=self.utc_offset))


class TelegramSettings(_Base):
    model_config = _cfg("TG_")

    token: SecretStr
    # Comma-separated Telegram user ids with full admin rights. Always
    # authorized; can grant/revoke access for other users.
    superusers: str = ""
    # SOCKS5/HTTP proxy used for ALL Telegram traffic, e.g.
    # socks5://user:pass@host:1080 . Telegram is unreachable without it.
    proxy_url: str | None = None

    @property
    def superuser_ids(self) -> list[int]:
        return [int(x) for x in self.superusers.replace(" ", "").split(",") if x]


class DatabaseSettings(_Base):
    model_config = _cfg("DB_")

    host: str = "postgres"
    port: int = 5432
    user: str = "alert"
    password: SecretStr = SecretStr("alert")
    name: str = "alert"

    @property
    def dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.name}"
        )


class MqttSettings(_Base):
    model_config = _cfg("MQTT_")

    host: str
    port: int = 1883
    tls: bool = False
    user: str | None = None
    password: SecretStr | None = None
    topic: str = "frigate/reviews"
    # Stable client id + persistent session so short disconnects don't drop events.
    client_id: str = "dacha-alert-bot"


class AmqpSettings(_Base):
    """Internal durable work queues live on the same RabbitMQ as the MQTT plugin."""

    model_config = _cfg("AMQP_")

    host: str
    port: int = 5672
    tls: bool = False  # use amqps:// (TLS), typically on port 5671
    user: str = "guest"
    password: SecretStr = SecretStr("guest")
    vhost: str = "/"

    exchange: str = "dacha.alert"
    jobs_queue: str = "dacha.alert.jobs"
    outbox_queue: str = "dacha.alert.outbox"
    # Delayed-retry backoff steps (seconds) applied via dead-letter TTL.
    retry_delays: tuple[int, ...] = (5, 15, 30, 60, 120, 300, 600)
    prefetch: int = 16

    @property
    def url(self) -> str:
        scheme = "amqps" if self.tls else "amqp"
        return (
            f"{scheme}://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.vhost.lstrip('/')}"
        )


class FrigateSettings(_Base):
    model_config = _cfg("FRIGATE_")

    url: str = "https://frigate.example.com"
    user: str
    password: SecretStr
    # How long to poll for the clip to become available after an event ends.
    clip_timeout: int = 90
    request_timeout: int = 30


class MinioSettings(_Base):
    model_config = _cfg("MINIO_")

    endpoint: str  # host:port, no scheme (minio-py convention)
    key: str
    secret: SecretStr
    bucket: str = "dacha-events"
    secure: bool = True
    presign_ttl: int = 604800  # 7 days


class Settings(_Base):
    app: AppSettings = Field(default_factory=AppSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    db: DatabaseSettings = Field(default_factory=DatabaseSettings)
    mqtt: MqttSettings = Field(default_factory=MqttSettings)
    amqp: AmqpSettings = Field(default_factory=AmqpSettings)
    frigate: FrigateSettings = Field(default_factory=FrigateSettings)
    minio: MinioSettings = Field(default_factory=MinioSettings)


@lru_cache
def get_settings() -> Settings:
    return Settings()
