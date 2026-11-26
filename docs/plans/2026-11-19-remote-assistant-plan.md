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

## MCP 流式等待与显式后台

2026-11-22 · 依据[流式等待设计](../design/2026-11-19-remote-assistant-design.md#31-mcp-流式等待与显式后台)。后端 MCP 适配和入口 Nginx 在同一 PR 交付。网页、Pi 运行时、任务数据模型沿用现有实现。

1. **统一提交和续接。** 在 `dataagent-backend/api/remote_mcp.py` 给 `ask` 加 `background: bool = False`。已有任务、任务唯一键冲突的重试和新提交都交给同一个返回路径。后台方式立即读当前状态；默认方式在 900 秒内等待终态。等待阶段通过线程池按当前用户和助手读取任务，只有终态才组装原结果。重用 `get_run` 的序列化逻辑，保留已完成结果、附件和原有错误字段。
2. **切换标准响应类型。** FastMCP 仍用 stateless Streamable HTTP，关闭 `json_response` 以采用 SDK SSE。通过 FastMCP `Context` 发进度。SDK 的请求上下文带原 HTTP 请求，可在发最终结果前复用现有授权检查。验证请求断开仅释放等待，任务可由协调器继续执行。版本验证以声明支持的 SDK 下限和当前 1.30.0 为准，若实际不兼容再调整依赖下限。
3. **超时链与部署说明。** 更新 `deploy/docker/nginx/default.conf` 和 `dataagent-frontend/nginx.conf` 的 MCP location，使用默认 1860 秒的读写超时和关闭缓冲。同步 `docs/handbook/api.md`、`deploy/README.md`，记录连接方式、`background`、断线恢复和等待上限，并提供经目标 ZCode 版本验证的客户端超时配置。核对现有 SQL 超时与网页 SDK 事件入口。
4. **工具错误。** 在 MCP 边界保留结构化结果，用 SDK 的 `CallToolResult` 标记工具失败，提供供客户端识别的错误码、面向用户的原因和下一步。测试会话忙、资源不可访问、运行失败的对外信息、状态读取和授权失效。
5. **定向测试。** 扩展 `dataagent-backend/tests/test_remote_mcp.py`，短工具和协议握手使用 SDK 或解析 JSON-RPC SSE。覆盖后台立即返回、默认完成仍在原请求、等待到期返回句柄、进度令牌有无、授权失效、同键并发与断线重连均只产生一个任务及原错误映射。等待上限通过测试时钟或缩短测试常量验证，部署值保持 900 秒。
6. **真实 HTTP 冒烟。** 用 Python 3.13、Docker MySQL `127.0.0.1:3326` 的隔离 schema 和本地 Docker Redis，走 Nginx 和主应用。用可控延迟的模型执行实际 MCP `ask`（至少一例超过 30 秒）。观察 `waiting → running → finished`、SSE 保活与进度、消息和附件的持久化。另测 `background: true`、断线后以原键恢复、`get_run` 取同一结果及通过原网页 API 取消后返回 `cancelled`。用官方 MCP Python SDK 和实际 ZCode 桌面版验证，记录协议版本、ZCode 长工具超时、线上代理差异及是否使用真实模型和凭据。覆盖结果不依赖 ZCode 的进度 UI。
7. **上线与回退。** 后端镜像及两份 Nginx 配置随该 PR 发布。若遇客户端兼容问题，撤回此变更，恢复 JSON 和原 8 秒等待契约。任务与会话均使用原表，已有 run 仍可通过 `get_run` 读取。

本地验证（2026-11-22）：Python 3.13 下分别使用 MCP SDK 1.23.0 和 1.30.0，30 项定向测试均通过。Nginx 配置检查通过。构建本分支 Pi 和沙箱镜像，在 Docker 网络内使用 `dataagent-mysql:3306`（宿主机 `127.0.0.1:3326`，隔离 schema `oda_mcp_sse_b9cec8a`）和现有 `dataagent-redis:6379`（DB 12）。透过实际 Nginx 入口调用，前台同连接等待 35.9 秒，收到 7 次进度、2 次保活和持久化的答案与附件；`get_run` 取回同一结果。显式后台提交后通过网页 API 取消并得到 `cancelled`。途中断开后同键重试仅保留一条任务。临时容器和 schema 已清理。

冒烟前两次因容器未挂载 `/dataagent_runtime`、以 root 启动导致沙箱拒绝 UID/GID 而中止，补齐标准挂载并以非 root 用户启动后通过。使用本地 OpenAI 兼容的确定性模型服务，无真实模型凭据和业务查询。当前机器未安装 ZCode 桌面端，长调用的目标版本兼容性留在待办。

工具错误补充验证（2026-11-22）：MCP SDK 1.23.0 和 1.30.0 各通过 39 项定向测试。使用同一套本地 Nginx、Python 3.13、Docker MySQL（隔离 schema `oda_mcp_sse_138f9a5`）、Redis 和 Pi，复测 SSE 前台、后台、取消及同键重连。官方 SDK 经 Nginx 实测会话忙、请求冲突、任务不存在均返回 `isError: true` 和下一步，撤销令牌后返回带更新配置提示的 HTTP 401。临时容器和 schema 已清理。

## 个人访问令牌替换 OAuth（2026-11-22）

依据设计第 4 节更新。后端与前端分开实施，接口契约以设计 4.2 为准。

1. **后端。** 迁移 `20261122_000040`（删 `da_mcp_refresh_token`、建 `da_mcp_access_token`，可降级）；`remote_mcp_tokens.py` 改为令牌创建、列表、撤销、按哈希查找；`remote_mcp_policy.verify_identity` 改查令牌表；`remote_mcp.py` 删除资源元数据、授权服务器元数据和 `/oauth/*` 路由，401 不带 `resource_metadata`，新增 `/api/v1/dataagent/mcp-tokens` 三个接口；两份 nginx 配置删除 well-known 转发；`tests/test_remote_mcp.py` 删 OAuth 用例、补令牌用例；更新 `docs/handbook/api.md`、`deploy/README.md`。
2. **前端。** 删除 `McpAuthorizeView.vue`、其测试、`/mcp-authorize` 路由和登录守卫特例；`api/dataagent.js` 增加三个令牌接口；新增 `/settings/mcp-tokens`（登录用户可见）和用户菜单入口；`AgentDetailView.vue` 复制配置改为 `headers.Authorization`，加「申请令牌」链接；更新相应单测。
3. **待办。** `docs/TODO.md`「远端问数助手对外调用」中的 OAuth 审查遗留和「查看/撤销已授权客户端」作废，移到「已完成」并注明被本次替换；ZCode 实测改为验证 HTTP + 固定令牌。

验证：
- 后端：`pytest tests/test_remote_mcp.py` 及迁移升降级（Docker MySQL `127.0.0.1:3326` 临时库）。
- 前端：`McpTokensView`、`AgentDetailView`、`authGuard` 的单测，`npm run build`。
- 端到端：本地起后端和前端，网页申请令牌，用 MCP Python SDK 以 HTTP + Bearer 调用 `ask` 和 `get_run`，撤销后再调用应得 401；ZCode 实测由用户执行。

### PR #57 审查修正（2026-11-22）

1. 管理接口采用登录会话与完整 Origin 校验。Nginx 保留端口，Vite 保留浏览器 Host 并代理 MCP。
2. 登录会话保留 SSO 显式角色。迁移为已有令牌设置普通用户权限，签发时保留显式授权或现有普通用户权限。普通用户可从可见助手的只读详情复制配置，管理开关仍限管理员。页面入口统一为「访问令牌」。
3. 后端验证角色优先级、跨源拒绝和可见性，前端验证普通用户复制配置。升级本地隔离 MySQL，使用浏览器签发普通用户令牌，再通过官方 MCP Python SDK 在 HTTP 上调用工具并验证撤销。回退应用版本前降级角色字段迁移。

本地验证（2026-11-22）：Docker MySQL `127.0.0.1:3326` 临时库、Docker Redis `127.0.0.1:6389`（DB 13）、Python 3.13。迁移从 000040 升至 000041，浏览器以普通用户测试会话复制助手配置、创建和撤销令牌。官方 MCP Python SDK 1.30.0 通过 HTTP 初始化、列工具和调用 `list_threads`，撤销后返回 401。使用本地签发的测试登录会话。真实 SSO、ZCode 与模型问数未运行。

### 对话页导出 MCP 配置（2026-11-22）

按[设计第 4.1.1 节](../design/2026-11-19-remote-assistant-design.md#411-对话页的连接配置)实施。

1. 在 `NL2SqlChatV2.vue` 的文件按钮左侧加菜单，沿用对话路由生成链接。新配置表单读取助手资料和本人有效令牌，与助手详情复用配置生成函数。
2. （2026-11-26 改回弹窗：创建表单和签发成功状态在 `McpTokenCreateDialog` 弹窗中，令牌列表和对话 MCP 配置表单共用；对话表单创建后自动选中新令牌并填入完整令牌，`/settings/mcp-tokens/new` 路由已删除。以下为原方案。）将创建表单和签发成功状态移到独立的 `/settings/mcp-tokens/new` 页面，默认「永不过期」。对话表单在新标签页打开创建页面并携带助手 ID，签发成功后用当次返回值导出配置。令牌列表也使用同一创建页面。在配置表单的令牌下拉框右侧加入刷新按钮，按需重新读取本人有效令牌。复用现有复制机制，用 Blob 下载 JSON。
3. 在 `AgentDetailView.vue` 使用省略号触发器和现有浮层组件。管理员开关实时生效，开启后显示「MCP 配置」并复用对话页配置窗口。验证管理员两种开关状态、只读入口和窗口之间的切换。
4. 在 Node 22.22.0 运行受影响的 Vitest 文件。在本地浏览器验证链接、零枚与单枚有效令牌、多选状态、配置复制和下载、新标签页创建，以及返回对话标签页后手动刷新和自动选中唯一令牌。浏览器可用模拟 API 验证页面交互。上线与回退只需要切换前端版本。

### 浏览器下载 MCP 会话文件（2026-11-22）

1. 本地复现：同一下载地址直接 GET 或只带网页登录会话时返回 401；带有效 MCP 令牌或从网页会话文件区访问时返回 200。
2. 复用 DataAgent 登录会话校验，限定在未携带 Authorization 的文件下载请求中。复用现有助手可见性、会话和文件授权；无需改变 URL、Nginx、前端和数据库。
3. 用浏览器点击实际下载地址，验证登录用户拿到附件、其他登录用户拿不到。验证 MCP Bearer 下载和撤销，以及 MCP 工具继续使用访问令牌。回退为撤回本次后端变更。

本地回归：Python 3.13、Docker MySQL `127.0.0.1:3326` 临时库、Docker Redis `127.0.0.1:6389`（DB 13）。Playwright Chromium 使用普通用户测试会话点击真实文件链接，验证附件名及内容；无会话 401、其他用户 404、跨源 403、有效 Bearer 下载成功、撤销 Bearer 401，MCP 工具仍校验令牌。本次未调用模型或 ZCode。
