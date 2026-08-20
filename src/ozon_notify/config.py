from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


TELEGRAM_TOPIC_KEYS = frozenset(
    {
        "sales",
        "rfbs",
        "messages",
        "returns",
        "logistics",
        "supplies",
        "finance",
        "news",
        "system",
    }
)


def load_dotenv(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def _positive_int_env(name: str, default: int) -> int:
    value = _int_env(name, default)
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive integer")
    return value


def _telegram_chat_id_env(name: str) -> str | None:
    value = (os.getenv(name) or "").strip()
    if not value:
        return None
    if value.isdigit() and value.startswith("100"):
        return f"-{value}"
    return value


@dataclass(frozen=True)
class AccountConfig:
    name: str
    slug: str
    client_id: str
    api_key: str
    telegram_chat_id: str | None = None
    telegram_topics: dict[str, int] | None = None

    @property
    def effective_chat_id(self) -> str | None:
        return self.telegram_chat_id or None

    def topic_id(self, topic: str | None) -> int | None:
        if not topic or not self.telegram_topics:
            return None
        return self.telegram_topics.get(topic)


@dataclass(frozen=True)
class MVideoConfig:
    name: str
    slug: str
    api_key: str
    telegram_chat_id: str | None = None
    telegram_topics: dict[str, int] | None = None

    @property
    def effective_chat_id(self) -> str | None:
        return self.telegram_chat_id or None

    def topic_id(self, topic: str | None) -> int | None:
        if not topic or not self.telegram_topics:
            return None
        return self.telegram_topics.get(topic)


@dataclass(frozen=True)
class Settings:
    app_env: str
    app_mode: str
    port: int
    bind_host: str
    database_path: str
    public_base_url: str
    webhook_secret: str
    telegram_bot_token: str
    telegram_news_bot_token: str | None
    telegram_default_chat_id: str | None
    telegram_news_chat_id: str | None
    telegram_channel_invite_link: str | None
    telegram_proxy_url: str | None
    telegram_relay_url: str | None
    telegram_relay_secret: str | None
    ozon_base_url: str
    accounts: list[AccountConfig]
    mvideo_base_url: str
    mvideo_account: MVideoConfig | None
    mvideo_poll_seconds: int
    mvideo_lookback_hours: int
    important_poll_seconds: int
    secondary_poll_seconds: int
    news_poll_seconds: int
    news_notifications_enabled: bool
    bootstrap_send_existing: bool
    bootstrap_lookback_hours: int

    @property
    def webhook_url_template(self) -> str:
        base = self.public_base_url.rstrip("/")
        return f"{base}/webhook/ozon/{{slug}}/{self.webhook_secret}"


def _load_accounts(required: bool = True) -> list[AccountConfig]:
    raw = os.getenv("OZON_ACCOUNTS_JSON")
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"OZON_ACCOUNTS_JSON is not valid JSON: {exc}") from exc
        if not isinstance(data, list):
            raise RuntimeError("OZON_ACCOUNTS_JSON must be a JSON list")
        return [_account_from_dict(item) for item in data]

    client_id = os.getenv("OZON_CLIENT_ID")
    api_key = os.getenv("OZON_API_KEY")
    if client_id and api_key:
        return [
            AccountConfig(
                name=os.getenv("OZON_ACCOUNT_NAME", "Ozon account"),
                slug=os.getenv("OZON_ACCOUNT_SLUG", "default"),
                client_id=client_id,
                api_key=api_key,
                telegram_chat_id=os.getenv("OZON_TELEGRAM_CHAT_ID") or None,
            )
        ]

    if required:
        raise RuntimeError("Configure OZON_ACCOUNTS_JSON or OZON_CLIENT_ID/OZON_API_KEY")
    return []


