import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

gemini_stub = types.ModuleType("gemini_webapi")
gemini_stub.GeminiClient = object
exceptions_stub = types.ModuleType("gemini_webapi.exceptions")


class StubAPIError(Exception):
    pass


class StubAuthError(Exception):
    pass


class StubGeminiError(Exception):
    pass


class StubTimeoutError(Exception):
    pass


exceptions_stub.APIError = StubAPIError
exceptions_stub.AuthError = StubAuthError
exceptions_stub.GeminiError = StubGeminiError
exceptions_stub.TimeoutError = StubTimeoutError

sys.modules.setdefault("gemini_webapi", gemini_stub)
sys.modules.setdefault("gemini_webapi.exceptions", exceptions_stub)

from gateway.config import GatewaySettings
from gateway.schemas import ChatCompletionRequest, ChatMessage
from gateway.service import GatewayService


class FakeGeminiClient:
    def __init__(self) -> None:
        self.init_calls: list[dict[str, object]] = []
        self.close_calls = 0
        self.generate_calls: list[dict[str, object]] = []
        self.stream_calls: list[dict[str, object]] = []

    async def init(self, **kwargs) -> None:
        self.init_calls.append(kwargs)

    async def close(self) -> None:
        self.close_calls += 1

    async def generate_content(self, **kwargs):
        self.generate_calls.append(kwargs)
        return SimpleNamespace(text="shared reply")

    async def generate_content_stream(self, **kwargs):
        self.stream_calls.append(kwargs)
        yield "shared "
        yield "stream"


class TestGatewayServiceLifecycle(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cookies_path = Path(self.temp_dir.name) / "cookies.json"
        self.cookies_path.write_text(
            json.dumps(
                {
                    "__Secure-1PSID": "psid-value",
                    "__Secure-1PSIDTS": "psidts-value",
                    "NID": "nid-value",
                }
            ),
            encoding="utf-8",
        )
        self.settings = GatewaySettings(
            api_key="test-key",
            proxy="http://127.0.0.1:7890",
            cookies_json_path=str(self.cookies_path),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def make_request(self) -> ChatCompletionRequest:
        return ChatCompletionRequest(
            model="gemini-3.5-flash",
            messages=[ChatMessage(role="user", content="hello")],
        )

    async def test_warmup_builds_and_initializes_shared_client_once(self) -> None:
        service = GatewayService(self.settings)
        fake_client = FakeGeminiClient()
        service._build_client_from_cached_cookies = Mock(return_value=fake_client)

        await service.warmup()
        shared_client = await service.get_shared_client()
        await service.warmup()

        self.assertIs(shared_client, fake_client)
        service._build_client_from_cached_cookies.assert_called_once_with()
        self.assertEqual(
            fake_client.init_calls,
            [
                {
                    "timeout": self.settings.request_timeout,
                    "auto_refresh": True,
                    "auto_close": False,
                }
            ],
        )

    async def test_shutdown_closes_shared_client_and_resets_warmup_state(self) -> None:
        service = GatewayService(self.settings)
        first_client = FakeGeminiClient()
        second_client = FakeGeminiClient()
        service._build_client_from_cached_cookies = Mock(
            side_effect=[first_client, second_client]
        )

        await service.warmup()
        await service.shutdown()
        rebuilt_client = await service.get_shared_client()

        self.assertEqual(first_client.close_calls, 1)
        self.assertIs(rebuilt_client, second_client)
        self.assertEqual(service._build_client_from_cached_cookies.call_count, 2)

    async def test_get_cached_cookies_does_not_reload_file_after_warmup(self) -> None:
        service = GatewayService(self.settings)
        service._build_client_from_cached_cookies = Mock(return_value=FakeGeminiClient())

        with patch.object(service, "load_cookies", wraps=service.load_cookies) as load_mock:
            await service.warmup()
            first = service.get_cached_cookies()
            second = service.get_cached_cookies()

        self.assertEqual(load_mock.call_count, 1)
        self.assertEqual(first, second)
        self.assertEqual(first["__Secure-1PSID"], "psid-value")

    async def test_generate_methods_reuse_shared_client(self) -> None:
        service = GatewayService(self.settings)
        fake_client = FakeGeminiClient()
        service._build_client_from_cached_cookies = Mock(return_value=fake_client)
        request = self.make_request()

        text = await service.generate_text(
            prompt="hello",
            upstream_model="gemini-3-flash",
            request=request,
        )
        chunks = [
            chunk
            async for chunk in service.generate_stream(
                prompt="hello",
                upstream_model="gemini-3-flash",
                request=request,
            )
        ]

        self.assertEqual(text, "shared reply")
        self.assertEqual(chunks, ["shared ", "stream"])
        service._build_client_from_cached_cookies.assert_called_once_with()
        self.assertEqual(len(fake_client.init_calls), 1)
        self.assertEqual(fake_client.close_calls, 0)
        self.assertEqual(len(fake_client.generate_calls), 1)
        self.assertEqual(len(fake_client.stream_calls), 1)


if __name__ == "__main__":
    unittest.main()
