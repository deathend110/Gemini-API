import unittest

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


if __name__ == "__main__":
    unittest.main()
