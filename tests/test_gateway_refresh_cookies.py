import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

from gateway.config import GatewaySettings
from gateway.refresh_cookies import (
    BrowserCookieRefreshError,
    BrowserCookieSelection,
    DevToolsEndpoint,
    build_manual_chrome_launch_command,
    collect_google_cookies,
    load_browser_cookies_from_profile,
    load_browser_cookies_via_cdp,
    load_devtools_endpoint_from_profile,
    main,
    print_manual_login_guidance,
    refresh_browser_cookies_to_file,
)
from gateway.service import GatewayService


class TestGatewayRefreshCookies(unittest.TestCase):
    def test_build_manual_chrome_launch_command_uses_profile_dir_and_gemini_url(
        self,
    ) -> None:
        command = build_manual_chrome_launch_command(
            Path(r"C:\Users\27355\.gemini-api\selenium-profile")
        )

        self.assertIn(
            '${env:ProgramFiles}\\Google\\Chrome\\Application\\chrome.exe',
            command,
        )
        self.assertIn(
            '--user-data-dir="C:\\Users\\27355\\.gemini-api\\selenium-profile"',
            command,
        )
        self.assertIn('--profile-directory="Default"', command)
        self.assertIn('"https://gemini.google.com/app"', command)

    def test_build_manual_chrome_launch_command_enables_remote_debugging(
        self,
    ) -> None:
        command = build_manual_chrome_launch_command(
            Path(r"C:\Users\27355\.gemini-api\selenium-profile")
        )

        self.assertIn("--remote-debugging-port=0", command)

    def test_load_devtools_endpoint_from_profile_reads_port_and_ws_path(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_dir = Path(temp_dir) / "profile"
            profile_dir.mkdir(parents=True, exist_ok=True)
            (profile_dir / "DevToolsActivePort").write_text(
                "9222\n/devtools/browser/test-browser-id\n",
                encoding="utf-8",
            )

            endpoint = load_devtools_endpoint_from_profile(profile_dir)

        self.assertEqual(
            endpoint,
            DevToolsEndpoint(
                port=9222,
                browser_websocket_url=(
                    "ws://127.0.0.1:9222/devtools/browser/test-browser-id"
                ),
                version_url="http://127.0.0.1:9222/json/version",
            ),
        )

    def test_load_devtools_endpoint_from_profile_requires_debugging_session_when_file_missing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_dir = Path(temp_dir) / "profile"
            profile_dir.mkdir(parents=True, exist_ok=True)

            with self.assertRaises(BrowserCookieRefreshError) as ctx:
                load_devtools_endpoint_from_profile(profile_dir)

        self.assertTrue(ctx.exception.manual_login_required)
        self.assertTrue(ctx.exception.debugging_session_required)

    def test_load_devtools_endpoint_from_profile_rejects_single_line_file(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_dir = Path(temp_dir) / "profile"
            profile_dir.mkdir(parents=True, exist_ok=True)
            (profile_dir / "DevToolsActivePort").write_text(
                "9222\n",
                encoding="utf-8",
            )

            with self.assertRaises(BrowserCookieRefreshError) as ctx:
                load_devtools_endpoint_from_profile(profile_dir)

        self.assertTrue(ctx.exception.manual_login_required)
        self.assertTrue(ctx.exception.debugging_session_required)

    def test_load_devtools_endpoint_from_profile_rejects_non_numeric_port(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_dir = Path(temp_dir) / "profile"
            profile_dir.mkdir(parents=True, exist_ok=True)
            (profile_dir / "DevToolsActivePort").write_text(
                "not-a-port\n/devtools/browser/test-browser-id\n",
                encoding="utf-8",
            )

            with self.assertRaises(BrowserCookieRefreshError) as ctx:
                load_devtools_endpoint_from_profile(profile_dir)

        self.assertTrue(ctx.exception.manual_login_required)
        self.assertTrue(ctx.exception.debugging_session_required)

    def test_load_devtools_endpoint_from_profile_rejects_ws_path_without_leading_slash(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_dir = Path(temp_dir) / "profile"
            profile_dir.mkdir(parents=True, exist_ok=True)
            (profile_dir / "DevToolsActivePort").write_text(
                "9222\ndevtools/browser/test-browser-id\n",
                encoding="utf-8",
            )

            with self.assertRaises(BrowserCookieRefreshError) as ctx:
                load_devtools_endpoint_from_profile(profile_dir)

        self.assertTrue(ctx.exception.manual_login_required)
        self.assertTrue(ctx.exception.debugging_session_required)

    def test_load_devtools_endpoint_from_profile_rejects_missing_ws_path_line(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_dir = Path(temp_dir) / "profile"
            profile_dir.mkdir(parents=True, exist_ok=True)
            (profile_dir / "DevToolsActivePort").write_text(
                "9222\n\n",
                encoding="utf-8",
            )

            with self.assertRaises(BrowserCookieRefreshError) as ctx:
                load_devtools_endpoint_from_profile(profile_dir)

        self.assertTrue(ctx.exception.manual_login_required)
        self.assertTrue(ctx.exception.debugging_session_required)

    def test_collect_google_cookies_filters_non_google_domains(self) -> None:
        cookies = collect_google_cookies(
            [
                {
                    "name": "__Secure-1PSID",
                    "value": "psid",
                    "domain": ".google.com",
                },
                {
                    "name": "__Secure-1PSIDTS",
                    "value": "psidts",
                    "domain": "accounts.google.com",
                },
                {
                    "name": "AEC",
                    "value": "aec",
                    "domain": ".google.com.hk",
                },
                {
                    "name": "NID",
                    "value": "nid",
                    "domain": ".example.com",
                },
            ]
        )

        self.assertEqual(cookies["__Secure-1PSID"], "psid")
        self.assertEqual(cookies["__Secure-1PSIDTS"], "psidts")
        self.assertNotIn("AEC", cookies)
        self.assertNotIn("NID", cookies)

    def test_load_browser_cookies_from_profile_uses_explicit_cookie_and_state_files(
        self,
    ) -> None:
        captured: dict[str, object] = {}

        class FakeCookie:
            def __init__(self, name: str, value: str, domain: str) -> None:
                self.name = name
                self.value = value
                self.domain = domain

            def is_expired(self) -> bool:
                return False

        def fake_loader(*, cookie_file: str, domain_name: str, key_file: str):
            captured["cookie_file"] = cookie_file
            captured["key_file"] = key_file
            captured["domain_name"] = domain_name
            return [
                FakeCookie("__Secure-1PSID", "psid", ".google.com"),
                FakeCookie("__Secure-1PSIDTS", "psidts", ".google.com"),
            ]

        selection = load_browser_cookies_from_profile(
            profile_dir=Path(r"C:\Users\27355\.gemini-api\selenium-profile"),
            browser_loader=fake_loader,
        )

        self.assertEqual(
            captured["cookie_file"],
            r"C:\Users\27355\.gemini-api\selenium-profile\Default\Network\Cookies",
        )
        self.assertEqual(
            captured["key_file"],
            r"C:\Users\27355\.gemini-api\selenium-profile\Local State",
        )
        self.assertEqual(captured["domain_name"], ".google.com")
        self.assertEqual(selection.source, "manual-chrome-profile-cdp")
        self.assertEqual(selection.cookies["__Secure-1PSID"], "psid")
        self.assertEqual(selection.cookies["__Secure-1PSIDTS"], "psidts")

    def test_load_browser_cookies_from_profile_requires_manual_login_when_profile_files_missing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(
                BrowserCookieRefreshError,
                "未检测到专用 Chrome profile 中的有效 Gemini 登录态",
            ) as ctx:
                load_browser_cookies_from_profile(
                    profile_dir=Path(temp_dir) / "profile",
                    browser_loader=lambda **kwargs: self.fail(
                        "browser_loader should not be called when profile files are missing"
                    ),
                )

        self.assertTrue(ctx.exception.manual_login_required)

    def test_load_browser_cookies_via_cdp_extracts_live_google_auth_cookies(
        self,
    ) -> None:
        endpoint = DevToolsEndpoint(
            port=58472,
            browser_websocket_url="ws://127.0.0.1:58472/devtools/browser/1234",
            version_url="http://127.0.0.1:58472/json/version",
        )
        fake_ws = Mock()
        fake_ws.recv_json.side_effect = [
            {
                "id": 1,
                "result": {
                    "cookies": [
                        {
                            "name": "__Secure-1PSID",
                            "value": "live-psid",
                            "domain": ".google.com",
                        },
                        {
                            "name": "__Secure-1PSIDTS",
                            "value": "live-psidts",
                            "domain": ".google.com",
                        },
                        {
                            "name": "NID",
                            "value": "ignore-me",
                            "domain": ".example.com",
                        },
                    ]
                },
            }
        ]
        fake_session = Mock()
        fake_session.ws_connect.return_value = fake_ws

        selection = load_browser_cookies_via_cdp(
            endpoint=endpoint,
            session_factory=lambda: fake_session,
        )

        fake_session.ws_connect.assert_called_once_with(
            "ws://127.0.0.1:58472/devtools/browser/1234"
        )
        fake_ws.send_json.assert_called_once_with(
            {"id": 1, "method": "Storage.getCookies"}
        )
        self.assertEqual(selection.source, "manual-chrome-profile-cdp")
        self.assertEqual(selection.cookies["__Secure-1PSID"], "live-psid")
        self.assertEqual(selection.cookies["__Secure-1PSIDTS"], "live-psidts")
        self.assertNotIn("NID", selection.cookies)

    def test_load_browser_cookies_via_cdp_skips_event_frames_until_matching_response(
        self,
    ) -> None:
        endpoint = DevToolsEndpoint(
            port=58472,
            browser_websocket_url="ws://127.0.0.1:58472/devtools/browser/1234",
            version_url="http://127.0.0.1:58472/json/version",
        )
        fake_ws = Mock()
        fake_ws.recv_json.side_effect = [
            {
                "method": "Target.targetCreated",
                "params": {"targetInfo": {"targetId": "background-page"}},
            },
            {
                "id": 1,
                "result": {
                    "cookies": [
                        {
                            "name": "__Secure-1PSID",
                            "value": "live-psid",
                            "domain": ".google.com",
                        }
                    ]
                },
            },
        ]
        fake_session = Mock()
        fake_session.ws_connect.return_value = fake_ws

        selection = load_browser_cookies_via_cdp(
            endpoint=endpoint,
            session_factory=lambda: fake_session,
        )

        self.assertEqual(fake_ws.recv_json.call_count, 2)
        self.assertEqual(selection.cookies["__Secure-1PSID"], "live-psid")

    def test_print_manual_login_guidance_outputs_copyable_pwsh_command(self) -> None:
        stdout = StringIO()

        with redirect_stdout(stdout):
            print_manual_login_guidance(
                profile_dir=Path(r"C:\Users\27355\.gemini-api\selenium-profile"),
                url="https://gemini.google.com/app",
            )

        output = stdout.getvalue()
        self.assertIn("已连接专用 Chrome，但未检测到有效 Gemini 登录态", output)
        self.assertIn(
            "Google 可能会阻止由自动化框架控制的 Chrome 登录账号",
            output,
        )
        self.assertIn(
            '--user-data-dir="C:\\Users\\27355\\.gemini-api\\selenium-profile"',
            output,
        )
        self.assertIn("不要关闭窗口", output)
        self.assertIn(
            "uv run --extra browser python -m gateway.refresh_cookies",
            output,
        )

    def test_refresh_browser_cookies_writes_gateway_compatible_json_without_leaking_values(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cookies_path = Path(temp_dir) / "cookies.json"
            stdout = StringIO()

            with patch(
                "gateway.refresh_cookies.load_devtools_endpoint_from_profile",
                return_value=DevToolsEndpoint(
                    port=58472,
                    browser_websocket_url="ws://127.0.0.1:58472/devtools/browser/1234",
                    version_url="http://127.0.0.1:58472/json/version",
                ),
            ), patch(
                "gateway.refresh_cookies.load_browser_cookies_via_cdp",
                return_value=BrowserCookieSelection(
                    source="manual-chrome-profile-cdp",
                    cookies={
                        "__Secure-1PSID": "secret-psid",
                        "__Secure-1PSIDTS": "secret-psidts",
                        "NID": "secret-nid",
                    },
                ),
            ), redirect_stdout(stdout):
                result = refresh_browser_cookies_to_file(
                    cookies_path,
                    profile_dir=Path(temp_dir) / "profile",
                )

            payload = json.loads(cookies_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["source"], "manual-chrome-profile-cdp")
            self.assertEqual(payload["cookies"]["__Secure-1PSID"], "secret-psid")
            self.assertEqual(
                payload["cookies"]["__Secure-1PSIDTS"],
                "secret-psidts",
            )
            self.assertEqual(result.source, "manual-chrome-profile-cdp")

            output = stdout.getvalue()
            self.assertIn("Browser cookies refreshed:", output)
            self.assertIn("source=manual-chrome-profile-cdp", output)
            self.assertIn("has_1psid=true", output)
            self.assertNotIn("secret-psid", output)
            self.assertNotIn("secret-psidts", output)

            service = GatewayService(
                GatewaySettings(
                    api_key="test-key",
                    cookies_json_path=str(cookies_path),
                    proxy="http://127.0.0.1:7890",
                )
            )
            self.assertEqual(service.load_cookies()["__Secure-1PSID"], "secret-psid")

    def test_refresh_browser_cookies_to_file_uses_live_cdp_cookies(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cookies_path = Path(temp_dir) / "cookies.json"
            stdout = StringIO()

            with patch(
                "gateway.refresh_cookies.load_devtools_endpoint_from_profile",
                return_value=DevToolsEndpoint(
                    port=58472,
                    browser_websocket_url="ws://127.0.0.1:58472/devtools/browser/1234",
                    version_url="http://127.0.0.1:58472/json/version",
                ),
            ) as load_endpoint, patch(
                "gateway.refresh_cookies.load_browser_cookies_via_cdp",
                return_value=BrowserCookieSelection(
                    source="manual-chrome-profile-cdp",
                    cookies={
                        "__Secure-1PSID": "live-psid",
                        "__Secure-1PSIDTS": "live-psidts",
                    },
                ),
            ) as load_cdp, patch(
                "gateway.refresh_cookies.load_browser_cookies_from_profile",
                side_effect=AssertionError(
                    "refresh_browser_cookies_to_file should not use the profile SQLite path"
                ),
            ), redirect_stdout(stdout):
                selection = refresh_browser_cookies_to_file(
                    cookies_path,
                    profile_dir=Path(temp_dir) / "selenium-profile",
                )

            load_endpoint.assert_called_once_with(Path(temp_dir) / "selenium-profile")
            load_cdp.assert_called_once()

            payload = json.loads(cookies_path.read_text(encoding="utf-8"))
            self.assertEqual(selection.source, "manual-chrome-profile-cdp")
            self.assertEqual(payload["source"], "manual-chrome-profile-cdp")
            self.assertEqual(payload["cookies"]["__Secure-1PSID"], "live-psid")
            self.assertEqual(payload["cookies"]["__Secure-1PSIDTS"], "live-psidts")

            output = stdout.getvalue()
            self.assertIn("Browser cookies refreshed:", output)
            self.assertIn("source=manual-chrome-profile-cdp", output)
            self.assertNotIn("live-psid", output)
            self.assertNotIn("live-psidts", output)

    def test_refresh_browser_cookies_does_not_overwrite_existing_file_when_invalid(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cookies_path = Path(temp_dir) / "cookies.json"
            cookies_path.write_text(
                '{"cookies":{"__Secure-1PSID":"old-psid"}}',
                encoding="utf-8",
            )

            with patch(
                "gateway.refresh_cookies.load_devtools_endpoint_from_profile",
                return_value=DevToolsEndpoint(
                    port=58472,
                    browser_websocket_url="ws://127.0.0.1:58472/devtools/browser/1234",
                    version_url="http://127.0.0.1:58472/json/version",
                ),
            ), patch(
                "gateway.refresh_cookies.load_browser_cookies_via_cdp",
                side_effect=BrowserCookieRefreshError(
                    "已连接专用 Chrome，但未检测到有效 Gemini 登录态。",
                    manual_login_required=True,
                ),
            ):
                with self.assertRaises(BrowserCookieRefreshError):
                    refresh_browser_cookies_to_file(
                        cookies_path,
                        profile_dir=Path(temp_dir) / "profile",
                    )

            self.assertEqual(
                json.loads(cookies_path.read_text(encoding="utf-8"))["cookies"][
                    "__Secure-1PSID"
                ],
                "old-psid",
            )

    def test_load_browser_cookies_via_cdp_requires_live_gemini_login_when_1psid_missing(
        self,
    ) -> None:
        endpoint = DevToolsEndpoint(
            port=58472,
            browser_websocket_url="ws://127.0.0.1:58472/devtools/browser/1234",
            version_url="http://127.0.0.1:58472/json/version",
        )
        fake_ws = Mock()
        fake_ws.recv_json.return_value = {
            "id": 1,
            "result": {
                "cookies": [
                    {
                        "name": "__Secure-1PSIDTS",
                        "value": "live-psidts",
                        "domain": ".google.com",
                    }
                ]
            },
        }
        fake_session = Mock()
        fake_session.ws_connect.return_value = fake_ws

        with self.assertRaisesRegex(
            BrowserCookieRefreshError,
            "未检测到有效 Gemini 登录态",
        ) as ctx:
            load_browser_cookies_via_cdp(
                endpoint=endpoint,
                session_factory=lambda: fake_session,
            )

        self.assertTrue(ctx.exception.manual_login_required)

    def test_load_browser_cookies_via_cdp_raises_when_matching_response_has_error(
        self,
    ) -> None:
        endpoint = DevToolsEndpoint(
            port=58472,
            browser_websocket_url="ws://127.0.0.1:58472/devtools/browser/1234",
            version_url="http://127.0.0.1:58472/json/version",
        )
        fake_ws = Mock()
        fake_ws.recv_json.return_value = {
            "id": 1,
            "error": {
                "code": -32000,
                "message": "Storage agent unavailable",
            },
        }
        fake_session = Mock()
        fake_session.ws_connect.return_value = fake_ws

        with self.assertRaisesRegex(
            BrowserCookieRefreshError,
            "Storage agent unavailable",
        ):
            load_browser_cookies_via_cdp(
                endpoint=endpoint,
                session_factory=lambda: fake_session,
            )

    def test_load_browser_cookies_from_profile_errors_when_cookie_never_appears(
        self,
    ) -> None:
        class FakeCookie:
            def __init__(self, name: str, value: str, domain: str) -> None:
                self.name = name
                self.value = value
                self.domain = domain

            def is_expired(self) -> bool:
                return False

        with self.assertRaisesRegex(
            BrowserCookieRefreshError,
            "未检测到专用 Chrome profile 中的有效 Gemini 登录态",
        ) as ctx:
            load_browser_cookies_from_profile(
                profile_dir=Path(tempfile.gettempdir()) / "selenium-profile-test",
                browser_loader=lambda **kwargs: [
                    FakeCookie("NID", "nid", ".google.com")
                ],
            )

        self.assertTrue(ctx.exception.manual_login_required)

    def test_load_browser_cookies_from_profile_reports_profile_in_use_on_permission_error(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            BrowserCookieRefreshError,
            "专用 Chrome profile 当前仍在运行",
        ) as ctx:
            load_browser_cookies_from_profile(
                profile_dir=Path(r"C:\Users\27355\.gemini-api\selenium-profile"),
                browser_loader=lambda **kwargs: (_ for _ in ()).throw(
                    PermissionError(
                        "[Errno 13] Permission denied: "
                        "'C:\\Users\\27355\\.gemini-api\\selenium-profile\\Default\\Network\\Cookies'"
                    )
                ),
            )

        self.assertTrue(ctx.exception.profile_in_use)
        self.assertFalse(ctx.exception.manual_login_required)

    def test_main_prints_manual_login_guidance_when_profile_not_logged_in(
        self,
    ) -> None:
        stdout = StringIO()
        stderr = StringIO()

        with (
            patch(
                "gateway.refresh_cookies.refresh_browser_cookies_to_file",
                side_effect=BrowserCookieRefreshError(
                    "未检测到专用 Chrome profile 中的有效 Gemini 登录态。",
                    manual_login_required=True,
                ),
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = main(
                ["--profile-dir", r"C:\Users\27355\.gemini-api\selenium-profile"]
            )

        self.assertEqual(exit_code, 1)
        self.assertIn(
            "未检测到专用 Chrome profile 中的有效 Gemini 登录态",
            stderr.getvalue(),
        )
        self.assertIn(
            "Google 可能会阻止由自动化框架控制的 Chrome 登录账号",
            stdout.getvalue(),
        )
        self.assertIn(
            '--user-data-dir="C:\\Users\\27355\\.gemini-api\\selenium-profile"',
            stdout.getvalue(),
        )

    def test_main_prints_remote_debugging_guidance_when_session_is_missing(
        self,
    ) -> None:
        stdout = StringIO()
        stderr = StringIO()

        with (
            patch(
                "gateway.refresh_cookies.refresh_browser_cookies_to_file",
                side_effect=BrowserCookieRefreshError(
                    "未检测到专用 Chrome profile 的远程调试会话，请先按指引手动启动带 remote debugging 的 Chrome。",
                    manual_login_required=True,
                    debugging_session_required=True,
                ),
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = main(
                ["--profile-dir", r"C:\Users\27355\.gemini-api\selenium-profile"]
            )

        self.assertEqual(exit_code, 1)
        self.assertIn("远程调试会话", stderr.getvalue())
        self.assertIn("--remote-debugging-port=0", stdout.getvalue())
        self.assertIn("不要关闭窗口", stdout.getvalue())
        self.assertIn(
            "再重新执行 refresh_cookies",
            stdout.getvalue(),
        )

    def test_main_prints_close_browser_guidance_when_profile_cookie_db_is_locked(
        self,
    ) -> None:
        stdout = StringIO()
        stderr = StringIO()

        with (
            patch(
                "gateway.refresh_cookies.refresh_browser_cookies_to_file",
                side_effect=BrowserCookieRefreshError(
                    "专用 Chrome profile 当前仍在运行，无法读取 Cookies 数据库。"
                    "请先关闭该 profile 的 Chrome 窗口后再重新执行。",
                    profile_in_use=True,
                ),
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = main(
                ["--profile-dir", r"C:\Users\27355\.gemini-api\selenium-profile"]
            )

        self.assertEqual(exit_code, 1)
        self.assertIn("专用 Chrome profile 当前仍在运行", stderr.getvalue())
        self.assertIn("请先关闭该 profile 的 Chrome 窗口", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
