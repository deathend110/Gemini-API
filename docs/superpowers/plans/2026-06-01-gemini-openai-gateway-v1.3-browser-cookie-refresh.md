# Gemini OpenAI 网关 V1.3 浏览器 Cookie 同步与续期 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 gateway 增加启动前浏览器 Cookie 同步、运行期 Cookie 周期性持久化、认证失败时可选浏览器兜底恢复，减少手动维护 `cookies.json` 的频率。

**Architecture:** 维持 OpenAI-compatible API 不变，新增独立 `gateway.refresh_cookies` 模块负责浏览器 Cookie 选择、脱敏摘要和原子写入；`GatewayService` 继续以共享 `GeminiClient` 为运行核心，并在生命周期中启动独立后台持久化任务。认证失败恢复路径只在显式配置开启时触发一次浏览器同步，避免无浏览器环境中反复阻塞请求。

**Tech Stack:** Python 3.10+、FastAPI、gemini_webapi、browser-cookie3 optional extra、PowerShell、unittest、unittest.mock、uv

---

## 文件结构

- Create: `gateway/refresh_cookies.py`
  - 浏览器 Cookie 读取、候选来源选择、脱敏摘要、原子写入和 CLI 入口
- Modify: `gateway/config.py`
  - 增加浏览器 Cookie 刷新配置项
- Modify: `gateway/service.py`
  - 增加运行期 Cookie 持久化任务与认证失败浏览器兜底恢复
- Modify: `gateway/main.py`
  - 在 lifespan 中启动和停止 Cookie 持久化任务
- Modify: `gateway/set_gateway_env.ps1`
  - 增加浏览器 Cookie 刷新相关环境变量与启动提示
- Create: `gateway/start_gateway.ps1`
  - 可选一键启动：配置环境、同步依赖、刷新 Cookie、启动 gateway
- Modify: `gateway/README.md`
  - 增加 V1.3 启动前 Cookie 刷新、运行期续期与 AstrBot 接入说明
- Create: `tests/test_gateway_refresh_cookies.py`
  - Cookie 来源选择、写入格式、CLI 错误处理、脱敏输出测试
- Modify: `tests/test_gateway_account_status.py`
  - 新增配置默认值与环境变量解析测试
- Modify: `tests/test_gateway_service_lifecycle.py`
  - 后台持久化任务、认证失败恢复路径测试
- Modify: `tests/test_gateway_api.py`
  - lifespan 启停持久化任务测试
- Modify: `tests/test_gateway_uv_startup_docs.py`
  - 文档和 PowerShell 启动提示测试

### Task 1: 新增浏览器 Cookie 刷新核心模块

**Files:**
- Create: `gateway/refresh_cookies.py`
- Create: `tests/test_gateway_refresh_cookies.py`

- [ ] **Step 1: 写 Cookie 来源选择失败测试**

```python
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from gateway.refresh_cookies import (
    BrowserCookieRefreshError,
    choose_cookie_source,
    refresh_browser_cookies_to_file,
)
from gateway.service import GatewayService
from gateway.config import GatewaySettings


class TestGatewayRefreshCookies(unittest.TestCase):
    def test_choose_cookie_source_prefers_psid_and_psidts(self) -> None:
        browser_cookies = {
            "chrome": [
                {"name": "__Secure-1PSID", "value": "chrome-psid", "domain": ".google.com"},
            ],
            "edge": [
                {"name": "__Secure-1PSID", "value": "edge-psid", "domain": ".google.com"},
                {"name": "__Secure-1PSIDTS", "value": "edge-psidts", "domain": ".google.com"},
            ],
        }

        selected = choose_cookie_source(browser_cookies)

        self.assertEqual(selected.source, "edge")
        self.assertEqual(selected.cookies["__Secure-1PSID"], "edge-psid")
        self.assertEqual(selected.cookies["__Secure-1PSIDTS"], "edge-psidts")

    def test_choose_cookie_source_accepts_psid_without_psidts(self) -> None:
        selected = choose_cookie_source(
            {
                "chrome": [
                    {"name": "__Secure-1PSID", "value": "chrome-psid", "domain": ".google.com"},
                    {"name": "NID", "value": "nid-value", "domain": ".google.com"},
                ]
            }
        )

        self.assertEqual(selected.source, "chrome")
        self.assertTrue(selected.has_1psid)
        self.assertFalse(selected.has_1psidts)

    def test_choose_cookie_source_honors_requested_source(self) -> None:
        selected = choose_cookie_source(
            {
                "chrome": [
                    {"name": "__Secure-1PSID", "value": "chrome-psid", "domain": ".google.com"},
                ],
                "edge": [
                    {"name": "__Secure-1PSID", "value": "edge-psid", "domain": ".google.com"},
                    {"name": "__Secure-1PSIDTS", "value": "edge-psidts", "domain": ".google.com"},
                ],
            },
            requested_source="chrome",
        )

        self.assertEqual(selected.source, "chrome")
        self.assertEqual(selected.cookies["__Secure-1PSID"], "chrome-psid")

    def test_choose_cookie_source_raises_without_psid(self) -> None:
        with self.assertRaisesRegex(BrowserCookieRefreshError, "No valid Gemini browser cookies"):
            choose_cookie_source({"edge": [{"name": "NID", "value": "nid", "domain": ".google.com"}]})
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run python -m unittest tests.test_gateway_refresh_cookies -v`

