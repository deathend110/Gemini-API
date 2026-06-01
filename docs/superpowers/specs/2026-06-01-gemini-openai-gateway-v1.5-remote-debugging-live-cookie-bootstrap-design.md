# Gemini OpenAI 网关 V1.5 运行中 Chrome Remote Debugging 取 Live Cookie 设计

## 1. 目标

V1.5 的目标是在 V1.4“手动专用 profile 登录”方案基础上，继续解决“启动前初始 Cookie 获取”体验和时效性不足的问题。

本次设计聚焦三个结果：

- 保留专用 Chrome profile 的隔离性
- 不改变 Gemini-API 运行中的自动 Cookie 续期机制
- 将 `gateway.refresh_cookies` 从“关闭 Chrome 后读取本地 Cookie 数据库”升级为“连接运行中的专用 Chrome，通过 remote debugging 直接读取 live cookie”

V1.5 的核心不是改造运行期续期，而是改造“初始 Cookie 引导与提取”的最后一段，让启动前获取到的就是浏览器当前会话里的最新登录态。

## 2. 背景与问题

### 2.1 V1.4 已解决的问题

V1.4 已明确放弃 Selenium 受控登录，改为：

- 用户手动启动专用 Chrome profile
- 用户手动登录 Gemini
- `gateway.refresh_cookies` 再从 profile 中读取 Cookie 并写回 `cookies.json`

这一步已经绕开了“由软件控制的 Chrome 禁止登录 Google 账号”的限制。

### 2.2 V1.4 仍存在的问题

虽然 V1.4 已可用，但当前读取方式仍然依赖专用 profile 目录中的本地 Cookie 数据库文件。

这会带来两个现实问题：

- 当专用 Chrome 仍在运行时，Windows 可能锁住 `Default\\Network\\Cookies`，导致读取报 `Permission denied`
- 即使用户关闭了 Chrome，读取到的也只是浏览器最近一次落盘后的状态，不一定是当前运行会话里的最新 Cookie

这说明 V1.4 的剩余问题不再是“能否登录”，而是“如何稳定、及时地拿到当前正在使用的 Cookie”。

### 2.3 问题本质

Gemini-API 自己在运行期刷新 Cookie 的方式，本质上不是重新读取浏览器磁盘文件，而是：

- 基于已建立的会话发起 `RotateCookies` 请求
- 直接刷新当前会话中的 `__Secure-1PSIDTS`
- 再把更新后的 Cookie 持久化到自己的缓存文件

这说明：

- 运行中的最新 Cookie，更接近“当前会话状态”
- 而不是“浏览器关闭后磁盘上残留的最后一份 SQLite 数据”

因此，V1.5 要解决的是“启动前如何拿到 live cookie”，而不是替换 Gemini-API 自己的自动续期能力。

## 3. 设计原则

V1.5 采用以下原则：

- 不改 Gemini-API 运行中的自动刷新机制
- 只替换启动前初始 Cookie 获取方式
- 保留专用 profile，避免污染用户日常浏览器环境
- 保持用户手动登录，不接管 Google 登录动作
- 优先读取运行中浏览器会话里的 live cookie，而不是依赖已落盘数据库
- 不在日志、错误或文档中泄露真实 Cookie 值
- 尽量复用 V1.4 的配置项、命令入口和写回格式
- 输出默认使用中文，强调操作可执行性

## 4. 范围

### 4.1 In Scope

- 为专用 Chrome profile 增加 remote debugging 启动约定
- 允许 `gateway.refresh_cookies` 连接运行中的专用 Chrome 会话
- 通过 DevTools Protocol 读取 `.google.com` / `gemini.google.com` 的 live cookie
- 将读取到的 `__Secure-1PSID` / `__Secure-1PSIDTS` 写回 `cookies.json`
- 保留现有 `gateway.refresh_cookies` 命令入口
- 更新相关文档、脚本提示和测试

### 4.2 Out of Scope

- 不替换 Gemini-API 的 `rotate_1psidts()` 自动续期逻辑
- 不改 OpenAI-compatible 接口协议
- 不改 gateway 运行期 Cookie 持久化与 shared client rebuild 主流程
- 不自动完成 Google 登录
- 不通过浏览器自动化点击页面
- 不处理多浏览器、多账号、多 profile 的复杂统一管理

## 5. 推荐方案

推荐采用“手动启动专用 Chrome profile + remote debugging + 读取 live cookie”方案。

