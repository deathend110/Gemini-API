# Gemini OpenAI 网关 V1.2 账户状态与会话可用性设计

## 1. 目标

V1.2 的目标不是新增更多 OpenAI-compatible 接口，而是解决当前网关在“账户状态判定”和“网页端能力可用性”上的不确定性，让本地反代服务更接近 Gemini 网页端的真实运行状态。

本次设计主要解决四个问题：

- `gemini_webapi` 报出 `UNAUTHENTICATED` warning，但实际对话仍可成功
- 网关当前只在启动时读取 `cookies.json`，不会把运行时更新过的 cookies 回写到用户文件
- 网关缺少“账户能力探测”，无法区分“基础可用”“能力受限”“完整可用”
- 当前无法保证“完整 Gemini 网页端功能”在启动时被显式验证

换句话说，V1.2 的核心目标是把“能跑”升级为“状态可解释、能力可验证、会话可续命”。

## 2. 背景与问题

### 2.1 当前现象

当前网关在正常对话时会持续出现类似 warning：

```text
Account status: UNAUTHENTICATED - Session is not authenticated or cookies have expired.
```

但实际排查发现：

- `GeminiClient.init()` 后的 `account_status` 确实可能是 `1016 / UNAUTHENTICATED`
- 即使如此，`gemini-3-flash`、`gemini-3-pro`、`gemini-3-flash-thinking` 仍可能成功生成内容
- deep research 等能力探针则可能返回 reject code，说明能力并不一致

这表明当前上游库的“账户状态码”与“真实能力”并非一一对应。

### 2.2 当前 CLI 与 Gateway 的差异

项目自带 `cli.py` 之所以看起来更稳，不是因为它拥有完全不同的认证逻辑，而是因为它额外做了两件事：

- 每次运行结束前，会把 `client.cookies` 合并回 `cookies.json`
- 每次调用都是短生命周期：`init -> request -> persist -> close`

而当前网关的行为是：

- 启动时只读一次 `cookies.json`
- 运行中复用共享 `GeminiClient`
- 依赖 `gemini_webapi` 的内部缓存和自动刷新，但不会把这些更新同步回用户维护的 `cookies.json`

这会带来两个后果：

- 网关重启后的会话延续性不如 CLI 明确
- 用户看到 warning 时，无法判断是“真失效”还是“受限但可用”

### 2.3 当前最关键的缺口

当前网关没有把“账户状态”和“能力状态”拆开建模。

这会导致：

- warning 文案过重，误导用户认为 cookie 已完全失效
- 实际能力受限时，网关仍对外宣称统一可用
- 用户无法在启动时明确知道当前账户是否满足“完整 Gemini 网页端功能”

## 3. 设计原则

V1.2 采用以下原则：

- 保持现有 OpenAI-compatible 对外接口不破坏
- 优先增强认证与会话管理，而不是扩展功能面
- 将“账户状态”“能力状态”“运行状态”分层表达
- 尽量复用 `gemini_webapi` 现有内部能力，不重复造轮子
- 对“完整网页端功能”的要求显式化，而不是隐式假设

V1.2 是一次“账户会话治理升级”，不是“API 形状重构”。

## 4. 范围

### 4.1 In Scope

- 网关级 cookies 持久化回写
- 启动阶段账户状态与能力探测
- 账户状态分层与标准化表达
- `strict` 模式下对“完整能力”的启动校验
- 网关运行中的账户状态快照输出
- 与共享 `GeminiClient` 生命周期协同的 cookie 同步策略

### 4.2 Out of Scope

- 新增 `/v1/responses`
- 新增图像生成 OpenAI 兼容接口
- 多账号池、多 cookie 轮换
- 自动浏览器登录
- Deep Research 功能本身的协议封装
- 完整前端管理后台

## 5. 问题拆解

V1.2 需要把当前问题拆成三个独立层面处理。

### 5.1 认证层

回答的问题是：

- 当前会话是不是一个可初始化的 Gemini 网页会话
- 当前 cookie 来源是否可持续
- 当前会话是否具备稳定的 cookie 刷新路径

### 5.2 能力层

回答的问题是：

- 基础聊天是否可用
- 高阶模型是否可用
- deep research / capability probe 是否可用
- 当前状态是否可视为“完整网页端能力”

### 5.3 持久化层

回答的问题是：

- 运行时自动刷新后的 cookies 是否应回写到用户的 `cookies.json`
- 回写时机是什么
- 回写失败是否影响当前服务继续运行

## 6. 推荐方案