Expected: FAIL，提示 `gateway.refresh_cookies` 不存在。

- [ ] **Step 3: 实现选择模型和脱敏摘要**

```python
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys
import time
from typing import Any
from uuid import uuid4


BROWSER_PRIORITY = {
    "edge": 0,
    "chrome": 1,
    "brave": 2,
    "chromium": 3,
    "firefox": 4,
    "vivaldi": 5,
    "opera": 6,
    "opera_gx": 7,
    "librewolf": 8,
    "safari": 9,
}


class BrowserCookieRefreshError(Exception):
    pass


@dataclass(frozen=True)
class BrowserCookieSelection:
    source: str
    cookies: dict[str, str]

    @property
    def has_1psid(self) -> bool:
        return "__Secure-1PSID" in self.cookies

    @property
    def has_1psidts(self) -> bool:
        return "__Secure-1PSIDTS" in self.cookies

    def summary(self) -> str:
        return (
            f"source={self.source}, "
            f"has_1psid={str(self.has_1psid).lower()}, "
            f"has_1psidts={str(self.has_1psidts).lower()}, "
            f"count={len(self.cookies)}"
        )


def _cookies_from_items(items: list[dict[str, Any]]) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for item in items:
        name = item.get("name")
        value = item.get("value")
        domain = item.get("domain")
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        if not value:
            continue
        if isinstance(domain, str) and "google.com" not in domain and "gemini.google.com" not in domain:
            continue
        cookies[name] = value
    return cookies


def choose_cookie_source(
    browser_cookies: dict[str, list[dict[str, Any]]],
    requested_source: str | None = None,
) -> BrowserCookieSelection:
    candidates: list[BrowserCookieSelection] = []
    for source, items in browser_cookies.items():
        if requested_source and source != requested_source:
            continue
        cookies = _cookies_from_items(items)
        if "__Secure-1PSID" in cookies:
            candidates.append(BrowserCookieSelection(source=source, cookies=cookies))

    if not candidates:
        source_text = f" for browser source {requested_source}" if requested_source else ""
        raise BrowserCookieRefreshError(
            "No valid Gemini browser cookies found"
            f"{source_text}. Please log in to https://gemini.google.com in your browser first."
        )

    return sorted(
        candidates,
        key=lambda candidate: (
            "__Secure-1PSIDTS" not in candidate.cookies,
            -len(candidate.cookies),
            BROWSER_PRIORITY.get(candidate.source, 100),
            candidate.source,
        ),
    )[0]
```

- [ ] **Step 4: 运行选择测试并确认通过**

Run: `uv run python -m unittest tests.test_gateway_refresh_cookies -v`

Expected: PASS 上面 4 个测试。

- [ ] **Step 5: 写原子写入和脱敏输出失败测试**

