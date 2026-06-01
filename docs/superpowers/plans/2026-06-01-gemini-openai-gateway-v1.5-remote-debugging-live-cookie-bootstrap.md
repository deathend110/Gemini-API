# Gemini OpenAI 网关 V1.5 Remote Debugging Live Cookie Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `gateway.refresh_cookies` 从“关闭专用 Chrome 后读取 profile SQLite Cookie 数据库”切换为“连接运行中的专用 Chrome remote debugging 会话并提取 live cookie”。

**Architecture:** 保留 V1.4 的命令入口、`cookies.json` 写回格式、`gateway.service` 调用方式和 Gemini-API 运行期自动续期机制，只替换启动前的初始 Cookie 引导链路。`gateway.refresh_cookies` 新增 DevToolsActivePort 解析与 CDP 取 cookie 能力，CLI 文案从“关闭 Chrome 再读”改成“保持专用 Chrome 运行并重新执行 refresh_cookies”。

**Tech Stack:** Python 3.10+、FastAPI、curl-cffi、unittest、unittest.mock、PowerShell、uv

---

## 文件结构

- Modify: `gateway/refresh_cookies.py`
  - 将 profile SQLite 读取路径替换为 remote debugging live-cookie 读取路径
- Modify: `gateway/service.py`
  - 保持 service 刷新入口不变，只同步新的错误语义和最小调用参数
- Modify: `gateway/README.md`
  - 更新启动步骤、Cookie 引导说明和“不要关闭 Chrome”的说明
- Modify: `gateway/start_gateway.ps1`
  - 更新头部提示，说明 `refresh_cookies` 打印的是带 remote debugging 参数的命令
- Modify: `gateway/set_gateway_env.ps1`
  - 更新推荐启动文案，强调保持专用 Chrome 运行
- Modify: `tests/test_gateway_refresh_cookies.py`
  - 覆盖 remote debugging 命令、DevToolsActivePort 解析、CDP 取 live cookie 和 CLI 引导
- Modify: `tests/test_gateway_service_lifecycle.py`
  - 覆盖 service 对新错误语义的兼容
- Modify: `tests/test_gateway_uv_startup_docs.py`
  - 覆盖 README 与 PowerShell 脚本中的 V1.5 文案

### Task 1: 为 `gateway.refresh_cookies` 增加 remote debugging 启动约定和 DevToolsActivePort 解析

**Files:**
- Modify: `gateway/refresh_cookies.py`
- Modify: `tests/test_gateway_refresh_cookies.py`

- [ ] **Step 1: 先写 remote debugging 命令与 DevToolsActivePort 解析的失败测试**

```python
    def test_build_manual_chrome_launch_command_enables_remote_debugging(self) -> None:
        command = build_manual_chrome_launch_command(
            Path(r"C:\Users\27355\.gemini-api\selenium-profile")
        )

        self.assertIn('--user-data-dir="C:\\Users\\27355\\.gemini-api\\selenium-profile"', command)
        self.assertIn('--profile-directory="Default"', command)
        self.assertIn("--remote-debugging-port=0", command)
        self.assertIn('"https://gemini.google.com/app"', command)

    def test_load_devtools_endpoint_from_profile_reads_port_and_ws_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_dir = Path(temp_dir) / "selenium-profile"
            profile_dir.mkdir(parents=True, exist_ok=True)
            (profile_dir / "DevToolsActivePort").write_text(
                "58472\n/devtools/browser/1234-5678\n",
                encoding="utf-8",
            )

            endpoint = load_devtools_endpoint_from_profile(profile_dir)

        self.assertEqual(endpoint.port, 58472)
        self.assertEqual(
            endpoint.browser_websocket_url,
            "ws://127.0.0.1:58472/devtools/browser/1234-5678",
        )
        self.assertEqual(endpoint.version_url, "http://127.0.0.1:58472/json/version")
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run python -m unittest tests.test_gateway_refresh_cookies.TestGatewayRefreshCookies.test_build_manual_chrome_launch_command_enables_remote_debugging tests.test_gateway_refresh_cookies.TestGatewayRefreshCookies.test_load_devtools_endpoint_from_profile_reads_port_and_ws_path -v`

Expected: FAIL，提示 `--remote-debugging-port=0` 尚未出现在命令中，或 `load_devtools_endpoint_from_profile()` / `DevToolsEndpoint` 尚未定义。

