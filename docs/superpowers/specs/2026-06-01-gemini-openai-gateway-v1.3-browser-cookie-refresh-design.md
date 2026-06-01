# Gemini OpenAI 网关 V1.3 浏览器 Cookie 同步与运行期续期设计

## 1. 目标

V1.3 的目标是在 V1.2 账户状态与会话治理基础上，进一步解决 `cookies.json` 容易失效、`__Secure-1PSIDTS` 会轮转、服务重启后可能回退到旧 Cookie 的问题。

本次设计主要解决四个问题：

- 启动 gateway 前，需要能从本机浏览器已登录的 Gemini 会话中自动提取 Cookie
- Google 会轮转更新 `__Secure-1PSIDTS`，网关需要把运行中刷新后的 Cookie 持久化回 `cookies.json`
- 服务异常退出时，不应只依赖 shutdown 才回写 Cookie
- 当运行中出现认证失败时，应能尝试从浏览器登录态重新恢复，而不是立即要求用户手动复制 Cookie

V1.3 的核心目标是把“用户手动维护 Cookie”升级为“启动前可自动同步、运行中可持续续期、失败时可自愈一次”。

## 2. 背景与问题

### 2.1 当前现象

当前 gateway 使用 `cookies.json` 作为 Gemini Web 登录态来源。文件中通常包含：

- `__Secure-1PSID`
- `__Secure-1PSIDTS`
- 其他 Google 域 Cookie

但实际使用时仍会出现：

```text
Account status: UNAUTHENTICATED - Session is not authenticated or cookies have expired.
```

排查发现：

- `cookies.json` 文件存在，且包含 `__Secure-1PSID` 和 `__Secure-1PSIDTS`
- 但 Google 仍可能返回 `1016 / UNAUTHENTICATED`
- 这说明 Cookie 值可能已经过期、轮转、与当前浏览器会话不一致，或只支持部分能力

### 2.2 V1.2 已有能力

V1.2 已经具备：

- 启动时读取 `cookies.json`
- 共享 `GeminiClient`
- `GeminiClient.init(auto_refresh=True)`
- shutdown 时把运行时 Cookie 强制回写
- shared client 重建前同步失败 client 的内存 Cookie
- `/v1/account/status` 账户状态诊断

但仍有两个缺口：

- 启动前不会主动从浏览器同步最新登录态
- 周期性 Cookie 回写配置已存在，但尚未形成独立后台 flush 任务

### 2.3 上游已有能力

`gemini_webapi` 内部已经有两类可复用能力：

- `load_browser_cookies(domain_name=...)`
  - 通过 `browser-cookie3` 从 Chrome、Edge、Brave、Firefox 等浏览器读取 Cookie
- `rotate_1psidts(...)`
  - 通过 Google `RotateCookies` 刷新 `__Secure-1PSIDTS`
  - `GeminiClient.start_auto_refresh()` 会周期性调用该能力

V1.3 应复用这些能力，不自行解析浏览器 Cookie 数据库，也不重写 Google Cookie 轮转协议。

## 3. 设计原则

V1.3 采用以下原则：

- 不改变 `/v1/chat/completions` 等 OpenAI-compatible 主接口
- 启动前浏览器 Cookie 同步必须显式执行，不在 `gateway.main` 中静默读取浏览器
- 运行中 Cookie 续期以 `GeminiClient.auto_refresh` 为主
- 网关负责把运行时最新 Cookie 同步到用户可见的 `cookies.json`
- Cookie 值属于敏感信息，日志、错误、测试输出不得泄露真实值
- 浏览器 Cookie 读取失败不能破坏已有手动 `cookies.json` 工作流

## 4. 范围

### 4.1 In Scope

- 新增启动前 Cookie 同步脚本
- 从本机浏览器登录态提取 Gemini/Google Cookie
- 将提取到的 Cookie 原子写入 `GEMINI_GATEWAY_COOKIES_JSON_PATH`
- 增加运行中周期性 Cookie 持久化任务
- 在认证失败重建路径中可选尝试从浏览器刷新 Cookie
- 更新 PowerShell 启动说明和 `gateway/README.md`
- 为 Cookie 同步、持久化、错误路径补充测试