def _account_from_dict(item: Any) -> AccountConfig:
    if not isinstance(item, dict):
        raise RuntimeError("Each Ozon account must be an object")
    name = str(item.get("name") or "").strip()
    slug = str(item.get("slug") or name.lower().replace(" ", "-")).strip()
    client_id = str(item.get("client_id") or "").strip()
    api_key = str(item.get("api_key") or "").strip()
    chat_id = str(item.get("telegram_chat_id") or "").strip() or None
    raw_topics = item.get("telegram_topics") or {}
    if not isinstance(raw_topics, dict):
        raise RuntimeError("telegram_topics must be an object")
    unknown_topics = set(raw_topics) - TELEGRAM_TOPIC_KEYS
    if unknown_topics:
        raise RuntimeError(
            "Unknown telegram_topics keys: " + ", ".join(sorted(unknown_topics))
        )
    topics: dict[str, int] = {}
    for topic, raw_thread_id in raw_topics.items():
        try:
            thread_id = int(raw_thread_id)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"telegram_topics.{topic} must be a positive integer"
            ) from exc
        if thread_id <= 0:
            raise RuntimeError(
                f"telegram_topics.{topic} must be a positive integer"
            )
        topics[topic] = thread_id

    missing = [
        field
        for field, value in {
            "name": name,
            "slug": slug,
            "client_id": client_id,
            "api_key": api_key,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f"Ozon account config misses fields: {', '.join(missing)}")

    return AccountConfig(
        name=name,
        slug=slug,
        client_id=client_id,
        api_key=api_key,
        telegram_chat_id=chat_id,
        telegram_topics=topics or None,
    )


def _load_mvideo_account(accounts: list[AccountConfig]) -> MVideoConfig | None:
    api_key = (os.getenv("MVIDEO_API_KEY") or "").strip()
    if not api_key:
        return None

    fallback = accounts[0] if accounts else None
    chat_id = _telegram_chat_id_env("MVIDEO_TELEGRAM_CHAT_ID")
    if chat_id is None and fallback:
        chat_id = fallback.telegram_chat_id

    raw_topics = (os.getenv("MVIDEO_TELEGRAM_TOPICS_JSON") or "").strip()
    if raw_topics:
        try:
            parsed_topics = json.loads(raw_topics)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"MVIDEO_TELEGRAM_TOPICS_JSON is not valid JSON: {exc}"
            ) from exc
        if not isinstance(parsed_topics, dict):
            raise RuntimeError("MVIDEO_TELEGRAM_TOPICS_JSON must be a JSON object")
        unknown_topics = set(parsed_topics) - TELEGRAM_TOPIC_KEYS
        if unknown_topics:
            raise RuntimeError(
                "Unknown MVIDEO_TELEGRAM_TOPICS_JSON keys: "
                + ", ".join(sorted(unknown_topics))
            )
        topics: dict[str, int] = {}
        for topic, raw_thread_id in parsed_topics.items():
            try:
                thread_id = int(raw_thread_id)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"MVIDEO_TELEGRAM_TOPICS_JSON.{topic} must be a positive integer"
                ) from exc
            if thread_id <= 0:
                raise RuntimeError(
                    f"MVIDEO_TELEGRAM_TOPICS_JSON.{topic} must be a positive integer"
                )
            topics[topic] = thread_id
    else:
        topics = dict(fallback.telegram_topics or {}) if fallback else {}

    return MVideoConfig(
        name=(os.getenv("MVIDEO_ACCOUNT_NAME") or "М.Видео").strip(),
        slug=(os.getenv("MVIDEO_ACCOUNT_SLUG") or "mvideo").strip(),
        api_key=api_key,
        telegram_chat_id=chat_id,
        telegram_topics=topics or None,
    )