- [ ] **Step 3: 写最小实现**

在 `gateway/refresh_cookies.py` 中新增 DevTools endpoint 结构与解析逻辑：

```python
REMOTE_DEBUGGING_HOST = "127.0.0.1"
REMOTE_DEBUGGING_PORT_FLAG = "--remote-debugging-port=0"
DEVTOOLS_ACTIVE_PORT_FILE = "DevToolsActivePort"


@dataclass(frozen=True)
class DevToolsEndpoint:
    port: int
    browser_websocket_url: str
    version_url: str


def build_manual_chrome_launch_command(
    profile_dir: str | Path,
    url: str = MANUAL_GEMINI_URL,
) -> str:
    resolved_profile_dir = str(Path(profile_dir))
    return (
        '$Chrome = "${env:ProgramFiles}\\Google\\Chrome\\Application\\chrome.exe"; '
        'if (-not (Test-Path $Chrome)) { '
        '$Chrome = "${env:ProgramFiles(x86)}\\Google\\Chrome\\Application\\chrome.exe" '
        '}; '
        f'& $Chrome --user-data-dir="{resolved_profile_dir}" '
        '--profile-directory="Default" '
        f'{REMOTE_DEBUGGING_PORT_FLAG} '
        f'"{url}"'
    )


def load_devtools_endpoint_from_profile(profile_dir: str | Path) -> DevToolsEndpoint:
    active_port_file = Path(profile_dir) / DEVTOOLS_ACTIVE_PORT_FILE
    if not active_port_file.is_file():
        raise BrowserCookieRefreshError(
            "未检测到专用 Chrome profile 的可用 remote debugging 会话。",
            manual_login_required=True,
            debugging_session_required=True,
        )

    lines = [
        line.strip()
        for line in active_port_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not lines:
        raise BrowserCookieRefreshError(
            "专用 Chrome profile 的 DevToolsActivePort 内容无效。",
        )

    try:
        port = int(lines[0])
    except ValueError as exc:
        raise BrowserCookieRefreshError(
            "专用 Chrome profile 的 DevToolsActivePort 端口无效。",
        ) from exc

    browser_path = (
        lines[1]
        if len(lines) >= 2
        else "/devtools/browser"
    )
    if not browser_path.startswith("/"):
        browser_path = f"/{browser_path}"

    return DevToolsEndpoint(
        port=port,
        browser_websocket_url=f"ws://{REMOTE_DEBUGGING_HOST}:{port}{browser_path}",
        version_url=f"http://{REMOTE_DEBUGGING_HOST}:{port}/json/version",
    )
```

并将 `BrowserCookieRefreshError` 扩展为：

```python
class BrowserCookieRefreshError(Exception):
    def __init__(
        self,
        message: str,
        *,
        manual_login_required: bool = False,
        profile_in_use: bool = False,
        debugging_session_required: bool = False,
    ) -> None:
        super().__init__(message)
        self.manual_login_required = manual_login_required
        self.profile_in_use = profile_in_use
        self.debugging_session_required = debugging_session_required
```

- [ ] **Step 4: 运行测试并确认通过**

Run: `uv run python -m unittest tests.test_gateway_refresh_cookies.TestGatewayRefreshCookies.test_build_manual_chrome_launch_command_enables_remote_debugging tests.test_gateway_refresh_cookies.TestGatewayRefreshCookies.test_load_devtools_endpoint_from_profile_reads_port_and_ws_path -v`

Expected: PASS，上述两个新测试通过。

- [ ] **Step 5: 提交**

```bash
git add gateway/refresh_cookies.py tests/test_gateway_refresh_cookies.py
git commit -m "增加 remote debugging 启动约定"
```

### Task 2: 通过 CDP 读取 live cookie 并替换旧的 profile SQLite 读取路径

**Files:**
- Modify: `gateway/refresh_cookies.py`
- Modify: `tests/test_gateway_refresh_cookies.py`

- [ ] **Step 1: 先写 CDP 取 live cookie 和写回 `cookies.json` 的失败测试**

