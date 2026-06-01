# Gemini OpenAI 网关 V1.4 手动专用 Profile 登录与 Cookie 同步设计

## 1. 目标

V1.4 的目标是在 V1.3 浏览器 Cookie 同步与运行期续期能力基础上，解决“受自动化框架控制的 Chrome 无法登录 Google 账号”这一现实阻塞。

本次设计聚焦三个结果：

- 保留专用 Chrome profile 的隔离性
- 放弃自动化框架接管登录流程，不尝试绕过 Google 登录限制
- 将 `gateway.refresh_cookies` 调整为“提示用户手动登录 + 同步 Cookie”的稳定工具

V1.4 的核心目标不是增强新的网关协议能力，而是把登录链路从“自动化登录”调整为“人工登录、程序同步”，让 V1.3 的 Cookie 治理方案可以在本机真实可用。

## 2. 背景与问题

### 2.1 当前问题

当前 `uv run --extra browser python -m gateway.refresh_cookies` 会启动由 Selenium / ChromeDriver 控制的独立 Chrome，并尝试在该浏览器上下文中完成 Gemini 登录态提取。

实际运行中出现的问题是：

- 独立 Chrome 可以被拉起
- 但在该浏览器中登录 Google 账号时，会被提示“由软件控制的 Chrome 禁止登录 Google 账号”
- 结果导致 V1.3 的“启动前自动同步 Cookie”无法走通

这说明当前障碍不在 Cookie 写回逻辑，而在“登录浏览器的方式”。

### 2.2 问题本质

这里的关键区别不是“是否由程序启动 Chrome”，而是“浏览器是否被自动化框架接管”。

一旦登录流程发生在受 WebDriver 控制的浏览器上下文中，就可能触发 Google 的安全限制。继续沿这条路径尝试规避，存在以下问题：

- 方案脆弱，后续很容易再次失效
- 风险较高，容易触发更多风控
- 与网关主目标无关，维护成本高
- 不适合作为仓库内的正式推荐路径

因此，V1.4 明确选择不绕过这类限制，而是调整登录方式。

## 3. 设计原则

V1.4 采用以下原则：

- 不尝试规避 Google 对受控浏览器登录的限制
- 保留专用 profile，避免污染用户日常浏览器环境
- 登录动作必须由用户手动完成，程序不接管登录
- `gateway.refresh_cookies` 负责提示、同步和写回，不负责自动登录
- 尽量复用 V1.3 已有配置、持久化和恢复机制，避免无关重构
- 输出默认使用中文，强调操作可执行性
- 不在日志、错误或命令输出中泄露真实 Cookie 值

## 4. 范围

### 4.1 In Scope

- 调整 `gateway.refresh_cookies` 的登录引导方式
- 提供完整可复制的 PowerShell 专用 Chrome 启动命令
- 支持用户手动打开专用 profile 并登录 Gemini
- 登录完成后继续从专用 profile 提取 Cookie 并写回 `cookies.json`
- 更新相关文档、脚本提示和测试
- 保持运行期 Cookie 持久化与认证失败恢复主结构不变

### 4.2 Out of Scope

- 绕过 Google 登录限制
- 自动填写 Google 账号密码
- 通过 Selenium 自动点击、等待并完成登录
- 直接切换到用户默认日常 Chrome profile 作为推荐路径
- 修改 OpenAI-compatible 主接口协议
- 重做 V1.3 的 account probe、strict mode 或共享 client 架构

## 5. 推荐方案

推荐采用“手动专用 profile 登录 + 程序同步 Cookie”方案。

整体流程如下：

1. 用户执行 `uv run --extra browser python -m gateway.refresh_cookies`
2. 程序尝试从专用 profile 读取 Gemini / Google Cookie
3. 如果未读取到有效 `__Secure-1PSID`：
   - 不再自动唤起受控 Chrome 登录
   - 输出中文说明和一条完整可复制的 PowerShell 命令
4. 用户复制该命令，手动打开专用 profile Chrome，并在该浏览器中完成 Gemini 登录
5. 用户重新运行 `uv run --extra browser python -m gateway.refresh_cookies`
6. 程序成功读取登录态并原子写回 `cookies.json`

该方案保留了专用 profile 的隔离性，同时避开了自动化登录限制。

## 6. 模块设计

### 6.1 `gateway.refresh_cookies`

`gateway.refresh_cookies` 从“自动化启动并控制 Chrome 登录”调整为“生成登录指令、提示手动登录、同步 Cookie”。

建议拆分三个明确职责：

#### 6.1.1 `build_manual_chrome_launch_command()`

生成一条完整可复制的 PowerShell 命令，要求：

