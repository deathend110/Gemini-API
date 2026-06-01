from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys
import time
from typing import Any
from uuid import uuid4


BROWSER_PRIORITY = {
    "edge": 0,
    "chrome": 1,
    "brave": 2,
    "chromium": 3,
    "firefox": 4,
    "vivaldi": 5,
    "opera": 6,
    "opera_gx": 7,
    "librewolf": 8,
    "safari": 9,
}


class BrowserCookieRefreshError(Exception):
    pass


@dataclass(frozen=True)
class BrowserCookieSelection:
    source: str
    cookies: dict[str, str]

    @property
    def has_1psid(self) -> bool:
        return "__Secure-1PSID" in self.cookies

    @property
    def has_1psidts(self) -> bool:
        return "__Secure-1PSIDTS" in self.cookies

    def summary(self) -> str:
        return (
            f"source={self.source}, "
            f"has_1psid={str(self.has_1psid).lower()}, "
            f"has_1psidts={str(self.has_1psidts).lower()}, "
            f"count={len(self.cookies)}"
        )


def _collect_browser_cookie_diagnostics(domain_name: str) -> list[str]:
    try:
        import browser_cookie3 as bc3
    except ImportError:
        return []

    browser_names = [
        "edge",
        "chrome",
        "chromium",
        "brave",
        "vivaldi",
        "opera",
        "opera_gx",
        "firefox",
        "librewolf",
        "safari",
    ]
    diagnostics: list[str] = []
    for browser_name in browser_names:
        cookie_fn = getattr(bc3, browser_name, None)
        if cookie_fn is None:
            continue
        try:
            jar = cookie_fn(domain_name=domain_name)
            count = len(list(jar))
            diagnostics.append(f"{browser_name}=ok:{count}")
        except Exception as exc:
            diagnostics.append(f"{browser_name}={type(exc).__name__}: {exc}")
    return diagnostics


def _cookies_from_items(items: list[dict[str, Any]]) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for item in items:
        name = item.get("name")
        value = item.get("value")
        domain = item.get("domain")
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        if not value:
            continue
        if (
            isinstance(domain, str)
            and "google.com" not in domain
            and "gemini.google.com" not in domain
        ):
            continue
        cookies[name] = value
    return cookies


def choose_cookie_source(
    browser_cookies: dict[str, list[dict[str, Any]]],
    requested_source: str | None = None,
) -> BrowserCookieSelection:
    candidates: list[BrowserCookieSelection] = []
    for source, items in browser_cookies.items():
        if requested_source and source != requested_source:
            continue
        cookies = _cookies_from_items(items)
        if "__Secure-1PSID" in cookies:
            candidates.append(BrowserCookieSelection(source=source, cookies=cookies))

    if not candidates:
        source_text = (
            f" for browser source {requested_source}" if requested_source else ""
        )
        raise BrowserCookieRefreshError(
            "No valid Gemini browser cookies found"
            f"{source_text}. Please log in to https://gemini.google.com in your browser first."
        )

    return sorted(
        candidates,
        key=lambda candidate: (
            "__Secure-1PSIDTS" not in candidate.cookies,
            -len(candidate.cookies),
            BROWSER_PRIORITY.get(candidate.source, 100),
            candidate.source,
        ),
    )[0]


def load_browser_cookies_from_domain(
    domain_name: str,
    verbose: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    try:
        from gemini_webapi.utils.load_browser_cookies import HAS_BC3
        from gemini_webapi.utils import load_browser_cookies
    except ImportError as exc:
        raise BrowserCookieRefreshError(
            "browser-cookie3 is not installed. Run: uv sync --extra browser"
        ) from exc

    if not HAS_BC3:
        raise BrowserCookieRefreshError(
            "browser-cookie3 is not installed. Run: uv sync --extra browser"
        )

    cookies = load_browser_cookies(domain_name=domain_name, verbose=verbose)
    if not cookies:
        diagnostics = _collect_browser_cookie_diagnostics(domain_name)
        diagnostics_suffix = ""
        if diagnostics:
            diagnostics_suffix = " Browser diagnostics: " + "; ".join(diagnostics)
        raise BrowserCookieRefreshError(
            "No browser cookies were found. Please log in to https://gemini.google.com in your browser first."
            + diagnostics_suffix
        )
    return cookies


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


def refresh_browser_cookies_to_file(
    cookies_path: str | Path,
    *,
    browser_source: str | None = None,
    domain: str = ".google.com",
    verbose: bool = False,
    print_summary: bool = True,
) -> BrowserCookieSelection:
    path = Path(cookies_path)
    browser_cookies = load_browser_cookies_from_domain(domain, verbose=verbose)
    selection = choose_cookie_source(
        browser_cookies,
        requested_source=browser_source,
    )
    payload = {
        "cookies": dict(sorted(selection.cookies.items())),
        "updated_at": int(time.time()),
        "source": selection.source,
    }
    _atomic_write_json(path, payload)
    if print_summary:
        print(f"Browser cookies refreshed: {selection.summary()}")
    return selection


def main(argv: list[str] | None = None) -> int:
    from gateway.config import GatewaySettings

    parser = argparse.ArgumentParser(
        description="Refresh Gemini gateway cookies from a logged-in browser."
    )
    parser.add_argument("--cookies-path", default=None)
    parser.add_argument("--browser-source", default=None)
    parser.add_argument("--domain", default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    settings = GatewaySettings()
    cookies_path = args.cookies_path or settings.cookies_json_path
    browser_source = args.browser_source or getattr(
        settings,
        "browser_cookie_source",
        "",
    ) or None
    domain = args.domain or getattr(settings, "browser_cookie_domain", ".google.com")

    try:
        refresh_browser_cookies_to_file(
            cookies_path,
            browser_source=browser_source,
            domain=domain,
            verbose=args.verbose,
        )
    except BrowserCookieRefreshError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