```python
    def test_refresh_browser_cookies_writes_gateway_compatible_json_without_leaking_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cookies_path = Path(temp_dir) / "cookies.json"
            stdout = StringIO()

            with patch(
                "gateway.refresh_cookies.load_browser_cookies_from_domain",
                return_value={
                    "edge": [
                        {"name": "__Secure-1PSID", "value": "secret-psid", "domain": ".google.com"},
                        {"name": "__Secure-1PSIDTS", "value": "secret-psidts", "domain": ".google.com"},
                        {"name": "NID", "value": "secret-nid", "domain": ".google.com"},
                    ]
                },
            ), redirect_stdout(stdout):
                result = refresh_browser_cookies_to_file(cookies_path)

            payload = json.loads(cookies_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["source"], "edge")
            self.assertEqual(payload["cookies"]["__Secure-1PSID"], "secret-psid")
            self.assertEqual(payload["cookies"]["__Secure-1PSIDTS"], "secret-psidts")
            self.assertEqual(result.source, "edge")

            output = stdout.getvalue()
            self.assertIn("Browser cookies refreshed:", output)
            self.assertIn("source=edge", output)
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

    def test_refresh_browser_cookies_does_not_overwrite_existing_file_when_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cookies_path = Path(temp_dir) / "cookies.json"
            cookies_path.write_text('{"cookies":{"__Secure-1PSID":"old-psid"}}', encoding="utf-8")

            with patch(
                "gateway.refresh_cookies.load_browser_cookies_from_domain",
                return_value={"edge": [{"name": "NID", "value": "nid", "domain": ".google.com"}]},
            ):
                with self.assertRaises(BrowserCookieRefreshError):
                    refresh_browser_cookies_to_file(cookies_path)

            self.assertEqual(
                json.loads(cookies_path.read_text(encoding="utf-8"))["cookies"]["__Secure-1PSID"],
                "old-psid",
            )
```

- [ ] **Step 6: 运行写入测试并确认失败**

Run: `uv run python -m unittest tests.test_gateway_refresh_cookies -v`

Expected: FAIL，提示 `refresh_browser_cookies_to_file` 或 `load_browser_cookies_from_domain` 未定义。

- [ ] **Step 7: 实现浏览器读取、原子写入和 CLI**

```python
def load_browser_cookies_from_domain(domain_name: str, verbose: bool = False) -> dict[str, list[dict[str, Any]]]:
    try:
        from gemini_webapi.utils import load_browser_cookies
    except ImportError as exc:
        raise BrowserCookieRefreshError(
            "browser-cookie3 is not installed. Run: uv sync --extra browser"
        ) from exc

    cookies = load_browser_cookies(domain_name=domain_name, verbose=verbose)
    if not cookies:
        raise BrowserCookieRefreshError(
            "No browser cookies were found. Please log in to https://gemini.google.com in your browser first."
        )
    return cookies


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


def refresh_browser_cookies_to_file(
    cookies_path: str | Path,
    *,
    browser_source: str | None = None,
    domain: str = ".google.com",
    verbose: bool = False,
    print_summary: bool = True,
) -> BrowserCookieSelection:
    path = Path(cookies_path)
    browser_cookies = load_browser_cookies_from_domain(domain, verbose=verbose)
    selection = choose_cookie_source(browser_cookies, requested_source=browser_source)
    payload = {
        "cookies": dict(sorted(selection.cookies.items())),
        "updated_at": int(time.time()),
        "source": selection.source,
    }
    _atomic_write_json(path, payload)
    if print_summary:
        print(f"Browser cookies refreshed: {selection.summary()}")
    return selection


def main(argv: list[str] | None = None) -> int:
    import argparse
    from gateway.config import GatewaySettings

    parser = argparse.ArgumentParser(description="Refresh Gemini gateway cookies from a logged-in browser.")
    parser.add_argument("--cookies-path", default=None)
    parser.add_argument("--browser-source", default=None)
    parser.add_argument("--domain", default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    settings = GatewaySettings()
    cookies_path = args.cookies_path or settings.cookies_json_path
    browser_source = args.browser_source or settings.browser_cookie_source or None
    domain = args.domain or settings.browser_cookie_domain
    try:
        refresh_browser_cookies_to_file(
            cookies_path,
            browser_source=browser_source,
            domain=domain,
            verbose=args.verbose,
        )
    except BrowserCookieRefreshError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 8: 运行刷新模块测试并确认通过**

Run: `uv run python -m unittest tests.test_gateway_refresh_cookies -v`

Expected: PASS

- [ ] **Step 9: 提交**

```bash
git add gateway/refresh_cookies.py tests/test_gateway_refresh_cookies.py
git commit -m "新增浏览器 Cookie 刷新模块"
```

### Task 2: 增加浏览器 Cookie 配置项

**Files:**
- Modify: `gateway/config.py`
- Modify: `tests/test_gateway_account_status.py`

- [ ] **Step 1: 写配置失败测试**

```python
    def test_gateway_settings_exposes_v13_browser_cookie_defaults(self) -> None:
        settings = GatewaySettings(api_key="test-key", proxy="http://127.0.0.1:7890")

        self.assertFalse(settings.browser_cookie_refresh_enabled)
        self.assertFalse(settings.browser_cookie_refresh_on_auth_error)
        self.assertEqual(settings.browser_cookie_source, "")
        self.assertEqual(settings.browser_cookie_domain, ".google.com")

    def test_gateway_settings_reads_browser_cookie_env(self) -> None:
        env = {
            "GEMINI_GATEWAY_BROWSER_COOKIE_REFRESH_ENABLED": "true",
            "GEMINI_GATEWAY_BROWSER_COOKIE_REFRESH_ON_AUTH_ERROR": "true",
            "GEMINI_GATEWAY_BROWSER_COOKIE_SOURCE": "edge",
            "GEMINI_GATEWAY_BROWSER_COOKIE_DOMAIN": "gemini.google.com",
        }

        with patch.dict(os.environ, env, clear=False):
            settings = GatewaySettings(api_key="test-key", proxy="http://127.0.0.1:7890")

        self.assertTrue(settings.browser_cookie_refresh_enabled)
        self.assertTrue(settings.browser_cookie_refresh_on_auth_error)
        self.assertEqual(settings.browser_cookie_source, "edge")
        self.assertEqual(settings.browser_cookie_domain, "gemini.google.com")