```python
    def test_load_browser_cookies_via_cdp_extracts_live_google_auth_cookies(self) -> None:
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

    def test_refresh_browser_cookies_to_file_uses_live_cdp_cookies(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cookies_path = Path(temp_dir) / "cookies.json"

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
                        "__Secure-1PSID": "live-psid",
                        "__Secure-1PSIDTS": "live-psidts",
                    },
                ),
            ):
                selection = refresh_browser_cookies_to_file(
                    cookies_path,
                    profile_dir=Path(temp_dir) / "selenium-profile",
                )

        payload = json.loads(cookies_path.read_text(encoding="utf-8"))
        self.assertEqual(selection.source, "manual-chrome-profile-cdp")
        self.assertEqual(payload["source"], "manual-chrome-profile-cdp")
        self.assertEqual(payload["cookies"]["__Secure-1PSID"], "live-psid")
        self.assertEqual(payload["cookies"]["__Secure-1PSIDTS"], "live-psidts")
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run python -m unittest tests.test_gateway_refresh_cookies.TestGatewayRefreshCookies.test_load_browser_cookies_via_cdp_extracts_live_google_auth_cookies tests.test_gateway_refresh_cookies.TestGatewayRefreshCookies.test_refresh_browser_cookies_to_file_uses_live_cdp_cookies -v`

Expected: FAIL，提示 `load_browser_cookies_via_cdp()` 不存在，或 `refresh_browser_cookies_to_file()` 仍然调用旧的 `load_browser_cookies_from_profile()`。

- [ ] **Step 3: 写最小实现并移除旧的 SQLite 读取主路径**

在 `gateway/refresh_cookies.py` 中新增 CDP 取 cookie 逻辑：

```python
from curl_cffi.requests import Session

DEFAULT_BROWSER_SOURCE = "manual-chrome-profile-cdp"


def _default_devtools_session_factory() -> Session:
    return Session()


def load_browser_cookies_via_cdp(
    *,
    endpoint: DevToolsEndpoint,
    session_factory: Callable[[], Any] | None = None,
) -> BrowserCookieSelection:
    session = session_factory() if session_factory is not None else _default_devtools_session_factory()
    websocket = None
    try:
        websocket = session.ws_connect(endpoint.browser_websocket_url)
        websocket.send_json({"id": 1, "method": "Storage.getCookies"})
        response = websocket.recv_json()
    except Exception as exc:
        raise BrowserCookieRefreshError(
            f"Failed to query Chrome DevTools cookies: {exc}"
        ) from exc
    finally:
        if websocket is not None:
            websocket.close()
        close = getattr(session, "close", None)
        if callable(close):
            close()

    result = response.get("result") if isinstance(response, dict) else None
    cookie_items = result.get("cookies") if isinstance(result, dict) else None
    if not isinstance(cookie_items, list):
        raise BrowserCookieRefreshError("Chrome DevTools 返回的 cookies 结构无效。")

    cookies = collect_google_cookies(cookie_items)
    if "__Secure-1PSID" not in cookies:
        raise BrowserCookieRefreshError(
            "已连接专用 Chrome profile，但未检测到有效 Gemini 登录态。",
            manual_login_required=True,
        )

    return BrowserCookieSelection(
        source=DEFAULT_BROWSER_SOURCE,
        cookies=cookies,
    )


def refresh_browser_cookies_to_file(
    cookies_path: str | Path,
    *,
    profile_dir: str | Path,
    url: str = MANUAL_GEMINI_URL,
    headless: bool = False,
    login_wait_seconds: int = 300,
    poll_interval_seconds: int = 2,
    page_load_timeout_seconds: int = 60,
    browser_binary: str | None = None,
    verbose: bool = False,
    print_summary: bool = True,
) -> BrowserCookieSelection:
    del headless
    del login_wait_seconds
    del poll_interval_seconds
    del page_load_timeout_seconds
    del browser_binary
    del verbose

    path = Path(cookies_path)
    endpoint = load_devtools_endpoint_from_profile(profile_dir)
    selection = load_browser_cookies_via_cdp(endpoint=endpoint)
    payload = {
        "cookies": dict(sorted(selection.cookies.items())),
        "updated_at": int(time.time()),
        "source": selection.source,
        "profile_dir": str(Path(profile_dir)),
        "url": url,
    }
    _atomic_write_json(path, payload)
    if print_summary:
        print(f"Browser cookies refreshed: {selection.summary()}")
    return selection
```

