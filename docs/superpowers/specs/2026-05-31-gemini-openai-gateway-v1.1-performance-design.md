# Gemini OpenAI 网关 V1.1 性能优化设计

## 1. 目标

V1.1 的目标不是扩展新接口能力，而是在保持现有 OpenAI-compatible 契约不变的前提下，显著降低网关请求延迟，尤其是 AstrBot 这类高频对话场景下的首包等待时间。

本次优化只做三件事：

- 共享并复用单个长驻 `GeminiClient`
- 在 FastAPI 启动阶段完成上游 client 预热
- 把 `cookies.json` 的读取从“每请求一次”改成“启动加载到内存”

## 2. 背景与问题

当前 V1.0 的主要性能瓶颈不在 FastAPI，而在网关每次请求都重新初始化 Gemini 网页会话。

现状是：

- `GatewayService.create_chat_completion()` 每次调用 `build_gemini_client()`
- `GatewayService.generate_text()` / `generate_stream()` 每次都执行 `await client.init(...)`
- 请求完成后立即 `await client.close()`
- `load_cookies()` 每次请求都重新读取并解析 `cookies.json`

而 `gemini_webapi.GeminiClient.init()` 并不是轻量操作，它会完成：

- access token 获取
- Gemini 网页会话建立
- 初始 RPC 调用
- 用户状态获取
- 会话相关初始化

这导致当前网关即使提示词很短，也会在每个请求上重复付出冷启动成本。

## 3. 设计原则

V1.1 采用最小侵入、最小风险方案：

- 不改变对外 API 形状
- 不改变模型映射、tools、图片、文件输入协议
- 不引入 client pool
- 不引入服务端对话记忆
- 优先利用 `gemini_webapi` 已具备的长连接、自动刷新能力

换句话说，V1.1 是一次“生命周期管理优化”，不是“功能重构”。

## 4. 范围

### 4.1 In Scope

- `GatewayService` 持有共享上游 `GeminiClient`
- FastAPI `startup` 阶段预热共享 client
- FastAPI `shutdown` 阶段关闭共享 client
- `cookies.json` 启动时读取并缓存到内存
- 上游 client 失效后的受控重建
- 保持现有 `/v1/models`、`/v1/chat/completions`、`/chat/completions` 行为不变

### 4.2 Out of Scope

- 多 client 池
- 并发请求调度优化
- tools 协议优化
- SSE 细粒度流式策略调整
- 多账号/多 cookies 轮换
- metrics、Prometheus、APM 等监控系统接入

## 5. 推荐方案

采用“单实例长驻复用”方案。

### 5.1 方案摘要

- 应用启动时创建一个共享 `GeminiClient`
- 启动阶段调用一次 `client.init(...)`
- 所有普通对话和流式对话都复用该实例
- `cookies.json` 仅在服务启动时读取一次并缓存
- 如果共享 client 失效，则在请求路径中触发一次受控重建

### 5.2 为什么不用 client pool

当前用户目标是先解决“速度有点慢”的主问题，而不是“高并发吞吐量”问题。

client pool 虽然对并发有帮助，但会带来：

- 多实例生命周期管理
- 上游登录态一致性问题
- 并发占用与回收逻辑
- 更复杂的错误恢复

因此 V1.1 不引入 client pool，等单实例复用收益验证完成后，再决定是否做 V1.2。

## 6. 目标架构

### 6.1 组件职责

#### `gateway/main.py`

负责：

- 创建 FastAPI 应用
- 注册 `startup` / `shutdown` 生命周期钩子
- 在启动时调用 `GatewayService.warmup()`
- 在关闭时调用 `GatewayService.shutdown()`

#### `gateway/service.py`

负责：

- 持有共享 `GeminiClient`
- 持有缓存后的 cookies
- 提供共享 client 的初始化、获取、重建、关闭逻辑
- 普通请求与流式请求复用同一个上游 client

#### `gateway/config.py`

无需大改，仅继续提供现有配置解析能力。

## 7. 生命周期设计

### 7.1 启动阶段

应用启动时执行：

1. 读取 `cookies.json`
2. 校验最少包含 `__Secure-1PSID`
3. 将 cookies 缓存到 `GatewayService`
4. 构建共享 `GeminiClient`
5. 执行一次 `client.init(...)`
6. 记录“预热完成”状态

目标是让第一条真实用户请求不再承担 init 冷启动。

### 7.2 请求阶段

普通请求和流式请求都通过统一的共享 client 获取路径：

1. 获取共享 client
2. 若当前 client 可用，则直接调用 `generate_content(...)` 或 `generate_content_stream(...)`
3. 不再在每个请求末尾关闭 client

### 7.3 关闭阶段

应用关闭时执行：

1. 若共享 client 存在，则调用 `client.close()`
2. 清理本地引用
3. 将 service 状态重置为未预热

