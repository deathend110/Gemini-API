from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys
import time
from typing import Any, Callable
from uuid import uuid4


SELENIUM_GEMINI_URL = "https://gemini.google.com/app"
DEFAULT_BROWSER_SOURCE = "selenium-chrome-profile"


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


def build_chrome_launch_args(
    user_data_dir: str | Path,
    *,
    headless: bool = False,
) -> list[str]:
    args = [
        f"--user-data-dir={Path(user_data_dir)}",
        "--profile-directory=Default",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-popup-blocking",
    ]
    if headless:
        args.append("--headless=new")
    return args


def _is_google_cookie_domain(domain: str) -> bool:
    normalized = domain.strip().lower()
    return (
        normalized == "google.com"
        or normalized.endswith(".google.com")
        or normalized == "gemini.google.com"
        or normalized.endswith(".gemini.google.com")
    )


def collect_google_cookies(items: list[dict[str, Any]]) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for item in items:
        name = item.get("name")
        value = item.get("value")
        domain = item.get("domain")
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        if not value:
            continue
        if not isinstance(domain, str) or not _is_google_cookie_domain(domain):
            continue
        cookies[name] = value
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


def _create_chrome_driver(
    profile_dir: str | Path,
    *,
    headless: bool = False,
    browser_binary: str | None = None,
    driver_factory: Callable[..., Any] | None = None,
) -> Any:
    if driver_factory is not None:
        return driver_factory(options=None)

    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options as ChromeOptions
    except ImportError as exc:
        raise BrowserCookieRefreshError(
            "selenium is not installed. Run: uv sync --extra browser"
        ) from exc

    options = ChromeOptions()
    for arg in build_chrome_launch_args(profile_dir, headless=headless):
        options.add_argument(arg)
    if browser_binary:
        options.binary_location = browser_binary

    try:
        return webdriver.Chrome(options=options)
    except Exception as exc:
        raise BrowserCookieRefreshError(
            f"Failed to start Chrome via Selenium: {exc}"
        ) from exc


def _wait_for_gemini_cookies(
    driver: Any,
    *,
    login_wait_seconds: int,
    poll_interval_seconds: int,
) -> dict[str, str]:
    deadline = time.monotonic() + max(login_wait_seconds, 0)
    while True:
        cookies = collect_google_cookies(driver.get_cookies())
        if "__Secure-1PSID" in cookies:
            return cookies

        if time.monotonic() >= deadline:
            break

        if poll_interval_seconds > 0:
            time.sleep(poll_interval_seconds)

    raise BrowserCookieRefreshError(
        "No Gemini cookies found in dedicated Chrome profile. "
        "Please sign in once in the opened browser profile and rerun."
    )


def load_browser_cookies_with_selenium(
    *,
    profile_dir: str | Path,
    url: str = SELENIUM_GEMINI_URL,
    headless: bool = False,
    login_wait_seconds: int = 300,
    poll_interval_seconds: int = 2,
    page_load_timeout_seconds: int = 60,
    browser_binary: str | None = None,
    driver_factory: Callable[..., Any] | None = None,
    verbose: bool = False,
) -> BrowserCookieSelection:
    driver = _create_chrome_driver(
        profile_dir,
        headless=headless,
        browser_binary=browser_binary,
        driver_factory=driver_factory,
    )

    try:
        try:
            driver.set_page_load_timeout(page_load_timeout_seconds)
        except Exception:
            pass

        if verbose:
            print(f"Opening Gemini in dedicated Chrome profile: {Path(profile_dir)}")

        driver.get(url)
        cookies = _wait_for_gemini_cookies(
            driver,
            login_wait_seconds=login_wait_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        return BrowserCookieSelection(
            source=DEFAULT_BROWSER_SOURCE,
            cookies=cookies,
        )
    except BrowserCookieRefreshError:
        raise
    except Exception as exc:
        raise BrowserCookieRefreshError(
            f"Failed to refresh browser cookies via Selenium: {exc}"
        ) from exc
    finally:
        quit_driver = getattr(driver, "quit", None)
        if callable(quit_driver):
            try:
                quit_driver()
            except Exception:
                pass


def refresh_browser_cookies_to_file(
    cookies_path: str | Path,
    *,
    profile_dir: str | Path,
    url: str = SELENIUM_GEMINI_URL,
    headless: bool = False,
    login_wait_seconds: int = 300,
    poll_interval_seconds: int = 2,
    page_load_timeout_seconds: int = 60,
    browser_binary: str | None = None,
    verbose: bool = False,
    print_summary: bool = True,
) -> BrowserCookieSelection:
    path = Path(cookies_path)
    selection = load_browser_cookies_with_selenium(
        profile_dir=profile_dir,
        url=url,
        headless=headless,
        login_wait_seconds=login_wait_seconds,
        poll_interval_seconds=poll_interval_seconds,
        page_load_timeout_seconds=page_load_timeout_seconds,
        browser_binary=browser_binary,
        verbose=verbose,
    )
    payload = {
        "cookies": dict(sorted(selection.cookies.items())),
        "updated_at": int(time.time()),
        "source": selection.source,
        "profile_dir": str(Path(profile_dir)),
        "url": url,
    }
    _atomic_write_json(path, payload)
    if print_summary:
        print(f"Browser cookies refreshed: {selection.summary()}")
    return selection


def main(argv: list[str] | None = None) -> int:
    from gateway.config import GatewaySettings

    parser = argparse.ArgumentParser(
        description="Refresh Gemini gateway cookies from a dedicated Selenium Chrome profile.",
    )
    parser.add_argument("--cookies-path", default=None)
    parser.add_argument("--profile-dir", default=None)
    parser.add_argument("--url", default=SELENIUM_GEMINI_URL)
    parser.add_argument("--login-wait-seconds", type=int, default=None)
    parser.add_argument("--poll-interval-seconds", type=int, default=None)
    parser.add_argument("--page-load-timeout-seconds", type=int, default=None)
    parser.add_argument("--browser-binary", default=None)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    settings = GatewaySettings()
    cookies_path = args.cookies_path or settings.cookies_json_path
    profile_dir = args.profile_dir or settings.browser_profile_dir
    login_wait_seconds = (
        args.login_wait_seconds
        if args.login_wait_seconds is not None
        else settings.browser_login_wait_seconds
    )
    poll_interval_seconds = (
        args.poll_interval_seconds
        if args.poll_interval_seconds is not None
        else settings.browser_poll_interval_seconds
    )
    page_load_timeout_seconds = (
        args.page_load_timeout_seconds
        if args.page_load_timeout_seconds is not None
        else settings.browser_page_load_timeout_seconds
    )
    headless = args.headless or settings.browser_headless

    try:
        refresh_browser_cookies_to_file(
            cookies_path,
            profile_dir=profile_dir,
            url=args.url,
            headless=headless,
            login_wait_seconds=login_wait_seconds,
            poll_interval_seconds=poll_interval_seconds,
            page_load_timeout_seconds=page_load_timeout_seconds,
            browser_binary=args.browser_binary,
            verbose=args.verbose,
        )
    except BrowserCookieRefreshError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