并删除或降级旧的 `load_browser_cookies_from_profile()` SQLite 路径，不再让它成为主流程入口；如果保留函数，也只作为内部兼容辅助，不能再被 `refresh_browser_cookies_to_file()` 直接调用。

- [ ] **Step 4: 运行测试并确认通过**

Run: `uv run python -m unittest tests.test_gateway_refresh_cookies -v`

Expected: PASS，刷新模块测试通过，且不再依赖“关闭 Chrome 再读 Cookies 数据库”的主路径。

- [ ] **Step 5: 提交**

```bash
git add gateway/refresh_cookies.py tests/test_gateway_refresh_cookies.py
git commit -m "改为通过 CDP 提取 live cookie"
```

### Task 3: 更新 CLI 引导、service 语义和 PowerShell 启动文案

**Files:**
- Modify: `gateway/refresh_cookies.py`
- Modify: `gateway/service.py`
- Modify: `tests/test_gateway_refresh_cookies.py`
- Modify: `tests/test_gateway_service_lifecycle.py`
- Modify: `gateway/start_gateway.ps1`
- Modify: `gateway/set_gateway_env.ps1`
- Modify: `gateway/README.md`
- Modify: `tests/test_gateway_uv_startup_docs.py`

- [ ] **Step 1: 先写新的 CLI 引导与 service 兼容测试**

```python
    def test_main_prints_remote_debugging_guidance_when_session_is_missing(self) -> None:
        stdout = StringIO()
        stderr = StringIO()

        with (
            patch(
                "gateway.refresh_cookies.refresh_browser_cookies_to_file",
                side_effect=BrowserCookieRefreshError(
                    "未检测到专用 Chrome profile 的可用 remote debugging 会话。",
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
        self.assertIn("未检测到专用 Chrome profile 的可用 remote debugging 会话", stderr.getvalue())
        self.assertIn("--remote-debugging-port=0", stdout.getvalue())
        self.assertIn("不要关闭窗口", stdout.getvalue())
        self.assertIn("uv run --extra browser python -m gateway.refresh_cookies", stdout.getvalue())

    def test_refresh_cookies_from_browser_returns_false_when_debugging_session_is_missing(self) -> None:
        settings = GatewaySettings(
            api_key="test-key",
            proxy="http://127.0.0.1:7890",
            cookies_json_path=str(self.cookies_path),
            browser_cookie_refresh_enabled=True,
        )
        service = GatewayService(settings)

        with patch(
            "gateway.refresh_cookies.refresh_browser_cookies_to_file",
            side_effect=BrowserCookieRefreshError(
                "未检测到专用 Chrome profile 的可用 remote debugging 会话。",
                manual_login_required=True,
                debugging_session_required=True,
            ),
        ), patch("builtins.print") as print_mock:
            refreshed = service.refresh_cookies_from_browser()

        self.assertFalse(refreshed)
        print_mock.assert_any_call(
            "Warning: browser cookies require manual profile login: "
            "未检测到专用 Chrome profile 的可用 remote debugging 会话。"
        )
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run python -m unittest tests.test_gateway_refresh_cookies.TestGatewayRefreshCookies.test_main_prints_remote_debugging_guidance_when_session_is_missing tests.test_gateway_service_lifecycle.TestGatewayServiceLifecycle.test_refresh_cookies_from_browser_returns_false_when_debugging_session_is_missing -v`

Expected: FAIL，提示 CLI 仍输出“关闭 Chrome 再运行”的旧文案，或 `refresh_cookies_from_browser()` 尚未覆盖新的 `debugging_session_required` 语义。

- [ ] **Step 3: 实现新的中文引导与最小 service 调整**

在 `gateway/refresh_cookies.py` 中将引导文案更新为：

