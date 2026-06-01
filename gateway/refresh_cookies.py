from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys
import time
from typing import Any, Callable
from uuid import uuid4

from curl_cffi.requests import Session


MANUAL_GEMINI_URL = "https://gemini.google.com/app"
DEFAULT_BROWSER_SOURCE = "manual-chrome-profile-cdp"


class BrowserCookieRefreshError(Exception):
    def __init__(
        self,
        message: str,
        *,
        manual_login_required: bool = False,
        debugging_session_required: bool = False,
        profile_in_use: bool = False,
    ) -> None:
        super().__init__(message)
        self.manual_login_required = manual_login_required
        self.debugging_session_required = debugging_session_required
        self.profile_in_use = profile_in_use


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


@dataclass(frozen=True)
class DevToolsEndpoint:
    port: int
    browser_websocket_url: str
    version_url: str


def build_manual_chrome_launch_command(
    profile_dir: str | Path,
    url: str = MANUAL_GEMINI_URL,
) -> str:
    resolved_profile_dir = str(Path(profile_dir))
    return (
        '$Chrome = "${env:ProgramFiles}\\Google\\Chrome\\Application\\chrome.exe"; '
        'if (-not (Test-Path $Chrome)) { '
        '$Chrome = "${env:ProgramFiles(x86)}\\Google\\Chrome\\Application\\chrome.exe" '
        '}; '
        f'& $Chrome --user-data-dir="{resolved_profile_dir}" '
        '--profile-directory="Default" '
        '--remote-debugging-port=0 '
        f'"{url}"'
    )