```

- [ ] **Step 2: 运行配置测试并确认失败**

Run: `uv run python -m unittest tests.test_gateway_account_status -v`

Expected: FAIL，提示 `GatewaySettings` 缺少浏览器 Cookie 字段。

- [ ] **Step 3: 实现配置字段**

在 `GatewaySettings` 中加入：

```python
    browser_cookie_refresh_enabled: bool = field(
        default_factory=lambda: _get_env_bool(
            "GEMINI_GATEWAY_BROWSER_COOKIE_REFRESH_ENABLED",
            False,
        )
    )
    browser_cookie_refresh_on_auth_error: bool = field(
        default_factory=lambda: _get_env_bool(
            "GEMINI_GATEWAY_BROWSER_COOKIE_REFRESH_ON_AUTH_ERROR",
            False,
        )
    )
    browser_cookie_source: str = field(
        default_factory=lambda: _get_env(
            "GEMINI_GATEWAY_BROWSER_COOKIE_SOURCE",
            "",
        )
    )
    browser_cookie_domain: str = field(
        default_factory=lambda: _get_env(
            "GEMINI_GATEWAY_BROWSER_COOKIE_DOMAIN",
            ".google.com",
        )
    )
```

- [ ] **Step 4: 运行配置测试并确认通过**

Run: `uv run python -m unittest tests.test_gateway_account_status -v`

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add gateway/config.py tests/test_gateway_account_status.py
git commit -m "新增浏览器 Cookie 刷新配置"
```

### Task 3: 增加运行期 Cookie 周期性持久化任务

**Files:**
- Modify: `gateway/service.py`
- Modify: `gateway/main.py`
- Modify: `tests/test_gateway_service_lifecycle.py`
- Modify: `tests/test_gateway_api.py`

- [ ] **Step 1: 写后台任务生命周期失败测试**

在 `tests/test_gateway_service_lifecycle.py` 增加：

```python
    async def test_start_cookie_persist_task_persists_changed_runtime_cookies(self) -> None:
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
        self.assertEqual(service.get_cached_cookies()["__Secure-1PSIDTS"], "rotated-psidts")

    async def test_stop_cookie_persist_task_is_idempotent(self) -> None:
        service = GatewayService(self.settings)

        await service.stop_cookie_persist_task()
        await service.start_cookie_persist_task()
        await service.stop_cookie_persist_task()
        await service.stop_cookie_persist_task()

        self.assertIsNone(service._cookie_persist_task)
```

- [ ] **Step 2: 写 FastAPI lifespan 启停失败测试**

在 `tests/test_gateway_api.py` 的 `test_app_lifecycle_warms_up_and_shuts_down_gateway_service` 中扩展 mock：

