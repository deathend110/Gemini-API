# Gemini OpenAI 网关 V1.4 手动专用 Profile 登录与 Cookie 同步 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `gateway.refresh_cookies` 从 Selenium 自动化登录路径切换为“打印手动启动专用 Chrome profile 命令 + 从专用 profile 读取 Cookie + 写回 `cookies.json`”。

**Architecture:** 保留 V1.3 的 `refresh_browser_cookies_to_file()`、shared client rebuild 和运行期 Cookie 持久化骨架，只替换启动前登录链路。`gateway.refresh_cookies` 负责构建手动启动命令、输出中文引导，并使用本地 profile Cookie 文件完成同步；`gateway.service` 继续调用相同入口，但在失败时输出更明确的“需要手动登录”语义。

**Tech Stack:** Python 3.10+、FastAPI、browser-cookie3、unittest、unittest.mock、PowerShell、uv

---

## 文件结构

- Modify: `pyproject.toml`
  - 将 `browser` extra 从 Selenium 自动化依赖调整为手动 profile Cookie 读取依赖
- Modify: `gateway/refresh_cookies.py`
  - 新增手动启动命令生成、中文引导输出、本地 profile Cookie 读取和新的错误语义
- Modify: `gateway/service.py`
  - 在认证失败刷新路径上保留原流程，但输出更明确的手动登录提示
- Modify: `gateway/README.md`
  - 将 V1.3 的 Selenium 登录说明改为 V1.4 的手动 profile 登录说明
- Modify: `gateway/set_gateway_env.ps1`
  - 更新启动提示文案，强调复制 `refresh_cookies` 打印的完整 PowerShell 命令
- Modify: `gateway/start_gateway.ps1`
  - 保留一键启动顺序，但提示用户在未登录时按 `refresh_cookies` 输出完成手动登录
- Modify: `tests/test_gateway_refresh_cookies.py`
  - 覆盖手动启动命令、中文引导、profile Cookie 读取、写回 `cookies.json` 和 CLI 失败路径
- Modify: `tests/test_gateway_service_lifecycle.py`
  - 覆盖 service 对“需要手动登录”语义的处理
- Modify: `tests/test_gateway_uv_startup_docs.py`
  - 覆盖 README 与 PowerShell 脚本的 V1.4 文案

### Task 1: 将 `gateway.refresh_cookies` 切换为手动 profile Cookie 读取

**Files:**
- Modify: `pyproject.toml`
- Modify: `gateway/refresh_cookies.py`
- Modify: `tests/test_gateway_refresh_cookies.py`

- [ ] **Step 1: 写手动命令和本地 profile 读取的失败测试**

```python
    def test_build_manual_chrome_launch_command_uses_profile_dir_and_gemini_url(self) -> None:
        command = build_manual_chrome_launch_command(
            Path(r"C:\Users\27355\.gemini-api\selenium-profile")
        )

        self.assertIn('${env:ProgramFiles}\\Google\\Chrome\\Application\\chrome.exe', command)
        self.assertIn('--user-data-dir="C:\\Users\\27355\\.gemini-api\\selenium-profile"', command)
        self.assertIn('--profile-directory="Default"', command)
        self.assertIn('"https://gemini.google.com/app"', command)

    def test_load_browser_cookies_from_profile_uses_explicit_cookie_and_state_files(self) -> None:
        captured: dict[str, object] = {}

        class FakeCookie:
            def __init__(self, name: str, value: str, domain: str) -> None:
                self.name = name
                self.value = value
                self.domain = domain
                self.path = "/"
                self.expires = None

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
        self.assertEqual(selection.source, "manual-chrome-profile")
        self.assertEqual(selection.cookies["__Secure-1PSID"], "psid")
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run python -m unittest tests.test_gateway_refresh_cookies.TestGatewayRefreshCookies.test_build_manual_chrome_launch_command_uses_profile_dir_and_gemini_url tests.test_gateway_refresh_cookies.TestGatewayRefreshCookies.test_load_browser_cookies_from_profile_uses_explicit_cookie_and_state_files -v`

Expected: FAIL，提示 `build_manual_chrome_launch_command` 或 `load_browser_cookies_from_profile` 未定义。

- [ ] **Step 3: 写最小实现并补浏览器依赖**

在 `pyproject.toml` 中将：

```toml
[project.optional-dependencies]
browser = ["selenium>=4.34.0,<5"]
```

替换为：

```toml
[project.optional-dependencies]
browser = ["browser-cookie3>=0.19.1,<0.20"]
```

