import asyncio
import json
import os
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
from gateway import service as gateway_service_module
from gateway.service import GatewayService

RecoverableAPIError = gateway_service_module.APIError
RecoverableAuthError = gateway_service_module.AuthError
RecoverableGeminiError = gateway_service_module.GeminiError
RecoverableTimeoutError = gateway_service_module.TimeoutError


class FakeCookie:
    def __init__(self, name: str, value: str) -> None:
        self.name = name
        self.value = value
        self.domain = ".google.com"
        self.path = "/"
        self.expires = None

    def is_expired(self) -> bool:
        return False


class FakeCookies:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = dict(values)

    @property
    def jar(self) -> list[FakeCookie]:
        return [FakeCookie(name, value) for name, value in self._values.items()]

    def to_dict(self) -> dict[str, str]:
        return dict(self._values)


class FakeGeminiClient:
    def __init__(
        self,
        *,
        text_result: str = "shared reply",
        stream_chunks: list[str] | None = None,
        init_error: Exception | None = None,
        generate_error: Exception | None = None,
        stream_error: Exception | None = None,
        stream_error_after_chunks: int | None = None,
        cookie_overrides: dict[str, str] | None = None,
        inspect_snapshot: dict[str, object] | None = None,
        account_status_name: str = "AVAILABLE",
        account_status_code: int = 1000,
    ) -> None:
        self.init_calls: list[dict[str, object]] = []
        self.close_calls = 0
        self.generate_calls: list[dict[str, object]] = []
        self.stream_calls: list[dict[str, object]] = []
        self.text_result = text_result
        self.stream_chunks = stream_chunks or ["shared ", "stream"]
        self.init_error = init_error
        self.generate_error = generate_error
        self.stream_error = stream_error
        self.stream_error_after_chunks = stream_error_after_chunks
        self.cookies = FakeCookies(
            {
                "__Secure-1PSID": "psid-value",
                "__Secure-1PSIDTS": "psidts-value",
                "NID": "nid-value",
                **(cookie_overrides or {}),
            }
        )
        self.inspect_snapshot = inspect_snapshot or {
            "summary": {"deep_research_feature_present": True, "rejected_probes": []}
        }
        self.account_status = SimpleNamespace(
            name=account_status_name,
            value=account_status_code,
        )
        self._model_registry = {
            "basic": SimpleNamespace(advanced_only=False, is_available=True),
            "advanced": SimpleNamespace(
                advanced_only=account_status_name == "AVAILABLE",
                is_available=account_status_name == "AVAILABLE",
            ),
        }

    async def init(self, **kwargs) -> None:
        self.init_calls.append(kwargs)
        if self.init_error is not None:
            error = self.init_error
            self.init_error = None
            raise error

    async def inspect_account_status(self) -> dict[str, object]:
        return self.inspect_snapshot

    async def close(self) -> None:
        self.close_calls += 1

    async def generate_content(self, **kwargs):
        self.generate_calls.append(kwargs)
        if self.generate_error is not None:
            error = self.generate_error
            self.generate_error = None
            raise error
        return SimpleNamespace(text=self.text_result)

    async def generate_content_stream(self, **kwargs):
        self.stream_calls.append(kwargs)
        if self.stream_error is not None:
            error = self.stream_error
            self.stream_error = None
            raise error
        for index, chunk in enumerate(self.stream_chunks, start=1):
            yield chunk
            if (
                self.stream_error_after_chunks is not None
                and index >= self.stream_error_after_chunks
            ):
                raise RecoverableAPIError("stream interrupted")


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
            model="gemini-3-flash",
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

    async def test_shutdown_persists_updated_cookies_to_json(self) -> None:
        service = GatewayService(self.settings)
        fake_client = FakeGeminiClient(
            cookie_overrides={
                "__Secure-1PSIDTS": "new-psidts-value",
                "SIDCC": "sidcc-value",
            }
        )
        service._build_client_from_cached_cookies = Mock(return_value=fake_client)

        await service.warmup()
        await service.shutdown()

        persisted = json.loads(self.cookies_path.read_text(encoding="utf-8"))
        self.assertEqual(
            persisted["cookies"]["__Secure-1PSIDTS"],
            "new-psidts-value",
        )
        self.assertEqual(persisted["cookies"]["SIDCC"], "sidcc-value")
        self.assertEqual(
            service.get_cached_cookies()["__Secure-1PSIDTS"],
            "new-psidts-value",
        )

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

    async def test_start_cookie_persist_task_persists_changed_runtime_cookies(
        self,
    ) -> None:
        settings = GatewaySettings(
            api_key="test-key",
            proxy="http://127.0.0.1:7890",
            cookies_json_path=str(self.cookies_path),
            cookie_persist_interval_seconds=1,
        )
        service = GatewayService(settings)
        fake_client = FakeGeminiClient()
        service._shared_client = fake_client
        service._is_warmed_up = True

        persist_mock = Mock(wraps=service.persist_cookies)
        service.persist_cookies = persist_mock

        await service.start_cookie_persist_task()
        fake_client.cookies = FakeCookies(
            {
                "__Secure-1PSID": "psid-value",
                "__Secure-1PSIDTS": "rotated-psidts",
            }
        )
        await asyncio.sleep(1.2)
        await service.stop_cookie_persist_task()

        self.assertGreaterEqual(persist_mock.call_count, 1)
        self.assertEqual(
            service.get_cached_cookies()["__Secure-1PSIDTS"],
            "rotated-psidts",
        )

    async def test_stop_cookie_persist_task_is_idempotent(self) -> None:
        service = GatewayService(self.settings)

        await service.stop_cookie_persist_task()
        await service.start_cookie_persist_task()
        await service.stop_cookie_persist_task()
        await service.stop_cookie_persist_task()

        self.assertIsNone(service._cookie_persist_task)

    async def test_warmup_builds_account_snapshot_from_probe_results(self) -> None:
        service = GatewayService(self.settings)
        fake_client = FakeGeminiClient(
            inspect_snapshot={
                "summary": {
                    "deep_research_feature_present": False,
                    "rejected_probes": ["caps"],
                }
            },
            account_status_name="UNAUTHENTICATED",
            account_status_code=1016,
        )
        service._build_client_from_cached_cookies = Mock(return_value=fake_client)

        await service.warmup()

        snapshot = service.get_account_snapshot()
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.mode, "degraded")
        self.assertEqual(snapshot.raw_account_status, "UNAUTHENTICATED")
        self.assertEqual(snapshot.raw_account_status_code, 1016)
        self.assertTrue(snapshot.chat_available)
        self.assertFalse(snapshot.deep_research_available)
        self.assertFalse(snapshot.full_web_capability_available)

    async def test_warmup_strict_mode_raises_when_required_level_missing(self) -> None:
        strict_settings = GatewaySettings(
            api_key="test-key",
            proxy="http://127.0.0.1:7890",
            cookies_json_path=str(self.cookies_path),
            account_strict_mode=True,
            account_required_level="full_web",
        )
        service = GatewayService(strict_settings)
        fake_client = FakeGeminiClient(
            inspect_snapshot={
                "summary": {
                    "deep_research_feature_present": False,
                    "rejected_probes": ["caps"],
                }
            },
            account_status_name="UNAUTHENTICATED",
            account_status_code=1016,
        )
        service._build_client_from_cached_cookies = Mock(return_value=fake_client)

        with self.assertRaisesRegex(ValueError, "full_web"):
            await service.warmup()

        self.assertEqual(fake_client.close_calls, 1)
        self.assertFalse(service._is_warmed_up)
        self.assertIsNone(service.get_account_snapshot())

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

    async def test_generate_text_rebuilds_shared_client_after_timeout(self) -> None:
        service = GatewayService(self.settings)
        first_client = FakeGeminiClient(generate_error=RecoverableTimeoutError("timeout"))
        second_client = FakeGeminiClient(text_result="recovered reply")
        service._build_client_from_cached_cookies = Mock(
            side_effect=[first_client, second_client]
        )
        request = self.make_request()

        text = await service.generate_text(
            prompt="hello",
            upstream_model="gemini-3-flash",
            request=request,
        )

        self.assertEqual(text, "recovered reply")
        self.assertEqual(first_client.close_calls, 1)
        self.assertEqual(len(second_client.init_calls), 1)
        self.assertEqual(len(first_client.generate_calls), 1)
        self.assertEqual(len(second_client.generate_calls), 1)

    async def test_auth_failure_does_not_refresh_browser_cookies_when_disabled(
        self,
    ) -> None:
        service = GatewayService(self.settings)
        first_client = FakeGeminiClient(generate_error=RecoverableAuthError("auth failed"))
        second_client = FakeGeminiClient(text_result="recovered reply")
        service._build_client_from_cached_cookies = Mock(
            side_effect=[first_client, second_client]
        )
        service.refresh_cookies_from_browser = Mock(return_value=False)
        request = self.make_request()

        text = await service.generate_text(
            prompt="hello",
            upstream_model="gemini-3-flash",
            request=request,
        )

        self.assertEqual(text, "recovered reply")
        service.refresh_cookies_from_browser.assert_not_called()

    async def test_auth_failure_refreshes_browser_cookies_once_when_enabled(
        self,
    ) -> None:
        settings = GatewaySettings(
            api_key="test-key",
            proxy="http://127.0.0.1:7890",
            cookies_json_path=str(self.cookies_path),
            browser_cookie_refresh_enabled=True,
            browser_cookie_refresh_on_auth_error=True,
        )
        service = GatewayService(settings)
        first_client = FakeGeminiClient(generate_error=RecoverableAuthError("auth failed"))
        second_client = FakeGeminiClient(text_result="recovered reply")
        service._build_client_from_cached_cookies = Mock(
            side_effect=[first_client, second_client]
        )
        service.refresh_cookies_from_browser = Mock(return_value=True)
        request = self.make_request()

        text = await service.generate_text(
            prompt="hello",
            upstream_model="gemini-3-flash",
            request=request,
        )

        self.assertEqual(text, "recovered reply")
        service.refresh_cookies_from_browser.assert_called_once_with()
        self.assertEqual(service._build_client_from_cached_cookies.call_count, 2)

    async def test_rebuild_can_preserve_browser_refreshed_auth_cookies(self) -> None:
        service = GatewayService(self.settings)
        service._cached_cookies = {
            "__Secure-1PSID": "browser-psid",
            "__Secure-1PSIDTS": "browser-fresh-psidts",
        }
        failed_client = FakeGeminiClient(
            cookie_overrides={
                "__Secure-1PSID": "stale-psid",
                "__Secure-1PSIDTS": "stale-psidts",
            }
        )
        service._shared_client = failed_client
        service._shared_client_generation = 1

        class RecordingGeminiClient:
            def __init__(
                self,
                *,
                secure_1psid: str,
                secure_1psidts: str,
                proxy: str,
            ) -> None:
                self.secure_1psid = secure_1psid
                self.secure_1psidts = secure_1psidts
                self.proxy = proxy
                self.cookies: dict[str, str] | None = None
                self.account_status = SimpleNamespace(name="AVAILABLE", value=1000)
                self._model_registry = {
                    "advanced": SimpleNamespace(
                        advanced_only=True,
                        is_available=True,
                    )
                }

            async def init(self, **kwargs) -> None:
                self.init_calls = [kwargs]

            async def inspect_account_status(self) -> dict[str, object]:
                return {
                    "summary": {
                        "deep_research_feature_present": True,
                        "rejected_probes": [],
                    }
                }

            async def close(self) -> None:
                self.close_calls = getattr(self, "close_calls", 0) + 1

        active_gemini_module = sys.modules["gemini_webapi"]
        with patch.object(
            active_gemini_module,
            "GeminiClient",
            RecordingGeminiClient,
        ):
            rebuilt_client, _ = await service._rebuild_shared_client_after_failure(
                failed_client=failed_client,
                failed_generation=1,
                sync_failed_client_cookies=False,
            )

        self.assertEqual(rebuilt_client.secure_1psid, "browser-psid")
        self.assertEqual(rebuilt_client.secure_1psidts, "browser-fresh-psidts")

    async def test_auth_failure_browser_refresh_failure_returns_original_error(
        self,
    ) -> None:
        settings = GatewaySettings(
            api_key="test-key",
            proxy="http://127.0.0.1:7890",
            cookies_json_path=str(self.cookies_path),
            browser_cookie_refresh_enabled=True,
            browser_cookie_refresh_on_auth_error=True,
        )
        service = GatewayService(settings)
        first_client = FakeGeminiClient(generate_error=RecoverableAuthError("auth failed"))
        service._build_client_from_cached_cookies = Mock(return_value=first_client)
        service.refresh_cookies_from_browser = Mock(return_value=False)
        request = self.make_request()

        with self.assertRaises(RecoverableAuthError):
            await service.generate_text(
                prompt="hello",
                upstream_model="gemini-3-flash",
                request=request,
            )

        service.refresh_cookies_from_browser.assert_called_once_with()
        self.assertEqual(service._build_client_from_cached_cookies.call_count, 1)

    async def test_generate_stream_rebuilds_shared_client_after_initial_failure(
        self,
    ) -> None:
        service = GatewayService(self.settings)
        first_client = FakeGeminiClient(stream_error=RecoverableAPIError("stream failed"))
        second_client = FakeGeminiClient(stream_chunks=["re", "covered"])
        service._build_client_from_cached_cookies = Mock(
            side_effect=[first_client, second_client]
        )
        request = self.make_request()

        chunks = [
            chunk
            async for chunk in service.generate_stream(
                prompt="hello",
                upstream_model="gemini-3-flash",
                request=request,
            )
        ]

        self.assertEqual(chunks, ["re", "covered"])
        self.assertEqual(first_client.close_calls, 1)
        self.assertEqual(len(second_client.init_calls), 1)
        self.assertEqual(len(first_client.stream_calls), 1)
        self.assertEqual(len(second_client.stream_calls), 1)

    async def test_flush_runtime_cookies_drops_stale_cached_cookie_values(self) -> None:
        service = GatewayService(self.settings)
        service._cached_cookies = {
            "__Secure-1PSID": "psid-value",
            "__Secure-1PSIDTS": "stale-psidts-value",
            "NID": "stale-nid-value",
        }
        service._shared_client = FakeGeminiClient(
            cookie_overrides={
                "__Secure-1PSIDTS": "",
                "NID": "",
            }
        )
        service._shared_client.cookies = FakeCookies(
            {
                "__Secure-1PSID": "psid-value",
            }
        )

        changed = await service.flush_runtime_cookies()

        self.assertTrue(changed)
        cached = service.get_cached_cookies()
        self.assertEqual(cached["__Secure-1PSID"], "psid-value")
        self.assertNotIn("__Secure-1PSIDTS", cached)
        self.assertNotIn("NID", cached)
        persisted = json.loads(self.cookies_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["cookies"], {"__Secure-1PSID": "psid-value"})

    async def test_generate_text_raises_when_rebuilt_client_fails_again(self) -> None:
        service = GatewayService(self.settings)
        first_client = FakeGeminiClient(generate_error=RecoverableGeminiError("first"))
        second_client = FakeGeminiClient(generate_error=RecoverableAPIError("second"))
        service._build_client_from_cached_cookies = Mock(
            side_effect=[first_client, second_client]
        )
        request = self.make_request()

        with self.assertRaises(RecoverableAPIError):
            await service.generate_text(
                prompt="hello",
                upstream_model="gemini-3-flash",
                request=request,
            )

        self.assertEqual(first_client.close_calls, 1)
        self.assertEqual(len(first_client.generate_calls), 1)
        self.assertEqual(len(second_client.generate_calls), 1)

    async def test_generate_text_closes_rebuilt_client_when_reinit_fails(self) -> None:
        service = GatewayService(self.settings)
        first_client = FakeGeminiClient(generate_error=RecoverableTimeoutError("timeout"))
        second_client = FakeGeminiClient(init_error=RecoverableAPIError("init failed"))
        service._build_client_from_cached_cookies = Mock(
            side_effect=[first_client, second_client]
        )
        request = self.make_request()

        with self.assertRaises(RecoverableAPIError):
            await service.generate_text(
                prompt="hello",
                upstream_model="gemini-3-flash",
                request=request,
            )

        self.assertEqual(first_client.close_calls, 1)
        self.assertEqual(second_client.close_calls, 1)

    async def test_rebuild_keeps_old_client_alive_until_last_holder_releases(self) -> None:
        service = GatewayService(self.settings)
        first_client = FakeGeminiClient()
        second_client = FakeGeminiClient(text_result="recovered reply")
        service._build_client_from_cached_cookies = Mock(
            side_effect=[first_client, second_client]
        )

        held_client, held_generation = await service._acquire_shared_client()
        rebuilt_client, rebuilt_generation = await service._rebuild_shared_client_after_failure(
            failed_client=held_client,
            failed_generation=held_generation,
        )

        self.assertIs(rebuilt_client, second_client)
        self.assertIs(service._shared_client, second_client)
        self.assertEqual(first_client.close_calls, 0)

        await service._release_shared_client(rebuilt_generation)
        self.assertEqual(first_client.close_calls, 0)

        await service._release_shared_client(held_generation)
        self.assertEqual(first_client.close_calls, 1)

    async def test_rebuild_shared_client_uses_latest_cookies_from_failed_client(
        self,
    ) -> None:
        service = GatewayService(self.settings)
        service._cached_cookies = {
            "__Secure-1PSID": "psid-value",
            "__Secure-1PSIDTS": "stale-psidts-value",
            "NID": "stale-nid-value",
        }
        failed_client = FakeGeminiClient(
            cookie_overrides={
                "__Secure-1PSIDTS": "fresh-psidts-value",
                "NID": "fresh-nid-value",
                "SIDCC": "sidcc-value",
            }
        )
        service._shared_client = failed_client
        service._shared_client_generation = 1

        class RecordingGeminiClient:
            def __init__(
                self,
                *,
                secure_1psid: str,
                secure_1psidts: str,
                proxy: str,
            ) -> None:
                self.secure_1psid = secure_1psid
                self.secure_1psidts = secure_1psidts
                self.proxy = proxy
                self.cookies: dict[str, str] | None = None
                self.init_calls: list[dict[str, object]] = []
                self.close_calls = 0
                self.account_status = SimpleNamespace(name="AVAILABLE", value=1000)
                self._model_registry = {
                    "advanced": SimpleNamespace(
                        advanced_only=True,
                        is_available=True,
                    )
                }

            async def init(self, **kwargs) -> None:
                self.init_calls.append(kwargs)

            async def inspect_account_status(self) -> dict[str, object]:
                return {
                    "summary": {
                        "deep_research_feature_present": True,
                        "rejected_probes": [],
                    }
                }

            async def close(self) -> None:
                self.close_calls += 1

        active_gemini_module = sys.modules["gemini_webapi"]
        with patch.object(
            active_gemini_module,
            "GeminiClient",
            RecordingGeminiClient,
        ):
            rebuilt_client, rebuilt_generation = await service._rebuild_shared_client_after_failure(
                failed_client=failed_client,
                failed_generation=1,
            )

        self.assertEqual(rebuilt_client.secure_1psid, "psid-value")
        self.assertEqual(rebuilt_client.secure_1psidts, "fresh-psidts-value")
        self.assertIsNone(rebuilt_client.cookies)
        self.assertEqual(
            service.get_cached_cookies()["__Secure-1PSIDTS"],
            "fresh-psidts-value",
        )
        self.assertEqual(service.get_cached_cookies()["NID"], "fresh-nid-value")
        self.assertEqual(service.get_cached_cookies()["SIDCC"], "sidcc-value")
        self.assertEqual(rebuilt_generation, 2)
        self.assertEqual(failed_client.close_calls, 1)

    def test_build_client_uses_only_auth_cookies_to_avoid_domain_conflicts(
        self,
    ) -> None:
        service = GatewayService(self.settings)
        service._cached_cookies = {
            "__Secure-1PSID": "psid-value",
            "__Secure-1PSIDTS": "psidts-value",
            "AEC": "aec-value",
            "NID": "nid-value",
        }

        class RecordingGeminiClient:
            def __init__(
                self,
                *,
                secure_1psid: str,
                secure_1psidts: str,
                proxy: str,
            ) -> None:
                self.secure_1psid = secure_1psid
                self.secure_1psidts = secure_1psidts
                self.proxy = proxy
                self.cookies: dict[str, str] | None = None

        active_gemini_module = sys.modules["gemini_webapi"]
        with patch.object(
            active_gemini_module,
            "GeminiClient",
            RecordingGeminiClient,
        ):
            client = service._build_client_from_cached_cookies()

        self.assertEqual(client.secure_1psid, "psid-value")
        self.assertEqual(client.secure_1psidts, "psidts-value")
        self.assertIsNone(client.cookies)

    async def test_generate_text_rebuild_does_not_close_client_held_by_other_request(
        self,
    ) -> None:
        service = GatewayService(self.settings)
        first_client = FakeGeminiClient()
        second_client = FakeGeminiClient(text_result="recovered reply")
        service._build_client_from_cached_cookies = Mock(
            side_effect=[first_client, second_client]
        )
        held_client, held_generation = await service._acquire_shared_client()
        held_client.generate_error = RecoverableTimeoutError("timeout")
        request = self.make_request()

        text = await service.generate_text(
            prompt="hello",
            upstream_model="gemini-3-flash",
            request=request,
        )

        self.assertEqual(text, "recovered reply")
        self.assertEqual(first_client.close_calls, 0)

        await service._release_shared_client(held_generation)
        self.assertEqual(first_client.close_calls, 1)

    async def test_shutdown_keeps_retired_client_alive_until_held_request_releases(
        self,
    ) -> None:
        service = GatewayService(self.settings)
        first_client = FakeGeminiClient()
        second_client = FakeGeminiClient(text_result="recovered reply")
        service._build_client_from_cached_cookies = Mock(
            side_effect=[first_client, second_client]
        )
        held_client, held_generation = await service._acquire_shared_client()
        rebuilt_client, rebuilt_generation = await service._rebuild_shared_client_after_failure(
            failed_client=held_client,
            failed_generation=held_generation,
        )

        self.assertIs(rebuilt_client, second_client)

        await service._release_shared_client(rebuilt_generation)
        await service.shutdown()

        self.assertEqual(first_client.close_calls, 0)
        self.assertEqual(second_client.close_calls, 1)

        await service._release_shared_client(held_generation)
        self.assertEqual(first_client.close_calls, 1)

    async def test_generate_stream_does_not_rebuild_after_partial_output(self) -> None:
        service = GatewayService(self.settings)
        first_client = FakeGeminiClient(
            stream_chunks=["partial"],
            stream_error_after_chunks=1,
        )
        second_client = FakeGeminiClient(stream_chunks=["recovered"])
        service._build_client_from_cached_cookies = Mock(
            side_effect=[first_client, second_client]
        )
        request = self.make_request()
        chunks: list[str] = []

        with self.assertRaises(RecoverableAPIError):
            async for chunk in service.generate_stream(
                prompt="hello",
                upstream_model="gemini-3-flash",
                request=request,
            ):
                chunks.append(chunk)

        self.assertEqual(chunks, ["partial"])
        self.assertEqual(first_client.close_calls, 0)
        self.assertEqual(second_client.init_calls, [])

    def test_persist_cookies_replaces_target_file_atomically(self) -> None:
        service = GatewayService(self.settings)
        service.get_cached_cookies()
        write_paths: list[Path] = []
        replace_calls: list[tuple[Path, Path]] = []
        original_write_text = Path.write_text
        original_replace = Path.replace

        def tracking_write_text(path: Path, data: str, *args, **kwargs) -> int:
            write_paths.append(Path(path))
            return original_write_text(path, data, *args, **kwargs)

        def tracking_replace(path: Path, target: Path, *args, **kwargs) -> Path:
            replace_calls.append((Path(path), Path(target)))
            return original_replace(path, target, *args, **kwargs)

        with patch.object(Path, "write_text", new=tracking_write_text), patch.object(
            Path,
            "replace",
            new=tracking_replace,
        ):
            service.persist_cookies(
                FakeCookies(
                    {
                        "__Secure-1PSID": "psid-value",
                        "__Secure-1PSIDTS": "atomic-psidts-value",
                        "SIDCC": "sidcc-value",
                    }
                ),
                force=True,
            )

        self.assertEqual(len(replace_calls), 1)
        temp_path, target_path = replace_calls[0]
        self.assertEqual(target_path, self.cookies_path)
        self.assertNotEqual(temp_path, self.cookies_path)
        self.assertNotIn(self.cookies_path, write_paths)
        self.assertEqual(write_paths, [temp_path])

        persisted = json.loads(self.cookies_path.read_text(encoding="utf-8"))
        self.assertEqual(
            persisted["cookies"]["__Secure-1PSIDTS"],
            "atomic-psidts-value",
        )
        self.assertEqual(persisted["cookies"]["SIDCC"], "sidcc-value")

    def test_load_cookies_prefers_newer_upstream_cookie_cache(self) -> None:
        service = GatewayService(self.settings)
        upstream_cache_dir = Path(self.temp_dir.name) / "gemini-cache"
        upstream_cache_dir.mkdir(parents=True, exist_ok=True)
        upstream_cache_path = upstream_cache_dir / ".cached_cookies_psid-value.json"
        upstream_cache_path.write_text(
            json.dumps(
                [
                    {
                        "name": "__Secure-1PSID",
                        "value": "psid-value",
                        "domain": ".google.com",
                        "path": "/",
                        "expires": None,
                    },
                    {
                        "name": "__Secure-1PSIDTS",
                        "value": "new-upstream-psidts",
                        "domain": ".google.com",
                        "path": "/",
                        "expires": None,
                    },
                ]
            ),
            encoding="utf-8",
        )
        os.utime(upstream_cache_path, (self.cookies_path.stat().st_mtime + 10,) * 2)

        with patch.dict(os.environ, {"GEMINI_COOKIE_PATH": str(upstream_cache_dir)}):
            cookies = service.load_cookies()

        self.assertEqual(cookies["__Secure-1PSID"], "psid-value")
        self.assertEqual(cookies["__Secure-1PSIDTS"], "new-upstream-psidts")


if __name__ == "__main__":
    unittest.main()