```python
        start_persist_mock = AsyncMock()
        stop_persist_mock = AsyncMock()

        with (
            patch.object(self.app.state.gateway_service, "warmup", warmup_mock),
            patch.object(self.app.state.gateway_service, "shutdown", shutdown_mock),
            patch.object(self.app.state.gateway_service, "start_cookie_persist_task", start_persist_mock),
            patch.object(self.app.state.gateway_service, "stop_cookie_persist_task", stop_persist_mock),
        ):
            with TestClient(self.app):
                warmup_mock.assert_awaited_once()
                start_persist_mock.assert_awaited_once()

        stop_persist_mock.assert_awaited_once()
        shutdown_mock.assert_awaited_once()
```

- [ ] **Step 3: 运行测试并确认失败**

Run: `uv run python -m unittest tests.test_gateway_service_lifecycle tests.test_gateway_api -v`

Expected: FAIL，提示 `start_cookie_persist_task` 或 `stop_cookie_persist_task` 未定义。

- [ ] **Step 4: 实现后台任务字段和启停方法**

在 `GatewayService.__init__` 中增加：

```python
        self._cookie_persist_task: asyncio.Task[None] | None = None
```

在 `GatewayService` 中增加：

```python
    async def start_cookie_persist_task(self) -> None:
        if not self.settings.cookie_persist_enabled:
            return
        if self._cookie_persist_task is not None and not self._cookie_persist_task.done():
            return
        self._cookie_persist_task = asyncio.create_task(self._run_cookie_persist_loop())

    async def stop_cookie_persist_task(self) -> None:
        task = self._cookie_persist_task
        self._cookie_persist_task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run_cookie_persist_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.cookie_persist_interval_seconds)
            try:
                await self.flush_runtime_cookies()
            except Exception as exc:
                print(f"Warning: failed to persist runtime cookies: {exc}")

    async def flush_runtime_cookies(self) -> bool:
        client = self._shared_client
        if client is None:
            return False
        runtime_cookies = self._serialize_cookies_for_json(getattr(client, "cookies", None))
        if not runtime_cookies:
            return False
        merged = self._merge_serialized_cookies_with_cache(runtime_cookies)
        if merged == (self._cached_cookies or {}):
            return False
        self.persist_cookies(merged)
        return True
```

- [ ] **Step 5: 调整 `persist_cookies` 支持 dict 输入**

确认 `persist_cookies` 当前已支持 dict。若实现中缺少 dict 分支，补充：

```python
        if isinstance(cookies, dict):
            return {
                name: value
                for name, value in cookies.items()
                if isinstance(name, str) and isinstance(value, str)
            }
```

- [ ] **Step 6: 在 lifespan 中接入启停**

修改 `gateway/main.py` 的 lifespan：

```python
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await app.state.gateway_service.warmup()
        await app.state.gateway_service.start_cookie_persist_task()
        print(f"Account mode: {resolve_startup_account_mode(app)}")
        try:
            yield
        finally:
            await app.state.gateway_service.stop_cookie_persist_task()
            await app.state.gateway_service.shutdown()
```

- [ ] **Step 7: 运行生命周期测试并确认通过**

Run: `uv run python -m unittest tests.test_gateway_service_lifecycle tests.test_gateway_api -v`

Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add gateway/service.py gateway/main.py tests/test_gateway_service_lifecycle.py tests/test_gateway_api.py
git commit -m "新增运行期 Cookie 周期性持久化"
```

### Task 4: 增加认证失败浏览器兜底恢复

**Files:**
- Modify: `gateway/service.py`
- Modify: `tests/test_gateway_service_lifecycle.py`

- [ ] **Step 1: 写配置关闭时不刷新浏览器的失败测试**

```python
    async def test_auth_failure_does_not_refresh_browser_cookies_when_disabled(self) -> None:
        service = GatewayService(self.settings)
        first_client = FakeGeminiClient(generate_error=RecoverableAuthError("auth failed"))
        second_client = FakeGeminiClient(text_result="recovered reply")
        service._build_client_from_cached_cookies = Mock(side_effect=[first_client, second_client])
        service.refresh_cookies_from_browser = Mock(return_value=False)

        text = await service.generate_text(
            prompt="hello",
            upstream_model="gemini-3-flash",
            request=self.make_request(),
        )

        self.assertEqual(text, "recovered reply")
        service.refresh_cookies_from_browser.assert_not_called()
