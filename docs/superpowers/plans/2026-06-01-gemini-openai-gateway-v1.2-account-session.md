# Gemini OpenAI 网关 V1.2 账户会话治理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为本地 Gemini OpenAI 网关补齐 cookies 持久化、账户能力探测、严格账户模式与诊断输出，让服务能够更稳定地续用网页端会话，并明确区分“可用”“受限”“不可用”。

**Architecture:** 保持现有 OpenAI-compatible 主接口不变，在 `GatewayService` 上增加会话治理层。启动时执行 warmup + probe，运行中按节流策略把共享 client 的最新 cookies 回写到 `cookies.json`，并生成标准化账户状态快照；必要时通过严格模式在 startup 阶段拒绝进入“完整网页能力不足”的状态。

**Tech Stack:** Python 3.10+、FastAPI、Pydantic v2、gemini_webapi、unittest、unittest.mock

---

## 文件结构

- Create: `gateway/account.py`
  - 账户状态快照、能力等级判定、严格模式校验
- Modify: `gateway/config.py`
  - 增加 cookies 持久化、账户探测、严格模式配置
- Modify: `gateway/service.py`
  - 增加 cookies 回写、账户 probe、状态快照存储、strict mode 校验
- Modify: `gateway/main.py`
  - 增加账户状态诊断路由，并在 startup 日志输出摘要
- Modify: `gateway/README.md`
  - 增补 V1.2 账户状态与 cookies 持久化说明
- Create: `tests/test_gateway_account_status.py`
  - 账户状态模型与严格模式单测
- Modify: `tests/test_gateway_service_lifecycle.py`
  - cookies 回写、probe、strict mode 生命周期测试
- Modify: `tests/test_gateway_api.py`
  - 账户状态路由与启动摘要测试
- Create: `docs/gateway-v1.2-开发结果报告.md`
  - V1.2 开发结果报告

### Task 1: 建立账户状态模型与配置

**Files:**
- Create: `gateway/account.py`
- Modify: `gateway/config.py`
- Test: `tests/test_gateway_account_status.py`

- [ ] **Step 1: 写账户状态与严格模式失败测试**

```python
import unittest

from gateway.account import (
    GatewayAccountSnapshot,
    evaluate_account_mode,
    validate_required_account_level,
)


class TestGatewayAccountStatus(unittest.TestCase):
    def test_evaluate_account_mode_marks_available_when_all_capabilities_present(self) -> None:
        snapshot = GatewayAccountSnapshot(
            raw_account_status="AVAILABLE",
            raw_account_status_code=1000,
            chat_available=True,
            advanced_models_available=True,
            deep_research_available=True,
            full_web_capability_available=True,
            mode="unknown",
            unavailable_reasons=[],
        )

        evaluated = evaluate_account_mode(snapshot)

        self.assertEqual(evaluated.mode, "available")

    def test_evaluate_account_mode_marks_degraded_when_chat_only(self) -> None:
        snapshot = GatewayAccountSnapshot(
            raw_account_status="UNAUTHENTICATED",
            raw_account_status_code=1016,
            chat_available=True,
            advanced_models_available=False,
            deep_research_available=False,
            full_web_capability_available=False,
            mode="unknown",
            unavailable_reasons=["advanced_models_unavailable"],
        )

        evaluated = evaluate_account_mode(snapshot)

        self.assertEqual(evaluated.mode, "degraded")

    def test_validate_required_account_level_raises_when_full_web_missing(self) -> None:
        snapshot = GatewayAccountSnapshot(
            raw_account_status="UNAUTHENTICATED",
            raw_account_status_code=1016,
            chat_available=True,
            advanced_models_available=True,
            deep_research_available=False,
            full_web_capability_available=False,
            mode="degraded",
            unavailable_reasons=["deep_research_unavailable"],
        )

        with self.assertRaisesRegex(ValueError, "full_web"):
            validate_required_account_level(snapshot, "full_web")
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `G:\Miniconda3\python.exe -m unittest tests.test_gateway_account_status -v`

Expected: FAIL，提示 `gateway.account` 不存在或相关函数未定义。

- [ ] **Step 3: 实现账户状态模型**

```python
from __future__ import annotations

