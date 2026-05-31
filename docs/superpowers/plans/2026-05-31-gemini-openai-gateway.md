# Gemini OpenAI Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在当前仓库中新增一个独立的本地 FastAPI 反代服务，对外暴露 OpenAI-compatible 的 `models` 与 `chat/completions` 接口，支持流式、tools、图片输入和扩展文件输入，供 AstrBot 与 Fitness Agent 复用。

**Architecture:** 在仓库根目录新增 `gateway/` 目录，使用 FastAPI 作为 HTTP 层，`gateway/service.py` 负责把 OpenAI 风格请求映射到 `gemini_webapi.GeminiClient`。对话接口保持无状态，由客户端传入完整 `messages` 历史；服务端只负责鉴权、模型/思考强度解析、图片/文件转换、工具协议约束、流式输出与错误规范化。

**Tech Stack:** Python 3.10+、FastAPI、Uvicorn、Pydantic v2、gemini_webapi、curl-cffi、unittest

---

## 文件结构

- Create: `gateway/__init__.py`
- Create: `gateway/main.py`
- Create: `gateway/config.py`
- Create: `gateway/auth.py`
- Create: `gateway/schemas.py`
- Create: `gateway/files.py`
- Create: `gateway/service.py`
- Create: `gateway/streaming.py`
- Create: `gateway/README.md`
- Create: `tests/test_gateway_config.py`
- Create: `tests/test_gateway_api.py`
- Modify: `pyproject.toml`
- Modify: `README.md`

### Task 1: 加入网关运行时依赖与配置解析

**Files:**
- Modify: `pyproject.toml`
- Create: `gateway/config.py`
- Create: `tests/test_gateway_config.py`

- [ ] **Step 1: 写配置解析失败测试**

```python
import unittest
from unittest.mock import patch

from gateway.config import GatewaySettings


class TestGatewaySettings(unittest.TestCase):
    def test_uses_proxy_fallback_when_env_missing(self):
        with patch.dict("os.environ", {}, clear=True):
            settings = GatewaySettings()
        self.assertEqual(settings.proxy, "http://127.0.0.1:10090/")

    def test_prefers_env_proxy_and_custom_api_key(self):
        with patch.dict(
            "os.environ",
            {
                "GEMINI_GATEWAY_API_KEY": "local-key",
                "HTTPS_PROXY": "http://127.0.0.1:7890/",
            },
            clear=True,
        ):
            settings = GatewaySettings()
        self.assertEqual(settings.api_key, "local-key")
        self.assertEqual(settings.proxy, "http://127.0.0.1:7890/")
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `G:\Miniconda3\python.exe -m unittest tests.test_gateway_config -v`
Expected: FAIL，提示 `gateway.config` 或 `GatewaySettings` 不存在

- [ ] **Step 3: 写最小配置实现与依赖声明**

```toml
[project]
dependencies = [
    "curl-cffi~=0.15.0",
    "fastapi~=0.116.1",
    "loguru~=0.7.3",
    "orjson~=3.11.7",
    "pydantic~=2.12.5",
    "uvicorn~=0.35.0",
]
```

```python
from dataclasses import dataclass
import os
import secrets


@dataclass
class GatewaySettings:
    host: str = "127.0.0.1"
    port: int = 8000
    cookies_json_path: str = "cookies.json"
    default_model: str = "gemini-3.5-flash"
    default_reasoning_effort: str = "standard"
    request_timeout: int = 300
    proxy: str = ""
    api_key: str = ""

    def __post_init__(self) -> None:
        self.proxy = self.proxy or (
            os.getenv("HTTPS_PROXY")
            or os.getenv("https_proxy")
            or os.getenv("HTTP_PROXY")
            or os.getenv("http_proxy")
            or "http://127.0.0.1:10090/"
        )
        self.api_key = self.api_key or os.getenv(
            "GEMINI_GATEWAY_API_KEY", secrets.token_urlsafe(24)
        )