```

测试文件中将 `RecoverableAuthError` 绑定为当前 service 模块的 `AuthError`：

```python
RecoverableAuthError = gateway_service_module.AuthError
```

- [ ] **Step 2: 写配置开启时刷新一次浏览器的失败测试**

```python
    async def test_auth_failure_refreshes_browser_cookies_once_when_enabled(self) -> None:
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
        service._build_client_from_cached_cookies = Mock(side_effect=[first_client, second_client])
        service.refresh_cookies_from_browser = Mock(return_value=True)

        text = await service.generate_text(
            prompt="hello",
            upstream_model="gemini-3-flash",
            request=self.make_request(),
        )

        self.assertEqual(text, "recovered reply")
        service.refresh_cookies_from_browser.assert_called_once_with()
        self.assertEqual(service._build_client_from_cached_cookies.call_count, 2)

    async def test_auth_failure_browser_refresh_failure_returns_original_error(self) -> None:
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

        with self.assertRaises(RecoverableAuthError):
            await service.generate_text(
                prompt="hello",
                upstream_model="gemini-3-flash",
                request=self.make_request(),
            )

        service.refresh_cookies_from_browser.assert_called_once_with()
        self.assertEqual(service._build_client_from_cached_cookies.call_count, 1)
```

- [ ] **Step 3: 运行测试并确认失败**

Run: `uv run python -m unittest tests.test_gateway_service_lifecycle -v`

Expected: FAIL，提示 `refresh_cookies_from_browser` 不存在或认证失败仍直接 rebuild。

- [ ] **Step 4: 实现浏览器刷新方法**

在 `GatewayService` 中加入：

```python
    def refresh_cookies_from_browser(self) -> bool:
        if not self.settings.browser_cookie_refresh_enabled:
            return False

        try:
            from gateway.refresh_cookies import refresh_browser_cookies_to_file

            result = refresh_browser_cookies_to_file(
                self.settings.cookies_json_path,
                browser_source=self.settings.browser_cookie_source or None,
                domain=self.settings.browser_cookie_domain,
                print_summary=False,
            )
        except Exception as exc:
            print(f"Warning: failed to refresh browser cookies: {exc}")
            return False

        self._cached_cookies = dict(result.cookies)
        return True
```

- [ ] **Step 5: 让认证失败重建前可选刷新浏览器 Cookie**

修改 `generate_text` 的异常分支：

```python
                except Exception as exc:
                    if attempt == 0 and self._should_rebuild_shared_client(exc):
                        refreshed_from_browser = False
                        if self._should_refresh_browser_cookies_after_error(exc):
                            refreshed_from_browser = self.refresh_cookies_from_browser()
                            if not refreshed_from_browser:
                                raise
                        rebuilt_client, rebuilt_generation = (
                            await self._rebuild_shared_client_after_failure(
                                failed_client=client,
                                failed_generation=generation,
                            )
                        )
```

新增判断方法：

```python
    def _should_refresh_browser_cookies_after_error(self, exc: Exception) -> bool:
        return (
            self.settings.browser_cookie_refresh_enabled
            and self.settings.browser_cookie_refresh_on_auth_error
            and isinstance(exc, AuthError)
        )
```

- [ ] **Step 6: 给 stream 初始失败路径接入相同逻辑**

在 `generate_stream` 的 `not yielded_any_chunk` rebuild 分支中加入相同刷新逻辑：

```python
                    if (
                        attempt == 0
                        and not yielded_any_chunk
                        and self._should_rebuild_shared_client(exc)
                    ):
                        refreshed_from_browser = False
                        if self._should_refresh_browser_cookies_after_error(exc):
                            refreshed_from_browser = self.refresh_cookies_from_browser()
                            if not refreshed_from_browser:
                                raise
                        rebuilt_client, rebuilt_generation = (
                            await self._rebuild_shared_client_after_failure(
                                failed_client=client,
                                failed_generation=generation,
                            )
                        )
```

- [ ] **Step 7: 运行认证失败恢复测试并确认通过**

Run: `uv run python -m unittest tests.test_gateway_service_lifecycle -v`

Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add gateway/service.py tests/test_gateway_service_lifecycle.py
git commit -m "支持认证失败时浏览器 Cookie 兜底恢复"
```

### Task 5: 更新 PowerShell 启动体验与文档

