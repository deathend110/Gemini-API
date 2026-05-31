# Gemini OpenAI 网关 V1.1 性能优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变现有 OpenAI-compatible 接口契约的前提下，为本地 Gemini 网关实现共享长驻 `GeminiClient`、启动预热和 `cookies.json` 内存缓存，从而显著降低连续请求延迟。

**Architecture:** 让 `GatewayService` 从“每请求新建上游 client 的执行器”升级为“带生命周期的共享上游 client 管理器”。FastAPI 在 `startup` 时完成预热、在 `shutdown` 时释放资源，请求路径统一复用共享 client，并在失效时执行一次受控重建。

**Tech Stack:** Python 3.10+、FastAPI、Pydantic v2、gemini_webapi、unittest、unittest.mock

---

## 文件结构

- Modify: `gateway/service.py`
  - 新增共享 client、cookies 缓存、预热、关闭、重建、重试逻辑
- Modify: `gateway/main.py`
  - 新增 FastAPI `startup` / `shutdown` 生命周期钩子
- Modify: `gateway/README.md`
  - 补充 V1.1 启动预热与 cookies 重启生效说明
- Modify: `tests/test_gateway_api.py`
  - 增加应用生命周期与对外契约不回归测试
- Create: `tests/test_gateway_service_lifecycle.py`
  - 增加共享 client、cookies 缓存、重建逻辑的单元测试

### Task 1: 为共享 client 生命周期补上失败测试

**Files:**
- Create: `tests/test_gateway_service_lifecycle.py`

- [ ] **Step 1: 写 `warmup()` 失败测试**

```python
import unittest
from unittest.mock import AsyncMock, patch

from gateway.config import GatewaySettings
from gateway.service import GatewayService


class TestGatewayServiceLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_warmup_builds_shared_client_once(self) -> None:
        service = GatewayService(GatewaySettings(api_key="test-key"))
        fake_client = AsyncMock()

        with (
            patch.object(
                service,
                "load_cookies",
                return_value={"__Secure-1PSID": "psid", "__Secure-1PSIDTS": "ts"},
            ) as load_cookies_mock,
            patch.object(service, "_build_client_from_cached_cookies", return_value=fake_client) as build_mock,
        ):
            await service.warmup()
            await service.warmup()

        self.assertEqual(load_cookies_mock.call_count, 1)
        self.assertEqual(build_mock.call_count, 1)
        fake_client.init.assert_awaited_once()
```

- [ ] **Step 2: 运行单测并确认失败**

Run: `G:\Miniconda3\python.exe -m unittest tests.test_gateway_service_lifecycle.TestGatewayServiceLifecycle.test_warmup_builds_shared_client_once -v`

Expected: FAIL，提示 `GatewayService` 缺少 `warmup` 或 `_build_client_from_cached_cookies`

- [ ] **Step 3: 写 `shutdown()` 失败测试**

```python
    async def test_shutdown_closes_shared_client_and_resets_state(self) -> None:
        service = GatewayService(GatewaySettings(api_key="test-key"))
        fake_client = AsyncMock()
        service._shared_client = fake_client
        service._cached_cookies = {"__Secure-1PSID": "psid"}
        service._warmed_up = True

        await service.shutdown()

        fake_client.close.assert_awaited_once()
        self.assertIsNone(service._shared_client)
        self.assertFalse(service._warmed_up)
        self.assertEqual(service._cached_cookies["__Secure-1PSID"], "psid")
```

- [ ] **Step 4: 运行单测并确认失败**

Run: `G:\Miniconda3\python.exe -m unittest tests.test_gateway_service_lifecycle.TestGatewayServiceLifecycle.test_shutdown_closes_shared_client_and_resets_state -v`

Expected: FAIL，提示 `shutdown` 不存在或状态未重置

- [ ] **Step 5: 写 cookies 缓存失败测试**

```python
    async def test_get_cached_cookies_does_not_reload_file_after_warmup(self) -> None:
        service = GatewayService(GatewaySettings(api_key="test-key"))
        fake_client = AsyncMock()

        with (
            patch.object(
                service,
                "load_cookies",
                return_value={"__Secure-1PSID": "psid", "__Secure-1PSIDTS": "ts"},
            ) as load_cookies_mock,
            patch.object(service, "_build_client_from_cached_cookies", return_value=fake_client),
        ):
            await service.warmup()
            service.get_cached_cookies()
            service.get_cached_cookies()

        self.assertEqual(load_cookies_mock.call_count, 1)
```

- [ ] **Step 6: 运行单测并确认失败**

Run: `G:\Miniconda3\python.exe -m unittest tests.test_gateway_service_lifecycle.TestGatewayServiceLifecycle.test_get_cached_cookies_does_not_reload_file_after_warmup -v`

Expected: FAIL，提示 `get_cached_cookies` 不存在

- [ ] **Step 7: 提交测试骨架**

```bash
git add tests/test_gateway_service_lifecycle.py
git commit -m "新增网关生命周期失败测试"
```

