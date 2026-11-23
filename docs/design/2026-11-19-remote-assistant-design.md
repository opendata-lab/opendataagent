# 将单个问数助手发布为 MCP 服务

日期 2026-11-19 · 基于 DataAgent main `f93adf1`

管理员在已发布的助手上打开「MCP 访问」开关（立即生效，不需要再发布），页面提供可复制的客户端配置。用户在网页申请个人 MCP 访问令牌，填入本地 MCP 客户端后，即可向该助手问数、追问和评价。

例如「经营分析助手」的地址是 `http://dataagent.example.com:8901/api/v1/remote-agents/agent_sales/mcp`。每个地址绑定一个 `agent_id`，所有助手共用同一个 FastAPI 进程和任务协调器。

## 1. 原则：MCP 会话就是普通会话

MCP 只是又一个入口。用户通过 MCP 发起的会话，和网页、Widget 发起的会话是同一种对象：

- 同一个会话、任务、消息模型，同一个工作区、检查点和历史。
- 同一个身份：MCP 令牌映射到和网页登录相同的平台用户。
- 同一套可见范围和权限。

唯一的区别是**无人值守**：没有人能回答追问或确认操作。会话来源记为 `remote`，界面上显示为「MCP」。

| 方面 | 做法 | 复用的现有能力 |
| --- | --- | --- |
| 身份 | DataAgent 自己签发 MCP 令牌（第 4 节）。用户用网页会话申请令牌，令牌的主体就是该平台用户 ID（`<provider>:<sub>` 或 `local:<name>`）；角色按签发授权与配置判定，可见范围每次请求检查 | 网页登录（SSO 或本地账号）、`resolve_oauth_role` |
| 能否使用 | 助手已发布且开启「MCP 访问」，并对该用户可见 | 助手可见范围 `agent_visible_to` |
| 会话 | 首问按网页同样的方式建会话，来源 `remote`；续聊传 `thread_id`，只能是自己在该助手下的会话。网页、MCP 和平台登录模式的 Widget 都列出本人的这些会话 | `create_topic`、本人会话判断 |
| 问数 | 提交为普通任务，标记 `interactive=false` | `submit_message_task`、协调器、执行器 |
| 结果 | 回答文本和本轮生成的文件，即网页消息里的同一批附件 | 助手消息与附件 |
| 下载 | MCP 令牌或 DataAgent 网页会话鉴权后读取同一个工作区文件 | `topic_file_response` |
| 评价 | 点赞点踩，与网页相同 | `update_message_feedback` |

## 2. 无人值守的运行

运行时增加一个通用开关 `interactive`，默认开启。MCP 提交的任务关闭它：

- 不注册追问（AskUserQuestion）和计划（ExitPlanMode）工具。
- 权限模式固定为默认。计划、自动接受、免确认都是用户在网页会话里做的选择。
- 原本要停下等用户确认的操作直接拒绝，模型收到原因后继续，例如说明无法执行写操作。
- 系统提示补充一段说明：不要追问；缺少必要条件且没有可信默认值时，在回答中说明缺什么并结束。

这与 Snowflake Cortex Agents 一致：无人在场时去掉追问工具；MCP 默认权限策略为 `always_ask`，未给出的确认按拒绝处理。见 [Ask user question tool](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-ask-user-question-tool) 和 [Managed MCP](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-mcp)。

所以 MCP 能做的，就是该用户在网页默认模式下不需要确认就能做的；需要确认的一律不做。数据范围由助手绑定的 MCP 服务和下游账号决定，与网页一致。

## 3. 工具契约

| 工具 | 输入 | 返回 |
| --- | --- | --- |
| `ask` | `question`、`request_key`，续聊时带 `thread_id` | 默认 SSE 等待终态，可传 `background: true` 立即返回当前状态。未完成时返回 `in_progress`、`run_id`、`thread_id`、`retry_after_ms`。会话已有任务在运行时返回 `thread_busy` 和该任务的 `run_id`。 |
| `list_threads` | 可选标题查询、页码、每页条数 | 该用户在这个助手下的会话（网页、Widget、MCP），含 `thread_id`、标题、来源、更新时间。 |
| `get_thread` | `thread_id`、页码、每页条数 | 最新在前的消息。 |
| `get_run` | `run_id` | `in_progress`，或 `answered`（`answer`、`message_id`、`files`），或 `failed`/`cancelled`（`error`）。 |
| `feedback` | `thread_id`、`message_id`、`rating: like\|dislike` | 当前评价。 |