整体流程如下：

1. 用户执行 `uv run --extra browser python -m gateway.refresh_cookies`
2. 如果未检测到可连接的专用 Chrome debugging 会话，脚本输出完整可复制的 PowerShell 启动命令
3. 用户复制该命令，手动启动专用 Chrome profile，并完成 Gemini 登录
4. 用户保持该专用 Chrome 运行，不再关闭窗口
5. 用户重新执行 `uv run --extra browser python -m gateway.refresh_cookies`
6. 程序读取专用 profile 下的 DevTools 连接信息，连接运行中的 Chrome
7. 程序通过 CDP 读取 live cookie，提取 `__Secure-1PSID` / `__Secure-1PSIDTS`
8. 程序原子写回 `cookies.json`
9. 后续启动 `uv run python -m gateway.main` 后，运行中的 Cookie 续期仍由 Gemini-API 自动完成

该方案的优点是：

- 不需要关闭 Chrome
- 读取到的是当前会话中的最新 Cookie
- 避免 Windows 对 SQLite Cookie 数据库的锁文件问题
- 与 Gemini-API 自己的运行期自动续期思路一致

## 6. 与 Gemini-API 自动刷新机制的关系

V1.5 必须明确区分两段责任：

### 6.1 启动前

`gateway.refresh_cookies` 负责获取第一份可用、最新的起始 Cookie。

V1.5 的变化只发生在这一段：

- V1.4：关闭 Chrome 后读本地数据库
- V1.5：连接运行中的 Chrome，读 live cookie

### 6.2 启动后

Gemini-API 仍按现有机制负责：

- 建立 Gemini Web 会话
- 后台自动调用 `RotateCookies`
- 刷新 `__Secure-1PSIDTS`
- 将刷新后的 Cookie 写入自己的缓存文件

gateway 仍按现有机制负责：

- 定期持久化运行期 Cookie
- 优先同步更“新”的上游缓存文件
- 在认证失败时尝试一次恢复

因此，V1.5 不是替换 Gemini-API 自动刷新，而是让“初始 Cookie 获取”更可靠。

## 7. 模块设计

### 7.1 `gateway.refresh_cookies`

V1.5 下，`gateway.refresh_cookies` 建议拆分为四个职责：

#### 7.1.1 `build_manual_chrome_launch_command()`

生成完整可复制的 PowerShell 命令，要求：

- 包含 Chrome 路径探测
- 显式传入 `--user-data-dir=<专用 profile 路径>`
- 固定 `--profile-directory=Default`
- 增加 remote debugging 启动参数
- 默认打开 `https://gemini.google.com/app`

推荐参数：

- `--remote-debugging-port=0`

选择端口 `0` 的原因是：

- 避免固定端口冲突
- Chrome 会自动分配空闲端口
- 后续可通过 `DevToolsActivePort` 文件读取实际端口

#### 7.1.2 `load_devtools_endpoint_from_profile()`

负责从专用 profile 目录中读取 Chrome 写出的 DevTools 连接信息。

推荐优先读取：

- `<profile_dir>\\DevToolsActivePort`

预期行为：

- 如果文件不存在，说明 Chrome 尚未以 remote debugging 模式运行
- 如果文件存在但内容无效，说明调试会话尚未就绪或 profile 状态异常
- 如果读取成功，则得到本地调试端口并拼出 DevTools HTTP / WebSocket 入口

#### 7.1.3 `load_browser_cookies_via_cdp()`

负责通过 Chrome DevTools Protocol 读取当前运行中浏览器会话的 cookie。

建议流程：

1. 连接本地 DevTools endpoint
2. 调用 `Network.getCookies` 或 `Storage.getCookies`
3. 限定读取域名为：
   - `https://gemini.google.com`
   - `https://www.google.com`
   - 或直接按 `.google.com` 过滤
4. 提取：
   - `__Secure-1PSID`
   - `__Secure-1PSIDTS`
5. 返回与现有 `BrowserCookieSelection` 兼容的结构

建议 `source` 更新为：

```text
manual-chrome-profile-cdp
```

#### 7.1.4 `refresh_browser_cookies_to_file()`

保留该主入口与写回职责，但内部逻辑改为：

1. 优先尝试读取运行中的 DevTools 会话
2. 若未发现 remote debugging 会话，则抛出“需要手动启动专用 Chrome profile（带 debugging 参数）”的明确错误
3. 若读取到了 live cookie，则原子写回 `cookies.json`