### Task 2: 实现共享 `GeminiClient`、预热与 cookies 缓存

**Files:**
- Modify: `gateway/service.py`
- Test: `tests/test_gateway_service_lifecycle.py`

- [ ] **Step 1: 为 `GatewayService` 增加共享状态字段**

```python
class GatewayService:
    def __init__(self, settings: GatewaySettings) -> None:
        self.settings = settings
        self._models_by_id = {model.canonical_id: model for model in CANONICAL_MODELS}
        self._shared_client: GeminiClient | None = None
        self._cached_cookies: dict[str, str] | None = None
        self._warmed_up = False
        self._rebuild_lock: asyncio.Lock | None = None
```

- [ ] **Step 2: 实现 cookies 缓存读取方法**

```python
    def get_cached_cookies(self) -> dict[str, str]:
        if self._cached_cookies is None:
            self._cached_cookies = self.load_cookies()
        return dict(self._cached_cookies)
```

- [ ] **Step 3: 实现基于缓存 cookies 的 client 构建方法**

```python
    def _build_client_from_cached_cookies(self) -> GeminiClient:
        from gemini_webapi import GeminiClient

        cookies = self.get_cached_cookies()
        psid = cookies.get("__Secure-1PSID")
        psidts = cookies.get("__Secure-1PSIDTS", "")
        extra_cookies = {
            name: value
            for name, value in cookies.items()
            if name not in {"__Secure-1PSID", "__Secure-1PSIDTS"}
        }
        client = GeminiClient(
            secure_1psid=psid,
            secure_1psidts=psidts,
            proxy=self.settings.proxy,
        )
        if extra_cookies:
            client.cookies = extra_cookies
        return client
```

- [ ] **Step 4: 实现 `warmup()` 和 `shutdown()`**

```python
    async def warmup(self) -> None:
        if self._warmed_up and self._shared_client is not None:
            return

        client = self._build_client_from_cached_cookies()
        await client.init(
            timeout=self.settings.request_timeout,
            auto_refresh=True,
            auto_close=False,
        )
        self._shared_client = client
        self._warmed_up = True

    async def shutdown(self) -> None:
        if self._shared_client is not None:
            await self._shared_client.close()
        self._shared_client = None
        self._warmed_up = False
```

- [ ] **Step 5: 把请求路径改为复用共享 client**

```python
    async def get_shared_client(self) -> GeminiClient:
        if self._shared_client is None or not self._warmed_up:
            await self.warmup()
        assert self._shared_client is not None
        return self._shared_client

    async def generate_text(...):
        client = await self.get_shared_client()
        response = await client.generate_content(
            prompt=prompt,
            model=upstream_model,
            files=files,
        )
        return response.text

    async def generate_stream(...):
        client = await self.get_shared_client()
        async for chunk in client.generate_content_stream(
            prompt=prompt,
            model=upstream_model,
            files=files,
        ):
            yield chunk
```

- [ ] **Step 6: 运行生命周期单测并确认通过**

Run: `G:\Miniconda3\python.exe -m unittest tests.test_gateway_service_lifecycle -v`

Expected: PASS

- [ ] **Step 7: 运行现有网关回归测试**

Run: `G:\Miniconda3\python.exe -m unittest tests.test_gateway_api tests.test_gateway_config -v`

Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add gateway/service.py tests/test_gateway_service_lifecycle.py
git commit -m "实现网关共享客户端与 cookies 缓存"
```

### Task 3: 接入 FastAPI 启动预热与关闭释放

**Files:**
- Modify: `gateway/main.py`
- Modify: `tests/test_gateway_api.py`

- [ ] **Step 1: 写 FastAPI 生命周期失败测试**

```python
    def test_app_startup_warms_up_gateway_service(self) -> None:
        with patch.object(self.app.state.gateway_service, "warmup", new=AsyncMock()) as warmup_mock:
            with TestClient(self.app):
                pass

        warmup_mock.assert_awaited_once()

    def test_app_shutdown_closes_gateway_service(self) -> None:
        with patch.object(self.app.state.gateway_service, "shutdown", new=AsyncMock()) as shutdown_mock:
            with TestClient(self.app):
                pass

        shutdown_mock.assert_awaited_once()
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `G:\Miniconda3\python.exe -m unittest tests.test_gateway_api.TestGatewayApi.test_app_startup_warms_up_gateway_service tests.test_gateway_api.TestGatewayApi.test_app_shutdown_closes_gateway_service -v`

Expected: FAIL，提示 `warmup` / `shutdown` 未被调用

- [ ] **Step 3: 在 `create_app()` 中接入生命周期钩子**

```python
    @app.on_event("startup")
    async def startup_event() -> None:
        await app.state.gateway_service.warmup()

    @app.on_event("shutdown")
    async def shutdown_event() -> None:
        await app.state.gateway_service.shutdown()
```

- [ ] **Step 4: 调整测试用法，确保 `TestClient` 真正触发生命周期**

