from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TestGatewayUvStartupDocs(unittest.TestCase):
    def test_gateway_env_script_points_to_uv_run_startup(self) -> None:
        script = (ROOT / "gateway" / "set_gateway_env.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("uv run python -m gateway.main", script)

    def test_gateway_readme_uses_uv_startup_commands(self) -> None:
        readme = (ROOT / "gateway" / "README.md").read_text(encoding="utf-8")

        self.assertIn("uv sync", readme)
        self.assertIn("uv run python -m gateway.main", readme)
        self.assertNotIn("pip install -e .", readme)

    def test_gateway_readme_documents_browser_cookie_refresh(self) -> None:
        readme = (ROOT / "gateway" / "README.md").read_text(encoding="utf-8")

        self.assertIn("uv sync --extra browser", readme)
        self.assertIn("uv run --extra browser python -m gateway.refresh_cookies", readme)
        self.assertIn("GEMINI_GATEWAY_BROWSER_COOKIE_REFRESH_ON_AUTH_ERROR", readme)
        self.assertIn("GEMINI_GATEWAY_BROWSER_PROFILE_DIR", readme)
        self.assertIn("Selenium", readme)

    def test_gateway_env_script_mentions_refresh_cookies(self) -> None:
        script = (ROOT / "gateway" / "set_gateway_env.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("gateway.refresh_cookies", script)
        self.assertIn("uv run --extra browser python -m gateway.refresh_cookies", script)
        self.assertIn("GEMINI_GATEWAY_BROWSER_PROFILE_DIR", script)

    def test_start_gateway_script_uses_uv_and_refresh_cookies(self) -> None:
        script = (ROOT / "gateway" / "start_gateway.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("uv sync --extra browser", script)
        self.assertIn("uv run --extra browser python -m gateway.refresh_cookies", script)
        self.assertIn("uv run python -m gateway.main", script)


if __name__ == "__main__":
    unittest.main()