### 7.2 `gateway.service`

`gateway.service` 尽量少改。

建议保持：

- `refresh_cookies_from_browser()` 仍调用 `refresh_browser_cookies_to_file()`
- 运行期 Cookie 周期性持久化不变
- 认证失败恢复路径不变

仅需更新语义：

- “browser refresh” 不再表示“读取已关闭浏览器的数据文件”
- 而表示“尝试从运行中的专用 Chrome 会话中提取 live cookie”

### 7.3 PowerShell 脚本

`gateway/start_gateway.ps1` 与 `gateway/set_gateway_env.ps1` 不接管浏览器交互，但需要更新提示文案：

- 明确专用 Chrome 启动命令会包含 remote debugging 参数
- 不再提示“登录后关闭 Chrome 再读取”
- 而是提示“保持专用 Chrome 运行，再重新执行 refresh_cookies”

## 8. 用户体验设计

V1.5 下，`gateway.refresh_cookies` 的失败路径建议区分为两种：

### 8.1 未发现可连接的 remote debugging 会话

输出顺序建议为：

1. 当前结论：

```text
未检测到专用 Chrome profile 的可用 remote debugging 会话。
```

2. 原因解释：

```text
请使用脚本提供的 PowerShell 命令手动启动专用 Chrome profile，并保持该窗口运行。
```

3. 完整 PowerShell 启动命令

4. 下一步提示：

```text
在打开的专用 Chrome 中完成 Gemini 登录后，不要关闭窗口，再重新执行：
uv run --extra browser python -m gateway.refresh_cookies
```

### 8.2 已连接 DevTools，但未读取到有效 Gemini 登录态

输出顺序建议为：

1. 当前结论：

```text
已连接专用 Chrome profile，但未检测到有效 Gemini 登录态。
```

2. 下一步提示：

```text
请在该专用 Chrome 窗口中确认已登录 Gemini，然后重新执行 refresh_cookies。
```

V1.5 的关键体验变化是：

- 登录后不再要求用户关闭 Chrome
- 而是要求用户保持专用 Chrome 会话继续运行

## 9. 配置与兼容性

V1.5 应继续复用以下配置：

- `GEMINI_GATEWAY_BROWSER_PROFILE_DIR`
- `GEMINI_GATEWAY_COOKIES_JSON_PATH`
- 既有 `gateway.refresh_cookies` 命令入口

如无必要，不新增复杂配置项。

如需新增，建议仅增加可选的调试连接参数，例如：

- `GEMINI_GATEWAY_BROWSER_REMOTE_DEBUGGING_ENABLED`

但默认推荐仍是：

- 由 `gateway.refresh_cookies` 自动生成带 remote debugging 参数的启动命令
- 用户无需手工记忆额外开关

Cookie 写入格式继续保持 V1.4 / V1.3 兼容格式：

```json
{
  "cookies": {
    "__Secure-1PSID": "...",
    "__Secure-1PSIDTS": "..."
  },
  "updated_at": 1780000000,
  "source": "manual-chrome-profile-cdp",
  "profile_dir": "C:\\Users\\name\\.gemini-api\\selenium-profile",
  "url": "https://gemini.google.com/app"
}
```

## 10. 错误语义

V1.5 需要区分四类错误：

### 10.1 需要手动启动带 debugging 参数的专用 Chrome

表示：

- 没有发现 `DevToolsActivePort`
- 或专用 Chrome 没有以 remote debugging 模式运行

这是用户前置动作未完成，不是程序崩溃。

### 10.2 已连接 Chrome，但未登录 Gemini

表示：

- remote debugging 会话有效
- 但 live cookie 中没有有效 `__Secure-1PSID`

这也是可恢复的用户前置条件问题。

### 10.3 DevTools 连接失败或返回异常

表示：

- 端口存在但连接失败
- DevTools endpoint 无响应
- CDP 返回异常结构

这类错误应给出明确本机排查方向。

### 10.4 `cookies.json` 写入失败

表示：

- 已成功提取 live cookie
- 但写回失败

该错误继续沿用现有原子写入与不覆盖旧文件策略。

## 11. 安全要求

V1.5 必须继续满足以下要求：

- 不打印真实 Cookie 值
- 不在文档、日志或测试输出中泄露真实敏感信息
- `cookies.json` 继续视为本地敏感文件
- 启动命令只输出 profile 路径、目标 URL 和 debugging 参数，不输出任何凭据
- 不通过脚本保存账号密码
- 不暴露远程开放的调试端口给外网，仅允许本机使用