- 包含 `chrome.exe` 路径探测
- 显式传入 `--user-data-dir=<专用 profile 路径>`
- 固定 `--profile-directory=Default`
- 默认打开 `https://gemini.google.com/app`
- 输出单段命令，用户无需自行拼接参数

目标是让用户可以直接复制粘贴运行。

#### 6.1.2 `print_manual_login_guidance()`

当脚本未检测到有效 Gemini 登录态时，输出简短中文引导，内容包含：

- 当前结论：未检测到有效登录态
- 原因解释：自动化框架控制的浏览器可能无法登录 Google
- 一条完整 PowerShell 启动命令
- 后续操作提示：手动登录后再次运行 `gateway.refresh_cookies`

该函数只负责用户提示，不负责启动浏览器。

#### 6.1.3 `refresh_browser_cookies_to_file()`

保留该主入口及“刷新到 `cookies.json`”的职责，但内部逻辑改为：

- 先尝试从专用 profile 对应的浏览器数据中读取 Cookie
- 如果读取不到有效 `__Secure-1PSID`，抛出“需要手动登录专用 profile”的明确错误
- 如果读取成功，继续使用原子写入更新 `cookies.json`

V1.4 的重点是改变失败路径体验，而不是改变成功路径的写入格式。

### 6.2 `gateway.service`

`gateway.service` 尽量少改。

保留：

- 运行期 Cookie 周期性持久化
- shared client rebuild
- 认证失败时的一次性恢复入口

调整点仅限于：

- 当底层刷新失败是因为“尚未完成手动登录”时，日志与错误语义需要更清楚
- `refresh_cookies_from_browser()` 的语义从“自动化拉起浏览器刷新”收敛为“尝试从专用 profile 的现有登录态刷新”

### 6.3 PowerShell 脚本

`gateway/start_gateway.ps1` 与 `gateway/set_gateway_env.ps1` 不需要接管登录流程，但需要更新提示文案：

- 不再暗示 Selenium 登录是推荐路径
- 推荐先手动启动专用 profile、登录 Gemini，再运行刷新命令
- 在启动说明里明确给出 `gateway.refresh_cookies` 的用途

## 7. 用户体验设计

当 `uv run --extra browser python -m gateway.refresh_cookies` 未检测到有效登录态时，建议按固定顺序输出 4 段信息。

### 7.1 结论

```text
未检测到专用 Chrome profile 中的有效 Gemini 登录态。
```

### 7.2 原因解释

```text
Google 可能会阻止由自动化框架控制的 Chrome 登录账号，因此请先手动启动专用 profile 并完成 Gemini 登录。
```

### 7.3 完整 PowerShell 命令

输出一条可以直接复制的完整命令，例如：

```powershell
$Chrome = "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe"; if (-not (Test-Path $Chrome)) { $Chrome = "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe" }; & $Chrome --user-data-dir="C:\Users\用户名\.gemini-api\selenium-profile" --profile-directory="Default" "https://gemini.google.com/app"
```

实际输出时应使用配置中的 profile 路径，而不是硬编码示例路径。
如果当前仓库为了兼容 V1.3 仍沿用 `selenium-profile` 作为目录名，这只代表历史路径命名，不再代表登录流程仍由 Selenium 接管。

### 7.4 下一步提示

```text
请复制上面的 PowerShell 命令并手动运行。
在打开的专用 Chrome 中完成 Gemini 登录后，再重新执行：
uv run --extra browser python -m gateway.refresh_cookies
```

该交互设计的目标是让用户在失败时只需要执行一个明确动作，而不是阅读堆栈或猜测下一步。

## 8. 配置与兼容性

V1.4 继续复用 V1.3 的专用 profile 配置，不新增复杂配置面。

优先复用：

- `GEMINI_GATEWAY_BROWSER_PROFILE_DIR`
- `GEMINI_GATEWAY_COOKIES_JSON_PATH`
- 既有 `gateway.refresh_cookies` 命令入口

如无必要，不新增“是否自动登录”之类开关。V1.4 的推荐路径应直接收敛为“只支持手动登录，不推荐自动化登录”。
如无必要，也不强制迁移现有 profile 目录名；若默认目录仍为 `.gemini-api\\selenium-profile`，可在文档中明确其仅为兼容历史命名。

Cookie 写入格式继续保持 V1.3 兼容格式：

```json
{
  "cookies": {
    "__Secure-1PSID": "...",
    "__Secure-1PSIDTS": "..."
  },
  "updated_at": 1780000000,
  "source": "manual-chrome-profile",
  "profile_dir": "C:\\Users\\name\\.gemini-api\\selenium-profile",
  "url": "https://gemini.google.com/app"
}
```

唯一变化是 `source` 语义应反映“手动 profile 登录”而不是 Selenium 自动控制。

## 9. 错误语义

V1.4 需要区分三类错误：

### 9.1 需要手动登录