**Files:**
- Modify: `gateway/set_gateway_env.ps1`
- Create: `gateway/start_gateway.ps1`
- Modify: `gateway/README.md`
- Modify: `tests/test_gateway_uv_startup_docs.py`

- [ ] **Step 1: 写文档和脚本提示失败测试**

在 `tests/test_gateway_uv_startup_docs.py` 增加：

```python
    def test_gateway_readme_documents_browser_cookie_refresh(self) -> None:
        readme = Path("gateway/README.md").read_text(encoding="utf-8")

        self.assertIn("uv sync --extra browser", readme)
        self.assertIn("uv run --extra browser python -m gateway.refresh_cookies", readme)
        self.assertIn("GEMINI_GATEWAY_BROWSER_COOKIE_REFRESH_ON_AUTH_ERROR", readme)

    def test_gateway_env_script_mentions_refresh_cookies(self) -> None:
        script = Path("gateway/set_gateway_env.ps1").read_text(encoding="utf-8")

        self.assertIn("gateway.refresh_cookies", script)
        self.assertIn("uv run --extra browser python -m gateway.refresh_cookies", script)

    def test_start_gateway_script_uses_uv_and_refresh_cookies(self) -> None:
        script = Path("gateway/start_gateway.ps1").read_text(encoding="utf-8")

        self.assertIn("uv sync --extra browser", script)
        self.assertIn("uv run --extra browser python -m gateway.refresh_cookies", script)
        self.assertIn("uv run python -m gateway.main", script)
```

- [ ] **Step 2: 运行文档测试并确认失败**

Run: `uv run python -m unittest tests.test_gateway_uv_startup_docs -v`

Expected: FAIL，提示 README 或脚本缺少刷新命令。

- [ ] **Step 3: 更新 `set_gateway_env.ps1`**

增加参数：

```powershell
    [bool]$BrowserCookieRefreshEnabled = $false,
    [bool]$BrowserCookieRefreshOnAuthError = $false,
    [string]$BrowserCookieSource = "",
    [string]$BrowserCookieDomain = ".google.com"
```

设置环境变量：

```powershell
$env:GEMINI_GATEWAY_BROWSER_COOKIE_REFRESH_ENABLED = $BrowserCookieRefreshEnabled.ToString().ToLower()
$env:GEMINI_GATEWAY_BROWSER_COOKIE_REFRESH_ON_AUTH_ERROR = $BrowserCookieRefreshOnAuthError.ToString().ToLower()
$env:GEMINI_GATEWAY_BROWSER_COOKIE_SOURCE = $BrowserCookieSource
$env:GEMINI_GATEWAY_BROWSER_COOKIE_DOMAIN = $BrowserCookieDomain
```

更新提示：

```powershell
Write-Host "Recommended next steps:"
Write-Host "  uv sync --extra browser"
Write-Host "  uv run --extra browser python -m gateway.refresh_cookies"
Write-Host "  uv run python -m gateway.main"
```

- [ ] **Step 4: 新增 `gateway/start_gateway.ps1`**

```powershell
param(
    [string]$ApiKey = "gemini-api",
    [string]$GatewayHost = "127.0.0.1",
    [int]$Port = 8010,
    [string]$DefaultModel = "gemini-3-flash",
    [ValidateSet("standard", "extended")]
    [string]$DefaultReasoningEffort = "standard",
    [string]$Proxy = "http://127.0.0.1:10090/",
    [string]$CookiesJsonPath = (Join-Path (Split-Path $PSScriptRoot -Parent) "cookies.json"),
    [string]$BrowserCookieSource = "",
    [string]$BrowserCookieDomain = ".google.com"
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "set_gateway_env.ps1") `
  -ApiKey $ApiKey `
  -GatewayHost $GatewayHost `
  -Port $Port `
  -DefaultModel $DefaultModel `
  -DefaultReasoningEffort $DefaultReasoningEffort `
  -Proxy $Proxy `
  -CookiesJsonPath $CookiesJsonPath `
  -BrowserCookieSource $BrowserCookieSource `
  -BrowserCookieDomain $BrowserCookieDomain

