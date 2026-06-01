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

    def test_gateway_readme_documents_manual_profile_login(self) -> None:
        readme = (ROOT / "gateway" / "README.md").read_text(encoding="utf-8")

        self.assertIn("uv sync --extra browser", readme)
        self.assertIn(
            "uv run --extra browser python -m gateway.refresh_cookies",
            readme,
        )
        self.assertIn("GEMINI_GATEWAY_BROWSER_COOKIE_REFRESH_ON_AUTH_ERROR", readme)
        self.assertIn("GEMINI_GATEWAY_BROWSER_PROFILE_DIR", readme)
        self.assertIn("手动启动专用 Chrome profile", readme)
        self.assertIn(
            "复制 `gateway.refresh_cookies` 输出的完整 PowerShell 命令",
            readme,
        )
        self.assertIn("关闭该专用 profile 的 Chrome 窗口", readme)
        self.assertIn("/v1/debug/models", readme)
        self.assertIn("curl http://127.0.0.1:8010/v1/debug/models", readme)
        self.assertNotIn("首次运行会打开一个独立 Chrome profile", readme)
        self.assertNotIn("Selenium", readme)

    def test_gateway_env_script_mentions_manual_profile_guidance(self) -> None:
        script = (ROOT / "gateway" / "set_gateway_env.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("gateway.refresh_cookies", script)
        self.assertIn(
            "uv run --extra browser python -m gateway.refresh_cookies",
            script,
        )
        self.assertIn("GEMINI_GATEWAY_BROWSER_PROFILE_DIR", script)
        self.assertIn(
            "如未登录 Gemini，请先运行 refresh_cookies 并复制其输出的 PowerShell 命令",
            script,
        )

    def test_start_gateway_script_keeps_refresh_step_before_gateway_start(self) -> None:
        script = (ROOT / "gateway" / "start_gateway.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("uv sync --extra browser", script)
        self.assertIn(
            "uv run --extra browser python -m gateway.refresh_cookies",
            script,
        )
        self.assertIn("uv run python -m gateway.main", script)
        self.assertIn("manual login is required", script)


if __name__ == "__main__":
    unittest.main()
