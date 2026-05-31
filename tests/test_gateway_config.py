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


if __name__ == "__main__":
    unittest.main()