from dataclasses import asdict, dataclass, field


ACCOUNT_REQUIRED_LEVELS = {"basic", "standard", "full_web"}


@dataclass(frozen=True)
class GatewayAccountSnapshot:
    raw_account_status: str
    raw_account_status_code: int | None
    chat_available: bool
    advanced_models_available: bool
    deep_research_available: bool
    full_web_capability_available: bool
    mode: str
    unavailable_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def evaluate_account_mode(snapshot: GatewayAccountSnapshot) -> GatewayAccountSnapshot:
    if snapshot.chat_available and snapshot.full_web_capability_available:
        mode = "available"
    elif snapshot.chat_available:
        mode = "degraded"
    else:
        mode = "blocked"
    return GatewayAccountSnapshot(
        raw_account_status=snapshot.raw_account_status,
        raw_account_status_code=snapshot.raw_account_status_code,
        chat_available=snapshot.chat_available,
        advanced_models_available=snapshot.advanced_models_available,
        deep_research_available=snapshot.deep_research_available,
        full_web_capability_available=snapshot.full_web_capability_available,
        mode=mode,
        unavailable_reasons=list(snapshot.unavailable_reasons),
    )


def validate_required_account_level(
    snapshot: GatewayAccountSnapshot,
    required_level: str,
) -> None:
    if required_level not in ACCOUNT_REQUIRED_LEVELS:
        raise ValueError(f"Unsupported required account level: {required_level}")
    if required_level == "basic" and snapshot.chat_available:
        return
    if required_level == "standard" and snapshot.chat_available and snapshot.advanced_models_available:
        return
    if required_level == "full_web" and snapshot.full_web_capability_available:
        return
    raise ValueError(
        f"Gateway account snapshot does not satisfy required level: {required_level}"
    )
```

- [ ] **Step 4: 扩展网关配置**

```python
@dataclass
class GatewaySettings:
    ...
    cookie_persist_enabled: bool = field(
        default_factory=lambda: _get_env_bool("GEMINI_GATEWAY_COOKIE_PERSIST_ENABLED", True)
    )
    cookie_persist_interval_seconds: int = field(
        default_factory=lambda: _get_env_int("GEMINI_GATEWAY_COOKIE_PERSIST_INTERVAL_SECONDS", 60)
    )
    account_probe_enabled: bool = field(
        default_factory=lambda: _get_env_bool("GEMINI_GATEWAY_ACCOUNT_PROBE_ENABLED", True)
    )
    account_strict_mode: bool = field(
        default_factory=lambda: _get_env_bool("GEMINI_GATEWAY_ACCOUNT_STRICT_MODE", False)
    )
    account_required_level: str = field(
        default_factory=lambda: _get_env("GEMINI_GATEWAY_ACCOUNT_REQUIRED_LEVEL", "basic")
    )
```

- [ ] **Step 5: 运行账户状态测试并确认通过**

Run: `G:\Miniconda3\python.exe -m unittest tests.test_gateway_account_status -v`

Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add gateway/account.py gateway/config.py tests/test_gateway_account_status.py
git commit -m "新增网关账户状态模型与配置"
```

### Task 2: 实现 cookies 持久化与服务级账户 probe

**Files:**
- Modify: `gateway/service.py`
- Modify: `tests/test_gateway_service_lifecycle.py`

- [ ] **Step 1: 写 cookies 回写与账户 probe 失败测试**

