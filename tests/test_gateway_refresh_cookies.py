import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from gateway.config import GatewaySettings
from gateway.refresh_cookies import (
    BrowserCookieRefreshError,
    choose_cookie_source,
    load_browser_cookies_from_domain,
    refresh_browser_cookies_to_file,
)
from gateway.service import GatewayService


class TestGatewayRefreshCookies(unittest.TestCase):
    def test_choose_cookie_source_prefers_psid_and_psidts(self) -> None:
        browser_cookies = {
            "chrome": [
                {
                    "name": "__Secure-1PSID",
                    "value": "chrome-psid",
                    "domain": ".google.com",
                },
            ],
            "edge": [
                {
                    "name": "__Secure-1PSID",
                    "value": "edge-psid",
                    "domain": ".google.com",
                },
                {
                    "name": "__Secure-1PSIDTS",
                    "value": "edge-psidts",
                    "domain": ".google.com",
                },
            ],
        }

        selected = choose_cookie_source(browser_cookies)

        self.assertEqual(selected.source, "edge")
        self.assertEqual(selected.cookies["__Secure-1PSID"], "edge-psid")
        self.assertEqual(selected.cookies["__Secure-1PSIDTS"], "edge-psidts")

    def test_choose_cookie_source_accepts_psid_without_psidts(self) -> None:
        selected = choose_cookie_source(
            {
                "chrome": [
                    {
                        "name": "__Secure-1PSID",
                        "value": "chrome-psid",
                        "domain": ".google.com",
                    },
                    {"name": "NID", "value": "nid-value", "domain": ".google.com"},
                ]
            }
        )

        self.assertEqual(selected.source, "chrome")
        self.assertTrue(selected.has_1psid)
        self.assertFalse(selected.has_1psidts)

    def test_choose_cookie_source_honors_requested_source(self) -> None:
        selected = choose_cookie_source(
            {
                "chrome": [
                    {
                        "name": "__Secure-1PSID",
                        "value": "chrome-psid",
                        "domain": ".google.com",
                    },
                ],
                "edge": [
                    {
                        "name": "__Secure-1PSID",
                        "value": "edge-psid",
                        "domain": ".google.com",
                    },
                    {
                        "name": "__Secure-1PSIDTS",
                        "value": "edge-psidts",
                        "domain": ".google.com",
                    },
                ],
            },
            requested_source="chrome",
        )

        self.assertEqual(selected.source, "chrome")
        self.assertEqual(selected.cookies["__Secure-1PSID"], "chrome-psid")

    def test_choose_cookie_source_raises_without_psid(self) -> None:
        with self.assertRaisesRegex(
            BrowserCookieRefreshError,
            "No valid Gemini browser cookies",
        ):
            choose_cookie_source(
                {
                    "edge": [
                        {"name": "NID", "value": "nid", "domain": ".google.com"}
                    ]
                }
            )

    def test_refresh_browser_cookies_writes_gateway_compatible_json_without_leaking_values(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cookies_path = Path(temp_dir) / "cookies.json"
            stdout = StringIO()

            with patch(
                "gateway.refresh_cookies.load_browser_cookies_from_domain",
                return_value={
                    "edge": [
                        {
                            "name": "__Secure-1PSID",
                            "value": "secret-psid",
                            "domain": ".google.com",
                        },
                        {
                            "name": "__Secure-1PSIDTS",
                            "value": "secret-psidts",
                            "domain": ".google.com",
                        },
                        {
                            "name": "NID",
                            "value": "secret-nid",
                            "domain": ".google.com",
                        },
                    ]
                },
            ), redirect_stdout(stdout):
                result = refresh_browser_cookies_to_file(cookies_path)

            payload = json.loads(cookies_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["source"], "edge")
            self.assertEqual(payload["cookies"]["__Secure-1PSID"], "secret-psid")
            self.assertEqual(
                payload["cookies"]["__Secure-1PSIDTS"],
                "secret-psidts",
            )
            self.assertEqual(result.source, "edge")

            output = stdout.getvalue()
            self.assertIn("Browser cookies refreshed:", output)
            self.assertIn("source=edge", output)
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
                "gateway.refresh_cookies.load_browser_cookies_from_domain",
                return_value={
                    "edge": [
                        {"name": "NID", "value": "nid", "domain": ".google.com"}
                    ]
                },
            ):
                with self.assertRaises(BrowserCookieRefreshError):
                    refresh_browser_cookies_to_file(cookies_path)

            self.assertEqual(
                json.loads(cookies_path.read_text(encoding="utf-8"))["cookies"][
                    "__Secure-1PSID"
                ],
                "old-psid",
            )

    def test_load_browser_cookies_from_domain_surfaces_browser_errors(self) -> None:
        fake_module = type(
            "FakeModule",
            (),
            {
                "HAS_BC3": True,
                "load_browser_cookies": staticmethod(lambda **kwargs: {}),
            },
        )

        with patch.dict(
            "sys.modules",
            {
                "gemini_webapi.utils.load_browser_cookies": fake_module,
                "gemini_webapi.utils": fake_module,
            },
        ):
            with self.assertRaisesRegex(
                BrowserCookieRefreshError,
                "No browser cookies were found",
            ):
                load_browser_cookies_from_domain(".google.com", verbose=False)

    def test_refresh_browser_cookies_reports_failure_details_in_verbose_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cookies_path = Path(temp_dir) / "cookies.json"
            stderr = StringIO()

            with (
                patch(
                    "gateway.refresh_cookies.load_browser_cookies_from_domain",
                    side_effect=BrowserCookieRefreshError(
                        "No browser cookies were found. Browser diagnostics: edge=RequiresAdminError: denied; chrome=RequiresAdminError: denied"
                    ),
                ),
                redirect_stdout(StringIO()),
                patch("sys.stderr", stderr),
            ):
                from gateway.refresh_cookies import main

                exit_code = main(
                    ["--cookies-path", str(cookies_path), "--domain", ".google.com"]
                )

            self.assertEqual(exit_code, 1)
            self.assertIn("Browser diagnostics:", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
