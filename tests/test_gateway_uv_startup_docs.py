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
        self.assertIn("gateway.refresh_cookies", readme)
        self.assertIn("PowerShell 命令", readme)
        self.assertIn("先关闭该专用 profile 当前已普通打开的 Chrome 窗口", readme)
        self.assertIn("保持该专用 Chrome 继续运行", readme)
        self.assertIn("再重新执行刷新命令", readme)
        self.assertNotIn("关闭该专用 profile 的 Chrome 窗口", readme)
        self.assertIn("/v1/debug/models", readme)
        self.assertIn("curl http://127.0.0.1:8010/v1/debug/models", readme)
        self.assertNotIn("首次运行会打开一个独立 Chrome profile", readme)
        self.assertNotIn("Selenium", readme)

    def test_gateway_readme_documents_start_script_skips_live_refresh_when_cookies_exist(
        self,
    ) -> None:
        readme = (ROOT / "gateway" / "README.md").read_text(encoding="utf-8")

        self.assertIn(r".\gateway\start_gateway.ps1 -ApiKey", readme)
        self.assertIn("如已有可用 `cookies.json`", readme)
        self.assertIn("直接启动网关", readme)
        self.assertIn("缺少可用 `cookies.json`", readme)
        self.assertIn("再按手动 profile 流程刷新", readme)

    def test_gateway_readme_marks_browser_wait_settings_as_compatibility_only(
        self,
    ) -> None:
        readme = (ROOT / "gateway" / "README.md").read_text(encoding="utf-8")

        self.assertRegex(
            readme,
            r"GEMINI_GATEWAY_BROWSER_LOGIN_WAIT_SECONDS.*(兼容|保留|当前.*不使用)",
        )
        self.assertRegex(
            readme,
            r"GEMINI_GATEWAY_BROWSER_POLL_INTERVAL_SECONDS.*(兼容|保留|当前.*不使用)",
        )
        self.assertRegex(
            readme,
            r"GEMINI_GATEWAY_BROWSER_PAGE_LOAD_TIMEOUT_SECONDS.*(兼容|保留|当前.*不使用)",
        )

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
            "如缺少可用 cookies.json 或需要主动刷新登录态",
            script,
        )
        self.assertIn("先关闭当前已普通打开的同 profile Chrome 窗口", script)
        self.assertIn("保持该专用 Chrome 继续运行", script)
        self.assertNotIn("关闭该专用 profile 的 Chrome 窗口", script)

    def test_start_gateway_script_skips_live_refresh_when_existing_cookies_exist(
        self,
    ) -> None:
        script = (ROOT / "gateway" / "start_gateway.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("Test-GatewayCookiesJsonUsable", script)
        self.assertIn("ConvertFrom-Json", script)
        self.assertIn("__Secure-1PSID", script)
        self.assertIn("跳过 refresh_cookies", script)
        self.assertIn("uv sync", script)
        self.assertIn(
            "uv run --extra browser python -m gateway.refresh_cookies",
            script,
        )
        self.assertIn("uv run python -m gateway.main", script)
        self.assertIn("缺少可用 cookies.json", script)


if __name__ == "__main__":
    unittest.main()