```python
    async def test_shutdown_persists_updated_cookies_to_json(self) -> None:
        service = GatewayService(self.settings)
        fake_client = FakeGeminiClient(cookie_overrides={"__Secure-1PSIDTS": "new-ts"})
        service._build_client_from_cached_cookies = Mock(return_value=fake_client)

        await service.warmup()
        await service.shutdown()

        persisted = json.loads(self.cookies_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["cookies"]["__Secure-1PSIDTS"], "new-ts")

    async def test_warmup_builds_account_snapshot_from_probe_results(self) -> None:
        service = GatewayService(self.settings)
        fake_client = FakeGeminiClient(
            inspect_snapshot={
                "summary": {"deep_research_feature_present": False},
            },
            account_status_name="UNAUTHENTICATED",
            account_status_code=1016,
        )
        service._build_client_from_cached_cookies = Mock(return_value=fake_client)

        await service.warmup()

        snapshot = service.get_account_snapshot()
        self.assertEqual(snapshot.mode, "degraded")
        self.assertTrue(snapshot.chat_available)
        self.assertFalse(snapshot.deep_research_available)
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `G:\Miniconda3\python.exe -m unittest tests.test_gateway_service_lifecycle -v`

Expected: FAIL，提示 cookies 未回写或 `get_account_snapshot()` 未定义。

- [ ] **Step 3: 为 FakeGeminiClient 增加 cookies 与 probe 支持**

```python
class FakeGeminiClient:
    def __init__(..., cookie_overrides: dict[str, str] | None = None, inspect_snapshot: dict | None = None, account_status_name: str = "AVAILABLE", account_status_code: int = 1000) -> None:
        self.cookies = FakeCookies(
            {
                "__Secure-1PSID": "psid-value",
                "__Secure-1PSIDTS": "psidts-value",
                "NID": "nid-value",
                **(cookie_overrides or {}),
            }
        )
        self.inspect_snapshot = inspect_snapshot or {"summary": {"deep_research_feature_present": True}}
        self.account_status = SimpleNamespace(name=account_status_name, value=account_status_code)

    async def inspect_account_status(self) -> dict:
        return self.inspect_snapshot