```python
        with TestClient(create_app(settings=self.settings)) as client:
            response = client.get("/health")
            self.assertEqual(response.status_code, 200)
```

- [ ] **Step 5: 运行 API 测试并确认通过**

Run: `G:\Miniconda3\python.exe -m unittest tests.test_gateway_api -v`

Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add gateway/main.py tests/test_gateway_api.py
git commit -m "接入网关启动预热与关闭释放"
```

### Task 4: 实现共享 client 失效后的受控重建

**Files:**
- Modify: `gateway/service.py`
- Modify: `tests/test_gateway_service_lifecycle.py`

- [ ] **Step 1: 写重建失败测试**

```python
    async def test_generate_text_rebuilds_shared_client_once_on_timeout(self) -> None:
        service = GatewayService(GatewaySettings(api_key="test-key"))
        first_client = AsyncMock()
        second_client = AsyncMock()
        first_client.generate_content = AsyncMock(side_effect=TimeoutError("timeout"))
        second_client.generate_content = AsyncMock(return_value=type("Resp", (), {"text": "ok"})())

        with patch.object(service, "warmup", new=AsyncMock()) as warmup_mock:
            service._shared_client = first_client
            service._cached_cookies = {"__Secure-1PSID": "psid"}
            service._warmed_up = True
            with patch.object(service, "_rebuild_shared_client", new=AsyncMock(return_value=second_client)) as rebuild_mock:
                result = await service.generate_text("p", "gemini-3-flash", None)

        self.assertEqual(result, "ok")
        rebuild_mock.assert_awaited_once()
        warmup_mock.assert_not_awaited()
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `G:\Miniconda3\python.exe -m unittest tests.test_gateway_service_lifecycle.TestGatewayServiceLifecycle.test_generate_text_rebuilds_shared_client_once_on_timeout -v`

Expected: FAIL，提示没有重建逻辑或返回异常

- [ ] **Step 3: 增加重建方法**

```python
    async def _rebuild_shared_client(self) -> GeminiClient:
        await self.shutdown()
        client = self._build_client_from_cached_cookies()
        await client.init(
            timeout=self.settings.request_timeout,
            auto_refresh=True,
            auto_close=False,
        )
        self._shared_client = client
        self._warmed_up = True
        return client
```

- [ ] **Step 4: 在 `generate_text()` / `generate_stream()` 中加入一次性重试**

```python
    async def generate_text(...):
        client = await self.get_shared_client()
        try:
            response = await client.generate_content(...)
            return response.text
        except (AuthError, TimeoutError, APIError, GeminiError):
            client = await self._rebuild_shared_client()
            response = await client.generate_content(...)
            return response.text
```

```python
    async def generate_stream(...):
        client = await self.get_shared_client()
        try:
            async for chunk in client.generate_content_stream(...):
                yield chunk
        except (AuthError, TimeoutError, APIError, GeminiError):
            client = await self._rebuild_shared_client()
            async for chunk in client.generate_content_stream(...):
                yield chunk
```

- [ ] **Step 5: 运行服务生命周期测试并确认通过**

Run: `G:\Miniconda3\python.exe -m unittest tests.test_gateway_service_lifecycle -v`

Expected: PASS

- [ ] **Step 6: 运行全量网关测试并确认通过**

Run: `G:\Miniconda3\python.exe -m unittest tests.test_gateway_api tests.test_gateway_service_lifecycle tests.test_gateway_config -v`

Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add gateway/service.py tests/test_gateway_service_lifecycle.py
git commit -m "实现网关共享客户端失效重建"
```

### Task 5: 更新接入文档并做最终验证

**Files:**
- Modify: `gateway/README.md`

- [ ] **Step 1: 补充 README 中的 V1.1 行为说明**

```markdown
## 性能优化说明

- 网关会在启动时预热上游 Gemini 会话
- 后续请求会复用共享上游 client
- 如果你手动更新了 `cookies.json`，请重启 gateway 让新 cookies 生效
```

- [ ] **Step 2: 补充启动预热注意事项**

```markdown
- 首次启动可能比 V1.0 多花一点时间，因为会在 startup 阶段完成预热
- 但启动完成后，连续请求延迟会明显下降
```

- [ ] **Step 3: 运行编译检查**

Run: `G:\Miniconda3\python.exe -m compileall gateway`

Expected: PASS

- [ ] **Step 4: 运行全量网关相关测试**

Run: `G:\Miniconda3\python.exe -m unittest tests.test_gateway_api tests.test_gateway_service_lifecycle tests.test_gateway_config -v`

Expected: PASS

- [ ] **Step 5: 做一次真实链路回归**

Run:

```bash
G:\Miniconda3\python.exe test.py
```

Expected:

- `/health` 200
- `/v1/models` 200
- 普通 chat 200
- stream chat 返回 `data: [DONE]`
- 若脚本保留 tools 测试，则 tools 请求也返回 200

- [ ] **Step 6: 提交**

```bash
git add gateway/README.md
git commit -m "补充网关 V1.1 性能优化说明"
```