推荐采用“网关内建会话治理层”方案。

### 6.1 方案摘要

- 在 `GatewayService` 上新增会话状态管理器
- 启动时完成 cookies 读取、client warmup、账户能力探测
- 运行时将共享 client 的 cookies 变化按策略回写到 `cookies.json`
- 对外提供标准化账户状态快照
- 支持 `strict account mode`，要求“完整网页能力”时启动失败

### 6.2 为什么不只做 warning 文案修正

只改 warning 文案不解决核心问题：

- 重启后的 cookies 演进仍未同步
- 能力探测仍然缺失
- 用户依旧不知道当前是“可用但受限”还是“完整可用”

因此 V1.2 不能只做文案层修补，必须补会话治理。

## 7. 目标架构

### 7.1 `gateway/service.py`

负责：

- 读取并缓存 `cookies.json`
- 构建共享 `GeminiClient`
- 在 warmup 后执行账户能力探测
- 管理运行时 cookies 的回写
- 持有标准化后的账户状态快照

### 7.2 `gateway/config.py`

新增会话治理相关配置，例如：

- 是否启用 cookies 回写
- 回写节流间隔
- 是否启用严格账户模式
- 严格模式下要求的能力等级

### 7.3 `gateway/main.py`

负责：

- startup 阶段执行完整 warmup + probe
- shutdown 阶段执行最终 cookies flush
- 必要时在启动日志中打印当前账户状态摘要

### 7.4 可选状态路由

V1.2 可选增加只读诊断路由，例如：

- `GET /v1/account/status`

用于返回：

- 原始 `account_status`
- 能力探测结果
- 当前模式：`available` / `degraded` / `strict_failed`

该路由属于运维诊断能力，不影响 OpenAI-compatible 主接口。

## 8. cookies 持久化设计

### 8.1 当前问题

当前网关只把 cookies 加载进内存，不会把运行期间更新的 cookies 同步回用户的 `cookies.json`。

虽然 `gemini_webapi` 自己会写入内部缓存，但：

- 该缓存路径对用户不可见
- 与用户手动维护的 `cookies.json` 分离
- 服务重启或迁移时不够直观

### 8.2 V1.2 处理方式

新增网关级 cookies 持久化：

- 以当前 `cookies.json` 为基础载入原始 cookies
- 从共享 `GeminiClient.cookies` 中提取最新 Google cookies
- 合并并写回 `cookies.json`

回写格式应兼容当前仓库已有 CLI 约定，优先采用：

```json
{
  "updated_at": "2026-06-01T00:00:00Z",
  "cookies": {
    "__Secure-1PSID": "...",
    "__Secure-1PSIDTS": "...",
    "NID": "..."
  }
}
```

### 8.3 回写时机

推荐采用“双路径回写”：

- shutdown 时强制 flush 一次
- 运行时按节流策略定期 flush

例如：

- 仅当检测到 cookies 变化时回写
- 两次写入最小间隔 60 秒

这样既避免频繁 I/O，也降低异常退出时丢失最新会话的风险。

### 8.4 失败策略

cookies 回写失败时：

- 记录 warning
- 不中断当前服务
- 不把当前请求直接判定失败

因为回写失败属于持久化问题，不等同于当前会话不可用。

## 9. 账户状态与能力探测设计

### 9.1 当前问题

`GeminiClient.account_status` 来自 `GetUserStatus`，但该结果并不能完整代表真实能力。

因此 V1.2 需要同时维护两类状态：

- 原始账户状态：来自上游 RPC
- 实际能力状态：来自能力探测结果

### 9.2 标准化状态模型

建议在网关侧定义统一状态快照：

- `raw_account_status`
- `raw_account_status_code`
- `chat_available`
- `advanced_models_available`
- `deep_research_available`
- `full_web_capability_available`
- `mode`

其中 `mode` 推荐分为：

- `available`
  - 账户状态正常，关键能力满足要求
- `degraded`
  - 基础能力可用，但存在功能受限
- `blocked`
  - 关键能力不可用，无法满足最小对话要求

### 9.3 探测方式

V1.2 启动后执行轻量 probe：

1. 读取 `account_status`
2. 使用 `inspect_account_status()` 做 RPC 能力探测
3. 结合模型可用性注册结果
4. 必要时执行一次最小生成探测

最终得到“原始状态 + 能力状态”的组合判断。

### 9.4 为什么需要最小生成探测

因为当前已经实测证明：

- `UNAUTHENTICATED`
- 但 `generate_content()` 仍可能成功