def load_settings() -> Settings:
    load_dotenv()
    app_mode = os.getenv("APP_MODE", "full").strip().lower()
    if app_mode not in {"full", "relay"}:
        raise RuntimeError("APP_MODE must be 'full' or 'relay'")

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
    telegram_relay_url = os.getenv("TELEGRAM_RELAY_URL") or None
    telegram_relay_secret = os.getenv("TELEGRAM_RELAY_SECRET") or None
    if telegram_relay_url and not telegram_relay_secret:
        raise RuntimeError("TELEGRAM_RELAY_SECRET is required when TELEGRAM_RELAY_URL is set")
    news_token = (os.getenv("NEWS_TELEGRAM_BOT_TOKEN") or "").strip() or None
    news_chat_id = _telegram_chat_id_env("NEWS_TELEGRAM_CHAT_ID")
    if news_token and not news_chat_id:
        raise RuntimeError(
            "NEWS_TELEGRAM_CHAT_ID is required when NEWS_TELEGRAM_BOT_TOKEN is set"
        )
    accounts = _load_accounts(required=app_mode == "full")
    mvideo_base_url = os.getenv(
        "MVIDEO_BASE_URL",
        "https://api.sellers.mvideo.ru",
    ).rstrip("/")
    parsed_mvideo_url = urlsplit(mvideo_base_url)
    if (
        parsed_mvideo_url.scheme != "https"
        or parsed_mvideo_url.hostname != "api.sellers.mvideo.ru"
        or parsed_mvideo_url.port not in {None, 443}
        or parsed_mvideo_url.username
        or parsed_mvideo_url.password
        or parsed_mvideo_url.path not in {"", "/"}
        or parsed_mvideo_url.query
        or parsed_mvideo_url.fragment
    ):
        raise RuntimeError(
            "MVIDEO_BASE_URL must be the official HTTPS Seller API host"
        )

    return Settings(
        app_env=os.getenv("APP_ENV", "development"),
        app_mode=app_mode,
        port=_int_env("PORT", 8080),
        bind_host=os.getenv("BIND_HOST", "0.0.0.0").strip(),
        database_path=os.getenv("DATABASE_PATH", "data/ozon_notify.sqlite3"),
        public_base_url=os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8080"),
        webhook_secret=os.getenv("WEBHOOK_SECRET", "dev-secret"),
        telegram_bot_token=token,
        telegram_news_bot_token=news_token,
        telegram_default_chat_id=_telegram_chat_id_env("TELEGRAM_DEFAULT_CHAT_ID"),
        telegram_news_chat_id=news_chat_id,
        telegram_channel_invite_link=os.getenv("TELEGRAM_CHANNEL_INVITE_LINK") or None,
        telegram_proxy_url=os.getenv("TELEGRAM_PROXY_URL") or None,
        telegram_relay_url=telegram_relay_url,
        telegram_relay_secret=telegram_relay_secret,
        ozon_base_url=os.getenv("OZON_BASE_URL", "https://api-seller.ozon.ru"),
        accounts=accounts,
        mvideo_base_url=mvideo_base_url,
        mvideo_account=_load_mvideo_account(accounts),
        mvideo_poll_seconds=_positive_int_env("MVIDEO_POLL_SECONDS", 60),
        mvideo_lookback_hours=_positive_int_env(
            "MVIDEO_LOOKBACK_HOURS",
            24 * 30,
        ),
        important_poll_seconds=_int_env("OZON_POLL_IMPORTANT_SECONDS", 60),
        secondary_poll_seconds=_int_env("OZON_POLL_SECONDARY_SECONDS", 300),
        news_poll_seconds=_int_env("NEWS_POLL_SECONDS", 900),
        news_notifications_enabled=_bool_env(
            "NEWS_NOTIFICATIONS_ENABLED",
            False,
        ),
        bootstrap_send_existing=_bool_env("BOOTSTRAP_SEND_EXISTING", False),
        bootstrap_lookback_hours=_int_env("BOOTSTRAP_LOOKBACK_HOURS", 72),
    )


def mask_secret(value: str, keep: int = 4) -> str:
    if len(value) <= keep * 2:
        return "***"
    return f"{value[:keep]}...{value[-keep:]}"