表示：

- 专用 profile 存在
- 但没有有效 Gemini 登录态

这不是程序崩溃，也不是写入失败，而是一个可恢复的用户操作前置条件。

### 9.2 本地浏览器或 profile 读取失败

表示：

- Chrome 路径不存在
- profile 路径异常
- 本地 Cookie 读取能力不可用

这类错误应给出明确本机排查方向。

### 9.3 `cookies.json` 写入失败

表示：

- 已成功读取有效 Cookie
- 但原子写回失败

该错误应继续沿用现有原子写入与失败不覆盖旧文件的策略。

## 10. 安全要求

V1.4 必须继续满足以下要求：

- 不打印真实 Cookie 值
- 不在文档、日志或测试输出中泄露真实敏感信息
- `cookies.json` 继续视为本地敏感文件
- 启动命令只输出 profile 路径和目标 URL，不输出任何凭据
- 不通过脚本保存账号密码

## 11. 测试设计

### 11.1 `gateway.refresh_cookies` 单元测试

覆盖：

- 能生成完整可复制的 PowerShell 启动命令
- 未登录时会输出中文引导与完整命令
- 已登录时仍能正确写入 `cookies.json`
- 输出中不泄露真实 Cookie 值

### 11.2 文档与脚本提示测试

覆盖：

- `gateway/README.md` 已更新为手动启动专用 profile 登录
- `gateway/start_gateway.ps1` 与 `gateway/set_gateway_env.ps1` 的提示文案更新
- 不再将 Selenium 登录描述为推荐路径

### 11.3 `gateway.service` 回归测试

覆盖：

- 认证失败时，若底层刷新失败，错误信息能体现“需要手动登录专用 profile”
- 原有 shared client rebuild 不回归
- 运行期 Cookie 周期性持久化不回归

### 11.4 本机人工验证

建议验证流程：

1. 运行 `uv run --extra browser python -m gateway.refresh_cookies`
2. 看到脚本打印完整 PowerShell 启动命令
3. 复制执行该命令，手动打开专用 Chrome 并登录 Gemini
4. 再次运行 `uv run --extra browser python -m gateway.refresh_cookies`
5. 确认 `cookies.json` 已成功刷新
6. 启动 `uv run python -m gateway.main`
7. 验证 `/health` 与 `/v1/account/status` 返回正常

## 12. 风险与取舍

### 12.1 风险

- 用户首次使用时需要手动完成一次登录，体验不如“全自动”顺滑
- Windows 本地浏览器 Cookie 读取仍可能受本机环境影响
- 专用 profile 若长期未使用，登录态仍可能失效，需要再次手动登录

### 12.2 取舍

V1.4 明确选择：

- 放弃自动化登录这条高风险路径
- 保留专用 profile 的隔离性
- 优先保证方案可用、可解释、可维护

这是一个偏工程稳定性的取舍，而不是追求“全自动”的取舍。

## 13. 验证标准

V1.4 通过标准：

- 在未登录专用 profile 时，`gateway.refresh_cookies` 会打印清晰中文提示和完整 PowerShell 命令
- 用户可复制该命令手动打开专用 Chrome 并完成 Gemini 登录
- 登录完成后再次运行刷新命令，可成功写回 `cookies.json`
- 运行期 Cookie 持久化、共享 client 重建与 `/v1/account/status` 不回归
- 不输出真实 Cookie 值

## 14. 与 V1.3 的关系

V1.4 不是推翻 V1.3，而是修正 V1.3 在“启动前登录获取 Cookie”这一步的实现路径。

保留不变的部分：

- 启动前同步 Cookie
- 运行期 Cookie 周期性持久化
- 认证失败时的一次性恢复入口
- OpenAI-compatible 接口与账户状态诊断

发生变化的部分：

- 不再由受控 Chrome 承担登录动作
- 登录方式从自动化登录调整为用户手动登录
- `gateway.refresh_cookies` 的核心价值从“自动打开并等待登录”调整为“提示、同步和写回”

## 15. 推荐实施顺序

1. 调整 `gateway.refresh_cookies` 的设计与错误语义
2. 新增完整 PowerShell 启动命令生成函数
3. 新增未登录时的中文引导输出
4. 保留成功路径的 Cookie 写入与脱敏摘要
5. 更新 `gateway/README.md`
6. 更新 `gateway/start_gateway.ps1` 与 `gateway/set_gateway_env.ps1`
7. 补充单测与回归测试
8. 进行一次本机人工验证

## 16. 下一步

本 spec 批准后，进入 V1.4 implementation plan 阶段。

实现计划应遵循最小改动原则：

- 优先改 `gateway.refresh_cookies`
- 再补文档与测试
- 最后做本机人工验证

不应在本阶段顺手重构无关模块。