## 8. cookies 缓存设计

### 8.1 当前问题

V1.0 每次请求都会读取一次 `cookies.json`，这虽然不是最大瓶颈，但属于纯重复 I/O。

### 8.2 V1.1 处理方式

在 `GatewayService` 初始化或 `warmup()` 时读取 cookies，并缓存为内存对象，例如：

- `self._cached_cookies`

后续请求只使用缓存值，不重复读文件。

### 8.3 刷新策略

V1.1 不做自动重新加载本地 `cookies.json` 文件。

原因：

- 当前目标是性能优化，不是配置热更新
- `gemini_webapi` 已支持 `auto_refresh`
- 热更新文件会引入额外状态复杂度

如果用户更换了本地 cookies 文件，V1.1 的预期操作是：

- 重启 gateway 服务

## 9. 上游 client 复用设计

### 9.1 初始化策略

共享 client 建议使用：

- `auto_refresh=True`
- `auto_close=False`

原因：

- `auto_refresh=True` 让 cookies / token 在后台维持更新
- `auto_close=False` 避免空闲时被自动关闭，从而再次触发冷启动

### 9.2 请求策略

普通请求：

- 直接复用共享 client 执行 `generate_content(...)`

流式请求：

- 直接复用共享 client 执行 `generate_content_stream(...)`

两条路径都不负责主动关闭 client。

## 10. 失败恢复设计

### 10.1 风险

共享长驻 client 比“每请求新建”更快，但也引入了“共享状态失效”的问题，例如：

- 上游连接失效
- cookies 被服务端判定过期
- client 内部状态异常

### 10.2 V1.1 恢复策略

当请求路径遇到特定上游异常时：

1. 关闭当前共享 client
2. 使用已缓存 cookies 重新构建 client
3. 执行一次重新 `init(...)`
4. 对当前请求进行一次有限重试

要求：

- 只允许一次受控重建
- 不允许无限重试
- 重建失败时返回现有 OpenAI-compatible 错误结构

### 10.3 适用异常

V1.1 建议对以下类型触发重建：

- `AuthError`
- `TimeoutError`
- `APIError`
- `GeminiError`

但要区分：

- “客户端状态损坏，适合重建”
- “请求本身失败，不适合无限重试”

因此 V1.1 只做一次重建尝试。

## 11. 并发与一致性

V1.1 不引入 client pool，因此需要接受一个现实约束：

- 单共享 client 在高并发下可能存在争用

但当前阶段的主要目标是先消除反复 init/close 的巨大延迟，因此：

- 先解决延迟问题
- 再观察 AstrBot 实际并发行为

如果后续出现明显并发瓶颈，再进入 V1.2 设计 client pool。

## 12. 对外行为要求

V1.1 必须保持以下对外契约不变：

- Base URL 仍为当前本地网关地址
- 鉴权方式仍是 `Authorization: Bearer <api_key>`
- `/v1/models` 行为不变
- `/v1/chat/completions` 行为不变
- `/chat/completions` 别名不变
- tools、图片、文件输入格式不变
- SSE 输出格式不变

也就是说，AstrBot 和现有测试脚本不需要改接入方式。

## 13. 预期收益

V1.1 预期主要收益：

- 显著降低非首启场景下的请求等待时间
- 降低每请求重复初始化上游网页会话的成本
- 降低 `cookies.json` 重复 I/O
- 提高 AstrBot 连续对话时的体感响应速度

## 14. 风险与取舍

### 14.1 风险

- 共享 client 失效时可能影响后续请求
- 并发下单实例可能成为瓶颈
- `gemini_webapi` 的长驻会话是否绝对稳定，仍需实测

### 14.2 取舍

V1.1 明确选择：

- 用“低复杂度 + 高收益”的单实例复用
- 换取“暂不处理高并发最优解”

这是一个面向当前实际使用场景的工程取舍，而不是最终形态。

## 15. 验证标准

V1.1 通过的标准应包括：

- 服务能正常启动并完成预热
- `/health` 正常
- `/v1/models` 正常
- 普通 chat 正常
- stream chat 正常
- AstrBot 无需修改接入方式即可继续使用
- 连续多次请求的体感延迟明显优于 V1.0
- 关闭服务时共享 client 能正常释放
- 上游失效时能完成一次受控重建或返回规范错误

## 16. 不做项说明

以下内容明确不在本次 spec 范围内：

- tools 响应正确率优化
- tool 参数乱码问题
- 更细粒度的流式策略
- 多实例或多账号轮换
- 指标系统与可观测性平台

## 17. 下一步

本 spec 批准后，进入实现计划阶段，按 SDD 方式拆成小任务执行：

- 生命周期接入
- 共享 client 管理
- cookies 缓存
- 失败恢复
- 回归测试与性能验证
