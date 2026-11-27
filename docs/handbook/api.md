# 会话 API

Base URL：`http://<DataAgent 主机>:8900/api/v1/nl2sql`。DataAgent 始终启用统一账号认证：平台业务接口需先调用 `POST /auth/login` 获取 `da_session` Cookie（或携带 `Authorization: Bearer <session_jwt>`）；仅 `/health`、`/auth/config`、`/auth/login`、`/auth/logout` 与 OAuth 入口可未登录访问。

Widget 接入（仅限可信内网，不设访问密钥）通过请求头 `X-DataAgent-Client: widget`、`X-DataAgent-Website-Id: <website-id>` 与 `X-DataAgent-User-Id: <纯用户名>` 访问会话运行和助手目录接口：站点须已在管理后台「Widget 接入白名单」登记，且请求不带 `Origin` 头（服务端 BFF 调用）或 `Origin` 位于该站点的 `allowed_origins` 中。未登记站点或未放行 `Origin` 返回 `403`，缺少用户名返回 `401 WIDGET_LOGIN_REQUIRED`。Widget 凭据不能访问 `/api/v1/dataagent/*` 账号/角色/授权/令牌或管理与评测路由（返回 `403`）。

## 账号、角色、资料与助手授权（`/api/v1/dataagent`）

| 方法 | 路径 | 登录要求 | 说明 |
| --- | --- | --- | --- |
| `GET` / `PUT` | `/profile` | 平台登录 | 查看用户名、显示名、登录方式（`password` / `oauth`）与角色，或修改显示名 |
| `PUT` | `/profile/password` | 平台登录 | 修改本人密码 |
| `GET` / `POST` | `/users` | 管理员 | 列出 / 新建用户（使用系统默认初始密码） |
| `GET` / `PUT` | `/users/{user_id}` | 管理员 | 查看 / 编辑用户、角色或启用停用（不提供物理删除） |
| `POST` | `/users/{user_id}/reset-password` | 管理员 | 将普通用户密码恢复为系统默认初始密码 |
| `GET` / `POST` / `PUT` / `DELETE` | `/roles[/{role_id}]` | 管理员 | 列出 / 管理自定义角色（内置 `admin` / `user` 不可删） |
| `GET` / `PUT` | `/roles/{role_id}/users` | 管理员 | 查看 / 批量设置角色成员 |
| `PUT` | `/roles/{role_id}/agents` | 管理员 | 批量设置该角色可访问的助手列表 |
| `GET` / `PUT` | `/agents/{agent_id}/grants` | 管理员 | 读取 / 覆盖指定助手的授权用户与角色（独立于草稿保存与发布） |

管理员会话审计使用 `GET /api/v1/nl2sql-admin/topics`。可按 `user_id` 和 `source` 筛选，用户下拉框使用 `GET /api/v1/dataagent/users?keyword=…&page=1&page_size=100`。

## 健康检查

```
GET /health
```

只说明进程存活；会话存储是否可用，以 `POST /topics` 能否成功为准。

## 会话

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/topics` | 创建会话，请求体 `{"title"?, "agent_id"?, "permission_mode"?}` |
| `GET` | `/topics` | 当前用户的会话列表 |
| `GET` | `/topics/{topic_id}` | 会话详情 |
| `PUT` | `/topics/{topic_id}` | 修改标题或权限模式 |
| `DELETE` | `/topics/{topic_id}` | 删除会话 |
| `GET` | `/topics/{topic_id}/messages` | 会话消息 |
| `POST` / `GET` | `/topics/{topic_id}/files` | 上传 / 列出会话附件 |
| `PUT` | `/topics/{topic_id}/messages/{message_id}/feedback` | 消息反馈 |
| `POST` | `/topics/{topic_id}/messages/{message_id}/followup-suggestions` | 生成追问建议 |

消息的 `created_at` 表示消息行创建时间。助手消息的 `finished_at` 来自对应任务的完成时间。用户消息或暂未记录完成时间的助手消息返回 `null`。

## 发送消息

```
POST /tasks/deliver-message
```

```json
{
  "topic_id": "<topic_id>",
  "content": "最近 30 天工作流发布次数趋势",
  "agent_id": "<可选，默认用会话绑定的智能体>"
}
```

所有任务使用同一档预算（总超时默认 1800 秒）。旧字段 `execution_mode` 已删除，旧客户端继续发送会被忽略。

另有通用入口 `POST /tasks`，请求体用 `message_type` 与 `message_content`（不是 `question`）。

响应：

```json
{
  "accepted": true,
  "topic_id": "<topic_id>",
  "task_id": "<task_id>",
  "task_status": "waiting",
  "user_message_id": "<id>",
  "assistant_message_id": "<id>"
}
```

## 任务

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/tasks/{task_id}` | 任务状态：`waiting` → `running` → `success` / `failed` / `suspended` |
| `GET` | `/tasks/{task_id}/message` | 任务对应的助手消息 |
| `GET` | `/tasks/{task_id}/sdk-events` | 已持久化的运行时事件 |
| `GET` | `/tasks/{task_id}/sdk-events/stream` | SSE 实时事件流（`/events/stream` 不存在） |
| `POST` | `/tasks/{task_id}/cancel` | 取消任务 |
| `POST` | `/tasks/{task_id}/permission-decision` | 审批工具调用：`{"request_id", "decision": "allow"\|"deny", "note"?}` |
| `POST` | `/tasks/{task_id}/question-answer` | 回答运行时向用户提出的问题 |