所以仅依赖上游状态码不足以判断网关是否可用。

最小生成探测可以作为最终真值来源之一。

## 10. 严格账户模式设计

### 10.1 目标

当用户明确要求“代理出来的 API 要尽量实现完整 Gemini 网页端功能”时，需要支持严格模式。

### 10.2 行为

开启严格模式后，startup 阶段必须满足指定能力门槛，否则拒绝启动。

例如可以支持：

- `basic`
  - 只要求基础聊天可用
- `standard`
  - 要求基础聊天 + 主要模型可用
- `full_web`
  - 要求基础聊天 + 主要模型 + 高阶 capability probe 满足

### 10.3 推荐默认值

默认不启用严格失败。

原因：

- 当前很多场景下，`UNAUTHENTICATED` 仍可正常生成
- 直接默认启动失败会影响现有使用者

因此推荐默认行为是：

- 启动成功
- 明确标记 `degraded`
- 在日志和状态路由中展示受限原因

只有用户显式启用严格模式时，才把能力不足视为致命错误。

## 11. 与 `gemini_webapi` 现有能力的协同

### 11.1 继续复用上游自动刷新

V1.2 不应替换 `gemini_webapi` 的 `auto_refresh`。

仍然应继续使用：

- `auto_refresh=True`
- 上游的 `rotate_1psidts()`
- 上游内部缓存能力

### 11.2 网关新增的是“用户文件同步”

网关新增的是：

- 把共享 client 的最新 cookies 与用户显式维护的 `cookies.json` 同步
- 把上游内部状态转成用户可理解的状态快照

因此 V1.2 应视为对上游能力的补足，而不是替代。

### 11.3 浏览器 cookies 作为可选增强源

若环境具备 `browser-cookie3`，V1.2 可进一步考虑：

- 启动时允许把本地浏览器登录态作为候选来源
- 当 `cookies.json` 初始化失败但浏览器态可用时，给出更明确提示

但这应是可选增强，不作为 V1.2 的硬依赖。

## 12. 配置设计

建议新增以下配置项：

- `GEMINI_GATEWAY_COOKIE_PERSIST_ENABLED`
- `GEMINI_GATEWAY_COOKIE_PERSIST_INTERVAL_SECONDS`
- `GEMINI_GATEWAY_ACCOUNT_STRICT_MODE`
- `GEMINI_GATEWAY_ACCOUNT_REQUIRED_LEVEL`
- `GEMINI_GATEWAY_ACCOUNT_PROBE_ENABLED`

配置原则：

- 默认保持兼容，不破坏现有接入
- 用户可显式切换到更严格的“完整能力模式”

## 13. 对外行为要求

V1.2 必须保持以下行为不变：

- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /chat/completions`
- 鉴权方式
- Base URL 结构
- 现有模型映射方式
- 现有图片、文件、tools 协议

V1.2 新增的账户状态治理能力不得破坏 AstrBot、Fitness Agent 等现有调用方式。

## 14. 风险与取舍

### 14.1 风险

- 能力探测可能增加 startup 时间
- 最小生成探测若设计过重，会增加上游消耗
- cookies 回写实现不当可能覆盖用户手动维护的文件格式
- “完整网页端能力”本身是一个会随上游变化而漂移的目标

### 14.2 取舍

V1.2 明确选择：

- 先增强状态可解释性与会话可持续性
- 不承诺一次性补齐所有 Gemini 网页高级功能

这是一次“稳定性与可观测性优先”的取舍。

## 15. 验证标准

V1.2 通过的标准应包括：

- 网关启动后能输出标准化账户状态摘要
- 当前共享 client 的 cookies 可按策略回写到 `cookies.json`
- 重启后可优先复用最近一次运行更新过的 cookies
- 能明确区分 `available` / `degraded` / `blocked`
- 在严格模式下，能力不足会显式拒绝启动
- 在非严格模式下，能力受限只做标记，不影响基础对话
- 现有 `/v1/chat/completions` 对话能力不回归

## 16. 推荐实施顺序

V1.2 推荐按以下顺序实施：

1. 增加网关级 cookies 持久化回写
2. 增加账户状态快照与能力探测
3. 增加严格账户模式
4. 增加诊断输出与回归测试

## 17. 下一步

本 spec 批准后，进入 V1.2 implementation plan 阶段，建议拆分为以下任务：

- cookies 持久化设计落地
- 账户能力探测与状态模型落地
- strict mode 与启动策略落地
- 诊断接口与测试补齐