## 12. 测试设计

### 12.1 `gateway.refresh_cookies` 单元测试

覆盖：

- 能生成带 remote debugging 参数的 PowerShell 启动命令
- 能从 `DevToolsActivePort` 读取调试端口
- 未发现 debugging 会话时会输出中文引导与完整命令
- 已连接 DevTools 时能提取 live cookie 并写回 `cookies.json`
- 输出中不泄露真实 Cookie 值

### 12.2 文档与脚本提示测试

覆盖：

- `gateway/README.md` 已更新为“保持专用 Chrome 运行，再读取 live cookie”
- `gateway/start_gateway.ps1` 与 `gateway/set_gateway_env.ps1` 的提示文案已同步更新
- 不再要求“登录后关闭 Chrome 再运行 refresh_cookies”

### 12.3 `gateway.service` 回归测试

覆盖：

- 认证失败时的一次性恢复入口不回归
- shared client rebuild 不回归
- 运行期 Cookie 周期性持久化不回归

### 12.4 本机人工验证

建议验证流程：

1. 运行 `uv run --extra browser python -m gateway.refresh_cookies`
2. 看到脚本打印带 remote debugging 参数的完整 PowerShell 命令
3. 复制执行该命令，手动打开专用 Chrome 并登录 Gemini
4. 保持该 Chrome 窗口运行
5. 再次执行 `uv run --extra browser python -m gateway.refresh_cookies`
6. 确认 `cookies.json` 已成功刷新
7. 启动 `uv run python -m gateway.main`
8. 验证 `/health`、`/v1/account/status` 与聊天接口返回正常

## 13. 风险与取舍

### 13.1 风险

- 需要在 Windows 本机稳定读取 `DevToolsActivePort` 并连接 CDP
- 浏览器 remote debugging 启动方式需要额外验证兼容性
- 若用户自行用其他参数启动 Chrome，可能导致脚本无法自动发现会话

### 13.2 取舍

V1.5 明确选择：

- 不继续优化“关闭 Chrome 后读本地数据库”这条路径
- 转向“从运行中浏览器读取 live cookie”的路径
- 以换取更好的时效性和更少的文件锁问题

这是一个偏工程一致性和体验稳定性的取舍。

## 14. 验证标准

V1.5 通过标准：

- `gateway.refresh_cookies` 能打印带 remote debugging 参数的完整 PowerShell 启动命令
- 用户可手动打开专用 Chrome、登录 Gemini、保持窗口运行
- 脚本可直接从运行中的 Chrome 会话读取 live cookie 并写回 `cookies.json`
- 不再依赖关闭 Chrome 才能读取本地 Cookie 数据库
- Gemini-API 运行期自动续期与 gateway 同步逻辑不回归

## 15. 与 V1.4 的关系

V1.5 不是推翻 V1.4，而是继续修正 V1.4 在“初始 Cookie 获取方式”上的局限。

保留不变的部分：

- 用户手动登录专用 Chrome profile
- 启动后 Gemini-API 自动续期
- gateway 运行期 Cookie 持久化
- 认证失败时的一次性恢复入口
- OpenAI-compatible 接口与账户状态诊断

发生变化的部分：

- 不再依赖关闭 Chrome 后读取 profile 数据库
- 初始 Cookie 提取方式改为连接运行中的 Chrome 会话
- `gateway.refresh_cookies` 的核心价值从“登录后同步写回”升级为“连接 live session 同步写回”

## 16. 推荐实施顺序

1. 为专用 Chrome 启动命令增加 remote debugging 参数
2. 新增 `DevToolsActivePort` 读取与 endpoint 解析
3. 新增通过 CDP 提取 live cookie 的能力
4. 保留 `refresh_browser_cookies_to_file()` 的写回接口
5. 更新错误语义和中文引导
6. 更新 `gateway/README.md`
7. 更新 `gateway/start_gateway.ps1` 与 `gateway/set_gateway_env.ps1`
8. 补充单测与回归测试
9. 进行一次本机人工验证

## 17. 下一步

本 spec 批准后，进入 V1.5 implementation plan 阶段。

实现计划应遵循最小改动原则：

- 优先改 `gateway.refresh_cookies`
- 再补脚本、文档与测试
- 最后做本机人工验证

不应在本阶段顺手重构无关模块。
