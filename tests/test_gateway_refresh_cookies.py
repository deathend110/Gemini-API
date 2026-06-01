import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from gateway.refresh_cookies import (
    BrowserCookieRefreshError,
    BrowserCookieSelection,
    build_chrome_launch_args,
    collect_google_cookies,
    load_browser_cookies_with_selenium,
    refresh_browser_cookies_to_file,
)
from gateway.config import GatewaySettings
from gateway.service import GatewayService


class TestGatewayRefreshCookies(unittest.TestCase):
    def test_build_chrome_launch_args_includes_dedicated_profile(self) -> None:
        args = build_chrome_launch_args(
            Path(r"C:\Users\27355\.gemini-api\selenium-profile"),
            headless=True,
        )

        self.assertIn(
            "--user-data-dir=C:\\Users\\27355\\.gemini-api\\selenium-profile",
            args,
        )
        self.assertIn("--profile-directory=Default", args)
        self.assertIn("--headless=new", args)
        self.assertIn("--no-first-run", args)

    def test_collect_google_cookies_filters_non_google_domains(self) -> None:
        cookies = collect_google_cookies(
            [
                {
                    "name": "__Secure-1PSID",
                    "value": "psid",
                    "domain": ".google.com",
                },
                {
                    "name": "__Secure-1PSIDTS",
                    "value": "psidts",
                    "domain": "accounts.google.com",
                },
                {
                    "name": "AEC",
                    "value": "aec",
                    "domain": ".google.com.hk",
                },
                {
                    "name": "NID",
                    "value": "nid",
                    "domain": ".example.com",
                },
            ]
        )

        self.assertEqual(cookies["__Secure-1PSID"], "psid")
        self.assertEqual(cookies["__Secure-1PSIDTS"], "psidts")
        self.assertNotIn("AEC", cookies)
        self.assertNotIn("NID", cookies)

    def test_load_browser_cookies_with_selenium_waits_for_login(self) -> None:
        class FakeDriver:
            def __init__(self) -> None:
                self.calls = 0
                self.urls: list[str] = []
                self.closed = False

            def get(self, url: str) -> None:
                self.urls.append(url)

            def get_cookies(self) -> list[dict[str, str]]:
                self.calls += 1
                if self.calls == 1:
                    return [
                        {
                            "name": "NID",
                            "value": "nid",
                            "domain": ".google.com",
                        }
                    ]
                return [
                    {
                        "name": "__Secure-1PSID",
                        "value": "psid",
                        "domain": ".google.com",
                    },
                    {
                        "name": "__Secure-1PSIDTS",
                        "value": "psidts",
                        "domain": ".google.com",
                    },
                ]

            def quit(self) -> None:
                self.closed = True

        driver = FakeDriver()
        selection = load_browser_cookies_with_selenium(
            profile_dir=Path(tempfile.gettempdir()) / "selenium-profile-test",
            driver_factory=lambda **kwargs: driver,
            login_wait_seconds=1,
            poll_interval_seconds=0,
            page_load_timeout_seconds=1,
            headless=False,
            verbose=False,
        )

        self.assertEqual(driver.urls, ["https://gemini.google.com/app"])
        self.assertTrue(driver.closed)
        self.assertEqual(selection.source, "selenium-chrome-profile")
        self.assertEqual(selection.cookies["__Secure-1PSID"], "psid")
        self.assertEqual(selection.cookies["__Secure-1PSIDTS"], "psidts")

    def test_refresh_browser_cookies_writes_gateway_compatible_json_without_leaking_values(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cookies_path = Path(temp_dir) / "cookies.json"
            stdout = StringIO()

            with patch(
                "gateway.refresh_cookies.load_browser_cookies_with_selenium",
                return_value=BrowserCookieSelection(
                    source="selenium-chrome-profile",
                    cookies={
                        "__Secure-1PSID": "secret-psid",
                        "__Secure-1PSIDTS": "secret-psidts",
                        "NID": "secret-nid",
                    },
                ),
            ), redirect_stdout(stdout):
                result = refresh_browser_cookies_to_file(
                    cookies_path,
                    profile_dir=Path(temp_dir) / "profile",
                )

            payload = json.loads(cookies_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["source"], "selenium-chrome-profile")
            self.assertEqual(payload["cookies"]["__Secure-1PSID"], "secret-psid")
            self.assertEqual(
                payload["cookies"]["__Secure-1PSIDTS"],
                "secret-psidts",
            )
            self.assertEqual(result.source, "selenium-chrome-profile")

            output = stdout.getvalue()
            self.assertIn("Browser cookies refreshed:", output)
            self.assertIn("source=selenium-chrome-profile", output)
            self.assertIn("has_1psid=true", output)
            self.assertNotIn("secret-psid", output)
            self.assertNotIn("secret-psidts", output)

            service = GatewayService(
                GatewaySettings(
                    api_key="test-key",
                    cookies_json_path=str(cookies_path),
                    proxy="http://127.0.0.1:7890",
                )
            )
            self.assertEqual(service.load_cookies()["__Secure-1PSID"], "secret-psid")

    def test_refresh_browser_cookies_does_not_overwrite_existing_file_when_invalid(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cookies_path = Path(temp_dir) / "cookies.json"
            cookies_path.write_text(
                '{"cookies":{"__Secure-1PSID":"old-psid"}}',
                encoding="utf-8",
            )

            with patch(
                "gateway.refresh_cookies.load_browser_cookies_with_selenium",
                side_effect=BrowserCookieRefreshError(
                    "No Gemini cookies found in dedicated Chrome profile."
                ),
            ):
                with self.assertRaises(BrowserCookieRefreshError):
                    refresh_browser_cookies_to_file(
                        cookies_path,
                        profile_dir=Path(temp_dir) / "profile",
                    )

            self.assertEqual(
                json.loads(cookies_path.read_text(encoding="utf-8"))["cookies"][
                    "__Secure-1PSID"
                ],
                "old-psid",
            )

    def test_load_browser_cookies_with_selenium_errors_when_cookie_never_appears(
        self,
    ) -> None:
        class FakeDriver:
            def __init__(self) -> None:
                self.closed = False

            def get(self, url: str) -> None:
                self.url = url

            def get_cookies(self) -> list[dict[str, str]]:
                return [
                    {
                        "name": "NID",
                        "value": "nid",
                        "domain": ".google.com",
                    }
                ]

            def quit(self) -> None:
                self.closed = True

        driver = FakeDriver()
        with self.assertRaisesRegex(
            BrowserCookieRefreshError,
            "No Gemini cookies found in dedicated Chrome profile",
        ):
            load_browser_cookies_with_selenium(
                profile_dir=Path(tempfile.gettempdir()) / "selenium-profile-test",
                driver_factory=lambda **kwargs: driver,
                login_wait_seconds=0,
                poll_interval_seconds=0,
                page_load_timeout_seconds=1,
                headless=False,
                verbose=False,
            )

        self.assertTrue(driver.closed)


if __name__ == "__main__":
    unittest.main()