```

- [ ] **Step 4: 运行测试并确认通过**

Run: `G:\Miniconda3\python.exe -m unittest tests.test_gateway_config -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add pyproject.toml gateway/config.py tests/test_gateway_config.py
git commit -m "新增网关配置与依赖声明"
```

### Task 2: 建立 FastAPI 入口、鉴权与基础端点

**Files:**
- Create: `gateway/__init__.py`
- Create: `gateway/auth.py`
- Create: `gateway/main.py`
- Create: `tests/test_gateway_api.py`

- [ ] **Step 1: 写基础 API 失败测试**

```python
import unittest
from fastapi.testclient import TestClient

from gateway.main import create_app


class TestGatewayApi(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = TestClient(self.app)

    def test_health_endpoint(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_models_requires_bearer_auth(self):
        response = self.client.get("/v1/models")
        self.assertEqual(response.status_code, 401)
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `G:\Miniconda3\python.exe -m unittest tests.test_gateway_api.TestGatewayApi -v`
Expected: FAIL，提示 `gateway.main` 或 `create_app` 不存在

- [ ] **Step 3: 实现入口、鉴权与最小端点**

```python
from fastapi import Depends, FastAPI, Header, HTTPException

from gateway.config import GatewaySettings


def verify_api_key(
    authorization: str | None = Header(default=None),
    settings: GatewaySettings | None = None,
) -> None:
    settings = settings or GatewaySettings()
    if authorization != f"Bearer {settings.api_key}":
        raise HTTPException(status_code=401, detail="unauthorized")


def create_app(settings: GatewaySettings | None = None) -> FastAPI:
    settings = settings or GatewaySettings()
    app = FastAPI(title="Gemini OpenAI Gateway", version="1.0.0")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/v1/models", dependencies=[Depends(lambda: verify_api_key(settings=settings))])
    def list_models():
        return {"object": "list", "data": []}

    return app
```

- [ ] **Step 4: 运行测试并确认通过**

Run: `G:\Miniconda3\python.exe -m unittest tests.test_gateway_api.TestGatewayApi -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add gateway/__init__.py gateway/auth.py gateway/main.py tests/test_gateway_api.py
git commit -m "新增网关基础入口与鉴权"
```

### Task 3: 实现模型列表、请求模型与消息转换

**Files:**
- Modify: `gateway/schemas.py`
- Modify: `gateway/service.py`
- Modify: `gateway/main.py`
- Modify: `tests/test_gateway_api.py`

- [ ] **Step 1: 写模型列表与消息转换失败测试**

```python
    def test_models_returns_canonical_gateway_models(self):
        response = self.client.get(
            "/v1/models",
            headers={"Authorization": f"Bearer {self.settings.api_key}"},
        )
        self.assertEqual(response.status_code, 200)
        models = [item["id"] for item in response.json()["data"]]
        self.assertIn("gemini-3.5-flash", models)

    def test_chat_completions_alias_route_exists(self):
        payload = {
            "model": "gemini-3.5-flash",
            "messages": [{"role": "user", "content": "hello"}],
        }
        response = self.client.post(
            "/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {self.settings.api_key}"},
        )
        self.assertNotEqual(response.status_code, 404)
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `G:\Miniconda3\python.exe -m unittest tests.test_gateway_api -v`
Expected: FAIL，模型列表为空或 `/chat/completions` 未实现

- [ ] **Step 3: 实现 schema、模型解析与无状态消息映射**

```python
from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str
    content: str | list[dict]
    tool_call_id: str | None = None


class ChatCompletionRequest(BaseModel):
    model: str | None = None
    messages: list[ChatMessage]
    stream: bool = False
    tools: list[dict] | None = None
    tool_choice: str | dict | None = None
    temperature: float | None = None
    max_tokens: int | None = Field(default=None, alias="max_tokens")
    reasoning_effort: str | None = None
    extra_body: dict | None = None
```

```python
CANONICAL_MODELS = [
    "gemini-3.1-pro",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
]
```

- [ ] **Step 4: 运行测试并确认通过**

Run: `G:\Miniconda3\python.exe -m unittest tests.test_gateway_api -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add gateway/schemas.py gateway/service.py gateway/main.py tests/test_gateway_api.py
git commit -m "实现网关模型列表与消息映射"
```

### Task 4: 实现聊天完成、流式输出、图片与扩展文件输入

**Files:**
- Create: `gateway/files.py`
- Create: `gateway/streaming.py`
- Modify: `gateway/service.py`
- Modify: `gateway/main.py`
- Modify: `tests/test_gateway_api.py`

- [ ] **Step 1: 写普通聊天、流式和图片输入失败测试**

```python
    def test_chat_completions_returns_openai_shape(self):
        payload = {
            "model": "gemini-3.5-flash",
            "messages": [{"role": "user", "content": "hello"}],
        }
        response = self.client.post(
            "/v1/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {self.settings.api_key}"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["object"], "chat.completion")
        self.assertEqual(body["choices"][0]["message"]["role"], "assistant")

    def test_streaming_chat_returns_done_marker(self):
        payload = {
            "model": "gemini-3.5-flash",
            "stream": True,
            "messages": [{"role": "user", "content": "hello"}],
        }
        with self.client.stream(
            "POST",
            "/v1/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {self.settings.api_key}"},
        ) as response:
            chunks = list(response.iter_text())
        self.assertTrue(any("[DONE]" in chunk for chunk in chunks))
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `G:\Miniconda3\python.exe -m unittest tests.test_gateway_api -v`
Expected: FAIL，聊天接口未返回 OpenAI 结构或无流式输出

- [ ] **Step 3: 实现最小聊天服务与流式封装**

```python
async def create_chat_completion(
    request: ChatCompletionRequest,
    settings: GatewaySettings,
) -> dict:
    client = await build_gemini_client(settings)
    prompt, files = await translate_messages(request.messages, request.extra_body)
    response = await client.generate_content(prompt, files=files, model=request.model)
    return {
        "id": "chatcmpl-local",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": request.model or settings.default_model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": response.text},
                "finish_reason": "stop",
            }
        ],
    }
```

- [ ] **Step 4: 运行测试并确认通过**

Run: `G:\Miniconda3\python.exe -m unittest tests.test_gateway_api -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add gateway/files.py gateway/streaming.py gateway/service.py gateway/main.py tests/test_gateway_api.py
git commit -m "实现网关聊天完成与流式输出"
```

### Task 5: 实现 tools、文档与最终接入说明

**Files:**
- Modify: `gateway/service.py`
- Create: `gateway/README.md`
- Modify: `README.md`
- Modify: `tests/test_gateway_api.py`

- [ ] **Step 1: 写 tools 输出失败测试**

```python
    def test_tool_call_response_uses_openai_tool_calls_shape(self):
        payload = {
            "model": "gemini-3.5-flash",
            "messages": [{"role": "user", "content": "帮我查天气"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "获取天气",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        }
        response = self.client.post(
            "/v1/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {self.settings.api_key}"},
        )
        self.assertEqual(response.status_code, 200)
        message = response.json()["choices"][0]["message"]
        self.assertTrue("content" in message or "tool_calls" in message)
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `G:\Miniconda3\python.exe -m unittest tests.test_gateway_api -v`
Expected: FAIL，尚未处理 `tools`

- [ ] **Step 3: 实现 tool 协议、README 和主 README 网关入口**

```python
def build_tool_instructions(tools: list[dict] | None) -> str:
    if not tools:
        return ""
    return (
        "如果需要调用工具，只能输出严格 JSON："
        '{"tool_calls":[{"name":"tool_name","arguments":{}}]}'
    )
```

```markdown
# Gateway 使用说明

## 启动

```bash
G:\Miniconda3\python.exe -m gateway.main
```

## AstrBot 接入

- Base URL: `http://127.0.0.1:8000/v1`
- API Key: 启动日志打印值
```

- [ ] **Step 4: 运行测试并确认通过**

Run: `G:\Miniconda3\python.exe -m unittest tests.test_gateway_api -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add gateway/service.py gateway/README.md README.md tests/test_gateway_api.py
git commit -m "补充网关工具调用与接入文档"
```
