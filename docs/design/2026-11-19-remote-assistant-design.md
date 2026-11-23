# 将单个问数助手发布为 MCP 服务

日期 2026-11-19 · 基于 DataAgent main `f93adf1`

管理员在助手上开启「MCP 访问」并发布，页面提供该助手的连接地址。用户在本地 MCP 客户端配置地址并登录后，即可向该助手问数、追问和评价。

例如「经营分析助手」的地址是 `https://dataagent.example.com/api/v1/remote-agents/agent_sales/mcp`。每个地址绑定一个 `agent_id`，所有助手共用同一个 FastAPI 进程和任务协调器。

## 1. 原则：MCP 会话就是普通会话

MCP 只是又一个入口。用户通过 MCP 发起的会话，和网页、Widget 发起的会话是同一种对象：

- 同一个会话、任务、消息模型，同一个工作区、检查点和历史。
- 同一个身份：MCP 令牌映射到和网页登录相同的平台用户。
- 同一套可见范围和权限。

唯一的区别是**无人值守**：没有人能回答追问或确认操作。会话来源记为 `remote`，界面上显示为「MCP」。

| 方面 | 做法 | 复用的现有能力 |
| --- | --- | --- |
| 身份 | DataAgent 自己签发 MCP 令牌（第 4 节）。用户在授权页上以网页登录的身份同意，令牌的主体就是该平台用户 ID（`<provider>:<sub>` 或 `local:<name>`）；角色和可见范围每次请求实时判定 | 网页登录（SSO 或本地账号）、`resolve_oauth_role` |
| 能否使用 | 助手已发布且开启「MCP 访问」，并对该用户可见 | 助手可见范围 `agent_visible_to` |
| 会话 | 首问按网页同样的方式建会话，来源 `remote`；续聊传 `thread_id`，只能是自己在该助手下的会话。网页、MCP 和平台登录模式的 Widget 都列出本人的这些会话 | `create_topic`、本人会话判断 |
| 问数 | 提交为普通任务，标记 `interactive=false` | `submit_message_task`、协调器、执行器 |
| 结果 | 回答文本和本轮生成的文件，即网页消息里的同一批附件 | 助手消息与附件 |
| 下载 | MCP 令牌鉴权后读同一个工作区文件 | `topic_file_response` |
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
| `ask` | `question`、`request_key`，续聊时带 `thread_id` | 最多等约 8 秒。完成返回结果；未完成返回 `in_progress`、`run_id`、`thread_id`、`retry_after_ms`。会话已有任务在运行时返回 `thread_busy` 和该任务的 `run_id`。 |
| `list_threads` | 可选标题查询、页码、每页条数 | 该用户在这个助手下的会话（网页、Widget、MCP），含 `thread_id`、标题、来源、更新时间。 |
| `get_thread` | `thread_id`、页码、每页条数 | 最新在前的消息。 |
| `get_run` | `run_id` | `in_progress`，或 `answered`（`answer`、`message_id`、`files`），或 `failed`/`cancelled`（`error`）。 |
| `feedback` | `thread_id`、`message_id`、`rating: like\|dislike` | 当前评价。 |

`request_key` 用于重试：任务表的 `request_key` 列存「助手 + 用户 + 键」的摘要并唯一。同键同问题返回原任务；同键换了问题或会话返回 `request_conflict`。`files` 中每个文件带下载地址 `.../remote-agents/<agent_id>/threads/<thread_id>/files/<path>`，须携带同一令牌。

## 4. 授权：DataAgent 作为授权服务器

MCP 客户端与 DataAgent 之间走标准 MCP 授权（OAuth 2.1：授权码 + PKCE），授权服务器就是 DataAgent 自己；DataAgent 再通过现有网页登录认人。SSO 不需要为 MCP 注册客户端、回调地址、scope 或 audience，DataAgent 也没有 MCP 专用配置。这与 Figma、Notion、Atlassian 等远程 MCP 服务的做法一致：用户登录的是服务自己的账号，企业 SSO 接在服务登录之后。

```
MCP 客户端 ⇄ DataAgent（授权与令牌）
              └─ 用户未登录时走现有网页登录 ⇄ SSO（原样不动）
```

### 4.1 流程

1. 客户端无令牌请求助手地址，收到 401 和 `WWW-Authenticate`，读受保护资源元数据，其 `authorization_servers` 为 DataAgent 自己的地址（`<origin>`，取自请求）。
2. 客户端读 `<origin>/.well-known/oauth-authorization-server`（同一内容也在 `/.well-known/openid-configuration`），得到授权、令牌和注册地址。
3. 客户端动态注册，或直接使用固定的公开客户端 `dataagent`。
4. 浏览器打开授权地址。DataAgent 校验参数后跳到前端授权页 `/mcp-authorize`；未登录时先经现有登录守卫完成网页登录再回来。页面显示客户端名称、回调地址的主机和当前用户，用户点「允许」或「拒绝」。
5. 浏览器回到客户端的回调地址，带授权码；客户端换取访问令牌和刷新令牌，之后每次请求携带 `Authorization: Bearer`，过期时用刷新令牌续期。

### 4.2 接口