uv sync --extra browser
uv run --extra browser python -m gateway.refresh_cookies
uv run python -m gateway.main
```

- [ ] **Step 5: 更新 `gateway/README.md`**

增加启动前刷新流程：

````markdown
## 浏览器 Cookie 自动同步

如果本机浏览器已经登录 https://gemini.google.com，可以先安装 browser extra 并刷新 `cookies.json`：

```powershell
uv sync --extra browser
. .\gateway\set_gateway_env.ps1 -ApiKey "your-local-key"
uv run --extra browser python -m gateway.refresh_cookies
uv run python -m gateway.main
```

也可以使用一键脚本：

```powershell
.\gateway\start_gateway.ps1 -ApiKey "your-local-key"
```

认证失败时浏览器兜底恢复默认关闭。需要时显式开启：

```powershell
. .\gateway\set_gateway_env.ps1 `
  -ApiKey "your-local-key" `
  -BrowserCookieRefreshEnabled $true `
  -BrowserCookieRefreshOnAuthError $true
```
````

- [ ] **Step 6: 运行文档测试并确认通过**

Run: `uv run python -m unittest tests.test_gateway_uv_startup_docs -v`

Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add gateway/set_gateway_env.ps1 gateway/start_gateway.ps1 gateway/README.md tests/test_gateway_uv_startup_docs.py
git commit -m "更新网关 V1.3 Cookie 刷新启动说明"
```

### Task 6: 最终验证与回归检查

**Files:**
- No source changes unless verification reveals a defect

- [ ] **Step 1: 搜索敏感值泄露风险**

Run: `rg -n "secret-psid|secret-psidts|your-cookie|__Secure-1PSID\\\": \\\"[^\\\"]" gateway tests docs -S`

Expected: 只允许测试断言里的假值和 README 占位符，不允许真实 Cookie 值。

- [ ] **Step 2: 运行全部 gateway 相关单测**

Run: `uv run python -m unittest tests.test_gateway_refresh_cookies tests.test_gateway_api tests.test_gateway_service_lifecycle tests.test_gateway_account_status tests.test_gateway_config tests.test_gateway_uv_startup_docs -v`

Expected: PASS

- [ ] **Step 3: 运行编译检查**

Run: `uv run python -m compileall gateway`

Expected: PASS

- [ ] **Step 4: 运行 diff 空白检查**

Run: `git diff --check`

Expected: 无 trailing whitespace 或 conflict marker。

- [ ] **Step 5: 本机手动验证浏览器同步命令**

Run:

```powershell
uv sync --extra browser
. .\gateway\set_gateway_env.ps1 -ApiKey "gemini-api"
uv run --extra browser python -m gateway.refresh_cookies
```

Expected:

```text
Browser cookies refreshed: source=<browser>, has_1psid=true, has_1psidts=<true|false>, count=<n>
```

输出中不得出现任何 Cookie 值。

- [ ] **Step 6: 本机手动验证 gateway 启动和 OpenAI-compatible 接口**

Run:

```powershell
uv run python -m gateway.main
```

另开 PowerShell：

```powershell
curl http://127.0.0.1:8010/health
curl http://127.0.0.1:8010/v1/models -H "Authorization: Bearer gemini-api"
curl http://127.0.0.1:8010/v1/account/status -H "Authorization: Bearer gemini-api"
```

Expected:

- `/health` 返回 `{"status":"ok"}`
- `/v1/models` 返回 `gemini-3-flash`、`gemini-3-flash-thinking`、`gemini-3-pro`
- `/v1/account/status` 返回账户状态 JSON

- [ ] **Step 7: 提交最终验证修正**

如果 Step 1-6 没有产生新的文件改动，跳过本步骤。若验证发现缺陷并修复，提交：

```bash
git add <changed-files>
git commit -m "修正 V1.3 Cookie 刷新验证问题"
```

## Self-Review Checklist

- Spec coverage: Task 1 覆盖启动前浏览器同步、候选选择、原子写入和脱敏输出；Task 2 覆盖新增配置；Task 3 覆盖运行期周期性持久化；Task 4 覆盖认证失败一次性浏览器兜底恢复；Task 5 覆盖 PowerShell 启动体验和 README；Task 6 覆盖测试、编译、敏感信息与本机手动验证。
- Placeholder scan: 本计划没有使用待填充占位步骤；每个实现步骤都给出目标文件、命令、预期结果或代码片段。
- Type consistency: `BrowserCookieSelection`、`BrowserCookieRefreshError`、`refresh_browser_cookies_to_file`、`GatewaySettings` 新字段、`GatewayService` 新方法在后续任务中的命名保持一致。
