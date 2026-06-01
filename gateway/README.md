# Gemini Gateway 使用说明

`gateway/` 提供一个本地 FastAPI 网关，把 `gemini_webapi` 封装成 OpenAI-compatible Chat Completions 接口。它适合接入 AstrBot、Fitness Agent 或其他支持 OpenAI `base_url + api_key + model` 配置方式的客户端。

## 1. 项目概览

网关对外提供：

- `GET /health`
- `GET /v1/models`
- `GET /v1/account/status`
- `POST /v1/chat/completions`
- `POST /chat/completions`

主要支持：

- OpenAI 风格 Bearer 鉴权
- 非流式与 SSE 流式聊天补全
- OpenAI 风格 `tools`
- 图片输入：`messages[].content[].image_url`
- 文件输入：`extra_body.files`
- 本地 `cookies.json` 登录态读取与持久化

默认监听地址：

```text
http://127.0.0.1:8010
```

OpenAI-compatible Base URL：

```text
http://127.0.0.1:8010/v1
```

## 2. 快速启动

本仓库使用 `uv` 管理本地虚拟环境。请在仓库根目录执行：

```powershell
cd G:\VSCODE-G\Gemini-API
uv sync
. .\gateway\set_gateway_env.ps1 -ApiKey "your-local-key"
uv run python -m gateway.main
```

说明：

- `uv sync` 会按 `pyproject.toml` 和 `uv.lock` 创建或更新本地 `.venv`
- `. .\gateway\set_gateway_env.ps1` 前面的 `. ` 是 PowerShell dot-source 语法，用于把环境变量写入当前会话
- 启动网关时统一使用 `uv run python -m gateway.main`，不要直接使用 conda/base 环境里的 `python`

如果需要刷新 `cookies.json`，推荐使用手动启动专用 Chrome profile 的流程。
当前实现会从正在运行的专用 Chrome 提取当前登录态。
首次运行 `gateway.refresh_cookies` 时：

- 如果缺少调试会话，请先关闭该专用 profile 当前已普通打开的 Chrome 窗口，再复制脚本打印出的 PowerShell 命令重新启动
- 如果已连上专用 Chrome 但还没有有效 Gemini 登录态，请直接在该窗口中完成登录
- 登录完成后保持该专用 Chrome 继续运行，不要关闭窗口，再重新执行刷新命令

```powershell
uv sync --extra browser
. .\gateway\set_gateway_env.ps1 -ApiKey "your-local-key"
uv run --extra browser python -m gateway.refresh_cookies
uv run python -m gateway.main
```

也可以直接用一键脚本：

```powershell
.\gateway\start_gateway.ps1 -ApiKey "your-local-key"
```

`start_gateway.ps1` 的默认行为已经改成：

- 如已有可用 `cookies.json`（至少包含 `__Secure-1PSID`），直接启动网关，不强制先做 live refresh
- 缺少可用 `cookies.json` 时，才会自动进入 `gateway.refresh_cookies` 流程
- 如果只是想主动刷新登录态，也可以跳过一键脚本，单独执行 `gateway.refresh_cookies`

启动后终端会输出：

- `Base URL`
- `API Key`
- `Default model`
- `Default reasoning effort`
- `Account mode`

健康检查：

```powershell
curl http://127.0.0.1:8010/health
```

推荐启动顺序分两种：

1. 如已有可用 `cookies.json`：
   执行 `uv sync`
   执行 `. .\gateway\set_gateway_env.ps1 -ApiKey "your-local-key"`
   执行 `uv run python -m gateway.main`
   或直接执行 `.\gateway\start_gateway.ps1 -ApiKey "your-local-key"`
2. 如缺少可用 `cookies.json`：
   执行 `uv sync --extra browser`
   执行 `. .\gateway\set_gateway_env.ps1 -ApiKey "your-local-key"`
   执行 `uv run --extra browser python -m gateway.refresh_cookies`
   如果脚本提示缺少调试会话，先关闭该专用 profile 当前已普通打开的 Chrome 窗口，再复制它打印的 PowerShell 启动命令
   在专用 Chrome 中完成 Gemini 登录后，保持该专用 Chrome 继续运行，不要关闭窗口
   再次执行 `uv run --extra browser python -m gateway.refresh_cookies`
   看到 `Browser cookies refreshed:` 后，再执行 `uv run python -m gateway.main`
   这一路径也可以通过 `.\gateway\start_gateway.ps1 -ApiKey "your-local-key"` 触发；它会在缺少可用 `cookies.json` 时再按手动 profile 流程刷新

