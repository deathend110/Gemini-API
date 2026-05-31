# Gemini Gateway 使用说明

本目录提供一个本地 OpenAI-compatible 网关，方便把 `gemini_webapi` 暴露给 AstrBot、Fitness Agent 或其他支持 OpenAI Chat Completions 协议的客户端。

当前 V1.2 核心能力：

- `GET /v1/models`
- `GET /v1/account/status`
- `POST /v1/chat/completions`
- `POST /chat/completions`
- `stream=true` 的 SSE 输出，结束返回 `data: [DONE]`
- OpenAI 风格 `tools` 输入与 `tool_calls` 输出
- `messages[].content[]` 图片输入：`type=text`、`type=image_url`
- 扩展文件输入：`extra_body.files[{name, content_type, data_base64}]`
- 启动阶段账户探测与账户模式摘要
- `cookies.json` 原子回写、重建前 cookies 同步、strict mode 能力门槛校验

## 1. 启动前准备

### 1.1 安装依赖

在仓库根目录安装项目依赖：

```bash
pip install -e .
```

### 1.2 准备 Cookies

网关通过本地 `cookies.json` 读取 Gemini Web 登录态。至少需要 `__Secure-1PSID`，建议同时包含 `__Secure-1PSIDTS`。

示例：

```json
{
  "__Secure-1PSID": "your-cookie",
  "__Secure-1PSIDTS": "your-cookie-ts"
}
```

### 1.3 配置环境变量

常用环境变量：

```bash
set GEMINI_GATEWAY_API_KEY=replace-with-your-key
set GEMINI_GATEWAY_COOKIES_JSON_PATH=G:\Gemini API\Gemini-API\cookies.json
set GEMINI_GATEWAY_HOST=127.0.0.1
set GEMINI_GATEWAY_PORT=8010
set GEMINI_GATEWAY_DEFAULT_MODEL=gemini-3.5-flash
set GEMINI_GATEWAY_DEFAULT_REASONING_EFFORT=standard
set GEMINI_GATEWAY_PROXY=http://127.0.0.1:10090/
set GEMINI_GATEWAY_COOKIE_PERSIST_ENABLED=true
set GEMINI_GATEWAY_COOKIE_PERSIST_INTERVAL_SECONDS=60
set GEMINI_GATEWAY_ACCOUNT_PROBE_ENABLED=true
set GEMINI_GATEWAY_ACCOUNT_STRICT_MODE=false
set GEMINI_GATEWAY_ACCOUNT_REQUIRED_LEVEL=basic
```

说明：

- 模型与思考强度分离；未传 `reasoning_effort` 时默认是 `standard`
- 当前支持 `reasoning_effort=standard|extended`；`extended` 会作为额外推理提示注入到 prompt
- 代理、Cookies 路径等本机依赖请显式配置，不要假设调用方环境
- `GEMINI_GATEWAY_ACCOUNT_REQUIRED_LEVEL` 支持 `basic`、`standard`、`full_web`
- `GEMINI_GATEWAY_COOKIE_PERSIST_INTERVAL_SECONDS` 必须为正整数

也可以直接使用仓库内置脚本为当前 PowerShell 会话设置环境变量：

```powershell
. .\gateway\set_gateway_env.ps1 -ApiKey "your-local-key"
```

说明：

- 前面的 `. ` 不能省略，这是 PowerShell 的 dot-source 语法
- 脚本默认把 `cookies.json` 指向仓库根目录下的 `cookies.json`
- 如需自定义代理、模型或端口，可直接传参数，例如：

```powershell
. .\gateway\set_gateway_env.ps1 `
  -ApiKey "your-local-key" `
  -Proxy "http://127.0.0.1:10090/" `
  -DefaultModel "gemini-3.5-flash" `
  -DefaultReasoningEffort "standard" `
  -Port 8010
