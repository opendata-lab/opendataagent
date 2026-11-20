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
| 身份 | 校验令牌的签发方、有效期、`aud`（该助手地址，或共用的 `https://<host>/api/v1/remote-agents`）和 `dataagent:ask` scope，主体映射为 `<provider>:<sub>`，角色按现有管理员名单判定。签发方和签名公钥取自网页登录 OAuth 配置里的 `server_metadata_url`（OIDC 发现文档），`<host>` 取调用方访问的地址，不另设配置 | 网页 OAuth 登录的配置、用户 ID 与 `resolve_oauth_role` |
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

## 4. 首次连接

1. 支持 OAuth 的 MCP 客户端首次请求收到 401 和 `WWW-Authenticate`，按受保护资源元数据找到授权服务器，打开浏览器让用户登录企业身份提供方（授权码 + PKCE）。
2. 客户端拿到面向该助手地址的访问令牌，之后自动携带；过期时按客户端和身份提供方的能力续期或重新登录。
3. 令牌为 RS256 JWT，`aud` 二选一：支持 RFC 8707 的 IdP 按客户端传的 `resource` 签发该助手地址；不支持的 IdP（如 Keycloak 26.7 及以前）在 `dataagent:ask` scope 上配一个 audience mapper，固定写入 `https://<host>/api/v1/remote-agents`，发布新助手无需改 IdP。标准 MCP 客户端会用服务端 `WWW-Authenticate` 给出的 scope，所以只能靠这一个 scope 携带 audience。共用 audience 的令牌可用于该用户可见的任一已开启 MCP 的助手，与网页登录一致。

## 5. 数据与代码

- 迁移 `20261119_000038`：`da_agent_profile` 增加 `remote_mcp_enabled`；`da_agent_task` 增加 `interactive`（默认 1）和 `request_key`（可空，唯一）。
- `api/remote_mcp.py`：5 个工具、资源元数据和下载路由，均调用现有存储与提交服务。
- `core/remote_mcp_policy.py`：令牌校验与身份映射。
- 配置：只在 `OAUTH_PROVIDERS[0]["remote_app"]` 增加可选的 `server_metadata_url`；没有 MCP 专用环境变量。对外地址由请求的 `Host` 和 `X-Forwarded-Proto` 得出，后端容器的 uvicorn 信任代理传来的协议头。助手详情接口返回路径 `remote_mcp_path`，页面用当前域名拼出完整地址。
- 运行时 `interactive` 开关：`cell.ts`、`frames.ts`、`pi_runtime.py`、`task_executor.py`，以及 `prompts/non_interactive_mode.md`。
- 助手编辑页标题右侧：「MCP 访问」开关、地址和复制按钮，发布后地址生效。

[实施计划](../plans/2026-11-19-remote-assistant-plan.md)