## 3. Cookies

网关通过本地 `cookies.json` 读取 Gemini Web 登录态。至少需要 `__Secure-1PSID`，建议同时包含 `__Secure-1PSIDTS`。

`gateway.refresh_cookies` 会从专用 Chrome profile 读取 Gemini Web 登录态，不会复用你的日常浏览器 profile，也不会要求在配置中保存 Google 账号密码。
推荐主路径是：通过 `gateway.refresh_cookies` 打印出的命令启动专用 Chrome，登录后保持该窗口继续运行，再重新执行 `gateway.refresh_cookies` 完成提取；只有在缺少调试会话且同 profile 已被普通方式打开时，才需要先关闭那个普通打开的窗口。

仓库根目录的 `cookies.json` 示例：

```json
{
  "__Secure-1PSID": "your-cookie",
  "__Secure-1PSIDTS": "your-cookie-ts"
}
```

也支持带 `cookies` 字段的对象格式：

```json
{
  "cookies": {
    "__Secure-1PSID": "your-cookie",
    "__Secure-1PSIDTS": "your-cookie-ts"
  }
}
```

注意：

- `cookies.json` 属于本地敏感文件，不要提交到 Git
- 如果手动更新了 `cookies.json`，建议重启网关
- 代理、Cookies 路径等本机依赖请显式配置，不要假设调用方环境

## 4. 环境变量

推荐通过脚本设置环境变量：

```powershell
. .\gateway\set_gateway_env.ps1 -ApiKey "your-local-key"
```

常用参数：

```powershell
. .\gateway\set_gateway_env.ps1 `
  -ApiKey "your-local-key" `
  -Proxy "http://127.0.0.1:10090/" `
  -DefaultModel "gemini-3-flash" `
  -DefaultReasoningEffort "standard" `
  -Port 8010
```

常用环境变量：

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `GEMINI_GATEWAY_API_KEY` | 客户端访问网关时使用的 Bearer Token | 脚本默认 `gemini-api` |
| `GEMINI_GATEWAY_COOKIES_JSON_PATH` | `cookies.json` 路径 | 仓库根目录 `cookies.json` |
| `GEMINI_GATEWAY_HOST` | 监听地址 | `127.0.0.1` |
| `GEMINI_GATEWAY_PORT` | 监听端口 | `8010` |
| `GEMINI_GATEWAY_DEFAULT_MODEL` | 默认模型 | `gemini-3-flash` |
| `GEMINI_GATEWAY_DEFAULT_REASONING_EFFORT` | 默认推理强度 | `standard` |
| `GEMINI_GATEWAY_PROXY` | Gemini 上游请求代理 | `http://127.0.0.1:10090/` |
| `GEMINI_GATEWAY_BROWSER_COOKIE_REFRESH_ENABLED` | 是否允许浏览器 Cookie 刷新 | `false` |
| `GEMINI_GATEWAY_BROWSER_COOKIE_REFRESH_ON_AUTH_ERROR` | 认证失败时是否尝试刷新浏览器 Cookie | `false` |
| `GEMINI_GATEWAY_BROWSER_PROFILE_DIR` | 专用 Chrome profile 路径 | 用户目录下 `.gemini-api\selenium-profile` |
| `GEMINI_GATEWAY_BROWSER_LOGIN_WAIT_SECONDS` | 兼容保留参数，当前 live-cookie 刷新流程不使用该等待时长 | `300` |
| `GEMINI_GATEWAY_BROWSER_POLL_INTERVAL_SECONDS` | 兼容保留参数，当前 live-cookie 刷新流程不使用该轮询间隔 | `2` |
| `GEMINI_GATEWAY_BROWSER_PAGE_LOAD_TIMEOUT_SECONDS` | 兼容保留参数，当前 live-cookie 刷新流程不使用该页面加载超时 | `60` |
| `GEMINI_GATEWAY_BROWSER_HEADLESS` | 兼容保留字段，当前手动 profile 登录流程不会使用 | `false` |
| `GEMINI_GATEWAY_ACCOUNT_PROBE_ENABLED` | 是否启动账户能力探测 | `true` |
| `GEMINI_GATEWAY_ACCOUNT_STRICT_MODE` | 是否要求启动时满足账户能力门槛 | `false` |
| `GEMINI_GATEWAY_ACCOUNT_REQUIRED_LEVEL` | strict mode 的能力要求 | `basic` |

`reasoning_effort` 支持：

- `standard`
- `extended`

