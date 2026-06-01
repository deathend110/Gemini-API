import contextlib
import io
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

gemini_stub = types.ModuleType("gemini_webapi")
gemini_stub.GeminiClient = object

exceptions_stub = types.ModuleType("gemini_webapi.exceptions")


class _AuthError(Exception):
    pass


class _APIError(Exception):
    pass


class _GeminiError(Exception):
    pass


class _TimeoutError(_GeminiError):
    pass


exceptions_stub.APIError = _APIError
exceptions_stub.AuthError = _AuthError
exceptions_stub.GeminiError = _GeminiError
exceptions_stub.TimeoutError = _TimeoutError

sys.modules.setdefault("gemini_webapi", gemini_stub)
sys.modules.setdefault("gemini_webapi.exceptions", exceptions_stub)

from gateway.config import GatewaySettings
from gateway.main import create_app, main
from gateway.schemas import ChatMessage, ChatToolCall, ChatToolFunction
from gemini_webapi.exceptions import TimeoutError

class TestGatewayApi(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = GatewaySettings(api_key="test-key")
        self.app = create_app(settings=self.settings)
        self.client = TestClient(self.app)

    def test_app_lifecycle_warms_up_and_shuts_down_gateway_service(self) -> None:
        warmup_mock = AsyncMock()
        shutdown_mock = AsyncMock()

        with patch.object(self.app.state.gateway_service, "warmup", warmup_mock), patch.object(
            self.app.state.gateway_service,
            "shutdown",
            shutdown_mock,
        ):
            with TestClient(self.app):
                warmup_mock.assert_awaited_once()

        shutdown_mock.assert_awaited_once()

    def test_health_endpoint(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_models_requires_bearer_auth(self) -> None:
        response = self.client.get("/v1/models")

        self.assertEqual(response.status_code, 401)

    def test_models_returns_openai_list_shape(self) -> None:
        response = self.client.get(
            "/v1/models",
            headers={"Authorization": f"Bearer {self.settings.api_key}"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["object"], "list")

    def test_models_returns_canonical_gateway_models(self) -> None:
        response = self.client.get(
            "/v1/models",
            headers={"Authorization": f"Bearer {self.settings.api_key}"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["id"] for item in response.json()["data"]],
            [
                "gemini-3-flash",
                "gemini-3-flash-thinking",
                "gemini-3-pro",
            ],
        )

    def test_gateway_models_map_to_same_upstream_web_model_names(self) -> None:
        for model_name in [
            "gemini-3-flash",
            "gemini-3-flash-thinking",
            "gemini-3-pro",
        ]:
            with self.subTest(model_name=model_name):
                model = self.app.state.gateway_service.resolve_model(model_name)
                self.assertEqual(model.upstream_name, model_name)

    def test_legacy_gateway_model_names_are_not_supported(self) -> None:
        for model_name in [
            "gemini-3.5-flash",
            "gemini-3.1-pro",
            "gemini-3.1-flash-lite",
            "3.1 Flash-Lite",
        ]:
            with self.subTest(model_name=model_name):
                with self.assertRaisesRegex(Exception, "Unsupported model"):
                    self.app.state.gateway_service.resolve_model(model_name)

    def test_account_status_requires_bearer_auth(self) -> None:
        response = self.client.get("/v1/account/status")

        self.assertEqual(response.status_code, 401)

    def test_account_status_returns_documented_schema(self) -> None:
        snapshot = SimpleNamespace(
            raw_account_status="AVAILABLE",
            raw_account_status_code=1,
            chat_available=True,
            advanced_models_available=True,
            deep_research_available=False,
            full_web_capability_available=False,
            mode="degraded",
            unavailable_reasons=["deep_research_unavailable"],
            internal_only="should-not-leak",
        )

        with patch.object(
            self.app.state.gateway_service,
            "get_account_snapshot",
            return_value=snapshot,
        ):
            response = self.client.get(
                "/v1/account/status",
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "raw_account_status": "AVAILABLE",
                "raw_account_status_code": 1,
                "chat_available": True,
                "advanced_models_available": True,
                "deep_research_available": False,
                "full_web_capability_available": False,
                "mode": "degraded",
                "unavailable_reasons": ["deep_research_unavailable"],
            },
        )
        self.assertNotIn("internal_only", response.json())

    def test_account_status_returns_503_when_snapshot_missing(self) -> None:
        with patch.object(
            self.app.state.gateway_service,
            "get_account_snapshot",
            return_value=None,
        ):
            response = self.client.get(
                "/v1/account/status",
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["error"]["code"],
            "account_snapshot_unavailable",
        )

    def test_chat_completions_alias_route_exists(self) -> None:
        payload = {
            "model": "gemini-3-flash",
            "messages": [{"role": "user", "content": "hello"}],
        }

        with patch.object(
            self.app.state.gateway_service,
            "generate_text",
            new=AsyncMock(return_value="stub reply"),
        ):
            response = self.client.post(
                "/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["object"], "chat.completion")

    def test_chat_completions_returns_openai_shape(self) -> None:
        payload = {
            "model": "gemini-3-flash",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
        }

        with patch.object(
            self.app.state.gateway_service,
            "generate_text",
            new=AsyncMock(return_value="stub reply"),
        ):
            response = self.client.post(
                "/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["object"], "chat.completion")
        self.assertEqual(body["model"], "gemini-3-flash")
        self.assertEqual(body["choices"][0]["index"], 0)
        self.assertEqual(body["choices"][0]["message"]["role"], "assistant")
        self.assertEqual(body["choices"][0]["message"]["content"], "stub reply")
        self.assertEqual(body["choices"][0]["finish_reason"], "stop")

    def test_streaming_chat_returns_done_marker(self) -> None:
        async def fake_generate_stream(*args, **kwargs):
            yield "stub "
            yield "reply"

        payload = {
            "model": "gemini-3-flash",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        }

        with patch.object(
            self.app.state.gateway_service,
            "generate_stream",
            new=fake_generate_stream,
        ):
            with self.client.stream(
                "POST",
                "/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
            ) as response:
                chunks = list(response.iter_text())

        self.assertEqual(response.status_code, 200)
        self.assertTrue(any("data: [DONE]" in chunk for chunk in chunks))

    def test_tool_call_response_uses_openai_tool_calls_shape(self) -> None:
        payload = {
            "model": "gemini-3-flash",
            "messages": [{"role": "user", "content": "帮我查深圳天气"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "获取天气",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "city": {"type": "string"},
                            },
                            "required": ["city"],
                        },
                    },
                }
            ],
        }

        with patch.object(
            self.app.state.gateway_service,
            "generate_text",
            new=AsyncMock(
                return_value='{"tool_calls":[{"name":"get_weather","arguments":{"city":"深圳"}}]}'
            ),
        ):
            response = self.client.post(
                "/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
            )

        self.assertEqual(response.status_code, 200)
        message = response.json()["choices"][0]["message"]
        self.assertEqual(message["content"], "")
        self.assertEqual(message["tool_calls"][0]["type"], "function")
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "get_weather")
        self.assertEqual(
            message["tool_calls"][0]["function"]["arguments"],
            '{"city":"深圳"}',
        )
        self.assertEqual(response.json()["choices"][0]["finish_reason"], "tool_calls")

    def test_data_image_input_is_forwarded_to_service_chain(self) -> None:
        captured = {}

        async def fake_generate_text(*args, **kwargs):
            captured["files"] = kwargs.get("files")
            return "image ok"

        payload = {
            "model": "gemini-3-flash",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "请描述图片"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO2pW8QAAAAASUVORK5CYII="
                            },
                        },
                    ],
                }
            ],
        }

        with patch.object(
            self.app.state.gateway_service,
            "generate_text",
            new=fake_generate_text,
        ):
            response = self.client.post(
                "/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(captured["files"]), 1)

    def test_extra_body_files_is_forwarded_to_service_chain(self) -> None:
        captured = {}

        async def fake_generate_text(*args, **kwargs):
            captured["files"] = kwargs.get("files")
            return "file ok"

        payload = {
            "model": "gemini-3-flash",
            "messages": [{"role": "user", "content": "请总结附件"}],
            "extra_body": {
                "files": [
                    {
                        "name": "note.txt",
                        "content_type": "text/plain",
                        "data_base64": "aGVsbG8gd29ybGQ=",
                    }
                ]
            },
        }

        with patch.object(
            self.app.state.gateway_service,
            "generate_text",
            new=fake_generate_text,
        ):
            response = self.client.post(
                "/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(captured["files"]), 1)

    def test_invalid_reasoning_effort_returns_structured_error(self) -> None:
        payload = {
            "model": "gemini-3-flash",
            "reasoning_effort": "turbo",
            "messages": [{"role": "user", "content": "hello"}],
        }

        response = self.client.post(
            "/v1/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {self.settings.api_key}"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_reasoning_effort")

    def test_upstream_timeout_returns_structured_error(self) -> None:
        payload = {
            "model": "gemini-3-flash",
            "messages": [{"role": "user", "content": "hello"}],
        }

        with patch.object(
            self.app.state.gateway_service,
            "generate_text",
            new=AsyncMock(side_effect=TimeoutError("timeout")),
        ):
            response = self.client.post(
                "/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
            )

        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.json()["error"]["code"], "upstream_timeout")

    def test_invalid_extra_body_files_returns_structured_error(self) -> None:
        payload = {
            "model": "gemini-3-flash",
            "messages": [{"role": "user", "content": "请总结附件"}],
            "extra_body": {
                "files": [
                    {
                        "name": "note.txt",
                        "content_type": "text/plain",
                        "data_base64": "%%%invalid%%%",
                    }
                ]
            },
        }

        response = self.client.post(
            "/v1/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {self.settings.api_key}"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "file_decode_failed")

    def test_build_prompt_preserves_tool_call_history(self) -> None:
        prompt = self.app.state.gateway_service.build_prompt_from_messages(
            messages=[
                ChatMessage(
                    role="assistant",
                    content=None,
                    tool_calls=[
                        ChatToolCall(
                            id="call_123",
                            function=ChatToolFunction(
                                name="get_weather",
                                arguments='{"city":"深圳"}',
                            ),
                        )
                    ],
                ),
                ChatMessage(
                    role="tool",
                    tool_call_id="call_123",
                    content="晴天 28 度",
                ),
                ChatMessage(role="user", content="继续"),
            ]
        )

        self.assertIn("get_weather", prompt)
        self.assertIn("call_123", prompt)
        self.assertIn("晴天 28 度", prompt)

    def test_lifespan_prints_startup_summary_after_warmup(self) -> None:
        stdout = io.StringIO()
        snapshot = SimpleNamespace(mode="degraded")
        warmup_mock = AsyncMock()
        shutdown_mock = AsyncMock()

        with (
            patch.object(self.app.state.gateway_service, "warmup", warmup_mock),
            patch.object(self.app.state.gateway_service, "shutdown", shutdown_mock),
            patch.object(
                self.app.state.gateway_service,
                "get_account_snapshot",
                return_value=snapshot,
            ),
            contextlib.redirect_stdout(stdout),
        ):
            with TestClient(self.app):
                pass

        output = stdout.getvalue()
        self.assertIn("Account mode: degraded", output)
        warmup_mock.assert_awaited_once()
        shutdown_mock.assert_awaited_once()

    def test_lifespan_startup_summary_ignores_account_snapshot_errors(self) -> None:
        stdout = io.StringIO()
        warmup_mock = AsyncMock()
        shutdown_mock = AsyncMock()

        with (
            patch.object(self.app.state.gateway_service, "warmup", warmup_mock),
            patch.object(self.app.state.gateway_service, "shutdown", shutdown_mock),
            patch.object(
                self.app.state.gateway_service,
                "get_account_snapshot",
                side_effect=RuntimeError("boom"),
            ),
            contextlib.redirect_stdout(stdout),
        ):
            with TestClient(self.app):
                pass

        output = stdout.getvalue()
        self.assertIn("Account mode: unavailable", output)
        warmup_mock.assert_awaited_once()
        shutdown_mock.assert_awaited_once()

    def test_main_prints_base_configuration_without_triggering_warmup(self) -> None:
        stdout = io.StringIO()
        warmup_mock = AsyncMock()
        app = SimpleNamespace(
            state=SimpleNamespace(
                gateway_service=SimpleNamespace(
                    warmup=warmup_mock,
                    get_account_snapshot=unittest.mock.Mock(return_value=SimpleNamespace(mode="degraded")),
                )
            )
        )

        with (
            patch("gateway.main.GatewaySettings", return_value=GatewaySettings(api_key="demo-key")),
            patch("gateway.main.create_app", return_value=app),
            patch("uvicorn.run") as run_mock,
            contextlib.redirect_stdout(stdout),
        ):
            main()

        output = stdout.getvalue()
        self.assertIn("Base URL: http://127.0.0.1:8010/v1", output)
        self.assertIn("API Key: demo-key", output)
        self.assertNotIn("Account mode:", output)
        warmup_mock.assert_not_awaited()
        run_mock.assert_called_once_with(
            app,
            host="127.0.0.1",
            port=8010,
        )


if __name__ == "__main__":
    unittest.main()