### 4.2 Out of Scope

- 自动打开浏览器并完成 Google 登录
- 绕过 Google 二次验证、风控或地区限制
- 多账号池或多浏览器账号选择 UI
- 在服务运行中频繁轮询浏览器 Cookie 数据库
- 把真实 Cookie 打印到日志或 API 响应
- 修改根目录 README

## 5. 推荐方案

推荐采用“三层 Cookie 自愈”方案：

1. 启动前浏览器同步
2. 运行中上游自动轮转 + 网关周期性持久化
3. 认证失败时浏览器兜底恢复

### 5.1 启动前浏览器同步

新增模块：

```text
gateway/refresh_cookies.py
```

提供命令：

```powershell
uv run --extra browser python -m gateway.refresh_cookies
```

脚本行为：

1. 读取 `GEMINI_GATEWAY_COOKIES_JSON_PATH`，确定目标文件
2. 调用 `load_browser_cookies()` 从浏览器读取 Cookie
3. 从候选浏览器中选择最合适的一组 Google Cookie
4. 校验至少包含 `__Secure-1PSID`
5. 尽量保留 `__Secure-1PSIDTS` 和其他未过期 Google Cookie
6. 原子写入 `cookies.json`
7. 输出脱敏摘要，例如：

```text
Browser cookies refreshed: source=edge, has_1psid=true, has_1psidts=true, count=8
```

### 5.2 运行中自动轮转

继续使用 `GeminiClient.init(auto_refresh=True)`。

`gemini_webapi` 会在后台周期性调用：

```text
rotate_1psidts()
```

该能力负责与 Google 交互并刷新 `__Secure-1PSIDTS`。

V1.3 不替代这部分逻辑，只补上“把最新运行时 Cookie 写回 `cookies.json`”。

### 5.3 周期性持久化

在 `GatewayService` 中新增后台任务：

```text
start_cookie_persist_task()
stop_cookie_persist_task()
```

任务行为：

1. 按 `GEMINI_GATEWAY_COOKIE_PERSIST_INTERVAL_SECONDS` 周期运行
2. 获取当前 shared `GeminiClient.cookies`
3. 与 `_cached_cookies` 合并
4. 若检测到 Cookie 内容有变化，则调用 `persist_cookies(...)`
5. 写入失败只记录 warning，不中断服务

该任务与 shutdown 强制回写并存：

- 周期性任务降低异常退出导致的 Cookie 丢失
- shutdown flush 保留最终兜底

### 5.4 认证失败兜底恢复

当请求路径遇到认证相关异常，例如：

- `AuthError`
- 上游返回认证失败并触发 shared client 重建

可选执行一次浏览器 Cookie 刷新：

1. 从浏览器重新同步 Cookie 到 `cookies.json`
2. 更新 `GatewayService._cached_cookies`
3. 基于新 Cookie 重建 shared `GeminiClient`
4. 当前请求最多重试一次

该能力必须受配置控制，避免服务在无浏览器环境中反复尝试。

建议新增配置：

```text
GEMINI_GATEWAY_BROWSER_COOKIE_REFRESH_ENABLED=true
GEMINI_GATEWAY_BROWSER_COOKIE_REFRESH_ON_AUTH_ERROR=true
```

默认策略建议：

- 启动前脚本由用户显式执行
- 运行中认证失败兜底默认关闭或只在安装 `browser` extra 后显式开启

## 6. Cookie 来源选择策略

### 6.1 候选域名

推荐读取域名：

- `.google.com`
- `google.com`
- `gemini.google.com`

实际实现中可先调用：

```python
load_browser_cookies(domain_name=".google.com")
```

如果读取结果为空，再尝试：

```python
load_browser_cookies(domain_name="gemini.google.com")
```

### 6.2 候选浏览器排序

`load_browser_cookies()` 返回多个浏览器的 Cookie 列表时，按以下规则选择：