## 5. OpenAI-compatible 接口

模型名严格使用 Gemini Web 上游名称：

- `gemini-3-flash`
- `gemini-3-flash-thinking`
- `gemini-3-pro`

历史包装名如 `gemini-3.5-flash`、`gemini-3.1-pro`、`gemini-3.1-flash-lite` 不再作为 gateway 模型名使用。

### 5.1 列模型

```powershell
curl http://127.0.0.1:8010/v1/models `
  -H "Authorization: Bearer your-local-key"
```

### 5.2 账户状态

```powershell
curl http://127.0.0.1:8010/v1/account/status `
  -H "Authorization: Bearer your-local-key"
```

### 5.3 动态模型调试

如果你想查看 Gemini Web 当前实际解析到的动态模型注册表，可以请求：

```powershell
curl http://127.0.0.1:8010/v1/debug/models `
  -H "Authorization: Bearer your-local-key"
```

返回内容会包含：

- `model_id`
- `model_name`
- `display_name`
- `description`
- `is_available`
- `advanced_only`
- `capacity`
- `capacity_field`

如果返回 `debug_models_unavailable`，通常说明网关当前还没有完成一次成功的 Gemini client warmup。

### 5.4 聊天补全

```powershell
curl http://127.0.0.1:8010/v1/chat/completions `
  -H "Authorization: Bearer your-local-key" `
  -H "Content-Type: application/json" `
  -d "{\"model\":\"gemini-3-flash\",\"messages\":[{\"role\":\"user\",\"content\":\"你好\"}]}"
```

### 5.5 流式输出

```powershell
curl http://127.0.0.1:8010/v1/chat/completions `
  -H "Authorization: Bearer your-local-key" `
  -H "Content-Type: application/json" `
  -d "{\"model\":\"gemini-3-flash\",\"stream\":true,\"messages\":[{\"role\":\"user\",\"content\":\"请分三点介绍你自己\"}]}"
```

流式响应为 SSE，结束标记为：

```text
data: [DONE]
```

## 6. 图片与文件输入

### 6.1 图片输入

`image_url.url` 支持 `data:`、`http://` 和 `https://`。

```json
{
  "model": "gemini-3-flash",
  "messages": [
    {
      "role": "user",
      "content": [
        { "type": "text", "text": "请描述图片内容" },
        {
          "type": "image_url",
          "image_url": {
            "url": "data:image/png;base64,...."
          }
        }
      ]
    }
  ]
}
```

### 6.2 文件输入

通过 `extra_body.files` 传入 base64 文件：

```json
{
  "model": "gemini-3-flash",
  "messages": [
    { "role": "user", "content": "请总结附件" }
  ],
  "extra_body": {
    "files": [
      {
        "name": "report.txt",
        "content_type": "text/plain",
        "data_base64": "SGVsbG8sIEdlbWluaSE="
      }
    ]
  }
}
```

## 7. Tools

网关接收 OpenAI `tools`，并把工具约束注入 Gemini prompt。如果 Gemini 按约定返回 JSON，网关会转换为 OpenAI 风格的 `message.tool_calls`。

Gemini 侧期望返回格式：

```json
{
  "tool_calls": [
    {
      "name": "get_weather",
      "arguments": {
        "city": "深圳"
      }
    }
  ]
}
```

## 8. AstrBot 接入

AstrBot 侧按 OpenAI 服务接入：

- Base URL: `http://127.0.0.1:8010/v1`
- API Key: `GEMINI_GATEWAY_API_KEY`
- Model: `gemini-3-flash`、`gemini-3-flash-thinking` 或 `gemini-3-pro`

建议先确认：

1. `/health` 返回正常
2. `/v1/models` 鉴权正常
3. `/v1/account/status` 的账户状态符合预期
4. 普通聊天补全能返回内容

## 9. 使用注意

- 网关是无状态 OpenAI-compatible 服务，客户端需要在每次请求中传完整 `messages`
- `tools` 是兼容层实现，依赖 Gemini 按约定输出 JSON
- 如果请求明显变慢，优先检查代理、Gemini Web 登录态、客户端传入的历史消息长度
- 如果 `cookies.json` 失效，请重新从 Gemini Web 登录态中获取 Cookie
- 如果想在认证失败时自动从浏览器刷新 Cookie，可以显式开启 `GEMINI_GATEWAY_BROWSER_COOKIE_REFRESH_ENABLED` 和 `GEMINI_GATEWAY_BROWSER_COOKIE_REFRESH_ON_AUTH_ERROR`
