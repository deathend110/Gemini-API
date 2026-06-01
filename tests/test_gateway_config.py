import unittest
from unittest.mock import patch

from gateway.config import GatewaySettings


class TestGatewaySettings(unittest.TestCase):
    def test_uses_proxy_fallback_when_env_missing(self):
        with patch.dict("os.environ", {}, clear=True):
            settings = GatewaySettings()

        self.assertEqual(settings.proxy, "http://127.0.0.1:10090/")
        self.assertTrue(settings.api_key)

    def test_prefers_env_proxy_and_custom_api_key(self):
        with patch.dict(
            "os.environ",
            {
                "GEMINI_GATEWAY_API_KEY": "local-key",
                "HTTPS_PROXY": "http://127.0.0.1:7890/",
            },
            clear=True,
        ):
            settings = GatewaySettings()

        self.assertEqual(settings.api_key, "local-key")
        self.assertEqual(settings.proxy, "http://127.0.0.1:7890/")

    def test_browser_selenium_settings_from_env(self):
        with patch.dict(
            "os.environ",
            {
                "GEMINI_GATEWAY_BROWSER_PROFILE_DIR": r"C:\gateway\profile",
                "GEMINI_GATEWAY_BROWSER_LOGIN_WAIT_SECONDS": "120",
                "GEMINI_GATEWAY_BROWSER_POLL_INTERVAL_SECONDS": "3",
                "GEMINI_GATEWAY_BROWSER_PAGE_LOAD_TIMEOUT_SECONDS": "30",
                "GEMINI_GATEWAY_BROWSER_HEADLESS": "true",
            },
            clear=True,
        ):
            settings = GatewaySettings()

        self.assertEqual(settings.browser_profile_dir, r"C:\gateway\profile")
        self.assertEqual(settings.browser_login_wait_seconds, 120)
        self.assertEqual(settings.browser_poll_interval_seconds, 3)
        self.assertEqual(settings.browser_page_load_timeout_seconds, 30)
        self.assertTrue(settings.browser_headless)


if __name__ == "__main__":
    unittest.main()
