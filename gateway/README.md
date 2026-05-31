# Gemini Gateway 使用说明

本目录提供一个本地 OpenAI-compatible 网关，方便把 `gemini_webapi` 暴露给 AstrBot、Fitness Agent 或其他支持 OpenAI Chat Completions 协议的客户端。

当前 V1.0 核心能力：

- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /chat/completions`
- `stream=true` 的 SSE 输出，结束返回 `data: [DONE]`
- OpenAI 风格 `tools` 输入与 `tool_calls` 输出
- `messages[].content[]` 图片输入：`type=text`、`type=image_url`
- 扩展文件输入：`extra_body.files[{name, content_type, data_base64}]`

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
set GEMINI_GATEWAY_PORT=8000
set GEMINI_GATEWAY_DEFAULT_MODEL=gemini-3.5-flash
set GEMINI_GATEWAY_DEFAULT_REASONING_EFFORT=standard
set GEMINI_GATEWAY_PROXY=http://127.0.0.1:10090/
```

说明：

- 模型与思考强度分离；未传 `reasoning_effort` 时默认是 `standard`
- 当前支持 `reasoning_effort=standard|extended`；`extended` 会作为额外推理提示注入到 prompt
- 代理、Cookies 路径等本机依赖请显式配置，不要假设调用方环境

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
  -Port 8000
```

## 2. 启动网关

在仓库根目录执行：

```bash
python -m gateway.main
```

默认监听：

```text
http://127.0.0.1:8000
```

启动后会打印：

- `Base URL`
- `API Key`
- `Default model`
- `Default reasoning effort`

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

## 3. OpenAI-compatible 接口

### 3.1 列模型

```bash
curl http://127.0.0.1:8000/v1/models ^
  -H "Authorization: Bearer replace-with-your-key"
```

### 3.2 聊天补全

```bash
curl http://127.0.0.1:8000/v1/chat/completions ^
  -H "Authorization: Bearer replace-with-your-key" ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"gemini-3.5-flash\",\"messages\":[{\"role\":\"user\",\"content\":\"你好\"}]}"
```

### 3.3 流式输出

```bash
curl http://127.0.0.1:8000/v1/chat/completions ^
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

- Base URL: `http://127.0.0.1:8000/v1`
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

## 7. 当前限制

- V1.0 仍为无状态网关，客户端必须每次传完整 `messages`
- `tools` 采用最小可用协议，依赖模型按约定输出 JSON
- 流式工具调用是兼容型实现，不保证逐 token 输出工具参数
