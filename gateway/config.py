from __future__ import annotations

from dataclasses import dataclass, field
import os
import secrets

from gateway.account import ACCOUNT_REQUIRED_LEVELS

DEFAULT_PROXY = "http://127.0.0.1:10090/"


def _get_env(name: str, default: str) -> str:
    return os.getenv(name, default)


def _get_env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _get_env_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value for {name}: {raw_value}")


def _resolve_proxy(proxy: str) -> str:
    if proxy:
        return proxy

    return (
        os.getenv("GEMINI_GATEWAY_PROXY")
        or os.getenv("HTTPS_PROXY")
        or os.getenv("https_proxy")
        or os.getenv("HTTP_PROXY")
        or os.getenv("http_proxy")
        or os.getenv("ALL_PROXY")
        or os.getenv("all_proxy")
        or DEFAULT_PROXY
    )


def _resolve_api_key(api_key: str) -> str:
    if api_key:
        return api_key

    return os.getenv("GEMINI_GATEWAY_API_KEY") or secrets.token_urlsafe(24)


def _normalize_account_required_level(required_level: str) -> str:
    normalized = required_level.strip().lower()
    if normalized not in ACCOUNT_REQUIRED_LEVELS:
        raise ValueError(
            "Invalid GEMINI_GATEWAY_ACCOUNT_REQUIRED_LEVEL: "
            f"{required_level}"
        )
    return normalized


@dataclass
class GatewaySettings:
    host: str = field(default_factory=lambda: _get_env("GEMINI_GATEWAY_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _get_env_int("GEMINI_GATEWAY_PORT", 8010))
    cookies_json_path: str = field(
        default_factory=lambda: _get_env(
            "GEMINI_GATEWAY_COOKIES_JSON_PATH",
            "cookies.json",
        )
    )
    default_model: str = field(
        default_factory=lambda: _get_env(
            "GEMINI_GATEWAY_DEFAULT_MODEL",
            "gemini-3.5-flash",
        )
    )
    default_reasoning_effort: str = field(
        default_factory=lambda: _get_env(
            "GEMINI_GATEWAY_DEFAULT_REASONING_EFFORT",
            "standard",
        )
    )
    request_timeout: int = field(
        default_factory=lambda: _get_env_int("GEMINI_GATEWAY_REQUEST_TIMEOUT", 300)
    )
    cookie_persist_enabled: bool = field(
        default_factory=lambda: _get_env_bool(
            "GEMINI_GATEWAY_COOKIE_PERSIST_ENABLED",
            True,
        )
    )
    cookie_persist_interval_seconds: int = field(
        default_factory=lambda: _get_env_int(
            "GEMINI_GATEWAY_COOKIE_PERSIST_INTERVAL_SECONDS",
            60,
        )
    )
    account_probe_enabled: bool = field(
        default_factory=lambda: _get_env_bool(
            "GEMINI_GATEWAY_ACCOUNT_PROBE_ENABLED",
            True,
        )
    )
    account_strict_mode: bool = field(
        default_factory=lambda: _get_env_bool(
            "GEMINI_GATEWAY_ACCOUNT_STRICT_MODE",
            False,
        )
    )
    account_required_level: str = field(
        default_factory=lambda: _get_env(
            "GEMINI_GATEWAY_ACCOUNT_REQUIRED_LEVEL",
            "basic",
        )
    )
    proxy: str = ""
    api_key: str = ""

    def __post_init__(self) -> None:
        self.proxy = _resolve_proxy(self.proxy)
        self.api_key = _resolve_api_key(self.api_key)
        self.account_required_level = _normalize_account_required_level(
            self.account_required_level
        )
