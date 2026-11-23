# 单助手 MCP 发布实施计划

日期 2026-11-19 · 基于 DataAgent main `f93adf1`

交付范围以 [单助手 MCP 发布设计](../design/2026-11-19-remote-assistant-design.md) 为准，作为一项能力交付。

1. **无人值守开关。** 运行时增加 `interactive`：关闭时不注册追问和计划工具，需要确认的操作直接拒绝。执行器在非交互时固定默认权限模式并追加 `non_interactive_mode.md`。协调器把任务的 `interactive` 传给执行器。
2. **任务标记与幂等。** 迁移为任务表增加 `interactive` 和唯一的 `request_key`；`submit_message_task` 和 `create_task` 透传这两项。
3. **MCP 入口。** 令牌校验映射到平台身份；5 个工具调用现有的建会话、提交、会话列表、消息分页、评价和文件下载。会话来源 `remote` 计入本人会话，网页列表显示「MCP」。
4. **发布与部署。** 助手草稿保存「MCP 访问」开关，发布后返回地址路径，页面拼上当前域名；nginx 转发 MCP、资源元数据和下载路由；部署文档说明 IdP 要求。
5. **配置合并（2026-11-20）。** 删除 `REMOTE_MCP_PUBLIC_ORIGIN`、`REMOTE_MCP_OAUTH_ISSUER`、`REMOTE_MCP_OAUTH_JWKS_URL`、`REMOTE_MCP_OAUTH_SUBJECT_CLAIM`：签发方和公钥从 `OAUTH_PROVIDERS` 的 `server_metadata_url` 读取，对外地址取自请求，主体固定为 `sub`。已按旧变量部署的环境改为填写 `server_metadata_url`，并确认令牌 `sub` 与用户信息钩子的 `sub` 一致。

6. **DataAgent 作为授权服务器（2026-11-20）。** 按设计第 4 节实现授权服务器元数据、动态注册、授权、令牌接口和前端授权页，删除 `server_metadata_url` 及拉取发现文档、校验外部 JWKS 的代码；迁移新增 `da_mcp_refresh_token`；nginx 转发两个发现地址；助手页改为「复制配置」；部署文档删去 IdP 侧 MCP 配置，只保留「开启平台登录」。

7. **MCP 开关即时生效（2026-11-22）。** 开关移出草稿，直接写已发布助手的 `remote_mcp_enabled`；发布不再覆盖该列；助手页切换即调用接口。「复制配置」在经局域网 IP 的 HTTP 访问时改用带回退的 `copyText`。

## 验证

- 单元：令牌映射与拒绝（过期、错误 audience、缺 scope、缺主体）；`ask` 建出非交互任务、同键重试幂等、换问题冲突；他人会话不可读写；会话忙返回在运行任务；运行时非交互不挂起。
- 真实 MySQL：迁移升级、降级、再升级；会话存储集成测试。
- 端到端：按端到端测试剧本用测试 IdP 走完首次登录、问数、取结果、下载、续聊、网页查看与续聊、管理员只读查看、Widget 查看本人会话和隔离用例。实际企业 IdP、令牌续期和真实业务问数在目标环境验收，见 [待办](../TODO.md#远端问数助手对外调用)。

## 本地验证

2026-11-20：后端 694 项通过（14 项需 MySQL 的跳过），另在 Docker MySQL 临时库上 14 项集成测试通过、迁移升降级通过；前端 820 项通过（10 项 `ToolOutputRenderer` 失败在 main 上同样存在）；Pi 111 项通过；前端构建通过。

端到端（2026-11-20，Keycloak 26.0.7 + HTTPS 统一入口 + 官方 MCP Python SDK 1.30 的 OAuth 客户端 + Playwright，模型与 MCP 服务用仓库的冒烟桩）：剧本 29 个用例全部通过，覆盖首次登录（授权码 + PKCE）、问数、取结果、带令牌下载、幂等重试与冲突、续聊、会话忙、评价、网页查看与续聊、管理员只读与文件预览、反馈跳转、Widget 本人会话、跨用户与跨助手隔离、令牌过期续期、关闭 MCP 访问、需要确认的写操作被拒绝。测试中修正三处：Keycloak 不支持 RFC 8707，标准客户端只请求服务端给出的 scope，改为同时接受共用 audience；MCP 开关原先未写入助手资料表；平台登录模式的 Widget 未列出本人的 MCP 会话。

配置合并复测（2026-11-20，同一套 Keycloak 环境）：OAuth 配置只加 `server_metadata_url`，不设任何 MCP 环境变量，用户信息钩子返回 Keycloak 的 `sub`。27 个用例全部通过；另确认 401 的元数据地址和资源元数据中的 `resource` 都取自请求域名，`authorization_servers` 来自发现文档，外域 Origin 返回 403。该环境中 nginx 经 localhost 访问后端，未能体现 `--forwarded-allow-ips` 的作用，需在 compose 部署上确认。