1. 同时包含 `__Secure-1PSID` 和 `__Secure-1PSIDTS`
2. 包含 `__Secure-1PSID`
3. Cookie 数量更多
4. 固定浏览器优先级作为最终 tie-breaker

建议默认浏览器优先级：

```text
edge > chrome > brave > chromium > firefox > vivaldi > opera
```

可选配置：

```text
GEMINI_GATEWAY_BROWSER_COOKIE_SOURCE=edge
```

当用户显式指定来源时：

- 只接受该浏览器来源
- 找不到有效 Cookie 时返回明确错误

### 6.3 写入格式

继续使用 V1.2 兼容格式：

```json
{
  "cookies": {
    "__Secure-1PSID": "...",
    "__Secure-1PSIDTS": "..."
  },
  "updated_at": 1780000000,
  "source": "edge"
}
```

`GatewayService.load_cookies()` 已支持 `cookies` 字段对象格式，因此该格式可直接复用。

## 7. 配置设计

新增配置项：

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `GEMINI_GATEWAY_BROWSER_COOKIE_REFRESH_ENABLED` | `false` | 是否允许网关运行中调用浏览器 Cookie 刷新能力 |
| `GEMINI_GATEWAY_BROWSER_COOKIE_REFRESH_ON_AUTH_ERROR` | `false` | 认证失败时是否尝试从浏览器重新同步 Cookie |
| `GEMINI_GATEWAY_BROWSER_COOKIE_SOURCE` | 空 | 指定浏览器来源；为空时自动选择 |
| `GEMINI_GATEWAY_BROWSER_COOKIE_DOMAIN` | `.google.com` | 浏览器 Cookie 查询域名 |

复用现有配置：

| 配置 | 说明 |
| --- | --- |
| `GEMINI_GATEWAY_COOKIES_JSON_PATH` | Cookie 写入目标 |
| `GEMINI_GATEWAY_COOKIE_PERSIST_ENABLED` | 是否启用运行时 Cookie 持久化 |
| `GEMINI_GATEWAY_COOKIE_PERSIST_INTERVAL_SECONDS` | 周期性回写间隔 |

## 8. PowerShell 启动体验

推荐新增一键启动脚本：

```text
gateway/start_gateway.ps1
```

行为：

1. dot-source 或调用 `set_gateway_env.ps1`
2. 执行 `uv sync --extra browser`
3. 执行 `uv run --extra browser python -m gateway.refresh_cookies`
4. 执行 `uv run python -m gateway.main`

也保留手动流程：

```powershell
cd G:\VSCODE-G\Gemini-API
uv sync --extra browser
. .\gateway\set_gateway_env.ps1 -ApiKey "gemini-api"
uv run --extra browser python -m gateway.refresh_cookies
uv run python -m gateway.main
```

`set_gateway_env.ps1` 应更新启动提示，明确推荐先刷新 Cookie：

```text
uv run --extra browser python -m gateway.refresh_cookies
uv run python -m gateway.main
```

## 9. 错误处理

### 9.1 未安装 browser extra

如果缺少 `browser-cookie3`，脚本应返回清晰错误：

```text
browser-cookie3 is not installed. Run: uv sync --extra browser
```

不得输出堆栈作为主要用户提示。

### 9.2 浏览器未登录 Gemini

如果找不到 `__Secure-1PSID`：

```text
No valid Gemini browser cookies found. Please log in to https://gemini.google.com in your browser first.
```

### 9.3 浏览器数据库被锁或权限不足

脚本应继续尝试其他浏览器来源。

如果所有来源失败：

- 输出失败原因摘要
- 不覆盖现有 `cookies.json`

### 9.4 写入失败

写入 `cookies.json` 必须使用临时文件 + 原子替换。

写入失败时：

- 启动前脚本应返回非零退出码
- 运行中周期性持久化只记录 warning
- 认证失败恢复路径应把恢复视为失败，并回到原有错误处理

## 10. 安全要求

Cookie 是敏感凭据，V1.3 必须满足：

- 不在日志中打印 Cookie 值
- 不在测试输出中打印 Cookie 值
- 不把 Cookie 写入 README、spec、测试快照
- `cookies.json` 继续保持在 `.gitignore`
- 错误信息只显示 Cookie 名称、是否存在、来源浏览器、数量等脱敏信息