在 `gateway/refresh_cookies.py` 中加入并替换读取路径：

```python
MANUAL_GEMINI_URL = "https://gemini.google.com/app"
DEFAULT_BROWSER_SOURCE = "manual-chrome-profile"


def build_manual_chrome_launch_command(profile_dir: str | Path, url: str = MANUAL_GEMINI_URL) -> str:
    resolved_profile_dir = str(Path(profile_dir))
    return (
        '$Chrome = "${env:ProgramFiles}\\Google\\Chrome\\Application\\chrome.exe"; '
        'if (-not (Test-Path $Chrome)) { '
        '$Chrome = "${env:ProgramFiles(x86)}\\Google\\Chrome\\Application\\chrome.exe" '
        '}; '
        f'& $Chrome --user-data-dir="{resolved_profile_dir}" '
        '--profile-directory="Default" '
        f'"{url}"'
    )


def _profile_cookie_file(profile_dir: str | Path) -> Path:
    return Path(profile_dir) / "Default" / "Network" / "Cookies"


def _profile_local_state_file(profile_dir: str | Path) -> Path:
    return Path(profile_dir) / "Local State"


def load_browser_cookies_from_profile(
    *,
    profile_dir: str | Path,
    domain_name: str = ".google.com",
    browser_loader: Callable[..., Any] | None = None,
) -> BrowserCookieSelection:
    if browser_loader is None:
        try:
            import browser_cookie3 as bc3
        except ImportError as exc:
            raise BrowserCookieRefreshError(
                "browser-cookie3 is not installed. Run: uv sync --extra browser"
            ) from exc
        browser_loader = bc3.chrome

    cookie_file = _profile_cookie_file(profile_dir)
    key_file = _profile_local_state_file(profile_dir)
    jar = browser_loader(
        cookie_file=str(cookie_file),
        domain_name=domain_name,
        key_file=str(key_file),
    )
    cookies = collect_google_cookies(
        [
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
            }
            for cookie in jar
            if not cookie.is_expired()
        ]
    )
    if "__Secure-1PSID" not in cookies:
        raise BrowserCookieRefreshError("manual-login-required")
    return BrowserCookieSelection(source=DEFAULT_BROWSER_SOURCE, cookies=cookies)
```

- [ ] **Step 4: 运行测试并确认通过**

Run: `uv run python -m unittest tests.test_gateway_refresh_cookies.TestGatewayRefreshCookies.test_build_manual_chrome_launch_command_uses_profile_dir_and_gemini_url tests.test_gateway_refresh_cookies.TestGatewayRefreshCookies.test_load_browser_cookies_from_profile_uses_explicit_cookie_and_state_files -v`

Expected: PASS，上述两个新测试通过。

- [ ] **Step 5: 提交**

```bash
git add pyproject.toml gateway/refresh_cookies.py tests/test_gateway_refresh_cookies.py
git commit -m "切换手动 profile Cookie 读取"
```

### Task 2: 增加中文引导和 CLI 手动登录失败路径

**Files:**
- Modify: `gateway/refresh_cookies.py`
- Modify: `tests/test_gateway_refresh_cookies.py`

- [ ] **Step 1: 写中文引导和 CLI 失败路径的失败测试**

```python
    def test_print_manual_login_guidance_outputs_copyable_pwsh_command(self) -> None:
        stdout = StringIO()

        with redirect_stdout(stdout):
            print_manual_login_guidance(
                profile_dir=Path(r"C:\Users\27355\.gemini-api\selenium-profile"),
                url="https://gemini.google.com/app",
            )

        output = stdout.getvalue()
        self.assertIn("未检测到专用 Chrome profile 中的有效 Gemini 登录态", output)
        self.assertIn("Google 可能会阻止由自动化框架控制的 Chrome 登录账号", output)
        self.assertIn("--user-data-dir=\"C:\\Users\\27355\\.gemini-api\\selenium-profile\"", output)
        self.assertIn("uv run --extra browser python -m gateway.refresh_cookies", output)

    def test_main_prints_manual_login_guidance_when_profile_not_logged_in(self) -> None:
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
            exit_code = main(["--profile-dir", r"C:\Users\27355\.gemini-api\selenium-profile"])

        self.assertEqual(exit_code, 1)
        self.assertIn("未检测到专用 Chrome profile 中的有效 Gemini 登录态", stderr.getvalue())
        self.assertIn("Google 可能会阻止由自动化框架控制的 Chrome 登录账号", stdout.getvalue())
        self.assertIn("--user-data-dir=\"C:\\Users\\27355\\.gemini-api\\selenium-profile\"", stdout.getvalue())
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run python -m unittest tests.test_gateway_refresh_cookies.TestGatewayRefreshCookies.test_print_manual_login_guidance_outputs_copyable_pwsh_command tests.test_gateway_refresh_cookies.TestGatewayRefreshCookies.test_main_prints_manual_login_guidance_when_profile_not_logged_in -v`

