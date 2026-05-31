from __future__ import annotations

from dataclasses import dataclass, field
import os
import secrets

DEFAULT_PROXY = "http://127.0.0.1:10090/"


def _get_env(name: str, default: str) -> str:
    return os.getenv(name, default)


def _get_env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


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


@dataclass
class GatewaySettings:
    host: str = field(default_factory=lambda: _get_env("GEMINI_GATEWAY_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _get_env_int("GEMINI_GATEWAY_PORT", 8000))
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
    proxy: str = ""
    api_key: str = ""

    def __post_init__(self) -> None:
        self.proxy = _resolve_proxy(self.proxy)
        self.api_key = _resolve_api_key(self.api_key)