```

或者在`set_gateway_env.ps1`内部设置好，直接powershell进入项目根目录运行. .\gateway\set_gateway_env.ps1

## 2. 启动网关

在仓库根目录执行：

```bash
python -m gateway.main
```

默认监听：

```text
http://127.0.0.1:8010
```

启动后会打印：

- `Base URL`
- `API Key`
- `Default model`
- `Default reasoning effort`
- `Account mode`

健康检查：

```bash
curl http://127.0.0.1:8010/health
```

## 2.1 V1.1 性能优化说明

V1.1 在不改变 OpenAI-compatible 接口形状的前提下，主要做了三项性能优化：

- 网关启动时会预热上游 Gemini 会话，首个真实请求不再承担完整初始化成本
- 普通对话与流式对话会复用共享上游 `GeminiClient`，不再每请求都 `init/close`
- `cookies.json` 会在启动后加载进内存缓存，后续请求不再重复读文件

同时，V1.1 增加了共享 client 失效后的受控重建：

- 当上游出现 `AuthError`、`TimeoutError`、`APIError`、`GeminiError` 时，当前请求最多触发一次重建重试
- 流式请求只会在首个 chunk 之前失败时重建，避免已经输出的内容重复

注意事项：

- 首次启动阶段会多花一点时间，因为 startup 里会先完成 warmup
- 如果你手动更新了本地 `cookies.json`，请重启 gateway，让新的 cookies 重新加载生效
- 当前优化重点是连续请求延迟，不是高并发连接池

## 2.2 V1.2 账户会话治理说明

V1.2 在 V1.1 的共享 client 基础上，补上了账户状态、cookies 持久化和诊断能力：

- `GatewayService` 会在 warmup 后构建账户快照，区分 `available`、`degraded`、`blocked`
- startup 摘要会输出 `Account mode`
- `GET /v1/account/status` 可返回标准化账户状态
- shutdown 时会把当前共享 client 的最新 cookies 原子回写到 `cookies.json`
- shared client 重建前会先同步内存 cookies，避免回退到旧 cookies
- 可通过 strict mode 在 startup 阶段要求 `basic`、`standard` 或 `full_web` 能力门槛

说明：

- `UNAUTHENTICATED` warning 不再直接等同于“完全不可用”，应结合账户快照一起判断
- 默认不启用 strict mode，保持现有基础对话兼容性
- `cookies.json` 会以原子替换方式更新，降低异常中断导致文件损坏的风险

## 3. OpenAI-compatible 接口

### 3.1 列模型

```bash
curl http://127.0.0.1:8010/v1/models ^
  -H "Authorization: Bearer replace-with-your-key"
```

### 3.2 账户状态

```bash
curl http://127.0.0.1:8010/v1/account/status ^
  -H "Authorization: Bearer replace-with-your-key"
```

返回示例：

```json
{
  "raw_account_status": "UNAUTHENTICATED",
  "raw_account_status_code": 1016,
  "chat_available": true,
  "advanced_models_available": false,
  "deep_research_available": false,
  "full_web_capability_available": false,
  "mode": "degraded",
  "unavailable_reasons": [
    "advanced_models_unavailable",
    "deep_research_unavailable"
  ]
}
```

### 3.3 聊天补全

```bash
curl http://127.0.0.1:8010/v1/chat/completions ^
  -H "Authorization: Bearer replace-with-your-key" ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"gemini-3.5-flash\",\"messages\":[{\"role\":\"user\",\"content\":\"你好\"}]}"
```

### 3.4 流式输出

```bash
curl http://127.0.0.1:8010/v1/chat/completions ^
  -H "Authorization: Bearer replace-with-your-key" ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"gemini-3.5-flash\",\"stream\":true,\"messages\":[{\"role\":\"user\",\"content\":\"请分三点介绍你自己\"}]}"
```

返回为 SSE，最后一条是：

```text
data: [DONE]
```

## 4. 图片与文件输入

### 4.1 `data:` 图片

```json
{
  "model": "gemini-3.5-flash",
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

### 4.2 远程图片

`image_url.url` 也支持 `http://` 或 `https://`。

### 4.3 扩展文件输入

```json
{
  "model": "gemini-3.5-flash",
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

## 5. Tools 约定

网关接收 OpenAI `tools`，并把工具约束注入到 Gemini prompt。

如果 Gemini 按约定返回 JSON：

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

网关会转成 OpenAI 风格的 `message.tool_calls`。非流式已支持；流式场景会尽量输出 `delta.tool_calls`。

## 6. AstrBot 接入

AstrBot 侧按 OpenAI 服务接入即可：

- Base URL: `http://127.0.0.1:8010/v1`
- API Key: 你配置的 `GEMINI_GATEWAY_API_KEY`
- Model: `gemini-3.5-flash`、`gemini-3.1-pro` 或 `gemini-3.1-flash-lite`

如果 AstrBot 支持自定义请求体，可按需透传：

- `reasoning_effort`
- `extra_body.files`
- `tools`
- `stream`

建议先用 AstrBot 的模型测试或普通对话确认：

1. 鉴权正常
2. `messages` 会完整透传
3. 代理与 Cookies 路径可被当前运行环境访问
4. `/v1/account/status` 返回的 `mode` 符合当前账户能力预期

## 7. 当前限制

- V1.2 仍为无状态网关，客户端必须每次传完整 `messages`
- `tools` 采用最小可用协议，依赖模型按约定输出 JSON
- 流式工具调用是兼容型实现，不保证逐 token 输出工具参数
- 共享 client 当前是单实例复用，优先优化连续请求延迟，不是最终并发形态
- strict mode 的能力判定仍基于当前网页端可观测能力，不等于官方 Gemini API 的全部特性