```python
def print_manual_login_guidance(
    *,
    profile_dir: str | Path,
    url: str = MANUAL_GEMINI_URL,
    debugging_session_required: bool = False,
) -> None:
    if debugging_session_required:
        print("未检测到专用 Chrome profile 的可用 remote debugging 会话。")
        print("请复制下面的 PowerShell 命令手动启动专用 Chrome profile，并保持该窗口运行。")
    else:
        print("已连接专用 Chrome profile，但未检测到有效 Gemini 登录态。")
        print("请在该专用 Chrome 窗口中确认已登录 Gemini。")
    print("")
    print(build_manual_chrome_launch_command(profile_dir, url=url))
    print("")
    print("在打开的专用 Chrome 中完成 Gemini 登录后，不要关闭窗口，再重新执行：")
    print("uv run --extra browser python -m gateway.refresh_cookies")


def main(argv: list[str] | None = None) -> int:
    ...
    try:
        refresh_browser_cookies_to_file(
            cookies_path,
            profile_dir=profile_dir,
            url=args.url,
            headless=headless,
            login_wait_seconds=login_wait_seconds,
            poll_interval_seconds=poll_interval_seconds,
            page_load_timeout_seconds=page_load_timeout_seconds,
            browser_binary=args.browser_binary,
            verbose=args.verbose,
        )
    except BrowserCookieRefreshError as exc:
        if exc.manual_login_required:
            print_manual_login_guidance(
                profile_dir=profile_dir,
                url=args.url,
                debugging_session_required=exc.debugging_session_required,
            )
        print(str(exc), file=sys.stderr)
        return 1
```

在 `gateway/service.py` 中保留现有刷新入口，仅把调用压缩到仍然实际使用的参数集合，并继续把所有 `manual_login_required` 情况视为可恢复告警：

```python
    def refresh_cookies_from_browser(self) -> bool:
        if not self.settings.browser_cookie_refresh_enabled:
            return False

        try:
            from gateway.refresh_cookies import (
                BrowserCookieRefreshError,
                refresh_browser_cookies_to_file,
            )

            selection = refresh_browser_cookies_to_file(
                self.settings.cookies_json_path,
                profile_dir=self.settings.browser_profile_dir,
                print_summary=False,
            )
        except BrowserCookieRefreshError as exc:
            if exc.manual_login_required:
                print(
                    "Warning: browser cookies require manual profile login: "
                    f"{exc}"
                )
            else:
                print(f"Warning: failed to refresh browser cookies: {exc}")
            return False
        except Exception as exc:
            print(f"Warning: failed to refresh browser cookies: {exc}")
            return False

        self._cached_cookies = dict(selection.cookies)
        return True
```

- [ ] **Step 4: 更新 README 和 PowerShell 脚本文案**

在 `gateway/README.md` 中把以下旧表述：

```markdown
登录完成后，关闭该专用 profile 的 Chrome 窗口，再重新运行刷新命令。
```

替换为：

```markdown
登录完成后，不要关闭该专用 profile 的 Chrome 窗口，直接重新运行刷新命令。
当前实现通过 remote debugging 从运行中的专用 Chrome 会话提取 live cookie，因此不再依赖关闭 Chrome 后读取 profile SQLite 数据库。
```

并把推荐顺序中的第 5-6 步更新为：

```markdown
5. 保持该专用 Chrome 窗口继续运行
6. 再次执行 `uv run --extra browser python -m gateway.refresh_cookies`
```

在 `gateway/set_gateway_env.ps1` 中将尾部提示更新为：

```powershell
Write-Host "Recommended next steps:"
Write-Host "  uv sync --extra browser"
Write-Host "  uv run --extra browser python -m gateway.refresh_cookies"
Write-Host "  如脚本打印专用 Chrome 启动命令，请复制运行并保持该窗口继续运行"
Write-Host "  登录 Gemini 后，不要关闭该专用 Chrome，再重新执行 refresh_cookies"
Write-Host "  uv run python -m gateway.main"
```

在 `gateway/start_gateway.ps1` 头部注释更新为：

```powershell
# If refresh_cookies prints a dedicated Chrome launch command, run it manually,
# sign in to Gemini in that profile, keep the window running, and rerun this script.
```

并在 `tests/test_gateway_uv_startup_docs.py` 中把断言从“关闭该专用 profile 的 Chrome 窗口”改成“保持该专用 Chrome 窗口继续运行”。

- [ ] **Step 5: 运行测试并确认通过**

Run: `uv run python -m unittest tests.test_gateway_refresh_cookies tests.test_gateway_service_lifecycle tests.test_gateway_uv_startup_docs -v`

Expected: PASS，CLI 引导、service 恢复语义和 README / PowerShell 文案全部通过。

- [ ] **Step 6: 提交**

```bash
git add gateway/refresh_cookies.py gateway/service.py gateway/README.md gateway/start_gateway.ps1 gateway/set_gateway_env.ps1 tests/test_gateway_refresh_cookies.py tests/test_gateway_service_lifecycle.py tests/test_gateway_uv_startup_docs.py
git commit -m "更新 live cookie 启动引导文案"
```