`request_key` 用于重试：任务表的 `request_key` 列存「助手 + 用户 + 键」的摘要并唯一。同键同问题返回原任务；同键换了问题或会话返回 `request_conflict`。`files` 中每个文件带下载地址 `.../remote-agents/<agent_id>/threads/<thread_id>/files/<path>`。MCP 客户端可携带同一令牌，浏览器可使用同一 DataAgent 地址下的登录会话。

### 3.1 MCP 流式等待与显式后台

2026-11-22，基于 main `8e5fe2a`。原 `ask` 用 JSON 返回，最多主动等待约 8 秒。超过 8 秒的查询要求调用方用 `get_run` 轮询。同一任务的网页事件接口已支持 SSE，MCP 调用需要的是同一个工具请求内的进度和最终结果。

#### 对齐范围

Snowflake 默认保持 `tools/call` 的 SSE 响应，期间发保活和可选的进度，最后在原请求返回完整结果。显式传 `background: true` 时立即返回运行句柄。DataAgent 采用相同的前台等待与显式后台语义，沿用现有工具名、请求幂等键、会话 ID、任务 ID 和结果字段。Snowflake 分开查运行状态和结果，DataAgent 的 `get_run` 一次调用即可完成相应读取。参考 [Snowflake MCP 工具调用](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-mcp#discover-and-invoke-tools)和[后台调用](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-mcp#run-an-agent-tool-in-the-background)。

#### 客户端契约

| 调用 | 行为 |
| --- | --- |
| `ask(question, request_key, thread_id?, background=false)` | 提交或找回原任务。在这次 POST 的 SSE 响应中等待，终态时返回一个 JSON-RPC 工具结果，随后结束响应。成功仍是 `answered`，包含 `answer`、`files`、`message_id`、`run_id`、`thread_id`。失败和取消沿用原字段。 |
| `ask(..., background=true)` | 提交或找回原任务后立即返回当前状态。未完成时返回 `in_progress`、`run_id`、`thread_id` 和 `retry_after_ms: 2000`。已完成的幂等重试直接返回持久化结果。 |
| `get_run(run_id)` | 读取当前状态或持久化的终态结果，供后台调用、等待上限和断线后恢复。已知 `run_id` 时可直接调用。 |
| 调用断开后重试 | 若还没有 `run_id`，以原 `request_key`、问题及续聊参数重试 `ask`。继续等待可以沿用默认模式，也可传 `background: true` 先拿句柄。相同请求复用原任务，冲突继续返回 `request_conflict` 供客户端识别。用户提示为「请求异常。请重新发起请求。如仍失败，请联系管理员。」 |
| 同一会话已有运行 | 以不同幂等键提交下一问仍返回 `thread_busy` 和已有任务 ID。同键并发重试归到原任务。 |

Streamable HTTP 地址沿用现有 `/mcp`。客户端 `Accept` 同时接受 `application/json` 和 `text/event-stream`，客户端协议类型保持 `http`。使用 FastMCP 的 `stateless_http=True, json_response=False`。SDK 按该配置处理所有 JSON-RPC 请求，短工具和握手也可能收到短 SSE 响应。SSE 先发可选通知，最终只发一个与原请求 ID 对应的工具结果。[MCP Streamable HTTP 规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports#sending-messages-to-the-server)

#### 等待、进度与授权

- 提交通道沿用 `submit_message_task` 和任务协调器，两种调用都创建同一个类型的 `interactive=false` 任务。断开 SSE 只结束该次等待，已落库的任务继续执行。客户端主动停止任务仍可用网页原有的取消入口。
- 等待时立即读取一次当前调用人可见的任务状态，然后每 5 秒查一次 MySQL 中同一任务。每次查询同时核对助手和会话范围。发现终态才读取并组装持久化的助手消息和附件。复用已有存储和结果函数，避免重读历史消息或转发网页 SDK 事件。
- 客户端提供 `_meta.progressToken` 时，用 FastMCP `Context.report_progress` 发送一次排队或执行状态，等待期间每 5 秒更新。数值为单调递增的已等待秒数，省略未知的 `total`。通知只携带运行状态和等待时长，最终正文和下载链接放在工具结果里。传输层持续用 SDK 自带的 SSE 注释保活，未请求进度时也能保活。[MCP 进度协议](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/progress)
- 入站沿用访问令牌与助手授权。长等待向客户端发最终结果前，再用原请求复核令牌和助手访问权。SSE 响应已经开始时，授权失败通过 MCP 工具错误返回，响应中无原任务内容。新请求继续按现有 HTTP 401/403 处理。

#### 工具错误与下一步

MCP 协议错误和工具参数的类型、长度及取值校验继续由 SDK 处理。DataAgent 工具拒绝请求时用 `isError: true`，并在 `structuredContent.error` 提供三个字段。`code` 供客户端判断，`message` 说明发生了什么，`next_step` 告诉用户和客户端如何继续。`content[0].text` 保留同一份 JSON，以便兼容使用文本结果的客户端。[MCP 工具错误规范](https://modelcontextprotocol.io/specification/2025-11-25/server/tools#error-handling)

- 会话忙返回已有的 `run_id`，下一步读取该任务，完成后再发下一问。
- 找不到会话、任务或回答时，指向会话列表、原任务或原会话中的回答。未知资源和不可访问资源使用同一对外文案。
- 请求冲突保留 `request_conflict`，用户可以重新发起请求。缺少问题内容则提示输入问题。
- 长连接中的令牌失效提示检查或更换令牌。助手不可访问时提示核对助手或联系管理员。
- 查询最终失败时，`ask` 标为工具错误，包含运行句柄。用 `get_run` 成功查到一个失败或已取消的任务时，结果仍是一次成功的状态读取。超时、长时间无进展、取消和其他执行失败各有对应提示。执行中断且外部操作结果未知时，先核对结果再重新提交。MCP 结果只暴露错误码和面向用户的内容，原始异常、堆栈和诊断详情留在服务端。

#### 超时与部署

| 层 | 设计 |
| --- | --- |
| MCP 等待上限 | 参照 Snowflake 对单次调用的 15 分钟上限。DataAgent 在 900 秒到达时再查一次持久状态，已结束就返回结果，否则在同一流用 `in_progress` 和句柄结束等待。 |
| 任务执行 | 继续采用任务自身的 `agent_timeout_seconds`，默认 1800 秒，并由协调器处理取消或终态。SQL 超时仍取任务提交时的现有设置。等待期满不会缩短任务原有的执行预算。 |
| Nginx | 两份 Nginx 配置保留不缓冲，在 MCP location 使用与现有网页 SSE 一致的读写超时（默认 1860 秒）。外部代理的读写超时应覆盖部署的后端预算，并能逐段转发 SSE。有总连接时限的代理还要覆盖 900 秒等待与网络余量。 |
| MCP 客户端 | 长调用的硬超时必须覆盖服务端等待上限和网络余量。ZCode CLI 文档有 `timeoutMs`，需要实测目标 ZCode 桌面版本是否应用该配置，以及收到进度后是否仍有独立的工具执行超时。明确短超时的调用方可选择 `background: true`。 |

MCP Python SDK 1.30.0 本地探针中，同一个 16 秒工具调用在约 15 秒收到 SDK 保活，最后收到结构化结果。有进度令牌的调用先收到 `notifications/progress`。沿用已验证的 SDK 保活周期，无需另写传输层。[Snowflake 超时说明](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-mcp#limitations) [ZCode CLI MCP 配置](https://github.com/zai-org/ZCode/blob/main/apps/zcode-cli/docs/usage/07-mcp.md)

## 4. 授权：个人访问令牌

2026-11-22 更新：原方案由 DataAgent 充当 OAuth 授权服务器（#55）。MCP 客户端（如 ZCode）走 OAuth 发现和授权时要求 HTTPS，内网部署就得为此配证书，因此改为用户自行申请的固定令牌，整套 OAuth 授权服务器删除，不并存。

```
用户（网页登录）── 申请令牌 ──> DataAgent（只存哈希）
MCP 客户端 ── Authorization: Bearer dak_… ──> DataAgent MCP 地址
```

### 4.1 申请与管理

- 入口：顶部用户菜单「访问令牌」，页面 `/settings/mcp-tokens`，所有已登录用户可用（不是 `adminOnly`）。
- 新建：填名称（必填，≤64 字），选有效期 30 / 90 / 180 天或永不过期，网页默认永不过期。完整令牌只在创建页面签发成功后显示一次，提供复制按钮。离开页面后无法再看到。
- 列表：名称、令牌前缀（`dak_` 后 6 位）、创建时间、最后使用时间、过期时间、状态（有效 / 已过期 / 已撤销），可撤销。只列有效和最近 30 天内失效的。
- 每个用户最多 10 个有效令牌，超出时创建返回 409。
- 管理员在同一页可切换「全部用户」，查看并撤销任何人的令牌（不能替别人创建）。

### 4.1.1 对话页的连接配置

- 会话文件按钮左侧提供「更多操作」菜单。「复制链接」复制包含助手与当前会话 ID 的应用地址。尚未创建会话时复制助手的对话入口。
- 「MCP 配置」打开表单。通过当前助手的可见资料接口读取 MCP 路径，通过本人令牌列表读取有效令牌。只有一个选项时自动选中。没有有效令牌时，选择框右侧的「新增令牌」在新标签页打开独立的令牌创建页面。创建成功后在该页直接导出当前助手的配置。令牌下拉框右侧可刷新列表。新签发后可回到原标签页刷新，已选令牌仍有效时保留选择及输入。
- 配置使用当前访问域名、助手的 MCP 路径和现有客户端 JSON 格式。没有选择令牌时使用 Bearer 占位符，配置支持复制或下载。选中的旧令牌在列表中只有前缀，可粘贴创建时保存的完整值生成配置。关闭表单或切换令牌时清除输入。
- 令牌查询与配置生成采用现有 HTTP 接口和权限，后端继续只存哈希。助手未开启 MCP 时窗口提示该状态。

### 4.2 接口

| 接口 | 说明 |
| --- | --- |
| `GET /api/v1/dataagent/mcp-tokens?scope=mine\|all` | 列表；`all` 仅管理员。不返回令牌或哈希 |
| `POST /api/v1/dataagent/mcp-tokens` | `{name, expires_in_days: 30\|90\|180\|null}`；返回记录和一次性的 `token` |
| `DELETE /api/v1/dataagent/mcp-tokens/{token_id}` | 撤销（写 `revoked_at`，不删行）；本人或管理员 |

记录字段：`{id, name, token_prefix, user_id, display_name, created_at, last_used_at, expires_at, revoked_at, status}`，时间为 ISO 8601 UTC，`status` 为 `active|expired|revoked`。列表返回 `{items: [记录]}`，按创建时间倒序；创建返回 `{item: 记录, token: "dak_…"}`（201）；撤销返回 `{item: 记录}`，已撤销的再撤销照样返回 200。名称为空或过长、有效期不在允许值内返回 422。

管理接口需要网页登录会话。创建和撤销必须携带与请求协议、主机、端口完全一致的 `Origin`，列表拒绝显式跨源请求。入口代理传递完整 `Host` 和外部协议。平台登录未开启时返回 404。

### 4.3 令牌与校验

| 项 | 做法 |
| --- | --- |
| 格式 | `dak_` + `secrets.token_urlsafe(32)` |
| 存储 | 表 `da_mcp_access_token` 只存 SHA-256 哈希，明文不落库、不进日志 |
| 校验 | MCP 请求以 `Authorization: Bearer` 按哈希查未撤销、未过期的令牌，`last_used_at` 最多每分钟写一次。下载请求在没有 Authorization 时可使用登录会话 |
| 身份 | 记录中的 `user_id`（`provider:account`）和显示名组成 `AuthIdentity`；本地账号每次查 `local_admins`；SSO 令牌沿用签发时会话的显式角色，普通用户签发的令牌不会提权。由 `ADMIN_USERS` 授权的管理员令牌每次重新核对配置 |
| 可见范围 | 不进令牌：令牌可用于该用户可见的任一已开启 MCP 的助手，仍由 `_published` 判断 |
| 401 | `WWW-Authenticate: Bearer realm="DataAgent MCP"`，**不带** `resource_metadata`，客户端不会发起 OAuth 发现 |

保留 `Origin` 头与请求来源一致的检查。下载继续验证助手开放状态、可见范围、会话归属和文件路径。请求携带 Authorization 时，只用该凭据校验。

### 4.4 代价与不做

- 走 HTTP 时令牌明文传输，只适用于内网或可信网络；需要上公网时在部署层加 HTTPS，代码不变。
- SSO 身份源的停用和显式角色变更不会主动通知 DataAgent。管理员可撤销已有令牌，默认有效期 90 天。
- 不做自动续期；过期后重新申请并更新客户端配置。
- 不做按助手限定的令牌。

## 5. 数据与代码

- 迁移 `20261119_000038`：`da_agent_profile` 增加 `remote_mcp_enabled`；`da_agent_task` 增加 `interactive`（默认 1）和 `request_key`（可空，唯一）。
- 迁移 `20261122_000040`：删除 `da_mcp_refresh_token`；新增 `da_mcp_access_token`（`id` 自增主键、`token_hash` CHAR(64) 唯一、`token_prefix`、`user_id`、`display_name`、`name`、`expires_at` 可空、`last_used_at` 可空、`revoked_at` 可空、`created_at`；索引 `user_id`）。时间均为 UTC。
- 迁移 `20261122_000041`：记录 SSO 令牌的角色覆盖，存量令牌按普通用户处理，网页登录可重新签发。
- `api/remote_mcp.py`：5 个工具和下载路由，均调用现有存储与提交服务；删除资源元数据、授权服务器元数据、`/oauth/register|authorize|token` 路由。
- `core/remote_mcp_policy.py`：只保留启用判断、地址拼接和 `verify_identity`（改为查令牌表）；删除 JWT、PKCE、客户端注册和刷新令牌相关代码。
- `core/remote_mcp_tokens.py`：改为个人令牌的创建、列表、撤销和查找；删除授权码（Redis）与刷新令牌存储。
- 令牌管理接口放在 `api/remote_mcp.py`（与 MCP 同一模块，`/api/v1/dataagent/mcp-tokens`）。
- 配置：无 MCP 专用配置，启用条件为平台登录已开启（SSO 或本地账号任一）。对外地址由请求的 `Host` 和 `X-Forwarded-Proto` 得出。nginx 删除 `/.well-known/oauth-authorization-server` 和 `/.well-known/openid-configuration` 两条转发（`deploy/docker/nginx/default.conf`、`dataagent-frontend/nginx.conf`）。
- 运行时 `interactive` 开关：`cell.ts`、`frames.ts`、`pi_runtime.py`、`task_executor.py`，以及 `prompts/non_interactive_mode.md`。
- 助手详情页标题右侧提供省略号菜单。管理员可在第一行切换「MCP 访问」。开关作用于已发布的助手，切换即生效（`PUT /api/v1/dataagent/agents/<agent_id>/remote-mcp`，仅管理员；首次发布前不可开启）。菜单在切换后保持展开。开启后出现「MCP 配置」，点击复用对话页的配置窗口。普通用户只有配置入口，显示条件为只读资料返回有效的 `remote_mcp_path`。导出的配置用当前页面的协议和域名拼出地址：

```json
{ "mcpServers": { "dataagent-<助手名>": {
    "type": "http",
    "url": "<当前协议>://<当前域名>/api/v1/remote-agents/<agent_id>/mcp",
    "headers": { "Authorization": "Bearer <你的访问令牌>" } } } }
```

- 前端删除授权页 `/mcp-authorize`（`McpAuthorizeView.vue`、路由和登录守卫中的特例）；新增 `views/settings/McpTokensView.vue` 及用户菜单入口。

[实施计划](../plans/2026-11-19-remote-assistant-plan.md)
