# 会话 API

Base URL：`http://<DataAgent 主机>:8900/api/v1/nl2sql`。启用认证后，请求需带登录会话（见部署指南的“登录配置”）。

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

## 单助手 MCP

登录 DataAgent 后，从用户菜单「访问令牌」新建令牌并复制。进入已开启「MCP 访问」的助手详情页「复制配置」，将令牌填入客户端配置的 `headers.Authorization`，值为 `Bearer dak_…`。客户端通过 Streamable HTTP 连接 `<scheme>://<host>/api/v1/remote-agents/<agent_id>/mcp`，协议和域名与打开详情页时一致。

每次请求都会检查令牌状态和助手可见性。令牌缺失、无效、过期或已撤销时，返回 `401` 和 `WWW-Authenticate: Bearer realm="DataAgent MCP"`。令牌过期后重新申请，并更新客户端配置。

### 管理 MCP 访问令牌

以下接口需要网页登录会话。创建和撤销还需要与页面一致的 `Origin`（协议、主机和端口）。平台登录关闭时返回 `404`。

| 接口 | 请求与响应 |
| --- | --- |
| `GET /api/v1/dataagent/mcp-tokens?scope=mine\|all` | 返回 `{items: [...]}`，按创建时间倒序列出有效和最近 30 天内失效的令牌。默认 `mine`，`all` 仅管理员可用。 |
| `POST /api/v1/dataagent/mcp-tokens` | 提交 `{name, expires_in_days}`。名称必填，最长 64 字，有效期为 30、90、180 天或 `null`（永不过期），默认 90 天。成功返回 `201` 和 `{item, token}`，完整令牌仅在此时显示。名称或有效期无效返回 `422`，每人已有 10 个有效令牌时返回 `409`。 |
| `DELETE /api/v1/dataagent/mcp-tokens/{token_id}` | 本人或管理员可撤销，返回 `200` 和 `{item}`。再次撤销返回同一记录。 |

令牌记录字段为 `id`、`name`、`token_prefix`、`user_id`、`display_name`、`created_at`、`last_used_at`、`expires_at`、`revoked_at`、`status`。时间为 ISO 8601 UTC，状态为 `active`、`expired` 或 `revoked`。

### 助手工具

Streamable HTTP 请求的 `Accept` 同时包含 `application/json` 和 `text/event-stream`。默认 `ask` 在同一次 SSE 响应中返回终态结果。客户端提供 `_meta.progressToken` 时可接收进度通知。服务器等待 15 分钟后若任务仍未完成，返回 `in_progress` 和 `run_id`，任务可继续执行。断线后用 `get_run` 取结果；若还未收到句柄，用原问题、原会话参数和同一个 `request_key` 重试。

| 工具 | 用法 |
| --- | --- |
| `ask` | 传 `question`、`request_key`，续聊时带已有 `thread_id`。首问自动建会话。原请求重试用相同键和相同参数。会话已有在途任务时返回 `thread_busy` 和该任务的 `run_id`。默认通过同一次 SSE 调用等待结果；显式传 `background: true` 时立即返回当前状态和 `run_id`。 |
| `list_threads` | 按 `query` 搜索标题，传可选的 `page`、`page_size`。只返回登录用户在这个助手下的会话（网页、Widget 和 MCP）。 |
| `get_thread` | 传 `thread_id` 和可选分页参数，读取最新在前的消息。 |
| `get_run` | 传 `run_id` 取结果。状态为 `in_progress` 时，按 `retry_after_ms` 轮询。完成后返回 `answered`（含 `answer`、`message_id` 和本轮生成文件的 `files`）、`failed` 或 `cancelled`。 |
| `feedback` | 传 `thread_id`、`message_id` 和 `rating`（`like` 或 `dislike`），与网页点赞点踩相同。 |

`files` 是网页会话文件面板里的同一批文件，下载地址使用同一 MCP 令牌，并按助手、会话和用户检查访问权限。参见 [部署文档](../../deploy/README.md#单助手-mcp-发布)。