Expected: FAIL，提示 `print_manual_login_guidance` 不存在，或 `BrowserCookieRefreshError` 不支持 `manual_login_required`。

- [ ] **Step 3: 实现错误语义、中文引导和 CLI 输出**

在 `gateway/refresh_cookies.py` 中调整异常类并补充引导逻辑：

```python
class BrowserCookieRefreshError(Exception):
    def __init__(self, message: str, *, manual_login_required: bool = False) -> None:
        super().__init__(message)
        self.manual_login_required = manual_login_required


def print_manual_login_guidance(
    *,
    profile_dir: str | Path,
    url: str = MANUAL_GEMINI_URL,
) -> None:
    print("未检测到专用 Chrome profile 中的有效 Gemini 登录态。")
    print("Google 可能会阻止由自动化框架控制的 Chrome 登录账号，因此请先手动启动专用 profile 并完成 Gemini 登录。")
    print("")
    print(build_manual_chrome_launch_command(profile_dir, url=url))
    print("")
    print("请复制上面的 PowerShell 命令并手动运行。")
    print("在打开的专用 Chrome 中完成 Gemini 登录后，再重新执行：")
    print("uv run --extra browser python -m gateway.refresh_cookies")


def load_browser_cookies_from_profile(
    *,
    profile_dir: str | Path,
    domain_name: str = ".google.com",
    browser_loader: Callable[..., Any] | None = None,
) -> BrowserCookieSelection:
    if "__Secure-1PSID" not in cookies:
        raise BrowserCookieRefreshError(
            "未检测到专用 Chrome profile 中的有效 Gemini 登录态。",
            manual_login_required=True,
        )
    return BrowserCookieSelection(source=DEFAULT_BROWSER_SOURCE, cookies=cookies)


def refresh_browser_cookies_to_file(
    cookies_path: str | Path,
    *,
    profile_dir: str | Path,
    url: str = MANUAL_GEMINI_URL,
    print_summary: bool = True,
) -> BrowserCookieSelection:
    selection = load_browser_cookies_from_profile(profile_dir=profile_dir)
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


def main(argv: list[str] | None = None) -> int:
    from gateway.config import GatewaySettings

    parser = argparse.ArgumentParser(
        description="Refresh Gemini gateway cookies from a manually signed-in dedicated Chrome profile.",
    )
    parser.add_argument("--cookies-path", default=None)
    parser.add_argument("--profile-dir", default=None)
    parser.add_argument("--url", default=MANUAL_GEMINI_URL)
    args = parser.parse_args(argv)

    settings = GatewaySettings()
    cookies_path = args.cookies_path or settings.cookies_json_path
    profile_dir = args.profile_dir or settings.browser_profile_dir
    try:
        refresh_browser_cookies_to_file(
            cookies_path,
            profile_dir=profile_dir,
            url=args.url,
        )
    except BrowserCookieRefreshError as exc:
        if exc.manual_login_required:
            print_manual_login_guidance(profile_dir=profile_dir, url=args.url)
        print(str(exc), file=sys.stderr)
        return 1
```

并在测试文件顶部增加：

```python
from contextlib import redirect_stderr, redirect_stdout
```

- [ ] **Step 4: 运行测试并确认通过**

Run: `uv run python -m unittest tests.test_gateway_refresh_cookies -v`

Expected: PASS，刷新模块测试全部通过，且不再依赖 Selenium 测试路径。

- [ ] **Step 5: 提交**

```bash
git add gateway/refresh_cookies.py tests/test_gateway_refresh_cookies.py
git commit -m "新增手动 profile 登录引导"
```

### Task 3: 保持 `gateway.service` 恢复路径可用并输出明确语义

**Files:**
- Modify: `gateway/service.py`
- Modify: `tests/test_gateway_service_lifecycle.py`

- [ ] **Step 1: 写 service 层手动登录语义的失败测试**