## 11. 对外行为要求

V1.3 必须保持以下行为不变：

- `GET /v1/models`
- `GET /v1/account/status`
- `POST /v1/chat/completions`
- `POST /chat/completions`
- OpenAI-compatible Base URL
- Bearer 鉴权方式
- 模型映射
- 图片、文件、tools 协议

Cookie 同步与续期能力是启动和会话治理增强，不改变客户端调用协议。

## 12. 测试设计

### 12.1 `gateway.refresh_cookies` 单元测试

测试点：

- 从多浏览器候选中选择同时包含 `__Secure-1PSID` 和 `__Secure-1PSIDTS` 的来源
- 只有 `__Secure-1PSID` 时仍可写入，但提示缺少 `__Secure-1PSIDTS`
- 找不到 `__Secure-1PSID` 时失败且不覆盖旧文件
- 写入格式兼容 `GatewayService.load_cookies()`
- 输出不包含真实 Cookie 值

### 12.2 `GatewayService` 周期性持久化测试

测试点：

- warmup 后启动持久化任务
- shutdown 时停止持久化任务
- Cookie 内容变化时触发 `persist_cookies`
- Cookie 未变化时不重复写入
- 写入失败只记录 warning，不中断请求

### 12.3 认证失败恢复测试

测试点：

- 配置关闭时不调用浏览器刷新
- 配置开启时认证失败会尝试刷新一次
- 刷新成功后使用新 Cookie 重建 shared client
- 刷新失败后返回原有规范错误
- 当前请求最多重试一次，不允许无限循环

### 12.4 文档测试

测试点：

- `gateway/README.md` 包含 `uv sync --extra browser`
- `gateway/README.md` 包含 `gateway.refresh_cookies`
- `set_gateway_env.ps1` 提示启动前刷新 Cookie

## 13. 风险与取舍

### 13.1 风险

- `browser-cookie3` 对不同浏览器、不同系统权限的支持不完全一致
- 浏览器正在运行时，Cookie 数据库可能被锁
- 本机浏览器可能登录了多个 Google 账号，自动选择不一定符合用户预期
- Google Cookie 轮转规则可能继续变化
- 运行中读取浏览器 Cookie 可能带来不确定延迟

### 13.2 取舍

V1.3 明确选择：

- 启动前浏览器同步显式执行
- 运行中续期以 `GeminiClient.auto_refresh` 为主
- 运行中浏览器读取只作为认证失败兜底，不作为常规轮询机制

这样可以提升自动化程度，同时避免让服务持续依赖浏览器数据库状态。

## 14. 验证标准

V1.3 通过标准：

- 用户登录浏览器 Gemini 后，可通过脚本生成或更新 `cookies.json`
- 启动 gateway 时无需手动复制 Cookie
- 运行中 `__Secure-1PSIDTS` 轮转后能定期写回 `cookies.json`
- 服务异常退出时，最多只丢失一个持久化周期内的 Cookie 更新
- 认证失败恢复路径在配置开启时可尝试从浏览器重新同步 Cookie
- Cookie 值不会出现在日志、测试输出或文档中
- 现有 OpenAI-compatible 接口不回归

## 15. 推荐实施顺序

1. 新增 `gateway.refresh_cookies` 核心函数与 CLI
2. 增加浏览器 Cookie 选择与原子写入测试
3. 增加 `GatewaySettings` 浏览器 Cookie 配置项
4. 增加 `GatewayService` 周期性 Cookie 持久化任务
5. 增加认证失败时浏览器刷新兜底路径
6. 更新 `set_gateway_env.ps1`
7. 更新 `gateway/README.md`
8. 可选新增 `gateway/start_gateway.ps1`
9. 跑 gateway 单测与一次本机真实启动验证

## 16. 下一步

本 spec 批准后，进入 V1.3 implementation plan 阶段。计划应按 TDD 拆分任务，优先保证 Cookie 同步脚本可独立测试，再接入 gateway 生命周期与认证失败恢复路径。