### Task 4: 做回归验证并确认 V1.5 不影响运行期自动续期

**Files:**
- No source changes unless verification reveals a defect

- [ ] **Step 1: 搜索仓库中残留的“关闭 Chrome 才能刷新”旧表述**

Run: `rg -n "关闭该专用 profile 的 Chrome 窗口|关闭 Chrome|Cookies 数据库|Permission denied" gateway tests docs/superpowers/plans/2026-06-01-gemini-openai-gateway-v1.5-remote-debugging-live-cookie-bootstrap.md -S`

Expected: `gateway/README.md`、`gateway/start_gateway.ps1`、`gateway/set_gateway_env.ps1` 和测试断言中不再把“关闭 Chrome 后读数据库”作为推荐路径；计划文档中出现旧路径描述仅限于迁移说明。

- [ ] **Step 2: 运行核心测试集**

Run: `uv run python -m unittest tests.test_gateway_refresh_cookies tests.test_gateway_service_lifecycle tests.test_gateway_uv_startup_docs tests.test_gateway_api tests.test_gateway_account_status tests.test_gateway_config -v`

Expected: PASS

- [ ] **Step 3: 运行编译检查**

Run: `uv run python -m compileall gateway`

Expected: PASS

- [ ] **Step 4: 运行 diff 空白检查**

Run: `git diff --check`

Expected: 无 trailing whitespace、无 conflict marker。

- [ ] **Step 5: 本机人工验证 remote debugging live-cookie 链路**

Run:

```powershell
uv sync --extra browser
. .\gateway\set_gateway_env.ps1 -ApiKey "gemini-api"
uv run --extra browser python -m gateway.refresh_cookies
```

Expected:

```text
未检测到专用 Chrome profile 的可用 remote debugging 会话。
<完整可复制的 PowerShell 启动命令，其中包含 --remote-debugging-port=0>
在打开的专用 Chrome 中完成 Gemini 登录后，不要关闭窗口，再重新执行：
uv run --extra browser python -m gateway.refresh_cookies
```

随后：

1. 复制并运行脚本输出的 PowerShell 命令
2. 在专用 Chrome profile 中手动登录 Gemini
3. 保持该专用 Chrome 窗口继续运行
4. 再次执行 `uv run --extra browser python -m gateway.refresh_cookies`

Expected:

```text
Browser cookies refreshed: source=manual-chrome-profile-cdp, has_1psid=true, has_1psidts=true, count=<n>
```

- [ ] **Step 6: 本机人工验证网关启动与运行期自动续期不回归**

Run:

```powershell
uv run python -m gateway.main
```

另开 PowerShell：

```powershell
curl http://127.0.0.1:8010/health
curl http://127.0.0.1:8010/v1/account/status -H "Authorization: Bearer gemini-api"
```

Expected:

- `/health` 返回 `{"status":"ok"}`
- `/v1/account/status` 返回账户状态 JSON
- 启动后 `Gemini-API` 仍按现有机制自动刷新运行期 Cookie，`gateway` 继续通过已有持久化与上游缓存同步机制消费更新结果

- [ ] **Step 7: 提交最终验证修正**

如果 Step 1-6 没有产生新的文件改动，跳过本步骤。若验证发现缺陷并修复，提交：

```bash
git add <changed-files>
git commit -m "修正 live cookie 引导回归问题"
```

## Self-Review Checklist

- Spec coverage: Task 1 覆盖 remote debugging 启动命令与 DevToolsActivePort；Task 2 覆盖 CDP live-cookie 提取与 `cookies.json` 写回；Task 3 覆盖 CLI / service / README / PowerShell 文案；Task 4 覆盖自动续期不回归的测试与人工验证。
- Placeholder scan: 计划中没有 `TODO`、`TBD`、`后续补上`、`适当处理异常` 之类占位语句；每个任务都给出了具体文件、测试、命令和预期结果。
- Type consistency: 计划统一使用 `DevToolsEndpoint`、`load_devtools_endpoint_from_profile()`、`load_browser_cookies_via_cdp()`、`debugging_session_required` 和 `manual-chrome-profile-cdp` 作为命名与语义。