`suspended` 表示任务在等待审批或回答，处理后自动继续。

任务状态和取消响应的 `finished_at` 为已记录的任务完成时间。`/tasks/{task_id}/message` 返回的助手消息使用同一时间。

## 单助手 MCP

登录 DataAgent 后，从用户菜单「访问令牌」新建令牌并复制。进入已开启「MCP 访问」的助手详情页「复制配置」，将令牌填入客户端配置的 `headers.Authorization`，值为 `Bearer dak_…`。客户端通过 Streamable HTTP 连接 `<scheme>://<host>/api/v1/remote-agents/<agent_id>/mcp`，协议和域名与打开详情页时一致。

每次请求都会检查令牌状态和助手可见性。令牌缺失、无效或已撤销时，返回 `401` 和 `WWW-Authenticate: Bearer realm="DataAgent MCP"`。个人令牌无有效期和数量上限。

### 管理 MCP 访问令牌

以下接口需要网页登录会话。创建和撤销还需要与页面一致的 `Origin`（协议、主机和端口）。列表和撤销只作用于当前账号的令牌。

| 接口 | 请求与响应 |
| --- | --- |
| `GET /api/v1/dataagent/mcp-tokens` | 返回 `{items: [...]}`，按创建时间倒序列出本人的有效令牌及最近 30 天内撤销的令牌。 |
| `POST /api/v1/dataagent/mcp-tokens` | 提交 `{name}`。名称必填，最长 64 字。成功返回 `201` 和 `{item, token}`，完整令牌仅在此时显示。名称无效返回 `422`。 |
| `DELETE /api/v1/dataagent/mcp-tokens/{token_id}` | 撤销本人的令牌，返回 `200` 和 `{item}`。再次撤销返回同一记录。令牌不属于当前账号时返回 `404`。 |

令牌记录字段为 `id`、`name`、`token_prefix`、`user_id`、`created_at`、`last_used_at`、`revoked_at`、`status`。时间为 ISO 8601 UTC，状态为 `active` 或 `revoked`。

### 助手工具

Streamable HTTP 请求的 `Accept` 同时包含 `application/json` 和 `text/event-stream`。默认 `ask` 在同一次 SSE 响应中返回终态结果。客户端提供 `_meta.progressToken` 时可接收进度通知。服务器等待 15 分钟后若任务仍未完成，返回 `in_progress` 和 `run_id`，任务可继续执行。断线后用 `get_run` 取结果；若还未收到句柄，用原问题、原会话参数和同一个 `request_key` 重试。

工具调用失败时返回 `isError: true`。`structuredContent.error` 包含 `code`（供客户端判断）、`message`（发生了什么）和 `next_step`（如何继续），同一结果也以 JSON 放在 `content[0].text` 中。收到 `thread_busy` 时，用返回的 `run_id` 查询当前任务。收到 `request_conflict` 时，提交新问题要使用新的请求键。查询已终止时，如用户决定重试，也用新的请求键提交；连接丢失或暂时未收到结果时，仍用原请求键恢复。`get_run` 查到已失败或已取消的任务时，正常返回任务状态以及相应的 `error` 和后续建议。外层 HTTP `401` 表示客户端需要检查访问令牌。参见 [MCP 工具错误规范](https://modelcontextprotocol.io/specification/2025-11-25/server/tools#error-handling)。

| 工具 | 用法 |
| --- | --- |
| `ask` | 传 `question`、`request_key`，续聊时带已有 `thread_id`。首问自动建会话。原请求重试用相同键和相同参数。会话已有在途任务时返回 `thread_busy` 和该任务的 `run_id`。默认通过同一次 SSE 调用等待结果；显式传 `background: true` 时立即返回当前状态和 `run_id`。 |
| `list_threads` | 按 `query` 搜索标题，传可选的 `page`、`page_size`。只返回登录用户在这个助手下的会话（网页、Widget 和 MCP）。 |
| `get_thread` | 传 `thread_id` 和可选分页参数，读取最新在前的消息。 |
| `get_run` | 传 `run_id` 取结果。状态为 `in_progress` 时，按 `retry_after_ms` 轮询。完成后返回 `answered`（含 `answer`、`message_id` 和本轮生成文件的 `files`）、`failed` 或 `cancelled`。 |
| `feedback` | 传 `thread_id`、`message_id` 和 `rating`（`like` 或 `dislike`），与网页点赞点踩相同。 |

`files` 是网页会话文件面板里的同一批文件，客户端可用同一 MCP 令牌下载。在浏览器打开链接前，先登录同一地址的 DataAgent。下载时仍校验助手可见性、会话归属和文件路径。若请求携带 Authorization，则使用该凭据。参见 [部署文档](../../deploy/README.md#单助手-mcp-发布)。