| 接口 | 说明 |
| --- | --- |
| `GET /.well-known/oauth-protected-resource/api/v1/remote-agents/<agent_id>/mcp` | `resource` 为该助手地址，`authorization_servers` 为 `[<origin>]`，`scopes_supported` 为 `["dataagent:ask"]` |
| `GET /.well-known/oauth-authorization-server`、`GET /.well-known/openid-configuration` | `issuer=<origin>`；`authorization_endpoint`、`token_endpoint`、`registration_endpoint`；`response_types_supported=["code"]`；`grant_types_supported=["authorization_code","refresh_token"]`；`code_challenge_methods_supported=["S256"]`；`token_endpoint_auth_methods_supported=["none"]` |
| `POST /api/v1/remote-agents/oauth/register` | RFC 7591 动态注册，只发公开客户端。不落库：`client_id` 是 DataAgent 签名的 `{client_name, redirect_uris}`。另接受固定 `client_id=dataagent`，无需注册 |
| `GET /api/v1/remote-agents/oauth/authorize` | 校验 `response_type=code`、客户端、`redirect_uri`、`code_challenge`（必须 S256）、`state`、`scope`（缺省为 `dataagent:ask`）、`resource`（可选）；通过后 302 到 `/mcp-authorize?<原参数>`。客户端或回调地址无效时直接显示错误，不回跳 |
| `POST /api/v1/remote-agents/oauth/authorize` | 前端授权页提交 `{参数, decision: allow\|deny}`，需网页登录会话和现有防 CSRF 头；返回带 `code`/`state` 或 `error=access_denied` 的回跳地址 |
| `POST /api/v1/remote-agents/oauth/token` | 表单提交。`authorization_code`：校验授权码、客户端、`redirect_uri` 和 PKCE；`refresh_token`：校验并轮换。返回 `access_token`、`token_type=Bearer`、`expires_in`、`refresh_token`、`scope` |

### 4.3 令牌

| 项 | 做法 |
| --- | --- |
| 访问令牌 | HS256 JWT，密钥由 `SECRET_KEY` 派生（与会话签名分开派生）；`iss=<origin>`，`aud=<origin>/api/v1/remote-agents`（共用受众，可用于该用户可见的任一已开启 MCP 的助手），`sub` 为平台用户 ID，含显示名、`client_id`、`scope`；1 小时有效 |
| 刷新令牌 | 随机串，库中只存哈希；30 天有效，每次使用即作废并签发新的 |
| 授权码 | Redis，5 分钟有效，只能用一次，绑定客户端、回调地址、PKCE 挑战和用户 |
| 角色与可见范围 | 不进令牌，每次请求按现有规则实时判定 |

回调地址只允许：本机 http（`localhost`、`127.0.0.1`、`[::1]`，端口不限）、https，以及桌面应用的自定义协议；拒绝 `javascript`、`data`、`file` 和非本机 http。授权页显示回调地址的主机供用户确认。

### 4.4 不做

- 用户查看和撤销已授权的客户端；退出网页登录不撤销 MCP 授权。撤销途径暂为等待刷新令牌过期，或关闭助手的「MCP 访问」。已记入[待办](../TODO.md#远端问数助手对外调用)。

## 5. 数据与代码

- 迁移 `20261119_000038`：`da_agent_profile` 增加 `remote_mcp_enabled`；`da_agent_task` 增加 `interactive`（默认 1）和 `request_key`（可空，唯一）。
- `api/remote_mcp.py`：5 个工具、资源元数据和下载路由，均调用现有存储与提交服务。
- `core/remote_mcp_policy.py`：令牌校验与身份映射；授权服务器接口与令牌签发（第 4 节）。
- 迁移：新增 `da_mcp_refresh_token`（令牌哈希、用户 ID、显示名、`client_id`、scope、过期时间、创建时间）。
- 配置：无 MCP 专用配置。删除 `OAUTH_PROVIDERS` 的 `server_metadata_url`（配置了会被忽略）。对外地址由请求的 `Host` 和 `X-Forwarded-Proto` 得出，后端容器的 uvicorn 信任代理传来的协议头；nginx 另外转发 `/.well-known/oauth-authorization-server` 和 `/.well-known/openid-configuration`。启用条件为平台登录已开启（SSO 或本地账号任一）。
- 运行时 `interactive` 开关：`cell.ts`、`frames.ts`、`pi_runtime.py`、`task_executor.py`，以及 `prompts/non_interactive_mode.md`。
- 助手编辑页标题右侧：「MCP 访问」开关和「复制配置」按钮，发布后可用。复制的内容为客户端配置，页面用当前域名拼出地址，并提示「粘贴到客户端的 MCP 设置后点打开授权」：

```json
{ "mcpServers": { "dataagent-<助手名>": {
    "type": "http",
    "url": "https://<当前域名>/api/v1/remote-agents/<agent_id>/mcp",
    "oauth": { "type": "authorization_code", "clientId": "dataagent" } } } }
```

- 前端授权页 `/mcp-authorize`：沿用登录守卫，显示客户端、回调主机和当前用户，「允许」「拒绝」两个按钮。

[实施计划](../plans/2026-11-19-remote-assistant-plan.md)