```python
    async def test_refresh_cookies_from_browser_updates_cached_cookies_from_manual_profile(self) -> None:
        settings = GatewaySettings(
            api_key="test-key",
            proxy="http://127.0.0.1:7890",
            cookies_json_path=str(self.cookies_path),
            browser_cookie_refresh_enabled=True,
        )
        service = GatewayService(settings)

        with patch(
            "gateway.refresh_cookies.refresh_browser_cookies_to_file",
            return_value=SimpleNamespace(
                source="manual-chrome-profile",
                cookies={"__Secure-1PSID": "new-psid", "__Secure-1PSIDTS": "new-psidts"},
            ),
        ):
            refreshed = service.refresh_cookies_from_browser()

        self.assertTrue(refreshed)
        self.assertEqual(service.get_cached_cookies()["__Secure-1PSID"], "new-psid")
        self.assertEqual(service.get_cached_cookies()["__Secure-1PSIDTS"], "new-psidts")

    async def test_refresh_cookies_from_browser_returns_false_when_manual_login_is_required(self) -> None:
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
                "未检测到专用 Chrome profile 中的有效 Gemini 登录态。",
                manual_login_required=True,
            ),
        ), patch("builtins.print") as print_mock:
            refreshed = service.refresh_cookies_from_browser()

        self.assertFalse(refreshed)
        print_mock.assert_any_call(
            "Warning: browser cookies require manual profile login: 未检测到专用 Chrome profile 中的有效 Gemini 登录态。"
        )
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run python -m unittest tests.test_gateway_service_lifecycle -v`

Expected: FAIL，提示旧实现没有区分 `manual_login_required` 语义。

- [ ] **Step 3: 实现 service 层的语义分支**

在 `gateway/service.py` 中将 `refresh_cookies_from_browser()` 调整为：

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

- [ ] **Step 4: 运行测试并确认通过**

Run: `uv run python -m unittest tests.test_gateway_service_lifecycle -v`

Expected: PASS，现有 rebuild / persist 测试继续通过，新测试通过。

- [ ] **Step 5: 提交**

```bash
git add gateway/service.py tests/test_gateway_service_lifecycle.py
git commit -m "明确手动 profile 登录恢复语义"
```

### Task 4: 更新 README 与 PowerShell 启动提示为 V1.4 手动登录链路

**Files:**
- Modify: `gateway/README.md`
- Modify: `gateway/set_gateway_env.ps1`
- Modify: `gateway/start_gateway.ps1`
- Modify: `tests/test_gateway_uv_startup_docs.py`

- [ ] **Step 1: 写文档和脚本提示的失败测试**

```python
    def test_gateway_readme_documents_manual_profile_login(self) -> None:
        readme = (ROOT / "gateway" / "README.md").read_text(encoding="utf-8")

        self.assertIn("手动启动专用 Chrome profile", readme)
        self.assertIn("复制 `gateway.refresh_cookies` 输出的完整 PowerShell 命令", readme)
        self.assertNotIn("首次运行会打开一个独立 Chrome profile", readme)
        self.assertNotIn("Selenium", readme)

    def test_gateway_env_script_mentions_manual_profile_guidance(self) -> None:
        script = (ROOT / "gateway" / "set_gateway_env.ps1").read_text(encoding="utf-8")

        self.assertIn("如未登录 Gemini，请先运行 refresh_cookies 并复制其输出的 PowerShell 命令", script)
        self.assertIn("uv run --extra browser python -m gateway.refresh_cookies", script)

    def test_start_gateway_script_keeps_refresh_step_before_gateway_start(self) -> None:
        script = (ROOT / "gateway" / "start_gateway.ps1").read_text(encoding="utf-8")

        self.assertIn("uv sync --extra browser", script)
        self.assertIn("uv run --extra browser python -m gateway.refresh_cookies", script)
        self.assertIn("uv run python -m gateway.main", script)
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run python -m unittest tests.test_gateway_uv_startup_docs -v`

Expected: FAIL，提示 README 仍包含 Selenium 推荐路径。

- [ ] **Step 3: 更新 README 与 PowerShell 文案**

在 `gateway/README.md` 中将启动前刷新说明替换为：

````markdown
如果需要刷新 `cookies.json`，推荐使用手动专用 Chrome profile 登录流程。
首次运行 `gateway.refresh_cookies` 时，如果未检测到有效 Gemini 登录态，脚本会打印一条完整可复制的 PowerShell 命令。请复制该命令，手动打开专用 Chrome profile 并完成 Gemini 登录，然后重新运行刷新命令。

