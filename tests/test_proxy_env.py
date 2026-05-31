import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from gemini_webapi import GeminiClient  # noqa: E402


class TestGeminiClientProxyEnv(unittest.TestCase):
    def test_uses_proxy_from_environment_when_not_provided(self):
        with patch.dict(
            os.environ,
            {"HTTPS_PROXY": "http://127.0.0.1:10090/"},
            clear=True,
        ):
            client = GeminiClient()

        self.assertEqual(client.proxy, "http://127.0.0.1:10090/")

    def test_explicit_proxy_overrides_environment(self):
        with patch.dict(
            os.environ,
            {"HTTPS_PROXY": "http://127.0.0.1:10090/"},
            clear=True,
        ):
            client = GeminiClient(proxy="http://127.0.0.1:7890/")

        self.assertEqual(client.proxy, "http://127.0.0.1:7890/")


if __name__ == "__main__":
    unittest.main()