```

- [ ] **Step 4: 在服务中实现 cookies 回写**

```python
    def _serialize_cookies_for_json(self, cookies: Any) -> dict[str, str]:
        result: dict[str, str] = {}
        jar = getattr(cookies, "jar", [])
        for cookie in jar:
            name = getattr(cookie, "name", None)
            value = getattr(cookie, "value", None)
            if isinstance(name, str) and isinstance(value, str) and value:
                result[name] = value
        return result

    def persist_cookies(self, cookies: Any) -> None:
        if not self.settings.cookie_persist_enabled:
            return
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "cookies": dict(sorted(self._serialize_cookies_for_json(cookies).items())),
        }
        Path(self.settings.cookies_json_path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._cached_cookies = dict(payload["cookies"])
```

- [ ] **Step 5: 在 warmup 中加入账户能力探测**

```python
    async def _build_account_snapshot(self, client: GeminiClient) -> GatewayAccountSnapshot:
        raw_status = getattr(client, "account_status", None)
        raw_name = getattr(raw_status, "name", "UNKNOWN")
        raw_code = getattr(raw_status, "value", None)
        probe = await client.inspect_account_status() if self.settings.account_probe_enabled else {}
        summary = probe.get("summary", {}) if isinstance(probe, dict) else {}
        deep_research_available = bool(summary.get("deep_research_feature_present", False))

        snapshot = GatewayAccountSnapshot(
            raw_account_status=raw_name,
            raw_account_status_code=raw_code,
            chat_available=True,
            advanced_models_available=raw_name != "BLOCKED",
            deep_research_available=deep_research_available,
            full_web_capability_available=deep_research_available and raw_name == "AVAILABLE",
            mode="unknown",
            unavailable_reasons=[],
        )
        return evaluate_account_mode(snapshot)
```

- [ ] **Step 6: 在生命周期中接入回写与 strict mode 校验**

```python
    async def warmup(self) -> None:
        ...
        await self._init_shared_client(client)
        self._account_snapshot = await self._build_account_snapshot(self._shared_client)
        if self.settings.account_strict_mode:
            validate_required_account_level(
                self._account_snapshot,
                self.settings.account_required_level,
            )

    async def shutdown(self) -> None:
        ...
        if client is not None:
            self.persist_cookies(client.cookies)
            await client.close()
```

- [ ] **Step 7: 运行服务生命周期测试并确认通过**

Run: `G:\Miniconda3\python.exe -m unittest tests.test_gateway_service_lifecycle -v`

Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add gateway/service.py tests/test_gateway_service_lifecycle.py
git commit -m "实现网关 cookies 持久化与账户探测"
```

### Task 3: 实现诊断路由与启动摘要

**Files:**
- Modify: `gateway/main.py`
- Modify: `tests/test_gateway_api.py`

- [ ] **Step 1: 写状态路由与严格模式启动失败测试**

```python
    def test_account_status_route_returns_snapshot(self) -> None:
        self.app.state.gateway_service.get_account_snapshot = Mock(
            return_value=SimpleNamespace(
                to_dict=lambda: {
                    "raw_account_status": "UNAUTHENTICATED",
                    "mode": "degraded",
                    "chat_available": True,
                }
            )
        )

        response = self.client.get(
            "/v1/account/status",
            headers={"Authorization": f"Bearer {self.settings.api_key}"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mode"], "degraded")
```

- [ ] **Step 2: 运行 API 测试并确认失败**

Run: `G:\Miniconda3\python.exe -m unittest tests.test_gateway_api -v`

Expected: FAIL，提示 `/v1/account/status` 不存在。

- [ ] **Step 3: 在 FastAPI 中增加诊断路由**

```python
    @app.get("/v1/account/status", dependencies=[Depends(verify_bearer)])
    def account_status(service: Any = Depends(get_gateway_service)) -> Any:
        return service.get_account_snapshot().to_dict()
```

- [ ] **Step 4: 在启动阶段打印账户状态摘要**

```python
def main() -> None:
    ...
    app = create_app(settings=settings)
    snapshot = getattr(app.state.gateway_service, "get_account_snapshot", None)
    if callable(snapshot):
        try:
            current = snapshot()
            print(f"Account mode: {current.mode}")
        except Exception:
            print("Account mode: unavailable")
    uvicorn.run(app, host=settings.host, port=settings.port)
```

- [ ] **Step 5: 运行 API 测试并确认通过**

Run: `G:\Miniconda3\python.exe -m unittest tests.test_gateway_api -v`

Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add gateway/main.py tests/test_gateway_api.py
git commit -m "新增网关账户状态诊断接口"
```

### Task 4: 更新说明文档并完成最终验证

**Files:**
- Modify: `gateway/README.md`
- Create: `docs/gateway-v1.2-开发结果报告.md`

- [ ] **Step 1: 更新 README 的 V1.2 说明**

```markdown
## V1.2 账户会话治理说明

- 网关会在 startup 后探测当前账户状态
- 运行中的共享 client 会按策略把最新 cookies 回写到 `cookies.json`
- `UNAUTHENTICATED` 不再直接等同于完全不可用，网关会进一步判断能力等级
- 可通过严格模式要求 `basic` / `standard` / `full_web` 能力门槛
```

- [ ] **Step 2: 写 V1.2 开发结果报告**

```markdown
# Gemini OpenAI 网关 V1.2 开发结果报告

## 1. 开发目标
- cookies 持久化
- 账户状态快照
- strict mode
- 诊断路由

## 2. 验证结果
- compileall 通过
- 单元测试通过
- test.py 真实链路通过
```

- [ ] **Step 3: 运行编译检查**

Run: `G:\Miniconda3\python.exe -m compileall gateway`

Expected: PASS

- [ ] **Step 4: 运行完整网关测试**

Run: `G:\Miniconda3\python.exe -m unittest tests.test_gateway_api tests.test_gateway_service_lifecycle tests.test_gateway_config tests.test_gateway_account_status -v`

Expected: PASS

- [ ] **Step 5: 运行真实链路回归**

Run: `G:\Miniconda3\python.exe test.py`

Expected:

- `/health` 200
- `/v1/models` 200
- 普通 chat 200
- stream chat 正常输出 `data: [DONE]`
- tools chat 返回 200

- [ ] **Step 6: 提交**

```bash
git add gateway/README.md docs/gateway-v1.2-开发结果报告.md tests/test_gateway_account_status.py
git commit -m "补充网关 V1.2 账户会话治理说明"
```