```powershell
uv sync --extra browser
. .\gateway\set_gateway_env.ps1 -ApiKey "your-local-key"
uv run --extra browser python -m gateway.refresh_cookies
uv run python -m gateway.main
```
````

在 `gateway/set_gateway_env.ps1` 中将结尾提示替换为：

```powershell
Write-Host "Recommended next steps:"
Write-Host "  uv sync --extra browser"
Write-Host "  uv run --extra browser python -m gateway.refresh_cookies"
Write-Host "  如未登录 Gemini，请先运行 refresh_cookies 并复制其输出的 PowerShell 命令"
Write-Host "  uv run python -m gateway.main"
```

在 `gateway/start_gateway.ps1` 中保留三条 `uv` 命令不变，只在文件头部注释增加：

```powershell
# If refresh_cookies reports that manual login is required, copy the printed
# PowerShell command, sign in to Gemini in the dedicated profile, then rerun this script.
```

- [ ] **Step 4: 运行测试并确认通过**

Run: `uv run python -m unittest tests.test_gateway_uv_startup_docs -v`

Expected: PASS，文档测试全部通过。

- [ ] **Step 5: 提交**

```bash
git add gateway/README.md gateway/set_gateway_env.ps1 gateway/start_gateway.ps1 tests/test_gateway_uv_startup_docs.py
git commit -m "更新 V1.4 手动 profile 登录说明"
```

### Task 5: 最终验证与回归检查

**Files:**
- No source changes unless verification reveals a defect

- [ ] **Step 1: 搜索过时 Selenium 登录描述**

Run: `rg -n "Selenium|自动化登录|受控 Chrome" gateway tests docs\\superpowers\\plans\\2026-06-01-gemini-openai-gateway-v1.4-manual-profile-login.md -S`

Expected: `V1.4` README 与脚本中不再把 Selenium 作为推荐登录路径；spec / plan 中保留历史描述属于预期。

- [ ] **Step 2: 运行核心测试集**

Run: `uv run python -m unittest tests.test_gateway_refresh_cookies tests.test_gateway_service_lifecycle tests.test_gateway_uv_startup_docs tests.test_gateway_api tests.test_gateway_account_status tests.test_gateway_config -v`

Expected: PASS

- [ ] **Step 3: 运行编译检查**

Run: `uv run python -m compileall gateway`

Expected: PASS

- [ ] **Step 4: 运行 diff 空白检查**

Run: `git diff --check`

Expected: 无 trailing whitespace 或 conflict marker。

- [ ] **Step 5: 本机人工验证手动登录链路**

Run:

```powershell
uv sync --extra browser
. .\gateway\set_gateway_env.ps1 -ApiKey "gemini-api"
uv run --extra browser python -m gateway.refresh_cookies
```

Expected:

```text
未检测到专用 Chrome profile 中的有效 Gemini 登录态。
Google 可能会阻止由自动化框架控制的 Chrome 登录账号，因此请先手动启动专用 profile 并完成 Gemini 登录。
<完整可复制的 PowerShell 启动命令>
```

随后：

1. 复制并运行脚本输出的 PowerShell 命令
2. 在专用 Chrome profile 中手动登录 Gemini
3. 再次执行 `uv run --extra browser python -m gateway.refresh_cookies`

Expected:

```text
Browser cookies refreshed: source=manual-chrome-profile, has_1psid=true, has_1psidts=<true|false>, count=<n>
```

- [ ] **Step 6: 本机人工验证网关启动**

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

- [ ] **Step 7: 提交最终验证修正**

如果 Step 1-6 没有产生新的文件改动，跳过本步骤。若验证发现缺陷并修复，提交：

```bash
git add <changed-files>
git commit -m "修正 V1.4 手动 profile 登录验证问题"
```

## Self-Review Checklist

- Spec coverage: Task 1 覆盖手动 profile Cookie 读取与浏览器依赖；Task 2 覆盖中文引导、完整 PowerShell 命令和 CLI 失败路径；Task 3 覆盖 service 恢复语义；Task 4 覆盖 README 与 PowerShell 启动说明；Task 5 覆盖测试、编译和本机人工验证。
- Placeholder scan: 计划中没有 `TODO`、`TBD`、`适当处理` 之类占位语句；每个任务都给出了文件、代码片段、命令和预期结果。
- Type consistency: 计划统一使用 `build_manual_chrome_launch_command()`、`print_manual_login_guidance()`、`load_browser_cookies_from_profile()`、`BrowserCookieRefreshError(manual_login_required=...)` 和 `manual-chrome-profile` 作为命名与语义。