def load_devtools_endpoint_from_profile(profile_dir: str | Path) -> DevToolsEndpoint:
    active_port_file = Path(profile_dir) / "DevToolsActivePort"
    if not active_port_file.is_file():
        raise BrowserCookieRefreshError(
            "未检测到专用 Chrome profile 的远程调试会话，请先按指引手动启动带 remote debugging 的 Chrome。",
            manual_login_required=True,
            debugging_session_required=True,
        )

    try:
        lines = [
            line.strip()
            for line in active_port_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if len(lines) < 2:
            raise ValueError("missing port or websocket path")

        port = int(lines[0])
        browser_path = lines[1]
        if not browser_path.startswith("/"):
            raise ValueError("invalid websocket path")
    except ValueError as exc:
        raise BrowserCookieRefreshError(
            "专用 Chrome profile 的远程调试会话信息无效，请重新按指引启动带 remote debugging 的 Chrome。",
            manual_login_required=True,
            debugging_session_required=True,
        ) from exc

    return DevToolsEndpoint(
        port=port,
        browser_websocket_url=f"ws://127.0.0.1:{port}{browser_path}",
        version_url=f"http://127.0.0.1:{port}/json/version",
    )


def _is_google_cookie_domain(domain: str) -> bool:
    normalized = domain.strip().lower()
    return normalized == "google.com" or normalized.endswith(".google.com")


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


def _select_google_auth_cookies(cookies: dict[str, str]) -> dict[str, str]:
    selected: dict[str, str] = {}
    for name in ("__Secure-1PSID", "__Secure-1PSIDTS"):
        value = cookies.get(name)
        if isinstance(value, str) and value:
            selected[name] = value
    return selected


def _profile_cookie_file(profile_dir: str | Path) -> Path:
    return Path(profile_dir) / "Default" / "Network" / "Cookies"


def _profile_local_state_file(profile_dir: str | Path) -> Path:
    return Path(profile_dir) / "Local State"


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


def _default_browser_loader() -> Callable[..., Any]:
    try:
        import browser_cookie3 as bc3
    except ImportError as exc:
        raise BrowserCookieRefreshError(
            "browser-cookie3 is not installed. Run: uv sync --extra browser"
        ) from exc

    return bc3.chrome


def _default_devtools_session_factory() -> Session:
    return Session()


def _normalize_browser_cookie_item(cookie: Any) -> dict[str, str] | None:
    name = getattr(cookie, "name", None)
    value = getattr(cookie, "value", None)
    domain = getattr(cookie, "domain", None)
    if not isinstance(name, str) or not isinstance(value, str) or not isinstance(domain, str):
        return None

    is_expired = getattr(cookie, "is_expired", None)
    if callable(is_expired) and is_expired():
        return None

    return {
        "name": name,
        "value": value,
        "domain": domain,
    }


def load_browser_cookies_from_profile(
    *,
    profile_dir: str | Path,
    domain_name: str = ".google.com",
    browser_loader: Callable[..., Any] | None = None,
) -> BrowserCookieSelection:
    cookie_file = _profile_cookie_file(profile_dir)
    key_file = _profile_local_state_file(profile_dir)
    if not cookie_file.is_file() or not key_file.is_file():
        raise BrowserCookieRefreshError(
            "未检测到专用 Chrome profile 中的有效 Gemini 登录态。",
            manual_login_required=True,
        )

    loader = browser_loader or _default_browser_loader()

    try:
        jar = loader(
            cookie_file=str(cookie_file),
            domain_name=domain_name,
            key_file=str(key_file),
        )
    except PermissionError as exc:
        raise BrowserCookieRefreshError(
            "专用 Chrome profile 当前仍在运行，无法读取 Cookies 数据库。"
            "请先关闭该 profile 的 Chrome 窗口后再重新执行。",
            profile_in_use=True,
        ) from exc
    except BrowserCookieRefreshError:
        raise
    except Exception as exc:
        raise BrowserCookieRefreshError(
            f"Failed to load dedicated Chrome profile cookies: {exc}"
        ) from exc

    normalized_items = []
    for cookie in jar:
        item = _normalize_browser_cookie_item(cookie)
        if item is not None:
            normalized_items.append(item)

    cookies = collect_google_cookies(normalized_items)
    if "__Secure-1PSID" not in cookies:
        raise BrowserCookieRefreshError(
            "未检测到专用 Chrome profile 中的有效 Gemini 登录态。",
            manual_login_required=True,
        )

    return BrowserCookieSelection(
        source=DEFAULT_BROWSER_SOURCE,
        cookies=cookies,
    )


def load_browser_cookies_via_cdp(
    *,
    endpoint: DevToolsEndpoint,
    session_factory: Callable[[], Any] | None = None,
) -> BrowserCookieSelection:
    request_id = 1
    session = (
        session_factory() if session_factory is not None else _default_devtools_session_factory()
    )
    websocket = None
    try:
        websocket = session.ws_connect(endpoint.browser_websocket_url)
        websocket.send_json({"id": request_id, "method": "Storage.getCookies"})
        while True:
            response = websocket.recv_json()
            if not isinstance(response, dict):
                continue
            if response.get("id") != request_id:
                continue
            break
    except BrowserCookieRefreshError:
        raise
    except Exception as exc:
        raise BrowserCookieRefreshError(
            f"Failed to query Chrome DevTools cookies: {exc}"
        ) from exc
    finally:
        if websocket is not None:
            websocket.close()
        close = getattr(session, "close", None)
        if callable(close):
            close()

    if "error" in response:
        error = response.get("error")
        message = "Chrome DevTools cookie 查询失败。"
        if isinstance(error, dict):
            error_message = error.get("message")
            if isinstance(error_message, str) and error_message:
                message = f"Chrome DevTools cookie 查询失败: {error_message}"
        raise BrowserCookieRefreshError(message)

    result = response.get("result") if isinstance(response, dict) else None
    cookie_items = result.get("cookies") if isinstance(result, dict) else None
    if not isinstance(cookie_items, list):
        raise BrowserCookieRefreshError("Chrome DevTools 返回的 cookies 结构无效。")

    cookies = _select_google_auth_cookies(collect_google_cookies(cookie_items))
    if "__Secure-1PSID" not in cookies:
        raise BrowserCookieRefreshError(
            "已连接专用 Chrome，但未检测到有效 Gemini 登录态。",
            manual_login_required=True,
        )

    return BrowserCookieSelection(
        source=DEFAULT_BROWSER_SOURCE,
        cookies=cookies,
    )


def print_manual_login_guidance(
    *,
    profile_dir: str | Path,
    url: str = MANUAL_GEMINI_URL,
    debugging_session_required: bool = False,
) -> None:
    if debugging_session_required:
        print("未检测到专用 Chrome profile 的 remote debugging 会话。")
    else:
        print("已连接专用 Chrome，但未检测到有效 Gemini 登录态。")
    print(
        "Google 可能会阻止由自动化框架控制的 Chrome 登录账号，因此请先手动启动专用 profile 并完成 Gemini 登录。"
    )
    print("")
    print(build_manual_chrome_launch_command(profile_dir, url=url))
    print("")
    print("请复制上面的 PowerShell 命令并手动运行。")
    print("在打开的专用 Chrome 中完成 Gemini 登录后，不要关闭窗口，再重新执行 refresh_cookies：")
    print("uv run --extra browser python -m gateway.refresh_cookies")


def print_profile_in_use_guidance(*, profile_dir: str | Path) -> None:
    print("专用 Chrome profile 当前仍在运行，暂时无法直接读取 Cookies 数据库。")
    print(f"请先关闭该 profile 的 Chrome 窗口：{Path(profile_dir)}")
    print("关闭后重新执行：")
    print("uv run --extra browser python -m gateway.refresh_cookies")


def refresh_browser_cookies_to_file(
    cookies_path: str | Path,
    *,
    profile_dir: str | Path,
    url: str = MANUAL_GEMINI_URL,
    headless: bool = False,
    login_wait_seconds: int = 300,
    poll_interval_seconds: int = 2,
    page_load_timeout_seconds: int = 60,
    browser_binary: str | None = None,
    verbose: bool = False,
    print_summary: bool = True,
) -> BrowserCookieSelection:
    del headless
    del login_wait_seconds
    del poll_interval_seconds
    del page_load_timeout_seconds
    del browser_binary
    del verbose

    path = Path(cookies_path)
    endpoint = load_devtools_endpoint_from_profile(profile_dir)
    selection = load_browser_cookies_via_cdp(endpoint=endpoint)
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
        description=(
            "Refresh Gemini gateway cookies from a manually signed-in dedicated "
            "Chrome profile."
        ),
    )
    parser.add_argument("--cookies-path", default=None)
    parser.add_argument("--profile-dir", default=None)
    parser.add_argument("--url", default=MANUAL_GEMINI_URL)
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
        if exc.manual_login_required:
            print_manual_login_guidance(
                profile_dir=profile_dir,
                url=args.url,
                debugging_session_required=exc.debugging_session_required,
            )
        elif exc.profile_in_use:
            print_profile_in_use_guidance(profile_dir=profile_dir)
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
