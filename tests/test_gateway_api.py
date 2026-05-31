import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from gateway.config import GatewaySettings
from gateway.main import create_app

class TestGatewayApi(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = GatewaySettings(api_key="test-key")
        self.app = create_app(settings=self.settings)
        self.client = TestClient(self.app)

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
                "gemini-3.1-pro",
                "gemini-3.5-flash",
                "gemini-3.1-flash-lite",
            ],
        )

    def test_chat_completions_alias_route_exists(self) -> None:
        payload = {
            "model": "gemini-3.5-flash",
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
            "model": "gemini-3.5-flash",
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
        self.assertEqual(body["model"], "gemini-3.5-flash")
        self.assertEqual(body["choices"][0]["index"], 0)
        self.assertEqual(body["choices"][0]["message"]["role"], "assistant")
        self.assertEqual(body["choices"][0]["message"]["content"], "stub reply")
        self.assertEqual(body["choices"][0]["finish_reason"], "stop")


if __name__ == "__main__":
    unittest.main()
