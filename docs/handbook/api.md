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

从助手详情页「复制配置」，在 MCP 客户端打开授权并在浏览器登录 DataAgent 后确认。客户端通过 Streamable HTTP 连接 `https://<host>/api/v1/remote-agents/<agent_id>/mcp`，`<host>` 为打开详情页时的域名。

资源元数据指向同域的 DataAgent OAuth 授权服务器；客户端可从 `/.well-known/oauth-authorization-server` 发现注册、授权和令牌接口。支持动态注册或使用公开客户端 `dataagent`，授权码交换必须使用 S256 PKCE。访问令牌通过 `Authorization: Bearer` 发送，有效期 1 小时；刷新令牌有效期 30 天，每次使用都会轮换。助手可见性仍在每次请求时检查。

| 工具 | 用法 |
| --- | --- |
| `ask` | 传 `question`、`request_key`，续聊时带已有 `thread_id`。首问自动建会话。原请求重试用相同键和相同参数。会话已有在途任务时返回 `thread_busy` 和该任务的 `run_id`。等待约 8 秒仍未完成时返回 `in_progress`。 |
| `list_threads` | 按 `query` 搜索标题，传可选的 `page`、`page_size`。只返回登录用户在这个助手下的会话（网页、Widget 和 MCP）。 |
| `get_thread` | 传 `thread_id` 和可选分页参数，读取最新在前的消息。 |
| `get_run` | 传 `run_id` 取结果。状态为 `in_progress` 时，按 `retry_after_ms` 轮询。完成后返回 `answered`（含 `answer`、`message_id` 和本轮生成文件的 `files`）、`failed` 或 `cancelled`。 |
| `feedback` | 传 `thread_id`、`message_id` 和 `rating`（`like` 或 `dislike`），与网页点赞点踩相同。 |

`files` 是网页会话文件面板里的同一批文件，下载地址使用同一 MCP 令牌，并按助手、会话和用户检查访问权限。参见 [部署文档](../../deploy/README.md#单助手-mcp-发布)。
